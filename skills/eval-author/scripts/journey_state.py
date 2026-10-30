#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist shared prerequisite evidence; never probe runtimes, infer approval, or write ETHOS.md.

The assistant records observed checks as verified and user answers as confirmed,
each with its source. Runtime availability does not establish that an evaluation
passed, and user confirmation is not runtime evidence. Show is read-only,
including when it discovers that a previously reviewed Ethos has changed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import first_eval_state  # noqa: E402
from conversation_templates import CHECKLIST_LABELS, JOURNEYS  # noqa: E402
from runtime_policy import load as runtime_policy  # noqa: E402
from working_paths import (  # noqa: E402
    atomic_write_json,
    json_bytes,
    repository_root,
    working_lock,
    working_path,
)

STAGES = {"explore", "interview", "draft", "write", "review", "complete"}
COMMON_PREREQUISITES = {
    "code_access",
    "file_writes",
    "command_execution",
    "skills",
    "ethos_skills",
    "python",
}
PREREQUISITES = COMMON_PREREQUISITES | {"runtime", "environment", "services", "data"}
DEFERRED = {"agent_connection", "model_credentials"}
PROVIDER_DEPENDENT = {"runtime", "python", "environment", "services", "data", *DEFERRED}
AGENT_DEPENDENT = {"services", "data", *DEFERRED}
AUDIT_DEPENDENT = PROVIDER_DEPENDENT | {"skills"}
LEGACY_NAMES = ("audit-progress.md", "first-eval.md", "intent-notes.md")
MAX_BYTES = 2 * 1024 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}")
TASK_IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
MAX_TASK_ENTRIES = 10000
MAX_TASK_BYTES = 64 * 1024 * 1024
MAX_TASK_EVIDENCE = 32


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def empty_ethos() -> dict:
    return {
        "path": "ETHOS.md",
        "stage": "explore",
        "draft_confirmed": False,
        "draft_source": None,
        "draft": None,
        "source_findings": [],
        "prior_answers": [],
        "confirmed_digest": None,
        "confirmation_source": None,
        "validation": None,
        "remaining_questions": [],
    }


def new_state(repo: Path) -> dict:
    return {
        "schema_version": 1,
        "revision": 0,
        "repository": str(repo),
        "agent": None,
        "journey": None,
        "setup": None,
        "prerequisites": {},
        "ethos": empty_ethos(),
        "scopes": {},
        "tasks": {},
        "journey_status": {},
        "blockers": [],
        "pending_question": None,
        "next_handoff": None,
        "legacy_imports": [],
        "updated_at": now(),
    }


def _text(value: object, field: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")


def _relative_task_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Task or evidence path must be a nonempty string")
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != value:
        raise ValueError("Task and evidence paths must be canonical repository-relative paths, not the root")
    return path


def _relative_ethos_path(value: object) -> Path:
    path = _relative_task_path(value)
    relative = path.as_posix()
    if path.name != "ETHOS.md" or "\\" in relative or ":" in path.parts[0] or any(ord(char) < 32 for char in relative):
        raise ValueError("Ethos must select a canonical repository-relative ETHOS.md path")
    return path


def _validate_tasks(tasks: object) -> None:
    if not isinstance(tasks, dict):
        raise ValueError("Task lifecycle records must be a mapping")
    for root, record in tasks.items():
        _relative_task_path(root)
        if not isinstance(record, dict) or set(record) != {"status", "source", "task_digest", "evidence", "updated_at"}:
            raise ValueError("Invalid task lifecycle record")
        if record["status"] not in ("draft", "accepted"):
            raise ValueError("Task lifecycle status must be draft or accepted; absent records are unknown")
        for key in ("source", "updated_at"):
            _text(record[key], f"task.{key}")
        if len(record["source"]) > 2000:
            raise ValueError("Task decision provenance exceeds the 2000-character limit")
        if not isinstance(record["task_digest"], str) or not DIGEST.fullmatch(record["task_digest"]):
            raise ValueError("Invalid task content digest")
        evidence = record["evidence"]
        if not isinstance(evidence, list) or len(evidence) > MAX_TASK_EVIDENCE:
            raise ValueError("Task evidence must be a bounded list")
        if record["status"] == "accepted" and not evidence:
            raise ValueError("Accepted tasks require retained workflow evidence")
        paths = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"path", "digest"}:
                raise ValueError("Task evidence requires a path and digest")
            _relative_task_path(item["path"])
            if item["path"] in paths:
                raise ValueError("Duplicate task evidence path")
            paths.add(item["path"])
            if not isinstance(item["digest"], str) or not DIGEST.fullmatch(item["digest"]):
                raise ValueError("Invalid task evidence digest")


def validate(state: dict, *, check_size: bool = True) -> None:
    # Schema validation is also used by the pure conversation renderer; creating
    # a state here would unnecessarily read the clock.
    fields = {
        "schema_version",
        "revision",
        "repository",
        "agent",
        "journey",
        "setup",
        "prerequisites",
        "ethos",
        "scopes",
        "tasks",
        "journey_status",
        "blockers",
        "pending_question",
        "next_handoff",
        "legacy_imports",
        "updated_at",
    }
    # Older v1 records predate task lifecycle tracking; their readiness is unknown.
    if not isinstance(state, dict) or set(state) not in (fields, fields - {"tasks"}):
        raise ValueError("Invalid journey state fields; preserve the file and resolve its schema")
    if type(state["schema_version"]) is not int or state["schema_version"] != 1:
        raise ValueError("Unsupported journey state schema")
    if type(state["revision"]) is not int or state["revision"] < 0:
        raise ValueError("Invalid journey state revision")
    for field in ("repository", "updated_at"):
        _text(state[field], field)
    for field in ("agent", "pending_question", "next_handoff"):
        _text(state[field], field, nullable=True)
    if state["journey"] not in (*JOURNEYS, None) or state["setup"] not in ("gym", "harbor", None):
        raise ValueError("Unknown journey or setup")
    if not isinstance(state["prerequisites"], dict):
        raise ValueError("Prerequisites must be a mapping")
    for key, answer in state["prerequisites"].items():
        if key not in PREREQUISITES | DEFERRED or not isinstance(answer, dict):
            raise ValueError(f"Unknown prerequisite: {key}")
        if set(answer) != {"status", "source"} or answer["status"] not in {
            "verified",
            "confirmed",
            "missing",
            "unknown",
            "not_required",
        }:
            raise ValueError(f"Invalid prerequisite answer: {key}")
        _text(answer["source"], f"{key}.source")
        if answer["status"] == "not_required" and key in COMMON_PREREQUISITES:
            raise ValueError(f"{key} is required for the shared prerequisite flow")
        if answer["status"] == "not_required" and key == "runtime" and state["journey"] != "audit":
            raise ValueError("runtime is required when creating or improving evaluations")
    ethos = state["ethos"]
    if not isinstance(ethos, dict) or set(ethos) != set(empty_ethos()):
        raise ValueError("Invalid Ethos state")
    _relative_ethos_path(ethos["path"])
    if ethos["stage"] not in STAGES:
        raise ValueError("Ethos must use a recognized skill stage")
    if type(ethos["draft_confirmed"]) is not bool:
        raise ValueError("Invalid draft confirmation")
    for field in ("draft_source", "confirmation_source", "validation"):
        _text(ethos[field], f"ethos.{field}", nullable=True)
    if ethos["draft_confirmed"] and not ethos["draft_source"]:
        raise ValueError("Draft confirmation requires its source")
    for field, keys in (
        ("source_findings", {"path", "summary"}),
        ("prior_answers", {"topic", "question", "answer", "source"}),
    ):
        if not isinstance(ethos[field], list):
            raise ValueError(f"ethos.{field} must be a list")
        for item in ethos[field]:
            if not isinstance(item, dict) or set(item) != keys:
                raise ValueError(f"Invalid ethos.{field} record")
            for key in keys:
                _text(item[key], f"ethos.{field}.{key}")
    draft = ethos["draft"]
    if draft is not None:
        if not isinstance(draft, dict) or set(draft) != {"path", "digest"}:
            raise ValueError("Draft must reference its working file and content digest")
        _text(draft["path"], "draft.path")
        relative = Path(draft["path"])
        if not relative.is_relative_to(".eval-author/.working") or ".." in relative.parts:
            raise ValueError("Drafts must be stored under .eval-author/.working")
        if not isinstance(draft["digest"], str) or not DIGEST.fullmatch(draft["digest"]):
            raise ValueError("Invalid draft digest")
    if ethos["draft_confirmed"] and draft is None:
        raise ValueError("Draft confirmation requires the exact recoverable draft")
    if ethos["stage"] == "write" and not ethos["draft_confirmed"]:
        raise ValueError("The write stage requires confirmed draft content")
    if not isinstance(ethos["remaining_questions"], list) or any(
        not isinstance(item, str) or not item.strip() for item in ethos["remaining_questions"]
    ):
        raise ValueError("Ethos questions must be nonempty strings")
    confirmed = ethos["confirmed_digest"]
    if confirmed is not None and (not isinstance(confirmed, str) or not DIGEST.fullmatch(confirmed)):
        raise ValueError("Invalid Ethos content digest")
    if ethos["stage"] == "complete" and confirmed is None:
        raise ValueError("Complete Ethos requires a reviewed content digest")
    if confirmed is not None and ethos["stage"] not in {"complete", "review"}:
        raise ValueError("Historical Ethos confirmation is retained only during review")
    if confirmed and (not ethos["confirmation_source"] or not ethos["validation"]):
        raise ValueError("Ethos confirmation requires review and validation provenance")
    for field in ("scopes", "journey_status"):
        if not isinstance(state[field], dict) or set(state[field]) - set(JOURNEYS):
            raise ValueError(f"Invalid {field}")
    creation_scope = state["scopes"].get("create", {})
    if isinstance(creation_scope, dict) and "first_eval" in creation_scope:
        first_eval_state.validate(creation_scope["first_eval"])
    if any(value not in ("pending", "in_progress", "complete") for value in state["journey_status"].values()):
        raise ValueError("Invalid journey completion status")
    _validate_tasks(state.get("tasks", {}))
    if not isinstance(state["blockers"], list):
        raise ValueError("Blockers must be a list")
    for item in state["blockers"]:
        if not isinstance(item, dict) or set(item) != {"id", "phase", "message"}:
            raise ValueError("Blockers require id, phase, and message")
        for field in ("id", "message"):
            _text(item[field], f"blocker.{field}")
        if item["phase"] not in {"setup", "ethos", *JOURNEYS}:
            raise ValueError("Unknown blocker phase")
    if len({item["id"] for item in state["blockers"]}) != len(state["blockers"]):
        raise ValueError("Duplicate blocker id")
    if not isinstance(state["legacy_imports"], list):
        raise ValueError("Legacy imports must be a list")
    for item in state["legacy_imports"]:
        if not isinstance(item, dict) or set(item) not in (
            {"source", "digest", "status"},
            {"source", "digest", "archive", "status"},
        ):
            raise ValueError("Invalid legacy import")
        if item["status"] not in ("unreviewed", "changed") or not DIGEST.fullmatch(str(item["digest"])):
            raise ValueError("Invalid legacy import status or digest")
        _text(item["source"], "legacy source")
        if "archive" in item:
            _text(item["archive"], "legacy archive")
    encoded = json_bytes(state)
    if check_size and len(encoded) > MAX_BYTES:
        raise ValueError("Journey state is too large; link domain artifacts instead of embedding them")


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Unsafe or oversized state input: {path}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)


def read_update_input(path: Path) -> dict:
    if path != Path("-"):
        return read_json(path)
    payload = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError(f"Oversized state input from stdin; maximum {MAX_BYTES} bytes")
    return json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)


def state_path(repo: Path) -> Path:
    path = repo / ".eval-author" / ".working" / "journey-state.json"
    for parent in (path.parent.parent, path.parent, path):
        if parent.is_symlink():
            raise ValueError(f"State must not traverse a symlink: {parent}")
    return path


def ethos_digest(repo: Path, relative: str = "ETHOS.md") -> str:
    _relative_ethos_path(relative)
    try:
        checksum, size = _file_digest(_task_local_path(repo, relative), MAX_BYTES)
    except (OSError, ValueError) as exc:
        raise ValueError("The selected ETHOS.md must be a bounded regular file without symlinks") from exc
    if not size:
        raise ValueError("The selected ETHOS.md must be nonempty")
    return checksum


def draft_digest(repo: Path, draft: dict) -> str:
    path = repo / draft["path"]
    for parent in (path, *path.parents):
        if parent == repo:
            break
        if parent.is_symlink():
            raise ValueError("Draft must not traverse a symlink")
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("The agreed draft is missing or exceeds the size limit")
    return digest(path.read_bytes())


def invalidate_scopes(state: dict) -> None:
    # Keep selected cases/traces and user priorities available for review. Only
    # their agreement is stale; a change to intent is not a request to forget them.
    for scope in state["scopes"].values():
        if isinstance(scope, dict):
            scope["needs_review"] = True
            scope["confirmed"] = False
    state["journey_status"] = {}


def load_state(repo: Path, *, state_only: bool = False) -> dict:
    state = read_json(state_path(repo))
    validate(state)
    state.setdefault("tasks", {})
    if state["repository"] != str(repo):
        raise ValueError(
            f"Journey state belongs to another repository: {state['repository']!r} (revision {state['revision']}). "
            f"For an intentional relocation to {str(repo)!r}, use rebind --from-repository "
            f"{state['repository']!r} --expected-revision {state['revision']}; this reopens saved approvals."
        )
    if state_only:
        # Prerequisite collection may reuse stored provenance, but must not look
        # up Ethos or its draft before the visible transition into Ethos work.
        return state
    confirmed = state["ethos"]["confirmed_digest"]
    if confirmed:
        try:
            current = ethos_digest(repo, state["ethos"]["path"])
        except (OSError, ValueError):
            current = None
        if confirmed != current:
            state["ethos"].update(
                stage="review",
                draft_confirmed=False,
                draft_source=None,
                confirmed_digest=None,
                confirmation_source=None,
                validation=None,
            )
            invalidate_scopes(state)
            state["next_handoff"] = "ethos-orchestrate"
            state["pending_question"] = "ETHOS.md changed or is unavailable; review its intended behavior again."
    if (
        state["ethos"]["draft_confirmed"]
        and state["ethos"]["stage"] != "complete"
        and state["ethos"]["confirmed_digest"] is None
    ):
        draft = state["ethos"]["draft"]
        try:
            current = draft_digest(repo, draft)
        except (OSError, ValueError):
            current = None
        if current != draft["digest"]:
            state["ethos"].update(stage="draft", draft_confirmed=False, draft_source=None)
            state["next_handoff"] = "ethos-orchestrate"
            state["pending_question"] = "The agreed draft changed or is unavailable; review the draft before writing."
    if (
        state["journey"] != "audit"
        and state["ethos"]["stage"] == "complete"
        and state["journey_status"].get("create") == "complete"
    ):
        creation = first_eval_state.progress(repo, state)
        if creation is not None and not creation["complete"]:
            state["journey_status"]["create"] = "in_progress"
    return state


def _task_local_path(repo: Path, relative: str) -> Path:
    path = repo
    for part in _relative_task_path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Task content and evidence must not traverse symlinks")
    return path


def _file_digest(path: Path, remaining: int) -> tuple[str, int]:
    """Read bounded regular files only; never follow a final-component symlink."""
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Task content or evidence must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > remaining:
            raise ValueError("Task content or evidence is not a bounded regular file")
        payload = stream.read(remaining + 1)
        if len(payload) > remaining:
            raise ValueError("Task content or evidence exceeds the byte limit")
        return digest(payload), len(payload)


def task_digest(repo: Path, task: str) -> str:
    """Hash the selected task tree, including README and modes, without executing it."""
    root = _task_local_path(repo, task)
    if not root.is_dir():
        raise ValueError("Task content must be an existing directory")
    if state_path(repo).is_relative_to(root):
        raise ValueError("Task content must not contain its own journey state")
    entries = []
    remaining = MAX_TASK_BYTES
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                if child.name in TASK_IGNORED:
                    continue
                if len(entries) >= MAX_TASK_ENTRIES:
                    raise ValueError("Task content exceeds the entry limit")
                info = child.stat(follow_symlinks=False)
                path = Path(child.path)
                relative = path.relative_to(root).as_posix()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    checksum = None
                elif stat.S_ISREG(info.st_mode):
                    checksum, size = _file_digest(path, remaining)
                    remaining -= size
                else:
                    raise ValueError("Task content must contain only regular files and directories, no symlinks")
                entries.append([relative, info.st_mode & 0o777, checksum])
    return digest(json.dumps(sorted(entries)).encode())


def record_task(repo: Path, *, task: str, status: str, source: str, evidence: list[str] | None = None) -> dict:
    """Record the calling flow's decision; its existing validation still owns acceptance."""
    references = evidence or []
    _relative_task_path(task)
    _text(source, "task.source")
    if status not in ("draft", "accepted"):
        raise ValueError("Task lifecycle status must be draft or accepted")
    if len(references) > MAX_TASK_EVIDENCE or (status == "accepted" and not references):
        raise ValueError("Accepted tasks require a bounded list of retained workflow evidence")
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo) if state_path(repo).exists() else new_state(repo)
        bound = [
            {"path": path, "digest": _file_digest(_task_local_path(repo, path), MAX_BYTES)[0]} for path in references
        ]
        state["tasks"][task] = {
            "status": status,
            "source": source,
            "task_digest": task_digest(repo, task),
            "evidence": bound,
            "updated_at": now(),
        }
        save(repo, state)
        return state


def task_lifecycle(repo: Path, state: dict, task_path: str) -> dict:
    """Resolve readiness independently of inventory; native manifests inherit their task root."""
    path = _relative_task_path(task_path)
    _validate_tasks(state.get("tasks", {}))
    for candidate in (path, *path.parents):
        root = candidate.as_posix()
        record = state.get("tasks", {}).get(root)
        if record is None:
            continue
        if record["status"] == "draft":
            return {"status": "draft", "reason": record["source"]}
        try:
            if task_digest(repo, root) != record["task_digest"]:
                return {"status": "draft", "reason": "Task content changed since acceptance; review the changes"}
            for item in record["evidence"]:
                current, _ = _file_digest(_task_local_path(repo, item["path"]), MAX_BYTES)
                if current != item["digest"]:
                    return {"status": "draft", "reason": "Acceptance evidence changed; review the evidence"}
        except (OSError, ValueError):
            return {"status": "draft", "reason": "Task content or acceptance evidence is unavailable or unsafe"}
        return {"status": "accepted", "reason": record["source"]}
    return {"status": "unknown", "reason": "No task acceptance decision is recorded"}


def checklist(state: dict, *, state_only: bool = False) -> list[dict]:
    """Project reconciled saved facts; never inspect artifacts from a renderer."""
    blocked = {item["phase"] for item in state["blockers"]}
    journey = state["journey"]
    # Readiness for Ethos differs from readiness for later execution.
    # Audit needs completed-run traces, not an authoring provider or runner.
    required = COMMON_PREREQUISITES | {"data" if journey == "audit" else "runtime"}
    setup_done = (
        bool(state["agent"])
        and (journey == "audit" or bool(state["setup"]))
        and all(state["prerequisites"].get(key, {}).get("status") in ("verified", "confirmed") for key in required)
        and "setup" not in blocked
    )
    ethos_done = not state_only and state["ethos"]["stage"] == "complete" and "ethos" not in blocked
    completed = state["journey_status"].get(journey) == "complete" and journey not in blocked
    rows = [
        {
            "label": CHECKLIST_LABELS[0],
            "complete": setup_done,
            "blocked": "setup" in blocked,
        },
        {
            "label": CHECKLIST_LABELS[1],
            "complete": ethos_done,
            "blocked": "ethos" in blocked,
        },
        {
            "label": JOURNEYS.get(journey, "Choose your evaluation goal"),
            "complete": bool(completed and setup_done and ethos_done),
            "blocked": journey in blocked if journey else False,
        },
    ]
    current = (
        None
        if "setup" in blocked
        else next((i for i, row in enumerate(rows) if not row["complete"] and not row["blocked"]), None)
    )
    for i, row in enumerate(rows):
        row["current"] = i == current
    return rows


def view(state: dict, *, state_only: bool = False) -> dict:
    policy = runtime_policy()
    provider = policy["providers"].get(state["setup"])
    if state["journey"] == "audit":
        requirements = {"runtime": None, **policy["audit_tools"]}
    elif provider:
        requirements = {"runtime": provider["supported"]["authoring"], "python": provider["python"]}
    else:
        requirements = None
    # Retained authoring evidence is not permission to inspect it for an audit.
    authoring_checks = not state_only and state["journey"] != "audit"
    observations = first_eval_state.observe(Path(state["repository"]), state) if authoring_checks else None
    creation = first_eval_state.derive(state, observations) if authoring_checks else None
    if (
        creation is not None
        and not creation["complete"]
        and state["ethos"]["stage"] == "complete"
        and state["journey_status"].get("create") == "complete"
    ):
        state = {**state, "journey_status": {**state["journey_status"], "create": "in_progress"}}
    return {
        "state": state,
        "artifact_checks_deferred": state_only,
        "task_readiness": (
            None
            if not authoring_checks
            else {task: task_lifecycle(Path(state["repository"]), state, task) for task in state.get("tasks", {})}
        ),
        "checklist": checklist(state, state_only=state_only) if state["journey"] else [],
        "runtime_requirements": requirements,
        "confirmation_is_runtime_evidence": False,
        "first_eval_observations": observations,
        "first_eval": creation,
    }


def save(repo: Path, state: dict) -> None:
    proposed: dict = {**copy.deepcopy(state), "revision": state["revision"] + 1, "updated_at": now()}
    creation = proposed.get("scopes", {}).get("create", {})
    first_eval = creation.get("first_eval") if isinstance(creation, dict) else None
    if isinstance(first_eval, dict):
        first_eval.pop("history", None)
        first_eval.pop("history_head", None)
    validate(proposed)
    atomic_write_json(working_path(repo, "journey-state.json"), proposed)
    state.update(proposed)


def initialize(
    repo: Path,
    *,
    agent: str | None = None,
    journey: str | None = None,
    setup: str | None = None,
    state_only: bool = False,
) -> dict:
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo, state_only=state_only) if state_path(repo).exists() else new_state(repo)
        previous_agent = state["agent"]
        previous_journey = state["journey"]
        if (
            journey == "audit"
            and previous_journey in {"create", "expand"}
            and (state["pending_question"] or state["next_handoff"])
        ):
            # Identity and setup changes below may clear the outgoing decision.
            scope = state["scopes"].setdefault(previous_journey, {})
            if not isinstance(scope, dict):
                raise ValueError("Saved authoring scope must be a mapping to retain its audit-entry context")
            scope["audit_entry"] = {
                "agent": previous_agent,
                "revision": state["revision"],
                "pending_question": state["pending_question"],
                "next_handoff": state["next_handoff"],
            }
        agent_changed = agent is not None and previous_agent is not None and agent != previous_agent
        if agent is not None and agent != previous_agent:
            state["agent"] = agent
            state["ethos"].update(
                stage="review" if previous_agent is not None else "explore",
                draft_confirmed=False,
                draft_source=None,
                confirmed_digest=None,
                confirmation_source=None,
                validation=None,
            )
            invalidate_scopes(state)
            for key in AGENT_DEPENDENT & state["prerequisites"].keys():
                state["prerequisites"][key]["status"] = "unknown"
        if setup is not None and setup != state["setup"]:
            if state["setup"] is None:
                # Selecting an authoring provider for the first time does not
                # change existing audit work. Reassess readiness, keeping sources.
                for key in PROVIDER_DEPENDENT & state["prerequisites"].keys():
                    state["prerequisites"][key]["status"] = "unknown"
            else:
                state["prerequisites"] = {
                    k: v for k, v in state["prerequisites"].items() if k not in PROVIDER_DEPENDENT
                }
                invalidate_scopes(state)
            state["setup"] = setup
            state["pending_question"] = None
            state["next_handoff"] = None
        if journey is not None and journey != state["journey"]:
            if state["journey"] is not None and "audit" in (journey, state["journey"]):
                # Audit tooling and authoring tooling have different requirements.
                # Keep evidence for review, but neither exemptions nor version
                # checks from the previous flow establish readiness for this one.
                for key in AUDIT_DEPENDENT & state["prerequisites"].keys():
                    state["prerequisites"][key]["status"] = "unknown"
                # Prerequisite IDs identify blockers whose applicability is now
                # stale. Retain their evidence under the previous flow; unknown
                # legacy IDs need caller review before they can be dismissed.
                for blocker in state["blockers"]:
                    if blocker["phase"] == "setup" and blocker["id"] in AUDIT_DEPENDENT:
                        blocker["phase"] = state["journey"]
            state["journey"] = journey
            state["pending_question"] = None
            state["next_handoff"] = None
        if agent_changed:
            state["pending_question"] = (
                f"Agent changed from {previous_agent!r} to {agent!r}; review which saved context and decisions still apply."
            )
            state["next_handoff"] = "eval-author"
        save(repo, state)
        return state


def rebind(repo: Path, *, previous_repository: str, expected_revision: int) -> dict:
    """Recover relocated current state and reopen affected approvals."""
    with working_lock(working_path(repo, "journey-state.lock")):
        state = read_json(state_path(repo))
        validate(state)
        if state["repository"] == str(repo):
            raise ValueError("Journey state is already bound to this repository; use show or init to resume")
        if previous_repository != state["repository"]:
            raise ValueError("Saved repository differs from --from-repository; use the path reported by show")
        if expected_revision != state["revision"]:
            raise ValueError("State changed; reload before rebinding")
        state.setdefault("tasks", {})
        state["repository"] = str(repo)
        for answer in state["prerequisites"].values():
            answer["status"] = "unknown"
        state["ethos"].update(
            stage="review",
            draft_confirmed=False,
            draft_source=None,
            confirmed_digest=None,
            confirmation_source=None,
            validation=None,
        )
        invalidate_scopes(state)
        for record in state["tasks"].values():
            if record["status"] == "accepted":
                record.update(
                    status="draft",
                    source=f"Repository relocated from {previous_repository!r}; reassess acceptance.",
                    updated_at=now(),
                )
        state["pending_question"] = (
            f"Repository changed from {previous_repository!r} to {str(repo)!r}; "
            "recheck prerequisites and review which saved context and decisions still apply."
        )
        state["next_handoff"] = "eval-author"
        validate(state)
        save(repo, state)
        return state


def update(repo: Path, patch: dict, *, expected_revision: int | None = None, state_only: bool = False) -> dict:
    allowed = {"prerequisites", "ethos", "scopes", "journey_status", "blockers", "pending_question", "next_handoff"}
    if not isinstance(patch, dict) or set(patch) - allowed:
        raise ValueError("Update accepts workflow fields only; use init to select agent, journey, or setup")
    if state_only and (
        set(patch) - {"prerequisites", "ethos", "scopes", "blockers", "pending_question", "next_handoff"}
        or ("ethos" in patch and patch["ethos"] != {"stage": "review"})
    ):
        raise ValueError("State-only updates accept prerequisites, blockers, routing, and ethos.stage=review only")
    if state_only and "scopes" in patch:
        scopes = patch["scopes"]
        if (
            not isinstance(scopes, dict)
            or set(scopes) != {"audit"}
            or not isinstance(scopes["audit"], dict)
            or set(scopes["audit"]) != {"framework", "framework_source"}
        ):
            raise ValueError("State-only updates accept only audit framework and framework_source scope observations")
        for field, value in scopes["audit"].items():
            _text(value, f"scopes.audit.{field}")
    if "ethos" in patch:
        if (
            not isinstance(patch["ethos"], dict)
            or set(patch["ethos"])
            - {
                "stage",
                "path",
                "draft_confirmed",
                "draft_source",
                "draft",
                "source_findings",
                "prior_answers",
                "remaining_questions",
            }
            or patch["ethos"].get("stage") == "complete"
        ):
            raise ValueError("Use confirm-ethos to record approval of the exact saved content")
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo, state_only=state_only)
        previous = copy.deepcopy(state)
        create_patch = patch.get("scopes", {}).get("create") if isinstance(patch.get("scopes"), dict) else None
        if state["journey"] == "audit" and any(
            isinstance(patch.get(field), dict) and "create" in patch[field] for field in ("scopes", "journey_status")
        ):
            raise ValueError("Select an authoring journey before updating creation progress")
        prior_create = state["scopes"].get("create", {})
        if (
            isinstance(patch.get("scopes"), dict)
            and "create" in patch["scopes"]
            and isinstance(prior_create, dict)
            and "first_eval" in prior_create
            and not isinstance(create_patch, dict)
        ):
            raise ValueError("Retain structured creation progress when updating its scope")
        typed_create = isinstance(create_patch, dict) and (
            "first_eval" in create_patch or isinstance(prior_create, dict) and "first_eval" in prior_create
        )
        if typed_create and expected_revision is None:
            raise ValueError("Creation scope updates require --expected-revision from the latest show")
        if expected_revision is not None and expected_revision != state["revision"]:
            raise ValueError("State changed; reload before updating")
        selected_path = patch.get("ethos", {}).get("path", state["ethos"]["path"])
        path_changed = selected_path != state["ethos"]["path"]
        if path_changed and expected_revision is None:
            raise ValueError("Selecting ETHOS.md requires --expected-revision from the latest show")
        if state_only and "scopes" in patch:
            if state["journey"] != "audit":
                raise ValueError("State-only framework observations require the audit journey")
            if not isinstance(state["scopes"].get("audit", {}), dict):
                raise ValueError("Saved audit scope must be a mapping; preserve it for review before updating")
        prior_draft = copy.deepcopy(state["ethos"]["draft"])
        for field, value in patch.items():
            if state_only and field == "scopes":
                # These are prerequisite observations, not a new scope agreement.
                # Preserve every existing trace, selection, approval, and source.
                state["scopes"].setdefault("audit", {}).update(copy.deepcopy(value["audit"]))
            elif field in {"prerequisites", "ethos", "scopes", "journey_status"}:
                if not isinstance(value, dict):
                    raise ValueError(f"{field} must be a mapping")
                changes = copy.deepcopy(value)
                if field == "scopes" and typed_create:
                    changes["create"] = {**copy.deepcopy(prior_create), **changes["create"]}
                state[field].update(changes)
            else:
                state[field] = copy.deepcopy(value)
        proposed = patch.get("ethos", {})
        if path_changed:
            state["ethos"].update(
                stage="review",
                draft=None,
                draft_confirmed=False,
                draft_source=None,
                confirmed_digest=None,
                confirmation_source=None,
                validation=None,
            )
            invalidate_scopes(state)
            if "pending_question" not in patch:
                state["pending_question"] = "Review the selected ETHOS.md and confirm its intended behavior."
            if "next_handoff" not in patch:
                state["next_handoff"] = "ethos-orchestrate"
        revising_intent = proposed.get("stage", "complete") not in {"complete", "review"}
        if state["ethos"]["draft"] != prior_draft or (revising_intent and state["ethos"]["confirmed_digest"]):
            if not (proposed.get("draft_confirmed") is True and proposed.get("draft_source")):
                state["ethos"].update(draft_confirmed=False, draft_source=None)
        # A fresh review pauses confirmation without discarding its historical
        # content binding or completed work. load_state still detects file changes.
        if revising_intent:
            state["ethos"]["confirmed_digest"] = None
            state["ethos"]["confirmation_source"] = None
            state["ethos"]["validation"] = None
            invalidate_scopes(state)
        validate(state, check_size=state_only)
        if not state_only and state["journey"] != "audit":
            first_eval_state.transition(repo, previous, state)
            creation = first_eval_state.progress(repo, state)
            if patch.get("journey_status", {}).get("create") == "complete" and creation and not creation["complete"]:
                raise ValueError("Creation completion requires current selected native runs and their exact review")
        if (
            not state_only
            and state["ethos"]["draft_confirmed"]
            and state["ethos"]["stage"] != "complete"
            and state["ethos"]["confirmed_digest"] is None
        ):
            draft = state["ethos"]["draft"]
            if draft["digest"] != draft_digest(repo, draft):
                raise ValueError("Draft differs from the agreed content; review before writing")
        save(repo, state)
        return state


def confirm_ethos(
    repo: Path,
    *,
    reviewed_digest: str,
    source: str,
    validation: str,
    ethos_path: str = "ETHOS.md",
    expected_revision: int | None = None,
) -> dict:
    _text(source, "review source")
    _text(validation, "validation result")
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo)
        if expected_revision is not None and expected_revision != state["revision"]:
            raise ValueError("State changed; reload before confirming ETHOS.md")
        if not state["agent"]:
            raise ValueError("Select the agent before confirming Ethos")
        if ethos_path != state["ethos"]["path"]:
            raise ValueError("The reviewed ETHOS.md path must match the selected document; supply --ethos")
        if reviewed_digest != ethos_digest(repo, ethos_path):
            raise ValueError("ETHOS.md differs from the reviewed content; review the saved file")
        state["ethos"].update(
            stage="complete", confirmed_digest=reviewed_digest, confirmation_source=source, validation=validation
        )
        save(repo, state)
        return state


def import_legacy(repo: Path) -> dict:
    """Record existing legacy notes for review without copying or versioning their content."""
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo)
        changed = False
        needs_review = False
        for name in LEGACY_NAMES:
            source = repo / ".eval-author" / name
            if not source.exists():
                continue
            if source.is_symlink() or source.stat().st_size > MAX_BYTES:
                raise ValueError(f"Legacy notes must be bounded regular files: {source}")
            payload = source.read_bytes()
            checksum = digest(payload)
            relative = source.relative_to(repo).as_posix()
            matches = [item for item in state["legacy_imports"] if item["source"] == relative]
            content_changed = not matches or matches[-1]["digest"] != checksum
            current = {
                "source": relative,
                "digest": checksum,
                "status": ("changed" if matches else "unreviewed") if content_changed else matches[-1]["status"],
            }
            if matches == [current]:
                continue
            state["legacy_imports"] = [item for item in state["legacy_imports"] if item["source"] != relative]
            state["legacy_imports"].append(current)
            changed = True
            needs_review |= content_changed
        if needs_review:
            phase = state["journey"] or "setup"
            state["blockers"] = [item for item in state["blockers"] if item["id"] != "legacy-review"] + [
                {
                    "id": "legacy-review",
                    "phase": phase,
                    "message": "Review imported notes for reusable decisions and conflicts; old checkboxes do not establish approval.",
                }
            ]
            if state["pending_question"] is None:
                state["pending_question"] = (
                    "Review the imported context and confirm any unresolved or conflicting decisions."
                )
        if changed:
            save(repo, state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--state-only",
        action="store_true",
        help="Prerequisite init/show/update only; retain saved provenance without reading Ethos, drafts, or task evidence",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize or resume; selectively invalidate changed identity/setup")
    init.add_argument("--agent")
    init.add_argument("--journey", choices=JOURNEYS)
    init.add_argument("--setup", choices=("gym", "harbor"))
    commands.add_parser("show", help="Read state and derive checklist without writing")
    relocation = commands.add_parser(
        "rebind", help="Recover relocated state and reopen approvals; preserve prior context"
    )
    relocation.add_argument("--from-repository", required=True, help="Exact saved repository path reported by show")
    relocation.add_argument("--expected-revision", required=True, type=int)
    patch = commands.add_parser("update", help="Apply observed checks and user answers from a JSON mapping")
    patch.add_argument("--input", type=Path, required=True, help="JSON mapping file, or - for bounded UTF-8 stdin")
    patch.add_argument("--expected-revision", type=int)
    confirm = commands.add_parser("confirm-ethos", help="Record explicit review of exact saved bytes")
    confirm.add_argument("--ethos", default="ETHOS.md", help="Exact selected repository-relative ETHOS.md path")
    confirm.add_argument("--expected-revision", type=int)
    confirm.add_argument("--digest", required=True)
    confirm.add_argument("--source", required=True, help="Source of the user's saved-content confirmation")
    confirm.add_argument("--validation", required=True, help="Validation result and provenance from ethos-write")
    task = commands.add_parser("record-task", help="Record the calling flow's task status and evidence")
    task.add_argument("--task", required=True, help="Repository-relative task directory")
    task.add_argument("--status", required=True, choices=("draft", "accepted"))
    task.add_argument("--source", required=True, help="Decision provenance from the existing authoring flow")
    task.add_argument("--evidence", action="append", default=[], help="Repository-relative retained evidence file")
    commands.add_parser("import-legacy", help="Import old notes without assuming their approvals")
    args = parser.parse_args()
    try:
        if args.state_only and args.command not in {"init", "show", "update"}:
            raise ValueError("--state-only supports prerequisite init, show, and update only")
        repo = repository_root(args.repo)
        if args.command == "init":
            state = initialize(
                repo, agent=args.agent, journey=args.journey, setup=args.setup, state_only=args.state_only
            )
        elif args.command == "rebind":
            state = rebind(repo, previous_repository=args.from_repository, expected_revision=args.expected_revision)
        elif args.command == "update":
            state = update(
                repo,
                read_update_input(args.input),
                expected_revision=args.expected_revision,
                state_only=args.state_only,
            )
        elif args.command == "confirm-ethos":
            state = confirm_ethos(
                repo,
                reviewed_digest=args.digest,
                source=args.source,
                validation=args.validation,
                ethos_path=args.ethos,
                expected_revision=args.expected_revision,
            )
        elif args.command == "record-task":
            state = record_task(repo, task=args.task, status=args.status, source=args.source, evidence=args.evidence)
        elif args.command == "import-legacy":
            state = import_legacy(repo)
        else:
            if args.state_only:
                try:
                    state_path(repo).stat()
                except FileNotFoundError:
                    print(json.dumps({"state": None, "persisted": False}))
                    return 0
            state = load_state(repo, state_only=args.state_only)
        print(json.dumps(view(state, state_only=args.state_only), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "persisted": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
