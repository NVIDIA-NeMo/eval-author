# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Aggregate typed verification-integrity measurements from existing receipt validation.

The owning validators classify each receipt. These helpers only aggregate those
classifications. They report record consistency and evidence coverage, not agent
intent, model-visible context, or context-rot causation.
"""

from __future__ import annotations

from typing import Literal, TypedDict

RECEIPT_DETECTOR = "receipt-integrity-v1"
VERIFICATION_DETECTOR = "required-verification-v1"
SUPERSESSION_DETECTOR = "manifest-supersession-v1"
ACCEPTANCE_DETECTOR = "acceptance-claim-v1"

CLAIMS = ("passed", "behavior_failed", "infrastructure_error")
STATES = ("pass", "fail", "missing", "mismatched", "execution_error", "unknown", "stale")

Code = Literal[
    "validated",
    "unassessed",
    "status_contradiction",
    "status_mismatch",
    "source_missing",
    "invalid_evidence",
    "excluded",
    "out_of_scope",
    "unsupported",
    "execution_error",
    "stale",
    "superseded",
    "evidence_unavailable",
    "reference_invalid",
]


class ReceiptIntegrity(TypedDict):
    claim: Literal["passed", "behavior_failed", "infrastructure_error"] | None
    purpose: Literal["closure", "baseline"] | None
    manifest_sha256: str | None
    basis: Literal["receipt", "native"] | None
    status: Literal["consistent", "mismatched", "unknown"]
    execution: Literal["healthy", "execution_error", "unknown"]
    behavior: Literal["passed", "failed", "unknown"]
    status_claim: Literal["consistent", "contradiction", "unknown", "excluded", "not_applicable"]
    code: Code
    freshness: Literal["current", "stale", "unknown"]


def new_integrity(code: Code = "unassessed") -> ReceiptIntegrity:
    return ReceiptIntegrity(
        claim=None,
        purpose=None,
        manifest_sha256=None,
        basis=None,
        status="unknown",
        execution="unknown",
        behavior="unknown",
        status_claim="unknown",
        code=code,
        freshness="unknown",
    )


def summarize_receipts(states: list[ReceiptIntegrity]) -> dict:
    """Count passed status claims. Other outcomes are not contradiction opportunities."""
    eligible = sum(row["status_claim"] in {"consistent", "contradiction"} for row in states)
    contradictions = sum(row["status_claim"] == "contradiction" for row in states)
    return {
        "detector_version": RECEIPT_DETECTOR,
        "selected_receipts": len(states),
        "eligible_claims": eligible,
        "contradictions": contradictions,
        "unknown": sum(row["status_claim"] == "unknown" for row in states),
        "excluded": sum(row["status_claim"] == "excluded" for row in states),
        "not_applicable": sum(row["status_claim"] == "not_applicable" for row in states),
        "execution_errors": sum(row["execution"] == "execution_error" for row in states),
        "mismatched": sum(row["status"] == "mismatched" for row in states),
        "contradiction_rate": contradictions / eligible if eligible else None,
        "context_rot_score": None,
    }


def receipt_state(integrity: ReceiptIntegrity) -> str:
    if integrity["code"] == "stale":
        return "stale"
    if integrity["status"] == "mismatched":
        return "mismatched"
    if integrity["execution"] == "execution_error":
        return "execution_error"
    if integrity["code"] == "validated":
        return "pass" if integrity["behavior"] == "passed" else "fail"
    return "unknown"


def _freshness(values: list[str]) -> str:
    if values and all(value == "current" for value in values):
        return "current"
    return "stale" if "stale" in values else "unknown"


def required_verification(manifests: dict[str, dict], rows: list[dict]) -> dict:
    """Join each selected manifest's required controls to its closure receipts.

    ``manifests`` maps repository-relative manifest paths to validated manifests.
    ``rows`` are receipt rows from ``inspect_receipt(..., include_integrity=True)``.
    A missing receipt is an evidence gap. The attempt ledger is not read, so it
    does not show that verification was skipped.
    """
    cases = []
    for path, manifest in sorted(manifests.items()):
        for name, case in sorted(manifest["contract"]["cases"].items()):
            if case["kind"] in {"agent", "diagnostic"}:
                continue
            matching = [
                row["integrity"]
                for row in rows
                if "integrity" in row
                and row.get("manifest") == path
                and row.get("case") == name
                and row["integrity"]["purpose"] != "baseline"
            ]
            current = [state for state in matching if state["code"] != "superseded"]
            states = [receipt_state(state) for state in current]
            state = next(
                (value for value in ("mismatched", "execution_error", "fail", "unknown", "stale") if value in states),
                "pass" if states else "missing",
            )
            cases.append(
                {
                    "manifest": path,
                    "case": name,
                    "kind": case["kind"],
                    "state": state,
                    "freshness": _freshness([row["freshness"] for row in current]),
                    "receipts": len(current),
                    "superseded_receipts": len(matching) - len(current),
                }
            )
    counts = {state: sum(row["state"] == state for row in cases) for state in STATES}
    current = {
        state: sum(row["state"] == state and row["freshness"] == "current" for row in cases)
        for state in ("pass", "fail")
    }
    return {
        "detector_version": VERIFICATION_DETECTOR,
        "required_cases": len(cases),
        "states": counts,
        "current_pass": current["pass"],
        "current_fail": current["fail"],
        "verified": bool(cases) and current["pass"] == len(cases),
        "capture": "unknown",
        "cases": cases,
    }


def superseded_references(manifests: dict[str, tuple[dict, str]], rows: list[dict]) -> dict:
    """Find receipts bound to a manifest that a selected repair explicitly supersedes.

    ``manifests`` maps relative paths to (manifest, sha256). Receipts have no
    trustworthy timestamps, so each reference is a review candidate. A current
    completion claim that cites one is confirmed by the acceptance check instead.
    """
    superseding = {
        manifest["supersedes"]["manifest_sha256"]: path
        for path, (manifest, _) in sorted(manifests.items())
        if isinstance(manifest.get("supersedes"), dict)
    }
    findings = [
        {
            "receipt": row["path"],
            "case": row.get("case"),
            "superseded_manifest_sha256": row["integrity"]["manifest_sha256"],
            "superseding_manifest": superseding[row["integrity"]["manifest_sha256"]],
            "effect": "attempted" if row["integrity"]["claim"] in (None, "infrastructure_error") else "enacted",
            "status": "review_candidate",
        }
        for row in rows
        if "integrity" in row and row["integrity"]["manifest_sha256"] in superseding
    ]
    return {
        "detector_version": SUPERSESSION_DETECTOR,
        "superseded_manifests": len(superseding),
        "references": len(findings),
        "enacted": sum(row["effect"] == "enacted" for row in findings),
        "attempted": sum(row["effect"] == "attempted" for row in findings),
        "confirmed": 0,
        "ordering": "unknown",
        "findings": findings,
    }
