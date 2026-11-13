# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bound selected retained native references; not closure or task qualification."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import audit_verifier_evidence as audit
from eval_author_shared import task_evidence
from rubric_identity import DIGEST, FIELDS, SelectedAccess, artifact_byte_limit, diagnostic, finish, read_selected, text
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
    raw, reason = read_selected(root, record["path"], limit)
    _require(reason is None)
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


def _integrity_summary(controls):
    states = [row["integrity"] for row in controls]
    eligible = sum(row["status_claim"] in {"consistent", "contradiction"} for row in states)
    contradictions = sum(row["status_claim"] == "contradiction" for row in states)
    return {
        "detector_version": "receipt-integrity-v1",
        "selected_receipts": len(states),
        "eligible_opportunities": eligible,
        "contradictions": contradictions,
        "unknown": sum(row["status_claim"] == "unknown" for row in states),
        "excluded": sum(row["status_claim"] == "excluded" for row in states),
        "execution_errors": sum(row["execution"] == "execution_error" for row in states),
        "mismatched": sum(row["status"] == "mismatched" for row in states),
        "violation_rate": contradictions / eligible if eligible else None,
        "context_rot_score": None,
    }


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
            "receipt_integrity": _integrity_summary(controls),
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
    access = SelectedAccess(identity["records"])
    indexed = {}
    for record in identity["records"]:
        if isinstance(record, dict) and text(record.get("id")):
            indexed.setdefault(record["id"], []).append(record)
    root = Path(root).resolve()
    snapshots, objects, records, total = {}, {}, {}, 0
    for ident in selected:
        candidates = indexed.get(ident, [])
        if len(candidates) != 1:
            issue(
                "identity_duplicate" if candidates else "artifact_missing", ident, "fail" if candidates else "unknown"
            )
            continue
        record = candidates[0]
        reason = access.reason(record.get("path")) if isinstance(record.get("path"), str) else None
        if reason:
            issue(reason, ident, "unknown")
            continue
        allowance = min(MAX_BYTES, artifact_byte_limit(record), MAX_TOTAL_BYTES - total)
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
        row = {
            "receipt": ident,
            "classification": "unproven",
            "task_freshness": "unknown",
            "integrity": {
                "status": "unknown",
                "execution": "unknown",
                "behavior": "unknown",
                "status_claim": "unknown",
                "code": "evidence_unavailable",
                "freshness": "unknown",
            },
        }
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
            # Missing output may still prove an execution error, but another
            # selected role must never authorize native-result consumption.
            if result_path in snapshots:
                _require(result_path in result_paths)
            if result_path in result_paths:
                native = review.object(result_path)
                _require(native.get("task_name") == contract["task_id"])
                config = native.get("config", {})
                _require(isinstance(config, dict))
                agent = config.get("agent", {})
                _require(isinstance(agent, dict))
                _require(agent.get("name") is None or isinstance(agent["name"], str))
            task = review.path(manifest["task_dir"])
            inspected = audit.inspect_receipt(
                review, root / records[ident]["path"], receipt, {task}, verify_historical=True, include_integrity=True
            )
            consumer_rows.append(inspected)
            if "integrity" in inspected:
                row["integrity"] = {**inspected["integrity"], "freshness": "unknown"}
            classification = inspected.get("historical_classification", inspected["classification"])
            row["classification"] = classification
            issue("task_freshness_unmeasured", ident, "unknown")
            if classification == "unproven":
                issue("control_unproven", ident, "unknown")
            _require(result_path in result_paths)
            consumed_paths.add(result_path)
            native = review.object(result_path)
            _require(hashlib.sha256(review.read(result_path)).hexdigest() == receipt.get("result_sha256"))
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
            trace_ref = trace_sha = None
            if receipt["trace"] is not None:
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
            if trace_ref is not None:
                _trace_graph(trace, trace_ref, run_ref, nodes, links, issue)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            issue("reference_invalid", ident)
    for path, ident in manifest_paths.items():
        try:
            manifest = review.object(path)
            _require(manifest.get("schema") in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA))
            _require(isinstance(manifest.get("task_sha256"), str) and DIGEST.fullmatch(manifest["task_sha256"]))
            task_evidence.validate_contract(manifest["contract"])
            rows = audit.inspect_manifest(
                review,
                path,
                manifest,
                {review.path(manifest["task_dir"])},
                consumer_rows,
                verify_historical=True,
            )
            for row in rows:
                issue("controls_missing" if row.get("cases") else "manifest_unproven", ident, "unknown")
        except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
            issue("manifest_invalid", ident)
    # Sealed bytes alone do not establish a selected native reference.
    for path, ident in (*result_paths.items(), *trace_paths.items()):
        if path not in consumed_paths:
            issue("reference_unconsumed", ident)
    return output()
