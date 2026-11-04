# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import HelixExecution, PinnedInput, load_cases
from metric_benchmark.helix_publication import PublicationSettings, PublishAgent, plan_publication, publish_helix_inputs
from metric_benchmark.io import file_manifest, input_digest, read_json
from test_metric_benchmark_config import prepared_benchmark


def prepared(tmp_path):
    prepared_benchmark(tmp_path)
    loaded = load_cases(tmp_path / "cases.yml", allow_unsigned=True)
    settings = PublicationSettings(
        agents={
            case_id: {
                agent.agent_bundle_id: PublishAgent(target={"source": {"name": agent.harbor["name"]}})
                for agent in case.case.agents()
            }
            for case_id, case in loaded.cases.items()
        }
    )
    execution = HelixExecution(base_url="https://helix.example.com", workspace="default", dispatcher_profile="default")
    return loaded, execution, settings


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


def test_registered_bundle_is_a_prepared_input(tmp_path):
    loaded, execution, settings = prepared(tmp_path / "input")
    case_id, case = next(iter(loaded.cases.items()))
    agent = case.case.baseline
    bundle = loaded.root / "bundle"
    bundle.mkdir()
    (bundle / "agent.yaml").write_text(yaml.safe_dump({"config_format": "nemo-agents-spec-v1", "name": "test"}))
    (bundle / "runtime.py").write_text("print('real prepared runtime')\n")
    agent.inputs["runtime"] = PinnedInput(path="bundle", kind="directory", sha256=input_digest(bundle))
    from metric_benchmark.helix_publication import PublishAgent

    settings.agents[case_id][agent.agent_bundle_id] = PublishAgent(bundle_input="runtime")
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    bindings = publish_helix_inputs(plan, tmp_path / "output", client)
    target = bindings.cases[case_id].targets[agent.agent_bundle_id]
    source = target["source"]
    assert isinstance(source, dict)
    ref = source["agent"]
    assert isinstance(ref, str) and ref.startswith("default/mb-agent-")
    assert client.calls[0] == ("validate", "agent.yaml")
    assert file_manifest(tmp_path / "output/sources" / plan.cases[0].agents[0].name) == file_manifest(bundle)
    (bundle / "runtime.py").write_text("changed runtime")
    with pytest.raises(ValueError, match="input digest differs"):
        plan_publication(loaded, execution, settings)


def test_publication_requires_exact_agent_membership(tmp_path):
    loaded, execution, settings = prepared(tmp_path)
    case_id = next(iter(settings.agents))
    settings.agents[case_id].pop("mutant")
    with pytest.raises(ValueError, match="exactly the prepared agents"):
        plan_publication(loaded, execution, settings)


def test_published_bundle_cannot_override_source():
    from metric_benchmark.helix_publication import PublishAgent

    with pytest.raises(ValueError, match="supplies the target source"):
        PublishAgent(bundle_input="runtime", target={"source": {"name": "nop"}})
    with pytest.raises(ValueError, match="provide a pinned bundle"):
        PublishAgent(target={"source": {"import_path": "not_installed:Agent"}})


def test_task_image_transport_checks_every_copy(tmp_path):
    from metric_benchmark.helix_publication import PublishAgent

    loaded, execution, settings = prepared(tmp_path / "input")
    case_id, prepared_case = next(iter(loaded.cases.items()))
    agent = prepared_case.case.baseline
    bundle = loaded.root / "runtime"
    bundle.mkdir()
    (bundle / "agent.py").write_text("print('prepared runtime')\n")
    agent.inputs["runtime"] = PinnedInput(path="runtime", kind="directory", sha256=input_digest(bundle))
    settings.agents[case_id][agent.agent_bundle_id] = PublishAgent(
        bundle_input="runtime",
        transport="task_image",
        image_bundle_path="environment/runtime",
        target={"source": {"import_path": "installed.module:Agent"}},
    )
    for task in prepared_case.case.task_sources.values():
        shutil.copytree(bundle, loaded.root / task.path / "environment/runtime")
        task.sha256 = input_digest(loaded.root / task.path)
    plan = plan_publication(loaded, execution, settings)
    client = Publisher()
    result = publish_helix_inputs(plan, tmp_path / "output", client)
    assert result.cases[case_id].targets[agent.agent_bundle_id]["source"] == {"import_path": "installed.module:Agent"}
    assert not any(call[0] == "agent" for call in client.calls)
    task = next(iter(prepared_case.case.task_sources.values()))
    (loaded.root / task.path / "environment/runtime/agent.py").write_text("changed")
    task.sha256 = input_digest(loaded.root / task.path)
    with pytest.raises(ValueError, match="task image bundle differs"):
        plan_publication(loaded, execution, settings)
