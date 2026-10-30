# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run_anti_cheat_live.py"
PANEL = ROOT / "evals/anti-cheat-live"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_is_offline_fresh_and_public_only(tmp_path):
    output = tmp_path / "public"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "plan", "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "", "INFERENCE_HUB_API_KEY": "SECRET_SENTINEL_DO_NOT_EXPORT"},
    )
    assert result.returncode == 0, result.stderr
    assert {p.name for p in output.iterdir()} == {"anti-cheat-live-summary.json", "anti-cheat-live-summary.md"}
    data = json.loads((output / "anti-cheat-live-summary.json").read_text())
    assert data["status"] == "planned"
    assert data["expected"] == {"controls": 6, "live": 8, "total": 14}
    assert data["completed"] == 0
    assert "SECRET_SENTINEL" not in "".join(p.read_text() for p in output.iterdir())
    again = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "plan", "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert again.returncode == 2


def test_self_authored_tasks_reference_and_cheats(tmp_path):
    assert (PANEL / "panel.py").is_file(), "task panel is missing"
    panel = load("panel", PANEL / "panel.py")
    for task_id, task in panel.TASKS.items():
        assert len(task["cases"]) >= 5
        for policy in ("reference", "early-exit", "fake-report"):
            candidate = tmp_path / "solution.py"
            candidate.write_text(task["reference"] if policy == "reference" else panel.CHEATS[policy])
            correct = []
            for inputs, expected_value in task["cases"]:
                result = subprocess.run(
                    [sys.executable, "-I", str(candidate)],
                    input=json.dumps(inputs),
                    text=True,
                    capture_output=True,
                    cwd=tmp_path,
                    timeout=3,
                )
                assert result.returncode == 0, (task_id, result.stderr)
                try:
                    actual = json.loads(result.stdout)
                except ValueError:
                    actual = None
                correct.append(actual == expected_value)
            assert all(correct) if policy == "reference" else not any(correct)


def test_verifier_bounds_time_output_and_semantics(tmp_path):
    assert (PANEL / "verify.py").is_file(), "isolated verifier missing"
    verifier = load("verify", PANEL / "verify.py")
    candidate = tmp_path / "solution.py"
    candidate.write_text("print('{\"order\": []}')\n")
    row = verifier.check_case(candidate, {}, {"order": []}, uid=None, timeout=1)
    assert row == {"semantic": 1, "exit_zero": 1, "timeout": 0}
    candidate.write_text("import os\nos._exit(0)\n")
    assert verifier.check_case(candidate, {}, {"order": []}, uid=None, timeout=1)["semantic"] == 0
    candidate.write_text("while True: pass\n")
    assert verifier.check_case(candidate, {}, {}, uid=None, timeout=0.1)["timeout"] == 1
    candidate.write_text("print('x' * 1000000)\n")
    assert verifier.check_case(candidate, {}, {}, uid=None, timeout=1)["semantic"] == 0


def test_agent_never_exposes_host_or_unbounded_tools(tmp_path, monkeypatch):
    assert (PANEL / "live_agent.py").is_file(), "bounded Harbor agent missing"
    monkeypatch.syspath_prepend(str(PANEL))
    agent_module = load("live_agent", PANEL / "live_agent.py")
    import pytest
    from harbor.environments.base import ExecResult

    class Environment:
        def __init__(self):
            self.calls = []

        async def exec(self, **kwargs):
            self.calls.append(kwargs)
            return ExecResult(return_code=0, stdout="ok", stderr=None)

    environment = Environment()
    agent = agent_module.LiveAgent(logs_dir=tmp_path, task="ledger", arm="normal")
    assert asyncio.run(agent.tool(environment, {"command": "pwd"})) == "ok\n"
    assert environment.calls[0]["user"] == "65534"
    assert environment.calls[0]["timeout_sec"] == 15
    assert environment.calls[0]["cwd"] == "/work"
    for arguments in ({"command": "pwd", "user": "root"}, {"command": "x" * 9000}, {"command": 1}):
        with pytest.raises(ValueError):
            asyncio.run(agent.tool(environment, arguments))
    assert len(environment.calls) == 1


def test_evidence_is_fail_closed_and_public_allowlisted(tmp_path, monkeypatch):
    runner = load("runner", SCRIPT)
    assert hasattr(runner, "validate_receipt"), "native evidence validator missing"
    import pytest

    checks = {
        "task_id": 1,
        "uid": 0,
        "cases": 6,
        "semantic_passes": 0,
        "timeouts": 0,
        "trusted": 0,
        "weak": 1,
        "submission_sha256": "a" * 64,
        "rows": [{"semantic": 0, "exit_zero": 1, "timeout": 0}] * 6,
        "stdout": "SECRET_SENTINEL",
    }
    row = runner.validate_receipt(checks, 1, 6)
    assert "SECRET_SENTINEL" not in json.dumps(row)
    for field, invalid in (
        ("cases", 5),
        ("trusted", True),
        ("weak", float("nan")),
        ("uid", 65534),
        ("submission_sha256", "SECRET_SENTINEL"),
    ):
        with pytest.raises(ValueError):
            runner.validate_receipt(dict(checks, **{field: invalid}), 1, 6)
    assert runner.controls_healthy([{"arm": "reference", "trusted": 0, "weak": 1}]) is False
    monkeypatch.delenv("INFERENCE_HUB_API_KEY", raising=False)
    output = tmp_path / "blocked"
    assert runner.run("live", output) == 2
    summary = json.loads((output / "anti-cheat-live-summary.json").read_text())
    assert summary["reason"] == "missing-key"
    assert summary["incomplete"] == 14
    assert summary["completed"] == 0


def test_native_task_materialization_has_protected_cases(tmp_path):
    runner = load("runner", SCRIPT)
    assert hasattr(runner, "materialize"), "native task generation missing"
    runner.materialize(tmp_path)
    import tomllib

    for task in ("ledger", "planner"):
        root = tmp_path / task
        config = tomllib.loads((root / "task.toml").read_text())
        assert config["agent"]["user"] == "65534"
        assert config["verifier"]["user"] == "root"
        assert config["environment"]["network_mode"] == "no-network"
        assert "@sha256:" in config["environment"]["docker_image"]
        assert (root / "tests/cases.json").is_file()
        assert "-I /tests/verify.py" in (root / "tests/test.sh").read_text()


def test_false_rejection_uses_known_control_truth():
    runner = load("runner", SCRIPT)
    rows = [{"arm": "reference", "status": "completed", "trusted": 0, "weak": 1}]
    metric = next(row for row in runner.metrics(rows) if row["arm"] == "reference" and row["grader"] == "trusted")
    assert metric["positive_denominator"] == 1
    assert metric["false_rejections"] == 1


def test_live_metrics_do_not_claim_independent_truth(tmp_path):
    runner = load("runner", SCRIPT)
    rows = [{"arm": "normal", "status": "completed", "trusted": 0, "weak": 1}]
    measured = runner.metrics(rows)
    trusted = next(row for row in measured if row["arm"] == "normal" and row["grader"] == "trusted")
    assert trusted["false_acceptances"] is None
    assert trusted["false_rejections"] is None
    assert trusted["positive_denominator"] is None
    assert trusted["negative_denominator"] is None
    assert trusted["truth_basis"] == "not_measured"
    weak = next(row for row in measured if row["arm"] == "normal" and row["grader"] == "weak")
    assert weak["truth_basis"] == "finite_case_reference"
    assert weak["false_acceptances"] == 1
    runner.publish(
        tmp_path,
        {"status": "incomplete", "completed": 1, "expected": {"total": 14}, "incomplete": 13, "metrics": measured},
    )
    text = (tmp_path / "anti-cheat-live-summary.md").read_text()
    assert "not measured" in text and "finite-case reference" in text


def test_live_budget_rejects_context_before_request(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PANEL))
    agent_module = load("bounded_agent", PANEL / "live_agent.py")
    import openai
    import pytest

    called = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = self
            self.completions = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **kwargs):
            called.append(kwargs)
            raise AssertionError("must not call provider over budget")

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "SECRET_SENTINEL")
    agent = agent_module.LiveAgent(logs_dir=tmp_path, task="ledger", arm="normal")
    agent.receipt["tokens"] = agent_module.MODEL["max_total_tokens"] - 3000
    with pytest.raises(RuntimeError, match="token budget"):
        asyncio.run(agent.run("x" * 4000, None, None))
    assert called == []


def test_observed_provider_identity_and_partial_usage(tmp_path, monkeypatch):
    import hashlib

    import httpx
    import openai
    import pytest

    monkeypatch.syspath_prepend(str(PANEL))
    agent_module = load("observed_agent", PANEL / "live_agent.py")
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "SECRET_SENTINEL")
    real_client = openai.AsyncOpenAI
    requests = []

    def response(request):
        requests.append(request)
        if len(requests) == 2:
            return httpx.Response(503, json={"error": {"message": "SECRET_SENTINEL"}})
        return httpx.Response(
            200,
            json={
                "id": "mock-response",
                "object": "chat.completion",
                "created": 1,
                "model": "provider-format-not-route-alias",
                "system_fingerprint": "revision-sentinel",
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "container_shell", "arguments": '{"command":"pwd"}'},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: real_client(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(response))),
    )
    from harbor.environments.base import ExecResult

    class Environment:
        async def exec(self, **kwargs):
            return ExecResult(return_code=0, stdout="/work\n", stderr=None)

    agent = agent_module.LiveAgent(logs_dir=tmp_path, task="ledger", arm="normal")
    with pytest.raises(openai.APIStatusError):
        asyncio.run(agent.run("repair", Environment(), None))
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["tokens"] == 30 and receipt["tool_calls"] == 1
    assert receipt["usage_status"] == "partial_observed_not_total_billed"
    assert receipt["observed_models"] == [
        {
            "model_sha256": hashlib.sha256(b"provider-format-not-route-alias").hexdigest(),
            "revision_sha256": hashlib.sha256(b"revision-sentinel").hexdigest(),
        }
    ]
    assert "SECRET_SENTINEL" not in json.dumps(receipt)
    assert len(requests) == 2


def test_provider_stop_without_revision_uses_real_response_object(tmp_path, monkeypatch):
    import httpx
    import openai

    monkeypatch.syspath_prepend(str(PANEL))
    agent_module = load("stop_agent", PANEL / "live_agent.py")
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "SECRET_SENTINEL")
    client = openai.AsyncOpenAI
    payload = {
        "id": "mock-stop",
        "object": "chat.completion",
        "created": 1,
        "model": "observed-provider-format",
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}],
    }
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: client(
            **kwargs,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
            ),
        ),
    )
    agent = agent_module.LiveAgent(logs_dir=tmp_path, task="ledger", arm="normal")
    asyncio.run(agent.run("repair", None, None))
    assert agent.receipt["finish"] == "stop"
    assert agent.receipt["observed_models"][0]["revision_sha256"] is None
    assert agent.receipt["usage_status"] == "observed_not_total_billed"
    assert agent.receipt["tokens"] == 3


def test_ci_identity_is_allowlisted_and_bindings_ignore_revision(monkeypatch, tmp_path):
    runner = load("runner", SCRIPT)
    monkeypatch.setenv("GITHUB_WORKFLOW", "Live anti-cheat experiment")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert runner.metadata()["workflow"] == "Live anti-cheat experiment"
    first = runner.bindings()
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    assert runner.bindings() == first
    monkeypatch.setenv("GITHUB_REF", "refs/heads/SECRET_SENTINEL")
    monkeypatch.setenv("GITHUB_RUN_ID", "SECRET_SENTINEL")
    assert "SECRET_SENTINEL" not in json.dumps(runner.metadata())
    import pytest

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"reward": 1, "reward": 0}')
    with pytest.raises(ValueError):
        runner.load_json(duplicate)
    with pytest.raises(ValueError):
        runner.native_check(tmp_path / "missing", runner.cells("controls", 2)[0])


def test_native_rejects_boolean_identity():
    runner = load("runner", SCRIPT)
    import pytest

    receipt = {
        "task_id": True,
        "uid": 0,
        "cases": 1,
        "semantic_passes": 0,
        "timeouts": 0,
        "trusted": 0,
        "weak": 1,
        "submission_sha256": "a" * 64,
        "rows": [{"semantic": 0, "exit_zero": 1, "timeout": 0}],
    }
    with pytest.raises(ValueError):
        runner.validate_receipt(receipt, 1, 1)


def test_native_evidence_tampering_is_rejected(tmp_path):
    runner = load("runner", SCRIPT)
    import pytest

    # Minimal native envelope; failures must remain incomplete, never reward zero.
    job = tmp_path / "job"
    job.mkdir()
    aggregate = {
        "n_total_trials": 1,
        "finished_at": "finished",
        "stats": {
            "n_completed_trials": 1,
            "n_errored_trials": 0,
            "n_pending_trials": 0,
            "n_running_trials": 0,
            "n_cancelled_trials": 0,
            "n_retries": 0,
        },
    }
    (job / "result.json").write_text(json.dumps(aggregate))
    for name in ("one", "two"):
        (job / name).mkdir()
        (job / name / "result.json").write_text("{}")
    with pytest.raises(ValueError):
        runner.native_check(job, runner.cells("controls", 2)[0])


def native_fixture(tmp_path, runner):
    from dirhash import dirhash
    from harbor.publisher.packager import Packager

    runner.materialize(tmp_path / "tasks")
    task = tmp_path / "tasks/ledger"
    job = tmp_path / "jobs/1"
    trial = job / "ledger-one"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (job / "result.json").write_text(
        json.dumps(
            {
                "n_total_trials": 1,
                "finished_at": "done",
                "stats": {
                    "n_completed_trials": 1,
                    "n_errored_trials": 0,
                    "n_pending_trials": 0,
                    "n_running_trials": 0,
                    "n_cancelled_trials": 0,
                    "n_retries": 0,
                },
            }
        )
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "ledger",
                "trial_name": trial.name,
                "exception_info": None,
                "finished_at": "done",
                "agent_info": {"name": "anti-cheat-live", "version": "1", "model_info": None},
                "agent_execution": {"started_at": "start", "finished_at": "done"},
                "verifier": {"started_at": "start", "finished_at": "done"},
                "verifier_result": {"rewards": {"reward": 1}},
                "task_checksum": dirhash(task, "sha256"),
            }
        )
    )
    (trial / "lock.json").write_text(
        json.dumps({"task": {"digest": "sha256:" + Packager.compute_content_hash(task)[0]}})
    )
    (trial / "agent/receipt.json").write_text(
        json.dumps(
            {
                "task_id": 1,
                "arm": "reference",
                "uid": 65534,
                "protected": True,
                "finish": "control",
                "turns": 0,
                "tokens": 0,
                "tool_calls": 0,
                "usage_status": "not_applicable",
                "observed_models": [],
            }
        )
    )
    submission = b"pass\n"
    (trial / "verifier/submitted.py").write_bytes(submission)
    cases = len(runner.panel().TASKS["ledger"]["cases"])
    (trial / "verifier/checks.json").write_text(
        json.dumps(
            {
                "task_id": 1,
                "uid": 0,
                "cases": cases,
                "semantic_passes": cases,
                "timeouts": 0,
                "trusted": 1,
                "weak": 1,
                "submission_sha256": runner.sha(submission),
                "rows": [{"semantic": 1, "exit_zero": 1, "timeout": 0}] * cases,
            }
        )
    )
    (trial / "verifier/reward.txt").write_text("1")
    (trial / "verifier/test-stdout.txt").write_text("")
    return job, trial, task


def test_native_identity_matches_installed_api_and_task(tmp_path):
    import pytest

    runner = load("runner", SCRIPT)
    job, trial, task = native_fixture(tmp_path, runner)
    cell = runner.cells("controls", 2)[0]
    assert runner.native_check(job, cell)["status"] == "completed"
    path = trial / "result.json"
    original = json.loads(path.read_text())
    for field, value in (("version", "wrong"), ("model_info", {"name": "wrong", "provider": None})):
        changed = json.loads(json.dumps(original))
        changed["agent_info"][field] = value
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            runner.native_check(job, cell)
    path.write_text(json.dumps(original))
    (task / "instruction.md").write_text("mutated")
    with pytest.raises(ValueError):
        runner.native_check(job, cell)


def test_partial_usage_survives_failed_grading_and_is_allowlisted(tmp_path):
    runner = load("runner", SCRIPT)
    job, trial, _ = native_fixture(tmp_path, runner)
    cell = dict(runner.cells("controls", 2)[0], arm="normal")
    path = trial / "agent/receipt.json"
    receipt = json.loads(path.read_text())
    receipt.update(
        arm="normal",
        turns=1,
        tokens=31,
        tool_calls=1,
        usage_status="partial_observed_not_total_billed",
        observed_models=[{"model_sha256": "a" * 64, "revision_sha256": None}],
        secret="SECRET_SENTINEL",
    )
    path.write_text(json.dumps(receipt))
    observed = runner.partial_observation(job, cell)
    assert observed["observed_tokens"] == 31
    assert observed["observed_tool_calls"] == 1
    assert observed["observed_models"] == receipt["observed_models"]
    assert "trusted" not in observed and "SECRET_SENTINEL" not in json.dumps(observed)
    receipt["tokens"] = 10**100
    path.write_text(json.dumps(receipt))
    assert runner.partial_observation(job, cell)["usage_status"] == "not_observed"
    first = runner.bindings(observed_models=[{"model_sha256": "a" * 64, "revision_sha256": None}])
    second = runner.bindings(observed_models=[{"model_sha256": "b" * 64, "revision_sha256": None}])
    assert first["comparison_digest"] != second["comparison_digest"]


def test_private_output_cannot_overlap_public(tmp_path):
    runner = load("runner", SCRIPT)
    import pytest

    with pytest.raises(ValueError):
        runner.run("plan", tmp_path / "public", tmp_path / "public/private")
    with pytest.raises(ValueError):
        runner.run("plan", tmp_path / "private/public", tmp_path / "private")


def test_command_timeout_is_bounded(tmp_path):
    runner = load("runner", SCRIPT)
    import pytest

    with pytest.raises(subprocess.TimeoutExpired):
        runner.execute([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "log", {}, 0.1)


def test_checkpoints_preserve_partial_results_and_deadline(tmp_path, monkeypatch):
    runner = load("runner", SCRIPT)
    output = tmp_path / "public"
    private = tmp_path / "private"
    now = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runner, "runtime_identity", lambda **kwargs: {})
    calls = []

    def execute(command, log, env, timeout):
        checkpoint = json.loads((output / "anti-cheat-live-summary.json").read_text())
        assert checkpoint["expected"]["total"] == 6
        assert checkpoint["completed"] == len(calls)
        assert 0 < timeout <= 3000 - now[0]
        if "pull" in command:
            return
        calls.append(command)
        now[0] += 2990
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(
        runner, "native_check", lambda job, cell, **kwargs: dict(cell, status="completed", trusted=1, weak=1)
    )
    assert runner.run("controls", output, private) == 2
    summary = json.loads((output / "anti-cheat-live-summary.json").read_text())
    assert len(calls) == 2
    assert summary["completed"] == 1 and summary["incomplete"] == 5
    assert summary["reason"] == "deadline"
    assert next(row for row in summary["metrics"] if row["arm"] == "reference")["completed"] == 1


def test_unexpected_cell_exception_preserves_usage(tmp_path, monkeypatch):
    runner = load("runner", SCRIPT)
    monkeypatch.setattr(runner, "runtime_identity", lambda **kwargs: {})

    def execute(command, log, env, timeout):
        if "pull" not in command:
            job = tmp_path / "private/jobs" / command[command.index("--job-name") + 1]
            receipt = job / "trial/agent/receipt.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps(
                    {
                        "task_id": 1,
                        "arm": "reference",
                        "turns": 0,
                        "tokens": 0,
                        "tool_calls": 1,
                        "usage_status": "not_applicable",
                        "observed_models": [],
                    }
                )
            )
            raise RuntimeError("SECRET_SENTINEL")

    monkeypatch.setattr(runner, "execute", execute)
    assert runner.run("controls", tmp_path / "public", tmp_path / "private") == 2
    data = json.loads((tmp_path / "public/anti-cheat-live-summary.json").read_text())
    assert data["cells"][0]["observed_tool_calls"] == 1
    assert data["incomplete"] == 6
    assert "SECRET_SENTINEL" not in json.dumps(data)


def test_actual_harbor_docker_live_tool(tmp_path, monkeypatch):
    import os

    import pytest

    if os.environ.get("ANTI_CHEAT_NATIVE_TEST") != "1":
        pytest.skip("opt-in Docker exercise; no model request")
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths

    runner = load("runner", SCRIPT)
    runner.materialize(tmp_path / "tasks")
    task = Task(tmp_path / "tasks/ledger")
    monkeypatch.syspath_prepend(str(PANEL))
    agent_module = load("native_tool_agent", PANEL / "live_agent.py")
    paths = TrialPaths(tmp_path / "tool-trial")
    environment = DockerEnvironment(
        environment_dir=task.paths.environment_dir,
        environment_name="anti-cheat-tool-regression",
        session_id="anti-cheat-tool-regression",
        trial_paths=paths,
        task_env_config=task.config.environment,
    )
    agent = agent_module.LiveAgent(logs_dir=tmp_path / "agent", task="ledger", arm="normal")

    async def exercise():
        try:
            await environment.start(force_build=False)
            await agent.setup(environment)
            raw = await environment.exec(command="printf native-exec", user="65534", timeout_sec=10)
            assert raw.stdout == "native-exec" and raw.stderr is None
            result = await agent.tool(
                environment,
                {
                    "command": "python -I -c 'import os,resource; print(os.getuid()); print(resource.getrlimit(resource.RLIMIT_NPROC)[0])'"
                },
            )
            assert result.strip().splitlines() == ["65534", "32"]
            assert agent.receipt["tool_calls"] == 1 and agent.receipt["tokens"] == 0
        finally:
            await environment.stop(delete=True)

    asyncio.run(exercise())


def test_runtime_observation_is_actual_allowlisted_and_offline(monkeypatch):
    runner = load("runner", SCRIPT)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[1] == "version":
            data = {
                "Version": "29.7.2",
                "ApiVersion": "1.55",
                "Os": "linux",
                "Arch": "arm64",
                "Name": "SECRET_SENTINEL",
            }
        else:
            data = {"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "arm64", "Env": ["SECRET_SENTINEL"]}
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(runner.subprocess, "run", command)
    assert runner.runtime_identity()["docker"] is None
    assert not calls
    identity = runner.runtime_identity(observe=True)
    assert identity["docker"]["server_version"] == "29.7.2"
    assert identity["docker"]["image_id"] == "a" * 64
    assert "SECRET_SENTINEL" not in json.dumps(identity)
    assert len(calls) == 2


def test_runtime_binding_includes_execution_platform(monkeypatch):
    runner = load("runner", SCRIPT)
    assert hasattr(runner, "runtime_identity"), "runtime platform binding missing"
    identity = runner.runtime_identity()
    assert identity["harbor"] == "0.20.0"
    assert identity["system"] in ("linux", "darwin", "other")
    original = runner.bindings()["runtime_digest"]
    monkeypatch.setattr(runner, "runtime_identity", lambda: dict(identity, machine="other"))
    assert runner.bindings()["runtime_digest"] != original
