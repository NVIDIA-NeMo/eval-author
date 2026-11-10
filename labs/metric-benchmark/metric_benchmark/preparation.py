# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare registry datasets and Git tasks for the local benchmark contract."""

import asyncio
import re
import shutil
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml
from harbor.constants import DEFAULT_REGISTRY_URL
from harbor.models.registry import DatasetSpec
from harbor.models.task.id import GitTaskId
from harbor.models.task.task import Task as HarborTask
from harbor.tasks.client import TaskClient, TaskIdType
from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from metric_benchmark.config import (
    AgentManifest,
    AnalysisSettings,
    BenchmarkDefinition,
    CaseDefinition,
    LocalExecution,
    PinnedInput,
    TaskManifest,
    TaskSource,
    read_yaml,
)
from metric_benchmark.io import digest_bytes, file_manifest, input_digest, json_bytes, read_json, write_json
from metric_benchmark.models import Digest, Model, Task, Text, relative_path, resolve_evidence, unique
from metric_benchmark.signing import check_benchmark, sign_benchmark

Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
RECEIPT = "preparation.json"
TASKS_DIR = "prepared-tasks"


def _url(value: str, *, git: bool = False) -> str:
    parsed = urlsplit(value)
    schemes = ("https", "http", "file") if git else ("https", "http")
    if parsed.scheme not in schemes or parsed.username or parsed.password:
        raise ValueError("use an HTTP(S) URL without embedded credentials (or file:// for Git fixtures)")
    return value


class RegistrySource(Model):
    url: Text = DEFAULT_REGISTRY_URL


class DatasetSource(Model):
    name: Text
    version: Text
    registry: RegistrySource = Field(default_factory=RegistrySource)

    @model_validator(mode="after")
    def explicit_version(self) -> Self:
        if self.version.lower() in ("latest", "head", "main", "master"):
            raise ValueError("dataset version must be explicit, not a floating selector")
        _url(self.registry.url)
        return self


class DatasetManifest(Model):
    dataset: DatasetSource


class GitSource(Model):
    git_url: Text
    git_commit_id: Commit
    path: Text

    @model_validator(mode="after")
    def valid_source(self) -> Self:
        _url(self.git_url, git=True)
        relative_path(self.path)
        return self

    def task_id(self) -> GitTaskId:
        return GitTaskId(git_url=self.git_url, git_commit_id=self.git_commit_id, path=Path(self.path))


class RemoteTask(Task):
    source: GitSource
    sha256: Digest | None = None


class SourceTasks(Model):
    tasks: list[TaskSource | RemoteTask] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        unique([task.task_id for task in self.tasks], "task IDs")
        return self


def _registry_rows(path: Path) -> list[DatasetSpec]:
    rows = TypeAdapter(list[DatasetSpec]).validate_python(read_json(path))
    unique([(row.name, row.version) for row in rows], "registry dataset versions")
    return rows


def download_registry(url: str, output: Path) -> list[DatasetSpec]:
    """Download and validate a registry snapshot without downloading its tasks."""
    _url(url)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        snapshot = Path(temporary) / "registry.json"
        with urlopen(url, timeout=60) as response:
            snapshot.write_bytes(response.read())
        rows = _registry_rows(snapshot)
        write_json(output, [row.model_dump(mode="json") for row in rows])
    return rows


def _resolve_git(url: str, ref: str | None) -> str:
    _url(url, git=True)
    if ref and re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    ref = ref or "HEAD"
    if ref.startswith("-"):
        raise ValueError("invalid Git revision")
    result = subprocess.run(
        ["git", "ls-remote", url, ref, ref + "^{}"], capture_output=True, text=True, check=True, timeout=60
    )
    rows = [line.split() for line in result.stdout.splitlines()]
    peeled = [sha for sha, name in rows if name.endswith("^{}")]
    commits = set(peeled or [sha for sha, _ in rows])
    if len(commits) != 1:
        raise ValueError(f"Git revision must resolve to exactly one commit: {ref}")
    commit = commits.pop()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("expected a full Git commit ID")
    return commit


def _dataset_tasks(source: DatasetSource, snapshot: Path) -> tuple[SourceTasks, dict]:
    rows = download_registry(source.registry.url, snapshot)
    matches = [row for row in rows if row.name == source.name and row.version == source.version]
    if len(matches) != 1:
        raise ValueError(f"dataset not found: {source.name}@{source.version}")
    spec = matches[0]
    tasks = []
    commits = {}
    for task in spec.tasks:
        if not task.git_url:
            raise ValueError("remote registry tasks must have Git sources in this proof of concept")
        key = task.git_url, task.git_commit_id
        if key not in commits:
            commits[key] = _resolve_git(*key)
        tasks.append(
            RemoteTask(
                task_id=task.name,
                revision=1,
                source=GitSource(git_url=task.git_url, git_commit_id=commits[key], path=task.path.as_posix()),
            )
        )
    return SourceTasks(tasks=tasks), {
        "dataset": source.model_dump(),
        "registry_entry": spec.model_dump(mode="json"),
        "registry_sha256": input_digest(snapshot),
    }


def _prepare_tasks(data: object, source_root: Path, root: Path, ordinal: int) -> tuple[dict, dict]:
    directory = root / TASKS_DIR / f"set-{ordinal:04d}"
    directory.mkdir(parents=True)
    origin = {}
    if isinstance(data, dict) and "dataset" in data:
        source = DatasetManifest.model_validate(data).dataset
        tasks, origin = _dataset_tasks(source, directory / "registry.json")
    else:
        tasks = SourceTasks.model_validate(data)
    remote = [task for task in tasks.tasks if isinstance(task, RemoteTask)]
    downloaded = {}
    if remote:
        ids: list[TaskIdType] = [task.source.task_id() for task in remote]
        # Duplicate sources would otherwise share a path and violate runner bindings.
        unique(ids, "remote task sources")
        try:
            result = asyncio.run(TaskClient().download_tasks(ids, output_dir=directory / "data"))
        except Exception as error:
            raise ValueError(f"Harbor task download failed: {error}") from error
        if len(result.results) != len(remote):
            raise ValueError("Harbor returned an incomplete task inventory")
        for task, item in zip(remote, result.results, strict=True):
            if item.resolved_git_commit_id != task.source.git_commit_id:
                raise ValueError("downloaded Git commit differs from the requested commit")
            downloaded[task.task_id] = item.path
    prepared = []
    sources = []
    for task in tasks.tasks:
        if isinstance(task, RemoteTask):
            path = downloaded[task.task_id]
            relative = path.relative_to(root).as_posix()
            PinnedInput(path=relative, kind="directory", sha256=task.sha256).resolve(root, allow_unsigned=True)
            sources.append({"task_id": task.task_id, "source": task.source.model_dump()})
        else:
            source_path = task.source().resolve(source_root, allow_unsigned=True)
            relative = task.path
            path = root / relative
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_path, path, symlinks=True)
            task.source().resolve(root, allow_unsigned=True)
        HarborTask(path)
        prepared.append(
            TaskSource(
                task_id=task.task_id,
                revision=task.revision,
                name=task.name,
                path=relative,
                sha256=input_digest(path),
            )
        )
    manifest = TaskManifest(tasks=prepared).model_dump(exclude_none=True)
    return manifest, {**origin, "sources": sources, "tasks_sha256": digest_bytes(json_bytes(manifest))}


def _write_yaml(path: Path, data: object) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _verify_authored_benchmark(path: Path) -> tuple[Path, list[CaseDefinition]]:
    definition = BenchmarkDefinition.model_validate(read_yaml(path))
    cases_path = definition.cases.resolve(path.parent, allow_unsigned=True)
    entries = TypeAdapter(list[CaseDefinition]).validate_python(read_yaml(cases_path))
    if not entries:
        raise ValueError("cases must not be empty")
    unique([entry.case_id for entry in entries], "case IDs")
    execution = definition.execution.resolve(path.parent, allow_unsigned=True)
    LocalExecution.model_validate(read_yaml(execution))
    analysis_path = definition.analysis.resolve(path.parent, allow_unsigned=True)
    analysis = AnalysisSettings.model_validate(read_yaml(analysis_path))
    if analysis.case_settings.keys() != {entry.case_id for entry in entries}:
        raise ValueError("analysis case_settings must match the cases exactly")
    root = cases_path.parent
    for entry in entries:
        agents = AgentManifest.model_validate(read_yaml(entry.agent.resolve(root, allow_unsigned=True)))
        for agent in [agents.baseline, *agents.mutants]:
            for source in agent.inputs.values():
                source.resolve(root, allow_unsigned=True)
        data = read_yaml(entry.tasks.resolve(root, allow_unsigned=True))
        if isinstance(data, dict) and "dataset" in data:
            DatasetManifest.model_validate(data)
        else:
            for task in SourceTasks.model_validate(data).tasks:
                if isinstance(task, TaskSource):
                    task.source().resolve(root, allow_unsigned=True)
        settings = analysis.case_settings[entry.case_id]
        settings.description.resolve(root, allow_unsigned=True)
        settings.subsets.resolve(root, allow_unsigned=True)
    return cases_path, entries


def _source_files(source: Path, benchmark: bool) -> dict:
    if benchmark:
        return file_manifest(source.parent)
    files = {source.name: input_digest(source)}
    data = read_yaml(source)
    if not (isinstance(data, dict) and "dataset" in data):
        for task in SourceTasks.model_validate(data).tasks:
            if isinstance(task, TaskSource):
                path = task.source().resolve(source.parent, allow_unsigned=True)
                files[task.path] = input_digest(path)
    return files


def prepare_inputs(source: Path, output: Path, *, benchmark: bool = False, cached_only: bool = False) -> dict:
    """Publish a prepared bundle atomically; verified retries do not access the network."""
    if version("harbor") != "0.24.0":
        raise ValueError("preparation requires the qualified Harbor 0.24.0 client")
    source = source.resolve(strict=True)
    source_root = source.parent
    output = output.absolute()
    if output.is_symlink() or output.resolve().is_relative_to(source_root):
        raise ValueError("output must be outside the source bundle")
    original = _source_files(source, benchmark)
    request = {"kind": "benchmark" if benchmark else "tasks", "source": source.name, "files": original}
    if output.exists():
        receipt = read_json(resolve_evidence(output, RECEIPT))
        if not isinstance(receipt, dict) or receipt.get("request") != request:
            raise ValueError("prepared request changed; use a new output directory")
        actual = file_manifest(output)
        actual.pop(RECEIPT)
        if actual != receipt.get("files"):
            raise ValueError("prepared contents changed")
        return receipt
    if cached_only:
        raise ValueError("cached-only preparation requires an existing prepared bundle")
    if (source_root / TASKS_DIR).exists() or (source_root / RECEIPT).exists():
        raise ValueError("source bundle uses a reserved preparation path")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=output.parent) as temporary:
        stage = Path(temporary) / "bundle"
        origins = {}
        if benchmark:
            cases_path, entries = _verify_authored_benchmark(source)
            shutil.copytree(source_root, stage, symlinks=True)
            root = stage / cases_path.parent.relative_to(source_root)
            seen = set()
            for entry in entries:
                if entry.tasks.path in seen:
                    continue
                seen.add(entry.tasks.path)
                target = root / entry.tasks.path
                manifest, origin = _prepare_tasks(read_yaml(target), cases_path.parent, root, len(seen) - 1)
                _write_yaml(target, manifest)
                origins[target.relative_to(stage).as_posix()] = origin
            sign_benchmark(stage / source.name, resign=True)
            check_benchmark(stage / source.name)
        else:
            stage.mkdir()
            manifest, origin = _prepare_tasks(read_yaml(source), source_root, stage, 0)
            _write_yaml(stage / "tasks.yml", manifest)
            origins["tasks.yml"] = origin
        current = _source_files(source, benchmark)
        if current != original:
            raise ValueError("source bundle changed during preparation")
        receipt = {
            "artifact_type": "prepared_inputs",
            "schema_version": 1,
            "harbor": version("harbor"),
            "request": request,
            "origins": origins,
            "files": file_manifest(stage),
        }
        write_json(stage / RECEIPT, receipt)
        if output.exists():
            raise ValueError("output appeared during preparation")
        stage.rename(output)
    return receipt
