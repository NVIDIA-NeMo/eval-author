# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic task-proof checking, independent of authoring and campaigns."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from generation_benchmark.eval_author.ports.errors import ExperimentError
from generation_benchmark.eval_author.ports.io import write_json

from .harbor import HarborTrace, validate_harbor_trial
from .identity import file_digest, tree_digest
from .suite_tasks import discover_suite

_LEDGER_ENTRY = re.compile(r"[0-9a-f]{64}\.json$")


def _generation_workspace(draft: Path) -> Path:
    """Return the generation work cell root that owns ``draft`` under ``.eval-author``."""
    parts = draft.resolve().parts
    if ".eval-author" not in parts:
        raise ExperimentError(f"draft is not under a generation workspace: {draft}")
    index = parts.index(".eval-author")
    return Path(*parts[:index])


def _closure_evidence_paths(workspace: Path, task_slug: str) -> list[Path]:
    """Collect closure receipts from the newest revision that has a complete attempt ledger."""
    measurements = workspace / ".eval-author" / "task-measurements" / task_slug
    revisions = _revision_manifests_with_ledgers(measurements)
    if not revisions:
        raise ExperimentError(
            f"no revision manifest with a complete attempt ledger under {measurements}"
        )
    errors: list[str] = []
    for revision in reversed(revisions):
        try:
            return _closure_receipts_from_ledger(revision)
        except ExperimentError as exc:
            errors.append(f"{revision.name}: {exc}")
    detail = "; ".join(errors)
    raise ExperimentError(
        "no closure receipts recorded for this task revision"
        + (f" ({detail})" if detail else "")
    )


def _revision_manifests_with_ledgers(measurements: Path) -> list[Path]:
    revisions = sorted(measurements.glob("revision-*.json"))
    usable: list[Path] = []
    for revision in revisions:
        try:
            manifest = json.loads(revision.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        ledger_value = manifest.get("attempts")
        if not isinstance(ledger_value, str) or not ledger_value.strip():
            continue
        ledger = Path(ledger_value).expanduser().resolve()
        if ledger.is_symlink() or not ledger.is_dir():
            continue
        usable.append(revision)
    return usable


def _closure_receipts_from_ledger(revision: Path) -> list[Path]:
    try:
        manifest = json.loads(revision.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read revision manifest {revision}: {exc}") from exc
    ledger_value = manifest.get("attempts")
    if not isinstance(ledger_value, str) or not ledger_value.strip():
        raise ExperimentError("revision manifest lacks attempts ledger")
    ledger = Path(ledger_value).expanduser().resolve()
    if ledger.is_symlink() or not ledger.is_dir():
        raise ExperimentError(f"attempt ledger is not an ordinary directory: {ledger}")
    receipts: list[Path] = []
    for entry in sorted(ledger.iterdir()):
        if not _LEDGER_ENTRY.fullmatch(entry.name):
            continue
        try:
            attempt = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExperimentError(f"cannot read attempt ledger entry {entry}: {exc}") from exc
        if attempt.get("purpose") != "closure":
            continue
        receipt = Path(str(attempt.get("receipt", ""))).expanduser().resolve()
        if not receipt.is_file():
            raise ExperimentError(f"closure receipt is missing: {receipt}")
        receipts.append(receipt)
    if not receipts:
        raise ExperimentError("no closure receipts recorded for this task revision")
    if len({path for path in receipts}) != len(receipts):
        raise ExperimentError("closure receipt paths must be distinct")
    return receipts


def verify_task(
    *,
    draft: Path,
    oracle: Path,
    repeats: list[Path],
    before_report: Path,
    audit: Path,
    audit_skill: Path,
    task_pipeline: Path,
    expected_model: str | None,
    expected_task: str,
    target: str,
    output: Path,
    skip_gap_verify: bool = False,
) -> dict[str, Any]:
    """Check existing Oracle and two distinct native-ATIF trials; never launch an agent."""
    draft, oracle, output = (path.expanduser().resolve() for path in (draft, oracle, output))
    repeats = [path.expanduser().resolve() for path in repeats]
    if len(repeats) != 2 or len(set(repeats)) != 2:
        raise ExperimentError("task proof requires two distinct repeat directories")
    for source in (draft, oracle, *repeats, before_report.resolve(), audit.resolve()):
        if output == source or output in source.parents or source in output.parents:
            raise ExperimentError("proof output must be separate from every input")
    tasks = discover_suite(draft)
    if len(tasks) != 1 or tasks[0].path != draft or tasks[0].name != expected_task:
        raise ExperimentError("draft must contain exactly the expected task at its root")
    required = (
        "instruction.md",
        "environment/Dockerfile",
        "tests/test.sh",
        "solution/solve.sh",
        "README.md",
    )
    missing = [name for name in required if not (draft / name).is_file()]
    if missing:
        raise ExperimentError("draft is missing required files: " + ", ".join(missing))
    oracle_proof = _validate_oracle(oracle)
    if json.loads((oracle / "result.json").read_text()).get("task_name") != expected_task:
        raise ExperimentError("Oracle task identity differs from the draft")
    validated = []
    reports: list[Path] = []
    receipts: list[dict[str, Any]] = []
    if skip_gap_verify:
        for index, repeat in enumerate(repeats, 1):
            trial = _validate_result_only_repeat(repeat, task_name=expected_task)
            validated.append(trial)
            name = f"repeat-{index}"
            receipts.append(
                {
                    "name": name,
                    "trial_dir": str(trial.trial_dir),
                    "trajectory": str(trial.trajectory),
                    "trajectory_digest": file_digest(trial.trajectory),
                    "reward": trial.reward,
                }
            )
    else:
        sessions = set()
        trajectories = set()
        for repeat in repeats:
            trial = validate_harbor_trial(repeat, expected_model=expected_model)
            result = json.loads(trial.result.read_text())
            if result.get("task_name") != expected_task:
                raise ExperimentError("repeat task identity differs from the draft")
            if trial.reward != 1.0:
                raise ExperimentError(f"repeat verifier reward is {trial.reward!r}, expected 1.0")
            payload = json.loads(trial.trajectory.read_text())
            session = payload.get("session_id")
            trajectory = payload.get("trajectory_id")
            if not session or not trajectory:
                raise ExperimentError(
                    "repeat must record native ATIF session and trajectory identities"
                )
            if session in sessions or trajectory in trajectories:
                raise ExperimentError("repeats reuse the same session or trajectory")
            sessions.add(session)
            trajectories.add(trajectory)
            validated.append(trial)
        for index, trial in enumerate(validated, 1):
            name = f"repeat-{index}"
            reports.append(
                _measure_repeat(
                    audit_skill=audit_skill,
                    audit=audit,
                    trial_dir=trial.trial_dir,
                    task_id=draft.name,
                    run_id=name,
                    output=output / name,
                )
            )
            receipts.append(
                {
                    "name": name,
                    "trial_dir": str(trial.trial_dir),
                    "trajectory": str(trial.trajectory),
                    "trajectory_digest": file_digest(trial.trajectory),
                    "reward": trial.reward,
                }
            )
    workspace = _generation_workspace(draft)
    evidence = _closure_evidence_paths(workspace, draft.name)
    if skip_gap_verify:
        verification = {
            "schema": "nemo.eval_author.task_gap_verification.v2",
            "accepted": True,
            "skipped": "generation-benchmark-poc-oracle-repeats",
        }
    else:
        verification = _run_verify(
            task_pipeline,
            before=before_report,
            after=reports,
            target=target,
            evidence=evidence,
        )
    verification_path = output / "verification.json"
    result = {
        "schema": "nemo.eval_author.task_proof.v1",
        "draft_digest": tree_digest(draft),
        "before_report_digest": file_digest(before_report),
        "audit_digest": file_digest(audit),
        "task_create_skill_digest": tree_digest(task_pipeline.parent.parent),
        "audit_skill_digest": tree_digest(audit_skill),
        "oracle": oracle_proof,
        "repeats": receipts,
        "after_reports": [str(path) for path in reports],
        "verification": verification,
        "verification_path": str(verification_path),
        "technically_passed": True,
        "finalized": verification["accepted"],
    }
    write_json(verification_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval-task-proof", description="Check existing task evidence without model calls"
    )
    for name in ("draft", "oracle", "before", "audit", "nemo-platform", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--repeat", type=Path, action="append", required=True)
    for name in ("model", "task-name", "target"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    skills = args.nemo_platform / "plugins" / "nemo-eval-author" / "skills"
    try:
        result = verify_task(
            draft=args.draft,
            oracle=args.oracle,
            repeats=args.repeat,
            before_report=args.before,
            audit=args.audit,
            audit_skill=skills / "eval-author-audit",
            task_pipeline=skills / "eval-author-task-create" / "scripts" / "task_pipeline.py",
            expected_model=args.model,
            expected_task=args.task_name,
            target=args.target,
            output=args.output,
        )
    except (ExperimentError, OSError) as exc:
        print(f"eval-task-proof: error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"accepted": result["finalized"], "proof": result["verification_path"]}, sort_keys=True
        )
    )
    return 0 if result["finalized"] else 1


def _validate_result_only_repeat(repeat: Path, *, task_name: str) -> HarborTrace:
    """Repeat check: verifier outcome only (oracle-style Harbor runs without ATIF)."""
    result_path = repeat / "result.json"
    if not result_path.is_file():
        raise ExperimentError("Harbor trial is missing result.json or agent/trajectory.json")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read repeat result {result_path}: {exc}") from exc
    if result.get("exception_info") is not None:
        raise ExperimentError(f"Harbor recorded an exception: {result['exception_info']}")
    if result.get("task_name") != task_name:
        raise ExperimentError("repeat task identity differs from the draft")
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    raw_reward = rewards.get("reward")
    reward = float(raw_reward) if isinstance(raw_reward, (int, float)) else None
    if reward != 1.0:
        raise ExperimentError(f"repeat verifier reward is {reward!r}, expected 1.0")
    trajectory_path = repeat / "agent" / "trajectory.json"
    if not trajectory_path.is_file():
        trajectory_path = result_path
    return HarborTrace(
        trial_dir=repeat,
        trajectory=trajectory_path,
        result=result_path,
        model="",
        reward=reward,
    )


def _measure_repeat(
    *,
    audit_skill: Path,
    audit: Path,
    trial_dir: Path,
    task_id: str,
    run_id: str,
    output: Path,
) -> Path:
    """Remeasure one native ATIF trial independently of generator-authored evidence."""
    coverage_dir = output / "coverage"
    report = output / "coverage-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(audit_skill / "scripts" / "audit_spec" / "measure.py"),
            "--audit",
            str(audit),
            "--trial-dir",
            str(trial_dir),
            "--task-id",
            task_id,
            "--run-id",
            run_id,
            "--measure",
            "tool_calls",
            "--out-dir",
            str(coverage_dir),
            "--compact",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"native ATIF measurement failed for {run_id}: {detail}")
    completed = subprocess.run(
        [
            sys.executable,
            str(audit_skill / "scripts" / "audit_spec" / "report.py"),
            "--audit",
            str(audit),
            "--coverage-dir",
            str(coverage_dir),
            "--out",
            str(report),
            "--compact",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"audit aggregation failed for {run_id}: {detail}")
    return report


def _validate_oracle(path: Path) -> dict[str, Any]:
    result_path = path / "result.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read Oracle result {result_path}: {exc}") from exc
    if result.get("exception_info") is not None:
        raise ExperimentError(f"Oracle recorded an exception: {result['exception_info']}")
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    if reward != 1.0:
        raise ExperimentError(f"Oracle verifier reward is {reward!r}, expected 1.0")
    return {"trial_dir": str(path.resolve()), "result": str(result_path.resolve()), "reward": 1.0}


def _run_verify(
    task_pipeline: Path,
    *,
    before: Path,
    after: list[Path],
    target: str,
    evidence: list[Path] | None = None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(task_pipeline),
        "verify",
        "--before",
        str(before),
        "--target",
        target,
    ]
    for report in after:
        command.extend(("--after", str(report)))
    for receipt in evidence or []:
        command.extend(("--evidence", str(receipt)))
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"cannot parse task-create verify output: {exc}") from exc
    if not isinstance(payload.get("accepted"), bool):
        raise ExperimentError(f"task-create verify returned no verdict for {target!r}: {payload}")
    if completed.returncode != 0 and payload["accepted"]:
        raise ExperimentError("task-create verify failed despite returning an accepted verdict")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
