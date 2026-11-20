# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sign generation experiment bundles and check that every referenced input is pinned."""

from pathlib import Path

from metric_benchmark.config import (
    AnalysisSettings,
    CaseDefinition,
    LocalExecution,
    PinnedInput,
    TaskManifest,
    read_yaml,
)
from metric_benchmark.io import digest_bytes, input_digest, read_json
from metric_benchmark.models import Subsets, unique
from metric_benchmark.signing import replace_file, yaml_bytes
from pydantic import TypeAdapter

from generation_benchmark.config import (
    GENERATION_INPUT_KEYS,
    GenerationAgentManifest,
    GenerationBenchmarkDefinition,
    GenerationCase,
    GenerationConfig,
    _generation_inputs,
    load_generation_benchmark,
)


def _collect(path: Path) -> tuple[dict[Path, dict | list], dict[tuple[Path, str], tuple[dict, Path, bool]]]:
    documents: dict[Path, dict | list] = {}
    references: dict[tuple[Path, str], tuple[dict, Path, bool]] = {}

    def document(target: Path):
        if target not in documents:
            data = read_yaml(target)
            if not isinstance(data, (dict, list)):
                raise ValueError(f"{target.name} must be a YAML mapping or list")
            documents[target] = data
        return documents[target]

    def reference(owner: Path, label: str, data: dict, source: PinnedInput, *, manifest=False, base=None) -> Path:
        try:
            target = source.resolve(base or path.parent, verify_digest=False)
        except (OSError, ValueError) as error:
            raise ValueError(f"{owner.name}:{label}: {error}") from error
        references[owner, label] = data, target, manifest
        return target

    experiment = document(path)
    definition = GenerationBenchmarkDefinition.model_validate(experiment)
    cases_path = reference(path, "cases", experiment["cases"], definition.cases, manifest=True)
    execution_path = reference(path, "execution", experiment["execution"], definition.execution, manifest=True)
    analysis_path = reference(path, "analysis", experiment["analysis"], definition.analysis, manifest=True)
    generation_path = reference(path, "generation", experiment["generation"], definition.generation, manifest=True)
    LocalExecution.model_validate(document(execution_path))
    analysis_data = document(analysis_path)
    analysis = AnalysisSettings.model_validate(analysis_data)
    generation_data = document(generation_path)
    if not isinstance(generation_data, dict):
        raise ValueError("generation settings must be a YAML mapping")
    GenerationConfig.model_validate({key: value for key, value in generation_data.items() if key != "inputs"})
    try:
        inputs = _generation_inputs(generation_data["inputs"])
    except KeyError as error:
        raise ValueError("generation settings require inputs") from error
    cases_data = document(cases_path)
    entries = TypeAdapter(list[CaseDefinition]).validate_python(cases_data)
    if not entries:
        raise ValueError("cases must not be empty")
    unique([entry.case_id for entry in entries], "case IDs")
    if set(analysis.case_settings) != {entry.case_id for entry in entries}:
        raise ValueError("analysis case_settings must match the cases exactly")
    bundle_root = cases_path.parent
    for entry, raw_case in zip(entries, cases_data, strict=True):
        label = f"cases[{entry.case_id}]"
        agent_path = reference(cases_path, f"{label}.agent", raw_case["agent"], entry.agent, manifest=True, base=bundle_root)
        tasks_path = reference(cases_path, f"{label}.tasks", raw_case["tasks"], entry.tasks, manifest=True, base=bundle_root)
        agent_data, tasks_data = document(agent_path), document(tasks_path)
        agent = GenerationAgentManifest.model_validate(agent_data)
        tasks = TaskManifest.model_validate(tasks_data)
        case = GenerationCase.from_manifests(entry, agent, tasks)
        settings = analysis.case_settings[entry.case_id]
        raw_settings = analysis_data["case_settings"][entry.case_id]
        subset_path = reference(
            analysis_path, f"{label}.subsets", raw_settings["subsets"], settings.subsets, base=bundle_root
        )
        Subsets.model_validate(read_json(subset_path)).resolve_tasks(case.task_inventory)
        description = reference(
            analysis_path, f"{label}.description", raw_settings["description"], settings.description, base=bundle_root
        )
        if not isinstance(read_json(description), dict):
            raise ValueError(f"case {entry.case_id}: case description must be a JSON object")
        for i, (task, raw_task) in enumerate(zip(tasks.tasks, tasks_data["tasks"], strict=True)):
            reference(tasks_path, f"tasks[{i}]", raw_task, task.source(), base=bundle_root)
        baseline = agent_data["baseline"]
        for name, source in case.baseline.inputs.items():
            reference(agent_path, f"baseline.inputs.{name}", baseline["inputs"][name], source, base=bundle_root)
    for name in sorted(GENERATION_INPUT_KEYS):
        reference(
            generation_path,
            f"inputs.{name}",
            generation_data["inputs"][name],
            inputs[name],
            base=bundle_root,
        )
    return documents, references


def check_generation_benchmark(path: Path) -> None:
    load_generation_benchmark(path)


def sign_generation_benchmark(path: Path, *, resign: bool = False) -> list[Path]:
    path = path.resolve(strict=True)
    root = path.parent
    documents, references = _collect(path)
    existing = [
        f"{owner.relative_to(root)}:{label}" for (owner, label), (data, _, _) in references.items() if "sha256" in data
    ]
    if existing and not resign:
        raise ValueError(
            "SHA fields already exist:\n  "
            + "\n  ".join(existing)
            + "\nDelete these fields or use --resign to update their pins."
        )

    for data, target, manifest in references.values():
        if not manifest:
            if any(target == item or target in item.parents for item in documents):
                raise ValueError(f"input overlaps a manifest being signed: {target.relative_to(root)}")
            data["sha256"] = input_digest(target)
    planned: dict[Path, bytes] = {}
    active: set[Path] = set()

    def finalize(target: Path) -> bytes:
        if target in active:
            raise ValueError("cyclic manifest references")
        if target not in planned:
            active.add(target)
            for (owner, _), (data, child, manifest) in references.items():
                if owner == target and manifest:
                    data["sha256"] = digest_bytes(finalize(child))
            planned[target] = yaml_bytes(target, documents[target])
            active.remove(target)
        return planned[target]

    finalize(path)
    changed = []
    for target, content in planned.items():
        if target.read_bytes() != content:
            replace_file(target, content)
            changed.append(target)
    return changed
