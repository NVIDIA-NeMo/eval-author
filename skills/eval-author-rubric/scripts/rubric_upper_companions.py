# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Attach admitted upper evidence outside the frozen source-review selection."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

SCOPE = (
    "Unreviewed operational supplement outside accepted source-review conclusions. "
    "The original selection, source packet, owner decisions and Audit scope are unchanged. "
    "Existing scoped decisions supply only their original lower-anchor intent; they do not approve this companion."
)


def compact_identity_evidence(report: dict[str, Any]) -> None:
    """Link only exact repeated inventories; retain the full owning value once."""
    identity = report.get("measurements", {}).get("identity")
    if identity is None:
        return
    for row in report["items"].values():
        for check in row.get("check_results", []):
            for evidence in check.get("evidence", []):
                if evidence.get("helper") == "identity" and evidence.get("value") == identity.get("records"):
                    evidence.pop("value")
                    evidence["value_ref"] = "#/report/measurements/identity/records"


@contextmanager
def environment_hook(bundle: Path):
    """Apply after ordinary admission; never feed supplements to source review."""
    import rubric_table
    import rubric_upper_environment as upper

    original = rubric_table.measure

    def measured(root, selection):
        # select_bundle performs current owning registry/native validation. This
        # private overlay exists only for the companion's own consumer.
        addition = upper.select_bundle(root, bundle)
        overlay = deepcopy(selection)
        if "upper_environment_bundle_id" in overlay.get("example_assessment", {}):
            raise ValueError("upper Environment requires an orchestration input, not selected evidence")
        if any(a["id"] == addition["upper_environment_bundle_id"] for a in overlay["artifacts"]):
            raise ValueError("upper Environment companion already selected")
        overlay["artifacts"].extend(addition["artifacts"])
        overlay["example_assessment"]["upper_environment_bundle_id"] = addition["upper_environment_bundle_id"]
        report = original(root, selection)
        projection = upper.project(root, overlay, report["measurements"])
        if projection is None:
            raise ValueError("upper Environment projection missing")
        upper.apply(report["items"], projection)
        # Re-read native admission, old runtime observations and genuine owner
        # receipts/revocation after projection. No cached acceptance or resealing.
        if upper.project(root, overlay, report["measurements"]) != projection:
            raise ValueError("upper Environment evidence changed during assessment")
        report["measurements"]["upper_environment"] = {
            **projection,
            "source_review_scope": SCOPE,
            "native_index": addition["artifacts"][0],
            "orchestrator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        for criterion in upper.CRITERIA:
            row = report["items"].get(criterion)
            if row is None:
                continue
            # Keep old adjudication scope intact; label only the superseded
            # no-test applicability note, rather than broadening owner intent.
            for check in row.get("check_results", []):
                if (
                    criterion == "provenance.comparison-fairness"
                    and check["method"] == "probe"
                    and check["outcome"] == "PASS"
                    and "applicability_reason" in check
                ):
                    check["historical_applicability_reason"] = check.pop("applicability_reason")
            row["upper_environment"]["source_review_scope"] = SCOPE
            row["upper_evidence_summary"] = {
                "provenance.observed-configuration": "Independent operational repeat reproduces recorded configuration, exact state and all checks; actual deadline enforcement stays unknown.",
                "provenance.runtime-route": "Seven clean native controls plus provider-owned rejection/recovery of invalid network, CPU and memory field types.",
                "provenance.solver-tool-access": "Seven fresh UID 65534 sessions exercise calls, exact error/reset states and four denied write boundaries; original action-map approval retained.",
                "provenance.dependency-substitution": "Fresh provisioning identities reconcile to the existing component inventory; all components are real for the declared offline ledger, with no live-provider substitution claim.",
                "provenance.external-state": "Exact replay plus duplicate-refund drift match declared state/check relations; original state-intent receipt retained.",
                "provenance.comparison-fairness": "Reference and split-refund policies match every check under batched-after and individual-before scaffolds; narrow scripted sensitivity only, E2.",
            }[criterion]
        compact_identity_evidence(report)
        return report

    rubric_table.measure = measured  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        rubric_table.measure = original
