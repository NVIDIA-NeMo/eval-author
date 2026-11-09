# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only measurements of explicitly selected local evidence, not discovery."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from eval_author_shared.task_evidence import digest, loads

DIGEST = re.compile(r"[0-9a-f]{64}")
FIELDS = {"id", "role", "path", "digest", "revision", "access", "selection_origin", "source", "evidence"}
CONFIG_FIELDS = {"agent", "run", "task", "tool_permissions", "provider", "runtime_pins", "environment"}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def local_file(root, value):
    if not text(value):
        raise ValueError("invalid path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value or value == ".":
        raise ValueError("invalid path")
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("symlink path")
    return path


def diagnostic(code, subject, status="fail"):
    return {"code": code, "subject": subject, "status": status}


def finish(records, diagnostics):
    diagnostics = sorted(diagnostics, key=lambda row: (row["subject"], row["code"], row["status"]))
    status = "fail" if any(row["status"] == "fail" for row in diagnostics) else "unknown" if diagnostics else "pass"
    return {"status": status, "records": records, "diagnostics": diagnostics}


def identify(root: Path, records: list) -> dict:
    diagnostics = []
    valid_records = []
    if not isinstance(records, list) or not records:
        return finish([], [diagnostic("identity.selection_missing", "selection", "unknown")])
    seen = set()
    for index, record in enumerate(records):
        subject = record.get("id") if isinstance(record, dict) else None
        subject = subject if text(subject) else f"record:{index}"
        if (
            not isinstance(record, dict)
            or set(record) != FIELDS
            or not all(text(record[key]) for key in ("id", "role", "selection_origin", "source"))
            or record["access"] not in ("available", "missing", "denied", "unresolved")
            or not isinstance(record["evidence"], list)
            or record["revision"] is not None
            and not text(record["revision"])
            or record["digest"] is not None
            and (not isinstance(record["digest"], str) or not DIGEST.fullmatch(record["digest"]))
        ):
            diagnostics.append(diagnostic("identity.record_invalid", subject))
            continue
        if subject in seen:
            diagnostics.append(diagnostic("identity.duplicate", subject))
        seen.add(subject)
        refs = [{"path": record["path"], "digest": record["digest"]}, *record["evidence"]]
        if any(
            not isinstance(ref, dict)
            or set(ref) != {"path", "digest"}
            or not text(ref["path"])
            or offset > 0
            and (not isinstance(ref["digest"], str) or not DIGEST.fullmatch(ref["digest"]))
            for offset, ref in enumerate(refs)
        ):
            diagnostics.append(diagnostic("identity.reference_invalid", subject))
            continue
        valid_records.append(copy.deepcopy(record))
        if record["access"] != "available":
            diagnostics.append(diagnostic("identity.access_" + record["access"], subject, "unknown"))
            continue
        if record["digest"] is None:
            diagnostics.append(diagnostic("identity.digest_unresolved", subject, "unknown"))
        if not record["evidence"]:
            diagnostics.append(diagnostic("identity.evidence_missing", subject, "unknown"))
        for ref in refs:
            try:
                path = local_file(root, ref["path"])
                actual = digest(path) if path.exists() else None
                if actual is None:
                    diagnostics.append(diagnostic("identity.artifact_missing", subject, "unknown"))
                elif ref["digest"] is not None and actual != ref["digest"]:
                    diagnostics.append(diagnostic("identity.digest_mismatch", subject))
            except PermissionError:
                diagnostics.append(diagnostic("identity.access_denied", subject, "unknown"))
            except (OSError, ValueError):
                diagnostics.append(diagnostic("identity.reference_invalid", subject))
    # Repeated references are not independent observations.
    diagnostics = [
        dict(zip(("code", "subject", "status"), values)) for values in sorted({tuple(d.values()) for d in diagnostics})
    ]
    return finish(valid_records, diagnostics)


def compare_config(declared: dict, observed: dict) -> dict:
    differences, unknowns = [], []

    def compare(left, right, path):
        if left is None or right is None or any(isinstance(value, str) and not text(value) for value in (left, right)):
            unknowns.append(path)
        elif isinstance(left, dict) and isinstance(right, dict):
            if not left and not right:
                unknowns.append(path)
            for key in sorted(left.keys() | right.keys()):
                token = key.replace("~", "~0").replace("/", "~1")
                compare(left.get(key), right.get(key), path + "/" + token)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                differences.append(path)
            for index in range(min(len(left), len(right))):
                compare(left[index], right[index], path + "/" + str(index))
        elif type(left) is not type(right) or left != right:
            differences.append(path)

    # JSON validation rejects nonfinite values and unsupported objects, rather
    # than accepting Python's bool/int equality or repr-based comparisons.
    json.dumps([declared, observed], allow_nan=False)
    if not isinstance(declared, dict) or not isinstance(observed, dict):
        raise ValueError("configuration must be an object")
    for key in sorted(CONFIG_FIELDS | declared.keys() | observed.keys()):
        compare(declared.get(key), observed.get(key), "/" + key.replace("~", "~0").replace("/", "~1"))
    return {
        "status": "different" if differences else "unknown" if unknowns else "equal",
        "differences": sorted(set(differences)),
        "unknowns": sorted(set(unknowns)),
        "acceptable": None,
    }


def resolve_route(root: Path, records: list, route: dict) -> dict:
    diagnostics = []
    selected = set()
    result = {"selected": [], "scorer": None, "executed_scorer": None}
    if (
        not isinstance(route, dict)
        or set(route) != {"entrypoint", "expected_scorer", "dependencies"}
        or not text(route["entrypoint"])
        or not text(route["expected_scorer"])
        or not isinstance(route["dependencies"], dict)
    ):
        return {**finish([], [diagnostic("route.invalid", "route")]), **result}
    if not isinstance(records, list):
        return {**finish([], identify(root, records)["diagnostics"]), **result}
    by_id = {}
    for row in records:
        if isinstance(row, dict) and text(row.get("id")):
            by_id.setdefault(row["id"], []).append(row)

    def validate(key):
        candidates = by_id[key]
        identity = identify(root, candidates)
        diagnostics.extend(identity["diagnostics"])
        if len(candidates) != 1:
            duplicate = diagnostic("identity.duplicate", key)
            if duplicate not in diagnostics:
                diagnostics.append(duplicate)
            return None, "fail"
        return next(iter(identity["records"]), None), identity["status"]

    if route["entrypoint"] not in by_id:
        return {**finish([], [diagnostic("route.entrypoint_missing", route["entrypoint"], "unknown")]), **result}
    entry, status = validate(route["entrypoint"])
    if status != "pass" or entry is None:
        return {**finish([], diagnostics), **result}
    try:
        data = local_file(root, entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["digest"]:
            return {**finish([], [diagnostic("route.dispatch_changed", entry["id"])]), **result}
        dispatch = loads(data)
    except (OSError, ValueError):
        return {**finish([], [diagnostic("route.dispatch_unresolved", entry["id"], "unknown")]), **result}
    if not isinstance(dispatch, dict) or not text(dispatch.get("selector")):
        return {**finish([], [diagnostic("route.dynamic_unresolved", entry["id"], "unknown")]), **result}
    routes = dispatch.get("routes")
    scorer = routes.get(dispatch["selector"]) if isinstance(routes, dict) else None
    if not isinstance(scorer, str) or not scorer.strip():
        return {**finish([], [diagnostic("route.selection_missing", entry["id"], "unknown")]), **result}
    result["scorer"] = scorer
    if scorer != route["expected_scorer"]:
        diagnostics.append(diagnostic("route.scorer_mismatch", scorer))
    pending = [scorer]
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        if key not in by_id:
            diagnostics.append(diagnostic("route.dependency_missing", key, "unknown"))
            continue
        record, _ = validate(key)
        if key == scorer and record is not None and record["role"] != "scorer":
            diagnostics.append(diagnostic("route.scorer_role_invalid", scorer))
        dependencies = route["dependencies"].get(key)
        if not isinstance(dependencies, list) or not all(text(item) for item in dependencies):
            diagnostics.append(diagnostic("route.dependencies_unresolved", key, "unknown"))
        else:
            pending.extend(dependencies)
    result["selected"] = sorted(selected)
    return {**finish([], diagnostics), **result}
