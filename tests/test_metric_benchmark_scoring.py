# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest
from metric_benchmark.models import RawResults, Subset, Tasks
from metric_benchmark.scoring import ScoringConfig, compute_mutant_scores
from test_metric_benchmark_config import fixed_subsets, inventory


def example():
    tasks = inventory(3)
    rows = []
    for agent, rewards in {"baseline": [1.0, 0.5, 0.0], "mutant": [0.0, 0.5, 1.0]}.items():
        for task, reward in zip(tasks.tasks, rewards, strict=True):
            rows.append(
                {
                    "agent_run_id": f"run-{agent}",
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
    raw = RawResults.model_validate(
        {"run_id": "test", "task_inventory": [task.model_dump() for task in tasks.tasks], "execution_results": rows}
    )
    subsets = fixed_subsets(tasks)
    config = ScoringConfig(
        case_id="rho-agent",
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
    subsets = fixed_subsets(Tasks(tasks=raw.task_inventory))
    subsets.eval_subsets = [Subset(eval_subset_id="all", task_ids=[t.task_id for t in raw.task_inventory])]
    raw.execution_results[1].metrics["reward"].value = 1.0
    raw.execution_results[4].metrics["reward"].value = 0.0
    raw.execution_results[5].metrics["reward"].value = 0.0
    assert next(iter(compute_mutant_scores(raw, subsets, config).scores.values())).value == pytest.approx(2 / 3)


@pytest.mark.parametrize("failure", ["missing", "failed", "metric"])
def test_incomplete_pairs_are_not_zero_or_dropped(failure):
    raw, subsets, config = example()
    if failure == "missing":
        raw.execution_results.pop(3)
    elif failure == "failed":
        raw.execution_results[3].status = "error"
    else:
        raw.execution_results[3].metrics.clear()
    scores = compute_mutant_scores(raw, subsets, config)
    assert scores.scores["subset-000001"].value is None
    assert scores.mutant_effects["subset-000001"]["mutant"].reason
    assert scores.scores["subset-000002"].value == 0


def test_retry_policy_selects_latest_even_if_failed():
    raw, subsets, config = example()
    retry = copy.deepcopy(raw.execution_results[0])
    retry.evaluation.execution = 2
    retry.status = "error"
    raw.execution_results.append(retry)
    with pytest.raises(ValueError, match="multiple attempts"):
        compute_mutant_scores(raw, subsets, config)
    config.attempt_policy = "latest"
    assert compute_mutant_scores(raw, subsets, config).scores["subset-000001"].value is None


def test_repetitions_and_incompatible_sources_rejected():
    raw, subsets, config = example()
    extra = copy.deepcopy(raw.execution_results[0])
    extra.evaluation.evaluation_id = "replica"
    raw.execution_results.append(extra)
    with pytest.raises(ValueError, match="repetition"):
        compute_mutant_scores(raw, subsets, config)
    raw.execution_results.pop()
    subsets.eval_subsets[0].task_ids = ["unknown-task"]
    with pytest.raises(ValueError, match="unknown task IDs"):
        compute_mutant_scores(raw, subsets, config)


def test_run_mapping_is_verified():
    raw, subsets, config = example()
    config.run_ids = {"baseline": "run-mutant", "mutant": "run-baseline"}
    with pytest.raises(ValueError, match="different agent"):
        compute_mutant_scores(raw, subsets, config)


def test_scoring_subsets_are_run_independent():
    raw, subsets, config = example()
    first = compute_mutant_scores(raw, subsets, config)
    raw.run_id = "a-new-run"
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
    raw.execution_results[0].metrics["reward"].value = 0.5
    raw.execution_results[3].metrics["reward"].value = 0.75
    other = copy.deepcopy(raw.execution_results[3:])
    for row in other:
        row.agent_bundle_id = "other"
        row.agent_run_id = "run-other"
        row.evaluation.evaluation_id = f"other-{row.evaluation.task.task_id}"
        row.metrics["reward"].value = 0.0
    raw.execution_results.extend(other)
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


def test_mutated_library_input_rejects_duplicate_attempts():
    raw, subsets, config = example()
    raw.execution_results.append(raw.execution_results[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="duplicate execution"):
        compute_mutant_scores(raw, subsets, config)


def test_configured_scoring_cli_needs_no_generated_selection(tmp_path):
    import subprocess
    import sys

    from metric_benchmark.io import read_json, write_json
    from metric_benchmark.validation import selection_for_results
    from test_metric_benchmark_config import LAB
    from test_metric_benchmark_validation import result_example

    loaded, case, raw, root, path = result_example(tmp_path)
    import shutil

    expected = compute_mutant_scores(
        raw, loaded.subsets[case.case_id], selection_for_results(case, raw, loaded.definition.scoring)
    )
    write_json(
        tmp_path / "execution.json",
        {
            "benchmark_sha256": loaded.identity,
            "run_id": raw.run_id,
            "cases": {case.case_id: (root.relative_to(tmp_path) / "results.json").as_posix()},
        },
    )
    # Scoring needs only the definition, fixed subsets and execution indexes.
    for source in case.task_sources.values():
        shutil.rmtree(loaded.root / source.path)
    (loaded.root / case.case_description.path).unlink()
    shutil.rmtree(root / "agents")
    out = tmp_path / "scores.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "compute_mutant_scores.py"),
            "--benchmark",
            str(path),
            "--case",
            case.case_id,
            "--results-root",
            str(tmp_path),
            "--output",
            str(out),
        ],
        check=True,
    )
    assert read_json(out) == expected.model_dump(mode="json")


@pytest.mark.parametrize("change, message", [("subset", "duplicate subset IDs"), ("selection", "run_ids must select")])
def test_mutable_scoring_inputs_are_validated_before_computation(change, message):
    raw, subsets, config = example()
    if change == "subset":
        subsets.eval_subsets.append(subsets.eval_subsets[0].model_copy(deep=True))
    else:
        config.run_ids.pop("mutant")
    with pytest.raises(ValueError, match=message):
        compute_mutant_scores(raw, subsets, config)


@pytest.mark.parametrize("change", ["missing-case", "run-id", "traversal", "wrong-name"])
def test_analysis_index_rejects_mismatched_or_unsafe_case_paths(tmp_path, change):
    from metric_benchmark.io import write_json
    from metric_benchmark.scoring import load_score_inputs
    from test_metric_benchmark_validation import result_example

    loaded, case, raw, root, path = result_example(tmp_path)
    manifest = {
        "benchmark_sha256": loaded.identity,
        "run_id": raw.run_id,
        "cases": {case.case_id: (root.relative_to(tmp_path) / "results.json").as_posix()},
    }
    if change == "missing-case":
        manifest["cases"] = {"another-case": "results/results.json"}
    elif change == "run-id":
        manifest["run_id"] = "another-run"
    elif change == "traversal":
        manifest["cases"][case.case_id] = "../results.json"
    else:
        (root / "results.json").rename(root / "other.json")
        manifest["cases"][case.case_id] = "results/other.json"
    write_json(tmp_path / "execution.json", manifest)
    with pytest.raises(ValueError):
        load_score_inputs(path, case.case_id, tmp_path)
