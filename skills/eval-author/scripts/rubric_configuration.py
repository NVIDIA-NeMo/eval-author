# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private-value comparison of explicitly selected configuration source fields.

Selectors are JSON pointers rooted in CONFIG_FIELDS; values are never returned.
Comparison paths are selector paths only: select nested settings explicitly for
nested diagnostics, rather than exporting potentially secret source object keys.
Source pointers and artifact IDs are caller-supplied public labels, not secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

from rubric_identity import CONFIG_FIELDS, SelectedAccess, identify, pointer_tokens, resolve_pointer, validate_json_tree

MAX_SOURCE_BYTES = 1_048_576
MAX_FILES = 64
MAX_FIELDS = 256
UNKNOWN_REASONS = {
    "omitted": {"field_not_selected", "field_not_declared"},
    "inaccessible": {"access_denied", "artifact_missing", "access_unresolved"},
    "provider_hidden": {"provider_not_disclosed"},
    "unobservable": {"not_recorded", "not_observable"},
}
CLAIM_LIMITS = {
    "omitted": "No setting identity or agreement is established for an omitted field.",
    "inaccessible": "The selected source cannot establish this setting or its agreement.",
    "provider_hidden": "Provider internals are not verified; comparisons exclude this setting.",
    "unobservable": "No usable setting observation is retained; agreement is unknown.",
}


def _unknown(state, reason, provenance=None):
    return None, {"state": state, "reason": reason, "provenance": provenance, "claim_limit": CLAIM_LIMITS[state]}


def _pointer(value):
    try:
        return pointer_tokens(value)
    except ValueError:
        raise ValueError("invalid configuration selection") from None


def _reference(ref):
    if not isinstance(ref, dict):
        raise ValueError("invalid configuration selection")
    if set(ref) == {"state", "reason"}:
        state, reason = ref["state"], ref["reason"]
        if not isinstance(state, str) or not isinstance(reason, str) or reason not in UNKNOWN_REASONS.get(state, set()):
            raise ValueError("invalid configuration selection")
    elif set(ref) == {"artifact_id", "pointer"}:
        if not isinstance(ref["artifact_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", ref["artifact_id"]):
            raise ValueError("invalid configuration selection")
        _pointer(ref["pointer"])
    else:
        raise ValueError("invalid configuration selection")


def _selection(selection):
    if not isinstance(selection, dict) or len(selection) > MAX_FIELDS:
        raise ValueError("invalid configuration selection")
    for path, reference in selection.items():
        tokens = _pointer(path)
        if not tokens or tokens[0] not in CONFIG_FIELDS or any(not token for token in tokens):
            raise ValueError("invalid configuration selection")
        _reference(reference)


def _read_file(root, relative):
    from rubric_identity import read_selected

    data, reason = read_selected(root, relative, MAX_SOURCE_BYTES)
    return data, "invalid_source_path" if reason == "reference_invalid" else reason


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _finite_tree(value):
    validate_json_tree(value)


class _Sources:
    def __init__(self, root, records):
        self.root, self.records = root, records
        self.access = SelectedAccess(records)
        self.files = {}
        self.sources = {}

    def read(self, path):
        reason = self.access.reason(path)
        if reason:
            return None, "artifact_missing" if reason == "access_missing" else reason
        if path not in self.files:
            if len(self.files) >= MAX_FILES:
                return None, "file_limit"
            self.files[path] = _read_file(self.root, path)
        return self.files[path]

    def source(self, artifact_id):
        if artifact_id not in self.sources:
            self.sources[artifact_id] = self.load(artifact_id)
        return self.sources[artifact_id]

    def load(self, artifact_id):
        candidates = [row for row in self.records if isinstance(row, dict) and row.get("id") == artifact_id]
        if len(candidates) != 1:
            return None, None, "duplicate_artifact" if candidates else "artifact_not_selected"
        record = candidates[0]
        if isinstance(record.get("evidence"), list) and len(record["evidence"]) > MAX_FILES:
            return None, None, "file_limit"
        # Reuse identity's record contract without its unbounded filesystem reads.
        metadata = dict(record, access="unresolved")
        validation = identify(self.root, [metadata])
        if any(row["status"] == "fail" for row in validation["diagnostics"]):
            return None, None, "invalid_identity"
        access = record.get("access")
        if not isinstance(access, str):
            return None, None, "invalid_identity"
        if access != "available":
            return (
                None,
                None,
                {"denied": "access_denied", "missing": "artifact_missing", "unresolved": "access_unresolved"}.get(
                    access, "invalid_identity"
                ),
            )
        if record["digest"] is None:
            return None, None, "digest_unresolved"
        if not record["evidence"]:
            return None, None, "evidence_missing"
        suffix = Path(record["path"]).suffix
        if suffix not in {".json", ".toml"}:
            return None, None, "unsupported_format"
        data, reason = self.read(record["path"])
        if reason:
            return None, None, reason
        if hashlib.sha256(data).hexdigest() != record["digest"]:
            return None, None, "digest_mismatch"
        for evidence in record["evidence"]:
            content, reason = self.read(evidence["path"])
            if reason:
                return None, None, reason
            if hashlib.sha256(content).hexdigest() != evidence["digest"]:
                return None, None, "evidence_digest_mismatch"
        try:
            value = (
                json.loads(data, object_pairs_hook=_pairs) if suffix == ".json" else tomllib.loads(data.decode("utf-8"))
            )
            _finite_tree(value)
        except (ValueError, TypeError, RecursionError):
            return None, None, "invalid_source"
        return value, {"digest": record["digest"], "format": suffix[1:]}, None


def _resolve(value, pointer):
    return resolve_pointer(value, pointer)


def _incomplete(value):
    if value is None or isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, dict):
        return not value or any(_incomplete(item) for item in value.values())
    if isinstance(value, list):
        return any(_incomplete(item) for item in value)
    return False


def _extract(sources, reference):
    if reference is None:
        return _unknown("omitted", "field_not_selected")
    if "state" in reference:
        return _unknown(reference["state"], reference["reason"])
    value, metadata, reason = sources.source(reference["artifact_id"])
    provenance = dict(reference, **metadata) if metadata else dict(reference, digest=None, format=None)
    if reason:
        return _unknown("inaccessible", reason, provenance)
    value, found = _resolve(value, reference["pointer"])
    if not found:
        return _unknown("omitted", "pointer_missing", provenance)
    if _incomplete(value):
        _, report = _unknown("unobservable", "empty_value", provenance)
        return value, report
    return value, {"state": "sourced", "reason": None, "provenance": provenance, "claim_limit": None}


def _known_difference(left, right):
    if any(v is None or isinstance(v, str) and not v.strip() or isinstance(v, dict) and not v for v in (left, right)):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return any(_known_difference(left.get(key), right.get(key)) for key in left.keys() | right.keys())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) != len(right) or any(_known_difference(a, b) for a, b in zip(left, right))
    return type(left) is not type(right) or left != right


def _equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate_binding(binding):
    if binding is None:
        return
    if not isinstance(binding, dict) or set(binding) != {"run", "task", "configuration_artifact_id", "observations"}:
        raise ValueError("invalid configuration selection")
    for key in ("run", "task"):
        _reference(binding[key])
        if "artifact_id" not in binding[key]:
            raise ValueError("invalid configuration selection")
    _reference({"artifact_id": binding["configuration_artifact_id"], "pointer": ""})
    observations = binding["observations"]
    if not isinstance(observations, dict) or len(observations) > MAX_FILES:
        raise ValueError("invalid configuration selection")
    for artifact_id, pointers in observations.items():
        _reference({"artifact_id": artifact_id, "pointer": ""})
        if not isinstance(pointers, dict) or set(pointers) != {"run", "task", "configuration_digest"}:
            raise ValueError("invalid configuration selection")
        for pointer in pointers.values():
            _pointer(pointer)


def _binding(sources, binding, declaration, observation):
    def unbound(reason):
        return {"status": "unbound", "reason": reason}

    if binding is None:
        return unbound("binding_not_selected")
    if not observation or "artifact_id" not in observation:
        return unbound("observation_binding_missing")
    pointers = binding["observations"].get(observation["artifact_id"])
    if pointers is None:
        return unbound("observation_binding_missing")
    configuration_id = binding["configuration_artifact_id"]
    if not declaration or declaration.get("artifact_id") != configuration_id:
        return unbound("declaration_configuration_mismatch")
    configuration, metadata, reason = sources.source(configuration_id)
    if reason:
        return unbound("binding_source_unavailable")
    ids = {key: binding[key]["artifact_id"] for key in ("run", "task")}
    ids["configuration"] = configuration_id
    provenance = {}
    expected = {"configuration_digest": metadata["digest"]}
    for role, artifact_id in ids.items():
        value, source, reason = sources.source(artifact_id)
        if reason:
            return unbound("binding_source_unavailable")
        record = next(row for row in sources.records if isinstance(row, dict) and row.get("id") == artifact_id)
        if record["role"] != role:
            return unbound("binding_role_invalid")
        provenance[role] = dict(artifact_id=artifact_id, **source)
        if role != "configuration":
            value, found = _resolve(value, binding[role]["pointer"])
            if not found or not isinstance(value, str) or not value.strip():
                return unbound("binding_identity_unobservable")
            expected[role] = value
            provenance[role]["pointer"] = binding[role]["pointer"]
    value, _, reason = sources.source(observation["artifact_id"])
    if reason:
        return unbound("binding_source_unavailable")
    for key, expected_value in expected.items():
        actual, found = _resolve(value, pointers[key])
        if not found or _incomplete(actual):
            return unbound("binding_identity_unobservable")
        if not _equal(actual, expected_value):
            return unbound(key + "_mismatch")
    return {"status": "run_bound", "reason": None, "basis": "selected_record_assertion", **provenance}


def compare_sourced_config(root: Path, records: list, declared: dict, observed: dict, *, binding=None) -> dict:
    """Compare selected fields without treating equality as merit or attestation."""
    _selection(declared)
    _selection(observed)
    _validate_binding(binding)
    if not isinstance(records, list) or len(records) > MAX_FILES:
        raise ValueError("invalid configuration selection")
    paths = declared.keys() | observed.keys()
    for category in CONFIG_FIELDS:
        if not any(path == "/" + category or path.startswith("/" + category + "/") for path in paths):
            paths.add("/" + category)
    if len(paths) > MAX_FIELDS:
        raise ValueError("invalid configuration selection")
    sources = _Sources(Path(root).resolve(), records)
    fields: dict[str, dict] = {}
    for path in sorted(paths):
        left, left_report = _extract(sources, declared.get(path))
        right, right_report = _extract(sources, observed.get(path))
        left_report["basis"] = "declaration"
        right_report["basis"] = "selected_source_not_native_attestation"
        right_report["binding"] = (
            {"status": "unbound", "reason": "field_unsourced"}
            if binding is not None and (left is None or right is None)
            else _binding(sources, binding, declared.get(path), observed.get(path))
        )
        status = (
            "different"
            if _known_difference(left, right)
            else "unknown"
            if _incomplete(left) or _incomplete(right)
            else "equal"
        )
        fields[path] = {"declared": left_report, "observed": right_report, "status": status}
    counts = {
        "fields": len(fields),
        "compared": 0,
        "equal": 0,
        "different": 0,
        "unknown": 0,
        "declared_sourced": 0,
        "observed_sourced": 0,
        "run_bound": 0,
    }
    for field in fields.values():
        counts[field["status"]] += 1
        counts["run_bound"] += (
            field["observed"]["state"] == "sourced" and field["observed"]["binding"]["status"] == "run_bound"
        )
        counts["compared"] += field["status"] != "unknown"
        for side in ("declared", "observed"):
            counts[side + "_sourced"] += field[side]["state"] == "sourced"
    differences = [path for path, field in fields.items() if field["status"] == "different"]
    unknowns = [path for path, field in fields.items() if field["status"] == "unknown"]
    return {
        "status": "different" if differences else "unknown" if unknowns else "equal",
        "fields": fields,
        "counts": counts,
        "differences": differences,
        "unknowns": unknowns,
        "unknown_reasons": {
            path: {
                side: fields[path][side]["reason"]
                for side in ("declared", "observed")
                if fields[path][side]["state"] != "sourced"
            }
            for path in unknowns
        },
        "acceptable": None,
        "score": None,
        "native_attestation": False,
        "native_adapter": {"status": "unsupported", "reason": "selected_config_sources_are_not_native_attestation"},
    }
