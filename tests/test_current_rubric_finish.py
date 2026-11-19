# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

# Retained private run artifacts live beside the checkout, as current_rubric_finish.RUNS expects.
PRIVATE_RUNS = Path(__file__).resolve().parents[2] / "rubric-guided-runs"
requires_private_runs = pytest.mark.skipif(
    not PRIVATE_RUNS.is_dir(), reason="requires retained private rubric-guided-runs artifacts"
)


@pytest.fixture
def finish(monkeypatch):
    directory = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric"
    monkeypatch.syspath_prepend(str(directory))
    return importlib.import_module("current_rubric_finish")


def fixture_value(f):
    ids = (*f.SCOPED, f.REVISION, "neighbor")
    report = {
        "items": {
            i: {
                "score": None if i in {"TRACE-04-A", "AUDIT-05-S03", "AUDIT-04-B"} else 1,
                "check_results": [{"outcome": "NOT_RUN", "evidence_refs": []}],
                "evidence_refs": [],
                "remaining_gaps": [],
                "findings": ["preserve finding"],
            }
            for i in ids
        },
        "measurements": {"native": {"unchanged": True}},
    }
    questions = dict(
        zip(f.SCOPED, ("OBS-AUDIT03", "OBS-ENV03", "OBS-AUDIT04B", "OBS-TRACE04A", "OBH-Q1", "OBH-Q2"), strict=True)
    )
    requests = [{"question_id": q, "binding": q, "scopes": {i: "exact question"}} for i, q in questions.items()]
    value = {
        "report_sha256": f.digest(report),
        "audit_report": deepcopy(report),
        "requests": requests,
        "receipts": dict.fromkeys(questions.values()),
        "observations": {
            "native": {"classes": [{"per_check_total_match": True}], "sensitivity": {"unexposed_subject_count": 0}},
            "trace": {"eligible_count": 1},
            "history": {"confirmation_contract": {"exploratory": True}},
        },
    }
    return report, value


def test_wrong_population_and_extra_artifact_rejected(finish, tmp_path):
    selection = {"artifacts": []}
    with pytest.raises(ValueError, match="population"):
        finish.check_population(tmp_path, selection, {"selection": selection})
    with pytest.raises(ValueError, match="population"):
        finish.check_population(finish.ROOT, {"artifacts": [{"id": "extra"}]}, {"selection": selection})


def test_typed_source_identity_and_alias_rejection(finish, tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"original")
    expected = finish.hashlib.sha256(b"original").hexdigest()
    assert finish.pin(path, expected) == b"original"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        finish.pin(path, expected)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="unreadable"):
        finish.raw(alias)
    assert finish.digest({"a": True}) != finish.digest({"a": 1})


def test_unanswered_is_not_consent_and_neighbors_remain_equal(finish):
    report, value = fixture_value(finish)
    original = deepcopy(report)
    output = finish.project(report, value)
    assert output["items"]["AUDIT-03"]["score"] == 1
    assert output["items"]["AUDIT-04-B"]["score"] is None
    assert output["items"]["neighbor"] == report["items"]["neighbor"]
    assert output["measurements"]["native"] == report["measurements"]["native"]
    assert report == original
    assert output["items"]["TRACE-04-A"]["score"] == 1  # evidenced partial review, not authorization
    assert all(r["findings"] == ["preserve finding"] for r in output["items"].values())


@pytest.mark.parametrize("decision,revoked", [("reject", False), ("defer", False), ("approve", True)])
def test_refusal_defer_revocation_never_promotes(finish, decision, revoked):
    report, value = fixture_value(finish)
    value["receipts"]["OBS-AUDIT03"] = {
        "revoked": revoked,
        "event": {"decisions": {"AUDIT-03": {"decision": decision}}},
    }
    assert finish.project(report, value)["items"]["AUDIT-03"]["score"] == 1


def test_actual_event_read_requires_exact_request_and_retention(finish, monkeypatch, tmp_path):
    store = tmp_path / "store"
    root = tmp_path / "subject"
    store.mkdir()
    root.mkdir()
    monkeypatch.setattr(finish, "_operator_root", lambda: store)
    request = {"binding": "a" * 64, "scopes": {"AUDIT-03": "test only"}}
    assert finish.read_decision(request, root, {"artifacts": []}) is None
    event = {
        "request": request,
        "decisions": {"AUDIT-03": {"decision": "approve", "content": "synthetic test", "rationale": "test only"}},
        "retained_scopes": ["AUDIT-03"],
        "retention_consent": "test only",
        "save_authorized": True,
    }
    target = store / (request["binding"] + ".original-finish.json")
    document = {"schema": "current-original-finish-review-v1", "trust_basis": finish.OPERATOR_BOUNDARY, "event": event}
    target.write_text(json.dumps(document))
    assert finish.read_decision(request, root, {"artifacts": []})["authenticated"] is False
    wrong_request = deepcopy(request)
    wrong_request["scopes"] = {"AUDIT-03": "different scope with same filename"}
    with pytest.raises(ValueError):
        finish.read_decision(wrong_request, root, {"artifacts": []})
    target.with_suffix(".revoked").write_text("revoked")
    assert finish.read_decision(request, root, {"artifacts": []})["revoked"] is True
    event["save_authorized"] = False
    target.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        finish.read_decision(request, root, {"artifacts": []})


def test_final_readback_changes_abort_without_mutation(finish, monkeypatch):
    report, value = fixture_value(finish)
    original = deepcopy(report)
    changed = deepcopy(value)
    changed["receipts"]["OBS-AUDIT03"] = {"revoked": True}
    monkeypatch.setattr(finish, "validate", lambda *args: changed)
    with pytest.raises(ValueError, match="changed during projection"):
        finish.apply(report, value, root=finish.ROOT, selection={})
    assert report == original


def test_unsupported_subject_uses_ordinary_path(finish, monkeypatch, tmp_path):
    sentinel = {"sentinel": True}
    monkeypatch.setattr(finish.audit_adapter.table, "assess_table", lambda *args: sentinel)
    monkeypatch.setattr(finish, "packet_inputs", lambda: {"selection": {"artifacts": []}})
    with finish.table_hook():
        assert finish.audit_adapter.table.assess_table(tmp_path, {"artifacts": []}, None) is sentinel
    assert finish.audit_adapter.table.assess_table(tmp_path, {}, None) is sentinel


def test_unsupported_subject_does_not_read_private_packets(finish, monkeypatch, tmp_path):
    sentinel = {"ordinary": True}
    monkeypatch.setattr(finish.audit_adapter.table, "assess_table", lambda *args: sentinel)

    def forbidden():
        raise AssertionError("unsupported root read private packet")

    monkeypatch.setattr(finish, "packet_inputs", forbidden)
    with finish.table_hook():
        assert finish.audit_adapter.table.assess_table(tmp_path, {}, None) is sentinel


def test_large_pin_rejects_nonliteral_path(finish, tmp_path):
    (tmp_path / "sub").mkdir()
    source = tmp_path / "large"
    source.write_bytes(b"a" * 2_000_001)
    expected = finish.hashlib.sha256(source.read_bytes()).hexdigest()
    assert finish.pin(source, expected) == b""
    with pytest.raises(ValueError, match="nonliteral"):
        finish.pin(tmp_path / "sub/../large", expected)


def test_failed_check_cannot_be_promoted(finish):
    report, value = fixture_value(finish)
    report["items"]["AUDIT-03"]["check_results"][0]["outcome"] = "FAIL"
    value["audit_report"] = deepcopy(report)
    value["report_sha256"] = finish.digest(report)
    value["receipts"]["OBS-AUDIT03"] = {"revoked": False, "event": {"decisions": {"AUDIT-03": {"decision": "approve"}}}}
    output = finish.project(report, value)
    assert output["items"]["AUDIT-03"]["score"] == 1
    assert output["items"]["AUDIT-03"]["check_results"][0]["outcome"] == "FAIL"


def test_approval_does_not_invent_confirmation_or_independence(finish):
    report, value = fixture_value(finish)
    for ident in ("AUDIT-05-B", "AUDIT-05-S03"):
        report["items"][ident]["check_results"] = [{"outcome": "NOT_RUN", "evidence_refs": []} for _ in range(3)]
    value["audit_report"] = deepcopy(report)
    value["report_sha256"] = finish.digest(report)
    for request in value["requests"]:
        value["receipts"][request["question_id"]] = {
            "revoked": False,
            "event": {"decisions": {scope: {"decision": "approve"} for scope in request["scopes"]}},
        }
    result = finish.project(report, value)
    assert result["items"]["AUDIT-03"]["score"] == 2
    assert result["items"]["AUDIT-04-B"]["score"] is None
    for ident in ("AUDIT-05-B", "AUDIT-05-S03"):
        row = result["items"][ident]
        assert row["score"] == 2
        assert row["execution_state"] == "PARTIAL"
        assert row["check_results"][-1]["outcome"] == "NOT_RUN"
        assert row["check_results"][-1]["evidence_refs"] == []
    assert result["items"]["neighbor"] == report["items"]["neighbor"]


def test_record_read_revoke_boundary(finish, monkeypatch, tmp_path):
    store, root = tmp_path / "store", tmp_path / "subject"
    store.mkdir()
    root.mkdir()
    monkeypatch.setattr(finish, "_operator_root", lambda: store)
    monkeypatch.setattr(finish, "current_request", lambda *args: None)
    request = {"binding": "b" * 64, "scopes": {"AUDIT-03": "synthetic only"}}
    event = {
        "request": request,
        "decisions": {"AUDIT-03": {"decision": "approve", "content": "test", "rationale": "test only"}},
        "retained_scopes": ["AUDIT-03"],
        "retention_consent": "test only",
        "save_authorized": True,
    }
    selection = {"artifacts": []}
    path = finish.record_decision(request, root, selection, lambda actual: deepcopy(event))
    original = path.read_bytes()
    assert finish.read_decision(request, root, selection)["event"] == event
    with pytest.raises(ValueError, match="already retained"):
        finish.record_decision(request, root, selection, lambda actual: deepcopy(event))
    finish.revoke_decision(request, root, selection)
    assert finish.read_decision(request, root, selection)["revoked"] is True
    assert path.read_bytes() == original


def test_conditional_na_requires_owner_and_current_followup(finish, monkeypatch):
    report, value = fixture_value(finish)
    value["native_followup"] = {"validated": True}
    assert finish.project(report, value)["items"]["AUDIT-04-B"].get("status") != "NOT_APPLICABLE"
    value["receipts"]["OBS-AUDIT04B"] = {
        "revoked": False,
        "event": {"decisions": {"AUDIT-04-B": {"decision": "approve"}}},
    }
    monkeypatch.setattr(finish, "load", lambda path: {"original_confirmed_affected": []})
    result = finish.project(report, value)
    assert result["items"]["AUDIT-04-B"]["score"] is None
    assert result["items"]["AUDIT-04-B"]["status"] == "NOT_APPLICABLE"
    assert result["items"]["neighbor"] == report["items"]["neighbor"]
    value["receipts"]["OBS-AUDIT04B"]["revoked"] = True
    assert finish.project(report, value)["items"]["AUDIT-04-B"].get("status") != "NOT_APPLICABLE"


@requires_private_runs
def test_native_suite_admission_is_separate_and_score_free(finish):
    value = finish.validate_suite_native(finish.RUNS / "suite-native-recovery-current/native-run")
    assert value["trial_count"] == 8
    assert value["raw_file_count"] == 64
    assert value["rubric_scores"] is None
    assert value["independent_review"] is False
    assert {r["task_credit"] for r in value["rows"]} == {0, 2, 3}


def test_suite_projection_preserves_every_original_row_and_measurement(finish, monkeypatch):
    report = {"items": {"B": {"score": 3}}, "measurements": {"original": {"receipt": "unchanged"}}}
    value = {"root": str(finish.SUITE_NATIVE), "rubric_scores": None}
    monkeypatch.setattr(finish, "validate_suite_native", lambda root: deepcopy(value))
    output = finish.apply_suite_native(report, value)
    assert output["items"] == report["items"]
    assert output["measurements"]["original"] == report["measurements"]["original"]
    assert output["measurements"]["suite_native_viability"] == value
    assert "suite_native_viability" not in report["measurements"]


@pytest.mark.parametrize("change", ["reward", "checks", "wrong-family", "wrong-image", "adapter", "source"])
@requires_private_runs
def test_suite_corruption_rejected_without_touching_frozen_inputs(finish, monkeypatch, change):
    root = finish.SUITE_NATIVE
    original_raw = finish.raw
    original_load = finish.load
    summary = original_load(root / "summary.json")
    batch = summary["native_trials"]
    episode = original_load(finish.RUNS / "suite-build-finish-current/manifest.json")["episodes"][0]
    receipt, attempt = deepcopy(batch["receipts"][0]), deepcopy(batch["attempts"][0])
    images = original_load(root / "image-manifest.json")["images"]
    if change == "wrong-family":
        episode["family"] = "dependency-release"
    if change == "wrong-image":
        images["interval-reservation"] = images["dependency-release"]
    if change in {"reward", "checks"}:
        suffix = "verifier/reward.txt" if change == "reward" else "verifier/checks.json"

        def corrupted(path):
            return b"corrupted" if str(path).endswith(suffix) else original_raw(path)

        monkeypatch.setattr(finish, "raw", corrupted)
    if change in {"adapter", "source"}:
        path = (
            root.parent / "harbor_adapter.py"
            if change == "adapter"
            else finish.HERE / "examples/finish-suite/interval-reservation/tests/verify.py"
        )
        monkeypatch.setattr(finish, "raw", lambda p: original_raw(p) + b"\n# drift" if p == path else original_raw(p))
        with pytest.raises(ValueError, match="changed"):
            finish.validate_suite_native(root)
    else:
        with pytest.raises(ValueError, match="family|inventory"):
            finish.suite_trial(root, episode, receipt, attempt, images)


@pytest.fixture
def contract_packet(finish):
    path = finish.RUNS / "suite-native-integration-current/ordinary-final/report/assessment.json"
    return finish.load(path)


@requires_private_runs
def test_actual_contract_packet_sources_and_comparisons(finish):
    request = finish.contract_review_request()
    assert request["binding"] == "32dcb90ec1d4ea26ef384972d7079e29d9430aff9e44389b9442182294ff361c"
    assert list(request["scopes"]) == [finish.CONTRACT_METHOD]
    assert "before freezing" in request["question"]
    value = finish.contract_comparison(finish.CONTRACT_REVIEW)
    assert value["original_healthy_comparisons"] == 54
    assert value["original_operational_errors"] == 1
    assert value["separate_zero_balance_comparisons"] == 2
    assert value["crosswalk_groups"] == 15
    assert value["cited_spans"] == 39


@pytest.mark.parametrize("name", ["REPORT.md", "exposure.md", "original-native-comparison.json", "input-manifest.json"])
@requires_private_runs
def test_contract_review_packet_drift_rejected(finish, monkeypatch, name):
    original = finish.raw
    monkeypatch.setattr(
        finish, "raw", lambda p: original(p) + b" " if p == finish.CONTRACT_REVIEW / name else original(p)
    )
    with pytest.raises(ValueError, match="changed"):
        finish.contract_review_request()


@pytest.mark.parametrize(
    "decision,revoked,score",
    [(None, False, 2), ("reject", False, 2), ("defer", False, 2), ("approve", True, 2), ("approve", False, 3)],
)
@requires_private_runs
def test_contract_qualification_is_exact_conditional_and_narrow(
    finish, monkeypatch, tmp_path, contract_packet, decision, revoked, score
):
    import rubric_general_review

    report = contract_packet["report"]
    selection = contract_packet["selection"]
    prior = deepcopy(report["measurements"]["scoped_reviews"]["contract"]["receipt"])
    prior["event"]["request"] = finish.load(finish.RUNS / "parallel-source-intake/contract-request.json")
    monkeypatch.setattr(rubric_general_review, "read", lambda *a, **kw: prior)
    receipt = (
        None
        if decision is None
        else {
            "revoked": revoked,
            "event": {"decisions": {finish.CONTRACT_METHOD: {"decision": decision}}},
        }
    )
    monkeypatch.setattr(finish, "read_decision", lambda *a: receipt)
    value = finish.validate_contract_review(finish.CONTRACT_REVIEW, finish.ROOT, selection, report)
    ref = tmp_path / "review.json"
    finish.completion.save(ref, value)
    original = deepcopy(report)
    result = finish.apply_contract_review(report, value, ref, root=finish.ROOT, selection=selection)
    assert result["items"][finish.CONTRACT_ID]["score"] == score
    assert all(result["items"][k] == v for k, v in report["items"].items() if k != finish.CONTRACT_ID)
    assert all(result["measurements"][k] == v for k, v in report["measurements"].items())
    assert result["items"][finish.CONTRACT_ID]["check_results"] == report["items"][finish.CONTRACT_ID]["check_results"]
    assert report == original
    changed = deepcopy(value)
    changed["method_qualified"] = not value["method_qualified"]
    monkeypatch.setattr(finish, "validate_contract_review", lambda *a: changed)
    with pytest.raises(ValueError, match="changed during projection"):
        finish.apply_contract_review(report, value, ref, root=finish.ROOT, selection=selection)


@requires_private_runs
def test_contract_intake_uses_existing_boundary_without_scope_transfer(finish, monkeypatch, tmp_path, contract_packet):
    store = tmp_path / "operator"
    store.mkdir()
    monkeypatch.setattr(finish, "_operator_root", lambda: store)
    request = finish.contract_review_request()
    event = {
        "request": request,
        "decisions": {
            finish.CONTRACT_METHOD: {
                "decision": "approve",
                "content": "Synthetic test only",
                "rationale": "Synthetic bounded alternative",
            }
        },
        "retained_scopes": [finish.CONTRACT_METHOD],
        "retention_consent": "Synthetic test retention",
        "save_authorized": True,
    }
    selection = contract_packet["selection"]
    original_validator = finish.current_request
    path = finish.record_contract_decision(request, finish.ROOT, selection, lambda actual: deepcopy(event))
    assert finish.current_request is original_validator
    assert finish.read_decision(request, finish.ROOT, selection)["event"] == event
    assert path.name.endswith(".original-finish.json")
    wrong = deepcopy(request)
    wrong["scopes"]["S"] = "Unauthorized broader scope"
    with pytest.raises(ValueError, match="exact current packet"):
        finish.record_contract_decision(wrong, finish.ROOT, selection, lambda actual: event)
    finish.revoke_decision(request, finish.ROOT, selection)
    assert finish.read_decision(request, finish.ROOT, selection)["revoked"] is True


@pytest.mark.parametrize("change", ["failed-check", "missing-check", "coverage", "revoked-lower", "lower-score"])
@requires_private_runs
def test_contract_method_acceptance_cannot_replace_lower_anchor(finish, monkeypatch, contract_packet, change):
    import rubric_general_review

    report = deepcopy(contract_packet["report"])
    owner = report["measurements"]["scoped_reviews"]["contract"]
    prior = deepcopy(owner["receipt"])
    prior["event"]["request"] = finish.load(finish.RUNS / "parallel-source-intake/contract-request.json")
    if change == "failed-check":
        report["items"][finish.CONTRACT_ID]["check_results"][0]["outcome"] = "FAIL"
    elif change == "missing-check":
        report["items"][finish.CONTRACT_ID]["check_results"].pop()
    elif change == "coverage":
        owner["automatic"][finish.CONTRACT_ID]["coverage"]["supported"] -= 1
    elif change == "revoked-lower":
        owner["receipt"]["revoked"] = prior["revoked"] = True
    else:
        report["items"][finish.CONTRACT_ID]["score"] = 1
    monkeypatch.setattr(rubric_general_review, "read", lambda *a, **kw: prior)
    monkeypatch.setattr(
        finish,
        "read_decision",
        lambda *a: {"revoked": False, "event": {"decisions": {finish.CONTRACT_METHOD: {"decision": "approve"}}}},
    )
    value = finish.validate_contract_review(finish.CONTRACT_REVIEW, finish.ROOT, contract_packet["selection"], report)
    assert value["method_qualified"] is True
    assert value["promotion_ready"] is False


@pytest.fixture
def provenance_packet(finish, monkeypatch):
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(finish.RUNS / "leakage-current-operator-store"))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", "localhost-assessment-operator-v1")
    return finish.load(finish.PROVENANCE_BASELINE)


@pytest.fixture
def decided_provenance_packet(provenance_packet):
    import rubric_leakage_current

    if provenance_packet["definitions_sha256"] != rubric_leakage_current.definitions_digest():
        # Retained owner decisions are bound to the definitions they were made against and never transfer.
        pytest.skip("retained private owner decisions predate the current rubric definitions")
    return provenance_packet


@requires_private_runs
def test_actual_provenance_comparison_and_existing_audit_qualification(finish, decided_provenance_packet):
    value = finish.validate_provenance_review(
        finish.PROVENANCE_REVIEW,
        finish.ROOT,
        decided_provenance_packet["selection"],
        decided_provenance_packet["report"],
    )
    assert value["comparison"]["comparison_count"] == 1161
    assert len(value["comparison"]["initial_failures"]) == 1
    assert value["comparison"]["release_audit"]["eligible_count"] == 194
    assert value["comparison"]["sensitivity"]["exclusion_effect"] is None
    assert value["predicates"]["AUDIT-05-B"]["promotion_ready"] is True
    assert value["historical_rows"]["AUDIT-05-B"]["check_results"][-1]["outcome"] == "NOT_RUN"


@pytest.mark.parametrize("name", ["packet.json", "semantic-review-basis.json", "verification-final.json"])
@requires_private_runs
def test_provenance_drift_rejected(finish, monkeypatch, name):
    original = finish.raw
    monkeypatch.setattr(
        finish, "raw", lambda p: original(p) + b" " if p == finish.PROVENANCE_REVIEW / name else original(p)
    )
    with pytest.raises(ValueError, match="changed"):
        finish.provenance_review_sources()


@pytest.mark.parametrize(
    "decision,revoked,expected",
    [(None, False, 2), ("defer", False, 2), ("reject", False, 2), ("approve", True, 2), ("approve", False, 3)],
)
@requires_private_runs
def test_provenance_scoped_method_and_neighbor_preservation(
    finish, monkeypatch, tmp_path, decided_provenance_packet, decision, revoked, expected
):
    real_read = finish.read_decision
    receipt = (
        None
        if decision is None
        else {"revoked": revoked, "event": {"decisions": {finish.TRACE_METHOD: {"decision": decision}}}}
    )
    monkeypatch.setattr(
        finish,
        "read_decision",
        lambda request, *a: receipt if request["question_id"] == "B-TRACE-CORROBORATION" else real_read(request, *a),
    )
    report = decided_provenance_packet["report"]
    value = finish.validate_provenance_review(
        finish.PROVENANCE_REVIEW, finish.ROOT, decided_provenance_packet["selection"], report
    )
    reference = tmp_path / "corroboration.json"
    finish.completion.save(reference, value)
    output = finish.apply_provenance_review(
        report, value, reference, root=finish.ROOT, selection=decided_provenance_packet["selection"]
    )
    assert output["items"]["AUDIT-05-B"]["score"] == 3
    assert output["items"]["TRACE-04-A"]["score"] == expected
    assert all(output["items"][k] == v for k, v in report["items"].items() if k not in finish.PROVENANCE_IDS)
    assert all(output["measurements"][k] == v for k, v in report["measurements"].items())
    assert value["historical_rows"] == {k: report["items"][k] for k in finish.PROVENANCE_IDS}
    assert output["items"]["TRACE-04-A"]["check_results"] == report["items"]["TRACE-04-A"]["check_results"]
    changed = deepcopy(value)
    changed["receipt"] = {"revoked": True}
    monkeypatch.setattr(finish, "validate_provenance_review", lambda *a: changed)
    with pytest.raises(ValueError, match="changed during projection"):
        finish.apply_provenance_review(
            report, value, reference, root=finish.ROOT, selection=decided_provenance_packet["selection"]
        )


@pytest.mark.parametrize(
    "ident,change", [(i, c) for i in ("AUDIT-05-B", "TRACE-04-A") for c in ("failed", "missing", "lower", "canonical")]
)
@requires_private_runs
def test_provenance_permission_never_replaces_lower_anchor(
    finish, monkeypatch, decided_provenance_packet, ident, change
):
    report = deepcopy(decided_provenance_packet["report"])
    row = report["items"][ident]
    if change == "failed":
        row["check_results"][0]["outcome"] = "FAIL"
    elif change == "missing":
        row["check_results"].pop()
    elif change == "canonical":
        row["check_results"][0]["action"] = "different scope"
    else:
        row["score"] = 1
    value = finish.validate_provenance_review(
        finish.PROVENANCE_REVIEW, finish.ROOT, decided_provenance_packet["selection"], report
    )
    assert value["predicates"][ident]["promotion_ready"] is False


@requires_private_runs
def test_provenance_intake_retains_only_exact_scope(finish, monkeypatch, tmp_path, provenance_packet):
    store = tmp_path / "operator"
    store.mkdir()
    monkeypatch.setattr(finish, "_operator_root", lambda: store)
    request = finish.provenance_review_request()
    event = {
        "request": request,
        "decisions": {
            finish.TRACE_METHOD: {"decision": "approve", "content": "synthetic only", "rationale": "synthetic test"}
        },
        "retained_scopes": [finish.TRACE_METHOD],
        "retention_consent": "synthetic test",
        "save_authorized": True,
    }
    selection = provenance_packet["selection"]
    path = finish.record_provenance_decision(request, finish.ROOT, selection, lambda actual: event)
    original = path.read_bytes()
    assert finish.read_decision(request, finish.ROOT, selection)["event"] == event
    wrong = deepcopy(request)
    wrong["scopes"]["AUDIT-05-B"] = "unauthorized transfer"
    with pytest.raises(ValueError, match="exact current packet"):
        finish.record_provenance_decision(wrong, finish.ROOT, selection, lambda actual: event)
    finish.revoke_decision(request, finish.ROOT, selection)
    assert finish.read_decision(request, finish.ROOT, selection)["revoked"] is True
    assert path.read_bytes() == original


def test_report_replacement_rejected(finish):
    report, value = fixture_value(finish)
    report["items"]["neighbor"]["score"] = 3
    with pytest.raises(ValueError, match="projection report differs"):
        finish.project(report, value)
