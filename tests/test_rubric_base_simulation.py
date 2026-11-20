# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/rubric_base_simulation/run.py"
# Expected values are written out by hand from the canonical anchors, not computed by the scorer.
EXPECTED_DROPS = {
    "approval-after-effect": {"task-contract.scoring-authority": 0, "AUDIT-03": 0},
    "completion-only": {"provenance.grading-path": 0, "AUDIT-03": 0, "AUDIT-04-B": 0},
    "disabled-ledger": {"provenance.runtime-route": 0, "provenance.solver-tool-access": 0},
    "hidden-completion": {"task-contract.clear-contract": 0, "task-contract.discoverable-requirements": 1},
    "ledger-omission": {"AUDIT-03": 0},
    "missing-contract": {"provenance.task-artifact-access": 0, "provenance.grading-dependency-access": 0},
    "tax-drift": {"provenance.external-state": 0},
    "unpinned-image": {"provenance.dependency-substitution": None},
    "no-independent-corroboration": {"provenance.grading-path": 2, "AUDIT-03": 2, "CREATE-02": 2},
    "heldout-attacks-unrun": {"AUDIT-03": 2},
    "no-native-run": {"provenance.observed-configuration": None},
    "stale-evidence-combined": {"provenance.evidence-revision-consistency": 0},
    "blind-discovery-failed": {"task-contract.discoverable-requirements": 1},
    "exploit-accepted-uncontained": {"AUDIT-04-B": 0, "AUDIT-04-B-SUITE": 0},
    "exposure-overclaimed": {"AUDIT-05-B": 0},
    "confirmation-exposed": {"AUDIT-05-S03": 0},
    "trace-unauthorized-use": {"TRACE-04-A": 0},
    "state-carryover": {"ENV-03-S01": 0},
    "cue-bypass": {"CREATE-02": 0},
    "misreported-config": {"provenance.declared-configuration": 0},
    "unmatched-comparison": {"provenance.comparison-fairness": 0},
    "family-cue-bypass": {"CREATE-02-SUITE": 0},
}


@pytest.fixture(scope="module")
def summary(tmp_path_factory: pytest.TempPathFactory) -> dict:
    output = tmp_path_factory.mktemp("simulation") / "run"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads((output / "summary.json").read_text())


def test_base_is_perfect_and_each_mutation_drops_its_target(summary: dict) -> None:
    scores = summary["scores"]
    assert len(summary["rubric_criteria"]) == 23
    assert all(score == 3 for score in scores["base"].values())
    assert set(scores) == {"base", *EXPECTED_DROPS}
    assert all(summary["mutations_below_base"].values())
    for case, drops in EXPECTED_DROPS.items():
        assert {ident: scores[case][ident] for ident in drops} == drops, case
