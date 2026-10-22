#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retain revision-bound native execution evidence using predeclared result checks.

The caller selects the trusted native command and its JSON result contract. This
does not sandbox that command or establish semantic correctness of its checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import construction
from . import workspace_contract as _workspace

SCHEMA = "nemo.eval_author.task_evidence.v1"
MANIFEST_SCHEMA = "nemo.eval_author.task_evidence.v2"
IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


class EvidenceError(ValueError):
    """Evidence is missing, stale, or inconsistent."""


def require_binding(actual, expected, message, error_type=EvidenceError):
    """A receipt may only attest the exact registered revision."""
    if actual != expected:
        raise error_type(message)


def require_distinct(values, message, error_type=EvidenceError):
    """Every counted execution has its own native identity and artifact path."""
    if len(set(values)) != len(values):
        raise error_type(message)


def loads(data: str | bytes) -> Any:
    def constant(value: str) -> Any:
        raise EvidenceError(f"nonfinite JSON value: {value}")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise EvidenceError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def read(path: Path) -> dict[str, Any]:
    try:
        value = loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"cannot read evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"expected regular evidence file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_digest(task: Path) -> str:
    if not task.is_dir() or task.is_symlink():
        raise EvidenceError("task must be a directory, not a symlink")
    entries = []
    for path in sorted(task.rglob("*")):
        relative = path.relative_to(task)
        if any(part in IGNORED for part in relative.parts):
            continue
        if path.is_symlink():
            raise EvidenceError(f"task symlinks are not supported: {relative}")
        entries.append([str(relative), path.stat().st_mode & 0o777, digest(path) if path.is_file() else None])
    if not entries:
        raise EvidenceError("task is empty")
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def pointer(value: Any, path: str) -> Any:
    if not isinstance(path, str) or not path.startswith("/"):
        raise EvidenceError("result checks require an absolute JSON pointer")
    try:
        for token in path[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise EvidenceError(f"missing result field: {path}") from exc
    return value


def assertions(value: dict[str, Any], checks: list[dict[str, Any]]) -> bool:
    outcomes = []
    for check in checks:
        actual = pointer(value, check["pointer"])
        if "equals" in check:
            outcomes.append(type(actual) is type(check["equals"]) and actual == check["equals"])
        else:
            outcomes.append(
                type(actual) in (int, float) and math.isfinite(actual) and check["min"] <= actual <= check["max"]
            )
    return all(outcomes)


def validate_contract(contract: dict[str, Any]) -> None:
    if not isinstance(contract, dict):
        raise EvidenceError("contract must be an object")
    if not isinstance(contract.get("task_id"), str) or not contract["task_id"].strip():
        raise EvidenceError("contract requires task_id")
    if contract.get("provider") not in ("harbor", "gym"):
        raise EvidenceError("contract provider must be harbor or gym")
    if not isinstance(contract.get("native_run_id"), str) or not contract["native_run_id"].startswith("/"):
        raise EvidenceError("contract requires native_run_id JSON pointer")
    if contract["provider"] == "gym" and contract["native_run_id"] != "/native_run_id":
        raise EvidenceError("Gym contracts use the maintained adapter and /native_run_id")
    cases = contract.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise EvidenceError("contract requires cases")
    kinds = set()
    for case in cases.values():
        if not isinstance(case, dict):
            raise EvidenceError("case must be an object")
        kind = case.get("kind")
        if kind not in ("reference", "incorrect", "empty", "alternative", "side_effect", "agent", "diagnostic"):
            raise EvidenceError("unknown case kind")
        kinds.add(kind)
        if not isinstance(case.get("requirement"), str) or not case["requirement"].strip():
            raise EvidenceError("each case must explain its requirement and expected behavior")
        for field in ("health", "checks"):
            checks = case.get(field)
            if not isinstance(checks, list) or not checks:
                raise EvidenceError(f"each case requires nonempty {field} assertions")
            for check in checks:
                if not isinstance(check, dict) or set(check) not in ({"pointer", "equals"}, {"pointer", "min", "max"}):
                    raise EvidenceError("check requires pointer and equals, or pointer and min/max")
                if not isinstance(check["pointer"], str) or not check["pointer"].startswith("/"):
                    raise EvidenceError("check requires an absolute JSON pointer")
                bounds = [check[k] for k in ("min", "max") if k in check]
                if bounds and (
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds) or bounds[0] > bounds[1]
                ):
                    raise EvidenceError("check bounds must be finite and ordered")
    if not {"reference", "incorrect", "empty", "agent"} <= kinds:
        raise EvidenceError("contract requires reference, incorrect, empty, and agent cases")
    for kind in ("alternative", "side_effect"):
        if kind not in kinds and not (
            isinstance(contract.get(f"{kind}_not_applicable"), str) and contract[f"{kind}_not_applicable"].strip()
        ):
            raise EvidenceError(f"include {kind} case or explain why it is not applicable")


def fresh(path: Path, task: Path) -> None:
    if path.exists() or path.is_symlink() or path.resolve().is_relative_to(task.resolve()):
        raise EvidenceError(f"output must be new and outside the task tree: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def write_new(path: Path, value: dict[str, Any]) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def prepare(
    task: Path,
    contract_path: Path,
    output: Path,
    previous: Path | None = None,
    reason: str | None = None,
    construction_plan: Path | None = None,
) -> dict[str, Any]:
    contract = read(contract_path)
    validate_contract(contract)
    fresh(output, task)
    repairs = []
    if previous:
        old = read(previous)
        if old.get("schema") not in (SCHEMA, MANIFEST_SCHEMA) or old.get("task_dir") != str(task.resolve()):
            raise EvidenceError("repair must refer to the same task's previous manifest")
        if old.get("contract", {}).get("task_id") != contract["task_id"] or not reason or not reason.strip():
            raise EvidenceError("repair requires the same task ID and a concrete reason")
        repairs = old.get("repairs", [])
        if not isinstance(repairs, list) or len(repairs) >= 3:
            raise EvidenceError("three-repair budget exhausted; retain the unresolved task")
        repairs = [*repairs, {"manifest": str(previous.resolve()), "sha256": digest(previous), "reason": reason}]
    elif reason:
        raise EvidenceError("repair reason requires --previous")
    from .construction import bind

    construction = bind(construction_plan, task, contract) if construction_plan else {"status": "unmeasured"}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "construction": construction,
        "task_dir": str(task.resolve()),
        "task_sha256": task_digest(task),
        "contract": contract,
        "repairs": repairs,
        "attempts": str(output.resolve()) + ".attempts",
    }
    if contract["provider"] == "harbor":
        try:
            from harbor.models.task.task import Task
        except ImportError as exc:
            raise EvidenceError("Harbor preparation needs the Harbor Python runtime used for execution") from exc

        manifest["harbor_task_checksum"] = Task(task.resolve()).checksum
    if contract["provider"] == "gym":
        from .gym_evidence import snapshot

        manifest["gym_inputs"] = snapshot(task.resolve(), contract)
    write_new(output, manifest)
    return manifest


def manifest_at(path: Path) -> dict[str, Any]:
    manifest = read(path)
    if manifest.get("schema") not in (SCHEMA, MANIFEST_SCHEMA):
        raise EvidenceError("unsupported manifest schema")
    validate_contract(manifest.get("contract", {}))
    if manifest["schema"] == MANIFEST_SCHEMA or "construction" in manifest:
        from .construction import recheck

        construction = manifest.get("construction")
        if construction != {"status": "unmeasured"}:
            recheck(construction, Path(manifest["task_dir"]), manifest["contract"])
    repairs = manifest.get("repairs")
    if not isinstance(repairs, list) or len(repairs) > 3:
        raise EvidenceError("invalid repair ledger")
    for repair in repairs:
        if digest(Path(repair["manifest"])) != repair["sha256"]:
            raise EvidenceError("superseded manifest changed")
    require_binding(
        task_digest(Path(manifest["task_dir"])),
        manifest.get("task_sha256"),
        "task revision changed; prepare a new manifest and rerun controls and agents",
    )
    return manifest


def native_identity(native: dict[str, Any], manifest: dict[str, Any]) -> str:
    contract = manifest["contract"]
    identity = pointer(native, contract["native_run_id"])
    if not isinstance(identity, str) or not identity.strip():
        raise EvidenceError("native run identity must be a nonempty string")
    if contract["provider"] == "harbor":
        checksum = manifest.get("harbor_task_checksum")
        if not isinstance(checksum, str) or not checksum or native.get("task_checksum") != checksum:
            raise EvidenceError("native Harbor task checksum differs from prepared task")
        if "exception_info" not in native or native["exception_info"] is not None:
            raise EvidenceError("native Harbor trial has missing health evidence or an exception")
    return identity


def run(
    manifest_path: Path,
    case_id: str,
    run_id: str,
    output: Path,
    result: Path,
    trace: Path | None,
    command: list[str],
    timeout: int = 1800,
    purpose: str = "closure",
    gym_source: Path | None = None,
) -> dict[str, Any]:
    manifest = manifest_at(manifest_path)
    if timeout <= 0:
        raise EvidenceError("timeout must be positive")
    manifest_sha = digest(manifest_path)
    case = manifest["contract"]["cases"].get(case_id)
    if case is None or not run_id.strip() or not command:
        raise EvidenceError("known case, nonempty run ID, and command are required")
    if purpose not in ("baseline", "closure"):
        raise EvidenceError("purpose must be baseline or closure")
    if purpose == "closure" and case["kind"] == "agent" and trace is None:
        raise EvidenceError("agent evidence requires a fresh ATIF trace")
    paths = [output, result] + ([trace] if trace is not None else [])
    if len({p.resolve() for p in paths}) != len(paths):
        raise EvidenceError("receipt, native result, and trace must have distinct paths")
    for path in paths:
        fresh(path, Path(manifest["task_dir"]))
    source = None
    if manifest["contract"]["provider"] == "gym":
        from .gym_evidence import source_paths

        if gym_source is None:
            raise EvidenceError("Gym recording requires --gym-source")
        source = read(gym_source)
        for path in source_paths(source):
            if path.exists() or path.is_symlink() or path.resolve().is_relative_to(Path(manifest["task_dir"])):
                raise EvidenceError("Gym native outputs must be fresh and outside the task")
    elif gym_source is not None:
        raise EvidenceError("--gym-source applies only to Gym")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "case": case_id,
        "purpose": purpose,
        "gym_source": source,
        "run_id": run_id,
        "command": command,
        "cwd": str(Path.cwd()),
        "result": str(result.resolve()),
        "trace": str(trace.resolve()) if trace else None,
        "status": "infrastructure_error",
    }
    attempts = Path(manifest.get("attempts", ""))
    if not manifest.get("attempts"):
        raise EvidenceError("unbound legacy manifest; prepare a new revision before execution")
    if attempts.is_symlink():
        raise EvidenceError("attempt ledger must not be a symlink")
    attempts.mkdir(parents=True, exist_ok=True)
    write_new(
        attempts / (hashlib.sha256(run_id.encode()).hexdigest() + ".json"),
        {
            "run_id": run_id,
            "receipt": str(output.resolve()),
            "purpose": purpose,
            "case": case_id,
            "kind": case["kind"],
            "manifest_sha256": manifest_sha,
        },
    )
    try:
        completed = subprocess.run(
            command, timeout=timeout, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        )
        receipt["returncode"] = completed.returncode
        if completed.returncode:
            raise EvidenceError("native command failed")
        manifest_at(manifest_path)
        if digest(manifest_path) != manifest_sha:
            raise EvidenceError("manifest changed during execution")
        if source is not None:
            from .gym_evidence import build

            write_new(result, build(source, manifest, case, purpose, trace))
        native = read(result)
        _validate_control(native, manifest["contract"], case)
        receipt["native_run_id"] = native_identity(native, manifest)
        receipt["result_sha256"] = digest(result)
        if trace:
            receipt["trace_sha256"] = digest(trace)
        if not assertions(native, case["health"]):
            raise EvidenceError("native health checks failed")
        receipt["status"] = "passed" if assertions(native, case["checks"]) else "behavior_failed"
        if source is not None:
            receipt["health_status"] = native["health"]["verdict"]
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        receipt["error"] = str(exc)
    write_new(output, receipt)
    return receipt


def _validate_control(native: dict, contract: dict, case: dict) -> None:
    if (
        case["kind"] == "incorrect"
        and contract["provider"] == "harbor"
        and native.get("config", {}).get("agent", {}).get("name") == "nop"
    ):
        raise EvidenceError("incorrect control must exercise a wrong answer; NOP belongs to the empty control")


def verify_receipts(paths: list[Path], task_id: str) -> dict[str, dict[str, Any]]:
    if not paths or len({p.resolve() for p in paths}) != len(paths):
        raise EvidenceError("distinct execution receipts are required")
    manifests = set()
    seen_cases = set()
    seen_runs = set()
    seen_results = set()
    seen_traces = set()
    seen_trace_contents = set()
    seen_native_runs = set()
    agents = {}
    contract: dict[str, Any] = {}
    for path in paths:
        receipt = read(path)
        if receipt.get("schema") != SCHEMA or receipt.get("status") != "passed" or receipt.get("returncode") != 0:
            raise EvidenceError("execution receipt is not passed")
        if receipt.get("purpose") != "closure":
            raise EvidenceError("baseline receipts cannot establish gap closure")
        manifest_path = Path(receipt["manifest"])
        manifest = manifest_at(manifest_path)
        sha = digest(manifest_path)
        require_binding(sha, receipt.get("manifest_sha256"), "manifest digest mismatch")
        manifests.add(sha)
        contract = manifest["contract"]
        if contract["task_id"] != task_id:
            raise EvidenceError("manifest task_id differs from selected task")
        case_id = receipt["case"]
        case = contract["cases"].get(case_id)
        if case is None or case["kind"] == "diagnostic":
            raise EvidenceError("diagnostic or unknown case cannot count as acceptance evidence")
        result = Path(receipt["result"])
        require_binding(digest(result), receipt.get("result_sha256"), "native result digest mismatch")
        native = read(result)
        _validate_control(native, contract, case)
        if contract["provider"] == "gym":
            from .gym_evidence import build

            retained_trace = Path(receipt["trace"]) if receipt.get("trace") else None
            rebuilt = build(receipt["gym_source"], manifest, case, "closure", retained_trace)
            if rebuilt != native:
                raise EvidenceError("Gym native source artifacts changed")
        native_run_id = native_identity(native, manifest)
        if native_run_id != receipt.get("native_run_id") or native_run_id in seen_native_runs:
            raise EvidenceError("native run identity is changed or reused")
        seen_native_runs.add(native_run_id)
        if not assertions(native, case["health"]) or not assertions(native, case["checks"]):
            raise EvidenceError("native evidence does not satisfy declared health and behavior checks")
        run_id = receipt.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip() or run_id in seen_runs:
            raise EvidenceError("execution run IDs must be nonempty and distinct")
        if result.resolve() in seen_results:
            raise EvidenceError("native results must be distinct")
        seen_runs.add(run_id)
        seen_results.add(result.resolve())
        seen_cases.add(case_id)
        if case["kind"] == "agent":
            trace = Path(receipt["trace"])
            trace_sha = digest(trace)
            if (
                trace_sha != receipt.get("trace_sha256")
                or trace.resolve() in seen_traces
                or trace_sha in seen_trace_contents
            ):
                raise EvidenceError("agent trace is changed or reused")
            seen_traces.add(trace.resolve())
            seen_trace_contents.add(trace_sha)
            agents[run_id] = receipt
    if len(manifests) != 1:
        raise EvidenceError("all evidence must use the same manifest revision")
    required = {name for name, case in contract["cases"].items() if case["kind"] not in ("agent", "diagnostic")}
    if not required <= seen_cases or len(agents) < 2:
        raise EvidenceError("every declared control and at least two agent runs are required")
    # Every pre-registered closure attempt counts, including interrupted/failed runs.
    # Repairs get a new manifest and ledger while preserving the previous revision.
    ledger = Path(manifest.get("attempts", ""))
    if not manifest.get("attempts") or ledger.is_symlink() or not ledger.is_dir():
        raise EvidenceError("complete attempt ledger required; reprepare legacy evidence")
    registered = []
    for entry in sorted(ledger.iterdir()):
        if not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name):
            continue
        attempt = read(entry)
        if attempt.get("manifest_sha256") != sha:
            raise EvidenceError("attempt ledger manifest mismatch")
        kind = attempt.get("kind")
        # Older ledgers omitted the case kind; a retained diagnostic receipt can
        # still identify it without editing the original ledger.
        receipt_path = Path(attempt["receipt"])
        if kind is None and receipt_path.is_file():
            case_id = read(receipt_path).get("case")
            kind = contract["cases"].get(case_id, {}).get("kind")
        if attempt.get("purpose") == "closure" and kind != "diagnostic":
            registered.append(Path(attempt["receipt"]).resolve())
    if len(set(registered)) != len(registered) or set(registered) != {path.resolve() for path in paths}:
        raise EvidenceError("all registered closure attempts must be supplied, including failures")
    return agents


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "workspace":
        from .commands import main as workspace_main

        return workspace_main("evidence", sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--task-dir", type=Path, required=True)
    prep.add_argument("--contract", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--construction-plan", type=Path, help="private construction declarations to bind and recheck")
    prep.add_argument("--previous", type=Path, help="previous manifest when repairing this task")
    prep.add_argument("--reason", help="concrete repair reason; retain all earlier receipts")
    execute = commands.add_parser("run")
    execute.add_argument("--manifest", type=Path, required=True)
    execute.add_argument("--case", required=True)
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--out", type=Path, required=True)
    execute.add_argument("--result", type=Path, required=True)
    execute.add_argument("--trace", type=Path)
    execute.add_argument("--timeout", type=int, default=1800)
    execute.add_argument("--purpose", choices=("baseline", "closure"), default="closure")
    execute.add_argument("--gym-source", type=Path)
    execute.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            payload = prepare(
                args.task_dir, args.contract, args.out, args.previous, args.reason, args.construction_plan
            )
        else:
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            payload = run(
                args.manifest,
                args.case,
                args.run_id,
                args.out,
                args.result,
                args.trace,
                command,
                args.timeout,
                args.purpose,
                args.gym_source,
            )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("status", "passed") == "passed" else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}))
        return 1


def _job_check_rows(job_dir: Path) -> list[dict[str, str]] | None:
    """Parse retained per-check rows from a Harbor job, when the verifier emitted them."""

    paths = sorted(
        path for path in job_dir.glob("task__*/verifier/results") if path.is_file() and not path.is_symlink()
    )
    if not paths:
        return None
    if len(paths) != 1:
        raise _workspace.ContractError(f"{job_dir.name} has ambiguous verifier results files")
    try:
        text = paths[0].read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise _workspace.ContractError(f"{paths[0]} must be UTF-8 text") from error
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _workspace._CHECK_ROW.fullmatch(line.strip())
        if match is None:
            raise _workspace.ContractError(f"malformed check row in {paths[0].name}: expected '<check-id>\\tPASS|FAIL'")
        rows.append({"check_id": match.group(1), "status": match.group(2)})
    if not rows:
        raise _workspace.ContractError(f"{paths[0].name} contains no check rows")
    check_ids = [row["check_id"] for row in rows]
    if len(check_ids) != len(set(check_ids)):
        raise _workspace.ContractError(f"{paths[0].name} repeats a check ID")
    return rows


def _probes_path(task_dir: Path) -> Path:
    return task_dir / "private" / "probes" / "probes.json"


def _load_probes(task_dir: Path) -> list[dict[str, Any]]:
    path = _probes_path(task_dir)
    if not path.is_file():
        return []
    payload = _workspace._load_object(path, label="probe ledger")
    if set(payload) != {"schema", "entries"} or payload.get("schema") != _workspace.PROBE_SCHEMA:
        raise _workspace.ContractError("probe ledger fields do not match the versioned contract")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise _workspace.ContractError("probe ledger entries must be a list")
    return entries


def _record_probe(
    task_dir: Path,
    arm: str,
    task_digest: str,
    job_dir: Path,
    reward: Any,
    checks: list[dict[str, str]] | None,
) -> dict[str, Any]:
    entries = _load_probes(task_dir)
    revision = [entry for entry in entries if entry["arm"] == arm and entry["task_tree_sha256"] == task_digest]
    if len(revision) >= _workspace._MAX_PROBES_PER_REVISION:
        raise _workspace.ContractError(
            f"probe budget exhausted for this {arm} revision ({_workspace._MAX_PROBES_PER_REVISION}); "
            "change the task or run the formal proof"
        )
    entry = {
        "arm": arm,
        "task_tree_sha256": task_digest,
        "job_dir": job_dir.relative_to(task_dir).as_posix(),
        "reward": reward,
        "checks": checks,
    }
    entries.append(entry)
    path = _probes_path(task_dir)
    _workspace._mkdir_private(path.parent)
    _workspace._write_json(path, {"schema": _workspace.PROBE_SCHEMA, "entries": entries})
    return {
        "status": "recorded",
        "entry": entry,
        "probes_used": len(revision) + 1,
        "probes_remaining": _workspace._MAX_PROBES_PER_REVISION - len(revision) - 1,
        "cached": False,
    }


def _probe(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _workspace._ensure_task_dir(args.task_dir)
    try:
        _workspace._validate_task(task_dir)
    except _workspace.ContractError as error:
        raise _workspace.ContractError(f"fix validate-task issues before probing: {error}") from error
    digest = _workspace._task_tree_info(task_dir / "task")["task_tree_sha256"]
    arms = ("nop", "oracle") if args.arm == "both" else (args.arm,)
    results: list[dict[str, Any]] = []
    for arm in arms:
        existing = [
            entry for entry in _load_probes(task_dir) if entry["arm"] == arm and entry["task_tree_sha256"] == digest
        ]
        if existing and args.results_from is None:
            results.append({"arm": arm, "cached": True, **existing[-1]})
            continue
        if len(existing) >= _workspace._MAX_PROBES_PER_REVISION:
            raise _workspace.ContractError(
                f"probe budget exhausted for this {arm} revision ({_workspace._MAX_PROBES_PER_REVISION}); "
                "change the task or run the formal proof"
            )
        if args.results_from is not None:
            job_dir, _result_path, result = _job_result(task_dir, args.results_from, arm=f"{arm} probe")
            identity = _agent_identity((result.get("config") or {}).get("agent"))
            if identity != arm:
                raise _workspace.ContractError(f"probe {arm} job must identify the {arm} agent")
        else:
            harbor = shutil.which("harbor")
            if harbor is None:
                results.append(
                    {
                        "arm": arm,
                        "status": "not_run",
                        "hint": "Install/use the existing Harbor environment to run diagnostic probes.",
                    }
                )
                continue
            total = len([entry for entry in _load_probes(task_dir) if entry["arm"] == arm])
            job_name = f"probe-{arm}-{total + 1}"
            jobs_root = task_dir / "private" / "probes" / "jobs"
            _workspace._mkdir_private(jobs_root)
            completed = subprocess.run(
                [
                    harbor,
                    "run",
                    "-p",
                    str(task_dir / "task"),
                    "-a",
                    arm,
                    "--jobs-dir",
                    str(jobs_root),
                    "--job-name",
                    job_name,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            job_dir = jobs_root / job_name
            if completed.returncode != 0 and not job_dir.is_dir():
                raise _workspace.ContractError(
                    f"harbor probe run failed ({completed.returncode}): {completed.stderr.strip()[:500]}"
                )
            job_dir, _result_path, result = _job_result(task_dir, job_dir, arm=f"{arm} probe")
        verifier_result = result.get("verifier_result")
        rewards = verifier_result.get("rewards") if isinstance(verifier_result, dict) else None
        reward = rewards.get("reward") if isinstance(rewards, dict) else None
        recorded = _record_probe(
            task_dir,
            arm,
            digest,
            job_dir,
            reward,
            _job_check_rows(job_dir),
        )
        results.append(
            {
                "arm": arm,
                **recorded["entry"],
                "status": recorded["status"],
                "probes_used": recorded["probes_used"],
                "probes_remaining": recorded["probes_remaining"],
            }
        )
    return {
        "task_dir": str(task_dir),
        "status": "not_run" if all(item.get("status") == "not_run" for item in results) else "recorded",
        "diagnostic_only": True,
        "proof_authority": "probes are never accepted as proof; use record-run-inputs + record-validation",
        "results": results,
    }


def _repairs(task_dir: Path) -> list[dict[str, Any]]:
    root = task_dir / "private" / "repairs"
    if not root.is_dir():
        return []
    receipts: list[tuple[int, dict[str, Any]]] = []
    for path in sorted(root.glob("repair-*.json")):
        match = _workspace._REPAIR_RECEIPT.fullmatch(path.name)
        if match is None:
            raise _workspace.ContractError(f"unexpected file in repair ledger: {path.name}")
        receipt = _workspace._load_object(path, label="repair receipt")
        if (
            set(receipt) != {"schema", "index", "reason_code", "note", "superseded"}
            or receipt.get("schema") != _workspace.REPAIR_SCHEMA
        ):
            raise _workspace.ContractError("repair receipt fields do not match the versioned contract")
        receipts.append((int(match.group(1)), receipt))
    indexes = [index for index, _ in receipts]
    if indexes != list(range(1, len(receipts) + 1)):
        raise _workspace.ContractError("repair ledger indexes must be contiguous from 1")
    for index, receipt in receipts:
        if receipt["index"] != index:
            raise _workspace.ContractError("repair receipt index does not match its file name")
        superseded = receipt["superseded"]
        for name in (
            "validation.json",
            "reproducibility.json",
            "candidate.json",
            "construction.json",
            "active-construction.json",
        ):
            record = superseded.get(name)
            archived = root / f"repair-{index}" / name
            if record is None:
                if archived.exists():
                    raise _workspace.ContractError(f"repair {index} archives an undeclared {name}")
                continue
            if not archived.is_file() or _workspace._sha256_file(archived) != record.get("sha256"):
                raise _workspace.ContractError(f"repair {index} archive of {name} is missing or changed")
    return [receipt for _, receipt in receipts]


def _record_repair(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _workspace._ensure_task_dir(args.task_dir)
    if args.reason_code not in _workspace._REPAIR_REASON_CODES:
        raise _workspace.ContractError(
            f"repair reason code must be one of: {', '.join(sorted(_workspace._REPAIR_REASON_CODES))}"
        )
    if args.reason_code == "other" and len(args.note.strip()) < 20:
        raise _workspace.ContractError("reason code 'other' requires a concrete note")
    if not args.note.strip():
        raise _workspace.ContractError("repair note must be nonempty")
    summary = _workspace._load_summary(task_dir)
    if summary["status"] != "pending":
        raise _workspace.ContractError(
            "repairs apply before finalize; a finalized failed environment stays failed as technical evidence"
        )
    validation_path = task_dir / "validation.json"
    reproducibility_path = task_dir / "reproducibility.json"
    if not validation_path.is_file() and not reproducibility_path.is_file():
        raise _workspace.ContractError(
            "record a repair only in response to a recorded proof attempt or failed probe evidence"
        )
    receipts = _repairs(task_dir)
    index = len(receipts) + 1
    if index > _workspace._MAX_REPAIRS:
        raise _workspace.ContractError(
            f"repair budget exhausted ({_workspace._MAX_REPAIRS}); finalize as failed instead of repairing further"
        )
    # Resolve and snapshot every input before changing any live report. A stale
    # active contract must not strand reports in a half-written repair archive.
    active_contract = construction.active_path(task_dir)
    marker = task_dir / "private/active-construction.json"
    sources = {
        "validation.json": validation_path,
        "reproducibility.json": reproducibility_path,
        "candidate.json": task_dir / "candidate.json",
        "construction.json": active_contract,
    }
    if marker.exists() or marker.is_symlink():
        sources["active-construction.json"] = marker
    snapshots = {}
    superseded: dict[str, Any] = {}
    for name, path in sources.items():
        if path.is_symlink():
            raise _workspace.ContractError("repair inputs must be regular files")
        if not path.exists():
            superseded[name] = None
            continue
        if not path.is_file():
            raise _workspace.ContractError("repair inputs must be regular files")
        data = path.read_bytes()
        snapshots[name] = data
        superseded[name] = {"sha256": _workspace._sha256(data)}
    archive = task_dir / "private" / "repairs" / f"repair-{index}"
    receipt_path = archive.parent / f"repair-{index}.json"
    if archive.exists() or receipt_path.exists():
        raise _workspace.ContractError("repair archive already exists; retain it for recovery")
    _workspace._mkdir_private(archive.parent)
    with tempfile.TemporaryDirectory(prefix="repair-stage-", dir=archive.parent) as temporary:
        staged = Path(temporary)
        for name, data in snapshots.items():
            _workspace._write_bytes_once(staged / name, data)
        for name, data in snapshots.items():
            require_binding(
                sources[name].read_bytes(), data, "repair input changed during archival", _workspace.ContractError
            )
        staged.rename(archive)
    receipt = {
        "schema": _workspace.REPAIR_SCHEMA,
        "index": index,
        "reason_code": args.reason_code,
        "note": args.note.strip(),
        "superseded": superseded,
    }
    _workspace._write_bytes_once(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    # Only superseded live reports and the selection marker leave their active
    # locations. Exact copies and their receipt are already retained. Source and
    # construction artifacts remain available for historical hash verification.
    for name in ("validation.json", "reproducibility.json", "active-construction.json"):
        if name in snapshots:
            require_binding(
                sources[name].read_bytes(),
                snapshots[name],
                "repair input changed after archival; retain both revisions",
                _workspace.ContractError,
            )
            sources[name].unlink()
    return {
        "task_dir": str(task_dir),
        "repair": index,
        "repairs_remaining": _workspace._MAX_REPAIRS - index,
        "reason_code": args.reason_code,
        "next": "record-reproducibility, then a fresh proof set; the archived reports remain private evidence",
    }


def _job_result(task_dir: Path, value: str | Path, *, arm: str) -> tuple[Path, Path, dict[str, Any]]:
    job_dir = Path(value)
    if not job_dir.is_absolute():
        job_dir = task_dir / job_dir
    if job_dir.is_symlink():
        raise _workspace.ContractError(f"{arm} Harbor job directory must not be a symlink")
    job_dir = job_dir.resolve()
    if not job_dir.is_relative_to(task_dir.resolve()):
        raise _workspace.ContractError(f"{arm} Harbor job directory must stay inside the task workspace")
    if not job_dir.is_dir():
        raise _workspace.ContractError(f"{arm} Harbor job directory does not exist as a regular directory")
    results = sorted(job_dir.glob("task__*/result.json"))
    if len(results) != 1:
        raise _workspace.ContractError(f"{arm} Harbor job directory must contain exactly one task__*/result.json")
    result_path = results[0]
    result = _workspace._load_object(result_path, label=f"{arm} Harbor result")
    return job_dir, result_path, result


def _harbor_task_checksum(task_dir: Path) -> str:
    # Let Harbor own task validation and checksum semantics. This is separate
    # from our full-tree digest, which additionally covers executable bits.
    try:
        from harbor.models.task.task import Task
    except ImportError as error:
        raise _workspace.ContractError(
            "proof commands require Harbor; use the existing Harbor Python environment"
        ) from error
    try:
        return Task(task_dir / "task").checksum
    except (ValueError, FileNotFoundError) as error:
        raise _workspace.ContractError(f"Harbor could not validate the proof task: {error}") from error


def _run_input_path(task_dir: Path, job_dir: Path) -> Path:
    relative = job_dir.relative_to(task_dir).as_posix()
    return task_dir / "private" / "run-inputs" / f"{hashlib.sha256(relative.encode()).hexdigest()}.json"


def _construction_context(task_dir: Path) -> dict[str, Any] | None:
    summary = _workspace._load_summary(task_dir)
    path = task_dir / construction.PATH
    if summary["schema"] == _workspace.SCHEMA:
        marker = task_dir / "private/active-construction.json"
        if path.exists() or path.is_symlink() or marker.exists() or marker.is_symlink():
            raise _workspace.ContractError(
                "historical workspace cannot adopt construction evidence in place; initialize a new workspace"
            )
        return None
    try:
        return construction.validate(task_dir)
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as error:
        raise _workspace.ContractError(
            f"construction contract invalid: {error}"
            if isinstance(error, construction.ConstructionError)
            else "construction contract malformed or unreadable"
        ) from error


def _agent_identity(config: Any) -> str:
    if not isinstance(config, dict):
        raise _workspace.ContractError("Harbor result must record config.agent")
    name, import_path = config.get("name"), config.get("import_path")
    for value in (name, import_path):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise _workspace.ContractError("Harbor agent name and import_path must be nonempty text or null")
    # Fail closed on conflicting fields rather than interpreting a provider's
    # precedence differently. No task-supplied module is imported to identify it.
    if name and import_path:
        raise _workspace.ContractError("proof agents must declare only name or import_path, not both")
    identity = import_path or name
    if not identity:
        raise _workspace.ContractError("Harbor result must explicitly identify its agent")
    aliases = {"harbor.agents.nop:NopAgent": "nop", "harbor.agents.oracle:OracleAgent": "oracle"}
    return aliases.get(identity, identity)


def _proof_control(task_dir: Path, agent: str, source: Path, rationale: str, *, arm: str) -> dict[str, Any]:
    identity = _agent_identity({"name": agent})
    if identity in ("nop", "oracle") or re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", identity) is None:
        raise _workspace.ContractError(
            f"{arm} control must identify a custom module:Class agent distinct from NOP and Oracle"
        )
    if not isinstance(rationale, str) or not rationale.strip():
        raise _workspace.ContractError(f"{arm} control requires a task-specific mutation rationale")
    source_path = source if source.is_absolute() else task_dir / source
    if source_path.is_symlink() or not source_path.is_file():
        raise _workspace.ContractError(f"{arm} control source must be a retained regular file")
    source_path = source_path.resolve()
    if not source_path.is_relative_to(task_dir / "private") or not source_path.stat().st_size:
        raise _workspace.ContractError(f"{arm} control source must be nonempty and stay under private/")
    if arm == "alternative" and any(
        path.is_file() and _workspace._sha256_file(path) == _workspace._sha256_file(source_path)
        for path in (task_dir / "task/solution").rglob("*")
    ):
        raise _workspace.ContractError("alternative control must not copy an oracle solution file")
    return {
        "agent": identity,
        "source_path": source_path.relative_to(task_dir).as_posix(),
        "source_sha256": _workspace._sha256_file(source_path),
        "rationale": rationale,
    }


def _record_run_inputs(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _workspace._ensure_task_dir(args.task_dir)
    job_dir = args.job_dir if args.job_dir.is_absolute() else task_dir / args.job_dir
    if job_dir.is_symlink():
        raise _workspace.ContractError("future Harbor job directory must not be a symlink")
    job_dir = job_dir.resolve()
    if not job_dir.is_relative_to(task_dir / "private") or job_dir == task_dir / "private":
        raise _workspace.ContractError("future Harbor job directory must stay under private/")
    if job_dir.is_relative_to(task_dir / "private" / "probes"):
        raise _workspace.ContractError(
            "diagnostic probes can never serve as proof; choose a job directory outside private/probes"
        )
    if job_dir.exists():
        raise _workspace.ContractError(
            "record run inputs before Harbor creates the job directory; use a fresh job name"
        )
    output = _run_input_path(task_dir, job_dir)
    if output.exists():
        raise _workspace.ContractError("run inputs already recorded; use a fresh job name")
    reproducibility = _workspace._validate_reproducibility(task_dir)
    context = _construction_context(task_dir)
    control_id = getattr(args, "control_id", None)
    if context is not None and args.arm in construction.CUSTOM_ARMS:
        if control_id not in context["controls"] or context["controls"][control_id]["arm"] != args.arm:
            raise _workspace.ContractError("custom proof arm requires a matching declared --control-id")
    elif context is not None and control_id is not None:
        raise _workspace.ContractError("built-in arms must not identify custom controls")
    elif control_id is not None or args.arm in ("alternative", "bypass"):
        raise _workspace.ContractError("control IDs, alternative and bypass arms require a construction contract")
    control = None
    if args.arm in construction.CUSTOM_ARMS:
        if not args.negative_agent or args.negative_source is None or not args.negative_rationale:
            raise _workspace.ContractError(
                f"{args.arm} run inputs require --control-agent, --control-source and --control-rationale"
            )
        control = _proof_control(
            task_dir, args.negative_agent, args.negative_source, args.negative_rationale, arm=args.arm
        )
    elif args.negative_agent or args.negative_source or args.negative_rationale:
        raise _workspace.ContractError("control options apply only to custom proof arms")
    receipt = {
        "schema": _workspace.RUN_INPUT_SCHEMA,
        "arm": args.arm,
        "job_dir": job_dir.relative_to(task_dir).as_posix(),
        "task_tree_sha256": reproducibility["task_tree_sha256"],
        "task_checksum": _harbor_task_checksum(task_dir),
        # Keep the v1 receipt field name for both control arms.
        "negative_control": control,
    }
    if context is not None:
        receipt.update(
            schema=_workspace.QUALIFIED_RUN_INPUT_SCHEMA, construction_sha256=context["sha256"], control_id=control_id
        )
        if "canonical_path" in context:
            receipt.update(schema=_workspace.CANONICAL_RUN_INPUT_SCHEMA, construction_path=context["canonical_path"])
    _workspace._mkdir_private(output.parent)
    _workspace._write_bytes_once(output, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    return {
        "task_dir": str(task_dir),
        "arm": args.arm,
        "run_inputs": str(output),
        "task_tree_sha256": receipt["task_tree_sha256"],
    }


def _validate_run_inputs(
    task_dir: Path, job_dir: Path, arm: str, result: dict[str, Any], task_digest: str, checksum: str
) -> dict[str, Any]:
    path = _run_input_path(task_dir, job_dir)
    receipt = _workspace._load_object(
        path, label="pre-run inputs (record-run-inputs is required before every Harbor job)"
    )
    context = _construction_context(task_dir)
    keys = {"schema", "arm", "job_dir", "task_tree_sha256", "task_checksum", "negative_control"}
    if context is not None:
        keys |= {"construction_sha256", "control_id"}
    schema = _workspace.QUALIFIED_RUN_INPUT_SCHEMA if context is not None else _workspace.RUN_INPUT_SCHEMA
    if context is not None and "canonical_path" in context:
        keys.add("construction_path")
        schema = _workspace.CANONICAL_RUN_INPUT_SCHEMA
        require_binding(
            receipt.get("construction_path"),
            context["canonical_path"],
            "pre-run construction path changed",
            _workspace.ContractError,
        )
    if set(receipt) != keys or receipt.get("schema") != schema:
        raise _workspace.ContractError("run inputs do not match the versioned contract")
    if context is not None:
        require_binding(
            receipt["construction_sha256"],
            context["sha256"],
            "pre-run construction contract changed; fresh jobs required",
            _workspace.ContractError,
        )
        control_id = receipt["control_id"]
        if arm in construction.CUSTOM_ARMS:
            if (
                not isinstance(control_id, str)
                or control_id not in context["controls"]
                or context["controls"][control_id]["arm"] != arm
            ):
                raise _workspace.ContractError("pre-run control differs from construction contract")
        elif control_id is not None:
            raise _workspace.ContractError("built-in arms must not identify custom controls")
    if receipt["arm"] != arm or receipt["job_dir"] != job_dir.relative_to(task_dir).as_posix():
        raise _workspace.ContractError("run inputs identify a different Harbor job or proof arm")
    if receipt["task_tree_sha256"] != task_digest or receipt["task_checksum"] != checksum:
        raise _workspace.ContractError(
            "pre-run task snapshot differs from the current task; fresh Harbor jobs are required"
        )
    require_binding(
        result.get("task_checksum"),
        checksum,
        "Harbor result task checksum differs from the current task",
        _workspace.ContractError,
    )
    config = result.get("config")
    identity = _agent_identity(config.get("agent") if isinstance(config, dict) else None)
    control = receipt["negative_control"]
    if arm in construction.CUSTOM_ARMS:
        if not isinstance(control, dict) or set(control) != {"agent", "source_path", "source_sha256", "rationale"}:
            raise _workspace.ContractError(f"{arm} run inputs require retained control source and rationale")
        if not isinstance(control["agent"], str) or not isinstance(control["source_path"], str):
            raise _workspace.ContractError(f"{arm} control agent and source_path must be strings")
        expected = _proof_control(
            task_dir, control["agent"], Path(control["source_path"]), control["rationale"], arm=arm
        )
        if expected != control or identity != control["agent"]:
            raise _workspace.ContractError(
                f"{arm} control source or recorded agent differs from its pre-run declaration"
            )
    elif control is not None or identity != arm:
        raise _workspace.ContractError(f"{arm} Harbor result must identify the {arm} agent")
    return {
        "agent": identity,
        "run_inputs_path": path.relative_to(task_dir).as_posix(),
        "run_inputs_sha256": _workspace._sha256_file(path),
    }


def _arm_from_result(task_dir: Path, job_dir: Path, result_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    verifier_result = result.get("verifier_result")
    rewards = verifier_result.get("rewards") if isinstance(verifier_result, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if reward is not None and (isinstance(reward, bool) or not isinstance(reward, (int, float))):
        raise _workspace.ContractError("Harbor result reward must be numeric or null")
    config = result.get("config")
    job_id = config.get("job_id") if isinstance(config, dict) else None
    if not isinstance(job_id, str) or not job_id.strip():
        raise _workspace.ContractError("Harbor result must record a nonempty config.job_id")
    return {
        "job_id": job_id,
        "job_dir": str(job_dir.relative_to(task_dir.resolve())),
        "result_path": str(result_path.relative_to(task_dir.resolve())),
        "result_sha256": _workspace._sha256(result_path.read_bytes()),
        "reward": reward,
        "exception_present": result.get("exception_info") is not None,
    }


def _validation_from_jobs(
    task_dir: Path,
    nop_job_dirs: list[str | Path],
    oracle_job_dirs: list[str | Path],
    negative_job_dirs: list[str | Path],
    harbor_version: str,
    *,
    allow_aggregate_only: bool = False,
    copy_job_dirs: list[str | Path] | None = None,
    alternative_job_dirs: list[str | Path] | None = None,
    bypass_job_dirs: list[str | Path] | None = None,
) -> dict[str, Any]:
    if not isinstance(harbor_version, str) or not harbor_version.strip():
        raise _workspace.ContractError("Harbor version must be nonempty text")
    task_contract = _workspace._validate_task(task_dir)
    reproducibility = _workspace._validate_reproducibility(task_dir)
    checksum = _harbor_task_checksum(task_dir)
    context = _construction_context(task_dir)
    inputs = {
        "nop": nop_job_dirs,
        "oracle": oracle_job_dirs,
        "negative": negative_job_dirs,
        "copy": copy_job_dirs or [],
    }
    minimum_runs = dict(_workspace._MIN_VALIDATION_RUNS)
    if context is not None:
        inputs.update(alternative=alternative_job_dirs or [], bypass=bypass_job_dirs or [])
        minimum_runs.update(alternative=1, bypass=0)
        if allow_aggregate_only:
            raise _workspace.ContractError("construction qualification requires per-check evidence")
    elif alternative_job_dirs or bypass_job_dirs:
        raise _workspace.ContractError("additional proof arms require a construction contract")
    for arm, minimum in minimum_runs.items():
        if len(inputs[arm]) < minimum:
            raise _workspace.ContractError(f"validation requires at least {minimum} independent {arm} Harbor jobs")
    runs: dict[str, list[dict[str, Any]]] = {arm: [] for arm in inputs}
    trials: list[dict[str, Any]] = []
    check_rows: list[list[dict[str, str]] | None] = []
    for arm, values in inputs.items():
        for index, value in enumerate(values, start=1):
            label = f"{arm} run {index}"
            job_dir, result_path, result = _job_result(task_dir, value, arm=label)
            trials.append(result)
            run_inputs = _validate_run_inputs(
                task_dir, job_dir, arm, result, reproducibility["task_tree_sha256"], checksum
            )
            checks = _job_check_rows(job_dir)
            check_rows.append(checks)
            run = {**_arm_from_result(task_dir, job_dir, result_path, result), **run_inputs}
            if checks is not None:
                run["checks"] = checks
            if context is not None:
                receipt = _workspace._load_object(_run_input_path(task_dir, job_dir), label="run inputs")
                try:
                    run["construction_execution"] = construction.execution(
                        task_dir, result_path, context, receipt["control_id"]
                    )
                except (ValueError, OSError, KeyError, TypeError, RecursionError) as error:
                    raise _workspace.ContractError(
                        "missing, malformed or stale construction execution evidence"
                    ) from error
            runs[arm].append(run)
    if copy_job_dirs and any(rows is None for rows in check_rows):
        raise _workspace.ContractError(
            "copy probes require per-check rows in every proof job; aggregate-only evidence is insufficient"
        )
    if any(rows is None for rows in check_rows):
        if any(rows is not None for rows in check_rows):
            raise _workspace.ContractError(
                "either every proof job or none must retain /logs/verifier/results check rows"
            )
        if not allow_aggregate_only:
            raise _workspace.ContractError(
                "missing_check_evidence: proof jobs retain no per-check rows; author the per-check grammar "
                "(references/check-grammar.md) and rerun, or record historical proof with --allow-aggregate-only"
            )
        check_evidence = "aggregate_only"
    else:
        check_evidence = "per_check"
        expected_ids = [{row["check_id"] for row in rows} for rows in check_rows if rows is not None]
        if any(ids != expected_ids[0] for ids in expected_ids):
            raise _workspace.ContractError("all proof jobs must report the same per-check ID set")
        for run in runs["nop"]:
            passed_checks = [row["check_id"] for row in run["checks"] if row["status"] == "PASS"]
            if passed_checks:
                raise _workspace.ContractError(
                    f"untouched environment passes scored checks: {', '.join(passed_checks)}"
                )
        for run in runs["oracle"]:
            failed_checks = [row["check_id"] for row in run["checks"] if row["status"] != "PASS"]
            if failed_checks and run["reward"] == 1:
                raise _workspace.ContractError(
                    f"oracle reward 1 conflicts with failing checks: {', '.join(failed_checks)}"
                )
        for arm in ("negative", "copy", "alternative", "bypass"):
            for run in runs.get(arm, []):
                if run["reward"] in (0, 1) and (run["reward"] == 1) != all(
                    row["status"] == "PASS" for row in run["checks"]
                ):
                    raise _workspace.ContractError(f"{arm} control reward conflicts with per-check results")
    job_dirs = [run["job_dir"] for arm_runs in runs.values() for run in arm_runs]
    job_ids = [run["job_id"] for arm_runs in runs.values() for run in arm_runs]
    require_distinct(job_dirs, "validation Harbor job directories must be distinct", _workspace.ContractError)
    require_distinct(job_ids, "validation Harbor config.job_id values must be distinct", _workspace.ContractError)
    checksums = {trial.get("task_checksum") for trial in trials}
    retained_checksum = next(iter(checksums))
    if (
        len(checksums) != 1
        or not isinstance(retained_checksum, str)
        or _workspace._TASK_CHECKSUM.fullmatch(retained_checksum) is None
    ):
        raise _workspace.ContractError("all validation Harbor results must have one matching task checksum")
    modes = {trial.get("verifier_environment_mode") for trial in trials}
    if modes != {"separate"}:
        raise _workspace.ContractError("all validation Harbor results must report separate verifier mode")
    mode = next(iter(modes))
    if mode != task_contract["verifier_environment_mode"]:
        raise _workspace.ContractError("Harbor verifier environment mode differs from task/task.toml")
    expected_rewards = {"nop": 0, "oracle": 1, "negative": 0, "copy": 0, "alternative": 1, "bypass": 0}
    passed = all(
        run["reward"] == expected_rewards[arm] and not run["exception_present"]
        for arm, arm_runs in runs.items()
        for run in arm_runs
    )
    qualification = None
    if context is not None:
        try:
            qualification = construction.qualify(context, runs)
        except (construction.ConstructionError, KeyError, TypeError) as error:
            raise _workspace.ContractError(f"construction qualification failed: {error}") from error
        passed = passed and qualification["status"] == "passed"
    report = {
        "schema": _workspace.QUALIFIED_VALIDATION_SCHEMA if context is not None else _workspace.VALIDATION_SCHEMA,
        "harbor_version": harbor_version,
        "task_checksum": retained_checksum,
        "task_tree_sha256": reproducibility["task_tree_sha256"],
        "verifier_environment_mode": mode,
        "distinct_jobs": True,
        "container_freshness": "unverified",
        "minimum_runs": minimum_runs,
        "check_evidence": check_evidence,
        "passed": passed,
        "runs": runs,
    }
    if qualification is not None:
        report["qualification"] = qualification
    return report


def _copy_probe_summary(validation: dict[str, Any]) -> dict[str, Any]:
    runs = validation["runs"]["copy"]
    return {
        "status": "recorded" if runs else "not_run",
        "runs": [
            {
                "reward": run["reward"],
                "exception_present": run["exception_present"],
                "passed_check_ids": [row["check_id"] for row in run["checks"] if row["status"] == "PASS"],
            }
            for run in runs
        ],
    }


def _record_validation(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _workspace._ensure_task_dir(args.task_dir)
    validation_path = task_dir / "validation.json"
    if validation_path.exists():
        raise _workspace.ContractError("refusing to replace existing validation.json")
    validation = _validation_from_jobs(
        task_dir,
        args.nop_job_dir,
        args.oracle_job_dir,
        args.negative_job_dir,
        args.harbor_version,
        allow_aggregate_only=args.allow_aggregate_only,
        copy_job_dirs=args.copy_job_dir,
        alternative_job_dirs=args.alternative_job_dir,
        bypass_job_dirs=args.bypass_job_dir,
    )
    _workspace._write_json(validation_path, validation)
    return {
        "task_dir": str(task_dir),
        "passed": validation["passed"],
        "qualification": validation.get("qualification", {"status": "unmeasured"}),
        "nop_rewards": [run["reward"] for run in validation["runs"]["nop"]],
        "oracle_rewards": [run["reward"] for run in validation["runs"]["oracle"]],
        "negative_rewards": [run["reward"] for run in validation["runs"]["negative"]],
        "copy_probes": _copy_probe_summary(validation),
        "verifier_environment_mode": validation["verifier_environment_mode"],
    }


def _validate_validation(task_dir: Path) -> dict[str, Any]:
    recorded = _workspace._load_object(task_dir / "validation.json", label="environment validation")
    expected_keys = {
        "schema",
        "harbor_version",
        "task_checksum",
        "task_tree_sha256",
        "verifier_environment_mode",
        "distinct_jobs",
        "container_freshness",
        "minimum_runs",
        "check_evidence",
        "passed",
        "runs",
    }
    qualified = recorded.get("schema") == _workspace.QUALIFIED_VALIDATION_SCHEMA
    minimum_runs = dict(_workspace._MIN_VALIDATION_RUNS)
    if qualified:
        expected_keys.add("qualification")
        minimum_runs.update(alternative=1, bypass=0)
    if set(recorded) != expected_keys or recorded.get("schema") not in (
        _workspace.VALIDATION_SCHEMA,
        _workspace.QUALIFIED_VALIDATION_SCHEMA,
    ):
        raise _workspace.ContractError("validation fields do not match the versioned contract")
    if not isinstance(recorded.get("harbor_version"), str) or not recorded["harbor_version"].strip():
        raise _workspace.ContractError("validation.harbor_version must be nonempty text")
    if (
        recorded.get("minimum_runs") != minimum_runs
        or recorded.get("distinct_jobs") is not True
        or recorded.get("container_freshness") != "unverified"
        or recorded.get("check_evidence") not in ("per_check", "aggregate_only")
    ):
        raise _workspace.ContractError(
            "validation repeat and job-evidence policy does not match the versioned contract"
        )
    runs = recorded.get("runs")
    if not isinstance(runs, dict) or set(runs) != set(minimum_runs):
        raise _workspace.ContractError("validation.runs fields do not match the versioned contract")
    for arm, minimum in minimum_runs.items():
        values = runs.get(arm)
        if not isinstance(values, list) or len(values) < minimum:
            raise _workspace.ContractError(f"validation.runs.{arm} does not meet the minimum run count")
        required_run_keys = {
            "job_id",
            "job_dir",
            "result_path",
            "result_sha256",
            "reward",
            "exception_present",
            "agent",
            "run_inputs_path",
            "run_inputs_sha256",
        }
        if qualified:
            required_run_keys.add("construction_execution")
        for value in values:
            if not isinstance(value, dict) or not required_run_keys.issubset(set(value)):
                raise _workspace.ContractError(f"validation.runs.{arm} fields do not match the versioned contract")
            if not set(value).issubset(required_run_keys | {"checks"}):
                raise _workspace.ContractError(f"validation.runs.{arm} carries fields outside the versioned contract")
    derived = _validation_from_jobs(
        task_dir,
        [run["job_dir"] for run in runs["nop"]],
        [run["job_dir"] for run in runs["oracle"]],
        [run["job_dir"] for run in runs["negative"]],
        recorded["harbor_version"],
        allow_aggregate_only=recorded["check_evidence"] == "aggregate_only",
        copy_job_dirs=[run["job_dir"] for run in runs["copy"]],
        alternative_job_dirs=[run["job_dir"] for run in runs.get("alternative", [])],
        bypass_job_dirs=[run["job_dir"] for run in runs.get("bypass", [])],
    )
    if recorded != derived:
        raise _workspace.ContractError("validation.json differs from the retained Harbor result evidence")
    return recorded


if __name__ == "__main__":
    raise SystemExit(main())
