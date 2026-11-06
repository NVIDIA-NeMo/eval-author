# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from generation_benchmark.analysis import analyze_generation, compute_recovery
from metric_benchmark.models import Score


def test_recovery_zero_gap():
    assert compute_recovery(Score(value=0.5), Score(value=0.6), Score(value=0.5)) == (None, "zero_gap")


def test_analyze_generation_subset():
    analysis = analyze_generation(
        benchmark_sha256="sha256:" + "a" * 64,
        case_id="case",
        m_full=Score(value=0.8),
        pre_by_subset={"subset-000001": Score(value=0.4)},
        post_by_subset={"subset-000001": Score(value=0.6)},
    )
    row = analysis.subsets[0]
    assert row.delta_m == pytest.approx(0.2)
    assert row.recovery_r == pytest.approx(0.5)
