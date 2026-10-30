# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep prerequisite findings, optional advice, and the next action distinct."""

import importlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def onboarding(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
    state_helper = importlib.import_module("journey_state")
    renderer = importlib.import_module("conversation_renderer")
    shared = importlib.import_module("conversation_templates")
    state = state_helper.new_state(tmp_path)
    state.update(agent="support assistant", journey="create", setup="harbor", revision=4)
    state["prerequisites"] = {
        key: {"status": "verified", "source": "Observed local prerequisite check"} for key in state_helper.PREREQUISITES
    }
    for key, status in {
        "services": "not_required",
        "data": "unknown",
        "agent_connection": "unknown",
        "model_credentials": "missing",
    }.items():
        state["prerequisites"][key] = {"status": status, "source": "Observed check; resolve before agent runs"}
    state["blockers"] = [
        {"id": key, "phase": "create", "message": "Needed before agent execution"}
        for key in ("agent_connection", "model_credentials")
    ]
    state["ethos"]["stage"] = "interview"
    state["pending_question"] = "What should this agent accomplish for its users?"
    state["next_handoff"] = "ethos-orchestrate"
    state_helper.validate(state)
    facts = {
        "rows": {
            key: {"status": status, "findings": finding, "source": "Observed prerequisite evidence"}
            for key, status, finding in (
                ("agent_repository", "verified", "The selected agent repository is available."),
                ("evaluation_framework", "verified", "Harbor is the selected evaluation framework."),
                ("skills", "verified", "The required Eval Author and ETHOS.md skills are available."),
                ("access", "verified", "File and command access are available."),
                ("runtime", "verified", "Python and the supported Harbor runtime are installed."),
                ("environment", "verified", "The local environment checks passed."),
                ("services_data", "unknown", "No additional services are needed; data selection is deferred."),
                ("agent_access", "missing", "Confirm the agent connection and credentials before agent runs."),
            )
        },
        "recommendations": [
            {
                "text": "Harbor’s assistant skills can help with task authoring. They are optional.",
                "link": "https://github.com/harbor-framework/harbor/tree/main/skills",
            }
        ],
        "blockers": [],
    }
    return state_helper, renderer, shared, state, facts


def presentation(onboarding, stage):
    _, _, _, state, facts = onboarding
    facts = deepcopy(facts)
    if stage == "results":
        facts["ethos"] = {"status": "absent"}
    return {"stage": stage, "state": state, "revision": state["revision"], "facts": facts}


def test_unblocked_results_keep_recommendations_before_next_steps(onboarding, monkeypatch):
    state_helper, renderer, shared, state, _ = onboarding
    payload = presentation(onboarding, "results")
    before = deepcopy(payload)

    def unexpected_io(*args, **kwargs):
        pytest.fail("Rendering must use the supplied snapshot without artifact reads or clock access")

    monkeypatch.setattr(Path, "open", unexpected_io)
    monkeypatch.setattr(state_helper, "now", unexpected_io)
    output = renderer.render(payload)

    assert re.findall(r"^### .+$", output, re.MULTILINE) == ["### Recommendations", "### Next Steps"]
    assert shared.RECOMMENDATIONS in output
    assert output.index("| Agent connection and credentials | Missing |") < output.index(shared.RECOMMENDATIONS)
    assert output.index(shared.RECOMMENDATIONS) < output.index(shared.NEXT_STEPS)
    assert "[Harbor’s assistant skills can help with task authoring. They are optional.]" in output
    assert "(<https://github.com/harbor-framework/harbor/tree/main/skills>)" in output
    assert output.rstrip().endswith(state["pending_question"])
    assert output.count("- [x]") == 1
    assert output.count("- [ ]") == 2
    assert "### Blockers" not in output
    assert payload == before
    assert renderer.render(payload) == output


def test_prerequisite_results_keep_optional_advice_without_execution_blockers(onboarding):
    _, renderer, shared, _, _ = onboarding
    output = renderer.render(presentation(onboarding, "prerequisite_results"))

    assert re.findall(r"^### .+$", output, re.MULTILINE) == ["### Recommendations"]
    assert shared.RECOMMENDATIONS in output
    assert output.rstrip().endswith("The prerequisites for reviewing ETHOS.md are complete.")
    assert "### Blockers" not in output
    assert not re.search(r"^- \[[ x]\] ", output, re.MULTILINE)
    assert "What should this agent accomplish" not in output


def test_existing_ethos_navigation_uses_selected_agent_path_without_io(onboarding, monkeypatch):
    state_helper, renderer, _, state, _ = onboarding
    relative = "agents/support/ETHOS.md"
    state["ethos"].update(path=relative, stage="review")
    state["pending_question"] = "Does this describe the selected agent’s intended behavior?"
    request = presentation(onboarding, "ethos")
    target = state["repository"] + "/" + relative
    request["facts"]["ethos"] = {"status": "existing", "link": target, "summary": "Support agent intent"}
    before = deepcopy(request)

    def unexpected_io(*args, **kwargs):
        pytest.fail("Selected ETHOS.md navigation must use state without inspecting files")

    monkeypatch.setattr(Path, "open", unexpected_io)
    monkeypatch.setattr(state_helper, "now", unexpected_io)
    output = renderer.render(request)
    assert f"[ETHOS.md](<{target}>)" in output and request == before
    request["facts"]["ethos"]["link"] = state["repository"] + "/ETHOS.md"
    with pytest.raises(ValueError, match="selected document"):
        renderer.render(request)


@pytest.mark.parametrize("stage", ["results", "prerequisite_results"])
def test_no_recommendations_does_not_emit_empty_section(onboarding, stage):
    _, renderer, _, _, _ = onboarding
    payload = presentation(onboarding, stage)
    payload["facts"]["recommendations"] = []

    output = renderer.render(payload)

    assert "### Recommendations" not in output
    assert "These suggestions are optional" not in output
    assert "### Blockers" not in output


def test_required_setup_blocker_follows_recommendations_and_suppresses_next_steps(onboarding):
    state_helper, renderer, shared, state, _ = onboarding
    state["prerequisites"]["ethos_skills"] = {"status": "missing", "source": "Required skill was not found"}
    state["blockers"].append({"id": "ethos_skills", "phase": "setup", "message": "Required ETHOS.md skill is missing"})
    state_helper.validate(state)
    payload = presentation(onboarding, "prerequisite_results")
    payload["facts"]["rows"]["skills"]["status"] = "missing"
    payload["facts"]["rows"]["skills"]["findings"] = "A required ETHOS.md skill is missing."
    payload["facts"]["blockers"] = [
        {
            "id": "ethos_skills",
            "step": "Review ETHOS.md",
            "reason": "A required ETHOS.md skill is missing.",
            "needed": "Install the missing required skill, then repeat its prerequisite check.",
        }
    ]

    output = renderer.render(payload)

    assert re.findall(r"^### .+$", output, re.MULTILINE) == ["### Recommendations", "### Blockers"]
    assert output.index(shared.RECOMMENDATIONS) < output.index(shared.BLOCKERS)
    assert "**Review ETHOS.md:**" in output
    assert "**Needed:** Install the missing required skill" in output
    assert "### Next Steps" not in output
    assert "The prerequisites for reviewing ETHOS.md are complete." not in output


def confirmed_request(onboarding, *, stage="confirmed", question=None):
    _, _, _, state, _ = onboarding
    state["ethos"].update(
        stage="complete",
        confirmed_digest="a" * 64,
        confirmation_source="User approved the exact saved document",
        validation="Writer validator passed",
    )
    state["pending_question"] = question
    state["next_handoff"] = "eval-author-first-eval"
    request = presentation(onboarding, stage)
    request["facts"]["recommendations"] = []
    request["facts"]["ethos"] = {"status": "existing", "link": state["repository"] + "/" + state["ethos"]["path"]}
    return request


@pytest.mark.parametrize("stage", ["confirmed", "resume", "results"])
@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("question", [None, "legacy", "source"])
def test_fresh_confirmation_collects_sources_without_an_extra_readiness_pause(
    onboarding, monkeypatch, stage, provider, question
):
    state_helper, renderer, shared, state, _ = onboarding
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    question = {None: None, "legacy": copy.CONTINUATION["create"], "source": flow.TRACE_QUESTION}[question]
    state["setup"] = provider
    state["ethos"]["path"] = "agents/support/ETHOS.md"
    request = confirmed_request(onboarding, stage=stage, question=question)
    before = deepcopy(request)

    def unexpected_io(*args, **kwargs):
        pytest.fail("Combined confirmation must use supplied state without file reads or clocks")

    monkeypatch.setattr(Path, "open", unexpected_io)
    monkeypatch.setattr(state_helper, "now", unexpected_io)
    output = renderer.render(request)

    assert output == renderer.render(request)
    assert "[ETHOS.md](<" + state["repository"] + "/agents/support/ETHOS.md>) is confirmed and validated." in output
    assert output.count(shared.NEXT_STEPS) == 1
    assert output.count("- [x]") == 2 and output.count("- [ ]") == 1
    assert output.index(shared.NEXT_STEPS) < output.index(flow.TRACE_INTRO) < output.index(flow.TRACE_QUESTION)
    assert output.count(flow.TRACE_QUESTION) == 1
    assert output.endswith("\n".join(f"- {label}" for label in flow.TRACE_CHOICES) + "\n")
    assert copy.CONTINUATION["create"] not in output
    assert request == before and not state["scopes"]


@pytest.mark.parametrize("journey", ["audit", "expand"])
def test_other_flows_retain_their_confirmation_handoff(onboarding, journey):
    _, renderer, _, state, _ = onboarding
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    state["journey"] = journey
    state["blockers"] = []
    state["prerequisites"]["data"] = {"status": "verified", "source": "Selected audit traces are available"}
    request = confirmed_request(onboarding, question=copy.CONTINUATION[journey])
    output = renderer.render(request)
    assert output.endswith(copy.CONTINUATION[journey] + "\n")
    assert flow.TRACE_QUESTION not in output


def test_known_sources_are_not_asked_again_at_confirmation(onboarding):
    _, renderer, _, state, _ = onboarding
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    state["scopes"]["create"] = {"trace_choice": "none"}
    output = renderer.render(confirmed_request(onboarding, question=copy.CONTINUATION["create"]))
    assert flow.TRACE_QUESTION not in output
    assert state["scopes"]["create"] == {"trace_choice": "none"}


@pytest.mark.parametrize("reason", ["answered", "started", "other_flow"])
def test_saved_source_question_cannot_restart_or_override_progress(onboarding, reason):
    _, renderer, _, state, _ = onboarding
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    request = confirmed_request(onboarding, stage="resume", question=flow.TRACE_QUESTION)
    if reason == "answered":
        state["scopes"]["create"] = {"trace_choice": "none"}
    elif reason == "started":
        state["journey_status"]["create"] = "in_progress"
        state["blockers"] = []
    else:
        state["journey"] = "expand"
        state["blockers"] = []
    with pytest.raises(ValueError, match="Saved source question is stale"):
        renderer.render(request)


def test_source_choice_waits_for_ethos_completion_and_verified_navigation(onboarding):
    _, renderer, _, state, _ = onboarding
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    request = confirmed_request(onboarding, stage="resume", question=flow.TRACE_QUESTION)
    request["facts"].pop("ethos")
    with pytest.raises(ValueError, match="verified link"):
        renderer.render(request)
    state["ethos"].update(stage="review", confirmed_digest=None, confirmation_source=None, validation=None)
    with pytest.raises(ValueError, match="review is unfinished"):
        renderer.render(request)


def test_blockers_suppress_the_combined_source_question(onboarding):
    _, renderer, _, state, _ = onboarding
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    request = confirmed_request(onboarding, question=flow.TRACE_QUESTION)
    state["blockers"].append({"id": "ethos_skills", "phase": "setup", "message": "Selected writer unavailable"})
    state["prerequisites"]["ethos_skills"] = {"status": "missing", "source": "Required writer disappeared"}
    request["facts"]["blockers"] = [
        {
            "id": "ethos_skills",
            "step": "Review ETHOS.md",
            "reason": "Selected writer unavailable",
            "needed": "Restore it",
        }
    ]
    output = renderer.render(request)
    assert "### Blockers" in output
    assert flow.TRACE_QUESTION not in output and "### Next Steps" not in output


def test_goal_switch_does_not_infer_a_new_source_pause(onboarding):
    _, renderer, _, state, _ = onboarding
    flow = importlib.import_module("conversation_flows.first_eval_templates")
    request = confirmed_request(onboarding, stage="resume")
    state["journey_status"]["audit"] = "in_progress"
    output = renderer.render(request)
    assert flow.TRACE_QUESTION not in output and "confirmed and validated" not in output


@pytest.mark.parametrize("origin", ["create", "expand"])
@pytest.mark.parametrize("status", ["in_progress", "complete"])
@pytest.mark.parametrize(
    "question",
    [
        "Are these the evaluation suites you want to audit?",
        "Are the Intended coverage checks and evidence requirements right?",
    ],
)
def test_audit_resume_asks_only_the_unresolved_decision_after_prior_journey(onboarding, origin, status, question):
    _, renderer, shared, state, _ = onboarding
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    confirmed_request(onboarding)
    state["journey"] = "audit"
    state["journey_status"][origin] = status
    state["prerequisites"]["data"] = {
        "status": "confirmed",
        "source": "User confirms saved traces from completed evaluations",
    }
    state["pending_question"] = question
    state["next_handoff"] = "eval-author-audit"
    request = {"stage": "resume", "state": state, "revision": state["revision"], "facts": {"question": question}}
    output = renderer.render(request)
    assert output.rstrip().endswith(question) and output.count(question) == 1
    assert output.count(shared.NEXT_STEPS) == 1
    assert output.count("- [x]") == 2 and output.count("- [ ]") == 1
    assert shared.JOURNEYS["audit"] in output
    assert copy.WELCOME not in output and copy.READY not in output
    assert all(pause not in output for pause in copy.CONTINUATION.values())
    assert "confirmed and validated" not in output and copy.TABLE_HEADER not in output


@pytest.mark.parametrize("trace_status", ["confirmed", "unknown", "missing"])
def test_audit_welcome_preserves_saved_progress_without_claiming_artifact_confirmation(
    onboarding, monkeypatch, trace_status
):
    state_helper, renderer, shared, state, _ = onboarding
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    confirmed_request(onboarding)
    state["journey"] = "audit"
    state["journey_status"]["audit"] = "complete"
    state["pending_question"] = "Which completed runs should this audit use?"
    state["next_handoff"] = "eval-author-audit"
    state["prerequisites"]["data"] = {"status": trace_status, "source": "Saved trace availability answer"}
    state["blockers"] = (
        [] if trace_status == "confirmed" else [{"id": "data", "phase": "setup", "message": "Traces required"}]
    )
    request = {"stage": "audit_welcome", "state": state, "revision": state["revision"]}
    before = deepcopy(request)

    def unexpected_io(*args, **kwargs):
        pytest.fail("The early audit welcome must not inspect artifacts, write state, or read clocks")

    monkeypatch.setattr(Path, "open", unexpected_io)
    monkeypatch.setattr(state_helper, "now", unexpected_io)
    monkeypatch.setattr(state_helper.first_eval_state, "observe", unexpected_io)
    output = renderer.render(request)

    assert output.startswith(copy.WELCOME_WITH_CHECKLIST + "\n\n")
    assert output.endswith(copy.AUDIT_REUSE_NOTICE + "\n")
    assert len(re.findall(r"^- \[[ x]\] ", output, re.MULTILINE)) == 3
    assert output.count("- [x]") == (1 if trace_status == "confirmed" else 0)
    assert f"- [ ] **{shared.CHECKLIST_LABELS[1]}**" in output
    assert f"- [ ] **{shared.JOURNEYS['audit']}**" in output
    assert copy.READY not in output and state["pending_question"] not in output
    assert all(pause not in output for pause in copy.CONTINUATION.values())
    assert "confirmed and validated" not in output and copy.TABLE_HEADER not in output
    assert renderer.render(request) == output and request == before


@pytest.mark.parametrize(
    "patch",
    [
        {"state": None},
        {"revision": 999},
        {"revision": None},
        {"journey": "audit"},
        {"ready": True},
        {"facts": {"question": "Continue?"}},
    ],
)
def test_audit_welcome_rejects_missing_state_and_presentation_overrides(onboarding, patch):
    _, renderer, _, state, _ = onboarding
    state["journey"] = "audit"
    request = {"stage": "audit_welcome", "state": state, "revision": state["revision"], **patch}
    with pytest.raises(ValueError):
        renderer.render(request)


@pytest.mark.parametrize("journey", ["create", "expand"])
def test_audit_welcome_requires_the_saved_audit_journey(onboarding, journey):
    _, renderer, _, state, _ = onboarding
    state["journey"] = journey
    with pytest.raises(ValueError, match="requires saved journey audit"):
        renderer.render({"stage": "audit_welcome", "state": state, "revision": state["revision"]})


def test_audit_welcome_cli_uses_saved_snapshot_without_a_readiness_preview(onboarding):
    _, renderer, _, state, _ = onboarding
    state["journey"] = "audit"
    request = {"stage": "audit_welcome", "state": state, "revision": state["revision"]}
    result = subprocess.run(
        [sys.executable, "-B", renderer.__file__, "--input", "-"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == renderer.render(request)
    assert "audit_welcome" not in renderer.PREVIEWS


def test_audit_entry_failure_renders_escaped_error_reason_and_recovery_without_io(onboarding, monkeypatch):
    state_helper, renderer, shared, _, _ = onboarding
    copy = importlib.import_module("conversation_flows.onboarding_templates")
    request = {
        "stage": "audit_entry_failure",
        "journey": "audit",
        "facts": {
            "state_error": "Saved **state** is corrupt | <foreign>\n# injected",
            "recovery": "Restore a *known valid copy* | <backup>\n# recovery",
        },
    }
    before = deepcopy(request)

    def unexpected_io(*args, **kwargs):
        pytest.fail("Saved-progress errors must render without artifacts, state writes, or clocks")

    monkeypatch.setattr(Path, "open", unexpected_io)
    for name in ("now", "load_state", "initialize", "update", "save"):
        monkeypatch.setattr(state_helper, name, unexpected_io)
    output = renderer.render(request)
    escaped = r"Saved \*\*state\*\* is corrupt &#124; &lt;foreign&gt; \# injected"
    recovery = r"Restore a \*known valid copy\* &#124; &lt;backup&gt; \# recovery"
    assert output == (
        shared.BLOCKERS + "\n\n" + copy.AUDIT_ENTRY_STATE_FAILURE.format(error=escaped, recovery=recovery) + "\n"
    )
    assert "I’m pausing audit checks because" in output
    assert "reload saved progress and continue" in output
    assert renderer.render(request) == output and request == before
    assert copy.WELCOME not in output and copy.READY not in output
    assert copy.TABLE_HEADER not in output and "- [x]" not in output and "- [ ]" not in output
    assert copy.PREREQUISITES_COMPLETE not in output and copy.PREREQUISITE_UNSAVED_NOTICE not in output


@pytest.mark.parametrize(
    "patch",
    [
        {"ready": True},
        {"ready": False},
        {"state": {}},
        {"revision": 1},
        {"rows": {}},
        {"journey": "create"},
        {"journey": "expand"},
        {"journey": None},
        {"facts": {"state_error": "Unreadable state", "recovery": "Restore access", "rows": {}}},
        {"facts": {"state_error": "Unreadable state", "recovery": "Restore access", "question": "Continue?"}},
        {"facts": {}},
    ],
)
def test_audit_entry_failure_rejects_readiness_state_and_unrelated_facts(onboarding, patch):
    _, renderer, _, _, _ = onboarding
    request = {
        "stage": "audit_entry_failure",
        "journey": "audit",
        "facts": {"state_error": "Unreadable state", "recovery": "Restore read access to journey-state.json"},
    }
    request.update(patch)
    before = deepcopy(request)
    with pytest.raises(ValueError):
        renderer.render(request)
    assert request == before


@pytest.mark.parametrize("field", ["state_error", "recovery"])
@pytest.mark.parametrize("value", [None, "", "  ", [], "x" * 2001])
def test_audit_entry_failure_requires_bounded_nonempty_error_and_recovery(onboarding, field, value):
    _, renderer, _, _, _ = onboarding
    request = {
        "stage": "audit_entry_failure",
        "journey": "audit",
        "facts": {"state_error": "Unreadable state", "recovery": "Restore access to the saved state file"},
    }
    request["facts"][field] = value
    with pytest.raises(ValueError, match=field):
        renderer.render(request)
    request["facts"][field] = "x" * 2000
    assert "x" * 2000 in renderer.render(request)
    del request["facts"][field]
    with pytest.raises(ValueError, match=field):
        renderer.render(request)


def test_audit_entry_failure_cli_stdin_matches_rendering_and_is_not_a_preview(onboarding, tmp_path):
    _, renderer, _, _, _ = onboarding
    request = {
        "stage": "audit_entry_failure",
        "journey": "audit",
        "facts": {
            "state_error": "Foreign saved state",
            "recovery": "Confirm the repository move before using rebind with the reported path and revision",
        },
    }
    script = str(Path(renderer.__file__))
    expected = renderer.render(request)
    result = subprocess.run(
        [sys.executable, script, "--input", "-"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0 and result.stderr == "" and result.stdout == expected
    assert "audit_entry_failure" not in renderer.PREVIEWS
    preview = subprocess.run(
        [sys.executable, script, "--stage", "audit_entry_failure", "--journey", "audit"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert preview.returncode == 2 and preview.stdout == ""
    assert "invalid choice" in preview.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("ready", [None, False])
def test_prerequisite_failure_still_requires_the_actual_readiness_reply(onboarding, ready):
    _, renderer, _, _, _ = onboarding
    request = {"stage": "prerequisite_failure", "journey": "audit", "facts": {"state_error": "Unreadable state"}}
    if ready is not None:
        request["ready"] = ready
    with pytest.raises(ValueError, match="readiness reply"):
        renderer.render(request)
