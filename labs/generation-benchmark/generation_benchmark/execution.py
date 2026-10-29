# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harbor execution for generation experiments (baseline and incremental tasks)."""

import shutil
from importlib.metadata import version
from pathlib import Path

from metric_benchmark.config import LoadedCase
from metric_benchmark.execution import (
    case_root,
    execution_identity,
    job_configuration,
    run_harbor,
    run_or_reuse_job,
)
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import ExecutionManifest, RawResults, Tasks, Text
from metric_benchmark.validation import validate_results

from generation_benchmark.config import GenerationCase, LoadedGenerationBenchmark


def _task_paths(prepared: LoadedCase) -> list[Path]:
    case = prepared.case
    paths = [
        case.task_sources[task.task_id].resolve(prepared.root, allow_unsigned=prepared.allow_unsigned)
        for task in sorted(case.task_inventory.tasks, key=lambda task: task.key())
    ]
    if len(set(paths)) != len(paths):
        raise ValueError(f"case {case.case_id}: task sources must have distinct paths")
    return paths


def execute_generation_baseline(
    loaded: LoadedGenerationBenchmark,
    case_id: str,
    run_id: Text,
    output: Path,
    *,
    runner=run_harbor,
) -> Path:
    if not run_id.strip():
        raise ValueError("run_id must be nonempty")
    if version("harbor") != loaded.execution.settings.harbor_version:
        raise ValueError("installed Harbor version differs from benchmark execution version")
    prepared = loaded.prepared(case_id)
    case = loaded.case(case_id)
    output = output.resolve()
    ordinal = next(i for i, entry in enumerate(loaded.execution.inputs.definitions) if entry.case_id == case_id)
    root = case_root(output, ordinal)
    identity = execution_identity(loaded.execution)
    config = job_configuration(loaded.execution, prepared, 0, root, _task_paths(prepared))
    rows = run_or_reuse_job(loaded.execution, case, 0, root, config, identity, runner=runner)
    raw = RawResults(
        run_id=run_id,
        task_inventory=case.task_inventory.tasks,
        execution_results=rows,
    )
    validate_results(raw, case, root, prepared.root, allow_unsigned=prepared.allow_unsigned)
    write_json(root / "results.json", raw)
    write_json(
        output / "execution.json",
        ExecutionManifest(
            cases_sha256=loaded.execution.inputs.identity,
            execution_sha256=identity,
            run_id=run_id,
            cases={case.case_id: (root / "results.json").relative_to(output).as_posix()},
        ),
    )
    return root


def execute_new_tasks(
    loaded: LoadedGenerationBenchmark,
    case: GenerationCase,
    new_task_ids: set[str],
    results_root: Path,
    run_id: Text,
    *,
    runner=run_harbor,
) -> RawResults:
    if not new_task_ids:
        return RawResults.model_validate(read_json(results_root / "results.json"))
    missing = new_task_ids - {t.task_id for t in case.task_inventory.tasks}
    if missing:
        raise ValueError(f"unknown task ids for incremental execute: {sorted(missing)}")
    raw = RawResults.model_validate(read_json(results_root / "results.json"))
    executed = {
        row.evaluation.task.task_id
        for row in raw.execution_results
        if row.agent_bundle_id == case.baseline.agent_bundle_id and row.status == "succeeded"
    }
    pending = new_task_ids - executed
    if not pending:
        return raw
    filtered_tasks = [t for t in case.task_inventory.tasks if t.task_id in pending]
    partial_case = case.model_copy(
        update={"task_inventory": Tasks.model_validate({**case.task_inventory.model_dump(), "tasks": filtered_tasks})}
    )
    prepared = loaded.prepared(case.case_id)
    partial_root = results_root / "incremental" / "-".join(sorted(pending))[:120]
    partial_root.mkdir(parents=True, exist_ok=True)
    partial_prepared = LoadedCase(partial_case, prepared.root, prepared.allow_unsigned)
    config = job_configuration(loaded.execution, partial_prepared, 0, partial_root, _task_paths(partial_prepared))
    new_rows = run_or_reuse_job(
        loaded.execution,
        partial_case,
        0,
        partial_root,
        config,
        execution_identity(loaded.execution),
        runner=runner,
    )
    merged = {(*r.agent_bundle_id, *r.evaluation.task.key()): r for r in raw.execution_results}
    job_root = results_root / "agents" / "agent-0000" / "run-0001"
    job_root.mkdir(parents=True, exist_ok=True)
    for row in new_rows:
        source = partial_root / row.native_results_path
        destination = job_root / source.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        row = row.model_copy(update={"native_results_path": destination.relative_to(results_root).as_posix()})
        merged[(row.agent_bundle_id, *row.evaluation.task.key())] = row
    updated = RawResults(
        run_id=run_id,
        task_inventory=case.task_inventory.tasks,
        execution_results=sorted(merged.values(), key=lambda r: (r.agent_bundle_id, r.evaluation.task.key())),
    )
    validate_results(updated, case, results_root, prepared.root, allow_unsigned=prepared.allow_unsigned)
    results_path = results_root / "results.json"
    if results_path.exists():
        results_path.unlink()
    write_json(results_path, updated)
    return updated
