# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the existing expansion entry and task-planning checkpoints."""

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import literal, mapping

from . import expansion_templates as copy
from . import onboarding

JOURNEYS = ("expand",)
STAGES = {"expansion_audit_opening", "expansion_continue", "expansion_scope"}
PREVIEWS: set[str] = set()


def _audit_opening(state: dict, facts: dict) -> str:
    scope = state["scopes"].get("expand")
    handoff = scope.get("audit_handoff") if isinstance(scope, dict) else None
    if handoff is None:
        raise ValueError("Retain the selected audit improvements before rendering their opening")
    selected = handoff["recommendation_ids"]
    labels = mapping(facts.get("selected_improvements"), "selected improvements", set(selected))
    if set(labels) != set(selected):
        raise ValueError("Supply labels for exactly the retained audit improvement selection")
    count = "one" if len(selected) == 1 else "two" if len(selected) == 2 else "many"
    parts = [
        copy.AUDIT_SELECTION_ACKNOWLEDGMENTS[count],
        copy.AUDIT_SELECTIONS.format(
            value="; ".join(literal(labels[key], "selected improvement label") for key in selected)
        ),
        copy.AUDIT_NEXT_STEPS,
    ]
    if "limits" in facts:
        parts.append(copy.AUDIT_LIMITS.format(value=literal(facts["limits"], "known support limits")))
    return "\n\n".join(parts) + "\n"


def _scope(state: dict, progress: dict) -> str:
    value = expansion_state.record(state)
    if value is None or value["schema_version"] != 2:
        raise ValueError("Save the existing authoring workflow's planning checkpoint first")
    if not progress["context_current"] or state["scopes"]["expand"].get("needs_review"):
        raise ValueError("Expansion context changed; reconcile it through the selected authoring workflow")
    if not expansion_state.checkpoint_ready(state):
        raise ValueError("Continue the saved prerequisite or intent question through shared onboarding")
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Continue the current question through its owning authoring workflow")
    plan = value["plan"]
    parts = (
        [copy.SCOPE_HEADING]
        if value["path"] == "recorded_interaction"
        else [shared.NEXT_STEPS, onboarding.checklist("expand", state), copy.SCOPE_HEADING]
    )
    parts.append(copy.SCOPE_FIELDS["summary"].format(value=literal(plan["summary"], "scope summary")))
    rows = [copy.SCOPE_TABLE]
    for task in plan["tasks"]:
        cells = (
            literal(task[key], f"scope {key}", table=True) for key in ("task", "scenario", "outcome", "verification")
        )
        rows.append("| " + " | ".join(cells) + " |")
    parts.append("\n".join(rows) if plan["tasks"] else copy.SCOPE_EMPTY)
    parts.append(copy.SCOPE_FIELDS["limits"].format(value=literal(plan["limits"], "scope limits")))
    if plan["deferred"]:
        parts.append(
            copy.SCOPE_DEFERRED + "\n\n" + "\n".join("- " + literal(item, "deferred work") for item in plan["deferred"])
        )
    if status := copy.SCOPE_STATUS[progress["stage"]]:
        parts.append(status)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "scope question"))
    return "\n\n".join(parts) + "\n"


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
    if payload["stage"] == "expansion_audit_opening":
        facts = mapping(payload.get("facts", {}), "audit opening facts", {"selected_improvements", "limits"})
        return _audit_opening(state, facts)
    value = expansion_state.record(state)
    if payload["stage"] == "expansion_continue" and value is not None and value["schema_version"] == 2:
        raise ValueError("Use expansion_scope for the saved planning checkpoint")
    facts = mapping(payload.get("facts", {}), "expansion facts", {"expansion_observations"})
    progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if payload["stage"] == "expansion_scope":
        return _scope(state, progress)
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
