# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Expansion entry decisions bound to confirmed intent and retained audit context."""

from __future__ import annotations

import hashlib
import json
import re


def record(state: dict) -> dict | None:
    scope = state["scopes"].get("expand", {})
    return scope.get("lifecycle") if isinstance(scope, dict) else None


def handoff_digest(state: dict) -> str | None:
    scope = state["scopes"].get("expand", {})
    handoff = scope.get("audit_handoff") if isinstance(scope, dict) else None
    if handoff is None:
        return None
    return hashlib.sha256(json.dumps(handoff, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != {"schema_version", "ethos_digest", "handoff_digest", "pending"}:
        raise ValueError("Expansion entry requires versioned context bindings and a pending decision")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported expansion lifecycle version")
    for field in ("ethos_digest", "handoff_digest"):
        digest = value[field]
        if digest is None and field == "handoff_digest":
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid expansion {field}")
    pending = value["pending"]
    if not isinstance(pending, dict) or set(pending) != {"question", "source"}:
        raise ValueError("Expansion entry requires the unresolved question and its source")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in pending.values()):
        raise ValueError("Expansion question and source must be bounded nonempty text")


def new_record(state: dict, *, question: str, source: str) -> dict:
    value = {
        "schema_version": 1,
        "ethos_digest": state["ethos"]["confirmed_digest"],
        "handoff_digest": handoff_digest(state),
        "pending": {"question": question, "source": source},
    }
    validate(value)
    return value


def observe(repo, state: dict) -> dict:
    from journey_state import ethos_digest

    digest = None
    if record(state) is not None:
        try:
            digest = ethos_digest(repo, state["ethos"]["path"])
        except (OSError, ValueError):
            pass
    return {"repository": state["repository"], "revision": state["revision"], "ethos_digest": digest}


def derive(state: dict, observed: object) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    if (
        not isinstance(observed, dict)
        or set(observed) != {"repository", "revision", "ethos_digest"}
        or observed["repository"] != state["repository"]
        or type(observed["revision"]) is not int
        or observed["revision"] != state["revision"]
    ):
        raise ValueError("Supply expansion observations from the current repository and revision")
    current = (
        state["ethos"]["stage"] == "complete"
        and value["ethos_digest"] == state["ethos"]["confirmed_digest"] == observed["ethos_digest"]
        and value["handoff_digest"] == handoff_digest(state)
    )
    return {
        "stage": "entry",
        "context_current": current,
        "pending_question": value["pending"]["question"] if current else None,
        "complete": False,
    }


def entry_ready(state: dict) -> bool:
    from journey_state import active_blockers, checklist

    return (
        state["journey"] == "expand"
        and not active_blockers(state)
        and all(row["complete"] for row in checklist(state)[:2])
    )


def reconcile_question(repo, state: dict, *, answered_ethos_review: bool = False) -> None:
    if (
        record(state) is None
        or (state["pending_question"] is not None and not answered_ethos_review)
        or not entry_ready(state)
    ):
        return
    progress = derive(state, observe(repo, state))
    if progress is not None and progress["context_current"]:
        state["pending_question"] = progress["pending_question"]


def transition(repo, previous: dict, updated: dict) -> None:
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured expansion progress when updating its scope")
    if after is None:
        return
    progress = derive(updated, observe(repo, updated))
    assert progress is not None
    if before != after:
        if not entry_ready(updated):
            raise ValueError("Expansion entry requires its journey, shared prerequisites, and confirmed Ethos")
        if not progress["context_current"]:
            raise ValueError("Bind the expansion question to the current confirmed intent and handoff")
        if updated["pending_question"] != progress["pending_question"]:
            raise ValueError("Save the expansion entry question as the pending question")
    if (
        entry_ready(updated)
        and progress["context_current"]
        and updated["pending_question"] != progress["pending_question"]
    ):
        if not entry_ready(previous) and updated["pending_question"] is None:
            reconcile_question(repo, updated)
        else:
            raise ValueError("Retain the unresolved expansion question until its lifecycle decision changes")
    if updated["journey_status"].get("expand") == "complete":
        raise ValueError("An unresolved expansion entry decision cannot establish completion")
