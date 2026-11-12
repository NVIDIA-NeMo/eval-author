# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


class InfrastructureError(RuntimeError):
    pass


class IncompatibleBackend(ValueError):
    pass


class CandidateGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class Seed:
    id: str
    split: str
    native: dict[str, Any]
    actions: list[dict[str, Any]]


def sequence_of(seed: Seed) -> list[str]:
    if not isinstance(seed, Seed):
        raise TypeError("Prepare normalized seeds before sampling")
    return [action["name"] for action in seed.actions]


@dataclass
class ValidationResult:
    success: bool
    db_ok: bool
    gt_agent_success: bool
    error: str | None = None
    failed_step: str | None = None
    gt_simulation: Any = None
    unsupported: tuple[str, ...] = ()
    evidence_path: str | None = None
    repair_scope: Literal["fixture", "candidate_and_fixture"] = "candidate_and_fixture"
    native_task: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    fixture: dict[str, Any] | None = None


@dataclass
class NativeTask:
    row: dict[str, Any]

    @property
    def id(self) -> str:
        return self.row["id"]

    @id.setter
    def id(self, value: str) -> None:
        self.row["id"] = value

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.row)


class TaskService(Protocol):
    def validate(self, candidate: dict, fixture: dict, *, hints: str, settings: dict) -> ValidationResult: ...

    def export(self, rows: list[dict[str, Any]], path: str | Path) -> Path: ...
