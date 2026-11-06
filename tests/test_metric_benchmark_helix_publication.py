# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
from pathlib import Path

import pytest
from metric_benchmark.config import HelixExecution, load_execution
from metric_benchmark.helix_publication import (
    PublicationPlan,
    PublishAgent,
    load_publication_settings,
    plan_publication,
    publish_helix_inputs,
)
from metric_benchmark.io import file_manifest, input_digest, read_json
from test_metric_benchmark_config import AGENT_DIR, FIXTURE, prepared_benchmark


def prepared(tmp_path, *, task_image=False):
    experiment, _ = prepared_benchmark(tmp_path)
    from metric_benchmark.signing import sign_benchmark

    shutil.copyfile(FIXTURE / "helix/execution.yml", tmp_path / "execution.yml")
    if task_image:
        shutil.copyfile(FIXTURE / "helix/agent-task-image.yml", tmp_path / AGENT_DIR / "agent.yml")
        bundle = tmp_path / "runtime"
        bundle.mkdir()
        (bundle / "agent.yaml").write_text("name: synthetic-test-agent\n")
    sign_benchmark(experiment, resign=True)
    loaded = load_execution(tmp_path / "cases.yml", tmp_path / "execution.yml")
    example = "publication-task-image.yml" if task_image else "publication-builtin.yml"
    settings = load_publication_settings(FIXTURE / "helix" / example)
    return loaded.inputs, loaded.settings, settings


class Publisher:
    def __init__(self):
        self.calls = []

    def require_destination(self, url, workspace):
        assert (url, workspace) == ("https://helix.example.com", "default")

    def publish_task(self, name, path, archive_path):
        self.calls.append(("task", name))
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(b"mock archive")
        return {"ref": f"default/{name}#{input_digest(path)[7:]}", "native_task_id": path.name}

    def validate_task(self, path):
        assert (path / "instruction.md").is_file()

    def publish_taskset(self, name, refs):
        self.calls.append(("taskset", name))
        return f"default/{name}#{'a' * 64}"

    def request_builds(self, refs):
        self.calls.append(("builds", refs))
        return {ref: {"id": "1", "status": "running"} for ref in refs}

    def provenance(self):
        return {"client_versions": {"harbor": "0.24.0"}, "server": {"revision": "mock"}}

    def validate_agent(self, bundle, config_path):
        self.calls.append(("validate", config_path))

    def publish_agent(self, name, bundle, config_path):
        self.calls.append(("agent", name))
        return {"ref": f"default/{name}", "sha256": input_digest(bundle)}


def test_publication_retains_sources_and_replays_identically(tmp_path):
    loaded, execution, settings = prepared(tmp_path / "input")
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    output = tmp_path / "published"
    result = publish_helix_inputs(plan, output, client)
    before = file_manifest(output)
    assert publish_helix_inputs(plan, output, client) == result
    assert file_manifest(output) == before
    saved = read_json(output / "bindings.json")
    assert isinstance(saved, dict) and saved["cases_sha256"] == loaded.identity
    case = next(iter(result.cases.values()))
    assert len(case.tasks) == 3
    assert len(case.targets) == 3
    assert not any(call[0] == "agent" for call in client.calls)
    assert len(list((output / "sources").glob("*/inspect-files"))) == 1


def test_entire_matrix_preflight_before_upload(tmp_path):
    loaded, execution, settings = prepared(tmp_path / "input")
    plan = plan_publication(loaded, execution, settings)
    task = plan.cases[0].tasks[-1]
    (Path(task.path) / "instruction.md").write_text("changed")
    client = Publisher()
    with pytest.raises(ValueError, match="changed after publication planning"):
        publish_helix_inputs(plan, tmp_path / "output", client)
    assert client.calls == []


@pytest.mark.parametrize("change", ["file_mode", "directory_mode", "empty_directory"])
def test_task_resource_identity_covers_archive_metadata(tmp_path, change):
    loaded, execution, settings = prepared(tmp_path / "input")
    before = plan_publication(loaded, execution, settings)
    source = Path(before.cases[0].tasks[0].path)
    if change == "file_mode":
        path = source / "instruction.md"
        path.chmod(path.stat().st_mode ^ 0o100)
    elif change == "directory_mode":
        source.chmod(source.stat().st_mode ^ 0o010)
    else:
        (source / "empty").mkdir()
    after = plan_publication(loaded, execution, settings)
    assert after.cases_sha256 == before.cases_sha256
    assert after.cases[0].tasks[0].context_sha256 == before.cases[0].tasks[0].context_sha256
    assert after.cases[0].tasks[0].name != before.cases[0].tasks[0].name
    client = Publisher()
    with pytest.raises(ValueError, match="archive permissions or directory inventory changed"):
        publish_helix_inputs(before, tmp_path / "stale", client)
    assert client.calls == []
    publish_helix_inputs(after, tmp_path / "current", client)


def test_publication_requires_exact_agent_membership(tmp_path):
    loaded, execution, settings = prepared(tmp_path)
    case_id = next(iter(settings.agents))
    settings.agents[case_id].pop("mutant")
    with pytest.raises(ValueError, match="exactly the prepared agents"):
        plan_publication(loaded, execution, settings)


def test_publication_cannot_override_prepared_agent():
    with pytest.raises(ValueError, match="Extra inputs"):
        PublishAgent.model_validate({"target": {"source": {"name": "nop"}}})
    with pytest.raises(ValueError, match="not qualified"):
        PublishAgent(transport="registered", bundle_input="runtime")


def task_image_inputs(tmp_path):
    return prepared(tmp_path / "input", task_image=True)


def test_task_image_overlay_preserves_signed_task_identity(tmp_path):
    loaded, execution, settings = task_image_inputs(tmp_path)
    original = file_manifest(loaded.root)
    identity = loaded.identity
    plan = plan_publication(loaded, execution, settings)
    result = publish_helix_inputs(plan, tmp_path / "output", Publisher())
    assert loaded.identity == identity == result.cases_sha256
    assert file_manifest(loaded.root) == original
    assert result.publication_sha256 == plan.identity
    case = plan.cases[0]
    for task in case.tasks:
        context = tmp_path / "output/contexts" / task.name / Path(task.path).name
        assert input_digest(context) == task.context_sha256 != task.sha256
        assert (context / "environment/benchmark-agents/baseline/agent.yaml").is_file()
        assert (context / "instruction.md").read_bytes() == (Path(task.path) / "instruction.md").read_bytes()
    target = result.cases[case.case_id].targets[case.agents[0].agent_bundle_id]
    assert target["agent_kwargs"] == {"fabric_config_target": "/opt/benchmark-agents/baseline"}
    assert target["env_secrets"] == {"OPENAI_API_KEY": "workspace/model-access"}
    assert target["env_vars"] == {}


def test_snapshot_interruption_does_not_publish_partial_copy(tmp_path, monkeypatch):
    from metric_benchmark.helix_publication import _snapshot

    source = tmp_path / "input"
    source.mkdir()
    (source / "complete").write_text("content")

    def interrupt(src, dst, *args, **kwargs):
        Path(dst).mkdir()
        (Path(dst) / "partial").write_text("incomplete")
        raise OSError("interrupted")

    destination = tmp_path / "snapshots/kept"
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copytree", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            _snapshot(str(source), input_digest(source), destination)
    assert not destination.exists()
    assert _snapshot(str(source), input_digest(source), destination) == destination
    assert file_manifest(source) == file_manifest(destination)


def test_build_observations_can_evolve_without_changing_bindings(tmp_path, monkeypatch):
    loaded, execution, settings = prepared(tmp_path / "input")
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    first = publish_helix_inputs(plan, tmp_path / "output", client)
    monkeypatch.setattr(
        client,
        "request_builds",
        lambda refs: {ref: {"id": "1", "status": "ready", "images": ["image"]} for ref in refs},
    )
    assert publish_helix_inputs(plan, tmp_path / "output", client) == first
    assert len(list((tmp_path / "output/receipts/builds").glob("*.json"))) == 6


@pytest.mark.parametrize("workspace", ["Default", "My_Workspace"])
def test_workspace_rejected_before_publication(workspace):
    with pytest.raises(ValueError):
        HelixExecution(base_url="https://helix.example.com", workspace=workspace, dispatcher_profile="default")


def test_duplicate_remote_ids_rejected_before_taskset_or_build(tmp_path, monkeypatch):
    loaded, execution, settings = prepared(tmp_path / "input")
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    publish = client.publish_task
    monkeypatch.setattr(client, "publish_task", lambda *args: {**publish(*args), "native_task_id": "duplicate"})
    with pytest.raises(ValueError, match="native task IDs"):
        publish_helix_inputs(plan, tmp_path / "output", client)
    assert not any(call[0] in ("taskset", "builds") for call in client.calls)


def test_fabric_transport_only_replaces_bundle_upload(tmp_path):
    from metric_benchmark.helix_publication import FABRIC_INSTALLED_AGENT, agent_target

    loaded, _, settings = task_image_inputs(tmp_path)
    case_id, case = next(iter(loaded.cases.items()))
    agent = case.case.baseline
    spec = settings.agents[case_id][agent.agent_bundle_id]
    agent.harbor = {
        "import_path": FABRIC_INSTALLED_AGENT,
        "env": agent.harbor["env"],
        "kwargs": {
            "fabric_config_bundle": "{input:runtime}",
            "fabric_config_target": "/opt/benchmark-agents/baseline",
            "fabric_config": {"temperature": 0.0},
            "fabric_package": "fixed==1.0",
        },
    }
    target = agent_target(agent, spec)
    assert target["agent_kwargs"] == {
        key: value for key, value in agent.harbor["kwargs"].items() if key != "fabric_config_bundle"
    }
    spec.image_mount_path = "/opt/agents/mutant"
    with pytest.raises(ValueError, match="upload destination"):
        agent_target(agent, spec)


@pytest.mark.parametrize("task_image", [False, True])
def test_example_plan_only_cli(tmp_path, monkeypatch, task_image):
    from publish_helix_inputs import main

    root = tmp_path / "input"
    loaded, _, _ = prepared(root, task_image=task_image)
    output = tmp_path / "plan"
    example = "publication-task-image.yml" if task_image else "publication-builtin.yml"
    # Context-derived coordinates remain unresolved during offline planning.
    shutil.copyfile(FIXTURE / "helix/execution-context.yml", root / "execution.yml")
    monkeypatch.setattr(
        "sys.argv",
        [
            "publish_helix_inputs.py",
            "--cases",
            str(root / "cases.yml"),
            "--execution",
            str(root / "execution.yml"),
            "--publication",
            str(FIXTURE / "helix" / example),
            "--output",
            str(output),
            "--plan-only",
        ],
    )
    main()
    plan = PublicationPlan.model_validate(read_json(output / "publication.json"))
    assert plan.cases_sha256 == loaded.identity
    assert plan.execution.workspace is None
    assert plan.execution.base_url is None
    assert len(plan.cases[0].tasks) == 3
    assert list(output.iterdir()) == [output / "publication.json"]
