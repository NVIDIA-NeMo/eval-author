# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical current-scope projection after owning evidence admission.

Legacy consumers remain intact. This seam consumes their observations, never
translates their numeric scores or transfers their owner decisions.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

REFERENCES = Path(__file__).resolve().parents[1] / "references"
LEGACY_CROSSWALK = {
    "AC-01": ["AUDIT-05-B", "CREATE-02", "AUDIT-03"],
    "AC-02": ["AUDIT-03"],
    "AC-03": ["AUDIT-03"],
    "AC-04": ["ENV-03-S01"],
}
SOURCES = ("artifact-configuration-provenance", "task-contract-authority", "leakage-and-credit")
GAPS = {
    "AUDIT-03": "Approve the exact attack applicability/per-check-credit matrix, including forbidden answer access and valid inaction; execute every unresolved class through the native scorer. Held-out independently authored attacks are still required for 3.",
    "AUDIT-04-B": "Supply a scoped owner containment disposition for the confirmed affected revision and a healthy repair replay on this task; independent attacks remain unrun. An exact rollback is not this repair panel.",
    "AUDIT-04-B-SUITE": "Supply containment dispositions for every affected retained task and healthy repair replay on retained affected AND new tasks; independent cross-family attacks remain unrun.",
    "AUDIT-05-B": "Supply exposure timestamps, searched and unknown corpus scope, and owner review of the bounded contamination statement. Retain original-population and exposed-subset sensitivity results; independent provenance review remains absent.",
    "AUDIT-05-S03": "Supply the chronological candidate/feedback ledger with every evaluated alternative and actual access lineage; obtain owner approval of a frozen confirmation/reuse rule and run untouched confirmation or authorized reuse-boundary controls.",
    "TRACE-04-A": "Supply private eligible-channel/candidate coverage, contextual and sampled-negative review, and qualified policy dispositions bound to exact material digest, recipients and use. Independent labels/release audit remain absent; never publish sensitive spans.",
    "ENV-03-S01": "Obtain owner disposition of isolated/shared surfaces (including caches, credentials, clock, queues and longitudinal exceptions). Complete predecessor/order/session comparisons and independent parallel/delayed-effect probes; stochastic repeats only if deterministic residuals require them.",
    "CREATE-02": "Qualify cue relevance, unchanged requirements, feasibility, scorer and valid alternatives. Run paired cue-only, nuisance-swap, misleading-cue and style-only controls with per-class transitions/uncertainty; independent held-out counterexamples remain absent.",
    "CREATE-02-SUITE": "Select independently authored new task families and run the task-level cue controls on each; cross-family comparison remains absent.",
}


def canonical_definitions() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in SOURCES:
        for item in json.loads((REFERENCES / (name + ".json")).read_bytes())["items"]:
            if item["id"] in result:
                raise ValueError("duplicate canonical criterion")
            result[item["id"]] = item
    return result


SUITE_REASON = (
    "Suite-level criterion: it needs evidence from other tasks, task families or selection history, "
    "so it is not scored for a single task."
)


def level(definition: dict[str, Any]) -> str:
    return definition.get("level", "task")


def task_definitions() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in canonical_definitions().items() if level(v) == "task"}


def suite_definitions() -> dict[str, dict[str, Any]]:
    return {k: v for k, v in canonical_definitions().items() if level(v) == "suite"}


def observed_score(row: dict[str, Any]) -> Any:
    """Task rows: the task score. Suite rows: the score this task's evidence supports for the suite."""
    if row.get("level") == "suite":
        return (row.get("suite_evidence") or {}).get("score")
    return row["score"]


def apply_levels(report: dict[str, Any]) -> dict[str, Any]:
    """Score only task criteria for one evaluation; keep suite rows' observations as suite evidence."""
    for ident, definition in canonical_definitions().items():
        row = report.get("items", {}).get(ident)
        if row is None:
            continue
        row["level"] = level(definition)
        if row["level"] != "suite" or (row["assessment_state"] == "NOT_APPLICABLE" and row["score"] is None):
            continue
        row["suite_evidence"] = {
            key: deepcopy(row.get(key))
            for key in ("score", "evidence_strength", "findings", "evidence_refs", "remaining_gaps")
        }
        row.update(
            score=None,
            status="NOT_APPLICABLE",
            assessment_state="NOT_APPLICABLE",
            execution_state="NOT_APPLICABLE",
            evidence_strength=None,
            applicability_reason=SUITE_REASON,
            remaining_gaps=[GAPS[ident]],
            next_action="Assess this criterion for the suite, not for a single task.",
        )
        for check in row["check_results"]:
            check.update(outcome="NOT_APPLICABLE", remaining_gap=None)
    return report


def definitions_digest() -> str:
    names = [name + suffix for name in SOURCES for suffix in (".json", ".md")] + ["anti-cheat.md"]
    value = {name: hashlib.sha256((REFERENCES / name).read_bytes()).hexdigest() for name in names}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inventory() -> dict[str, Any]:
    definitions = canonical_definitions()
    return {
        "schema": "rubric-current-inventory-v1",
        "definitions_sha256": definitions_digest(),
        "criteria": definitions,
        "legacy_crosswalk": deepcopy(LEGACY_CROSSWALK),
        "criterion_count": len(definitions),
        "check_count": sum(len(d["checks"]) for d in definitions.values()),
        "numeric_cells": sum(len(d["scoring"]) for d in definitions.values()),
        "missing_evidence_cells": len(definitions),
        "undefined": ["METHOD-J1"],
        "acceptance": [
            {
                "criterion": ident,
                "check": check["id"],
                "method": check["method"],
                "required_evidence": check["expected_evidence"],
                "required_action": check["action"],
                "state": "final_execution_or_prerequisite_pending",
                "remaining": GAPS.get(
                    ident,
                    "Retain owning current evidence and final-stage assessment/replay; no historical qualification transfer.",
                ),
            }
            for ident, definition in definitions.items()
            for check in definition["checks"]
        ],
        "count_basis": "11 provenance + 3 contract + 9 leakage (7 task-level, 2 suite-only); four overlapping legacy scoring rows replaced, not added. METHOD-J1 has no defined checks/anchors and is excluded from numeric denominators.",
    }


def _row(definition: dict[str, Any]) -> dict[str, Any]:
    ident = definition["id"]
    return {
        "item_id": ident,
        "score": None,
        "status": "NOT_ASSESSED",
        "assessment_state": "NOT_ASSESSED",
        "execution_state": "NOT_RUN",
        "evidence_strength": None,
        "source": definition.get("source"),
        "evidence_refs": [],
        "findings": [],
        "remaining_gaps": [GAPS[ident]],
        "limitations": [],
        "legacy_source_ids": [key for key, ids in LEGACY_CROSSWALK.items() if ident in ids],
        "check_results": [
            {
                **deepcopy(c),
                "check_id": c["id"],
                "outcome": "NOT_RUN",
                "evidence_refs": [],
                "remaining_gap": "Absent or unrun: " + c["expected_evidence"],
                "next_action": c["action"],
            }
            for c in definition["checks"]
        ],
    }


def _observe(row: dict[str, Any], suffix: str, outcome: str, refs: list[dict[str, Any]], reason: str) -> None:
    check = next(c for c in row["check_results"] if c["id"] == row["item_id"] + "-" + suffix)
    check.update(outcome=outcome, evidence_refs=deepcopy(refs), remaining_gap=reason)
    for ref in refs:
        if ref not in row["evidence_refs"]:
            row["evidence_refs"].append(deepcopy(ref))


def _score(row: dict[str, Any], score: int, strength: str, reason: str) -> None:
    row.update(
        score=score,
        status="ASSESSED",
        assessment_state="ASSESSED",
        execution_state="PARTIAL",
        evidence_strength=strength,
    )
    row["findings"].append(reason)


def source_observations(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    from rubric_external import check_external
    from rubric_identity import identify
    from rubric_layout import label_task
    from rubric_leakage import scan_task

    task_id = selection.get("example_assessment", {}).get("task_config_id")
    records = selection.get("artifacts", [])
    task = next((a for a in records if a.get("id") == task_id), None)
    if not task or task.get("access") != "available":
        return {}
    path = Path(task["path"]).parent.as_posix()
    layout = label_task(root, {"task": path})
    required = {r["path"]: r.get("digest") for r in layout["files"]}
    selected = {a["path"]: a.get("digest") for a in records if a.get("access") == "available"}
    if not required or any(selected.get(p) != d for p, d in required.items()):
        return {}
    if identify(root, records)["status"] != "pass":
        return {}
    # Explicit allowed overlaps belong to the owning scanner, not this adapter.
    leakage = selection.get("leakage", {"task": path})
    return {"layout": layout, "leakage": scan_task(root, leakage), "external": check_external(root, {"task": path})}


def _number(value: Any) -> bool:
    return type(value) in (int, float)


def reconcile(report: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(report)
    legacy = {k: result["items"].pop(k) for k in LEGACY_CROSSWALK if k in result["items"]}
    if "legacy_evidence_items" in result:
        raise ValueError("current-scope projection applied twice")
    result["legacy_evidence_items"] = legacy
    if "anti_cheat_scores" in result:
        result["legacy_anti_cheat_scores"] = result.pop("anti_cheat_scores")
    measurements = result.setdefault("measurements", {})
    observed = {
        key: value
        for key, value in measurements.items()
        if key in ("layout", "leakage", "overlap", "external", "reuse")
    }
    observed.update(sources)
    proof: dict[str, Any] = {
        "status": "unknown",
        "sources": observed,
        "shared_controls": [],
        "operational_errors": 0,
        "boundary": "Owning measurements only; no score mapping, transferred approval, training-cleanliness or independence claim.",
    }
    measurements["leakage_current"] = proof
    definitions = canonical_definitions()
    rows = {ident: _row(definitions[ident]) for ident in GAPS}
    for ident in rows:
        if ident in result["items"]:
            raise ValueError("duplicate current criterion emitter")
    links = [{"measurement": "leakage_current.sources." + key} for key in observed]
    inventory_present = bool(
        observed.get("layout", {}).get("files") or observed.get("leakage", {}).get("counts", {}).get("public_files")
    )
    if inventory_present:
        row = rows["AUDIT-05-B"]
        _observe(
            row,
            "C01",
            "PARTIAL",
            links,
            "Local inventory inspected; exposure chronology, corpus searches and training provenance are not established.",
        )
        _score(
            row,
            1,
            "E1",
            "Inspectable local exposure inventory; known broader provenance gaps are not yet bounded by a reviewed statement.",
        )
    if observed.get("leakage"):
        _observe(
            rows["CREATE-02"],
            "C01",
            "PARTIAL",
            links,
            "Local content/filename candidates only; no control admissibility, isolated cue effect or semantic zero inferred.",
        )
    if observed.get("reuse") or observed.get("overlap"):
        _observe(
            rows["AUDIT-05-S03"],
            "C01",
            "PARTIAL",
            links,
            "Declared split/reuse measurements do not reconcile selection history or establish untouched confirmation.",
        )
    native = measurements.get("example_assessment", {}).get("native") or {}
    panels = native.get("panels", [native])
    attack_count = 0
    reset_count = 0
    excess = False
    shared: set[tuple[str, str]] = set()
    for panel in panels:
        if panel.get("native_execution") is not True or panel.get("task_freshness") != "current":
            continue
        scope = panel.get("scope")
        if scope not in {
            "eval-author.refund-attack-panel.v1",
            "eval-author.refund-boundary-panel.v1",
            "eval-author.refund-access-panel.v1",
        }:
            continue
        for control in panel.get("controls", []):
            identity = (str(panel.get("index_digest", scope)), control["case"])
            if identity in shared:
                continue
            shared.add(identity)
            ref = {
                "measurement": "example_assessment.native",
                "digest": panel.get("index_digest"),
                "case": control["case"],
            }
            proof["shared_controls"].append(
                {"identity": list(identity), "source": ref, "status": control.get("status"), "scope": scope}
            )
            if control.get("status") == "infrastructure_error":
                proof["operational_errors"] += 1
                continue
            checks, expected = control.get("checks", {}), control.get("expected", {})
            if scope == "eval-author.refund-attack-panel.v1" and checks.get("health") == "ok":
                actual_checks = checks.get("checks", {})
                expected_checks = expected.get("checks", {})
                if (
                    not actual_checks
                    or actual_checks.keys() != expected_checks.keys()
                    or not all(
                        _number(v)
                        for v in [
                            *actual_checks.values(),
                            *expected_checks.values(),
                            checks.get("total_credit"),
                            expected.get("total_credit"),
                        ]
                    )
                ):
                    continue
                attack_count += 1
                forbidden = checks["total_credit"] > expected["total_credit"] or any(
                    actual_checks[k] > v for k, v in expected_checks.items()
                )
                excess |= forbidden
            elif scope == "eval-author.refund-boundary-panel.v1":
                if control["case"].startswith(("target-", "sentinel-")) and control.get("status") == "passed":
                    reset_count += 1
                elif (
                    control["case"] in ("forged-text", "forged-json", "wrong-task")
                    and checks.get("health") == "ok"
                    and _number(control.get("reward"))
                    and _number(checks.get("max_credit"))
                    and checks["max_credit"] > 0
                ):
                    attack_count += 1
                    excess |= control["reward"] > checks["total_credit"] / checks["max_credit"]
    refs = [{"measurement": "leakage_current.shared_controls"}]
    if attack_count:
        row = rows["AUDIT-03"]
        reason = (
            "Healthy native controls grant forbidden per-check or total credit."
            if excess
            else "Native attack/consumer controls inspected; canonical human applicability approval and complete combined attack classes remain unresolved."
        )
        _score(row, 0 if excess else 1, "E2", reason)
        _observe(row, "C02", "FAIL" if excess else "PARTIAL", refs, reason)
        _observe(row, "C03", "FAIL" if excess else "PARTIAL", refs, reason)
    if reset_count:
        row = rows["ENV-03-S01"]
        reason = "Native reset/sentinel observations partly verify isolation; full canonical surface declarations, order, delayed-effect and independent parallel coverage remain unresolved."
        _score(row, 1, "E2", reason)
        _observe(row, "C02", "PARTIAL", refs, reason)
        _observe(row, "C03", "PARTIAL", refs, reason)
    inspection = (
        measurements.get("refund_contract", {}).get("task-contract.scoring-authority", {}).get("source_inspection", {})
    )
    if inspection.get("variant") in {"completion-only", "approval-after-effect", "ledger-omission"}:
        bypasses = []
        for name, control in inspection.get("controls", {}).items():
            actual, expected = control["selected"], control["authority_counterpart"]
            if actual.get("health") != "ok" or expected.get("health") != "ok":
                continue
            # Compare each arm on its own declared scale. A source-bound accepted
            # result against a rejected mandatory-gate counterpart is the bypass,
            # not an arbitrary score difference or optional partial credit.
            if actual.get("accepted") is True and expected.get("accepted") is False:
                bypasses.append(name)
        if bypasses:
            row = rows["AUDIT-03"]
            reason = (
                "Current owning finite-source reconstruction demonstrates a reachable mandatory-gate bypass in "
                + ", ".join(bypasses)
                + ". Local evidence only; native controls and human applicability review remain unrun."
            )
            _score(row, 0, "E2" if row["score"] == 0 and row["evidence_strength"] == "E2" else "E1", reason)
            _observe(
                row,
                "C03",
                "FAIL",
                [{"measurement": "refund_contract.task-contract.scoring-authority.source_inspection"}],
                reason,
            )
    # This subject was admitted and reconstructed by the owning native hook
    # before this projection; its canonical predicate is not the AC numeric row.
    mutation = measurements.get("native_numeric_mutations", {})
    if mutation.get("case") == "persistent-state":
        controls = mutation["controls"]
        alone, prior, after = [
            controls["persistent-state/" + name] for name in ("target-alone", "predecessor", "target-after")
        ]
        if [alone["reward"], prior["reward"], after["reward"]] != [0, 1, 1] or len(
            {r["native_run_id"] for r in (alone, prior, after)}
        ) != 3:
            raise ValueError("native reset control predicate differs")
        row = rows["ENV-03-S01"]
        reason = "Owning native admission verifies exact unintended carried state and ordered distinct predecessor/target trials: unchanged empty target earns 0 alone and 1 after predecessor."
        _score(row, 0, "E2", reason)
        _observe(row, "C02", "FAIL", [{"measurement": "native_numeric_mutations"}], reason)
        _observe(row, "C03", "FAIL", [{"measurement": "native_numeric_mutations"}], reason)
    result["items"].update(rows)
    result["items"] = {k: result["items"][k] for k in definitions if k in result["items"]}
    result["current_inventory"] = {k: v for k, v in inventory().items() if k not in {"criteria", "acceptance"}}
    result["undefined_criteria"] = {
        "METHOD-J1": {
            "score": None,
            "status": "NOT_ASSESSED",
            "reason": "Missing canonical definition, checks and anchors; request upstream judge trust-boundary definition before assessment.",
        }
    }
    return result


def assess_current(root: Path, selection: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from rubric_identity import identify

    if not ({"example_assessment", "layout", "leakage", "overlap", "external", "reuse"} & selection.keys()):
        return report
    before = identify(root, selection.get("artifacts", []))
    sources = source_observations(root, selection)
    result = reconcile(report, sources)
    from rubric_leakage_controls import collect

    projection = collect(root, selection)
    if projection is not None:
        apply_bounded(result, projection, root=root, selection=selection)
        if collect(root, selection) != projection:
            raise ValueError("bounded leakage observations changed during projection")
    if source_observations(root, selection) != sources or identify(root, selection.get("artifacts", [])) != before:
        raise ValueError("current-scope evidence changed during projection")
    return apply_levels(result)


def apply_bounded(report: dict[str, Any], projection: dict[str, Any], *, root: Path, selection: dict[str, Any]) -> None:
    from rubric_leakage_controls import digest, forbidden
    from rubric_leakage_intake import approved, read, request

    ident = projection["criterion"]
    report["measurements"]["leakage_bounded"] = deepcopy(projection)
    row = report["items"][ident]
    row["assessment_scope"] = "synthetic-local-refund-controls"
    row["limitations"].extend(
        [
            "Inert synthetic fixture; effects recomputed through current guarded local code, not native/model evidence.",
            "Independent upper-anchor methods need fresh review and separately collected corroboration; score 3 is not implemented by this local panel.",
        ]
    )
    if projection["observations"] is None:
        # Withholding this panel cannot erase an independently demonstrated zero.
        if row["score"] != 0:
            row.update(score=None, status="NOT_ASSESSED", assessment_state="NOT_ASSESSED", evidence_strength=None)
        row["execution_state"] = projection["execution_state"]
        row["remaining_gaps"] = [
            projection.get(
                "missing_reason",
                "Executed operational fault; no healthy control result. Not a successful defense or numeric zero.",
            )
        ]
        return
    o = projection["observations"]
    owner = read(projection, root=root, selection=selection)
    agent = read(projection, root=root, selection=selection, kind="agent") if ident == "CREATE-02" else None
    authority = approved(owner, ident)
    qualified = authority and (ident != "CREATE-02" or approved(agent, ident))
    report["measurements"]["leakage_bounded"]["reviews"] = {"owner": owner, "agent": agent}
    report["measurements"]["leakage_bounded"]["pending_request"] = None if qualified else request(projection)
    refs = [{"measurement": "leakage_bounded", "digest": digest(projection["binding"])}]
    checks: dict[str, tuple[str, str]] = {}
    score: int | None = None
    reason = ""
    if ident == "AUDIT-03":
        probes = list(o["probes"].values())
        healthy = all(p["health"] == "ok" for p in probes)
        defect = any(forbidden(p) for p in probes)
        complete = (
            o["access_class"] != "unresolved"
            and healthy
            and all(p["health"] == "error" for p in o["parser_rejections"].values())
        )
        score = 0 if defect else 2 if complete and authority else 1
        reason = (
            "Healthy mandatory-gate bypass."
            if defect
            else "Local event API controls compared; native-access exclusions need scoped disposition."
        )
        checks = {
            "C01": ("PASS" if authority else "NOT_RUN", "Actual scoped attack/exclusion review required."),
            "C02": ("PASS" if complete and authority else "PARTIAL", o["scope"]),
            "C03": (
                "FAIL" if defect else "PASS" if complete else "PARTIAL",
                "Per-check and total credit recomputed; parser errors are not defenses.",
            ),
        }
    elif ident == "AUDIT-04-B":
        confirmed = forbidden(o["confirmed"])
        repair = o["repairs"]
        defect = any(forbidden(r["mechanism"]) for r in repair.values())
        complete = set(repair) == {"retained", "new"} and all(
            r["mechanism"]["health"] == r["valid"]["health"] == "ok"
            and r["valid"]["actual"]["accepted"]
            and not forbidden(r["mechanism"])
            for r in repair.values()
        )
        score = (0 if defect else 2 if complete and authority else 1) if confirmed else None
        reason = (
            "Healthy retained repair still credits the exploit."
            if defect
            else "Confirmed affected revision retained with new-instance repair; quarantine/bounded-use decision is separate."
        )
        checks = {
            "C01": (
                "PASS" if authority else "NOT_RUN",
                "Containment disposition for exact affected revision required.",
            ),
            "C02": (
                "FAIL" if defect else "PASS" if complete else "PARTIAL",
                "Retained affected AND new-task attack and valid-work results.",
            ),
            "C03": (
                "FAIL" if defect else "PASS" if complete and authority else "PARTIAL",
                "Mechanism, affected revision, disposition and repair coverage reconciled.",
            ),
        }
    elif ident == "AUDIT-05-B":
        overlap = any(r["matching_bytes"] for r in o["inventory"])
        contradiction = overlap and o["statement"] == "no-answer-exposure"
        bounded = bool(o["unknown"]) and not contradiction
        score = 0 if contradiction and authority else 2 if bounded and authority else 1
        reason = (
            "Exact copied-answer exposure contradicts the reviewed cleanliness statement."
            if contradiction and authority
            else "Finite exposure inventory and original/excluded-subset sensitivity retained; training provenance unknown."
        )
        checks = {
            "C01": (
                "PASS" if bounded else "PARTIAL",
                "Synthetic logical exposure time and exact corpus match, not historical publication evidence.",
            ),
            "C02": (
                "FAIL" if contradiction and authority else "PASS" if bounded and authority else "NOT_RUN",
                "Owner must disposition the exact statement and unknown scope.",
            ),
            "C03": ("PASS", "Original population and predeclared exposure-excluded outcomes recomputed."),
        }
    elif ident == "AUDIT-05-S03":
        complete = o["execution_log"] == o["decision_ledger"]
        freeze = next(r["sequence"] for r in o["execution_log"] if r["kind"] == "freeze")
        violation = (
            any(
                r["kind"] == "access" and r["partition"] == "confirmation" and r["sequence"] < freeze
                for r in o["execution_log"]
            )
            and o["claim"] == "untouched-confirmation"
        )
        healthy = o["confirmation"]["health"] == "ok"
        score = 0 if violation else 2 if complete and healthy and authority else 1
        reason = (
            "Logged development access contradicts untouched confirmation."
            if violation
            else "Actual scripted alternatives and feedback reconciled against declared chronology; new-instance confirmation is same-family only."
        )
        checks = {
            "C01": (
                "FAIL" if violation else "PASS" if complete else "PARTIAL",
                "Retain all evaluated alternatives, feedback and access lineage.",
            ),
            "C02": (
                "PASS" if authority and not violation else "NOT_RUN",
                "Exact bounded same-family rule needs owner approval; no independent holdout claim.",
            ),
            "C03": (
                "FAIL" if violation else "PASS" if healthy else "ERROR",
                "Current unchanged scorer executes post-freeze confirmation.",
            ),
        }
    elif ident == "TRACE-04-A":
        coverage = set(o["eligible_channels"]) == set(o["screened_channels"])
        defect = o["handling"] == "released" and o["release_digest"] == o["material_sha256"]
        score = 0 if defect and authority else 2 if coverage and qualified and not defect else 1
        reason = (
            "Exact inert fixture reached a prohibited local sink under reviewed fixture policy."
            if defect and authority
            else "Fake canary/context candidates and sampled negatives are inventoried; contextual review and exact-use disposition remain distinct."
        )
        checks = {
            "C01": (
                "PASS" if coverage else "PARTIAL",
                "No actual sensitive material read; only fixed inert fixture channels.",
            ),
            "C02": (
                "PASS" if authority and coverage else "NOT_RUN",
                "Qualified owner contextual and sampled-negative review required.",
            ),
            "C03": (
                "FAIL" if defect and authority else "PASS" if authority and not defect else "NOT_RUN",
                "Review binds exact material digest, recipients and use; no lexical clearance.",
            ),
        }
    elif ident == "ENV-03-S01":
        observations = o["observations"]
        baseline = next(r for r in observations if r["order"] == "alone")
        persistence = any(
            r["post_reset"] != r["initial"]
            and r["target"]["health"] == "ok"
            and (
                r["target"] != baseline["target"] or r["post_reset"]["candidate"] != baseline["post_reset"]["candidate"]
            )
            for r in observations
        )
        complete = {r["order"] for r in observations} == {
            "alone",
            "after-reference",
            "after-no-work",
            "reverse-order",
        } and all(r["post_reset"] == r["initial"] and r["record_mode"] == 0o600 for r in observations)
        score = 0 if persistence else 2 if complete and authority else 1
        reason = (
            "Unreset predecessor candidate changes target evidence/credit."
            if persistence
            else "Exact local initial/reset state and permissions compared across distinct sessions/orders."
        )
        checks = {
            "C01": (
                "PASS" if authority else "NOT_RUN",
                "Owner must approve absent-surface exclusions for local Ledger only.",
            ),
            "C02": (
                "FAIL" if persistence else "PASS" if complete else "PARTIAL",
                "Candidate, backend rows and record modes compared.",
            ),
            "C03": (
                "FAIL" if persistence else "PASS" if complete and authority else "PARTIAL",
                "Local deterministic controls only; native/concurrency/delayed effects not claimed.",
            ),
            "C04": ("NOT_APPLICABLE" if authority and complete else "NOT_RUN", o["stochastic_residual"]),
        }
    else:
        controls = o["outcomes"]
        witnesses = [r["cue_only_witness"] for r in controls if r["class"] == "cue-only"]
        demonstrated = any(
            w["aligned"]["health"] == w["misleading"]["health"] == "ok"
            and w["aligned"]["candidate_sha256"] != w["misleading"]["candidate_sha256"]
            and w["aligned"]["actual"]["accepted"]
            and not w["misleading"]["actual"]["accepted"]
            and not w["aligned"]["decision"]["evidence_used_for_selection"]
            for w in witnesses
        )
        healthy = all(
            p["health"] == "ok"
            for r in controls
            for p in [r["baseline"], r["intervention"], r["valid_alternative"], *r["cue_only_witness"].values()]
        )
        complete = (
            set(o["class_transitions"]) == {"cue-only", "nuisance-swap", "misleading-cue", "style-only"}
            and healthy
            and demonstrated
            and not o["unresolved_confounds"]
            and all(
                r["valid_alternative"]["actual"]["accepted"]
                and (r["class"] == "cue-only" or r["decisive_evidence_preserved"])
                and r["baseline"]["decision"]["policy"] == r["intervention"]["decision"]["policy"]
                for r in controls
            )
        )
        # Correct cue-selected actions still satisfy the existing action claim.
        # A new evidence-reasoning claim requires separately authorized task scope.
        defect = demonstrated and o["claim_contradiction"]
        score = (
            (0 if defect and qualified else 2 if complete and qualified else 1) if demonstrated and healthy else None
        )
        reason = (
            "Qualified cue success contradicts the declared subject claim."
            if defect and qualified
            else "Scripted cue/evidence selection measured; performed actions are not absence of task work. "
            + o["zero_scope_requirement"]
        )
        checks = {
            "C01": (
                "PASS",
                "Actual artifact fields, consumer branches and finite cue correlations retained; private/stale access excluded, not defended.",
            ),
            "C02": (
                "PASS" if qualified else "NOT_RUN",
                "Fresh agent confound review AND scoped owner acceptance required; historical rejection is not superseded by source edits.",
            ),
            "C03": (
                "FAIL" if defect and qualified else "PASS" if complete and qualified else "PARTIAL",
                "Fixed within-pair policy/requirements/scorer; actual evidence, cue-only flip witnesses and split-refund alternatives. "
                + "; ".join(o["unresolved_confounds"]),
            ),
            "C04": ("PASS", o["uncertainty"]),
        }
        row["limitations"].extend(o["limits"] + o["unresolved_confounds"] + [o["zero_scope_requirement"]])
    for suffix, (outcome, gap) in checks.items():
        _observe(row, suffix, outcome, refs, gap)
    if score is not None:
        # A separate confirmed zero is never erased by a healthy companion.
        _score(row, min(row["score"], score) if row["score"] == 0 else score, "E2", reason)
    row["execution_state"] = "COMPLETED"
    row["remaining_gaps"] = ([] if qualified else [request(projection)["scopes"][ident]]) + [row["limitations"][-1]]
    if read(projection, root=root, selection=selection) != owner or (
        ident == "CREATE-02" and read(projection, root=root, selection=selection, kind="agent") != agent
    ):
        raise ValueError("leakage review changed during projection")


def current_expectations(expected: dict[str, Any]) -> dict[str, Any]:
    """Route external historical targets, never assessment observations.

    AC-01 has no equivalent numeric predicate: retain its original target only
    in legacy_expected. It contributes measurements, not three numeric cells.
    """
    result: dict[str, Any] = {}
    for ident, value in expected.items():
        if ident == "AC-01":
            continue
        target = {"AC-02": "AUDIT-03", "AC-03": "AUDIT-03", "AC-04": "ENV-03-S01"}.get(ident, ident)
        if target in result and (type(result[target]) is not type(value) or result[target] != value):
            raise ValueError("overlapping external targets disagree")
        result[target] = value
    return result
