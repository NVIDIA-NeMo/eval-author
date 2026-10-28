# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Save audit decisions and resume milestones against explicitly selected files."""

from __future__ import annotations

import copy
import re
from pathlib import Path, PurePosixPath

from first_eval_state import fingerprint

STAGES = ("suite_selection", "trace_selection", "checks_review", "measurement", "findings_review", "next_action")
ACTIONS = {"finish", "improve", "create", "collect_evidence", "fix_agent"}


def new_record() -> dict:
    selection = {"status": "pending", "paths": [], "candidates": [], "exclusions": [], "source": None}
    return {
        "schema_version": 1,
        "suites": copy.deepcopy(selection),
        "traces": copy.deepcopy(selection),
        "suite_files": [],
        "trace_files": [],
        "specification": None,
        "checks_approval": None,
        "measurement": None,
        "findings": None,
        "findings_review": None,
        "next_action": None,
        "pending": None,
    }


def record(state: dict) -> dict | None:
    scope = state.get("scopes", {}).get("audit", {})
    return scope.get("lifecycle") if isinstance(scope, dict) else None


def _fields(value, fields: str) -> None:
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(f"Audit record requires exactly: {fields}")


def _text(value) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Audit decisions require nonempty text and provenance")


def _digest(value) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Audit evidence requires a SHA-256 digest")


def _path(value) -> None:
    _text(value)
    path = PurePosixPath(value)
    if (
        path.as_posix() != value
        or any(part in path.parts for part in ("..", "~"))
        or any(char in value for char in ("\\", ":", "\n", "\r"))
    ):
        raise ValueError("Audit evidence requires canonical local paths")


def _paths(value) -> None:
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("Audit files require a bounded list")
    for path in value:
        _path(path)
    if len(set(value)) != len(value):
        raise ValueError("Audit paths must be unique")


def _ref(value) -> None:
    _fields(value, "path digest")
    _path(value["path"])
    _digest(value["digest"])


def _decision(value) -> None:
    _fields(value, "digest source")
    _digest(value["digest"])
    _text(value["source"])


def validate(value: dict) -> None:
    try:
        _fields(value, " ".join(new_record()))
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("Unsupported audit lifecycle version")
        for key in ("suites", "traces"):
            selection = value[key]
            _fields(selection, "status paths candidates exclusions source")
            if selection["status"] not in {"pending", "selected", "none" if key == "suites" else "unavailable"}:
                raise ValueError("Unknown audit selection status")
            _paths(selection["paths"])
            _paths(selection["candidates"])
            if not isinstance(selection["exclusions"], list):
                raise ValueError("Audit exclusions require a list")
            for exclusion in selection["exclusions"]:
                _fields(exclusion, "path reason source")
                _path(exclusion["path"])
                _text(exclusion["reason"])
                _text(exclusion["source"])
            if bool(selection["paths"]) != (selection["status"] == "selected"):
                raise ValueError("Selected audit sources require paths")
            if selection["status"] == "pending":
                if selection["source"] is not None:
                    raise ValueError("A pending selection cannot imply a user decision")
            else:
                _text(selection["source"])
        for key in ("suite_files", "trace_files"):
            _paths(value[key])
        if value["specification"] is not None:
            _path(value["specification"])
        for key in ("checks_approval", "findings_review"):
            if value[key] is not None:
                _decision(value[key])
        measurement = value["measurement"]
        if measurement is not None:
            _fields(measurement, "checks_digest status evidence source")
            _digest(measurement["checks_digest"])
            _text(measurement["source"])
            if measurement["status"] not in {"partial", "complete", "deferred"}:
                raise ValueError("Unknown audit measurement status")
            if not isinstance(measurement["evidence"], list):
                raise ValueError("Measurement evidence requires file references")
            for ref in measurement["evidence"]:
                _ref(ref)
            _paths([ref["path"] for ref in measurement["evidence"]])
            if measurement["status"] == "complete" and not measurement["evidence"]:
                raise ValueError("Completed measurement requires verified artifact references")
        findings = value["findings"]
        if findings is not None:
            _fields(findings, "report source")
            _ref(findings["report"])
            _text(findings["source"])
        action = value["next_action"]
        if action is not None:
            _fields(action, "kind findings_digest source")
            if action["kind"] not in ACTIONS:
                raise ValueError("Unknown audit next action")
            _digest(action["findings_digest"])
            _text(action["source"])
        pending = value["pending"]
        if pending is not None:
            _fields(pending, "stage digest question source")
            if pending["stage"] not in STAGES or pending["stage"] == "measurement":
                raise ValueError("Unknown audit decision checkpoint")
            _digest(pending["digest"])
            _text(pending["question"])
            _text(pending["source"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed structured audit lifecycle") from exc


def _within(path: str, roots: list[str], repo: str) -> bool:
    target = PurePosixPath(repo) / path
    return any(target.is_relative_to(PurePosixPath(repo) / root) for root in roots)


def _sources(state: dict) -> set[str]:
    value = record(state)
    if value is None:
        return set()
    files = {value["specification"]} if value["specification"] else set()
    for key, field in (("suites", "suite_files"), ("traces", "trace_files")):
        if value[key]["status"] == "selected" and (key == "suites" or value["suites"]["status"] != "pending"):
            files.update(value[field])
    if value["findings"]:
        files.add(value["findings"]["report"]["path"])
    if value["measurement"] and value["suites"]["status"] != "pending" and value["traces"]["status"] == "selected":
        files.update(ref["path"] for ref in value["measurement"]["evidence"])
    for key, field in (("suites", "suite_files"), ("traces", "trace_files")):
        selection = value[key]
        excluded = [row["path"] for row in selection["exclusions"]]
        bounds = selection["paths"] + selection["candidates"] + value[field]
        files = {
            path
            for path in files
            if not _within(path, excluded, state["repository"])
            and (
                not _within(path, bounds, state["repository"])
                or selection["status"] == "selected"
                and (key == "suites" or value["suites"]["status"] != "pending")
                and _within(path, selection["paths"], state["repository"])
            )
        }
    return files


def observe(repo, state: dict) -> dict:
    from journey_state import MAX_TASK_BYTES, _file_digest

    observed = {"files": {}}
    remaining = MAX_TASK_BYTES
    for name in sorted(_sources(state)):
        path = Path(repo) / name
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError("Audit files must not traverse symlinks")
            digest, size = _file_digest(path, remaining)
            observed["files"][name] = digest
            remaining -= size
        except (OSError, ValueError):
            observed["files"][name] = None
    return observed


def derive(state: dict, observed: dict) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    _fields(observed, "files")
    if not isinstance(observed["files"], dict):
        raise ValueError("Audit observations require file digests")
    for path, digest in observed["files"].items():
        _path(path)
        if digest is not None:
            _digest(digest)
    allowed = _sources(state)
    files = {path: digest for path, digest in observed["files"].items() if path in allowed}
    sources_ready = all(value[key]["status"] != "pending" for key in ("suites", "traces"))
    inputs = value["suite_files"] + value["trace_files"] + ([value["specification"]] if value["specification"] else [])
    checks_digest = None
    if (
        sources_ready
        and value["specification"]
        and state["ethos"]["stage"] == "complete"
        and state["ethos"]["confirmed_digest"]
        and all(files.get(path) for path in inputs)
        and (value["suites"]["status"] != "selected" or value["suite_files"])
    ):
        checks_digest = fingerprint(
            {
                "agent": state["agent"],
                "ethos": {key: state["ethos"][key] for key in ("path", "confirmed_digest")},
                "scope": {
                    key: {field: value[key][field] for field in ("status", "paths", "exclusions")}
                    for key in ("suites", "traces")
                },
                "files": {path: files[path] for path in inputs},
            }
        )
    approval = value["checks_approval"]
    approved = bool(checks_digest and approval and approval["digest"] == checks_digest)
    measurement = value["measurement"]
    current = bool(approved and measurement and measurement["checks_digest"] == checks_digest)
    evidence_current = bool(current and all(files.get(ref["path"]) == ref["digest"] for ref in measurement["evidence"]))
    measured = bool(
        evidence_current
        and measurement["status"] == "complete"
        and value["trace_files"]
        and value["suites"]["status"] == value["traces"]["status"] == "selected"
    )
    deferred = bool(current and measurement["status"] == "deferred")
    findings = value["findings"]
    findings_digest = None
    if approved and findings and files.get(findings["report"]["path"]) == findings["report"]["digest"]:
        findings_digest = fingerprint(
            {
                "checks": checks_digest,
                "report": findings["report"],
                "measurement": measurement,
                "evidence": {ref["path"]: files.get(ref["path"]) for ref in (measurement or {}).get("evidence", [])},
            }
        )
    reviewed = bool(
        (measured or deferred)
        and findings_digest
        and value["findings_review"]
        and value["findings_review"]["digest"] == findings_digest
    )
    action_current = bool(
        reviewed and value["next_action"] and value["next_action"]["findings_digest"] == findings_digest
    )
    resolved = dict(
        zip(
            STAGES,
            (
                value["suites"]["status"] != "pending",
                value["traces"]["status"] != "pending",
                approved,
                measured or deferred,
                reviewed,
                action_current,
            ),
        )
    )
    checkpoint_digests = {
        "suite_selection": fingerprint(value["suites"]),
        "trace_selection": fingerprint(value["traces"]),
        "checks_review": checks_digest,
        "findings_review": findings_digest,
        "next_action": findings_digest,
    }
    stage = next((name for name in STAGES if not resolved[name]), "complete")
    pending = value["pending"]
    applicable = bool(pending and pending["stage"] == stage and checkpoint_digests[stage] == pending["digest"])
    return {
        "stage": stage,
        "pending_checkpoint": copy.deepcopy(pending) if applicable else None,
        "checkpoint_digests": checkpoint_digests,
        "checks_digest": checks_digest,
        "checks_approved": approved,
        "measurement_complete": measured,
        "findings_digest": findings_digest,
        "findings_reviewed": reviewed,
        "next_action_current": action_current,
        "complete": measured and reviewed,
    }


def progress(repo, state: dict) -> dict | None:
    return derive(state, observe(repo, state)) if record(state) is not None else None


def transition(repo, previous: dict, updated: dict) -> None:
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured audit progress")
    if after is None:
        return
    validate(after)
    observed = observe(repo, updated)
    current = derive(updated, observed)
    # Current scope bounds also apply when checking the previously saved approval.
    prior = derive(previous, observed) if before else None
    assert current is not None
    for field in ("checks_approval", "measurement", "findings_review", "next_action", "pending"):
        value = after[field]
        if value is None or value == (before or {}).get(field):
            continue
        if field == "checks_approval" and not current["checks_approved"]:
            raise ValueError("Approve the current checks after resolving source selections")
        if field == "measurement":
            if not (prior and prior["checks_approved"] and current["checks_approved"]):
                raise ValueError("Save current check approval before recording audit measurement")
            if value["checks_digest"] != current["checks_digest"]:
                raise ValueError("Bind measurement to the approved audit inputs")
            if value["status"] == "complete" and not current["measurement_complete"]:
                raise ValueError("Completed measurement requires current selected evidence")
        if field == "findings_review" and not current["findings_reviewed"]:
            raise ValueError("Review current findings after measurement or explicit deferral")
        if field == "next_action" and not current["next_action_current"]:
            raise ValueError("Select a next action explicitly against reviewed current findings")
        if field == "pending" and not current["pending_checkpoint"]:
            raise ValueError("Pending audit checkpoint must match the current unresolved decision")
