# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The rubric scorer: criterion predicates applied to source-bound facts.

API: assess_evidence(root: Path, selection: dict) -> item-ID-keyed rich rows.
selection['criterion_evidence'] = {'artifact_id': ID}; ID names an ordinary
selected artifact in selection['artifacts'] (id/path/digest/access metadata).
canonical_definitions() returns the unchanged canonical anchors and checks.

Version 1 packet contract (no caller-supplied scores):
  schema: 'rubric-criterion-evidence-v1'
  subject: {artifact_id: selected task ID, digest: exact SHA256}
  definitions_digest: definitions_digest()
  assessment_scope: 'recorded-evidence-assessment' OR
                    'synthetic-scoring-demonstration'
  items: [{item_id: canonical ID, facts: {FACT_NAME: {
    basis: 'json_observation' OR 'recorded_judgment',
    source: {artifact_id: ID, pointer: '/facts/name', digest: value_digest(value)},
    support: [{artifact_id: ID, pointer: JSON_POINTER, digest: value_digest(value)}]
  }}}]
Each fact source must be JSON with schema='rubric-fact-source-v1', the same
subject and definitions_digest, plus provenance={kind: 'synthetic'|'agent'|
'human'|'tool', actor_id: nonempty string, activity: 'review'|'observation',
limitations: [nonempty strings]}. Source values are extracted, NOT supplied by
packet callers. Support references name nonempty exact JSON values in selected
artifacts (the task itself may be a support). All artifact bytes are bounded,
no-follow, digest-checked. FACT_POLICIES exposes criterion-specific fact names,
types, and explicit anchor predicates; absent/null facts stay unknown.
Robustness rows retain role/expected/observed fields. For each criterion and role,
ROBUSTNESS_OUTCOMES gives (success, failure): expected must be success; observed
must be success, failure, or null. Arbitrary equal text is rejected. Completed,
same-revision failures lower acceptance (confirmed defect outcomes to zero);
unrun/error/other-revision controls neither corroborate nor establish defects.

Both scopes apply the same criterion predicates. A score of 2 needs the
criterion's observed facts; 3 also needs completed corroborating controls by a
different actor. Simulated rows get E1; recorded rows get E2, or E3 when
corroborated. Facts are content-bound to selected artifacts; reviewer identities
are recorded, not authenticated.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict

from eval_author_shared.task_evidence import loads
from rubric_identity import MAX_FILES, MAX_TOTAL_BYTES, SelectedAccess, read_selected
from rubric_identity import text as _text


class FactPolicy(TypedDict):
    fields: dict[str, tuple[str, ...] | str]
    description: str


FACT_POLICIES: dict[str, dict[str, FactPolicy]] = {
    "provenance.task-artifact-access": {
        "task_artifacts": {
            "fields": {
                "id": "text",
                "access": ("accessible", "missing", "denied", "unresolved"),
                "revision": "nullable_text",
                "inspection_effect": ("none", "blocks", "unknown"),
            },
            "description": "One row per declared required task artifact; performed access, identity and inspection consequence.",
        },
    },
}


def _policy(description: str, **fields: tuple[str, ...] | str) -> FactPolicy:
    return {"description": description, "fields": {"id": "text", **fields}}


FACT_POLICIES.update(
    {
        "provenance.grading-path": {
            "grading_routes": _policy(
                "Declared versus selected scorer and explicit branch/configuration identity.",
                declared_scorer="text",
                selected_scorer="nullable_text",
                configuration="nullable_text",
                branch=("resolved", "ambiguous", "omitted"),
            )
        },
        "provenance.grading-dependency-access": {
            "grading_dependencies": _policy(
                "Required scorer dependency access and reference-completeness review.",
                access=("accessible", "missing", "denied", "unresolved"),
                revision="nullable_text",
                inspection_effect=("none", "blocks", "unknown"),
                reference_review=("resolved", "unresolved"),
            )
        },
        "provenance.declared-configuration": {
            "configuration_fields": _policy(
                "Per-field source, value, reported identity, unavailable reason and claim limitation.",
                value="nullable_text",
                source_identity="nullable_text",
                availability=("sourced", "provider_hidden", "omitted"),
                claim_limit="text",
                reported_value="nullable_text",
            )
        },
        "provenance.observed-configuration": {
            "run_settings": _policy(
                "Declared/observed field pairs on an identified retained run, with difference disposition.",
                declared="text",
                observed="nullable_text",
                run_identity="nullable_text",
                disposition=("agrees", "justified", "unresolved", "invalidates"),
            )
        },
        "provenance.runtime-route": {
            "provider_validation": _policy(
                "Provider manifest validation and adapter compatibility are distinct outcomes.",
                provider="text",
                adapter_revision="nullable_text",
                manifest_identity="text",
                validation=("accepted", "incompatible", "not_run", "error"),
                adapter=("compatible", "incompatible", "unresolved"),
            )
        },
        "provenance.comparison-fairness": {
            "setup_differences": _policy(
                "Each paired configuration difference, rationale, adjudication and demonstrated advantage.",
                left="text",
                right="text",
                rationale="nullable_text",
                disposition=("justified", "unjustified", "disputed"),
                advantage=("none", "demonstrated", "unknown"),
            )
        },
        "provenance.solver-tool-access": {
            "tool_probes": _policy(
                "Required action with actual solver identity and expected/actual call, schema, reset or error outcome.",
                action="text",
                solver_identity="nullable_text",
                expected="text",
                actual="nullable_text",
                probe=("call", "schema", "reset", "error", "boundary"),
                execution=("completed", "partial", "not_run", "error"),
            )
        },
        "provenance.dependency-substitution": {
            "components": _policy(
                "Required component availability, real/substitute form, observed and approved revisions.",
                form=("real", "substitute"),
                observed_revision="nullable_text",
                approved_revision="nullable_text",
                authorization=("not_required", "approved", "unapproved", "unresolved"),
                availability=("available", "missing", "partly_reconciled", "unknown"),
            )
        },
        "provenance.external-state": {
            "external_state": _policy(
                "Material external-state identities, pin/live policy, observed drift and outcome relation.",
                captured_identity="nullable_text",
                mode=("pinned", "live"),
                live_bound="nullable_text",
                observation=("within_bound", "drifted", "capture_gap", "unknown"),
                outcome_relation=("interpretable", "invalidated", "unknown"),
            )
        },
        "provenance.evidence-revision-consistency": {
            "result_revisions": _policy(
                "Result/current identity pairs, material-change classification, reuse disposition and lineage.",
                recorded_revision="nullable_text",
                current_revision="nullable_text",
                change=("unchanged", "material", "unknown"),
                disposition=("current", "invalidated", "regraded", "stale", "combined"),
                lineage="nullable_text",
            )
        },
        "task-contract.clear-contract": {
            "contract_requirements": _policy(
                "Public versus scorer requirements with interpretation source and ambiguity disposition.",
                public_requirement="nullable_text",
                scorer_requirement="text",
                interpretation=("public", "hidden_intent", "unresolved"),
                resolution=("resolved", "ambiguous", "prevents_grading"),
            )
        },
        "task-contract.discoverable-requirements": {
            "scored_conditions": _policy(
                "Full declared scored-condition crosswalk with authority and solver access.",
                condition="text",
                authority="nullable_text",
                solver_access=("accessible", "denied", "unknown"),
                mapping=("supported", "disputed", "unmapped", "hidden"),
            )
        },
        "task-contract.scoring-authority": {
            "scoring_authorities": _policy(
                "Expected and authoritative results, obligation and semantic dispute disposition.",
                expected="text",
                authoritative="nullable_text",
                obligation="text",
                semantics=("resolved", "disputed", "contradicted"),
            )
        },
    }
)

ROBUSTNESS_POLICIES = {
    "provenance.task-artifact-access": ("inventory_reconstruction", ("inventory",)),
    "provenance.grading-path": ("route_reconstruction", ("selection",)),
    "provenance.grading-dependency-access": ("dependency_reconstruction", ("coverage", "access")),
    "provenance.declared-configuration": ("manifest_reconstruction", ("manifest", "limitations")),
    "provenance.observed-configuration": ("configuration_repeat", ("configuration", "control")),
    "provenance.runtime-route": ("compatibility_controls", ("clean_setup", "invalid_manifest")),
    "provenance.comparison-fairness": ("matched_sensitivity", ("matched_setup", "sensitivity")),
    "provenance.solver-tool-access": ("clean_access_controls", ("reset", "error", "boundary")),
    "provenance.dependency-substitution": ("provisioning_reconciliation", ("provisioning", "approval")),
    "provenance.external-state": ("replay_drift", ("replay", "deliberate_drift")),
    "provenance.evidence-revision-consistency": (
        "change_injection",
        ("changed_invalidated", "unchanged_preserved", "lineage_preserved"),
    ),
    "task-contract.clear-contract": ("boundary_solutions", ("independent_solution", "valid_boundary")),
    "task-contract.discoverable-requirements": ("blind_discovery", ("blind_discovery", "solver_access")),
    "task-contract.scoring-authority": ("authority_corroboration", ("reference", "scoring_contract")),
}
# Role-specific outcome enums, not arbitrary caller-chosen equality assertions.
ROBUSTNESS_OUTCOMES: dict[str, dict[str, tuple[str, str]]] = {
    "provenance.task-artifact-access": {"inventory": ("inventory_reconstructed", "inventory_incomplete")},
    "provenance.grading-path": {"selection": ("route_reconstructed", "route_mismatch")},
    "provenance.grading-dependency-access": {
        "coverage": ("dependencies_complete", "dependencies_incomplete"),
        "access": ("dependencies_accessible", "dependencies_inaccessible"),
    },
    "provenance.declared-configuration": {
        "manifest": ("manifest_reconstructed", "manifest_mismatch"),
        "limitations": ("limitations_preserved", "limitations_omitted"),
    },
    "provenance.observed-configuration": {
        "configuration": ("configuration_reproduced", "configuration_mismatch"),
        "control": ("configuration_control_agrees", "configuration_control_differs"),
    },
    "provenance.runtime-route": {
        "clean_setup": ("clean_setup_accepted", "clean_setup_failed"),
        "invalid_manifest": ("invalid_manifest_rejected", "invalid_manifest_accepted"),
    },
    "provenance.comparison-fairness": {
        "matched_setup": ("matched_setup_comparable", "matched_setup_incomparable"),
        "sensitivity": ("sensitivity_bounded", "sensitivity_unbounded"),
    },
    "provenance.solver-tool-access": {
        "reset": ("reset_preserved_access", "reset_broke_access"),
        "error": ("error_contract_preserved", "error_contract_violated"),
        "boundary": ("access_boundary_preserved", "access_boundary_violated"),
    },
    "provenance.dependency-substitution": {
        "provisioning": ("provisioning_reconciled", "provisioning_mismatch"),
        "approval": ("substitutions_authorized", "substitutions_unauthorized"),
    },
    "provenance.external-state": {
        "replay": ("replay_interpretable", "replay_uninterpretable"),
        "deliberate_drift": ("drift_detected", "drift_missed"),
    },
    "provenance.evidence-revision-consistency": {
        "changed_invalidated": ("changed_result_invalidated", "changed_result_reused"),
        "unchanged_preserved": ("unchanged_result_preserved", "unchanged_result_lost"),
        "lineage_preserved": ("lineage_preserved", "lineage_lost"),
    },
    "task-contract.clear-contract": {
        "independent_solution": ("public_solution_accepted", "public_solution_rejected"),
        "valid_boundary": ("valid_boundary_accepted", "valid_boundary_rejected"),
    },
    "task-contract.discoverable-requirements": {
        "blind_discovery": ("conditions_discovered", "conditions_undiscoverable"),
        "solver_access": ("authority_accessible", "authority_inaccessible"),
    },
    "task-contract.scoring-authority": {
        "reference": ("reference_agrees", "reference_contradicts"),
        "scoring_contract": ("scoring_contract_agrees", "scoring_contract_contradicts"),
    },
}

ATTACK_CLASSES = (
    "no_work",
    "all_failure",
    "inert_reference",
    "hardcoded_answer",
    "forged_success",
    "scorer_tampering",
    "answer_access",
)
CUE_CLASSES = ("cue_only", "nuisance_swap", "misleading_cue", "style_only")
_CONTAINMENT = {
    "affected_revision": "nullable_text",
    "disposition": ("quarantined", "bounded_use", "accepted", "none"),
    "residual_risk": "nullable_text",
    "repair_revision": "nullable_text",
    "replay": ("credit_withheld", "credit_awarded", "unrun"),
    "valid_after_repair": ("credited", "rejected", "unrun"),
}
_CUE = {
    "cue_class": CUE_CLASSES,
    "qualified": ("yes", "no"),
    "health": ("healthy", "error", "unrun"),
    "outcome": ("no_bypass", "bypass", "confounded"),
}
# Leakage and credit criteria (references/leakage-and-credit.json); predicates follow its anchors.
FACT_POLICIES.update(
    {
        "AUDIT-03": {
            "attack_controls": _policy(
                "Per-control attack class, applicability, expected/actual credit, health and reach.",
                attack_class=(*ATTACK_CLASSES, "valid_inaction", "realistic_wrong"),
                applicability=("applicable", "excluded"),
                expected_credit="number",
                actual_credit="number",
                health=("healthy", "error", "unrun"),
                reach=("reached", "unresolved", "unreachable"),
            )
        },
        "AUDIT-04-B": {
            "exploit_containment": _policy(
                "Each confirmed mechanism: affected revision, containment disposition and repaired-task replay.",
                **_CONTAINMENT,
            )
        },
        "AUDIT-04-B-SUITE": {
            "suite_containment": _policy(
                "Each confirmed mechanism per retained affected or new task, with disposition and replay.",
                family=("retained", "new"),
                **_CONTAINMENT,
            )
        },
        "AUDIT-05-B": {
            "exposure_inventory": _policy(
                "Exposed task/solution item, known overlap, disclosure, statement support and sensitivity.",
                exposure=("public", "private", "unknown"),
                overlap=("none", "verified", "unknown"),
                disclosed=("yes", "no"),
                statement=("within_support", "overclaims"),
                sensitivity=("computed", "missing"),
            )
        },
        "AUDIT-05-S03": {
            "selection_ledger": _policy(
                "Chronological candidate, feedback, freeze and confirmation events with holdout access.",
                event=("candidate", "feedback", "freeze", "confirmation"),
                alternatives_retained=("yes", "no"),
                holdout_access=("none", "before_freeze", "after_freeze"),
                label=("exploratory", "confirmatory"),
            )
        },
        "TRACE-04-A": {
            "material_review": _policy(
                "Eligible channel, candidate category, review, disposition and exact-use approval match.",
                channel="text",
                candidate=("secret", "proprietary", "none"),
                reviewed=("yes", "no"),
                disposition=("authorized", "withheld", "unauthorized_use", "pending"),
                approval_match=("match", "mismatch", "not_required"),
            )
        },
        "ENV-03-S01": {
            "isolation_surfaces": _policy(
                "Material state surface, declared sharing, reset verification and paired target outcomes.",
                sharing=("isolated", "intentional"),
                reset_verified=("yes", "no", "unrun"),
                target_alone="nullable_text",
                target_after="nullable_text",
                consequence=("none", "credit_change", "evidence_change", "unknown"),
            )
        },
        "CREATE-02": {"cue_controls": _policy("Qualified cue-class control outcome and health.", **_CUE)},
        "CREATE-02-SUITE": {
            "family_cue_controls": _policy(
                "Per-family cue-class control outcome, health and family authorship.",
                family="text",
                authorship=("independent", "development"),
                **_CUE,
            )
        },
    }
)
ROBUSTNESS_POLICIES.update(
    {
        "AUDIT-03": ("heldout_attacks", ("heldout_attack", "valid_work")),
        "AUDIT-04-B": ("containment_attacks", ("independent_attack",)),
        "AUDIT-04-B-SUITE": ("suite_containment_attacks", ("independent_attack", "cross_family_replay")),
        "AUDIT-05-B": ("exposure_review", ("provenance_review", "exposure_sensitivity")),
        "AUDIT-05-S03": ("confirmation_review", ("independent_confirmation", "feedback_boundary")),
        "TRACE-04-A": ("material_audit", ("independent_labels", "release_audit")),
        "ENV-03-S01": ("isolation_probes", ("parallel", "delayed_effect", "heldout_order")),
        "CREATE-02": ("cue_corroboration", ("heldout_counterexample", "new_family")),
        "CREATE-02-SUITE": ("family_corroboration", ("independent_family", "heldout_counterexample")),
    }
)
ROBUSTNESS_OUTCOMES.update(
    {
        "AUDIT-03": {
            "heldout_attack": ("heldout_credit_withheld", "heldout_credit_awarded"),
            "valid_work": ("valid_work_credited", "valid_work_rejected"),
        },
        "AUDIT-04-B": {"independent_attack": ("containment_corroborated", "containment_bypassed")},
        "AUDIT-04-B-SUITE": {
            "independent_attack": ("containment_corroborated", "containment_bypassed"),
            "cross_family_replay": ("cross_family_contained", "cross_family_bypassed"),
        },
        "AUDIT-05-B": {
            "provenance_review": ("statement_corroborated", "statement_contradicted"),
            "exposure_sensitivity": ("sensitivity_corroborated", "sensitivity_diverges"),
        },
        "AUDIT-05-S03": {
            "independent_confirmation": ("gain_confirmed", "gain_not_confirmed"),
            "feedback_boundary": ("boundary_held", "boundary_breached"),
        },
        "TRACE-04-A": {
            "independent_labels": ("labels_agree", "labels_disagree"),
            "release_audit": ("release_matches", "release_mismatch"),
        },
        "ENV-03-S01": {
            "parallel": ("parallel_isolated", "parallel_carryover"),
            "delayed_effect": ("delayed_effect_absent", "delayed_effect_observed"),
            "heldout_order": ("heldout_order_isolated", "heldout_order_carryover"),
        },
        "CREATE-02": {
            "heldout_counterexample": ("counterexample_robust", "counterexample_bypass"),
            "new_family": ("new_family_robust", "new_family_bypass"),
        },
        "CREATE-02-SUITE": {
            "independent_family": ("independent_family_robust", "independent_family_bypass"),
            "heldout_counterexample": ("counterexample_robust", "counterexample_bypass"),
        },
    }
)

ROBUSTNESS_DEFECTS = {
    "reference_contradicts",
    "scoring_contract_contradicts",
    "heldout_credit_awarded",
    "containment_bypassed",
    "cross_family_bypassed",
    "statement_contradicted",
    "boundary_breached",
    "release_mismatch",
    "parallel_carryover",
    "delayed_effect_observed",
    "heldout_order_carryover",
    "counterexample_bypass",
    "new_family_bypass",
    "independent_family_bypass",
}

for _ident, (_name, _roles) in ROBUSTNESS_POLICIES.items():
    FACT_POLICIES[_ident][_name] = _policy(
        "Additional canonical robustness controls; compare scoped expected/observed relations and actor separation.",
        role=_roles,
        expected="text",
        observed="nullable_text",
        original_actor="text",
        corroborating_actor="text",
        source_identity="text",
        execution=("completed", "unrun", "error"),
        scope=("same-task-revision", "other-revision"),
    )

REFERENCES = Path(__file__).resolve().parents[1] / "references"


def canonical_definitions() -> dict[str, dict[str, Any]]:
    """The 23 criteria of the current rubric that criterion evidence scores."""
    definitions = {}
    for name in ("artifact-configuration-provenance", "task-contract-authority", "leakage-and-credit"):
        definitions.update({item["id"]: item for item in loads((REFERENCES / f"{name}.json").read_bytes())["items"]})
    return definitions


def value_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def definitions_digest() -> str:
    return value_digest(
        {
            "definitions": canonical_definitions(),
            "sources": {
                name: hashlib.sha256((REFERENCES / name).read_bytes()).hexdigest()
                for name in (
                    "artifact-configuration-provenance.json",
                    "artifact-configuration-provenance.md",
                    "task-contract-authority.json",
                    "task-contract-authority.md",
                    "leakage-and-credit.json",
                    "leakage-and-credit.md",
                )
            },
        }
    )


def _empty_rows() -> dict[str, dict[str, Any]]:
    return {
        ident: {
            "item_id": ident,
            "title": definition["title"],
            "level": definition.get("level", "task"),
            **({"source": deepcopy(definition["source"])} if "source" in definition else {}),
            "selected": False,
            "assessment_scope": None,
            "assessment_state": "NOT_ASSESSED",
            "execution_state": "NOT_RUN",
            "score_provenance": {"kind": "unselected"},
            "status": "NOT_ASSESSED",
            "score": None,
            "evidence_strength": None,
            "evidence_refs": [],
            "check_results": [
                {
                    "check_id": check["id"],
                    **({"source_id": check["source_id"]} if "source_id" in check else {}),
                    "outcome": "NOT_ASSESSED",
                    "method": check["method"],
                }
                for check in definition["checks"]
            ],
            "remaining_gaps": ["No source-bound criterion evidence selected."],
            "limitations": ["Retained facts do not authenticate reviewers or native execution."],
        }
        for ident, definition in canonical_definitions().items()
    }


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("malformed criterion evidence object")
    return value


class EvidenceReader:
    def __init__(self, root: Path, selection: dict[str, Any]):
        self.root = root.absolute()
        records = selection.get("artifacts")
        if not isinstance(records, list) or len(records) > MAX_FILES:
            raise ValueError("invalid artifact inventory")
        self.records: dict[str, dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict) or not _text(record.get("id")) or record["id"] in self.records:
                raise ValueError("ambiguous artifact identity")
            self.records[record["id"]] = record
        self.access = SelectedAccess(records)
        self.raw: dict[str, bytes] = {}
        self.documents: dict[str, Any] = {}

    def document(self, ident: str) -> Any:
        if not isinstance(ident, str) or ident not in self.records:
            raise ValueError("unselected evidence artifact")
        if ident not in self.documents:
            record = self.records[ident]
            digest = record.get("digest")
            if (
                record.get("access") != "available"
                or self.access.reason(record.get("path"))
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError("unavailable or invalid evidence identity")
            data, reason = read_selected(self.root, record.get("path"))
            if reason or data is None or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("missing, changed or unsafe evidence source")
            self.raw[ident] = data
            if sum(map(len, self.raw.values())) > MAX_TOTAL_BYTES:
                raise ValueError("evidence budget exceeded")
            self.documents[ident] = loads(data)
        return self.documents[ident]

    def reference(self, reference: Any) -> Any:
        ref = _object(reference, {"artifact_id", "pointer", "digest"})
        value = self.document(ref["artifact_id"])
        pointer = ref["pointer"]
        if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
            raise ValueError("invalid evidence pointer")
        if pointer:
            for part in pointer[1:].split("/"):
                if re.search(r"~(?![01])", part):
                    raise ValueError("invalid pointer escape")
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict) and part in value:
                    value = value[part]
                elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and int(part) < len(value):
                    value = value[int(part)]
                else:
                    raise ValueError("evidence reference does not exist")
        if value_digest(value) != ref["digest"]:
            raise ValueError("evidence content binding mismatch")
        return value

    def revalidate(self) -> None:
        for ident, original in self.raw.items():
            if self.access.reason(self.records[ident]["path"]):
                raise ValueError("unavailable evidence source")
            current, reason = read_selected(self.root, self.records[ident]["path"])
            if reason or current != original:
                raise ValueError("evidence changed during assessment")


def _validate_fact(value: Any, policy: FactPolicy) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value or len(value) > 256:
        raise ValueError("fact must be a nonempty bounded row inventory or null")
    ids = set()
    for row in value:
        _object(row, set(policy["fields"]))
        for name, kind in policy["fields"].items():
            entry = row[name]
            if isinstance(kind, tuple):
                valid = type(entry) is str and entry in kind
            elif kind == "nullable_text":
                valid = entry is None or _text(entry)
            elif kind == "number":
                valid = type(entry) in (int, float) and abs(entry) < 1e12
            else:
                valid = _text(entry)
            if not valid:
                raise ValueError("invalid typed fact field")
        if row["id"] in ids:
            raise ValueError("duplicate or contradictory fact identity")
        ids.add(row["id"])


def _derive(ident: str, facts: dict[str, Any]) -> int | None:
    name = next(iter(FACT_POLICIES[ident]))
    inventory = facts.get(name)
    if not inventory:
        return None
    for r in inventory:
        contradictory = (
            ident == "provenance.observed-configuration"
            and r["disposition"] == "agrees"
            and r["declared"] != r["observed"]
            or ident == "provenance.evidence-revision-consistency"
            and r["change"] == "unchanged"
            and r["recorded_revision"] != r["current_revision"]
            or ident == "provenance.dependency-substitution"
            and r["form"] == "real"
            and (r["authorization"] != "not_required" or r["approved_revision"] is not None)
        )
        if contradictory:
            raise ValueError("contradictory source observations")
    if ident in ("provenance.task-artifact-access", "provenance.grading-dependency-access"):
        if any(r["access"] in ("missing", "denied") and r["inspection_effect"] == "blocks" for r in inventory):
            return 0
        if all(
            r["access"] == "accessible" and r["revision"] and r.get("reference_review", "resolved") == "resolved"
            for r in inventory
        ):
            return 2
        if any(r["access"] == "accessible" for r in inventory):
            return 1
    elif ident == "provenance.grading-path":
        if any(
            r["selected_scorer"] and r["declared_scorer"] != r["selected_scorer"] or r["branch"] == "omitted"
            for r in inventory
        ):
            return 0
        if all(r["selected_scorer"] and r["configuration"] and r["branch"] == "resolved" for r in inventory):
            return 2
        if any(r["selected_scorer"] for r in inventory):
            return 1
    elif ident == "provenance.declared-configuration":
        if any(r["value"] != r["reported_value"] and r["claim_limit"] == "uninterpretable" for r in inventory):
            return 0
        if all(
            (
                r["availability"] == "sourced"
                and r["value"] is not None
                and r["value"] == r["reported_value"]
                and r["source_identity"]
            )
            or (r["availability"] == "provider_hidden" and r["claim_limit"] != "none")
            for r in inventory
        ):
            return 2
        if any(r["value"] is not None for r in inventory):
            return 1
    elif ident == "provenance.observed-configuration":
        if not any(r["run_identity"] for r in inventory):
            return None
        if any(
            r["run_identity"]
            and r["observed"] is not None
            and r["declared"] != r["observed"]
            and r["disposition"] == "invalidates"
            for r in inventory
        ):
            return 0
        if all(
            r["run_identity"]
            and r["observed"] is not None
            and (r["declared"] == r["observed"] or r["disposition"] == "justified")
            for r in inventory
        ):
            return 2
        if any(r["run_identity"] and r["observed"] is not None for r in inventory):
            return 1
    elif ident == "provenance.runtime-route":
        if any(r["validation"] == "incompatible" or r["adapter"] == "incompatible" for r in inventory):
            return 0
        if all(
            r["validation"] == "accepted" and r["adapter"] == "compatible" and r["adapter_revision"] for r in inventory
        ):
            return 2
        if any(r["validation"] == "accepted" for r in inventory):
            return 1
    elif ident == "provenance.comparison-fairness":
        if any(
            r["left"] != r["right"] and r["disposition"] == "unjustified" and r["advantage"] == "demonstrated"
            for r in inventory
        ):
            return 0
        if all(r["disposition"] == "justified" and r["rationale"] for r in inventory):
            return 2
        if any(r["disposition"] in ("disputed", "unjustified") for r in inventory):
            return 1
    elif ident == "provenance.solver-tool-access":
        reached = [
            r for r in inventory if r["solver_identity"] and r["execution"] == "completed" and r["actual"] is not None
        ]
        if any(r["actual"] is not None and r["actual"] != r["expected"] for r in reached):
            return 0
        if len(reached) == len(inventory) and all(r["actual"] == r["expected"] for r in reached):
            return 2
        if reached or any(r["execution"] == "partial" and r["actual"] == r["expected"] for r in inventory):
            return 1
    elif ident == "provenance.dependency-substitution":
        if any(
            r["availability"] == "missing" or (r["form"] == "substitute" and r["authorization"] == "unapproved")
            for r in inventory
        ):
            return 0
        if all(
            r["availability"] == "available"
            and r["observed_revision"]
            and (
                r["form"] == "real"
                or (r["authorization"] == "approved" and r["approved_revision"] == r["observed_revision"])
            )
            for r in inventory
        ):
            return 2
        if any(r["observed_revision"] and r["availability"] != "unknown" for r in inventory):
            return 1
    elif ident == "provenance.external-state":
        if any(r["observation"] == "drifted" and r["outcome_relation"] == "invalidated" for r in inventory):
            return 0
        if all(
            r["captured_identity"]
            and (r["mode"] == "pinned" or r["live_bound"])
            and r["observation"] == "within_bound"
            and r["outcome_relation"] == "interpretable"
            for r in inventory
        ):
            return 2
        if any(r["captured_identity"] for r in inventory):
            return 1
    elif ident == "provenance.evidence-revision-consistency":
        if any(
            r["recorded_revision"] != r["current_revision"]
            and r["change"] == "material"
            and r["disposition"] == "combined"
            for r in inventory
        ):
            return 0
        if all(
            r["recorded_revision"]
            and r["current_revision"]
            and r["lineage"]
            and (
                (r["recorded_revision"] == r["current_revision"] and r["disposition"] == "current")
                or (r["change"] == "material" and r["disposition"] in ("invalidated", "regraded"))
            )
            for r in inventory
        ):
            return 2
        if any(r["recorded_revision"] for r in inventory):
            return 1
    elif ident == "task-contract.clear-contract":
        if any(
            r["public_requirement"] != r["scorer_requirement"] and r["resolution"] == "prevents_grading"
            for r in inventory
        ):
            return 0
        if all(
            r["public_requirement"] and r["interpretation"] == "public" and r["resolution"] == "resolved"
            for r in inventory
        ):
            return 2
        if any(r["resolution"] == "ambiguous" for r in inventory):
            return 1
    elif ident == "task-contract.discoverable-requirements":
        if any(r["mapping"] == "hidden" and r["solver_access"] == "denied" for r in inventory):
            return 0
        if all(
            r["authority"] and r["solver_access"] == "accessible" and r["mapping"] == "supported" for r in inventory
        ):
            return 2
        if any(r["mapping"] in ("disputed", "unmapped") for r in inventory):
            return 1
    elif ident == "task-contract.scoring-authority":
        if any(r["authoritative"] is not None and r["expected"] != r["authoritative"] for r in inventory):
            return 0
        if all(r["authoritative"] and r["semantics"] == "resolved" for r in inventory):
            return 2
        if any(r["authoritative"] and r["semantics"] == "resolved" for r in inventory) and any(
            r["semantics"] == "disputed" for r in inventory
        ):
            return 1
    elif ident == "AUDIT-03":
        applicable = [r for r in inventory if r["applicability"] == "applicable"]
        reached = [r for r in applicable if r["health"] == "healthy" and r["reach"] == "reached"]
        # 0: a healthy control obtains forbidden credit.
        if any(r["actual_credit"] > r["expected_credit"] for r in reached):
            return 0
        # 2: every declared class is dispositioned and every applicable control gets its intended score.
        if (
            set(ATTACK_CLASSES) | {"valid_inaction", "realistic_wrong"} <= {r["attack_class"] for r in inventory}
            and len(reached) == len(applicable)
            and all(r["actual_credit"] == r["expected_credit"] for r in reached)
        ):
            return 2
        if reached:
            return 1
    elif ident in ("AUDIT-04-B", "AUDIT-04-B-SUITE"):
        contained = ("quarantined", "bounded_use")
        # 0: a confirmed exploit is accepted uncontained, or a healthy repair replay still earns credit.
        if any(r["disposition"] not in contained or r["replay"] == "credit_awarded" for r in inventory):
            return 0
        verified = all(
            r["affected_revision"]
            and r["residual_risk"]
            and r["repair_revision"]
            and r["replay"] == "credit_withheld"
            and r["valid_after_repair"] == "credited"
            for r in inventory
        )
        if verified and (ident == "AUDIT-04-B" or {r["family"] for r in inventory} == {"retained", "new"}):
            return 2
        return 1
    elif ident == "AUDIT-05-B":
        # 0: verified exposure contradicts the statement or is concealed.
        if any(
            r["overlap"] == "verified" and (r["disclosed"] == "no" or r["statement"] == "overclaims") for r in inventory
        ):
            return 0
        if all(
            r["disclosed"] == "yes" and r["statement"] == "within_support" and r["sensitivity"] == "computed"
            for r in inventory
        ):
            return 2
        return 1
    elif ident == "AUDIT-05-S03":
        events = {r["event"] for r in inventory}
        exposed = any(r["holdout_access"] == "before_freeze" for r in inventory)
        # 0: development-visible feedback is presented as untouched confirmation.
        if exposed and any(r["event"] == "confirmation" and r["label"] == "confirmatory" for r in inventory):
            return 0
        if (
            {"freeze", "confirmation"} <= events
            and not exposed
            and all(r["alternatives_retained"] == "yes" for r in inventory)
            and all(r["label"] == "exploratory" for r in inventory if r["event"] != "confirmation")
        ):
            return 2
        return 1
    elif ident == "TRACE-04-A":
        if any(
            r["disposition"] == "unauthorized_use"
            or (r["disposition"] == "authorized" and r["approval_match"] == "mismatch")
            for r in inventory
        ):
            return 0
        if all(
            r["reviewed"] == "yes"
            and r["disposition"] != "pending"
            and (r["disposition"] != "authorized" or r["approval_match"] == "match")
            for r in inventory
        ):
            return 2
        if any(r["reviewed"] == "yes" for r in inventory):
            return 1
    elif ident == "ENV-03-S01":
        # 0: confirmed unintended persistence changes evidence or grades.
        if any(
            r["sharing"] == "isolated"
            and r["target_alone"] is not None
            and r["target_after"] is not None
            and r["target_alone"] != r["target_after"]
            and r["consequence"] in ("credit_change", "evidence_change")
            for r in inventory
        ):
            return 0
        if all(
            r["consequence"] == "none"
            and (
                r["sharing"] == "intentional"
                or (
                    r["reset_verified"] == "yes"
                    and r["target_alone"] is not None
                    and r["target_alone"] == r["target_after"]
                )
            )
            for r in inventory
        ):
            return 2
        if any(r["reset_verified"] == "yes" for r in inventory):
            return 1
    elif ident in ("CREATE-02", "CREATE-02-SUITE"):
        healthy = [r for r in inventory if r["health"] == "healthy" and r["qualified"] == "yes"]
        # 0: a healthy qualified control succeeds through a task-irrelevant cue.
        if any(r["outcome"] == "bypass" for r in healthy):
            return 0
        if ident == "CREATE-02":
            groups = [inventory]
        else:
            families = {r["family"] for r in inventory if r["authorship"] == "independent"}
            groups = [[r for r in inventory if r["family"] == family] for family in sorted(families)]
        if groups and all(
            {r["cue_class"] for r in group} == set(CUE_CLASSES)
            and all(r in healthy and r["outcome"] == "no_bypass" for r in group)
            for group in groups
        ):
            return 2
        if healthy:
            return 1
    return None


def assess_evidence(root: Path, selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _empty_rows()
    if "criterion_evidence" not in selection:
        return rows
    selector = _object(selection["criterion_evidence"], {"artifact_id"})
    reader = EvidenceReader(root, selection)
    packet = _object(
        reader.document(selector["artifact_id"]),
        {
            "schema",
            "subject",
            "definitions_digest",
            "assessment_scope",
            "items",
        },
    )
    if packet["schema"] != "rubric-criterion-evidence-v1" or packet["definitions_digest"] != definitions_digest():
        raise ValueError("unsupported or stale criterion definitions")
    scope = packet["assessment_scope"]
    if scope not in ("recorded-evidence-assessment", "synthetic-scoring-demonstration"):
        raise ValueError("invalid assessment scope")
    subject = _object(packet["subject"], {"artifact_id", "digest"})
    reader.document(subject["artifact_id"])
    task = reader.records[subject["artifact_id"]]
    if task["digest"] != subject["digest"] or task.get("role") != "task":
        raise ValueError("task subject mismatch")
    if not isinstance(packet["items"], list) or not packet["items"] or len(packet["items"]) > len(FACT_POLICIES):
        raise ValueError("invalid criterion selection")
    seen = set()
    for item in packet["items"]:
        _object(item, {"item_id", "facts"})
        ident = item["item_id"]
        if not isinstance(ident, str) or ident not in rows or ident in seen:
            raise ValueError("unknown or duplicate criterion")
        seen.add(ident)
        if not isinstance(item["facts"], dict) or item["facts"].keys() - FACT_POLICIES.get(ident, {}).keys():
            raise ValueError("unknown criterion fact")
        facts: dict[str, Any] = {}
        fact_results = []
        for name, record in item["facts"].items():
            _object(record, {"basis", "source", "support"})
            if record["basis"] not in ("recorded_judgment", "json_observation"):
                raise ValueError("unsupported fact basis")
            value = reader.reference(record["source"])
            source = _object(
                reader.document(record["source"]["artifact_id"]),
                {
                    "schema",
                    "subject",
                    "definitions_digest",
                    "provenance",
                    "facts",
                },
            )
            if (
                source["schema"] != "rubric-fact-source-v1"
                or source["subject"] != subject
                or source["definitions_digest"] != packet["definitions_digest"]
            ):
                raise ValueError("fact subject or definitions mismatch")
            provenance = _object(source["provenance"], {"kind", "actor_id", "activity", "limitations"})
            if provenance["kind"] not in ("synthetic", "agent", "human", "tool") or not _text(provenance["actor_id"]):
                raise ValueError("invalid authorship provenance")
            if (
                provenance["activity"] not in ("review", "observation")
                or not isinstance(provenance["limitations"], list)
                or not provenance["limitations"]
                or not all(_text(v) for v in provenance["limitations"])
            ):
                raise ValueError("missing provenance limits")
            if (provenance["kind"] == "synthetic") != (scope == "synthetic-scoring-demonstration"):
                raise ValueError("synthetic evidence cannot qualify a real task")
            if record["basis"] == "json_observation" and provenance["activity"] != "observation":
                raise ValueError("review cannot masquerade as observation")
            if not isinstance(record["support"], list) or not record["support"] or len(record["support"]) > 64:
                raise ValueError("missing underlying evidence")
            if record["source"]["pointer"] != "/facts/" + name:
                raise ValueError("fact source pointer does not name the selected fact")
            for support in record["support"]:
                _object(support, {"artifact_id", "pointer", "digest"})
                supported = reader.reference(support)
                support_record = reader.records[support["artifact_id"]]
                if any(
                    support_record["path"] == reader.records[ident]["path"]
                    or support_record["digest"] == reader.records[ident]["digest"]
                    for ident in (record["source"]["artifact_id"], selector["artifact_id"])
                ):
                    raise ValueError("fact assertion cannot support itself")
                if supported is None or supported == "" or supported == [] or supported == {}:
                    raise ValueError("empty underlying evidence")
            _validate_fact(value, FACT_POLICIES[ident][name])
            facts[name] = value
            fact_results.append(
                {
                    "name": name,
                    "basis": record["basis"],
                    "value": value,
                    "source": record["source"],
                    "support": record["support"],
                    "provenance": provenance,
                }
            )
        candidate = _derive(ident, facts)
        robust_name, roles = ROBUSTNESS_POLICIES[ident]
        controls = facts.get(robust_name)
        for control in controls or []:
            success, failure = ROBUSTNESS_OUTCOMES[ident][control["role"]]
            if control["expected"] != success or control["observed"] not in (success, failure, None):
                raise ValueError("invalid criterion-specific robustness outcome")
            if (
                control["observed"] == failure
                and control["execution"] == "completed"
                and control["scope"] == "same-task-revision"
            ):
                if failure in ROBUSTNESS_DEFECTS:
                    candidate = 0
                elif candidate == 2:
                    candidate = 1
        if (
            candidate == 2
            and controls
            and set(roles) <= {r["role"] for r in controls}
            and all(
                r["observed"] == ROBUSTNESS_OUTCOMES[ident][r["role"]][0]
                and r["execution"] == "completed"
                and r["scope"] == "same-task-revision"
                and (
                    ident in ("provenance.comparison-fairness", "provenance.evidence-revision-consistency")
                    or r["original_actor"] != r["corroborating_actor"]
                )
                for r in controls
            )
        ):
            candidate = 3
        synthetic = scope == "synthetic-scoring-demonstration"
        # One rule for simulated and recorded evidence; only the label and strength differ.
        score = candidate
        row = rows[ident]
        row.update(
            status="ASSESSED" if score is not None else "NOT_ASSESSED",
            assessment_state="ASSESSED" if score is not None else "NOT_ASSESSED",
            execution_state="NOT_RUN",
            score=score,
            assessment_scope=scope,
            selected=True,
            subject_id=subject["artifact_id"],
            subject_revision="sha256:" + subject["digest"],
            evidence_strength=None if score is None else "E1" if synthetic else "E3" if score == 3 else "E2",
            fact_results=fact_results,
            reviewer_authenticated=False,
            native_execution_verified=False,
            score_provenance={
                "kind": "synthetic-anchor-application" if synthetic else "recorded-anchor-application",
                "definitions_digest": packet["definitions_digest"],
                "packet_artifact_id": selector["artifact_id"],
            },
            measurements={
                name: {
                    "rows": value,
                    "denominator": len(value) if value else 0,
                    "unit": "declared inventory rows",
                    "missingness": "unknown" if value is None else "undeclared scope not inferred",
                }
                for name, value in facts.items()
            },
            evidence_refs=[
                selector["artifact_id"],
                subject["artifact_id"],
                *sorted({f["source"]["artifact_id"] for f in fact_results}),
            ],
            remaining_gaps=[] if score is not None else ["Required facts are missing; no anchor applies."],
        )
        row["limitations"].append(
            "Simulated evidence."
            if synthetic
            else "Facts are content-bound to selected artifacts; recorded reviewer identities are not authenticated."
        )
        for check in row["check_results"]:
            check.update(
                outcome="RECORDED" if fact_results else "NOT_ASSESSED",
                execution_state="NOT_RUN",
                evidence_refs=row["evidence_refs"],
            )
    reader.revalidate()
    return rows
