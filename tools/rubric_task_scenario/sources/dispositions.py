# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed, source/evidence-bound dispositions within fiction, never real approvals."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal, TypedDict


class FictionalScope(TypedDict):
    world: str
    subject: str
    provider: str
    empirical: bool


class Binding(TypedDict):
    evidence_sha256: str
    sources_sha256: str


class Disposition(TypedDict):
    kind: Literal["SUPPLIED_FICTIONAL_DISPOSITION"]
    disposition: Literal["ACCEPTED_WITHIN_FICTION", "REJECTED"]
    scope: FictionalScope
    conditions: dict[str, bool]
    contradictions: list[str]
    rationale: str
    binding: Binding


CONDITIONS = {
    "contract": "public_obligations_and_boundary_solutions_agree",
    "discovery": "public_only_reader_froze_complete_obligation_register",
    "authority": "public_specification_oracle_and_scoring_agree",
    "attacks": "independent_attack_classes_and_semantic_vectors_qualified",
    "exposure": "complete_exposure_lineage_and_bounded_sensitivity_reviewed",
    "confirmation": "independent_untouched_full_cohort_and_selection_history_reviewed",
    "trace": "contextual_labels_and_exact_private_release_qualified",
    "isolation": "independent_surface_order_parallel_reset_controls_qualified",
    "cues": "matched_consumed_style_controls_and_confound_review_qualified",
    "artifact": "complete_task_inventory_independently_reconstructed",
    "path": "selected_grading_route_independently_resolved",
    "dependencies": "complete_local_dependency_closure_independently_resolved",
    "configuration": "declared_finite_configuration_independently_reconstructed",
    "repeat": "full_check_repeat_independently_corroborated",
    "runtime": "declared_local_alternative_setup_and_rejections_qualified",
    "fairness": "equal_information_permissions_and_matched_comparisons_justified",
    "tools": "public_actions_errors_closure_and_reset_independently_reviewed",
    "substitution": "exact_local_transport_alternative_and_limits_authorized",
    "state": "mutable_state_drift_and_fresh_answers_independently_reviewed",
    "revision": "current_bound_revision_and_invalidation_scope_reviewed",
    "alternative": "local_ledger_source_route_only_no_native_equivalence",
}
SCOPE: FictionalScope = {
    "world": "refund-one-task-fiction-v1",
    "subject": "perfect-task/refund",
    "provider": "local-ledger-source-v1",
    "empirical": False,
}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def bind_templates(templates: dict, evidence: dict, sources: dict) -> dict:
    """Instantiate fictional assertions, not an automatic real review admission."""
    result = copy.deepcopy(templates)
    binding = {"evidence_sha256": digest(evidence), "sources_sha256": digest(sources)}
    for record in [*result.get("reviews", {}).values(), result.get("method_qualification", {}).get("alternative")]:
        if isinstance(record, dict):
            record["binding"] = binding
    return result


def accepted(record: Any, key: str, evidence: dict, sources: dict) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        set(record) == {"kind", "disposition", "scope", "conditions", "contradictions", "rationale", "binding"}
        and record["kind"] == "SUPPLIED_FICTIONAL_DISPOSITION"
        and record["disposition"] == "ACCEPTED_WITHIN_FICTION"
        and type(record["scope"]) is dict
        and digest(record["scope"]) == digest(SCOPE)
        and digest(record["conditions"]) == digest({CONDITIONS[key]: True, "no_material_contradiction": True})
        and record["contradictions"] == []
        and isinstance(record["rationale"], str)
        and bool(record["rationale"].strip())
        and record["binding"] == {"evidence_sha256": digest(evidence), "sources_sha256": digest(sources)}
    )
