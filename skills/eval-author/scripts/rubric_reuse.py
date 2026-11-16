# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find benchmark-specific private content added to prompts, skills, or harness code.

Someone reviews benchmark results and edits a skill. If the edit adds a task's
expected value, a hidden test name, or a line of its grader, the answer now reaches
the agent through the skill instead of through the task. This reads the added
lines of a git diff, not the author's description of the change.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from rubric_identity import diagnostic, finish, text
from rubric_layout import label_task
from rubric_leakage import bounded, hint_lines, json_values, normalize, read_texts, test_names

Kind = Literal["expected-value", "private-line", "test-name", "task-id"]
MIN_TASK_ID_CHARS = 6
_FAILING_KINDS = frozenset({"expected-value", "private-line"})
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_GIT_TIMEOUT_SEC = 60


class Finding(TypedDict):
    kind: Kind
    task: str
    private: str | None
    changed: str
    line: int


class _Needle(TypedDict):
    kind: Kind
    task: str
    private: str | None
    value: str


def _needles(root: Path, task_path: str) -> tuple[str, list[_Needle], list[dict]]:
    layout = label_task(root, {"task": task_path})
    task = layout["task"]
    if layout["format"] != "harbor":
        return task, [], [diagnostic("reuse.task_not_harbor", task, "unknown")]
    texts, unscanned = read_texts(root, layout)
    diagnostics = [
        diagnostic("reuse.private_unscanned", path, "unknown")
        for path in unscanned
        if "/tests/" in path or "/solution/" in path
    ]
    needles: list[_Needle] = []
    name = PurePosixPath(task).name
    if len(name) >= MIN_TASK_ID_CHARS:
        needles.append({"kind": "task-id", "task": task, "private": None, "value": name})
    for private in texts["private"]:
        source = private["path"]
        needles.extend(
            {"kind": "expected-value", "task": task, "private": source, "value": value}
            for value in json_values(private["content"])
        )
        needles.extend(
            {"kind": "test-name", "task": task, "private": source, "value": value} for value in test_names(private)
        )
        needles.extend(
            {"kind": "private-line", "task": task, "private": source, "value": value}
            for value in hint_lines(private["content"])
        )
    return task, needles, diagnostics


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(root), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=_GIT_TIMEOUT_SEC,
        check=False,
    )


def added_lines(root: Path, base: str, head: str | None, paths: list[str]) -> list[tuple[str, int, str]]:
    """Return ``(file, new line number, text)`` for every line the diff adds."""
    revisions = [base] if head is None else [base, head]
    completed = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--no-renames",
        "--unified=0",
        "--relative",
        *revisions,
        "--",
        *paths,
    )
    if completed.returncode != 0:
        raise ValueError("diff failed")
    rows: list[tuple[str, int, str]] = []
    current: str | None = None
    line = 0
    for raw in completed.stdout.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            current = target[2:] if target.startswith("b/") else None
        elif (match := _HUNK.match(raw)) is not None:
            line = int(match["start"])
        elif raw.startswith("+") and current is not None:
            rows.append((current, line, raw[1:]))
            line += 1
    return rows


def _matches(needle: _Needle, added: str) -> bool:
    if needle["kind"] == "private-line":
        return needle["value"] in normalize(added)
    if needle["kind"] in ("test-name", "task-id"):
        return bounded(added, needle["value"])
    return needle["value"] in added


def _revision(root: Path, value: object) -> bool:
    if not text(value) or not isinstance(value, str) or value.startswith("-"):
        raise ValueError("invalid reuse selection")
    return _git(root, "rev-parse", "--verify", "--quiet", value + "^{commit}").returncode == 0


def check_reuse(root: Path, selected: object) -> dict:
    """Scan a git change for private content from the selected benchmark tasks.

    ``selected`` is ``{"tasks": [<task directory>], "base": <revision>, "head":
    <revision or null for the working tree>, "paths": [<pathspec>]}``. ``paths``
    names the prompts, skills, and harness code to check; changes inside the
    selected task directories are ignored.
    """
    if not isinstance(selected, dict) or set(selected) != {"tasks", "base", "head", "paths"}:
        raise ValueError("invalid reuse selection")
    tasks, paths = selected["tasks"], selected["paths"]
    if (
        not isinstance(tasks, list)
        or not tasks
        or not all(text(task) for task in tasks)
        or not isinstance(paths, list)
        or not paths
        or not all(text(path) and not str(path).startswith("-") for path in paths)
    ):
        raise ValueError("invalid reuse selection")
    root = Path(root).resolve(strict=True)
    result: dict = {"base": selected["base"], "head": selected["head"], "findings": [], "counts": {}}
    unresolved = [
        name
        for name, value in (("base", selected["base"]), ("head", selected["head"]))
        if value is not None and not _revision(root, value)
    ]
    if unresolved:
        return {**result, **_status([diagnostic("reuse.revision_unresolved", name, "unknown") for name in unresolved])}
    diagnostics: list[dict] = []
    needles: list[_Needle] = []
    task_dirs = []
    for task_path in tasks:
        task, found, problems = _needles(root, task_path)
        task_dirs.append(task + "/")
        needles.extend(found)
        diagnostics.extend(problems)
    try:
        lines = added_lines(root, selected["base"], selected["head"], paths)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {**result, **_status([*diagnostics, diagnostic("reuse.diff_unavailable", "diff", "unknown")])}
    findings: list[Finding] = []
    seen = set()
    for changed, line, added in lines:
        if any(changed.startswith(prefix) for prefix in task_dirs):
            continue
        for needle in needles:
            key = (needle["kind"], needle["task"], needle["private"], changed, line)
            if key not in seen and _matches(needle, added):
                seen.add(key)
                findings.append(
                    {
                        "kind": needle["kind"],
                        "task": needle["task"],
                        "private": needle["private"],
                        "changed": changed,
                        "line": line,
                    }
                )
    for finding in findings:
        status = "fail" if finding["kind"] in _FAILING_KINDS else "unknown"
        diagnostics.append(diagnostic(f"reuse.{finding['kind']}", finding["changed"], status))
    result["findings"] = findings
    result["counts"] = {
        "added_lines": len(lines),
        "private_strings": len(needles),
        **{
            kind: sum(row["kind"] == kind for row in findings)
            for kind in ("expected-value", "private-line", "test-name", "task-id")
        },
    }
    return {**result, **_status(diagnostics)}


def _status(diagnostics: list[dict]) -> dict:
    unique = {(row["code"], row["subject"], row["status"]): row for row in diagnostics}
    summary = finish([], list(unique.values()))
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
