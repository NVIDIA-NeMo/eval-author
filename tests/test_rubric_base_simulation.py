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
    "approval-after-effect": {"task-contract.scoring-authority": 0, "AC-02": 0},
    "completion-only": {"provenance.grading-path": 0, "AC-02": 0, "AC-03": 0},
    "disabled-ledger": {"provenance.runtime-route": 0, "provenance.solver-tool-access": 0},
    "hidden-completion": {"task-contract.clear-contract": 0, "task-contract.discoverable-requirements": 1},
    "ledger-omission": {"AC-02": 0},
    "missing-contract": {"provenance.task-artifact-access": 0, "provenance.grading-dependency-access": 0},
    "tax-drift": {"provenance.external-state": 0},
    "no-independent-corroboration": {"provenance.grading-path": 2, "AC-02": 2},
    "heldout-attacks-unrun": {"AC-02": 2},
    "no-native-run": {"provenance.observed-configuration": None},
    "stale-evidence-combined": {"provenance.evidence-revision-consistency": 0},
    "blind-discovery-failed": {"task-contract.discoverable-requirements": 1},
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
    assert all(scores["base"][ident] == 3 for ident in summary["scored_criteria"])
    assert set(scores) == {"base", *EXPECTED_DROPS}
    for case, drops in EXPECTED_DROPS.items():
        assert {ident: scores[case][ident] for ident in drops} == drops, case
