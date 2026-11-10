# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Measure selected reconstruction declarations without executing task code."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from eval_author_shared.task_evidence import loads
from rubric_configuration import _equal, _finite_tree, _pointer, _resolve
from rubric_identity import (
    MAX_FILES,
    MAX_RECORDS,
    MAX_SOURCE_BYTES,
    MAX_TOTAL_BYTES,
    _read_selected,
    diagnostic,
    finish,
    identify,
    text,
)


class _Issue(Exception):
    def __init__(self, code, status="fail"):
        self.code, self.status = code, status


def _require(condition, code):
    if not condition:
        raise _Issue(code)


def _at(document, pointer, label) -> Any:
    try:
        value, found = _resolve(document, pointer)
    except ValueError:
        raise _Issue(label + "_pointer_invalid") from None
    _require(found, label + "_missing")
    return value


def _relative(value, label):
    _require(text(value), label + "_path_invalid")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute() and ".." not in path.parts and path.as_posix() == value and value != ".",
        label + "_path_invalid",
    )
    return path


class _Sources:
    def __init__(self, root, artifacts):
        self.root = Path(root)
        self.artifacts = artifacts
        self.files = {}
        self.remaining = MAX_TOTAL_BYTES
        self.unavailable = {
            row["path"]
            for row in artifacts
            if isinstance(row, dict) and text(row.get("path")) and row.get("access") != "available"
        }

    def read(self, path, label):
        if path in self.unavailable:
            raise _Issue(label + "_access_unavailable", "unknown")
        if path not in self.files:
            if len(self.files) >= MAX_FILES or self.remaining <= 0:
                raise _Issue("source_limit", "unknown")
            limit = min(MAX_SOURCE_BYTES, self.remaining)
            data, reason = _read_selected(self.root, path, limit)
            self.remaining -= len(data) if data is not None else limit
            self.files[path] = data, reason
        data, reason = self.files[path]
        if reason:
            raise _Issue(label + "_" + reason, "fail" if reason == "reference_invalid" else "unknown")
        return data

    def artifact(self, ident, label):
        selected = [row for row in self.artifacts if isinstance(row, dict) and row.get("id") == ident]
        _require(bool(selected), label + "_not_selected")
        _require(len(selected) == 1, label + "_duplicate")
        record = selected[0]
        validation = identify(self.root, [dict(record, access="unresolved")])
        _require(not any(row["status"] == "fail" for row in validation["diagnostics"]), label + "_identity_invalid")
        if any(row["code"] == "identity.reference_limit" for row in validation["diagnostics"]):
            raise _Issue("source_limit", "unknown")
        _require(record.get("access") in ("available", "denied", "missing", "unresolved"), label + "_identity_invalid")
        if record["access"] != "available":
            raise _Issue(label + "_access_" + record["access"], "unknown")
        if record["digest"] is None:
            raise _Issue(label + "_digest_unresolved", "unknown")
        if not record["evidence"]:
            raise _Issue(label + "_evidence_missing", "unknown")
        data = self.read(record["path"], label)
        _require(hashlib.sha256(data).hexdigest() == record["digest"], label + "_digest_mismatch")
        for reference in record["evidence"]:
            evidence = self.read(reference["path"], label)
            _require(hashlib.sha256(evidence).hexdigest() == reference["digest"], label + "_evidence_digest_mismatch")
        return record, data

    def document(self, ident, label):
        record, data = self.artifact(ident, label)
        try:
            value = loads(data)
            _finite_tree(value)
        except (ValueError, TypeError, RecursionError):
            raise _Issue(label + "_json_invalid") from None
        _require(isinstance(value, dict), label + "_json_invalid")
        return record, value, len(data)


def _entry(entry):
    _require(
        isinstance(entry, dict)
        and set(entry)
        == {
            "artifact_id",
            "candidate",
            "source",
            "trace_id",
            "step",
            "content",
            "declaration",
        },
        "entry_invalid",
    )
    _require(text(entry["artifact_id"]) and text(entry["trace_id"]), "entry_invalid")
    _require(entry["declaration"] in ("recovered", "partial", "generated"), "declaration_invalid")
    for label in ("candidate", "source"):
        ref = entry[label]
        _require(isinstance(ref, dict) and set(ref) == {"artifact_id", "pointer"}, label + "_reference_invalid")
        _require(text(ref["artifact_id"]), label + "_reference_invalid")
        try:
            _pointer(ref["pointer"])
        except ValueError:
            raise _Issue(label + "_pointer_invalid") from None
    _require(entry["source"]["pointer"] == "/source", "source_pointer_invalid")
    step = entry["step"]
    _require(isinstance(step, dict) and set(step) == {"pointer", "step_id"}, "step_invalid")
    _require(type(step["step_id"]) is int or text(step["step_id"]), "step_invalid")
    position = r"(0|[1-9][0-9]{0,8})"
    pattern = (
        rf"/root_spans/{position}(/children/{position})*/turns/{position}"
        if text(step["step_id"])
        else rf"/steps/{position}"
    )
    _require(
        isinstance(step["pointer"], str) and re.fullmatch(pattern, step["pointer"]),
        "step_pointer_invalid",
    )
    content = entry["content"]
    if content is None:
        return
    _require(isinstance(content, dict) and set(content) == {"pointer", "encoding"}, "content_invalid")
    _require(content["encoding"] == "utf-8", "content_encoding_unsupported")
    try:
        _pointer(content["pointer"])
    except ValueError:
        raise _Issue("content_pointer_invalid") from None


def _trace_turns(document):
    pending = document.get("root_spans")
    _require(isinstance(pending, list), "trace_steps_invalid")
    pending = list(pending)
    turns, visited = [], 0
    while pending:
        span = pending.pop()
        visited += 1
        _require(visited <= 2000 and isinstance(span, dict), "trace_steps_invalid")
        children = span.get("children", [])
        _require(isinstance(children, list) and len(children) + len(pending) <= 2000, "trace_steps_invalid")
        pending.extend(children)
        if span.get("kind") == "AGENT":
            recorded = span.get("turns", [])
            _require(isinstance(recorded, list) and len(recorded) + len(turns) <= 2000, "trace_steps_invalid")
            turns.extend(recorded)
    return turns


def _project(sources, entry, row):
    _entry(entry)
    row["declaration"] = entry["declaration"]
    artifact, raw = sources.artifact(entry["artifact_id"], "artifact")
    row["artifact_digest"] = artifact["digest"]
    candidate, candidate_doc, _ = sources.document(entry["candidate"]["artifact_id"], "candidate")
    row["candidate_digest"] = candidate["digest"]
    current = candidate_doc.get("schema") == "nemo.eval_author.trace_environment_candidate.v3"
    _require(
        current or candidate_doc.get("schema") == "nemo.eval_author.trace_environment_candidate.v2",
        "candidate_schema_unsupported",
    )
    _require(candidate_doc.get("state_basis", "recorded") in ("recorded", "reconstructed"), "candidate_state_invalid")
    _require(
        re.fullmatch(r"/ground_truth/artifacts/(0|[1-9][0-9]{0,8})", entry["candidate"]["pointer"]),
        "candidate_pointer_invalid",
    )
    retained = _at(candidate_doc, entry["candidate"]["pointer"], "candidate")
    _require(isinstance(retained, dict), "candidate_artifact_invalid")
    _require(retained.get("sha256") == "sha256:" + artifact["digest"], "candidate_artifact_mismatch")
    expected_path = (Path(candidate["path"]).parent / _relative(retained.get("path"), "candidate")).as_posix()
    _require(expected_path == artifact["path"], "candidate_artifact_mismatch")
    provenance = retained.get("provenance")
    ids_key = "turn_ids" if current else "step_ids"
    _require(
        isinstance(provenance, dict) and set(provenance) == {"kind", ids_key, "uri", "revision", "source_id"},
        "candidate_provenance_invalid",
    )
    step_ids = provenance[ids_key]
    _require(
        provenance["kind"] == ("trace_turn" if current else "atif_step")
        and all(provenance[k] is None for k in ("uri", "revision", "source_id")),
        "candidate_provenance_invalid",
    )
    _require(
        isinstance(step_ids, list)
        and 0 < len(step_ids) <= MAX_RECORDS
        and all(text(s) if current else type(s) is int for s in step_ids),
        "candidate_provenance_invalid",
    )
    _require(len(step_ids) == len(set(step_ids)) and entry["step"]["step_id"] in step_ids, "candidate_step_mismatch")
    source, source_doc, _ = sources.document(entry["source"]["artifact_id"], "source")
    row["source_digest"] = source["digest"]
    _require(
        source_doc.get("schema")
        in (
            ("nemo.eval_author.trace_environment_summary.v4",)
            if current
            else ("nemo.eval_author.trace_environment_summary.v2", "nemo.eval_author.trace_environment_summary.v3")
        ),
        "source_schema_unsupported",
    )
    _require(
        Path(candidate["path"]).name == "candidate.json"
        and Path(source["path"]).name == "summary.json"
        and Path(candidate["path"]).parent == Path(source["path"]).parent,
        "candidate_source_mismatch",
    )
    _require(
        "path" not in candidate_doc
        and _equal(source_doc.get("candidate"), {"path": "candidate.json", **candidate_doc}),
        "candidate_source_mismatch",
    )
    binding = _at(source_doc, entry["source"]["pointer"], "source")
    _require(isinstance(binding, dict), "source_binding_invalid")
    trace, trace_doc, trace_size = sources.document(entry["trace_id"], "trace")
    row["trace_digest"] = trace["digest"]
    _require(
        binding.get("safe_sha256") == "sha256:" + trace["digest"]
        and type(binding.get("safe_size_bytes")) is int
        and binding["safe_size_bytes"] == trace_size,
        "source_trace_mismatch",
    )
    expected_path = (Path(source["path"]).parent / _relative(binding.get("safe_path"), "source")).as_posix()
    _require(expected_path == trace["path"], "source_trace_mismatch")
    _require(trace_doc.get("schema_version") == ("3" if current else "ATIF-v1.7"), "trace_schema_unsupported")
    steps = _trace_turns(trace_doc) if current else trace_doc.get("steps")
    id_key = "id" if current else "step_id"
    _require(isinstance(steps, list) and len(steps) <= 2000, "trace_steps_invalid")
    _require(
        all(
            isinstance(item, dict) and (text(item.get(id_key)) if current else type(item.get(id_key)) is int)
            for item in steps
        ),
        "trace_steps_invalid",
    )
    counts = Counter(item[id_key] for item in steps)
    _require(all(counts[ident] > 0 for ident in step_ids), "candidate_step_missing")
    _require(all(counts[ident] == 1 for ident in step_ids), "step_ambiguous")
    step = _at(trace_doc, entry["step"]["pointer"], "step")
    _require(
        isinstance(step, dict)
        and (text(step.get(id_key)) if current else type(step.get(id_key)) is int)
        and step[id_key] == entry["step"]["step_id"]
        and any(step is item for item in steps),
        "step_mismatch",
    )
    row["source_step_id"] = step[id_key]
    row["source_step_index"] = int(entry["step"]["pointer"].rsplit("/", 1)[1])
    if entry["content"] is not None:
        content = _at(step, entry["content"]["pointer"], "content")
        _require(isinstance(content, str), "content_invalid")
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeError:
            raise _Issue("content_invalid") from None
        row["source_content_digest"] = hashlib.sha256(content_bytes).hexdigest()
        if entry["declaration"] == "recovered":
            _require(raw == content_bytes, "content_digest_mismatch")
    if entry["declaration"] != "recovered":
        raise _Issue(entry["declaration"] + "_source_proof_unknown", "unknown")
    if entry["content"] is None:
        raise _Issue("content_unavailable", "unknown")
    row["source_proof"] = "exact_selected_bytes"


def project_reconstruction(root, artifacts, selection):
    if not isinstance(selection, list) or not isinstance(artifacts, list):
        return finish([], [diagnostic("reconstruction.selection_invalid", "reconstruction")])
    if len(selection) > MAX_RECORDS or len(artifacts) > MAX_RECORDS:
        return finish([], [diagnostic("reconstruction.selection_limit", "reconstruction", "unknown")])
    if not selection:
        return finish([], [diagnostic("reconstruction.selection_missing", "reconstruction", "unknown")])
    sources = _Sources(root, artifacts)
    records, diagnostics = [], []
    for index, entry in enumerate(selection):
        row = {
            "selection_index": index,
            "artifact_digest": None,
            "candidate_digest": None,
            "source_digest": None,
            "trace_digest": None,
            "source_content_digest": None,
            "declaration": None,
            "source_proof": "unknown",
            "semantic_proof": False,
            "source_step_id": None,
            "source_step_index": None,
        }
        try:
            _project(sources, entry, row)
            row["status"] = "pass"
        except _Issue as issue:
            diagnostics.append(diagnostic("reconstruction." + issue.code, f"reconstruction:{index}", issue.status))
            row["status"] = issue.status
        records.append(row)
    return finish(records, diagnostics)
