#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Account for independent semantic comparison batches without recalibrating a baseline."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from discovery_determinism import encoded, loads, sha
from semantic_regression import contained, digest, validate_suite


def check(suite_path: Path, paths: list[Path]) -> dict:
    suite_raw = suite_path.read_bytes()
    suite = loads(suite_raw)
    validate_suite(suite)
    if len(paths) < 2 or len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("two or more distinct retained comparison batches are required")
    expected_cases = {case["id"] for case in suite["cases"]}
    condition = None
    baselines, comparisons, retained, findings = {}, {}, [], []
    counts = {"passed": 0, "regression": 0, "incomplete": 0}
    for path in paths:
        raw = path.read_bytes()
        report = loads(raw)
        retained.append({"path": str(path.resolve()), "sha256": sha(raw)})
        if (
            report["mode"] != "check"
            or report["suite_digest"] != sha(suite_raw)
            or report["runs"] != suite["runs"]
            or report["models"] != suite["models"]
            or not report.get("source_revision")
            or report.get("inputs_clean") is not True
        ):
            raise ValueError("batch must use the frozen suite, clean source revision, models and check mode")
        identity = encoded([report["source_revision"], report["models"], report["suite_digest"], report["runs"]])
        if condition is not None and condition != identity:
            raise ValueError("batch input conditions changed")
        condition = identity
        rows = report["observations"]
        if len(rows) != len(expected_cases) or {row["case_id"] for row in rows} != expected_cases:
            raise ValueError("batch omitted or duplicated a planned case")
        for row in rows:
            case, status = row["case_id"], row["status"]
            if status not in counts:
                raise ValueError("unknown semantic verdict")
            if status != "incomplete":
                baseline, comparison = row.get("baseline_digest"), row.get("comparison_digest")
                batch = row.get("batch_digest")
                if not baseline or not comparison or not batch or row.get("metrics") is None:
                    raise ValueError("complete semantic verdict lacks baseline, comparison or metrics")
                gate_raw = contained(path.parent, f"{case}/gate-result.json").read_bytes()
                gate = loads(gate_raw)
                if sha(gate_raw) != row.get("gate_digest") or encoded({key: row.get(key) for key in gate}) != encoded(
                    gate
                ):
                    raise ValueError("summary verdict or metrics differ from retained gate result")
                case_spec = next(item for item in suite["cases"] if item["id"] == case)
                lock_path = contained(suite_path.parent, case_spec["baseline"])
                lock = loads(lock_path.read_bytes())
                if (
                    lock.get("schema") != "nemo.semantic_lock.v1"
                    or digest(lock) != comparison
                    or not re.fullmatch(r"[0-9a-f]{64}", lock.get("reviewed_mapping_digest", ""))
                    or sha(contained(lock_path.parent, "baseline.json").read_bytes()) != baseline
                    or lock.get("baseline_digest") != baseline
                ):
                    raise ValueError("retained reviewed baseline differs from comparison")
                batch_path = contained(path.parent, f"{case}/batch.json")
                batch_raw = batch_path.read_bytes()
                accounting = loads(batch_raw)
                if sha(batch_raw) != batch or len(accounting["runs"]) != suite["runs"]:
                    raise ValueError("retained batch differs from summary or run denominator")
                run_ids = []
                for run in accounting["runs"]:
                    run_id = run["run_id"]
                    if not re.fullmatch(r"[a-f0-9]{32}-r[1-9][0-9]*", run_id) or run["status"] != "completed":
                        raise ValueError("incomplete or invalid batch run")
                    if (
                        sha(contained(batch_path.parent, f"{run_id}.txt").read_bytes()) != run["report_sha256"]
                        or sha(contained(batch_path.parent, f"{run_id}.json").read_bytes()) != run["claims_sha256"]
                    ):
                        raise ValueError("retained report or claims changed")
                    run_ids.append(run_id)
                if len(set(run_ids)) != len(run_ids):
                    raise ValueError("duplicate run identities")
                if set(run_ids) & comparisons.setdefault(case + ":runs", set()):
                    raise ValueError("run identities reused across batches")
                comparisons[case + ":runs"].update(run_ids)
                if case in baselines and baselines[case] != (baseline, comparison):
                    raise ValueError("baseline changed between independent batches")
                if batch in comparisons.setdefault(case, set()):
                    raise ValueError("comparison reused; copied results are not independent batches")
                baselines[case] = (baseline, comparison)
                comparisons[case].add(batch)
            counts[status] += 1
            findings.append({"batch": str(path), "case": case, "status": status})
    status = "regression" if counts["regression"] else "incomplete" if counts["incomplete"] else "passed"
    return {
        "schema": "nemo.semantic_batches.v1",
        "status": status,
        "gate": "advisory",
        "batch_count": len(paths),
        "planned_observations": len(paths) * len(expected_cases),
        "counts": counts,
        "observations": findings,
        "retained": retained,
        "task_quality": "not_measured",
        "factual_correctness": "not_measured",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("batches", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        result = check(args.suite, args.batches)
        print(json.dumps(result, indent=2, sort_keys=True))
        return {"passed": 0, "regression": 1, "incomplete": 2}[result["status"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
