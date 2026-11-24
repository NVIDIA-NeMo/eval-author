# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep completed grader outcomes distinct from agent and infrastructure status."""

import re
from collections import Counter
from pathlib import Path

from harbor.models.trial.result import TrialResult

from metric_benchmark.io import read_object
from metric_benchmark.models import Score

AGENT_FAILURES = {"AgentTimeoutError", "AgentSafetyRefusalError"}


def completed_pytest(stdout: str, reward: float) -> bool:
    sessions = re.split(r"(?m)^=+ test session starts =+$", stdout)
    if len(sessions) < 2:
        return False
    final = sessions[-1]
    collected = re.findall(r"(?m)^collected (\d+) items?\s*$", final)
    summaries = re.findall(r"(?m)^=+\s+(.+?)\s+in\s+[\d.]+s(?:\s+\([^)]*\))?\s+=+$", final)
    if len(collected) != 1 or not summaries:
        return False
    counts = Counter()
    for part in summaries[-1].split(", "):
        match = re.fullmatch(r"(\d+) (passed|failed|skipped|warnings?)", part)
        if match is None:
            return False
        if not match[2].startswith("warning"):
            counts[match[2]] += int(match[1])
    total = sum(counts.values())
    return (
        total > 0
        and total == int(collected[0])
        and (
            (reward == 1.0 and counts["passed"] > 0 and counts["failed"] == 0)
            or (reward == 0.0 and counts["failed"] > 0)
        )
    )


def retained_grader_reward(
    trial: TrialResult,
    native: Path,
    service_status: str,
    *,
    allow_capacity_failure: bool = False,
    allow_completed_member: bool = False,
) -> Score:
    def unavailable(reason: str) -> Score:
        return Score(value=None, missing_score_reason=reason)

    exception = trial.exception_info
    capacity_failure = False
    if allow_capacity_failure and exception is not None and exception.exception_type == "UnknownApiError":
        try:
            error = read_object(native / "agent/result.json").get("error")
            source = read_object(native / "agent/source-identity.json")
            capacity_failure = (
                source.get("variant_id") == "nooa-atom-m-001"
                and isinstance(error, str)
                and error.endswith(
                    "Original error: ValueError: M-001: protected content exceeds reduced input capacity"
                )
            )
        except (OSError, ValueError):
            pass
    if (
        exception is None
        or (exception.exception_type not in AGENT_FAILURES and not capacity_failure)
        or (
            service_status != "failed" and not (allow_completed_member and service_status in {"succeeded", "cancelled"})
        )
    ):
        return unavailable("not a qualified terminal agent failure")
    return completed_grader_reward(trial, native)


def completed_grader_reward(trial: TrialResult, native: Path) -> Score:
    def unavailable(reason: str) -> Score:
        return Score(value=None, missing_score_reason=reason)

    required = [
        "agent/source-identity.json",
        "agent/bundle-identity.json",
        "agent/model-context.json",
        "agent/trajectory.json",
        "verifier/test-stdout.txt",
        "verifier/reward.txt",
    ]
    if any(not (native / path).is_file() or (native / path).is_symlink() for path in required):
        return unavailable("agent failure lacks required identity, trace or grader evidence")
    if trial.verifier_result is None or trial.verifier_result.rewards is None:
        return unavailable("native verifier reward absent")
    reward = trial.verifier_result.rewards.get("reward")
    if reward is None or reward not in (0.0, 1.0):
        return unavailable("qualified grader policy requires a binary reward")
    try:
        if float((native / "verifier/reward.txt").read_text().strip()) != reward:
            return unavailable("reward file differs from native verifier")
        if not (native / "verifier/ctrf.json").exists():
            if not completed_pytest((native / "verifier/test-stdout.txt").read_text(), float(reward)):
                return unavailable("grader completion is not established by CTRF or a complete pytest transcript")
            return Score(value=float(reward))
        result = read_object(native / "verifier/ctrf.json")["results"]
        summary, tests = result["summary"], result["tests"]
        counts = Counter(test["status"] for test in tests)
        if not tests or set(counts) - {"passed", "failed", "skipped"}:
            return unavailable("grader did not finish a supported test result set")
        for key in ("tests", "passed", "failed", "skipped", "pending", "other"):
            expected = len(tests) if key == "tests" else counts[key]
            if type(summary[key]) is not int or summary[key] != expected:
                return unavailable("grader summary does not agree with its tests")
        if (reward == 1.0 and (counts["failed"] or not counts["passed"])) or (reward == 0.0 and not counts["failed"]):
            return unavailable("binary reward is not supported by completed grader tests")
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable("invalid retained grader evidence")
    return Score(value=float(reward))
