# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
                "tests_readable": False,
            },
        )
        write_json(
            trial / "verifier" / "checks.json",
            {
                "grader": grader,
                "uid": 0,
                "cases": 3,
                "semantic_passes": 3 if control == "reference" else 1 if control == "boolean-output" else 0,
                "exit_codes": [None if control == "hang" else 0] * 3,
                "timeouts": 3 if control == "hang" else 0,
                "tests_uid": 0,
                "tests_mode": 0o700,
                "rows": [
                    {
                        "return_code": None if control == "hang" else 0,
                        "timed_out": control == "hang",
                        "semantic_pass": control == "reference" or (control == "boolean-output" and index == 1),
                        "stderr": "hidden-cases-blocked\n" if control == "hidden-case-read" else "",
                    }
                    for index in range(3)
                ],
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
        "native_trials": 18,
        "passed_checks": 18,
        "expected_checks": 18,
        "sound_reference_accepted": 1,
        "sound_exploits_rejected": 5,
        "vulnerable_exploits_rewarded": 4,
        "submission_timeouts_rejected": 3,
        "always_fail_reference_rejected": 1,
    }
    assert report["errors"] == []


def test_control_inventory_and_rewards():
    module = collector()
    controls = {"reference", "early-exit", "fake-success", "hidden-case-read", "hang", "boolean-output"}
    assert set(module.PANEL) == {
        f"{grader}--{control}" for grader in ("sound", "vulnerable", "always-fail") for control in controls
    }
    for name, reward in module.PANEL.items():
        grader, control = name.split("--")
        assert reward == int(
            (grader == "vulnerable" and control != "hang") or (grader == "sound" and control == "reference")
        )
    assert {path.stem for path in (ROOT / "evals/verifier-cheats/controls").glob("*.py")} == controls


@pytest.mark.parametrize("field,value", [("tests_readable", True), ("tests_writable", True)])
def test_readable_or_writable_hidden_cases_cannot_pass(tmp_path, field, value):
    module = unit_evidence(tmp_path)
    path = tmp_path / "jobs/sound--reference/normalizer__unit/agent/control.json"
    data = json.loads(path.read_text())
    data[field] = value
    write_json(path, data)
    assert module.summarize(tmp_path, 0)["status"] == "incomplete"


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
    public = json.loads((output / "verifier-cheat-summary.json").read_text())
    report = json.loads((output / "verifier-cheat-private.json").read_text())
    assert public["execution_failed"] is True
    assert "runtime unavailable" not in json.dumps(public)
    assert report["status"] == "incomplete"
    assert report["model_performance_measured"] is False
    assert "runtime unavailable" in report["execution_error"]


def test_public_summary_is_an_allowlisted_projection():
    module = collector()
    sentinel = "/Users/private-person/secret-path arbitrary-private-error"
    report = {
        "schema": "nemo.eval_author.verifier_cheat_evidence.v2",
        "protocol": "harbor-verifier-cheats-v2",
        "status": "incomplete",
        "source_revision": "a" * 40,
        "inputs_clean": False,
        "panel_digest": "sha256:" + "b" * 64,
        "runtime": {
            "python": "3.13.9",
            "harbor": "0.20.0",
            "docker": {"Server": {"Version": "29.7.2", "Name": sentinel}, "Client": {"Context": sentinel}},
            "image": [{"Id": "sha256:" + "c" * 64, "Config": {"Env": [sentinel]}, "RepoTags": [sentinel]}],
        },
        "execution_error": sentinel,
        "errors": [sentinel],
        "inputs": {sentinel: sentinel},
        "artifacts": {sentinel: sentinel},
        "unexpected": sentinel,
        "metrics": {"native_trials": 1, "unexpected": sentinel},
        "checks": [
            {
                "name": "sound--reference",
                "reward": 1,
                "expected_reward": 1,
                "status": "passed",
                "semantic_passes": 3,
                "timeouts": 0,
                "trial_result": sentinel,
                "task_digest": sentinel,
            }
        ],
    }
    public = module.public_summary(report)
    assert sentinel not in json.dumps(public)
    assert public["runtime"] == {
        "python": "3.13.9",
        "harbor": "0.20.0",
        "docker": "29.7.2",
        "image_id": "sha256:" + "c" * 64,
    }
    assert public["source_revision"] == "a" * 40
    assert public["panel_digest"] == "sha256:" + "b" * 64
    assert public["error_count"] == 1
    assert public["execution_failed"] is True
    assert not {"errors", "execution_error", "inputs", "artifacts"} & public.keys()
    report["runtime"]["docker"]["Server"]["Version"] = sentinel
    report["runtime"]["image"][0]["Id"] = sentinel
    report["source_revision"] = sentinel
    report["panel_digest"] = sentinel
    assert sentinel not in json.dumps(module.public_summary(report))


def load_fixture_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evals/verifier-cheats" / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_verifier_locally(tmp_path, monkeypatch, policy, grader="sound"):
    module = load_fixture_module("verify", "normalizer/tests/verify.py")
    program = tmp_path / "normalize.py"
    program.write_text(policy)
    native_run = subprocess.run

    def local_run(command, **kwargs):
        for key in ("user", "group", "extra_groups"):
            kwargs.pop(key)
        kwargs.update(cwd=tmp_path, timeout=0.1)
        return native_run([sys.executable, "-I", str(program)], **kwargs)

    monkeypatch.setattr(module.subprocess, "run", local_run)
    monkeypatch.setattr(module, "Path", lambda _: tmp_path)
    monkeypatch.setenv("GRADER", grader)
    module.main()
    return json.loads((tmp_path / "checks.json").read_text())


@pytest.mark.parametrize("grader", ["sound", "vulnerable", "always-fail"])
def test_submission_timeout_is_a_zero_reward_not_infrastructure(tmp_path, monkeypatch, grader):
    checks = run_verifier_locally(
        tmp_path, monkeypatch, "import time; print('partial', flush=True); time.sleep(60)", grader
    )
    assert checks["reward"] == 0
    assert checks["semantic_passes"] == 0
    assert checks["exit_codes"] == [None] * checks["cases"]
    assert checks["timeouts"] == checks["cases"]
    assert all(row["timed_out"] and row["stdout"] == "partial\n" for row in checks["rows"])
    assert (tmp_path / "reward.txt").read_text() == "0\n"


@pytest.mark.parametrize("replacement", ["bool(value) if value in (0, 1) else value", "float(value)"])
def test_numeric_lookalikes_do_not_satisfy_integer_contract(tmp_path, monkeypatch, replacement):
    policy = (
        "import json,sys; values=sorted(set(json.load(sys.stdin))); "
        f"print(json.dumps([{replacement} for value in values]))"
    )
    checks = run_verifier_locally(tmp_path, monkeypatch, policy)
    assert checks["reward"] == 0
    assert checks["semantic_passes"] == 1
    assert [row["semantic_pass"] for row in checks["rows"]] == [False, True, False]


def test_valid_json_whitespace_still_passes(tmp_path, monkeypatch):
    checks = run_verifier_locally(
        tmp_path,
        monkeypatch,
        "import json,sys; print(json.dumps(sorted(set(json.load(sys.stdin))), indent=2))",
    )
    assert checks["reward"] == 1


def test_setup_keeps_hidden_cases_private(tmp_path):
    module = load_fixture_module("control_agent", "control_agent.py")

    class Environment:
        async def exec(self, command, **kwargs):
            # Run actual permission operations; host ownership is not container ownership.
            command = command.replace("chown ", "true ")
            for directory in ("/logs", "/tests", "/work", "/controls"):
                command = command.replace(directory, str(tmp_path / directory[1:]))
            result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
            return SimpleNamespace(return_code=result.returncode)

        async def upload_file(self, **kwargs):
            pass

    asyncio.run(module.ControlAgent.setup(SimpleNamespace(control="reference"), Environment()))
    assert (tmp_path / "tests").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "logs").stat().st_mode & 0o777 == 0o700


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
