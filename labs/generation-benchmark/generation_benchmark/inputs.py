# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load analysis inputs for a generation experiment case."""

from pathlib import Path

from metric_benchmark.config import ScoringConfig
from metric_benchmark.io import read_json
from metric_benchmark.models import RawResults, Subsets

from generation_benchmark.config import GenerationCase, load_generation_benchmark


def baseline_selection(case: GenerationCase, raw: RawResults, scoring) -> ScoringConfig:
    runs: dict[str, str] = {}
    for row in raw.execution_results:
        if row.agent_bundle_id != case.baseline.agent_bundle_id:
            raise ValueError(f"unexpected agent in generation results: {row.agent_bundle_id}")
        if runs.setdefault(row.agent_bundle_id, row.agent_run_id) != row.agent_run_id:
            raise ValueError(f"case {case.case_id}: select one run per agent")
    if set(runs) != {case.baseline.agent_bundle_id}:
        raise ValueError(f"case {case.case_id}: incomplete baseline matrix")
    return case.selection(runs, scoring)


def load_score_inputs(
    benchmark: Path, case_id: str, results_root: Path
) -> tuple[GenerationCase, RawResults, Subsets, ScoringConfig]:
    loaded = load_generation_benchmark(benchmark)
    case = loaded.case(case_id)
    raw = RawResults.model_validate(read_json(results_root / "results.json"))
    subsets = loaded.analysis.load_subsets(case_id)
    return case, raw, subsets, baseline_selection(case, raw, loaded.analysis.settings.scoring)


def subset_only(subsets: Subsets, eval_subset_id: str) -> Subsets:
    match = [s for s in subsets.eval_subsets if s.eval_subset_id == eval_subset_id]
    if len(match) != 1:
        raise ValueError(f"unknown eval_subset_id: {eval_subset_id}")
    return Subsets.model_validate({**subsets.model_dump(), "eval_subsets": match})


def full_inventory_subset(subsets: Subsets) -> Subsets:
    task_ids = sorted({task_id for subset in subsets.eval_subsets for task_id in subset.task_ids})
    return Subsets.model_validate(
        {
            **subsets.model_dump(),
            "eval_subsets": [{"eval_subset_id": "__full_inventory__", "task_ids": task_ids}],
        }
    )
