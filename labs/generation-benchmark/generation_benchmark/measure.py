# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Coverage measurement wrappers for generation experiments."""

from pathlib import Path
from typing import Literal

from metric_benchmark.coverage import CoverageScores, invoke_coverage
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import Subsets

from generation_benchmark.config import GenerationCase, LoadedGenerationBenchmark
from generation_benchmark.inputs import baseline_selection, full_inventory_subset, subset_only


def measure_coverage(
    loaded: LoadedGenerationBenchmark,
    case: GenerationCase,
    raw,
    subsets: Subsets,
    *,
    results_root: Path,
    work_root: Path,
    cached_only: bool = False,
) -> CoverageScores:
    selection = baseline_selection(case, raw, loaded.analysis.settings.scoring)
    description = loaded.analysis.load_description(case.case_id)
    coverage_settings = dict(loaded.analysis.settings.coverage.settings or {})
    coverage_settings.pop("before_measure_command", None)
    configuration = loaded.analysis.settings.coverage.model_copy(update={"settings": coverage_settings})
    return invoke_coverage(
        raw,
        subsets,
        selection,
        configuration,
        description,
        results_root,
        work_root,
        cached_only=cached_only,
    )


def measure_baseline_full(
    benchmark: Path,
    loaded: LoadedGenerationBenchmark,
    case_id: str,
    results_root: Path,
    work_root: Path,
    *,
    cached_only: bool = False,
) -> CoverageScores:
    from generation_benchmark.inputs import load_score_inputs

    case, raw, subsets, _ = load_score_inputs(benchmark, case_id, results_root)
    return measure_coverage(
        loaded,
        case,
        raw,
        full_inventory_subset(subsets),
        results_root=results_root,
        work_root=work_root,
        cached_only=cached_only,
    )


def measure_subset(
    loaded: LoadedGenerationBenchmark,
    case: GenerationCase,
    raw,
    subsets: Subsets,
    eval_subset_id: str,
    *,
    input_root: Path,
    results_root: Path,
    coverage_work_root: Path,
    suite_dir: Path | None = None,
    cached_only: bool = False,
) -> tuple[CoverageScores, Path]:
    filtered = subset_only(subsets, eval_subset_id)
    scores = measure_coverage(
        loaded,
        case,
        raw,
        filtered,
        results_root=results_root,
        work_root=coverage_work_root,
        cached_only=cached_only,
    )
    if suite_dir is not None:
        _stage_subset_suite(case, input_root, filtered, suite_dir)
    return scores, _coverage_work_cell(coverage_work_root, eval_subset_id)


def _coverage_work_cell(work_root: Path, eval_subset_id: str) -> Path:
    for cell in sorted(work_root.glob("subset-*")):
        request_path = cell / "request.json"
        if not request_path.is_file():
            continue
        payload = read_json(request_path)
        if isinstance(payload, dict) and payload.get("eval_subset_id") == eval_subset_id:
            return cell
    raise ValueError(f"coverage work cell missing for {eval_subset_id} under {work_root}")


def _stage_subset_suite(case: GenerationCase, input_root: Path, subsets: Subsets, staging: Path) -> Path:
    import shutil

    staging.mkdir(parents=True, exist_ok=True)
    task_ids = set(subsets.eval_subsets[0].task_ids)
    for task_id in sorted(task_ids):
        src = case.task_sources[task_id].resolve(input_root, allow_unsigned=False)
        dst = staging / task_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
    return staging


def write_coverage_scores(path: Path, scores: CoverageScores, scope: Literal["baseline", "subset"]) -> None:
    write_json(path, {"scope": scope, **scores.model_dump()})
