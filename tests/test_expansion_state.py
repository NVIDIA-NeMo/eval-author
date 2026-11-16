# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_journey_state import ethos_ready, setup_ready

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(
        importlib.import_module(name) for name in ("journey_state", "expansion_state", "conversation_renderer")
    )


def start(modules, repo, *, handoff=False):
    state, expansion, _ = modules
    setup_ready(state, repo, journey="expand")
    saved = ethos_ready(state, repo)
    legacy = "Earlier requested additions"
    scope = {"legacy_context": legacy, "priorities": ["refunds"]}
    if handoff:
        audit = importlib.import_module("audit_state")
        lifecycle = audit.new_record()
        lifecycle["findings"] = {
            "report": {"path": ".eval-author/audit-coverage-report.md", "digest": "b" * 64},
            "content": {"path": ".eval-author/.working/audit/report-content.json", "digest": "c" * 64},
            "inputs": [],
            "source": "Reviewed audit findings",
        }
        lifecycle["next_action"] = {
            "kind": "improve",
            "recommendation_ids": ["R1", "R3"],
            "findings_digest": "a" * 64,
            "source": "User selected these audit recommendations",
        }
        audit.retain_handoff({"scopes": {}}, {"scopes": {"audit": {"lifecycle": lifecycle}, "expand": scope}})
    saved["scopes"]["expand"] = scope
    state.save(repo, saved)
    value = expansion.new_record(saved, question="Which supported improvement should we plan?", source="User request")
    return state.update(
        repo,
        {"scopes": {"expand": {"lifecycle": value}}, "pending_question": value["pending"]["question"]},
        expected_revision=saved["revision"],
    )


def payload(modules, saved):
    state, _, _ = modules
    view = state.view(saved)
    return {
        "stage": "expansion_continue",
        "state": view["state"],
        "revision": saved["revision"],
        "facts": {"expansion_observations": view["expansion_observations"]},
    }


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
    assert expansion.record(resumed)["schema_version"] == 1
    assert ("retained your selected audit improvements" in rendered) == handoff


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
    before = state.state_path(tmp_path).read_bytes()
    for revision, message in [(None, "expected-revision"), (saved["revision"] - 1, "State changed")]:
        with pytest.raises(ValueError, match=message):
            state.update(tmp_path, patch, expected_revision=revision)
        assert state.state_path(tmp_path).read_bytes() == before
    updated = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert updated["scopes"]["expand"] == {**saved["scopes"]["expand"], "pending_design": "Refund scenarios"}
    with pytest.raises(ValueError, match="Retain structured expansion"):
        state.update(tmp_path, {"scopes": {"expand": None}}, expected_revision=updated["revision"])


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


@pytest.mark.parametrize("mutation", ["version", "extra", "binding", "question", "source", "approval"])
def test_invalid_lifecycle_is_rejected_without_writes(modules, tmp_path, mutation):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    if mutation == "version":
        value["schema_version"] = True
    elif mutation == "extra":
        value["complete"] = True
    elif mutation == "binding":
        value["ethos_digest"] = "d" * 64
    elif mutation in {"question", "source"}:
        value["pending"][mutation] = ""
    else:
        value["pending"]["approved"] = True
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.update(tmp_path, {"scopes": {"expand": {"lifecycle": value}}}, expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


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
