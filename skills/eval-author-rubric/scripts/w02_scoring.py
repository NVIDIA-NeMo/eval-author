# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conservative canonical W02 assessment over deterministic measurements.

``assess(measurements, selection, root=None)`` returns eleven item-ID-keyed rows,
never an aggregate. Measurements come from the identity/route/config/links/approvals
helpers, not caller-authored verdicts. Inventory content findings require ``root``;
only selected local JSON sources are read, with their exact parsed bytes rehashed.
See ``references/identity.md`` for supported scope and review projections.

The bounded implementation scores DISC-01-A/B/C only. Other criteria retain
specific gaps: comparison, link and approval predicates alone cannot satisfy
whole-criterion anchors. No native/reviewer authentication protocol is invented.
"""

import hashlib
from pathlib import Path
from typing import Any

from eval_author_shared.task_evidence import loads
from rubric_identity import local_file

ITEM_LIMITATIONS = {
    "DISC-01-A": "Required task-artifact selection and source-bound access evidence are needed.",
    "DISC-01-B": "Selected scorer, scoring configuration and complete explicit dispatch evidence are needed.",
    "DISC-01-C": "Selected grading dependencies need source-bound access evidence and completeness review.",
    "DISC-02-A": "Configuration comparison does not establish per-field sources, relevant-setting coverage or claim limits.",
    "DISC-02-B": "No trusted native run observations bound to task/configuration are consumed; supplied values may echo declarations.",
    "DISC-03-A": "No trusted provider-owned manifest and adapter validation is consumed.",
    "AUD-02": "Configuration differences alone do not establish fairness or adjudicate accommodations.",
    "ENV-01": "No actual solver-identity call, error, reset and permission probe evidence is consumed.",
    "ENV-02-A": "Approval matching does not authenticate scope-owner authority or establish required-component availability.",
    "ENV-03": "Pins and identity differences do not establish material external-state coverage or justified live-state bounds.",
    "CREATE-07": "Identity/link checks do not establish change tracking, invalidation or preserved regrading lineage.",
}


def _observations(measurements):
    identity = measurements.get("identity", {})
    records = {}
    for record in identity.get("records", []):
        records.setdefault(record["id"], []).append(record)
    diagnostics = identity.get("diagnostics", [])
    return records, diagnostics


def _checked_record(records, diagnostics, ident):
    if not isinstance(ident, str) or not ident:
        return None
    candidates = records.get(ident, [])
    if len(candidates) != 1:
        return None
    record = candidates[0]
    if (
        record.get("access") != "available"
        or not record.get("digest")
        or not record.get("evidence")
        or any(d["subject"] == ident for d in diagnostics)
    ):
        return None
    return record


def _score(row, value, refs):
    row.update(status="ASSESSED", score=value, evidence_strength="E2", evidence_refs=refs)


def _source_json(root, record):
    if root is None or record is None:
        return None
    try:
        path = local_file(root, record["path"])
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        # Hash the parsed bytes, not a separate read that could observe a swap.
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != record["digest"]:
            return None
        value = loads(data)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _inventory(row, scope, records, diagnostics, root):
    if not isinstance(scope, dict):
        return
    row["evidence_strength"] = "E0"
    unresolved = scope.get("unresolved_material")
    if not isinstance(unresolved, list) or not all(isinstance(ref, str) and ref for ref in unresolved):
        return
    source = _checked_record(records, diagnostics, scope.get("source"))
    required = scope.get("required")
    if not source or not isinstance(required, list) or not required:
        return
    if not all(isinstance(ident, str) and ident for ident in required) or len(set(required)) != len(required):
        return
    roles = (
        {"task", "instructions", "input", "fixture", "metadata", "task_input", "task_metadata"}
        if row["item_id"] == "DISC-01-A"
        else {"scorer", "dependency", "test", "expected_result", "dataset", "judge_prompt", "rubric", "configuration"}
    )
    for ident in required:
        candidates = records.get(ident, [])
        if len(candidates) == 1 and (
            candidates[0].get("role") not in roles
            or candidates[0].get("source") != source["path"]
            or not candidates[0].get("selection_origin")
        ):
            row["limitations"].append("Required artifact role/reference origin is not bound to the selected source.")
            return
    counts = {"required": len(required), "accessible": 0, "missing": 0, "denied": 0, "unresolved": 0}
    for ident in required:
        if _checked_record(records, diagnostics, ident):
            counts["accessible"] += 1
            continue
        candidates = records.get(ident, [])
        codes = {d["code"] for d in diagnostics if d["subject"] == ident}
        record = candidates[0] if len(candidates) == 1 else {}
        # identify() reports the artifact ID, not the failing reference. A
        # secondary evidence read failure cannot prove the artifact is missing.
        exact_reference = record.get("evidence") and all(
            ref.get("path") == record.get("path") for ref in record["evidence"]
        )
        attempted = record.get("access") == "available" and exact_reference
        if attempted and "identity.artifact_missing" in codes:
            counts["missing"] += 1
        elif attempted and "identity.access_denied" in codes:
            counts["denied"] += 1
        else:
            counts["unresolved"] += 1
    row["measurements"] = counts
    row["evidence_strength"] = "E2"
    row["evidence_refs"] = [source["id"], *required]
    if counts["missing"] or counts["denied"]:
        _score(row, 0, row["evidence_refs"])
    elif counts["accessible"] and counts["unresolved"]:
        _score(row, 1, row["evidence_refs"])
    elif counts["accessible"] == len(required):
        document = _source_json(root, source)
        kind = "task_artifacts" if row["item_id"] == "DISC-01-A" else "grading_dependencies"
        if document is not None and document.get(kind) == {"required": required, "unresolved_material": unresolved}:
            _score(row, 1 if unresolved else 2, row["evidence_refs"])
        else:
            row["limitations"].append("Supported inventory source does not substantiate the selected projection.")
    row["limitations"] += [
        "Only the explicitly declared required inventory was measured; no undeclared dependency closure or independent review is established.",
        "Reviewer file access does not establish solver permissions, grader correctness or runtime readiness.",
    ]


def _route(row, measurement, selection, records, diagnostics):
    if not isinstance(selection, dict):
        return
    row["evidence_strength"] = "E0"
    entry = _checked_record(records, diagnostics, selection.get("entrypoint"))
    scorer = measurement.get("scorer")
    if not entry or not isinstance(scorer, str) or not scorer:
        return
    codes = {d["code"] for d in measurement.get("diagnostics", [])}
    refs = [entry["id"], scorer]
    if "route.scorer_mismatch" in codes and scorer != selection.get("expected_scorer"):
        _score(row, 0, refs)
    elif codes & {"route.dependencies_unresolved", "route.dependency_missing", "route.scorer_role_invalid"}:
        _score(row, 1, refs)
    elif (
        measurement.get("status") == "pass"
        and not codes
        and scorer == selection.get("expected_scorer")
        and scorer in measurement.get("selected", [])
        and all(_checked_record(records, diagnostics, ident) for ident in measurement["selected"])
    ):
        _score(row, 2, refs)
    row["limitations"] = [
        "Explicit JSON dispatch and declared dependency edges only; arbitrary code, remote branches and native run selection were not evaluated.",
        "Route identification does not establish grading correctness or provider compatibility; no independent resolution is established.",
    ]


def assess(measurements, selection, root: Path | None = None):
    rows: dict[str, dict[str, Any]] = {
        item_id: {
            "item_id": item_id,
            "status": "NOT_ASSESSED",
            "score": None,
            "evidence_strength": None,
            "evidence_refs": [],
            "limitations": [limitation],
        }
        for item_id, limitation in ITEM_LIMITATIONS.items()
    }
    records, diagnostics = _observations(measurements)
    selected = selection.get("artifacts", [])
    records = {
        ident: candidates
        for ident, candidates in records.items()
        if len(candidates) == 1 and sum(record == candidates[0] for record in selected) == 1
    }
    scope = selection.get("w02_scope", {})
    if not isinstance(scope, dict):
        raise ValueError("w02_scope must be an object")
    _inventory(rows["DISC-01-A"], scope.get("task_artifacts"), records, diagnostics, root)
    dependency_scope = scope.get("grading_dependencies")
    _inventory(rows["DISC-01-C"], dependency_scope, records, diagnostics, root)
    if rows["DISC-01-C"]["score"] == 2 and isinstance(dependency_scope, dict):
        review = _checked_record(records, diagnostics, dependency_scope.get("review_source"))
        source = _checked_record(records, diagnostics, dependency_scope.get("source"))
        conclusion = _source_json(root, review)
        if (
            source
            and review
            and review.get("role") == "review"
            and review.get("source") == source["path"]
            and conclusion
            == {
                "source": source["id"],
                "required": dependency_scope["required"],
                "unresolved_material": [],
                "complete": True,
            }
            and conclusion["complete"] is True
        ):
            rows["DISC-01-C"]["evidence_refs"].append(review["id"])
        else:
            rows["DISC-01-C"].update(status="NOT_ASSESSED", score=None)
            rows["DISC-01-C"]["limitations"].append(
                "Supported source-bound completeness review is missing or contradictory."
            )
    if selection.get("declared_config"):
        rows["DISC-02-A"]["evidence_strength"] = "E0"
    if selection.get("observed_config"):
        rows["DISC-02-B"]["evidence_strength"] = "E0"
    _route(rows["DISC-01-B"], measurements.get("route", {}), selection.get("route"), records, diagnostics)
    if any(d["code"] == "identity.digest_mismatch" for d in diagnostics):
        rows["CREATE-07"]["limitations"].append(
            "A mismatched digest alone does not show incompatible results were silently combined; material change impact and reuse disposition are unknown."
        )
    return rows
