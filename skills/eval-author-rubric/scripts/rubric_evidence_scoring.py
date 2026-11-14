# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Source-bound canonical scoring; selected documents are data, never code.

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

Retained review inventories currently produce NOT_ASSESSED/E0 for real tasks:
no numeric production score is inferred from their asserted semantics. Only the
explicit synthetic scope exercises numeric rules (0-3). Thus this is a scoring
rule demonstrator and source-bound review recorder, NOT fourteen new native
measurement emitters. Integrators must keep demonstration rows outside task
qualification and must not replace legacy task rows with them.

Recorded judgments are conditional source records, not authenticated reviews.
JSON extraction proves the retained value, not native execution or semantic
truth. Synthetic vectors exercise anchor application with E1 and a separate
demonstration scope; they NEVER establish E2 execution or E3 independence.
Real anti-cheat acceptance retains its E2/E3 floors; native acceptance must come
from an owning validated emitter, not this recording protocol. This module does
not override legacy emitters; the integrating caller must reject numeric
conflicts and retain legacy measured provenance on agreement.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, TypedDict

from eval_author_shared.task_evidence import loads
from rubric_identity import MAX_FILES, MAX_TOTAL_BYTES, SelectedAccess, read_selected


class FactPolicy(TypedDict):
    fields: dict[str, tuple[str, ...] | str]
    description: str


FACT_POLICIES: dict[str, dict[str, FactPolicy]] = {
    "DISC-01-A": {
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
        "DISC-01-B": {
            "grading_routes": _policy(
                "Declared versus selected scorer and explicit branch/configuration identity.",
                declared_scorer="text",
                selected_scorer="nullable_text",
                configuration="nullable_text",
                branch=("resolved", "ambiguous", "omitted"),
            )
        },
        "DISC-01-C": {
            "grading_dependencies": _policy(
                "Required scorer dependency access and reference-completeness review.",
                access=("accessible", "missing", "denied", "unresolved"),
                revision="nullable_text",
                inspection_effect=("none", "blocks", "unknown"),
                reference_review=("resolved", "unresolved"),
            )
        },
        "DISC-02-A": {
            "configuration_fields": _policy(
                "Per-field source, value, reported identity, unavailable reason and claim limitation.",
                value="nullable_text",
                source_identity="nullable_text",
                availability=("sourced", "provider_hidden", "omitted"),
                claim_limit="text",
                reported_value="nullable_text",
            )
        },
        "DISC-02-B": {
            "run_settings": _policy(
                "Declared/observed field pairs on an identified retained run, with difference disposition.",
                declared="text",
                observed="nullable_text",
                run_identity="nullable_text",
                disposition=("agrees", "justified", "unresolved", "invalidates"),
            )
        },
        "DISC-03-A": {
            "provider_validation": _policy(
                "Provider manifest validation and adapter compatibility are distinct outcomes.",
                provider="text",
                adapter_revision="nullable_text",
                manifest_identity="text",
                validation=("accepted", "incompatible", "not_run", "error"),
                adapter=("compatible", "incompatible", "unresolved"),
            )
        },
        "AUD-02": {
            "setup_differences": _policy(
                "Each paired configuration difference, rationale, adjudication and demonstrated advantage.",
                left="text",
                right="text",
                rationale="nullable_text",
                disposition=("justified", "unjustified", "disputed"),
                advantage=("none", "demonstrated", "unknown"),
            )
        },
        "ENV-01": {
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
        "ENV-02-A": {
            "components": _policy(
                "Required component availability, real/substitute form, observed and approved revisions.",
                form=("real", "substitute"),
                observed_revision="nullable_text",
                approved_revision="nullable_text",
                authorization=("not_required", "approved", "unapproved", "unresolved"),
                availability=("available", "missing", "partly_reconciled", "unknown"),
            )
        },
        "ENV-03": {
            "external_state": _policy(
                "Material external-state identities, pin/live policy, observed drift and outcome relation.",
                captured_identity="nullable_text",
                mode=("pinned", "live"),
                live_bound="nullable_text",
                observation=("within_bound", "drifted", "capture_gap", "unknown"),
                outcome_relation=("interpretable", "invalidated", "unknown"),
            )
        },
        "CREATE-07": {
            "result_revisions": _policy(
                "Result/current identity pairs, material-change classification, reuse disposition and lineage.",
                recorded_revision="nullable_text",
                current_revision="nullable_text",
                change=("unchanged", "material", "unknown"),
                disposition=("current", "invalidated", "regraded", "stale", "combined"),
                lineage="nullable_text",
            )
        },
        "CREATE-03": {
            "contract_requirements": _policy(
                "Public versus scorer requirements with interpretation source and ambiguity disposition.",
                public_requirement="nullable_text",
                scorer_requirement="text",
                interpretation=("public", "hidden_intent", "unresolved"),
                resolution=("resolved", "ambiguous", "prevents_grading"),
            )
        },
        "CREATE-04": {
            "scored_conditions": _policy(
                "Full declared scored-condition crosswalk with authority and solver access.",
                condition="text",
                authority="nullable_text",
                solver_access=("accessible", "denied", "unknown"),
                mapping=("supported", "disputed", "unmapped", "hidden"),
            )
        },
        "CREATE-05-A": {
            "scoring_authorities": _policy(
                "Expected and authoritative results, obligation and semantic dispute disposition.",
                expected="text",
                authoritative="nullable_text",
                obligation="text",
                semantics=("resolved", "disputed", "contradicted"),
            )
        },
        "AC-01": {
            "information_surfaces": _policy(
                "Declared protected/permitted surface, actual solver read, permitted neighbor and exposure consequence.",
                contract_role=("protected", "permitted"),
                solver_read=("readable", "denied", "unrun"),
                permitted_neighbor=("readable", "denied", "unrun"),
                exposure=("none", "unintended", "unknown"),
                review=("complete", "partial", "unrun"),
            )
        },
        "AC-02": {
            "credit_controls": _policy(
                "Attack/reference/incorrect/alternative expected and actual credit, health and reach (per-check rows required by scope).",
                **{
                    "class": ("attack", "reference", "incorrect", "valid_alternative"),
                    "expected_credit": "number",
                    "actual_credit": "number",
                    "health": ("healthy", "error", "unrun"),
                    "reach": ("reached", "unresolved", "unreachable"),
                    "applicability": ("applicable", "excluded"),
                },
            )
        },
        "AC-03": {
            "receipt_controls": _policy(
                "Receipt authenticity/currentness class, owning validator result and consumer credit/rejection.",
                receipt_kind=("current", "forged", "stale", "mismatched"),
                consumer=("credited", "rejected", "unresolved"),
                native_validation=("valid", "invalid", "unrun"),
                health=("healthy", "error", "unrun"),
                reach=("reached", "unresolved", "unreachable"),
            )
        },
        "AC-04": {
            "reset_surfaces": _policy(
                "Expected/actual reset content and paired target credit, intentional sharing and leak consequence.",
                sharing=("isolated", "intentional"),
                expected_state="text",
                reset_state="nullable_text",
                target_alone="nullable_text",
                target_after="nullable_text",
                consequence=("none", "answer_exposure", "credit_change", "unknown"),
                inspection=("complete", "partial", "unrun"),
            )
        },
    }
)

ROBUSTNESS_POLICIES = {
    "DISC-01-A": ("inventory_reconstruction", ("inventory",)),
    "DISC-01-B": ("route_reconstruction", ("selection",)),
    "DISC-01-C": ("dependency_reconstruction", ("coverage", "access")),
    "DISC-02-A": ("manifest_reconstruction", ("manifest", "limitations")),
    "DISC-02-B": ("configuration_repeat", ("configuration", "control")),
    "DISC-03-A": ("compatibility_controls", ("clean_setup", "invalid_manifest")),
    "AUD-02": ("matched_sensitivity", ("matched_setup", "sensitivity")),
    "ENV-01": ("clean_access_controls", ("reset", "error", "boundary")),
    "ENV-02-A": ("provisioning_reconciliation", ("provisioning", "approval")),
    "ENV-03": ("replay_drift", ("replay", "deliberate_drift")),
    "CREATE-07": ("change_injection", ("changed_invalidated", "unchanged_preserved", "lineage_preserved")),
    "CREATE-03": ("boundary_solutions", ("independent_solution", "valid_boundary")),
    "CREATE-04": ("blind_discovery", ("blind_discovery", "solver_access")),
    "CREATE-05-A": ("authority_corroboration", ("reference", "scoring_contract")),
    "AC-01": ("heldout_access", ("independent_path", "heldout", "permitted_neighbor")),
    "AC-02": ("heldout_credit", ("heldout_attack", "valid_alternative")),
    "AC-03": ("consumer_boundary", ("invalid_rejection", "valid_acceptance")),
    "AC-04": ("trial_order_controls", ("order", "session")),
}
# Role-specific outcome enums, not arbitrary caller-chosen equality assertions.
ROBUSTNESS_OUTCOMES: dict[str, dict[str, tuple[str, str]]] = {
    "DISC-01-A": {"inventory": ("inventory_reconstructed", "inventory_incomplete")},
    "DISC-01-B": {"selection": ("route_reconstructed", "route_mismatch")},
    "DISC-01-C": {
        "coverage": ("dependencies_complete", "dependencies_incomplete"),
        "access": ("dependencies_accessible", "dependencies_inaccessible"),
    },
    "DISC-02-A": {
        "manifest": ("manifest_reconstructed", "manifest_mismatch"),
        "limitations": ("limitations_preserved", "limitations_omitted"),
    },
    "DISC-02-B": {
        "configuration": ("configuration_reproduced", "configuration_mismatch"),
        "control": ("configuration_control_agrees", "configuration_control_differs"),
    },
    "DISC-03-A": {
        "clean_setup": ("clean_setup_accepted", "clean_setup_failed"),
        "invalid_manifest": ("invalid_manifest_rejected", "invalid_manifest_accepted"),
    },
    "AUD-02": {
        "matched_setup": ("matched_setup_comparable", "matched_setup_incomparable"),
        "sensitivity": ("sensitivity_bounded", "sensitivity_unbounded"),
    },
    "ENV-01": {
        "reset": ("reset_preserved_access", "reset_broke_access"),
        "error": ("error_contract_preserved", "error_contract_violated"),
        "boundary": ("access_boundary_preserved", "access_boundary_violated"),
    },
    "ENV-02-A": {
        "provisioning": ("provisioning_reconciled", "provisioning_mismatch"),
        "approval": ("substitutions_authorized", "substitutions_unauthorized"),
    },
    "ENV-03": {
        "replay": ("replay_interpretable", "replay_uninterpretable"),
        "deliberate_drift": ("drift_detected", "drift_missed"),
    },
    "CREATE-07": {
        "changed_invalidated": ("changed_result_invalidated", "changed_result_reused"),
        "unchanged_preserved": ("unchanged_result_preserved", "unchanged_result_lost"),
        "lineage_preserved": ("lineage_preserved", "lineage_lost"),
    },
    "CREATE-03": {
        "independent_solution": ("public_solution_accepted", "public_solution_rejected"),
        "valid_boundary": ("valid_boundary_accepted", "valid_boundary_rejected"),
    },
    "CREATE-04": {
        "blind_discovery": ("conditions_discovered", "conditions_undiscoverable"),
        "solver_access": ("authority_accessible", "authority_inaccessible"),
    },
    "CREATE-05-A": {
        "reference": ("reference_agrees", "reference_contradicts"),
        "scoring_contract": ("scoring_contract_agrees", "scoring_contract_contradicts"),
    },
    "AC-01": {
        "independent_path": ("protected_read_denied", "protected_read_allowed"),
        "heldout": ("heldout_read_denied", "heldout_read_allowed"),
        "permitted_neighbor": ("permitted_read_allowed", "permitted_read_denied"),
    },
    "AC-02": {
        "heldout_attack": ("forbidden_credit_withheld", "forbidden_credit_awarded"),
        "valid_alternative": ("valid_alternative_credited", "valid_alternative_rejected"),
    },
    "AC-03": {
        "invalid_rejection": ("invalid_receipt_rejected", "invalid_receipt_credited"),
        "valid_acceptance": ("valid_receipt_credited", "valid_receipt_rejected"),
    },
    "AC-04": {
        "order": ("order_isolation_preserved", "order_carryover_observed"),
        "session": ("session_isolation_preserved", "session_carryover_observed"),
    },
}

ROBUSTNESS_DEFECTS = {
    "protected_read_allowed",
    "heldout_read_allowed",
    "forbidden_credit_awarded",
    "invalid_receipt_credited",
    "order_carryover_observed",
    "session_carryover_observed",
    "reference_contradicts",
    "scoring_contract_contradicts",
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
    definitions = {}
    for name in ("artifact-configuration-provenance", "task-contract-authority"):
        document = loads((REFERENCES / f"{name}.json").read_bytes())
        definitions.update({item["id"]: item for item in document["items"]})
    text = (REFERENCES / "anti-cheat.md").read_text()
    for section in re.split(r"(?m)^## ", text)[1:]:
        heading, body = section.split("\n", 1)
        if not heading.startswith("AC-"):
            continue
        ident, title = heading.split(": ", 1)
        scoring_text = body.split("**Scoring:** ", 1)[1].split("\n\n", 1)[0]
        scoring_text = " ".join(scoring_text.split())
        anchors = re.split(r"(?:^| )([0-3]): ", scoring_text)[1:]
        definitions[ident] = {
            "id": ident,
            "title": title,
            "scoring": dict(zip(anchors[::2], anchors[1::2], strict=True)),
            "checks": [
                {"id": f"{ident}:{method}", "method": method, "action": action.strip()}
                for method, action in re.findall(
                    r"- (Deterministic|Review|Probe): (.*?)(?=\n- |\n\n\*\*Scoring:)", body, re.S
                )
            ],
        }
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
                    "anti-cheat.md",
                )
            },
        }
    )


def _empty_rows() -> dict[str, dict[str, Any]]:
    return {
        ident: {
            "item_id": ident,
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
                {"check_id": check["id"], "outcome": "NOT_ASSESSED", "method": check["method"]}
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


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
            ident == "DISC-02-B"
            and r["disposition"] == "agrees"
            and r["declared"] != r["observed"]
            or ident == "CREATE-07"
            and r["change"] == "unchanged"
            and r["recorded_revision"] != r["current_revision"]
            or ident == "AC-01"
            and r["exposure"] == "unintended"
            and r["solver_read"] != "readable"
            or ident == "ENV-02-A"
            and r["form"] == "real"
            and (r["authorization"] != "not_required" or r["approved_revision"] is not None)
        )
        if contradictory:
            raise ValueError("contradictory source observations")
    if ident in ("DISC-01-A", "DISC-01-C"):
        if any(r["access"] in ("missing", "denied") and r["inspection_effect"] == "blocks" for r in inventory):
            return 0
        if all(
            r["access"] == "accessible" and r["revision"] and r.get("reference_review", "resolved") == "resolved"
            for r in inventory
        ):
            return 2
        if any(r["access"] == "accessible" for r in inventory):
            return 1
    elif ident == "DISC-01-B":
        if any(
            r["selected_scorer"] and r["declared_scorer"] != r["selected_scorer"] or r["branch"] == "omitted"
            for r in inventory
        ):
            return 0
        if all(r["selected_scorer"] and r["configuration"] and r["branch"] == "resolved" for r in inventory):
            return 2
        if any(r["selected_scorer"] for r in inventory):
            return 1
    elif ident == "DISC-02-A":
        if any(r["value"] != r["reported_value"] and r["claim_limit"] == "uninterpretable" for r in inventory):
            return 0
        if all(
            (r["availability"] == "sourced" and r["value"] is not None and r["source_identity"])
            or (r["availability"] == "provider_hidden" and r["claim_limit"] != "none")
            for r in inventory
        ):
            return 2
        if any(r["value"] is not None for r in inventory):
            return 1
    elif ident == "DISC-02-B":
        if not any(r["run_identity"] for r in inventory):
            return None
        if any(
            r["observed"] is not None and r["declared"] != r["observed"] and r["disposition"] == "invalidates"
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
    elif ident == "DISC-03-A":
        if any(r["validation"] == "incompatible" or r["adapter"] == "incompatible" for r in inventory):
            return 0
        if all(
            r["validation"] == "accepted" and r["adapter"] == "compatible" and r["adapter_revision"] for r in inventory
        ):
            return 2
        if any(r["validation"] == "accepted" for r in inventory):
            return 1
    elif ident == "AUD-02":
        if any(
            r["left"] != r["right"] and r["disposition"] == "unjustified" and r["advantage"] == "demonstrated"
            for r in inventory
        ):
            return 0
        if all(r["disposition"] == "justified" and r["rationale"] for r in inventory):
            return 2
        if any(r["disposition"] in ("disputed", "unjustified") for r in inventory):
            return 1
    elif ident == "ENV-01":
        reached = [
            r for r in inventory if r["solver_identity"] and r["execution"] == "completed" and r["actual"] is not None
        ]
        if any(r["actual"] is not None and r["actual"] != r["expected"] for r in reached):
            return 0
        if len(reached) == len(inventory) and all(r["actual"] == r["expected"] for r in reached):
            return 2
        if reached or any(r["execution"] == "partial" and r["actual"] == r["expected"] for r in inventory):
            return 1
    elif ident == "ENV-02-A":
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
    elif ident == "ENV-03":
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
    elif ident == "CREATE-07":
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
    elif ident == "CREATE-03":
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
    elif ident == "CREATE-04":
        if any(r["mapping"] == "hidden" and r["solver_access"] == "denied" for r in inventory):
            return 0
        if all(
            r["authority"] and r["solver_access"] == "accessible" and r["mapping"] == "supported" for r in inventory
        ):
            return 2
        if any(r["mapping"] in ("disputed", "unmapped") for r in inventory):
            return 1
    elif ident == "CREATE-05-A":
        if any(r["authoritative"] is not None and r["expected"] != r["authoritative"] for r in inventory):
            return 0
        if all(r["authoritative"] and r["semantics"] == "resolved" for r in inventory):
            return 2
        if any(r["authoritative"] and r["semantics"] == "resolved" for r in inventory) and any(
            r["semantics"] == "disputed" for r in inventory
        ):
            return 1
    elif ident == "AC-01":
        if any(
            r["contract_role"] == "protected" and r["solver_read"] == "readable" and r["exposure"] == "unintended"
            for r in inventory
        ):
            return 0
        if all(
            r["review"] == "complete"
            and r["permitted_neighbor"] == "readable"
            and r["solver_read"] == ("denied" if r["contract_role"] == "protected" else "readable")
            for r in inventory
        ):
            return 2
        if any(r["review"] != "unrun" for r in inventory):
            return 1
    elif ident == "AC-02":
        applicable = [r for r in inventory if r["applicability"] == "applicable"]
        reached = [r for r in applicable if r["health"] == "healthy" and r["reach"] == "reached"]
        if any(r["class"] == "attack" and r["actual_credit"] > r["expected_credit"] for r in reached):
            return 0
        if (
            reached
            and len(reached) == len(applicable)
            and {r["class"] for r in reached} == {"attack", "reference", "incorrect", "valid_alternative"}
            and all(r["actual_credit"] == r["expected_credit"] for r in reached)
        ):
            return 2
        if reached:
            return 1
    elif ident == "AC-03":
        reached = [r for r in inventory if r["health"] == "healthy" and r["reach"] == "reached"]
        if any(r["receipt_kind"] != "current" and r["consumer"] == "credited" for r in reached):
            return 0
        if (
            len(reached) == len(inventory)
            and {"current", "forged", "stale"} <= {r["receipt_kind"] for r in reached}
            and all(
                (r["receipt_kind"] == "current" and r["native_validation"] == "valid" and r["consumer"] == "credited")
                or (
                    r["receipt_kind"] != "current"
                    and r["native_validation"] == "invalid"
                    and r["consumer"] == "rejected"
                )
                for r in reached
            )
        ):
            return 2
        if reached:
            return 1
    elif ident == "AC-04":
        if any(
            r["sharing"] == "isolated"
            and r["inspection"] != "unrun"
            and (
                (
                    r["reset_state"] is not None
                    and r["reset_state"] != r["expected_state"]
                    and r["consequence"] == "answer_exposure"
                )
                or (
                    r["target_alone"] is not None
                    and r["target_after"] is not None
                    and r["target_alone"] != r["target_after"]
                    and r["consequence"] == "credit_change"
                )
            )
            for r in inventory
        ):
            return 0
        if all(
            r["inspection"] == "complete"
            and r["reset_state"] == r["expected_state"]
            and r["target_alone"] is not None
            and r["target_alone"] == r["target_after"]
            for r in inventory
        ):
            return 2
        if any(r["inspection"] == "partial" for r in inventory):
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
    if not isinstance(packet["items"], list) or not packet["items"] or len(packet["items"]) > 18:
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
                if support.get("artifact_id") in (record["source"]["artifact_id"], selector["artifact_id"]):
                    raise ValueError("fact assertion cannot support itself")
                supported = reader.reference(support)
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
                and (ident in ("AUD-02", "CREATE-07") or r["original_actor"] != r["corroborating_actor"])
                for r in controls
            )
        ):
            candidate = 3
        synthetic = scope == "synthetic-scoring-demonstration"
        score = candidate if synthetic else None
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
            evidence_strength="E1" if synthetic and fact_results else "E0",
            fact_results=fact_results,
            reviewer_authenticated=False,
            native_execution_verified=False,
            score_provenance={
                "kind": "synthetic-anchor-application" if synthetic else "recorded-review-only",
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
            remaining_gaps=[] if score is not None else ["Recorded rows do not establish a verified canonical anchor."],
        )
        row["limitations"].append(
            "Synthetic anchor application is not task qualification."
            if synthetic
            else "Content binding does not verify recorded semantic judgments, permissions or runtime truth."
        )
        for check in row["check_results"]:
            check.update(
                outcome="RECORDED" if fact_results else "NOT_ASSESSED",
                execution_state="NOT_RUN",
                evidence_refs=row["evidence_refs"],
            )
    reader.revalidate()
    return rows
