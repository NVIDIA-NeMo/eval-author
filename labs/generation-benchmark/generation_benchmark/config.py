# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load a generation experiment and verify its prepared inputs."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from metric_benchmark.config import (
    Agent,
    AgentManifest,
    AnalysisSettings,
    Case,
    CaseDefinition,
    CoverageConfig,
    LoadedAnalysis,
    LoadedCase,
    LoadedCases,
    LoadedExecution,
    LocalExecution,
    PinnedInput,
    TaskManifest,
    TaskSource,
    input_digest,
    read_yaml,
)
from metric_benchmark.io import digest_bytes, json_bytes
from metric_benchmark.models import Model, Text, unique
from pydantic import Field, TypeAdapter, model_validator

GENERATION_INPUT_KEYS = frozenset({"agent_config", "generator_config"})
GENERATED_TASKS_FILENAME = "tasks.generated.yml"


class GenerationConfig(CoverageConfig):
    """Generation adapter subprocess contract (mirrors coverage)."""


class GenerationAgentManifest(AgentManifest):
    """Baseline agent.

    Harbor helpers still see a metric ``Case`` whose ``agents()`` list is the
    baseline alone. Generation manifests leave that list empty by omitting mutants.
    """

    mutants: list[Agent] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def omit_mutants(cls, data: object) -> object:
        if isinstance(data, dict) and "mutants" in data:
            raise ValueError("generation agent manifests omit mutants")
        return data


class GenerationCase(Case):
    """In-memory metric case used by Harbor execution and results validation."""

    mutants: list[Agent] = Field(default_factory=list)

    @classmethod
    def from_manifests(
        cls, definition: CaseDefinition, agent: GenerationAgentManifest, tasks: TaskManifest
    ) -> Self:
        return cls(
            case_id=definition.case_id,
            baseline=agent.baseline,
            mutants=agent.mutants,
            task_inventory=tasks.task_inventory,
            task_sources=tasks.task_sources,
        )


class GenerationBenchmarkDefinition(Model):
    benchmark_id: Text
    revision: int = Field(ge=1)
    experiment_kind: Literal["generation"] = "generation"
    cases: PinnedInput
    execution: PinnedInput
    analysis: PinnedInput
    generation: PinnedInput

    @model_validator(mode="after")
    def file_references(self) -> Self:
        pins = (self.cases, self.execution, self.analysis, self.generation)
        if any(source.kind != "file" for source in pins):
            raise ValueError("cases, execution, analysis, and generation must reference files")
        return self


@dataclass(frozen=True)
class LoadedGenerationBenchmark:
    definition: GenerationBenchmarkDefinition
    execution: LoadedExecution
    analysis: LoadedAnalysis
    generation: GenerationConfig
    generation_inputs: dict[str, PinnedInput]

    @property
    def root(self) -> Path:
        return self.execution.inputs.root

    @property
    def identity(self) -> str:
        from metric_benchmark.execution import execution_identity

        return digest_bytes(
            json_bytes(
                {
                    "execution_sha256": execution_identity(self.execution),
                    "analysis_sha256": self.analysis.identity,
                    "generation_sha256": generation_process_sha256(self.generation),
                }
            )
        )

    def case(self, case_id: str) -> GenerationCase:
        prepared = self.prepared(case_id)
        if not isinstance(prepared.case, GenerationCase):
            raise ValueError(f"case {case_id} is not a generation case")
        return prepared.case

    def prepared(self, case_id: str) -> LoadedCase:
        return self.execution.inputs.case(case_id)

    def resolved_generation_settings(self) -> dict:
        settings = dict(self.generation.settings)
        allow_unsigned = self.execution.inputs.allow_unsigned
        for name in GENERATION_INPUT_KEYS:
            settings[name] = str(self.generation_inputs[name].resolve(self.root, allow_unsigned=allow_unsigned))
        return settings


def read_generation_benchmark(path: Path) -> GenerationBenchmarkDefinition:
    try:
        data = read_yaml(path)
        json_bytes(data)
        return GenerationBenchmarkDefinition.model_validate(data)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid experiment YAML: {error}") from error


def generation_process_sha256(generation: GenerationConfig) -> str:
    return digest_bytes(
        json_bytes(
            {
                "system_id": generation.system_id,
                "system_revision": generation.system_revision,
                "command": generation.command,
                "timeout_seconds": generation.timeout_seconds,
            }
        )
    )


def _generation_inputs(data: object) -> dict[str, PinnedInput]:
    if not isinstance(data, dict):
        raise ValueError("generation inputs must be a mapping")
    inputs = {name: PinnedInput.model_validate(source) for name, source in data.items()}
    if set(inputs) != GENERATION_INPUT_KEYS:
        raise ValueError("generation inputs must pin exactly: " + ", ".join(sorted(GENERATION_INPUT_KEYS)))
    for name, source in inputs.items():
        if source.kind != "file":
            raise ValueError(f"generation input {name} must be a file pin")
    return inputs


def draft_dir_from_settings(settings: dict) -> Path:
    """Relative work-cell directory where G deposits Harbor task drafts."""
    value = settings.get("draft_dir")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("generation.settings.draft_dir is required")
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(
            "generation.settings.draft_dir must be a relative path inside the generator work cell"
        )
    return path


def _verify_generation_policy(
    generation: GenerationConfig,
    inputs: dict[str, PinnedInput],
    root: Path,
    allow_unsigned: bool,
) -> None:
    settings = generation.settings
    draft_dir_from_settings(settings)
    expected_process = settings.get("process_sha256")
    if not isinstance(expected_process, str) or not expected_process.startswith("sha256:"):
        raise ValueError("generation.settings.process_sha256 is required")
    actual_process = generation_process_sha256(generation)
    if expected_process != actual_process:
        raise ValueError("generation process_sha256 does not match generation command metadata")
    source_sha = settings.get("source_sha256")
    eval_root_value = settings.get("eval_author_root")
    if not isinstance(source_sha, str) or not isinstance(eval_root_value, str):
        raise ValueError("generation.settings requires eval_author_root and source_sha256")
    eval_root = Path(eval_root_value).expanduser().resolve()
    from metric_benchmark.eval_author import source_digest

    if source_digest(eval_root) != source_sha:
        raise ValueError("generation eval_author source_sha256 does not match eval_author_root")
    skill_pin = settings.get("task_create_skill_sha256")
    if skill_pin is not None:
        from generation_benchmark.eval_author.identity import tree_digest

        skill_root = eval_root / "skills/eval-author-task-create"
        if tree_digest(skill_root) != skill_pin:
            raise ValueError("generation task_create_skill_sha256 does not match eval_author_root")
    for source in inputs.values():
        source.resolve(root, allow_unsigned=allow_unsigned)


def _load_task_manifest(tasks_pin: PinnedInput, root: Path, *, allow_unsigned: bool) -> TaskManifest:
    tasks_path = tasks_pin.resolve(root, allow_unsigned=allow_unsigned)
    base = TaskManifest.model_validate(read_yaml(tasks_path))
    generated_path = root / GENERATED_TASKS_FILENAME
    if not generated_path.is_file():
        return base
    extra = TaskManifest.model_validate(read_yaml(generated_path))
    merged: dict[str, TaskSource] = {task.task_id: task for task in base.tasks}
    for task in extra.tasks:
        merged.setdefault(task.task_id, task)
    ordered = sorted(merged.values(), key=lambda task: task.task_id)
    return TaskManifest(tasks=ordered)


def _load_cases(cases_path: Path, *, allow_unsigned: bool) -> LoadedCases:
    definitions = TypeAdapter(list[CaseDefinition]).validate_python(read_yaml(cases_path))
    if not definitions:
        raise ValueError("cases must not be empty")
    unique([entry.case_id for entry in definitions], "case IDs")
    root = cases_path.parent
    cases: dict[str, LoadedCase] = {}
    for entry in definitions:
        try:
            agent = GenerationAgentManifest.model_validate(
                read_yaml(entry.agent.resolve(root, allow_unsigned=allow_unsigned))
            )
            tasks = _load_task_manifest(entry.tasks, root, allow_unsigned=allow_unsigned)
            case = GenerationCase.from_manifests(entry, agent, tasks)
        except (OSError, ValueError) as error:
            raise ValueError(f"case {entry.case_id}: {error}") from error
        cases[case.case_id] = LoadedCase(case, root, allow_unsigned=allow_unsigned)
    return LoadedCases(cases, root, allow_unsigned)


def load_generation_benchmark(path: Path, *, allow_unsigned: bool = False) -> LoadedGenerationBenchmark:
    definition = read_generation_benchmark(path)
    experiment_root = path.resolve().parent
    cases_path = definition.cases.resolve(experiment_root, allow_unsigned=allow_unsigned)
    execution_path = definition.execution.resolve(experiment_root, allow_unsigned=allow_unsigned)
    analysis_path = definition.analysis.resolve(experiment_root, allow_unsigned=allow_unsigned)
    generation_path = definition.generation.resolve(experiment_root, allow_unsigned=allow_unsigned)
    loaded_cases = _load_cases(cases_path, allow_unsigned=allow_unsigned)
    execution = LoadedExecution(loaded_cases, LocalExecution.model_validate(read_yaml(execution_path)))
    loaded_cases.verify_execution_inputs()
    analysis_settings = AnalysisSettings.model_validate(read_yaml(analysis_path))
    if set(analysis_settings.case_settings) != set(loaded_cases.cases):
        raise ValueError("analysis case_settings must match the cases exactly")
    analysis = LoadedAnalysis(loaded_cases, analysis_settings)
    for case_id in loaded_cases.cases:
        analysis.load_subsets(case_id)
        analysis.load_description(case_id)
    generation_raw = read_yaml(generation_path)
    if not isinstance(generation_raw, dict):
        raise ValueError("generation settings must be a YAML mapping")
    try:
        inputs = _generation_inputs(generation_raw["inputs"])
    except KeyError as error:
        raise ValueError("generation settings require inputs") from error
    generation = GenerationConfig.model_validate({key: value for key, value in generation_raw.items() if key != "inputs"})
    _verify_generation_policy(generation, inputs, loaded_cases.root, allow_unsigned)
    return LoadedGenerationBenchmark(
        definition=definition,
        execution=execution,
        analysis=analysis,
        generation=generation,
        generation_inputs=inputs,
    )


def append_generated_task_manifest(
    root: Path,
    *,
    task_id: str,
    relative_path: str,
    digest: str,
) -> None:
    path = root / GENERATED_TASKS_FILENAME
    tasks: list[dict] = []
    if path.is_file():
        existing = TaskManifest.model_validate(read_yaml(path))
        tasks = [
            {
                "task_id": task.task_id,
                "revision": task.revision,
                "path": task.path,
                **({"sha256": task.sha256} if task.sha256 else {}),
                **({"name": task.name} if task.name else {}),
            }
            for task in existing.tasks
        ]
    if task_id not in {row["task_id"] for row in tasks}:
        tasks.append(
            {
                "task_id": task_id,
                "revision": 1,
                "path": relative_path,
                "sha256": digest,
            }
        )
    path.write_text(
        yaml.safe_dump({"tasks": sorted(tasks, key=lambda row: row["task_id"])}, sort_keys=False), encoding="utf-8"
    )


__all__ = [
    "GENERATION_INPUT_KEYS",
    "GENERATED_TASKS_FILENAME",
    "GenerationBenchmarkDefinition",
    "GenerationCase",
    "GenerationConfig",
    "LoadedGenerationBenchmark",
    "append_generated_task_manifest",
    "generation_process_sha256",
    "input_digest",
    "load_generation_benchmark",
    "read_generation_benchmark",
]
