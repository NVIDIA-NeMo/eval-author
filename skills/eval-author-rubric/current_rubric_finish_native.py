# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "examples/finish-suite"
FAMILIES = ("interval-reservation", "dependency-release")
POLICIES = ("evidence", "first-template", "lexical")


def _load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("finish_" + hashlib.sha256(str(path).encode()).hexdigest(), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inventory(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("aliased_source")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "sha256": _sha(path.read_bytes()),
                "executable": bool(path.stat().st_mode & 0o111),
            }
    return result


def source_bindings() -> dict:
    bindings = {"templates": inventory(TEMPLATES), "refund": inventory(ROOT / "examples/refund/base")}
    bindings["producers"] = {
        name: _sha((ROOT / name).read_bytes())
        for name in ("current_rubric_finish_native.py", "current_rubric_finish_policies.py")
    }
    return bindings


def materialize(family: str, destination: Path, *, case: dict | None = None, grader: str = "mandatory-gated") -> dict:
    if family not in FAMILIES or grader not in {"mandatory-gated", "completion-only"}:
        raise ValueError("unsupported_family_or_grader")
    source = TEMPLATES / family
    selected_case = case if case is not None else json.loads((source / "environment/case.json").read_bytes())
    _load(source / "tests/verify.py").expected_choice(selected_case)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    (destination / "environment/case.json").write_bytes(_canonical(selected_case) + b"\n")
    if grader == "completion-only":
        path = destination / "tests/verify.py"
        code = path.read_text()
        needle = 'GRADER = "mandatory-gated"'
        if code.count(needle) != 1:
            raise ValueError("unsupported_grader_source")
        path.write_text(code.replace(needle, 'GRADER = "completion-only"'))
    return {
        "family": family,
        "scope": "S/new-family" if grader == "mandatory-gated" else "cohort/completion-only",
        "grader": grader,
        "files": inventory(destination),
        "native_execution": False,
    }


def _development_inputs() -> dict:
    inputs = {}
    for family in FAMILIES:
        case = json.loads((TEMPLATES / family / "environment/case.json").read_bytes())
        inputs[(family, "template")] = _canonical(case)
        changed = json.loads(_canonical(case))
        changed["id"] = family + "-exposed-development-02"
        if family == "interval-reservation":
            changed["occupied"] = [[3, 7]]
            changed["options"] = [{"id": "a", "interval": [-2, 3]}, {"id": "z", "interval": [5, 8]}]
        else:
            changed["states"] = {"build": "passed", "audit": "passed", "release": "revoked"}
            changed["options"] = [{"id": "a", "requires": []}, {"id": "z", "requires": ["release"]}]
        inputs[(family, "variation")] = _canonical(changed)
    base = json.loads((ROOT / "examples/refund/base/environment/contract.json").read_bytes())
    inputs[("refund", "template")] = _canonical(base)
    # Explicit development input change; the published refund rules and B files stay unchanged.
    variation = _load(ROOT / "examples/refund/base/tests/refund_scorer.py").contract_for_case(
        base,
        {
            "contract_variant": {
                "sources": {
                    "policy": {"refund_tax": False, "approval_threshold_cents": 5600},
                    "order": {"discount_cents": 2500},
                }
            }
        },
    )
    inputs[("refund", "variation")] = _canonical(variation)
    return inputs


def _development() -> list:
    policies = _load(ROOT / "current_rubric_finish_policies.py")
    rows = []
    for (family, input_id), raw in _development_inputs().items():
        scorer_path = (
            ROOT / "examples/refund/base/tests/refund_scorer.py"
            if family == "refund"
            else TEMPLATES / family / "tests/verify.py"
        )
        scorer = _load(scorer_path)
        for name in POLICIES:
            trace = policies.run_policy(name, family, raw)
            result = scorer.evaluate(json.loads(raw), trace["candidate"])
            rows.append(
                {
                    "family": family,
                    "input_id": input_id,
                    "public_input": json.loads(raw),
                    "policy": name,
                    "partition": "exposed_development",
                    "trace": trace,
                    "result": result,
                    "scope": "local_algorithm_not_native",
                    "scorer_sha256": _sha(scorer_path.read_bytes()),
                }
            )
    return rows


def select_policy(rows: list) -> dict:
    required = {(family, input_id, policy) for family, input_id in _development_inputs() for policy in POLICIES}
    keys = [(row["family"], row["input_id"], row["policy"]) for row in rows]
    if len(keys) != len(set(keys)) or set(keys) != required:
        raise ValueError("incomplete_development_feedback")
    totals = dict.fromkeys(POLICIES, 0)
    for row in rows:
        result = row["result"]
        if result["health"] != "ok" or type(result["accepted"]) is not bool:
            raise ValueError("development_error_no_selection")
        totals[row["policy"]] += int(result["accepted"])
    selected = max(POLICIES, key=lambda name: totals[name])
    return {
        "rule": "maximum_correct_then_fixed_catalog_order",
        "tie_order": list(POLICIES),
        "correct_counts": totals,
        "selected": selected,
        "comparator": "first-template",
        "claim": "finite confirmation correctness difference only; null or negative gain retained",
    }


def episode_plan() -> list:
    episodes = []
    policies = _load(ROOT / "current_rubric_finish_policies.py")
    for family in FAMILIES:
        raw = (TEMPLATES / family / "environment/case.json").read_bytes()
        case = json.loads(raw)
        valid = policies.run_policy("evidence", family, raw)["candidate"]
        wrong = next(option["id"] for option in case["options"] if option["id"] != valid["choice"])
        candidates = {
            "valid": valid,
            "invalid": {"choice": wrong, "success": True},
            "partial": {**valid, "success": False},
            "missing": None,
        }
        for control in ("valid", "invalid", "partial", "missing"):
            candidate = candidates[control]
            candidate_raw = _canonical(candidate) + b"\n" if candidate is not None else None
            episodes.append(
                {
                    "id": f"{family}/{control}",
                    "family": family,
                    "control": control,
                    "candidate": candidate,
                    "candidate_sha256": _sha(candidate_raw) if candidate_raw is not None else None,
                    "action": "write_exact_candidate_bytes" if candidate is not None else "leave_candidate_absent",
                    "consumer": "/logs/verifier/reward.txt",
                    "expected_task_credit": {"valid": 3, "invalid": 0, "partial": 2, "missing": 0}[control],
                    "expected_max_credit": 3,
                    "expected_health": "ok",
                    "task": f"tasks/{family}",
                    "scoring_path": "Harbor-full-Trial/root-test.sh",
                    "scope": "S/new-family-viability",
                    "status": "not_run",
                    "attempt_limit": 1,
                    "hard_timeout_sec": 180,
                    "reward": None,
                    "trial_id": None,
                    "container_id": None,
                }
            )
    return episodes


def _preregistration() -> dict:
    return {
        "schema": "finish-suite-local-preregistration-v1",
        "bindings": source_bindings(),
        "candidate_order": list(POLICIES),
        "selection_rule": "maximum_correct_then_fixed_catalog_order",
        "development_inputs": {"/".join(k): _sha(v) for k, v in _development_inputs().items()},
        "exposure": "All development inputs and algorithms are exposed; no held-out cases included.",
        "confirmation": {
            "status": "awaiting_independent_custody",
            "cases": [],
            "openings": 0,
            "custodian": None,
            "access_denial_observed": None,
        },
        "exclusion_rule": "Report full finite cohort and exclude known-exposed cases in a separate sensitivity; unknown stays unknown.",
        "authority": None,
    }


def _document(preregistration: dict, rows: list, tasks: dict) -> dict:
    return {
        "schema": "finish-suite-prepared-v1",
        "scope": "S/B-rooted-additive-suite",
        "original_B": "unchanged; no inherited S credit or authority",
        "native_execution": False,
        "authorization": None,
        "rubric_scores": None,
        "preregistration_sha256": _sha(_canonical(preregistration)),
        "bindings": preregistration["bindings"],
        "tasks": tasks,
        "development": rows,
        "selection": select_policy(rows),
        "confirmation": preregistration["confirmation"],
        "episodes": episode_plan(),
        "budget": {
            "status": "proposal_not_authorized",
            "episodes": 8,
            "models": 0,
            "retries": 0,
            "peak_task_containers": 1,
            "batch_deadline_sec": 1800,
            "cleanup_reserve_sec": 120,
            "deadline_utc": None,
            "watchdog_owner": None,
            "native_budget_remaining": 0,
        },
        "required_before_native": [
            "Explicit renewed authorization for exact source-bound eight-slot manifest; this file is not approval.",
            "Independent host supervisor owns CREATE admission, unique run labels and exact container IDs before launch.",
            "Bind an absolute UTC deadline at approval; refuse admission after deadline minus cleanup reserve.",
            "180 seconds per full Trial, outer 1800 seconds including 120 seconds cleanup; no retries or extra cases.",
            "Supervisor survives client death, kills owned process groups, settles pending CREATE, removes only owned resources.",
            "Retain raw errors, per-check results, Harbor consumed rewards, actual UID/mount/runtime/source identities and teardown receipts.",
            "Use isolated credential-free HOME; preserve B and all frozen native evidence.",
            "No confirmation until independent custody, actual denial and one-opening guards exist; no local ledger claim substitutes.",
        ],
        "limits": [
            "Local algorithm checks and Harbor schema validity are not Trial execution or independent qualification.",
            "Whole-document reads are recorded honestly; naive policies are not cue-only restricted access.",
            "These eight viability slots do not cover T3 attacks/cues/reset/containment/confirmation or blind review.",
            "T3 must freeze its separately authored exact manifest within the DESIGN ceiling before requesting execution.",
        ],
    }


def prepare(destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True, mode=0o700)
    preregistration = _preregistration()
    with (destination / "preregistration.json").open("xb") as handle:
        handle.write(_canonical(preregistration) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    tasks = {family: materialize(family, destination / "tasks" / family) for family in FAMILIES}
    rows = _development()
    document = _document(preregistration, rows, tasks)
    with (destination / "manifest.json").open("xb") as handle:
        handle.write(_canonical(document) + b"\n")
    return document


def validate_prepared(destination: Path) -> dict:
    preregistration = json.loads((destination / "preregistration.json").read_bytes())
    document = json.loads((destination / "manifest.json").read_bytes())
    if preregistration != _preregistration():
        raise ValueError("preregistration_binding_mismatch")
    tasks = document["tasks"]
    if set(tasks) != set(FAMILIES):
        raise ValueError("task_binding_mismatch")
    for family in FAMILIES:
        expected = inventory(TEMPLATES / family)
        raw_case = _canonical(json.loads((TEMPLATES / family / "environment/case.json").read_bytes())) + b"\n"
        expected["environment/case.json"]["sha256"] = _sha(raw_case)
        metadata = {
            "family": family,
            "scope": "S/new-family",
            "grader": "mandatory-gated",
            "files": expected,
            "native_execution": False,
        }
        if tasks[family] != metadata or inventory(destination / "tasks" / family) != expected:
            raise ValueError("task_binding_mismatch")
    if document != _document(preregistration, _development(), tasks):
        raise ValueError("observation_binding_mismatch")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-only suite preparation; never launches Harbor or models")
    parser.add_argument("mode", choices=("prepare", "validate"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.output) if args.mode == "prepare" else validate_prepared(args.output)
    print(
        json.dumps(
            {
                "status": "prepared" if args.mode == "prepare" else "validated",
                "native_execution": False,
                "development_observations": len(result["development"]),
                "proposed_episodes": len(result["episodes"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
