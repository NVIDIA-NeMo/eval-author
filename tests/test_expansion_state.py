# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from journey_state_support import ethos_ready, setup_ready
from journey_state_support import expansion_payload as payload
from journey_state_support import expansion_start as start

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(
        importlib.import_module(name) for name in ("journey_state", "expansion_state", "conversation_renderer")
    )


@pytest.mark.parametrize("handoff", [False, True])
def test_entry_resume_retains_context_and_question(modules, tmp_path, handoff):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=handoff)
    before = copy.deepcopy(saved["scopes"])
    rendered = renderer.render(payload(modules, saved))
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["scopes"] == before
    assert resumed["pending_question"] == saved["pending_question"]
    assert renderer.render(payload(modules, resumed)) == rendered
    assert not state.view(resumed)["expansion"]["complete"]
    assert not state.checklist(resumed)[2]["complete"]
    assert expansion.record(resumed)["checkpoint"] == "entry"


def test_audit_handoff_can_enter_planning_before_scope_agreement(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    scope = saved["scopes"]["expand"]
    assert scope["needs_review"] and scope["confirmed"] is False
    assert scope["priorities"] == ["refunds"]
    assert expansion.record(saved)["handoff_digest"] == expansion.handoff_digest(saved)
    assert state.view(saved)["expansion"]["context_current"]
    assert not state.checklist(saved)[2]["complete"]
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["scopes"]["expand"] == scope
    assert renderer.render(payload(modules, resumed)) == renderer.render(payload(modules, saved))


def test_updates_preserve_context_and_require_current_revision(modules, tmp_path):
    state, _, _ = modules
    saved = start(modules, tmp_path)
    patch = {"scopes": {"expand": {"pending_design": "Refund scenarios"}}}
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    for revision, message in [(None, "expected-revision"), (saved["revision"] - 1, "State changed")]:
        with pytest.raises(ValueError, match=message):
            state.update(tmp_path, patch, expected_revision=revision)
        assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert updated["scopes"]["expand"] == {**saved["scopes"]["expand"], "pending_design": "Refund scenarios"}
    with pytest.raises(ValueError, match="Retain structured expansion"):
        state.update(tmp_path, {"scopes": {"expand": None}}, expected_revision=updated["revision"])


def test_expansion_decision_preserves_creation_scope_and_task_acceptance(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    creation_scope = {
        "cases": ["account-lookup"],
        "confirmed": True,
        "source": "User approved the earlier starter suite",
    }
    saved["scopes"]["create"] = copy.deepcopy(creation_scope)
    saved["journey_status"]["create"] = "complete"
    state.save(tmp_path, saved)
    task_path = ".eval-author/tasks/account-lookup"
    task = tmp_path / task_path
    task.mkdir(parents=True)
    (task / "README.md").write_text("# Account lookup\n")
    evidence_path = ".eval-author/.working/account-lookup-controls.json"
    (tmp_path / evidence_path).write_text('{"result": "passed"}\n')
    saved = state.record_task(
        tmp_path,
        task=task_path,
        status="accepted",
        source="Earlier starter-suite task review",
        evidence=[evidence_path],
    )
    tasks = copy.deepcopy(saved["tasks"])
    handoff = copy.deepcopy(saved["scopes"]["expand"]["audit_handoff"])
    question = "Which refund edge case should the new task cover?"
    lifecycle = expansion.new_record(saved, question=question, source="User narrows the selected improvement")
    updated = state.update(
        tmp_path,
        {"scopes": {"expand": {"lifecycle": lifecycle}}, "pending_question": question},
        expected_revision=saved["revision"],
    )
    resumed = state.initialize(tmp_path, journey="expand")
    for current in (updated, resumed):
        assert current["scopes"]["create"] == creation_scope
        assert current["tasks"] == tasks
        assert current["journey_status"]["create"] == "complete"
        assert current["scopes"]["expand"]["audit_handoff"] == handoff
        assert state.task_lifecycle(tmp_path, current, task_path)["status"] == "accepted"
        assert not state.checklist(current)[2]["complete"]
        assert question in renderer.render(payload(modules, current))


@pytest.mark.parametrize("change", ["ethos", "handoff"])
def test_context_changes_block_stale_entry_without_erasing_it(modules, tmp_path, change):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    lifecycle = copy.deepcopy(expansion.record(saved))
    if change == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Changed intent\n")
    else:
        saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
        state.save(tmp_path, saved)
    current = state.load_state(tmp_path)
    assert expansion.record(current) == lifecycle
    assert not state.view(current)["expansion"]["context_current"]
    with pytest.raises(ValueError, match="context changed"):
        renderer.render(payload(modules, current))


@pytest.mark.parametrize(
    "mutation",
    ["checkpoint", "old_version", "missing_setup", "extra", "binding", "setup", "question", "source", "approval"],
)
def test_invalid_lifecycle_is_rejected_without_writes(modules, tmp_path, mutation):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    if mutation == "checkpoint":
        value["checkpoint"] = "unsupported"
    elif mutation == "old_version":
        value["schema_version"] = 1
        del value["checkpoint"]
    elif mutation == "missing_setup":
        del value["setup"]
    elif mutation == "extra":
        value["complete"] = True
    elif mutation == "binding":
        value["ethos_digest"] = "d" * 64
    elif mutation == "setup":
        value["setup"] = "unsupported"
    elif mutation in {"question", "source"}:
        value["pending"][mutation] = ""
    else:
        value["pending"]["approved"] = True
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.update(tmp_path, {"scopes": {"expand": {"lifecycle": value}}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


def test_provider_binding_requires_entry_reconciliation(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    saved = state.initialize(tmp_path, setup="harbor")
    assert saved["pending_question"] is None
    saved = state.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Rechecked Harbor prerequisites"}
                for key in state.PROVIDER_DEPENDENT
            }
        },
        expected_revision=saved["revision"],
    )
    original_scope = copy.deepcopy(saved["scopes"]["expand"])
    resumed = state.initialize(tmp_path, journey="expand")
    current = state.load_state(tmp_path)
    assert current == resumed
    assert expansion.entry_ready(current)
    assert current["scopes"]["expand"] == original_scope
    assert current["pending_question"] is None
    assert not state.view(current)["expansion"]["context_current"]
    with pytest.raises(ValueError, match="context changed"):
        renderer.render(payload(modules, current))
    question = f"Which {current['setup']} task should the suite add?"
    value = expansion.new_record(current, question=question, source="User reconciled the provider context")
    updated = state.update(
        tmp_path,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": question},
        expected_revision=current["revision"],
    )
    assert updated["scopes"]["expand"] == {**original_scope, "lifecycle": value}
    resumed = state.initialize(tmp_path, journey="expand")
    assert state.view(resumed)["expansion"]["context_current"]
    assert question in renderer.render(payload(modules, resumed))


def test_entry_cannot_claim_completion_or_cross_journey_progress(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    with pytest.raises(ValueError, match="cannot establish completion"):
        state.update(tmp_path, {"journey_status": {"expand": "complete"}}, expected_revision=saved["revision"])
    switched = state.initialize(tmp_path, journey="create")
    with pytest.raises(ValueError, match="Select the expansion journey"):
        state.update(
            tmp_path,
            {"scopes": {"expand": {"lifecycle": expansion.record(switched)}}},
            expected_revision=switched["revision"],
        )


def test_renderer_is_pure_and_matches_fixture(modules, tmp_path, monkeypatch):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path)
    data = payload(modules, saved)
    expected = (Path(__file__).parent / "fixtures/expansion-conversations/entry.md").read_text().split("\n\n", 1)[1]
    before = copy.deepcopy(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not inspect or change files")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(expansion, "observe", forbidden)
    monkeypatch.setattr(state, "load_state", forbidden)
    monkeypatch.setattr(state, "save", forbidden)
    assert renderer.render(data) == renderer.render(data) == expected
    assert data == before


@pytest.mark.parametrize("change", ["revision", "observations", "facts", "question", "blocker", "setup", "journey"])
def test_renderer_rejects_wrong_authority(modules, tmp_path, change):
    _, _, renderer = modules
    data = payload(modules, start(modules, tmp_path))
    if change == "revision":
        data["revision"] -= 1
    elif change == "observations":
        data["facts"]["expansion_observations"]["revision"] -= 1
    elif change == "facts":
        data["facts"]["complete"] = True
    elif change == "question":
        data["state"]["pending_question"] = "Another question"
    elif change == "blocker":
        data["state"]["blockers"] = [{"id": "missing", "phase": "expand", "message": "Missing inputs"}]
    elif change == "setup":
        data["state"]["prerequisites"]["runtime"]["status"] = "missing"
    else:
        data["state"]["journey"] = "create"
    with pytest.raises(ValueError):
        renderer.render(data)


@pytest.mark.parametrize(
    "claim",
    [
        {"complete": True},
        {"scope_approval": {"approved": True, "source": "Display text"}},
        {"execution_approval": {"approved": True, "source": "Display text"}},
        {"accepted_tasks": [".eval-author/tasks/refund"]},
    ],
)
def test_display_claims_cannot_establish_progress_or_permission(modules, tmp_path, claim):
    state, _, renderer = modules
    saved = start(modules, tmp_path)
    data = payload(modules, saved)
    data["facts"].update(claim)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        renderer.render(data)
    assert state.state_path(tmp_path).read_bytes() == before
    current = state.load_state(tmp_path)
    assert current == saved
    assert current["tasks"] == {}
    assert not state.view(current)["expansion"]["complete"]


def test_unstructured_legacy_context_stays_readable(modules, tmp_path):
    state, expansion, _ = modules
    saved = state.initialize(tmp_path, journey="expand")
    saved["scopes"]["expand"] = "Prior suite expansion notes"
    state.save(tmp_path, saved)
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["scopes"]["expand"] == "Prior suite expansion notes"
    assert expansion.record(resumed) is None
    assert state.view(resumed)["expansion"] is None


def test_adopting_legacy_scope_preserves_it(modules, tmp_path):
    state, expansion, _ = modules
    setup_ready(state, tmp_path, journey="expand")
    saved = ethos_ready(state, tmp_path)
    saved["scopes"]["expand"] = "Prior suite expansion notes"
    state.save(tmp_path, saved)
    value = expansion.new_record(saved, question="Which supported addition?", source="User entry request")
    adopted = state.update(
        tmp_path,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
        expected_revision=saved["revision"],
    )
    assert adopted["scopes"]["expand"]["legacy_context"] == "Prior suite expansion notes"
    assert state.view(adopted)["expansion"]["context_current"]


def test_adopting_mapping_scope_preserves_existing_fields(modules, tmp_path):
    state, expansion, _ = modules
    setup_ready(state, tmp_path, journey="expand")
    saved = ethos_ready(state, tmp_path)
    scope = {
        "priorities": ["refunds"],
        "existing_suite": "tests/refunds",
        "pending_design": "Refund edge cases",
    }
    saved["scopes"]["expand"] = copy.deepcopy(scope)
    state.save(tmp_path, saved)
    value = expansion.new_record(saved, question="Which supported addition?", source="User entry request")
    adopted = state.update(
        tmp_path,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
        expected_revision=saved["revision"],
    )
    assert adopted["scopes"]["expand"] == {**scope, "lifecycle": value}
    assert state.view(adopted)["expansion"]["context_current"]


def test_pending_question_cannot_be_cleared_independently(modules, tmp_path):
    state, _, _ = modules
    saved = start(modules, tmp_path)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="expected-revision"):
        state.update(tmp_path, {"pending_question": None})
    with pytest.raises(ValueError, match="Retain the unresolved"):
        state.update(tmp_path, {"pending_question": None}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


def test_returning_from_another_journey_restores_entry_question(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    original_scope = copy.deepcopy(saved["scopes"]["expand"])
    switched = state.initialize(tmp_path, journey="create")
    assert switched["pending_question"] is None
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["scopes"]["expand"] == original_scope
    assert resumed["pending_question"] == expansion.record(saved)["pending"]["question"]
    updated = state.update(tmp_path, {"next_handoff": "eval-author-task-create"})
    assert renderer.render(payload(modules, updated)) == renderer.render(payload(modules, saved))


@pytest.mark.parametrize("state_only", [False, True])
def test_prerequisite_recovery_takes_precedence_then_restores_entry(modules, tmp_path, state_only):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    original_scope = copy.deepcopy(saved["scopes"]["expand"])
    recovery_question = "Restore the missing runtime and confirm it is available."
    blocked = state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "missing", "source": "Runtime probe failed"}},
            "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime is unavailable"}],
            "pending_question": recovery_question,
        },
        state_only=state_only,
        expected_revision=saved["revision"],
    )
    assert blocked["pending_question"] == recovery_question
    assert state.load_state(tmp_path)["pending_question"] == recovery_question
    with pytest.raises(ValueError, match="saved blockers"):
        renderer.render(payload(modules, blocked))
    repaired = state.update(
        tmp_path,
        {
            "prerequisites": {"runtime": {"status": "verified", "source": "Runtime probe passed"}},
            "blockers": [],
            "pending_question": None,
        },
        state_only=state_only,
        expected_revision=blocked["revision"],
    )
    if state_only:
        assert repaired["pending_question"] is None
    current = state.load_state(tmp_path)
    assert current["pending_question"] == expansion.record(saved)["pending"]["question"]
    assert current["scopes"]["expand"] == original_scope
    assert renderer.render(payload(modules, current)) == renderer.render(payload(modules, saved))


@pytest.mark.parametrize("changed", ["handoff", "ethos"])
def test_returning_does_not_restore_question_for_stale_context(modules, tmp_path, changed):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    state.initialize(tmp_path, journey="create")
    if changed == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Changed intended behavior\n")
    else:
        saved = state.load_state(tmp_path)
        saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
        state.save(tmp_path, saved)
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["pending_question"] != expansion.record(resumed)["pending"]["question"]
    with pytest.raises(ValueError, match="context changed"):
        renderer.render(payload(modules, resumed))


def test_state_only_view_defers_expansion_inspection(modules, tmp_path, monkeypatch):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Prerequisite-only inspection must not inspect expansion artifacts")

    monkeypatch.setattr(expansion, "observe", forbidden)
    view = state.view(saved, state_only=True)
    assert view["expansion"] is None
    assert view["expansion_observations"] is None


@pytest.mark.parametrize("state_only", [False, True])
def test_unchanged_ethos_confirmation_resumes_retained_entry(modules, tmp_path, state_only):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    scope = copy.deepcopy(saved["scopes"]["expand"])
    review = state.update(
        tmp_path,
        {
            "ethos": {"stage": "review"},
            "pending_question": "Is this still the correct document and intended behavior?",
            "next_handoff": "ethos-orchestrate",
        },
        expected_revision=saved["revision"],
        state_only=state_only,
    )
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User confirms unchanged saved intent",
        validation="Saved-file validation passed",
        expected_revision=review["revision"],
    )
    assert confirmed["pending_question"] == expansion.record(saved)["pending"]["question"]
    assert confirmed["scopes"]["expand"] == scope
    resumed = state.initialize(tmp_path, journey="expand")
    updated = state.update(tmp_path, {"next_handoff": "eval-author-task-create"})
    assert updated["pending_question"] == resumed["pending_question"] == confirmed["pending_question"]
    assert renderer.render(payload(modules, updated)) == renderer.render(payload(modules, saved))


def test_ethos_confirmation_keeps_unresolved_intent_question(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    question = "Which account recovery actions require approval?"
    review = state.update(
        tmp_path,
        {"ethos": {"stage": "review", "remaining_questions": [question]}, "pending_question": question},
        expected_revision=saved["revision"],
    )
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Resolve the saved intent questions"):
        state.confirm_ethos(
            tmp_path,
            reviewed_digest=state.ethos_digest(tmp_path),
            source="User confirms unchanged saved intent",
            validation="Saved-file validation passed",
            expected_revision=review["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert state.load_state(tmp_path)["pending_question"] == question
    resolved = state.update(
        tmp_path,
        {"ethos": {"remaining_questions": []}},
        expected_revision=review["revision"],
    )
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User resolved saved intent questions and confirms the document",
        validation="Saved-file validation passed",
        expected_revision=resolved["revision"],
    )
    assert confirmed["pending_question"] == expansion.record(saved)["pending"]["question"]
    assert renderer.render(payload(modules, confirmed)) == renderer.render(payload(modules, saved))


def test_unresolved_saved_intent_blocks_entry_adoption_without_writes(modules, tmp_path):
    state, expansion, _ = modules
    setup_ready(state, tmp_path, journey="expand")
    ethos_ready(state, tmp_path)
    saved = state.update(tmp_path, {"ethos": {"remaining_questions": ["Which actions require approval?"]}})
    assert saved["ethos"]["stage"] == "complete"
    value = expansion.new_record(saved, question="Which supported addition?", source="User entry request")
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Expansion entry requires"):
        state.update(
            tmp_path,
            {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
            expected_revision=saved["revision"],
        )
    assert state.state_path(tmp_path).read_bytes() == before
    assert expansion.record(state.load_state(tmp_path)) is None


@pytest.mark.parametrize("clear_pending", [False, True])
def test_unresolved_saved_intent_blocks_entry_continuation_and_restoration(modules, tmp_path, clear_pending):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    patch = {"ethos": {"remaining_questions": ["Which account recovery actions require approval?"]}}
    if clear_pending:
        patch["pending_question"] = None
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    before = state.state_path(tmp_path).read_bytes()
    current = state.load_state(tmp_path)
    assert current["ethos"]["stage"] == "complete"
    assert current["pending_question"] == (None if clear_pending else saved["pending_question"])
    assert current["scopes"]["expand"] == saved["scopes"]["expand"]
    assert not expansion.entry_ready(current)
    with pytest.raises(ValueError, match="Resolve shared prerequisites, intent questions, and saved blockers"):
        renderer.render(payload(modules, current))
    with pytest.raises(ValueError, match="Resolve the saved intent questions"):
        renderer.render({"stage": "resume", "state": current, "revision": updated["revision"]})
    assert state.state_path(tmp_path).read_bytes() == before


def test_unresolved_saved_intent_recovers_before_retained_entry(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    scope = copy.deepcopy(saved["scopes"]["expand"])
    question = "Which account recovery actions require approval?"
    unresolved = state.update(
        tmp_path,
        {"ethos": {"remaining_questions": [question]}, "pending_question": None},
        expected_revision=saved["revision"],
    )
    review = state.update(
        tmp_path,
        {"ethos": {"stage": "review"}, "pending_question": question, "next_handoff": "ethos-orchestrate"},
        expected_revision=unresolved["revision"],
    )
    resumed = state.load_state(tmp_path)
    assert resumed["pending_question"] == question
    assert resumed["scopes"]["expand"] == scope
    output = renderer.render(
        {
            "stage": "resume",
            "state": resumed,
            "revision": review["revision"],
            "facts": {
                "ethos": {
                    "status": "existing",
                    "link": str(tmp_path / "ETHOS.md"),
                    "summary": "The saved document describes the agent's intended behavior.",
                }
            },
        }
    )
    assert question in output
    assert expansion.record(saved)["pending"]["question"] not in output
    resolved = state.update(
        tmp_path,
        {"ethos": {"remaining_questions": []}},
        expected_revision=review["revision"],
    )
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User resolved the saved intent question and confirms the document",
        validation="Saved-file validation passed",
        expected_revision=resolved["revision"],
    )
    assert confirmed["scopes"]["expand"] == scope
    assert confirmed["pending_question"] == expansion.record(saved)["pending"]["question"]
    assert renderer.render(payload(modules, confirmed)) == renderer.render(payload(modules, saved))


@pytest.mark.parametrize("change", ["ethos", "handoff"])
def test_ethos_confirmation_does_not_resume_stale_entry(modules, tmp_path, change):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    question = "Is this still the correct document and intended behavior?"
    review = state.update(
        tmp_path,
        {"ethos": {"stage": "review"}, "pending_question": question},
        expected_revision=saved["revision"],
        state_only=True,
    )
    if change == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Changed agent intent\n")
    else:
        review["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
        state.save(tmp_path, review)
    reconciled = state.load_state(tmp_path)
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User confirms saved intent",
        validation="Saved-file validation passed",
    )
    assert confirmed["pending_question"] == reconciled["pending_question"]
    assert confirmed["pending_question"] != expansion.record(saved)["pending"]["question"]
    assert expansion.record(confirmed) == expansion.record(saved)
    assert not state.view(confirmed)["expansion"]["context_current"]
    with pytest.raises(ValueError, match="context changed"):
        renderer.render(payload(modules, confirmed))


@pytest.mark.parametrize("phase", ["setup", "ethos", "expand"])
def test_ethos_confirmation_preserves_active_blocker_recovery(modules, tmp_path, phase):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    question = "Resolve the unavailable dependency before continuing."
    review = state.update(
        tmp_path,
        {
            "ethos": {"stage": "review"},
            "blockers": [{"id": "dependency", "phase": phase, "message": "Required dependency unavailable"}],
            "pending_question": question,
        },
        expected_revision=saved["revision"],
        state_only=True,
    )
    confirmed = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User confirms unchanged saved intent",
        validation="Saved-file validation passed",
        expected_revision=review["revision"],
    )
    assert confirmed["blockers"] == review["blockers"]
    assert confirmed["pending_question"] == state.load_state(tmp_path)["pending_question"] == question
    repaired = state.update(
        tmp_path,
        {"blockers": [], "pending_question": None},
        expected_revision=confirmed["revision"],
    )
    assert repaired["pending_question"] == expansion.record(saved)["pending"]["question"]
    assert renderer.render(payload(modules, repaired)) == renderer.render(payload(modules, saved))


def test_fresh_entry_binding_after_intent_review_preserves_handoff(modules, tmp_path):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, handoff=True)
    original = copy.deepcopy(saved["scopes"]["expand"]["audit_handoff"])
    (tmp_path / "ETHOS.md").write_text("# Revised agent intent\n")
    state.load_state(tmp_path)
    saved = state.confirm_ethos(
        tmp_path,
        reviewed_digest=state.ethos_digest(tmp_path),
        source="User reviewed revised intent",
        validation="Ethos validation passed",
    )
    question = "Which selected improvement still applies to the revised intent?"
    value = expansion.new_record(saved, question=question, source="Reconcile user's changed intent")
    updated = state.update(
        tmp_path,
        {
            "scopes": {"expand": {"lifecycle": value}},
            "pending_question": question,
        },
        expected_revision=saved["revision"],
    )
    assert updated["scopes"]["expand"]["audit_handoff"] == original
    assert updated["scopes"]["expand"]["needs_review"]
    assert updated["scopes"]["expand"]["confirmed"] is False
    assert question in renderer.render(payload(modules, updated))


@pytest.mark.parametrize("handoff", [False, True])
def test_cli_continuation_uses_the_same_stateful_contract(modules, tmp_path, handoff):
    state, _, renderer = modules
    saved = start(modules, tmp_path, handoff=handoff)
    data = payload(modules, saved)
    before = state.state_path(tmp_path).read_bytes()
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "conversation_renderer.py"), "--input", "-"],
        input=json.dumps(data),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == renderer.render(data)
    assert state.state_path(tmp_path).read_bytes() == before
    data["revision"] -= 1
    rejected = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "conversation_renderer.py"), "--input", "-"],
        input=json.dumps(data),
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 2 and not rejected.stdout
    assert "revision differs" in json.loads(rejected.stderr)["error"]
    assert state.state_path(tmp_path).read_bytes() == before


def test_prerequisite_entry_choice_preserves_bound_lifecycle_and_audit_handoff(modules, tmp_path, monkeypatch):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, handoff=True)
    scope = copy.deepcopy(saved["scopes"]["expand"])
    choice = {"kind": "starter", "source": "User chose starter expansion"}

    def forbidden(*args, **kwargs):
        raise AssertionError("Path selection must not inspect artifacts")

    monkeypatch.setattr(expansion, "observe", forbidden)
    monkeypatch.setattr(state, "ethos_digest", forbidden)
    updated = state.update(
        tmp_path,
        {
            "scopes": {"expand": {"entry_choice": choice}},
            "blockers": [{"id": "python", "phase": "setup", "message": "Runtime missing"}],
            "pending_question": "Can you restore the runtime?",
        },
        expected_revision=saved["revision"],
        state_only=True,
    )
    assert updated["scopes"]["expand"] == {**scope, "entry_choice": choice}
    resumed = state.initialize(tmp_path, journey="expand", state_only=True)
    assert resumed["scopes"] == updated["scopes"]
    assert resumed["pending_question"] == "Can you restore the runtime?"
    assert resumed["ethos"] == saved["ethos"]
