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


@pytest.mark.parametrize(
    "change", ["context", "provider_round_trip", "provider_recovery", "later_question", "recovery"]
)
def test_scope_continuation_leaves_other_questions_with_their_owner(modules, tmp_path, change):
    state, _, renderer = modules
    saved = decide(modules, tmp_path, start(modules, tmp_path), "approved")
    if change == "context":
        (tmp_path / "ETHOS.md").write_text("# Changed agent intent\n")
        saved = state.load_state(tmp_path)
        message = "shared onboarding"
    elif change in ("provider_round_trip", "provider_recovery"):
        saved = state.initialize(tmp_path, setup="harbor")
        if change == "provider_round_trip":
            saved = setup_ready(state, tmp_path, journey="expand")
            assert modules[1].derive(saved, modules[1].observe(tmp_path, saved))["context_current"]
            assert saved["scopes"]["expand"]["needs_review"]
            message = "reconcile.*authoring workflow"
        else:
            assert not modules[1].checkpoint_ready(saved)
            message = "shared onboarding"
    else:
        patch = {"pending_question": "Which account should this task use?"}
        if change == "recovery":
            patch.update(
                prerequisites={"runtime": {"status": "missing", "source": "Runtime probe failed"}},
                blockers=[{"id": "runtime", "phase": "setup", "message": "Runtime unavailable"}],
            )
        saved = state.update(tmp_path, patch, expected_revision=saved["revision"])
        message = "shared onboarding" if change == "recovery" else "owning authoring workflow"
    before = state.state_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match=message):
        renderer.render(payload(modules, saved))
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
    assert "First &#124; second \\# Escaped" in text
    assert "\\[click\\](https://example.com) &lt;tag&gt;" in text


@pytest.mark.parametrize(("count", "all_unsupported"), [(1, True), (2, False), (2, True), (3, False)])
def test_audit_selection_opening_precedes_planning_without_changing_saved_decisions(
    modules, tmp_path, count, all_unsupported
):
    state, _, renderer = modules
    from conversation_flows.expansion_templates import UNSUPPORTED_GRADER_REPAIR

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
            "limits": (
                "Neither selected change has a supported authoring path inside Eval Author."
                if all_unsupported and count > 1
                else UNSUPPORTED_GRADER_REPAIR
            ),
            "all_selected_unsupported": all_unsupported,
        },
    }
    before = copy.deepcopy(data)
    saved_bytes = state.state_path(tmp_path).read_bytes()
    text = renderer.render(data)
    if count == 1 or (count == 2 and not all_unsupported):
        fixture = "audit-opening-unsupported.md" if all_unsupported else "audit-opening.md"
        expected = (FIXTURES / fixture).read_text().split("\n\n", 1)[1]
        assert text == expected
    else:
        assert text.startswith(
            "Both of your improvement selections are saved." if count == 2 else "Your improvement selections are saved."
        )
    assert "; ".join(labels[key] for key in selected) in text
    if all_unsupported:
        assert "I’ll check" not in text
        assert "task plan" not in text
        assert "running your agent" not in text
    assert data == before
    assert state.state_path(tmp_path).read_bytes() == saved_bytes
    resumed = state.initialize(tmp_path, journey="expand")
    assert renderer.render({**data, "state": resumed, "revision": resumed["revision"]}) == text
    assert resumed["scopes"] == saved["scopes"]
    assert resumed["tasks"] == saved["tasks"]


@pytest.mark.parametrize(
    "change",
    [
        "no_handoff",
        "missing_selection",
        "extra_selection",
        "revision",
        "unsupported_without_limits",
        "unsupported_not_bool",
        "unsupported_number",
    ],
)
def test_audit_opening_requires_the_retained_selection_and_current_state(modules, tmp_path, change):
    _, _, renderer = modules
    saved = start(modules, tmp_path, "audit_tool_gap")
    labels = {"R1": "New case", "R3": "Repair verifier"}
    facts: dict[str, object] = {"selected_improvements": labels}
    data = {
        "stage": "expansion_audit_opening",
        "state": saved,
        "revision": saved["revision"],
        "facts": facts,
    }
    if change == "no_handoff":
        saved["scopes"]["expand"].pop("audit_handoff")
    elif change == "missing_selection":
        labels.pop("R3")
    elif change == "extra_selection":
        labels["R4"] = "Unselected case"
    elif change.startswith("unsupported_"):
        facts["all_selected_unsupported"] = {
            "unsupported_without_limits": True,
            "unsupported_not_bool": "true",
            "unsupported_number": 1,
        }[change]
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
