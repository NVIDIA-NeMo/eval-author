# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import datetime, timezone

import pytest
from harbor.models.trial.result import ExceptionInfo, TrialResult
from metric_benchmark.grader_outcomes import completed_pytest, retained_grader_reward
from metric_benchmark.io import read_json
from metric_benchmark.scaled_import import observation_measurements
from test_metric_benchmark_validation import result_example


@pytest.fixture
def graded_failure(tmp_path):
    _, _, raw, root, _ = result_example(tmp_path)
    native = root / raw.execution_results[0].native_results_path
    trial = TrialResult.model_validate(read_json(native / "result.json"))
    trial.exception_info = ExceptionInfo(
        exception_type="AgentTimeoutError",
        exception_message="timeout",
        exception_traceback="timeout",
        occurred_at=datetime.now(timezone.utc),
    )
    for name in ["source-identity.json", "bundle-identity.json", "model-context.json", "trajectory.json"]:
        path = native / "agent" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("{}")
    verifier = native / "verifier"
    verifier.mkdir()
    (verifier / "test-stdout.txt").write_text("completed grader")
    (verifier / "reward.txt").write_text("1")
    (verifier / "ctrf.json").write_text(
        json.dumps(
            {
                "results": {
                    "tests": [{"status": "passed"}],
                    "summary": {"tests": 1, "passed": 1, "failed": 0, "skipped": 0, "pending": 0, "other": 0},
                }
            }
        )
    )
    return trial, native


def test_retains_actual_passing_grader_reward_without_erasing_agent_failure(graded_failure):
    trial, native = graded_failure
    metadata = {"status": "failed", "last_failure_code": "AgentTimeoutError"}
    status, metrics = observation_measurements(trial, metadata, native, True)
    assert status == "error"
    assert metrics["reward"].value is None
    assert metrics["verified_grader_reward"].value == 1.0
    assert set(observation_measurements(trial, metadata, native, False)[1]) == {"reward"}


@pytest.mark.parametrize(
    "damage", ["missing_identity", "different_reward", "inconsistent_tests", "infrastructure_failure"]
)
def test_does_not_promote_incomplete_or_infrastructure_results(graded_failure, damage):
    trial, native = graded_failure
    if damage == "missing_identity":
        (native / "agent/source-identity.json").unlink()
    if damage == "different_reward":
        (native / "verifier/reward.txt").write_text("0")
    if damage == "inconsistent_tests":
        file = native / "verifier/ctrf.json"
        data = json.loads(file.read_text())
        data["results"]["summary"]["tests"] = 2
        file.write_text(json.dumps(data))
    if damage == "infrastructure_failure":
        trial.exception_info.exception_type = "UnknownApiError"
    assert retained_grader_reward(trial, native, "failed").value is None


def test_pytest_fallback_requires_completed_consistent_tests():
    prefix = "================ test session starts ================\ncollected 1 item\n"
    failed = prefix + "================ 1 failed in 75.89s (0:01:15) ================\n"
    assert completed_pytest(failed, 0.0)
    assert not completed_pytest(failed, 1.0)
    assert not completed_pytest(failed.replace("collected 1", "collected 2"), 0.0)
    assert not completed_pytest(prefix + "curl: command not found\nuvx: command not found", 0.0)
    assert not completed_pytest(failed.replace("1 failed", "1 error"), 0.0)
    assert not completed_pytest(prefix, 0.0)


def test_capacity_mutation_requires_explicit_policy_and_exact_identity(graded_failure):
    trial, native = graded_failure
    trial.exception_info.exception_type = "UnknownApiError"
    (native / "agent/source-identity.json").write_text(json.dumps({"variant_id": "nooa-atom-m-001"}))
    (native / "agent/result.json").write_text(
        json.dumps(
            {
                "error": "turn failed: Original error: ValueError: M-001: protected content exceeds reduced input capacity"
            }
        )
    )
    metadata = {"status": "failed", "last_failure_code": "UnknownApiError"}
    assert observation_measurements(trial, metadata, native, True)[1]["verified_grader_reward"].value is None
    assert (
        observation_measurements(trial, metadata, native, True, "terminal-agent-capacity-v2")[1][
            "verified_grader_reward"
        ].value
        == 1
    )
    (native / "agent/source-identity.json").write_text(json.dumps({"variant_id": "nooa-atom-baseline"}))
    assert (
        observation_measurements(trial, metadata, native, True, "terminal-agent-capacity-v2")[1][
            "verified_grader_reward"
        ].value
        is None
    )


def test_completed_service_does_not_validate_a_failed_grader_setup(graded_failure):
    trial, native = graded_failure
    trial.exception_info = None
    (native / "verifier/ctrf.json").unlink()
    (native / "verifier/test-stdout.txt").write_text("apt: 404 Not Found\nuvx: command not found\n")
    metadata = {"status": "succeeded", "reward": 1.0}
    status, metrics = observation_measurements(trial, metadata, native, True, "terminal-agent-capacity-v2")
    assert status == "succeeded"
    assert metrics["reward"].value == 1.0
    assert metrics["verified_grader_reward"].value is None
    assert observation_measurements(trial, metadata, native, True)[1]["verified_grader_reward"].value == 1.0


def test_repetition_grader_uses_its_own_failure_not_the_member_aggregate(graded_failure):
    trial, native = graded_failure
    metadata = {"status": "succeeded", "n_attempts": 5, "last_failure_code": None}
    status, metrics = observation_measurements(trial, metadata, native, True)
    assert status == "error"
    assert metrics["reward"].value is None
    assert metrics["verified_grader_reward"].value == 1.0
    trial.exception_info.exception_type = "SandboxStartError"
    assert observation_measurements(trial, metadata, native, True)[1]["verified_grader_reward"].value is None
