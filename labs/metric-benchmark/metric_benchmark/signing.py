# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Apply content pins to a prepared benchmark without executing its agents."""

import argparse
import os
import tempfile
from pathlib import Path

import yaml

from metric_benchmark.config import (
    AgentManifest,
    BenchmarkDefinition,
    Case,
    PinnedInput,
    TaskManifest,
    input_digest,
    read_yaml,
)
from metric_benchmark.io import cli, digest_bytes, read_json
from metric_benchmark.models import Subsets


def _yaml_bytes(path: Path, data: dict) -> bytes:
    # Preserve leading comments (including license headers); PyYAML rewrites the body.
    prefix = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.strip() and not line.lstrip().startswith("#"):
            break
        prefix.append(line)
    return ("".join(prefix) + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)).encode("utf-8")


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


def sign_benchmark(path: Path, *, force: bool = False) -> list[Path]:
    """Validate inputs, then write their hashes into manifests, leaves first."""
    path = path.resolve(strict=True)
    root = path.parent
    documents: dict[Path, dict] = {}
    references: dict[tuple[Path, str], tuple[dict, Path, bool]] = {}

    def document(target: Path) -> dict:
        if target not in documents:
            data = read_yaml(target)
            if not isinstance(data, dict):
                raise ValueError(f"{target.relative_to(root)} must be a YAML mapping")
            documents[target] = data
        return documents[target]

    def reference(owner: Path, label: str, data: dict, source: PinnedInput, *, manifest: bool = False) -> Path:
        try:
            target = source.resolve(root, verify_digest=False)
        except (OSError, ValueError) as error:
            raise ValueError(f"{owner.relative_to(root)}:{label}: {error}") from error
        references[owner, label] = data, target, manifest
        return target

    experiment = document(path)
    definition = BenchmarkDefinition.model_validate(experiment)
    for entry, raw_case in zip(definition.cases, experiment["cases"], strict=True):
        label = f"cases[{entry.case_id}]"
        agent_path = reference(path, f"{label}.agent", raw_case["agent"], entry.agent, manifest=True)
        tasks_path = reference(path, f"{label}.tasks", raw_case["tasks"], entry.tasks, manifest=True)
        agent_data, tasks_data = document(agent_path), document(tasks_path)
        agent, tasks = AgentManifest.model_validate(agent_data), TaskManifest.model_validate(tasks_data)
        case = Case.from_manifests(entry, agent, tasks)
        subset_path = reference(path, f"{label}.subsets", raw_case["subsets"], entry.subsets)
        Subsets.model_validate(read_json(subset_path)).resolve_tasks(case.task_inventory)
        description = reference(agent_path, "description", agent_data["description"], agent.description)
        if not isinstance(read_json(description), dict):
            raise ValueError(f"case {entry.case_id}: case description must be a JSON object")
        for i, (task, raw_task) in enumerate(zip(tasks.tasks, tasks_data["tasks"], strict=True)):
            reference(tasks_path, f"tasks[{i}]", raw_task, task.source())
        versions = zip(case.agents(), [agent_data["baseline"], *agent_data["mutants"]], strict=True)
        for i, (version, raw_version) in enumerate(versions):
            agent_label = "baseline" if i == 0 else f"mutants[{i - 1}]"
            for name, source in version.inputs.items():
                reference(agent_path, f"{agent_label}.inputs.{name}", raw_version["inputs"][name], source)

    existing = [
        f"{owner.relative_to(root)}:{label}" for (owner, label), (data, _, _) in references.items() if "sha256" in data
    ]
    if existing and not force:
        raise ValueError(
            "SHA fields already exist:\n  "
            + "\n  ".join(existing)
            + "\nDelete these fields or use --force to reapply them."
        )

    for data, target, manifest in references.values():
        if not manifest:
            # Rewriting a manifest inside a hashed input would invalidate that input's pin.
            if any(target == item or target in item.parents for item in documents):
                raise ValueError(f"input overlaps a manifest being signed: {target.relative_to(root)}")
            data["sha256"] = input_digest(target)
    planned = {target: _yaml_bytes(target, data) for target, data in documents.items() if target != path}
    for data, target, manifest in references.values():
        if manifest:
            data["sha256"] = digest_bytes(planned[target])
    planned[path] = _yaml_bytes(path, experiment)
    changed = []
    for target, content in planned.items():
        if target.read_bytes() != content:
            _replace(target, content)
            changed.append(target)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(prog="sha-sign", description="Validate and SHA-pin a prepared benchmark.")
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--force", action="store_true", help="replace existing SHA fields with current content hashes")

    def operation(args: argparse.Namespace) -> None:
        for changed in sign_benchmark(args.experiment, force=args.force):
            print(changed)

    cli(parser, operation)
