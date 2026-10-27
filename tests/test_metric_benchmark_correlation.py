# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import math

import pytest
from metric_benchmark.correlation import correlate, ranks, spearman
from metric_benchmark.coverage import invoke_coverage
from metric_benchmark.models import Score
from metric_benchmark.scoring import compute_mutant_scores
from test_metric_benchmark_coverage import coverage_example


def test_tie_ranks_and_known_spearman_values():
    assert ranks([5.0, 1.0, 5.0, 3.0]) == [3.5, 1.0, 3.5, 2.0]
    assert spearman([1, 2, 3], [2, 4, 8]).value == 1
    assert spearman([1, 2, 3], [8, 4, 2]).value == -1
    assert spearman([1, 1, 2], [1, 2, 3]).value == pytest.approx(math.sqrt(3) / 2)
    assert spearman([1, 1], [1, 2]).value is None
    assert spearman([], []).value is None
    with pytest.raises(ValueError):
        spearman([float("nan")], [1])


def test_matched_pairs_missing_policy_and_provenance(tmp_path):
    args = coverage_example(tmp_path)
    coverage = invoke_coverage(*args)
    mutants = compute_mutant_scores(*args[:3])
    result = correlate(coverage, mutants)
    assert result.coefficient.value == pytest.approx(math.sqrt(3) / 2)
    assert len(result.pairs) == 3
    assert mutants.diagnostics
    assert [(p.eval_subset_id, p.mutant_score) for p in result.pairs if p.mutant_score < 0] == [("subset-000003", -1.0)]
    assert result.exclusions == {}
    coverage.scores["subset-000001"] = Score(value=None, reason="system abstained")
    with pytest.raises(ValueError, match="unavailable"):
        correlate(coverage, mutants)
    result = correlate(coverage, mutants, missing_policy="exclude")
    assert result.coefficient.value == 1
    assert result.exclusions == {"subset-000001": ["coverage: system abstained"]}
    coverage.context.case_id = "different"
    with pytest.raises(ValueError, match="different experiment"):
        correlate(coverage, mutants)


def test_no_silent_intersection_of_mismatched_subsets(tmp_path):
    args = coverage_example(tmp_path)
    coverage = invoke_coverage(*args)
    mutants = compute_mutant_scores(*args[:3])
    coverage.scores.pop("subset-000001")
    with pytest.raises(ValueError, match="same subset IDs"):
        correlate(coverage, mutants, missing_policy="exclude")


def test_correlation_cli_library_parity(tmp_path):
    import subprocess
    import sys

    from metric_benchmark.coverage import invoke_coverage
    from metric_benchmark.io import json_bytes, write_json
    from metric_benchmark.scoring import compute_mutant_scores
    from test_metric_benchmark_config import LAB
    from test_metric_benchmark_coverage import coverage_example

    args = coverage_example(tmp_path)
    coverage = invoke_coverage(*args)
    mutants = compute_mutant_scores(args[0], args[1], args[2])
    write_json(tmp_path / "coverage.json", coverage)
    write_json(tmp_path / "mutants.json", mutants)
    out = tmp_path / "correlation.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "correlate.py"),
            "--coverage",
            str(tmp_path / "coverage.json"),
            "--mutant-scores",
            str(tmp_path / "mutants.json"),
            "--output",
            str(out),
        ],
        check=True,
    )
    assert out.read_bytes() == json_bytes(correlate(coverage, mutants))


def test_different_scoring_selection_cannot_be_correlated(tmp_path):
    args = coverage_example(tmp_path)
    coverage = invoke_coverage(*args)
    args[2].direction = "lower_is_better"
    mutants = compute_mutant_scores(*args[:3])
    with pytest.raises(ValueError, match="different experiment inputs or selection"):
        correlate(coverage, mutants)
