# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harbor suite discovery for generation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from generation_benchmark.eval_author.ports.errors import ExperimentError


@dataclass(frozen=True)
class SuiteTask:
    name: str
    path: Path
    aliases: frozenset[str]


def discover_suite(suite: Path) -> list[SuiteTask]:
    if not suite.is_dir():
        raise ExperimentError(f"suite does not exist: {suite}")
    tasks: list[SuiteTask] = []
    by_name: dict[str, Path] = {}
    for task_toml in sorted(suite.rglob("task.toml")):
        try:
            payload = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ExperimentError(f"cannot read Harbor task {task_toml}: {exc}") from exc
        name = str((payload.get("task") or {}).get("name", "")).strip()
        if not name:
            raise ExperimentError(f"Harbor task has no [task].name: {task_toml}")
        if name in by_name:
            raise ExperimentError(
                f"suite has duplicate task name {name!r}: {by_name[name]} and {task_toml.parent}"
            )
        by_name[name] = task_toml.parent
        tasks.append(
            SuiteTask(
                name=name,
                path=task_toml.parent,
                aliases=frozenset({name, name.rsplit("/", 1)[-1], task_toml.parent.name}),
            )
        )
    if not tasks:
        raise ExperimentError(f"suite contains no task.toml files: {suite}")
    return tasks
