# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the planned target-runner assessment saved with a task design."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict, cast

from interaction_types import USER_SIM

TargetAction = Literal["reuse", "create", "extend", "handoff"]
Transport = Literal["native", "cli", "python", "http"]
Capability = Literal["single_turn", "conversation"]


class RunnerPlan(TypedDict):
    target_action: TargetAction
    transport: Transport
    capability: Capability
    reason: str
    source: str


CHOICES: dict[str, frozenset[str]] = {
    "target_action": frozenset(TargetAction.__args__),
    "transport": frozenset(Transport.__args__),
    "capability": frozenset(Capability.__args__),
}


def needs_conversation(tasks: list[Mapping[str, object]]) -> bool:
    return any(task.get("interaction_type") == USER_SIM for task in tasks)


def validate_runner_plan(value: object, tasks: list[Mapping[str, object]]) -> RunnerPlan:
    if not isinstance(value, dict) or set(value) != {*CHOICES, "reason", "source"}:
        raise ValueError("Runner planning requires target_action, transport, capability, reason, and source")
    for field, allowed in CHOICES.items():
        if value[field] not in allowed:
            raise ValueError(f"Unknown runner {field}")
    for field in ("reason", "source"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError("Runner planning requires its finding and inspected source")
    if needs_conversation(tasks) and value["capability"] != "conversation":
        raise ValueError("User simulation requires a conversational target runner")
    return cast(RunnerPlan, value)


def runner_plan(plan: Mapping[str, object]) -> RunnerPlan | None:
    tasks = cast(list[Mapping[str, object]], plan["tasks"])
    value = plan.get("runner")
    if value is None:
        if needs_conversation(tasks):
            raise ValueError("Assess the conversational target runner before proposing User-sim tasks")
        return None
    return validate_runner_plan(value, tasks)
