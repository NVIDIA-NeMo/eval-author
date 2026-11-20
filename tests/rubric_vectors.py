# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authored per-criterion fact vectors for the rubric predicates: score 0, 1, 2 and corroboration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))
from rubric_evidence_scoring import ATTACK_CLASSES, CUE_CLASSES, ROBUSTNESS_OUTCOMES, ROBUSTNESS_POLICIES  # noqa: E402

CONTAINED = {
    "id": "exploit",
    "affected_revision": "v0",
    "disposition": "quarantined",
    "residual_risk": "Affected results excluded",
    "repair_revision": "v1",
    "replay": "credit_withheld",
    "valid_after_repair": "credited",
}
CUE = {"qualified": "yes", "health": "healthy", "outcome": "no_bypass"}

VECTORS = {
    "provenance.task-artifact-access": (
        "task_artifacts",
        {"id": "a", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
        {"access": "missing", "inspection_effect": "blocks"},
        {"revision": None},
    ),
    "provenance.grading-path": (
        "grading_routes",
        {"id": "a", "declared_scorer": "s", "selected_scorer": "s", "configuration": "v1", "branch": "resolved"},
        {"selected_scorer": "other"},
        {"branch": "ambiguous"},
    ),
    "provenance.grading-dependency-access": (
        "grading_dependencies",
        {
            "id": "a",
            "access": "accessible",
            "revision": "v1",
            "inspection_effect": "none",
            "reference_review": "resolved",
        },
        {"access": "denied", "inspection_effect": "blocks"},
        {"reference_review": "unresolved"},
    ),
    "provenance.declared-configuration": (
        "configuration_fields",
        {
            "id": "model",
            "value": "model-v1",
            "source_identity": "config-v1",
            "availability": "sourced",
            "claim_limit": "none",
            "reported_value": "model-v1",
        },
        {"reported_value": "wrong", "claim_limit": "uninterpretable"},
        {"source_identity": None},
    ),
    "provenance.observed-configuration": (
        "run_settings",
        {"id": "model", "declared": "m1", "observed": "m1", "run_identity": "run1", "disposition": "agrees"},
        {"observed": "m2", "disposition": "invalidates"},
        {"observed": None, "disposition": "unresolved"},
    ),
    "provenance.runtime-route": (
        "provider_validation",
        {
            "id": "route",
            "provider": "harbor",
            "adapter_revision": "v1",
            "manifest_identity": "m1",
            "validation": "accepted",
            "adapter": "compatible",
        },
        {"validation": "incompatible"},
        {"adapter": "unresolved"},
    ),
    "provenance.comparison-fairness": (
        "setup_differences",
        {
            "id": "budget",
            "left": "100",
            "right": "200",
            "rationale": "Scope permits accommodations",
            "disposition": "justified",
            "advantage": "none",
        },
        {"disposition": "unjustified", "advantage": "demonstrated"},
        {"disposition": "disputed"},
    ),
    "provenance.solver-tool-access": (
        "tool_probes",
        {
            "id": "read",
            "action": "read",
            "solver_identity": "solver",
            "expected": "allowed",
            "actual": "allowed",
            "probe": "call",
            "execution": "completed",
        },
        {"actual": "denied"},
        {"probe": "schema", "execution": "partial"},
    ),
    "provenance.dependency-substitution": (
        "components",
        {
            "id": "tool",
            "form": "real",
            "observed_revision": "v1",
            "approved_revision": None,
            "authorization": "not_required",
            "availability": "available",
        },
        {"availability": "missing"},
        {"availability": "partly_reconciled"},
    ),
    "provenance.external-state": (
        "external_state",
        {
            "id": "dataset",
            "captured_identity": "sha256-v1",
            "mode": "pinned",
            "live_bound": None,
            "observation": "within_bound",
            "outcome_relation": "interpretable",
        },
        {"observation": "drifted", "outcome_relation": "invalidated"},
        {"captured_identity": None, "observation": "capture_gap"},
    ),
    "provenance.evidence-revision-consistency": (
        "result_revisions",
        {
            "id": "result",
            "recorded_revision": "v1",
            "current_revision": "v1",
            "change": "unchanged",
            "disposition": "current",
            "lineage": "original",
        },
        {"current_revision": "v2", "change": "material", "disposition": "combined"},
        {"current_revision": "v2", "change": "material", "disposition": "stale"},
    ),
    "task-contract.clear-contract": (
        "contract_requirements",
        {
            "id": "format",
            "public_requirement": "JSON",
            "scorer_requirement": "JSON",
            "interpretation": "public",
            "resolution": "resolved",
        },
        {"scorer_requirement": "CSV", "resolution": "prevents_grading"},
        {"resolution": "ambiguous"},
    ),
    "task-contract.discoverable-requirements": (
        "scored_conditions",
        {
            "id": "format",
            "condition": "JSON",
            "authority": "JSON",
            "solver_access": "accessible",
            "mapping": "supported",
        },
        {"authority": None, "solver_access": "denied", "mapping": "hidden"},
        {"mapping": "disputed"},
    ),
    "task-contract.scoring-authority": (
        "scoring_authorities",
        {
            "id": "answer",
            "expected": "42",
            "authoritative": "42",
            "obligation": "exact result",
            "semantics": "resolved",
        },
        {"authoritative": "43", "semantics": "contradicted"},
        {"semantics": "disputed"},
    ),
    "AUDIT-03": (
        "attack_controls",
        {
            "id": "no_work",
            "attack_class": "no_work",
            "applicability": "applicable",
            "expected_credit": 0,
            "actual_credit": 0,
            "health": "healthy",
            "reach": "reached",
        },
        {"actual_credit": 1},
        {"attack_class": "all_failure"},
    ),
    "AUDIT-04-B": (
        "exploit_containment",
        CONTAINED,
        {"replay": "credit_awarded"},
        {"repair_revision": None},
    ),
    "AUDIT-04-B-SUITE": (
        "suite_containment",
        {**CONTAINED, "family": "retained"},
        {"disposition": "accepted"},
        {"family": "new"},
    ),
    "AUDIT-05-B": (
        "exposure_inventory",
        {
            "id": "solution",
            "exposure": "public",
            "overlap": "unknown",
            "disclosed": "yes",
            "statement": "within_support",
            "sensitivity": "computed",
        },
        {"overlap": "verified", "statement": "overclaims"},
        {"sensitivity": "missing"},
    ),
    "AUDIT-05-S03": (
        "selection_ledger",
        {
            "id": "confirmation",
            "event": "confirmation",
            "alternatives_retained": "yes",
            "holdout_access": "after_freeze",
            "label": "confirmatory",
        },
        {"holdout_access": "before_freeze"},
        {"alternatives_retained": "no"},
    ),
    "TRACE-04-A": (
        "material_review",
        {
            "id": "trace",
            "channel": "trace",
            "candidate": "none",
            "reviewed": "yes",
            "disposition": "authorized",
            "approval_match": "match",
        },
        {"disposition": "unauthorized_use"},
        {"disposition": "pending"},
    ),
    "ENV-03-S01": (
        "isolation_surfaces",
        {
            "id": "workspace",
            "sharing": "isolated",
            "reset_verified": "yes",
            "target_alone": "1",
            "target_after": "1",
            "consequence": "none",
        },
        {"target_after": "0", "consequence": "credit_change"},
        {"consequence": "unknown"},
    ),
    "CREATE-02": (
        "cue_controls",
        {"id": "cue_only", "cue_class": "cue_only", **CUE},
        {"outcome": "bypass"},
        {"outcome": "confounded"},
    ),
    "CREATE-02-SUITE": (
        "family_cue_controls",
        {"id": "cue_only", "cue_class": "cue_only", "family": "new-a", "authorship": "independent", **CUE},
        {"outcome": "bypass"},
        {"outcome": "confounded"},
    ),
}


def facts_for(ident: str, variant: int) -> dict[str, Any]:
    name, base, defect, partial = VECTORS[ident]
    row = {**base, **({0: defect, 1: partial}.get(variant, {}))}
    rows = [row]
    if variant == 1 and ident in (
        "provenance.observed-configuration",
        "provenance.external-state",
        "task-contract.scoring-authority",
    ):
        rows.append({**base, "id": "established-neighbor"})
    if ident == "AUDIT-03":
        rows.extend(
            {**base, "id": kind, "attack_class": kind}
            for kind in ATTACK_CLASSES[1:] + ("valid_inaction", "realistic_wrong")
            if kind != row["attack_class"]
        )
    if ident == "AUDIT-04-B-SUITE" and variant != 1:
        rows.append({**base, "id": "exploit-new", "family": "new"})
    if ident == "AUDIT-05-S03":
        rows.extend(
            {**base, "id": event, "event": event, "holdout_access": "none", "label": "exploratory"}
            for event in ("candidate", "freeze")
        )
    if ident in ("CREATE-02", "CREATE-02-SUITE"):
        rows.extend({**base, "id": kind, "cue_class": kind} for kind in CUE_CLASSES[1:])
    return {name: rows}


def robustness_for(ident: str) -> dict[str, Any]:
    # All canonical control roles must be present, not merely one green receipt.
    name, roles = ROBUSTNESS_POLICIES[ident]
    return {
        name: [
            {
                "id": role,
                "role": role,
                "expected": ROBUSTNESS_OUTCOMES[ident][role][0],
                "observed": ROBUSTNESS_OUTCOMES[ident][role][0],
                "original_actor": "author-a",
                "corroborating_actor": "reviewer-b",
                "source_identity": f"independent-{role}",
                "execution": "completed",
                "scope": "same-task-revision",
            }
            for role in roles
        ]
    }
