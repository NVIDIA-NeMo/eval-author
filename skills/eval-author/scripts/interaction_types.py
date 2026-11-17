# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared proposal interaction types and their planning requirements."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

InteractionType = Literal["single_turn", "scripted_multi_step", "user_sim"]

INTERACTION_TYPE_LABELS = {
    "single_turn": "Single-turn",
    "scripted_multi_step": "Scripted multi-step",
    "user_sim": "User-sim",
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
    if kind in {"scripted_multi_step", "user_sim"}:
        required.append("state_requirements")
    if kind == "user_sim":
        required.append("conversation_scenario")
    for field in ("state_requirements", "conversation_scenario"):
        if field in required or field in task:
            value = task.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must describe the selected interaction")
    if "prerequisite_checks" in task or kind == "scripted_multi_step":
        checks = task.get("prerequisite_checks")
        if not isinstance(checks, list) or any(not isinstance(check, str) or not check.strip() for check in checks):
            raise ValueError("prerequisite_checks must be a list of nonempty checks")
        if kind == "scripted_multi_step" and not checks:
            raise ValueError("Scripted multi-step tasks require checked prerequisites")
