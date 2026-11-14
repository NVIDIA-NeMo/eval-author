# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/rubric_evidence_scoring.py"


def module():
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    assert SCRIPT.is_file(), "source-bound canonical evidence scorer is missing"
    spec = importlib.util.spec_from_file_location("rubric_evidence_scoring", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_all_eighteen_definitions_preserve_canonical_anchors(tmp_path):
    scorer = module()
    definitions = scorer.canonical_definitions()
    assert len(definitions) == 18
    for name in ("artifact-configuration-provenance", "task-contract-authority"):
        source = json.loads((SCRIPT.parents[1] / f"references/{name}.json").read_text())
        for item in source["items"]:
            assert definitions[item["id"]]["scoring"] == item["scoring"]
    rows = scorer.assess_evidence(tmp_path, {})
    assert set(rows) == set(definitions)
    assert all(row["status"] == "NOT_ASSESSED" and row["score"] is None for row in rows.values())
    assert all(row["check_results"] and row["remaining_gaps"] for row in rows.values())


def add_artifact(root, selection, ident, document, role="evidence"):
    import hashlib

    raw = json.dumps(document).encode()
    (root / f"{ident}.json").write_bytes(raw)
    record = {
        "id": ident,
        "path": f"{ident}.json",
        "digest": hashlib.sha256(raw).hexdigest(),
        "access": "available",
        "role": role,
        "revision": "fixture-v1",
        "source": "explicit-fixture",
        "selection_origin": "explicit fixture",
        "evidence": [{"path": f"{ident}.json", "digest": hashlib.sha256(raw).hexdigest()}],
    }
    selection["artifacts"].append(record)
    return record


def packet(root, scorer, item_id, facts, *, synthetic=True, kind="agent"):
    selection = {"artifacts": []}
    task = add_artifact(root, selection, "task", {"requirements": ["output exact JSON"]}, "task")
    subject = {"artifact_id": "task", "digest": task["digest"]}
    source = {
        "schema": "rubric-fact-source-v1",
        "subject": subject,
        "definitions_digest": scorer.definitions_digest(),
        "provenance": {
            "kind": "synthetic" if synthetic else kind,
            "actor_id": "fixture-author",
            "activity": "review",
            "limitations": ["Synthetic unit fixture; not native execution."]
            if synthetic
            else ["Recorded review; identity not authenticated."],
        },
        "facts": facts,
    }
    add_artifact(root, selection, "facts", source)
    selected_facts = {
        name: {
            "basis": "recorded_judgment",
            "source": {"artifact_id": "facts", "pointer": f"/facts/{name}", "digest": scorer.value_digest(value)},
            "support": [
                {
                    "artifact_id": "task",
                    "pointer": "/requirements",
                    "digest": scorer.value_digest(["output exact JSON"]),
                }
            ],
        }
        for name, value in facts.items()
    }
    envelope = {
        "schema": "rubric-criterion-evidence-v1",
        "subject": subject,
        "definitions_digest": scorer.definitions_digest(),
        "assessment_scope": "synthetic-scoring-demonstration" if synthetic else "recorded-evidence-assessment",
        "items": [{"item_id": item_id, "facts": selected_facts}],
    }
    add_artifact(root, selection, "packet", envelope)
    selection["criterion_evidence"] = {"artifact_id": "packet"}
    return selection


@pytest.mark.parametrize("synthetic", [True, False])
def test_measure_cli_separates_demonstration_from_task_scores(tmp_path, synthetic):
    import subprocess

    scorer = module()
    selection = packet(tmp_path, scorer, "CREATE-04", facts_for("CREATE-04", 0), synthetic=synthetic)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(SCRIPT.with_name("measure.py")), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    if synthetic:
        assert report["criterion_demonstration"]["CREATE-04"]["score"] == 0
        assert "CREATE-04" not in report["items"]
    else:
        assert "criterion_demonstration" not in report
        assert report["items"]["CREATE-04"]["score"] is None
        assert report["items"]["CREATE-04"]["evidence_strength"] == "E0"
    assert report["preflight_success"] is False


@pytest.mark.parametrize("target", ["packet", "task", "facts"])
@pytest.mark.parametrize("access", ["denied", "missing", "unresolved"])
def test_selection_wide_alias_restrictions_precede_reads(tmp_path, monkeypatch, target, access):
    scorer = module()
    selection = packet(tmp_path, scorer, "DISC-01-A", facts_for("DISC-01-A", 2))
    record = next(r for r in selection["artifacts"] if r["id"] == target)
    selection["artifacts"].append({**record, "id": "restricted-alias", "access": access})
    original = scorer.read_selected
    reads = []

    def tracked_read(root, path):
        reads.append(path)
        return original(root, path)

    monkeypatch.setattr(scorer, "read_selected", tracked_read)
    with pytest.raises(ValueError, match="unavailable"):
        scorer.assess_evidence(tmp_path, selection)
    assert record["path"] not in reads


def test_source_bound_access_rows_have_explicit_demo_scope(tmp_path):
    scorer = module()
    selection = packet(
        tmp_path,
        scorer,
        "DISC-01-A",
        {
            "task_artifacts": [
                {"id": "instructions", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
                {"id": "fixture", "access": "missing", "revision": None, "inspection_effect": "blocks"},
            ]
        },
    )
    row = scorer.assess_evidence(tmp_path, selection)["DISC-01-A"]
    assert row["score"] == 0
    assert row["assessment_scope"] == "synthetic-scoring-demonstration"
    assert row["evidence_strength"] == "E1"
    assert row["score_provenance"]["kind"] == "synthetic-anchor-application"
    assert row["measurements"]["task_artifacts"]["denominator"] == 2
    assert row["fact_results"][0]["basis"] == "recorded_judgment"
    assert row["reviewer_authenticated"] is False
    assert row["native_execution_verified"] is False
    assert all(r["score"] is None for k, r in scorer.assess_evidence(tmp_path, selection).items() if k != "DISC-01-A")


# Literal source-row vectors, deliberately not generated from production policy.
VECTORS = {
    "DISC-01-A": (
        "task_artifacts",
        {"id": "a", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
        {"access": "missing", "inspection_effect": "blocks"},
        {"revision": None},
    ),
    "DISC-01-B": (
        "grading_routes",
        {"id": "a", "declared_scorer": "s", "selected_scorer": "s", "configuration": "v1", "branch": "resolved"},
        {"selected_scorer": "other"},
        {"branch": "ambiguous"},
    ),
    "DISC-01-C": (
        "grading_dependencies",
        {
            "id": "a",
            "access": "accessible",
            "revision": "v1",
            "inspection_effect": "none",
            "reference_review": "resolved",
        },
        {"access": "denied", "inspection_effect": "blocks"},
        {"reference_review": "unresolved"},
    ),
    "DISC-02-A": (
        "configuration_fields",
        {
            "id": "model",
            "value": "model-v1",
            "source_identity": "config-v1",
            "availability": "sourced",
            "claim_limit": "none",
            "reported_value": "model-v1",
        },
        {"reported_value": "wrong", "claim_limit": "uninterpretable"},
        {"source_identity": None},
    ),
    "DISC-02-B": (
        "run_settings",
        {"id": "model", "declared": "m1", "observed": "m1", "run_identity": "run1", "disposition": "agrees"},
        {"observed": "m2", "disposition": "invalidates"},
        {"observed": None, "disposition": "unresolved"},
    ),
    "DISC-03-A": (
        "provider_validation",
        {
            "id": "route",
            "provider": "harbor",
            "adapter_revision": "v1",
            "manifest_identity": "m1",
            "validation": "accepted",
            "adapter": "compatible",
        },
        {"validation": "incompatible"},
        {"adapter": "unresolved"},
    ),
    "AUD-02": (
        "setup_differences",
        {
            "id": "budget",
            "left": "100",
            "right": "200",
            "rationale": "Scope permits accommodations",
            "disposition": "justified",
            "advantage": "none",
        },
        {"disposition": "unjustified", "advantage": "demonstrated"},
        {"disposition": "disputed"},
    ),
    "ENV-01": (
        "tool_probes",
        {
            "id": "read",
            "action": "read",
            "solver_identity": "solver",
            "expected": "allowed",
            "actual": "allowed",
            "probe": "call",
            "execution": "completed",
        },
        {"actual": "denied"},
        {"probe": "schema", "execution": "partial"},
    ),
    "ENV-02-A": (
        "components",
        {
            "id": "tool",
            "form": "real",
            "observed_revision": "v1",
            "approved_revision": None,
            "authorization": "not_required",
            "availability": "available",
        },
        {"availability": "missing"},
        {"availability": "partly_reconciled"},
    ),
    "ENV-03": (
        "external_state",
        {
            "id": "dataset",
            "captured_identity": "sha256-v1",
            "mode": "pinned",
            "live_bound": None,
            "observation": "within_bound",
            "outcome_relation": "interpretable",
        },
        {"observation": "drifted", "outcome_relation": "invalidated"},
        {"captured_identity": None, "observation": "capture_gap"},
    ),
    "CREATE-07": (
        "result_revisions",
        {
            "id": "result",
            "recorded_revision": "v1",
            "current_revision": "v1",
            "change": "unchanged",
            "disposition": "current",
            "lineage": "original",
        },
        {"current_revision": "v2", "change": "material", "disposition": "combined"},
        {"current_revision": "v2", "change": "material", "disposition": "stale"},
    ),
    "CREATE-03": (
        "contract_requirements",
        {
            "id": "format",
            "public_requirement": "JSON",
            "scorer_requirement": "JSON",
            "interpretation": "public",
            "resolution": "resolved",
        },
        {"scorer_requirement": "CSV", "resolution": "prevents_grading"},
        {"resolution": "ambiguous"},
    ),
    "CREATE-04": (
        "scored_conditions",
        {
            "id": "format",
            "condition": "JSON",
            "authority": "JSON",
            "solver_access": "accessible",
            "mapping": "supported",
        },
        {"authority": None, "solver_access": "denied", "mapping": "hidden"},
        {"mapping": "disputed"},
    ),
    "CREATE-05-A": (
        "scoring_authorities",
        {
            "id": "answer",
            "expected": "42",
            "authoritative": "42",
            "obligation": "exact result",
            "semantics": "resolved",
        },
        {"authoritative": "43", "semantics": "contradicted"},
        {"semantics": "disputed"},
    ),
    "AC-01": (
        "information_surfaces",
        {
            "id": "answer",
            "contract_role": "protected",
            "solver_read": "denied",
            "permitted_neighbor": "readable",
            "exposure": "none",
            "review": "complete",
        },
        {"solver_read": "readable", "exposure": "unintended"},
        {"review": "partial"},
    ),
    "AC-02": (
        "credit_controls",
        {
            "id": "shortcut",
            "class": "attack",
            "expected_credit": 0,
            "actual_credit": 0,
            "health": "healthy",
            "reach": "reached",
            "applicability": "applicable",
        },
        {"actual_credit": 1},
        {"reach": "unresolved"},
    ),
    "AC-03": (
        "receipt_controls",
        {
            "id": "stale",
            "receipt_kind": "stale",
            "consumer": "rejected",
            "native_validation": "invalid",
            "health": "healthy",
            "reach": "reached",
        },
        {"consumer": "credited"},
        {"reach": "unresolved"},
    ),
    "AC-04": (
        "reset_surfaces",
        {
            "id": "cache",
            "sharing": "isolated",
            "expected_state": "empty",
            "reset_state": "empty",
            "target_alone": "0",
            "target_after": "0",
            "consequence": "none",
            "inspection": "complete",
        },
        {"reset_state": "answer", "consequence": "answer_exposure"},
        {"inspection": "partial"},
    ),
}


def facts_for(ident, variant):
    name, base, defect, partial = VECTORS[ident]
    row = {**base, **({0: defect, 1: partial}.get(variant, {}))}
    rows = [row]
    if variant == 1 and ident in ("DISC-02-B", "ENV-03", "CREATE-05-A"):
        rows.append({**base, "id": "established-neighbor"})
    if ident == "AC-02":
        rows.extend(
            {**base, "id": kind, "class": kind, "expected_credit": credit, "actual_credit": credit}
            for kind, credit in (("reference", 1), ("incorrect", 0), ("valid_alternative", 1))
        )
    if ident == "AC-03":
        rows.extend(
            [
                {**base, "id": "forged", "receipt_kind": "forged"},
                {
                    **base,
                    "id": "valid",
                    "receipt_kind": "current",
                    "consumer": "credited",
                    "native_validation": "valid",
                },
            ]
        )
    return {name: rows}


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("anchor", [0, 1, 2])
def test_all_criterion_specific_low_partial_acceptance_relations(tmp_path, ident, anchor):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, facts_for(ident, anchor))
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["score"] == anchor
    assert row["assessment_scope"] == "synthetic-scoring-demonstration"
    assert row["evidence_strength"] == "E1"


@pytest.mark.parametrize("ident", VECTORS)
def test_missing_inventory_is_unknown_not_a_defect(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {VECTORS[ident][0]: None})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] is None


ROBUSTNESS = {
    "DISC-01-A": "inventory_reconstruction",
    "DISC-01-B": "route_reconstruction",
    "DISC-01-C": "dependency_reconstruction",
    "DISC-02-A": "manifest_reconstruction",
    "DISC-02-B": "configuration_repeat",
    "DISC-03-A": "compatibility_controls",
    "AUD-02": "matched_sensitivity",
    "ENV-01": "clean_access_controls",
    "ENV-02-A": "provisioning_reconciliation",
    "ENV-03": "replay_drift",
    "CREATE-07": "change_injection",
    "CREATE-03": "boundary_solutions",
    "CREATE-04": "blind_discovery",
    "CREATE-05-A": "authority_corroboration",
    "AC-01": "heldout_access",
    "AC-02": "heldout_credit",
    "AC-03": "consumer_boundary",
    "AC-04": "trial_order_controls",
}


def robustness_for(ident):
    # All requested control roles must be present, not merely one green receipt.
    roles = {
        "DISC-01-A": ["inventory"],
        "DISC-01-B": ["selection"],
        "DISC-01-C": ["coverage", "access"],
        "DISC-02-A": ["manifest", "limitations"],
        "DISC-02-B": ["configuration", "control"],
        "DISC-03-A": ["clean_setup", "invalid_manifest"],
        "AUD-02": ["matched_setup", "sensitivity"],
        "ENV-01": ["reset", "error", "boundary"],
        "ENV-02-A": ["provisioning", "approval"],
        "ENV-03": ["replay", "deliberate_drift"],
        "CREATE-07": ["changed_invalidated", "unchanged_preserved", "lineage_preserved"],
        "CREATE-03": ["independent_solution", "valid_boundary"],
        "CREATE-04": ["blind_discovery", "solver_access"],
        "CREATE-05-A": ["reference", "scoring_contract"],
        "AC-01": ["independent_path", "heldout", "permitted_neighbor"],
        "AC-02": ["heldout_attack", "valid_alternative"],
        "AC-03": ["invalid_rejection", "valid_acceptance"],
        "AC-04": ["order", "session"],
    }[ident]
    outcomes = {
        "DISC-01-A": ["inventory_reconstructed"],
        "DISC-01-B": ["route_reconstructed"],
        "DISC-01-C": ["dependencies_complete", "dependencies_accessible"],
        "DISC-02-A": ["manifest_reconstructed", "limitations_preserved"],
        "DISC-02-B": ["configuration_reproduced", "configuration_control_agrees"],
        "DISC-03-A": ["clean_setup_accepted", "invalid_manifest_rejected"],
        "AUD-02": ["matched_setup_comparable", "sensitivity_bounded"],
        "ENV-01": ["reset_preserved_access", "error_contract_preserved", "access_boundary_preserved"],
        "ENV-02-A": ["provisioning_reconciled", "substitutions_authorized"],
        "ENV-03": ["replay_interpretable", "drift_detected"],
        "CREATE-07": ["changed_result_invalidated", "unchanged_result_preserved", "lineage_preserved"],
        "CREATE-03": ["public_solution_accepted", "valid_boundary_accepted"],
        "CREATE-04": ["conditions_discovered", "authority_accessible"],
        "CREATE-05-A": ["reference_agrees", "scoring_contract_agrees"],
        "AC-01": ["protected_read_denied", "heldout_read_denied", "permitted_read_allowed"],
        "AC-02": ["forbidden_credit_withheld", "valid_alternative_credited"],
        "AC-03": ["invalid_receipt_rejected", "valid_receipt_credited"],
        "AC-04": ["order_isolation_preserved", "session_isolation_preserved"],
    }[ident]
    return {
        ROBUSTNESS[ident]: [
            {
                "id": role,
                "role": role,
                "expected": outcome,
                "observed": outcome,
                "original_actor": "author-a",
                "corroborating_actor": "reviewer-b",
                "source_identity": f"independent-{role}",
                "execution": "completed",
                "scope": "same-task-revision",
            }
            for role, outcome in zip(roles, outcomes, strict=True)
        ]
    }


@pytest.mark.parametrize("ident", VECTORS)
def test_additional_scoped_controls_derive_three_without_inventing_e3(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robustness_for(ident)})
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["score"] == 3
    assert row["evidence_strength"] == "E1"
    assert row["native_execution_verified"] is False
    assert row["reviewer_authenticated"] is False


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("outcome", ["arbitrary-equal-text", "forged receipt credited"])
def test_robustness_rejects_untyped_equal_assertions(tmp_path, ident, outcome):
    scorer = module()
    robust = robustness_for(ident)
    for control in next(iter(robust.values())):
        control.update(expected=outcome, observed=outcome)
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    with pytest.raises(ValueError, match="robustness outcome"):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize(
    "ident,role,outcome",
    [
        ("AC-01", "independent_path", "protected_read_allowed"),
        ("AC-02", "heldout_attack", "forbidden_credit_awarded"),
        ("AC-03", "invalid_rejection", "invalid_receipt_credited"),
        ("AC-04", "order", "order_carryover_observed"),
    ],
)
def test_completed_scoped_corroboration_defect_overrides_acceptance(tmp_path, ident, role, outcome):
    scorer = module()
    robust = robustness_for(ident)
    control = next(r for r in next(iter(robust.values())) if r["role"] == role)
    control["observed"] = outcome
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 0


@pytest.mark.parametrize("ident", VECTORS)
def test_failed_corroboration_cannot_preserve_full_acceptance(tmp_path, ident):
    scorer = module()
    for role, (_, failure) in scorer.ROBUSTNESS_OUTCOMES[ident].items():
        robust = robustness_for(ident)
        control = next(r for r in next(iter(robust.values())) if r["role"] == role)
        control["observed"] = failure
        selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
        assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] in (0, 1)


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("state", ["unrun", "error", "other-revision", "missing", "failed-expectation"])
def test_incomplete_or_misdeclared_corroboration_never_qualifies(tmp_path, ident, state):
    scorer = module()
    robust = robustness_for(ident)
    control = next(iter(robust.values()))[0]
    _, failure = scorer.ROBUSTNESS_OUTCOMES[ident][control["role"]]
    control["observed"] = failure
    if state in ("unrun", "error"):
        control["execution"] = state
    elif state == "other-revision":
        control["scope"] = state
    elif state == "missing":
        control["observed"] = None
    else:
        control["expected"] = failure
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    if state == "failed-expectation":
        with pytest.raises(ValueError, match="robustness outcome"):
            scorer.assess_evidence(tmp_path, selection)
    else:
        assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 2


@pytest.mark.parametrize("ident", VECTORS)
def test_robust_controls_never_erase_a_demonstrated_defect(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 0), **robustness_for(ident)})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 0


@pytest.mark.parametrize("ident", VECTORS)
def test_same_actor_is_not_independent_even_in_demo(tmp_path, ident):
    scorer = module()
    robust = robustness_for(ident)
    for row in next(iter(robust.values())):
        row["corroborating_actor"] = row["original_actor"]
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == (3 if ident in ("AUD-02", "CREATE-07") else 2)


def rewrite(root, selection, ident, mutate):
    import hashlib

    target = next(r for r in selection["artifacts"] if r["id"] == ident)
    doc = json.loads((root / target["path"]).read_text())
    mutate(doc)
    raw = json.dumps(doc).encode()
    (root / target["path"]).write_bytes(raw)
    target["digest"] = hashlib.sha256(raw).hexdigest()
    target["evidence"][0]["digest"] = target["digest"]


@pytest.mark.parametrize(
    "mutation",
    [
        "packet_score",
        "unknown_fact",
        "duplicate_item",
        "unknown_item",
        "stale_definitions",
        "wrong_subject",
        "missing_source",
        "bad_pointer",
        "bad_content_digest",
        "empty_support",
        "missing_support",
        "human_true",
        "unknown_author",
        "missing_limits",
        "promote_demo",
        "review_as_observation",
        "same_source_as_support",
        "wrong_fact_pointer",
        "contradiction",
    ],
)
def test_invalid_or_unbound_evidence_is_rejected(tmp_path, mutation):
    scorer = module()
    selection = packet(tmp_path, scorer, "DISC-01-B", facts_for("DISC-01-B", 2))
    field = "grading_routes"
    if mutation == "packet_score":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0].update(score=3))
    elif mutation == "unknown_fact":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"].update(satisfies_anchor=True))
    elif mutation == "duplicate_item":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"].append(deepcopy(d["items"][0])))
    elif mutation == "unknown_item":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0].update(item_id="AC-05"))
    elif mutation == "stale_definitions":
        rewrite(tmp_path, selection, "packet", lambda d: d.update(definitions_digest="0" * 64))
    elif mutation == "wrong_subject":
        rewrite(tmp_path, selection, "facts", lambda d: d["subject"].update(digest="0" * 64))
    elif mutation == "missing_source":
        (tmp_path / "facts.json").unlink()
    elif mutation == "bad_pointer":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field]["source"].update(pointer="/not_there"),
        )
    elif mutation == "bad_content_digest":
        rewrite(
            tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field]["source"].update(digest="0" * 64)
        )
    elif mutation == "empty_support":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field].update(support=[]))
    elif mutation == "missing_support":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field]["support"][0].update(artifact_id="absent"),
        )
    elif mutation == "human_true":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(human=True))
    elif mutation == "unknown_author":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(kind="authenticated-human"))
    elif mutation == "missing_limits":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(limitations=[]))
    elif mutation == "promote_demo":
        rewrite(tmp_path, selection, "packet", lambda d: d.update(assessment_scope="recorded-evidence-assessment"))
    elif mutation == "review_as_observation":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field].update(basis="json_observation"))
    elif mutation == "same_source_as_support":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field].update(support=[deepcopy(d["items"][0]["facts"][field]["source"])]),
        )
    elif mutation == "wrong_fact_pointer":
        rewrite(tmp_path, selection, "facts", lambda d: d.update(copy=d["facts"][field]))
        rewrite(
            tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field]["source"].update(pointer="/copy")
        )
    elif mutation == "contradiction":
        bad = facts_for("DISC-01-B", 2)
        bad[field].append({**bad[field][0], "selected_scorer": "different"})
        selection = packet(tmp_path, scorer, "DISC-01-B", bad)
    with pytest.raises(ValueError):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("kind", ["agent", "human", "tool"])
def test_recorded_authorship_does_not_authenticate_or_grant_real_scores(tmp_path, ident, kind):
    scorer = module()
    selection = packet(
        tmp_path, scorer, ident, {**facts_for(ident, 2), **robustness_for(ident)}, synthetic=False, kind=kind
    )
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["status"] == "NOT_ASSESSED"
    assert row["score"] is None
    assert row["evidence_strength"] == "E0"
    assert row["reviewer_authenticated"] is False
    assert row["native_execution_verified"] is False


@pytest.mark.parametrize("mutation", ["symlink", "parent_symlink", "oversize", "digest", "duplicate_json", "nan"])
def test_source_reader_fails_closed(tmp_path, mutation):
    scorer = module()
    selection = packet(tmp_path, scorer, "DISC-01-A", facts_for("DISC-01-A", 2))
    source = tmp_path / "facts.json"
    record = next(r for r in selection["artifacts"] if r["id"] == "facts")
    if mutation == "symlink":
        source.rename(tmp_path / "real.json")
        source.symlink_to(tmp_path / "real.json")
    elif mutation == "parent_symlink":
        (tmp_path / "real").mkdir()
        source.rename(tmp_path / "real/facts.json")
        (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
        record["path"] = "linked/facts.json"
    else:
        raw = {"oversize": b" " * 1_048_577, "digest": b"{}", "duplicate_json": b'{"a":1,"a":2}', "nan": b'{"a":NaN}'}[
            mutation
        ]
        source.write_bytes(raw)
        if mutation != "digest":
            import hashlib

            record["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize("with_neighbor", [False, True])
def test_justified_setting_without_observation_stays_unknown(tmp_path, with_neighbor):
    scorer = module()
    facts = facts_for("DISC-02-B", 2)
    rows = facts["run_settings"]
    if with_neighbor:
        rows.append({**rows[0], "id": "observed-neighbor"})
    rows[0].update(observed=None, disposition="justified")
    selection = packet(tmp_path, scorer, "DISC-02-B", facts)
    assert scorer.assess_evidence(tmp_path, selection)["DISC-02-B"]["score"] == (1 if with_neighbor else None)


@pytest.mark.parametrize("with_neighbor", [False, True])
def test_completed_probe_without_actual_outcome_stays_unknown(tmp_path, with_neighbor):
    scorer = module()
    facts = facts_for("ENV-01", 2)
    rows = facts["tool_probes"]
    if with_neighbor:
        rows.append({**rows[0], "id": "observed-neighbor"})
    rows[0]["actual"] = None
    selection = packet(tmp_path, scorer, "ENV-01", facts)
    assert scorer.assess_evidence(tmp_path, selection)["ENV-01"]["score"] == (1 if with_neighbor else None)


@pytest.mark.parametrize(
    "patch",
    [
        {"reset_state": None, "inspection": "unrun", "consequence": "answer_exposure"},
        {"reset_state": None, "inspection": "complete", "consequence": "answer_exposure"},
        {"reset_state": "answer", "inspection": "unrun", "consequence": "answer_exposure"},
        {"target_alone": None, "target_after": "1", "inspection": "unrun", "consequence": "credit_change"},
        {"target_alone": "0", "target_after": None, "inspection": "complete", "consequence": "credit_change"},
    ],
)
def test_reset_defect_requires_observed_state_or_paired_credit(tmp_path, patch):
    scorer = module()
    facts = facts_for("AC-04", 2)
    facts["reset_surfaces"][0].update(patch)
    selection = packet(tmp_path, scorer, "AC-04", facts)
    assert scorer.assess_evidence(tmp_path, selection)["AC-04"]["score"] is None


@pytest.mark.parametrize("semantics", ["resolved", "disputed", "contradicted"])
def test_authority_mismatch_is_derived_not_overridden_by_semantics(tmp_path, semantics):
    scorer = module()
    facts = facts_for("CREATE-05-A", 2)
    facts["scoring_authorities"][0].update(authoritative="43", semantics=semantics)
    selection = packet(tmp_path, scorer, "CREATE-05-A", facts)
    assert scorer.assess_evidence(tmp_path, selection)["CREATE-05-A"]["score"] == 0


def test_private_hidden_authority_is_a_defect_even_when_identified(tmp_path):
    scorer = module()
    facts = facts_for("CREATE-04", 2)
    facts["scored_conditions"][0].update(authority="private-grader-rule", solver_access="denied", mapping="hidden")
    selection = packet(tmp_path, scorer, "CREATE-04", facts)
    assert scorer.assess_evidence(tmp_path, selection)["CREATE-04"]["score"] == 0


@pytest.mark.parametrize("ident", ["DISC-02-B", "ENV-03", "CREATE-05-A"])
def test_partial_requires_some_established_scope_not_just_an_asserted_gap(tmp_path, ident):
    scorer = module()
    name, base, _, partial = VECTORS[ident]
    selection = packet(tmp_path, scorer, ident, {name: [{**base, **partial}]})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] is None


def test_fact_read_is_revalidated_after_projection(tmp_path, monkeypatch):
    scorer = module()
    selection = packet(tmp_path, scorer, "DISC-01-A", facts_for("DISC-01-A", 2))
    original = scorer._derive

    def change_after_read(ident, facts):
        result = original(ident, facts)
        (tmp_path / "facts.json").write_text("{}")
        return result

    monkeypatch.setattr(scorer, "_derive", change_after_read)
    with pytest.raises(ValueError, match="changed during assessment"):
        scorer.assess_evidence(tmp_path, selection)


def test_definition_binding_includes_supplemental_evidence_floors(tmp_path, monkeypatch):
    scorer = module()
    import shutil

    refs = tmp_path / "references"
    shutil.copytree(scorer.REFERENCES, refs)
    before = scorer.definitions_digest()
    monkeypatch.setattr(scorer, "REFERENCES", refs)
    assert scorer.definitions_digest() == before
    path = refs / "task-contract-authority.md"
    path.write_text(path.read_text() + "\nChanged adjudication policy.\n")
    assert scorer.definitions_digest() != before


@pytest.mark.parametrize("bad", [True, 1, "human=true", ["not-a-row"], {"score": 3}])
def test_fact_inventory_rejects_booleans_scalars_and_verdict_objects(tmp_path, bad):
    scorer = module()
    selection = packet(tmp_path, scorer, "CREATE-03", {"contract_requirements": bad})
    with pytest.raises(ValueError):
        scorer.assess_evidence(tmp_path, selection)


def test_zero_and_partial_merit_do_not_change_evidence_strength(tmp_path):
    scorer = module()
    strengths = []
    for score in (0, 1, 2, 3):
        facts = facts_for("CREATE-03", min(score, 2))
        if score == 3:
            facts.update(robustness_for("CREATE-03"))
        selection = packet(tmp_path, scorer, "CREATE-03", facts)
        result = scorer.assess_evidence(tmp_path, selection)["CREATE-03"]
        strengths.append(result["evidence_strength"])
    assert strengths == ["E1"] * 4


@pytest.mark.parametrize(
    "ident,patch",
    [
        ("DISC-02-B", {"observed": "different", "disposition": "agrees"}),
        ("CREATE-07", {"current_revision": "other", "change": "unchanged"}),
        ("AC-01", {"solver_read": "denied", "exposure": "unintended"}),
        ("ENV-02-A", {"form": "real", "authorization": "approved", "approved_revision": "v1"}),
    ],
)
def test_internally_contradictory_observations_are_rejected(tmp_path, ident, patch):
    scorer = module()
    facts = facts_for(ident, 2)
    next(iter(facts.values()))[0].update(patch)
    selection = packet(tmp_path, scorer, ident, facts)
    with pytest.raises(ValueError, match="contradictory"):
        scorer.assess_evidence(tmp_path, selection)
