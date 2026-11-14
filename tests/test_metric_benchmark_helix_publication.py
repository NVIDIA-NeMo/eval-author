# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import HelixExecution, load_execution
from metric_benchmark.helix_publication import (
    PublishAgent,
    load_publication_settings,
    plan_publication,
    publish_helix_inputs,
)
from metric_benchmark.io import file_manifest, input_digest, read_json
from test_metric_benchmark_config import AGENT_DIR, FIXTURE, LAB, prepared_benchmark


def prepared(tmp_path, *, registered=False):
    experiment, _ = prepared_benchmark(tmp_path)
    from metric_benchmark.signing import sign_benchmark

    shutil.copyfile(FIXTURE / "helix/execution.yml", tmp_path / "execution.yml")
    if registered:
        agent_file = tmp_path / AGENT_DIR / "agent.yml"
        manifest = yaml.safe_load(agent_file.read_text())
        manifest["baseline"] = yaml.safe_load((LAB / "examples/helix/agents.yml").read_text())["baseline"]
        manifest["baseline"]["helix-registered"]["kwargs"] = {"fabric_workspace": "/"}
        manifest["baseline"]["inputs"]["runtime"]["path"] = "runtime"
        agent_file.write_text(yaml.safe_dump(manifest))
        bundle = tmp_path / "runtime"
        bundle.mkdir()
        (bundle / "agent.yaml").write_text(
            yaml.safe_dump(
                {
                    "config_format": "nemo-agents-spec-v1",
                    "name": "test-agent",
                    "default_harness": "deepagents",
                    "harnesses": {"deepagents": {"kind": "deepagents", "settings": {"deepagents": {}}}},
                    "models": {"default": {"provider": "nvidia", "model": "example"}},
                    "telemetry": {"enabled": False},
                }
            )
        )
    sign_benchmark(experiment, resign=True)
    loaded = load_execution(tmp_path / "cases.yml", tmp_path / "execution.yml")
    example = "publication-registered.yml" if registered else "publication-builtin.yml"
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

    def reuse_task(self, ref, path, archive_path):
        self.calls.append(("reuse", ref))
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(b"mock archive")
        return {"ref": ref, "native_task_id": path.name}

    def validate_task(self, path):
        assert (path / "instruction.md").is_file()

    def publish_taskset(self, name, refs):
        self.calls.append(("taskset", name))
        return f"default/{name}#{'a' * 64}"

    def retain_agent(self, name, bundle, definition):
        return {"ref": "default/source-" + name, "files": file_manifest(bundle), "sha256": input_digest(bundle)}

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
    result = publish_helix_inputs(plan, output, client, source_root=loaded.root)
    before = file_manifest(output)
    assert publish_helix_inputs(plan, output, client, source_root=loaded.root) == result
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
    (loaded.root / task.path / "instruction.md").write_text("changed")
    client = Publisher()
    with pytest.raises(ValueError, match="changed after publication planning"):
        publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    assert client.calls == []


@pytest.mark.parametrize("change", ["file_mode", "directory_mode", "empty_directory"])
def test_task_resource_identity_covers_archive_metadata(tmp_path, change):
    loaded, execution, settings = prepared(tmp_path / "input")
    before = plan_publication(loaded, execution, settings)
    source = loaded.root / before.cases[0].tasks[0].path
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
        publish_helix_inputs(before, tmp_path / "stale", client, source_root=loaded.root)
    assert client.calls == []
    publish_helix_inputs(after, tmp_path / "current", client, source_root=loaded.root)


def test_publication_requires_exact_agent_membership(tmp_path):
    loaded, execution, settings = prepared(tmp_path)
    case_id = next(iter(settings.agents))
    settings.agents[case_id].pop("mutant")
    with pytest.raises(ValueError, match="exactly the prepared agents"):
        plan_publication(loaded, execution, settings)


def test_publication_cannot_override_prepared_agent():
    with pytest.raises(ValueError, match="Extra inputs"):
        PublishAgent.model_validate({"target": {"source": {"name": "nop"}}})
    with pytest.raises(ValueError, match="Extra inputs"):
        PublishAgent.model_validate({"transport": "task_image"})


def registered_inputs(tmp_path):
    return prepared(tmp_path / "input", registered=True)


def test_empty_registered_options_preserve_absolute_task_paths(tmp_path):
    from metric_benchmark.config import RegisteredAgent
    from metric_benchmark.helix_publication import agent_target

    loaded, _, _ = registered_inputs(tmp_path)
    agent = next(iter(loaded.cases.values())).case.baseline
    agent.registered = RegisteredAgent(bundle_input=agent.registered.bundle_input, kwargs={})
    assert agent_target(agent, PublishAgent())["agent_kwargs"] == {"fabric_workspace": "/"}
    with pytest.raises(ValueError, match="preserve task paths"):
        RegisteredAgent(bundle_input="runtime", kwargs={"fabric_workspace": "/testbed"})


def test_registered_agents_leave_tasks_unchanged(tmp_path):
    loaded, execution, settings = registered_inputs(tmp_path)
    original = file_manifest(loaded.root)
    identity = loaded.identity
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    result = publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    assert loaded.identity == identity == result.cases_sha256
    assert file_manifest(loaded.root) == original
    assert result.publication_sha256 == plan.identity
    case = plan.cases[0]
    for task in case.tasks:
        context = tmp_path / "output/sources" / task.name / Path(task.path).name
        assert input_digest(context) == task.context_sha256 == task.sha256
        assert file_manifest(context) == file_manifest(loaded.root / task.path)
        assert (context / "instruction.md").read_bytes() == (loaded.root / task.path / "instruction.md").read_bytes()
    target = result.cases[case.case_id].targets[case.agents[0].agent_bundle_id]
    assert target["agent_kwargs"] == {"fabric_workspace": "/"}
    assert case.agents[0].name is not None
    assert target["source"] == {"agent": "default/" + case.agents[0].name}
    assert len([call for call in client.calls if call[0] == "agent"]) == 1
    assert "baseline" in result.cases[case.case_id].registrations
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
    first = publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    monkeypatch.setattr(
        client,
        "request_builds",
        lambda refs: {ref: {"id": "1", "status": "ready", "images": ["image"]} for ref in refs},
    )
    assert publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root) == first
    assert not any(call[0] == "builds" for call in client.calls)


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
        publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    assert not any(call[0] in ("taskset", "builds") for call in client.calls)


def test_existing_task_is_reused_without_upload(tmp_path):
    loaded, execution, settings = prepared(tmp_path / "input")
    case_id = next(iter(loaded.cases))
    example = load_publication_settings(LAB / "examples/helix/publication-existing-task.yml")
    settings.tasks = {case_id: example.tasks["example"]}
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    result = publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    assert result.cases[case_id].tasks["inspect-files"].ref == settings.tasks[case_id]["inspect-files"]
    assert len([call for call in client.calls if call[0] == "task"]) == 2
    assert client.calls[0][0] == "reuse"


def test_changed_existing_task_fails_before_remote_writes(tmp_path, monkeypatch):
    loaded, execution, settings = prepared(tmp_path / "input")
    settings.tasks = {next(iter(loaded.cases)): {"inspect-files": "default/existing#" + "a" * 64}}
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()

    def mismatch(*args):
        raise ValueError("existing task differs")

    monkeypatch.setattr(client, "reuse_task", mismatch)
    with pytest.raises(ValueError, match="existing task differs"):
        publish_helix_inputs(plan, tmp_path / "output", client, source_root=loaded.root)
    assert client.calls == []


def test_separate_publication_needs_only_selected_sources(tmp_path):
    from metric_benchmark.helix_sources import References, combine_references

    loaded, execution, settings = registered_inputs(tmp_path)
    client = Publisher()
    root = loaded.root
    tasks = root / "tasks"
    tasks.rename(root / "hidden-tasks")
    agents_plan = plan_publication(loaded, execution, settings, resources={"agents"})
    agents = References(
        publication=agents_plan,
        bindings=publish_helix_inputs(agents_plan, tmp_path / "agents", client, source_root=root),
        provenance=client.provenance(),
    )
    (root / "hidden-tasks").rename(tasks)
    shutil.rmtree(root / "runtime")
    tasks_plan = plan_publication(loaded, execution, settings, resources={"tasks"})
    tasks_ref = References(
        publication=tasks_plan,
        bindings=publish_helix_inputs(tasks_plan, tmp_path / "tasks", client, source_root=root),
        provenance=client.provenance(),
    )
    combined = combine_references(agents, tasks_ref)
    assert len(combined.publication.cases[0].agents) == 3
    assert len(combined.publication.cases[0].tasks) == 3
    assert combined.bindings.cases[combined.publication.cases[0].case_id].taskset
    assert not any(call[0] == "builds" for call in client.calls)
    assert combined.inputs(tmp_path / "absent").identity == loaded.identity
    assert combine_references(combined, tasks_ref) == combined


def test_agent_source_download_survives_lost_original_and_detects_corruption(tmp_path):
    from metric_benchmark.helix_sources import References, download_sources
    from metric_benchmark.io import write_json

    loaded, execution, settings = registered_inputs(tmp_path)

    class RetainingPublisher(Publisher):
        def retain_agent(self, name, bundle, definition):
            self.remote = tmp_path / "remote"
            shutil.copytree(bundle, self.remote / "bundle")
            write_json(self.remote / "agent.json", definition)
            return {"ref": "default/retained", "files": file_manifest(self.remote), "sha256": input_digest(self.remote)}

        def download_files(self, ref, destination):
            assert ref == "default/retained"
            shutil.copytree(self.remote, destination, dirs_exist_ok=True)

    client = RetainingPublisher()
    plan = plan_publication(loaded, execution, settings, resources={"agents"})
    bindings = publish_helix_inputs(plan, tmp_path / "publication", client, source_root=loaded.root)
    refs = References(publication=plan, bindings=bindings, provenance=client.provenance())
    expected = file_manifest(loaded.root / "runtime")
    shutil.rmtree(loaded.root)
    download_sources(refs, tmp_path / "downloaded", client, resources={"agents"})
    assert file_manifest(tmp_path / "downloaded/runtime") == expected
    download_sources(refs, tmp_path / "downloaded", client, resources={"agents"})
    (tmp_path / "downloaded/runtime/agent.yaml").write_text("changed")
    with pytest.raises(ValueError, match="conflicts"):
        download_sources(refs, tmp_path / "downloaded", client, resources={"agents"})
    (client.remote / "agent.json").write_text("{}")
    with pytest.raises(ValueError, match="missing or changed"):
        download_sources(refs, tmp_path / "new-download", client, resources={"agents"})
    assert not (tmp_path / "new-download/runtime").exists()
