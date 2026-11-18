# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render expansion entry, planning, and creation checkpoints."""

import conversation_templates as shared
import expansion_state
import journey_state
from conversation_formatting import literal, mapping

from . import expansion_templates as copy
from . import onboarding

JOURNEYS = ("expand",)
STAGES = {"expansion_continue", "expansion_scope", "expansion_progress"}
PREVIEWS: set[str] = set()


def _checkpoint(state: dict, progress: dict) -> expansion_state.ScopeRecord:
    value = expansion_state.record(state)
    if value is None or value["schema_version"] != 2:
        raise ValueError("Save the existing authoring workflow's planning checkpoint first")
    if not progress["context_current"] or state["scopes"]["expand"].get("needs_review"):
        raise ValueError("Expansion context changed; reconcile it through the selected authoring workflow")
    if not expansion_state.checkpoint_ready(state):
        raise ValueError("Continue the saved prerequisite or intent question through shared onboarding")
    if state["pending_question"] != progress["pending_question"]:
        raise ValueError("Continue the current question through its owning authoring workflow")
    return value


def _scope(state: dict, progress: dict) -> str:
    value = _checkpoint(state, progress)
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


def _progress(state: dict, progress: dict) -> str:
    value = _checkpoint(state, progress)
    creation = progress.get("creation")
    if progress["stage"] != "creation" or creation is None:
        raise ValueError("Save creation progress for the agreed task plan first")
    rows = [copy.CREATION_TABLE]
    for task in creation["rows"]:
        rows.append(f"| {literal(task['task'], 'task title', table=True)} | {copy.CREATION_STATUS[task['status']]} |")
    parts = [copy.CREATION_HEADING, "\n".join(rows)]
    if creation["detail"] is not None:
        parts.append(literal(creation["detail"], "creation detail"))
    parts.append(copy.SCOPE_FIELDS["limits"].format(value=literal(value["plan"]["limits"], "scope limits")))
    if creation["ready_for_review"]:
        parts.append(copy.CREATION_READY)
    if progress["pending_question"] is not None:
        parts.append(literal(progress["pending_question"], "creation question"))
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
    value = expansion_state.record(state)
    if payload["stage"] == "expansion_continue" and value is not None and value["schema_version"] == 2:
        checkpoint = "expansion_progress" if value.get("creation") is not None else "expansion_scope"
        raise ValueError(f"Use {checkpoint} for the saved expansion checkpoint")
    facts = mapping(payload.get("facts", {}), "expansion facts", {"expansion_observations"})
    progress = expansion_state.derive(state, facts.get("expansion_observations"))
    if progress is None:
        raise ValueError("Save a structured expansion lifecycle before continuation")
    if payload["stage"] == "expansion_progress":
        return _progress(state, progress)
    if payload["stage"] == "expansion_scope":
        if progress["stage"] == "creation":
            raise ValueError("Use expansion_progress for saved creation and validation progress")
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
