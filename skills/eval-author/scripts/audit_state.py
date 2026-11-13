# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Save audit decisions and resume milestones against explicitly selected files."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from first_eval_state import fingerprint

STAGES = ("suite_selection", "trace_selection", "checks_review", "measurement", "findings_review", "next_action")
ACTIONS = {"finish", "improve", "create", "collect_evidence", "fix_agent"}
MODES = {"task_review", "measured"}


def check_kind(kind: object) -> str:
    if not isinstance(kind, str) or kind not in MODES:
        raise ValueError("Choose task_review or measured for the audit approach")
    return kind


def validate_mode(value: dict) -> None:
    _fields(value, "kind source")
    check_kind(value["kind"])
    _text(value["source"])


def mode(state: dict) -> str:
    scope = state.get("scopes", {}).get("audit", {})
    decision = scope.get("mode") if isinstance(scope, dict) else None
    if decision is None:
        # Audits saved before the approach choice always required measurement.
        return "measured"
    validate_mode(decision)
    return decision["kind"]


def traces_unused(state: dict) -> bool:
    """True for an audit whose approach never reads or requires traces."""
    return state["journey"] == "audit" and mode(state) == "task_review"


def new_record() -> dict:
    selection = {"status": "pending", "paths": [], "candidates": [], "exclusions": [], "source": None}
    return {
        "schema_version": 1,
        "suites": copy.deepcopy(selection),
        "traces": copy.deepcopy(selection),
        "suite_files": [],
        "trace_files": [],
        "selection_manifest": None,
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
        path.is_absolute()
        or value == "."
        or path.as_posix() != value
        or any(part in path.parts for part in ("..", "~"))
        or ":" in path.parts[0]
        or any(char in value for char in ("\\", "\n", "\r"))
    ):
        raise ValueError("Audit evidence requires canonical repository-relative paths")


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


def recommendation_ids(value) -> None:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 1000
        or any(not isinstance(item, str) or not re.fullmatch(r"R[1-9][0-9]*", item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Select unique report recommendation IDs such as R1")


def validate_handoff(value: dict) -> None:
    _fields(value, "recommendation_ids findings_digest report content source")
    recommendation_ids(value["recommendation_ids"])
    _digest(value["findings_digest"])
    _ref(value["report"])
    _ref(value["content"])
    _text(value["source"])


def selected_recommendations(repo: Path, lifecycle: dict) -> list[dict]:
    action = lifecycle["next_action"]
    ids = action.get("recommendation_ids", []) if action else []
    if not ids:
        return []
    findings = lifecycle["findings"]
    if not findings or "content" not in findings:
        raise ValueError("Recommendation selection requires the reviewed structured report")
    ref = findings["content"]
    path = (repo / ref["path"]).resolve()
    if not path.is_relative_to(repo.resolve()):
        raise ValueError("Report content must stay inside the repository")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["digest"]:
        raise ValueError("Report content changed; review before selecting recommendations")
    rows = {row["id"]: row for row in json.loads(raw)["recommendations"]}
    if any(identifier not in rows for identifier in ids):
        raise ValueError("Select recommendation IDs from the reviewed report")
    allowed = {
        "improve": {"new_task", "revise_task", "repair_verifier"},
        "collect_evidence": {"collect_evidence"},
        "fix_agent": {"fix_agent"},
    }.get(action["kind"], set())
    if any(rows[identifier]["action"] not in allowed for identifier in ids):
        raise ValueError("Selected recommendations must match the next action")
    return [rows[identifier] for identifier in ids]


def retain_handoff(previous: dict, updated: dict) -> None:
    lifecycle = record(updated)
    action = lifecycle["next_action"] if lifecycle else None
    if not action or action == (record(previous) or {}).get("next_action"):
        return
    assert lifecycle is not None
    if action["kind"] != "improve" or not action.get("recommendation_ids"):
        # A declined or replacement action must not leave an older selection active.
        scope = updated["scopes"].get("expand", {})
        if isinstance(scope, dict):
            scope.pop("audit_handoff", None)
        return
    scope = updated["scopes"].setdefault("expand", {})
    if not isinstance(scope, dict):
        scope = {"legacy_context": scope}
        updated["scopes"]["expand"] = scope
    scope["audit_handoff"] = copy.deepcopy(
        {key: action[key] for key in ("recommendation_ids", "findings_digest", "source")}
        | {key: lifecycle["findings"][key] for key in ("report", "content")}
    )
    scope.update(confirmed=False, needs_review=True)


def validate(value: dict) -> None:
    try:
        value = {"selection_manifest": None, **value}
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
        for key, field in (("suites", "suite_files"), ("traces", "trace_files")):
            _paths(value[field])
            if value[field] and value[key]["status"] != "selected":
                raise ValueError(f"{field} requires a selected {key} source")
            if any(not _within(path, value[key]["paths"]) for path in value[field]):
                raise ValueError(f"{field} must stay within the selected {key} paths")
        for key in ("specification", "selection_manifest"):
            if value[key] is not None:
                _path(value[key])
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
            _fields(findings, "report source" + (" content inputs" if "content" in findings else ""))
            _ref(findings["report"])
            _text(findings["source"])
            if "content" in findings:
                _ref(findings["content"])
                if not isinstance(findings["inputs"], list):
                    raise ValueError("Structured findings require bound inputs")
                for ref in findings["inputs"]:
                    _fields(ref, "path digest kind")
                    _ref({key: ref[key] for key in ("path", "digest")})
                    if ref["kind"] not in {"file", "task"}:
                        raise ValueError("Findings inputs require file or task bindings")
                _paths([ref["path"] for ref in findings["inputs"]])
        action = value["next_action"]
        if action is not None:
            _fields(
                action,
                "kind findings_digest source" + (" recommendation_ids" if "recommendation_ids" in action else ""),
            )
            if action["kind"] not in ACTIONS:
                raise ValueError("Unknown audit next action")
            _digest(action["findings_digest"])
            _text(action["source"])
            if "recommendation_ids" in action:
                recommendation_ids(action["recommendation_ids"])
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


def _within(path: str, roots: list[str]) -> bool:
    return any(PurePosixPath(path).is_relative_to(PurePosixPath(root)) for root in roots)


def _findings_inputs(state: dict, value: dict) -> list[dict]:
    """Findings input bindings that apply to the selected approach.

    Task review ignores bindings that name a historical run source (a recorded
    trace file or the selection manifest) kept from an earlier measured approach,
    so they are neither read nor required to exist. Matching is by exact path, not
    directory ancestry: every other binding, including verifier control manifests
    and native results under a retained trace directory, stays observed and
    checked for freshness.
    """
    refs = (value["findings"] or {}).get("inputs", [])
    if mode(state) != "task_review":
        return refs
    historical = {*value["trace_files"], value.get("selection_manifest")}
    return [ref for ref in refs if ref["path"] not in historical]


def _sources(state: dict) -> set[str]:
    value = record(state)
    if value is None:
        return set()
    files = {value["specification"]} if value["specification"] else set()
    task_review = mode(state) == "task_review"
    for key, field in (("suites", "suite_files"), ("traces", "trace_files")):
        if task_review and key == "traces":
            continue
        if value[key]["status"] == "selected" and (key == "suites" or value["suites"]["status"] != "pending"):
            files.update(value[field])
    if (
        not task_review
        and value.get("selection_manifest")
        and value["suites"]["status"] != "pending"
        and value["traces"]["status"] == "selected"
    ):
        files.add(value["selection_manifest"])
    if value["findings"]:
        files.add(value["findings"]["report"]["path"])
        if "content" in value["findings"]:
            files.add(value["findings"]["content"]["path"])
            files.update(ref["path"] for ref in _findings_inputs(state, value))
    if (
        not task_review
        and value["measurement"]
        and value["suites"]["status"] != "pending"
        and value["traces"]["status"] == "selected"
    ):
        files.update(ref["path"] for ref in value["measurement"]["evidence"])
    for key, field in (("suites", "suite_files"), ("traces", "trace_files")):
        selection = value[key]
        excluded = [row["path"] for row in selection["exclusions"]]
        bounds = selection["paths"] + selection["candidates"] + value[field]
        files = {
            path
            for path in files
            if not _within(path, excluded)
            and (
                not _within(path, bounds)
                or selection["status"] == "selected"
                and (key == "suites" or value["suites"]["status"] != "pending")
                and _within(path, selection["paths"])
            )
        }
    return files


def observe(repo, state: dict) -> dict:
    from journey_state import _file_digest

    observed = {"files": {}}
    if state["ethos"]["stage"] != "complete":
        return observed
    for name in sorted(_sources(state)):
        path = Path(repo) / name
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError("Audit files must not traverse symlinks")
            bindings = _findings_inputs(state, record(state) or {})
            if any(ref["path"] == name and ref["kind"] == "task" for ref in bindings):
                from eval_author_shared.task_evidence import task_digest

                observed["files"][name] = task_digest(path)
            else:
                observed["files"][name] = _file_digest(path, None)[0]
        except (OSError, ValueError):
            observed["files"][name] = None
    return observed


def observed_files(state: dict, observed: dict) -> dict:
    _fields(observed, "files")
    if not isinstance(observed["files"], dict):
        raise ValueError("Audit observations require file digests")
    for path, digest in observed["files"].items():
        _path(path)
        if digest is not None:
            _digest(digest)
    allowed = _sources(state)
    return {path: digest for path, digest in observed["files"].items() if path in allowed}


def derive(state: dict, observed: dict) -> dict | None:
    value = record(state)
    if value is None:
        return None
    validate(value)
    files = observed_files(state, observed)
    task_review = mode(state) == "task_review"
    source_keys = ("suites",) if task_review else ("suites", "traces")
    sources_ready = all(value[key]["status"] != "pending" for key in source_keys)
    inputs = value["suite_files"] + ([] if task_review else value["trace_files"])
    inputs += [value["specification"]] if value["specification"] else []
    bound_inputs = _findings_inputs(state, value)
    task_refs = [ref for ref in bound_inputs if ref["kind"] == "task"]
    if not task_review and value.get("selection_manifest"):
        inputs.append(value["selection_manifest"])
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
                "repository": state["repository"],
                "agent": state["agent"],
                "ethos": {key: state["ethos"][key] for key in ("path", "confirmed_digest")},
                "scope": {
                    key: {field: value[key][field] for field in ("status", "paths", "exclusions")}
                    for key in source_keys
                },
                "files": {path: files[path] for path in inputs},
                **({"mode": "task_review"} if task_review else {}),
            }
        )
    approval = value["checks_approval"]
    # Task bindings gate approval without entering the approved digest, so recording findings
    # after approval and measurement does not revoke them. A later task change does.
    changed_tasks = [ref["path"] for ref in task_refs if files.get(ref["path"]) != ref["digest"]]
    approved = bool(checks_digest and approval and approval["digest"] == checks_digest and not changed_tasks)
    measurement = None if task_review else value["measurement"]
    current = bool(approved and measurement and measurement["checks_digest"] == checks_digest)
    evidence_current = bool(current and all(files.get(ref["path"]) == ref["digest"] for ref in measurement["evidence"]))
    measured = bool(
        evidence_current
        and measurement is not None
        and measurement["status"] == "complete"
        and value["trace_files"]
        and value["suites"]["status"] == value["traces"]["status"] == "selected"
    )
    current_partial = bool(evidence_current and measurement is not None and measurement["status"] == "partial")
    deferred = bool(current and measurement["status"] == "deferred")
    findings = value["findings"]
    findings_digest = None
    findings_inputs = [findings["content"], *bound_inputs] if findings and "content" in findings else []
    if (
        approved
        and findings
        and files.get(findings["report"]["path"]) == findings["report"]["digest"]
        and all(files.get(ref["path"]) == ref["digest"] for ref in findings_inputs)
    ):
        findings_digest = fingerprint(
            {
                "checks": checks_digest,
                "report": findings["report"],
                "measurement": measurement,
                **({"content": findings["content"], "inputs": bound_inputs} if "content" in findings else {}),
                "evidence": {ref["path"]: files.get(ref["path"]) for ref in (measurement or {}).get("evidence", [])},
            }
        )
    reviewed = bool(
        (task_review or measured or current_partial or deferred)
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
                task_review or value["traces"]["status"] != "pending",
                approved,
                task_review or measured or deferred,
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
    if (
        pending
        and current_partial
        and pending["stage"] == ("next_action" if reviewed else "findings_review")
        and not resolved[pending["stage"]]
        and pending["digest"] == findings_digest
    ):
        stage = pending["stage"]
    applicable = bool(pending and pending["stage"] == stage and checkpoint_digests[stage] == pending["digest"])
    return {
        "mode": mode(state),
        "stage": stage,
        "pending_checkpoint": copy.deepcopy(pending) if applicable else None,
        "checkpoint_digests": checkpoint_digests,
        "checks_digest": checks_digest,
        "checks_approved": approved,
        "changed_task_bindings": changed_tasks,
        "measurement_evidence_current": evidence_current,
        "measurement_complete": measured,
        "findings_digest": findings_digest,
        "findings_reviewed": reviewed,
        "next_action_current": action_current,
        "complete": reviewed and (measured or task_review and value["suites"]["status"] == "selected"),
    }


def progress(repo, state: dict) -> dict | None:
    return derive(state, observe(repo, state)) if record(state) is not None else None


def transition(repo, previous: dict, updated: dict) -> dict | None:
    before, after = record(previous), record(updated)
    if before is not None and after is None:
        raise ValueError("Retain structured audit progress")
    if after is None:
        return
    validate(after)
    if before == after and mode(previous) == mode(updated):
        return
    observed = observe(repo, updated)
    current = derive(updated, observed)
    # Current scope bounds also apply when checking the previously saved approval.
    prior = derive(previous, observed) if before else None
    assert current is not None
    for field in ("checks_approval", "measurement", "findings_review", "next_action", "pending"):
        value = after[field]
        if value is None or value == (before or {}).get(field):
            continue
        if current["changed_task_bindings"] and field in {"measurement", "findings_review", "next_action"}:
            raise ValueError(
                "Tasks recorded in the findings changed after approval: "
                + ", ".join(current["changed_task_bindings"])
                + ". Re-review the checks before recording more audit progress"
            )
        if field == "checks_approval" and not current["checks_approved"]:
            raise ValueError("Approve the current checks after resolving source selections")
        if field == "measurement":
            if mode(updated) == "task_review":
                raise ValueError("Task review leaves run coverage unmeasured")
            if not (prior and prior["checks_approved"] and current["checks_approved"]):
                raise ValueError("Save current check approval before recording audit measurement")
            if value["checks_digest"] != current["checks_digest"]:
                raise ValueError("Bind measurement to the approved audit inputs")
            if value["status"] == "complete" and not current["measurement_complete"]:
                raise ValueError("Completed measurement requires current selected evidence")
        if field == "findings_review" and not current["findings_reviewed"]:
            raise ValueError("Review current findings from complete, partial, or explicitly deferred measurement")
        if field == "next_action" and not current["next_action_current"]:
            raise ValueError("Select a next action explicitly against reviewed current findings")
        if field == "next_action":
            selected_recommendations(repo, after)
        if field == "pending" and not current["pending_checkpoint"]:
            raise ValueError("Pending audit checkpoint must match the current unresolved decision")
    return current
