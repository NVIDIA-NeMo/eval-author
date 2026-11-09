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
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import NotRequired, TypedDict

import live_agent_diagnostics
import live_shard_boundary
from collect_skill_evaluations import EVALUATOR_REVISION, execute, now, sha, tree_digest
from license_duplication_policy import POLICY, expected_license_duplicate
from live_evaluation_diagnostics import diagnostics
from patched_opencode_runtime import CONFIGURED_VERSION as OPENCODE_VERSION
from patched_opencode_runtime import LIVE_EXECUTION_SECONDS, LIVE_HANDOFF_SECONDS, live_job_remaining
from run_pinned_evaluator import CONTEXT_POLICY, CONTEXT_SCALAR_LIMIT

HUB_BASE_URL = "https://inference-api.nvidia.com/v1"
HUB_KEY = "INFERENCE_HUB_API_KEY"
DEFAULT_CHAT_MODEL = "azure/openai/gpt-5.6-luna"
DEFAULT_EMBEDDING_MODEL = "azure/openai/text-embedding-3-small"
TIER3_TIMEOUT_SECONDS = LIVE_EXECUTION_SECONDS
DIMENSIONS = ("security", "correctness", "discoverability", "effectiveness", "efficiency")
FINDINGS = {"duplicate", "EXACT_DUPLICATE", "HIGH_SIMILARITY", "SIMILAR", "LOOSELY_RELATED", "DISTINCT"}


class ArmMetrics(TypedDict):
    dimensions: dict[str, float | None]
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
    dimension_lift: NotRequired[dict[str, float | None]]
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
    agent_diagnostics: NotRequired[dict]


class LiveReport(TypedDict):
    schema: str
    repository: str
    source_revision: str
    inputs_clean: bool
    configured_evaluator_revision: str
    started_at: str
    mode: str
    ci: dict[str, str | None]
    ci_shard: NotRequired[str]
    policy: dict[str, object]
    observations: list[Observation]
    updated_at: NotRequired[str]
    finished_at: NotRequired[str]
    counts: NotRequired[dict[str, int]]


class InsufficientLiveBudget(TimeoutError):
    pass


def execute_with_progress(
    argv, repo, env, log, timeout, *, reserve_live_budget=False, boundary_state=None, final=False
):
    """Keep CI responsive without streaming unredacted subprocess output."""
    if reserve_live_budget:
        try:
            live_job_remaining(timeout + LIVE_HANDOFF_SECONDS)
        except (TimeoutError, ValueError) as exc:
            raise InsufficientLiveBudget("insufficient live job budget") from exc
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
        if boundary_state is not None:
            return live_shard_boundary.execute_in_boundary(boundary_state, argv, repo, env, log, timeout, final=final)
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


def metric_names(value) -> set[str]:
    if not isinstance(value, list) or any(
        not isinstance(name, str) or not name or name != name.strip() for name in value
    ):
        raise ValueError("invalid metric names")
    names = set(value)
    if len(names) != len(value):
        raise ValueError("duplicate metric names")
    return names


def validate_metric_coverage(agent, arm):
    requested = metric_names(agent.get("metrics_" + arm))
    scores = agent.get(arm)
    not_applicable = agent.get("not_applicable_metrics")
    if not isinstance(scores, dict) or not isinstance(not_applicable, dict):
        raise ValueError("invalid metric evidence map")
    scored = metric_names(list(scores))
    skipped = metric_names(not_applicable.get(arm))
    for value in scores.values():
        number(value)
    if skipped - requested or skipped & scored:
        raise ValueError("inconsistent not-applicable metrics")
    if requested - scored - skipped:
        raise ValueError("missing required metric")


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
        validate_metric_coverage(agent, arm)
        dimensions = agent.get("dimensions_" + arm)
        if not isinstance(dimensions, dict):
            raise ValueError("invalid dimension map")
        # Native dimension_scores() omits both N/A and missing-source dimensions.
        # Validate requested metric coverage before treating omissions as unmeasured.
        scores: dict[str, float | None] = {}
        for key in DIMENSIONS:
            if key not in dimensions:
                scores[key] = None
            else:
                entry = dimensions[key]
                if not isinstance(entry, dict):
                    raise ValueError("invalid dimension entry")
                scores[key] = number(entry.get("score"))
        passes = agent["pass_at_k"][arm]
        passed, total = count(passes["passed_cases"]), count(passes["total_cases"])
        if total != cases or passed > total or count(passes["k"]) != 1:
            raise ValueError("incompatible denominator")
        arms[arm] = {"dimensions": scores, "passed_cases": passed, "total_cases": total, "pass_rate": passed / total}
    lifts: dict[str, float | None] = {}
    for key in DIMENSIONS:
        with_score = arms["with_skill"]["dimensions"][key]
        without_score = arms["without_skill"]["dimensions"][key]
        lifts[key] = with_score - without_score if with_score is not None and without_score is not None else None
    return {
        "status": "completed",
        "reason": None,
        "arms": arms,
        "dimension_lift": lifts,
        "pass_rate_lift": arms["with_skill"]["pass_rate"] - arms["without_skill"]["pass_rate"],
    }


def configuration(provider, *, read_credentials=True):
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
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "HARBOR_TELEMETRY": "0",
    }
    env.update(
        SKILL_EVAL_LLM_PROVIDER="openai-compatible",
        SKILL_EVAL_EMBEDDING_PROVIDER="openai-compatible",
        SKILL_EVAL_LLM_BASE_URL=HUB_BASE_URL,
        SKILL_EVAL_EMBEDDING_BASE_URL=HUB_BASE_URL,
        SKILL_EVAL_LLM_MODEL=models["chat"],
        SKILL_EVAL_EMBEDDING_MODEL=models["embedding"],
    )
    key = os.environ.get(HUB_KEY, "").strip() if read_credentials else ""
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
    if data["skill_name"] != skill.name or not isinstance(cases, list) or not 1 <= len(cases) <= 6:
        raise ValueError("expected one to six authored cases")
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


def persist_report(report, output):
    for row in report["observations"]:
        live_agent_diagnostics.project_public_row(row)
    report["updated_at"] = now()
    report["counts"] = dict(Counter(row["status"] for row in report["observations"]))
    target = output / "live-skillevaluator-summary.json"
    temporary = output / "summary.tmp"
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temporary.replace(target)
    lines = [
        "# SkillEvaluator Tier 2/3",
        "",
        f"Mode: `{report['mode']}`; revision: `{report['source_revision']}`.",
        "",
        "| Tier | Scope | Check | Status | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["observations"]:
        lines.append(f"| {row['tier']} | {row['skill']} | {row['check']} | {row['status']} | {row['reason'] or ''} |")
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
                values = [
                    "unmeasured" if value is None else format(value, spec)
                    for value, spec in (
                        (arms["with_skill"]["dimensions"][key], ".3f"),
                        (arms["without_skill"]["dimensions"][key], ".3f"),
                        (row["dimension_lift"][key], "+.3f"),
                    )
                ]
                lines.append(f"| {key} | " + " | ".join(values) + " |")
    lines.extend(["", "Completed Tier 3 means both arms were scored; it does not mean the skill improved results."])
    (output / "live-skillevaluator-summary.md").write_text("\n".join(lines) + "\n")


def collect(*args, boundary_state=None, **kwargs):
    if boundary_state is None:
        return _collect(*args, **kwargs)
    closed = False
    close_error = None

    def close_boundary():
        nonlocal closed, close_error
        if close_error is not None:
            raise close_error
        if not closed:
            try:
                live_shard_boundary.close_boundary(boundary_state)
            except BaseException as exc:
                # A later cleanup retry must not authorize parsing after uncertainty.
                close_error = exc
                raise
            closed = True

    try:
        return _collect(*args, boundary_state=boundary_state, _close_boundary=close_boundary, **kwargs)
    finally:
        close_boundary()


def _collect(
    repo,
    output,
    *,
    tier="both",
    skill_name="all",
    provider="inference_hub",
    run=False,
    evaluator="skillevaluator",
    check_name="all",
    ci_context_budget=False,
    evaluator_python=None,
    boundary_state=None,
    native_output=None,
    _close_boundary=None,
):
    repo, output = repo.resolve(), output.resolve()
    if output.is_relative_to(repo):
        raise ValueError("live output must be outside the repository")
    native_output = output if native_output is None else native_output.resolve()
    if native_output.is_relative_to(repo):
        raise ValueError("native output must be outside the repository")
    if native_output != output and (native_output.is_relative_to(output) or output.is_relative_to(native_output)):
        raise ValueError("public and private output directories must be disjoint")
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
    budget_failed = False
    if run and tier in ("3", "both") and check_name in ("live", "all"):
        try:
            live_job_remaining(TIER3_TIMEOUT_SECONDS + LIVE_HANDOFF_SECONDS)
        except (TimeoutError, ValueError):
            budget_failed = True
    report: LiveReport = {
        "schema": "nemo.eval_author.live_skill_evaluations.v2",
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
            "models": {},
            "agent": "opencode",
            "configured_opencode_version": OPENCODE_VERSION,
            "environment": "docker",
            "attempts": 1,
            "concurrency": 1,
            "baseline": True,
            "max_cases_per_skill": 4,
            "tier2_timeout_seconds": 300,
            "tier3_timeout_seconds": TIER3_TIMEOUT_SECONDS,
            "monetary_budget_enforced": False,
            "context_duplication_exception": POLICY,
            "requested_check": check_name,
            "context_comparison_policy": CONTEXT_POLICY if ci_context_budget else "upstream",
            "context_scalar_limit": CONTEXT_SCALAR_LIMIT if ci_context_budget else 25_000_000,
        },
        "observations": [],
    }

    def persist():
        persist_report(report, output)

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
            if not selected or tier not in ("both", level) or check_name not in ("all", check):
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
                "status": "not_run" if check_name in ("all", "similarity") else "skipped",
                "reason": "explicit_run_required" if check_name in ("all", "similarity") else "not_selected",
                "cases": None,
            }
        )
    if run and boundary_state is not None and sum(row["status"] == "not_run" for row in report["observations"]) > 1:
        raise ValueError("single-use boundary requires at most one runnable observation")
    if boundary_state is not None:
        native_evaluator = Path(evaluator)
        if (
            not native_evaluator.is_absolute()
            or str(native_evaluator) != evaluator
            or ".." in native_evaluator.parts
            or ":" in evaluator
            or "\x00" in evaluator
        ):
            raise ValueError("boundary requires an unambiguous absolute evaluator path")
    models, env, missing_key = configuration(provider, read_credentials=not budget_failed)
    if boundary_state is not None:
        # Keep the writable runtime discoverable only inside native execution.
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + str(Path(evaluator).parent)
    report["policy"]["models"] = models
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    if native_output != output:
        native_output.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Docker needs a home for its client state; never inherit host credentials
    # or let an unset HOME redirect that state into the skill repository.
    runtime_home = native_output / "runtime-home"
    runtime_home.mkdir(mode=0o700)
    env["HOME"] = str(runtime_home)
    for row in report["observations"]:
        if row["status"] == "not_run" and budget_failed:
            row.update(status="incomplete", reason="insufficient_job_budget")
        elif row["status"] == "not_run" and missing_key:
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
        target = native_output / f"check-{index}"
        target.mkdir(mode=0o700)
        skill = repo / "skills" / row["skill"]
        row.update(status="incomplete", reason="interrupted", started_at=now())
        print(f"{now()} check-{index}: Tier {row['tier']} {row['skill']} {row['check']}", flush=True)
        persist()
        data = {}
        runtime_failure = None
        try:
            if row["tier"] == 2:
                command = "context-optimization-check" if row["check"] == "context" else "similarity-check"
                scope = skill if row["check"] == "context" else repo / "skills"
                argv = [evaluator, command, str(scope), "-r", "json", "-o", str(target / "reports")]
                if row["check"] == "context" and ci_context_budget:
                    argv = [
                        evaluator_python or sys.executable,
                        str(Path(__file__).with_name("run_pinned_evaluator.py")),
                        *argv[1:],
                    ]
                code, error = execute_with_progress(
                    argv,
                    repo,
                    env,
                    target / "console.log",
                    300,
                    **({"boundary_state": boundary_state, "final": True} if boundary_state is not None else {}),
                )
                if _close_boundary is not None:
                    _close_boundary()
                paths = list((target / "reports").glob("*.json"))
            else:
                # Native contract validation is keyless; no generation or autopilot.
                code, error = execute_with_progress(
                    [evaluator, "tier3", "validate", str(skill), "--json"],
                    repo,
                    env,
                    target / "validate.log",
                    60,
                    **({"boundary_state": boundary_state} if boundary_state is not None else {}),
                )
                if code != 0 or error:
                    row.update(reason="dataset_validation_failed", exit_code=code)
                    continue
                argv = [
                    evaluator_python or sys.executable,
                    str(Path(__file__).with_name("run_pinned_live_evaluator.py")),
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
                    "--harbor-keep-jobs",
                    "--results-dir",
                    str(target / "results"),
                    "--progress",
                    "off",
                ]
                live_env = dict(env)
                failure_path = target / "runtime-failure.json"
                failure_token = secrets.token_hex(32)
                live_env.update(_EA_LIVE_FAILURE_PATH=str(failure_path), _EA_LIVE_FAILURE_TOKEN=failure_token)
                for key in (
                    "EA_PATCHED_OPENCODE_MANIFEST",
                    "EA_REQUIRE_PATCHED_OPENCODE",
                    "EA_PREPARED_IMAGES_MANIFEST",
                    "EA_REQUIRE_PREPARED_IMAGES",
                    "EA_LIVE_JOB_DEADLINE",
                ):
                    if key in os.environ:
                        live_env[key] = os.environ[key]
                code, error = execute_with_progress(
                    argv,
                    repo,
                    live_env,
                    target / "console.log",
                    TIER3_TIMEOUT_SECONDS,
                    reserve_live_budget=True,
                    **({"boundary_state": boundary_state, "final": True} if boundary_state is not None else {}),
                )
                if _close_boundary is not None:
                    _close_boundary()
                runtime_failure = live_agent_diagnostics.read_runtime_failure(failure_path, failure_token)
                if runtime_failure is not None:
                    row.update(reason=runtime_failure["reason"], exit_code=code)
                    continue
                # Native runs also publish a latest -> run symlink. Read only
                # physical run directories so this alias cannot duplicate a report.
                try:
                    paths = [live_agent_diagnostics.native_run(target, skill.name) / "result.json"]
                except (OSError, ValueError):
                    paths = []
            row["exit_code"] = code
            if error:
                row.update(reason="timeout")
                continue
            if len(paths) != 1 or paths[0].is_symlink() or paths[0].stat().st_size > 10_000_000:
                row.update(reason="report_missing_or_ambiguous")
                continue
            raw = (
                live_agent_diagnostics.read_regular(paths[0], 10_000_000) if row["tier"] == 3 else paths[0].read_bytes()
            )
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
            row = {**row, **result}
            report["observations"][index] = row
        except InsufficientLiveBudget:
            row.update(status="incomplete", reason="insufficient_job_budget")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            row.update(status="incomplete", reason="tool_or_report_error")
            data = {"execution_errors": [f"Collector {type(exc).__name__}: {exc}"], "results": [data]}
        finally:
            if _close_boundary is not None:
                _close_boundary()
            if row["tier"] == 3:
                # Diagnostics are advisory even when native execution/report parsing failed.
                # Never let a diagnostic parser replace the original outcome.
                try:
                    row["agent_diagnostics"] = live_agent_diagnostics.capture(data, target, skill.name)
                except Exception:
                    row["agent_diagnostics"] = live_agent_diagnostics.unavailable()
                if runtime_failure is not None:
                    row["agent_diagnostics"]["runtime_failure"] = runtime_failure
                row["agent_diagnostics"] = live_agent_diagnostics.public_projection(row["agent_diagnostics"])
                row["diagnostics"] = live_agent_diagnostics.render(row["agent_diagnostics"])
            if row["tier"] == 2 and (
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
                    "agent_diagnostics",
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
    parser.add_argument("--check", choices=("all", "context", "similarity", "live"), default="all")
    parser.add_argument("--ci-context-budget", action="store_true")
    parser.add_argument("--evaluator-python")
    parser.add_argument("--boundary-state", type=Path)
    parser.add_argument("--native-output", type=Path)
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
        check_name=args.check,
        ci_context_budget=args.ci_context_budget,
        evaluator_python=args.evaluator_python,
        **({"boundary_state": args.boundary_state} if args.boundary_state is not None else {}),
        **({"native_output": args.native_output} if args.native_output is not None else {}),
    )
    print(json.dumps({"mode": report["mode"], "counts": report["counts"]}))
    return int(args.run and any(r["status"] in ("incomplete", "not_run") for r in report["observations"]))


if __name__ == "__main__":
    raise SystemExit(main())
