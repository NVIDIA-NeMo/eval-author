# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared proposal interaction types and their planning requirements."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, TypedDict, cast

USER_SIM_SCENARIO_FILENAME: Final = "user-sim.yaml"

InteractionType = Literal["single_turn", "scripted_multi_step", "user_sim"]

SINGLE_TURN: Final = "single_turn"
SCRIPTED_MULTI_STEP: Final = "scripted_multi_step"
USER_SIM: Final = "user_sim"

INTERACTION_TYPE_LABELS = {
    SINGLE_TURN: "Single Turn",
    SCRIPTED_MULTI_STEP: "Scripted Multi-step",
    USER_SIM: "User Simulation",
}


class InteractionPlan(TypedDict, total=False):
    interaction_type: InteractionType
    state_requirements: str
    prerequisite_checks: list[str]
    conversation_scenario: str


def interaction_type_label(value: object) -> str:
    if value is None:
        return "Not documented"
    if not isinstance(value, str) or value not in INTERACTION_TYPE_LABELS:
        raise ValueError("Unknown task interaction_type")
    return INTERACTION_TYPE_LABELS[value]


def validate_interaction_plan(task: Mapping[str, object]) -> None:
    fields = ("state_requirements", "prerequisite_checks", "conversation_scenario")
    if "interaction_type" not in task:
        if any(field in task for field in fields):
            raise ValueError("Interaction planning fields require interaction_type")
        return
    kind = task["interaction_type"]
    if kind is None:
        raise ValueError("Unknown task interaction_type")
    interaction_type_label(kind)
    required: list[str] = []
    if kind in {SCRIPTED_MULTI_STEP, USER_SIM}:
        required.append("state_requirements")
    if kind == USER_SIM:
        required.append("conversation_scenario")
    for field in ("state_requirements", "conversation_scenario"):
        if field in required or field in task:
            value = task.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must describe the selected interaction")
    if "prerequisite_checks" in task or kind == SCRIPTED_MULTI_STEP:
        checks = task.get("prerequisite_checks")
        if not isinstance(checks, list) or any(not isinstance(check, str) or not check.strip() for check in checks):
            raise ValueError("prerequisite_checks must be a list of nonempty checks")
        if kind == SCRIPTED_MULTI_STEP and not checks:
            raise ValueError("Scripted multi-step tasks require checked prerequisites")


class UserSimulationDecision(TypedDict):
    enabled: bool
    source: str


def validate_user_simulation(value: object) -> UserSimulationDecision:
    if (
        not isinstance(value, dict)
        or set(value) != {"enabled", "source"}
        or type(value["enabled"]) is not bool
        or not isinstance(value["source"], str)
        or not value["source"].strip()
        or len(value["source"]) > 4000
    ):
        raise ValueError("User simulation requires a boolean enabled choice and bounded user source")
    return cast(UserSimulationDecision, value)


def user_simulation_enabled(state: dict, journey: str = "create") -> bool:
    scope = state["scopes"].get(journey, {})
    if not isinstance(scope, dict) or "user_simulation" not in scope:
        return False
    return validate_user_simulation(scope["user_simulation"])["enabled"]
