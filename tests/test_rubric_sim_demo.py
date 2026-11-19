# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tools/rubric_sim_demo"
sys.path.insert(0, str(DEMO))

import checks  # noqa: E402  # ty: ignore[unresolved-import]
import run_demo  # noqa: E402  # ty: ignore[unresolved-import]


def _canonical_ids() -> list[str]:
    refs = ROOT / "skills/eval-author-rubric/references"
    ids = []
    for name in ("leakage-and-credit", "task-contract-authority", "artifact-configuration-provenance"):
        ids += [item["id"] for item in json.loads((refs / f"{name}.json").read_text())["items"]]
    return ids


def _scores(ev: dict[str, Any]) -> dict[str, Any]:
    return {ident: row["score"] for ident, row in run_demo.score(ev).items()}


def _dropped(base: dict[str, Any], other: dict[str, Any]) -> set[str]:
    assert all(checks.rank(other[k]) <= checks.rank(base[k]) for k in base), "a mutation must not raise any row"
    return {k for k in base if checks.rank(other[k]) < checks.rank(base[k])}


@pytest.fixture(scope="module")
def base_evidence(tmp_path_factory) -> dict[str, Any]:
    work = tmp_path_factory.mktemp("sim-base")
    return run_demo.collect("base", work, use_agents=True, offline=True)


def test_each_canonical_row_has_exactly_one_check():
    assert sorted(c.criterion for c in checks.CHECKS) == sorted(_canonical_ids())


def test_controls_are_partitioned_without_overlap():
    owned = [cid for ids in checks.PARTITION.values() for cid in ids]
    assert len(owned) == len(set(owned))
    controls = json.loads((ROOT / "skills/eval-author-rubric/examples/refund/controls.json").read_text())["cases"]
    assert set(owned) == {row["id"] for row in controls}


def test_base_task_rows_reach_the_top_and_suite_rows_need_a_suite(base_evidence):
    base = _scores(base_evidence)
    task = {c.criterion: base[c.criterion] for c in checks.CHECKS if c.level == "task"}
    suite = {c.criterion: base[c.criterion] for c in checks.CHECKS if c.level == "suite"}
    assert task.pop("AUDIT-04-B") == checks.NA
    assert len(task) == 18 and set(task.values()) == {3}
    assert len(suite) == 4 and set(suite.values()) <= {checks.U, checks.NA}


@pytest.mark.parametrize(
    ("case", "target", "exact"),
    [
        # Real scorer exploit; held-out confirmation also stops corroborating, so AUDIT-05-S03 falls to 2.
        ("approval-after-effect", "AUDIT-03", {"AUDIT-03", "AUDIT-04-B", "AUDIT-04-B-SUITE"}),
        # Reworded text has no recipe to match: the contract reviewer must find the contradiction itself.
        ("contradictory-contract-reworded", "task-contract.clear-contract", None),
        ("style-advantage", "provenance.comparison-fairness", None),
    ],
)
def test_collected_mutations_are_detected_by_observation(base_evidence, tmp_path, case, target, exact):
    mutated = _scores(run_demo.collect(case, tmp_path, use_agents=True, offline=True))
    dropped = _dropped(_scores(base_evidence), mutated)
    assert mutated[target] == 0
    assert dropped == exact if exact else target in dropped


def _with(ev: dict[str, Any], change) -> dict[str, Any]:
    changed = copy.deepcopy(ev)
    change(changed)
    return changed


def _disagreeing_confirmation(ev: dict[str, Any]) -> None:
    for result in ev["heldout"].values():
        result["mismatch_total"] = True


@pytest.mark.parametrize(
    ("change", "row", "score", "also"),
    [
        (lambda ev: ev.update(trace_hits=1), "TRACE-04-A", 0, set()),
        (lambda ev: ev.update(exposure_hits=["instruction.md"]), "AUDIT-05-B", 0, set()),
        (lambda ev: ev["static"].update(image_pinned=False), "provenance.declared-configuration", 1, set()),
        (lambda ev: ev["harbor"].update(valid=False), "provenance.runtime-route", 0, set()),
        (
            lambda ev: ev["evidence_bindings"].update(controls="0" * 64),
            "provenance.evidence-revision-consistency",
            0,
            set(),
        ),
        # Disagreeing held-out results cost AUDIT-03 its top anchor; they are shared evidence
        # (checks.SHARED), so the other task rows that read them drop too.
        (
            _disagreeing_confirmation,
            "AUDIT-03",
            2,
            {"CREATE-02", "task-contract.clear-contract"},
        ),
        # The socket service and the file route must agree for observed configuration.
        (lambda ev: ev["ledger"]["totals"].update(reference=0), "provenance.observed-configuration", 1, set()),
    ],
    ids=[
        "trace-secret",
        "answer-exposure",
        "unpinned-image",
        "invalid-manifest",
        "stale-evidence",
        "heldout-disagrees",
        "service-disagrees",
    ],
)
def test_each_change_lowers_only_its_rows(base_evidence, change, row, score, also):
    changed = _scores(_with(base_evidence, change))
    assert changed[row] == score
    assert _dropped(_scores(base_evidence), changed) == {row} | also
    assert also <= {r for readers in checks.SHARED.values() for r in readers}


@pytest.mark.parametrize(
    ("change", "row"),
    [
        (lambda ev: ev.update(controls=None), "AUDIT-03"),
        (lambda ev: ev.update(policy=None), "task-contract.scoring-authority"),
        (lambda ev: ev["ledger"].update(ran=False, reason="refused"), "provenance.observed-configuration"),
        (lambda ev: ev.update(harbor={"valid": None, "invalid_manifest_rejected": None}), "provenance.runtime-route"),
    ],
    ids=["attack-panel-blocked", "no-owner", "service-not-observed", "no-provider-validator"],
)
def test_missing_evidence_is_unassessed_not_low(base_evidence, change, row):
    assert _scores(_with(base_evidence, change))[row] == checks.U


def test_without_independent_review_top_anchors_cap_at_two(base_evidence):
    scores = _scores(_with(base_evidence, lambda ev: ev.update(agents_enabled=False)))
    assert all(scores[k] == 2 for k in ("AUDIT-03", "CREATE-02", "task-contract.discoverable-requirements"))


def test_demo_mutations_change_the_task_bytes_they_claim(tmp_path):
    for mutation, path in (
        ("answer-exposure", "instruction.md"),
        ("unpinned-image", "environment/Dockerfile"),
        ("invalid-manifest", "task.toml"),
    ):
        task = tmp_path / mutation
        (task / "environment").mkdir(parents=True)
        for name in ("instruction.md", "environment/Dockerfile", "task.toml"):
            (task / name).write_bytes((run_demo.fx.BASE / name).read_bytes())
        run_demo._apply_demo_mutation(mutation, task)
        changed = {
            n
            for n in ("instruction.md", "environment/Dockerfile", "task.toml")
            if (task / n).read_bytes() != (run_demo.fx.BASE / n).read_bytes()
        }
        assert changed == {path}, mutation
