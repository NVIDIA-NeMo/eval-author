# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One owning check per rubric row.

Each criterion has exactly one tool. Controls are partitioned so no control result
feeds two rows. A check returns 0-3, "N/A" for a trigger-based exclusion, or "U"
(Unassessed) when its evidence is missing, unrun or blocked. Missing evidence is
never a low score: only observed evidence can lower a row, and only the row's own
tool can establish a defect (0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Score = int | str  # 0-3, "N/A" (not triggered) or "U" (unassessed: evidence missing)
Ev = dict[str, Any]
U = "U"
NA = "N/A"

ATTACKS = (
    "no-work",
    "all-failure",
    "wrong-customer",
    "wrong-order",
    "wrong-amount",
    "wrong-destination",
    "missing-approval",
    "late-approval",
    "missing-ledger",
    "duplicate-ledger",
    "protected-mutation",
    "historical-mutation",
    "stale-carryover",
    "malformed-events",
    "helper-error",
    "ordering-evidence-unavailable",
)
BOUNDARY = ("approval-threshold-inclusive", "below-approval-threshold", "permitted-zero-balance-inaction")
REFERENCE = ("reference", "split-alternative")
OPTIONAL = ("missing-completion",)
PARTITION = {
    "AUDIT-03": ATTACKS,
    "task-contract.clear-contract": BOUNDARY,
    "task-contract.scoring-authority": REFERENCE,
    "task-contract.discoverable-requirements": OPTIONAL,
}


@dataclass(frozen=True)
class Check:
    criterion: str
    tool: str
    fn: Callable[[Ev], tuple[Score, str]]


def _ctrl(ev: Ev, ids: tuple[str, ...]) -> dict | None:
    c = ev["controls"]
    if c is None:
        return None
    return {k: c[k] for k in ids if k in c}


def _pol(ev: Ev, key: str) -> Any:
    return (ev["policy"] or {}).get(key)


def _agent(ev: Ev, role: str) -> dict | None:
    return ev["agents"].get(role) if ev["agents_enabled"] else None


def _heldout(ev: Ev, kind: str) -> dict | None:
    h = ev["heldout"]
    return None if h is None else {k: v for k, v in h.items() if v["class"] == kind}


def _agrees(cases: dict | None) -> bool:
    return bool(cases) and not any(v["mismatch_total"] for v in cases.values())


def audit_03(ev: Ev) -> tuple[Score, str]:
    panel = _ctrl(ev, ATTACKS)
    if panel is None:
        return U, "declared attack panel blocked"
    held = _heldout(ev, "attack") or {}
    if any(v["forbidden"] for v in (*panel.values(), *held.values())):
        return 0, "healthy attack obtained forbidden credit: " + ", ".join(
            k for k, v in {**panel, **held}.items() if v["forbidden"]
        )
    if len(panel) < len(ATTACKS):
        return U, "declared attack evidence withheld"
    if any(v["mismatch_total"] for v in panel.values()):
        return 1, "declared attack classes unresolved"
    if not (_pol(ev, "attack_matrix") or {}).get("approved"):
        return U, "attack applicability matrix not supplied by the owner"
    if ev["heldout_frozen"] and _agrees(held) and ev["agents_enabled"]:
        return 3, f"{len(panel)} declared + {len(held)} independently authored held-out attacks credited as intended"
    return 2, "declared attacks credited as intended; held-out independent attacks absent or unconfirmed"


def audit_04b(ev: Ev) -> tuple[Score, str]:
    confirmed = [k for k, v in {**(ev["controls"] or {}), **(ev["heldout"] or {})}.items() if v["forbidden"]]
    if not confirmed:
        return NA, "no exploit confirmed in this revision; containment is trigger-based"
    if ev["task_sha256"] not in (_pol(ev, "exploit_dispositions") or {}):
        return 0, "confirmed exploit accepted without containment disposition: " + ", ".join(confirmed)
    return 2, "contained with disposition; independent cross-family replay absent"


def audit_05b(ev: Ev) -> tuple[Score, str]:
    if ev["exposure_hits"]:
        return 0, "answer present on solver-visible surface: " + ", ".join(ev["exposure_hits"])
    if ev["oracle_total"] is None:
        return U, "answer unknown without the contract; exposure scan blocked"
    if not _pol(ev, "exposure_statement"):
        return U, "no bounded exposure statement supplied"
    r = _agent(ev, "exposure-reviewer")
    if r is None:
        return 2, "deterministic scan clean and statement bounded; independent exposure review absent"
    if r["answer_exposed"]:
        return 1, "independent reviewer disputes scan: " + ", ".join(r["where"])
    return 3, "scan and independent exposure review agree: no answer exposure"


def audit_05s03(ev: Ev) -> tuple[Score, str]:
    if ev["heldout"] is None:
        return U, "no confirmation set run"
    if not ev["heldout_frozen"]:
        return 0, "held-out set changed after freeze but is still presented as untouched confirmation"
    if not _pol(ev, "selection_rule"):
        return U, "no frozen confirmation rule supplied"
    if not _agrees(ev["heldout"]):
        return 2, "frozen confirmation ran but did not corroborate the result"
    prov = ev["heldout_provenance"] or {}
    independent = prov.get("role") == "heldout-author" and set(prov.get("input_names", [])) == {
        "contract.json",
        "instruction.md",
    }
    if independent and ev["agents_enabled"]:
        return 3, "independently authored frozen confirmation set reproduces every expected result"
    return 2, "frozen confirmation agrees; independent authorship absent"


def trace_04a(ev: Ev) -> tuple[Score, str]:
    if ev["trace_hits"]:
        return 0, f"{ev['trace_hits']} secret pattern(s) in traces"
    if not _pol(ev, "trace_disposition"):
        return U, "no release disposition supplied"
    r = _agent(ev, "trace-labeler")
    if r is None:
        return 2, "scan clean with disposition; independent labels absent"
    if r["contains_sensitive"]:
        return 1, "labeler disputes: " + "; ".join(r["findings"])
    return 3, "scan and independent labels agree"


def env_03(ev: Ev) -> tuple[Score, str]:
    if ev["controls"] is None or not ev["ledger"]["ran"]:
        return U, "reset/order controls blocked"
    if not (ev["repeat_identical"] and ev["order_independent"]):
        return 0, "results depend on predecessor or order"
    if not _pol(ev, "isolation"):
        return U, "isolated/shared surfaces not declared by the owner"
    if ev["parallel_identical"] and ev["heldout_order_independent"]:
        return 3, "repeat, reversed, parallel and held-out ordering probes identical; fresh service per episode"
    return 2, "deterministic predecessor/order controls clean"


def create_02(ev: Ev) -> tuple[Score, str]:
    if ev["cue_only"] is None:
        return U, "cue-only controls blocked"
    if any(v["forbidden"] for v in ev["cue_only"].values()):
        return 0, "success cue alone earned credit"
    held = _heldout(ev, "cue_only")
    if ev["agents_enabled"] and held and not any(v["forbidden"] for v in held.values()) and _agrees(held):
        return 3, "generated and independently authored cue-only controls earn nothing"
    return 2, "generated cue-only controls earn nothing; independent counterexamples absent"


def clear_contract(ev: Ev) -> tuple[Score, str]:
    if not ev["static"]["contract_present"]:
        return 0, "contract missing; grading cannot be justified"
    r = _agent(ev, "contract-reviewer")
    if r and r["material_contradictions"]:
        return 0, "material contradiction: " + "; ".join(r["material_contradictions"])
    if ev["lint"]:
        return 1, "contract lint: " + "; ".join(ev["lint"])
    if r and r["material_ambiguities"]:
        return 1, "unresolved material ambiguity: " + "; ".join(r["material_ambiguities"])
    panel = _ctrl(ev, BOUNDARY)
    if not panel or len(panel) < len(BOUNDARY):
        return U, "boundary controls blocked"
    if any(v["mismatch"] for v in panel.values()):
        return 1, "boundary cases unresolved"
    if not _pol(ev, "attack_matrix"):
        return U, "owner interpretation not supplied"
    alts = _heldout(ev, "valid_alternative")
    if r is not None and _agrees(alts):
        return 3, f"boundaries hold; {len(alts or {})} independent alternative solutions credited as predicted"
    return 2, "public contract and boundaries resolve ambiguity; independent solutions absent or disputed"


def discoverable(ev: Ev) -> tuple[Score, str]:
    panel = _ctrl(ev, OPTIONAL)
    if not panel:
        return U, "optional-signal control blocked"
    if any(v["lost"] for v in panel.values()):
        ref = (ev["controls"] or {}).get("reference")
        if not (ref and ref["observed"].get("accepted")):
            return 1, "optional-signal semantics unresolved: the reference itself no longer passes"
        return 0, "scorer enforces a requirement the contract calls optional"
    b = ev["blind"]
    if b is None or not ev["agents_enabled"]:
        return 2, "every scored condition has public authority; blind solve absent"
    if not _agrees(b):
        return 1, "blind public-only solver did not reach expected credit: " + ", ".join(
            k for k, v in b.items() if v["mismatch_total"]
        )
    return 3, "blind public-only solver earns full credit with and without the optional signal"


def scoring_authority(ev: Ev) -> tuple[Score, str]:
    panel = _ctrl(ev, REFERENCE)
    if not panel or len(panel) < len(REFERENCE):
        return U, "reference evidence withheld or blocked"
    if ev["reference_total"] != ev["oracle_total"] or any(v["mismatch"] for v in panel.values()):
        return 0, f"reference total {ev['reference_total']} contradicts contract-derived {ev['oracle_total']}"
    if not _pol(ev, "attack_matrix"):
        return U, "expected results lack an owner justification"
    a = _agent(ev, "adjudicator")
    if a and a["correct_total_cents"] == ev["oracle_total"] and a["reference_satisfies_contract"]:
        return 3, "spec oracle and independent adjudicator corroborate reference and scoring"
    return 2, "reference justified by spec oracle; independent adjudication absent or disputed"


def _reviewer(ev: Ev, pred: Callable[[dict], bool], two: str, three: str) -> tuple[Score, str]:
    r = _agent(ev, "independent-reviewer")
    return (3, three) if r is not None and pred(r) else (2, two)


def _base(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def artifact_access(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if not (s["contract_present"] and s["copy_sources_exist"]):
        return 0, "required task artifact missing: " + ", ".join(
            s["missing_copy_sources"] or ["environment/contract.json"]
        )
    return _reviewer(
        ev,
        lambda r: (
            r["copy_sources_all_exist"] and set(map(_base, r["copy_sources"])) == set(map(_base, s["copy_sources"]))
        ),
        "inventory complete",
        "independent reviewer reproduces inventory",
    )


def grading_path(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if s["grader"] != "reviewed":
        return 0, f"verify.py selects '{s['grader']}', not the declared reviewed scorer"
    if not s["contract_present"]:
        return U, "declared acceptance rule unavailable"
    return _reviewer(
        ev,
        lambda r: "reviewed" in r["selected_grader"].lower(),
        "grader resolved",
        "independent reviewer resolves same grader",
    )


def grading_deps(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if not s["verifier_imports_resolve"]:
        return 0, "verifier import missing"
    return _reviewer(
        ev,
        lambda r: (
            {_base(m).removesuffix(".py") for m in r["verifier_local_imports"]} == set(s["verifier_local_imports"])
        ),
        "verifier imports resolve",
        "independent reviewer reproduces dependency set",
    )


def declared_config(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if not s["toml_parses"]:
        return 0, "task.toml unreadable"
    if not (s["toml_fields_complete"] and s["image_pinned"]):
        return 1, "configuration partly identified: " + (
            "image not digest-pinned" if not s["image_pinned"] else "missing task.toml settings"
        )
    if not _pol(ev, "declared_configuration_limits"):
        return U, "limits of unknown settings not assessed by the owner"
    return _reviewer(
        ev,
        lambda r: r["image_pinned_by_digest"] and r["network_modes"] == s["network_modes"],
        "manifest sourced",
        "independent reviewer reconstructs manifest",
    )


def observed_config(ev: Ev) -> tuple[Score, str]:
    if not ev["static"]["entrypoint_service"]:
        return 0, "image entrypoint does not start the declared ledger service"
    if ev["controls"] is None:
        return U, "observation blocked"
    # "Independent repeat" is read as a fresh, separately executed environment, not a separate person.
    if ev["repeat_identical"]:
        return 3, "fresh independent repeat reproduces observations"
    return 2, "observed agrees"


def runtime_route(ev: Ev) -> tuple[Score, str]:
    h = ev["harbor"]
    if h["valid"] is None:
        return U, "provider validator unavailable"
    if not h["valid"]:
        return 0, "Harbor rejects the task manifest"
    # "Independent clean setup" is read as a fresh parse plus an invalid-manifest control.
    if h["invalid_manifest_rejected"]:
        return 3, "Harbor validates; invalid-manifest control rejected"
    return 2, "Harbor validates"


def comparison(ev: Ev) -> tuple[Score, str]:
    m = ev["matched"]
    if m is None:
        return U, "matched setups blocked"
    if len(set(m.values())) > 1:
        return 0, f"equivalent solutions earn different credit: {m}"
    if not _pol(ev, "comparison"):
        return U, "intended comparison not declared by the owner"
    return 3, f"{len(m)} matched equivalent setups earn identical credit"


def tool_access(ev: Ev) -> tuple[Score, str]:
    led = ev["ledger"]
    if not led["ran"]:
        return U, "socket probes blocked: " + led.get("reason", "")
    if not led["valid_actions_ok"]:
        return 0, "valid solver action rejected"
    if not led["boundary_rejected"]:
        return 1, "boundary probes accepted a forbidden action"
    if not _pol(ev, "solver_actions"):
        return U, "required action map not supplied by the owner"
    return 3, "valid actions accepted, boundary actions rejected, fresh service per probe"


def substitution(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if not s["components_present"] or s["env_non_stdlib"]:
        return 0, "required component missing or unapproved dependency: " + ", ".join(s["env_non_stdlib"])
    if not _pol(ev, "substitutions"):
        return U, "substitution approval not supplied"
    return _reviewer(
        ev,
        lambda r: not r["environment_non_stdlib_imports"],
        "inventory reconciled",
        "independent reviewer reconciles inventory",
    )


def external_state(ev: Ev) -> tuple[Score, str]:
    s = ev["static"]
    if not s["contract_present"]:
        return U, "pinned state unavailable"
    pin = (_pol(ev, "pins") or {}).get("contract_sha256")
    if pin and s["contract_sha256"] != pin:
        return 0, "contract drifted from the pin its expectations were authored against"
    if s["scorer_nondeterminism"]:
        return 1, "scorer reads uncontrolled state: " + ", ".join(s["scorer_nondeterminism"])
    if not pin:
        return U, "no state pin supplied"
    return (3, "pinned; deliberate drift probe detected") if ev["drift_probe_detected"] else (2, "pinned")


def revision(ev: Ev) -> tuple[Score, str]:
    stale = [k for k, sha in ev["evidence_bindings"].items() if sha != ev["task_sha256"]]
    if stale:
        return 0, "evidence from another task revision combined: " + ", ".join(stale)
    return (3, "every item bound; change injection invalidates") if ev["change_injection_invalidates"] else (2, "bound")


CHECKS = [
    Check("AUDIT-03", "forbidden-credit panel (16 declared attacks + held-out)", audit_03),
    Check("AUDIT-04-B", "exploit containment ledger", audit_04b),
    Check("AUDIT-05-B", "solver-surface answer scan + exposure reviewer", audit_05b),
    Check("AUDIT-05-S03", "held-out freeze + confirmation results", audit_05s03),
    Check("TRACE-04-A", "trace secret scan + trace labeler", trace_04a),
    Check("ENV-03-S01", "repeat/reverse/parallel reset panel", env_03),
    Check("CREATE-02", "cue-only panel", create_02),
    Check("task-contract.clear-contract", "contract reviewer + lint + boundary panel", clear_contract),
    Check("task-contract.discoverable-requirements", "optional-signal control + blind solver", discoverable),
    Check("task-contract.scoring-authority", "spec oracle + adjudicator", scoring_authority),
    Check("provenance.task-artifact-access", "artifact inventory", artifact_access),
    Check("provenance.grading-path", "grader resolution", grading_path),
    Check("provenance.grading-dependency-access", "verifier import closure", grading_deps),
    Check("provenance.declared-configuration", "manifest identity", declared_config),
    Check("provenance.observed-configuration", "entrypoint vs observed service", observed_config),
    Check("provenance.runtime-route", "Harbor TaskConfig validation", runtime_route),
    Check("provenance.comparison-fairness", "matched-setup sensitivity", comparison),
    Check("provenance.solver-tool-access", "socket action probes", tool_access),
    Check("provenance.dependency-substitution", "component inventory", substitution),
    Check("provenance.external-state", "state pin + nondeterminism scan", external_state),
    Check("provenance.evidence-revision-consistency", "evidence binding + change injection", revision),
]


def rank(value: Score) -> int:
    """Ordering for preference only: N/A is not a deficit; U (not established) ranks below any score."""
    if value == NA:
        return 3
    if value == U:
        return -1
    return int(value)
