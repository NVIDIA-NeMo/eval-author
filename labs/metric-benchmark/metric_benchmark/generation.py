# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Generate experiment configs from prepared cases and explicit selections."""

import copy
import shutil
import tempfile
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, JsonValue, TypeAdapter, model_validator

from metric_benchmark.config import (
    Agent,
    AgentManifest,
    AnalysisSettings,
    BenchmarkDefinition,
    CaseAnalysis,
    CaseDefinition,
    LocalExecution,
    PinnedInput,
    TaskManifest,
    TaskSource,
    load_analysis,
    read_yaml,
)
from metric_benchmark.io import input_digest, write_json
from metric_benchmark.models import Model, Subset, Subsets, Text, unique
from metric_benchmark.signing import check_benchmark
from metric_benchmark.validation import agent_config


class Selection(Model):
    source_case_id: Text
    case_id: Text
    mutant_ids: Literal["all"] | list[Text] = Field(min_length=1)
    task_ids: Literal["all"] | list[Text] = Field(min_length=1)
    subsets: Literal["parent"] | list[Subset] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_selections(self) -> Self:
        for label, values in (("mutant IDs", self.mutant_ids), ("task IDs", self.task_ids)):
            if isinstance(values, list):
                unique(values, label)
        return self


class GenerationSummary(Model):
    experiment: str
    cases: int
    agents: int
    tasks: int
    subsets: int
    trials: int


def _selected(requested: Literal["all"] | list[str], available: list[str], label: str) -> set[str]:
    if requested == "all":
        return set(available)
    unique(requested, label)
    if not requested:
        raise ValueError(f"{label} must not be empty")
    unknown = set(requested) - set(available)
    if unknown:
        raise ValueError(f"unknown {label}: {sorted(unknown)}")
    return set(requested)


def _merge(original: dict[str, JsonValue], overrides: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = copy.deepcopy(original)
    for name, value in overrides.items():
        previous = result.get(name)
        result[name] = (
            _merge(previous, value) if isinstance(previous, dict) and isinstance(value, dict) else copy.deepcopy(value)
        )
    return result


def _pin(path: Path, root: Path) -> PinnedInput:
    return PinnedInput(
        path=path.relative_to(root).as_posix(), sha256=input_digest(path), kind="directory" if path.is_dir() else "file"
    )


def _write_yaml(path: Path, value: Model | list[CaseDefinition]) -> None:
    data = (
        [entry.model_dump(exclude_none=True) for entry in value]
        if isinstance(value, list)
        else value.model_dump(exclude_none=True)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _copy_input(source_root: Path, output: Path, reference: PinnedInput) -> PinnedInput:
    source = reference.resolve(source_root)
    target = output / "inputs" / reference.path
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    copied = reference.model_copy(update={"path": target.relative_to(output).as_posix()})
    copied.resolve(output)
    return copied


def _copy_agent(agent: Agent, root: Path, output: Path, overrides: dict[str, JsonValue]) -> Agent:
    return Agent.model_validate(
        {
            **agent.model_dump(),
            "inputs": {name: _copy_input(root, output, ref) for name, ref in agent.inputs.items()},
            "harbor": _merge(agent.harbor, overrides),
        }
    )


def _snapshot_author(analysis: AnalysisSettings, author: Path, stage: Path, output: Path) -> PinnedInput:
    author = author.resolve(strict=True)
    coverage = analysis.coverage
    command = coverage.settings.get("author_command")
    if not isinstance(command, list) or command.count(str(author)) != 1:
        raise ValueError("coverage author must appear exactly once as an absolute path in author_command")
    if input_digest(author) != coverage.system_revision:
        raise ValueError("coverage author differs from system_revision")
    target = stage / "coverage" / author.name
    target.parent.mkdir()
    shutil.copy2(author, target)
    ref = _pin(target, stage)
    if ref.sha256 != coverage.system_revision:
        raise ValueError("coverage author changed while copying")
    command[command.index(str(author))] = str(output / ref.path)
    return ref


def generate_experiment(
    source: Path,
    output: Path,
    *,
    execution: Path,
    benchmark_id: str,
    selection: Selection | None = None,
    case_ids: list[str] | None = None,
    analysis: Path | None = None,
    agent_settings: Path | None = None,
    coverage_author: Path | None = None,
) -> GenerationSummary:
    source, output = source.resolve(strict=True), output.resolve()
    if output.exists():
        raise ValueError("output already exists; use a new experiment directory")
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("output must be outside the source bundle")
    if selection is not None and case_ids is not None:
        raise ValueError("use either --selection or --case, not both")
    analysis_path = analysis or source / "analysis.yml"
    loaded = load_analysis(source / "cases.yml", analysis_path)
    execution_settings = LocalExecution.model_validate(read_yaml(execution))
    overrides = TypeAdapter(dict[str, JsonValue]).validate_python(read_yaml(agent_settings)) if agent_settings else {}
    available = list(loaded.inputs.cases)
    if selection is not None:
        if selection.case_id in available:
            raise ValueError("selected experiment needs a distinct case_id")
        loaded.inputs.case(selection.source_case_id)
        requests = [selection]
    else:
        chosen_ids = _selected(case_ids if case_ids is not None else "all", available, "case IDs")
        requests = [
            Selection(source_case_id=case_id, case_id=case_id, mutant_ids="all", task_ids="all", subsets="parent")
            for case_id in available
            if case_id in chosen_ids
        ]
    # Check the identity before copying potentially large inputs.
    TypeAdapter(Text).validate_python(benchmark_id)
    receipt: dict[str, JsonValue] = {
        "artifact_type": "generated_experiment",
        "schema_version": 1,
        "source_cases_sha256": input_digest(source / "cases.yml"),
        "analysis_sha256": input_digest(analysis_path),
        "execution_sha256": input_digest(execution),
        "selections": [request.model_dump(mode="json") for request in requests],
    }
    if agent_settings:
        receipt["agent_settings_sha256"] = input_digest(agent_settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = dict(cases=len(requests), agents=0, tasks=0, subsets=0, trials=0)
    with tempfile.TemporaryDirectory(prefix=".experiment-", dir=output.parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        definitions: list[CaseDefinition] = []
        case_settings: dict[str, CaseAnalysis] = {}
        for index, request in enumerate(requests):
            case = loaded.inputs.case(request.source_case_id).case
            mutants = _selected(request.mutant_ids, [agent.agent_bundle_id for agent in case.mutants], "mutant IDs")
            task_ids = _selected(request.task_ids, [task.task_id for task in case.task_inventory.tasks], "task IDs")
            agents = AgentManifest(
                baseline=_copy_agent(case.baseline, source, stage, overrides),
                mutants=[
                    _copy_agent(agent, source, stage, overrides)
                    for agent in case.mutants
                    if agent.agent_bundle_id in mutants
                ],
            )
            tasks = []
            for task in case.task_inventory.tasks:
                if task.task_id in task_ids:
                    ref = _copy_input(source, stage, case.task_sources[task.task_id])
                    tasks.append(TaskSource(**task.model_dump(), path=ref.path, sha256=ref.sha256))
            manifest = TaskManifest(tasks=tasks)
            subsets = (
                loaded.load_subsets(request.source_case_id)
                if request.subsets == "parent"
                else Subsets(
                    sampling_parameters={"method": "explicit frozen selection"},
                    eval_subsets=request.subsets,
                )
            )
            subsets.resolve_tasks(manifest.task_inventory)
            loaded.load_description(request.source_case_id)
            settings = loaded.settings.case_settings[request.source_case_id]
            description = _copy_input(source, stage, settings.description)
            directory = stage / "manifests" / f"case-{index:04d}"
            directory.mkdir(parents=True)
            _write_yaml(directory / "agents.yml", agents)
            _write_yaml(directory / "tasks.yml", manifest)
            if request.subsets == "parent":
                shutil.copy2(settings.subsets.resolve(source), directory / "subsets.json")
            else:
                write_json(directory / "subsets.json", subsets)
            definitions.append(
                CaseDefinition(
                    case_id=request.case_id,
                    agent=_pin(directory / "agents.yml", stage),
                    tasks=_pin(directory / "tasks.yml", stage),
                )
            )
            case_settings[request.case_id] = CaseAnalysis(
                description=description, subsets=_pin(directory / "subsets.json", stage)
            )
            for agent in [agents.baseline, *agents.mutants]:
                agent_config(agent, stage)
            counts["agents"] += 1 + len(agents.mutants)
            counts["tasks"] += len(tasks)
            counts["subsets"] += len(subsets.eval_subsets)
            counts["trials"] += (1 + len(agents.mutants)) * len(tasks)
        settings = loaded.settings.model_copy(deep=True, update={"case_settings": case_settings})
        if coverage_author is not None:
            receipt["coverage_author"] = _snapshot_author(settings, coverage_author, stage, output).model_dump(
                mode="json"
            )
        _write_yaml(stage / "cases.yml", definitions)
        _write_yaml(stage / "analysis.yml", settings)
        _write_yaml(stage / "execution.yml", execution_settings)
        _write_yaml(
            stage / "experiment.yml",
            BenchmarkDefinition(
                benchmark_id=benchmark_id,
                revision=1,
                cases=_pin(stage / "cases.yml", stage),
                execution=_pin(stage / "execution.yml", stage),
                analysis=_pin(stage / "analysis.yml", stage),
            ),
        )
        write_json(stage / "generation.json", receipt)
        check_benchmark(stage / "experiment.yml")
        if output.exists():
            raise ValueError("output appeared during generation; use a new directory")
        stage.rename(output)
    return GenerationSummary(experiment=str(output / "experiment.yml"), **counts)
