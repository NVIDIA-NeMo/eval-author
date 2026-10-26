# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep prerequisite findings, optional advice, and the next action distinct."""

import importlib
import re
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
