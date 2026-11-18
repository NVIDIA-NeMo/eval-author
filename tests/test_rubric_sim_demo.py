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
    def eff(value: Any) -> int:
        return 3 if value == "N/A" else value

    assert all(eff(other[k]) <= eff(base[k]) for k in base), "a mutation must not raise any row"
    return {k for k in base if eff(other[k]) < eff(base[k])}


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


def test_base_scores_twenty_threes_and_one_trigger_na(base_evidence):
    base = _scores(base_evidence)
    assert base.pop("AUDIT-04-B") == "N/A"
    assert set(base.values()) == {3}


def test_real_scorer_exploit_drops_only_forbidden_credit_rows(base_evidence, tmp_path):
    mutated = run_demo.collect("approval-after-effect", tmp_path, use_agents=True, offline=True)
    assert _dropped(_scores(base_evidence), _scores(mutated)) == {"AUDIT-03", "AUDIT-04-B"}


def _with(ev: dict[str, Any], change) -> dict[str, Any]:
    changed = copy.deepcopy(ev)
    change(changed)
    return changed


@pytest.mark.parametrize(
    ("mutation", "change", "row"),
    [
        ("trace-secret", lambda ev: ev.update(trace_hits=1), "TRACE-04-A"),
        ("answer-exposure", lambda ev: ev.update(exposure_hits=["instruction.md"]), "AUDIT-05-B"),
        ("unpinned-image", lambda ev: ev["static"].update(image_pinned=False), "provenance.declared-configuration"),
        ("invalid-manifest", lambda ev: ev["harbor"].update(valid=False), "provenance.runtime-route"),
        (
            "stale-evidence",
            lambda ev: ev["evidence_bindings"].update(controls="0" * 64),
            "provenance.evidence-revision-consistency",
        ),
    ],
)
def test_each_demo_mutation_drops_only_its_row(base_evidence, mutation, change, row):
    assert _dropped(_scores(base_evidence), _scores(_with(base_evidence, change))) == {row}, mutation


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
