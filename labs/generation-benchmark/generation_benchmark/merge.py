# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Merge generated Harbor tasks into experiment case state."""

import shutil
from pathlib import Path

from metric_benchmark.config import PinnedInput, input_digest
from metric_benchmark.models import Subset, Subsets, Task, Tasks

from generation_benchmark.config import (
    GenerationCase,
    append_generated_task_manifest,
)


def merge_tasks_into_case(
    case: GenerationCase,
    suite_plus: Path,
    new_task_ids: list[str],
    input_root: Path,
    *,
    runtime_namespace: str,
) -> GenerationCase:
    tasks = list(case.task_inventory.tasks)
    sources = dict(case.task_sources)
    existing = {t.task_id for t in tasks}
    for task_id in sorted(new_task_ids):
        if task_id in existing:
            continue
        src = suite_plus / task_id
        if not src.is_dir():
            raise ValueError(f"generated task directory missing: {src}")
        dest = input_root / "generated-runtime" / runtime_namespace / task_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, symlinks=True)
        tasks.append(Task(task_id=task_id, revision=1))
        rel = dest.relative_to(input_root)
        digest = input_digest(dest)
        sources[task_id] = PinnedInput(path=rel.as_posix(), sha256=digest, kind="directory")
        append_generated_task_manifest(input_root, task_id=task_id, relative_path=rel.as_posix(), digest=digest)
        existing.add(task_id)
    updated_inventory = Tasks.model_validate(
        {**case.task_inventory.model_dump(), "tasks": [t.model_dump() for t in tasks]}
    )
    return case.model_copy(update={"task_inventory": updated_inventory, "task_sources": sources})


def expand_subset(subsets: Subsets, eval_subset_id: str, extra_task_ids: list[str]) -> Subsets:
    base = next(s for s in subsets.eval_subsets if s.eval_subset_id == eval_subset_id)
    expanded_ids = sorted(set(base.task_ids) | set(extra_task_ids))
    expanded = Subset(eval_subset_id=eval_subset_id, task_ids=expanded_ids)
    others = [s for s in subsets.eval_subsets if s.eval_subset_id != eval_subset_id]
    return Subsets.model_validate({**subsets.model_dump(), "eval_subsets": [*others, expanded]})
