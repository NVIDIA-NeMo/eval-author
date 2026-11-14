# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download remote Harbor tasks into validated local benchmark bundles."""

import asyncio
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml
from harbor.constants import DEFAULT_REGISTRY_URL
from harbor.models.registry import DatasetSpec
from harbor.models.task.id import GitTaskId
from harbor.models.task.task import Task as HarborTask
from harbor.tasks.client import TaskClient, TaskIdType
from pydantic import Field, JsonValue, StringConstraints, TypeAdapter, model_validator

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
from metric_benchmark.models import Digest, Model, Subsets, Task, Tasks, Text, relative_path, resolve_evidence, unique
from metric_benchmark.signing import check_benchmark, sign_benchmark

Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
RECEIPT = "preparation.json"
TASKS_DIR = "prepared-tasks"
MAX_REGISTRY_BYTES = 32 * 1024 * 1024


def _url(value: str, *, git: bool = False) -> str:
    parsed = urlsplit(value)
    schemes = ("https", "http", "file") if git else ("https", "http")
    if parsed.scheme not in schemes or parsed.username or parsed.password:
        message = "use an HTTP(S) URL without embedded credentials"
        if git:
            message += " (or file:// for Git fixtures)"
        raise ValueError(message)
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


class DownloadRequest(Model):
    kind: Literal["benchmark", "tasks"]
    source: Text
    files: dict[Text, Digest]


class DownloadedSource(Model):
    task_id: Text
    source: GitSource


class TaskOrigin(Model):
    sources: list[DownloadedSource]
    tasks_sha256: Digest
    dataset: DatasetSource | None = None
    registry_entry: dict[str, JsonValue] | None = None
    registry_sha256: Digest | None = None


class PreparationReceipt(Model):
    artifact_type: Literal["prepared_inputs"]
    schema_version: Literal[1]
    harbor: Text
    request: DownloadRequest
    origins: dict[Text, TaskOrigin]
    files: dict[Text, Digest]


@dataclass
class _DownloadSources:
    registries: dict[str, tuple[list[DatasetSpec], str]] = field(default_factory=dict)
    commits: dict[tuple[str, str], str] = field(default_factory=dict)


def _task_document(data: object) -> DatasetManifest | SourceTasks:
    if isinstance(data, dict) and "dataset" in data:
        return DatasetManifest.model_validate(data)
    return SourceTasks.model_validate(data)


def _registry_rows(path: Path) -> list[DatasetSpec]:
    rows = TypeAdapter(list[DatasetSpec]).validate_python(read_json(path))
    unique([(row.name, row.version) for row in rows], "registry dataset versions")
    return rows


def download_registry(url: str, output: Path) -> list[DatasetSpec]:
    """Download and validate a registry snapshot without downloading its tasks."""
    rows, _ = _download_registry(url, output)
    return rows


def _download_registry(url: str, output: Path) -> tuple[list[DatasetSpec], str]:
    _url(url)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        snapshot = Path(temporary) / "registry.json"
        with urlopen(url, timeout=60) as response:
            content = response.read(MAX_REGISTRY_BYTES + 1)
        if len(content) > MAX_REGISTRY_BYTES:
            raise ValueError(f"registry response exceeds {MAX_REGISTRY_BYTES} bytes")
        snapshot.write_bytes(content)
        rows = _registry_rows(snapshot)
        write_json(output, [row.model_dump(mode="json") for row in rows])
    return rows, digest_bytes(content)


def _error_detail(error: Exception) -> str:
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return f"{error}: {stderr.strip()}" if stderr and stderr.strip() else str(error)


def _resolve_git(url: str, ref: str | None) -> str:
    _url(url, git=True)
    if ref and re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    ref = ref or "HEAD"
    if ref.startswith("-"):
        raise ValueError("invalid Git revision")
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, ref, ref + "^{}"], capture_output=True, text=True, check=True, timeout=60
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Git source resolution failed: {_error_detail(error)}") from error
    refs = {name: sha for sha, name in (line.split() for line in result.stdout.splitlines())}
    commits = {refs.get(name + "^{}", sha) for name, sha in refs.items() if not name.endswith("^{}")}
    if len(commits) != 1:
        raise ValueError(f"Git revision must resolve to exactly one commit: {ref}")
    commit = commits.pop()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("expected a full Git commit ID")
    return commit


def _dataset_tasks(source: DatasetSource, snapshot: Path, downloads: _DownloadSources) -> tuple[SourceTasks, dict]:
    url = source.registry.url
    if url not in downloads.registries:
        downloads.registries[url] = _download_registry(url, snapshot)
    rows, registry_sha256 = downloads.registries[url]
    matches = [row for row in rows if row.name == source.name and row.version == source.version]
    if len(matches) != 1:
        raise ValueError(f"dataset not found: {source.name}@{source.version}")
    spec = matches[0]
    tasks = []
    for task in spec.tasks:
        if not task.git_url:
            raise ValueError("remote registry tasks must have Git sources")
        key = task.git_url, task.git_commit_id or "HEAD"
        if key not in downloads.commits:
            downloads.commits[key] = _resolve_git(*key)
        tasks.append(
            RemoteTask(
                task_id=task.name,
                revision=1,
                source=GitSource(git_url=task.git_url, git_commit_id=downloads.commits[key], path=task.path.as_posix()),
            )
        )
    return SourceTasks(tasks=tasks), {
        "dataset": source.model_dump(),
        "registry_entry": spec.model_dump(mode="json"),
        "registry_sha256": registry_sha256,
    }


def _prepare_tasks(
    data: object,
    source_root: Path,
    root: Path,
    ordinal: int,
    downloads: _DownloadSources,
    *,
    subsets: Sequence[Subsets] = (),
) -> tuple[dict, dict]:
    directory = root / TASKS_DIR / f"set-{ordinal:04d}"
    directory.mkdir(parents=True)
    origin = {}
    document = _task_document(data)
    if isinstance(document, DatasetManifest):
        tasks, origin = _dataset_tasks(document.dataset, directory / "registry.json", downloads)
    else:
        tasks = document
    if subsets:
        inventory = Tasks(tasks=[*tasks.tasks])
        for selection in subsets:
            selection.resolve_tasks(inventory)
    remote = [task for task in tasks.tasks if isinstance(task, RemoteTask)]
    downloaded = {}
    if remote:
        ids: list[TaskIdType] = [task.source.task_id() for task in remote]
        # Duplicate sources would otherwise share a path and violate runner bindings.
        unique(ids, "remote task sources")
        try:
            result = asyncio.run(TaskClient().download_tasks(ids, output_dir=directory / "data"))
        except Exception as error:
            raise ValueError(f"Harbor task download failed: {_error_detail(error)}") from error
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
            source_digest = input_digest(source_path)
            relative = task.path
            path = root / relative
            # Benchmark preparation already copied the bundle; standalone tasks may still need copying.
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_path, path, symlinks=True)
            task.source().resolve(root, allow_unsigned=True)
            if input_digest(path) != source_digest:
                raise ValueError(f"copied task contents differ from source: {task.path}")
        HarborTask(path)  # Construction validates the native task configuration and input files.
        if not HarborTask.is_valid_dir(path):
            raise ValueError(f"invalid Harbor task directory: {relative}")
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


def _verify_authored_benchmark(path: Path) -> tuple[Path, list[CaseDefinition], dict[Path, list[Subsets]]]:
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
    subsets: dict[Path, list[Subsets]] = {}
    for entry in entries:
        agents = AgentManifest.model_validate(read_yaml(entry.agent.resolve(root, allow_unsigned=True)))
        for agent in [agents.baseline, *agents.mutants]:
            for source in agent.inputs.values():
                source.resolve(root, allow_unsigned=True)
        task_path = entry.tasks.resolve(root, allow_unsigned=True)
        document = _task_document(read_yaml(task_path))
        if isinstance(document, SourceTasks):
            for task in document.tasks:
                if isinstance(task, TaskSource):
                    task.source().resolve(root, allow_unsigned=True)
        settings = analysis.case_settings[entry.case_id]
        settings.description.resolve(root, allow_unsigned=True)
        subset_path = settings.subsets.resolve(root, allow_unsigned=True)
        subsets.setdefault(task_path, []).append(Subsets.model_validate(read_json(subset_path)))
    return cases_path, entries, subsets


def _source_files(source: Path, benchmark: bool) -> dict:
    if benchmark:
        return file_manifest(source.parent)
    files = {source.name: input_digest(source)}
    document = _task_document(read_yaml(source))
    if isinstance(document, SourceTasks):
        for task in document.tasks:
            if isinstance(task, TaskSource):
                path = task.source().resolve(source.parent, allow_unsigned=True)
                files[task.path] = input_digest(path)
    return files


def download_harbor_tasks(source: Path, output: Path, *, benchmark: bool = False, cached_only: bool = False) -> dict:
    """Publish a prepared bundle atomically; verified retries do not access the network."""
    harbor_version = version("harbor")
    expected_version = LocalExecution().harbor_version
    if harbor_version != expected_version:
        raise ValueError(f"preparation requires the qualified Harbor {expected_version} client")
    source = source.resolve(strict=True)
    source_root = source.parent
    output = output.absolute()
    if output.is_symlink() or output.resolve().is_relative_to(source_root):
        raise ValueError("output must be outside the source bundle")
    original = _source_files(source, benchmark)
    request = DownloadRequest(kind="benchmark" if benchmark else "tasks", source=source.name, files=original)
    if output.exists():
        if not output.is_dir() or not (output / RECEIPT).is_file():
            raise ValueError("output exists and is not a prepared bundle; choose a new directory")
        receipt = PreparationReceipt.model_validate(read_json(resolve_evidence(output, RECEIPT)))
        if receipt.request != request:
            raise ValueError("prepared request changed; use a new output directory")
        actual = file_manifest(output)
        actual.pop(RECEIPT)
        if actual != receipt.files:
            raise ValueError("prepared contents changed")
        return receipt.model_dump(mode="json", exclude_none=True)
    if cached_only:
        raise ValueError("cached-only preparation requires an existing prepared bundle")
    authored = _verify_authored_benchmark(source) if benchmark else None
    tasks_root = authored[0].parent if authored is not None else source_root
    for reserved in (tasks_root / TASKS_DIR, source_root / RECEIPT):
        if reserved.exists():
            raise ValueError(f"source bundle uses a reserved preparation path: {reserved.relative_to(source_root)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=output.parent) as temporary:
        stage = Path(temporary) / "bundle"
        origins = {}
        downloads = _DownloadSources()
        if authored is not None:
            cases_path, entries, subsets = authored
            shutil.copytree(source_root, stage, symlinks=True)
            if file_manifest(stage) != original:
                raise ValueError("copied benchmark contents differ from source")
            root = stage / cases_path.parent.relative_to(source_root)
            seen = set()
            for entry in entries:
                target = root / entry.tasks.path
                if target in seen:
                    continue
                seen.add(target)
                manifest, origin = _prepare_tasks(
                    read_yaml(target),
                    cases_path.parent,
                    root,
                    len(seen) - 1,
                    downloads,
                    subsets=subsets[cases_path.parent / entry.tasks.path],
                )
                _write_yaml(target, manifest)
                origins[target.relative_to(stage).as_posix()] = TaskOrigin.model_validate(origin)
            sign_benchmark(stage / source.name, resign=True)
            check_benchmark(stage / source.name)
        else:
            stage.mkdir()
            manifest, origin = _prepare_tasks(read_yaml(source), source_root, stage, 0, downloads)
            _write_yaml(stage / "tasks.yml", manifest)
            origins["tasks.yml"] = TaskOrigin.model_validate(origin)
        current = _source_files(source, benchmark)
        if current != original:
            raise ValueError("source bundle changed during preparation")
        receipt = PreparationReceipt(
            artifact_type="prepared_inputs",
            schema_version=1,
            harbor=harbor_version,
            request=request,
            origins=origins,
            files=file_manifest(stage),
        )
        payload = receipt.model_dump(mode="json", exclude_none=True)
        write_json(stage / RECEIPT, payload)
        if output.exists():
            raise ValueError("output appeared during preparation")
        stage.rename(output)
    return payload
