# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish derived execution artifacts without changing prepared experiment inputs."""

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Protocol

import yaml
from pydantic import Field, JsonValue, StringConstraints, model_validator

from metric_benchmark.agent_configuration import resolved_agent_values
from metric_benchmark.config import Agent, Case, HelixExecution, LoadedCases, StrictLoader
from metric_benchmark.helix_client import HelixDestination
from metric_benchmark.io import digest_bytes, input_digest, json_bytes, write_json
from metric_benchmark.models import Digest, Model, Text, resolve_evidence, unique

PinnedRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*#[0-9a-f]{64}$")]
FABRIC_INSTALLED_AGENT = "nhx_evals_sdk.agent_eval.runtimes.harbor.fabric_installed_agent:FabricInstalledAgent"


class PublishAgent(Model):
    env_secrets: dict[Text, Text] = Field(default_factory=dict)


def registered_name(agent: Agent) -> str:
    return "mb-agent-" + digest_bytes(json_bytes(agent))[7:39]


def agent_target(agent: Agent, packaging: PublishAgent, workspace: str | None = None) -> dict[str, JsonValue]:
    secrets: dict[str, JsonValue] = dict(packaging.env_secrets)
    if agent.registered is not None:
        return {
            "source": {"agent": f"{workspace}/{registered_name(agent)}" if workspace else registered_name(agent)},
            "agent_kwargs": {"fabric_workspace": "/", **agent.registered.kwargs},
            "agent_setup_timeout_multiplier": agent.registered.setup_timeout_multiplier,
            "env_vars": {},
            "env_secrets": secrets,
        }

    def no_input(name: str) -> str:
        raise ValueError("builtin controls do not transport agent inputs")

    values = resolved_agent_values(agent, no_input)
    if values.get("name") not in ("oracle", "nop") or set(values) - {"name", "kwargs", "env"}:
        raise ValueError("Helix custom agents must use a registered bundle")
    environment = values.get("env", {})
    if not isinstance(environment, dict):
        raise ValueError("prepared environment must be a mapping")
    env = dict(environment)
    for name in packaging.env_secrets:
        if env.get(name) != "${" + name + "}":
            raise ValueError("builtin secret binding must match a prepared environment placeholder")
        del env[name]
    return {
        "source": {"name": values["name"]},
        "agent_kwargs": values.get("kwargs", {}),
        "env_vars": env,
        "env_secrets": secrets,
    }


class PublicationSettings(Model):
    agents: dict[Text, dict[Text, PublishAgent]]
    tasks: dict[Text, dict[Text, PinnedRef]] = Field(default_factory=dict)


class PublishTask(Model):
    task_id: Text
    name: Text
    path: Text
    sha256: Digest
    context_sha256: Digest
    ref: PinnedRef | None = None


class PublishBundle(Model):
    agent_bundle_id: Text
    name: Text | None = None
    path: Text | None = None
    sha256: Digest | None = None
    packaging: PublishAgent
    target: dict[str, JsonValue]
    config_path: Text | None = None
    config: dict[str, JsonValue] | None = None


class PublishCase(Model):
    case_id: Text
    tasks: list[PublishTask] = Field(default_factory=list)
    agents: list[PublishBundle] = Field(default_factory=list)

    @model_validator(mode="after")
    def membership(self):
        unique([task.task_id for task in self.tasks], "publication task IDs")
        unique([agent.agent_bundle_id for agent in self.agents], "publication agent IDs")
        return self


class PublicationPlan(Model):
    artifact_type: Literal["helix_publication"] = "helix_publication"
    derivation: Literal["registered-agents-v1"] = "registered-agents-v1"
    cases_sha256: Digest
    destination: HelixDestination
    definitions: list[Case] = Field(min_length=1)
    cases: list[PublishCase] = Field(min_length=1)

    @model_validator(mode="after")
    def membership(self):
        unique([case.case_id for case in self.cases], "publication case IDs")
        return self

    @property
    def identity(self) -> str:
        return digest_bytes(json_bytes(self))


class TaskBinding(Model):
    ref: PinnedRef
    native_task_id: Text
    source: dict[str, JsonValue] = Field(default_factory=dict)


class CaseBinding(Model):
    taskset: PinnedRef | None = None
    tasks: dict[Text, TaskBinding] = Field(default_factory=dict)
    agents: dict[Text, PublishAgent] = Field(default_factory=dict)
    targets: dict[Text, dict[str, JsonValue]] = Field(default_factory=dict)
    registrations: dict[Text, dict[str, JsonValue]] = Field(default_factory=dict)


class HelixBindings(Model):
    cases_sha256: Digest
    publication_sha256: Digest
    cases: dict[Text, CaseBinding]


class Publisher(Protocol):
    def require_destination(self, base_url: str | None, workspace: str | None, /) -> None: ...
    def validate_task(self, directory: Path, /): ...
    def publish_task(self, name: str, directory: Path, archive_path: Path, /) -> dict: ...
    def validate_agent(self, bundle: Path, config_path: str, /): ...
    def publish_agent(self, name: str, bundle: Path, config_path: str, /) -> dict: ...
    def reuse_task(self, ref: str, directory: Path, archive_path: Path, /) -> dict: ...
    def publish_taskset(self, name: str, refs: list[str], /) -> str: ...
    def retain_agent(self, name: str, bundle: Path, definition: Agent, /) -> dict: ...
    def provenance(self) -> dict: ...


def load_publication_settings(path: Path) -> PublicationSettings:
    return PublicationSettings.model_validate(yaml.load(path.read_text(), Loader=StrictLoader))


def _task_name(context: Path, digest: str) -> str:
    # Native archives retain permissions and empty directories, unlike the prepared content digest.
    modes = {
        path.relative_to(context).as_posix(): stat.S_IMODE(path.stat().st_mode)
        for path in [context, *sorted(context.rglob("*"))]
    }
    identity = digest_bytes(json_bytes({"sha256": digest, "directory": context.name, "modes": modes}))
    return "mb-task-" + identity[7:39]


def plan_publication(
    loaded: LoadedCases,
    execution: HelixDestination | HelixExecution,
    settings: PublicationSettings,
    *,
    resources=frozenset({"agents", "tasks"}),
) -> PublicationPlan:
    if loaded.allow_unsigned:
        raise ValueError("Helix publication requires fully signed inputs")
    loaded.identity_payload()
    if execution.base_url is None or execution.workspace is None:
        raise ValueError("publication requires a resolved destination")
    if set(settings.agents) != set(loaded.cases) or set(settings.tasks) - set(loaded.cases):
        raise ValueError("publication settings must cover exactly the prepared cases")
    cases = []
    for case_id, prepared in loaded.cases.items():
        case = prepared.case
        if set(settings.agents[case_id]) != {agent.agent_bundle_id for agent in case.agents()}:
            raise ValueError("publication settings must cover exactly the prepared agents")
        if set(settings.tasks.get(case_id, {})) - set(case.task_sources):
            raise ValueError("existing task references must name prepared tasks")
        agents = []
        for agent in case.agents() if "agents" in resources else []:
            spec = settings.agents[case_id][agent.agent_bundle_id]
            path, digest, name, config, config_path = None, None, None, None, None
            if agent.registered is not None:
                path = agent.inputs[agent.registered.bundle_input].resolve(prepared.root)
                if not path.is_dir():
                    raise ValueError("registered bundle_input must be a directory")
                digest, name = input_digest(path), registered_name(agent)
                config_path = agent.registered.config_path
                config = yaml.load(resolve_evidence(path, config_path).read_text(), Loader=StrictLoader)
                if not isinstance(config, dict) or config.get("config_format") != "nemo-agents-spec-v1":
                    raise ValueError("registered bundle requires a nemo-agents-spec-v1 agent config")
                if "${NEMO_DEFAULT_MODEL}" in str(config):
                    raise ValueError("registered agent must select an explicit model")
                for file in path.rglob("*"):
                    if file.is_file():
                        try:
                            file.read_text(encoding="utf-8")
                        except UnicodeDecodeError:
                            raise ValueError("registered agent FileSets currently require text files") from None
            agents.append(
                PublishBundle(
                    agent_bundle_id=agent.agent_bundle_id,
                    name=name,
                    path=agent.inputs[agent.registered.bundle_input].path if agent.registered else None,
                    sha256=digest,
                    packaging=spec,
                    target=agent_target(agent, spec, execution.workspace),
                    config_path=config_path,
                    config=config,
                )
            )
        tasks = []
        for task_id, source in case.task_sources.items() if "tasks" in resources else []:
            path = source.resolve(prepared.root)
            digest = input_digest(path)
            tasks.append(
                PublishTask(
                    task_id=task_id,
                    name=_task_name(path, digest),
                    path=source.path,
                    sha256=digest,
                    context_sha256=digest,
                    ref=settings.tasks.get(case_id, {}).get(task_id),
                )
            )
        unique([task.name for task in tasks], "published task identities")
        unique([Path(task.path).name.casefold() for task in tasks], "native task directory names")
        cases.append(PublishCase(case_id=case_id, tasks=tasks, agents=agents))
    return PublicationPlan(
        cases_sha256=loaded.identity,
        destination=HelixDestination(base_url=execution.base_url, workspace=execution.workspace),
        definitions=[prepared.case for prepared in loaded.cases.values()],
        cases=cases,
    )


def _snapshot(source: str, digest: str, destination: Path) -> Path:
    if input_digest(Path(source)) != digest:
        raise ValueError("prepared input changed after publication planning")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "snapshot"
            shutil.copytree(source, staged)
            if input_digest(staged) != digest:
                raise ValueError("prepared input changed while copying its snapshot")
            try:
                os.rename(staged, destination)
            except OSError:
                if not destination.exists():
                    raise
    if input_digest(destination) != digest:
        raise ValueError("saved publication snapshot is corrupt; recover it before resuming")
    return destination


def publish_helix_inputs(plan: PublicationPlan, output: Path, client: Publisher, *, source_root: Path) -> HelixBindings:
    client.require_destination(plan.destination.base_url, plan.destination.workspace)
    if plan.destination.base_url is None or plan.destination.workspace is None:
        raise ValueError("resolve the execution destination before publishing")
    write_json(output / "publication.json", plan)
    provenance = output / "provenance.json"
    if not provenance.exists():
        write_json(provenance, client.provenance())
    contexts, bundles = {}, {}
    for case in plan.cases:
        for agent in case.agents:
            if agent.path is not None:
                assert agent.name is not None and agent.sha256 is not None and agent.config_path is not None
                bundle = _snapshot(str(source_root / agent.path), agent.sha256, output / "sources" / agent.name)
                if yaml.load((bundle / agent.config_path).read_text(), Loader=StrictLoader) != agent.config:
                    raise ValueError("registered config differs from publication plan")
                client.validate_agent(bundle, agent.config_path)
                bundles[agent.name] = bundle
        for task in case.tasks:
            source = _snapshot(
                str(source_root / task.path), task.sha256, output / "sources" / task.name / Path(task.path).name
            )
            if task.context_sha256 != task.sha256 or _task_name(source, task.sha256) != task.name:
                raise ValueError("task archive permissions or directory inventory changed after publication planning")
            client.validate_task(source)
            contexts[task.name] = source
    # Existing remote tasks are checked before creating any new remote resources.
    reused = {}
    for case in plan.cases:
        for task in case.tasks:
            if task.ref:
                reused[task.ref] = client.reuse_task(
                    task.ref, contexts[task.name], output / "archives" / f"{task.name}.tar.gz"
                )
    bindings = {}
    for case in plan.cases:
        registrations, targets = {}, {}
        for agent in case.agents:
            target = dict(agent.target)
            if agent.name is not None:
                assert agent.config_path is not None
                definition = next(
                    a
                    for c in plan.definitions
                    if c.case_id == case.case_id
                    for a in c.agents()
                    if a.agent_bundle_id == agent.agent_bundle_id
                )
                retained = client.retain_agent(agent.name, bundles[agent.name], definition)
                receipt = client.publish_agent(agent.name, bundles[agent.name], agent.config_path)
                receipt["retained_source"] = retained
                expected_ref = f"{plan.destination.workspace}/{agent.name}"
                if receipt["ref"] != expected_ref or receipt["sha256"] != agent.sha256:
                    raise ValueError("registered agent differs from publication plan")
                target["source"] = {"agent": expected_ref}
                write_json(output / "receipts" / f"{agent.name}.json", receipt)
                registrations[agent.agent_bundle_id] = receipt
            targets[agent.agent_bundle_id] = target
        tasks = {}
        for task in case.tasks:
            receipt = (
                reused[task.ref]
                if task.ref
                else client.publish_task(task.name, contexts[task.name], output / "archives" / f"{task.name}.tar.gz")
            )
            write_json(
                output / "receipts" / f"{task.name}.json",
                {**receipt, "source_sha256": task.sha256, "image_context_sha256": task.sha256},
            )
            tasks[task.task_id] = TaskBinding(
                ref=receipt["ref"],
                native_task_id=receipt["native_task_id"],
                source=receipt.get("spec", {}).get("source", {}),
            )
        unique([task.native_task_id.casefold() for task in tasks.values()], "remote native task IDs")
        refs = [task.ref for task in tasks.values()]
        taskset_name = "mb-tasks-" + digest_bytes(json_bytes(refs))[7:39]
        taskset = client.publish_taskset(taskset_name, refs) if refs else None
        if taskset:
            write_json(output / "receipts" / f"{taskset_name}.json", {"ref": taskset, "tasks": refs})
        bindings[case.case_id] = CaseBinding(
            taskset=taskset,
            tasks=tasks,
            agents={a.agent_bundle_id: a.packaging for a in case.agents},
            targets=targets,
            registrations=registrations,
        )
    result = HelixBindings(cases_sha256=plan.cases_sha256, publication_sha256=plan.identity, cases=bindings)
    write_json(output / "bindings.json", result)
    return result
