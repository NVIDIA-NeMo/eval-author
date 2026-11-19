# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Base-only fictional Harbor subject adapter with retained native observations."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ATTRIBUTION = "SUPPLIED_FICTIONAL_HISTORY_WITH_LOCAL_REGRESSIONS"
SUBJECT = "perfect-task/refund"
SUITE = "perfect-suite/refund+interval-reservation+dependency-release"
FAMILIES = ("refund", "interval-reservation", "dependency-release")
RECIPES = {
    "refund": ("no-work-forged-success", "missing-approval", "confirmation-0-fixed-template"),
    "interval-reservation": ("development-1-fixed-template", "cue-3-cue-only-baseline", "cue-4-cue-only-intervention"),
    "dependency-release": ("development-2-fixed-template", "cue-5-cue-only-baseline", "cue-6-cue-only-intervention"),
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def task_identity(sources: dict, family: str = "refund") -> dict:
    prefix = "prior-package/perfect-task/" + ("refund/" if family == "refund" else "companions/" + family + "/")
    return {name.removeprefix(prefix): value for name, value in sources.items() if name.startswith(prefix)}


def containment_packet(evidence: dict, sources: dict) -> dict:
    """Instantiate explicit invented prehistory; current regressions remain measured."""
    by = {row["id"]: row for row in evidence["observations"]}
    affected, regressions = [], []
    for family, recipes in RECIPES.items():
        repaired = task_identity(sources, family)
        ident = "fictional-history/" + family + "/completion-only-v0"
        known = by[recipes[0]]
        affected.append(
            {
                "id": ident,
                "family": family,
                "subject": SUBJECT if family == "refund" else "perfect-task/companions/" + family,
                "mechanism": "The historical completion-only publication credited a success declaration despite failed mandatory work.",
                "affected_revision": {
                    "kind": "SUPPLIED_FICTIONAL_REVISION",
                    "id": ident,
                    "rule": "success flag alone grants full credit",
                },
                "affected_results": [ident + "/accepted-result"],
                "historical_observation": {
                    "attribution": "SUPPLIED_FICTIONAL_OBSERVATION",
                    "candidate": known["candidate"],
                    "case_sha256": digest(known["case"]),
                    "published_credit": known["result"]["max_credit"],
                    "authorized_credit": 0,
                },
                "disposition": "quarantined",
                "accepted_for_use": False,
                "quarantined_results": [ident + "/accepted-result"],
                "repair": {
                    "status": "already-repaired-in-supplied-history",
                    "authorization": "Fictional O authorized mandatory gating before current assessment.",
                    "change": "Require every mandatory predicate before any credit; preserve optional completion credit.",
                    "repaired_task": repaired,
                },
                "residual_risk": "Finite scripted cases only; local regressions are not native exploit or cross-family Harbor trials.",
            }
        )
        for number, name in enumerate(recipes):
            row = by[name]
            regressions.append(
                {
                    "observation": name,
                    "family": family,
                    "affected_id": ident,
                    "cohort": "retained-affected" if number < 2 else "new-task",
                    "attack_role": "known-mechanism" if number == 0 else "independent-attack",
                    "independence": "SUPPLIED_FICTIONAL_A_AUTHORSHIP" if number else "known historical recipe",
                    "execution_scope": "ACTUAL_LOCAL_REGRESSION_NOT_HARBOR",
                    "case_sha256": digest(row["case"]),
                    "candidate_sha256": digest(row["candidate"]),
                    "repaired_task": repaired,
                    "result": copy.deepcopy(row["result"]),
                    "authorized_credit": 0,
                }
            )
    return {
        "attribution": ATTRIBUTION,
        "subject": SUBJECT,
        "suite": SUITE,
        "notice": "Invented affected revisions, historical credit, quarantine, prior authorization/repair and independent authorship. No new repair stage or historical native claim. Regression results are current local executions on bound task/scorer sources.",
        "affected": affected,
        "regressions": regressions,
        "unresolved": [],
        "source_sha256": digest(sources),
    }


def current_exploits(evidence: dict, suite: bool) -> list[str]:
    return [
        row["id"]
        for row in evidence["observations"]
        if (suite or row["family"] == "refund")
        and "oracle" in row
        and row["result"]["health"] == "ok"
        and row["result"]["total_credit"] > row["oracle"]["total_credit"]
    ]


def containment_checks(packet: dict, evidence: dict, sources: dict, suite: bool) -> tuple[bool, bool, bool]:
    try:
        # Historical quarantine cannot contain an exploit of the current revision.
        if current_exploits(evidence, suite):
            return False, False, False
        families = set(FAMILIES if suite else ("refund",))
        header = (
            packet["attribution"] == ATTRIBUTION
            and packet["subject"] == SUBJECT
            and packet["suite"] == SUITE
            and packet["source_sha256"] == digest(sources)
        )
        affected = [row for row in packet["affected"] if row["family"] in families]
        scope = {row["family"] for row in affected} == families and len(affected) == len(families)
        decisions = (
            header
            and scope
            and all(
                row["disposition"] == "quarantined"
                and row["accepted_for_use"] is False
                and row["subject"]
                == (SUBJECT if row["family"] == "refund" else "perfect-task/companions/" + row["family"])
                and row["affected_results"]
                and row["quarantined_results"] == row["affected_results"]
                and row["historical_observation"]["attribution"] == "SUPPLIED_FICTIONAL_OBSERVATION"
                and row["historical_observation"]["published_credit"]
                > row["historical_observation"]["authorized_credit"]
                and row["affected_revision"]["kind"] == "SUPPLIED_FICTIONAL_REVISION"
                and row["repair"]["status"] == "already-repaired-in-supplied-history"
                and row["repair"]["authorization"]
                and row["residual_risk"]
                and row["repair"]["repaired_task"] == task_identity(sources, row["family"])
                for row in affected
            )
        )
        by = {row["id"]: row for row in evidence["observations"]}
        affected_by_family = {row["family"]: row for row in affected}
        lineage = scope
        for row in affected:
            known = by[RECIPES[row["family"]][0]]
            history = row["historical_observation"]
            lineage = lineage and (
                row["affected_revision"]["id"] == row["id"]
                and row["affected_revision"]["rule"] == "success flag alone grants full credit"
                and known["family"] == row["family"]
                and history["candidate"] == known["candidate"]
                and history["candidate"]["success"] is True
                and history["case_sha256"] == digest(known["case"])
                and history["published_credit"] == known["result"]["max_credit"]
                and history["authorized_credit"] == 0
            )
        decisions = decisions and lineage
        regressions = [row for row in packet["regressions"] if row["family"] in families]
        expected = {name for family in families for name in RECIPES[family]}
        replay = (
            lineage and len(regressions) == len(expected) and {row["observation"] for row in regressions} == expected
        )
        for row in regressions:
            original = by[row["observation"]]
            recipes = RECIPES[row["family"]]
            replay = replay and (
                row["result"] == original["result"]
                and row["family"] == original["family"]
                and row["observation"] in recipes
                and row["attack_role"]
                == ("known-mechanism" if row["observation"] == recipes[0] else "independent-attack")
                and row["cohort"] == ("new-task" if row["observation"] == recipes[2] else "retained-affected")
                and row["case_sha256"] == digest(original["case"])
                and row["candidate_sha256"] == digest(original["candidate"])
                and row["repaired_task"] == task_identity(sources, row["family"])
                and row["affected_id"] == affected_by_family[row["family"]]["affected_revision"]["id"]
                and row["result"]["health"] == "ok"
                and row["result"]["accepted"] is False
                and row["result"]["total_credit"] == row["authorized_credit"] == 0
                and row["execution_scope"] == "ACTUAL_LOCAL_REGRESSION_NOT_HARBOR"
            )
        for family in families:
            selected = [r for r in regressions if r["family"] == family]
            replay = replay and {r["cohort"] for r in selected} == {"retained-affected", "new-task"}
            replay = replay and any(
                r["attack_role"] == "independent-attack" and r["independence"] == "SUPPLIED_FICTIONAL_A_AUTHORSHIP"
                for r in selected
            )
        reconciled = decisions and replay and packet["unresolved"] == []
        return bool(decisions), bool(replay), bool(reconciled)
    except (KeyError, TypeError, ValueError):
        return False, False, False


def native_packet(root: Path, sources: dict) -> dict:
    native = root / "harbor-input"
    index = json.loads((native / "native-index.json").read_bytes())
    for record in index["files"]:
        path = native / record["path"]
        if (
            not path.resolve().is_relative_to(native.resolve())
            or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
            or path.stat().st_mode & 0o777 != record["mode"]
        ):
            raise ValueError("retained Harbor identity mismatch")
    task = {
        r["path"].removeprefix("task/"): {"sha256": r["sha256"], "mode": r["mode"]}
        for r in index["files"]
        if r["path"].startswith("task/")
    }
    if len(task) != 11 or task != task_identity(sources):
        raise ValueError("Harbor task bytes or modes differ from scenario")
    validation = json.loads((native / "validation.json").read_bytes())
    controls = {}
    for case in ("oracle", "nop"):
        controls[case] = {
            key: json.loads((native / path).read_bytes())
            for key, path in {
                "result": f"trials/{case}/result.json",
                "config": f"trials/{case}/config.json",
                "checks": f"trials/{case}/verifier/checks.json",
                "receipt": f"receipts/{case}.json",
            }.items()
        }
        controls[case]["reward_text"] = float((native / f"trials/{case}/verifier/reward.txt").read_text())
    return {
        "attribution": "ACTUAL_RETAINED_HARBOR_ORACLE_NOP",
        "subject": SUBJECT,
        "task": task,
        "raw_documents": {
            f"trials/{case}/{name}": (native / f"trials/{case}/{name}").read_text()
            for case in ("oracle", "nop")
            for name in ("config.json", "result.json", "verifier/checks.json")
        },
        "raw_receipts": {case: (native / f"receipts/{case}.json").read_text() for case in ("oracle", "nop")},
        "validation": validation,
        "controls": controls,
        "index_sha256": hashlib.sha256((native / "native-index.json").read_bytes()).hexdigest(),
        "scope": "Owning registry admission and validate_pair passed before copy. Original task/config/result/verifier bytes retained. No new native trial; no native access, isolation, repair or suite proof.",
    }


def native_checks(packet: dict, sources: dict) -> bool:
    try:
        if (
            packet["attribution"] != "ACTUAL_RETAINED_HARBOR_ORACLE_NOP"
            or packet["subject"] != SUBJECT
            or packet["task"] != task_identity(sources)
            or len(packet["task"]) != 11
        ):
            return False
        validation = packet["validation"]
        if validation["summary"]["counts"] != {"attempts": 2, "completed": 2, "infrastructure_errors": 0}:
            return False
        ids = set()
        for case, accepted, credit in (("oracle", True, 9), ("nop", False, 0)):
            row = packet["controls"][case]
            result, receipt, checks = row["result"], row["receipt"], row["checks"]
            receipt_raw = packet["raw_receipts"][case]
            if (
                json.loads(receipt_raw) != receipt
                or hashlib.sha256(receipt_raw.encode()).hexdigest()
                != sources[f"harbor-input/receipts/{case}.json"]["sha256"]
            ):
                return False
            summary = next(c for c in validation["summary"]["controls"] if c["case"] == case)
            for key, name in (("config", "config.json"), ("result", "result.json"), ("checks", "verifier/checks.json")):
                path = f"trials/{case}/{name}"
                raw = packet["raw_documents"][path]
                if (
                    json.loads(raw) != row[key]
                    or hashlib.sha256(raw.encode()).hexdigest() != sources["harbor-input/" + path]["sha256"]
                ):
                    return False
            if not (
                result["exception_info"] is None
                and receipt["status"] == "passed"
                and receipt["returncode"] == 0
                and receipt["native_run_id"] == result["id"]
                and result["config"]["agent"]["name"] == case
                and result["config"]["environment"]["type"] == "docker"
                and checks == summary["checks"]
                and checks["health"] == "ok"
                and checks["accepted"] is accepted
                and checks["total_credit"] == credit
                and checks["max_credit"] == 9
                and result["verifier_result"]["rewards"]["reward"] == row["reward_text"] == credit / 9
            ):
                return False
            ids.add(result["id"])
        return len(ids) == 2 and validation["provider_validation"]["valid"]["status"] == "accepted"
    except (KeyError, TypeError, ValueError, StopIteration):
        return False


def apply(report: dict, evidence: dict, premises: dict, sources: dict, accepted_review, binding_valid: bool) -> dict:
    native = evidence["harbor"]
    native_ok = binding_valid and native_checks(native, sources)
    review_ok = accepted_review(premises.get("reviews", {}).get("harbor_base"), "harbor_base", evidence, sources)
    packet = evidence["containment"]
    for ident, row in report["items"].items():
        if ident in ("AUDIT-04-B", "AUDIT-04-B-SUITE"):
            suite = ident.endswith("SUITE")
            confirmed = current_exploits(evidence, suite)
            predicates = containment_checks(packet, evidence, sources, suite=suite)
            review = accepted_review(premises.get("reviews", {}).get("containment"), "containment", evidence, sources)
            finding = (
                "Current confirmed forbidden-credit mechanisms lack current containment: " + repr(confirmed)
                if confirmed
                else "Fictional history, quarantine and prior repair checked against current local regression lineage; independent authorship is supplied fiction."
            )
            for check, ok in zip(row["check_results"], predicates, strict=True):
                check.update(
                    outcome="PASS" if ok and review and native_ok else "FAIL",
                    actual_measurement_predicate=ok,
                    premise_present=review,
                    interpretation=check["interpretation"] + " " + finding,
                    attribution="MIXED_LOCAL_REGRESSIONS_AND_FICTIONAL_HISTORY",
                    evidence_refs=check["evidence_refs"]
                    + [
                        {"path": "containment.json", "pointer": "", "attribution": ATTRIBUTION},
                        {
                            "path": "bound-premises.json",
                            "pointer": "/reviews/containment",
                            "attribution": "SUPPLIED_FICTIONAL_PREMISE",
                        },
                    ],
                )
                check.pop("applicability_reason", None)
            passed = all(predicates) and review and native_ok and review_ok
            row.update(
                score=3 if passed else None,
                assessment_state="ASSESSED" if passed else "NOT_ASSESSED",
                status="ASSESSED" if passed else "NOT_ASSESSED",
                applicability_reason=None,
                remaining_gaps=[]
                if passed
                else [
                    finding
                    if confirmed
                    else "Containment history, affected/repaired lineage, regression reconciliation or required fictional review failed."
                ],
                findings=[finding, packet["notice"], report["notice"]],
                top_anchor_predicate={"satisfied": passed, "interpretation": row["canonical_anchors"]["3"]},
            )
        refs = [
            {"path": "harbor.json", "pointer": "", "attribution": native["attribution"]},
            {
                "path": "bound-premises.json",
                "pointer": "/reviews/harbor_base",
                "attribution": "SUPPLIED_FICTIONAL_PREMISE",
            },
        ]
        for check in row["check_results"]:
            check["evidence_refs"].extend(copy.deepcopy(refs))
            check["subject_scope"] = (
                "Exact Harbor task; local controls supplemental. Native unmeasured runtime/suite/review facts are supplied fiction."
            )
            if ident not in ("AUDIT-04-B", "AUDIT-04-B-SUITE"):
                check["interpretation"] = (
                    "Supplemental local-control finding, not native evidence: " + check["interpretation"]
                )
        row["evidence_refs"] = [r for c in row["check_results"] for r in c["evidence_refs"]]
        row["evidence_strength"] = (
            "Actual retained Harbor Oracle/NOP + actual local regressions + explicit fictional companions"
        )
        row["findings"].insert(
            0,
            "Exact Harbor Oracle/NOP task with supplied fictional runtime, review and suite premises. Local controls are supplemental. "
            + row["findings"][0],
        )
        if not native_ok or not review_ok:
            row.update(score=None, assessment_state="NOT_ASSESSED", status="NOT_ASSESSED")
            row["top_anchor_predicate"]["satisfied"] = False
            row["remaining_gaps"].append(
                "Actual Harbor receipt/task binding or fictional Harbor subject review failed."
            )
    report["native_harbor_run"] = True
    report["native_execution_scope"] = native["scope"]
    report["measurements"]["actual_retained_harbor"] = native
    report["assessment_adapter"] += (
        " Base-only Harbor subject adapter; local controls remain supplemental, historical containment is fictional."
    )
    return report
