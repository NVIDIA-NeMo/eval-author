#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run advisory Tier 2/3 checks with bounded metrics and redacted diagnostics."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path
from typing import NotRequired, TypedDict

from collect_skill_evaluations import EVALUATOR_REVISION, execute, now, sha, tree_digest
from license_duplication_policy import POLICY, expected_license_duplicate
from live_evaluation_diagnostics import diagnostics

HUB_BASE_URL = "https://inference-api.nvidia.com/v1"
HUB_KEY = "INFERENCE_HUB_API_KEY"
DEFAULT_CHAT_MODEL = "azure/openai/gpt-5.6-luna"
DEFAULT_EMBEDDING_MODEL = "azure/openai/text-embedding-3-small"
DIMENSIONS = ("security", "correctness", "discoverability", "effectiveness", "efficiency")
FINDINGS = {"duplicate", "EXACT_DUPLICATE", "HIGH_SIMILARITY", "SIMILAR", "LOOSELY_RELATED", "DISTINCT"}


class ArmMetrics(TypedDict):
    dimensions: dict[str, float]
    passed_cases: int
    total_cases: int
    pass_rate: float


class EvaluationSummary(TypedDict):
    status: str
    reason: str | None
    severity_counts: NotRequired[dict[str, int]]
    raw_severity_counts: NotRequired[dict[str, int]]
    excluded_license_findings: NotRequired[int]
    arms: NotRequired[dict[str, ArmMetrics]]
    dimension_lift: NotRequired[dict[str, float]]
    pass_rate_lift: NotRequired[float]


class Observation(EvaluationSummary):
    tier: int
    skill: str
    check: str
    tree_digest: str
    cases: int | None
    started_at: NotRequired[str]
    finished_at: NotRequired[str]
    exit_code: NotRequired[int | None]
    report_digest: NotRequired[str]
    diagnostics: NotRequired[str]


class LiveReport(TypedDict):
    schema: str
    repository: str
    source_revision: str
    inputs_clean: bool
    configured_evaluator_revision: str
    started_at: str
    mode: str
    ci: dict[str, str | None]
    policy: dict[str, object]
    observations: list[Observation]
    updated_at: NotRequired[str]
    finished_at: NotRequired[str]
    counts: NotRequired[dict[str, int]]


def execute_with_progress(argv, repo, env, log, timeout):
    """Keep CI responsive without streaming unredacted subprocess output."""
    label = f"{log.parent.name}/{log.name}"
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            print(f"{now()} {label}: running ({int(time.monotonic() - started)}s; limit {timeout}s)", flush=True)

    worker = threading.Thread(target=heartbeat, daemon=True)
    print(f"{now()} {label}: starting (limit {timeout}s)", flush=True)
    worker.start()
    try:
        return execute(argv, repo, env, log, timeout)
    finally:
        stopped.set()
        worker.join()
        print(f"{now()} {label}: finished after {int(time.monotonic() - started)}s", flush=True)


def number(value, minimum=0, maximum=1) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid metric")
    return value


def count(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid count")
    return value


def summarize_tier2(report, code, validator, skill: Path | None = None) -> EvaluationSummary:
    """Findings are distinct from provider/analysis errors, even when both exit 1."""
    rows = report["results"]
    if len(rows) != 1 or rows[0]["validator"] != validator or code not in (0, 1):
        raise ValueError("incompatible Tier 2 report")
    row = rows[0]
    if not isinstance(row["findings"], list) or not isinstance(row["incomplete_scans"], list):
        raise ValueError("incompatible Tier 2 evidence")
    counts = {key: count(row["summary"][key + "_count"]) for key in ("critical", "high", "medium", "low")}
    analysis_error = (
        bool(row["incomplete_scans"])
        or count(row["summary"]["errors"]) > counts["critical"] + counts["high"]
        or any(f["check_name"] not in FINDINGS for f in row["findings"])
    )
    if analysis_error:
        return {"status": "incomplete", "reason": "analysis_incomplete", "severity_counts": counts}
    if type(row["passed"]) is not bool or row["status"] != ("passed" if row["passed"] else "failed"):
        raise ValueError("inconsistent Tier 2 result")
    if (code == 0) != row["passed"]:
        raise ValueError("inconsistent Tier 2 exit")
    if validator == "Context Deduplication" and skill is not None:
        # Only reinterpret complete, internally consistent findings. Raw JSON is
        # retained unchanged; effective counts drive the dashboard's CI verdict.
        observed = Counter(f.get("severity") for f in row["findings"])
        consistent = (
            all(key in counts for key in observed)
            and all(observed[key] == value for key, value in counts.items())
            and row["summary"]["errors"] == counts["critical"] + counts["high"]
            and row["passed"] == (counts["critical"] + counts["high"] == 0)
        )
        if consistent:
            excluded = sum(expected_license_duplicate(f, skill) for f in row["findings"])
            if excluded:
                effective = {**counts, "high": counts["high"] - excluded}
                return {
                    "status": "failed" if effective["critical"] + effective["high"] else "passed",
                    "reason": f"{excluded} expected license-header finding(s) excluded",
                    "severity_counts": effective,
                    "raw_severity_counts": counts,
                    "excluded_license_findings": excluded,
                }
    return {"status": row["status"], "reason": None, "severity_counts": counts}


def summarize_tier3(report, code, cases) -> EvaluationSummary:
    """A completed A/B experiment may have poor scores; completion is not quality."""
    if code != 0 or report.get("execution_status") != "succeeded":
        # Export fixed categories, never the provider/agent error payload.
        errors = report.get("execution_errors", [])
        detail = "\n".join(item for item in errors if isinstance(item, str)) if isinstance(errors, list) else ""
        reason = "execution_incomplete"
        if "Docker compose command failed" in detail and " build." in detail:
            reason = "docker_build_failed"
        elif "Docker compose command failed" in detail:
            reason = "docker_runtime_failed"
        elif "OpenCode emitted error event" in detail:
            reason = "agent_api_failed"
        elif "NonZeroAgentExitCodeError" in detail and any(word in detail for word in ("apt-get", "nvm install")):
            reason = "agent_install_failed"
        elif "NonZeroAgentExitCodeError" in detail:
            reason = "agent_command_failed"
        elif "AgentSetupTimeoutError" in detail:
            reason = "agent_install_timeout"
        elif "AgentTimeoutError" in detail:
            reason = "agent_execution_timeout"
        elif "no agent artifacts" in detail:
            reason = "agent_artifacts_missing"
        elif "runtime preflight failed" in detail:
            reason = "agent_runtime_preflight_failed"
        return {"status": "incomplete", "reason": reason}
    agent = report["agents"]["opencode"]
    if agent["execution_status"] != "succeeded" or report.get("execution_errors") or agent.get("execution_errors"):
        raise ValueError("inconsistent Tier 3 execution")
    arms: dict[str, ArmMetrics] = {}
    for arm in ("with_skill", "without_skill"):
        execution = agent["conditions"][arm]
        if (
            execution["execution_status"] != "succeeded"
            or execution.get("execution_errors")
            or count(execution["expected_attempts"]) != cases
            or count(execution["scored_attempts"]) != cases
        ):
            raise ValueError("incomplete arm")
        scores = {key: number(agent["dimensions_" + arm][key]["score"]) for key in DIMENSIONS}
        passes = agent["pass_at_k"][arm]
        passed, total = count(passes["passed_cases"]), count(passes["total_cases"])
        if total != cases or passed > total or count(passes["k"]) != 1:
            raise ValueError("incompatible denominator")
        arms[arm] = {"dimensions": scores, "passed_cases": passed, "total_cases": total, "pass_rate": passed / total}
    return {
        "status": "completed",
        "reason": None,
        "arms": arms,
        "dimension_lift": {
            key: arms["with_skill"]["dimensions"][key] - arms["without_skill"]["dimensions"][key] for key in DIMENSIONS
        },
        "pass_rate_lift": arms["with_skill"]["pass_rate"] - arms["without_skill"]["pass_rate"],
    }


def configuration(provider):
    if provider != "inference_hub":
        raise ValueError("only NVIDIA Inference Hub is supported")
    chat = os.environ.get("SKILL_EVAL_LLM_MODEL", "").strip() or DEFAULT_CHAT_MODEL
    agent = os.environ.get("SKILL_EVAL_AGENT_MODEL", "").strip() or chat
    models = {
        "chat": chat,
        "embedding": os.environ.get("SKILL_EVAL_EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL,
        "agent": "openai/" + agent,
    }
    # Do not forward unrelated host credentials, agent config, or routing overrides.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(
        SKILL_EVAL_LLM_PROVIDER="openai-compatible",
        SKILL_EVAL_EMBEDDING_PROVIDER="openai-compatible",
        SKILL_EVAL_LLM_BASE_URL=HUB_BASE_URL,
        SKILL_EVAL_EMBEDDING_BASE_URL=HUB_BASE_URL,
        SKILL_EVAL_LLM_MODEL=models["chat"],
        SKILL_EVAL_EMBEDDING_MODEL=models["embedding"],
    )
    key = os.environ.get(HUB_KEY, "").strip()
    if key:
        env["SKILL_EVAL_LLM_API_KEY"] = key
        env["SKILL_EVAL_EMBEDDING_API_KEY"] = key
    return models, env, not bool(key)


def dataset_cases(skill):
    """The initial CI profile deliberately accepts only small checked-in JSON sets."""
    path = skill / "evals/evals.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    cases = data["evals"]
    if data["skill_name"] != skill.name or not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        raise ValueError("expected one to four authored cases")
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case IDs")
    for case in cases:
        if not all(isinstance(case.get(key), str) and case[key].strip() for key in ("id", "prompt", "expected_output")):
            raise ValueError("incomplete dataset")
    return len(cases)


def unchanged(skills, digests):
    try:
        return all(tree_digest(skill) == digests[skill.name] for skill in skills)
    except (OSError, ValueError):
        return False


def collect(
    repo, output, *, tier="both", skill_name="all", provider="inference_hub", run=False, evaluator="skillevaluator"
):
    repo, output = repo.resolve(), output.resolve()
    if output.is_relative_to(repo):
        raise ValueError("live output must be outside the repository")
    skills = sorted(path.parent for path in (repo / "skills").glob("*/SKILL.md"))
    if not skills or (skill_name != "all" and skill_name not in {s.name for s in skills}):
        raise ValueError("unknown skill or empty collection")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "skills"], cwd=repo, text=True)
    if run and dirty:
        raise ValueError("live skill inputs must be clean in Git")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    # Reject linked inputs before provider calls or output creation.
    if any(s.is_symlink() for s in skills) or (repo / "skills").is_symlink():
        raise ValueError("linked skill root")
    if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s.name) for s in skills):
        raise ValueError("invalid skill name")
    digests = {s.name: tree_digest(s) for s in skills}
    models, env, missing_key = configuration(provider)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Docker needs a home for its client state; never inherit host credentials
    # or let an unset HOME redirect that state into the skill repository.
    runtime_home = output / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    env["HOME"] = str(runtime_home)
    report: LiveReport = {
        "schema": "nemo.eval_author.live_skill_evaluations.v1",
        "repository": "NVIDIA-NeMo/eval-author",
        "source_revision": revision,
        "inputs_clean": not bool(dirty),
        "configured_evaluator_revision": EVALUATOR_REVISION,
        "started_at": now(),
        "mode": "run" if run else "plan",
        "ci": {key: os.environ.get(key) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME")},
        "policy": {
            "requested_tier": tier,
            "requested_skill": skill_name,
            "provider": provider,
            "api_base_url": HUB_BASE_URL,
            "evaluator_provider": "openai-compatible",
            "models": models,
            "agent": "opencode",
            "environment": "docker",
            "attempts": 1,
            "concurrency": 1,
            "baseline": True,
            "max_cases_per_skill": 4,
            "tier2_timeout_seconds": 300,
            "tier3_timeout_seconds": 1800,
            "monetary_budget_enforced": False,
            "context_duplication_exception": POLICY,
        },
        "observations": [],
    }

    def persist():
        report["updated_at"] = now()
        report["counts"] = dict(Counter(row["status"] for row in report["observations"]))
        target = output / "live-skillevaluator-summary.json"
        temporary = output / "summary.tmp"
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        temporary.replace(target)
        lines = [
            "# SkillEvaluator Tier 2/3",
            "",
            f"Mode: `{report['mode']}`; revision: `{revision}`.",
            "",
            "| Tier | Scope | Check | Status | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in report["observations"]:
            lines.append(
                f"| {row['tier']} | {row['skill']} | {row['check']} | {row['status']} | {row['reason'] or ''} |"
            )
        for row in report["observations"]:
            if row.get("diagnostics"):
                lines.extend(
                    [
                        "",
                        f"## Tier {row['tier']} {row['skill']} diagnostics",
                        "",
                        "<pre>" + html.escape(row["diagnostics"]) + "</pre>",
                    ]
                )
            if row["status"] == "completed":
                lines.extend(
                    [
                        "",
                        f"## {row['skill']} A/B metrics",
                        "",
                        "| Metric | With skill | Without skill | Lift |",
                        "| --- | --- | --- | --- |",
                    ]
                )
                arms = row["arms"]
                lines.append(
                    f"| Pass rate | {arms['with_skill']['pass_rate']:.3f} | "
                    f"{arms['without_skill']['pass_rate']:.3f} | {row['pass_rate_lift']:+.3f} |"
                )
                for key in DIMENSIONS:
                    lines.append(
                        f"| {key} | {arms['with_skill']['dimensions'][key]:.3f} | "
                        f"{arms['without_skill']['dimensions'][key]:.3f} | {row['dimension_lift'][key]:+.3f} |"
                    )
        lines.extend(["", "Completed Tier 3 means both arms were scored; it does not mean the skill improved results."])
        (output / "live-skillevaluator-summary.md").write_text("\n".join(lines) + "\n")

    for skill in skills:
        selected = skill_name in ("all", skill.name)
        for level, check in (("2", "context"), ("3", "live")):
            row: Observation = {
                "tier": int(level),
                "skill": skill.name,
                "check": check,
                "tree_digest": digests[skill.name],
                "status": "not_run",
                "reason": "explicit_run_required",
                "cases": None,
            }
            if not selected or tier not in ("both", level):
                row.update(status="skipped", reason="not_selected")
            elif level == "3":
                try:
                    row["cases"] = dataset_cases(skill)
                    if row["cases"] is None:
                        row.update(status="skipped", reason="missing_dataset")
                except (OSError, ValueError, KeyError, TypeError):
                    row.update(status="incomplete", reason="invalid_dataset")
            report["observations"].append(row)
    if tier in ("2", "both"):
        report["observations"].append(
            {
                "tier": 2,
                "skill": "collection",
                "check": "similarity",
                "tree_digest": sha(json.dumps(digests, sort_keys=True).encode()),
                "status": "not_run",
                "reason": "explicit_run_required",
                "cases": None,
            }
        )
    for row in report["observations"]:
        if row["status"] == "not_run" and missing_key:
            row["reason"] = "missing_provider_key"
    persist()
    if not run or missing_key:
        return report

    for index, row in enumerate(report["observations"]):
        if row["status"] != "not_run":
            continue
        if not unchanged(skills, digests):
            row.update(status="incomplete", reason="skill_changed_during_run")
            persist()
            continue
        target = output / f"check-{index}"
        target.mkdir(mode=0o700)
        skill = repo / "skills" / row["skill"]
        row.update(status="incomplete", reason="interrupted", started_at=now())
        print(f"{now()} check-{index}: Tier {row['tier']} {row['skill']} {row['check']}", flush=True)
        persist()
        data = {}
        try:
            if row["tier"] == 2:
                command = "context-optimization-check" if row["check"] == "context" else "similarity-check"
                scope = skill if row["check"] == "context" else repo / "skills"
                argv = [evaluator, command, str(scope), "-r", "json", "-o", str(target / "reports")]
                code, error = execute_with_progress(argv, repo, env, target / "console.log", 300)
                paths = list((target / "reports").glob("*.json"))
            else:
                # Native contract validation is keyless; no generation or autopilot.
                code, error = execute_with_progress(
                    [evaluator, "tier3", "validate", str(skill), "--json"], repo, env, target / "validate.log", 60
                )
                if code != 0 or error:
                    row.update(reason="dataset_validation_failed", exit_code=code)
                    continue
                argv = [
                    evaluator,
                    "tier3",
                    "evaluate",
                    str(skill),
                    "--agents",
                    "opencode",
                    "--env-mode",
                    "docker",
                    "--agent-model",
                    "opencode=" + models["agent"],
                    "--n-attempts",
                    "1",
                    "--n-concurrent",
                    "1",
                    "--max-agents",
                    "1",
                    "--grading-mode",
                    "default",
                    "--skill-workspace-mode",
                    "isolated",
                    "--agent-runtime-preflight",
                    "--results-dir",
                    str(target / "results"),
                    "--progress",
                    "off",
                ]
                code, error = execute_with_progress(argv, repo, env, target / "console.log", 1800)
                # Native runs also publish a latest -> run symlink. Read only
                # physical run directories so this alias cannot duplicate a report.
                paths = [
                    path
                    for path in (target / "results" / skill.name).glob("*/result.json")
                    if not path.parent.is_symlink()
                ]
            row["exit_code"] = code
            if error:
                row.update(reason="timeout")
                continue
            if len(paths) != 1 or paths[0].is_symlink() or paths[0].stat().st_size > 10_000_000:
                row.update(reason="report_missing_or_ambiguous")
                continue
            raw = paths[0].read_bytes()
            row["report_digest"] = sha(raw)
            data = json.loads(raw)
            result = (
                summarize_tier2(
                    data,
                    code,
                    "Context Deduplication" if row["check"] == "context" else "Similarity Check",
                    skill=skill if row["check"] == "context" else None,
                )
                if row["tier"] == 2
                else summarize_tier3(data, code, row["cases"])
            )
            row.update(result)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            row.update(status="incomplete", reason="tool_or_report_error")
            data = {"execution_errors": [f"Collector {type(exc).__name__}: {exc}"], "results": [data]}
        finally:
            if (
                row["status"] in ("failed", "incomplete")
                or any(row.get("severity_counts", {}).values())
                or row.get("excluded_license_findings")
            ):
                row["diagnostics"] = diagnostics(data, target if row["status"] == "incomplete" else None)
            if not unchanged(skills, digests):
                row.update(status="incomplete", reason="skill_changed_during_run")
                for key in (
                    "arms",
                    "dimension_lift",
                    "pass_rate_lift",
                    "severity_counts",
                    "diagnostics",
                    "raw_severity_counts",
                    "excluded_license_findings",
                ):
                    row.pop(key, None)
            row["finished_at"] = now()
            persist()
            print(
                json.dumps(
                    {
                        "check": index,
                        "skill": row["skill"],
                        "status": row["status"],
                        "reason": row["reason"],
                        "diagnostics": row.get("diagnostics", ""),
                    }
                ),
                flush=True,
            )
    report["finished_at"] = now()
    persist()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tier", choices=("2", "3", "both"), default="both")
    parser.add_argument("--skill", default="all")
    parser.add_argument("--provider", choices=("inference_hub",), default="inference_hub")
    parser.add_argument("--evaluator", default="skillevaluator")
    parser.add_argument("--run", action="store_true", help="Authorize provider calls and Docker agent execution")
    args = parser.parse_args()
    report = collect(
        args.repo,
        args.output,
        tier=args.tier,
        skill_name=args.skill,
        provider=args.provider,
        run=args.run,
        evaluator=args.evaluator,
    )
    print(json.dumps({"mode": report["mode"], "counts": report["counts"]}))
    return int(args.run and any(r["status"] in ("incomplete", "not_run") for r in report["observations"]))


if __name__ == "__main__":
    raise SystemExit(main())
