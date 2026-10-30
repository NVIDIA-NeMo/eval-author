# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read a versioned benchmark definition and verify its prepared local inputs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, JsonValue, TypeAdapter, model_validator

from metric_benchmark.io import digest_bytes, json_bytes, read_json, verify_input
from metric_benchmark.io import file_manifest as file_manifest
from metric_benchmark.io import input_digest as input_digest
from metric_benchmark.models import Digest, Model, Subsets, Task, Tasks, Text, resolve_evidence, unique


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("YAML mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class PinnedInput(Model):
    path: Text
    sha256: Digest | None = None
    kind: Literal["file", "directory"] = "file"

    def resolve(self, root: Path, *, allow_unsigned: bool = False, verify_digest: bool = True) -> Path:
        try:
            path = resolve_evidence(root, self.path)
        except FileNotFoundError:
            raise ValueError(f"missing pinned input: {self.path}") from None
        if (self.kind == "directory" and not path.is_dir()) or (self.kind == "file" and not path.is_file()):
            raise ValueError(f"wrong input kind: {self.path}")
        if verify_digest:
            verify_input(path, self.sha256, allow_unsigned=allow_unsigned, label=self.path)
        return path


class Agent(Model):
    agent_bundle_id: Text
    revision: Text
    harbor: dict[str, JsonValue]
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
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=3600, ge=1)


class AgentManifest(Model):
    baseline: Agent
    mutants: list[Agent] = Field(min_length=1)


class TaskSource(Task):
    path: Text
    sha256: Digest | None = None

    def source(self) -> PinnedInput:
        return PinnedInput(path=self.path, sha256=self.sha256, kind="directory")


class TaskManifest(Model):
    tasks: list[TaskSource] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        unique([task.task_id for task in self.tasks], "task IDs")
        return self

    @property
    def task_inventory(self) -> Tasks:
        return Tasks(tasks=[Task(task_id=t.task_id, revision=t.revision, name=t.name) for t in self.tasks])

    @property
    def task_sources(self) -> dict[str, PinnedInput]:
        return {task.task_id: task.source() for task in self.tasks}


class CaseDefinition(Model):
    case_id: Text
    agent: PinnedInput
    tasks: PinnedInput

    @model_validator(mode="after")
    def file_references(self) -> Self:
        if any(source.kind != "file" for source in (self.agent, self.tasks)):
            raise ValueError("agent and tasks must reference manifest files")
        return self


class Case(Model):
    case_id: Text
    task_inventory: Tasks
    task_sources: dict[Text, PinnedInput]
    baseline: Agent
    mutants: list[Agent] = Field(min_length=1)

    @classmethod
    def from_manifests(cls, definition: CaseDefinition, agent: AgentManifest, tasks: TaskManifest) -> Self:
        return cls(
            case_id=definition.case_id,
            baseline=agent.baseline,
            mutants=agent.mutants,
            task_inventory=tasks.task_inventory,
            task_sources=tasks.task_sources,
        )

    def agents(self) -> list[Agent]:
        return [self.baseline, *self.mutants]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        unique([a.agent_bundle_id for a in self.agents()], "agent IDs")
        if set(self.task_sources) != {t.task_id for t in self.task_inventory.tasks}:
            raise ValueError("task_sources must match the task inventory")
        if any(source.kind != "directory" for source in self.task_sources.values()):
            raise ValueError("Harbor task sources must be directories")
        return self

    def selection(self, run_ids: dict[str, str], scoring: ScoringPolicy) -> ScoringConfig:
        return ScoringConfig(
            **scoring.model_dump(),
            case_id=self.case_id,
            baseline_bundle_id=self.baseline.agent_bundle_id,
            mutant_bundle_ids=[a.agent_bundle_id for a in self.mutants],
            run_ids=run_ids,
        )


class LocalExecution(Model):
    backend: Literal["local_harbor"] = "local_harbor"
    harbor_version: Literal["0.20.0"] = "0.20.0"
    n_concurrent_trials: int = Field(default=1, ge=1)
    environment: dict[str, JsonValue] = Field(default_factory=lambda: dict[str, JsonValue](type="docker"))


class CaseAnalysis(Model):
    subsets: PinnedInput
    description: PinnedInput

    @model_validator(mode="after")
    def file_references(self) -> Self:
        if self.subsets.kind != "file" or self.description.kind != "file":
            raise ValueError("subsets and description must reference files")
        return self


class AnalysisSettings(Model):
    case_settings: dict[Text, CaseAnalysis] = Field(min_length=1)
    coverage: CoverageConfig
    scoring: ScoringPolicy = Field(default_factory=ScoringPolicy)
    missing_policy: Literal["error", "exclude"] = "error"


class BenchmarkDefinition(Model):
    benchmark_id: Text
    revision: int = Field(ge=1)
    cases: PinnedInput
    execution: PinnedInput
    analysis: PinnedInput

    @model_validator(mode="after")
    def file_references(self) -> Self:
        if any(source.kind != "file" for source in (self.cases, self.execution, self.analysis)):
            raise ValueError("cases, execution and analysis must reference files")
        return self


@dataclass(frozen=True)
class LoadedCase:
    case: Case
    root: Path
    allow_unsigned: bool = False


@dataclass(frozen=True)
class LoadedCases:
    cases: dict[str, LoadedCase]
    root: Path
    allow_unsigned: bool = False

    def case(self, case_id: str) -> LoadedCase:
        if case_id not in self.cases:
            raise ValueError(f"unknown case: {case_id}")
        return self.cases[case_id]

    @property
    def identity(self) -> str:
        return digest_bytes(json_bytes(self.identity_payload()))

    def identity_payload(self, *, verify: bool = False) -> list[dict]:
        """Use content digests whether inputs were signed before or after execution.

        Signed metadata suffices for analysis without executable inputs. Unsigned
        sources must still be available to establish their content identity.
        """
        cases = []
        for prepared in self.cases.values():
            case = prepared.case.model_dump()
            sources = list(case["task_sources"].values())
            sources.extend(
                source for agent in [case["baseline"], *case["mutants"]] for source in agent["inputs"].values()
            )
            for source in sources:
                reference = PinnedInput.model_validate(source)
                if verify or reference.sha256 is None:
                    path = reference.resolve(prepared.root, allow_unsigned=prepared.allow_unsigned)
                    source["sha256"] = reference.sha256 or input_digest(path)
            cases.append(case)
        return cases

    def verify_execution_inputs(self) -> None:
        for prepared in self.cases.values():
            case = prepared.case
            sources = [(f"task {name}", source) for name, source in case.task_sources.items()]
            sources.extend(
                (f"agent {agent.agent_bundle_id}, input {name}", source)
                for agent in case.agents()
                for name, source in agent.inputs.items()
            )
            for label, source in sources:
                try:
                    source.resolve(self.root, allow_unsigned=self.allow_unsigned)
                except (OSError, ValueError) as error:
                    raise ValueError(f"case {case.case_id}: {label}: {error}") from error


@dataclass(frozen=True)
class LoadedExecution:
    inputs: LoadedCases
    settings: LocalExecution


@dataclass(frozen=True)
class LoadedAnalysis:
    inputs: LoadedCases
    settings: AnalysisSettings

    def load_subsets(self, case_id: str) -> Subsets:
        prepared = self.inputs.case(case_id)
        try:
            source = self.settings.case_settings[case_id].subsets
            subsets = Subsets.model_validate(
                read_json(source.resolve(self.inputs.root, allow_unsigned=self.inputs.allow_unsigned))
            )
            subsets.resolve_tasks(prepared.case.task_inventory)
            return subsets
        except (OSError, ValueError) as error:
            raise ValueError(f"case {case_id}, subsets: {error}") from error

    def load_description(self, case_id: str) -> dict:
        self.inputs.case(case_id)
        try:
            source = self.settings.case_settings[case_id].description
            description = read_json(source.resolve(self.inputs.root, allow_unsigned=self.inputs.allow_unsigned))
            if not isinstance(description, dict):
                raise ValueError("case description must be a JSON object")
            return description
        except (OSError, ValueError) as error:
            raise ValueError(f"case {case_id}, case_description: {error}") from error

    @property
    def identity(self) -> str:
        artifacts = {}
        for case_id in self.inputs.cases:
            artifacts[case_id] = {
                "subsets": self.load_subsets(case_id).model_dump(),
                "description": self.load_description(case_id),
            }
        return digest_bytes(
            json_bytes({"cases": self.inputs.identity, "settings": self.settings.model_dump(), "artifacts": artifacts})
        )


@dataclass(frozen=True)
class LoadedBenchmark:
    definition: BenchmarkDefinition
    execution: LoadedExecution
    analysis: LoadedAnalysis


def read_yaml(path: Path) -> object:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {path.name}: {error}") from error


def load_cases(path: Path, *, allow_unsigned: bool = False) -> LoadedCases:
    """Read case and agent/task metadata without opening executable inputs."""
    definitions = TypeAdapter(list[CaseDefinition]).validate_python(read_yaml(path))
    if not definitions:
        raise ValueError("cases must not be empty")
    unique([entry.case_id for entry in definitions], "case IDs")
    root = path.resolve().parent
    cases = {}
    for entry in definitions:
        try:
            agent = AgentManifest.model_validate(read_yaml(entry.agent.resolve(root, allow_unsigned=allow_unsigned)))
            tasks = TaskManifest.model_validate(read_yaml(entry.tasks.resolve(root, allow_unsigned=allow_unsigned)))
            case = Case.from_manifests(entry, agent, tasks)
        except (OSError, ValueError) as error:
            raise ValueError(f"case {entry.case_id}: {error}") from error
        cases[case.case_id] = LoadedCase(case, root, allow_unsigned=allow_unsigned)
    return LoadedCases(cases, root, allow_unsigned)


def load_execution(cases: Path, execution: Path, *, allow_unsigned: bool = False) -> LoadedExecution:
    inputs = load_cases(cases, allow_unsigned=allow_unsigned)
    settings = LocalExecution.model_validate(read_yaml(execution))
    inputs.verify_execution_inputs()
    return LoadedExecution(inputs, settings)


def load_analysis(cases: Path, analysis: Path, *, allow_unsigned: bool = False) -> LoadedAnalysis:
    inputs = load_cases(cases, allow_unsigned=allow_unsigned)
    settings = AnalysisSettings.model_validate(read_yaml(analysis))
    if settings.case_settings.keys() != inputs.cases.keys():
        raise ValueError("analysis case_settings must match the cases exactly")
    return LoadedAnalysis(inputs, settings)


def load_benchmark(path: Path, *, allow_unsigned: bool = False) -> LoadedBenchmark:
    """Read the experiment and stage configurations without opening executable inputs."""
    definition = BenchmarkDefinition.model_validate(read_yaml(path))
    root = path.resolve().parent
    cases_path = definition.cases.resolve(root, allow_unsigned=allow_unsigned)
    execution_path = definition.execution.resolve(root, allow_unsigned=allow_unsigned)
    analysis_path = definition.analysis.resolve(root, allow_unsigned=allow_unsigned)
    analysis = load_analysis(cases_path, analysis_path, allow_unsigned=allow_unsigned)
    execution = LoadedExecution(analysis.inputs, LocalExecution.model_validate(read_yaml(execution_path)))
    return LoadedBenchmark(definition, execution, analysis)
