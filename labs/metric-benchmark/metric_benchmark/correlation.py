# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tie-aware descriptive Spearman correlation over matched subset scores."""

import argparse
import math
from pathlib import Path
from typing import Literal

from metric_benchmark.coverage import CoverageScores
from metric_benchmark.io import cli, read_json, write_json
from metric_benchmark.models import Digest, Model, Score, Text
from metric_benchmark.scoring import Context, MutantScores, identity


class Pair(Model):
    eval_subset_id: Text
    coverage: float
    mutant_score: float


class Correlation(Model):
    artifact_type: Literal["correlation_results"] = "correlation_results"
    context: Context
    method: Literal["spearman_average_ties_v1"] = "spearman_average_ties_v1"
    missing_policy: Literal["error", "exclude"]
    coverage_sha256: Digest
    mutant_scores_sha256: Digest
    pairs: list[Pair]
    exclusions: dict[Text, list[Text]]
    coefficient: Score


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[start]] == values[order[stop]]:
            stop += 1
        average = (start + 1 + stop) / 2
        for i in order[start:stop]:
            result[i] = average
        start = stop
    return result


def spearman(x: list[float], y: list[float]) -> Score:
    if len(x) != len(y) or any(not math.isfinite(v) for v in [*x, *y]):
        raise ValueError("Spearman requires equal-length finite inputs")
    if len(x) < 2:
        return Score(value=None, missing_score_reason="fewer than two paired subsets")
    mean = (len(x) + 1) / 2
    rx = [r - mean for r in ranks(x)]
    ry = [r - mean for r in ranks(y)]
    xx, yy = math.fsum(r * r for r in rx), math.fsum(r * r for r in ry)
    if xx == 0 or yy == 0:
        return Score(value=None, missing_score_reason="constant score ranks")
    value = math.fsum(a * b for a, b in zip(rx, ry, strict=True)) / math.sqrt(xx * yy)
    return Score(value=max(-1.0, min(1.0, value)))


def correlate(
    coverage: CoverageScores, mutant_scores: MutantScores, *, missing_policy: Literal["error", "exclude"] = "error"
) -> Correlation:
    if missing_policy not in ("error", "exclude"):
        raise ValueError("unknown missing policy")
    if coverage.context != mutant_scores.context:
        raise ValueError("score artifacts refer to different experiment inputs or selection")
    if set(coverage.scores) != set(mutant_scores.scores):
        raise ValueError("score artifacts must contain exactly the same subset IDs")
    pairs, exclusions = [], {}
    for subset_id in sorted(coverage.scores):
        cov, mutant = coverage.scores[subset_id], mutant_scores.scores[subset_id]
        if cov.value is None or mutant.value is None:
            reasons = []
            if cov.value is None:
                reasons.append(f"coverage: {cov.missing_score_reason}")
            if mutant.value is None:
                reasons.append(f"mutant score: {mutant.missing_score_reason}")
            exclusions[subset_id] = reasons
        else:
            pairs.append(Pair(eval_subset_id=subset_id, coverage=cov.value, mutant_score=mutant.value))
    if exclusions and missing_policy == "error":
        raise ValueError("unavailable pairs; explicitly select missing_policy=exclude to omit them")
    return Correlation(
        context=coverage.context,
        missing_policy=missing_policy,
        coverage_sha256=identity(coverage),
        mutant_scores_sha256=identity(mutant_scores),
        pairs=pairs,
        exclusions=exclusions,
        coefficient=spearman([p.coverage for p in pairs], [p.mutant_score for p in pairs]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("coverage", "mutant-scores", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--missing-policy", choices=("error", "exclude"), default="error")

    def run(args: argparse.Namespace) -> None:
        write_json(
            args.output,
            correlate(
                CoverageScores.model_validate(read_json(args.coverage)),
                MutantScores.model_validate(read_json(args.mutant_scores)),
                missing_policy=args.missing_policy,
            ),
        )

    cli(parser, run)
