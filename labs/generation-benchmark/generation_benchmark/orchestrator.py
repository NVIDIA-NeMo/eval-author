# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end generation experiment driver."""

import argparse
from pathlib import Path

from metric_benchmark.io import cli, write_json
from metric_benchmark.models import Score

from generation_benchmark.analysis import analyze_generation, write_analysis
from generation_benchmark.config import load_generation_benchmark
from generation_benchmark.execution import execute_generation_baseline, execute_new_tasks
from generation_benchmark.generation import invoke_generation
from generation_benchmark.inputs import load_score_inputs
from generation_benchmark.measure import measure_baseline_full, measure_subset
from generation_benchmark.measure_hooks import run_before_measure_command
from generation_benchmark.merge import expand_subset, merge_tasks_into_case


def run_generation_benchmark(
    benchmark: Path,
    case_id: str,
    work_root: Path,
    run_id: str,
    *,
    cached_only: bool = False,
    subset_ids: list[str] | None = None,
) -> Path:
    loaded = load_generation_benchmark(benchmark)
    case = loaded.case(case_id)
    prepared = loaded.prepared(case_id)
    work_root.mkdir(parents=True, exist_ok=True)
    execution_root = work_root / "execution-results"
    execute_generation_baseline(loaded, case_id, run_id, execution_root)
    ordinal = list(loaded.execution.inputs.cases).index(case_id)
    results_root = execution_root / "cases" / f"case-{ordinal:04d}" / "execution-results"
    run_before_measure_command(loaded.analysis.settings.coverage.settings, results_root)
    baseline_scores = measure_baseline_full(
        benchmark, loaded, case_id, results_root, work_root / "coverage-baseline", cached_only=cached_only
    )
    m_full = baseline_scores.scores["__full_inventory__"]
    write_json(work_root / "baseline-coverage.json", baseline_scores)
    _, raw, subsets, _ = load_score_inputs(benchmark, case_id, results_root)
    generation_settings = loaded.resolved_generation_settings()
    generation_config = loaded.generation.model_copy(update={"settings": generation_settings})
    selected = subset_ids or [s.eval_subset_id for s in subsets.eval_subsets]
    memberships = subsets.resolve_tasks(case.task_inventory)
    pre_scores: dict[str, Score] = {}
    post_scores: dict[str, Score] = {}
    generation_index = []
    for eval_subset_id in selected:
        subset_dir = work_root / "subsets" / eval_subset_id
        subset_dir.mkdir(parents=True, exist_ok=True)
        suite_staged = subset_dir / "suite-staging"
        pre, coverage_cell = measure_subset(
            loaded,
            case,
            raw,
            subsets,
            eval_subset_id,
            input_root=prepared.root,
            results_root=results_root,
            coverage_work_root=subset_dir / "coverage-pre",
            suite_dir=suite_staged,
            cached_only=cached_only,
        )
        pre_scores[eval_subset_id] = pre.scores[eval_subset_id]
        subset_tasks = [task.model_dump() for task in memberships[eval_subset_id]]
        suite_plus, receipt = invoke_generation(
            generation_config,
            loaded.analysis.load_description(case_id),
            eval_subset_id,
            subset_tasks,
            suite_staged,
            coverage_cell,
            subset_dir / "generation",
            coverage=pre.scores[eval_subset_id],
            cached_only=cached_only,
        )
        new_ids = list(receipt.response.new_task_ids)
        if new_ids:
            case = merge_tasks_into_case(
                case, suite_plus, new_ids, prepared.root, runtime_namespace=eval_subset_id
            )
            execute_new_tasks(loaded, case, set(new_ids), results_root, run_id)
            run_before_measure_command(loaded.analysis.settings.coverage.settings, results_root)
            _, raw, subsets, _ = load_score_inputs(benchmark, case_id, results_root)
            subsets = expand_subset(subsets, eval_subset_id, new_ids)
            memberships = subsets.resolve_tasks(case.task_inventory)
        post, _ = measure_subset(
            loaded,
            case,
            raw,
            subsets,
            eval_subset_id,
            input_root=prepared.root,
            results_root=results_root,
            coverage_work_root=subset_dir / "coverage-post",
            cached_only=cached_only,
        )
        post_scores[eval_subset_id] = post.scores[eval_subset_id]
        generation_index.append(
            {
                "eval_subset_id": eval_subset_id,
                "suite_plus": str(suite_plus),
                "new_task_ids": list(receipt.response.new_task_ids),
                "receipt": receipt.model_dump(),
            }
        )
    generation_meta = {
        "generation_mode": str(generation_settings.get("generation_mode") or "unknown"),
        "subsets_sequential_coupled": True,
        "subsets": generation_index,
    }
    write_json(work_root / "generation-results.json", generation_meta)
    analysis = analyze_generation(
        benchmark_sha256=loaded.identity,
        case_id=case_id,
        m_full=m_full,
        pre_by_subset=pre_scores,
        post_by_subset=post_scores,
    )
    write_analysis(work_root / "generation-analysis.json", analysis)
    return work_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True, help="Prepared experiment.yml path")
    parser.add_argument("--case", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--subset-id", action="append", dest="subset_ids")
    parser.add_argument("--cached-only", action="store_true")

    def run(args: argparse.Namespace) -> None:
        run_generation_benchmark(
            args.benchmark,
            args.case,
            args.work_root,
            args.run_id,
            cached_only=args.cached_only,
            subset_ids=args.subset_ids,
        )

    cli(parser, run)
