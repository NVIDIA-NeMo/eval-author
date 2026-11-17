# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find private benchmark content in the agent's prompts, skills, or harness code.

The agent's prompts and skills exist before an evaluation starts, so any task's
expected value, hidden test name, or grader line in them reaches the agent
whatever the task hides. This reads the current contents of every selected file.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from rubric_identity import MAX_SOURCE_BYTES, diagnostic, finish, read_selected, text
from rubric_layout import MAX_LAYOUT_FILES, label_task
from rubric_leakage import bounded, hint_lines, json_values, normalize, read_texts, test_names

Kind = Literal["expected-value", "private-line", "test-name", "task-id"]
KINDS: tuple[Kind, ...] = ("expected-value", "private-line", "test-name", "task-id")
MIN_TASK_ID_CHARS = 6
_FAILING_KINDS = frozenset({"expected-value", "private-line"})


class Finding(TypedDict):
    kind: Kind
    task: str
    private: str | None
    file: str
    line: int


class _Needle(TypedDict):
    kind: Kind
    task: str
    private: str | None
    value: str


def _needles(root: Path, task_path: str) -> tuple[str, list[_Needle], list[dict]]:
    layout = label_task(root, {"task": task_path})
    task = layout["task"]
    # Any layout problem means some private file may be missing from the search.
    diagnostics = [
        {**row, "code": row["code"].replace("layout.", "reuse.layout_", 1), "status": "unknown"}
        for row in layout["diagnostics"]
    ]
    if layout["format"] != "harbor":
        return task, [], diagnostics
    texts, unscanned = read_texts(root, layout)
    private = {row["path"] for row in layout["files"] if row["visibility"] == "private"}
    diagnostics.extend(diagnostic("reuse.private_unscanned", path, "unknown") for path in unscanned if path in private)
    needles: list[_Needle] = []
    name = PurePosixPath(task).name
    if len(name) >= MIN_TASK_ID_CHARS:
        needles.append({"kind": "task-id", "task": task, "private": None, "value": name})
    for row in texts["private"]:
        source = row["path"]
        groups: tuple[tuple[Kind, list[str]], ...] = (
            ("expected-value", json_values(row["content"])),
            ("test-name", test_names(row)),
            ("private-line", hint_lines(row["content"])),
        )
        for kind, values in groups:
            needles.extend({"kind": kind, "task": task, "private": source, "value": value} for value in values)
    return task, needles, diagnostics


def _selected_files(root: Path, paths: list[str], excluded: list[str]) -> tuple[list[str], list[dict]]:
    """Return root-relative files under the selected paths, skipping the selected tasks."""
    files: set[str] = set()
    diagnostics = []
    for selected in paths:
        relative = PurePosixPath(selected)
        if relative.is_absolute() or ".." in relative.parts or "\\" in selected:
            raise ValueError("invalid reuse selection")
        start = root / relative
        if start.is_symlink() or not start.exists():
            diagnostics.append(diagnostic("reuse.path_unavailable", selected, "unknown"))
            continue
        if start.is_file():
            files.add(relative.as_posix())
            continue

        def failed(error: OSError, selected: str = selected) -> None:
            diagnostics.append(diagnostic("reuse.directory_unreadable", selected, "unknown"))

        for current, directories, names in os.walk(start, followlinks=False, onerror=failed):
            directories.sort()
            base = Path(current)
            for name in [*names, *(entry for entry in directories if (base / entry).is_symlink())]:
                files.add((base / name).relative_to(root).as_posix())
    kept = sorted(path for path in files if not any(path == task or path.startswith(task + "/") for task in excluded))
    if len(kept) > MAX_LAYOUT_FILES:
        diagnostics.append(diagnostic("reuse.too_many_files", "paths", "unknown"))
        kept = kept[:MAX_LAYOUT_FILES]
    return kept, diagnostics


def _matches(needle: _Needle, line: str) -> bool:
    if needle["kind"] == "private-line":
        return needle["value"] in normalize(line)
    if needle["kind"] in ("test-name", "task-id"):
        return bounded(line, needle["value"])
    return needle["value"] in line


def check_reuse(root: Path, selected: object) -> dict:
    """Scan the agent's files for private content from the selected benchmark tasks.

    ``selected`` is ``{"tasks": [<task directory>], "paths": [<file or directory>]}``
    with paths relative to the root. ``paths`` names the prompts, skills, and
    harness code the agent uses. Files inside the selected tasks are skipped.
    """
    if not isinstance(selected, dict) or set(selected) != {"tasks", "paths"}:
        raise ValueError("invalid reuse selection")
    tasks, paths = selected["tasks"], selected["paths"]
    if (
        not isinstance(tasks, list)
        or not tasks
        or not all(text(task) for task in tasks)
        or not isinstance(paths, list)
        or not paths
        or not all(text(path) for path in paths)
    ):
        raise ValueError("invalid reuse selection")
    root = Path(root).resolve(strict=True)
    diagnostics: list[dict] = []
    needles: list[_Needle] = []
    task_dirs = []
    for task_path in tasks:
        task, found, problems = _needles(root, task_path)
        if task == ".":
            # The agent's files must sit outside the task, so the root can't be the task.
            raise ValueError("invalid reuse selection")
        task_dirs.append(task)
        needles.extend(found)
        diagnostics.extend(problems)
    files, problems = _selected_files(root, paths, task_dirs)
    diagnostics.extend(problems)
    findings: list[Finding] = []
    scanned = 0
    for path in files:
        data, _ = read_selected(root, path, MAX_SOURCE_BYTES)
        try:
            content = data.decode("utf-8") if data is not None else None
        except UnicodeDecodeError:
            content = None
        if content is None:
            diagnostics.append(diagnostic("reuse.file_unscanned", path, "unknown"))
            continue
        scanned += 1
        seen = set()
        for number, line in enumerate(content.splitlines(), 1):
            for needle in needles:
                key = (needle["kind"], needle["task"], needle["private"], number)
                if key not in seen and _matches(needle, line):
                    seen.add(key)
                    findings.append(
                        {
                            "kind": needle["kind"],
                            "task": needle["task"],
                            "private": needle["private"],
                            "file": path,
                            "line": number,
                        }
                    )
    for finding in findings:
        status = "fail" if finding["kind"] in _FAILING_KINDS else "unknown"
        diagnostics.append(diagnostic(f"reuse.{finding['kind']}", finding["file"], status))
    return {
        "findings": findings,
        "counts": {
            "files": len(files),
            "scanned_files": scanned,
            "private_strings": len(needles),
            **{kind: sum(row["kind"] == kind for row in findings) for kind in KINDS},
        },
        **_status(diagnostics),
    }


def _status(diagnostics: list[dict]) -> dict:
    unique = {(row["code"], row["subject"], row["status"]): row for row in diagnostics}
    summary = finish([], list(unique.values()))
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
