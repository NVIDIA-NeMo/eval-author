# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validated benchmark inputs and execution-result records."""

from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def unique(values: list, label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")


class Score(Model):
    value: float | None
    reason: Text | None = None

    @model_validator(mode="after")
    def available_or_reason(self) -> Self:
        if (self.value is None) != (self.reason is not None):
            raise ValueError("a null score requires a reason; a numeric score must not have one")
        return self


class Task(Model):
    task_id: Text
    revision: int = Field(ge=1)
    name: Text | None = None

    def key(self) -> tuple[str, int]:
        return self.task_id, self.revision


class Tasks(Model):
    tasks: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_membership(self) -> Self:
        unique([task.task_id for task in self.tasks], "task IDs")
        return self


class Evaluation(Model):
    evaluation_id: Text
    execution: int = Field(ge=1)
    task: Task


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == "." or any(c in value for c in (":", "\\", "\0")):
        raise ValueError("evidence path must be relative to the record root without traversal")
    return value


def resolve_evidence(root: Path, value: str) -> Path:
    """Resolve an existing evidence path without allowing escape through symlinks."""
    relative_path(value)
    base = root.resolve(strict=True)
    path = base / value
    current = base
    for part in Path(value).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("evidence paths must not contain symlinks")
    path.resolve(strict=True).relative_to(base)
    return path


class Execution(Model):
    agent_run_id: Text
    agent_bundle_id: Text
    framework_profile_id: Text | None = None
    evaluation: Evaluation
    status: Text
    outcome_category: Text | None = None
    metrics: dict[Text, Score] = Field(default_factory=dict)
    native_results_path: Text
    native_results_format: Literal["tar.gz", "directory"]
    unavailable_evidence: dict[Text, Text] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        relative_path(self.native_results_path)
        return self


class RawResults(Model):
    run_id: Text
    task_inventory: Tasks
    execution_results: list[Execution] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_executions(self) -> Self:
        unique(
            [(r.evaluation.evaluation_id, r.evaluation.execution) for r in self.execution_results],
            "execution identities",
        )
        allowed = {task.key() for task in self.task_inventory.tasks}
        runs: dict[str, tuple] = {}
        evaluations: dict[str, tuple] = {}
        for row in self.execution_results:
            if row.evaluation.task.key() not in allowed:
                raise ValueError("execution references an unknown task revision")
            identity = row.agent_bundle_id, row.framework_profile_id
            if runs.setdefault(row.agent_run_id, identity) != identity:
                raise ValueError("one run must identify one agent and profile")
            identity = row.agent_run_id, row.evaluation.task.key()
            if evaluations.setdefault(row.evaluation.evaluation_id, identity) != identity:
                raise ValueError("evaluation retries must identify the same run and task")
        return self


class Subset(Model):
    eval_subset_id: Text
    task_ids: list[Text] = Field(min_length=1)


class Subsets(Model):
    artifact_type: Literal["eval_subset_definitions"] = "eval_subset_definitions"
    sampling_parameters: dict[str, int | str]
    eval_subsets: list[Subset] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_memberships(self) -> Self:
        unique([s.eval_subset_id for s in self.eval_subsets], "subset IDs")
        for subset in self.eval_subsets:
            unique(subset.task_ids, "subset tasks")
        return self

    def resolve_tasks(self, inventory: Tasks) -> dict[str, list[Task]]:
        tasks = {task.task_id: task for task in inventory.tasks}
        resolved = {}
        for subset in self.eval_subsets:
            unknown = set(subset.task_ids) - tasks.keys()
            if unknown:
                raise ValueError(f"subset {subset.eval_subset_id} contains unknown task IDs: {sorted(unknown)}")
            resolved[subset.eval_subset_id] = [tasks[task_id] for task_id in subset.task_ids]
        return resolved
