# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Label a selected Harbor task's files public or private from its directory layout.

Harbor gives the agent ``instruction.md`` and the container built from
``environment/``; it copies ``tests/`` in after the agent finishes and
``solution/`` only for the reference run. The labels describe that layout, not a
tested runtime boundary.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from rubric_identity import MAX_SOURCE_BYTES, diagnostic, finish, read_selected, text

MAX_LAYOUT_FILES = 2000
Visibility = Literal["public", "private", "configuration", "unlabeled", "unresolved"]
VISIBILITIES: tuple[Visibility, ...] = ("public", "private", "configuration", "unlabeled", "unresolved")
# A compose or Dockerfile line that names the private directories may put them in the agent's container.
_PRIVATE_REFERENCE = re.compile(rb"(?:^|[\s\"'=:/.])(?:tests|solution)(?:/|[\s\"':]|$)", re.MULTILINE)
_BUILD_FILES = re.compile(r"(?:^|/)(?:Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|compose[^/]*\.ya?ml)$")


class LabeledFile(TypedDict):
    path: str
    visibility: Visibility
    digest: str | None


def visibility(relative: PurePosixPath) -> Visibility:
    """Return the Harbor layout label for a path relative to the task directory."""
    top = relative.parts[0]
    if relative.as_posix() == "instruction.md" or top == "environment":
        return "public"
    if top in ("tests", "solution"):
        return "private"
    if relative.as_posix() == "task.toml":
        return "configuration"
    return "unlabeled"


def _task_directory(root: Path, task: object) -> Path:
    if not text(task) or not isinstance(task, str):
        raise ValueError("invalid layout selection")
    relative = PurePosixPath(task)
    if relative.is_absolute() or ".." in relative.parts or "\\" in task:
        raise ValueError("invalid layout selection")
    directory = root
    for part in relative.parts:
        directory = directory / part
        if directory.is_symlink():
            raise ValueError("invalid layout selection")
    if not directory.is_dir():
        raise ValueError("invalid layout selection")
    return directory


def label_task(root: Path, selected: object) -> dict:
    """Label every file under one selected Harbor task directory.

    ``selected`` is ``{"task": <directory relative to root>}``. Symlinks are
    never followed and stay unresolved. A build file under ``environment/`` that
    names ``tests`` or ``solution`` is a candidate exception to the layout rule,
    not a confirmed exposure.
    """
    if not isinstance(selected, dict) or set(selected) != {"task"}:
        raise ValueError("invalid layout selection")
    root = Path(root).resolve(strict=True)
    directory = _task_directory(root, selected["task"])
    task = directory.relative_to(root).as_posix()
    result: dict = {"task": task, "format": None, "files": [], "counts": dict.fromkeys(VISIBILITIES, 0)}
    if not ((directory / "task.toml").is_file() and (directory / "instruction.md").is_file()):
        return {**result, **_status([diagnostic("layout.not_harbor", task, "unknown")])}
    result["format"] = "harbor"
    diagnostics = []
    files: list[LabeledFile] = []
    for path in _entries(directory):
        relative = PurePosixPath(path.relative_to(directory).as_posix())
        subject = f"{task}/{relative}"
        if len(files) == MAX_LAYOUT_FILES:
            diagnostics.append(diagnostic("layout.too_many_files", task, "unknown"))
            break
        if path.is_symlink():
            files.append({"path": subject, "visibility": "unresolved", "digest": None})
            diagnostics.append(diagnostic("layout.symlink", subject, "unknown"))
            continue
        label = visibility(relative)
        data, reason = read_selected(root, subject, MAX_SOURCE_BYTES)
        if data is None:
            diagnostics.append(diagnostic("layout." + str(reason), subject, "unknown"))
        elif label == "public" and _BUILD_FILES.search(relative.as_posix()) and _PRIVATE_REFERENCE.search(data):
            diagnostics.append(diagnostic("layout.private_path_in_environment", subject, "unknown"))
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        files.append({"path": subject, "visibility": label, "digest": digest})
    result["files"] = files
    for record in files:
        result["counts"][record["visibility"]] += 1
    return {**result, **_status(diagnostics)}


def _entries(directory: Path) -> list[Path]:
    """Return regular files and symlinks under ``directory`` in a stable order, without following links."""
    entries = []
    for current, directories, names in os.walk(directory, followlinks=False):
        base = Path(current)
        directories.sort()
        entries.extend(base / name for name in names)
        entries.extend(base / name for name in directories if (base / name).is_symlink())
    return sorted(entries)


def _status(diagnostics: list[dict]) -> dict:
    summary = finish([], diagnostics)
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
