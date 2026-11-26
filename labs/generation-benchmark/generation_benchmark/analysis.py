# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-subset coverage change and gap recovery for generation benchmarks."""

from statistics import fmean, median
from typing import Literal

from metric_benchmark.io import write_json
from metric_benchmark.models import Model, Score, Text


class SubsetGenerationAnalysis(Model):
    eval_subset_id: Text
    m_pre: Score
    m_post: Score
    m_full: Score
    delta_m: float | None
    recovery_r: float | None
    recovery_reason: Text | None = None


class GenerationAnalysis(Model):
    artifact_type: Literal["generation_analysis"] = "generation_analysis"
    benchmark_sha256: Text
    case_id: Text
    subsets: list[SubsetGenerationAnalysis]
    delta_mean: float | None
    delta_median: float | None
    recovery_mean: float | None
    recovery_median: float | None


def compute_delta(pre: Score, post: Score) -> float | None:
    if pre.value is None or post.value is None:
        return None
    return post.value - pre.value


def compute_recovery(pre: Score, post: Score, full: Score) -> tuple[float | None, str | None]:
    if pre.value is None or post.value is None or full.value is None:
        return None, "missing_score"
    gap = full.value - pre.value
    if gap == 0:
        return None, "zero_gap"
    return (post.value - pre.value) / gap, None


def analyze_generation(
    *,
    benchmark_sha256: str,
    case_id: str,
    m_full: Score,
    pre_by_subset: dict[str, Score],
    post_by_subset: dict[str, Score],
) -> GenerationAnalysis:
    rows: list[SubsetGenerationAnalysis] = []
    deltas: list[float] = []
    recoveries: list[float] = []
    for subset_id in sorted(pre_by_subset):
        pre = pre_by_subset[subset_id]
        post = post_by_subset[subset_id]
        delta = compute_delta(pre, post)
        recovery, reason = compute_recovery(pre, post, m_full)
        if delta is not None:
            deltas.append(delta)
        if recovery is not None:
            recoveries.append(recovery)
        rows.append(
            SubsetGenerationAnalysis(
                eval_subset_id=subset_id,
                m_pre=pre,
                m_post=post,
                m_full=m_full,
                delta_m=delta,
                recovery_r=recovery,
                recovery_reason=reason,
            )
        )
    return GenerationAnalysis(
        benchmark_sha256=benchmark_sha256,
        case_id=case_id,
        subsets=rows,
        delta_mean=fmean(deltas) if deltas else None,
        delta_median=median(deltas) if deltas else None,
        recovery_mean=fmean(recoveries) if recoveries else None,
        recovery_median=median(recoveries) if recoveries else None,
    )


def write_analysis(path, analysis: GenerationAnalysis) -> None:
    write_json(path, analysis)
