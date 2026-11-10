# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only measurements of explicitly selected local evidence, not discovery.

Route dependency contract (W01/W05/package panels):
- Keep route.entrypoint, expected_scorer, and dependencies (literal JSON mapping).
- Optionally add dependency_source={"artifact_id": ID, "pointer": JSON_POINTER}.
  The selected artifact must retain the JSON graph at that pointer. Without an
  explicit source, dispatch.dependencies is used when present in entry bytes.
- The caller mapping must exactly equal the retained mapping, including unused
  branches; only the selected scorer closure is validated/traversed. Unsupported
  branch shapes, missing identities, cycles, and duplicate edges on unused
  branches do not poison an otherwise valid selected closure.
- resolve_route always emits bound_dependency (bool) and dependency_source
  (null or {artifact_id, pointer, digest}). bound_dependency is true only after
  source identity, literal mapping equality, and selected closure pass. Consumers
  MUST require route.status == "pass" AND route.bound_dependency is True for
  package preflight/acceptance. A raw caller-only graph keeps legacy statuses and
  diagnostics but is explicitly unbound and cannot establish package acceptance.
- Source binding proves this finite declared JSON graph, not arbitrary Python
  import discovery, execution, or completeness of undeclared runtime behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from eval_author_shared.task_evidence import loads

MAX_SOURCE_BYTES = 1_048_576
MAX_TRACE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 16_777_216
MAX_FILES = 64
MAX_RECORDS = 64
MAX_REFERENCES = 64

DIGEST = re.compile(r"[0-9a-f]{64}")
FIELDS = {"id", "role", "path", "digest", "revision", "access", "selection_origin", "source", "evidence"}
CONFIG_FIELDS = {"agent", "run", "task", "tool_permissions", "provider", "runtime_pins", "environment"}


def text(value):
    return isinstance(value, str) and bool(value.strip())


def _relative_path(value):
    if not text(value) or "\x00" in value:
        raise ValueError("invalid path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != value or value == ".":
        raise ValueError("invalid path")
    return relative


def local_file(root, value):
    root = Path(root).absolute()
    relative = _relative_path(value)
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink path")
    return path


def diagnostic(code, subject, status="fail"):
    return {"code": code, "subject": subject, "status": status}


def finish(records, diagnostics):
    diagnostics = sorted(diagnostics, key=lambda row: (row["subject"], row["code"], row["status"]))
    status = "fail" if any(row["status"] == "fail" for row in diagnostics) else "unknown" if diagnostics else "pass"
    return {"status": status, "records": records, "diagnostics": diagnostics}


def validate_identity_selection(selection):
    """Validate the shared selection envelope; identity and route validate content."""
    if (
        not isinstance(selection, dict)
        or set(selection) != {"artifacts", "route"}
        or not isinstance(selection["artifacts"], list)
        or not isinstance(selection["route"], dict)
    ):
        raise ValueError("Select artifacts and a declared route")


def _read_selected(root, relative, limit=MAX_SOURCE_BYTES):
    return read_selected(root, relative, limit)


def read_selected(root, relative, limit=MAX_SOURCE_BYTES):
    """Read bounded regular bytes under a root resolved once by the caller.

    Keep the resolved path unchanged between reads so replacement symlinks fail.
    Directory descriptors pin the full chain and are rechecked after the read.
    """
    descriptors = []
    try:
        root = Path(root).absolute()
        path = local_file(root, relative)
        absolute_root = root.absolute()
        if ".." in absolute_root.parts:
            return None, "reference_invalid"
        descriptors.append(os.open(absolute_root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        parts = (*absolute_root.parts[1:], *path.relative_to(root).parts)
        for part in parts[:-1]:
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptors[-1]))
        descriptors.append(os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1]))
        before = os.fstat(descriptors[-1])
        if not stat.S_ISREG(before.st_mode):
            return None, "reference_invalid"
        if before.st_size > limit:
            return None, "source_too_large"
        with os.fdopen(os.dup(descriptors[-1]), "rb") as stream:
            data = stream.read(limit + 1)
        after = os.fstat(descriptors[-1])
        if len(data) > limit:
            return None, "source_too_large"
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            return None, "source_changed"
        # Confirm the opened chain still names this source, without following
        # replacements or comparing atime (which the read itself may advance).
        try:
            for index, part in enumerate(parts):
                named = os.stat(part, dir_fd=descriptors[index], follow_symlinks=False)
                opened = os.fstat(descriptors[index + 1])
                if (named.st_dev, named.st_ino, named.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
                    return None, "source_changed"
        except OSError:
            return None, "source_changed"
        return data, None
    except FileNotFoundError:
        return None, "artifact_missing"
    except PermissionError:
        return None, "access_denied"
    except (OSError, ValueError):
        return None, "reference_invalid"
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class SelectedAccess:
    """Keep full-selection access restrictions when a reader selects a subset."""

    def __init__(self, records):
        self.unavailable = {}
        for record in records:
            if not isinstance(record, dict) or record.get("access") not in ("missing", "denied", "unresolved"):
                continue
            try:
                path = _relative_path(record.get("path")).as_posix()
            except ValueError:
                continue
            self.unavailable[path] = min(record["access"], self.unavailable.get(path, record["access"]))

    def reason(self, relative):
        access = self.unavailable.get(relative)
        return "access_" + access if access else None


def artifact_byte_limit(record):
    return MAX_TRACE_BYTES if record.get("role") == "trace" else MAX_SOURCE_BYTES


def read_charge(data, reason, limit):
    if data is not None:
        return len(data)
    return limit if reason in {"source_changed", "source_too_large"} else 0


class SelectedReadError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("selected source unavailable")


class _Hashes:
    def __init__(self, root, hasher, access, records):
        self.root, self.hasher = root, hasher
        self.limits = {}
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                path = record["path"]
                self.limits[path] = min(self.limits.get(path, MAX_TRACE_BYTES), artifact_byte_limit(record))
        self.access = access
        self.files = {}
        self.remaining = MAX_TOTAL_BYTES

    def read(self, relative):
        reason = self.access.reason(relative)
        if reason:
            return None, reason
        if relative in self.files:
            return self.files[relative]
        if len(self.files) >= MAX_FILES:
            return None, "file_limit"
        if self.hasher is None:
            source_limit = self.limits.get(relative, MAX_SOURCE_BYTES)
            limit = min(source_limit, self.remaining)
            data, reason = _read_selected(self.root, relative, limit)
            self.remaining -= read_charge(data, reason, limit)
            if reason == "source_too_large" and limit < source_limit:
                reason = "total_bytes_limit"
            result = (hashlib.sha256(data).hexdigest(), None) if data is not None else (None, reason)
        else:
            try:
                result = self.hasher(local_file(self.root, relative)), None
            except SelectedReadError as exc:
                result = None, exc.reason
            except FileNotFoundError:
                result = None, "artifact_missing"
            except PermissionError:
                result = None, "access_denied"
            except (OSError, ValueError):
                result = None, "reference_invalid"
        self.files[relative] = result
        return result


def identify(root: Path, records: list, *, hasher=None, access=None) -> dict:
    """Hash bounded selected bytes; injected snapshot hashers own byte limits."""
    root = Path(root).resolve()
    diagnostics = []
    valid_records = []
    if not isinstance(records, list) or not records:
        return finish([], [diagnostic("identity.selection_missing", "selection", "unknown")])
    if len(records) > MAX_RECORDS:
        return finish([], [diagnostic("identity.selection_limit", "selection", "unknown")])
    hashes = _Hashes(root, hasher, access or SelectedAccess(records), records)
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
        if len(record["evidence"]) > MAX_REFERENCES:
            diagnostics.append(diagnostic("identity.reference_limit", subject, "unknown"))
            continue
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
        try:
            for ref in refs:
                _relative_path(ref["path"])
        except ValueError:
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
            actual, reason = hashes.read(ref["path"])
            if reason:
                diagnostics.append(
                    diagnostic("identity." + reason, subject, "fail" if reason == "reference_invalid" else "unknown")
                )
            elif ref["digest"] is not None and actual != ref["digest"]:
                diagnostics.append(diagnostic("identity.digest_mismatch", subject))
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


def _route_literal(value):
    validate_json_tree(value)
    return json.dumps(value, sort_keys=True, allow_nan=False)


def validate_json_tree(value):
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 50_000 or depth > 32:
            raise ValueError("dependency structure limit")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("non-JSON dependency key")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("non-JSON dependency value")
    json.dumps(value, allow_nan=False)


def pointer_tokens(value):
    if not isinstance(value, str) or len(value) > 1024 or (value and not value.startswith("/")):
        raise ValueError("invalid JSON pointer")
    if re.search(r"~(?![01])", value) or any(ord(char) < 32 for char in value):
        raise ValueError("invalid JSON pointer")
    return [token.replace("~1", "/").replace("~0", "~") for token in value.split("/")[1:]]


def resolve_pointer(value, pointer):
    for token in pointer_tokens(pointer):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]*", token)
            and len(token) < 10
            and int(token) < len(value)
        ):
            value = value[int(token)]
        else:
            return None, False
    return value, True


def _route_pointer(value, path):
    value, found = resolve_pointer(value, path)
    if not found:
        raise ValueError("missing dependency pointer")
    return value


def resolve_route(root: Path, records: list, route: dict) -> dict:
    root = Path(root).resolve()
    diagnostics = []
    selected = set()
    result = {
        "selected": [],
        "scorer": None,
        "executed_scorer": None,
        "bound_dependency": False,
        "dependency_source": None,
    }
    if (
        not isinstance(route, dict)
        or set(route)
        not in (
            {"entrypoint", "expected_scorer", "dependencies"},
            {"entrypoint", "expected_scorer", "dependencies", "dependency_source"},
        )
        or not text(route["entrypoint"])
        or not text(route["expected_scorer"])
        or not isinstance(route["dependencies"], dict)
    ):
        return {**finish([], [diagnostic("route.invalid", "route")]), **result}
    if not isinstance(records, list):
        return {**finish([], identify(root, records)["diagnostics"]), **result}
    if len(records) > MAX_RECORDS:
        return {**finish([], [diagnostic("identity.selection_limit", "selection", "unknown")]), **result}
    access = SelectedAccess(records)
    by_id = {}
    for row in records:
        if isinstance(row, dict) and text(row.get("id")):
            by_id.setdefault(row["id"], []).append(row)

    def validate(key):
        candidates = by_id[key]
        identity = identify(root, candidates, access=access)
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
        data, reason = _read_selected(root, entry["path"], MAX_SOURCE_BYTES)
        if reason:
            return {**finish([], [diagnostic("route.dispatch_unresolved", entry["id"], "unknown")]), **result}
        if hashlib.sha256(data).hexdigest() != entry["digest"]:
            return {**finish([], [diagnostic("route.dispatch_changed", entry["id"])]), **result}
        dispatch = loads(data)
    except (OSError, ValueError, RecursionError):
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
    source = route.get("dependency_source")
    if "dependency_source" not in route and "dependencies" in dispatch:
        source = {"artifact_id": entry["id"], "pointer": "/dependencies"}
    if source is not None or "dependency_source" in route:
        if (
            not isinstance(source, dict)
            or set(source) != {"artifact_id", "pointer"}
            or not text(source["artifact_id"])
            or not isinstance(source["pointer"], str)
        ):
            diagnostics.append(diagnostic("route.dependency_source_invalid", "route"))
            return {**finish([], diagnostics), **result}
        key = source["artifact_id"]
        if key not in by_id:
            diagnostics.append(diagnostic("route.dependency_source_missing", key, "unknown"))
            return {**finish([], diagnostics), **result}
        if key == entry["id"]:
            source_record, document = entry, dispatch
        else:
            source_record, status = validate(key)
            if status != "pass" or source_record is None:
                return {**finish([], diagnostics), **result}
            data, reason = _read_selected(root, source_record["path"], MAX_SOURCE_BYTES)
            if reason:
                diagnostics.append(diagnostic("route.dependency_source_unresolved", key, "unknown"))
                return {**finish([], diagnostics), **result}
            if hashlib.sha256(data).hexdigest() != source_record["digest"]:
                diagnostics.append(diagnostic("route.dependency_source_changed", key))
                return {**finish([], diagnostics), **result}
            try:
                document = loads(data)
            except (ValueError, RecursionError):
                diagnostics.append(diagnostic("route.dependency_source_invalid", key))
                return {**finish([], diagnostics), **result}
        try:
            graph = _route_pointer(document, source["pointer"])
            if not isinstance(graph, dict):
                raise ValueError("dependency graph must be an object")
            matches = _route_literal(graph) == _route_literal(route["dependencies"])
        except (ValueError, TypeError, RecursionError):
            diagnostics.append(diagnostic("route.dependency_source_invalid", key))
            return {**finish([], diagnostics), **result}
        if not matches:
            diagnostics.append(diagnostic("route.dependency_source_mismatch", key))
            return {**finish([], diagnostics), **result}
        result["dependency_source"] = {**source, "digest": source_record["digest"]}
    pending = [(scorer, False)]
    active = set()
    while pending:
        key, exiting = pending.pop()
        if exiting:
            active.remove(key)
            continue
        if key in active:
            diagnostics.append(diagnostic("route.dependency_cycle", key))
            continue
        if key in selected:
            continue
        if len(selected) >= MAX_RECORDS:
            diagnostics.append(diagnostic("route.dependency_limit", key, "unknown"))
            break
        selected.add(key)
        active.add(key)
        pending.append((key, True))
        if key not in by_id:
            diagnostics.append(diagnostic("route.dependency_missing", key, "unknown"))
            continue
        record, _ = validate(key)
        if key == scorer and record is not None and record["role"] != "scorer":
            diagnostics.append(diagnostic("route.scorer_role_invalid", scorer))
        dependencies = route["dependencies"].get(key)
        if isinstance(dependencies, list) and len(dependencies) > MAX_REFERENCES:
            diagnostics.append(diagnostic("route.dependency_limit", key, "unknown"))
        elif not isinstance(dependencies, list) or not all(text(item) for item in dependencies):
            diagnostics.append(diagnostic("route.dependencies_unresolved", key, "unknown"))
        else:
            if len(set(dependencies)) != len(dependencies):
                diagnostics.append(diagnostic("route.dependency_duplicate", key))
            pending.extend((item, False) for item in reversed(dict.fromkeys(dependencies)))
    result["selected"] = sorted(selected)
    result["bound_dependency"] = result["dependency_source"] is not None and not diagnostics
    return {**finish([], diagnostics), **result}
