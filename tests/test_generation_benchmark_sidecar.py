# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from generation_benchmark.models import GenerationAgent, read_yaml
from generation_benchmark.validation import check_bundle, resolve_harbor_agent_config, task_checksum
from generation_controls import LOG_NAME, SCRIPT_CONTROL_IMPORT_PATH, ScriptControl
from generator import sidecar as sidecar_module
from generator.harbor_shim import METADATA_HEADER, extract_tar, pack_tree
from generator.sidecar import (
    HarborRun,
    HarborSidecar,
    build_job_config,
    build_trial_config,
    default_bind_host,
)
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig

GEN_LAB_ROOT = Path(__file__).resolve().parents[1] / "labs" / "generation-benchmark"
SMOKE_CASES = GEN_LAB_ROOT / "examples" / "smoke"
SMOKE_TASK = SMOKE_CASES / "tasks" / "smoke" / "alpha"
HARBOR_CLI = Path(sys.executable).with_name("harbor")
SECRET = "sk-sidecar-test-secret-value"


def smoke_agent() -> tuple[GenerationAgent, AgentConfig]:
    agent = GenerationAgent.model_validate(read_yaml(SMOKE_CASES / "agents" / "smoke" / "agent.yml"))
    return agent, resolve_harbor_agent_config(agent, SMOKE_CASES)


def make_task(root: Path, name: str = "alpha") -> Path:
    task = root / name
    shutil.copytree(SMOKE_TASK, task)
    (task / ".eval-author-lineage.json").write_text('{"lineage": true}\n', encoding="utf-8")
    (task / "private").mkdir()
    (task / "private" / "incorrect.sh").write_text("mkdir -p /app && printf wrong > /app/answer\n", encoding="utf-8")
    return task


class FakeHarbor:
    """Records each run and writes a Harbor-shaped trial directory, optionally blocking until released."""

    def __init__(self, *, exit_code: int = 0, block: bool = False) -> None:
        self.runs: list[HarborRun] = []
        self.exit_code = exit_code
        self.release = threading.Event()
        self.started = threading.Event()
        self.active = 0
        self.max_active = 0
        self.seen_files: list[set[str]] = []
        self._lock = threading.Lock()
        if not block:
            self.release.set()

    def __call__(self, run: HarborRun) -> int:
        with self._lock:
            self.runs.append(run)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.seen_files.append({p.relative_to(run.task_dir).as_posix() for p in run.task_dir.rglob("*")})
        self.started.set()
        self.release.wait(10)
        trial = run.output_dir / run.name
        (trial / "agent").mkdir(parents=True)
        agent = run.agent_config.model_dump(mode="json")
        agent["env"] = {"API_KEY": SECRET}
        result = {
            "id": f"uuid-{run.name}",
            "task_name": "smoke/alpha",
            "trial_name": run.name,
            "trial_uri": f"file://{trial}",
            "task_id": {"path": str(run.task_dir)},
            "source": None,
            "task_checksum": task_checksum(HarborTask(run.task_dir)),
            "config": {"job_id": "job-1", "agent": agent, "task": {"path": str(run.task_dir)}},
            "agent_info": {"name": agent.get("name"), "version": "1"},
            "agent_result": {"metadata": {"env": SECRET}},
            "verifier_result": {"rewards": {"reward": 1.0}},
            "exception_info": None,
            "started_at": "2026-01-01T00:00:00",
        }
        (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (trial / "config.json").write_text(json.dumps({"agent": agent}), encoding="utf-8")
        (trial / "trial.log").write_text(f"agent env API_KEY={SECRET}\n", encoding="utf-8")
        with self._lock:
            self.active -= 1
        return self.exit_code


def call(
    sidecar: HarborSidecar,
    method: str,
    path: str,
    *,
    token: str | None = "default",
    body: bytes | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {sidecar.token if token == 'default' else token}"
    if metadata is not None:
        headers[METADATA_HEADER] = json.dumps(metadata)
    request = urllib.request.Request(sidecar.url + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def metadata_for(task: Path, name: str, agent: str = "oracle", **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "agent": agent,
        "client_task_path": str(task),
        "client_trials_dir": str(task.parent / "trials"),
        **extra,
    }


def submit(
    sidecar: HarborSidecar, task: Path, name: str, agent: str = "oracle", **extra: Any
) -> tuple[int, dict[str, Any]]:
    status, body = call(
        sidecar, "POST", "/jobs", body=pack_tree(task, task.name), metadata=metadata_for(task, name, agent, **extra)
    )
    return status, json.loads(body)


def wait_finished(sidecar: HarborSidecar, name: str) -> dict[str, Any]:
    for _ in range(500):
        state = sidecar.job(name)
        if state is not None and state["status"] in ("completed", "failed"):
            return state
        threading.Event().wait(0.02)
    raise AssertionError(f"job {name} did not finish")


@pytest.fixture
def make_sidecar(tmp_path: Path) -> Iterator[Callable[..., HarborSidecar]]:
    started: list[HarborSidecar] = []

    def factory(runner: FakeHarbor | None = None, agent_config: AgentConfig | None = None) -> HarborSidecar:
        agent, config = smoke_agent()
        sidecar = HarborSidecar(
            agent,
            agent_config or config,
            host="127.0.0.1",
            harbor_runner=runner or FakeHarbor(),
            work_root=tmp_path,
        )
        started.append(sidecar.start())
        return sidecar

    yield factory
    for sidecar in started:
        sidecar.stop(timeout=5)


@pytest.mark.parametrize(
    ("method", "path"), [("POST", "/jobs"), ("GET", "/jobs/x"), ("GET", "/jobs/x/artifacts"), ("GET", "/nope")]
)
def test_every_endpoint_requires_the_session_token(make_sidecar, method: str, path: str) -> None:
    sidecar = make_sidecar()
    body = b"x" * 10 if method == "POST" else None
    assert call(sidecar, method, path, token=None, body=body)[0] == 401
    assert call(sidecar, method, path, token="wrong-token", body=body)[0] == 401
    assert call(sidecar, method, path, token="", body=body)[0] == 401
    assert call(sidecar, "GET", "/jobs/x")[0] == 404
    sidecar.revoke()
    assert call(sidecar, method, path, body=body)[0] == 401


def test_token_is_rejected_after_stop_and_each_run_gets_a_fresh_token(make_sidecar) -> None:
    first, second = make_sidecar(), make_sidecar()
    assert first.token != second.token and len(first.token) >= 43
    assert first.host == "127.0.0.1" and first.port > 0
    first.stop(timeout=5)
    assert not first.authorized(f"Bearer {first.token}")
    with pytest.raises(urllib.error.URLError):
        call(first, "GET", "/jobs/x")
    with pytest.raises(RuntimeError, match="starts once"):
        first.start()


def test_jobs_run_one_at_a_time_in_submission_order(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor(block=True)
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    for name in ("first", "second", "third"):
        assert submit(sidecar, task, name)[0] == 202
    assert runner.started.wait(5)
    assert sidecar.job("first")["status"] == "running"
    assert sidecar.job("second")["status"] == "queued"
    assert sidecar.job("third")["status"] == "queued"
    assert [run.name for run in runner.runs] == ["first"]
    runner.release.set()
    for name in ("first", "second", "third"):
        assert wait_finished(sidecar, name)["status"] == "completed"
    assert [run.name for run in runner.runs] == ["first", "second", "third"]
    assert runner.max_active == 1


def test_resubmission_is_idempotent_and_a_conflicting_one_is_rejected(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    status, accepted = submit(sidecar, task, "ref-1")
    assert status == 202 and accepted["status"] == "queued"
    wait_finished(sidecar, "ref-1")
    status, again = submit(sidecar, task, "ref-1")
    assert status == 200 and again["status"] == "completed"
    assert again["bundle_sha256"] == accepted["bundle_sha256"]
    assert len(runner.runs) == 1

    assert submit(sidecar, task, "ref-1", agent="nop")[0] == 409
    (task / "instruction.md").write_text("changed\n", encoding="utf-8")
    status, rejected = submit(sidecar, task, "ref-1")
    assert status == 409 and "different bundle" in rejected["error"]
    assert len(runner.runs) == 1


@pytest.mark.parametrize("agent", ["claude-code", "terminus-2", "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"])
def test_an_agent_outside_the_allowlist_is_rejected_before_harbor(make_sidecar, tmp_path: Path, agent: str) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    status, body = submit(sidecar, make_task(tmp_path), "bad-agent", agent=agent)
    assert status == 400 and "not allowed" in body["error"]
    assert sidecar.job("bad-agent") is None
    assert runner.runs == []


def test_model_or_config_that_differs_from_agent_yml_is_rejected(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    own = "smoke-agent"
    import_path = "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"
    rejected = [
        {"model": "openai/gpt-other"},
        {"config": {"agent": {"import_path": "other.module:Agent"}}},
        {"config": {"agent": {"kwargs": {"max_turns": 99}}}},
        {"config": {"agent": {"import_path": import_path}, "environment": {"type": "docker"}}},
    ]
    for index, extra in enumerate(rejected):
        status, body = submit(sidecar, task, f"own-{index}", agent=own, **extra)
        assert status == 400, body
    for builtin in ("oracle", "nop", SCRIPT_CONTROL_IMPORT_PATH):
        assert submit(sidecar, task, f"{builtin.split(':')[0]}-model", agent=builtin, model="openai/gpt-x")[0] == 400
    assert runner.runs == []
    assert submit(sidecar, task, "own-ok", agent=own, config={"agent": {"import_path": import_path}})[0] == 202


@pytest.mark.parametrize(
    "field", ["host_path", "command", "mounts", "env", "agent_kwargs", "extra_docker_compose", "agent_env"]
)
def test_job_fields_that_reach_the_host_are_rejected(make_sidecar, tmp_path: Path, field: str) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    status, body = submit(sidecar, make_task(tmp_path), "escape", **{field: "/etc"})
    assert status == 400 and field in body["error"]
    assert runner.runs == []


def test_bundle_rule_violations_are_rejected_before_harbor(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    (task / "environment" / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    status, body = submit(sidecar, task, "compose")
    assert status == 400 and "docker-compose" in body["error"]

    task = make_task(tmp_path, "beta")
    (task / "notes.txt").write_text(f"token {sidecar.token}\n", encoding="utf-8")
    status, body = submit(sidecar, task, "token")
    assert status == 400 and "session token" in body["error"]
    assert runner.runs == []
    assert not list(tmp_path.glob("harbor-sidecar-*/job-*"))


def _bundle(tmp_path: Path, task_toml: str = "") -> Path:
    task = make_task(tmp_path)
    if task_toml:
        (task / "task.toml").write_text(task_toml, encoding="utf-8")
    return task


def _snapshot(task: Path) -> list[tuple[str, int, bytes]]:
    return [
        (
            path.as_posix(),
            path.lstat().st_mode,
            str(path.readlink()).encode() if path.is_symlink() else path.read_bytes(),
        )
        for path in sorted(task.rglob("*"))
        if path.is_symlink() or path.is_file()
    ]


def _assert_rejected(task: Path, match: str, token: str | None = None) -> None:
    before = _snapshot(task)
    with pytest.raises(ValueError, match=match):
        check_bundle(task, session_token=token)
    assert _snapshot(task) == before


@pytest.mark.parametrize("name", ["docker-compose.yaml", "Docker-Compose.override.yml", "COMPOSE.YML", "compose.yaml"])
def test_check_bundle_rejects_a_docker_compose_file(tmp_path: Path, name: str) -> None:
    task = _bundle(tmp_path)
    (task / "environment" / name).write_text("services: {}\n", encoding="utf-8")
    _assert_rejected(task, "docker-compose")


@pytest.mark.parametrize(
    "toml",
    [
        '[environment.env]\nTOKEN = "${HOST_TOKEN}"\n',
        '[verifier]\ntimeout_sec = 30\n[verifier.env]\nKEY = "${OPENAI_API_KEY}"\n',
        '[solution.env]\nKEY = "${HOME}"\n',
    ],
)
def test_check_bundle_rejects_a_template(tmp_path: Path, toml: str) -> None:
    _assert_rejected(_bundle(tmp_path, f'version = "1.0"\n[task]\nname = "smoke/alpha"\n{toml}'), "template")


def test_check_bundle_rejects_an_mcp_servers_entry(tmp_path: Path) -> None:
    toml = 'version = "1.0"\n[environment]\n[[environment.mcp_servers]]\nname = "host"\ntransport = "sse"\nurl = "http://x"\n'
    _assert_rejected(_bundle(tmp_path, toml), "mcp_servers")


def test_check_bundle_rejects_a_prebuilt_docker_image(tmp_path: Path) -> None:
    _assert_rejected(
        _bundle(tmp_path, 'version = "1.0"\n[environment]\ndocker_image = "ubuntu:24.04"\n'), "docker_image"
    )


def test_check_bundle_rejects_the_session_token_in_names_and_bytes(tmp_path: Path) -> None:
    token = "tok-0123456789abcdef"
    task = _bundle(tmp_path)
    (task / "tests" / f"{token}.txt").write_text("x\n", encoding="utf-8")
    _assert_rejected(task, "session token", token)
    shutil.rmtree(task)
    task = _bundle(tmp_path)
    (task / "instruction.md").write_text(f"Use {token}.\n", encoding="utf-8")
    _assert_rejected(task, "session token", token)
    check_bundle(task)


def test_check_bundle_rejects_symlinks_and_missing_task_toml(tmp_path: Path) -> None:
    task = _bundle(tmp_path)
    (task / "link").symlink_to("/etc/passwd")
    _assert_rejected(task, "symlinks")
    (task / "link").unlink()
    (task / "task.toml").unlink()
    _assert_rejected(task, "task.toml")


def test_check_bundle_accepts_a_harbor_task_init_bundle(tmp_path: Path) -> None:
    subprocess.run([str(HARBOR_CLI), "task", "init", "demo/fresh"], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / "fresh"
    assert "mcp_servers = []" in (task / "task.toml").read_text(encoding="utf-8")
    check_bundle(task, session_token="tok-0123456789abcdef")
    check_bundle(make_task(tmp_path), session_token="tok-0123456789abcdef")


def test_dotfiles_reach_harbor_and_the_checksum_matches_the_client_task(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    status, accepted = submit(sidecar, task, "dotfile")
    assert status == 202
    assert accepted["task_checksum"] == task_checksum(HarborTask(task))
    wait_finished(sidecar, "dotfile")
    assert ".eval-author-lineage.json" in runner.seen_files[0]
    assert "private/incorrect.sh" in runner.seen_files[0]
    assert runner.runs[0].task_dir.name == "alpha"


def test_returned_result_keeps_identity_fields_and_no_agent_environment(
    make_sidecar, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIDECAR_TEST_API_KEY", SECRET)
    _agent, config = smoke_agent()
    config = config.model_copy(update={"env": {"API_KEY": "${SIDECAR_TEST_API_KEY}"}})
    runner = FakeHarbor()
    sidecar = make_sidecar(runner, agent_config=config)
    task = make_task(tmp_path)
    assert submit(sidecar, task, "agent-1", agent="smoke-agent")[0] == 202
    assert wait_finished(sidecar, "agent-1")["status"] == "completed"
    assert runner.runs[0].env.get("SIDECAR_TEST_API_KEY") == SECRET
    status, archive = call(sidecar, "GET", "/jobs/agent-1/artifacts")
    assert status == 200
    out = tmp_path / "client-trial"
    extract_tar(archive, out)
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert set(result) == {"id", "task_checksum", "exception_info", "verifier_result", "config"}
    assert result["config"] == {
        "agent": {"name": None, "import_path": "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"}
    }
    assert result["verifier_result"] == {"rewards": {"reward": 1.0}}
    assert result["exception_info"] is None and result["id"] == "uuid-agent-1"
    for path in out.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert SECRET not in text and "SIDECAR_TEST_API_KEY" not in text, path
    assert "env" not in json.loads((out / "config.json").read_text(encoding="utf-8"))["agent"]


def test_harbor_runs_without_unrelated_host_secrets(make_sidecar, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UNRELATED_HOST_SECRET", SECRET)
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    submit(sidecar, make_task(tmp_path), "env-check")
    wait_finished(sidecar, "env-check")
    env = runner.runs[0].env
    assert "UNRELATED_HOST_SECRET" not in env
    assert str(GEN_LAB_ROOT) in env["PYTHONPATH"].split(":")


def test_each_allowed_agent_gets_its_own_harbor_agent_config(make_sidecar, tmp_path: Path) -> None:
    runner = FakeHarbor()
    sidecar = make_sidecar(runner)
    task = make_task(tmp_path)
    for name, agent in (("o", "oracle"), ("n", "nop"), ("s", SCRIPT_CONTROL_IMPORT_PATH), ("a", "smoke-agent")):
        assert submit(sidecar, task, name, agent=agent)[0] == 202
        wait_finished(sidecar, name)
    oracle, nop, script, own = (run.agent_config for run in runner.runs)
    assert (oracle.name, oracle.import_path, oracle.kwargs) == ("oracle", None, {})
    assert (nop.name, nop.import_path) == ("nop", None)
    assert script.name is None and script.import_path == SCRIPT_CONTROL_IMPORT_PATH
    assert script.kwargs == {"task_dir": str(runner.runs[2].task_dir)}
    assert own == smoke_agent()[1]

    run = runner.runs[0]
    trial = build_trial_config(run)
    assert (trial.trial_name, trial.trials_dir, trial.task.path) == (run.name, run.output_dir, run.task_dir)
    assert trial.agent == run.agent_config
    job = build_job_config(run)
    assert (job.job_name, job.jobs_dir, job.n_attempts, job.n_concurrent_trials) == (run.name, run.output_dir, 1, 1)
    assert [t.path for t in job.tasks] == [run.task_dir] and job.agents == [run.agent_config]


def test_a_failed_harbor_run_reports_failure_and_keeps_artifacts(make_sidecar, tmp_path: Path) -> None:
    sidecar = make_sidecar(FakeHarbor(exit_code=3))
    submit(sidecar, make_task(tmp_path), "fails")
    state = wait_finished(sidecar, "fails")
    assert state["status"] == "failed" and state["exit_code"] == 3 and "Harbor exited 3" in state["error"]
    assert call(sidecar, "GET", "/jobs/fails/artifacts")[0] == 200


def test_sidecar_refuses_a_harbor_version_other_than_0_24_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sidecar_module, "installed_harbor_version", lambda: "0.25.0")
    agent, config = smoke_agent()
    with pytest.raises(RuntimeError, match="requires Harbor 0.24.0"):
        HarborSidecar(agent, config, host="127.0.0.1")


def test_default_bind_host_is_loopback_on_macos_and_the_bridge_gateway_on_linux(tmp_path: Path) -> None:
    assert default_bind_host("Darwin") == "127.0.0.1"
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho '172.17.0.1 '\n", encoding="utf-8")
    docker.chmod(0o755)
    assert default_bind_host("Linux", docker=str(docker)) == "172.17.0.1"


class FakeEnvironment:
    """Stands in for the trial container: records uploads and runs commands against a fake filesystem."""

    def __init__(self, return_code: int = 0) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.return_code = return_code

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self.files[target_path] = Path(source_path).read_bytes()

    async def exec(self, command: str, cwd=None, env=None, timeout_sec=None, user=None) -> ExecResult:
        self.commands.append(command)
        if command.startswith("bash "):
            return ExecResult(stdout="wrote wrong\n", stderr="", return_code=self.return_code)
        return ExecResult(stdout="", stderr="", return_code=0)


def test_script_control_runs_the_private_script_in_the_trial_container(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    marker = tmp_path / "imported.txt"
    (task / "private" / "evil.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n", encoding="utf-8"
    )
    modules_before = set(sys.modules)
    logs = tmp_path / "logs"
    agent = ScriptControl(logs_dir=logs, task_dir=str(task))
    assert agent.name() == "script-control"
    environment = FakeEnvironment()
    asyncio.run(agent.run("instruction", cast(BaseEnvironment, environment), AgentContext()))
    assert environment.files == {"/tmp/incorrect.sh": (task / "private" / "incorrect.sh").read_bytes()}
    assert environment.commands == ["chmod +x /tmp/incorrect.sh", "bash /tmp/incorrect.sh 2>&1"]
    assert (logs / LOG_NAME).read_text(encoding="utf-8").endswith("exit_code=0\n")
    assert not marker.exists()
    assert not {name for name in set(sys.modules) - modules_before if "evil" in name}

    failing = cast(BaseEnvironment, FakeEnvironment(return_code=1))
    with pytest.raises(RuntimeError, match="exited 1"):
        asyncio.run(agent.run("instruction", failing, AgentContext()))


def test_script_control_requires_a_nonempty_script_under_private(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    with pytest.raises(ValueError, match="task_dir"):
        ScriptControl(logs_dir=tmp_path / "logs")
    with pytest.raises(ValueError, match="under private/"):
        ScriptControl(logs_dir=tmp_path / "logs", task_dir=str(task), script_relpath="solution/solve.sh")
    with pytest.raises(ValueError):
        ScriptControl(logs_dir=tmp_path / "logs", task_dir=str(task), script_relpath="private/../solution/solve.sh")
    (task / "private" / "empty.sh").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="nonempty"):
        ScriptControl(logs_dir=tmp_path / "logs", task_dir=str(task), script_relpath="private/empty.sh")
    with pytest.raises(ValueError, match="missing"):
        ScriptControl(logs_dir=tmp_path / "logs", task_dir=str(task), script_relpath="private/absent.sh")
