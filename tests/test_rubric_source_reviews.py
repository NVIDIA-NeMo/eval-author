# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
import rubric_source_reviews as reviews  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "source_review_test_helpers", ROOT / "tests/test_rubric_example_assessment.py"
)
assert spec and spec.loader
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def make_request(tmp_path, *, nested=False):
    task = tmp_path / "task" if nested else tmp_path
    task.mkdir(exist_ok=True)
    selection = helpers.package(task)
    if nested:
        (tmp_path / "instruction.md").write_bytes((task / "instruction.md").read_bytes())
        for record in selection["artifacts"]:
            record["path"] = "task/" + record["path"]
            record["evidence"] = [{**ref, "path": "task/" + ref["path"]} for ref in record["evidence"]]
    _, baseline = helpers.run_cli(tmp_path, selection)
    document = helpers.add_review(tmp_path, selection, baseline)
    criterion = "provenance.task-artifact-access"
    document["schema"] = "rubric-source-review-v2"
    document["decisions"][0]["check_id"] = criterion + ".human-review"
    document["reviewer"] = {"kind": "agent", "identity": "Synthetic test agent", "session_id": "review-test"}
    document["method"] = {
        "id": "independent-source-reconstruction-v1",
        "producer_session_id": "producer-test",
        "received_scope": "Synthetic task inventory only.",
        "prior_exposure": ["Synthetic test fixture, not real review evidence."],
        "separation_evidence": "Synthetic separate execution record for tests only.",
        "unavailable_method_reason": "No human inventory reviewer in this synthetic fixture.",
        "qualification_rationale": "Owner must inspect the source reconstruction and separation record.",
    }
    document["reconstruction"] = {
        criterion: {
            "independent_result": reviews.comparison_basis(baseline["items"][criterion]),
            "differences": [],
            "limitations": ["Reviewer access only, not solver permissions."],
        }
    }
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    return selection, baseline, document


def test_qualified_review_request_is_pending_and_preserves_measured_checks(tmp_path):
    selection, baseline, _ = make_request(tmp_path)
    criterion = "provenance.task-artifact-access"
    result, after = helpers.run_cli(tmp_path, selection)
    assert result.returncode == 1
    assert (
        after["measurements"]["example_assessment"]["source_reviews"]["records"][0]["acceptance"]["status"]
        == "pending_owner_qualification"
    )
    row = after["items"][criterion]
    assert row["score"] == 2
    assert row["check_results"] == baseline["items"][criterion]["check_results"]
    assert row["source_review_acceptance"]["status"] == "pending_owner_qualification"


def sign_decision(tmp_path, packet, monkeypatch):
    # Disposable test signer is not a real owner approval.
    trust = tmp_path.parent / (tmp_path.name + "-trust")
    trust.mkdir(mode=0o700)
    key = trust / "test-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    allowed = trust / "allowed_signers"
    allowed.write_text("source-review-owner " + key.with_suffix(".pub").read_text())
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_TRUST", str(trust))
    request = reviews.owner_decision_request(packet)
    request.update(decision="approve", rationale="Synthetic test adjudication of exact packet and separation only.")
    target = trust / (request["packet_digest"] + ".json")
    target.write_text(json.dumps(request))
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "eval-author-source-review", str(target)],
        check=True,
        capture_output=True,
    )
    return trust, target


def test_signed_owner_qualification_consumed_through_real_cli(tmp_path, monkeypatch):
    selection, before, _ = make_request(tmp_path)
    _, target = sign_decision(tmp_path, (tmp_path / "review.json").read_bytes(), monkeypatch)
    result, report = helpers.run_cli(tmp_path, selection)
    assert result.returncode == 1
    criterion = "provenance.task-artifact-access"
    row = report["items"][criterion]
    assert row["score"] == 3
    assert row["evidence_strength"] == "E3"
    assert row["source_review_acceptance"]["status"] == "accepted"
    assert row["check_results"][:-1] == before["items"][criterion]["check_results"][:-1]
    assert row["check_results"][-1]["actual_method"] == "qualified_agent_review"
    assert row["recorded_source_reviews"][0]["reviewer"]["kind"] == "agent"
    assert row["recorded_source_reviews"][0]["authority_authenticated"] is False
    assert row["source_review_acceptance"]["owner_signature_verified"] is True
    assert row["recorded_source_reviews"][0]["independence_established"] is True
    assert not any("independent" in gap.lower() for gap in row["remaining_gaps"])
    assert report["items"]["task-contract.clear-contract"]["score"] is None
    target.write_text(target.read_text().replace("approve", "reject"))
    result, _ = helpers.run_cli(tmp_path, selection)
    assert result.returncode == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "different_inventory",
        "difference",
        "contradicted",
        "ambiguous",
        "same_session",
        "fake_human",
        "extra_scope",
        "self_approval",
    ],
)
def test_signed_packet_cannot_bypass_comparison_or_method_scope(tmp_path, monkeypatch, mutation):
    selection, _, document = make_request(tmp_path)
    criterion = "provenance.task-artifact-access"
    if mutation == "different_inventory":
        document["reconstruction"][criterion]["independent_result"]["evidence_refs"] = []
    elif mutation == "difference":
        document["reconstruction"][criterion]["differences"] = ["Missing source in the first inventory."]
    elif mutation in {"contradicted", "ambiguous"}:
        document["decisions"][0]["status"] = mutation
    elif mutation == "same_session":
        document["reviewer"]["session_id"] = "producer-test"
    elif mutation == "fake_human":
        document["reviewer"]["kind"] = "human"
    elif mutation == "extra_scope":
        document["reconstruction"]["task-contract.clear-contract"] = document["reconstruction"].pop(criterion)
    else:
        document["approved"] = True
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    if mutation in {"different_inventory", "difference", "contradicted", "ambiguous"}:
        sign_decision(tmp_path, (tmp_path / "review.json").read_bytes(), monkeypatch)
    result, after = helpers.run_cli(tmp_path, selection)
    if result.returncode != 2:
        assert after["items"][criterion]["score"] == 2
        assert after["items"][criterion]["source_review_acceptance"]["status"] == "blocked_comparison"


def test_accepted_review_replay_requires_current_owner_signature(tmp_path, monkeypatch):
    selection, _, _ = make_request(tmp_path)
    trust, target = sign_decision(tmp_path, (tmp_path / "review.json").read_bytes(), monkeypatch)
    _, report = helpers.run_cli(tmp_path, selection)
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 1
    target.unlink()
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2
    assert (trust / "allowed_signers").exists()


def test_other_selected_review_contradiction_blocks_qualified_packet(tmp_path, monkeypatch):
    selection, _, document = make_request(tmp_path)
    other = deepcopy(document)
    other["schema"] = "rubric-source-review-v1"
    other.pop("method")
    other.pop("reconstruction")
    other["decisions"][0]["status"] = "contradicted"
    helpers.add_source(tmp_path, selection, "dissent.json", json.dumps(other))
    selection["artifacts"][-1]["role"] = "review"
    selection["example_assessment"]["review_ids"].append("dissent.json")
    sign_decision(tmp_path, (tmp_path / "review.json").read_bytes(), monkeypatch)
    _, report = helpers.run_cli(tmp_path, selection)
    row = report["items"]["provenance.task-artifact-access"]
    assert row["score"] == 2
    assert row["source_review_acceptance"]["contradictions"][0]["artifact_id"] == "dissent.json"


def test_trust_may_live_under_common_root_but_not_in_selected_artifacts(tmp_path, monkeypatch):
    task = tmp_path / "task"
    task.mkdir()
    selection, _, _ = make_request(task)
    sign_decision(task, (task / "review.json").read_bytes(), monkeypatch)
    selection["artifacts"] = [
        {**r, "path": "task/" + r["path"], "evidence": [{**e, "path": "task/" + e["path"]} for e in r["evidence"]]}
        for r in selection["artifacts"]
    ]
    result, report = helpers.run_cli(tmp_path, selection)
    assert result.returncode != 2
    # The reconstruction was for a different source-root spelling, so it cannot promote.
    assert report["items"]["provenance.task-artifact-access"]["score"] == 2


def test_unavailable_selected_review_blocks_otherwise_accepted_review(tmp_path, monkeypatch):
    selection, _, document = make_request(tmp_path)
    helpers.add_source(tmp_path, selection, "missing-review.json", json.dumps(document))
    selection["artifacts"][-1]["role"] = "review"
    selection["example_assessment"]["review_ids"].append("missing-review.json")
    (tmp_path / "missing-review.json").unlink()
    sign_decision(tmp_path, (tmp_path / "review.json").read_bytes(), monkeypatch)
    _, report = helpers.run_cli(tmp_path, selection)
    assert report["items"]["provenance.task-artifact-access"]["score"] == 2


def test_all_four_consumer_gates_preserve_lower_anchors(tmp_path, monkeypatch):
    selection, _, document = make_request(tmp_path)
    _, report = helpers.run_cli(tmp_path, selection)
    # Synthetic projection coverage, not evidence about a real task.
    for ident, check_id in reviews.SOURCE_CHECKS.items():
        rows = deepcopy(report["items"])
        row = rows[ident]
        row["score"] = 2
        decision = deepcopy(document["decisions"][0])
        decision["check_id"] = check_id
        packet = deepcopy(document)
        packet["decisions"] = [decision]
        packet["reconstruction"] = {
            ident: {
                "independent_result": reviews.comparison_basis(row),
                "differences": [],
                "limitations": ["Synthetic projection test only."],
            }
        }
        review = {
            "artifact_id": "synthetic",
            "reviewer": packet["reviewer"],
            "decisions": [decision],
            "reconstruction": packet["reconstruction"],
            "acceptance": {
                "status": "pending_owner_qualification",
                "packet_digest": "synthetic",
                "owner_signature_verified": True,
                "decision": "approve",
                "decision_digest": "synthetic",
            },
        }
        for lower in (None, 0, 1):
            trial = deepcopy(rows)
            trial[ident]["score"] = lower
            reviews.apply(trial, {"records": [review]})
            assert trial[ident]["score"] == lower
        reviews.apply(rows, {"records": [review]})
        assert rows[ident]["score"] == 3
        assert (
            next(c for c in rows[ident]["check_results"] if c["id"] == check_id)["actual_method"]
            == "qualified_agent_review"
        )


def test_unsigned_decision_cli_never_prepopulates_approval(tmp_path):
    _, _, _ = make_request(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "rubric_source_reviews.py"),
            "--decision-request",
            str(tmp_path / "review.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    request = json.loads(result.stdout)
    assert request["decision"] is None and request["rationale"] is None
    assert request["criteria"] == ["provenance.task-artifact-access"]


def test_duplicate_qualified_reconstructions_are_rejected(tmp_path):
    selection, _, document = make_request(tmp_path)
    helpers.add_source(tmp_path, selection, "second.json", json.dumps(document))
    selection["artifacts"][-1]["role"] = "review"
    selection["example_assessment"]["review_ids"].append("second.json")
    result, _ = helpers.run_cli(tmp_path, selection)
    assert result.returncode == 2


def session_event(raw):
    return {
        "session_id": "synthetic-owner-session",
        "message_id": "synthetic-user-turn",
        "owner_id": "synthetic-owner",
        "role": "user",
        "purpose": "source_review_qualification",
        "request": reviews.owner_decision_request(raw),
        "decision": "approve",
        "rationale": "Synthetic explicit qualification of this method and comparison.",
        "content": "Synthetic owner decision, not real approval.",
        "save_authorized": True,
    }


def test_one_skill_source_qualification_uses_current_selection_and_host(tmp_path, monkeypatch):
    selection, _, _ = make_request(tmp_path)
    trust = tmp_path.parent / (tmp_path.name + "-host")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    raw = (tmp_path / "review.json").read_bytes()
    event = session_event(raw)
    calls = []

    def owner(expected):
        calls.append(expected)
        return event

    result = reviews.qualify_selected(tmp_path, selection, owner)
    assert len(calls) == 1
    assert result["items"]["provenance.task-artifact-access"]["score"] == 3
    assert result["items"]["task-contract.clear-contract"]["score"] is None
    assert helpers.run_cli(tmp_path, selection)[1]["items"] == result["items"]


def test_one_skill_source_qualification_does_not_ask_about_incomplete_comparison(tmp_path, monkeypatch):
    selection, _, document = make_request(tmp_path)
    document["reconstruction"]["provenance.task-artifact-access"]["differences"] = ["Missing comparison."]
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    calls = []
    result = reviews.qualify_selected(tmp_path, selection, lambda request: calls.append(request))
    assert calls == []
    assert (
        result["items"]["provenance.task-artifact-access"]["source_review_acceptance"]["status"] == "blocked_comparison"
    )


def test_session_owner_decision_roundtrip_without_signing(tmp_path, monkeypatch):
    selection, before, _ = make_request(tmp_path)
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-session")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    event = session_event(raw)
    # This callback models the application's trusted event store, not a transcript parser.
    target = reviews.record_session_decision(raw, lambda expected: event)
    assert json.loads(target.read_text())["event"] == event
    result, after = helpers.run_cli(tmp_path, selection)
    assert result.returncode == 1
    row = after["items"]["provenance.task-artifact-access"]
    assert row["score"] == 3
    assert row["source_review_acceptance"]["owner_signature_verified"] is False
    assert row["source_review_acceptance"]["owner_session_decision_verified"] is True
    assert row["check_results"][:-1] == before["items"]["provenance.task-artifact-access"]["check_results"][:-1]
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(after))
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 1
    target.unlink()
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2


@pytest.mark.parametrize(
    "change", ["absent", "assistant", "execution", "scope", "self", "status", "boolean", "no_save"]
)
def test_session_intake_rejects_non_owner_or_unscoped_events(tmp_path, monkeypatch, change):
    make_request(tmp_path)
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-session")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    event = session_event(raw)
    if change == "absent":
        event = None
    elif change == "assistant":
        event["role"] = "assistant"
    elif change == "execution":
        event["purpose"] = "execution_approval"
    elif change == "scope":
        event["request"]["criteria"] = []
    elif change == "self":
        event["session_id"] = "review-test"
    elif change == "status":
        event["decision"] = "supported"
    elif change == "boolean":
        event["decision"] = True
    else:
        event["save_authorized"] = False
    with pytest.raises(ValueError):
        reviews.record_session_decision(raw, lambda expected: event)
    assert list(trust.iterdir()) == []


def test_session_intake_requires_explicit_save_consent(tmp_path, monkeypatch):
    make_request(tmp_path)
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-session")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    event = session_event(raw)
    event.pop("save_authorized", None)
    with pytest.raises(ValueError):
        reviews.record_session_decision(raw, lambda expected: event)
    assert list(trust.iterdir()) == []


@pytest.mark.parametrize(
    "change", ["difference", "comparison", "contradiction", "reject", "packet_edit", "self_status"]
)
def test_session_approval_keeps_all_scoring_gates(tmp_path, monkeypatch, change):
    selection, _, document = make_request(tmp_path)
    criterion = "provenance.task-artifact-access"
    if change == "difference":
        document["reconstruction"][criterion]["differences"] = ["Unresolved difference."]
    elif change == "comparison":
        document["reconstruction"][criterion]["independent_result"]["evidence_refs"] = []
    elif change == "contradiction":
        document["decisions"][0]["status"] = "ambiguous"
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-session")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    event = session_event(raw)
    if change == "reject":
        event["decision"] = "reject"
    reviews.record_session_decision(raw, lambda expected: event)
    with pytest.raises(FileExistsError):
        reviews.record_session_decision(raw, lambda expected: event)
    if change == "packet_edit":
        document["method"]["prior_exposure"].append("Changed packet.")
        helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    elif change == "self_status":
        document["acceptance"] = {"owner_session_decision_verified": True, "decision": "approve"}
        helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    result, report = helpers.run_cli(tmp_path, selection)
    if result.returncode != 2:
        assert report["items"][criterion]["score"] == 2


@pytest.mark.parametrize("boundary", ["selected", "symlink", "public"])
def test_session_store_is_external_and_private(tmp_path, monkeypatch, boundary):
    selection, _, _ = make_request(tmp_path)
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-session")
    trust.mkdir(mode=0o700)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(trust))
    reviews.record_session_decision(raw, lambda expected: session_event(raw))
    if boundary == "selected":
        monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(tmp_path))
        tmp_path.chmod(0o700)
    elif boundary == "symlink":
        link = tmp_path.parent / (tmp_path.name + "-link")
        link.symlink_to(trust, target_is_directory=True)
        monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", str(link))
    else:
        trust.chmod(0o755)
    assert helpers.run_cli(tmp_path, selection)[0].returncode == 2


def operator_event(raw):
    return {
        "request": reviews.owner_decision_request(raw),
        "decision": "approve",
        "rationale": "Synthetic scoped method qualification for adapter testing only.",
        "content": "Synthetic owner answer, not an actual decision.",
        "retention_consent": "Synthetic explicit retention consent.",
        "save_authorized": True,
    }


def test_local_operator_qualifier_and_retained_replay(tmp_path, monkeypatch):
    selection, before, _ = make_request(tmp_path)
    trust = tmp_path.parent / (tmp_path.name + "-operator")
    trust.mkdir()
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(trust))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", "localhost-assessment-operator-v1")
    raw = (tmp_path / "review.json").read_bytes()
    result = reviews.qualify_selected(tmp_path, selection, operator_decision=lambda request: operator_event(raw))
    row = result["items"]["provenance.task-artifact-access"]
    assert row["score"] == 3
    admission = row["source_review_acceptance"]
    assert admission["local_operator_decision_recorded"] is True
    assert admission["owner_signature_verified"] is False
    assert admission["owner_session_decision_verified"] is False
    assert "owner_event_ref" not in admission
    assert "same-user" in admission["trust_basis"]
    assert row["check_results"][:-1] == before["items"]["provenance.task-artifact-access"]["check_results"][:-1]
    retained = tmp_path / "operator-retained.json"
    retained.write_text(json.dumps(result))
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 1
    reviews.revoke_operator_decision(raw)
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2
    assert helpers.run_cli(tmp_path, selection)[1]["items"]["provenance.task-artifact-access"]["score"] == 2


@pytest.mark.parametrize("change", ["tamper", "stale_packet", "self_approval", "dissent", "difference", "comparison"])
def test_operator_qualification_cannot_replace_current_evidence(tmp_path, monkeypatch, change):
    selection, _, document = make_request(tmp_path)
    trust = tmp_path.parent / (tmp_path.name + "-operator")
    trust.mkdir()
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(trust))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", reviews.OPERATOR_TRUST)
    raw = (tmp_path / "review.json").read_bytes()
    target = reviews.record_operator_decision(raw, lambda request: operator_event(raw))
    _, report = helpers.run_cli(tmp_path, selection)
    retained = tmp_path / "retained.json"
    retained.write_text(json.dumps(report))
    criterion = "provenance.task-artifact-access"
    if change == "tamper":
        record = json.loads(target.read_text())
        record["event"]["rationale"] = "Changed after acceptance."
        target.write_text(json.dumps(record))
    elif change == "stale_packet":
        document["method"]["prior_exposure"].append("New exposure after acceptance.")
    elif change == "self_approval":
        document["acceptance"] = {"local_operator_decision_recorded": True, "decision": "approve"}
    elif change == "dissent":
        document["decisions"][0]["status"] = "contradicted"
    elif change == "difference":
        document["reconstruction"][criterion]["differences"] = ["Unresolved source difference."]
    else:
        document["reconstruction"][criterion]["independent_result"]["evidence_refs"] = []
    if change != "tamper":
        helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    assert helpers.run_cli(tmp_path, selection, "--verify-existing", str(retained))[0].returncode == 2
    if change != "tamper":
        result, after = helpers.run_cli(tmp_path, selection)
        assert result.returncode == 2 or after["items"][criterion]["score"] == 2


@pytest.mark.parametrize("change", ["no_trust", "consent", "scope", "status", "invented_id", "selected_store"])
def test_operator_boundary_is_explicit_and_separate(tmp_path, monkeypatch, change):
    selection, _, _ = make_request(tmp_path)
    trust = tmp_path.parent / (tmp_path.name + "-operator")
    trust.mkdir()
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(trust))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", reviews.OPERATOR_TRUST)
    raw = (tmp_path / "review.json").read_bytes()
    event = operator_event(raw)
    if change == "no_trust":
        monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST")
    elif change == "consent":
        event["save_authorized"] = False
    elif change == "scope":
        event["request"]["criteria"] = []
    elif change == "status":
        event["decision"] = "supported"
    elif change == "invented_id":
        event["owner_id"] = "guessed-owner"
    else:
        monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(tmp_path))
    with pytest.raises(ValueError):
        reviews.qualify_selected(tmp_path, selection, operator_decision=lambda request: event)
    assert not list(trust.iterdir())


def extension_fixture(tmp_path, monkeypatch):
    import rubric_combined as combined

    selection, baseline, document = make_request(tmp_path, nested=True)
    inventory = [
        {key: r[key] for key in ("id", "path", "role", "digest")}
        for r in selection["artifacts"]
        if r["role"] != "review"
    ]
    frozen = json.dumps({"selected_inventory": inventory}).encode()
    (tmp_path / "raw-reconstruction.json").write_bytes(frozen)
    document["method"]["separation_evidence"] += " Frozen reconstruction bindings: " + json.dumps(
        {"raw-reconstruction.json": hashlib.sha256(frozen).hexdigest()}
    )
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    raw = (tmp_path / "review.json").read_bytes()
    trust = tmp_path.parent / (tmp_path.name + "-extension-operator")
    trust.mkdir()
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(trust))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", reviews.OPERATOR_TRUST)
    reviews.record_operator_decision(raw, lambda _: operator_event(raw))
    helpers.add_source(
        tmp_path, selection, "heldout/native-index.json", '{"schema":"eval-author.refund-heldout-panel.v1"}'
    )
    supplemental = selection["artifacts"][-1]
    supplemental["id"] = "native-panel:" + supplemental["digest"]
    selection["example_assessment"]["native_bundle_ids"] = [supplemental["id"]]
    panel = {
        "scope": "eval-author.refund-heldout-panel.v1",
        "index_digest": supplemental["digest"],
        "counts": {"declared": 1, "completed": 1, "disagreements": 1, "infrastructure_errors": 0},
        "controls": [{"case": "synthetic-dissent", "differences": [{"check": "ordering", "actual": 0, "expected": 1}]}],
        "independence_qualified": False,
        "human_decisions": [],
        "rubric_score": None,
    }
    # Only native admission is synthetic. Exercise the real source projection,
    # comparisons, qualification, current evidence and review bindings.
    monkeypatch.setattr(
        combined,
        "select_bundle",
        lambda *_: {"native_bundle_id": supplemental["id"], "artifacts": [deepcopy(supplemental)]},
    )
    monkeypatch.setattr(
        combined,
        "project_panels",
        lambda sources, selector, task: [deepcopy(panel)] if selector.get("native_bundle_ids") else [],
    )
    return selection, baseline, document, panel


def extension_measure(tmp_path, selection):
    spec = importlib.util.spec_from_file_location("extension_measure", SCRIPTS / "measure.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.measure(tmp_path, selection)


def test_admitted_heldout_extension_reuses_only_frozen_source_scope(tmp_path, monkeypatch):
    selection, baseline, _, panel = extension_fixture(tmp_path, monkeypatch)
    before = (tmp_path / "review.json").read_bytes()
    report = extension_measure(tmp_path, selection)
    row = report["items"]["provenance.task-artifact-access"]
    assert row["score"] == 3
    assert row["source_review_acceptance"]["comparison_matches"] is True
    assert row["check_results"][:-1] == baseline["items"]["provenance.task-artifact-access"]["check_results"][:-1]
    scope = report["measurements"]["example_assessment"]["source_reviews"]["records"][0]["scope"]
    assert scope["kind"] == "frozen-reviewed-subselection"
    assert scope["supplemental_evidence"][0]["review_status"] == "unreviewed"
    assert scope["supplemental_evidence"][0]["inherited_authority"] is False
    assert scope["supplemental_evidence"][0]["dissent"] == panel["controls"]
    assert (tmp_path / "review.json").read_bytes() == before
    assert extension_measure(tmp_path, selection) == report


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "task_source",
        "configuration",
        "role",
        "changed_path",
        "changed_bytes",
        "changed_scorer",
        "deleted",
        "alias",
        "unselected_index",
        "unadmitted_index",
        "manifest",
        "task_config",
        "definitions",
    ],
)
def test_extension_rejects_material_or_unbound_changes(tmp_path, monkeypatch, change):
    import rubric_combined as combined

    selection, _, document, _ = extension_fixture(tmp_path, monkeypatch)
    original = selection["artifacts"][0]
    supplemental = selection["artifacts"][-1]
    if change in {"unknown", "task_source", "configuration"}:
        path = "task/tests/new.py" if change == "task_source" else "extra.json"
        helpers.add_source(tmp_path, selection, path, "{}")
        if change == "configuration":
            selection["artifacts"][-1]["role"] = "configuration"
    elif change == "role":
        original["role"] = "supplemental"
    elif change == "changed_path":
        target = tmp_path / "copied.toml"
        target.write_bytes((tmp_path / original["path"]).read_bytes())
        original["path"] = "copied.toml"
    elif change in {"changed_bytes", "changed_scorer"}:
        target = tmp_path / ("task/tests/check.py" if change == "changed_scorer" else "task/tests/helper.py")
        helpers.refresh(tmp_path, selection, target.relative_to(tmp_path).as_posix(), "expected = 18\n")
    elif change == "deleted":
        selection["artifacts"].remove(selection["artifacts"][1])
    elif change == "alias":
        alias = deepcopy(supplemental)
        alias["id"] = "alias"
        selection["artifacts"].append(alias)
    elif change == "unselected_index":
        selection["example_assessment"].pop("native_bundle_ids")
    elif change == "unadmitted_index":

        def reject(*_):
            raise ValueError("synthetic owning admission rejected")

        monkeypatch.setattr(combined, "select_bundle", reject)
    elif change == "manifest":
        (tmp_path / "raw-reconstruction.json").write_text("{}")
    elif change == "task_config":
        selection["example_assessment"]["task_config_id"] = "not-reviewed"
    else:
        monkeypatch.setattr(reviews, "DEFINITION_FILES", ())
    with pytest.raises((ValueError, KeyError)):
        extension_measure(tmp_path, selection)


@pytest.mark.parametrize("change", ["comparison", "dissent", "downgrade"])
def test_extension_does_not_bypass_current_comparison_or_dissent(tmp_path, monkeypatch, change):
    selection, _, document, _ = extension_fixture(tmp_path, monkeypatch)
    report = extension_measure(tmp_path, selection)
    rows = deepcopy(report["items"])
    ident = "provenance.task-artifact-access"
    rows[ident]["score"] = 2
    measurement = deepcopy(report["measurements"]["example_assessment"]["source_reviews"])
    if change == "comparison":
        rows[ident]["check_results"][0]["evidence"].append({"new": "contradiction"})
    elif change == "dissent":
        decision = deepcopy(document["decisions"][0])
        decision["status"] = "contradicted"
        measurement["records"].append(
            {"artifact_id": "dissent", "reviewer": document["reviewer"], "decisions": [decision]}
        )
    else:
        rows[ident]["score"] = 1
    reviews.apply(rows, measurement)
    assert rows[ident]["score"] == (1 if change == "downgrade" else 2)
    assert rows[ident]["source_review_acceptance"]["status"].startswith("blocked")


def test_extension_rejects_changed_owning_projection_even_with_exact_bytes(tmp_path, monkeypatch):
    import rubric_example_assessment as example

    selection, _, _, _ = extension_fixture(tmp_path, monkeypatch)
    project = example.project

    def changed(root, selected):
        result = project(root, selected)
        if not selected["example_assessment"].get("review_ids") and selected["example_assessment"].get(
            "native_bundle_ids"
        ):
            result["route_gaps"].append("Supplemental observation contradicts source route.")
        return result

    monkeypatch.setattr(example, "project", changed)
    with pytest.raises(ValueError):
        extension_measure(tmp_path, selection)


def test_extension_rejects_unselected_review_role_alias(tmp_path, monkeypatch):
    selection, _, _, _ = extension_fixture(tmp_path, monkeypatch)
    helpers.add_source(tmp_path, selection, "unknown.json", "{}")
    selection["artifacts"][-1]["role"] = "review"
    with pytest.raises(ValueError):
        extension_measure(tmp_path, selection)


def test_extension_cannot_cite_unreviewed_supplemental_evidence(tmp_path, monkeypatch):
    selection, _, document, _ = extension_fixture(tmp_path, monkeypatch)
    supplemental = selection["artifacts"][-1]
    document["decisions"][0]["citations"] = [
        {
            "artifact_id": supplemental["id"],
            "start_line": 1,
            "end_line": 1,
            "quote": (tmp_path / supplemental["path"]).read_text(),
        }
    ]
    helpers.refresh(tmp_path, selection, "review.json", json.dumps(document))
    with pytest.raises(ValueError):
        extension_measure(tmp_path, selection)


def test_extension_records_scope_on_qualified_row(tmp_path, monkeypatch):
    selection, _, _, _ = extension_fixture(tmp_path, monkeypatch)
    report = extension_measure(tmp_path, selection)
    scope = report["measurements"]["example_assessment"]["source_reviews"]["records"][0]["scope"]
    acceptance = report["items"]["provenance.task-artifact-access"]["source_review_acceptance"]
    assert acceptance["scope"] == scope


def test_extension_preserves_every_original_native_projection(tmp_path, monkeypatch):
    import rubric_combined as combined

    selection, _, document, panel = extension_fixture(tmp_path, monkeypatch)
    original = {
        "scope": "eval-author.refund-heldout-panel.v1",
        "index_digest": "original",
        "counts": {},
        "controls": [],
    }
    calls = []

    def project(sources, selector, task):
        calls.append(selector)
        if selector.get("native_bundle_ids"):
            return [deepcopy(panel)]
        return [deepcopy(original)]

    monkeypatch.setattr(combined, "project_panels", project)
    with pytest.raises(ValueError):
        extension_measure(tmp_path, selection)
