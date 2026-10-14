#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retain discovery reports and compare decisions under explicitly frozen inputs.

No provider commands run here. Only the top-level discovered_at timestamp and
ordering of declared set-like collections are incidental. Sample/result order,
runtime identity, diagnostics, unknown fields and duplicate entries are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "nemo.discovery_comparison.v1"
IDENTITIES = ("repository", "skills", "policy", "fixture", "runtime")
COLLECTIONS = {"checks", "configs", "datasets", "gym_manifests", "other_eval_candidates"}


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path) -> Any:
    return loads(path.read_bytes())


def loads(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON value: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def projection(report: dict) -> dict:
    """Preserve the complete report, normalizing only reviewed incidental fields."""
    required = {
        "schema_version",
        "repo_root",
        "runtime",
        "phases",
        "checks",
        "configs",
        "datasets",
        "gym_manifests",
        "proven",
        "runnable",
        "task_count",
    }
    if not isinstance(report, dict) or required - report.keys():
        raise ValueError("not a complete discovery report")
    if not all(isinstance(report[key], list) for key in ("checks", "configs", "datasets", "gym_manifests")):
        raise ValueError("discovery collections must be arrays")
    if not isinstance(report["runtime"], dict) or not isinstance(report["phases"], dict):
        raise ValueError("runtime and phases must be objects")
    if type(report["runnable"]) is not bool or type(report["proven"]) is not bool:
        raise ValueError("runnable and proven must be booleans")

    def visit(value, path=()):
        if isinstance(value, dict):
            return {k: visit(v, (*path, k)) for k, v in sorted(value.items())}
        if isinstance(value, list):
            entries = [visit(v, (*path, "*")) for v in value]
            unordered = (len(path) == 1 and path[0] in COLLECTIONS) or (
                len(path) == 3 and path[0] in {"configs", "datasets", "gym_manifests"} and path[1:] == ("*", "checks")
            )
            return sorted(entries, key=encoded) if unordered else entries
        return value

    return visit({key: value for key, value in report.items() if key != "discovered_at"})


def capture(report_path: Path, provenance: dict, exit_status: int) -> dict:
    if not isinstance(provenance, dict) or any(not provenance.get(key) for key in IDENTITIES):
        raise ValueError(f"provenance must identify {', '.join(IDENTITIES)}")
    raw = report_path.read_bytes()
    report = loads(raw)
    decision = projection(report)
    if type(exit_status) is not int or exit_status != (0 if report["runnable"] else 1):
        raise ValueError("exit status does not match the discovery runnable verdict")
    return {
        "schema": SCHEMA,
        "provenance": provenance,
        "raw_report": str(report_path.resolve()),
        "raw_sha256": sha(raw),
        "exit_status": exit_status,
        "decision": decision,
    }


def validate_capture(record: dict) -> None:
    if record.get("schema") != SCHEMA:
        raise ValueError("unknown capture schema")
    rebuilt = capture(Path(record["raw_report"]), record["provenance"], record["exit_status"])
    if rebuilt != record:
        raise ValueError("capture or retained raw report changed")


def compare(records: list[dict], *, mutation: bool = False) -> dict:
    if len(records) < 2:
        raise ValueError("at least two retained attempts are required")
    if len({str(Path(record["raw_report"]).resolve()) for record in records}) != len(records):
        raise ValueError("each attempt requires a distinct retained raw report")
    for record in records:
        validate_capture(record)
    conditions = [encoded(record["provenance"]) for record in records]
    decisions = [encoded([record["exit_status"], record["decision"]]) for record in records]
    changed_inputs = len(set(conditions)) != 1
    changed_decisions = len(set(decisions)) != 1
    passed = (changed_inputs and changed_decisions) if mutation else (not changed_inputs and not changed_decisions)
    return {
        "schema": SCHEMA,
        "mode": "mutation" if mutation else "repeat",
        "passed": passed,
        "attempted": len(records),
        "inputs_changed": changed_inputs,
        "decisions_changed": changed_decisions,
        "raw_reports": [{"path": r["raw_report"], "sha256": r["raw_sha256"]} for r in records],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("capture")
    take.add_argument("--report", type=Path, required=True)
    take.add_argument("--provenance", type=Path, required=True)
    take.add_argument("--exit-status", type=int, required=True)
    repeat = commands.add_parser("compare")
    repeat.add_argument("captures", type=Path, nargs="+")
    repeat.add_argument("--mutation", action="store_true")
    args = parser.parse_args()
    try:
        result = (
            capture(args.report, read(args.provenance), args.exit_status)
            if args.command == "capture"
            else compare([read(path) for path in args.captures], mutation=args.mutation)
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result.get("passed", True) else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"passed": False, "incomplete": True, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
