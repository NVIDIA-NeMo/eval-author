# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed models, strict config loaders, and identity helpers for generation benchmarks."""

from __future__ import annotations

import copy
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from metric_benchmark.io import digest_bytes, json_bytes, read_json, write_json
from metric_benchmark.models import (
    Digest,
    Model,
    Score,
    Subset,
    Subsets,
    Task,
    Tasks,
    Text,
    relative_path,
    resolve_evidence,
    unique,
)
from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from generation_benchmark.digests import path_digest

__all__ = [
    "CaseDefinition",
    "CommandDigest",
    "Digest",
    "DigestSpec",
    "DirectoryModel",
    "GenerationAgent",
    "GenerationAgentRef",
    "GenerationAttempt",
    "GenerationConfig",
    "GenerationExecution",
    "GenerationExperiment",
    "GenerationInputs",
    "GenerationLock",
    "GenerationRequest",
    "GenerationResponse",
    "GenerationWorkspacePaths",
    "GlobDigest",
    "ImplementationRecord",
    "InputRef",
    "LoadedCase",
    "LoadedExperiment",
    "LockEntry",
    "MetricCommandReceipt",
    "MetricConfig",
    "MetricScoreConfig",
    "MetricSetupConfig",
    "Model",
    "OutputSpec",
    "PathRef",
    "SchemaModel",
    "Score",
    "ScoreOutput",
    "ScoreRequest",
    "ScoreTrialEntry",
    "SetupRequest",
    "Stage0SetupReceipt",
    "Stage1ExecutionReceipt",
    "Stage1ExecutionRow",
    "Stage1Results",
    "Stage3GenerationReceipt",
    "Subset",
    "Subsets",
    "SubsetsStageReceipt",
    "Task",
    "TaskInventoryManifest",
    "TaskSourceEntry",
    "Tasks",
    "Text",
    "execution_identity_form",
    "extract_settings_refs",
    "field_is_identity",
    "generation_identity_form",
    "identity_dump",
    "load_experiment",
    "metric_identity_form",
    "metric_setup_identity_form",
    "read_yaml",
    "relative_path",
    "resolve_evidence",
    "resolve_settings_paths",
    "unique",
    "write_model",
]

_OUTPUT_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


class StrictYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("YAML mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def read_yaml(path: Path) -> object:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictYamlLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {path.name}: {error}") from error


class SchemaModel(Model):
    """Base model supporting JSON `schema` via field alias without shadowing BaseModel.schema."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", by_alias=True, warnings="error")


def write_model(path: Path, model: SchemaModel | Model) -> None:
    payload = model.to_payload() if isinstance(model, SchemaModel) else model
    write_json(path, payload)


def field_is_identity(model_cls: type[Model], field_name: str) -> bool:
    field_info = model_cls.model_fields[field_name]
    extra = field_info.json_schema_extra
    if isinstance(extra, dict) and "identity" in extra:
        return bool(extra["identity"])
    return True


def identity_dump(model: Model) -> dict[str, Any]:
    """Return a dict containing only identity fields of *model*."""
    result: dict[str, Any] = {}
    for field_name in type(model).model_fields:
        if not field_is_identity(type(model), field_name):
            continue
        value = getattr(model, field_name)
        if isinstance(value, Model):
            result[field_name] = identity_dump(value)
        else:
            result[field_name] = copy.deepcopy(value)
    return result


class DirectoryModel(Model, ABC):
    """Contract model loaded from a command output directory."""

    @classmethod
    @abstractmethod
    def load(cls, directory: Path) -> Self:
        """Load declared artifacts from *directory*; raise on missing/invalid, ignore undeclared."""
        raise NotImplementedError


class ScoreOutput(DirectoryModel):
    """Default output contract for metric.score.command reading score.json."""

    score: Score

    @classmethod
    def load(cls, directory: Path) -> Self:
        try:
            score_path = resolve_evidence(directory, "score.json")
        except FileNotFoundError:
            raise ValueError(f"missing score.json in {directory}") from None
        if not score_path.is_file():
            raise ValueError(f"score.json is not a regular file in {directory}")
        score = Score.model_validate(read_json(score_path))
        return cls(score=score)


class PathRef(Model):
    path: Text


class InputRef(Model):
    path: Text
    kind: Literal["file", "directory"] | None = None

    def resolve_path(self, root: Path) -> Path:
        try:
            resolved = resolve_evidence(root, self.path)
        except FileNotFoundError:
            raise ValueError(f"missing input path: {self.path}") from None
        if self.kind == "directory" and not resolved.is_dir():
            raise ValueError(f"expected directory for {self.path}")
        if self.kind == "file" and not resolved.is_file():
            raise ValueError(f"expected file for {self.path}")
        if not (resolved.is_file() or resolved.is_dir()):
            raise ValueError(f"input must be a regular file or directory: {self.path}")
        return resolved


class GlobDigest(Model):
    method: Literal["glob"]
    patterns: list[Text] = Field(min_length=1)
    excludes: list[Text] = Field(default_factory=list)


class CommandDigest(Model):
    method: Literal["command"]
    command: list[Text] = Field(min_length=1)


DigestSpec = Annotated[GlobDigest | CommandDigest, Field(discriminator="method")]


class OutputSpec(Model):
    python: Text
    type: Text

    @field_validator("type")
    @classmethod
    def validate_module_class(cls, value: str) -> str:
        if not _OUTPUT_TYPE_RE.match(value):
            raise ValueError("output type must have format 'module:Class'")
        return value


class MetricSetupConfig(Model):
    command: list[Text] = Field(min_length=1, json_schema_extra={"identity": True})
    digest: DigestSpec | None = Field(default=None, json_schema_extra={"identity": True})
    timeout_seconds: int = Field(default=3600, ge=1, json_schema_extra={"identity": True})
    output: OutputSpec = Field(..., json_schema_extra={"identity": True})


class MetricScoreConfig(Model):
    command: list[Text] = Field(min_length=1, json_schema_extra={"identity": True})
    digest: DigestSpec | None = Field(default=None, json_schema_extra={"identity": True})
    timeout_seconds: int = Field(default=3600, ge=1, json_schema_extra={"identity": True})
    output: OutputSpec | None = Field(default=None, json_schema_extra={"identity": True})


class MetricConfig(Model):
    setup: MetricSetupConfig | None = Field(default=None, json_schema_extra={"identity": True})
    score: MetricScoreConfig = Field(..., json_schema_extra={"identity": True})


class GenerationConfig(Model):
    command: list[Text] = Field(min_length=1, json_schema_extra={"identity": True})
    digest: DigestSpec | None = Field(default=None, json_schema_extra={"identity": True})
    timeout_seconds: int = Field(default=14400, ge=1, json_schema_extra={"identity": True})
    settings: dict[Text, JsonValue] = Field(default_factory=dict, json_schema_extra={"identity": True})


class GenerationExecution(Model):
    backend: Literal["local_harbor"] = Field(default="local_harbor", json_schema_extra={"identity": True})
    harbor_version: Text = Field(default="0.24.0", json_schema_extra={"identity": True})
    n_concurrent_trials: int = Field(default=1, ge=1, json_schema_extra={"identity": False})
    environment: dict[str, JsonValue] = Field(
        default_factory=lambda: dict[str, JsonValue](type="docker"),
        json_schema_extra={"identity": True},
    )


class GenerationExperiment(Model):
    benchmark_id: Text = Field(..., json_schema_extra={"identity": False})
    revision: int = Field(..., ge=1, json_schema_extra={"identity": False})
    cases: PathRef = Field(..., json_schema_extra={"identity": True})
    execution: GenerationExecution = Field(..., json_schema_extra={"identity": True})
    metric: MetricConfig = Field(..., json_schema_extra={"identity": True})
    generation: GenerationConfig = Field(..., json_schema_extra={"identity": True})


class CaseDefinition(Model):
    case_id: Text = Field(..., json_schema_extra={"identity": True})
    agent: PathRef = Field(..., json_schema_extra={"identity": True})
    tasks: PathRef = Field(..., json_schema_extra={"identity": True})
    subsets: PathRef = Field(..., json_schema_extra={"identity": True})
    ethos: PathRef = Field(..., json_schema_extra={"identity": True})
    agent_source_excludes: list[Text] = Field(default_factory=list, json_schema_extra={"identity": True})
    settings: dict[Text, JsonValue] = Field(default_factory=dict, json_schema_extra={"identity": True})


class GenerationAgent(Model):
    agent_bundle_id: Text = Field(..., json_schema_extra={"identity": True})
    revision: Text = Field(..., json_schema_extra={"identity": True})
    harbor: dict[str, JsonValue] = Field(..., json_schema_extra={"identity": True})
    inputs: dict[Text, InputRef] = Field(default_factory=dict, json_schema_extra={"identity": True})
    digest: DigestSpec | None = Field(default=None, json_schema_extra={"identity": True})


class TaskSourceEntry(Task):
    path: Text


class TaskInventoryManifest(Model):
    tasks: list[TaskSourceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        unique([task.task_id for task in self.tasks], "task IDs")
        return self

    @property
    def task_inventory(self) -> Tasks:
        return Tasks(tasks=[Task(task_id=t.task_id, revision=t.revision, name=t.name) for t in self.tasks])


class LockEntry(Model):
    type: Literal["file", "directory", "block", "module", "digest"]
    path: Text
    selector: Text | None = None
    sha256: Digest

    @model_validator(mode="after")
    def check_selector(self) -> Self:
        if self.type in ("block", "module", "digest") and self.selector is None:
            raise ValueError(f"lock entry of type {self.type} requires selector")
        if self.type in ("file", "directory") and self.selector is not None:
            raise ValueError(f"lock entry of type {self.type} must not have selector")
        return self

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude_none=True, warnings="error")


class GenerationLock(SchemaModel):
    schema_id: Literal["nemo.eval_author.generation_lock.v1"] = Field(
        default="nemo.eval_author.generation_lock.v1",
        alias="schema",
    )
    entries: dict[Text, LockEntry]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema_id,
            "entries": {key: self.entries[key].to_payload() for key in sorted(self.entries)},
        }


class ImplementationRecord(Model):
    sources: dict[Text, Digest]
    harbor: Text
    pydantic: Text
    sha256: Digest


class GenerationInputs(Model):
    run_id: Text
    implementation: ImplementationRecord
    entries: dict[Text, LockEntry]

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "implementation": self.implementation.model_dump(mode="python", warnings="error"),
            "entries": {key: self.entries[key].to_payload() for key in sorted(self.entries)},
        }


class SetupRequest(SchemaModel):
    schema_id: Literal["nemo.eval_author.metric_setup_request.v1"] = Field(
        default="nemo.eval_author.metric_setup_request.v1",
        alias="schema",
    )
    case_id: Text
    ethos_path: Text
    agent_path: Text
    tasks_path: Text
    subsets_path: Text
    settings: dict[Text, JsonValue] = Field(default_factory=dict)


class ScoreTrialEntry(Model):
    task_id: Text
    revision: int = Field(ge=1)
    status: Text
    trial_path: Text


class ScoreRequest(SchemaModel):
    schema_id: Literal["nemo.eval_author.metric_score_request.v1"] = Field(
        default="nemo.eval_author.metric_score_request.v1",
        alias="schema",
    )
    case_id: Text
    scope: Literal["full", "subset", "plus"]
    subset_id: Text | None = None
    metric_setup_dir: Text | None = None
    tasks: list[Task] = Field(min_length=1)
    trials: list[ScoreTrialEntry] = Field(min_length=1)
    settings: dict[Text, JsonValue] = Field(default_factory=dict)


class Stage0SetupReceipt(SchemaModel):
    schema_id: Literal["nemo.eval_author.stage0_setup_receipt.v1"] = Field(
        default="nemo.eval_author.stage0_setup_receipt.v1",
        alias="schema",
    )
    case_id: Text
    command: list[Text] = Field(min_length=1)
    setup_identity_sha256: Digest
    setup_digest_sha256: Digest | None = None
    output_module_sha256: Digest
    case_lock_sha256: dict[Text, Digest]
    request_sha256: Digest
    files: dict[Text, Digest]


class Stage1ExecutionRow(Model):
    agent_run_id: Text
    agent_bundle_id: Text
    task: Task
    status: Text
    rewards: dict[Text, Score] = Field(default_factory=dict)
    native_results_path: Text
    trajectory_path: Text

    @model_validator(mode="after")
    def safe_paths(self) -> Self:
        relative_path(self.native_results_path)
        relative_path(self.trajectory_path)
        return self


class Stage1Results(Model):
    run_id: Text
    case_id: Text
    task_inventory: list[Task] = Field(min_length=1)
    execution_results: list[Stage1ExecutionRow] = Field(min_length=1)


class Stage1ExecutionReceipt(SchemaModel):
    schema_id: Literal["nemo.eval_author.stage1_execution_receipt.v1"] = Field(
        default="nemo.eval_author.stage1_execution_receipt.v1",
        alias="schema",
    )
    case_id: Text
    request_sha256: Digest
    job_receipt_sha256: Digest
    results_sha256: Digest


class MetricCommandReceipt(SchemaModel):
    schema_id: Literal["nemo.eval_author.metric_command_receipt.v1"] = Field(
        default="nemo.eval_author.metric_command_receipt.v1",
        alias="schema",
    )
    case_id: Text
    scope: Literal["full", "subset", "plus"]
    subset_id: Text | None = None
    command: list[Text] = Field(min_length=1)
    metric_digest: Digest | None = None
    metric_block_sha256: Digest
    metric_setup_sha256: Digest | None = None
    settings_sha256: dict[Text, Digest] = Field(default_factory=dict)
    execution_receipt_sha256: Digest
    request_sha256: Digest
    trial_set: list[Text] = Field(min_length=1)
    status: Literal["complete", "unavailable"]
    score: Score | None = None
    unavailable_reason: Text | None = None
    output_files: dict[Text, Digest] = Field(default_factory=dict)


class SubsetsStageReceipt(SchemaModel):
    schema_id: Literal["nemo.eval_author.subsets_stage_receipt.v1"] = Field(
        default="nemo.eval_author.subsets_stage_receipt.v1",
        alias="schema",
    )
    case_id: Text
    subsets_lock_sha256: Digest
    execution_receipt_sha256: Digest
    metric_block_sha256: Digest
    metric_digest: Digest | None = None
    metric_setup_sha256: Digest | None = None
    settings_sha256: dict[Text, Digest] = Field(default_factory=dict)
    subset_receipts: dict[Text, Digest]


def _output_relative_path(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return relative_path(value)
    except ValueError:
        raise ValueError(f"path must be relative to output/ without traversal: {value!r}") from None


class GenerationWorkspacePaths(Model):
    ethos: Text = "ETHOS.md"
    agent: Text = "agent"
    tasks: Text = "tasks"


class GenerationAgentRef(Model):
    agent_bundle_id: Text
    revision: Text
    harbor: dict[str, JsonValue]


class GenerationRequest(SchemaModel):
    """Generation command request for one subset: Bᵢ only, no held-out ids, no B-level scores."""

    schema_id: Literal["nemo.eval_author.generation_request.v1"] = Field(
        default="nemo.eval_author.generation_request.v1",
        alias="schema",
    )
    case_id: Text
    eval_subset_id: Text
    tasks: list[Task] = Field(min_length=1)
    workspace: GenerationWorkspacePaths = Field(default_factory=GenerationWorkspacePaths)
    metric_output_dir: Text
    agent: GenerationAgentRef
    settings: dict[Text, JsonValue] = Field(default_factory=dict)
    max_gaps: int | None = Field(default=None, ge=1)


class GenerationAttempt(Model):
    slug: Text
    tool: Text | None = None
    status: Text
    task_path: Text | None = None
    log_path: Text | None = None
    reason: Text | None = None
    pending_question: Text | None = None

    @field_validator("task_path", "log_path")
    @classmethod
    def safe_paths(cls, value: str | None) -> str | None:
        return _output_relative_path(value)


class GenerationResponse(SchemaModel):
    schema_id: Literal["nemo.eval_author.generation_response.v1"] = Field(
        default="nemo.eval_author.generation_response.v1",
        alias="schema",
    )
    attempts: list[GenerationAttempt] = Field(default_factory=list)
    unattempted: list[Text] = Field(default_factory=list)
    error: Text | None = None
    baseline_report_path: Text | None = None

    @field_validator("baseline_report_path")
    @classmethod
    def safe_report_path(cls, value: str | None) -> str | None:
        return _output_relative_path(value)


class Stage3GenerationReceipt(SchemaModel):
    schema_id: Literal["nemo.eval_author.stage3_generation_receipt.v1"] = Field(
        default="nemo.eval_author.stage3_generation_receipt.v1",
        alias="schema",
    )
    case_id: Text
    eval_subset_id: Text
    status: Literal["complete", "partial", "unavailable", "zero_gap"]
    unavailable_reason: Text | None = None
    command: list[Text] = Field(min_length=1)
    configuration_sha256: Digest
    request_sha256: Digest | None = None
    metric_full_receipt_sha256: Digest
    metric_subset_receipt_sha256: Digest
    case_lock_sha256: dict[Text, Digest]
    returncode: int | None = None
    timed_out: bool = False
    generated_tasks: list[Text] = Field(default_factory=list)
    attempt_summary: dict[Text, JsonValue] = Field(default_factory=dict)
    output_files: dict[Text, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reason_matches_status(self) -> Self:
        if (self.status == "unavailable") != (self.unavailable_reason is not None):
            raise ValueError("unavailable_reason is required exactly when status is unavailable")
        return self


@dataclass(frozen=True)
class LoadedCase:
    index: int
    definition: CaseDefinition
    agent: GenerationAgent
    agent_path: Path
    tasks: TaskInventoryManifest
    tasks_path: Path
    subsets: Subsets
    subsets_path: Path
    ethos_path: Path
    cases_root: Path
    resolved_settings: dict[str, JsonValue]
    case_settings_refs: dict[str, tuple[InputRef, Path]]
    agent_input_paths: dict[str, Path]
    task_paths: dict[str, Path]


@dataclass(frozen=True)
class LoadedExperiment:
    experiment_path: Path
    experiment_dir: Path
    experiment: GenerationExperiment
    cases_path: Path
    cases_dir: Path
    cases: dict[str, LoadedCase]
    generation_settings_refs: dict[str, tuple[InputRef, Path]]


def _contains_key(obj: object, target_key: str) -> bool:
    if isinstance(obj, dict):
        if target_key in obj:
            return True
        return any(_contains_key(v, target_key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(item, target_key) for item in obj)
    return False


def extract_settings_refs(settings: dict[str, Any], prefix: str = "") -> list[tuple[str, InputRef]]:
    """Return `(dotted_key, InputRef)` for every mapping containing a `path` key under *settings*."""
    refs: list[tuple[str, InputRef]] = []
    for key, value in settings.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        refs.extend(_walk_setting_value(value, dotted))
    return refs


def _walk_setting_value(value: Any, prefix: str) -> list[tuple[str, InputRef]]:
    if isinstance(value, dict):
        if "path" in value:
            if "sha256" in value:
                raise ValueError(f"settings.{prefix}: sha256 is not allowed; digests live only in the lock file")
            return [(prefix, InputRef.model_validate(value))]
        out: list[tuple[str, InputRef]] = []
        for k, v in value.items():
            out.extend(_walk_setting_value(v, f"{prefix}.{k}"))
        return out
    if isinstance(value, list):
        out = []
        for idx, item in enumerate(value):
            out.extend(_walk_setting_value(item, f"{prefix}[{idx}]"))
        return out
    return []


def resolve_settings_paths(settings: dict[str, Any], root: Path) -> dict[str, JsonValue]:
    """Return a deep copy of *settings* with every `{"path": ...}` resolved to an absolute path."""

    def transform(value: Any) -> Any:
        if isinstance(value, dict):
            if "path" in value:
                ref = InputRef.model_validate(value)
                resolved = str(ref.resolve_path(root).resolve())
                out = {"path": resolved}
                if ref.kind is not None:
                    out["kind"] = ref.kind
                return out
            return {k: transform(v) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(item) for item in value]
        return value

    return {k: transform(v) for k, v in settings.items()}


def _settings_identity_value(settings: dict[str, Any], root: Path) -> dict[str, Any]:
    def transform(value: Any) -> Any:
        if isinstance(value, dict):
            if "path" in value:
                ref = InputRef.model_validate(value)
                resolved = ref.resolve_path(root)
                return {
                    "type": "directory" if resolved.is_dir() else "file",
                    "sha256": path_digest(resolved),
                }
            return {k: transform(v) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(item) for item in value]
        return value

    return {k: transform(v) for k, v in settings.items()}


def execution_identity_form(execution: GenerationExecution) -> dict[str, Any]:
    return identity_dump(execution)


def metric_setup_identity_form(
    setup: MetricSetupConfig,
    setup_digest_sha256: str | None,
    output_module_sha256: str,
) -> dict[str, Any]:
    data = identity_dump(setup)
    data["digest"] = setup_digest_sha256
    data["output"] = {
        "python": setup.output.python,
        "type": setup.output.type,
        "sha256": output_module_sha256,
    }
    return data


def metric_identity_form(
    metric: MetricConfig,
    *,
    setup_digest_sha256: str | None,
    setup_output_module_sha256: str | None,
    score_digest_sha256: str | None,
    score_output_module_sha256: str | None,
) -> dict[str, Any]:
    score_data = identity_dump(metric.score)
    score_data["digest"] = score_digest_sha256
    if metric.score.output is not None:
        score_data["output"] = {
            "python": metric.score.output.python,
            "type": metric.score.output.type,
            "sha256": score_output_module_sha256,
        }
    setup_data: dict[str, Any] | None = None
    if metric.setup is not None:
        assert setup_output_module_sha256 is not None
        setup_data = metric_setup_identity_form(metric.setup, setup_digest_sha256, setup_output_module_sha256)
    return {"setup": setup_data, "score": score_data}


def generation_identity_form(
    generation: GenerationConfig,
    *,
    generation_digest_sha256: str | None,
    experiment_dir: Path,
) -> dict[str, Any]:
    data = identity_dump(generation)
    data["digest"] = generation_digest_sha256
    data["settings"] = _settings_identity_value(generation.settings, experiment_dir)
    return data


def load_experiment(path: Path) -> LoadedExperiment:
    """Strictly load and validate a generation experiment, its cases, manifests, and paths."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing experiment file: {path}")
    raw_text = path.read_text(encoding="utf-8")
    if "${" in raw_text:
        raise ValueError(f"{path}: '${{' template syntax is not allowed in generation-experiment.yml")
    experiment_dir = path.resolve().parent
    raw_exp = read_yaml(path)
    experiment = GenerationExperiment.model_validate(raw_exp)

    try:
        cases_path = resolve_evidence(experiment_dir, experiment.cases.path)
    except FileNotFoundError:
        raise ValueError(f"missing cases file: {experiment.cases.path}") from None
    if not cases_path.is_file():
        raise ValueError(f"cases reference must be a file: {experiment.cases.path}")

    raw_cases = read_yaml(cases_path)
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases.yml must be a nonempty list")
    case_defs: list[CaseDefinition] = []
    for idx, raw_item in enumerate(raw_cases):
        label = (
            raw_item.get("case_id")
            if isinstance(raw_item, dict) and isinstance(raw_item.get("case_id"), str)
            else f"index {idx}"
        )
        try:
            case_defs.append(CaseDefinition.model_validate(raw_item))
        except ValueError as err:
            raise ValueError(f"case {label}: {err}") from err
    counts = Counter(c.case_id for c in case_defs)
    dups = sorted(cid for cid, cnt in counts.items() if cnt > 1)
    if dups:
        raise ValueError(f"duplicate case IDs: {', '.join(dups)}")

    cases_dir = cases_path.parent
    cases: dict[str, LoadedCase] = {}
    for index, entry in enumerate(case_defs):
        case_id = entry.case_id
        try:
            agent_path = _resolve_case_file(cases_dir, entry.agent.path, "agent")
            raw_agent = read_yaml(agent_path)
            if _contains_key(raw_agent, "sha256"):
                raise ValueError(f"{agent_path.name}: sha256 field is not allowed; digests live only in the lock file")
            agent = GenerationAgent.model_validate(raw_agent)

            tasks_path = _resolve_case_file(cases_dir, entry.tasks.path, "tasks")
            raw_tasks = read_yaml(tasks_path)
            if _contains_key(raw_tasks, "sha256"):
                raise ValueError(f"{tasks_path.name}: sha256 field is not allowed; digests live only in the lock file")
            tasks = TaskInventoryManifest.model_validate(raw_tasks)

            subsets_path = _resolve_case_file(cases_dir, entry.subsets.path, "subsets")
            subsets = Subsets.model_validate(read_json(subsets_path))
            subsets.resolve_tasks(tasks.task_inventory)

            ethos_path = _resolve_case_file(cases_dir, entry.ethos.path, "ethos")

            agent_input_paths: dict[str, Path] = {}
            for input_name, input_ref in agent.inputs.items():
                try:
                    agent_input_paths[input_name] = input_ref.resolve_path(cases_dir)
                except ValueError as err:
                    raise ValueError(f"agent.inputs.{input_name}: {err}") from err

            task_paths: dict[str, Path] = {}
            for task_entry in tasks.tasks:
                try:
                    t_path = resolve_evidence(cases_dir, task_entry.path)
                except FileNotFoundError:
                    raise ValueError(f"task {task_entry.task_id}: missing path {task_entry.path}") from None
                if not t_path.is_dir():
                    raise ValueError(f"task {task_entry.task_id}: path must be a directory: {task_entry.path}")
                task_paths[task_entry.task_id] = t_path

            case_settings_refs: dict[str, tuple[InputRef, Path]] = {}
            for dotted, ref in extract_settings_refs(entry.settings):
                case_settings_refs[dotted] = (ref, ref.resolve_path(cases_dir))
            resolved_settings = resolve_settings_paths(entry.settings, cases_dir)
        except (OSError, ValueError) as error:
            raise ValueError(f"case {case_id}: {error}") from error

        cases[case_id] = LoadedCase(
            index=index,
            definition=entry,
            agent=agent,
            agent_path=agent_path,
            tasks=tasks,
            tasks_path=tasks_path,
            subsets=subsets,
            subsets_path=subsets_path,
            ethos_path=ethos_path,
            cases_root=cases_dir,
            resolved_settings=resolved_settings,
            case_settings_refs=case_settings_refs,
            agent_input_paths=agent_input_paths,
            task_paths=task_paths,
        )

    generation_settings_refs: dict[str, tuple[InputRef, Path]] = {}
    for dotted, ref in extract_settings_refs(experiment.generation.settings):
        generation_settings_refs[dotted] = (ref, ref.resolve_path(experiment_dir))

    return LoadedExperiment(
        experiment_path=path.resolve(),
        experiment_dir=experiment_dir,
        experiment=experiment,
        cases_path=cases_path,
        cases_dir=cases_dir,
        cases=cases,
        generation_settings_refs=generation_settings_refs,
    )


def _resolve_case_file(root: Path, rel: str, label: str) -> Path:
    try:
        resolved = resolve_evidence(root, rel)
    except FileNotFoundError:
        raise ValueError(f"missing {label} file: {rel}") from None
    if not resolved.is_file():
        raise ValueError(f"{label} reference must be a file: {rel}")
    return resolved


def block_sha256(identity_form: dict[str, Any]) -> str:
    return digest_bytes(json_bytes(identity_form))
