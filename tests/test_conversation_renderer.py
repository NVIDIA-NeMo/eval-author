# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conversation output is a pure projection of checked facts and shared journey state."""

import builtins
import copy
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
FIXTURES = Path(__file__).parent / "fixtures/journey-conversation"
GOLDEN_HEADER = (
    "<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->\n"
    "<!-- SPDX-License-Identifier: Apache-2.0 -->\n\n"
)
JOURNEYS = ("create", "audit", "expand")
ROWS = {
    "create": (
        "agent_repository",
        "evaluation_framework",
        "skills",
        "access",
        "runtime",
        "environment",
        "services_data",
        "agent_access",
    ),
    "audit": ("agent_repository", "evaluation_framework", "traces", "skills", "access", "tooling"),
}
ROWS["expand"] = ROWS["create"]
PROGRESS_INTRO = "Here’s what I already know about your setup and what I’m checking before we continue."
RESULTS_INTRO = "Here’s what the prerequisite checks found, including anything that still needs attention."


def golden_output(path):
    contents = path.read_text(encoding="utf-8")
    assert contents.startswith(GOLDEN_HEADER), f"Missing fixture license metadata: {path}"
    return contents.removeprefix(GOLDEN_HEADER)


@pytest.fixture
def conversation(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("conversation_renderer")


@pytest.fixture
def state_module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("journey_state")


def ready_state(state_module, journey="create"):
    state = state_module.new_state(Path("/workspace/agent"))
    state.update(agent="Account Support", journey=journey, setup="gym", revision=7)
    state["updated_at"] = "2026-10-23T12:00:00+00:00"
    required = (
        state_module.COMMON_PREREQUISITES | {"data"}
        if journey == "audit"
        else state_module.PREREQUISITES | state_module.DEFERRED
    )
    state["prerequisites"] = {
        key: {"status": "verified", "source": "Retained prerequisite check"} for key in sorted(required)
    }
    return state


def payload_for(state_module, *, stage="results", journey="create"):
    state = ready_state(state_module, journey)
    question = "What should happen when the account cannot be found?"
    state.update(pending_question=question, next_handoff="ethos-orchestrate")
    return {
        "stage": stage,
        "revision": state["revision"],
        "state": state,
        "facts": {
            "rows": {
                row: {
                    "status": "verified",
                    "findings": "Applicable check passed.",
                    "source": "Retained prerequisite check",
                }
                for row in ROWS[journey]
            },
            "ethos": {"status": "absent"},
            "question": question,
        },
    }


def complete_ethos(payload):
    payload["state"]["ethos"].update(
        stage="complete",
        confirmed_digest="a" * 64,
        confirmation_source="User confirmed current intent in reply 4",
        validation="ethos-write validation passed",
    )
    payload["state"]["pending_question"] = None
    payload["state"]["next_handoff"] = (
        "eval-author-audit" if payload["state"]["journey"] == "audit" else "eval-author-first-eval"
    )
    payload["facts"].pop("question", None)
    payload["facts"]["ethos"] = {
        "status": "existing",
        "link": "/workspace/agent/ETHOS.md",
        "summary": "The agent helps users recover access to their accounts.",
    }


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_golden_conversations_are_reproducible(conversation, fixture):
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    expected = golden_output(fixture.with_suffix(".md"))
    before = copy.deepcopy(payload)
    assert conversation.render(payload) == expected
    assert conversation.render(payload) == expected
    assert payload == before
    assert expected.endswith("\n")
    assert "[Shared three-item checklist" not in expected
    assert "[Focused question" not in expected


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_golden_inputs_replay_through_cli(fixture):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "conversation_renderer.py"), "--input", str(fixture)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == golden_output(fixture.with_suffix(".md"))
    assert result.stderr == ""


def test_render_neither_reads_files_nor_executes_nor_mutates(conversation, state_module, monkeypatch):
    payload = payload_for(state_module)
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering must not perform I/O or run commands")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        first = conversation.render(payload)
        second = conversation.render(payload)
    assert first == second
    assert payload == before


@pytest.mark.parametrize("journey", JOURNEYS)
def test_orientation_does_not_need_state(conversation, journey):
    result = conversation.render({"stage": "orientation", "journey": journey})
    assert result.startswith("Welcome to Eval Author!")
    assert result.count("- [ ]") == 3
    assert result.count("**We're here**") == 1
    assert result.endswith("Ready to get started?\n")
    assert "### Next Steps" not in result
    assert "| Prerequisite |" not in result


@pytest.mark.parametrize(
    "payload",
    [
        {"stage": "unregistered", "journey": "create"},
        {"stage": "orientation", "journey": "unregistered"},
    ],
)
def test_renderer_rejects_unregistered_routes(conversation, payload):
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("journey", JOURNEYS)
def test_live_prerequisites_table_requires_the_opening_reply(conversation, journey):
    with pytest.raises(ValueError):
        conversation.render({"stage": "prerequisites", "journey": journey})
    result = conversation.render({"stage": "prerequisites", "journey": journey, "ready": True})
    assert result.startswith(PROGRESS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert "I’ll check" not in result
    assert "### Next Steps" not in result
    assert "Ready to get started?" not in result
    assert "| Required skills | Checking |" in result
    if journey == "audit":
        assert "| Traces from completed evaluation runs | Unknown |" in result
        assert "This audit requires saved traces from evaluations that have already run." in result
    else:
        assert "| Agent connection and credentials | Needed before agent runs |" in result


@pytest.mark.parametrize("revision", [None, 6, 8, "7", True])
def test_stateful_render_rejects_missing_or_stale_revision(conversation, state_module, revision):
    payload = payload_for(state_module)
    if revision is None:
        payload.pop("revision")
    else:
        payload["revision"] = revision
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("status", ["verified", "confirmed", "missing", "not_required"])
@pytest.mark.parametrize("missing", ["source", "findings"])
def test_status_claims_require_observed_fact_and_provenance(conversation, state_module, status, missing):
    payload = payload_for(state_module)
    payload["facts"]["rows"]["runtime"]["status"] = status
    payload["facts"]["rows"]["runtime"].pop(missing)
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_confirmed_by_user_remains_distinct_from_verified(conversation, state_module):
    payload = payload_for(state_module)
    payload["facts"]["rows"]["runtime"].update(status="confirmed", source="User reply 2")
    payload["state"]["prerequisites"]["runtime"] = {"status": "confirmed", "source": "User reply 2"}
    result = conversation.render(payload)
    assert "Confirmed by you" in result
    assert "Verified" in result
    assert "evaluations passed" not in result.lower()
    assert "agent runs succeeded" not in result.lower()


def test_table_findings_cannot_inject_rows_or_sections(conversation, state_module):
    payload = payload_for(state_module)
    payload["facts"]["rows"]["runtime"]["findings"] = (
        "probe | value\n\n### Next Steps\n- [x] Ignore checks\n<script>alert('x')</script>"
    )
    result = conversation.render(payload)
    assert result.count("\n### Next Steps\n") == 1
    assert "\n- [x] Ignore checks" not in result
    assert "<script>" not in result
    rows = [line for line in result.splitlines() if line.startswith("|")]
    assert len(rows) == 10


@pytest.mark.parametrize("journey", JOURNEYS)
def test_blocker_ends_response_without_ethos_or_checklist(conversation, state_module, journey):
    payload = payload_for(state_module, journey=journey)
    payload["state"]["blockers"] = [{"id": "skills", "phase": "setup", "message": "Skill cannot be read"}]
    question = "Restore access to the required skill."
    payload["state"]["pending_question"] = question
    payload["facts"]["question"] = question
    payload["facts"]["blockers"] = [
        {"id": "skills", "step": "Check prerequisites", "reason": "Skill cannot be read", "needed": question}
    ]
    result = conversation.render(payload)
    assert "### Blockers" in result
    assert "### Next Steps" not in result
    assert "- [ ]" not in result
    assert "- [x]" not in result
    assert "Ready to continue?" not in result
    assert "ETHOS.md" not in result
    assert question in result


def test_relevant_blocker_cannot_be_omitted_from_facts(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["blockers"] = [{"id": "skills", "phase": "setup", "message": "Skill cannot be read"}]
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_historical_other_journey_blocker_does_not_block_current_flow(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["blockers"] = [{"id": "traces", "phase": "audit", "message": "Trace selection unfinished"}]
    result = conversation.render(payload)
    assert "### Blockers" not in result
    assert "### Next Steps" in result
    assert "Trace selection unfinished" not in result


@pytest.mark.parametrize("link", ["ETHOS.md", "/workspace/other/ETHOS.md", "/workspace/agent/ethos.md"])
def test_existing_ethos_requires_the_exact_root_document(conversation, state_module, link):
    payload = payload_for(state_module, stage="ethos")
    payload["state"]["ethos"]["stage"] = "review"
    payload["facts"]["ethos"] = {"status": "existing", "link": link, "summary": "Account recovery intent"}
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_existing_ethos_requires_a_grounded_summary(conversation, state_module):
    payload = payload_for(state_module, stage="ethos")
    payload["state"]["ethos"]["stage"] = "review"
    payload["facts"]["ethos"] = {"status": "existing", "link": "/workspace/agent/ETHOS.md"}
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_historical_confirmation_does_not_complete_pending_review(conversation, state_module):
    payload = payload_for(state_module, stage="ethos")
    complete_ethos(payload)
    payload["state"]["ethos"]["stage"] = "review"
    payload["state"]["pending_question"] = "Is this document still correct and current?"
    result = conversation.render(payload)
    assert result.count("- [x]") == 1
    assert "- [ ] **Agree on how your agent behaves" in result
    assert "Ready to continue?" not in result


@pytest.mark.parametrize("journey", JOURNEYS)
def test_completion_pause_marks_only_shared_steps_complete(conversation, state_module, journey):
    payload = payload_for(state_module, stage="confirmed", journey=journey)
    complete_ethos(payload)
    result = conversation.render(payload)
    assert result.count("- [x]") == 2
    assert result.count("- [ ]") == 1
    assert result.count("**We're here**") == 1
    assert result == golden_output(FIXTURES / f"confirmed-{journey}.md")
    assert "ETHOS.md" in result


@pytest.mark.parametrize("unfinished", ["prerequisite", "review", "question", "in_progress", "complete"])
def test_confirmation_surface_rejects_unfinished_or_started_work(conversation, state_module, unfinished):
    payload = payload_for(state_module, stage="confirmed")
    complete_ethos(payload)
    if unfinished == "prerequisite":
        payload["state"]["prerequisites"]["runtime"]["status"] = "unknown"
    elif unfinished == "review":
        payload["state"]["ethos"]["stage"] = "review"
    elif unfinished == "question":
        payload["state"]["ethos"]["remaining_questions"] = ["Which agent is intended?"]
    else:
        payload["state"]["journey_status"]["create"] = unfinished
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("journey", JOURNEYS)
def test_resume_preserves_pending_downstream_decision(conversation, state_module, journey):
    payload = payload_for(state_module, stage="resume", journey=journey)
    complete_ethos(payload)
    question = "Which supplied case should we review first?"
    payload["state"]["pending_question"] = question
    payload["state"]["journey_status"][journey] = "in_progress"
    result = conversation.render(payload)
    assert question in result
    assert "Welcome to Eval Author" not in result
    assert "Ready to get started?" not in result
    assert "Ready to continue?" not in result
    assert result.count("- [x]") == 2


def test_resume_uses_unanswered_intent_question_when_no_pending_question(conversation, state_module):
    payload = payload_for(state_module, stage="resume")
    payload["state"]["pending_question"] = None
    payload["facts"].pop("question")
    question = "Should the agent disclose uncertain account matches?"
    payload["state"]["ethos"]["remaining_questions"] = [question]
    result = conversation.render(payload)
    assert question in result
    assert "Ready to continue?" not in result


def test_render_preserves_saved_digest_revision_and_readiness(conversation, state_module, tmp_path):
    state_module.initialize(tmp_path, agent="Account Support", journey="create", setup="gym")
    state_module.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current check"} for key in state_module.PREREQUISITES
            }
        },
    )
    (tmp_path / "ETHOS.md").write_text("# Account support\n", encoding="utf-8")
    state_module.confirm_ethos(
        tmp_path,
        reviewed_digest=state_module.ethos_digest(tmp_path),
        source="User confirmed saved content",
        validation="ethos-write validation passed",
    )
    saved = state_module.load_state(tmp_path)
    payload = payload_for(state_module, stage="confirmed")
    payload.update(state=saved, revision=saved["revision"])
    payload["facts"]["ethos"] = {"status": "existing", "link": str(tmp_path / "ETHOS.md"), "summary": "Account support"}
    payload["facts"].pop("question")
    before = state_module.state_path(tmp_path).read_bytes()
    readiness = state_module.checklist(saved)
    conversation.render(payload)
    assert state_module.state_path(tmp_path).read_bytes() == before
    assert state_module.load_state(tmp_path) == saved
    assert state_module.checklist(saved) == readiness


def test_cli_accepts_stateless_arguments_and_stdin(conversation):
    payload = {"stage": "orientation", "journey": "audit"}
    expected = conversation.render(payload)
    arguments = subprocess.run(
        [sys.executable, str(SCRIPTS / "conversation_renderer.py"), "--stage", "orientation", "--journey", "audit"],
        capture_output=True,
        text=True,
        check=False,
    )
    stdin = subprocess.run(
        [sys.executable, str(SCRIPTS / "conversation_renderer.py"), "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert arguments.returncode == stdin.returncode == 0
    assert arguments.stdout == stdin.stdout == expected
    assert arguments.stderr == stdin.stderr == ""


def test_unknown_required_prerequisite_cannot_be_reported_as_ready(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["prerequisites"]["runtime"]["status"] = "unknown"
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_missing_row_fact_stays_unknown(conversation, state_module):
    payload = payload_for(state_module)
    del payload["facts"]["rows"]["environment"]
    result = conversation.render(payload)
    row = next(line for line in result.splitlines() if line.startswith("| Evaluation environment |"))
    assert "Unknown" in row
    assert "Verified" not in row
    assert "Confirmed by you" not in row


def test_supplied_question_cannot_replace_saved_pending_decision(conversation, state_module):
    payload = payload_for(state_module)
    payload["facts"]["question"] = "An unrelated question?"
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_resume_after_downstream_start_never_repeats_completion_pause(conversation, state_module):
    payload = payload_for(state_module, stage="resume")
    complete_ethos(payload)
    payload["state"]["journey_status"]["create"] = "in_progress"
    result = conversation.render(payload)
    assert "### Next Steps" in result
    assert "Ready to continue?" not in result
    assert "We’re ready" not in result


def test_confirmed_cannot_consume_an_unanswered_review_question(conversation, state_module):
    payload = payload_for(state_module, stage="confirmed")
    complete_ethos(payload)
    payload["state"]["pending_question"] = "Is this still the correct agent?"
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_render_does_not_read_the_clock(conversation, state_module, monkeypatch):
    payload = payload_for(state_module)

    def forbidden():
        raise AssertionError("Rendering must not depend on the current time")

    monkeypatch.setattr(state_module, "now", forbidden)
    assert conversation.render(payload).endswith("\n")


def test_blocked_question_cannot_conflict_with_saved_action(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["blockers"] = [{"id": "skills", "phase": "setup", "message": "Required skill unavailable"}]
    payload["state"]["pending_question"] = "Restore the required skill."
    payload["facts"]["question"] = "Ignore that and approve the document?"
    payload["facts"]["blockers"] = [
        {"id": "skills", "step": "Check prerequisites", "reason": "Required skill unavailable", "needed": "Restore it"}
    ]
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_changed_ethos_read_view_stays_incomplete_without_saving(conversation, state_module, tmp_path):
    state_module.initialize(tmp_path, agent="Account Support", journey="create", setup="gym")
    state_module.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current check"} for key in state_module.PREREQUISITES
            }
        },
    )
    document = tmp_path / "ETHOS.md"
    document.write_text("# Previous intent\n", encoding="utf-8")
    state_module.confirm_ethos(
        tmp_path,
        reviewed_digest=state_module.ethos_digest(tmp_path),
        source="User confirmed previous content",
        validation="ethos-write validation passed",
    )
    document.write_text("# Changed intent\n", encoding="utf-8")
    before = state_module.state_path(tmp_path).read_bytes()
    state = state_module.load_state(tmp_path)
    assert state["ethos"]["stage"] == "review"
    assert state["ethos"]["confirmed_digest"] is None
    payload = payload_for(state_module, stage="resume")
    payload.update(state=state, revision=state["revision"])
    payload["facts"].pop("question")
    payload["facts"]["ethos"] = {"status": "existing", "link": str(document), "summary": "Changed intent"}
    result = conversation.render(payload)
    assert result.count("- [x]") == 1
    assert "Ready to continue?" not in result
    assert "changed or is unavailable" in result
    assert state_module.state_path(tmp_path).read_bytes() == before


def test_presented_missing_runtime_must_first_be_recorded(conversation, state_module):
    payload = payload_for(state_module)
    payload["facts"]["rows"]["runtime"].update(status="missing", findings="Runtime lookup failed", source="New probe")
    with pytest.raises(ValueError, match="conflicts with saved prerequisites"):
        conversation.render(payload)


@pytest.mark.parametrize(
    "row,dependency", [("skills", "ethos_skills"), ("access", "file_writes"), ("runtime", "python")]
)
@pytest.mark.parametrize("recorded", ["confirmed", "missing", "unknown", "absent"])
def test_composite_rows_cannot_overstate_a_component(conversation, state_module, row, dependency, recorded):
    payload = payload_for(state_module)
    if recorded == "absent":
        payload["state"]["prerequisites"].pop(dependency)
    else:
        payload["state"]["prerequisites"][dependency] = {"status": recorded, "source": "Recorded component check"}
    with pytest.raises(ValueError, match="conflicts with saved prerequisites"):
        conversation.render(payload)


@pytest.mark.parametrize(
    "services,data,aggregate,label",
    [
        ("missing", "unknown", "missing", "Missing"),
        ("unknown", "confirmed", "unknown", "Unknown"),
        ("confirmed", "verified", "confirmed", "Confirmed by you"),
        ("verified", "not_required", "verified", "Verified"),
        ("not_required", "not_required", "not_required", "Not needed now"),
    ],
)
def test_composite_row_keeps_the_weakest_applicable_evidence(
    conversation, state_module, services, data, aggregate, label
):
    payload = payload_for(state_module)
    for key, status in (("services", services), ("data", data)):
        payload["state"]["prerequisites"][key] = {"status": status, "source": "Recorded requirement check"}
    payload["facts"]["rows"]["services_data"].update(status=aggregate, findings="Later execution requirements")
    result = conversation.render(payload)
    row = next(line for line in result.splitlines() if line.startswith("| Required services and data |"))
    assert f"| {label} |" in row


def draft_payload(state_module, *, stage="draft"):
    payload = payload_for(state_module, stage="ethos")
    path = ".eval-author/.working/ethos/draft.md"
    payload["state"]["ethos"].update(stage=stage, draft={"path": path, "digest": "c" * 64})
    if stage == "write":
        payload["state"]["ethos"].update(draft_confirmed=True, draft_source="User approved exact draft in reply 3")
        question = "Save the approved draft to repository-root ETHOS.md and validate it."
    else:
        question = "Does this draft accurately describe the intended behavior?"
    payload["state"]["pending_question"] = question
    payload["facts"]["question"] = question
    payload["facts"]["draft"] = {"link": f"/workspace/agent/{path}", "digest": "c" * 64}
    return payload


@pytest.mark.parametrize("stage", ["draft", "write"])
def test_draft_pause_links_exact_content_without_advancing_approval(conversation, state_module, stage):
    payload = draft_payload(state_module, stage=stage)
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    draft_link = "[ETHOS.md draft](</workspace/agent/.eval-author/.working/ethos/draft.md>)"
    assert draft_link in result
    assert result.index(draft_link) < result.index(payload["state"]["pending_question"])
    assert result.count("- [x]") == 1
    assert "Ready to continue?" not in result
    assert "confirmed and validated" not in result
    assert payload == before


@pytest.mark.parametrize("mismatch", ["missing", "digest", "link", "relative_link", "canonical_document"])
def test_draft_pause_rejects_missing_or_different_content(conversation, state_module, mismatch):
    payload = draft_payload(state_module)
    if mismatch == "missing":
        payload["facts"].pop("draft")
    elif mismatch == "digest":
        payload["facts"]["draft"]["digest"] = "d" * 64
    elif mismatch == "link":
        payload["facts"]["draft"]["link"] = "/workspace/agent/.eval-author/.working/ethos/other.md"
    elif mismatch == "relative_link":
        payload["facts"]["draft"]["link"] = payload["state"]["ethos"]["draft"]["path"]
    else:
        payload["facts"]["draft"]["link"] = "/workspace/agent/ETHOS.md"
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_supplied_question_cannot_replace_remaining_intent_question(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["pending_question"] = None
    payload["state"]["ethos"]["remaining_questions"] = ["What is the permitted refund limit?"]
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("row", ["agent_repository", "evaluation_framework"])
def test_missing_identity_or_framework_requires_saved_setup_blocker(conversation, state_module, row):
    payload = payload_for(state_module)
    payload["facts"]["rows"][row].update(
        status="missing", findings="Required choice unresolved", source="Current check"
    )
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize(
    "row,dependency,label",
    [
        ("skills", "ethos_skills", "Required skills"),
        ("access", "file_writes", "File and command access"),
        ("runtime", "python", "Python and evaluation runtime"),
    ],
)
def test_composite_rows_keep_user_confirmation_distinct(conversation, state_module, row, dependency, label):
    payload = payload_for(state_module)
    payload["state"]["prerequisites"][dependency] = {"status": "confirmed", "source": "User reply 2"}
    payload["facts"]["rows"][row].update(status="confirmed", source="User reply 2")
    result = conversation.render(payload)
    line = next(line for line in result.splitlines() if line.startswith(f"| {label} |"))
    assert "| Confirmed by you |" in line
    assert "Verified" not in line


def test_cli_writes_utf8_even_when_terminal_encoding_is_ascii(conversation):
    payload = {"stage": "orientation", "journey": "create"}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "conversation_renderer.py"), "--stage", "orientation", "--journey", "create"],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == conversation.render(payload).encode("utf-8")
    assert result.stderr == b""


def test_saved_execution_credentials_do_not_block_unfinished_ethos(conversation, state_module):
    payload = payload_for(state_module)
    payload["state"]["prerequisites"]["model_credentials"] = {"status": "missing", "source": "Configuration inspection"}
    payload["state"]["blockers"] = [
        {"id": "model_credentials", "phase": "create", "message": "Credentials are required before actual-agent runs."}
    ]
    payload["facts"]["rows"]["agent_access"].update(
        status="missing",
        findings="Credentials are required before actual-agent runs.",
        source="Configuration inspection",
    )
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert "### Blockers" not in result
    assert "### Next Steps" in result
    assert payload["state"]["pending_question"] in result
    assert "Ready to continue?" not in result
    assert payload == before


def confirmed_endpoint_payload(state_module):
    payload = payload_for(state_module, stage="confirmed")
    complete_ethos(payload)
    payload["state"]["prerequisites"]["agent_connection"] = {"status": "missing", "source": "Endpoint access check"}
    payload["state"]["blockers"] = [
        {"id": "agent_connection", "phase": "create", "message": "The selected agent endpoint cannot be reached."}
    ]
    payload["facts"]["rows"]["agent_access"].update(
        status="missing", findings="The selected agent endpoint cannot be reached.", source="Endpoint access check"
    )
    return payload


def test_saved_endpoint_blocker_allows_initial_planning_handoff(conversation, state_module):
    payload = confirmed_endpoint_payload(state_module)
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert "### Blockers" not in result
    assert result == golden_output(FIXTURES / "confirmed-deferred-endpoint.md")
    assert "- [ ] **Build, run, and review your first evaluations** — **We're here**" in result
    assert payload == before


def test_saved_endpoint_blocker_applies_once_downstream_work_starts(conversation, state_module):
    payload = confirmed_endpoint_payload(state_module)
    payload["stage"] = "resume"
    payload["state"]["journey_status"]["create"] = "in_progress"
    payload["state"]["pending_question"] = "Restore access to the selected agent endpoint."
    payload["facts"]["blockers"] = [
        {
            "id": "agent_connection",
            "step": "Run the selected evaluations",
            "reason": "The selected agent endpoint cannot be reached.",
            "needed": "Restore access to the selected agent endpoint.",
        }
    ]
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert "### Blockers" in result
    assert "### Next Steps" not in result
    assert "Ready to continue?" not in result
    assert "- [x]" not in result
    assert payload == before


def test_missing_handoff_capability_blocks_initial_planning_handoff(conversation, state_module):
    payload = payload_for(state_module, stage="confirmed")
    complete_ethos(payload)
    payload["state"]["blockers"] = [
        {
            "id": "authoring_handoff",
            "phase": "create",
            "message": "No supported authoring route matches the selected suite.",
        }
    ]
    payload["facts"]["blockers"] = [
        {
            "id": "authoring_handoff",
            "step": "Begin the selected authoring flow",
            "reason": "No supported authoring route matches the selected suite.",
            "needed": "Provide a supported authoring route for the selected suite.",
        }
    ]
    result = conversation.render(payload)
    assert "### Blockers" in result
    assert "### Next Steps" not in result
    assert "Ready to continue?" not in result


@pytest.mark.parametrize("prior_status", ["in_progress", "complete"])
def test_real_goal_switch_does_not_repeat_completed_shared_pause(conversation, state_module, tmp_path, prior_status):
    state_module.initialize(tmp_path, agent="Account Support", journey="create", setup="gym")
    state_module.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current check"} for key in state_module.PREREQUISITES
            }
        },
    )
    document = tmp_path / "ETHOS.md"
    document.write_text("# Confirmed account support intent\n", encoding="utf-8")
    state_module.confirm_ethos(
        tmp_path,
        reviewed_digest=state_module.ethos_digest(tmp_path),
        source="User confirmed current saved content",
        validation="ethos-write validation passed",
    )
    previous = state_module.update(
        tmp_path,
        {
            "journey_status": {"create": prior_status},
            "pending_question": "Which existing case should we review?",
            "next_handoff": "eval-author-first-eval",
        },
    )
    switched = state_module.initialize(tmp_path, journey="expand")
    assert switched["pending_question"] is None
    assert switched["prerequisites"] == previous["prerequisites"]
    assert switched["ethos"] == previous["ethos"]
    assert switched["journey_status"] == {"create": prior_status}
    payload = payload_for(state_module, stage="resume", journey="expand")
    payload.update(state=switched, revision=switched["revision"])
    payload["facts"].pop("question")
    payload["facts"]["ethos"] = {"status": "existing", "link": str(document), "summary": "Account support intent"}
    before = copy.deepcopy(payload)
    saved = state_module.state_path(tmp_path).read_bytes()
    result = conversation.render(payload)
    assert "Welcome to Eval Author" not in result
    assert "Ready to continue?" not in result
    assert "Ready to get started?" not in result
    assert "- [ ] **Improve, run, and review your existing evaluations** — **We're here**" in result
    assert result.count("- [x]") == 2
    assert payload == before
    assert state_module.state_path(tmp_path).read_bytes() == saved


@pytest.mark.parametrize("prior_status", ["in_progress", "complete"])
def test_explicit_current_pause_survives_historical_journey_progress(conversation, state_module, prior_status):
    payload = payload_for(state_module, stage="resume", journey="expand")
    complete_ethos(payload)
    payload["state"]["journey_status"] = {"create": prior_status}
    question = "We’re ready to improve your existing evaluations. Ready to continue?"
    payload["state"]["pending_question"] = question
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert result.endswith(question + "\n")
    assert "Welcome to Eval Author" not in result
    assert "- [ ] **Improve, run, and review your existing evaluations** — **We're here**" in result
    assert payload == before


def prerequisite_results_payload(state_module, *, journey="create"):
    payload = payload_for(state_module, stage="prerequisite_results", journey=journey)
    payload["facts"].pop("ethos")
    payload["facts"].pop("question")
    return payload


@pytest.mark.parametrize("journey", JOURNEYS)
def test_prerequisite_results_shows_completed_table_without_repeating_the_checklist(
    conversation, state_module, journey
):
    payload = prerequisite_results_payload(state_module, journey=journey)
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert result.startswith(RESULTS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert "### Next Steps" not in result
    assert "- [x]" not in result
    assert "- [ ]" not in result
    assert "**We're here**" not in result
    assert payload["state"]["pending_question"] not in result
    assert "Ready to continue?" not in result
    assert "ETHOS.md records" not in result
    assert "I found" not in result
    assert "confirmed and validated" not in result
    assert result.endswith("The prerequisites for reviewing ETHOS.md are complete.\n")
    assert payload == before


def test_prerequisite_results_does_not_read_artifacts_or_clock(conversation, state_module, monkeypatch):
    payload = payload_for(state_module, stage="prerequisite_results")
    complete_ethos(payload)
    payload["facts"].pop("ethos")
    payload["state"]["pending_question"] = "We’re ready to build your evaluation suite. Ready to continue?"
    payload["state"]["journey_status"]["create"] = "complete"
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Prerequisite results must use supplied state only")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(state_module, "now", forbidden)
        patch.setattr(state_module, "ethos_digest", forbidden)
        patch.setattr(state_module, "draft_digest", forbidden)
        result = conversation.render(payload)
    assert "- [x]" not in result
    assert "- [ ]" not in result
    assert "### Next Steps" not in result
    assert result.endswith("The prerequisites for reviewing ETHOS.md are complete.\n")
    assert "Ready to continue?" not in result
    assert payload == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("ethos", {"status": "existing", "link": "/workspace/agent/ETHOS.md", "summary": "Old intent"}),
        ("ethos", {"status": "absent"}),
        ("draft", {"link": "/workspace/agent/.eval-author/.working/draft.md", "digest": "a" * 64}),
        ("question", "Does this describe the intended behavior?"),
    ],
)
def test_prerequisite_results_rejects_artifact_and_interview_facts(conversation, state_module, field, value):
    payload = prerequisite_results_payload(state_module)
    payload["facts"][field] = value
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_prerequisite_results_ignores_stale_saved_completion_until_artifact_reconciliation(
    conversation, state_module, tmp_path, monkeypatch
):
    state_module.initialize(tmp_path, agent="Account Support", journey="create", setup="gym")
    state_module.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current prerequisite check"}
                for key in state_module.PREREQUISITES | state_module.DEFERRED
            }
        },
    )
    document = tmp_path / "ETHOS.md"
    document.write_text("# Previously reviewed account support intent\n", encoding="utf-8")
    state_module.confirm_ethos(
        tmp_path,
        reviewed_digest=state_module.ethos_digest(tmp_path),
        source="Earlier user approval",
        validation="Earlier saved document validation",
    )
    document.write_text("# Unreviewed changed intent\n", encoding="utf-8")
    before = state_module.state_path(tmp_path).read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Artifact reconciliation must wait until after prerequisite results")

    monkeypatch.setattr(state_module, "ethos_digest", forbidden)
    snapshot = state_module.load_state(tmp_path, state_only=True)
    assert snapshot["ethos"]["stage"] == "complete"
    payload = prerequisite_results_payload(state_module)
    payload.update(state=snapshot, revision=snapshot["revision"])
    result = conversation.render(payload)
    assert "- [x]" not in result
    assert "- [ ]" not in result
    assert "confirmed and validated" not in result
    assert result.endswith("The prerequisites for reviewing ETHOS.md are complete.\n")
    assert "Ready to continue?" not in result
    assert snapshot["ethos"]["stage"] == "complete"
    assert state_module.state_path(tmp_path).read_bytes() == before


def test_prerequisite_results_preserves_audit_framework_and_selected_scope(conversation, state_module, tmp_path):
    state_module.initialize(tmp_path, agent="Account Support", journey="audit", state_only=True)
    state_module.update(
        tmp_path,
        {"scopes": {"audit": {"trace_sources": ["selected-runs.jsonl"], "confirmed": True}}},
    )
    state_module.update(
        tmp_path,
        {
            "prerequisites": {
                key: {"status": "verified", "source": "Current audit check"}
                for key in state_module.COMMON_PREREQUISITES | {"data"}
            },
            "scopes": {"audit": {"framework": "Custom evaluation suite", "framework_source": "Suite documentation"}},
        },
        state_only=True,
    )
    snapshot = state_module.load_state(tmp_path, state_only=True)
    before = copy.deepcopy(snapshot)
    payload = prerequisite_results_payload(state_module, journey="audit")
    payload.update(state=snapshot, revision=snapshot["revision"])
    payload["facts"]["rows"]["evaluation_framework"].update(
        findings="Custom evaluation suite", source="Suite documentation"
    )
    result = conversation.render(payload)
    assert "Custom evaluation suite" in result
    assert "| Audit tooling |" in result
    assert "| Python and evaluation runtime |" not in result
    assert "Ready to continue?" not in result
    assert snapshot == before
    assert snapshot["setup"] is None
    assert snapshot["scopes"]["audit"] == {
        "trace_sources": ["selected-runs.jsonl"],
        "confirmed": True,
        "framework": "Custom evaluation suite",
        "framework_source": "Suite documentation",
    }


def test_prerequisite_results_blocker_does_not_replay_saved_intent_question(conversation, state_module):
    payload = prerequisite_results_payload(state_module)
    payload["state"]["prerequisites"]["runtime"] = {"status": "missing", "source": "Runtime lookup failed"}
    payload["state"]["blockers"] = [{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}]
    payload["facts"]["rows"]["runtime"].update(status="missing", findings="Runtime unavailable", source="Runtime check")
    payload["facts"]["blockers"] = [
        {
            "id": "runtime",
            "step": "Continue prerequisites",
            "reason": "Runtime unavailable",
            "needed": "Provide the selected runtime.",
        }
    ]
    result = conversation.render(payload)
    assert "### Blockers" in result
    assert "Provide the selected runtime." in result
    assert payload["state"]["pending_question"] not in result
    assert "### Next Steps" not in result
    assert "- [ ]" not in result
    assert "- [x]" not in result
    assert "Ready to continue?" not in result


def test_prerequisite_results_does_not_activate_stale_downstream_blockers(conversation, state_module):
    payload = payload_for(state_module, stage="prerequisite_results")
    complete_ethos(payload)
    payload["facts"].pop("ethos")
    payload["state"]["journey_status"]["create"] = "in_progress"
    payload["state"]["blockers"] = [
        {"id": "authoring_handoff", "phase": "create", "message": "Old downstream dependency"}
    ]
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert "### Blockers" not in result
    assert "- [x]" not in result
    assert "### Next Steps" not in result
    assert result.endswith("The prerequisites for reviewing ETHOS.md are complete.\n")
    assert "Ready to continue?" not in result
    assert payload == before


@pytest.mark.parametrize(
    "mutation", ["missing_revision", "stale_revision", "ready_override", "journey_override", "incomplete"]
)
def test_prerequisite_results_enforces_state_gate(conversation, state_module, mutation):
    payload = prerequisite_results_payload(state_module)
    if mutation == "missing_revision":
        payload.pop("revision")
    elif mutation == "stale_revision":
        payload["revision"] += 1
    elif mutation == "ready_override":
        payload["ready"] = True
    elif mutation == "journey_override":
        payload["journey"] = "audit"
    else:
        payload["state"]["prerequisites"]["runtime"]["status"] = "unknown"
        payload["facts"]["rows"]["runtime"]["status"] = "unknown"
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_unknown_goal_orientation_uses_short_welcome_without_checklist(conversation):
    result = conversation.render({"stage": "orientation"})
    assert result.startswith("Welcome to Eval Author!")
    assert "Here’s what we’ll work through:" not in result
    assert "What would you like to do?" in result
    assert "- [ ]" not in result
    assert "Ready to get started?" not in result


LEGACY_AUDIT_CONTINUATION = "We’re ready to check what your existing evaluations cover. Ready to continue?"
AUDIT_CONTINUATION = (
    "We’re ready to review evaluation coverage, identify gaps, and recommend improvements. Ready to continue?"
)


def test_legacy_audit_continuation_cannot_escape_a_saved_blocker(conversation, state_module):
    payload = payload_for(state_module, stage="resume", journey="audit")
    complete_ethos(payload)
    payload["state"]["pending_question"] = LEGACY_AUDIT_CONTINUATION
    payload["state"]["blockers"] = [{"id": "skills", "phase": "setup", "message": "Audit skill unavailable"}]
    payload["facts"]["blockers"] = [
        {
            "id": "skills",
            "step": "Continue prerequisites",
            "reason": "Audit skill unavailable",
            "needed": "Restore the required audit skill.",
        }
    ]
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert "### Blockers" in result
    assert "Ready to continue?" not in result
    assert LEGACY_AUDIT_CONTINUATION not in result
    assert payload == before


@pytest.mark.parametrize("unfinished", ["review", "in_progress", "complete"])
def test_legacy_audit_continuation_cannot_bypass_current_progress(conversation, state_module, unfinished):
    payload = payload_for(state_module, stage="resume", journey="audit")
    complete_ethos(payload)
    payload["state"]["pending_question"] = LEGACY_AUDIT_CONTINUATION
    if unfinished == "review":
        payload["state"]["ethos"]["stage"] = "review"
    else:
        payload["state"]["journey_status"]["audit"] = unfinished
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError):
        conversation.render(payload)
    assert payload == before


@pytest.mark.parametrize("stage", ["confirmed", "resume"])
@pytest.mark.parametrize(
    "saved,explicit",
    [
        (LEGACY_AUDIT_CONTINUATION, None),
        (LEGACY_AUDIT_CONTINUATION, LEGACY_AUDIT_CONTINUATION),
        (LEGACY_AUDIT_CONTINUATION, AUDIT_CONTINUATION),
        (AUDIT_CONTINUATION, LEGACY_AUDIT_CONTINUATION),
    ],
)
def test_legacy_audit_continuation_uses_current_copy_without_rewriting_state(
    conversation, state_module, stage, saved, explicit
):
    payload = payload_for(state_module, stage=stage, journey="audit")
    complete_ethos(payload)
    payload["state"]["pending_question"] = saved
    if explicit is not None:
        payload["facts"]["question"] = explicit
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert result.endswith(AUDIT_CONTINUATION + "\n")
    assert LEGACY_AUDIT_CONTINUATION not in result
    assert result.count("Ready to continue?") == 1
    assert payload == before


def unsupported_format_payload(state_module, *, stage="results", journey="expand"):
    payload = (
        prerequisite_results_payload(state_module, journey=journey)
        if stage == "prerequisite_results"
        else payload_for(state_module, stage=stage, journey=journey)
    )
    action = "Convert the existing evaluations to NeMo Gym or Harbor before we continue."
    payload["state"]["blockers"] = [
        {"id": "evaluation_format", "phase": "setup", "message": "The supplied suite uses an unsupported format."}
    ]
    payload["state"]["pending_question"] = action
    payload["state"]["next_handoff"] = "eval-author"
    payload["facts"]["rows"]["evaluation_framework"].update(
        status="missing", findings="The supplied suite uses an unsupported format.", source="Suite format inspection"
    )
    if stage != "prerequisite_results":
        payload["facts"]["question"] = action
    payload["facts"]["blockers"] = [
        {
            "id": "evaluation_format",
            "skill_link": "/skills/eval-author/SKILL.md",
            "step": "Continue shared prerequisites",
            "reason": "The supplied suite uses an unsupported format.",
            "needed": action,
        }
    ]
    return payload


@pytest.mark.parametrize("stage", ["results", "prerequisite_results"])
def test_conversion_handoff_is_copyable_guidance_inside_blockers(conversation, state_module, stage, monkeypatch):
    payload = unsupported_format_payload(state_module, stage=stage)
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Conversion guidance must not execute or change anything")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        result = conversation.render(payload)
        assert conversation.render(payload) == result
    assert result.count("```text\n") == 1
    assert result.count("```") == 2
    prompt = result.split("```text\n", 1)[1].split("```", 1)[0]
    assert "<suite path>" in prompt
    assert "<current framework>" in prompt
    assert "<NeMo Gym or Harbor>" in prompt
    assert "original suite" in prompt
    assert "scoring rules" in prompt
    assert "offline verifier checks" in prompt
    assert "live agent/model runs separate" in prompt
    assert result.index("### Blockers") < result.index("```text\n")
    assert "### Next Steps" not in result
    assert "- [ ]" not in result
    assert "- [x]" not in result
    assert "ETHOS.md" not in result
    assert "Ready to continue?" not in result
    assert payload == before


@pytest.mark.parametrize(
    "journey,blocker_id,phase",
    [
        ("create", "evaluation_format", "setup"),
        ("audit", "evaluation_format", "setup"),
        ("expand", "unsupported_format", "setup"),
        ("expand", "evaluation_format", "ethos"),
        ("expand", "evaluation_format", "expand"),
        ("expand", "evaluation_format", "audit"),
    ],
)
def test_conversion_handoff_requires_the_applicable_saved_format_blocker(
    conversation, state_module, journey, blocker_id, phase
):
    payload = unsupported_format_payload(state_module, journey=journey)
    complete_ethos(payload)
    payload["facts"]["rows"]["evaluation_framework"].update(status="verified", findings="Recorded framework")
    payload["state"]["blockers"][0].update(id=blocker_id, phase=phase)
    payload["facts"]["blockers"][0]["id"] = blocker_id
    payload["facts"]["blockers"][0].pop("skill_link")
    if phase not in {"setup", "ethos", journey}:
        payload["facts"].pop("blockers")
    result = conversation.render(payload)
    assert "```text" not in result
    assert "<suite path>" not in result


@pytest.mark.parametrize("missing", ["saved_blocker", "explanation"])
def test_conversion_handoff_needs_both_saved_blocker_and_explanation(conversation, state_module, missing):
    payload = unsupported_format_payload(state_module)
    if missing == "saved_blocker":
        payload["state"]["blockers"] = []
        payload["facts"]["rows"]["evaluation_framework"].update(status="verified", findings="Recorded framework")
    else:
        payload["facts"].pop("blockers")
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_conversion_handoff_retains_other_blocker_explanations(conversation, state_module):
    payload = unsupported_format_payload(state_module)
    payload["state"]["prerequisites"]["ethos_skills"] = {"status": "missing", "source": "Skill access check"}
    payload["state"]["blockers"].append(
        {"id": "ethos_skills", "phase": "setup", "message": "Required Ethos skill cannot be read."}
    )
    payload["facts"]["rows"]["skills"].update(
        status="missing", findings="Required Ethos skill cannot be read.", source="Skill access check"
    )
    payload["facts"]["blockers"].append(
        {
            "id": "ethos_skills",
            "step": "Review the agent intent",
            "reason": "Required Ethos skill cannot be read.",
            "needed": "Restore the required Ethos skill.",
        }
    )
    result = conversation.render(payload)
    assert "Required Ethos skill cannot be read." in result
    assert "Restore the required Ethos skill." in result
    assert "The supplied suite uses an unsupported format." in result
    assert result.count("```text\n") == 1
    assert "### Next Steps" not in result


AUDIT_TRACE_SKILL = "/skills/eval-author-audit/SKILL.md"
CORE_SKILL = "/skills/eval-author/SKILL.md"
AUDIT_TRACE_CLAUSE = "Confirm that traces from completed evaluations exist before continuing to Ethos"
FORMAT_CLAUSE = "Keep the blocker until the converted suite's actual format is checked"


def audit_trace_blocker_payload(state_module, *, status="unknown", stage="results", paraphrase="first"):
    payload = (
        prerequisite_results_payload(state_module, journey="audit")
        if stage == "prerequisite_results"
        else payload_for(state_module, stage=stage, journey="audit")
    )
    message = f"{paraphrase} chat says trace availability needs resolving."
    question = f"{paraphrase} chat asks whether completed evaluation traces can be supplied."
    payload["state"]["prerequisites"]["data"] = {"status": status, "source": f"{paraphrase} availability evidence"}
    payload["state"]["blockers"] = [{"id": "data", "phase": "setup", "message": message}]
    payload["state"]["pending_question"] = question
    payload["state"]["next_handoff"] = "eval-author"
    payload["facts"]["rows"]["traces"] = {
        "status": status,
        "findings": f"{paraphrase} chat describes the trace finding differently.",
        "source": f"{paraphrase} availability evidence",
    }
    if stage != "prerequisite_results":
        payload["facts"]["question"] = question
    payload["facts"]["blockers"] = [
        {
            "id": "data",
            "skill_link": AUDIT_TRACE_SKILL,
            "step": f"{paraphrase} blocking step",
            "reason": message,
            "needed": question,
        }
    ]
    return payload


@pytest.mark.parametrize("stage", ["results", "prerequisite_results", "resume"])
@pytest.mark.parametrize("status", ["unknown", "missing"])
def test_known_audit_blocker_is_identical_across_chat_paraphrases(conversation, state_module, stage, status):
    first = audit_trace_blocker_payload(state_module, status=status, stage=stage, paraphrase="first")
    second = audit_trace_blocker_payload(state_module, status=status, stage=stage, paraphrase="second")
    before = [copy.deepcopy(first), copy.deepcopy(second)]
    output = conversation.render(first)
    assert output == conversation.render(second)
    assert "first chat" not in output
    assert "second chat" not in output
    assert "first blocking step" not in output
    assert first["state"]["pending_question"] not in output
    assert "### Blockers" in output
    assert "### Next Steps" not in output
    assert "- [ ]" not in output
    assert "Ready to continue?" not in output
    assert AUDIT_TRACE_CLAUSE in output
    assert f"(<{AUDIT_TRACE_SKILL}>)" in output
    assert [first, second] == before


@pytest.mark.parametrize("stage", ["results", "prerequisite_results", "resume"])
def test_known_format_blocker_is_identical_across_chat_paraphrases(conversation, state_module, stage):
    first = unsupported_format_payload(state_module, stage=stage)
    second = copy.deepcopy(first)
    second["state"]["blockers"][0]["message"] = "Another chat describes an incompatible suite."
    second["state"]["pending_question"] = "Another chat asks for conversion before continuing."
    if stage != "prerequisite_results":
        second["facts"]["question"] = second["state"]["pending_question"]
    second["facts"]["blockers"][0].update(
        step="Another blocking step", reason="Another reason", needed="Another action"
    )
    before = [copy.deepcopy(first), copy.deepcopy(second)]
    result = conversation.render(first)
    assert result == conversation.render(second)
    assert FORMAT_CLAUSE in result
    assert f"(<{CORE_SKILL}>)" in result
    assert "Another action" not in result
    assert result.count("```text\n") == 1
    assert "### Next Steps" not in result
    assert [first, second] == before


def test_known_audit_blocker_uses_saved_availability_even_without_row_facts(conversation, state_module):
    outputs = {}
    for status, label in (("unknown", "Unknown"), ("missing", "Missing")):
        payload = audit_trace_blocker_payload(state_module, status=status)
        payload["facts"]["rows"].pop("traces")
        result = conversation.render(payload)
        row = next(line for line in result.splitlines() if line.startswith("| Traces from completed evaluation runs |"))
        assert f"| {label} |" in row
        assert "first chat" not in row
        assert "Verified" not in row
        assert "Confirmed by you" not in row
        outputs[status] = result
    assert outputs["unknown"] != outputs["missing"]


@pytest.mark.parametrize("status", ["unknown", "missing"])
def test_known_audit_blocker_only_requires_id_and_verified_skill_reference(conversation, state_module, status):
    payload = audit_trace_blocker_payload(state_module, status=status)
    payload["facts"]["blockers"] = [{"id": "data", "skill_link": AUDIT_TRACE_SKILL}]
    result = conversation.render(payload)
    assert AUDIT_TRACE_CLAUSE in result
    assert "### Blockers" in result


@pytest.mark.parametrize("status,phase", [("verified", "setup"), ("confirmed", "setup"), ("unknown", "ethos")])
def test_audit_blocker_outside_known_availability_condition_keeps_custom_copy(
    conversation, state_module, status, phase
):
    payload = audit_trace_blocker_payload(state_module, status=status)
    payload["state"]["blockers"][0]["phase"] = phase
    item = payload["facts"]["blockers"][0]
    item.pop("skill_link")
    item.update(
        step="Resolve selected evidence access",
        reason="Custom source-access constraint",
        needed="Grant selected-source read access.",
    )
    result = conversation.render(payload)
    assert "Custom source-access constraint" in result
    assert "Grant selected-source read access." in result
    assert AUDIT_TRACE_CLAUSE not in result


def test_known_blocker_keeps_other_custom_needed_actions(conversation, state_module):
    payload = audit_trace_blocker_payload(state_module)
    payload["state"]["blockers"].append({"id": "code_access", "phase": "setup", "message": "Source access denied"})
    payload["facts"]["blockers"].append(
        {
            "id": "code_access",
            "step": "Inspect source",
            "reason": "Source access denied",
            "needed": "Grant access to the selected repository.",
        }
    )
    result = conversation.render(payload)
    assert AUDIT_TRACE_CLAUSE in result
    assert "Grant access to the selected repository." in result
    assert payload["state"]["pending_question"] not in result
    assert "### Next Steps" not in result


def test_known_blocker_attribution_does_not_inspect_link_or_mutate_state(conversation, state_module, monkeypatch):
    payload = audit_trace_blocker_payload(state_module)
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Known blocker rendering must use the already-verified source reference")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(state_module, "now", forbidden)
        result = conversation.render(payload)
        assert result == conversation.render(payload)
    assert AUDIT_TRACE_CLAUSE in result
    assert payload == before


@pytest.mark.parametrize("kind", ["audit", "format"])
@pytest.mark.parametrize(
    "skill_link",
    [
        None,
        "skills/eval-author/SKILL.md",
        "/skills/eval-author/README.md",
        "javascript:alert(1)",
        "https:///SKILL.md",
        "https://example.test/SKILL.md\nmalicious",
    ],
)
def test_known_blocker_rejects_missing_or_bad_skill_attribution(conversation, state_module, kind, skill_link):
    payload = audit_trace_blocker_payload(state_module) if kind == "audit" else unsupported_format_payload(state_module)
    if skill_link is None:
        payload["facts"]["blockers"][0].pop("skill_link")
    else:
        payload["facts"]["blockers"][0]["skill_link"] = skill_link
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize(
    "skill_link",
    [
        "/installed/custom/SKILL.md",
        "https://example.test/skills/eval-author-audit/SKILL.md",
        "http://example.test/skills/eval-author-audit/SKILL.md",
    ],
)
def test_known_blocker_preserves_the_actual_supplied_skill_reference(conversation, state_module, skill_link):
    payload = audit_trace_blocker_payload(state_module)
    payload["facts"]["blockers"] = [{"id": "data", "skill_link": skill_link}]
    result = conversation.render(payload)
    assert f"(<{skill_link}>)" in result
    assert AUDIT_TRACE_CLAUSE in result


@pytest.mark.parametrize("journey", JOURNEYS)
def test_one_turn_has_live_and_completed_tables_then_one_recommendation_section_and_checklist(
    conversation, state_module, journey
):
    preview = {"stage": "prerequisites", "journey": journey, "ready": True}
    final = payload_for(state_module, stage="results", journey=journey)
    final["facts"]["recommendations"] = [{"text": "Optional guidance for suite maintainers."}]
    final["state"]["ethos"]["stage"] = "review"
    question = "Is this the correct and current intended behavior?"
    final["state"]["pending_question"] = question
    final["facts"]["question"] = question
    final["facts"]["ethos"] = {
        "status": "existing",
        "link": "/workspace/agent/ETHOS.md",
        "summary": "The agent restores account access after confirming identity.",
    }
    inputs = [preview, final]
    before = copy.deepcopy(inputs)
    messages = [conversation.render(payload) for payload in inputs]
    turn = "\n".join(messages)
    assert turn.count("| Prerequisite | Status | Findings |") == 2
    assert turn.count("### Recommendations") == 1
    assert turn.count("### Next Steps") == 1
    assert turn.count("- [x]") == 1
    assert turn.count("- [ ]") == 2
    assert turn.count(question) == 1
    assert messages[0].startswith(PROGRESS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert "| Checking |" in messages[0]
    assert messages[1].startswith(RESULTS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert messages[1].count("| Prerequisite | Status | Findings |") == 1
    assert "| Checking |" not in messages[1]
    assert "### Recommendations" in messages[1]
    assert "### Next Steps" in messages[1]
    assert messages[1].endswith("**" + question + "**\n")
    assert inputs == before


def test_live_prerequisites_table_reuses_known_framework_and_retains_unknown_traces(conversation):
    payload = {
        "stage": "prerequisites",
        "journey": "audit",
        "ready": True,
        "facts": {
            "rows": {
                "evaluation_framework": {
                    "status": "confirmed",
                    "findings": "You selected the custom evaluation suite.",
                    "source": "User reply 1",
                },
                "traces": {"status": "unknown", "findings": "Saved trace availability is unresolved."},
            }
        },
    }
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert result.startswith(PROGRESS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert "| Evaluation framework | Confirmed by you | You selected the custom evaluation suite. |" in result
    assert "| Traces from completed evaluation runs | Unknown | Saved trace availability is unresolved. |" in result
    assert "| Required skills | Checking |" in result
    assert "I’ll check" not in result
    assert "### Next Steps" not in result
    assert payload == before
    payload["ready"] = False
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("missing", ["source", "findings"])
def test_live_prerequisites_known_facts_require_provenance_and_findings(conversation, missing):
    record = {"status": "confirmed", "findings": "You selected NeMo Gym.", "source": "User reply 1"}
    record.pop(missing)
    with pytest.raises(ValueError):
        conversation.render(
            {
                "stage": "prerequisites",
                "journey": "create",
                "ready": True,
                "facts": {"rows": {"evaluation_framework": record}},
            }
        )


def test_checking_status_is_only_valid_in_the_live_prerequisites_table(conversation, state_module):
    result = conversation.render(
        {
            "stage": "prerequisites",
            "journey": "create",
            "ready": True,
            "facts": {"rows": {"runtime": {"status": "checking"}}},
        }
    )
    assert "| Python and evaluation runtime | Checking |" in result
    payload = payload_for(state_module)
    payload["facts"]["rows"]["runtime"]["status"] = "checking"
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("stage", ["orientation", "goal_selected"])
def test_opening_stages_still_reject_prerequisite_findings(conversation, stage):
    with pytest.raises(ValueError):
        conversation.render({"stage": stage, "journey": "audit", "facts": {"rows": {}}})


@pytest.mark.parametrize("surface", ["ethos", "draft", "evidence"])
def test_local_artifact_links_preserve_literal_filename_characters(conversation, state_module, surface):
    repository = "/workspace/agent #release?case%20literal"
    encoded_repository = "/workspace/agent%20%23release%3Fcase%2520literal"
    if surface == "draft":
        payload = draft_payload(state_module)
        path = ".eval-author/.working/ethos/draft #review?version%20literal.md"
        payload["state"]["ethos"]["draft"]["path"] = path
        payload["facts"]["draft"]["link"] = repository + "/" + path
        label = "ETHOS.md draft"
        expected = encoded_repository + "/.eval-author/.working/ethos/draft%20%23review%3Fversion%2520literal.md"
    elif surface == "ethos":
        payload = payload_for(state_module, stage="ethos")
        payload["state"]["ethos"]["stage"] = "review"
        payload["facts"]["ethos"] = {
            "status": "existing",
            "link": repository + "/ETHOS.md",
            "summary": "Account recovery intent",
        }
        label = "ETHOS.md"
        expected = encoded_repository + "/ETHOS.md"
    else:
        payload = payload_for(state_module)
        label = "Observed evidence"
        payload["facts"]["rows"]["agent_repository"]["links"] = [
            {"label": label, "target": repository + "/evidence #run?case%20literal.json"}
        ]
        expected = encoded_repository + "/evidence%20%23run%3Fcase%2520literal.json"
    payload["state"]["repository"] = repository
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert f"[{label}](<{expected}>)" in result
    assert payload == before


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_web_links_preserve_query_fragment_and_existing_percent_encoding(conversation, state_module, scheme):
    payload = payload_for(state_module)
    target = f"{scheme}://example.test/evidence%20run.json?case=a%2Fb&label=two words#run-1"
    expected = f"{scheme}://example.test/evidence%20run.json?case=a%2Fb&label=two%20words#run-1"
    payload["facts"]["rows"]["agent_repository"]["links"] = [{"label": "Evidence", "target": target}]
    payload["facts"]["recommendations"] = [{"text": "Review documentation", "link": target}]
    before = copy.deepcopy(payload)
    result = conversation.render(payload)
    assert f"[Evidence](<{expected}>)" in result
    assert f"[Review documentation](<{expected}>)" in result
    assert payload == before


STATE_FAILURE_NOTICE = (
    "These findings are retained only in this conversation. A fresh chat cannot resume them until they are saved."
)


def prerequisite_failure_payload(journey="create"):
    return {
        "stage": "prerequisite_failure",
        "journey": journey,
        "ready": True,
        "facts": {
            "state_error": "Permission denied: .eval-author/.working/journey-state.json",
            "rows": {
                "agent_repository": {
                    "status": "verified",
                    "findings": "The selected agent repository is available.",
                    "source": "Repository inspection",
                },
                "access": {
                    "status": "missing",
                    "findings": "The repository working directory is read-only.",
                    "source": "The state-save attempt failed",
                },
            },
        },
    }


@pytest.mark.parametrize("journey", JOURNEYS)
def test_prerequisite_failure_reports_unsaved_findings_without_state_or_io(
    conversation, state_module, monkeypatch, journey
):
    payload = prerequisite_failure_payload(journey)
    before = copy.deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Reporting a state failure must not access files, state, or commands")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        for name in ("load_state", "initialize", "update", "confirm_ethos", "now"):
            patch.setattr(state_module, name, forbidden)
        result = conversation.render(payload)
        assert conversation.render(payload) == result
    assert result.startswith(RESULTS_INTRO + "\n\n| Prerequisite | Status | Findings |\n")
    assert "| Agent and repository | Verified | The selected agent repository is available. |" in result
    assert "| File and command access | Missing | The repository working directory is read-only. |" in result
    assert "| Required skills | Unknown |" in result
    assert "| Checking |" not in result
    assert result.count("### Blockers") == 1
    assert "Permission denied:" in result
    assert result.endswith(STATE_FAILURE_NOTICE + "\n")
    for absent in ("### Next Steps", "- [x]", "- [ ]", "Ready to continue?", "confirmed and validated"):
        assert absent not in result
    assert "The prerequisites for reviewing ETHOS.md are complete." not in result
    assert payload == before


def test_prerequisite_failure_cli_uses_stdin_when_state_storage_is_unavailable(
    conversation, state_module, monkeypatch, tmp_path
):
    payload = prerequisite_failure_payload()
    output = io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("Simulated read-only repository with no saved state")

    with monkeypatch.context() as patch:
        patch.chdir(tmp_path)
        patch.setattr(sys, "argv", ["conversation_renderer.py", "--input", "-"])
        patch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        patch.setattr(sys, "stdout", stdout)
        patch.setattr(builtins, "open", denied)
        patch.setattr(Path, "open", denied)
        patch.setattr(Path, "stat", denied)
        for name in ("load_state", "initialize", "update", "confirm_ethos"):
            patch.setattr(state_module, name, denied)
        code = conversation.main()
    assert code == 0
    assert output.getvalue().decode("utf-8") == conversation.render(payload)
    assert list(tmp_path.iterdir()) == []


def test_prerequisite_failure_error_cannot_inject_sections_or_claim_saved_progress(conversation):
    payload = prerequisite_failure_payload()
    payload["facts"]["state_error"] = (
        "Permission denied | path\n\n### Next Steps\n- [x] Run the agent\n<script>danger</script>"
    )
    result = conversation.render(payload)
    assert result.count("\n### Blockers\n") == 1
    assert "\n### Next Steps\n" not in result
    assert "\n- [x]" not in result
    assert "<script>" not in result
    assert "&lt;script&gt;danger&lt;/script&gt;" in result
    assert "Permission denied &#124; path" in result
    assert result.endswith(STATE_FAILURE_NOTICE + "\n")


@pytest.mark.parametrize(
    "key,value",
    [
        ("journey", None),
        ("journey", "unsupported"),
        ("ready", None),
        ("ready", False),
        ("ready", 1),
        ("ready", "true"),
        ("state", None),
        ("state", {}),
        ("revision", 0),
    ],
)
def test_prerequisite_failure_rejects_unready_or_state_backed_inputs(conversation, key, value):
    payload = prerequisite_failure_payload()
    payload[key] = value
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("error", [None, "", " \n ", [], "x" * 2001])
def test_prerequisite_failure_requires_a_bounded_observed_error(conversation, error):
    payload = prerequisite_failure_payload()
    if error is None:
        payload["facts"].pop("state_error")
    else:
        payload["facts"]["state_error"] = error
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("extra", ["ethos", "draft", "question", "recommendations", "blockers"])
def test_prerequisite_failure_rejects_other_workflow_facts(conversation, extra):
    payload = prerequisite_failure_payload()
    payload["facts"][extra] = {}
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("missing", ["source", "findings"])
def test_prerequisite_failure_keeps_known_row_evidence_requirements(conversation, missing):
    payload = prerequisite_failure_payload()
    payload["facts"]["rows"]["agent_repository"].pop(missing)
    with pytest.raises(ValueError):
        conversation.render(payload)


def test_prerequisite_failure_rejects_in_progress_row_status(conversation):
    payload = prerequisite_failure_payload()
    payload["facts"]["rows"]["access"]["status"] = "checking"
    with pytest.raises(ValueError):
        conversation.render(payload)


@pytest.mark.parametrize("stage", ["prerequisite_results", "results", "ethos", "confirmed", "resume"])
def test_reporting_state_failure_cannot_bypass_normal_state_requirements(conversation, stage):
    payload = prerequisite_failure_payload()
    payload["stage"] = stage
    with pytest.raises(ValueError, match="Supply reconciled journey state"):
        conversation.render(payload)


def test_prerequisite_failure_is_not_a_cli_preview(conversation, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["conversation_renderer.py", "--stage", "prerequisite_failure", "--journey", "create", "--ready"],
    )
    with pytest.raises(SystemExit) as exc:
        conversation.main()
    assert exc.value.code == 2
