#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect pinned SkillEvaluator reports without model calls or source mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

EVALUATOR_REVISION = "7e189c6bdada8910dfa1684f25feedca87f2db85"
CHECKS = "schema,version,security,pii,license,code-integrity,unicode,quality,lint"
VALIDATORS = {
    "Schema & Repository Governance",
    "Semantic Version Validation",
    "Security Scan",
    "PII Scan",
    "License Compliance",
    "Code Risk Analysis",
    "Secrets Detection",
    "Code Integrity & Hygiene",
    "Unicode Smuggling Detection",
    "QUALITY",
    "SCRIPT_LINT",
}


def now():
    return datetime.now(UTC).isoformat()


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def tree_digest(skill):
    files = []
    for path in sorted(skill.rglob("*")):
        if path.is_symlink():
            raise ValueError("skill tree contains a symlink")
        if path.is_file():
            files.append(
                [path.relative_to(skill).as_posix(), sha(path.read_bytes()), bool(path.stat().st_mode & 0o111)]
            )
    return sha(json.dumps(files, separators=(",", ":")).encode())


def summarize(report, exit_code):
    """Allowlist metrics, excluding paths, source snippets and scanner findings."""
    result = {"status": "incomplete", "reason": "missing_or_incompatible_report", "quality": None}
    if not isinstance(report, dict):
        return result
    quality = report.get("quality_summary")
    if isinstance(quality, list) and len(quality) == 1 and isinstance(quality[0], dict):
        score = quality[0].get("overall_score")
        grade = quality[0].get("grade")
        if (
            type(score) in (int, float)
            and math.isfinite(score)
            and 0 <= score <= 100
            and grade in ("A", "B", "C", "D", "F")
        ):
            result["quality"] = {"score": score, "grade": grade, "independent_of_gate": True}
    rows = report.get("results")
    if not isinstance(rows, list) or len(rows) != len(VALIDATORS):
        return result
    if any(not isinstance(r, dict) or not isinstance(r.get("validator"), str) for r in rows):
        return result
    if {r["validator"] for r in rows} != VALIDATORS:
        return result
    policy = report.get("policy") or {}
    if not isinstance(policy, dict):
        return result
    if policy.get("profile") != "external" or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(policy.get("digest"))):
        return result
    result["policy_digest"] = policy["digest"]
    result["validators"] = [{"name": r["validator"], "status": r.get("status")} for r in rows]
    if not isinstance(report.get("incomplete_scans"), list) or any(
        not isinstance(r.get("incomplete_scans"), list) for r in rows
    ):
        return result
    if report["incomplete_scans"] or any(r["incomplete_scans"] for r in rows):
        result["reason"] = "required_scanner_evidence_incomplete"
        return result
    if any(
        r.get("gating") != {"tier": 1, "blocking": True}
        or r.get("status") not in ("passed", "failed")
        or type(r.get("passed")) is not bool
        or r["passed"] != (r["status"] == "passed")
        for r in rows
    ):
        return result
    severity = report.get("severity_counts", {})
    if not isinstance(severity, dict):
        return result
    if any(type(severity.get(k)) is not int or severity[k] < 0 for k in ("critical", "high")):
        return result
    if exit_code not in (0, 1) or type(report.get("overall_passed")) is not bool:
        return result
    passed = (
        exit_code == 0
        and report["overall_passed"]
        and report.get("overall_status") == "passed"
        and all(r["passed"] for r in rows)
        and not severity["critical"]
        and not severity["high"]
    )
    result.update(
        status="passed" if passed else "failed",
        reason=None,
        blocking_severity_counts={k: severity[k] for k in ("critical", "high")},
    )
    return result


def execute(argv, cwd, env, log, timeout):
    with log.open("xb") as stream:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            return process.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return process.returncode, "timeout"


# Skill trees that ship from this repository: Eval Author's and the separately
# installed ethos skills.
SKILL_ROOTS = ("skills", "ethos/skills")


def collect(repo, output, evaluator, timeout):
    repo, output = repo.resolve(), output.resolve()
    if any(output.is_relative_to(repo / root) for root in SKILL_ROOTS):
        raise ValueError("output must be outside the skill trees")
    skills = [path for root in SKILL_ROOTS for path in sorted((repo / root).glob("*/SKILL.md"))]
    if not skills or not (repo / "skills/eval-author/SKILL.md").is_file():
        raise ValueError("main eval-author skill and sub-skills required")
    names = [path.parent.name for path in skills]
    if len(names) != len(set(names)):
        raise ValueError("skill names must be unique across skill trees")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *SKILL_ROOTS], cwd=repo, text=True)
    if dirty:
        raise ValueError("skill inputs must be clean")
    output.mkdir(parents=True, exist_ok=False)
    started = now()
    tool_versions = {}
    for tool in (evaluator, "skillspector", "semgrep", "gitleaks"):
        try:
            version = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=30)
            match = re.search(r"(?<![\w.])v?(\d+\.\d+\.\d+)", version.stdout + version.stderr)
            tool_versions[Path(tool).name] = match[1] if version.returncode == 0 and match else None
        except (OSError, subprocess.TimeoutExpired):
            tool_versions[Path(tool).name] = None
    rows = []
    for entry in skills:
        skill = entry.parent
        target = output / skill.name
        target.mkdir()
        home = target / "home"
        home.mkdir()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "SEMGREP_SEND_METRICS": "off",
            "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        before = tree_digest(skill)
        argv = [
            evaluator,
            "validate",
            str(skill),
            "--type",
            "skill",
            "--profile",
            "external",
            "--checks",
            CHECKS,
            "--no-dedup",
            "--no-llm",
            "--continue-on-failure",
            "--min-score",
            "70",
            "-r",
            "json",
            "-o",
            str(target / "reports"),
        ]
        begin = now()
        error, code, raw = None, None, None
        try:
            code, error = execute(argv, repo, env, target / "console.log", timeout)
            paths = list((target / "reports").glob("*.json"))
            if len(paths) == 1:
                raw = paths[0].read_bytes()
            else:
                error = error or "report_missing_or_ambiguous"
            summary = summarize(json.loads(raw) if raw else None, code)
            if summary["reason"] == "missing_or_incompatible_report":
                error = error or "missing_or_incompatible_report"
        except (OSError, ValueError) as exc:
            error = type(exc).__name__
            summary = summarize(None, code)
        if tree_digest(skill) != before:
            error = "skill_changed_during_scan"
        if error:
            summary.update(status="incomplete", reason=error)
        rows.append(
            {
                "skill": skill.name,
                "path": entry.parent.relative_to(repo).as_posix(),
                "tree_digest": before,
                "started_at": begin,
                "finished_at": now(),
                "exit_code": code,
                "collection_error": error,
                "report_digest": sha(raw) if raw else None,
                **summary,
            }
        )
    counts = dict(Counter(row["status"] for row in rows))
    report = {
        "schema": "nemo.eval_author.skill_evaluations.v1",
        "repository": "NVIDIA-NeMo/labs-eval-author",
        "source_revision": revision,
        "started_at": started,
        "finished_at": now(),
        "ci": {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME")},
        "configured_evaluator_revision": EVALUATOR_REVISION,
        "tool_versions": tool_versions,
        "policy": {"profile": "external", "checks": CHECKS, "min_score": 70, "llm": False, "dedup": False},
        "skills": rows,
        "counts": counts,
        "status": "incomplete" if counts.get("incomplete") else "failed" if counts.get("failed") else "passed",
    }
    (output / "skillevaluator-summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluator", default="skillevaluator")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--enforce", action="store_true", help="Fail on failed/incomplete scans as well as collection errors"
    )
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    report = collect(args.repo, args.output, args.evaluator, args.timeout)
    print(json.dumps({"status": report["status"], "counts": report["counts"]}))
    return int(any(r["collection_error"] for r in report["skills"]) or (args.enforce and report["status"] != "passed"))


if __name__ == "__main__":
    raise SystemExit(main())
