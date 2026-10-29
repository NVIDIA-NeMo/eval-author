# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sign prepared benchmarks and check that every referenced input is pinned."""

import os
import tempfile
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from metric_benchmark.config import (
    AgentManifest,
    AnalysisSettings,
    BenchmarkDefinition,
    Case,
    CaseDefinition,
    LocalExecution,
    PinnedInput,
    TaskManifest,
    load_benchmark,
    read_yaml,
)
from metric_benchmark.io import digest_bytes, input_digest, read_json
from metric_benchmark.models import Subsets, unique


def _sha_edits(node: yaml.Node, before, after, text: str) -> list[tuple[int, int, str]]:
    if before == after:
        return []
    edits = []
    if isinstance(node, yaml.SequenceNode):
        for child, old, new in zip(node.value, before, after, strict=True):
            edits.extend(_sha_edits(child, old, new, text))
        return edits
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("signing can only change sha256 fields")
    if after.keys() - before.keys() - {"sha256"}:
        raise ValueError("signing can only add sha256 fields")
    if "sha256" in after and "sha256" not in before:
        first_key = node.value[0][0].start_mark
        newline = "\r\n" if "\r\n" in text else "\n"
        separator = ", " if node.flow_style else newline + " " * first_key.column
        edits.append((first_key.index, first_key.index, f"sha256: {after['sha256']}" + separator))
    for key, value in node.value:
        name = key.value
        if name != "sha256":
            edits.extend(_sha_edits(value, before[name], after[name], text))
        elif before[name] != after[name]:
            if not isinstance(value, yaml.ScalarNode) or value.style not in (None, "'", '"'):
                raise ValueError("use a plain or quoted scalar for sha256 before re-signing")
            start, end = value.start_mark.index, value.end_mark.index
            original = text[start:end]
            if original.startswith(("&", "!", "*")):
                raise ValueError("use an unanchored, untagged scalar for sha256 before re-signing")
            quote = value.style or ""
            replacement = quote + after[name] + quote
            if start and text[start - 1] == ":":
                replacement = " " + replacement
            if end < len(text) and text[end] == "#":
                replacement += " "
            edits.append((start, end, replacement))
    return edits


def _yaml_bytes(path: Path, data: dict | list) -> bytes:
    # Parser positions let us change pins without reformatting YAML or losing comments.
    text = path.read_bytes().decode("utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is None:
        raise ValueError(f"empty YAML manifest: {path}")
    edits = set(_sha_edits(node, read_yaml(path), data, text))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode("utf-8")


def _replace(path: Path, data: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def yaml_bytes(path: Path, data: dict | list) -> bytes:
    return _yaml_bytes(path, data)


def replace_file(path: Path, data: bytes) -> None:
    _replace(path, data)


def _collect(path: Path) -> tuple[dict[Path, dict | list], dict[tuple[Path, str], tuple[dict, Path, bool]]]:
    root = path.parent
    documents: dict[Path, dict | list] = {}
    references: dict[tuple[Path, str], tuple[dict, Path, bool]] = {}

    def document(target: Path):
        if target not in documents:
            data = read_yaml(target)
            if not isinstance(data, (dict, list)):
                raise ValueError(f"{target.name} must be a YAML mapping or list")
            documents[target] = data
        return documents[target]

    def reference(owner: Path, label: str, data: dict, source: PinnedInput, *, manifest=False) -> Path:
        try:
            target = source.resolve(root, verify_digest=False)
        except (OSError, ValueError) as error:
            raise ValueError(f"{owner.name}:{label}: {error}") from error
        references[owner, label] = data, target, manifest
        return target

    experiment = document(path)
    definition = BenchmarkDefinition.model_validate(experiment)
    cases_path = reference(path, "cases", experiment["cases"], definition.cases, manifest=True)
    execution_path = reference(path, "execution", experiment["execution"], definition.execution, manifest=True)
    analysis_path = reference(path, "analysis", experiment["analysis"], definition.analysis, manifest=True)
    LocalExecution.model_validate(document(execution_path))
    analysis_data = document(analysis_path)
    analysis = AnalysisSettings.model_validate(analysis_data)
    cases_data = document(cases_path)
    entries = TypeAdapter(list[CaseDefinition]).validate_python(cases_data)
    if not entries:
        raise ValueError("cases must not be empty")
    unique([entry.case_id for entry in entries], "case IDs")
    if set(analysis.case_settings) != {entry.case_id for entry in entries}:
        raise ValueError("analysis case_settings must match the cases exactly")
    root = cases_path.parent
    for entry, raw_case in zip(entries, cases_data, strict=True):
        label = f"cases[{entry.case_id}]"
        agent_path = reference(cases_path, f"{label}.agent", raw_case["agent"], entry.agent, manifest=True)
        tasks_path = reference(cases_path, f"{label}.tasks", raw_case["tasks"], entry.tasks, manifest=True)
        agent_data, tasks_data = document(agent_path), document(tasks_path)
        agent, tasks = AgentManifest.model_validate(agent_data), TaskManifest.model_validate(tasks_data)
        case = Case.from_manifests(entry, agent, tasks)
        settings, raw_settings = analysis.case_settings[entry.case_id], analysis_data["case_settings"][entry.case_id]
        subset_path = reference(analysis_path, f"{label}.subsets", raw_settings["subsets"], settings.subsets)
        Subsets.model_validate(read_json(subset_path)).resolve_tasks(case.task_inventory)
        description = reference(
            analysis_path, f"{label}.description", raw_settings["description"], settings.description
        )
        if not isinstance(read_json(description), dict):
            raise ValueError(f"case {entry.case_id}: case description must be a JSON object")
        for i, (task, raw_task) in enumerate(zip(tasks.tasks, tasks_data["tasks"], strict=True)):
            reference(tasks_path, f"tasks[{i}]", raw_task, task.source())
        versions = zip(case.agents(), [agent_data["baseline"], *agent_data["mutants"]], strict=True)
        for i, (version, raw_version) in enumerate(versions):
            agent_label = "baseline" if i == 0 else f"mutants[{i - 1}]"
            for name, source in version.inputs.items():
                reference(agent_path, f"{agent_label}.inputs.{name}", raw_version["inputs"][name], source)
    return documents, references


def check_benchmark(path: Path) -> None:
    """Require and verify every input pin without writing files."""
    loaded = load_benchmark(path)
    loaded.execution.inputs.verify_execution_inputs()
    for case_id in loaded.analysis.inputs.cases:
        loaded.analysis.load_subsets(case_id)
        loaded.analysis.load_description(case_id)


def sign_benchmark(path: Path, *, resign: bool = False) -> list[Path]:
    """Validate inputs, then write their hashes into manifests, leaves first."""
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
            # Rewriting a manifest inside a hashed input would invalidate that input's pin.
            if any(target == item or target in item.parents for item in documents):
                raise ValueError(f"input overlaps a manifest being signed: {target.relative_to(root)}")
            data["sha256"] = input_digest(target)
    planned = {}
    active = set()

    def finalize(target):
        if target in active:
            raise ValueError("cyclic manifest references")
        if target not in planned:
            active.add(target)
            for (owner, _), (data, child, manifest) in references.items():
                if owner == target and manifest:
                    data["sha256"] = digest_bytes(finalize(child))
            planned[target] = _yaml_bytes(target, documents[target])
            active.remove(target)
        return planned[target]

    finalize(path)
    changed = []
    for target, content in planned.items():
        if target.read_bytes() != content:
            _replace(target, content)
            changed.append(target)
    return changed
