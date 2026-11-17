# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Proposal interaction contracts preserve missing evidence and checked progression."""

from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"


@pytest.fixture
def interaction_types(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("task_types")


def test_legacy_plan_does_not_gain_an_inferred_type(interaction_types: ModuleType) -> None:
    plan: dict[str, object] = {"id": "refund", "input": "Return the damaged item."}
    original = deepcopy(plan)
    interaction_types.validate_interaction_plan(plan)
    assert plan == original
    assert interaction_types.test_type_label(plan.get("test_type")) == "Not documented"


@pytest.mark.parametrize(
    ("plan", "label"),
    [
        (
            {
                "test_type": "single_turn",
                "test_type_reason": "All order and return facts are present in the initial request.",
            },
            "Single-turn",
        ),
        (
            {
                "test_type": "scripted_multi_step",
                "test_type_reason": "A fixed correction request tests the order created in the first stage.",
                "state_requirements": "Keep the same cart and conversation between requests.",
                "prerequisite_checks": ["The requested item exists in the cart before requesting its removal."],
            },
            "Scripted multi-step",
        ),
        (
            {
                "test_type": "user_sim",
                "test_type_reason": "The customer must choose between the replacement options actually offered.",
                "state_requirements": "Preserve the current order and the agent's latest offer.",
                "conversation_scenario": "Ask for a replacement, then accept the least expensive offered equivalent.",
            },
            "User-sim",
        ),
    ],
)
def test_supported_plans_keep_their_authored_meaning(
    interaction_types: ModuleType, plan: dict[str, object], label: str
) -> None:
    original = deepcopy(plan)
    interaction_types.validate_interaction_plan(plan)
    assert plan == original
    assert interaction_types.test_type_label(plan["test_type"]) == label


@pytest.mark.parametrize("kind", [None, "multi_turn", "User-sim", "", True, 1, [], {}])
def test_invalid_explicit_type_cannot_be_treated_as_legacy(interaction_types: ModuleType, kind: object) -> None:
    with pytest.raises(ValueError, match="test_type"):
        interaction_types.validate_interaction_plan({"test_type": kind, "test_type_reason": "A proposed interaction."})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test_type_reason", "The next turn depends on the reply."),
        ("state_requirements", "Keep the order."),
        ("prerequisite_checks", ["The order exists."]),
        ("conversation_scenario", "Request a replacement."),
    ],
)
def test_orphaned_interaction_details_need_an_explicit_type(
    interaction_types: ModuleType, field: str, value: object
) -> None:
    with pytest.raises(ValueError, match="require test_type"):
        interaction_types.validate_interaction_plan({field: value})


@pytest.mark.parametrize("reason", [None, "", " \n ", 12, ["adaptive"]])
def test_type_without_a_usable_rationale_is_rejected(interaction_types: ModuleType, reason: object) -> None:
    with pytest.raises(ValueError, match="test_type_reason"):
        interaction_types.validate_interaction_plan({"test_type": "single_turn", "test_type_reason": reason})


@pytest.mark.parametrize("kind", ["scripted_multi_step", "user_sim"])
def test_multi_request_plan_requires_state_continuity(interaction_types: ModuleType, kind: str) -> None:
    with pytest.raises(ValueError, match="state_requirements"):
        interaction_types.validate_interaction_plan(
            {
                "test_type": kind,
                "test_type_reason": "The final request acts on the earlier cart.",
                "prerequisite_checks": ["The cart contains the requested item."],
                "conversation_scenario": "Remove the item that was just added.",
            }
        )


@pytest.mark.parametrize("checks", [None, [], "cart exists", [""], [" \n"], [False], {"cart": "exists"}])
def test_scripted_path_cannot_advance_without_usable_prerequisite_checks(
    interaction_types: ModuleType, checks: object
) -> None:
    with pytest.raises(ValueError, match="prerequisite|prerequisites"):
        interaction_types.validate_interaction_plan(
            {
                "test_type": "scripted_multi_step",
                "test_type_reason": "Reach a known cart state before testing its removal.",
                "state_requirements": "Keep the cart between requests.",
                "prerequisite_checks": checks,
            }
        )


def test_adaptive_plan_must_describe_the_conversation(interaction_types: ModuleType) -> None:
    with pytest.raises(ValueError, match="conversation_scenario"):
        interaction_types.validate_interaction_plan(
            {
                "test_type": "user_sim",
                "test_type_reason": "The customer must respond to the offered options.",
                "state_requirements": "Preserve the order and current offer.",
            }
        )


def test_single_turn_plan_cannot_hide_malformed_optional_details(interaction_types: ModuleType) -> None:
    with pytest.raises(ValueError, match="prerequisite_checks"):
        interaction_types.validate_interaction_plan(
            {
                "test_type": "single_turn",
                "test_type_reason": "All required facts are supplied initially.",
                "prerequisite_checks": "none",
            }
        )
