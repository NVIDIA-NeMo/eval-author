# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the unresolved expansion entry decision from supplied current state."""

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import literal, mapping

from . import onboarding

JOURNEYS = ("expand",)
STAGES = {"expansion_continue"}
PREVIEWS: set[str] = set()


def render(payload: dict) -> str:
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("Supply the complete saved expansion state")
    journey_state.validate(state)
    if payload.get("stage") not in STAGES or state["journey"] != "expand":
        raise ValueError("Expansion continuation requires the saved expansion journey")
    if set(payload) - {"stage", "state", "revision", "facts"}:
        raise ValueError("Expansion continuation requires stateful input only")
    if type(payload.get("revision")) is not int or payload["revision"] != state["revision"]:
        raise ValueError("State revision differs; reload before rendering expansion")
    value = expansion_state.record(state)
    if value is not None and value["schema_version"] == 2:
        raise ValueError("Use resume for the saved planning checkpoint")
    facts = mapping(payload.get("facts", {}), "expansion facts", {"expansion_observations"})
    progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if not progress["context_current"]:
        raise ValueError("Expansion context changed; reconcile provider, intent, and handoff before continuation")
    if not expansion_state.entry_ready(state):
        raise ValueError(
            "Resolve shared prerequisites, intent questions, and saved blockers before expansion continuation"
        )
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Render only the saved unresolved expansion question")
    parts = [shared.NEXT_STEPS, onboarding.checklist("expand", state)]
    parts.append(literal(progress["pending_question"], "expansion question"))
    return "\n\n".join(parts) + "\n"
