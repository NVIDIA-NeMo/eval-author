#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CI reporting and safe diagnostics for fixed semantic baselines."""

import argparse
import re
from pathlib import Path

import semantic_regression as semantic


def recorded_revision(suite_root, case):
    """Optional provenance, independent of measurement compatibility or validity."""
    try:
        lock = semantic.load(semantic.contained(suite_root, case["baseline"]))
        revision = lock.get("source_revision")
        if isinstance(revision, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
            return revision
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        pass
    return None


def preflight(suite, runtime, suite_root):
    failures = {}
    for case in suite["cases"]:
        try:
            lock_path = semantic.contained(suite_root, case["baseline"])
            lock = semantic.load(lock_path)
            actual = semantic.normalize_binding(lock["binding"])
            expected = semantic.binding(suite, case, runtime)
            if lock["schema"] != "nemo.semantic_lock.v1" or actual != expected:
                # Only field names from our own binding are public; never values from the bundle.
                fields = [key for key in expected if actual.get(key) != expected[key]]
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


def run(suite_path, runtime, output, mode, *, warn_on_regression=False):
    suite = semantic.load(suite_path)
    semantic.validate_suite(suite)
    failures = preflight(suite, runtime, suite_path.parent) if mode == "check" else {}
    report = semantic.run(suite_path, runtime, output, "plan" if failures else mode)
    report["repository"] = "NVIDIA-NeMo/eval-author"
    report["mode"] = mode
    for case, row in zip(suite["cases"], report["observations"], strict=True):
        row["baseline_source_revision"] = recorded_revision(suite_path.parent, case)
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
        summary.write(f"\nCandidate source revision: `{report['source_revision']}`\n")
        summary.write("\n| Case | Recorded baseline source revision |\n| --- | --- |\n")
        for row in report["observations"]:
            revision = row["baseline_source_revision"]
            value = f"`{revision}`" if revision else "unavailable"
            summary.write(f"| {row['case_id']} | {value} |\n")
        for row in report["observations"]:
            # Reasons are fixed codes from this runner or the measurement pipeline.
            summary.write(f"\n- {row['case_id']}: {row['reason']}")
            if row.get("binding_mismatch_fields"):
                summary.write(" (" + ", ".join(row["binding_mismatch_fields"]) + ")")
        summary.write("\n")
        for row in report["observations"]:
            metrics = row.get("metrics")
            if metrics:
                summary.write(
                    f"\n- {row['case_id']}: retention {metrics['retention']:.1%} "
                    f"(minimum {metrics['retention_threshold']:.1%}); "
                    f"consistency {metrics['consistency']:.1%} "
                    f"(baseline {metrics['baseline_consistency']:.1%}, "
                    f"allowed drop {metrics['allowed_drop'] * 100:.1f} percentage points); "
                    f"presence check {'passed' if metrics['presence_passed'] else 'failed'}.\n"
                )
        if mode == "check" and warn_on_regression:
            summary.write(
                "\nCI treats measured regressions as warnings, not passing measurements. "
                "Artifact verdicts and measurement exit codes are unchanged. "
                "Incomplete evidence still fails the comparison step.\n"
            )
    if mode == "plan":
        return 0
    regressions = [row for row in report["observations"] if row["status"] == "regression"]
    if warn_on_regression:
        for row in regressions:
            print(f"::warning::{row['case_id']}: measured semantic regression; see the report summary.")
    if any(row["status"] == "incomplete" for row in report["observations"]):
        print("::error::Semantic comparison has incomplete evidence; see the report summary.")
        return 2
    return 1 if regressions and not warn_on_regression else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "check"), default="plan")
    parser.add_argument(
        "--warn-on-regression",
        action="store_true",
        help="Report measured regressions as CI warnings; incomplete evidence still fails",
    )
    parser.add_argument("--suite", type=Path, default=semantic.ROOT / "evals/semantic/pilot.json")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "check" and args.runtime is None:
        parser.error("--runtime required for check")
    return run(
        args.suite.resolve(),
        args.runtime.resolve() if args.runtime else None,
        args.output.resolve(),
        args.mode,
        warn_on_regression=args.warn_on_regression,
    )


if __name__ == "__main__":
    raise SystemExit(main())
