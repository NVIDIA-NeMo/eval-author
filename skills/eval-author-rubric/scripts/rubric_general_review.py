# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scoped local-operator Environment decisions; no transcript authentication."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from rubric_review_intake import _digest, prepare_task, read_selected
from rubric_source_reviews import OPERATOR_BOUNDARY, _operator_root, loads

SCOPES = {
    "provenance.solver-tool-access": "solver_actions",
    "provenance.external-state": "external_state",
}
BOUNDARY = (
    "Historical task intent applies only to the listed current source conditions. "
    "It approves neither all runtime behavior nor independent corroboration, "
    "task-contract authority, fairness or source-review alternatives. "
    "Current automatic evidence is required separately; no automatic level 3."
)


def _require(value: Any) -> None:
    if not value:
        raise ValueError("invalid scoped Environment review")


def prepare(task: Path, *, kind: str = "environment", comparison=None) -> dict[str, Any]:
    intake = prepare_task(task)
    if kind != "environment":
        _require(kind in {"contract", "fairness"})
        if kind == "contract":
            from rubric_contract_positive import crosswalk as current_crosswalk

            scopes = {key: "contract_crosswalk" for key in intake["questions"]["contract_crosswalk"]["criteria"]}
            evidence = {"crosswalk": current_crosswalk(task)}
            boundary = "Owner adjudication of this exact public/enforcement crosswalk only. Independent corroboration, blind discovery and level 3 remain separate."
        else:
            _require(isinstance(comparison, dict) and comparison.get("model_study") is False)
            scopes = {"provenance.comparison-fairness": "scripted_policy_variant"}
            evidence = {"comparison": deepcopy(comparison)}
            boundary = "Only scripted reference versus split refunds on the matched task/scorer/permissions. No model performance or matched sensitivity claim."
        request = {
            "schema": "rubric-" + kind + "-review-request-v1",
            "sources": intake["sources"],
            "definitions": intake["definitions"],
            "scopes": scopes,
            "boundary": boundary,
            **evidence,
        }
        request["binding"] = _digest(request)
        return request
    crosswalk = {row["check"]: row for row in intake["crosswalk"]}
    request = {
        "schema": "rubric-environment-review-request-v1",
        "sources": intake["sources"],
        "definitions": intake["definitions"],
        "scopes": {key: crosswalk[check] for key, check in SCOPES.items()},
        "boundary": BOUNDARY,
    }
    request["binding"] = _digest(request)
    return request


def _event(request: dict[str, Any], value: Any) -> dict[str, Any]:
    _require(
        isinstance(value, dict)
        and set(value) == {"request", "decisions", "retention_consent", "retained_scopes", "save_authorized"}
    )
    _require(value["request"] == request and value["save_authorized"] is True)
    _require(value["retained_scopes"] == list(request["scopes"]))
    _require(isinstance(value["retention_consent"], str) and value["retention_consent"].strip())
    _require(isinstance(value["decisions"], dict) and set(value["decisions"]) == set(request["scopes"]))
    for decision in value["decisions"].values():
        _require(isinstance(decision, dict) and set(decision) == {"decision", "content", "rationale"})
        _require(decision["decision"] in {"approve", "reject", "defer"})
        _require(all(isinstance(decision[k], str) and decision[k].strip() for k in ("content", "rationale")))
    return deepcopy(value)


def _target(task: Path, *, kind="environment", comparison=None) -> tuple[Path, dict[str, Any]]:
    root = _operator_root()
    _require(root is not None)
    request = prepare(task, kind=kind, comparison=comparison)
    return root / (request["binding"] + "." + kind + ".json"), request


def _write(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    actual, reason = read_selected(path.parent, path.name)
    _require(reason is None and actual == content)


def record(
    task: Path,
    observed_decision: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    kind="environment",
    comparison=None,
) -> Path:
    """Operator-only callback for actual answers and explicit per-scope retention consent."""
    target, request = _target(task, kind=kind, comparison=comparison)
    _require(not target.with_suffix(".revoked").exists() and not target.exists())
    event = _event(request, observed_decision(deepcopy(request)))
    document = {"schema": "rubric-" + kind + "-review-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}
    _write(target, (json.dumps(document, sort_keys=True, allow_nan=False) + "\n").encode())
    observed = read(task, kind=kind, comparison=comparison)
    _require(observed is not None and observed["event"] == event)
    return target


def read(task: Path, *, kind="environment", comparison=None) -> dict[str, Any] | None:
    if _operator_root() is None:
        return None
    target, request = _target(task, kind=kind, comparison=comparison)
    if not target.exists():
        return None
    raw, reason = read_selected(target.parent, target.name)
    if reason is not None or raw is None:
        raise ValueError("unavailable scoped Environment review")
    document = loads(raw)
    _require(isinstance(document, dict) and set(document) == {"schema", "trust_basis", "event"})
    _require(document["schema"] == "rubric-" + kind + "-review-v1" and document["trust_basis"] == OPERATOR_BOUNDARY)
    event = _event(request, document["event"])
    return {
        **document,
        "event": event,
        "revoked": target.with_suffix(".revoked").exists(),
        "digest": hashlib.sha256(raw).hexdigest(),
        "local_operator_decision_recorded": True,
        "owner_signature_verified": False,
        "owner_session_decision_verified": False,
    }


def revoke(task: Path, *, kind="environment", comparison=None) -> Path:
    target, _ = _target(task, kind=kind, comparison=comparison)
    _require(target.exists())
    revoked = target.with_suffix(".revoked")
    _write(revoked, b"revoked\n")
    observed = read(task, kind=kind, comparison=comparison)
    _require(observed is not None and observed["revoked"] is True)
    return revoked


def _automatic(native: Any, request: dict[str, Any]) -> dict[str, Any]:
    panels = native.get("panels", [native]) if isinstance(native, dict) else []
    runtime = [p for p in panels if p.get("scope") == "eval-author.refund-runtime-observation.v1"]
    if len(runtime) != 1:
        return {"ready": False, "reason": "A current admitted runtime action/state panel is required."}
    panel = runtime[0]
    environment = panel.get("environment", {})
    controls = environment.get("action_controls", [])
    expected = {"nop": (0, 0), "actions": (3, 1), "reset": (0, 0)}
    ready = (
        panel.get("native_execution") is True
        and panel.get("task_freshness") == "current"
        and len(controls) == len(expected)
        and {c["case"] for c in controls} == set(expected)
        and all(
            type(c["uid"]) is int
            and c["uid"] == 65534
            and c["errors_preserve_exact_state"] is True
            and c["initial_state_matches"] is True
            and (c["event_count"], c["reward"]) == expected[c["case"]]
            for c in controls
        )
        and len({c["container_id"] for c in controls}) == 3
        and len({c["session_id"] for c in controls}) == 3
    )
    contract = environment.get("contract", {})
    ready = ready and set(contract.get("actions", {})) == {
        "read",
        "approve",
        "refund",
        "ledger",
        "complete",
        "reset",
        "error",
    }
    state = environment.get("state_relation", {})
    ready = ready and state.get("relation") == {"nop": 0, "actions": 1, "reset": 0} and state.get("live_sources") == []
    components = environment.get("components", {})
    required = {
        "contract",
        "harbor-docker-adapter",
        "ledger",
        "python-stdlib",
        "scorer",
        "shell",
        "socket-service",
        "verifier",
    }
    ready = (
        ready
        and set(components) == required
        and all(
            component.get("status") == "real"
            and component.get("source_sha256") == request["sources"].get(component.get("source"))
            and set(component.get("observed_in", [])) == set(expected)
            and set(component.get("observed_identities", {})) == set(expected)
            and all(component["observed_identities"].values())
            for component in components.values()
        )
    )
    return {
        "ready": bool(ready),
        "reason": "Current owning validator admitted solver calls, exact error/reset states, component identities and bounded state/reward relations."
        if ready
        else "The current admitted runtime action/state evidence is incomplete.",
        "panel_digest": panel["index_digest"],
        "evidence_ref": {"measurement": "example_assessment.native.environment", "digest": panel["index_digest"]},
        "level_3": False,
        "level_3_gap": "Independent clean-runtime reset/error/boundary corroboration and independent replay/deliberate drift tests are absent. Repeated controls by one collector do not establish independence.",
        "boundary": "Only the declared offline ledger and observed runtime. Timeout enforcement, live payment fidelity, changed-policy native evidence and model performance are not claimed.",
    }


def project(root: Path, selection: dict[str, Any], measurements: dict[str, Any]) -> dict[str, Any] | None:
    config = selection.get("example_assessment")
    if not isinstance(config, dict):
        return None
    artifacts = selection["artifacts"]
    task = next((a for a in artifacts if a["id"] == config["task_config_id"]), None)
    if task is None:
        return None
    task_root = (root / task["path"]).parent
    try:
        request = prepare(task_root)
    except ValueError:
        return None
    store = _operator_root()
    if store is not None:
        _require(all(not (root / a["path"]).absolute().is_relative_to(store) for a in artifacts))
    receipt = read(task_root)
    return {
        "request": request,
        "receipt": receipt,
        "automatic": _automatic(measurements.get("example_assessment", {}).get("native"), request),
        "boundary": BOUNDARY,
    }


def apply(rows: dict[str, Any], projection: dict[str, Any] | None) -> None:
    if projection is None:
        return
    receipt = projection["receipt"]
    automatic = projection["automatic"]
    for ident in SCOPES:
        if ident not in rows:
            continue
        row = rows[ident]
        decision = receipt["event"]["decisions"][ident] if receipt else None
        accepted = bool(receipt and decision and not receipt["revoked"] and decision["decision"] == "approve")
        blocked = row["score"] in (0, 1) or any(
            c["outcome"] in {"FAIL", "ERROR", "BLOCKED", "PARTIAL"} for c in row["check_results"]
        )
        gap = (
            "Scoped intent is retained, but failed or incomplete current checks prevent level 2."
            if accepted and blocked
            else "Independent corroboration remains absent; scoped operator intent and current native evidence support level 2 only."
            if accepted and automatic["ready"]
            else automatic["reason"]
            if accepted
            else "Environment intent needs an exact scoped operator record with explicit retention consent; source-review consent does not cover it."
        )
        if receipt and receipt["revoked"]:
            gap = "The scoped Environment decision is revoked; its historical answer is retained without current authority."
        elif decision and decision["decision"] != "approve":
            gap = (
                "The scoped Environment decision is "
                + decision["decision"]
                + "; preserve its rationale and resolve the scope-owner question."
            )
        ref = {"measurement": "general_review", "binding": projection["request"]["binding"]}
        row["evidence_refs"].append(deepcopy(ref))
        if receipt:
            row["scoped_operator_review"] = {
                "decision": deepcopy(decision),
                "revoked": receipt["revoked"],
                "receipt_digest": receipt["digest"],
                "trust_basis": receipt["trust_basis"],
            }
        superseded = set()
        for check in row["check_results"]:
            if check["method"] != "human_review" or check["outcome"] in {"FAIL", "ERROR", "BLOCKED"}:
                continue
            if accepted:
                superseded.add(check.get("remaining_gap"))
                superseded.add("No qualified evidence consumed for: " + check.get("expected_evidence", ""))
                check.update(
                    outcome="PASS",
                    remaining_gap=None,
                    evidence_boundary=BOUNDARY,
                    next_action="No further intent decision needed for this exact scope.",
                )
                check["evidence_refs"].append(deepcopy(ref))
            else:
                check.update(remaining_gap=gap, next_action=gap)
        if accepted:
            superseded.add(
                "Native automatic checks passed. Scope-owner review of the declared action/state map remains pending."
            )
        if automatic["ready"]:
            from rubric_report import PARTIAL_HELPERS
            from rubric_scoring import ITEM_LIMITATIONS

            superseded.add(ITEM_LIMITATIONS[ident])
            for check in row["check_results"]:
                if check["method"] != "human_review" and check["outcome"] == "PASS":
                    partial = PARTIAL_HELPERS.get(check["id"])
                    if partial:
                        superseded.add(partial[1] + " Still required: " + check.get("expected_evidence", ""))
        for key in ("limitations", "remaining_gaps"):
            row[key] = [value for value in row.get(key, []) if value not in superseded]
            if gap not in row[key]:
                row[key].append(gap)
        if (
            accepted
            and automatic["ready"]
            and row["score"] in (None, 2)
            and all(c["outcome"] == "PASS" for c in row["check_results"])
        ):
            row.update(
                score=2,
                status="ASSESSED",
                assessment_state="ASSESSED",
                execution_state="COMPLETED",
                evidence_strength="E2",
                next_action=automatic["level_3_gap"],
            )


def projection_receipt(receipt):
    if receipt is None:
        return None
    result = deepcopy(receipt)
    request = result["event"].pop("request")
    result["event"]["request_ref"] = {"binding": request["binding"]}
    return result


def project_scoped(root, selection, measurements, audit=None):
    config = selection.get("example_assessment")
    if not isinstance(config, dict):
        return {}
    task = next(a for a in selection["artifacts"] if a["id"] == config["task_config_id"])
    task_root = (root / task["path"]).parent
    try:
        request = prepare(task_root, kind="contract")
    except ValueError:
        return {}
    store = _operator_root()
    if store is not None:
        _require(all(not (root / a["path"]).absolute().is_relative_to(store) for a in selection["artifacts"]))
    from rubric_contract_positive import automatic as contract_automatic

    result = {
        "contract": {
            "request": request,
            "receipt": projection_receipt(read(task_root, kind="contract")),
            "automatic": contract_automatic(request, measurements),
        }
    }
    if audit is not None and "comparison" in audit:
        from rubric_fairness import automatic as fairness_automatic

        comparison = audit["comparison"]
        request = prepare(task_root, kind="fairness", comparison=comparison)
        result["fairness"] = {
            "request": request,
            "receipt": projection_receipt(read(task_root, kind="fairness", comparison=comparison)),
            "automatic": fairness_automatic(request),
        }
    return result


def apply_scoped(rows, projection):
    """Apply only complete level-2 predicates; retain higher checks and failures."""
    if projection is None:
        return
    request, receipt = projection["request"], projection["receipt"]
    for ident in request["scopes"]:
        if ident not in rows:
            continue
        row = rows[ident]
        automatic = projection["automatic"][ident]
        decision = receipt["event"]["decisions"][ident] if receipt else None
        accepted = bool(receipt and not receipt["revoked"] and decision["decision"] == "approve")
        ref = {"measurement": "scoped_reviews", "binding": request["binding"]}
        row["evidence_refs"].append(ref)
        if receipt:
            row["scoped_operator_review"] = {
                "decision": deepcopy(decision),
                "revoked": receipt["revoked"],
                "receipt_digest": receipt["digest"],
                "trust_basis": receipt["trust_basis"],
            }
        blocked = row["score"] in (0, 1) or any(
            c["outcome"] in {"FAIL", "ERROR", "BLOCKED"} for c in row["check_results"]
        )
        superseded = set()
        for check in row["check_results"]:
            if check["outcome"] in {"FAIL", "ERROR", "BLOCKED"}:
                continue
            human = check["method"] in {"human_review", "llm_as_judge"}
            if (human and accepted) or (check["method"] == "deterministic" and automatic["ready"]):
                superseded.add(check.get("remaining_gap"))
                superseded.add("No qualified evidence consumed for: " + check.get("expected_evidence", ""))
                check.update(
                    outcome="PASS", remaining_gap=None, next_action=None, evidence_boundary=request["boundary"]
                )
                check["evidence_refs"].append(deepcopy(ref))
                if human:
                    check["reviewer_kind"] = "human"
                    check["adjudication_method"] = "human_review"
                    check["qualification_basis"] = (
                        "Scope-owner decision recorded by the trusted local assessment operator; no authenticated identity claim."
                    )
            elif check["method"] == "probe" and accepted and automatic["ready"]:
                superseded.add(check.get("remaining_gap"))
                if automatic["pending_probe"]:
                    check.update(remaining_gap=automatic["level_3_gap"], next_action=automatic["level_3_gap"])
                else:
                    check.update(
                        outcome="NOT_APPLICABLE",
                        remaining_gap=None,
                        next_action=None,
                        applicability_reason="No disputed setup difference remains in this narrow owner-adjudicated comparison. Sensitivity robustness is not claimed.",
                    )
        gap = (
            "Current failed checks prevent positive qualification."
            if blocked
            else automatic["level_3_gap"]
            if accepted and automatic["ready"]
            else automatic["reason"]
            if accepted
            else "An exact scope-owner adjudication with retention consent is absent, deferred, rejected or revoked."
        )
        if accepted:
            superseded.add(
                "No qualified task-contract adjudication is consumed. Source-bound recorded decisions remain separate from human authority, independent review and native controls."
            )
            superseded.add(
                "Human fairness review and matched sensitivity tests remain pending. Hash equality does not establish independence."
            )
        for key in ("remaining_gaps", "limitations"):
            row[key] = [value for value in row.get(key, []) if value not in superseded]
            if gap not in row[key]:
                row[key].append(gap)
        if accepted and automatic["ready"] and not blocked and row["score"] in (None, 2):
            row.update(
                score=2,
                status="ASSESSED",
                assessment_state="ASSESSED",
                execution_state="PARTIAL" if automatic["pending_probe"] else "COMPLETED",
                evidence_strength=automatic["evidence_strength"],
                next_action=gap,
            )
