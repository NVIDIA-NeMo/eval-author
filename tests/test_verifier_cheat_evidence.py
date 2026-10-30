# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def collector():
    path = ROOT / "tools/collect_verifier_cheat_evidence.py"
    assert path.is_file(), "the native evidence collector must exist"
    spec = importlib.util.spec_from_file_location("verifier_cheat_evidence", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_native_evidence_cannot_pass(tmp_path):
    report = collector().summarize(tmp_path, exit_code=0)
    assert report["status"] == "incomplete"
    assert report["metrics"]["native_trials"] == 0
    assert report["metrics"]["passed_checks"] == 0
    assert report["errors"]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def unit_evidence(output):
    """Synthetic parser inputs only; never used by the native collector runner."""
    module = collector()
    for name, expected in module.PANEL.items():
        grader, control = name.split("--")
        job = output / "jobs" / name
        trial = job / "normalizer__unit"
        write_json(
            job / "result.json",
            {
                "n_total_trials": 1,
                "finished_at": "2026-10-30T12:01:00+00:00",
                "stats": {
                    "n_completed_trials": 1,
                    "n_errored_trials": 0,
                    "n_pending_trials": 0,
                    "n_running_trials": 0,
                    "n_cancelled_trials": 0,
                    "n_retries": 0,
                },
            },
        )
        write_json(
            trial / "result.json",
            {
                "task_name": "normalizer",
                "trial_name": trial.name,
                "task_checksum": "a" * 64,
                "agent_info": {"name": "verifier-cheat-control", "model_info": None},
                "exception_info": None,
                "finished_at": "2026-10-30T12:01:00+00:00",
                "agent_execution": {
                    "started_at": "2026-10-30T12:00:00+00:00",
                    "finished_at": "2026-10-30T12:00:01+00:00",
                },
                "verifier": {"started_at": "2026-10-30T12:00:01+00:00", "finished_at": "2026-10-30T12:00:02+00:00"},
                "verifier_result": {"rewards": {"reward": expected}},
            },
        )
        write_json(
            trial / "agent" / "control.json",
            {
                "control": control,
                "return_code": 0,
                "uid": 65534,
                "verifier_writable": False,
                "tests_writable": False,
            },
        )
        write_json(
            trial / "verifier" / "checks.json",
            {
                "grader": grader,
                "uid": 0,
                "cases": 3,
                "semantic_passes": 3 if control == "reference" else 0,
                "exit_codes": [0, 0, 0],
                "reward": expected,
            },
        )
        (trial / "verifier" / "reward.txt").write_text(str(expected))
        (trial / "verifier" / "test-stdout.txt").write_text("")
        write_json(trial / "lock.json", {"task": {"digest": "sha256:" + "a" * 64}})
    return module


def test_complete_panel_counts_paired_semantics(tmp_path):
    module = unit_evidence(tmp_path)
    report = module.summarize(tmp_path, exit_code=0)
    assert report["status"] == "passed"
    assert report["metrics"] == {
        "native_trials": 9,
        "passed_checks": 9,
        "expected_checks": 9,
        "sound_reference_accepted": 1,
        "sound_exploits_rejected": 2,
        "vulnerable_exploits_rewarded": 2,
        "always_fail_reference_rejected": 1,
    }
    assert report["errors"] == []


@pytest.mark.parametrize("count", [0, 2, True])
def test_incorrect_native_counts_cannot_pass(tmp_path, count):
    module = unit_evidence(tmp_path)
    path = tmp_path / "jobs/sound--reference/result.json"
    data = json.loads(path.read_text())
    data["n_total_trials"] = count
    write_json(path, data)
    assert module.summarize(tmp_path, exit_code=0)["status"] == "incomplete"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_reward",
        "missing_receipt",
        "duplicate_trial",
        "wrong_reward",
        "native_error",
        "writable_verifier",
        "nan_reward",
        "extra_job",
    ],
)
def test_invalid_native_evidence_cannot_pass(tmp_path, mutation):
    module = unit_evidence(tmp_path)
    job = tmp_path / "jobs/sound--reference"
    trial = job / "normalizer__unit"
    if mutation == "missing_reward":
        (trial / "verifier/reward.txt").unlink()
    elif mutation == "missing_receipt":
        (trial / "verifier/checks.json").unlink()
    elif mutation == "duplicate_trial":
        write_json(job / "duplicate/result.json", {})
    elif mutation == "extra_job":
        write_json(tmp_path / "jobs/unexpected/result.json", {})
    elif mutation == "writable_verifier":
        path = trial / "agent/control.json"
        data = json.loads(path.read_text())
        data["verifier_writable"] = True
        write_json(path, data)
    else:
        path = trial / "result.json"
        data = json.loads(path.read_text())
        if mutation == "native_error":
            data["exception_info"] = {"exception_type": "TimeoutError"}
        else:
            data["verifier_result"]["rewards"]["reward"] = 0 if mutation == "wrong_reward" else float("nan")
        write_json(path, data)
    assert module.summarize(tmp_path, exit_code=0)["status"] != "passed"


def test_failed_process_cannot_pass_with_complete_evidence(tmp_path):
    module = unit_evidence(tmp_path)
    assert module.summarize(tmp_path, exit_code=1)["status"] != "passed"


def test_runner_refuses_existing_output(tmp_path):
    with pytest.raises(FileExistsError):
        collector().run(tmp_path)


def test_runner_retains_failed_setup_summary(tmp_path, monkeypatch):
    module = collector()

    def failed_command(*args, **kwargs):
        raise OSError("runtime unavailable")

    monkeypatch.setattr(module, "execute", failed_command)
    output = tmp_path / "evidence"
    assert module.run(output) == 1
    report = json.loads((output / "verifier-cheat-summary.json").read_text())
    assert report["status"] == "incomplete"
    assert report["model_performance_measured"] is False
    assert "runtime unavailable" in report["execution_error"]


def test_fixture_runtime_contract():
    import tomllib

    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task

    fixture = ROOT / "evals/verifier-cheats/normalizer"
    assert Task.is_valid_dir(fixture)
    config = TaskConfig.model_validate(tomllib.loads((fixture / "task.toml").read_text()))
    assert config.environment.network_mode.value == "no-network"
    assert config.environment.docker_image and "@sha256:" in config.environment.docker_image
    assert config.agent.user == "65534"
    assert config.verifier.user == "root"
    assert config.verifier.network_mode and config.verifier.network_mode.value == "no-network"
    assert config.agent.timeout_sec is not None and config.agent.timeout_sec <= 30
    assert config.verifier.timeout_sec <= 30
