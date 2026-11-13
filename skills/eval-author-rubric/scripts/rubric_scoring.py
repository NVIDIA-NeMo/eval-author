# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conservative canonical rubric assessment over deterministic measurements.

``assess(measurements, selection, root=None)`` returns eleven item-ID-keyed rows,
never an aggregate. Measurements come from the identity/route/config/links/approvals
helpers, not caller-authored verdicts. Inventory content findings require ``root``;
only selected local JSON sources are read, with their exact parsed bytes rehashed.
See ``references/identity.md`` for supported scope and review projections.

The bounded implementation scores DISCOVERY-01-A/B/C only. Other criteria retain
specific gaps: comparison, link and approval predicates alone cannot satisfy
whole-criterion anchors. No native/reviewer authentication protocol is invented.
"""

import hashlib
from pathlib import Path
from typing import Any

from eval_author_shared.task_evidence import loads
from rubric_identity import _read_selected

ITEM_LIMITATIONS = {
    "DISCOVERY-01-A": "Required task-artifact selection and source-bound access evidence are needed.",
    "DISCOVERY-01-B": "Selected scorer, scoring configuration and complete explicit dispatch evidence are needed.",
    "DISCOVERY-01-C": "Selected grading dependencies need source-bound access evidence and completeness review.",
    "DISCOVERY-02-A": "Configuration comparison does not establish per-field sources, relevant-setting coverage or claim limits.",
    "DISCOVERY-02-B": "No trusted native run observations bound to task/configuration are consumed; supplied values may echo declarations.",
    "DISCOVERY-03-A": "No trusted provider-owned manifest and adapter validation is consumed.",
    "AUDIT-02": "Configuration differences alone do not establish fairness or adjudicate accommodations.",
    "ENVIRONMENT-01": "No actual solver-identity call, error, reset and permission probe evidence is consumed.",
    "ENVIRONMENT-02-A": "Approval matching does not authenticate scope-owner authority or establish required-component availability.",
    "ENVIRONMENT-03": "Pins and identity differences do not establish material external-state coverage or justified live-state bounds.",
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
        data, reason = _read_selected(root, record["path"])
        if reason:
            return None
        # Hash the parsed bytes, not a separate read that could observe a swap.
        if hashlib.sha256(data).hexdigest() != record["digest"]:
            return None
        value = loads(data)
    except (OSError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def classify_inventory(item_id, scope, records, diagnostics, root, valid=None):
    if not isinstance(scope, dict):
        return None
    required = scope.get("required")
    unresolved = scope.get("unresolved_material")
    if (
        not isinstance(scope.get("source"), str)
        or not scope["source"]
        or not isinstance(required, list)
        or not required
        or not all(isinstance(ident, str) and ident for ident in required)
        or len(set(required)) != len(required)
        or not isinstance(unresolved, list)
        or not all(isinstance(ref, str) and ref for ref in unresolved)
    ):
        return None
    source = _checked_record(records, diagnostics, scope.get("source"))
    if source and valid is not None and source["id"] not in valid:
        source = None
    kind = "task_artifacts" if item_id == "DISCOVERY-01-A" else "grading_dependencies"
    document = _source_json(root, source)
    source_bound = document is not None and document.get(kind) == {
        "required": required,
        "unresolved_material": unresolved,
    }
    roles = (
        {"task", "instructions", "input", "fixture", "metadata", "task_input", "task_metadata"}
        if item_id == "DISCOVERY-01-A"
        else {"scorer", "dependency", "test", "expected_result", "dataset", "judge_prompt", "rubric", "configuration"}
    )
    observed, access = [], []
    invalid_origins = False
    for ident in required:
        candidates = records.get(ident, [])
        record = candidates[0] if len(candidates) == 1 else None
        state = "unresolved"
        if record:
            observed.append(record)
            bound = bool(
                source
                and record.get("source") == source["path"]
                and record.get("role") in roles
                and record.get("selection_origin")
            )
            invalid_origins |= not bound
            if bound and _checked_record(records, diagnostics, ident) and (valid is None or ident in valid):
                state = "accessible"
            elif bound:
                codes = {d["code"] for d in diagnostics if d["subject"] == ident}
                # A secondary evidence read failure does not prove the artifact is missing.
                exact = record.get("evidence") and all(
                    ref.get("path") == record.get("path") for ref in record["evidence"]
                )
                if record.get("access") == "available" and exact:
                    if "identity.artifact_missing" in codes:
                        state = "missing"
                    elif "identity.access_denied" in codes:
                        state = "denied"
        access.append(
            {
                "id": ident,
                "access": state,
                "digest": record.get("digest") if record else None,
                "declared_access": record.get("access") if record else None,
            }
        )
    counts = {
        "required": len(required),
        **{
            state: sum(row["access"] == state for row in access)
            for state in ("accessible", "missing", "denied", "unresolved")
        },
    }
    return {
        "source": source,
        "source_bound": source_bound,
        "required": required,
        "unresolved_material": unresolved,
        "records": observed,
        "access": access,
        "counts": counts,
        "invalid_origins": invalid_origins,
        "origins_bound": not invalid_origins and len(observed) == len(required),
    }


def _inventory(row, scope, records, diagnostics, root):
    if not isinstance(scope, dict):
        return
    row["evidence_strength"] = "E0"
    inventory = classify_inventory(row["item_id"], scope, records, diagnostics, root)
    if inventory is None or inventory["source"] is None:
        return
    counts = inventory["counts"]
    row["measurements"] = counts
    row["evidence_strength"] = "E2"
    row["evidence_refs"] = [inventory["source"]["id"], *inventory["required"]]
    row["limitations"] = [
        "Only the explicitly declared required inventory was measured; no undeclared dependency closure or independent review is established.",
        "Reviewer file access does not establish solver permissions, grader correctness or runtime readiness.",
    ]
    if not inventory["source_bound"]:
        row["limitations"].append("Supported inventory source does not substantiate the selected projection.")
    elif inventory["invalid_origins"]:
        row["limitations"].append("Required artifact role/reference origin is not bound to the selected source.")
    elif counts["missing"] or counts["denied"]:
        _score(row, 0, row["evidence_refs"])
    elif counts["accessible"] and counts["unresolved"]:
        _score(row, 1, row["evidence_refs"])
    elif counts["accessible"] == counts["required"]:
        _score(row, 1 if inventory["unresolved_material"] else 2, row["evidence_refs"])


def route_is_bound(measurement):
    return measurement.get("status") == "pass" and measurement.get("bound_dependency") is True


def _route(row, measurement, selection, records, diagnostics, root):
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
        document = _source_json(root, entry)
        if document is not None and document.get("expected_scorer") == selection.get("expected_scorer"):
            _score(row, 0, refs)
    elif measurement.get("dependency_source") and codes & {
        "route.dependencies_unresolved",
        "route.dependency_missing",
        "route.scorer_role_invalid",
    }:
        _score(row, 1, refs)
    elif (
        route_is_bound(measurement)
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
    scope = selection.get("rubric_scope", {})
    if not isinstance(scope, dict):
        raise ValueError("rubric_scope must be an object")
    _inventory(rows["DISCOVERY-01-A"], scope.get("task_artifacts"), records, diagnostics, root)
    dependency_scope = scope.get("grading_dependencies")
    _inventory(rows["DISCOVERY-01-C"], dependency_scope, records, diagnostics, root)
    if rows["DISCOVERY-01-C"]["score"] == 2 and isinstance(dependency_scope, dict):
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
            rows["DISCOVERY-01-C"]["evidence_refs"].append(review["id"])
        else:
            rows["DISCOVERY-01-C"].update(status="NOT_ASSESSED", score=None)
            rows["DISCOVERY-01-C"]["limitations"].append(
                "Supported source-bound completeness review is missing or contradictory."
            )
    if selection.get("declared_config"):
        rows["DISCOVERY-02-A"]["evidence_strength"] = "E0"
    if selection.get("observed_config"):
        rows["DISCOVERY-02-B"]["evidence_strength"] = "E0"
    _route(rows["DISCOVERY-01-B"], measurements.get("route", {}), selection.get("route"), records, diagnostics, root)
    if any(d["code"] == "identity.digest_mismatch" for d in diagnostics):
        rows["CREATE-07"]["limitations"].append(
            "A mismatched digest alone does not show incompatible results were silently combined; material change impact and reuse disposition are unknown."
        )
    return rows
