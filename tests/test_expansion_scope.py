# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from journey_state_support import expansion_scope_approve as approve
from journey_state_support import expansion_scope_decide as decide
from journey_state_support import expansion_scope_plan, expansion_start
from journey_state_support import expansion_scope_present as present
from journey_state_support import expansion_scope_progress as progress
from journey_state_support import expansion_scope_replace as replace_scope
from journey_state_support import expansion_scope_start as start

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return tuple(
        importlib.import_module(name) for name in ("journey_state", "expansion_state", "conversation_renderer")
    )


@pytest.mark.parametrize("path", ["audit_tool_gap", "starter_suite", "recorded_interaction"])
def test_only_current_presented_scope_approval_permits_construction(modules, tmp_path, path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path=path)
    tasks = copy.deepcopy(saved["tasks"])
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, saved, task_id="cover-refund", expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before
    agreed = approve(modules, tmp_path, saved)
    ready = progress(modules, tmp_path, agreed)
    assert ready["scope_approved"] and ready["construction_ready"]
    before = state.state_path(tmp_path).read_bytes()
    allowed = expansion.construction_guard(
        tmp_path, agreed, task_id="cover-refund", expected_revision=agreed["revision"]
    )
    assert allowed["task"]["id"] == "cover-refund"
    assert allowed["limits"] == expansion.record(agreed)["plan"]["limits"]
    assert state.state_path(tmp_path).read_bytes() == before
    assert agreed["tasks"] == tasks
    assert not ready["complete"]
    assert agreed["journey_status"].get("expand") != "complete"
    assert "execution_approval" not in allowed


def test_approval_requires_a_previously_saved_presentation(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["presented"] = {"digest": expansion.scope_digest(saved), "source": "The scope table was displayed"}
    value["decision"] = {"digest": expansion.scope_digest(saved), "kind": "approved", "source": "User approves"}
    value["pending"] = None
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        replace_scope(modules, tmp_path, saved, value)
    assert state.state_path(tmp_path).read_bytes() == before
    assert progress(modules, tmp_path, saved)["construction_ready"] is False


@pytest.mark.parametrize("revision", [None, -1])
def test_scope_mutation_and_guard_require_exact_revision_without_writes(modules, tmp_path, revision):
    state, expansion, _ = modules
    saved = approve(modules, tmp_path, start(modules, tmp_path))
    expected = revision if revision is None else saved["revision"] - 1
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        state.update(
            tmp_path,
            {"scopes": {"expand": {"lifecycle": expansion.record(saved)}}},
            expected_revision=expected,
        )
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, saved, task_id="cover-refund", expected_revision=expected)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "missing", ["no_tasks", "selection", "examples", "task_input", "plan_input", "limit_input", "trace_admission"]
)
def test_incomplete_proposals_cannot_be_approved(modules, tmp_path, missing):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path="recorded_interaction" if missing == "trace_admission" else "audit_tool_gap")
    value = copy.deepcopy(expansion.record(saved))
    questions = [
        "Review the selected interaction for privacy",
        "Confirm task-candidate admission from the recorded tools",
    ]
    if missing == "no_tasks":
        value["plan"]["tasks"] = []
        value["plan"]["selected_task_ids"] = []
    elif missing == "selection":
        value["plan"]["selected_task_ids"] = []
    elif missing == "examples":
        value["plan"]["tasks"][0]["example_ids"] = []
    elif missing == "task_input":
        value["plan"]["tasks"][0]["unresolved_inputs"] = ["Which refund amount should the case use?"]
    elif missing == "plan_input":
        value["plan"]["unresolved_inputs"] = ["Which test account may the new task use?"]
    elif missing == "trace_admission":
        value["plan"]["tasks"][0]["unresolved_inputs"] = questions
    else:
        value["plan"]["limits"]["unresolved_inputs"] = ["What construction cost cap should apply?"]
    saved = replace_scope(modules, tmp_path, saved, value)
    if missing == "trace_admission":
        assert expansion.record(saved)["plan"]["tasks"][0]["unresolved_inputs"] == questions
    assert progress(modules, tmp_path, saved)["blockers"]
    shown = present(modules, tmp_path, saved)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        decide(modules, tmp_path, shown)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("change", ["outcome", "scenario", "grading", "limits", "selection"])
def test_scope_edits_invalidate_only_the_affected_agreement(modules, tmp_path, change):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path="starter_suite")
    unrelated_tasks = copy.deepcopy(saved["tasks"])
    saved = approve(modules, tmp_path, saved)
    old = copy.deepcopy(expansion.record(saved))
    value = copy.deepcopy(old)
    if change == "outcome":
        value["request"]["outcome"] = "Add declined-refund coverage"
    elif change in {"scenario", "grading"}:
        value["plan"]["tasks"][0][change] += " for an expired order"
    elif change == "limits":
        value["plan"]["limits"]["max_minutes"] = 10
    else:
        value["plan"]["selected_task_ids"] = []
    value["pending"] = {"question": "Do you approve the revised scope?", "source": "User changed the proposal"}
    changed = replace_scope(modules, tmp_path, saved, value)
    assert expansion.scope_digest(changed) != old["decision"]["digest"]
    assert not progress(modules, tmp_path, changed)["construction_ready"]
    assert changed["tasks"] == unrelated_tasks
    assert old["decision"] in ([expansion.record(changed)["decision"]] + expansion.record(changed)["history"])
    assert expansion.record(changed)["plan"]["limits"]["max_cost_usd"] == 3
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, changed, task_id="cover-refund", expected_revision=changed["revision"])
    resumed = state.initialize(tmp_path, journey="expand")
    assert resumed["pending_question"] == "Do you approve the revised scope?"
    assert resumed["tasks"] == unrelated_tasks


@pytest.mark.parametrize("change", ["source", "ethos", "audit_content", "audit_selection"])
def test_changed_context_blocks_construction_without_erasing_history(modules, tmp_path, change):
    state, expansion, _ = modules
    saved = approve(modules, tmp_path, start(modules, tmp_path))
    original = copy.deepcopy(expansion.record(saved))
    if change == "source":
        (tmp_path / original["plan"]["sources"][0]["path"]).write_text("{}\n")
    elif change == "ethos":
        (tmp_path / "ETHOS.md").write_text("# Changed intent\n")
    elif change == "audit_content":
        handoff = saved["scopes"]["expand"]["audit_handoff"]
        (tmp_path / handoff["content"]["path"]).write_text('{"recommendations": []}\n')
    else:
        saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = ["R2"]
        state.save(tmp_path, saved)
    current = state.load_state(tmp_path)
    assert expansion.record(current) == original
    assert not progress(modules, tmp_path, current)["construction_ready"]
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, current, task_id="cover-refund", expected_revision=current["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "path,change",
    [
        ("starter_suite", "agent"),
        ("recorded_interaction", "agent"),
        ("starter_suite", "ethos_path"),
        ("recorded_interaction", "repository"),
    ],
)
def test_changed_identity_requires_a_new_scope_presentation_and_reply(modules, tmp_path, path, change):
    state, expansion, _ = modules
    if change == "repository":
        tmp_path = tmp_path / "original"
        tmp_path.mkdir()
    saved = approve(modules, tmp_path, start(modules, tmp_path, path=path))
    original = copy.deepcopy(expansion.record(saved))
    if change == "agent":
        changed = state.initialize(tmp_path, agent="replacement-assistant")
        changed = state.update(
            tmp_path,
            {
                "prerequisites": {
                    key: {"status": "verified", "source": "Rechecked access for the selected agent"}
                    for key in state.AGENT_DEPENDENT
                },
                "pending_question": None,
            },
            expected_revision=changed["revision"],
        )
    elif change == "repository":
        relocated = tmp_path.with_name("relocated")
        tmp_path.rename(relocated)
        tmp_path = relocated
        changed = state.rebind(tmp_path, previous_repository=saved["repository"], expected_revision=saved["revision"])
        changed = state.update(
            tmp_path,
            {
                "prerequisites": {
                    key: {"status": "verified", "source": "Rechecked runtime in the relocated repository"}
                    for key in ("python", "runtime")
                },
                "pending_question": None,
            },
            expected_revision=changed["revision"],
        )
    else:
        document = tmp_path / "agents/ETHOS.md"
        document.parent.mkdir()
        document.write_bytes((tmp_path / "ETHOS.md").read_bytes())
        changed = state.update(tmp_path, {"ethos": {"path": "agents/ETHOS.md"}}, expected_revision=saved["revision"])
    if path != "recorded_interaction":
        selected_path = changed["ethos"]["path"]
        changed = state.confirm_ethos(
            tmp_path,
            reviewed_digest=state.ethos_digest(tmp_path, selected_path),
            ethos_path=selected_path,
            source="User confirms the same document content for the selected agent and path",
            validation="Saved-file validation passed",
            expected_revision=changed["revision"],
        )
        assert changed["ethos"]["confirmed_digest"] == original["ethos_digest"]
    assert expansion.record(changed) == original
    assert not progress(modules, tmp_path, changed)["scope_approved"]
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, changed, task_id="cover-refund", expected_revision=changed["revision"])
    reply = "User approves this scope for the selected repository, agent, and document"
    with pytest.raises(ValueError):
        decide(modules, tmp_path, changed, source=reply)
    assert state.state_path(tmp_path).read_bytes() == before
    agreed = decide(modules, tmp_path, present(modules, tmp_path, changed), source=reply)
    assert expansion.record(agreed)["history"] == [original["decision"]]
    assert expansion.construction_guard(tmp_path, agreed, task_id="cover-refund", expected_revision=agreed["revision"])


@pytest.mark.parametrize("recovery", ["prerequisite", "changed_ethos", "unchanged_review"])
def test_scope_uses_shared_recovery_and_restores_its_question_after_unchanged_review(modules, tmp_path, recovery):
    state, expansion, renderer = modules
    saved = start(modules, tmp_path, path="starter_suite")
    original = copy.deepcopy(expansion.record(saved))
    if recovery == "prerequisite":
        question = "Restore the evaluation runtime before continuing."
        current = state.update(
            tmp_path,
            {
                "prerequisites": {"runtime": {"status": "missing", "source": "Runtime probe failed"}},
                "blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
                "pending_question": question,
            },
            expected_revision=saved["revision"],
        )
        stage = "results"
        facts = {
            "blockers": [
                {"id": "runtime", "step": "Prerequisites", "reason": "Runtime unavailable", "needed": question}
            ]
        }
    else:
        if recovery == "changed_ethos":
            (tmp_path / "ETHOS.md").write_text("# Revised agent intent\n")
            current = state.load_state(tmp_path)
        else:
            current = state.update(
                tmp_path,
                {"ethos": {"stage": "review"}, "pending_question": "Does the saved intent still apply?"},
                expected_revision=saved["revision"],
            )
        question = current["pending_question"]
        stage = "resume"
        facts = {
            "ethos": {
                "status": "existing",
                "link": str(tmp_path / "ETHOS.md"),
                "summary": "The saved document describes the agent's intended behavior.",
            }
        }
    before = state.state_path(tmp_path).read_bytes()
    output = renderer.render({"stage": stage, "state": current, "revision": current["revision"], "facts": facts})
    assert question in output
    assert original["pending"]["question"] not in output
    assert state.state_path(tmp_path).read_bytes() == before
    assert expansion.record(current) == original
    if recovery == "changed_ethos":
        return
    if recovery == "prerequisite":
        current = state.update(
            tmp_path,
            {
                "prerequisites": {"runtime": {"status": "verified", "source": "Runtime probe passed"}},
                "blockers": [],
                "pending_question": None,
            },
            expected_revision=current["revision"],
        )
    else:
        current = state.confirm_ethos(
            tmp_path,
            reviewed_digest=state.ethos_digest(tmp_path),
            source="User confirms the unchanged saved intent",
            validation="Saved-file validation passed",
            expected_revision=current["revision"],
        )
    assert expansion.record(current) == original
    assert current["pending_question"] == original["pending"]["question"]
    with pytest.raises(ValueError, match="Use expansion_scope"):
        renderer.render({"stage": "resume", "state": current, "revision": current["revision"]})


@pytest.mark.parametrize("kind", ["declined", "revise"])
def test_decline_and_revision_resume_the_actual_decision_and_retain_limits(modules, tmp_path, kind):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    limits = copy.deepcopy(expansion.record(saved)["plan"]["limits"])
    decided = decide(modules, tmp_path, present(modules, tmp_path, saved), kind)
    resumed = state.initialize(tmp_path, journey="expand")
    assert expansion.record(resumed)["decision"]["kind"] == kind
    assert expansion.record(resumed)["plan"]["limits"] == limits
    assert not progress(modules, tmp_path, resumed)["construction_ready"]
    assert resumed["pending_question"] == ("What would you like to change in this scope?" if kind == "revise" else None)
    if kind == "revise":
        new_value = copy.deepcopy(expansion.record(resumed))
        new_value["plan"]["tasks"][0]["scenario"] = "A customer requests a full refund"
        revised = replace_scope(modules, tmp_path, resumed, new_value)
        approved = approve(modules, tmp_path, revised)
        assert expansion.record(decided)["decision"] in expansion.record(approved)["history"]


@pytest.mark.parametrize("invalid", ["not_selected", "repair", "different_tool", "no_measured_gap", "hidden_deferred"])
def test_audit_scope_reuses_selection_but_only_eligible_tool_gaps_can_be_approved(modules, tmp_path, invalid):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    handoff = saved["scopes"]["expand"]["audit_handoff"]
    if invalid == "not_selected":
        value["plan"]["tasks"][0]["recommendation_id"] = "R3"
    elif invalid == "repair":
        value["plan"]["tasks"][0]["recommendation_id"] = "R2"
    elif invalid == "different_tool":
        value["plan"]["tasks"][0]["target_tool"] = "lookup_order"
    elif invalid == "hidden_deferred":
        value["plan"]["deferred"] = []
    else:
        source = value["plan"]["sources"][0]
        report = tmp_path / source["path"]
        measured = json.loads(report.read_text())
        measured["uncovered_items"] = []
        report.write_text(json.dumps(measured))
        source["digest"] = hashlib.sha256(report.read_bytes()).hexdigest()
    saved = replace_scope(modules, tmp_path, saved, value)
    assert saved["scopes"]["expand"]["audit_handoff"] == handoff
    assert progress(modules, tmp_path, saved)["blockers"]
    shown = present(modules, tmp_path, saved)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        decide(modules, tmp_path, shown)
    assert state.state_path(tmp_path).read_bytes() == before


def test_trace_scope_is_a_partial_onboarding_exception_without_execution_permission(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path="recorded_interaction")
    assert saved["ethos"]["confirmed_digest"] is None
    assert not expansion.entry_ready(saved)
    assert expansion.scope_ready(saved)
    agreed = approve(modules, tmp_path, saved)
    ready = progress(modules, tmp_path, agreed)
    assert ready["construction_ready"]
    assert ready["handoff"] == "eval-author-task-create"
    assert not state.checklist(agreed)[1]["complete"]
    assert agreed["tasks"] == {}
    assert agreed["scopes"].get("create", {}).get("execution_approval") is None


def test_trace_scope_accepts_recorded_interactions_larger_than_journey_state(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, path="recorded_interaction")
    value = copy.deepcopy(expansion.record(saved))
    source = value["plan"]["sources"][0]
    interaction = tmp_path / source["path"]
    interaction.write_text(json.dumps({"trace_id": "refund-1", "steps": [], "padding": " " * state.MAX_BYTES}))
    source["digest"] = hashlib.sha256(interaction.read_bytes()).hexdigest()
    agreed = approve(modules, tmp_path, replace_scope(modules, tmp_path, saved, value))
    assert progress(modules, tmp_path, agreed)["construction_ready"]
    assert expansion.construction_guard(tmp_path, agreed, task_id="cover-refund", expected_revision=agreed["revision"])


@pytest.mark.parametrize(
    "change,status",
    [(key, status) for key in ("python", "runtime") for status in ("missing", "unknown")]
    + [("runtime_blocker", None), ("privacy_blocker", None), ("provider", None)],
)
def test_trace_scope_still_requires_its_selected_provider_and_runtime(modules, tmp_path, change, status):
    state, expansion, _ = modules
    saved = approve(modules, tmp_path, start(modules, tmp_path, path="recorded_interaction"))
    if change == "provider":
        saved = state.initialize(tmp_path, setup="harbor")
        patch = {
            "prerequisites": {
                key: {"status": "verified", "source": "Rechecked the selected Harbor runtime"}
                for key in ("python", "runtime")
            }
        }
    elif change == "runtime_blocker":
        patch = {"blockers": [{"id": "runtime", "phase": "setup", "message": "Runtime probe failed"}]}
    elif change == "privacy_blocker":
        patch = {"blockers": [{"id": "privacy", "phase": "expand", "message": "Interaction privacy review pending"}]}
    else:
        patch = {"prerequisites": {change: {"status": status, "source": "Current runtime probe"}}}
    saved = state.update(tmp_path, patch, expected_revision=saved["revision"])
    assert saved["ethos"]["confirmed_digest"] is None
    assert not progress(modules, tmp_path, saved)["construction_ready"]
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, saved, task_id="cover-refund", expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


def test_v1_entry_can_upgrade_but_v2_cannot_downgrade_or_drop_progress(modules, tmp_path):
    state, expansion, _ = modules
    entry = expansion_start(modules, tmp_path)
    legacy = copy.deepcopy(expansion.record(entry))
    plan, _ = expansion_scope_plan(tmp_path)
    value = expansion.new_scope(
        entry,
        path="audit_tool_gap",
        request={"outcome": "Cover partial refunds", "source": "User request"},
        plan=plan,
        question="Approve this supported addition?",
        source="User selected the task path",
    )
    upgraded = replace_scope(modules, tmp_path, entry, value)
    assert expansion.record(upgraded)["schema_version"] == 2
    assert upgraded["scopes"]["expand"]["entry_history"] == [legacy]
    before = state.state_path(tmp_path).read_bytes()
    for replacement in (legacy, None):
        with pytest.raises(ValueError):
            state.update(
                tmp_path,
                {"scopes": {"expand": {"lifecycle": replacement}}},
                expected_revision=upgraded["revision"],
            )
        assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "path,decision",
    [
        ("audit_tool_gap", None),
        ("recorded_interaction", None),
        ("audit_tool_gap", "approved"),
        ("audit_tool_gap", "declined"),
    ],
)
def test_saved_scope_uses_its_own_checkpoint_instead_of_shared_onboarding(modules, tmp_path, path, decision):
    _, _, renderer = modules
    saved = start(modules, tmp_path, path=path)
    if decision is not None:
        saved = decide(modules, tmp_path, present(modules, tmp_path, saved), decision)
    stages = ["resume", "expansion_continue"]
    if path == "recorded_interaction":
        stages.append("prerequisite_results")
    for stage in stages:
        with pytest.raises(ValueError, match="Use expansion_scope"):
            renderer.render({"stage": stage, "state": saved, "revision": saved["revision"]})


def test_construction_guard_rejects_an_old_approved_snapshot_after_scope_changes(modules, tmp_path):
    state, expansion, _ = modules
    saved = approve(modules, tmp_path, start(modules, tmp_path))
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["limits"]["max_cost_usd"] = 1
    value["pending"] = {"question": "Approve the smaller construction budget?", "source": "User revised the cost cap"}
    replace_scope(modules, tmp_path, saved, value)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        expansion.construction_guard(tmp_path, saved, task_id="cover-refund", expected_revision=saved["revision"])
    assert state.state_path(tmp_path).read_bytes() == before


def test_starter_pattern_must_retain_its_existing_acceptance_after_source_revision(modules, tmp_path):
    _, expansion, _ = modules
    saved = start(modules, tmp_path, path="starter_suite")
    value = copy.deepcopy(expansion.record(saved))
    source = value["plan"]["sources"][0]
    (tmp_path / source["path"] / "README.md").write_text("# Unreviewed changed starter task\n")
    source["digest"] = modules[0].task_digest(tmp_path, source["path"])
    updated = replace_scope(modules, tmp_path, saved, value)
    assert any("starter task" in item for item in progress(modules, tmp_path, updated)["blockers"])
    with pytest.raises(ValueError):
        approve(modules, tmp_path, updated)


def test_scope_approval_cannot_reuse_audit_acceptance(modules, tmp_path):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    shown = present(modules, tmp_path, saved)
    value = copy.deepcopy(expansion.record(shown))
    value["pending"] = None
    value["decision"] = {
        "digest": expansion.scope_digest(shown),
        "kind": "approved",
        "source": shown["scopes"]["expand"]["audit_handoff"]["source"],
    }
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        replace_scope(modules, tmp_path, shown, value)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("missing", ["measurement", "report_binding", "next_action"])
def test_handoff_scope_requires_the_originating_measurement_and_selection(modules, tmp_path, missing):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    audit = saved["scopes"]["audit"]["lifecycle"]
    if missing == "measurement":
        audit["measurement"] = None
    elif missing == "report_binding":
        audit["measurement"]["evidence"] = []
        audit["measurement"]["status"] = "deferred"
    else:
        audit["next_action"] = None
    state.save(tmp_path, saved)
    assert progress(modules, tmp_path, saved)["blockers"]
    shown = present(modules, tmp_path, saved)
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        decide(modules, tmp_path, shown)
    assert state.state_path(tmp_path).read_bytes() == before
    assert expansion.record(state.load_state(tmp_path))["decision"] is None


def test_bound_measured_gap_can_be_selected_without_an_audit_handoff(modules, tmp_path):
    state, expansion, _ = modules
    entry = expansion_start(modules, tmp_path)
    plan, _ = expansion_scope_plan(tmp_path)
    plan["deferred"] = []
    plan["tasks"][0]["recommendation_id"] = None
    value = expansion.new_scope(
        entry,
        path="audit_tool_gap",
        request={"outcome": "Cover partial refunds", "source": "User selected this measured tool gap directly"},
        plan=plan,
        question="Approve this supported addition?",
        source="User selected the tool-gap authoring path",
    )
    saved = replace_scope(modules, tmp_path, entry, value)
    agreed = approve(modules, tmp_path, saved)
    assert progress(modules, tmp_path, agreed)["construction_ready"]
    assert state.load_state(tmp_path)["scopes"]["expand"].get("audit_handoff") is None


@pytest.mark.parametrize("scenario", ["approved", "unapproved", "stale_revision", "unselected_task"])
def test_construction_check_cli_observes_current_scope_without_writing(modules, tmp_path, scenario):
    state, _, _ = modules
    saved = start(modules, tmp_path)
    if scenario != "unapproved":
        saved = approve(modules, tmp_path, saved)
    before = state.state_path(tmp_path).read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPTS / "journey_state.py"),
            "--repo",
            str(tmp_path),
            "check-expansion-scope",
            "--task-id",
            "arbitrary-extra-task" if scenario == "unselected_task" else "cover-refund",
            "--expected-revision",
            str(saved["revision"] - (scenario == "stale_revision")),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == (0 if scenario == "approved" else 2), result.stderr
    if scenario == "approved":
        result_data = json.loads(result.stdout)
        assert result_data["task"]["id"] == "cover-refund"
        assert result_data["execution_authorized"] is False
    else:
        assert not result.stdout
        assert json.loads(result.stderr)["persisted"] is False
    assert state.state_path(tmp_path).read_bytes() == before


def test_replacing_an_agreement_keeps_the_original_receipt_and_rejects_history_edits(modules, tmp_path):
    state, expansion, _ = modules
    saved = approve(modules, tmp_path, start(modules, tmp_path))
    old_decision = copy.deepcopy(expansion.record(saved)["decision"])
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["limits"]["max_minutes"] = 10
    value["pending"] = {
        "question": "Approve the revised construction budget?",
        "source": "User requested a smaller budget",
    }
    shown = present(modules, tmp_path, replace_scope(modules, tmp_path, saved, value))
    agreed = decide(modules, tmp_path, shown, source="User approves the revised budget in a later reply")
    assert expansion.record(agreed)["history"] == [old_decision]
    value = copy.deepcopy(expansion.record(agreed))
    value["history"] = []
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        replace_scope(modules, tmp_path, agreed, value)
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("changed", ["action", "items", "proposed_task"])
def test_audit_batch_retains_scope_through_progress_updates_but_not_semantic_changes(modules, tmp_path, changed):
    state, expansion, _ = modules
    saved = start(modules, tmp_path, extra_gap=True)
    agreed = approve(modules, tmp_path, saved)
    original = copy.deepcopy(expansion.record(agreed))
    assert progress(modules, tmp_path, agreed)["construction_ready"]
    assert {item["id"] for item in expansion.record(agreed)["plan"]["deferred"]} == {"R2"}
    before = state.state_path(tmp_path).read_bytes()
    handoff = agreed["scopes"]["expand"]["audit_handoff"]
    content_file = tmp_path / handoff["content"]["path"]
    content = json.loads(content_file.read_text())
    for task_id in ("cover-refund", "cover-cancel-order"):
        allowed = expansion.construction_guard(tmp_path, agreed, task_id=task_id, expected_revision=agreed["revision"])
        assert allowed["task"]["id"] == task_id
        assert allowed["execution_authorized"] is False
        if task_id == "cover-refund":
            content["recommendations"][0]["follow_through"] = {
                "status": "in_progress",
                "summary": "The first selected task is under construction; no agent runs were recorded.",
                "evidence": [],
            }
            content_file.write_text(json.dumps(content) + "\n")
            report = tmp_path / handoff["report"]["path"]
            report.write_text(report.read_text() + "\nPartial-refund follow-through: construction in progress.\n")
    assert state.state_path(tmp_path).read_bytes() == before
    assert agreed["tasks"] == {}
    assert expansion.record(state.load_state(tmp_path)) == original
    content["recommendations"][0][changed] = {
        "action": "repair_verifier",
        "items": ["lookup_order"],
        "proposed_task": "Cover full refunds for expired orders instead",
    }[changed]
    content_file.write_text(json.dumps(content) + "\n")
    with pytest.raises(ValueError):
        expansion.construction_guard(
            tmp_path, agreed, task_id="cover-cancel-order", expected_revision=agreed["revision"]
        )
    assert state.state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("changed", ["suite_task", "trace", "audit_spec", "findings_input"])
@pytest.mark.parametrize("approved", [False, True])
def test_originating_audit_inputs_must_still_support_the_selected_findings(modules, tmp_path, changed, approved):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    if approved:
        saved = approve(modules, tmp_path, saved)
    original = copy.deepcopy(expansion.record(saved))
    handoff = saved["scopes"]["expand"]["audit_handoff"]
    report_bytes = (tmp_path / handoff["report"]["path"]).read_bytes()
    content_bytes = (tmp_path / handoff["content"]["path"]).read_bytes()
    audit = saved["scopes"]["audit"]["lifecycle"]
    paths = {
        "suite_task": audit["suite_files"][0],
        "trace": audit["trace_files"][0],
        "audit_spec": audit["specification"],
        "findings_input": audit["findings"]["inputs"][-1]["path"],
    }
    (tmp_path / paths[changed]).write_text("Changed after the audit findings were reviewed\n")
    current = state.load_state(tmp_path)
    assert (tmp_path / handoff["report"]["path"]).read_bytes() == report_bytes
    assert (tmp_path / handoff["content"]["path"]).read_bytes() == content_bytes
    assert expansion.record(current) == original
    assert not progress(modules, tmp_path, current)["construction_ready"]
    if approved:
        before = state.state_path(tmp_path).read_bytes()
        with pytest.raises(ValueError):
            expansion.construction_guard(
                tmp_path, current, task_id="cover-refund", expected_revision=current["revision"]
            )
    else:
        current = present(modules, tmp_path, current)
        before = state.state_path(tmp_path).read_bytes()
        with pytest.raises(ValueError):
            decide(modules, tmp_path, current)
    assert state.state_path(tmp_path).read_bytes() == before


def test_agreed_subset_does_not_authorize_unselected_improvements(modules, tmp_path):
    _, expansion, _ = modules
    saved = start(modules, tmp_path, extra_gap=True)
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["selected_task_ids"] = ["cover-refund"]
    reason = "User chose to keep this eligible improvement for later"
    value["plan"]["deferred"].append({"id": "R3", "improvement": "Order cancellation coverage", "reason": reason})
    agreed = approve(modules, tmp_path, replace_scope(modules, tmp_path, saved, value))
    assert expansion.construction_guard(tmp_path, agreed, task_id="cover-refund", expected_revision=agreed["revision"])
    with pytest.raises(ValueError):
        expansion.construction_guard(
            tmp_path, agreed, task_id="cover-cancel-order", expected_revision=agreed["revision"]
        )


@pytest.mark.parametrize("changed", ["missing_report", "missing_content", "changed_content"])
def test_missing_audit_proof_keeps_scope_revisions_saveable_without_rebinding(modules, tmp_path, changed):
    state, expansion, _ = modules
    saved = start(modules, tmp_path)
    original = copy.deepcopy(expansion.record(saved))
    handoff = copy.deepcopy(saved["scopes"]["expand"]["audit_handoff"])
    if changed.startswith("missing_"):
        expansion.record(saved).pop("audit_content_digest")
        state.save(tmp_path, saved)
        (tmp_path / handoff[changed.removeprefix("missing_")]["path"]).unlink()
    else:
        content_path = tmp_path / handoff["content"]["path"]
        content = json.loads(content_path.read_text())
        content["recommendations"][0]["proposed_task"] = "A changed construction recommendation"
        content_path.write_text(json.dumps(content) + "\n")
    value = expansion.new_scope(
        saved,
        path="audit_tool_gap",
        request=original["request"],
        plan=original["plan"],
        question="How should the changed audit context be reconciled?",
        source="User revises the blocked construction proposal",
    )
    assert value["audit_content_digest"] == (original["audit_content_digest"] if changed == "changed_content" else None)
    revised = replace_scope(modules, tmp_path, saved, value)
    assert revised["scopes"]["expand"]["audit_handoff"] == handoff
    assert progress(modules, tmp_path, revised)["blockers"]
    with pytest.raises(ValueError):
        approve(modules, tmp_path, revised)
