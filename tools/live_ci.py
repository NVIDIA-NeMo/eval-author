# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Plan independent CI jobs and publish a complete, provenance-bound aggregate."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
from pathlib import Path

from collect_live_skill_evaluations import DIMENSIONS, collect, configuration, count, number, persist_report
from collect_skill_evaluations import now

SUMMARY = "live-skillevaluator-summary.json"
MAX_JOBS = 64
BINDINGS = ("schema", "repository", "source_revision", "inputs_clean", "configured_evaluator_revision", "ci")
METRICS = (
    "severity_counts",
    "raw_severity_counts",
    "excluded_license_findings",
    "arms",
    "dimension_lift",
    "pass_rate_lift",
)
ROW_FIELDS = {
    "tier",
    "skill",
    "check",
    "tree_digest",
    "cases",
    "status",
    "reason",
    "started_at",
    "finished_at",
    "exit_code",
    "report_digest",
    "diagnostics",
    *METRICS,
}


def reject_constant(value):
    raise ValueError("nonfinite JSON value")


def read_report(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or path.stat().st_size > 5_000_000:
        raise ValueError("linked or oversized report")
    return json.loads(path.read_text(), parse_constant=reject_constant)


def key(row):
    return row["tier"], row["skill"], row["check"]


def job_id(row):
    return row["check"] + "-" + row["skill"]


def jobs(report):
    entries = [
        {"id": job_id(row), "tier": str(row["tier"]), "skill": row["skill"], "check": row["check"]}
        for row in report["observations"]
        if row["status"] == "not_run"
    ]
    if len(entries) > MAX_JOBS:
        raise ValueError("CI matrix exceeds its bounded job count")
    return entries


def initialize(args):
    report = collect(
        args.repo,
        args.output,
        tier=args.tier,
        skill_name=args.skill,
        check_name=args.check,
        ci_context_budget=True,
    )
    report["policy"]["max_parallel_jobs"] = 2
    entries = jobs(report)
    if args.command == "plan":
        # GitHub rejects empty matrices, even if there is no work to execute.
        matrix = {"include": entries or [{"id": "none", "tier": "2", "skill": "all", "check": "context"}]}
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
                stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
        (args.output / "matrix.json").write_text(json.dumps(matrix, indent=2) + "\n")
    else:
        report["mode"] = "run"
        report["policy"]["models_resolved"] = args.models_resolved and all(
            re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", os.environ.get(name, ""))
            for name in ("SKILL_EVAL_LLM_MODEL", "SKILL_EVAL_EMBEDDING_MODEL", "SKILL_EVAL_AGENT_MODEL")
        )
        if args.check != "all":
            if len(entries) != 1:
                raise ValueError("a shard must select exactly one executable observation")
            report["ci_shard"] = entries[0]["id"]
        for row in report["observations"]:
            if row["status"] == "not_run":
                row.update(status="incomplete", reason="job_report_unavailable")
        report["finished_at"] = now()
    persist_report(report, args.output)
    return report


def expected_policy(base, row):
    policy = dict(base["policy"])
    policy.pop("max_parallel_jobs", None)
    policy.pop("models_resolved", None)
    policy.update(
        requested_tier=str(row["tier"]),
        requested_skill="all" if row["check"] == "similarity" else row["skill"],
        requested_check=row["check"],
    )
    return policy


def validated_row(candidate, base, expected):
    if candidate["mode"] != "run" or any(candidate[k] != base[k] for k in BINDINGS):
        raise ValueError("report provenance mismatch")
    if candidate["inputs_clean"] is not True:
        raise ValueError("dirty source inputs")
    policy = dict(candidate["policy"])
    policy.pop("max_parallel_jobs", None)
    policy.pop("models_resolved", None)
    if policy != expected_policy(base, expected):
        raise ValueError("report policy mismatch")
    rows = candidate["observations"]
    if len({key(row) for row in rows}) != len(rows):
        raise ValueError("duplicate observations")
    matches = [row for row in rows if key(row) == key(expected)]
    if len(matches) != 1:
        raise ValueError("missing assigned observation")
    row = copy.deepcopy({k: v for k, v in matches[0].items() if k in ROW_FIELDS})
    if row["reason"] is not None and (not isinstance(row["reason"], str) or len(row["reason"]) > 500):
        raise ValueError("invalid outcome reason")
    if "diagnostics" in row and (not isinstance(row["diagnostics"], str) or len(row["diagnostics"]) > 13_000):
        raise ValueError("invalid diagnostics")
    if any(row[k] != expected[k] for k in ("tree_digest", "cases")):
        raise ValueError("changed observation inputs")
    if row["status"] in ("not_run", "incomplete"):
        row["status"] = "incomplete"
        row["reason"] = row.get("reason") or "job_did_not_finish"
        for field in METRICS:
            row.pop(field, None)
        return row
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", row["report_digest"]):
        raise ValueError("missing native report digest")
    if row["tier"] == 2:
        if row["status"] not in ("passed", "failed") or row["exit_code"] not in (0, 1):
            raise ValueError("invalid Tier 2 outcome")
        counts = row["severity_counts"]
        if set(counts) != {"critical", "high", "medium", "low"}:
            raise ValueError("incomplete severity counts")
        for value in counts.values():
            count(value)
        if row["status"] == "passed" and counts["critical"] + counts["high"]:
            raise ValueError("contradictory Tier 2 outcome")
        excluded = count(row.get("excluded_license_findings", 0))
        if excluded:
            raw = row["raw_severity_counts"]
            if row["check"] != "context" or set(raw) != set(counts):
                raise ValueError("invalid exclusion scope")
            if any(count(raw[k]) != counts[k] + (excluded if k == "high" else 0) for k in counts):
                raise ValueError("invalid exclusion counts")
        if row["status"] == "passed" and row["exit_code"] != (1 if excluded else 0):
            raise ValueError("contradictory Tier 2 exit")
        if row["status"] == "failed" and row["exit_code"] != 1:
            raise ValueError("contradictory Tier 2 exit")
    else:
        if row["status"] != "completed" or row["exit_code"] != 0:
            raise ValueError("invalid Tier 3 outcome")
        arms = row["arms"]
        for name in ("with_skill", "without_skill"):
            arm = arms[name]
            total, passed = count(arm["total_cases"]), count(arm["passed_cases"])
            if total != expected["cases"] or total == 0 or passed > total:
                raise ValueError("changed Tier 3 denominator")
            if abs(number(arm["pass_rate"]) - passed / total) > 1e-9:
                raise ValueError("invalid pass rate")
            for dimension in DIMENSIONS:
                number(arm["dimensions"][dimension])
        expected_lift = arms["with_skill"]["pass_rate"] - arms["without_skill"]["pass_rate"]
        if abs(number(row["pass_rate_lift"], -1, 1) - expected_lift) > 1e-9:
            raise ValueError("invalid pass rate lift")
        for dimension in DIMENSIONS:
            lift = arms["with_skill"]["dimensions"][dimension] - arms["without_skill"]["dimensions"][dimension]
            if abs(number(row["dimension_lift"][dimension], -1, 1) - lift) > 1e-9:
                raise ValueError("invalid dimension lift")
    return row


def merge(args):
    # This fallback already exists before setup/download; replace it atomically.
    report = read_report(args.output / SUMMARY)
    for index, expected in enumerate(report["observations"]):
        if expected.get("reason") != "job_report_unavailable":
            continue  # Preserve not-selected, missing/invalid datasets, and prior evidence.
        if not report["policy"]["models_resolved"]:
            expected["reason"] = "authorization_or_configuration_unavailable"
            continue
        if args.command == "stage":
            paths = [args.source / SUMMARY] if (args.source / SUMMARY).is_file() else []
        else:
            name = f"skill-shard-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{job_id(expected)}"
            paths = list(args.source.glob(name + "/" + SUMMARY))
        if not paths:
            if args.command == "stage":
                expected["reason"] = "job_setup_or_execution_failed"
            continue
        try:
            if len(paths) != 1:
                raise ValueError("ambiguous shard artifacts")
            candidate = read_report(paths[0])
            if args.command == "aggregate" and candidate.get("ci_shard") != job_id(expected):
                raise ValueError("wrong shard artifact")
            report["observations"][index] = validated_row(candidate, report, expected)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            expected["reason"] = "invalid_job_report"
    report["finished_at"] = now()
    persist_report(report, args.output)
    return report


def resolve_models():
    models, _, _ = configuration("inference_hub")
    models["agent"] = models["agent"].removeprefix("openai/")
    # Outputs are nonsecret, single-line model identifiers, never provider config.
    if any(not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", value) for value in models.values()):
        raise ValueError("invalid model identifier")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        for name, value in models.items():
            stream.write(f"{name}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "initialize", "stage", "aggregate", "config"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--tier", choices=("2", "3", "both"), default="both")
    parser.add_argument("--skill", default="all")
    parser.add_argument("--check", choices=("all", "context", "similarity", "live"), default="all")
    parser.add_argument("--models-resolved", action="store_true")
    args = parser.parse_args()
    if args.command == "config":
        resolve_models()
    elif args.command in ("plan", "initialize"):
        initialize(args)
    else:
        merge(args)


if __name__ == "__main__":
    main()
