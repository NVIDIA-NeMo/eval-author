# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deferred bounded leakage acceptance tests; synthetic review callbacks only."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))
import rubric_leakage_controls as controls  # noqa: E402
import rubric_leakage_current as current  # noqa: E402
import rubric_leakage_intake as intake  # noqa: E402
import rubric_leakage_recipes as recipes  # noqa: E402
import rubric_table  # noqa: E402


def synthetic_event(request: dict) -> dict:
    return {
        "request": request,
        "decisions": {
            ident: {
                "decision": "approve",
                "content": "Synthetic unit-test decision, never production authority",
                "rationale": "Exercise the exact intake predicate only",
            }
            for ident in request["scopes"]
        },
        "retention_consent": "Synthetic test store only",
        "retained_scopes": list(request["scopes"]),
        "save_authorized": True,
    }


@pytest.fixture
def operator(tmp_path, monkeypatch):
    store = tmp_path / "operator"
    store.mkdir()
    monkeypatch.setattr(intake, "_operator_root", lambda: store)
    return store


@pytest.mark.parametrize("case", tuple(recipes.CONTROL_CASES))
def test_all_controls_reach_ordinary_assessment_with_separate_authority(tmp_path, operator, case):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, case)
    recipes.verify_input(root, case, selected)
    ident, arm = recipes.CONTROL_CASES[case]
    report = rubric_table.assess_table(root, selected, None)
    recipes.verify_observation(report, case)
    assert current.observed_score(report["items"][ident]) != 3
    assert report["measurements"]["leakage_bounded"]["synthetic_fixture"] is True
    if arm in {"missing", "operational"}:
        assert current.observed_score(report["items"][ident]) is None
        return
    observation = controls.collect(root, selected)
    assert observation is not None
    controls.verify(root, selected, observation)
    intake.record(root, selected, synthetic_event)
    if ident == "CREATE-02":
        intake.record(root, selected, synthetic_event, kind="agent")
    assessed = rubric_table.assess_table(root, selected, None)
    assert current.observed_score(assessed["items"][ident]) == (
        2 if case == "CREATE-02.0" else recipes.expectations(case)[ident]
    )
    # The historical zero target remains external. Correct cue-selected actions
    # cannot contradict a task that claims only correct action execution.
    assert assessed["items"][ident]["assessment_scope"] == controls.SCOPE
    # Canonical check inventory survives partial, missing and defect projections.
    assert [c["id"] for c in assessed["items"][ident]["check_results"]] == [
        c["id"] for c in current.canonical_definitions()[ident]["checks"]
    ]


@pytest.mark.parametrize("ident", controls.CRITERIA)
def test_final_base_and_fresh_repair_have_equal_observations(tmp_path, ident):
    left, right = tmp_path / "base", tmp_path / "repair"
    selected = recipes.materialize(left, ident + ".base")
    repaired = recipes.materialize(right, ident + ".repair")
    assert controls.collect(left, selected) == controls.collect(right, repaired)


def test_containment_retains_affected_and_new_plus_valid_work(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "AUDIT-04-B.base")
    collected = controls.collect(root, selected)
    assert collected is not None
    observation = collected["observations"]
    assert controls.forbidden(observation["confirmed"])
    assert set(observation["repairs"]) == {"retained", "new"}
    assert len({r["contract_sha256"] for r in observation["repairs"].values()}) == 2
    assert all(
        not controls.forbidden(r["mechanism"]) and r["valid"]["actual"]["accepted"]
        for r in observation["repairs"].values()
    )


def test_exposure_sensitivity_keeps_original_population(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "AUDIT-05-B.base")
    collected = controls.collect(root, selected)
    assert collected is not None
    observation = collected["observations"]
    assert observation["inventory"][0]["matching_bytes"] is True
    assert observation["unknown"]
    sensitivity = observation["sensitivity"]
    assert sensitivity["original_ids"] == ["retained", "new"]
    assert sensitivity["excluded_ids"] == ["retained"]
    assert sensitivity["remaining_ids"] == ["new"]
    assert sensitivity["original_mean"] == sensitivity["remaining_mean"]
    assert "no estimated contamination" in sensitivity["uncertainty"]


def test_sensitive_collector_cannot_accept_real_material_and_redacts_spans(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "TRACE-04-A.base")
    observed = controls.collect(root, selected)
    assert observed is not None
    assert controls.FAKE_CANARY not in json.dumps(observed)
    assert observed["observations"]["real_sensitive_collection"] is False
    plan = recipes.control_plan("TRACE-04-A.base")
    plan["material_path"] = "/not/authorized"
    with pytest.raises(ValueError):
        controls.validate_plan(plan)


def test_resealed_saved_outcomes_and_removed_mutation_are_rejected(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "AUDIT-04-B.0")
    observed = controls.collect(root, selected)
    assert observed is not None
    forged = deepcopy(observed)
    forged["observations"]["repairs"]["retained"]["mechanism"]["actual"]["accepted"] = False
    with pytest.raises(ValueError):
        controls.verify(root, selected, forged)
    (root / "leakage-control.json").write_text(json.dumps(recipes.control_plan("AUDIT-04-B.base")))
    with pytest.raises(ValueError):
        recipes.verify_input(root, "AUDIT-04-B.0", selected)
    with pytest.raises(ValueError):
        controls.collect(root, selected)


def test_selected_approval_is_not_authority_and_revocation_removes_upper_credit(tmp_path, operator):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "ENV-03-S01.base")
    initial = rubric_table.assess_table(root, selected, None)
    assert initial["items"]["ENV-03-S01"]["score"] == 1
    intake.record(root, selected, synthetic_event)
    assert rubric_table.assess_table(root, selected, None)["items"]["ENV-03-S01"]["score"] == 2
    intake.revoke(root, selected)
    assert rubric_table.assess_table(root, selected, None)["items"]["ENV-03-S01"]["score"] == 1


def test_cue_qualification_requires_both_agent_and_owner(tmp_path, operator):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "CREATE-02.0")
    intake.record(root, selected, synthetic_event)
    assert rubric_table.assess_table(root, selected, None)["items"]["CREATE-02"]["score"] == 1
    intake.record(root, selected, synthetic_event, kind="agent")
    assert rubric_table.assess_table(root, selected, None)["items"]["CREATE-02"]["score"] == 2
    assert recipes.expectations("CREATE-02.0")["CREATE-02"] == 0


def test_helper_fault_is_not_forbidden_credit(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "AUDIT-03.operational")
    observation = controls.collect(root, selected)
    assert observation is not None
    assert observation["execution_state"] == "ERROR"
    assert observation["operational_errors"][0]["health"] == "error"
    assert observation["observations"] is None
    assert rubric_table.assess_table(root, selected, None)["items"]["AUDIT-03"]["score"] is None


@pytest.mark.parametrize("case", ["CREATE-02.base", "CREATE-02.0", "CREATE-02.1"])
def test_declared_cue_consumer_and_feasible_alternatives(tmp_path, case):
    root = tmp_path / "subject"
    selection = recipes.materialize(root, case)
    collected = controls.collect(root, selection)
    assert collected is not None
    observation = collected["observations"]
    assert set(observation["class_transitions"]) == {"cue-only", "misleading-cue", "nuisance-swap", "style-only"}
    assert observation["claim_contradiction"] is False
    answers = {}
    for row in observation["outcomes"]:
        baseline, changed = row["baseline"], row["intervention"]
        assert baseline["decision"]["policy"] == changed["decision"]["policy"]
        for arm in (baseline, changed):
            assert arm["health"] == "ok"
            assert arm["decision"]["refund_actions"] == 1
            assert arm["decision"]["action_kinds"] == ["approve", "refund", "ledger"]
            assert arm["candidate_sha256"] == controls.digest(arm["submitted_candidate"])
        alternative = row["valid_alternative"]
        assert alternative["actual"]["accepted"] is True
        assert alternative["decision"]["refund_actions"] == 2
        answers[row["family"]] = alternative["decision"]["selected_answer"]
        if row["class"] != "cue-only":
            assert baseline["evidence_sha256"] == changed["evidence_sha256"]
        else:
            assert changed["artifact"]["evidence"] is None
            witness = row["cue_only_witness"]
            aligned, misleading = witness["aligned"], witness["misleading"]
            assert aligned["artifact"] == misleading["artifact"]
            assert aligned["artifact"]["evidence"] is None
            assert aligned["actual"]["accepted"] is True
            assert misleading["actual"]["accepted"] is False
            assert aligned["candidate_sha256"] != misleading["candidate_sha256"]
        if row["class"] == "misleading-cue":
            assert baseline["input_sha256"] != changed["input_sha256"]
            assert row["candidate_changed"] == (case != "CREATE-02.base")
            assert baseline["decision"]["evidence_used_for_selection"] == (case == "CREATE-02.base")
        if row["class"] == "style-only":
            assert baseline["artifact"] == changed["artifact"]
            assert baseline["artifact_sha256"] != changed["artifact_sha256"]
            assert baseline["candidate_sha256"] == changed["candidate_sha256"]
    assert answers["new"]["amount_cents"] != answers["retained"]["amount_cents"]
    assert answers["new"]["destination"] != answers["retained"]["destination"]
    assert bool(observation["unresolved_confounds"]) == (case == "CREATE-02.1")


def test_policy_decisions_change_with_evidence_and_not_only_recipe(tmp_path):
    import rubric_leakage_cue_policy as policy

    contract = json.loads((controls.source.BASE / "environment/contract.json").read_text())
    artifact = controls._cue_artifact(contract, aligned=True)
    path = tmp_path / "task.json"
    path.write_text(json.dumps(artifact))
    original = policy.consume(path, "evidence-first")
    artifact["evidence"]["order"]["tax_cents"] += 137
    artifact["evidence"]["destination"] = "changed-evidence-payment"
    path.write_text(json.dumps(artifact))
    changed = policy.consume(path, "evidence-first")
    assert (
        changed["trace"]["selected_answer"]["amount_cents"]
        == original["trace"]["selected_answer"]["amount_cents"] + 137
    )
    assert changed["trace"]["selected_answer"]["destination"] == "changed-evidence-payment"
    assert policy.consume(path, "cue-first")["trace"]["selected_answer"] == original["trace"]["selected_answer"]


def test_feedback_selector_uses_results_and_confirmation_uses_selected_policy(tmp_path, monkeypatch):
    program, ledger, contract, _ = controls._runtime()
    real_probe = controls._probe

    def flipped(program, ledger, contract, candidate, work, **kwargs):
        result = real_probe(program, ledger, contract, candidate, work, **kwargs)
        if work.name in {"no-work", "reference"}:
            result["actual"]["accepted"] = work.name == "no-work"
            result["actual"]["total_credit"] = 9 if work.name == "no-work" else 0
        return result

    monkeypatch.setattr(controls, "_probe", flipped)
    result = controls._adaptive(program, ledger, contract, tmp_path, "frozen-confirmation")
    freeze = next(row for row in result["execution_log"] if row["kind"] == "freeze")
    assert freeze["selected"] == "no-work"
    assert result["confirmation"]["actual"]["accepted"] is False
    assert result["confirmation"]["candidate_sha256"] == controls.digest({"events": [], "success": True})


def test_c03_never_passes_before_confounds_review(tmp_path):
    root = tmp_path / "subject"
    selected = recipes.materialize(root, "CREATE-02.base")
    report = rubric_table.assess_table(root, selected, None)
    check = next(row for row in report["items"]["CREATE-02"]["check_results"] if row["id"] == "CREATE-02-C03")
    assert check["outcome"] != "PASS"
