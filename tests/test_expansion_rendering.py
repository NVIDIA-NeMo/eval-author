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
    value["pending"] = {"question": "Which interaction should this task use?", "source": "Missing interaction"}
    saved = save_scope(modules, tmp_path, saved, value)
    text = renderer.render(payload(modules, saved))
    assert "No tasks are proposed yet." in text
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
    assert "First &#124; second<br>\\# Escaped" in text
    assert "\\[click\\](https://example.com) &lt;tag&gt;" in text
