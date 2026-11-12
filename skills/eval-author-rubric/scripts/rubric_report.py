# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical rubric projection; helper outcomes never assign criterion merit.

``build_report(items, measurements, selection, root)`` returns item-ID-keyed
rows. ``items`` is the existing assess() decision mapping (or an explicit
subset); ``measurements`` must be helper outputs, not caller verdicts. Subject
revisions use validated content digests, never unverified revision labels.

Optional ``measurements.sourced_config``, ``measurements.freshness``,
``measurements.native`` and sourced ``measurements.approvals`` are revalidated
against selected sources once per report.
``predicate_results`` records bounded deterministic outcomes without upgrading broader canonical
checks or assigning merit. Field denominators include omitted category
placeholders; freshness denominators count selected evidence packets only.
Definitions/defaults remain canonical; native/review execution is never
inferred from raw helper status or selection data.
"""

import json
from copy import deepcopy
from pathlib import Path

from eval_author_shared.task_evidence import loads
from rubric_identity import identify, resolve_route
from rubric_scoring import _checked_record, _observations, classify_inventory, route_is_bound

CANONICAL = Path(__file__).resolve().parents[1] / "references/artifact-configuration-provenance.json"
INVENTORIES = {"DISC-01-A": "task_artifacts", "DISC-01-C": "grading_dependencies"}


def _validated(measurements, selection, root):
    records, diagnostics = _observations(measurements)
    current = identify(Path(root), selection.get("artifacts", []))
    current_records, current_diagnostics = _observations({"identity": current})
    records = {
        ident: candidates
        for ident, candidates in records.items()
        if len(candidates) == 1 and current_records.get(ident) == candidates
    }
    valid = {}
    for ident in records:
        record = _checked_record(records, diagnostics, ident)
        if record and record == _checked_record(current_records, current_diagnostics, ident):
            valid[ident] = record
    return records, valid


def _subject(valid, selection, item_id):
    tasks = [record for record in valid.values() if record["role"] == "task"]
    if len(tasks) == 1:
        return tasks[0]
    if tasks:
        return None
    scope = selection.get("rubric_scope", {}).get(INVENTORIES.get(item_id), {})
    reference = scope.get("source") if isinstance(scope, dict) else None
    if not isinstance(reference, str):
        route = selection.get("route")
        reference = route.get("entrypoint") if isinstance(route, dict) else None
    return valid.get(reference) if isinstance(reference, str) else None


def _diagnostics(measurements, helper, subjects=None):
    return [
        {"ref": f"#/measurements/{helper}/diagnostics/{index}", **deepcopy(diagnostic)}
        for index, diagnostic in enumerate(measurements.get(helper, {}).get("diagnostics", []))
        if subjects is None or diagnostic["subject"] in subjects
    ]


def _observed(check, helper, value, refs, diagnostics, boundary, outcome="PARTIAL"):
    check.update(
        outcome=outcome,
        evidence=[{"helper": helper, "value": deepcopy(value)}],
        evidence_refs=list(dict.fromkeys(refs)),
        diagnostic_refs=diagnostics,
        evidence_boundary=boundary,
    )
    if outcome == "PASS":
        check["remaining_gap"] = None


def _inventory(check, item_id, measurements, selection, root, records, valid):
    scope = selection.get("rubric_scope", {}).get(INVENTORIES[item_id])
    inventory = classify_inventory(
        item_id, scope, records, measurements.get("identity", {}).get("diagnostics", []), root, valid=valid
    )
    if inventory is None:
        return
    source = inventory["source"]
    source_id = scope.get("source")
    required = inventory["required"]
    source_bound = inventory["source_bound"]
    observed = inventory["records"]
    diagnostics = _diagnostics(measurements, "identity", set([source_id, *required]))
    if check["id"].endswith("C01"):
        if source is None:
            check["diagnostic_refs"] = diagnostics
            return
        origins_bound = inventory["origins_bound"]
        _observed(
            check,
            "identity",
            {"source": source_id, "source_projection_matches": source_bound, "records": observed},
            [source_id, *[record["id"] for record in observed]],
            diagnostics,
            "Explicit selected JSON inventory and reference origins only; no undeclared dependency discovery or independent review.",
            "PASS" if source_bound and origins_bound else "PARTIAL",
        )
    elif measurements.get("identity"):
        access = inventory["access"]
        outcome = "PARTIAL"
        if source_bound and not inventory["invalid_origins"]:
            if any(record["access"] in {"missing", "denied"} for record in access):
                outcome = "FAIL"
            elif all(record["access"] == "accessible" for record in access):
                outcome = "PASS"
        _observed(
            check,
            "identity",
            access,
            [record["id"] for record in observed],
            diagnostics,
            "Reviewer access to selected local files only; declared unavailable is unresolved, not a performed read failure. No solver-permission proof.",
            outcome,
        )
        check["measurements"] = [
            {
                "name": state,
                "value": sum(record["access"] == state for record in access),
                "unit": "artifacts",
                "denominator": len(required),
                "missingness": "Unselected/unrecorded required references count as unresolved.",
                "uncertainty": "Declared inventory only; no completeness claim.",
            }
            for state in ("accessible", "missing", "denied", "unresolved")
        ]


PARTIAL_HELPERS = {
    "DISC-02-A-C02": ("config", "Unknown JSON pointers only; no per-field source or provider-hidden classification."),
    "DISC-02-B-C01": (
        "config",
        "Caller-projected declared/observed diff only; no trusted native run/task binding or independently observed settings.",
    ),
    "DISC-02-B-C02": (
        "config",
        "Unavailable projected values stay unknown; observable native coverage and its denominator are not established.",
    ),
    "AUD-02-C01": (
        "config",
        "Declared/observed projection comparison only; not a version-bound cross-system comparison or fairness judgment.",
    ),
    "ENV-02-A-C01": (
        "identity",
        "Selected local identities only; installed component availability and complete MCP inventory are not established.",
    ),
    "ENV-02-A-C03": (
        "approvals",
        "Declared substitution bindings only; no installed runtime observation or scope-owner authentication.",
    ),
    "ENV-03-C01": (
        "identity",
        "Selected file digests only; no material external-state inventory, live response capture or drift bounds.",
    ),
    "CREATE-07-C01": (
        "links",
        "Explicit projected graph only; no complete native result-to-revision binding or semantic compatibility.",
    ),
    "CREATE-07-C02": (
        "identity",
        "Selected current byte comparisons only; no material change-impact adjudication, score invalidation or preserved regrading lineage.",
    ),
}


def _partial_helper(check, measurements, selection):
    helper, boundary = PARTIAL_HELPERS[check["id"]]
    measurement = measurements.get(helper, {})
    if not measurement:
        return
    diagnostics = _diagnostics(measurements, helper)
    if helper == "identity":
        value = measurement.get("records", [])
        refs = [row["id"] for row in value]
    elif helper == "config":
        value = {key: measurement[key] for key in ("differences", "unknowns") if key in measurement}
        refs = ["#/selection/declared_config", "#/selection/observed_config"]
    elif helper == "links":
        value = {
            "nodes": selection.get("nodes", []),
            "links": selection.get("links", []),
            "status": measurement.get("status"),
        }
        refs = ["#/selection/nodes", "#/selection/links"]
        if not value["nodes"]:
            value = None
    else:
        value = {"status": measurement.get("status"), "authority_authenticated": False}
        refs = [f"#/selection/approvals/{index}" for index, _ in enumerate(selection.get("approvals", []))]
        if not refs:
            value = None
    check["diagnostic_refs"] = diagnostics
    if value:
        _observed(check, helper, value, refs, diagnostics, boundary)
        check["remaining_gap"] = boundary + " Still required: " + check["expected_evidence"]


CONFIG_CHECKS = {
    "DISC-02-A-C01": "Selected field provenance only; required model, prompt, sampling, budget, harness and dependency coverage is not independently established.",
    "DISC-02-A-C02": "Selected fields and omitted category placeholders only; required-field completeness and version-reference semantics are not established.",
    "DISC-02-B-C01": "Selected-record run/task/configuration assertions only, not native attestation or complete runtime-setting coverage.",
    "DISC-02-B-C02": "Observable coverage is limited to selected fields and omitted category placeholders, not an independently established native denominator.",
    "AUD-02-C01": "Declared/observed source comparison only; distinct cross-system identities, complete comparison settings and fairness are not established.",
}


def _predicate(check, ident, outcome, scope):
    check.setdefault("predicate_results", []).append(
        {"id": ident, "outcome": outcome, "scope": scope, "evidence_refs": list(check["evidence_refs"])}
    )


def _counts(check, values, denominator, unit, missingness, uncertainty):
    check["measurements"] = [
        {
            "name": name,
            "value": value,
            "denominator": denominator,
            "unit": unit,
            "missingness": missingness,
            "uncertainty": uncertainty,
        }
        for name, value in values.items()
    ]


def _artifact_refs(selection, ids):
    return [
        f"#/selection/artifacts/{index}"
        for index, row in enumerate(selection.get("artifacts", []))
        if isinstance(row, dict) and row.get("id") in ids
    ]


def _sourced_config(check, measurement, selection):
    boundary = CONFIG_CHECKS[check["id"]]
    fields = measurement["fields"]
    ids = {
        field[side]["provenance"]["artifact_id"]
        for field in fields.values()
        for side in ("declared", "observed")
        if field[side]["provenance"]
    }
    for field in fields.values():
        binding = field["observed"]["binding"]
        ids.update(binding[role]["artifact_id"] for role in ("run", "task", "configuration") if role in binding)
    refs = ["#/measurements/sourced_config", "#/selection/sourced_config", *_artifact_refs(selection, ids)]
    _observed(check, "sourced_config", measurement, refs, [], boundary)
    check["remaining_gap"] = boundary + " Still required: " + check["expected_evidence"]
    counts = measurement["counts"]
    values = {name: value for name, value in counts.items() if name != "fields"}
    values.update(
        {
            side + "_" + state: sum(field[side]["state"] == state for field in fields.values())
            for side in ("declared", "observed")
            for state in ("omitted", "inaccessible", "provider_hidden", "unobservable")
        }
    )
    _counts(
        check,
        values,
        counts["fields"],
        "fields",
        "Unselected categories count as omitted; unavailable settings are unknown, never equal.",
        boundary,
    )
    ident = check["id"]
    if ident == "DISC-02-A-C01":
        _predicate(
            check,
            "declared_fields_sourced",
            "PASS" if counts["declared_sourced"] == counts["fields"] else "PARTIAL",
            "Every reported declared selector resolves to nonempty digest-bound source content.",
        )
    elif ident == "DISC-02-A-C02":
        _predicate(
            check,
            "unknown_states_classified",
            "PASS",
            "Omitted, inaccessible, provider-hidden and unobservable states retain fixed reasons and claim limits for reported fields.",
        )
    elif ident == "DISC-02-B-C02":
        _predicate(
            check,
            "unavailable_observations_not_matched",
            "PASS",
            "Unsourced observed fields remain unknown; counts retain all reported fields in the denominator.",
        )
    else:
        _predicate(
            check,
            "selected_fields_compared",
            "PASS" if not counts["unknown"] else "PARTIAL",
            "Type-preserving comparison over selected source fields; different is a measured difference, not a fairness failure.",
        )
        if ident == "DISC-02-B-C01":
            bindings = [field["observed"]["binding"] for field in fields.values()]
            mismatch = any(binding["reason"] and binding["reason"].endswith("_mismatch") for binding in bindings)
            outcome = (
                "FAIL"
                if mismatch
                else "PASS"
                if counts["run_bound"] == counts["fields"]
                else "PARTIAL"
                if counts["run_bound"]
                else "BLOCKED"
            )
            _predicate(
                check,
                "selected_run_binding",
                outcome,
                "Selected observation assertions match run/task identifiers and configuration digest; no native execution attestation.",
            )


FRESHNESS_CHECKS = {
    "CREATE-07-C01": "Selected result/dependency lineage only; completeness of all material instructions, fixtures, scorer, ground truth, judge and runtime bindings is not established.",
    "CREATE-07-C02": "Derived freshness and reuse eligibility only; no persisted score invalidation, regrading execution or complete regrading history is established.",
    "CREATE-07-C04": "Selected ETHOS byte/revision lineage only; obligation references, semantic change-impact adjudication and changed/unchanged fixture outcomes are not established.",
}


def _freshness(check, measurements, selection):
    measurement = measurements["freshness"]
    if any(row["code"] == "freshness.input_invalid" for row in measurement["diagnostics"]):
        check["diagnostic_refs"] = _diagnostics(measurements, "freshness")
        return
    indexed = list(enumerate(measurement["records"]))
    if check["id"] == "CREATE-07-C04":
        ethos = {
            row["id"]
            for row in selection.get("artifacts", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("role") == "ethos"
        }
        indexed = [(index, row) for index, row in indexed if any(dep["id"] in ethos for dep in row["lineage"])]
        if not indexed:
            return
    records = [row for _, row in indexed]
    boundary = FRESHNESS_CHECKS[check["id"]]
    refs = [f"#/measurements/freshness/records/{index}" for index, _ in indexed]
    if not refs:
        refs = ["#/measurements/freshness"]
    ids = {row["id"] for row in records}
    ids.update(dep["id"] for row in records for dep in row["lineage"])
    for index, entry in enumerate(selection["freshness"]):
        if isinstance(entry, dict) and entry.get("id") in {row["id"] for row in records}:
            refs.append(f"#/selection/freshness/{index}")
            if isinstance(entry.get("source"), str):
                ids.add(entry["source"])
    refs.extend(_artifact_refs(selection, ids))
    _observed(
        check,
        "freshness",
        records,
        refs,
        _diagnostics(measurements, "freshness", {row["id"] for row in records} if records else None),
        boundary,
    )
    check["remaining_gap"] = boundary + " Still required: " + check["expected_evidence"]
    values = {state: sum(row["status"] == state for row in records) for state in ("current", "stale", "unresolved")}
    values["usable"] = sum(row["usable"] for row in records)
    _counts(
        check,
        values,
        len(records),
        "evidence packets",
        "Unresolved selected packets remain in the denominator; unselected evidence is outside scope.",
        boundary,
    )
    current = "BLOCKED" if not records or values["unresolved"] else "FAIL" if values["stale"] else "PASS"
    if check["id"] == "CREATE-07-C01":
        # A failed dependency can leave a valid-looking prefix of lineage.
        bound = (
            records
            and len(records) == len(selection["freshness"])
            and not any(row["code"] == "freshness.binding_invalid" for row in measurement["diagnostics"])
            and all(
                row["lineage"] and all(dep["old"]["digest"] and dep["new"]["digest"] for dep in row["lineage"])
                for row in records
            )
        )
        outcome = "PASS" if bound else "BLOCKED"
        _predicate(
            check,
            "selected_evidence_lineage_bound",
            outcome,
            "Selected retained evidence has source-bound old/current dependency identities; no material-completeness claim.",
        )
    elif check["id"] == "CREATE-07-C02":
        _predicate(
            check,
            "selected_evidence_current",
            current,
            "Selected dependency identities match retained evidence; usable negative controls are not passing controls.",
        )
        _predicate(
            check,
            "noncurrent_evidence_withheld",
            "PASS" if records else "BLOCKED",
            "The derived helper marks stale/unresolved evidence unusable; it does not mutate scores or source artifacts.",
        )
    else:
        _predicate(
            check,
            "selected_ethos_identity_current",
            current,
            "Freshness of selected packets with ETHOS dependencies; changed ETHOS semantic impact remains unresolved.",
        )


def _native(check, measurements, selection):
    measurement = measurements["native"]
    if any(row["code"] == "native.selection_invalid" for row in measurement["diagnostics"]):
        check["diagnostic_refs"].extend(_diagnostics(measurements, "native"))
        return
    boundary = (
        "Selected retained native task/run/trace/tool references only; no complete material dependency binding, "
        "current task freshness, native execution, semantic compatibility or task-quality qualification."
    )
    refs = [
        "#/measurements/native",
        "#/selection/native",
        *_artifact_refs(selection, {row["id"] for row in measurement["bindings"]}),
    ]
    subjects = measurement["subjects"]
    selected = selection["native"]
    bound = (
        subjects
        and len(subjects) == len(selected["receipt_ids"])
        and len(measurement["bindings"]) == sum(len(ids) for ids in selected.values())
        and all(subject["native_identity"] == "verified" for subject in subjects)
        and measurement["link_check"]["status"] == "pass"
    )
    outcome = "FAIL" if measurement["status"] == "fail" else "PASS" if bound else "BLOCKED"
    native_check = {}
    _observed(native_check, "native", measurement, refs, _diagnostics(measurements, "native"), boundary)
    _predicate(
        native_check,
        "selected_native_references_bound",
        outcome,
        "Selected retained receipt/manifest/result/ATIF identities and qualified task/run/trace/tool links only; "
        "control classification is not reference integrity, current task freshness or executed qualification.",
    )
    # Append after freshness so its scoped predicates, counts and evidence survive.
    for key in ("evidence", "evidence_refs", "diagnostic_refs", "predicate_results"):
        check.setdefault(key, []).extend(native_check[key])
    check["evidence_refs"] = list(dict.fromkeys(check["evidence_refs"]))
    check["outcome"] = "PARTIAL"
    check["evidence_boundary"] += " " + boundary
    check["remaining_gap"] += " " + boundary


def _sourced_approvals(check, measurements, selection):
    measurement = measurements["approvals"]
    boundary = (
        "Selected retained substitution decisions, task scope and current byte bindings only; "
        "no installed runtime observation, declared-difference fidelity, selection completeness or authority authentication."
    )
    refs = [
        "#/measurements/approvals",
        "#/selection/approval_sources",
        *[f"#/selection/artifacts/{index}" for index, _ in enumerate(selection.get("artifacts", []))],
    ]
    _observed(check, "approvals", measurement, refs, _diagnostics(measurements, "approvals"), boundary)
    check["remaining_gap"] = boundary + " Still required: " + check["expected_evidence"]
    diagnostics = measurement["diagnostics"]
    scope_codes = {
        "approvals.not_approved",
        "approvals.scope_mismatch",
        "approvals.substitution_mismatch",
        "approvals.binding_mismatch",
        "approvals.scope_unbound",
        "approvals.substitution_missing",
        "approvals.bindings_incomplete",
        "approvals.source_in_material",
        "approvals.role_missing",
    }
    freshness_codes = {"approvals.digest_stale", "approvals.current_unresolved"}
    source_diagnostics = [row for row in diagnostics if row["code"] not in scope_codes | freshness_codes]
    source_bound = (
        bool(selection.get("approvals"))
        and bool(measurement["evidence"])
        and len(measurement["evidence"]) == len(selection.get("decisions", []))
        and not source_diagnostics
    )
    source_outcome = (
        "PASS" if source_bound else "FAIL" if any(row["status"] == "fail" for row in source_diagnostics) else "BLOCKED"
    )
    scoped = [row for row in diagnostics if row["code"] in scope_codes]
    scope_outcome = (
        "BLOCKED"
        if not source_bound
        else "FAIL"
        if any(row["status"] == "fail" for row in scoped)
        else "BLOCKED"
        if scoped
        else "PASS"
    )
    freshness_outcome = (
        "BLOCKED"
        if scope_outcome != "PASS"
        else "FAIL"
        if any(row["code"] == "approvals.digest_stale" for row in diagnostics)
        else "BLOCKED"
        if diagnostics
        else "PASS"
    )
    for ident, outcome, scope in (
        (
            "selected_approval_sources_match",
            source_outcome,
            "Projected decisions match selected digest-bound retained JSON records.",
        ),
        (
            "selected_approval_scope_matches",
            scope_outcome,
            "Retained approved decisions match the selected task/substitution scope and material binding IDs.",
        ),
        (
            "selected_approval_bindings_current",
            freshness_outcome,
            "Retained decision bindings equal selected current artifact digests; not an age or runtime check.",
        ),
    ):
        _predicate(check, ident, outcome, scope)


def _verified_helpers(measurements, selection, root):
    from rubric_configuration import compare_sourced_config
    from rubric_freshness import check_freshness
    from rubric_links import check_sourced_approvals
    from rubric_native import project_native

    verified = set()
    for helper in ("sourced_config", "freshness", "native", "approvals"):
        selector = "approval_sources" if helper == "approvals" else helper
        if helper not in measurements or selector not in selection:
            continue
        try:
            if helper == "approvals":
                current = check_sourced_approvals(
                    Path(root),
                    selection.get("approvals", []),
                    selection.get("decisions", []),
                    identify(Path(root), selection.get("artifacts", [])),
                    selection[selector],
                )
            elif helper == "sourced_config":
                config = selection[helper]
                current = compare_sourced_config(
                    Path(root),
                    selection.get("artifacts", []),
                    config["declared"],
                    config["observed"],
                    binding=config.get("binding"),
                )
            elif helper == "native":
                current = project_native(
                    Path(root), identify(Path(root), selection.get("artifacts", [])), **selection[helper]
                )
            else:
                current = check_freshness(Path(root), selection.get("artifacts", []), selection[helper])
            if json.dumps(current, sort_keys=True) == json.dumps(measurements[helper], sort_keys=True):
                verified.add(helper)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            continue
    return verified


def _helper_check(check, item_id, measurements, selection, root, records, valid, verified):
    if check["method"] != "deterministic":
        return
    if check["id"] == "ENV-02-A-C03" and (
        "approval_sources" in selection or "evidence" in measurements.get("approvals", {})
    ):
        if "approvals" in verified:
            _sourced_approvals(check, measurements, selection)
        else:
            check["remaining_gap"] = (
                "Selected approval evidence failed source/selection revalidation. " + check["remaining_gap"]
            )
        return
    helper = (
        "sourced_config" if check["id"] in CONFIG_CHECKS else "freshness" if check["id"] in FRESHNESS_CHECKS else None
    )
    if helper in measurements:
        if helper not in verified:
            check["remaining_gap"] = (
                "Selected helper evidence failed source/selection revalidation. " + check["remaining_gap"]
            )
        elif helper == "sourced_config":
            _sourced_config(check, measurements[helper], selection)
        else:
            _freshness(check, measurements, selection)
    elif check["id"] in PARTIAL_HELPERS:
        _partial_helper(check, measurements, selection)
    elif item_id in INVENTORIES and check["method"] == "deterministic":
        _inventory(check, item_id, measurements, selection, root, records, valid)
    elif check["id"] == "DISC-01-B-C01":
        route = measurements.get("route", {})
        selected = selection.get("route")
        if not route or not isinstance(selected, dict):
            return
        entry = selected.get("entrypoint")
        if not isinstance(entry, str) or entry not in valid:
            check["diagnostic_refs"] = _diagnostics(measurements, "route")
            return
        outcome = "PASS" if route_is_bound(route) else "FAIL" if route.get("status") == "fail" else "PARTIAL"
        current_route = resolve_route(Path(root), selection.get("artifacts", []), selected)
        if route != current_route or (
            outcome == "PASS" and (not route.get("selected") or any(ident not in valid for ident in route["selected"]))
        ):
            outcome = "PARTIAL"
        _observed(
            check,
            "route",
            route,
            [entry, *route.get("selected", [])],
            _diagnostics(measurements, "route"),
            "Selected explicit JSON dispatch and declared dependency edges only; executed scorer and provider compatibility are not established.",
            outcome,
        )

    if check["id"] == "CREATE-07-C01" and "native" in measurements:
        if "native" in verified:
            _native(check, measurements, selection)
        else:
            check["remaining_gap"] += " Native helper evidence failed source/selection revalidation."


def build_report(items, measurements, selection, root):
    definitions = loads(CANONICAL.read_bytes())
    known = {item["id"] for item in definitions["items"]}
    if not isinstance(items, dict) or not items.keys() <= known:
        raise ValueError("unknown rubric item")
    records, valid = _validated(measurements, selection, root)
    verified = _verified_helpers(measurements, selection, root)
    rows = {}
    for item_index, definition in enumerate(definitions["items"]):
        ident = definition["id"]
        if ident not in items:
            continue
        subject = _subject(valid, selection, ident)
        row = deepcopy(definitions["result_template"])
        row.update(deepcopy(items[ident]))
        if isinstance(row["measurements"], dict):
            row["legacy_measurements"] = row["measurements"]
            row["measurements"] = []
        row.update(
            item_id=ident,
            skill_id=definition["skill_id"],
            assessment_state=row["status"],
            subject_id=subject["id"] if subject else None,
            subject_revision="sha256:" + subject["digest"] if subject else None,
            applicability_reason=definition["requirement"],
            check_results=[],
        )
        for index, check in enumerate(definition["checks"]):
            gap = "No qualified evidence consumed for: " + check["expected_evidence"]
            result = {
                **deepcopy(check),
                "check_id": check["id"],
                "condition": check["when"],
                "source_ids": deepcopy(definition["source_ids"]),
                "definition_ref": f"artifact-configuration-provenance.json#/items/{item_index}/checks/{index}",
                "outcome": "NOT_RUN" if check["method"] != "deterministic" else "BLOCKED",
                "evidence": [],
                "evidence_refs": [],
                "diagnostic_refs": [],
                "evidence_boundary": definition["boundary"],
                "remaining_gap": gap,
                "next_action": check["action"],
            }
            _helper_check(result, ident, measurements, selection, root, records, valid, verified)
            row["check_results"].append(result)
            row["measurements"].extend(deepcopy(result.get("measurements", [])))
        row["execution_state"] = "PARTIAL" if any(check["evidence"] for check in row["check_results"]) else "NOT_RUN"
        row["remaining_gaps"] = list(
            dict.fromkeys(
                [
                    *row.get("limitations", []),
                    *[check["remaining_gap"] for check in row["check_results"] if check["remaining_gap"]],
                    *(["No unambiguous validated selected subject identity is available."] if not subject else []),
                ]
            )
        )
        row["next_action"] = definition["on_gap"]
        rows[ident] = row
    return rows
