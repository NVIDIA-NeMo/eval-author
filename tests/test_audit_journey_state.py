# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state"), importlib.import_module("audit_state")


def ready(modules, repo):
    state, _ = modules
    state.initialize(repo, agent="support", journey="audit")
    state.update(
        repo,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Observed shared audit prerequisites"}
                for key in state.COMMON_PREREQUISITES | {"data"}
            }
        },
    )
    (repo / "ETHOS.md").write_text("# Support agent intent\n")
    return state.confirm_ethos(
        repo,
        reviewed_digest=state.ethos_digest(repo),
        source="User confirmed the saved intent",
        validation="Ethos parser accepted the saved document",
    )


def forbidden(*_args, **_kwargs):
    raise AssertionError("An unrelated workflow's evidence must not be inspected")


def start_lifecycle(modules, repo):
    state, audit = modules
    saved = ready(modules, repo)
    return state.update(
        repo,
        {
            "scopes": {
                "audit": {
                    "framework": "NAT",
                    "framework_source": "Selected evaluation documentation",
                    "legacy_context": {"priorities": ["refunds"]},
                    "lifecycle": audit.new_record(),
                }
            }
        },
        expected_revision=saved["revision"],
    )


def test_typed_audit_updates_require_current_revision_and_preserve_scope_siblings(modules, tmp_path):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    lifecycle = copy.deepcopy(audit.record(saved))
    lifecycle["suites"]["candidates"] = ["evals/refunds"]
    patch = {"scopes": {"audit": {"lifecycle": lifecycle}}}
    original = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, patch)
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, patch, expected_revision=saved["revision"] - 1)
    assert state.state_path(tmp_path).read_bytes() == original
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert updated["scopes"]["audit"] == {**saved["scopes"]["audit"], "lifecycle": lifecycle}
    observed = state.update(
        tmp_path,
        {"scopes": {"audit": {"framework": "NAT / custom runner", "framework_source": "Current README"}}},
        expected_revision=updated["revision"],
    )
    assert audit.record(observed) == lifecycle
    assert observed["scopes"]["audit"]["legacy_context"] == saved["scopes"]["audit"]["legacy_context"]


@pytest.mark.parametrize("replacement", [None, "legacy", {"lifecycle": None}, {"lifecycle": {"pending": None}}])
def test_typed_audit_cannot_be_dropped_or_replaced_with_an_incomplete_record(modules, tmp_path, replacement):
    state, _ = modules
    saved = start_lifecycle(modules, tmp_path)
    original = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.update(tmp_path, {"scopes": {"audit": replacement}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == original


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_authoring_cannot_update_retained_audit_lifecycle_or_completion(modules, tmp_path, journey):
    state, audit = modules
    start_lifecycle(modules, tmp_path)
    saved = state.initialize(tmp_path, journey=journey, setup="harbor")
    lifecycle = copy.deepcopy(audit.record(saved))
    lifecycle["suites"]["candidates"] = ["evals/refunds"]
    original = state.state_path(tmp_path).read_bytes()
    for patch in ({"scopes": {"audit": {"lifecycle": lifecycle}}}, {"journey_status": {"audit": "in_progress"}}):
        with pytest.raises(ValueError, match="Select the audit journey"):
            state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == original


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_new_audit_lifecycle_cannot_claim_completion_during_authoring(modules, tmp_path, journey):
    state, audit = modules
    ready(modules, tmp_path)
    saved = state.initialize(tmp_path, journey=journey, setup="harbor")
    saved["journey_status"][journey] = "complete"
    state.save(tmp_path, saved)
    original = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Select the audit journey"):
        state.update(
            tmp_path,
            {"scopes": {"audit": {"lifecycle": audit.new_record()}}, "journey_status": {"audit": "complete"}},
            expected_revision=saved["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == original
    assert state.load_state(tmp_path)["journey_status"][journey] == "complete"


def test_domain_pending_checkpoint_survives_switch_and_resumes_without_reselection(modules, tmp_path):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    lifecycle = copy.deepcopy(audit.record(saved))
    lifecycle["suites"]["candidates"] = ["evals/refunds"]
    lifecycle["pending"] = {
        "stage": "suite_selection",
        "digest": audit.fingerprint(lifecycle["suites"]),
        "question": "Which evaluation suites should be included?",
        "source": "Presented the bounded suite inventory",
    }
    state.update(
        tmp_path,
        {"scopes": {"audit": {"lifecycle": lifecycle}}, "pending_question": lifecycle["pending"]["question"]},
        expected_revision=saved["revision"],
    )
    switched = state.initialize(tmp_path, journey="create", setup="harbor")
    assert switched["pending_question"] is None
    resumed = state.initialize(tmp_path, journey="audit")
    output = state.view(resumed)
    assert audit.record(resumed) == lifecycle
    assert output["audit"]["stage"] == "suite_selection"
    assert output["audit"]["pending_checkpoint"] == lifecycle["pending"]


def test_state_only_framework_observation_preserves_typed_audit_without_artifact_reads(modules, tmp_path, monkeypatch):
    state, audit = modules
    saved = start_lifecycle(modules, tmp_path)
    monkeypatch.setattr(audit, "observe", forbidden)
    monkeypatch.setattr(audit, "transition", forbidden)
    monkeypatch.setattr(state.first_eval_state, "observe", forbidden)
    monkeypatch.setattr(state, "ethos_digest", forbidden)
    updated = state.update(
        tmp_path,
        {"scopes": {"audit": {"framework": "Harbor", "framework_source": "User framework answer"}}},
        state_only=True,
        expected_revision=saved["revision"],
    )
    assert audit.record(updated) == audit.record(saved)
    assert updated["scopes"]["audit"]["legacy_context"] == saved["scopes"]["audit"]["legacy_context"]


def test_legacy_audit_completion_is_retained_without_current_completion_authority(modules, tmp_path):
    state, _ = modules
    saved = ready(modules, tmp_path)
    saved["scopes"]["audit"] = {"confirmed": True, "traces": ["earlier-run.json"]}
    saved["journey_status"]["audit"] = "complete"
    state.save(tmp_path, saved)
    original = state.state_path(tmp_path).read_bytes()
    output = state.view(state.load_state(tmp_path))
    assert output["state"]["journey_status"]["audit"] == "complete"
    assert output["audit"] is None
    assert not output["checklist"][2]["complete"]
    assert state.state_path(tmp_path).read_bytes() == original


def test_audit_completion_without_scope_patch_still_requires_revision_and_typed_proof(modules, tmp_path):
    state, _ = modules
    saved = ready(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    patch = {"journey_status": {"audit": "complete"}}
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, patch)
    with pytest.raises(ValueError, match="State changed"):
        state.update(tmp_path, patch, expected_revision=0)
    with pytest.raises(ValueError, match="Audit completion"):
        state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("journey", ["create", "expand"])
@pytest.mark.parametrize("field", ["scopes", "journey_status"])
def test_audit_updates_cannot_change_authoring_progress(modules, tmp_path, journey, field):
    state, _ = modules
    saved = ready(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Select an authoring journey"):
        state.update(
            tmp_path,
            {field: {journey: "in_progress" if field == "journey_status" else {"confirmed": False}}},
            expected_revision=saved["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == before


def test_audit_ethos_change_preserves_prior_authoring_until_return(modules, tmp_path, monkeypatch):
    state, _ = modules
    saved = ready(modules, tmp_path)
    for journey in ("create", "expand"):
        saved["scopes"][journey] = {"confirmed": True, "cases": ["refund"]}
        saved["journey_status"][journey] = "complete"
    state.save(tmp_path, saved)
    before = state.state_path(tmp_path).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(state.first_eval_state, "observe", forbidden)
        patch.setattr(state.first_eval_state, "progress", forbidden)
        patch.setattr(state, "task_lifecycle", forbidden)
        (tmp_path / "ETHOS.md").write_text("# Changed support intent\n")
        loaded = state.load_state(tmp_path)
        output = state.view(loaded)
        assert output["first_eval"] is None and output["task_readiness"] is None
        assert loaded["ethos"]["stage"] == "review"
        assert state.state_path(tmp_path).read_bytes() == before
        for journey in ("create", "expand"):
            assert loaded["journey_status"][journey] == "complete"
            assert loaded["scopes"][journey]["needs_review"]
        state.initialize(tmp_path, journey="audit")
        state.confirm_ethos(
            tmp_path,
            reviewed_digest=state.ethos_digest(tmp_path),
            source="User reviewed the changed intent",
            validation="Updated Ethos passed validation",
        )
    returned = state.initialize(tmp_path, journey="create", setup="harbor")
    assert returned["journey_status"] == {"create": "in_progress", "expand": "in_progress"}
    assert not state.checklist(returned)[2]["complete"]


def test_state_only_view_defers_both_domain_observations(modules, tmp_path, monkeypatch):
    state, audit = modules
    ready(modules, tmp_path)
    monkeypatch.setattr(audit, "observe", forbidden)
    monkeypatch.setattr(audit, "derive", forbidden)
    monkeypatch.setattr(state.first_eval_state, "observe", forbidden)
    monkeypatch.setattr(state.first_eval_state, "derive", forbidden)
    monkeypatch.setattr(state, "ethos_digest", forbidden)
    output = state.view(state.load_state(tmp_path, state_only=True), state_only=True)
    for field in ("audit", "audit_observations", "first_eval", "first_eval_observations", "task_readiness"):
        assert output[field] is None


def test_authoring_view_does_not_inspect_retained_audit_sources(modules, tmp_path, monkeypatch):
    state, audit = modules
    ready(modules, tmp_path)
    state.initialize(tmp_path, journey="create", setup="harbor")
    monkeypatch.setattr(audit, "observe", forbidden)
    monkeypatch.setattr(audit, "derive", forbidden)
    output = state.view(state.load_state(tmp_path))
    assert output["audit"] is None and output["audit_observations"] is None
