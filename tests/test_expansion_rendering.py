# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
from pathlib import Path

import pytest
from journey_state_support import expansion_payload, setup_ready
from test_expansion_scope import modules as modules
from test_expansion_scope import save_scope, start

FIXTURES = Path(__file__).parent / "fixtures/expansion-conversations"


def payload(modules, saved):
    return {**expansion_payload(modules, saved), "stage": "expansion_scope"}


def decide(modules, repo, saved, kind):
    value = copy.deepcopy(modules[1].record(saved))
    value["decision"] = {"kind": kind, "source": f"User reply: {kind}"}
    value["pending"] = (
        {"question": "Which refund scenario instead?", "source": "User requests revision"} if kind == "revise" else None
    )
    return save_scope(modules, repo, saved, value)


@pytest.mark.parametrize("variant", ["pending", "approved", "declined", "revision"])
def test_existing_plan_matches_fixture_and_is_unchanged_after_resume(modules, tmp_path, variant):
    state, _, renderer = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    if variant != "pending":
        saved = decide(modules, tmp_path, saved, "revise" if variant == "revision" else variant)
    expected = (FIXTURES / f"scope-{variant}.md").read_text().split("\n\n", 1)[1]
    data = payload(modules, saved)
    original = copy.deepcopy(data)
    before = state.state_path(tmp_path).read_bytes()
    assert renderer.render(data) == renderer.render(data) == expected
    assert data == original
    assert state.state_path(tmp_path).read_bytes() == before
    resumed = state.initialize(tmp_path, journey="expand")
    assert renderer.render(payload(modules, resumed)) == expected
    assert resumed["pending_question"] == saved["pending_question"]


@pytest.mark.parametrize("change", ["context", "provider_round_trip", "later_question", "recovery"])
def test_scope_continuation_leaves_other_questions_with_their_owner(modules, tmp_path, change):
    state, _, renderer = modules
    saved = decide(modules, tmp_path, start(modules, tmp_path), "approved")
    if change == "context":
        (tmp_path / "ETHOS.md").write_text("# Changed agent intent\n")
        saved = state.load_state(tmp_path)
        message = "reconcile.*authoring workflow"
    elif change == "provider_round_trip":
        state.initialize(tmp_path, setup="harbor")
        saved = setup_ready(state, tmp_path, journey="expand")
        assert modules[1].derive(saved, modules[1].observe(tmp_path, saved))["context_current"]
        assert saved["scopes"]["expand"]["needs_review"]
        message = "reconcile.*authoring workflow"
    else:
        patch = {"pending_question": "Which account should this task use?"}
        if change == "recovery":
            patch.update(
                prerequisites={"runtime": {"status": "missing", "source": "Runtime probe failed"}},
                blockers=[{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
            )
        saved = state.update(tmp_path, patch, expected_revision=saved["revision"])
        message = "shared onboarding"
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match=message):
        renderer.render(payload(modules, saved))
    if change == "later_question":
        output = renderer.render({**expansion_payload(modules, saved), "stage": "resume"})
        assert "Which account should this task use?" in output
        assert "Create this partial-refund task?" not in output
    assert state.state_path(tmp_path).read_bytes() == before


def test_recorded_interaction_and_incomplete_plan_preserve_existing_workflow(modules, tmp_path):
    _, expansion, renderer = modules
    saved = start(modules, tmp_path, "recorded_interaction")
    text = renderer.render(payload(modules, saved))
    assert "ETHOS.md" not in text
    assert "Create this partial-refund task?" in text
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["tasks"] = []
    value["plan"]["deferred"] = []
    value["pending"] = {"question": "Which interaction should this task use?", "source": "Missing interaction"}
    saved = save_scope(modules, tmp_path, saved, value)
    text = renderer.render(payload(modules, saved))
    assert "No tasks are proposed yet." in text
    assert "**Deferred work:**" not in text
    assert text.endswith(value["pending"]["question"] + "\n")


@pytest.mark.parametrize("stage", ["expansion_scope", "expansion_progress", "expansion_review"])
def test_existing_lifecycle_without_observations_requests_current_evidence(modules, tmp_path, stage):
    from test_expansion_review import ready

    saved, _ = ready(modules, tmp_path) if stage == "expansion_review" else (start(modules, tmp_path), None)
    data = {**expansion_payload(modules, saved), "stage": stage}
    data["facts"].pop("expansion_observations")
    before = modules[0].state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="Supply expansion observations from the current repository and revision"):
        modules[2].render(data)
    assert modules[0].state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("blocker_id", ["environment", "services", "data", "agent_connection", "model_credentials"])
def test_later_dependencies_remain_deferred_before_expansion_execution(modules, tmp_path, blocker_id):
    state, _, renderer = modules
    saved = start(modules, tmp_path)
    saved = state.update(
        tmp_path,
        {"blockers": [{"id": blocker_id, "phase": "expand", "message": "Needed for later execution"}]},
        expected_revision=saved["revision"],
    )
    assert not state.downstream_started(saved)
    assert not state.active_blockers(saved)
    assert "Create this partial-refund task?" in renderer.render(payload(modules, saved))


def test_supplied_plan_prose_stays_inside_its_paragraph_or_table_cell(modules, tmp_path):
    _, expansion, renderer = modules
    saved = start(modules, tmp_path)
    value = copy.deepcopy(expansion.record(saved))
    value["plan"]["summary"] = "# Heading\n- [x] approve | <tag>"
    value["plan"]["tasks"][0]["scenario"] = "First | second\n# Escaped"
    value["plan"]["deferred"] = ["[click](https://example.com) <tag>"]
    saved = save_scope(modules, tmp_path, saved, value)
    text = renderer.render(payload(modules, saved))
    assert "**Improvement:** \\# Heading - \\[x\\] approve &#124; &lt;tag&gt;" in text
    assert "First &#124; second<br>\\# Escaped" in text
    assert "\\[click\\](https://example.com) &lt;tag&gt;" in text


@pytest.mark.parametrize("count", [1, 2, 3])
def test_audit_selection_opening_precedes_planning_without_changing_saved_decisions(modules, tmp_path, count):
    state, _, renderer = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    saved["scopes"]["expand"].pop("lifecycle")
    selected = ["R3"] if count == 1 else ["R1", "R3", "R4"][:count]
    saved["scopes"]["expand"]["audit_handoff"]["recommendation_ids"] = selected
    saved["prerequisites"]["runtime"] = {"status": "missing", "source": "Runtime needs recovery"}
    state.save(tmp_path, saved)
    labels = {
        "R1": "Add a linked-issue inspection case",
        "R3": "Repair the existing verifier",
        "R4": "Add a read-only question case",
    }
    data = {
        "stage": "expansion_audit_opening",
        "state": saved,
        "revision": saved["revision"],
        "facts": {
            "selected_improvements": {key: labels[key] for key in reversed(selected)},
            "limits": "Editing this suite’s existing verifier is not yet supported inside Eval Author",
        },
    }
    before = copy.deepcopy(data)
    saved_bytes = state.state_path(tmp_path).read_bytes()
    text = renderer.render(data)
    if count == 2:
        expected = (FIXTURES / "audit-opening.md").read_text().split("\n\n", 1)[1]
        assert text == expected
    else:
        assert text.startswith(
            "Your improvement selection is saved." if count == 1 else "Your improvement selections are saved."
        )
    assert "; ".join(labels[key] for key in selected) in text
    assert data == before
    assert state.state_path(tmp_path).read_bytes() == saved_bytes
    resumed = state.initialize(tmp_path, journey="expand")
    assert renderer.render({**data, "state": resumed, "revision": resumed["revision"]}) == text
    assert resumed["scopes"] == saved["scopes"]
    assert resumed["tasks"] == saved["tasks"]


@pytest.mark.parametrize("change", ["no_handoff", "missing_selection", "extra_selection", "revision"])
def test_audit_opening_requires_the_retained_selection_and_current_state(modules, tmp_path, change):
    _, _, renderer = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    data = {
        "stage": "expansion_audit_opening",
        "state": saved,
        "revision": saved["revision"],
        "facts": {"selected_improvements": {"R1": "New case", "R3": "Repair verifier"}},
    }
    if change == "no_handoff":
        saved["scopes"]["expand"].pop("audit_handoff")
    elif change == "missing_selection":
        data["facts"]["selected_improvements"].pop("R3")
    elif change == "extra_selection":
        data["facts"]["selected_improvements"]["R4"] = "Unselected case"
    else:
        data["revision"] -= 1
    with pytest.raises(ValueError):
        renderer.render(data)


def test_audit_opening_treats_labels_and_known_limits_as_literal_prose(modules, tmp_path):
    _, _, renderer = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    text = renderer.render(
        {
            "stage": "expansion_audit_opening",
            "state": saved,
            "revision": saved["revision"],
            "facts": {
                "selected_improvements": {"R1": "# Approve\n- [x] create", "R3": "<script>repair</script>"},
                "limits": "[link](https://example.com)\n# New heading",
            },
        }
    )
    assert "\\# Approve - \\[x\\] create; &lt;script&gt;repair&lt;/script&gt;" in text
    assert "\\[link\\](https://example.com) \\# New heading" in text
