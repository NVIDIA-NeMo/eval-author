# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read a versioned benchmark definition and verify its prepared local inputs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from metric_benchmark.io import digest_bytes, digest_file, json_bytes, read_json
from metric_benchmark.models import Artifact, Digest, Model, Subsets, Tasks, Text, resolve_evidence, unique


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("YAML mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def file_manifest(root: Path) -> dict[str, str]:
    """Hash ordinary files only; reject symlinks and special entries."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("evidence root must be an ordinary directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("evidence must contain only ordinary files and directories")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest_file(path)
    return result


def input_digest(path: Path) -> str:
    return digest_bytes(json_bytes(file_manifest(path))) if path.is_dir() else digest_file(path)


class PinnedInput(Model):
    path: Text
    sha256: Digest
    kind: Literal["file", "directory"] = "file"

    def resolve(self, root: Path) -> Path:
        path = resolve_evidence(root, self.path)
        if (self.kind == "directory" and not path.is_dir()) or (self.kind == "file" and not path.is_file()):
            raise ValueError(f"wrong input kind: {self.path}")
        if input_digest(path) != self.sha256:
            raise ValueError(f"input digest differs: {self.path}")
        return path


class Agent(Model):
    agent_bundle_id: Text
    revision: Text
    harbor: dict
    # Bundle files, local skills, or other prepared dependencies. Harbor kwargs
    # reference these using {input:NAME}; paths are resolved only for execution.
    inputs: dict[Text, PinnedInput] = Field(default_factory=dict)


class ScoringPolicy(Model):
    attempt_policy: Literal["only", "latest"] = "only"
    metric: Text = "reward"
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"
    completed_statuses: list[Text] = Field(default_factory=lambda: ["succeeded"], min_length=1)


class ScoringConfig(ScoringPolicy):
    case_id: Text
    baseline_bundle_id: Text
    mutant_bundle_ids: list[Text] = Field(min_length=1)
    run_ids: dict[Text, Text]

    @model_validator(mode="after")
    def valid_agents(self) -> Self:
        agents = [self.baseline_bundle_id, *self.mutant_bundle_ids]
        unique(agents, "agent IDs")
        unique(list(self.run_ids.values()), "selected run IDs")
        unique(self.completed_statuses, "completed statuses")
        if set(self.run_ids) != set(agents):
            raise ValueError("run_ids must select exactly one run for the baseline and each mutant")
        return self


class CoverageConfig(Model):
    system_id: Text
    system_revision: Text
    command: list[Text] = Field(min_length=1)
    settings: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=3600, ge=1)


class Case(Model):
    case_id: Text
    task_inventory: Tasks
    task_sources: dict[Text, PinnedInput]
    case_description: PinnedInput
    subsets: PinnedInput
    baseline: Agent
    mutants: list[Agent] = Field(min_length=1)
    coverage: CoverageConfig
    scoring: ScoringPolicy = Field(default_factory=ScoringPolicy)

    def agents(self) -> list[Agent]:
        return [self.baseline, *self.mutants]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        unique([a.agent_bundle_id for a in self.agents()], "agent IDs")
        if set(self.task_sources) != {t.task_id for t in self.task_inventory.tasks}:
            raise ValueError("task_sources must match the task inventory")
        if any(source.kind != "directory" for source in self.task_sources.values()):
            raise ValueError("Harbor task sources must be directories")
        if self.subsets.kind != "file" or self.case_description.kind != "file":
            raise ValueError("subsets and case description must be files")
        return self

    def selection(self) -> ScoringConfig:
        return ScoringConfig(
            **self.scoring.model_dump(),
            case_id=self.case_id,
            baseline_bundle_id=self.baseline.agent_bundle_id,
            mutant_bundle_ids=[a.agent_bundle_id for a in self.mutants],
            run_ids={a.agent_bundle_id: f"agent-{i:04d}/run-0001" for i, a in enumerate(self.agents())},
        )


class LocalExecution(Model):
    backend: Literal["local_harbor"] = "local_harbor"
    harbor_version: Literal["0.20.0"] = "0.20.0"
    n_concurrent_trials: int = Field(default=1, ge=1)
    environment: dict = Field(default_factory=lambda: {"type": "docker"})


class BenchmarkDefinition(Artifact):
    benchmark_id: Text
    revision: int = Field(ge=1)
    cases: list[Case] = Field(min_length=1)
    execution: LocalExecution = Field(default_factory=LocalExecution)
    missing_policy: Literal["error", "exclude"] = "error"

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        unique([c.case_id for c in self.cases], "case IDs")
        return self


@dataclass(frozen=True)
class LoadedBenchmark:
    definition: BenchmarkDefinition
    root: Path
    subsets: dict[str, Subsets]
    descriptions: dict[str, dict]

    @property
    def identity(self) -> str:
        return digest_bytes(json_bytes(self.definition))


def load_benchmark(path: Path) -> LoadedBenchmark:
    try:
        definition = BenchmarkDefinition.model_validate(yaml.load(path.read_text(), Loader=StrictLoader))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid benchmark YAML: {error}") from error
    # Also rejects non-JSON native settings such as YAML dates and infinities.
    json_bytes(definition)
    root = path.resolve().parent
    manifests, descriptions = {}, {}
    for case in definition.cases:
        for source in case.task_sources.values():
            source.resolve(root)
        for agent in case.agents():
            for source in agent.inputs.values():
                source.resolve(root)
        subsets = Subsets.model_validate(read_json(case.subsets.resolve(root)))
        if subsets.benchmark_id != definition.benchmark_id or subsets.task_inventory != case.task_inventory:
            raise ValueError("subset manifest does not match benchmark identity and case task inventory")
        description = read_json(case.case_description.resolve(root))
        if not isinstance(description, dict):
            raise ValueError("case description must be a JSON object")
        manifests[case.case_id], descriptions[case.case_id] = subsets, description
    return LoadedBenchmark(definition, root, manifests, descriptions)
