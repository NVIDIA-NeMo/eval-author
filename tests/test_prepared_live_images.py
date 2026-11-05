# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preparation(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return importlib.import_module("patched_opencode_runtime")


def test_preparation_identity_binds_exact_context_modes_platform_binary_and_runtime(preparation, tmp_path):
    context = tmp_path / "environment"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM python:3.12-slim\n")
    fixture = context / "fixture"
    fixture.write_text("private synthetic input")
    assert hasattr(preparation, "preparation_identity"), "prepared image binding is missing"
    identity = preparation.preparation_identity(context, "linux/arm64", "a" * 64)
    assert len(identity) == 64
    assert identity == preparation.preparation_identity(context, "linux/arm64", "a" * 64)
    assert identity != preparation.preparation_identity(context, "linux/amd64", "a" * 64)
    assert identity != preparation.preparation_identity(context, "linux/arm64", "b" * 64)
    fixture.chmod(0o700)
    assert identity != preparation.preparation_identity(context, "linux/arm64", "a" * 64)
    fixture.write_text("changed")
    assert identity != preparation.preparation_identity(context, "linux/arm64", "a" * 64)
    fixture.unlink()
    fixture.symlink_to(context / "Dockerfile")
    with pytest.raises(ValueError, match="unsafe"):
        preparation.preparation_identity(context, "linux/arm64", "a" * 64)


@pytest.fixture
def native_skill(tmp_path):
    pytest.importorskip("skillevaluator")
    skill = tmp_path / "source" / "synthetic"
    (skill / "evals" / "environment").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: synthetic\ndescription: Synthetic control\n---\nDo the task.\n")
    (skill / "evals/evals.json").write_text(
        json.dumps(
            {"skill_name": "synthetic", "evals": [{"id": 1, "prompt": "Synthetic task", "expected_output": "ok"}]}
        )
    )
    (skill / "evals/environment/Dockerfile").write_text("FROM python:3.12-slim\nRUN printf custom > /custom-marker\n")
    return skill


def test_keyless_staging_preserves_custom_environment_and_both_arms(preparation, native_skill, tmp_path, monkeypatch):
    monkeypatch.setattr("os.environ", {"HOME": str(tmp_path), "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"})
    assert hasattr(preparation, "stage_preparation"), "keyless native staging is missing"
    tasks = preparation.stage_preparation(native_skill, tmp_path / "stage")
    assert set(tasks) == {"with", "without"}
    assert len(tasks["with"]) == len(tasks["without"]) == 1
    for arm, paths in tasks.items():
        context = paths[0] / "environment"
        assert "printf custom > /custom-marker" in (context / "Dockerfile").read_text()
        assert (context / "skills/synthetic/SKILL.md").exists() is (arm == "with")
        assert not list(context.rglob("expected_output"))
    again = preparation.stage_preparation(native_skill, tmp_path / "stage-again")
    for arm in tasks:
        assert preparation.preparation_identity(
            tasks[arm][0] / "environment", "linux/arm64", "a" * 64
        ) == preparation.preparation_identity(again[arm][0] / "environment", "linux/arm64", "a" * 64)


@pytest.fixture
def binary_manifest(tmp_path):
    import hashlib

    binary = tmp_path / "opencode"
    binary.write_bytes(b"synthetic, never executed")
    binary.chmod(0o700)
    manifest = tmp_path / "binary.json"
    manifest.write_text(
        json.dumps(
            dict(
                schema_version=1,
                binary_path=str(binary),
                sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                expected_version="1.19.5+nodesink.e991596fecc9",
            )
        )
    )
    return manifest


def test_prepare_builds_exact_native_contexts_then_verifies_offline_before_publishing(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch
):
    assert hasattr(preparation, "prepare_images"), "keyless prepared image builder is missing"
    commands = []
    images = {}

    def docker(args, **kwargs):
        commands.append(args)
        if args[0] == "info":
            return "linux/arm64"
        if args[:2] == ["image", "inspect"]:
            return json.dumps(images[args[-1]])
        if args[0] == "build":
            tag = args[args.index("-t") + 1]
            identity = tag.split(":")[1]
            images[tag] = dict(
                Id="sha256:" + ("a" if "source" in tag else "b") * 64,
                Os="linux",
                Architecture="arm64",
                Config=dict(User="1000:1000", Labels={"org.eval-author.preparation": identity}),
            )
            if "prepared" in tag:
                layer = (Path(args[-1]) / "Dockerfile").read_text()
                assert all(
                    name + "=" + preparation.PACKAGE_VERSIONS[name] in layer for name in preparation.SYSTEM_DEPENDENCIES
                )
                assert layer.endswith("USER 1000:1000\n")
        if args[0] == "run":
            assert "--network=none" in args and "--pull=never" in args
            assert "--read-only" in args and "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m" in args
            assert "--mount" in args
            return (
                "\n".join(k + "=" + v for k, v in preparation.PACKAGE_VERSIONS.items())
                + "\n"
                + preparation.EXPECTED_VERSION
                + "\n"
                + preparation.load_manifest(binary_manifest)["sha256"]
                + "  /ea-binary\n"
            )
        return ""

    monkeypatch.setattr(preparation, "_docker", docker)
    path = preparation.prepare_images(native_skill, tmp_path / "prepared", binary_manifest)
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert set(data["tasks"]) == {"with", "without"}
    assert len(data["images"]) == 2
    assert len([c for c in commands if c[0] == "run"]) == 2
    assert all(c[0] in {"info", "image", "build", "run"} for c in commands)
    assert preparation.load_preparation(path, binary_manifest) == data


@pytest.mark.parametrize("onbuild", [False, True])
def test_dependency_layer_uses_inspected_parent_and_preserves_execution_metadata(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch, onbuild
):
    images = {}
    layers = []
    source_id = "sha256:" + "a" * 64
    config = dict(
        User="1000:1000",
        WorkingDir="/custom",
        Shell=["/bin/bash", "-ec"],
        Entrypoint=["/custom-entrypoint"],
        Cmd=["custom"],
        Env=["SYNTHETIC=1"],
        OnBuild=["RUN touch /unexpected"] if onbuild else None,
    )

    def docker(args, **kwargs):
        if args[0] == "info":
            return "linux/arm64"
        if args[:2] == ["image", "inspect"]:
            image = images[args[-1]]
            if "source" in args[-1]:
                # Simulate an alias being reassigned immediately after inspection.
                images[args[-1]] = {**image, "Id": "sha256:" + "c" * 64}
            return json.dumps(image)
        if args[0] == "build":
            tag = args[args.index("-t") + 1]
            if "prepared" in tag:
                layer = (Path(args[-1]) / "Dockerfile").read_text()
                layers.append(layer)
                assert layer.startswith("FROM " + source_id + "\n")
                assert 'RUN ["/bin/sh", "-ec", ' in layer
                assert not onbuild, "ONBUILD was inherited and executed again"
            images[tag] = dict(
                Id=source_id if "source" in tag else "sha256:" + "b" * 64,
                Os="linux",
                Architecture="arm64",
                Config=config,
            )
        return ""

    monkeypatch.setattr(preparation, "_docker", docker)
    monkeypatch.setattr(preparation, "check_prepared_image", lambda *a, **kw: None)
    if onbuild:
        with pytest.raises(ValueError, match="ONBUILD"):
            preparation.prepare_images(native_skill, tmp_path / "prepared", binary_manifest)
        assert not layers
    else:
        path = preparation.prepare_images(native_skill, tmp_path / "prepared", binary_manifest)
        data = json.loads(path.read_text())
        assert len(layers) == 2
        assert all(record["source_image_id"] == source_id for record in data["images"].values())


def test_real_securedocker_setup_reuses_prepared_images_offline(preparation, native_skill, tmp_path, monkeypatch):
    import asyncio
    import os
    import tomllib
    import uuid

    manifest = os.environ.get("EA_TEST_PATCHED_BINARY_MANIFEST")
    if not manifest:
        pytest.skip("requires explicit final binary and local Docker control authorization")
    from harbor.agents.installed.base import BaseInstalledAgent
    from harbor.agents.installed.opencode import OpenCode
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths

    runner = importlib.import_module("skillevaluator.tier3.harbor.runner")
    runtime_preflight = importlib.import_module("skillevaluator.tier3.harbor.runtime_preflight")
    SkillEvaluatorGatewayOpenCode = importlib.import_module(
        "skillevaluator.tier3.harbor.local_agents"
    ).SkillEvaluatorGatewayOpenCode
    Secure = importlib.import_module(
        "skillevaluator.tier3.harbor.secure_docker_environment"
    ).SkillEvaluatorSecureDockerEnvironment

    home = tmp_path / "home"
    home.mkdir()
    plugin = os.environ.get("EA_TEST_COMPOSE_PLUGIN")
    if plugin:
        directory = home / ".docker/cli-plugins"
        directory.mkdir(parents=True)
        (directory / "docker-compose").symlink_to(plugin)
    environment = {key: os.environ[key] for key in ("PATH", "DOCKER_HOST") if key in os.environ}
    environment.update(
        HOME=str(home),
        TMPDIR=str(tmp_path),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHON_DOTENV_DISABLED="1",
        HARBOR_TELEMETRY="0",
    )
    monkeypatch.setattr(os, "environ", environment)
    (native_skill / "evals/config.yaml").write_text(
        "schema_version: 1\nharbor:\n  resources:\n    cpus: 1\n    memory_mb: 1024\n"
    )
    path = preparation.prepare_images(native_skill, tmp_path / "prepared", Path(manifest))
    data = preparation.load_preparation(path, Path(manifest))
    for obj, attr in [
        (OpenCode, "install"),
        (BaseInstalledAgent, "_exec"),
        (Secure, "start"),
        (Secure, "_run_docker_compose_command"),
        (runner, "_harbor_launch_environment"),
        (runtime_preflight, "_harbor_launch_environment"),
    ]:
        monkeypatch.setattr(obj, attr, getattr(obj, attr))
    monkeypatch.setenv(preparation.PREPARATION_ENV, str(path))
    monkeypatch.setenv(preparation.REQUIRE_PREPARATION_ENV, "1")
    tasks = preparation.stage_preparation(native_skill, tmp_path / "actual")
    outcomes = []
    import subprocess
    import sys

    startup = {**environment, **preparation.activation_environment(Path(manifest))}
    startup.pop(preparation.PREPARATION_DIGEST_ENV)
    failed = subprocess.run(
        [sys.executable, "-c", "print('main must not run')"], env=startup, capture_output=True, timeout=30
    )
    assert failed.returncode == 78 and failed.stdout == b"", "unbound image preparation reached child main"
    capture = tmp_path / "children.jsonl"
    recorder = tmp_path / "harbor-recorder"
    recorder.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nfrom harbor.agents.installed.opencode import OpenCode\nfrom skillevaluator.tier3.harbor.secure_docker_environment import SkillEvaluatorSecureDockerEnvironment as Secure\nwith Path({str(capture)!r}).open('a') as stream:\n stream.write(json.dumps(dict(argv=sys.argv[1:], binary=getattr(OpenCode.install,'_ea_patched_opencode',False), prepared=Secure.start.__module__.endswith('patched_opencode_runtime'))) + '\\n')\nraise SystemExit(19)\n"
    )
    recorder.chmod(0o700)
    monkeypatch.setattr(runner, "_harbor_bin", lambda: str(recorder))
    monkeypatch.setattr(runner, "_HARBOR_AGENT_KWARGS", runner._HARBOR_AGENT_KWARGS)
    gateway = "skillevaluator.tier3.harbor.local_agents:SkillEvaluatorGatewayOpenCode"

    def keyless_cli():
        common = dict(
            agent="opencode",
            model="openai/synthetic",
            env_mode="docker",
            jobs_dir=tmp_path / "jobs",
            run_env=environment,
            agent_import_path=gateway,
        )
        assert not runtime_preflight.run_agent_runtime_preflight(
            dataset=tasks["with"][0].parent, **common, timeout_seconds=30
        ).ok
        for arm in ("with", "without"):
            ok, detail = runner._run_harbor(
                dataset=tasks[arm][0].parent,
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

    cli = importlib.import_module("skillevaluator.cli")
    monkeypatch.setattr(cli, "cli", keyless_cli)
    monkeypatch.setenv(preparation.MANIFEST_ENV, manifest)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "wrapper",
            "tier3",
            "evaluate",
            str(native_skill),
            "--agents",
            "opencode",
            "--env-mode",
            "docker",
            "--agent-model",
            "opencode=openai/synthetic",
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
            str(tmp_path / "results"),
            "--progress",
            "off",
        ],
    )
    importlib.import_module("run_pinned_live_evaluator").main()
    children = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(children) == 3 and all(c["binary"] and c["prepared"] for c in children)
    from harbor.cli.utils import parse_kwargs

    for child in children:
        argv = child["argv"]
        assert argv[argv.index("--agent") + 1] == gateway
        assert argv[argv.index("--env") + 1] == runner.SECURE_DOCKER_ENV_IMPORT_PATH
        assert parse_kwargs([argv[i + 1] for i, token in enumerate(argv) if token == "--ak"]) == {"version": "1.19.5"}
    assert ["--disable-verification" in c["argv"] for c in children] == [True, False, False]

    async def run():
        for label, arm, missing in [
            ("preflight", "with", False),
            ("with", "with", False),
            ("without", "without", False),
            ("missing-node", "without", True),
        ]:
            task = tasks[arm][0]
            session = "ea-prep-control-" + uuid.uuid4().hex[:12]
            trial = tmp_path / label
            trial.mkdir()
            config = EnvironmentConfig.model_validate(tomllib.loads((task / "task.toml").read_text())["environment"])
            env = Secure(
                environment_dir=task / "environment",
                environment_name=preparation.prepared_task_config(task).task.short_name,
                session_id=session,
                trial_paths=TrialPaths(trial),
                task_env_config=config,
            )
            try:
                await env.start(False)
                assert getattr(env, "_ea_prepared_image", None), "native setup did not select a verified prepared image"
                container = preparation._docker(
                    [
                        "ps",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + session,
                        "--filter",
                        "label=com.docker.compose.service=main",
                    ]
                )
                assert len(container.splitlines()) == 1
                networks = json.loads(
                    preparation._docker(["inspect", "--format", "{{json .NetworkSettings.Networks}}", container])
                )
                for network in networks:
                    preparation._docker(["network", "disconnect", network, container])
                assert (
                    json.loads(
                        preparation._docker(["inspect", "--format", "{{json .NetworkSettings.Networks}}", container])
                    )
                    == {}
                )
                if missing:
                    result = await env.exec(command="rm /usr/bin/node", user="root")
                    assert result.return_code == 0
                agent = SkillEvaluatorGatewayOpenCode(
                    logs_dir=trial / "agent", model_name="openai/synthetic", version="1.19.5"
                )
                if missing:
                    with pytest.raises(RuntimeError, match="dependencies"):
                        await agent.setup(env)
                    assert not (agent.logs_dir / ".ea-patched-binary.json").exists()
                else:
                    await agent.setup(env)
                    receipt = json.loads((agent.logs_dir / ".ea-patched-binary.json").read_text())
                    assert receipt["container_sha256"] == data["binary_sha256"]
                    marker = await env.exec(command="cat /custom-marker", user="root")
                    assert marker.stdout == "custom"
                outcomes.append(dict(label=label, setup="rejected" if missing else "verified", network="none"))
            finally:
                await env.stop(delete=True)

    asyncio.run(run())
    (tmp_path / "offline-controls.json").write_text(json.dumps(outcomes))
    assert len(outcomes) == 4


@pytest.fixture
def native_activation(preparation, native_skill, binary_manifest, tmp_path, monkeypatch):
    import tomllib

    from harbor.models.task.config import TaskConfig
    from harbor.models.trial.paths import TrialPaths

    Secure = importlib.import_module(
        "skillevaluator.tier3.harbor.secure_docker_environment"
    ).SkillEvaluatorSecureDockerEnvironment
    tasks = preparation.stage_preparation(native_skill, tmp_path / "bound-tasks")
    bound: dict = dict(
        platform="linux/arm64", binary_sha256=preparation.load_manifest(binary_manifest)["sha256"], images={}, tasks={}
    )
    for arm, paths in tasks.items():
        bound["tasks"][arm] = []
        for task in paths:
            identity = preparation.prepared_task_identity(task, bound["platform"], bound["binary_sha256"])
            bound["tasks"][arm].append(identity)
            bound["images"][identity] = dict(image_id="sha256:" + "a" * 64)
    monkeypatch.setenv(preparation.PREPARATION_ENV, str(tmp_path / "manifest.json"))
    monkeypatch.setattr(preparation, "load_preparation", lambda *a: bound)
    monkeypatch.setattr(preparation, "check_prepared_image", lambda *a, **kw: None)
    monkeypatch.setattr(Secure, "start", Secure.start)
    monkeypatch.setattr(Secure, "_run_docker_compose_command", Secure._run_docker_compose_command)
    preparation.prepared_activation(binary_manifest)

    def construct(arm="with", **kwargs):
        task = tasks[arm][0]
        config = TaskConfig.model_validate(tomllib.loads((task / "task.toml").read_text()))
        assert config.task is not None
        defaults = dict(
            environment_dir=task / "environment",
            environment_name=config.task.short_name,
            session_id="synthetic-control",
            trial_paths=TrialPaths(tmp_path / "trial"),
            task_env_config=config.environment,
        )
        defaults.update(kwargs)
        return Secure(**defaults)

    return tasks, construct


@pytest.mark.parametrize("mutation", ["workdir", "env", "resources", "cross-arm", "task-config", "unknown-config"])
def test_native_start_rejects_task_and_arm_substitution(preparation, native_activation, monkeypatch, mutation):
    import asyncio

    tasks, construct = native_activation
    env = construct()
    if mutation == "workdir":
        env.task_env_config.workdir = "/changed"
    elif mutation == "env":
        env.task_env_config.env["SYNTHETIC"] = "changed"
    elif mutation == "resources":
        env.task_env_config.memory_mb = 1024
    elif mutation == "cross-arm":
        env = construct("without", environment_dir=tasks["with"][0] / "environment")
    else:
        path = tasks["with"][0] / "task.toml"
        text = path.read_text()
        if mutation == "task-config":
            text = text.replace("timeout_sec = 300.0", "timeout_sec = 999.0")
        else:
            text = 'unexpected = "must-not-be-discarded"\n' + text
        path.write_text(text)

    def native_start_reached(*args):
        raise AssertionError("unprepared task reached native Docker startup")

    monkeypatch.setattr(type(env), "_validate_daemon_mode", native_start_reached)
    with pytest.raises(ValueError, match="prepared|Extra inputs"):
        asyncio.run(env.start(False))


@pytest.mark.parametrize("policy", ["startup", "phase"])
def test_native_sidecar_builder_never_runs_for_prepared_images(native_activation, monkeypatch, policy):
    import asyncio

    from harbor.models.task.config import NetworkPolicy

    _, construct = native_activation
    docker = importlib.import_module("harbor.environments.docker.docker")
    monkeypatch.setattr(docker.DockerEnvironment, "_egress_control_kernel_support", lambda *a: True)
    monkeypatch.setattr(docker.DockerEnvironment, "_validate_daemon_mode", lambda *a: None)
    calls = []

    async def builder(**kwargs):
        calls.append(kwargs)
        raise AssertionError("native sidecar image builder reached")

    monkeypatch.setattr(docker, "ensure_docker_image_built", builder)
    network = NetworkPolicy(network_mode="no-network")
    env = construct(**({"network_policy": network} if policy == "startup" else {"phase_network_policies": [network]}))
    with pytest.raises(ValueError, match="prepared"):
        asyncio.run(env.start(False))
    assert not calls


def test_native_credential_staging_retains_only_exact_placeholder_variance(preparation, native_skill, tmp_path):
    import shutil

    adapter = importlib.import_module("skillevaluator.tier3.harbor.adapter")
    baseline = preparation.stage_preparation(native_skill, tmp_path / "keyless")["with"][0]
    runtime = {name: "${" + name + "}" for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
    verifier = {
        **runtime,
        "SKILL_EVAL_LLM_PROVIDER": "${SKILL_EVAL_LLM_PROVIDER}",
        "SKILL_EVAL_LLM_MODEL": "${SKILL_EVAL_LLM_MODEL}",
    }
    live = adapter.generate_harbor_tasks(
        native_skill,
        tmp_path / "native-live",
        with_skill=True,
        runtime_env=runtime,
        verifier_env=verifier,
        arm_suffix="-with-skill",
    )[0]
    identity = preparation.prepared_task_identity(baseline, "linux/arm64", "a" * 64)
    assert preparation.prepared_task_identity(live, "linux/arm64", "a" * 64) == identity
    copied = tmp_path / "preflight" / live.name
    shutil.copytree(live, copied)
    assert preparation.prepared_task_identity(copied, "linux/arm64", "a" * 64) == identity
    config = live / "task.toml"
    original = config.read_text()
    for changed in (
        original.replace("${OPENAI_API_KEY}", "${OTHER_KEY}"),
        original.replace("${OPENAI_API_KEY}", "literal-synthetic-value"),
        original + '\nSYNTHETIC = "${SYNTHETIC}"\n',
    ):
        config.write_text(changed)
        assert preparation.prepared_task_identity(live, "linux/arm64", "a" * 64) != identity


def test_build_inputs_are_pinned_without_replacing_custom_commands(preparation):
    assert hasattr(preparation, "pinned_dockerfile"), "immutable base-image binding is missing"
    source = "FROM python:3.12-slim\nRUN printf custom > /marker\nUSER 1000\n"
    for platform in ("linux/arm64", "linux/amd64"):
        pinned = preparation.pinned_dockerfile(source, platform)
        assert pinned.startswith("FROM python:3.12.16-slim-trixie@sha256:")
        assert pinned.splitlines()[1:] == source.splitlines()[1:]
    assert preparation.PACKAGE_VERSIONS == {
        "curl": "8.14.1-2+deb13u7",
        "bash": "5.2.37-2+b10",
        "coreutils": "9.7-3",
        "nodejs": "20.19.2+dfsg-1+deb13u3",
        "npm": "9.2.0~ds1-3",
    }
    with pytest.raises(ValueError, match="pinned"):
        preparation.pinned_dockerfile("FROM ubuntu:latest\n", "linux/arm64")


@pytest.mark.parametrize(
    "dockerfile",
    [
        "RUN printf custom > /marker\n",
        "FROM python:3.12-slim\n  FROM python:3.12-slim\n",
        "FROM python:3.12-slim\n\tFROM \\\npython:3.12-slim\n",
    ],
)
def test_original_custom_dockerfile_rejected_before_native_fallback(preparation, native_skill, tmp_path, dockerfile):
    (native_skill / "evals/environment/Dockerfile").write_text(dockerfile)
    with pytest.raises(ValueError, match="Dockerfile|base image"):
        preparation.stage_preparation(native_skill, tmp_path / "invalid-stage")


def test_prepared_image_package_drift_fails_closed(preparation, monkeypatch):
    assert hasattr(preparation, "PACKAGE_VERSIONS"), "package identity verification is missing"
    identity = "a" * 64
    record = dict(
        tag="ea-live-prepared:" + identity, image_id="sha256:" + "b" * 64, source_image_id="sha256:" + "c" * 64
    )
    monkeypatch.setattr(
        preparation,
        "inspect_image",
        lambda _: dict(
            Id=record["image_id"],
            Os="linux",
            Architecture="arm64",
            Config=dict(Labels={preparation.IMAGE_LABEL: identity}),
        ),
    )
    monkeypatch.setattr(preparation, "_docker", lambda *a, **kw: "nodejs=wrong\n")
    with pytest.raises(ValueError, match="package"):
        preparation.check_prepared_image(identity, record, "linux/arm64", probe=True)


@pytest.mark.parametrize(
    "problem", ["empty", "missing_arm", "uncovered", "bad_id", "bad_tag", "extra", "runtime", "source", "packages"]
)
def test_manifest_rejects_missing_stale_or_mismatched_preparation(
    preparation, binary_manifest, tmp_path, monkeypatch, problem
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input").write_text("fixed")
    binary = preparation.load_manifest(binary_manifest)
    identity = "a" * 64
    data: dict = dict(
        schema_version=1,
        platform="linux/arm64",
        binary_sha256=binary["sha256"],
        runtime_sha256=preparation.sha256(Path(preparation.__file__)),
        skill_path=str(source),
        source_identity=preparation.preparation_identity(source, "linux/arm64", binary["sha256"]),
        tasks={"with": [identity], "without": [identity]},
        images={
            identity: dict(
                tag="ea-live-prepared:" + identity, image_id="sha256:" + "b" * 64, source_image_id="sha256:" + "c" * 64
            )
        },
        packages=preparation.PACKAGE_VERSIONS,
    )
    path = tmp_path / "prepared.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(preparation, "docker_platform", lambda: "linux/arm64")
    monkeypatch.setattr(preparation, "check_prepared_image", lambda *a, **kw: None)
    assert preparation.load_preparation(path, binary_manifest) == data
    if problem == "empty":
        data["images"] = {}
    elif problem == "missing_arm":
        del data["tasks"]["without"]
    elif problem == "uncovered":
        data["tasks"]["without"] = ["d" * 64]
    elif problem == "bad_id":
        data["images"][identity]["image_id"] = "latest"
    elif problem == "bad_tag":
        data["images"][identity]["tag"] = "unbound:latest"
    elif problem == "extra":
        data["images"][identity]["untrusted"] = "ignored?"
    elif problem == "runtime":
        data["runtime_sha256"] = "0" * 64
    elif problem == "source":
        (source / "input").write_text("changed")
    else:
        data["packages"] = {}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        preparation.load_preparation(path, binary_manifest)


def test_preparation_aggregate_deadline_caps_and_cancels_docker_commands(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch
):
    import subprocess
    from types import SimpleNamespace

    clock = [100.0]
    timeouts = []
    tags = {}
    binary = preparation.load_manifest(binary_manifest)

    def run(argv, *, timeout, **kwargs):
        args = argv[1:]
        output = ""
        if args[0] == "info":
            output = "linux/arm64"
        elif args[0] == "build":
            timeouts.append(timeout)
            if timeout <= 200:
                clock[0] += timeout
                raise subprocess.TimeoutExpired(argv, timeout)
            clock[0] += 200
            tag = args[args.index("-t") + 1]
            tags[tag] = dict(
                Id="sha256:" + "b" * 64,
                Os="linux",
                Architecture="arm64",
                Config=dict(Labels={preparation.IMAGE_LABEL: tag.split(":")[1]}),
            )
        elif args[:2] == ["image", "inspect"]:
            output = json.dumps(tags[args[-1]])
        elif args[0] == "run":
            output = "\n".join(k + "=" + v for k, v in preparation.PACKAGE_VERSIONS.items())
            output += "\n" + preparation.EXPECTED_VERSION + "\n" + binary["sha256"] + "  /ea-binary\n"
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(preparation.subprocess, "run", run)
    with pytest.raises((TimeoutError, subprocess.TimeoutExpired)):
        preparation.prepare_images(native_skill, tmp_path / "prepared", binary_manifest)
    assert timeouts == [600, 400, 200]
    assert not (tmp_path / "prepared/prepared-images.json").exists()


@pytest.mark.parametrize("remaining", [0, -1, float("nan"), float("inf")])
def test_preparation_exhausted_job_window_stops_before_native_or_docker(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch, remaining
):
    monkeypatch.setattr(preparation.time, "monotonic", lambda: 100.0)
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(100 + 2760 + remaining))
    monkeypatch.setattr(
        preparation, "verify_contract", lambda: pytest.fail("native startup reached without job budget")
    )
    with pytest.raises(TimeoutError, match="budget"):
        preparation.prepare_images(native_skill, tmp_path / "exhausted", binary_manifest)
    assert not (tmp_path / "exhausted").exists()


def test_live_workflow_reserves_execution_and_report_headroom():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())
    steps = workflow["jobs"]["evaluate"]["steps"]
    initialize = next(s for s in steps if "live_ci.py initialize" in s.get("run", ""))
    assert "EA_LIVE_JOB_DEADLINE" in initialize["run"]
    for marker in ("prepare_live_images.py", "collect_live_skill_evaluations.py"):
        step = next(s for s in steps if marker in s.get("run", ""))
        assert step["timeout-minutes"] <= 47


def test_live_workflow_prepares_before_secrets_and_requires_preparation():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())
    steps = workflow["jobs"]["evaluate"]["steps"]
    preparation = [s for s in steps if "prepare_live_images.py" in s.get("run", "")]
    assert len(preparation) == 1, "live-only keyless image preparation step is missing"
    step = preparation[0]
    live = next(s for s in steps if "collect_live_skill_evaluations.py" in s.get("run", ""))
    assert steps.index(step) < steps.index(live)
    assert "matrix.check == 'live'" in step["if"]
    assert "secrets." not in json.dumps(step)
    assert "EA_REQUIRE_PREPARED_IMAGES=1" in live["run"]
    assert "EA_PREPARED_IMAGES_MANIFEST=" in live["run"]


def test_wrapper_rejects_missing_preparation_before_cli(preparation, monkeypatch):
    import sys

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    wrapper = importlib.import_module("run_pinned_live_evaluator")
    monkeypatch.setattr(sys, "argv", ["wrapper", "tier3", "evaluate", "skills/synthetic"])
    monkeypatch.setenv("EA_REQUIRE_PREPARED_IMAGES", "1")
    monkeypatch.delenv("EA_PATCHED_OPENCODE_MANIFEST", raising=False)
    monkeypatch.delenv("EA_PREPARED_IMAGES_MANIFEST", raising=False)
    with pytest.raises(ValueError, match="prepar"):
        wrapper.main()


def test_precredential_gate_restages_both_arms_and_checks_every_image(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch
):
    assert hasattr(preparation, "validate_prepared_run"), "pre-credential image gate is missing"
    tasks = preparation.stage_preparation(native_skill, tmp_path / "prepared")
    bound: dict = dict(
        skill_path=str(native_skill),
        platform="linux/arm64",
        binary_sha256=preparation.load_manifest(binary_manifest)["sha256"],
        images={},
        tasks={},
    )
    for arm, paths in tasks.items():
        bound["tasks"][arm] = [
            preparation.prepared_task_identity(p, bound["platform"], bound["binary_sha256"]) for p in paths
        ]
        bound["images"].update({identity: {} for identity in bound["tasks"][arm]})
    checked = []
    monkeypatch.setenv(preparation.PREPARATION_ENV, str(tmp_path / "manifest"))
    monkeypatch.setenv(preparation.MANIFEST_ENV, str(binary_manifest))
    monkeypatch.setattr(preparation, "load_preparation", lambda *a: bound)
    monkeypatch.setattr(
        preparation,
        "check_prepared_image",
        lambda identity, record, platform, *, probe, binary: checked.append((identity, probe)),
    )
    argv = [
        "tier3",
        "evaluate",
        str(native_skill),
        "--agents",
        "opencode",
        "--env-mode",
        "docker",
        "--agent-model",
        "opencode=openai/synthetic",
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
        str(tmp_path / "results"),
        "--progress",
        "off",
    ]
    preparation.validate_prepared_run(argv)
    assert set(checked) == {(identity, True) for identity in bound["images"]}
    checked.clear()
    (native_skill / "SKILL.md").write_text("changed")
    with pytest.raises(ValueError, match="prepared"):
        preparation.validate_prepared_run(argv)
    assert not checked
    with pytest.raises(ValueError, match="prepared"):
        preparation.validate_prepared_run(argv + ["--skip-baseline"])


def test_failed_build_never_publishes_a_preparation_manifest(
    preparation, native_skill, binary_manifest, tmp_path, monkeypatch
):
    calls = []

    def docker(args, **kwargs):
        calls.append(args[0])
        if args[0] == "info":
            return "linux/arm64"
        raise ValueError("synthetic package repository unavailable")

    monkeypatch.setattr(preparation, "_docker", docker)
    with pytest.raises(ValueError, match="repository"):
        preparation.prepare_images(native_skill, tmp_path / "failed", binary_manifest)
    assert not (tmp_path / "failed/prepared-images.json").exists()
    assert calls == ["info", "build"]


def test_preparation_cli_failure_is_bounded_and_keyless(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/prepare_live_images.py"),
            "--skill",
            str(tmp_path / "missing"),
            "--binary-manifest",
            str(tmp_path / "missing.json"),
            "--output",
            str(tmp_path / "output"),
        ],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "INFERENCE_HUB_API_KEY": "synthetic-secret-not-a-credential",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "image preparation failed; inference was not started\n"
    assert not (tmp_path / "output/prepared-images.json").exists()


def test_preparation_rejects_incompatible_binary_before_credentials(preparation, binary_manifest, monkeypatch):
    identity = "a" * 64
    record = dict(tag="ea-live-prepared:" + identity, image_id="sha256:" + "b" * 64)
    monkeypatch.setattr(
        preparation,
        "inspect_image",
        lambda _: dict(
            Id=record["image_id"],
            Os="linux",
            Architecture="arm64",
            Config=dict(Labels={preparation.IMAGE_LABEL: identity}),
        ),
    )
    output = (
        "\n".join(k + "=" + v for k, v in preparation.PACKAGE_VERSIONS.items()) + "\nwrong-binary-version\nwrong-hash\n"
    )
    monkeypatch.setattr(preparation, "_docker", lambda *a, **kw: output)
    with pytest.raises(ValueError, match="binary"):
        preparation.check_prepared_image(
            identity, record, "linux/arm64", probe=True, binary=preparation.load_manifest(binary_manifest)
        )
