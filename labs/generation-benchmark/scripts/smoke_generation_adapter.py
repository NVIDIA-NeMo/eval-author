#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smoke G adapter: generator sidecar (stdin prompt, work cell cwd).

Copies checked-in ``templates/smoke-scaffold/`` Harbor drafts. Live generation uses
``eval_author_generate.py`` with a coding-agent generator config.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

_SLUG_RE = re.compile(r"with slug `([^`]+)`")
_DRAFT_RE = re.compile(r"[Ss]caffold exactly to `([^`]+)`")
_TASK_NAME_RE = re.compile(r"use task name `([^`]+)`")
_TASK_NAME_LINE = re.compile(r'^name = ".*"$', re.MULTILINE)
_SELECTION_RE = re.compile(r"Its selection record is:\n(\{.*?\n\})\n\nThe canonical audit", re.DOTALL)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _template_for_slug(case_root: Path, slug: str) -> Path:
    smoke_root = case_root / "templates" / "smoke-scaffold"
    for subset in ("eval-subset-000001", "eval-subset-000002"):
        candidate = smoke_root / subset / slug
        if candidate.is_dir():
            return candidate
    if slug == "cover-tool-0":
        return case_root / "generated-runtime/eval-subset-000002/cover-tool-0"
    if slug == "cover-tool-1":
        return case_root / "generated-runtime/eval-subset-000001/cover-tool-1"
    return case_root / "generated-runtime/eval-subset-000001/cover-task-1"


def main() -> int:
    case_root = os.environ.get("GENERATION_CASE_ROOT", "").strip()
    if not case_root:
        print("GENERATION_CASE_ROOT must point at the generation case directory", file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    match = _SLUG_RE.search(prompt)
    if not match:
        print("could not parse task slug from generator prompt", file=sys.stderr)
        return 1
    slug = match.group(1)
    draft_match = _DRAFT_RE.search(prompt)
    if not draft_match:
        print("could not parse draft path from generator prompt", file=sys.stderr)
        return 1
    task_name_match = _TASK_NAME_RE.search(prompt)
    task_name = task_name_match.group(1) if task_name_match else f"oracle-baseline/{slug}"
    draft = Path(draft_match.group(1))
    workspace = Path.cwd()
    draft.parent.mkdir(parents=True, exist_ok=True)
    template = _template_for_slug(Path(case_root).resolve(), slug)
    if not template.is_dir():
        print(f"template task missing: {template}", file=sys.stderr)
        return 1
    if draft.exists():
        shutil.rmtree(draft)
    shutil.copytree(template, draft, symlinks=True)
    task_toml = draft / "task.toml"
    if task_toml.is_file():
        text = task_toml.read_text(encoding="utf-8")
        task_toml.write_text(_TASK_NAME_LINE.sub(f'name = "{task_name}"', text, count=1), encoding="utf-8")
    selection_match = _SELECTION_RE.search(prompt)
    if not selection_match:
        print("could not parse selection record from generator prompt", file=sys.stderr)
        return 1
    target_name = str(json.loads(selection_match.group(1)).get("name", "")).strip()
    if not target_name:
        print("selection record is missing name", file=sys.stderr)
        return 1
    report_path = workspace / ".eval-author" / "audit-coverage-report.json"
    proposal_path = workspace / ".eval-author" / "proposals" / f"{slug}-instruction.md"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    if not proposal_path.is_file():
        instruction = draft / "instruction.md"
        proposal_path.write_text(
            instruction.read_text(encoding="utf-8") if instruction.is_file() else f"Proposal for {slug}\n",
            encoding="utf-8",
        )
    lineage_path = draft / ".eval-author-lineage.json"
    lineage_path.write_text(
        json.dumps(
            {
                "target_tool": target_name,
                "report": {"path": str(report_path.resolve()), "sha256": _digest(report_path)},
                "proposal": {"path": str(proposal_path.resolve()), "sha256": _digest(proposal_path)},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
