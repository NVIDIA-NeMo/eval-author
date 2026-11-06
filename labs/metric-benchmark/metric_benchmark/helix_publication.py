# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish derived execution artifacts without changing prepared experiment inputs."""

import json
import os
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol

import yaml
from pydantic import Field, JsonValue, StringConstraints, model_validator

from metric_benchmark.agent_configuration import resolved_agent_values
from metric_benchmark.config import Agent, HelixExecution, LoadedCases, StrictLoader
from metric_benchmark.io import digest_bytes, input_digest, json_bytes, write_json
from metric_benchmark.models import Digest, Model, Text, relative_path, resolve_evidence, unique

PinnedRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*#[0-9a-f]{64}$")]
FABRIC_INSTALLED_AGENT = "nhx_evals_sdk.agent_eval.runtimes.harbor.fabric_installed_agent:FabricInstalledAgent"


class PublishAgent(Model):
    transport: Literal["builtin", "task_image", "registered"] = "builtin"
    bundle_input: Text | None = None
    image_bundle_path: Text | None = None
    image_mount_path: Text | None = None
    config_path: Text = "agent.yaml"
    env_secrets: dict[Text, Text] = Field(default_factory=dict)

    @model_validator(mode="after")
    def packaging(self):
        relative_path(self.config_path)
        if Path(self.config_path).name != self.config_path:
            raise ValueError("agent config must be at the bundle root")
        if self.transport == "registered":
            raise ValueError("registered-agent execution mapping is not qualified; use a supported task_image adapter")
        if self.transport == "task_image":
            if self.bundle_input is None or self.image_bundle_path is None or self.image_mount_path is None:
                raise ValueError("task_image requires bundle_input, image_bundle_path and image_mount_path")
            relative_path(self.image_bundle_path)
            if not self.image_bundle_path.startswith("environment/"):
                raise ValueError("image_bundle_path must be inside the task environment build context")
            mount = PurePosixPath(self.image_mount_path)
            if (
                not mount.is_absolute()
                or ".." in mount.parts
                or str(mount) != self.image_mount_path
                or len(mount.parts) < 3
            ):
                raise ValueError("image_mount_path must be a normalized absolute path below a named directory")
        elif any(value is not None for value in (self.bundle_input, self.image_bundle_path, self.image_mount_path)):
            raise ValueError("bundle packaging requires task_image transport")
        return self


def agent_target(agent: Agent, packaging: PublishAgent) -> dict[str, JsonValue]:
    def resolve_input(name: str) -> str:
        if name != packaging.bundle_input or packaging.image_mount_path is None:
            raise ValueError(f"agent {agent.agent_bundle_id}: input {name} has no declared image transport")
        return packaging.image_mount_path

    values = resolved_agent_values(agent, resolve_input)
    unsupported = set(values) - {"name", "import_path", "model_name", "kwargs", "env"}
    if unsupported:
        raise ValueError(f"Helix agent mapping does not support prepared fields: {sorted(unsupported)}")
    source = {key: values[key] for key in ("name", "import_path", "model_name") if key in values}
    if packaging.transport == "builtin" and source.get("name") not in ("oracle", "nop"):
        raise ValueError("custom agents require a declared task_image transport")
    prepared_kwargs = agent.harbor.get("kwargs", {})
    resolved_kwargs = values.get("kwargs", {})
    environment = values.get("env", {})
    if (
        not isinstance(prepared_kwargs, dict)
        or not isinstance(resolved_kwargs, dict)
        or not isinstance(environment, dict)
    ):
        raise ValueError("prepared agent kwargs and env must be mappings")
    kwargs = dict(resolved_kwargs)
    if source.get("import_path") == FABRIC_INSTALLED_AGENT:
        # The same adapter uploads the bundle locally; Helix preloads those exact bytes into its image.
        if prepared_kwargs.get("fabric_config_bundle") != "{input:" + str(packaging.bundle_input) + "}":
            raise ValueError("Fabric requires fabric_config_bundle to reference its declared prepared input")
        if kwargs.get("fabric_config_target") != packaging.image_mount_path:
            raise ValueError("Fabric image mount differs from its prepared upload destination")
        kwargs.pop("fabric_config_bundle")
    env = dict(environment)
    for name in packaging.env_secrets:
        if env.get(name) != "${" + name + "}":
            raise ValueError(f"secret {name} must reference the same environment variable in the prepared agent")
        del env[name]
    if any(isinstance(value, str) and value.startswith("${") for value in env.values()):
        raise ValueError("prepared secret environment reference has no execution binding")
    secrets: dict[str, JsonValue] = {key: value for key, value in packaging.env_secrets.items()}
    return {"source": source, "agent_kwargs": kwargs, "env_vars": env, "env_secrets": secrets}


class PublicationSettings(Model):
    agents: dict[Text, dict[Text, PublishAgent]]


class PublishTask(Model):
    task_id: Text
    name: Text
    path: Text
    sha256: Digest
    context_sha256: Digest


class PublishBundle(Model):
    agent_bundle_id: Text
    name: Text | None = None
    path: Text | None = None
    sha256: Digest | None = None
    packaging: PublishAgent
    target: dict[str, JsonValue]


class PublishCase(Model):
    case_id: Text
    tasks: list[PublishTask] = Field(min_length=1)
    agents: list[PublishBundle] = Field(min_length=1)

    @model_validator(mode="after")
    def membership(self):
        unique([task.task_id for task in self.tasks], "publication task IDs")
        unique([agent.agent_bundle_id for agent in self.agents], "publication agent IDs")
        return self


class PublicationPlan(Model):
    artifact_type: Literal["helix_publication"] = "helix_publication"
    derivation: Literal["task-image-overlay-v1"] = "task-image-overlay-v1"
    cases_sha256: Digest
    execution: HelixExecution
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


class CaseBinding(Model):
    taskset: PinnedRef
    tasks: dict[Text, TaskBinding]
    agents: dict[Text, PublishAgent]
    targets: dict[Text, dict[str, JsonValue]]


class HelixBindings(Model):
    cases_sha256: Digest
    publication_sha256: Digest
    cases: dict[Text, CaseBinding]


class Publisher(Protocol):
    def require_destination(self, base_url: str | None, workspace: str | None, /) -> None: ...
    def validate_task(self, directory: Path, /): ...
    def publish_task(self, name: str, directory: Path, archive_path: Path, /) -> dict: ...
    def publish_taskset(self, name: str, refs: list[str], /) -> str: ...
    def request_builds(self, refs: list[str], /) -> dict: ...
    def provenance(self) -> dict: ...


def load_publication_settings(path: Path) -> PublicationSettings:
    return PublicationSettings.model_validate(yaml.load(path.read_text(), Loader=StrictLoader))


def overlay_task(source: Path, agents: list[PublishBundle], destination: Path) -> Path:
    shutil.copytree(source, destination)
    overlays = [agent for agent in agents if agent.packaging.transport == "task_image"]
    if not overlays:
        return destination
    config = tomllib.loads((source / "task.toml").read_text())
    environment = config.get("environment", {})
    if environment.get("docker_image") or (source / "environment/docker-compose.yaml").exists():
        raise ValueError("agent overlays require a task built from its Dockerfile, without a prebuilt or compose image")
    dockerfile = resolve_evidence(destination, "environment/Dockerfile")
    ignore = destination / "environment/.dockerignore"
    if ignore.exists() and any(
        line.strip() and not line.lstrip().startswith("#") for line in ignore.read_text().splitlines()
    ):
        raise ValueError("task-image overlays do not support .dockerignore rules")
    commands = []
    for agent in overlays:
        spec = agent.packaging
        assert agent.path is not None and spec.image_bundle_path is not None and spec.image_mount_path is not None
        target = destination / spec.image_bundle_path
        if target.exists():
            raise ValueError("generated agent overlay would replace prepared task content")
        shutil.copytree(agent.path, target)
        if input_digest(target) != agent.sha256:
            raise ValueError("prepared agent changed while constructing its overlay")
        commands.append(
            "COPY "
            + json.dumps([str(PurePosixPath(spec.image_bundle_path).relative_to("environment")), spec.image_mount_path])
        )
    with dockerfile.open("a") as stream:
        stream.write("\n" + "\n".join(commands) + "\n")
    return destination


def _task_name(context: Path, digest: str) -> str:
    # Native archives retain permissions and empty directories, unlike the prepared content digest.
    modes = {
        path.relative_to(context).as_posix(): stat.S_IMODE(path.stat().st_mode)
        for path in [context, *sorted(context.rglob("*"))]
    }
    identity = digest_bytes(json_bytes({"sha256": digest, "directory": context.name, "modes": modes}))
    return "mb-task-" + identity[7:39]


def plan_publication(loaded: LoadedCases, execution: HelixExecution, settings: PublicationSettings) -> PublicationPlan:
    loaded.identity_payload(verify=True)
    if set(settings.agents) != set(loaded.cases):
        raise ValueError("publication settings must cover exactly the prepared cases")
    cases = []
    for case_id, prepared in loaded.cases.items():
        case = prepared.case
        if set(settings.agents[case_id]) != {agent.agent_bundle_id for agent in case.agents()}:
            raise ValueError("publication settings must cover exactly the prepared agents")
        agents = []
        mounts = []
        for agent in case.agents():
            spec = settings.agents[case_id][agent.agent_bundle_id]
            path, digest, name = None, None, None
            if spec.bundle_input is not None:
                if spec.bundle_input not in agent.inputs:
                    raise ValueError(f"agent {agent.agent_bundle_id}: unknown bundle_input")
                path = agent.inputs[spec.bundle_input].resolve(prepared.root)
                if not path.is_dir():
                    raise ValueError("agent bundle_input must be a directory")
                digest = input_digest(path)
                name = "mb-agent-" + digest[7:39]
                assert spec.image_mount_path is not None
                mount = PurePosixPath(spec.image_mount_path)
                if any(mount == other or mount in other.parents or other in mount.parents for other in mounts):
                    raise ValueError("agent image mount paths must not overlap")
                mounts.append(mount)
            agents.append(
                PublishBundle(
                    agent_bundle_id=agent.agent_bundle_id,
                    name=name,
                    path=str(path) if path else None,
                    sha256=digest,
                    packaging=spec,
                    target=agent_target(agent, spec),
                )
            )
        tasks = []
        for task_id, source in case.task_sources.items():
            path = source.resolve(prepared.root)
            digest = input_digest(path)
            with tempfile.TemporaryDirectory(prefix="metric-benchmark-overlay-") as temporary:
                context = overlay_task(path, agents, Path(temporary) / path.name)
                context_digest = input_digest(context)
                name = _task_name(context, context_digest)
            tasks.append(
                PublishTask(
                    task_id=task_id,
                    name=name,
                    path=str(path),
                    sha256=digest,
                    context_sha256=context_digest,
                )
            )
        unique([task.name for task in tasks], "published task identities")
        unique([Path(task.path).name.casefold() for task in tasks], "native task directory names")
        cases.append(PublishCase(case_id=case_id, tasks=tasks, agents=agents))
    return PublicationPlan(cases_sha256=loaded.identity, execution=execution, cases=cases)


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


def publish_helix_inputs(plan: PublicationPlan, output: Path, client: Publisher) -> HelixBindings:
    client.require_destination(plan.execution.base_url, plan.execution.workspace)
    if plan.execution.base_url is None or plan.execution.workspace is None:
        raise ValueError("resolve the execution destination before publishing")
    write_json(output / "publication.json", plan)
    provenance = output / "provenance.json"
    if not provenance.exists():
        write_json(provenance, client.provenance())
    contexts = {}
    for case in plan.cases:
        agents = []
        for agent in case.agents:
            if agent.path is not None:
                assert agent.name is not None and agent.sha256 is not None
                bundle = _snapshot(agent.path, agent.sha256, output / "sources" / agent.name)
                agent = agent.model_copy(update={"path": str(bundle)})
            agents.append(agent)
        for task in case.tasks:
            source = _snapshot(task.path, task.sha256, output / "sources" / task.name / Path(task.path).name)
            destination = output / "contexts" / task.name / source.name
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".overlay-", dir=destination.parent) as temporary:
                    staged = overlay_task(source, agents, Path(temporary) / source.name)
                    if input_digest(staged) != task.context_sha256:
                        raise ValueError("derived task context differs from the publication plan")
                    staged.rename(destination)
            if input_digest(destination) != task.context_sha256:
                raise ValueError("saved derived task context is corrupt")
            if _task_name(destination, task.context_sha256) != task.name:
                raise ValueError("task archive permissions or directory inventory changed after publication planning")
            client.validate_task(destination)
            contexts[task.name] = destination
    bindings = {}
    for case in plan.cases:
        tasks = {}
        for task in case.tasks:
            receipt = client.publish_task(task.name, contexts[task.name], output / "archives" / f"{task.name}.tar.gz")
            write_json(
                output / "receipts" / f"{task.name}.json",
                {**receipt, "source_sha256": task.sha256, "image_context_sha256": task.context_sha256},
            )
            tasks[task.task_id] = TaskBinding(ref=receipt["ref"], native_task_id=receipt["native_task_id"])
        unique([task.native_task_id.casefold() for task in tasks.values()], "remote native task IDs")
        refs = [task.ref for task in tasks.values()]
        taskset_name = "mb-tasks-" + digest_bytes(json_bytes(refs))[7:39]
        taskset = client.publish_taskset(taskset_name, refs)
        write_json(output / "receipts" / f"{taskset_name}.json", {"ref": taskset, "tasks": refs})
        builds = client.request_builds(refs)
        for ref, observation in builds.items():
            # Status and images evolve even when POST reuses the same build attempt.
            key = digest_bytes(json_bytes({"ref": ref, "observation": observation}))[7:]
            write_json(output / "receipts/builds" / f"{key}.json", {"ref": ref, "build": observation})
        bindings[case.case_id] = CaseBinding(
            taskset=taskset,
            tasks=tasks,
            agents={agent.agent_bundle_id: agent.packaging for agent in case.agents},
            targets={agent.agent_bundle_id: agent.target for agent in case.agents},
        )
    result = HelixBindings(cases_sha256=plan.cases_sha256, publication_sha256=plan.identity, cases=bindings)
    write_json(output / "bindings.json", result)
    return result
