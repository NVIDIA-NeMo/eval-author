#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CI reporting outside the byte-bound, reviewed semantic measurement pipeline."""

import argparse
import re
from pathlib import Path

import semantic_regression as semantic


def preflight(suite, runtime, suite_root):
    failures = {}
    for case in suite["cases"]:
        try:
            lock_path = semantic.contained(suite_root, case["baseline"])
            lock = semantic.load(lock_path)
            expected = semantic.binding(suite, case, runtime)
            if lock["schema"] != "nemo.semantic_lock.v1" or lock["binding"] != expected:
                # Only field names from our own binding are public; never values from the bundle.
                fields = [key for key in expected if lock["binding"].get(key) != expected[key]]
                failures[case["id"]] = ("baseline_binding_mismatch", fields)
                continue
            baseline = semantic.contained(lock_path.parent, "baseline.json")
            if semantic.sha(baseline.read_bytes()) != lock["baseline_digest"]:
                failures[case["id"]] = ("baseline_digest_mismatch", [])
            elif not re.fullmatch(r"[0-9a-f]{64}", lock["reviewed_mapping_digest"]):
                failures[case["id"]] = ("baseline_review_missing", [])
            elif semantic.load(baseline)["N"] != suite["runs"]:
                failures[case["id"]] = ("baseline_run_count_mismatch", [])
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            failures[case["id"]] = ("baseline_input_error", [])
    return failures


def run(suite_path, runtime, output, mode):
    suite = semantic.load(suite_path)
    semantic.validate_suite(suite)
    failures = preflight(suite, runtime, suite_path.parent) if mode == "check" else {}
    report = semantic.run(suite_path, runtime, output, "plan" if failures else mode)
    report["repository"] = "NVIDIA-NeMo/eval-author"
    report["mode"] = mode
    if failures:
        for row in report["observations"]:
            reason, fields = failures.get(row["case_id"], ("not_run", []))
            row.update(reason=reason)
            if fields:
                row["binding_mismatch_fields"] = fields
        for reason, fields in failures.values():
            print(f"::error::{reason}" + (": " + ", ".join(fields) if fields else ""))
    semantic.publish(output, report)
    with (output / "semantic-regression-summary.md").open("a") as summary:
        for row in report["observations"]:
            # Reasons are fixed codes from this runner or the measurement pipeline.
            summary.write(f"\n- {row['case_id']}: {row['reason']}")
            if row.get("binding_mismatch_fields"):
                summary.write(" (" + ", ".join(row["binding_mismatch_fields"]) + ")")
        summary.write("\n")
    if mode == "plan":
        return 0
    if any(row["status"] == "incomplete" for row in report["observations"]):
        return 2
    return 1 if any(row["status"] == "regression" for row in report["observations"]) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "check"), default="plan")
    parser.add_argument("--suite", type=Path, default=semantic.ROOT / "evals/semantic/pilot.json")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "check" and args.runtime is None:
        parser.error("--runtime required for check")
    return run(args.suite.resolve(), args.runtime.resolve() if args.runtime else None, args.output.resolve(), args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
