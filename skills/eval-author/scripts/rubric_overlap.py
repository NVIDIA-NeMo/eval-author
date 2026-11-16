# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Flag Harbor tasks shared between declared development and held-out splits.

Only the declared task lists are compared. A split that isn't declared, or a
task that can't be read, leaves the result unknown rather than clean.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from itertools import combinations
from pathlib import Path
from typing import Literal, TypedDict

from rubric_identity import diagnostic, finish, read_selected, text
from rubric_layout import label_task

Kind = Literal["duplicate", "sibling"]
_REPO_KEYS = ("repo", "repository", "repo_url", "source_repo", "github_repo")
_COMMIT_KEYS = ("commit", "base_commit", "revision", "source_commit")
_CLONE = re.compile(r"git\s+clone\s+(?:-{1,2}[\w-]+(?:[ =]\S+)?\s+)*(?P<url>(?:https?://|git@)\S+)")
_CHECKOUT = re.compile(r"git\s+(?:-C\s+\S+\s+)?(?:checkout|reset\s+--hard)\s+(?P<sha>[0-9a-f]{7,40})\b")


class TaskRef(TypedDict):
    split: str
    task: str


class Finding(TypedDict):
    kind: Kind
    method: str
    tasks: list[TaskRef]


class _Task(TypedDict):
    split: str
    task: str
    instruction: str | None
    normalized: str | None
    origin: str | None


def normalize(value: str) -> str:
    return " ".join(value.split()).lower()


def _repository(url: str) -> str:
    url = url.strip().removesuffix("/").removesuffix(".git").lower()
    return re.sub(r"^(?:https?://|git@)", "", url).replace(":", "/", 1)


def _metadata_origin(document: dict) -> tuple[str | None, str | None]:
    metadata = document.get("metadata", {})
    if not isinstance(metadata, dict):
        return None, None
    repo = next((metadata[key] for key in _REPO_KEYS if text(metadata.get(key))), None)
    commit = next((metadata[key] for key in _COMMIT_KEYS if text(metadata.get(key))), None)
    return repo, commit


def _origin(root: Path, layout: dict) -> tuple[str | None, list[dict]]:
    """Return a repository-and-commit key from task metadata or the environment's git commands."""
    task = layout["task"]
    repo = commit = None
    diagnostics = []
    data, _ = read_selected(root, f"{task}/task.toml")
    if data is not None:
        try:
            repo, commit = _metadata_origin(tomllib.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            diagnostics.append(diagnostic("overlap.task_config_invalid", task, "unknown"))
    if not (repo and commit):
        for record in layout["files"]:
            if not record["path"].startswith(f"{task}/environment/") or "ockerfile" not in record["path"]:
                continue
            content, _ = read_selected(root, record["path"])
            source = content.decode("utf-8", "replace") if content is not None else ""
            clone, checkout = _CLONE.search(source), _CHECKOUT.search(source)
            repo = repo or (clone["url"] if clone else None)
            commit = commit or (checkout["sha"] if checkout else None)
    if not (repo and commit):
        return None, diagnostics
    return f"{_repository(str(repo))}@{str(commit).lower()}", diagnostics


def _read_task(root: Path, split: str, task: str) -> tuple[_Task, list[dict]]:
    layout = label_task(root, {"task": task})
    entry: _Task = {"split": split, "task": layout["task"], "instruction": None, "normalized": None, "origin": None}
    if layout["format"] != "harbor":
        return entry, [diagnostic("overlap.task_not_harbor", layout["task"], "unknown")]
    data, _ = read_selected(root, f"{layout['task']}/instruction.md")
    try:
        content = data.decode("utf-8") if data is not None else None
    except UnicodeDecodeError:
        content = None
    if content is None:
        return entry, [diagnostic("overlap.instruction_unreadable", layout["task"], "unknown")]
    entry["instruction"] = hashlib.sha256(content.encode()).hexdigest()
    entry["normalized"] = hashlib.sha256(normalize(content).encode()).hexdigest()
    entry["origin"], diagnostics = _origin(root, layout)
    return entry, diagnostics


def _compare(first: _Task, second: _Task) -> Finding | None:
    pair: list[TaskRef] = [
        {"split": first["split"], "task": first["task"]},
        {"split": second["split"], "task": second["task"]},
    ]
    if first["task"] == second["task"]:
        return {"kind": "duplicate", "method": "same-directory", "tasks": pair}
    if first["instruction"] and first["instruction"] == second["instruction"]:
        return {"kind": "duplicate", "method": "exact-instruction", "tasks": pair}
    if first["normalized"] and first["normalized"] == second["normalized"]:
        return {"kind": "duplicate", "method": "normalized-instruction", "tasks": pair}
    if first["origin"] and first["origin"] == second["origin"]:
        return {"kind": "sibling", "method": "repository-commit", "tasks": pair}
    return None


def check_overlap(root: Path, selected: object) -> dict:
    """Compare tasks across declared splits.

    ``selected`` is ``{"splits": {<name>: [<task directory>, ...]}, "required":
    [<name>, ...]}``. ``required`` names the splits a complete check needs, such
    as development and test; a required split that isn't declared is unknown.
    """
    if not isinstance(selected, dict) or set(selected) != {"splits", "required"}:
        raise ValueError("invalid overlap selection")
    splits, required = selected["splits"], selected["required"]
    if (
        not isinstance(splits, dict)
        or not isinstance(required, list)
        or len(required) < 2
        or len(set(required)) != len(required)
        or not all(text(name) for name in [*splits, *required])
        or not all(isinstance(tasks, list) and all(text(task) for task in tasks) for tasks in splits.values())
    ):
        raise ValueError("invalid overlap selection")
    root = Path(root).resolve(strict=True)
    diagnostics = [diagnostic("overlap.split_missing", name, "unknown") for name in required if name not in splits]
    tasks: list[_Task] = []
    for split in sorted(splits):
        for task in splits[split]:
            entry, problems = _read_task(root, split, task)
            tasks.append(entry)
            diagnostics.extend(problems)
    findings = [
        finding
        for first, second in combinations(tasks, 2)
        if first["split"] != second["split"] and (finding := _compare(first, second)) is not None
    ]
    diagnostics.extend(diagnostic(f"overlap.{row['kind']}", row["tasks"][1]["task"]) for row in findings)
    summary = finish([], diagnostics)
    return {
        "status": summary["status"],
        "splits": {
            name: {
                "declared": name in splits,
                "tasks": len(splits.get(name, [])),
                "compared": sum(row["split"] == name and row["instruction"] is not None for row in tasks),
                "with_origin": sum(row["split"] == name and row["origin"] is not None for row in tasks),
            }
            for name in sorted({*splits, *required})
        },
        "findings": findings,
        "counts": {
            "duplicate": sum(row["kind"] == "duplicate" for row in findings),
            "sibling": sum(row["kind"] == "sibling" for row in findings),
        },
        "diagnostics": summary["diagnostics"],
    }
