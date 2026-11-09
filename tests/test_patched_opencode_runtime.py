# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keyless checks of the guarded, native OpenCode substitution."""

import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHED_VERSION = "1.19.5+nodesink.e991596fecc9"


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    assert (ROOT / "tools/patched_opencode_runtime.py").is_file(), "runtime hook is missing"
    return importlib.import_module("patched_opencode_runtime")


@pytest.fixture
def binary_manifest(tmp_path):
    binary = tmp_path / "opencode"
    binary.write_bytes(b"synthetic binary: never executed")
    binary.chmod(0o700)
    data = dict(
        schema_version=1,
        binary_path=str(binary),
        sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        expected_version=PATCHED_VERSION,
    )
    path = tmp_path / "binary-manifest.json"
    path.write_text(json.dumps(data))
    return path, data


def test_real_wrapper_rejects_ambient_startup_hook_but_clean_interpreter_dispatches(binary_manifest, tmp_path):
    import venv
    from importlib.metadata import distribution

    pytest.importorskip("skillevaluator", reason="requires the isolated pinned live runtime")
    # Model a distro interpreter's automatic customization without modifying the
    # installed runtime or passing PYTHONPATH (which has a separate guard).
    isolated = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(isolated)
    site = isolated / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    (site / "native-runtime.pth").write_text(str(distribution("skillevaluator").locate_file("")) + "\n")
    hook = site / "sitecustomize.py"
    hook.write_text("# Synthetic foreign startup hook; no side effects.\n")
    environment = dict(
        HOME=str(tmp_path),
        PATH="/usr/bin:/bin",
        TMPDIR=str(tmp_path),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHON_DOTENV_DISABLED="1",
        HARBOR_TELEMETRY="0",
        EA_REQUIRE_PATCHED_OPENCODE="1",
        EA_PATCHED_OPENCODE_MANIFEST=str(binary_manifest[0]),
        _EA_LIVE_FAILURE_TOKEN="a" * 64,
    )
    for foreign in (True, False):
        if not foreign:
            hook.unlink()
        failure = tmp_path / f"failure-{foreign}.json"
        environment["_EA_LIVE_FAILURE_PATH"] = str(failure)
        result = subprocess.run(
            [
                str(isolated / "bin/python"),
                str(ROOT / "tools/run_pinned_live_evaluator.py"),
                "tier3",
                "evaluate",
                "--help",
            ],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == (1 if foreign else 0), (result.stdout, result.stderr)
        assert ("Usage:" in result.stdout) is not foreign
        if foreign:
            assert result.stderr == "live evaluator failed; consult the allowlisted summary\n"
            assert json.loads(failure.read_text()) == {
                "schema": "nemo.eval_author.live_runtime_failure.v1",
                "token": "a" * 64,
                "source": "wrapper",
                "reason": "patched_runtime_activation_failed",
            }
        else:
            assert not failure.exists()


def test_manifest_binds_exact_native_version_and_host_hash(runtime, binary_manifest):
    path, data = binary_manifest
    assert runtime.load_manifest(path) == data
    assert runtime.CONFIGURED_VERSION == "1.19.5"
    assert runtime.EXPECTED_VERSION == PATCHED_VERSION


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("expected_version", "1.19.5"),
        ("expected_version", "1.19.5+nodesink.wrong"),
        ("sha256", "A" * 64),
        ("sha256", "0" * 64),
        ("binary_path", "relative"),
        ("binary_path", None),
        ("extra", "not allowed"),
    ],
)
def test_manifest_rejects_invalid_contract(runtime, binary_manifest, field, value):
    path, data = binary_manifest
    path.write_text(json.dumps({**data, field: value}))
    with pytest.raises((ValueError, OSError)):
        runtime.load_manifest(path)


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "malformed",
        "list",
        "duplicate",
        "symlink",
        "binary_symlink",
        "ancestor_symlink",
        "not_executable",
        "changed",
    ],
)
def test_manifest_rejects_unsafe_files(runtime, binary_manifest, tmp_path, problem):
    path, data = binary_manifest
    binary = Path(data["binary_path"])
    if problem == "missing":
        path.unlink()
    elif problem == "malformed":
        path.write_text("{")
    elif problem == "list":
        path.write_text("[]")
    elif problem == "duplicate":
        path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    elif problem == "symlink":
        link = tmp_path / "manifest-link"
        link.symlink_to(path)
        path = link
    elif problem == "binary_symlink":
        link = tmp_path / "binary-link"
        link.symlink_to(binary)
        path.write_text(json.dumps({**data, "binary_path": str(link)}))
    elif problem == "ancestor_symlink":
        link = tmp_path / "linked-dir"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / path.name
    elif problem == "not_executable":
        binary.chmod(0o600)
    elif problem == "changed":
        binary.write_bytes(b"changed")
    with pytest.raises((ValueError, OSError)):
        runtime.load_manifest(path)


@pytest.fixture
def native(runtime, monkeypatch, tmp_path):
    pytest.importorskip("skillevaluator", reason="requires the isolated pinned live runtime")
    from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
    from harbor.agents.installed.opencode import OpenCode
    from harbor.models.agent.context import AgentContext

    runner = importlib.import_module("skillevaluator.tier3.harbor.runner")
    runtime_preflight = importlib.import_module("skillevaluator.tier3.harbor.runtime_preflight")
    SkillEvaluatorGatewayOpenCode = importlib.import_module(
        "skillevaluator.tier3.harbor.local_agents"
    ).SkillEvaluatorGatewayOpenCode
    SkillEvaluatorSecureDockerEnvironment = importlib.import_module(
        "skillevaluator.tier3.harbor.secure_docker_environment"
    ).SkillEvaluatorSecureDockerEnvironment

    monkeypatch.setattr(
        os,
        "environ",
        {
            "HOME": str(tmp_path),
            "OPENAI_BASE_URL": "https://gateway.invalid/v1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HARBOR_TELEMETRY": "0",
            "PYTHON_DOTENV_DISABLED": "1",
        },
    )
    for obj, attr in [
        (OpenCode, "setup"),
        (OpenCode, "install"),
        (BaseInstalledAgent, "_exec"),
        (runner, "_harbor_launch_environment"),
        (runtime_preflight, "_harbor_launch_environment"),
    ]:
        monkeypatch.setattr(obj, attr, getattr(obj, attr))
    return SimpleNamespace(
        Base=BaseInstalledAgent,
        OpenCode=OpenCode,
        Gateway=SkillEvaluatorGatewayOpenCode,
        Secure=SkillEvaluatorSecureDockerEnvironment,
        Context=AgentContext,
        Failure=NonZeroAgentExitCodeError,
        runner=runner,
        preflight=runtime_preflight,
    )


def test_native_contract_matches_installed_distribution(runtime, native):
    assert hasattr(runtime, "verify_contract"), "installed-source contract is missing"
    runtime.verify_contract()


@pytest.mark.parametrize(
    "problem",
    ["source", "revision", "package", "function", "install", "preflight", "gateway", "gateway_run", "gateway_install"],
)
def test_native_contract_rejects_drift_before_mutation(runtime, native, monkeypatch, binary_manifest, problem):
    before = native.OpenCode.setup, native.Base._exec, native.runner._harbor_launch_environment
    if problem == "source":
        monkeypatch.setattr(
            runtime, "SOURCE_HASHES", {**runtime.SOURCE_HASHES, "harbor/agents/installed/opencode.py": "0" * 64}
        )
    elif problem in ("revision", "package"):
        original = runtime.distribution

        def dist(name):
            result = original(name)
            if name == "skillevaluator":
                return SimpleNamespace(
                    version="wrong" if problem == "package" else result.version,
                    read_text=lambda _: json.dumps({"vcs_info": {"vcs": "git", "commit_id": "wrong"}}),
                    locate_file=result.locate_file,
                )
            return result

        monkeypatch.setattr(runtime, "distribution", dist)
    elif problem == "function":
        monkeypatch.setattr(native.Base, "_exec", lambda *a, **kw: None)
    elif problem == "install":
        monkeypatch.setattr(native.OpenCode, "install", lambda *a, **kw: None)
    elif problem in ("gateway", "gateway_run", "gateway_install"):
        attr = {"gateway": "__init__", "gateway_run": "run", "gateway_install": "install"}[problem]
        monkeypatch.setattr(native.Gateway, attr, lambda *a, **kw: None)
    else:
        monkeypatch.setattr(native.preflight, "_harbor_launch_environment", lambda env: env)
    with pytest.raises((ValueError, RuntimeError), match="contract|source|revision|version"):
        runtime.verify_contract()
    assert native.OpenCode.setup is before[0]
    assert native.runner._harbor_launch_environment is before[2]


@pytest.fixture
def boundary(runtime, native, binary_manifest, monkeypatch):
    commands, uploads = [], []
    response = {"sha": binary_manifest[1]["sha256"], "version": PATCHED_VERSION, "code": 0}

    async def execute(self, **kwargs):
        commands.append(kwargs)
        command = kwargs["command"]
        stdout = ""
        if command.endswith("sha256sum " + runtime.REMOTE):
            stdout = f"{response['sha']}  {runtime.REMOTE}\n"
        if command.endswith(runtime.REMOTE + " --version"):
            stdout = f"{response['version']}\n"
        if command.startswith("dpkg-query"):
            stdout = "\n".join(k + "=" + v for k, v in runtime.PACKAGE_VERSIONS.items())
        code = 7 if "run --format=json" in command else (response["code"] if runtime.REMOTE in command else 0)
        if "nvm install 22" in command and response.get("nvm_failure"):
            return SimpleNamespace(return_code=3, stdout="", stderr="Version '22' not found")
        return SimpleNamespace(return_code=code, stdout=stdout, stderr="")

    async def upload(self, source_path, target_path):
        uploads.append((Path(source_path).read_bytes(), target_path, len(commands)))

    monkeypatch.setattr(native.Secure, "exec", execute)
    monkeypatch.setattr(native.Secure, "upload_file", upload)
    return SimpleNamespace(
        commands=commands, uploads=uploads, response=response, environment=object.__new__(native.Secure)
    )


@pytest.mark.parametrize("agent_class", ["OpenCode", "Gateway"])
def test_patched_install_avoids_nvm_failure_preserving_base_and_dependencies(
    runtime, native, boundary, binary_manifest, tmp_path, monkeypatch, agent_class
):
    cls = getattr(native, agent_class)
    original_setup = native.Base.setup
    original_install = native.OpenCode.install
    dependencies = []
    ensure = native.Base.ensure_system_dependencies

    async def record_dependencies(self, environment, required):
        dependencies.append(required)
        await ensure(self, environment, required)

    monkeypatch.setattr(native.Base, "ensure_system_dependencies", record_dependencies)
    boundary.response["nvm_failure"] = True
    baseline = cls(logs_dir=tmp_path / "ordinary", model_name="openai/synthetic", version="1.19.5")
    with pytest.raises(native.Failure, match="Version '22' not found"):
        asyncio.run(baseline.setup(boundary.environment))
    assert native.OpenCode.install is original_install
    assert not boundary.uploads
    assert not (baseline.logs_dir / ".ea-patched-binary.json").exists()
    dependencies.clear()
    boundary.commands.clear()

    runtime.activate(binary_manifest[0])
    agent = cls(logs_dir=tmp_path / "patched", model_name="openai/synthetic", version="1.19.5")
    asyncio.run(agent.setup(boundary.environment))
    assert native.Base.setup is original_setup
    assert native.OpenCode.setup is native.Gateway.setup is original_setup
    assert dependencies == [("curl", "bash", "coreutils", "nodejs", "npm")]
    assert (agent.logs_dir / "setup").is_dir()
    assert boundary.commands[0] == {"command": "[ -d /installed-agent ] || mkdir -p /installed-agent", "user": "root"}
    assert not any("nvm install" in c["command"] or "npm i -g opencode" in c["command"] for c in boundary.commands)
    assert boundary.uploads
    assert (agent.logs_dir / ".ea-patched-binary.json").is_file()
    assert agent._ea_patched_ready is boundary.environment


@pytest.mark.parametrize("agent_class", ["OpenCode", "Gateway"])
@pytest.mark.parametrize("existing_nvm", [False, True])
def test_native_setup_preserves_gateway_argv_pipeline_and_nonzero(
    runtime, native, boundary, binary_manifest, tmp_path, agent_class, existing_nvm
):
    cls = getattr(native, agent_class)

    def make(label):
        return cls(logs_dir=tmp_path / label, model_name="openai/azure/openai/synthetic", version="1.19.5")

    prompt = "quoted --model=not-a-flag; opencode --model=x"
    baseline = make("baseline")
    asyncio.run(baseline.setup(boundary.environment))
    assert "npm i -g opencode-ai@1.19.5" in boundary.commands[-1]["command"]
    ordinary_setup = list(boundary.commands[:-1])
    with pytest.raises(native.Failure):
        asyncio.run(baseline.run(prompt, boundary.environment, native.Context()))
    before = list(boundary.commands)
    boundary.commands.clear()
    assert hasattr(runtime, "activate"), "binary substitution is missing"
    runtime.activate(binary_manifest[0])
    agent = make("patched")
    assert type(agent) is cls
    assert agent._opencode_config == baseline._opencode_config
    assert agent.model_name == baseline.model_name
    asyncio.run(agent.setup(boundary.environment))
    assert boundary.commands[: len(ordinary_setup)] == ordinary_setup
    assert boundary.uploads == [
        (Path(binary_manifest[1]["binary_path"]).read_bytes(), runtime.REMOTE, len(ordinary_setup) + 1)
    ]
    with pytest.raises(native.Failure):
        asyncio.run(agent.run(prompt, boundary.environment, native.Context()))
    original_run = next(c for c in before if "run --format=json" in c["command"])
    patched_run = next(c for c in boundary.commands if "run --format=json" in c["command"])
    restored = {
        **patched_run,
        "command": patched_run["command"].replace(runtime.REMOTE + " --model=", "opencode --model=", 1),
    }
    assert restored == original_run
    assert patched_run["env"]["OPENCODE_FAKE_VCS"] == "git"
    assert patched_run["command"].startswith("set -o pipefail; " + runtime.PREFIX)
    receipt_path = agent.logs_dir / ".ea-patched-binary.json"
    receipt = json.loads(receipt_path.read_text())
    assert set(receipt) == {
        "schema_version",
        "configured_version",
        "expected_version",
        "observed_version",
        "host_sha256",
        "container_sha256",
        "agent_class",
    }
    assert receipt == dict(
        schema_version=1,
        configured_version="1.19.5",
        expected_version=PATCHED_VERSION,
        observed_version=PATCHED_VERSION,
        host_sha256=binary_manifest[1]["sha256"],
        container_sha256=binary_manifest[1]["sha256"],
        agent_class=cls.__module__ + ":" + cls.__name__,
    )
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    # Execute the native shell launcher with only absolute container paths mapped
    # to scratch. This is a scripted nonzero control, not model evidence.
    shell = tmp_path / "shell"
    shell.mkdir()
    if existing_nvm:
        (shell / ".nvm").mkdir()
        (shell / ".nvm/nvm.sh").write_text('printf sourced > "$HOME/nvm-sourced"\n')
    executable = shell / "opencode"
    args_file = shell / "args.json"
    executable.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))\nprint('synthetic stdout')\nprint('synthetic stderr',file=sys.stderr)\nraise SystemExit(7)\n"
    )
    executable.chmod(0o700)
    stdbuf = shell / "stdbuf"
    stdbuf.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    stdbuf.chmod(0o700)
    command = (
        patched_run["command"]
        .replace(runtime.REMOTE, str(executable), 1)
        .replace(agent.environment_logs_dir.as_posix(), str(shell))
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env={"HOME": str(shell), "PATH": str(shell) + ":/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 7
    assert b"synthetic stdout" in result.stdout and b"synthetic stderr" in result.stdout
    assert (shell / "nvm-sourced").exists() is existing_nvm
    assert json.loads(args_file.read_text()) == [
        "--model=" + agent.model_name,
        "run",
        "--format=json",
        "--thinking",
        "--dangerously-skip-permissions",
        "--",
        prompt,
    ]


@pytest.mark.parametrize(
    "problem",
    [
        "sha",
        "version",
        "code",
        "pin",
        "backend",
        "subclass",
        "changed_manifest",
        "missing_binary",
        "dependencies",
        "unsetup",
    ],
)
def test_native_failed_verification_never_runs_or_writes_receipt(
    runtime, native, boundary, binary_manifest, tmp_path, monkeypatch, problem
):
    runtime.activate(binary_manifest[0])
    cls = native.Gateway
    if problem == "subclass":

        class Other(cls):
            pass

        cls = Other
    agent = cls(
        logs_dir=tmp_path / "broken", model_name="openai/synthetic", version="latest" if problem == "pin" else "1.19.5"
    )
    if problem in ("sha", "version", "code"):
        boundary.response[problem] = {"sha": "0" * 64, "version": "1.19.5", "code": 11}[problem]
    elif problem == "changed_manifest":
        Path(binary_manifest[1]["binary_path"]).write_bytes(b"changed")
    elif problem == "missing_binary":
        Path(binary_manifest[1]["binary_path"]).unlink()
    elif problem == "dependencies":

        async def fail_dependencies(self, environment, required):
            raise RuntimeError("system dependency install failed")

        monkeypatch.setattr(native.Base, "ensure_system_dependencies", fail_dependencies)
    env = SimpleNamespace(exec=boundary.environment.exec) if problem == "backend" else boundary.environment
    with pytest.raises((RuntimeError, ValueError)):
        if problem == "unsetup":
            asyncio.run(agent.run("test", env, native.Context()))
        else:
            asyncio.run(agent.setup(env))
    assert not (agent.logs_dir / ".ea-patched-binary.json").exists()
    assert not any("run --format=json" in c["command"] for c in boundary.commands)
    assert getattr(agent, "_ea_patched_ready", None) is None


def test_wrapper_launches_preflight_and_both_arms_with_child_hook(
    runtime, native, binary_manifest, monkeypatch, tmp_path
):
    import run_pinned_live_evaluator as wrapper

    cli = importlib.import_module("skillevaluator.cli")
    from harbor.cli.utils import parse_kwargs

    capture = tmp_path / "children.jsonl"
    failure = tmp_path / "runtime-failure.json"
    monkeypatch.setenv("_EA_LIVE_FAILURE_PATH", str(failure))
    monkeypatch.setenv("_EA_LIVE_FAILURE_TOKEN", "a" * 64)
    recorder = tmp_path / "harbor-recorder"
    recorder.write_text(
        f"#!{sys.executable}\nimport importlib,json,sys\nfrom harbor.agents.installed.opencode import OpenCode\nfrom skillevaluator.tier3.harbor import runner,runtime_preflight\nassert runner._harbor_launch_environment is runtime_preflight._harbor_launch_environment\nassert importlib.import_module('skillevaluator.tier3.harbor.runner') is runner\nassert importlib.import_module('skillevaluator.tier3.harbor.runtime_preflight') is runtime_preflight\nfrom pathlib import Path\nwith Path({str(capture)!r}).open('a') as out:\n out.write(json.dumps({{'argv':sys.argv[1:],'active':getattr(OpenCode.install,'_ea_patched_opencode',False)}})+'\\n')\nraise SystemExit(19)\n"
    )
    recorder.chmod(0o700)
    monkeypatch.setattr(native.runner, "_harbor_bin", lambda: str(recorder))
    monkeypatch.setattr(native.runner, "_HARBOR_AGENT_KWARGS", native.runner._HARBOR_AGENT_KWARGS)
    monkeypatch.setenv("EA_PATCHED_OPENCODE_MANIFEST", str(binary_manifest[0]))
    monkeypatch.setattr(sys, "argv", ["wrapper", "tier3", "evaluate"])
    dataset = tmp_path / "dataset"
    (dataset / "synthetic").mkdir(parents=True)
    (dataset / "synthetic/task.toml").write_text('version = "1.0"\n')
    gateway = "skillevaluator.tier3.harbor.local_agents:SkillEvaluatorGatewayOpenCode"
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HARBOR_TELEMETRY": "0",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    common = dict(
        dataset=dataset,
        agent="opencode",
        model="openai/synthetic",
        env_mode="docker",
        jobs_dir=tmp_path / "jobs",
        run_env=env,
        agent_import_path=gateway,
    )
    baseline = native.runner.build_harbor_run_command(
        dataset_path=dataset, agent="opencode", job_name="baseline", env_mode="docker", agent_import_path=gateway
    )

    def evaluate():
        assert not native.preflight.run_agent_runtime_preflight(**common, timeout_seconds=15).ok
        for arm in ("with", "without"):
            ok, detail = native.runner._run_harbor(
                **common,
                job_name=arm,
                n_attempts=1,
                n_concurrent=1,
                timeout_multiplier=1.0,
                override_cpus=None,
                override_memory_mb=None,
                override_storage_mb=None,
            )
            assert not ok and "exited 19" in detail

    monkeypatch.setattr(cli, "cli", evaluate)
    wrapper.main()
    children = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(children) == 3
    assert all(child["active"] for child in children), "patch was not activated in each actual Harbor child"
    for child in children:
        command = child["argv"]
        assert command[command.index("--agent") + 1] == gateway == baseline[baseline.index("--agent") + 1]
        assert command[command.index("--env") + 1] == native.runner.SECURE_DOCKER_ENV_IMPORT_PATH
        assert parse_kwargs([command[i + 1] for i, value in enumerate(command) if value == "--ak"]) == {
            "version": "1.19.5"
        }
    assert ["--disable-verification" in child["argv"] for child in children] == [True, False, False]
    assert not failure.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("PYTHONPATH", "/other/instrumentation"),
        ("PYTHONPATH", ""),
        ("PYTHONHOME", "/other"),
        ("PYTHONSTARTUP", "/other/start.py"),
        ("_EA_PATCHED_OPENCODE_HOOK", "wrong"),
    ],
)
def test_activation_conflicts_fail_without_clobbering(runtime, native, binary_manifest, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    before = native.OpenCode.setup, native.Base._exec, native.runner._harbor_launch_environment
    with pytest.raises(RuntimeError, match="conflict"):
        runtime.activate(binary_manifest[0])
    assert os.environ[field] == value
    assert (native.OpenCode.setup, native.Base._exec, native.runner._harbor_launch_environment) == before


@pytest.mark.parametrize("arm", ["runner", "preflight"])
def test_launch_environment_preserves_protocol_and_rejects_conflicts(runtime, native, binary_manifest, arm):
    original = native.runner._harbor_launch_environment
    runtime.activate(binary_manifest[0])
    source = {"HOME": "/synthetic", "OPENAI_BASE_URL": "https://gateway.invalid/v1", "SYNTHETIC_MARKER": "untouched"}
    result = getattr(native, arm)._harbor_launch_environment(source)
    activation = runtime.activation_environment(binary_manifest[0])
    assert result == {**original(source), **activation}
    assert source == {
        "HOME": "/synthetic",
        "OPENAI_BASE_URL": "https://gateway.invalid/v1",
        "SYNTHETIC_MARKER": "untouched",
    }
    with pytest.raises(RuntimeError, match="conflict"):
        getattr(native, arm)._harbor_launch_environment({**source, "PYTHONPATH": "/other"})


@pytest.mark.parametrize(
    "problem",
    [
        "missing_manifest",
        "bad_schema",
        "host_hash",
        "native_version",
        "source",
        "package",
        "revision",
        "runtime_hash",
        "manifest_rebound",
        "missing_digest",
        "token",
        "loader_import",
        "prepared_without_binary",
    ],
)
def test_actual_child_startup_failures_exit_78_without_running_main(
    runtime, native, binary_manifest, tmp_path, problem
):
    import shutil

    # A private copy lets us inject source/loader drift without editing an
    # installed package, production file, or a concurrently running worker.
    tools = tmp_path / "tools"
    hook = tools / "patched_opencode_hook"
    hook.mkdir(parents=True)
    copied = tools / "patched_opencode_runtime.py"
    shutil.copyfile(ROOT / "tools/patched_opencode_runtime.py", copied)
    shutil.copyfile(ROOT / "tools/patched_opencode_hook/sitecustomize.py", hook / "sitecustomize.py")
    path, data = binary_manifest
    environment = runtime.activation_environment(path)
    environment.update(
        HOME=str(tmp_path), PATH="/usr/bin:/bin", PYTHONPATH=str(hook), PYTHON_DOTENV_DISABLED="1", HARBOR_TELEMETRY="0"
    )
    source = copied.read_text()
    if problem == "missing_manifest":
        path.unlink()
    elif problem == "bad_schema":
        path.write_text("[]")
    elif problem == "host_hash":
        Path(data["binary_path"]).write_bytes(b"changed")
    elif problem == "native_version":
        path.write_text(json.dumps({**data, "expected_version": "1.19.5"}))
    elif problem == "source":
        source = source.replace(runtime.SOURCE_HASHES["harbor/agents/installed/opencode.py"], "0" * 64)
    elif problem == "package":
        source = source.replace('evaluator.version != "0.7.0"', 'evaluator.version != "wrong"')
    elif problem == "revision":
        source = source.replace(runtime.EVALUATOR_REVISION, "0" * 40)
    elif problem == "runtime_hash":
        environment[runtime.DIGEST_ENV] = "0" * 64
    elif problem == "manifest_rebound":
        other = tmp_path / "another-binary"
        other.write_bytes(b"a different valid synthetic binary")
        other.chmod(0o700)
        path.write_text(
            json.dumps({**data, "binary_path": str(other), "sha256": hashlib.sha256(other.read_bytes()).hexdigest()})
        )
    elif problem == "missing_digest":
        del environment[runtime.MANIFEST_DIGEST_ENV]
    elif problem == "token":
        environment[runtime.TOKEN_ENV] = "wrong"
    elif problem == "loader_import":
        source = "raise RuntimeError('synthetic secret must not escape')\n"
    elif problem == "prepared_without_binary":
        environment.pop(runtime.MANIFEST_ENV)
        environment.pop(runtime.TOKEN_ENV)
        environment["EA_REQUIRE_PREPARED_IMAGES"] = "1"
        environment["EA_PREPARED_IMAGES_MANIFEST"] = "/missing-preparation"
    copied.write_text(source)
    if problem != "runtime_hash":
        environment[runtime.DIGEST_ENV] = hashlib.sha256(copied.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, "-c", "print('MAIN MUST NOT RUN')"], env=environment, capture_output=True, timeout=20
    )
    assert result.returncode == 78, (problem, result.stdout, result.stderr)
    assert result.stdout == b""
    assert result.stderr == b"patched OpenCode activation failed; refusing execution\n"


def test_required_preparation_missing_fails_before_native_mutation(runtime, native, binary_manifest, monkeypatch):
    monkeypatch.setenv("EA_REQUIRE_PREPARED_IMAGES", "1")
    original = native.OpenCode.install
    with pytest.raises(ValueError, match="prepar"):
        runtime.activate(binary_manifest[0])
    assert native.OpenCode.install is original


def test_prepared_install_checks_dependencies_without_repository_fallback(
    runtime, native, boundary, binary_manifest, tmp_path, monkeypatch
):
    assert hasattr(runtime, "prepared_install_check"), "prepared dependency guard is missing"
    checks = []

    async def check(agent, environment):
        checks.append(environment)
        await runtime.prepared_install_check(agent, environment)

    # Exercise the real pinned setup and dependency probe. The Docker lifecycle
    # is covered separately; this boundary supplies its verified identity.
    monkeypatch.setattr(runtime, "prepared_activation", lambda *_: check)
    runtime.activate(binary_manifest[0])
    agent = native.Gateway(logs_dir=tmp_path / "prepared", model_name="openai/synthetic", version="1.19.5")
    asyncio.run(agent.setup(boundary.environment))
    assert checks == [boundary.environment]
    assert any("command -v node" in c["command"] and "command -v npm" in c["command"] for c in boundary.commands)
    assert not any("apt-get" in c["command"] or "npm i" in c["command"] for c in boundary.commands)
    assert boundary.uploads
