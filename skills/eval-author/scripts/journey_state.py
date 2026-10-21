#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persist shared prerequisite decisions; never probe runtimes, infer approval, or write ETHOS.md.

The assistant records user answers with provenance. These records are not proof
that a runtime works or an evaluation passed. Show is read-only, including when
it discovers that a previously reviewed Ethos has changed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_policy import load as runtime_policy  # noqa: E402
from working_paths import (  # noqa: E402
    atomic_write,
    atomic_write_json,
    repository_root,
    working_lock,
    working_path,
)

JOURNEYS = {"create": "Build your eval suite", "audit": "Audit your coverage", "expand": "Improve your eval suite"}
STAGES = {"explore", "interview", "draft", "write", "review", "complete"}
PREREQUISITES = {
    "code_access",
    "file_writes",
    "command_execution",
    "skills",
    "ethos_skills",
    "runtime",
    "python",
    "environment",
    "services",
    "data",
}
DEFERRED = {"agent_connection", "model_credentials"}
PROVIDER_DEPENDENT = {"runtime", "python", "environment", "services", "data", *DEFERRED}
AGENT_DEPENDENT = {"services", "data", *DEFERRED}
LEGACY_NAMES = ("audit-progress.md", "first-eval.md", "intent-notes.md")
MAX_BYTES = 2 * 1024 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}")


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


def validate(state: dict) -> None:
    if not isinstance(state, dict) or set(state) != set(new_state(Path("/"))):
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
            "confirmed",
            "missing",
            "unknown",
            "not_required",
        }:
            raise ValueError(f"Invalid prerequisite answer: {key}")
        _text(answer["source"], f"{key}.source")
        if answer["status"] == "not_required" and key in {
            "code_access",
            "file_writes",
            "command_execution",
            "skills",
            "ethos_skills",
            "runtime",
            "python",
        }:
            raise ValueError(f"{key} is required for the shared prerequisite flow")
    ethos = state["ethos"]
    if not isinstance(ethos, dict) or set(ethos) != set(empty_ethos()):
        raise ValueError("Invalid Ethos state")
    if ethos["path"] != "ETHOS.md" or ethos["stage"] not in STAGES:
        raise ValueError("Ethos must use the repository-root ETHOS.md and a recognized skill stage")
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
    if (ethos["stage"] == "complete") != (confirmed is not None):
        raise ValueError("Complete Ethos requires a reviewed content digest")
    if confirmed and (not ethos["confirmation_source"] or not ethos["validation"]):
        raise ValueError("Ethos confirmation requires review and validation provenance")
    for field in ("scopes", "journey_status"):
        if not isinstance(state[field], dict) or set(state[field]) - set(JOURNEYS):
            raise ValueError(f"Invalid {field}")
    if any(value not in ("pending", "in_progress", "complete") for value in state["journey_status"].values()):
        raise ValueError("Invalid journey completion status")
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
        if not isinstance(item, dict) or set(item) != {"source", "digest", "archive", "status"}:
            raise ValueError("Invalid legacy import")
        if item["status"] not in ("unreviewed", "changed") or not DIGEST.fullmatch(str(item["digest"])):
            raise ValueError("Invalid legacy import status or digest")
        _text(item["source"], "legacy source")
        _text(item["archive"], "legacy archive")
    encoded = json.dumps(state, allow_nan=False).encode()
    if len(encoded) > MAX_BYTES:
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


def state_path(repo: Path) -> Path:
    path = repo / ".eval-author" / ".working" / "journey-state.json"
    for parent in (path.parent.parent, path.parent, path):
        if parent.is_symlink():
            raise ValueError(f"State must not traverse a symlink: {parent}")
    return path


def ethos_digest(repo: Path) -> str:
    path = repo / "ETHOS.md"
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("A nonempty repository-root ETHOS.md is required")
    return digest(path.read_bytes())


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


def load_state(repo: Path) -> dict:
    state = read_json(state_path(repo))
    validate(state)
    if state["repository"] != str(repo):
        raise ValueError("Journey state belongs to another repository; do not reuse its approvals")
    confirmed = state["ethos"]["confirmed_digest"]
    if confirmed:
        try:
            current = ethos_digest(repo)
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
    if state["ethos"]["draft_confirmed"] and state["ethos"]["stage"] != "complete":
        draft = state["ethos"]["draft"]
        try:
            current = draft_digest(repo, draft)
        except (OSError, ValueError):
            current = None
        if current != draft["digest"]:
            state["ethos"].update(stage="draft", draft_confirmed=False, draft_source=None)
            state["next_handoff"] = "ethos-orchestrate"
            state["pending_question"] = "The agreed draft changed or is unavailable; review the draft before writing."
    return state


def checklist(state: dict) -> list[dict]:
    blocked = {item["phase"] for item in state["blockers"]}
    setup_done = (
        bool(state["agent"] and state["setup"])
        and all(
            state["prerequisites"].get(key, {}).get("status") in ("confirmed", "not_required") for key in PREREQUISITES
        )
        and "setup" not in blocked
    )
    ethos_done = state["ethos"]["stage"] == "complete" and "ethos" not in blocked
    journey = state["journey"]
    completed = state["journey_status"].get(journey) == "complete" and journey not in blocked
    rows = [
        {"label": "Confirm your agent and evaluation setup", "complete": setup_done, "blocked": "setup" in blocked},
        {
            "label": "Find out what your agent is supposed to do (Ethos)",
            "complete": ethos_done,
            "blocked": "ethos" in blocked,
        },
        {
            "label": JOURNEYS.get(journey, "Choose your evaluation goal"),
            "complete": bool(completed and setup_done and ethos_done),
            "blocked": journey in blocked if journey else False,
        },
    ]
    current = next((i for i, row in enumerate(rows) if not row["complete"] and not row["blocked"]), None)
    for i, row in enumerate(rows):
        row["current"] = i == current
    return rows


def view(state: dict) -> dict:
    policy = runtime_policy()["providers"].get(state["setup"])
    return {
        "state": state,
        "checklist": checklist(state) if state["journey"] else [],
        "runtime_requirements": {"runtime": policy["supported"]["authoring"], "python": policy["python"]}
        if policy
        else None,
        "confirmation_is_runtime_evidence": False,
    }


def save(repo: Path, state: dict) -> None:
    state["revision"] += 1
    state["updated_at"] = now()
    validate(state)
    atomic_write_json(working_path(repo, "journey-state.json"), state)


def initialize(repo: Path, *, agent: str | None = None, journey: str | None = None, setup: str | None = None) -> dict:
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo) if state_path(repo).exists() else new_state(repo)
        if agent is not None and agent != state["agent"]:
            state["agent"] = agent
            state["ethos"] = empty_ethos()
            state["scopes"] = {}
            state["journey_status"] = {}
            state["prerequisites"] = {k: v for k, v in state["prerequisites"].items() if k not in AGENT_DEPENDENT}
            state["blockers"] = [item for item in state["blockers"] if item["phase"] == "setup"]
            state["pending_question"] = None
            state["next_handoff"] = None
        if setup is not None and setup != state["setup"]:
            state["setup"] = setup
            state["prerequisites"] = {k: v for k, v in state["prerequisites"].items() if k not in PROVIDER_DEPENDENT}
            invalidate_scopes(state)
            state["pending_question"] = None
            state["next_handoff"] = None
        if journey is not None and journey != state["journey"]:
            state["journey"] = journey
            state["pending_question"] = None
            state["next_handoff"] = None
        save(repo, state)
        return state


def update(repo: Path, patch: dict, *, expected_revision: int | None = None) -> dict:
    allowed = {"prerequisites", "ethos", "scopes", "journey_status", "blockers", "pending_question", "next_handoff"}
    if not isinstance(patch, dict) or set(patch) - allowed:
        raise ValueError("Update accepts workflow fields only; use init to select agent, journey, or setup")
    if "ethos" in patch:
        if (
            not isinstance(patch["ethos"], dict)
            or set(patch["ethos"])
            - {
                "stage",
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
        state = load_state(repo)
        if expected_revision is not None and expected_revision != state["revision"]:
            raise ValueError("State changed; reload before updating")
        prior_draft = copy.deepcopy(state["ethos"]["draft"])
        for field, value in patch.items():
            if field in {"prerequisites", "ethos", "scopes", "journey_status"}:
                if not isinstance(value, dict):
                    raise ValueError(f"{field} must be a mapping")
                state[field].update(copy.deepcopy(value))
            else:
                state[field] = copy.deepcopy(value)
        if state["ethos"]["draft"] != prior_draft:
            proposed = patch.get("ethos", {})
            if not (proposed.get("draft_confirmed") is True and proposed.get("draft_source")):
                state["ethos"].update(draft_confirmed=False, draft_source=None)
        if "ethos" in patch and patch["ethos"].get("stage", "complete") != "complete":
            state["ethos"]["confirmed_digest"] = None
            state["ethos"]["confirmation_source"] = None
            state["ethos"]["validation"] = None
            invalidate_scopes(state)
        validate(state)
        if state["ethos"]["draft_confirmed"] and state["ethos"]["stage"] != "complete":
            draft = state["ethos"]["draft"]
            if draft["digest"] != draft_digest(repo, draft):
                raise ValueError("Draft differs from the agreed content; review before writing")
        save(repo, state)
        return state


def confirm_ethos(repo: Path, *, reviewed_digest: str, source: str, validation: str) -> dict:
    _text(source, "review source")
    _text(validation, "validation result")
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo)
        if not state["agent"]:
            raise ValueError("Select the agent before confirming Ethos")
        if reviewed_digest != ethos_digest(repo):
            raise ValueError("ETHOS.md differs from the reviewed content; review the saved file")
        state["ethos"].update(
            stage="complete", confirmed_digest=reviewed_digest, confirmation_source=source, validation=validation
        )
        save(repo, state)
        return state


def import_legacy(repo: Path) -> dict:
    """Retain bounded legacy notes as unreviewed evidence; never parse prose as approval."""
    with working_lock(working_path(repo, "journey-state.lock")):
        state = load_state(repo)
        changed = False
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
            if any(item["digest"] == checksum for item in matches):
                continue
            archive = working_path(repo, "legacy", name, f"{checksum}.md")
            atomic_write(archive, payload)
            state["legacy_imports"].append(
                {
                    "source": relative,
                    "digest": checksum,
                    "archive": archive.relative_to(repo).as_posix(),
                    "status": "changed" if matches else "unreviewed",
                }
            )
            changed = True
        if changed:
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
            save(repo, state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize or resume; selectively invalidate changed identity/setup")
    init.add_argument("--agent")
    init.add_argument("--journey", choices=JOURNEYS)
    init.add_argument("--setup", choices=("gym", "harbor"))
    commands.add_parser("show", help="Read state and derive checklist without writing")
    patch = commands.add_parser("update", help="Apply explicit answers from a JSON mapping")
    patch.add_argument("--input", type=Path, required=True)
    patch.add_argument("--expected-revision", type=int)
    confirm = commands.add_parser("confirm-ethos", help="Record explicit review of exact saved bytes")
    confirm.add_argument("--digest", required=True)
    confirm.add_argument("--source", required=True, help="Source of the user's saved-content confirmation")
    confirm.add_argument("--validation", required=True, help="Validation result and provenance from ethos-write")
    commands.add_parser("import-legacy", help="Import old notes without assuming their approvals")
    args = parser.parse_args()
    try:
        repo = repository_root(args.repo)
        if args.command == "init":
            state = initialize(repo, agent=args.agent, journey=args.journey, setup=args.setup)
        elif args.command == "update":
            state = update(repo, read_json(args.input), expected_revision=args.expected_revision)
        elif args.command == "confirm-ethos":
            state = confirm_ethos(repo, reviewed_digest=args.digest, source=args.source, validation=args.validation)
        elif args.command == "import-legacy":
            state = import_legacy(repo)
        else:
            state = load_state(repo)
        print(json.dumps(view(state), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "persisted": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
