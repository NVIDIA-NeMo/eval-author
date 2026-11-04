# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish prepared, content-pinned inputs without launching evaluations."""

import shutil
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, JsonValue, StringConstraints, model_validator

from metric_benchmark.config import HelixExecution, LoadedCases, StrictLoader
from metric_benchmark.io import digest_bytes, file_manifest, input_digest, json_bytes, write_json
from metric_benchmark.models import Digest, Model, Text, relative_path, resolve_evidence, unique

PinnedRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*#[0-9a-f]{64}$")]


class TaskBinding(Model):
    ref: PinnedRef
    native_task_id: Text


class CaseBinding(Model):
    taskset: PinnedRef
    tasks: dict[Text, TaskBinding]
    targets: dict[Text, dict[str, JsonValue]]


class HelixBindings(Model):
    cases_sha256: Digest
    cases: dict[Text, CaseBinding]


class PublishAgent(Model):
    bundle_input: Text | None = None
    transport: Literal["registered", "task_image"] = "registered"
    image_bundle_path: Text | None = None
    config_path: Text = "agent.yaml"
    target: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source(self):
        relative_path(self.config_path)
        if Path(self.config_path).name != self.config_path:
            raise ValueError("agent config must be at the bundle root")
        source = self.target.get("source")
        if self.transport == "task_image":
            if self.bundle_input is None or self.image_bundle_path is None:
                raise ValueError("task_image requires bundle_input and image_bundle_path")
            relative_path(self.image_bundle_path)
            if not self.image_bundle_path.startswith("environment/"):
                raise ValueError("image_bundle_path must be inside the task environment build context")
            if not isinstance(source, dict) or not source.get("import_path"):
                raise ValueError("task_image requires an explicit installed Harbor agent import_path")
        elif self.image_bundle_path is not None:
            raise ValueError("image_bundle_path requires task_image transport")
        elif self.bundle_input is not None:
            if source is not None:
                raise ValueError("a published bundle supplies the target source")
        elif not isinstance(source, dict) or set(source) != {"name"} or source["name"] not in ("oracle", "nop"):
            raise ValueError("provide a pinned bundle_input, or an explicit oracle/nop control source")
        return self


class PublicationSettings(Model):
    agents: dict[Text, dict[Text, PublishAgent]]


class PublishTask(Model):
    task_id: Text
    name: Text
    path: Text
    sha256: Digest


class PublishBundle(Model):
    agent_bundle_id: Text
    name: Text | None = None
    path: Text | None = None
    sha256: Digest | None = None
    config_path: Text
    transport: Literal["registered", "task_image"]
    image_bundle_path: Text | None = None
    target: dict[str, JsonValue]


class PublishCase(Model):
    case_id: Text
    tasks: list[PublishTask]
    agents: list[PublishBundle]


class PublicationPlan(Model):
    artifact_type: Literal["helix_publication"] = "helix_publication"
    cases_sha256: Digest
    execution: HelixExecution
    cases: list[PublishCase]


def load_publication_settings(path: Path) -> PublicationSettings:
    return PublicationSettings.model_validate(yaml.load(path.read_text(), Loader=StrictLoader))


def plan_publication(loaded: LoadedCases, execution: HelixExecution, settings: PublicationSettings) -> PublicationPlan:
    # Validate every local input before the first upload, including unused dependencies.
    loaded.identity_payload(verify=True)
    if set(settings.agents) != set(loaded.cases):
        raise ValueError("publication settings must cover exactly the prepared cases")
    cases = []
    for case_id, prepared in loaded.cases.items():
        case = prepared.case
        if set(settings.agents[case_id]) != {agent.agent_bundle_id for agent in case.agents()}:
            raise ValueError(f"case {case_id}: publication settings must cover exactly the prepared agents")
        tasks = []
        for task_id, source in case.task_sources.items():
            path = source.resolve(prepared.root, allow_unsigned=prepared.allow_unsigned)
            digest = input_digest(path)
            # The directory name participates in Harbor's native task identity.
            name_hash = digest_bytes(json_bytes({"sha256": digest, "directory": path.name}))
            tasks.append(PublishTask(task_id=task_id, name="mb-task-" + name_hash[7:39], path=str(path), sha256=digest))
        agents = []
        unique([task.name for task in tasks], "published task identities")
        unique([Path(task.path).name.casefold() for task in tasks], "native task directory names")
        for agent in case.agents():
            spec = settings.agents[case_id][agent.agent_bundle_id]
            path, digest, name = None, None, None
            if spec.bundle_input is not None:
                if spec.bundle_input not in agent.inputs:
                    raise ValueError(f"agent {agent.agent_bundle_id}: unknown bundle_input")
                source = agent.inputs[spec.bundle_input]
                path = source.resolve(prepared.root, allow_unsigned=prepared.allow_unsigned)
                if not path.is_dir():
                    raise ValueError("agent bundle_input must be a directory")
                if spec.transport == "registered":
                    config_path = resolve_evidence(path, spec.config_path)
                    config = yaml.load(config_path.read_text(), Loader=StrictLoader)
                    if not isinstance(config, dict) or config.get("config_format") != "nemo-agents-spec-v1":
                        raise ValueError("agent bundle requires a nemo-agents-spec-v1 config")
                for relative in file_manifest(path) if spec.transport == "registered" else []:
                    # Registered agent snapshots are text artifacts. Never silently omit binaries.
                    try:
                        (path / relative).read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        raise ValueError(f"agent bundle contains a non-text artifact: {relative}") from None
                digest = input_digest(path)
                if spec.transport == "task_image":
                    assert spec.image_bundle_path is not None
                    for task in tasks:
                        staged = resolve_evidence(Path(task.path), spec.image_bundle_path)
                        if input_digest(staged) != digest:
                            raise ValueError("task image bundle differs from the prepared agent input")
                name_hash = digest_bytes(json_bytes({"sha256": digest, "config_path": spec.config_path}))
                name = "mb-agent-" + name_hash[7:39]
            agents.append(
                PublishBundle(
                    agent_bundle_id=agent.agent_bundle_id,
                    name=name,
                    path=str(path) if path else None,
                    sha256=digest,
                    config_path=spec.config_path,
                    transport=spec.transport,
                    image_bundle_path=spec.image_bundle_path,
                    target=spec.target,
                )
            )
        cases.append(PublishCase(case_id=case_id, tasks=tasks, agents=agents))
    return PublicationPlan(cases_sha256=loaded.identity, execution=execution, cases=cases)


def _snapshot(source: str, digest: str, destination: Path) -> Path:
    if input_digest(Path(source)) != digest:
        raise ValueError("prepared input changed after publication planning")
    if not destination.exists():
        shutil.copytree(source, destination)
    if input_digest(destination) != digest:
        raise ValueError("publication snapshot differs from its prepared input")
    return destination


def publish_helix_inputs(plan: PublicationPlan, output: Path, client) -> HelixBindings:
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    write_json(output / "publication.json", plan)
    # Stage and validate the full matrix before contacting mutating endpoints.
    for case in plan.cases:
        for task in case.tasks:
            _snapshot(task.path, task.sha256, output / "sources" / task.name / Path(task.path).name)
        for agent in case.agents:
            if agent.path is not None:
                assert agent.name is not None and agent.sha256 is not None
                bundle = _snapshot(agent.path, agent.sha256, output / "sources" / agent.name)
                if agent.transport == "registered":
                    client.validate_agent(bundle, agent.config_path)
    bindings = {}
    for case in plan.cases:
        for task in case.tasks:
            client.validate_task(output / "sources" / task.name / Path(task.path).name)
    for case in plan.cases:
        tasks, targets = {}, {}
        for task in case.tasks:
            source = output / "sources" / task.name / Path(task.path).name
            receipt = client.publish_task(task.name, source, output / "archives" / f"{task.name}.tar.gz")
            write_json(output / "receipts" / f"{task.name}.json", receipt)
            tasks[task.task_id] = TaskBinding(ref=receipt["ref"], native_task_id=receipt["native_task_id"])
        refs = [task.ref for task in tasks.values()]
        taskset_name = "mb-tasks-" + digest_bytes(json_bytes(refs))[7:39]
        taskset = client.publish_taskset(taskset_name, refs)
        write_json(output / "receipts" / f"{taskset_name}.json", {"ref": taskset, "tasks": refs})
        for agent in case.agents:
            target = dict(agent.target)
            if agent.path is not None and agent.transport == "registered":
                assert agent.name is not None
                receipt = client.publish_agent(agent.name, output / "sources" / agent.name, agent.config_path)
                write_json(output / "receipts" / f"{agent.name}.json", receipt)
                target["source"] = {"agent": receipt["ref"]}
            elif agent.transport == "task_image":
                assert agent.name is not None
                write_json(
                    output / "receipts" / f"{agent.name}.json",
                    {
                        "transport": "task_image",
                        "sha256": agent.sha256,
                        "image_bundle_path": agent.image_bundle_path,
                        "files": file_manifest(output / "sources" / agent.name),
                    },
                )
            targets[agent.agent_bundle_id] = target
        bindings[case.case_id] = CaseBinding(taskset=taskset, tasks=tasks, targets=targets)
        client.request_builds(refs)
    result = HelixBindings(cases_sha256=plan.cases_sha256, cases=bindings)
    write_json(output / "bindings.json", result)
    return result
