# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest
from metric_benchmark.models import RawResults, Subset
from metric_benchmark.scoring import ScoringConfig, compute_mutant_scores
from test_metric_benchmark_config import fixed_subsets, inventory


def example():
    tasks = inventory(3)
    rows = []
    for agent, rewards in {"baseline": [1.0, 0.5, 0.0], "mutant": [0.0, 0.5, 1.0]}.items():
        for task, reward in zip(tasks.tasks, rewards, strict=True):
            rows.append(
                {
                    "run_id": f"run-{agent}",
                    "agent_bundle_id": agent,
                    "evaluation": {
                        "evaluation_id": f"{agent}-{task.task_id}",
                        "execution": 1,
                        "task": task.model_dump(),
                    },
                    "status": "succeeded",
                    "metrics": {"reward": {"value": reward}},
                    "native_results_path": f"evidence/{agent}/{task.task_id}",
                    "native_results_format": "directory",
                }
            )
    raw = RawResults.model_validate({"experiment_id": "test", "task_inventory": tasks.model_dump(), "results": rows})
    subsets = fixed_subsets(tasks)
    config = ScoringConfig(
        case_id="case",
        baseline_bundle_id="baseline",
        mutant_bundle_ids=["mutant"],
        run_ids={"baseline": "run-baseline", "mutant": "run-mutant"},
        attempt_policy="only",
        completed_statuses=["succeeded"],
    )
    return raw, subsets, config


def test_signed_effects_include_improvement_and_explicit_direction():
    raw, subsets, config = example()
    scores = compute_mutant_scores(raw, subsets, config)
    assert [s.value for s in scores.scores.values()] == [1.0, 0.0, -1.0]
    config.direction = "lower_is_better"
    assert [s.value for s in compute_mutant_scores(raw, subsets, config).scores.values()] == [-1.0, 0.0, 1.0]


def test_equal_weight_paired_mean():
    raw, _, config = example()
    subsets = fixed_subsets(raw.task_inventory)
    subsets.eval_subsets = [Subset(eval_subset_id="all", tasks=raw.task_inventory.tasks)]
    assert next(iter(compute_mutant_scores(raw, subsets, config).scores.values())).value == 0.0


@pytest.mark.parametrize("failure", ["missing", "failed", "metric"])
def test_incomplete_pairs_are_not_zero_or_dropped(failure):
    raw, subsets, config = example()
    if failure == "missing":
        raw.results.pop(3)
    elif failure == "failed":
        raw.results[3].status = "error"
    else:
        raw.results[3].metrics.clear()
    scores = compute_mutant_scores(raw, subsets, config)
    assert scores.scores["subset-000001"].value is None
    assert scores.mutant_effects["subset-000001"]["mutant"].reason
    assert scores.scores["subset-000002"].value == 0


def test_retry_policy_selects_latest_even_if_failed():
    raw, subsets, config = example()
    retry = copy.deepcopy(raw.results[0])
    retry.evaluation.execution = 2
    retry.status = "error"
    raw.results.append(retry)
    with pytest.raises(ValueError, match="multiple attempts"):
        compute_mutant_scores(raw, subsets, config)
    config.attempt_policy = "latest"
    assert compute_mutant_scores(raw, subsets, config).scores["subset-000001"].value is None


def test_repetitions_and_incompatible_sources_rejected():
    raw, subsets, config = example()
    extra = copy.deepcopy(raw.results[0])
    extra.evaluation.evaluation_id = "replica"
    raw.results.append(extra)
    with pytest.raises(ValueError, match="repetition"):
        compute_mutant_scores(raw, subsets, config)
    raw.results.pop()
    subsets.task_inventory.benchmark.revision = 2
    with pytest.raises(ValueError, match="task inventories"):
        compute_mutant_scores(raw, subsets, config)


def test_run_mapping_is_verified():
    raw, subsets, config = example()
    config.run_ids = {"baseline": "run-mutant", "mutant": "run-baseline"}
    with pytest.raises(ValueError, match="different agent"):
        compute_mutant_scores(raw, subsets, config)


def test_scoring_cli_library_parity_and_run_independent_subsets(tmp_path):
    import subprocess
    import sys

    from metric_benchmark.io import json_bytes, write_json
    from test_metric_benchmark_config import LAB

    raw, subsets, config = example()
    for name, value in [("results", raw), ("subsets", subsets), ("config", config)]:
        write_json(tmp_path / f"{name}.json", value)
    out = tmp_path / "scores.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "compute_mutant_scores.py"),
            "--results",
            str(tmp_path / "results.json"),
            "--subsets",
            str(tmp_path / "subsets.json"),
            "--config",
            str(tmp_path / "config.json"),
            "--output",
            str(out),
        ],
        check=True,
    )
    first = compute_mutant_scores(raw, subsets, config)
    assert out.read_bytes() == json_bytes(first)
    raw.experiment_id = "a-new-run"
    assert compute_mutant_scores(raw, subsets, config).scores == first.scores


def test_negative_diagnostics_preserve_scores_and_follow_metric_direction():
    raw, subsets, config = example()
    for direction, subset_id in [("higher_is_better", "subset-000003"), ("lower_is_better", "subset-000001")]:
        config.direction = direction
        result = compute_mutant_scores(raw, subsets, config)
        assert [(d.eval_subset_id, d.mutant_bundle_id, d.value) for d in result.diagnostics] == [
            (subset_id, None, -1.0),
            (subset_id, "mutant", -1.0),
        ]
        assert result.scores[subset_id].value == -1.0
        assert result.scores[subset_id].reason is None


def test_negative_effect_flag_survives_positive_or_unavailable_aggregate():
    raw, subsets, config = example()
    raw.results[0].metrics["reward"].value = 0.5
    raw.results[3].metrics["reward"].value = 0.75
    other = copy.deepcopy(raw.results[3:])
    for row in other:
        row.agent_bundle_id = "other"
        row.run_id = "run-other"
        row.evaluation.evaluation_id = f"other-{row.evaluation.task.task_id}"
        row.metrics["reward"].value = 0.0
    raw.results.extend(other)
    config.mutant_bundle_ids.append("other")
    config.run_ids["other"] = "run-other"
    result = compute_mutant_scores(raw, subsets, config)
    assert result.scores["subset-000001"].value == 0.125
    flags = [d for d in result.diagnostics if d.eval_subset_id == "subset-000001"]
    assert [(d.mutant_bundle_id, d.value) for d in flags] == [("mutant", -0.25)]
    other[0].status = "error"
    result = compute_mutant_scores(raw, subsets, config)
    assert result.scores["subset-000001"].value is None
    assert [d for d in result.diagnostics if d.eval_subset_id == "subset-000001"] == flags
