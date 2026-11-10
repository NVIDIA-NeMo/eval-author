# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bound selected retained native references; not closure or task qualification."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path

import audit_verifier_evidence as audit
from eval_author_shared import task_evidence
from rubric_identity import DIGEST, FIELDS, diagnostic, finish, text
from rubric_links import check_links

MAX_ARTIFACTS = 64
MAX_BYTES = 2_000_000
MAX_TOTAL_BYTES = 16_000_000
MAX_STEPS = 2000
MAX_REFERENCES = 10000


class _Invalid(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _Invalid("native.invalid")


def _ref(role, *parts):
    return role + ":" + hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _read(root, record, limit):
    relative = Path(record["path"])
    _require(not relative.is_absolute() and ".." not in relative.parts and relative.as_posix() == record["path"])
    _require(relative.parts and record["path"] != ".")
    absolute_root = root.absolute()
    _require(".." not in absolute_root.parts)
    fd = os.open(absolute_root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Opening only the root leaf would still follow replaced ancestors.
        for component in (*absolute_root.parts[1:], *relative.parts[:-1]):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and before.st_size <= limit)
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            stable = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            _require(len(raw) <= limit and all(getattr(before, key) == getattr(after, key) for key in stable))
    finally:
        os.close(fd)
    _require(hashlib.sha256(raw).hexdigest() == record["digest"])
    return raw


class _SelectedReview(audit.Review):
    """Consumer reads use sealed bytes only; no implicit task-tree scan."""

    def __init__(self, repo, snapshots):
        self.repo = repo
        self.roots = []
        self.evidence = {}
        self.task_digests = {}
        self.manifests = {}
        self.snapshots = snapshots

    def path(self, value):
        path = Path(value)
        path = path if path.is_absolute() else self.repo / path
        if ".." in path.parts or not path.is_relative_to(self.repo):
            raise audit.EvidenceScopeError("native.path_unselected")
        return path

    def read(self, path):
        path = self.path(path)
        if path not in self.snapshots:
            raise audit.EvidenceScopeError("native.path_unselected")
        raw = self.snapshots[path]
        self.evidence[path.relative_to(self.repo).as_posix()] = hashlib.sha256(raw).hexdigest()
        return raw

    def task_digest(self, task):
        # An impossible digest forces historical validation, never current-task proof.
        return "unmeasured"


def _trace_graph(trace, trace_ref, run_ref, nodes, links, issue):
    _require(trace.get("schema_version") == "ATIF-v1.7")
    _require(text(trace.get("session_id")))
    steps = trace.get("steps")
    _require(isinstance(steps, list) and len(steps) <= MAX_STEPS)
    seen_steps = set()
    for step in steps:
        _require(isinstance(step, dict) and type(step.get("step_id")) is int)
        if step["step_id"] in seen_steps:
            issue("step_duplicate", trace_ref)
        seen_steps.add(step["step_id"])
        calls = step.get("tool_calls", [])
        observation = step.get("observation", {})
        calls = [] if calls is None else calls
        observation = {} if observation is None else observation
        _require(isinstance(calls, list) and isinstance(observation, dict))
        results = observation.get("results", [])
        results = [] if results is None else results
        _require(isinstance(results, list))
        _require(len(nodes) + len(links) + 2 * len(calls) + 3 * len(results) <= MAX_REFERENCES)
        call_counts = Counter(
            call.get("tool_call_id") for call in calls if isinstance(call, dict) and text(call.get("tool_call_id"))
        )
        result_counts = Counter(
            result.get("source_call_id")
            for result in results
            if isinstance(result, dict) and text(result.get("source_call_id"))
        )
        for call_id in call_counts:
            if not result_counts[call_id]:
                issue("tool_result_missing", trace_ref)
        for call_id, count in result_counts.items():
            if count > 1:
                issue("tool_result_duplicate", trace_ref)
            if call_counts[call_id] != 1:
                issue("tool_call_unresolved", trace_ref)
        for call in calls:
            _require(isinstance(call, dict) and text(call.get("tool_call_id")))
            call_ref = _ref("tool_call", trace_ref, step["step_id"], call["tool_call_id"])
            nodes.append({"id": call_ref, "role": "tool_call"})
            links.append({"source": call_ref, "target": trace_ref, "relation": "trace"})
        for index, result in enumerate(results):
            _require(isinstance(result, dict) and text(result.get("source_call_id")))
            result_ref = _ref("result", trace_ref, step["step_id"], index)
            nodes.append({"id": result_ref, "role": "result"})
            links.extend(
                [
                    {"source": result_ref, "target": run_ref, "relation": "run"},
                    {
                        "source": result_ref,
                        "target": _ref("tool_call", trace_ref, step["step_id"], result["source_call_id"]),
                        "relation": "tool_call",
                    },
                ]
            )
        _require(len(nodes) + len(links) <= MAX_REFERENCES)


def project_native(
    root: Path,
    identity: dict,
    *,
    receipt_ids: list[str],
    manifest_ids: list[str],
    result_ids: list[str],
    trace_ids: list[str],
) -> dict:
    """Project explicit IDs from an existing rubric_identity.identify result.

    Every selected file is re-read with no-follow bounded exact-hash checks.
    Linked files are never discovered. Native task-evidence v1 receipts and
    v1/v2 manifests, Harbor results and ATIF-v1.7 reference fields are supported.
    Graph IDs hash qualified native identifiers; task/code, prompts, arguments,
    tool output, checks and exception strings are never returned. Control
    classifications reuse audit_verifier_evidence on the same sealed bytes.
    Task freshness is unknown: no task-tree scan or closure validation occurs.
    """
    diagnostics, nodes, links, controls, subjects = [], [], [], [], []
    bindings = []

    def issue(code, subject="selection", status="fail"):
        diagnostics.append(diagnostic("native." + code, subject, status))

    def output():
        link_check = check_links(nodes, links)
        return {
            **finish([], diagnostics + link_check["diagnostics"]),
            "nodes": nodes,
            "links": links,
            "controls": controls,
            "subjects": subjects,
            "bindings": bindings,
            "link_check": link_check,
            "native_execution": False,
            "task_freshness": "unknown",
        }

    selections = (receipt_ids, manifest_ids, result_ids, trace_ids)
    if not all(isinstance(ids, list) and all(text(ident) for ident in ids) for ids in selections):
        issue("selection_invalid")
        return output()
    selected = [ident for ids in selections for ident in ids]
    if not selected:
        issue("selection_missing", status="unknown")
        return output()
    if len(selected) > MAX_ARTIFACTS or len(set(selected)) != len(selected):
        issue("selection_duplicate_or_limit")
        return output()
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get("records"), list)
        or not isinstance(identity.get("diagnostics", []), list)
        or not all(isinstance(row, dict) for row in identity.get("diagnostics", []))
    ):
        issue("identity_invalid")
        return output()
    indexed = {}
    for record in identity["records"]:
        if isinstance(record, dict) and text(record.get("id")):
            indexed.setdefault(record["id"], []).append(record)
    root = Path(root).absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        issue("root_invalid")
        return output()
    snapshots, objects, records, total = {}, {}, {}, 0
    for ident in selected:
        candidates = indexed.get(ident, [])
        if len(candidates) != 1:
            issue(
                "identity_duplicate" if candidates else "artifact_missing", ident, "fail" if candidates else "unknown"
            )
            continue
        record = candidates[0]
        allowance = min(MAX_BYTES, MAX_TOTAL_BYTES - total)
        if allowance <= 0:
            issue("total_limit", ident)
            continue
        try:
            _require(set(record) == FIELDS)
            _require(
                record["access"] == "available"
                and isinstance(record["digest"], str)
                and DIGEST.fullmatch(record["digest"])
            )
            _require(not any(row.get("subject") == ident for row in identity.get("diagnostics", [])))
            # Reserve even failed reads so malformed inputs cannot exceed the budget.
            total += allowance
            raw = _read(root, record, allowance)
            total -= allowance - len(raw)
            value = task_evidence.loads(raw)
            _require(isinstance(value, dict))
            json.dumps(value, allow_nan=False)
            path = root / record["path"]
            _require(path not in snapshots)
            snapshots[path], objects[ident], records[ident] = raw, value, record
            bindings.append({"id": ident, "digest": record["digest"]})
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            issue("artifact_invalid", ident)
    review = _SelectedReview(root, snapshots)
    manifest_paths = {root / records[ident]["path"]: ident for ident in manifest_ids if ident in records}
    result_paths = {root / records[ident]["path"]: ident for ident in result_ids if ident in records}
    trace_paths = {root / records[ident]["path"]: ident for ident in trace_ids if ident in records}
    consumer_rows = []
    seen_traces = set()
    consumed_paths = set()
    for ident in receipt_ids:
        row = {"receipt": ident, "classification": "unproven", "task_freshness": "unknown"}
        controls.append(row)
        if ident not in objects:
            continue
        receipt = objects[ident]
        try:
            _require(receipt.get("schema") == task_evidence.SCHEMA)
            _require(text(receipt.get("case")))
            manifest_path = review.path(receipt["manifest"])
            _require(manifest_path in manifest_paths)
            manifest = review.object(manifest_path)
            _require(manifest.get("schema") in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA))
            # Agent classification bypasses the shared consumer's task-revision path.
            _require(isinstance(manifest.get("task_sha256"), str) and DIGEST.fullmatch(manifest["task_sha256"]))
            _require(hashlib.sha256(review.read(manifest_path)).hexdigest() == receipt.get("manifest_sha256"))
            task_evidence.validate_contract(manifest["contract"])
            contract = manifest["contract"]
            _require(contract["provider"] == "harbor")
            result_path = review.path(receipt["result"])
            _require(result_path in result_paths)
            consumed_paths.add(result_path)
            native = review.object(result_path)
            _require(hashlib.sha256(review.read(result_path)).hexdigest() == receipt.get("result_sha256"))
            # The shared control consumer assumes these optional containers are objects.
            config = native.get("config", {})
            _require(isinstance(config, dict))
            agent = config.get("agent", {})
            _require(isinstance(agent, dict))
            _require(agent.get("name") is None or isinstance(agent["name"], str))
            task = review.path(manifest["task_dir"])
            inspected = audit.inspect_receipt(
                review, root / records[ident]["path"], receipt, {task}, verify_historical=True
            )
            consumer_rows.append(inspected)
            classification = inspected.get("historical_classification", inspected["classification"])
            row["classification"] = classification
            issue("task_freshness_unmeasured", ident, "unknown")
            if classification == "unproven":
                issue("control_unproven", ident, "unknown")
            native_id = task_evidence.pointer(native, contract["native_run_id"])
            _require(text(native_id))
            identity_status = "verified"
            try:
                task_evidence.native_identity(native, manifest)
            except (ValueError, KeyError, TypeError):
                identity_status = "unverified"
                issue("native_identity_unverified", ident, "unknown")
            _require(native.get("task_checksum") == manifest.get("harbor_task_checksum"))
            _require(native_id == receipt.get("native_run_id"))
            _require(text(native.get("task_name")) and text(native.get("trial_name")))
            _require(native["task_name"] == contract["task_id"])
            task_ref = _ref("task", contract["task_id"], manifest.get("task_sha256"))
            run_ref = _ref("run", task_ref, native_id)
            new_task = not any(node["id"] == task_ref for node in nodes)
            _require(len(nodes) + len(links) + 4 + int(new_task) <= MAX_REFERENCES)
            if new_task:
                nodes.append({"id": task_ref, "role": "task"})
            nodes.append({"id": run_ref, "role": "run"})
            links.append({"source": run_ref, "target": task_ref, "relation": "task"})
            trace_path = review.path(receipt["trace"])
            _require(trace_path in trace_paths)
            consumed_paths.add(trace_path)
            trace = review.object(trace_path)
            trace_sha = hashlib.sha256(review.read(trace_path)).hexdigest()
            _require(trace_sha == receipt.get("trace_sha256"))
            if trace_sha in seen_traces:
                issue("trace_reused", ident)
            seen_traces.add(trace_sha)
            trace_id = trace.get("trajectory_id") or trace.get("session_id")
            _require(text(trace_id))
            trace_ref = _ref("trace", run_ref, trace_id)
            nodes.append({"id": trace_ref, "role": "trace"})
            links.append({"source": trace_ref, "target": run_ref, "relation": "run"})
            subjects.append(
                {
                    "receipt": ident,
                    "task": task_ref,
                    "run": run_ref,
                    "trace": trace_ref,
                    "trace_sha256": trace_sha,
                    "native_identity": identity_status,
                    "accounting_task": _ref("accounting_task", native["task_name"]),
                    "accounting_run": _ref("accounting_run", native["trial_name"]),
                }
            )
            _trace_graph(trace, trace_ref, run_ref, nodes, links, issue)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            issue("reference_invalid", ident)
    for path, ident in manifest_paths.items():
        try:
            manifest = review.object(path)
            _require(manifest.get("schema") in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA))
            _require(isinstance(manifest.get("task_sha256"), str) and DIGEST.fullmatch(manifest["task_sha256"]))
            task_evidence.validate_contract(manifest["contract"])
            missing = [
                case
                for case, definition in manifest["contract"]["cases"].items()
                if definition["kind"] not in {"agent", "diagnostic"}
                and not any(
                    row.get("manifest") == path.relative_to(root).as_posix() and row.get("case") == case
                    for row in consumer_rows
                )
            ]
            if missing:
                issue("controls_missing", ident, "unknown")
            rows = audit.inspect_manifest(review, path, manifest, {review.path(manifest["task_dir"])}, consumer_rows)
            if rows:
                issue("manifest_unproven", ident, "unknown")
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            issue("manifest_invalid", ident)
    # Sealed bytes alone do not establish a selected native reference.
    for path, ident in (*result_paths.items(), *trace_paths.items()):
        if path not in consumed_paths:
            issue("reference_unconsumed", ident)
    return output()
