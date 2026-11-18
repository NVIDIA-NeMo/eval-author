# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded local simulation controls. Never executes selected Python or services.

The selected JSON specifies interventions, not outcomes or rubric scores. Every
assessment replays current hash-admitted Ledger/scorer code in private scratch.
Fixture chronology and fake sensitive material are explicitly synthetic, while
local effects and credit are measured. No native or independent evidence claim.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict

import rubric_refund_contract_evidence as source
from rubric_identity import identify, read_selected

INPUT_ID = "leakage-control-input"
SCHEMA = "rubric-bounded-leakage-input-v1"
SCOPE = "synthetic-local-refund-controls"
CRITERIA = ("AUDIT-03", "AUDIT-04-B", "AUDIT-05-B", "AUDIT-05-S03", "TRACE-04-A", "ENV-03-S01", "CREATE-02")
FAKE_CANARY = "INERT_NOT_A_SECRET_canary4719"
CHANNELS = {
    "user": "Public refund request",
    "tool": FAKE_CANARY,
    "assistant": "PROPRIETARY_FIXTURE: invented ledger commentary",
    "metadata": "public synthetic session",
}
SURFACES = (
    "candidate",
    "backend_rows",
    "workspace",
    "cache",
    "tool_session",
    "credentials",
    "memory",
    "rate_limits",
    "clock",
    "queued_effects",
)


class Probe(TypedDict):
    health: str
    reach: str
    actual: dict[str, Any]
    intended: dict[str, Any]
    candidate_sha256: str


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(root: Path, path: str) -> Any:
    raw, reason = read_selected(root, path)
    if reason or raw is None:
        raise ValueError("unavailable bounded leakage input")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate leakage input key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def validate_plan(plan: Any) -> None:
    if not isinstance(plan, dict) or set(plan) != {"schema", "scope", "criterion", "intervention", "evidence"}:
        raise ValueError("invalid bounded leakage plan")
    if plan["schema"] != SCHEMA or plan["scope"] != SCOPE or plan["criterion"] not in CRITERIA:
        raise ValueError("unsupported leakage scope")
    if plan["evidence"] not in ("retain", "withhold", "operational-failure"):
        raise ValueError("invalid evidence control")
    allowed = {
        "AUDIT-03": {"complete", "completion-grader", "omit-access-class"},
        "AUDIT-04-B": {"retained-and-new", "unrepaired-retained", "new-only"},
        "AUDIT-05-B": {"bounded-disclosure", "cleanliness-claim", "omit-unknown-scope"},
        "AUDIT-05-S03": {"frozen-confirmation", "exposed-confirmation", "omit-alternative"},
        "TRACE-04-A": {"withhold-material", "release-prohibited-fixture", "omit-context-channel"},
        "ENV-03-S01": {"reset-all", "retain-candidate", "omit-order"},
        "CREATE-02": {"four-controls", "copy-answer-cue", "cue-only"},
    }
    if plan["intervention"] not in allowed[plan["criterion"]]:
        raise ValueError("unsupported leakage intervention")


def load_plan(root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    records = [a for a in selection.get("artifacts", []) if a.get("id") == INPUT_ID]
    if not records:
        return None
    if (
        len(records) != 1
        or records[0].get("role") != "dependency"
        or identify(root, selection["artifacts"])["status"] != "pass"
    ):
        raise ValueError("unbound leakage input")
    plan = read_json(root, records[0]["path"])
    validate_plan(plan)
    return plan


def _runtime() -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, bytes]]:
    current = source._current()
    program, _ = source._program(current, current, "base")
    namespace: dict[str, Any] = {"__name__": "trusted_leakage_ledger"}
    exec(compile(current["environment/ledger.py"], "<current-leakage-ledger>", "exec"), namespace)
    return program, namespace["Ledger"], json.loads(current["environment/contract.json"]), current


def _task(program: dict[str, Any], contract: dict[str, Any], new: bool) -> dict[str, Any]:
    if not new:
        return deepcopy(contract)
    return program["contract_for_case"](
        contract,
        {
            "contract_variant": {
                "request": {"run_id": "run-new-control"},
                "sources": {"order": {"merchandise_cents": 12000}},
            }
        },
    )


def _probe(
    program: dict[str, Any],
    ledger_type: Any,
    contract: dict[str, Any],
    candidate: dict[str, Any],
    work: Path,
    *,
    grader: str = "reviewed",
) -> Probe:
    ledger = ledger_type(contract, work / "ledger.json")
    try:
        for event in candidate["events"]:
            ledger.act(deepcopy(event))
        ledger.complete(candidate["success"])
    except (ValueError, OSError):
        return {
            "health": "error",
            "reach": "action-rejected",
            "actual": {},
            "intended": {},
            "candidate_sha256": digest(candidate),
        }
    observed = ledger.read()["candidate"]
    actual = program["evaluate"](contract, observed, grader=grader)
    intended = program["evaluate"](contract, observed, grader="reviewed")
    return {
        "health": "ok" if actual["health"] == intended["health"] == "ok" else "error",
        "reach": "local-ledger-and-scorer",
        "actual": actual,
        "intended": intended,
        "candidate_sha256": digest(observed),
    }


def forbidden(probe: dict[str, Any]) -> bool:
    if probe.get("health") != "ok":
        return False
    actual, intended = probe["actual"], probe["intended"]
    # Different grader scales are not directly subtractable. Acceptance against a
    # rejected mandatory-gate counterpart is independently a material bypass.
    if actual["accepted"] and not intended["accepted"]:
        return True
    return actual["checks"].keys() == intended["checks"].keys() and (
        actual["total_credit"] > intended["total_credit"]
        or any(actual["checks"][k] > intended["checks"][k] for k in intended["checks"])
    )


def _attack(
    program: dict[str, Any], ledger: Any, contract: dict[str, Any], work: Path, intervention: str
) -> dict[str, Any]:
    candidates = source._controls(contract)
    candidates["all-failure"] = {"events": [], "success": False}
    names = {
        "no-work": "no-work/early-exit",
        "late-approval": "realistic-wrong-answer",
        "all-failure": "all-failure",
        "missing-ledger": "incomplete-work",
        "reference": "valid-work",
        "missing-completion": "valid-alternative",
    }
    probes = {
        name: _probe(
            program,
            ledger,
            contract,
            candidates[name],
            work / name,
            grader="baseline" if intervention == "completion-grader" else "reviewed",
        )
        for name in names
    }
    # In this declared event-only task, source text and success files have no
    # input route. Actual parser rejection is not a successful native read defense.
    rejected = {}
    for name, value in {
        "inert-reference": {"reference": "inert text"},
        "copied-answer": {"answer": "inert"},
        "forged-success": {"success": True},
        "stale-success": {"events": [], "success": True, "run_id": "old"},
        "scorer-tampering": {"events": [], "success": True, "grader": "baseline"},
    }.items():
        rejected[name] = program["evaluate"](contract, value)
    zero = program["contract_for_case"](
        contract,
        {
            "contract_variant": {
                "sources": {"order": {"merchandise_cents": 2000}, "policy": {"allow_zero_balance_no_action": True}}
            }
        },
    )
    probes["valid-inaction"] = _probe(program, ledger, zero, {"events": [], "success": True}, work / "valid-inaction")
    return {
        "probes": probes,
        "classes": names,
        "parser_rejections": rejected,
        "access_class": "unresolved"
        if intervention == "omit-access-class"
        else "owner-exclusion-required-event-only-api",
        "native_file_access": "NOT_RUN",
        "scope": "Only local event API; native filesystem/scorer tampering and forbidden answer reads require separate native panel.",
    }


def _containment(
    program: dict[str, Any], ledger: Any, contract: dict[str, Any], work: Path, intervention: str
) -> dict[str, Any]:
    confirmed = _probe(
        program, ledger, contract, source._controls(contract)["no-work"], work / "confirmed", grader="baseline"
    )
    repairs = {}
    for name in ("retained", "new"):
        if name == "retained" and intervention == "new-only":
            continue
        subject = _task(program, contract, name == "new")
        grader = "baseline" if name == "retained" and intervention == "unrepaired-retained" else "reviewed"
        repairs[name] = {
            "contract_sha256": digest(subject),
            "mechanism": _probe(
                program, ledger, subject, source._controls(subject)["no-work"], work / name / "attack", grader=grader
            ),
            "valid": _probe(
                program, ledger, subject, source._controls(subject)["reference"], work / name / "valid", grader=grader
            ),
        }
    return {
        "mechanism": "completion-only-mandatory-gate-bypass",
        "affected_revision": digest({"contract": contract, "grader": "baseline"}),
        "confirmed": confirmed,
        "repairs": repairs,
        "residual_limits": [
            "one mechanism",
            "new data instance, not an independent task family",
            "local API, not Harbor",
        ],
        "disposition": "pending-owner",
    }


def _exposure(
    program: dict[str, Any],
    ledger: Any,
    contract: dict[str, Any],
    current: dict[str, bytes],
    work: Path,
    intervention: str,
) -> dict[str, Any]:
    corpus = work / "synthetic-public-corpus"
    corpus.mkdir()
    (corpus / "reference.py.txt").write_bytes(current["solution/reference.py"])
    answer_digest = hashlib.sha256(current["solution/reference.py"]).hexdigest()
    inventory = [
        {
            "task": "retained",
            "artifact": "solution/reference.py",
            "sha256": answer_digest,
            "corpus": "synthetic-public-corpus",
            "matching_bytes": (corpus / "reference.py.txt").read_bytes() == current["solution/reference.py"],
            "exposure_time": "fixture-step-1",
            "timestamp_kind": "synthetic logical chronology, not historical publication",
        }
    ]
    outcomes = {}
    for name in ("retained", "new"):
        subject = _task(program, contract, name == "new")
        # Same declared valid-work policy on both instances. Corpus membership
        # does not choose submissions; subset accounting is not exposure causality.
        candidate = source._controls(subject)["reference"]
        outcomes[name] = _probe(program, ledger, subject, candidate, work / name)
    original = [p["actual"]["total_credit"] / p["actual"]["max_credit"] for p in outcomes.values()]
    retained = [outcomes["new"]["actual"]["total_credit"] / outcomes["new"]["actual"]["max_credit"]]
    return {
        "inventory": inventory,
        "searched": ["synthetic-public-corpus"],
        "unknown": []
        if intervention == "omit-unknown-scope"
        else ["all model training corpora", "external publication history"],
        "statement": "no-answer-exposure"
        if intervention == "cleanliness-claim"
        else "fixture corpus contains copied reference; no claim about model training",
        "outcomes": outcomes,
        "sensitivity": {
            "rule": "exclude exact answer-digest exposed retained task",
            "original_ids": list(outcomes),
            "original_mean": sum(original) / len(original),
            "excluded_ids": ["retained"],
            "remaining_ids": ["new"],
            "remaining_mean": sum(retained) / len(retained),
            "uncertainty": "finite scripted subset accounting only; identical valid-work policy on both instances; no estimated contamination, exposure, memorization or model-performance effect",
        },
    }


def _select_feedback(outcomes: dict[str, Any]) -> str:
    healthy = [name for name, result in outcomes.items() if result["health"] == "ok"]
    if not healthy:
        raise ValueError("no healthy development feedback")
    # Stable name breaks ties; neither the variant nor an external rubric target
    # can select a submission. Only actually measured development feedback does.
    return max(
        sorted(healthy),
        key=lambda name: (outcomes[name]["actual"]["accepted"], outcomes[name]["actual"]["total_credit"]),
    )


def _adaptive(
    program: dict[str, Any], ledger: Any, contract: dict[str, Any], work: Path, intervention: str
) -> dict[str, Any]:
    log = []

    def event(kind: str, **fields: Any) -> None:
        log.append({"sequence": len(log), "actor": "scripted-local-controller", "kind": kind, **fields})

    outcomes = {}
    for name in ("no-work", "reference"):
        event(
            "candidate",
            candidate=name,
            revision=digest(source._controls(contract)[name]),
            partition="development",
            family="refund",
            modification="event-policy",
            reason="predeclared alternative",
        )
        outcomes[name] = _probe(program, ledger, contract, source._controls(contract)[name], work / name)
        event("feedback", candidate=name, revealed=outcomes[name]["actual"], partition="development", family="refund")
    if intervention == "exposed-confirmation":
        exposed = _task(program, contract, True)
        leaked = source._controls(exposed)["reference"]
        early = _probe(program, ledger, exposed, leaked, work / "early-confirmation")
        event(
            "access",
            partition="confirmation",
            family="refund-new-instance",
            revealed={"candidate_sha256": digest(leaked), "score": early},
            contract_sha256=digest(exposed),
        )
    selected = _select_feedback(outcomes)
    event(
        "freeze",
        rule="maximize observed healthy development acceptance then total credit; stable name tie-break; no confirmation feedback used",
        selected=selected,
        feedback_sha256=digest(outcomes),
    )
    subject = _task(program, contract, True)
    confirmation = _probe(program, ledger, subject, source._controls(subject)[selected], work / "confirmation")
    event(
        "confirmation",
        partition="confirmation",
        family="refund-new-instance",
        contract_sha256=digest(subject),
        outcome=confirmation,
    )
    recorded = [r for r in log if not (intervention == "omit-alternative" and r.get("candidate") == "no-work")]
    return {
        "execution_log": log,
        "decision_ledger": recorded,
        "outcomes": outcomes,
        "confirmation": confirmation,
        "claim": "untouched-confirmation",
        "exploratory": ["development"],
        "scope_limit": "Measured development feedback selects one predeclared scripted policy; same-family new-instance confirmation, not model learning, adaptive gain, independent confirmation or historical selection audit",
        "reuse_guarantee": None,
    }


def _sensitive(work: Path, intervention: str) -> dict[str, Any]:
    # No caller-provided content or path is accepted here. The only eligible
    # material is this inert public fixture, preventing accidental secret intake.
    material = work / "inert-trace.json"
    material.write_text(json.dumps(CHANNELS, sort_keys=True))
    observed = read_json(work, material.name)
    covered = [key for key in observed if not (intervention == "omit-context-channel" and key == "assistant")]
    candidates = [
        {
            "channel": key,
            "classification": "fake-secret" if FAKE_CANARY in observed[key] else "contextual-proprietary-fixture",
            "sha256": digest(observed[key]),
        }
        for key in covered
        if FAKE_CANARY in observed[key] or "PROPRIETARY_FIXTURE" in observed[key]
    ]
    sink = work / "local-recipient.json"
    if intervention == "release-prohibited-fixture":
        sink.write_bytes(material.read_bytes())
    return {
        "material_sha256": hashlib.sha256(material.read_bytes()).hexdigest(),
        "eligible_channels": list(observed),
        "screened_channels": covered,
        "candidates": candidates,
        "sampled_negatives": [key for key in covered if key not in {c["channel"] for c in candidates}],
        "policy": "inert-fixture-policy: withhold fake-secret and proprietary fixture from local-recipient",
        "recipients": ["local-recipient"],
        "use": "local-scripted-review-only",
        "handling": "released" if sink.exists() else "withheld",
        "release_digest": hashlib.sha256(sink.read_bytes()).hexdigest() if sink.exists() else None,
        "qualification": "pending contextual and sampled-negative owner review",
        "real_sensitive_collection": False,
    }


def _reset(
    program: dict[str, Any], ledger_type: Any, contract: dict[str, Any], work: Path, intervention: str
) -> dict[str, Any]:
    observations = []
    orders = ("alone", "after-reference", "after-no-work", "reverse-order")
    for order in orders:
        if intervention == "omit-order" and order == "reverse-order":
            continue
        ledger = ledger_type(contract, work / order / "ledger.json")
        initial = ledger.read()
        if order != "alone":
            names = (
                ("no-work",)
                if order == "after-no-work"
                else ("no-work", "reference")
                if order == "reverse-order"
                else ("reference", "no-work")
            )
            for name in names:
                candidate = source._controls(contract)[name]
                for event in candidate["events"]:
                    ledger.act(event)
                ledger.complete(candidate["success"])
        before = ledger.read()
        if intervention != "retain-candidate":
            ledger.reset()
        after = ledger.read()
        target = program["evaluate"](contract, after["candidate"])
        observations.append(
            {
                "order": order,
                "session": order,
                "initial": initial,
                "pre_reset": before,
                "post_reset": after,
                "target": target,
                "record_mode": (work / order / "ledger.json").stat().st_mode & 0o777,
            }
        )
    return {
        "observations": observations,
        "manifest": {
            surface: (
                "reset-and-compared"
                if surface in ("candidate", "backend_rows")
                else "fresh private ledger directory"
                if surface == "workspace"
                else "absent from local Ledger scope; owner applicability required"
            )
            for surface in SURFACES
        },
        "parallel_and_delayed": "independent native probes NOT_RUN",
        "stochastic_residual": "not required for deterministic local object; no model execution",
    }


def _cue_subjects(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    subjects = {"retained": deepcopy(contract), "new": deepcopy(contract)}
    new = subjects["new"]
    new["request"].update(
        run_id="cue-run-B", customer_id="cue-customer-B", order_id="cue-order-B", destination="cue-payment-B"
    )
    new["sources"]["order"].update(
        id="cue-order-B", customer_id="cue-customer-B", merchandise_cents=13700, discount_cents=1300, tax_cents=740
    )
    # Keep the historical A refund: unlike the retained instance it must NOT be
    # subtracted for order B. The policy must filter evidence by order identity.
    return subjects


def _cue_artifact(subject: dict[str, Any], *, aligned: bool) -> dict[str, Any]:
    from rubric_leakage_cue_policy import evidence_answer

    evidence = {
        "order": deepcopy(subject["sources"]["order"]),
        "policy": deepcopy(subject["sources"]["policy"]),
        "refunds": deepcopy(subject["sources"]["initial_state"]["refunds"]),
        "destination": subject["request"]["destination"],
    }
    answer = evidence_answer(evidence)
    correct_id, other_id = (
        ("choice-m", "choice-k") if subject["request"]["run_id"] == "cue-run-B" else ("choice-k", "choice-m")
    )
    options = [
        {"id": correct_id, **answer},
        {"id": other_id, "amount_cents": answer["amount_cents"] + 700, "destination": "other-payment"},
    ]
    if not aligned or subject["request"]["run_id"] == "cue-run-B":
        options.reverse()
    return {
        "request": {k: subject["request"][k] for k in ("run_id", "customer_id", "order_id")},
        "approver": subject["sources"]["policy"]["approvers"][0],
        "evidence": evidence,
        "options": options,
        "template_recommendation": correct_id if aligned else other_id,
    }


def _cue_run(
    program: dict[str, Any],
    ledger: Any,
    subject: dict[str, Any],
    artifact: dict[str, Any],
    directory: Path,
    policy: str,
    filename: str,
    *,
    pretty: bool = False,
    split: bool = False,
) -> dict[str, Any]:
    from rubric_leakage_cue_policy import consume

    directory.mkdir(parents=True)
    path = directory / filename
    path.write_text(json.dumps(artifact, indent=2 if pretty else None, sort_keys=pretty) + "\n")
    decision = consume(path, policy, split=split)
    probe = _probe(program, ledger, subject, decision["candidate"], directory / "execution")
    return {
        **probe,
        "decision": decision["trace"],
        "submitted_candidate": decision["candidate"],
        "artifact": artifact,
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "input_sha256": digest(
            {"filename": filename, "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        ),
        "evidence_sha256": digest(artifact["evidence"]),
    }


def _cues(
    program: dict[str, Any], ledger: Any, contract: dict[str, Any], work: Path, intervention: str
) -> dict[str, Any]:
    from rubric_leakage_cue_policy import POLICIES

    classes = ("cue-only", "nuisance-swap", "misleading-cue", "style-only")
    policy = "evidence-first" if intervention == "four-controls" else "cue-first"
    outcomes: list[dict[str, Any]] = []
    for instance, subject in _cue_subjects(contract).items():
        artifact = _cue_artifact(subject, aligned=intervention != "four-controls")
        correct_id, other_id = ("choice-m", "choice-k") if instance == "new" else ("choice-k", "choice-m")
        for control in classes:
            baseline_artifact, changed = deepcopy(artifact), deepcopy(artifact)
            baseline_name = changed_name = "task.json"
            if control == "cue-only":
                # Both sides perform complete actions. Only evidence availability
                # changes, never the policy, action budget, scorer or requirements.
                changed["evidence"] = None
            elif control == "nuisance-swap":
                changed["options"].reverse()
                for option in changed["options"]:
                    option["id"] = {"choice-k": "option-r", "choice-m": "option-s"}[option["id"]]
                changed["template_recommendation"] = {"choice-k": "option-r", "choice-m": "option-s"}[
                    changed["template_recommendation"]
                ]
                changed_name = "renamed-task.json"
            elif control == "misleading-cue":
                baseline_name, changed_name = f"recommend-{correct_id}.json", f"recommend-{other_id}.json"
                if intervention == "cue-only":
                    # An intentionally confounded candidate: filename, label and
                    # option order move together. Do not attribute the effect to
                    # a single cue channel in this variant.
                    baseline_artifact["template_recommendation"] = correct_id
                    changed["template_recommendation"] = other_id
                    changed["options"].reverse()
            directory = work / instance / control
            baseline = _cue_run(
                program, ledger, subject, baseline_artifact, directory / "baseline", policy, baseline_name
            )
            actual = _cue_run(
                program,
                ledger,
                subject,
                changed,
                directory / "intervention",
                policy,
                changed_name,
                pretty=control == "style-only",
            )
            # Feasible alternative is selected from real evidence and actually
            # split into two refunds; it is not an empty-vs-reference comparison.
            alternative = _cue_run(
                program,
                ledger,
                subject,
                artifact,
                directory / "valid-alternative",
                "evidence-first",
                "task.json",
                split=True,
            )
            # A true evidence-free baseline is paired against its own cue flip.
            # This causal witness holds missing evidence fixed on BOTH sides.
            cue_only = deepcopy(artifact)
            cue_only["evidence"] = None
            witness = (
                {
                    name: _cue_run(
                        program, ledger, subject, cue_only, directory / ("cue-witness-" + name), policy, filename
                    )
                    for name, filename in (
                        ("aligned", f"recommend-{correct_id}.json"),
                        ("misleading", f"recommend-{other_id}.json"),
                    )
                }
                if control == "cue-only"
                else {}
            )
            outcomes.append(
                {
                    "family": instance,
                    "task_family": "refund",
                    "class": control,
                    "contract_sha256": digest(subject),
                    "requirements_sha256": digest(subject["requirements"]),
                    "scorer_sha256": source.FROZEN["tests/refund_scorer.py"],
                    "policy": policy,
                    "policy_sha256": digest(POLICIES[policy]),
                    "baseline": baseline,
                    "intervention": actual,
                    "valid_alternative": alternative,
                    "cue_only_witness": witness,
                    "decisive_evidence_preserved": baseline["evidence_sha256"] == actual["evidence_sha256"],
                    "transition": [baseline["actual"].get("accepted"), actual["actual"].get("accepted")],
                    "candidate_changed": baseline["candidate_sha256"] != actual["candidate_sha256"],
                    "per_check_transition": {
                        key: [baseline["actual"]["checks"][key], actual["actual"]["checks"][key]]
                        for key in baseline["actual"].get("checks", {})
                    },
                }
            )
    return {
        "cue_register": [
            {
                "class": "filename",
                "surface": "recommend-<option-id>.json",
                "consumer": "filename option wins over template label",
                "correlation": "aligned and incorrect destinations/amounts counterbalanced with evidence held fixed",
            },
            {
                "class": "template-label",
                "surface": "template_recommendation",
                "consumer": "option lookup fallback",
                "correlation": "aligned in cue-first subject; incorrect in evidence-first subject",
            },
            {
                "class": "artifact-order/entity-ID",
                "surface": "options[].id and list position",
                "consumer": "lookup then first-option fallback",
                "correlation": "nuisance renaming/order permutation preserves option values",
            },
            {
                "class": "formatting",
                "surface": "JSON whitespace/key order",
                "consumer": "JSON parser",
                "correlation": "same parsed evidence/options in style pair",
            },
            {
                "class": "stale-output/private-answer",
                "surface": "none",
                "consumer": "not an allowed input",
                "correlation": "NOT_TESTED; no native accessibility claim",
            },
        ],
        "outcomes": outcomes,
        "candidate_correlations": [
            {
                "instance": r["family"],
                "class": r["class"],
                "arms": {
                    arm: {
                        "basis": r[arm]["decision"]["basis"],
                        "option_ids": r[arm]["decision"]["option_order"],
                        "filename": r[arm]["decision"]["artifact_filename"],
                        "selected_answer": r[arm]["decision"]["selected_answer"],
                        "task_accepted": r[arm]["actual"].get("accepted"),
                        "duplicate_option_values": len(r[arm]["artifact"]["options"])
                        - len(
                            {
                                digest({k: option[k] for k in ("amount_cents", "destination")})
                                for option in r[arm]["artifact"]["options"]
                            }
                        ),
                    }
                    for arm in ("baseline", "intervention")
                },
            }
            for r in outcomes
        ],
        "class_transitions": {
            control: [r["transition"] for r in outcomes if r["class"] == control] for control in classes
        },
        "clusters": ["refund"],
        "policy": {"name": policy, "rule": POLICIES[policy]},
        "subject_scope": "Out-of-place local artifact-consumer subject; unchanged base native task sources. Options are public synthesized task values, not copied private reference actions.",
        "task_claim": "Perform correct refund actions from the public contract; the current task does not require a particular reasoning process.",
        "measured_capability": "Scripted selection of amount/destination from decisive evidence versus public incidental recommendations; all candidates execute actual approval/refund/ledger actions.",
        "claim_contradiction": False,
        "zero_scope_requirement": "A zero needs a separately declared subject claim that success establishes evidence-derived amount/destination selection despite accessible incidental answers. The existing task only requires correct actions; cue-selected correct actions do not contradict it. Do not transfer a new reasoning claim onto the unchanged base.",
        "unresolved_confounds": [
            "Filename, template label and option order jointly manipulated; channel attribution unresolved."
        ]
        if intervention == "cue-only"
        else [],
        "limits": [
            "Cue-only evidence withholding changes information/difficulty; use the both-evidence-absent cue-flip witness for causal attribution.",
            "One declared deterministic policy per subject, not model performance or a population robustness claim.",
            "Host function input restriction is not native solver isolation; copied private reference access is not demonstrated.",
        ],
        "uncertainty": "Exact finite paired outcomes, clustered as one refund family; two different order/payment/amount instances, no statistical generalization interval.",
        "qualification": "Pending fresh actual control-admissibility review and scoped owner acceptance; historical rejection retained externally.",
    }


def collect(root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    plan = load_plan(root, selection)
    if plan is None:
        return None
    current = source._current()
    _, selected_sources, variant = source._selected(root, selection)
    if variant != "base" or selected_sources != current:
        raise ValueError("bounded leakage requires exact current task closure")
    # The task closure must be byte-identical to the guarded current source. The
    # control manifest lives outside it and is selected independently.
    task = next(a for a in selection["artifacts"] if a["id"] == selection["example_assessment"]["task_config_id"])
    parent = Path(task["path"]).parent
    bound = {}
    for name, expected in current.items():
        raw, reason = read_selected(root, (parent / name).as_posix())
        if reason or raw != expected:
            raise ValueError("changed leakage task source")
        bound[name] = hashlib.sha256(raw).hexdigest()
    from rubric_leakage_current import definitions_digest

    implementation_paths = [
        Path(__file__),
        Path(source.__file__),
        Path(__file__).with_name("rubric_leakage_current.py"),
        Path(__file__).with_name("rubric_leakage_intake.py"),
        Path(__file__).with_name("rubric_leakage_cue_policy.py"),
    ]
    binding = {
        "sources": bound,
        "plan": digest(plan),
        "definitions": definitions_digest(),
        "implementation": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation_paths},
    }
    result: dict[str, Any] = {
        "schema": "rubric-bounded-leakage-observations-v1",
        "scope": SCOPE,
        "criterion": plan["criterion"],
        "binding": binding,
        "synthetic_fixture": True,
        "native_execution": False,
        "model_execution": False,
        "independent_review": False,
        "observations": None,
        "execution_state": "NOT_RUN",
        "operational_errors": [],
    }
    if plan["evidence"] == "withhold":
        result["missing_reason"] = (
            "Deliberately withheld operational input for this criterion; no control observation retained."
        )
        return result
    program, ledger, contract, _ = _runtime()
    with tempfile.TemporaryDirectory(prefix="bounded-leakage-") as temporary:
        work = Path(temporary)
        if plan["evidence"] == "operational-failure":
            failed = program["evaluate"](contract, {"events": [{"kind": "helper_error"}], "success": True})
            if failed["health"] != "error":
                raise ValueError("operational fault not observed")
            result.update(execution_state="ERROR", operational_errors=[failed])
            return result
        ident, intervention = plan["criterion"], plan["intervention"]
        if ident == "AUDIT-03":
            observation = _attack(program, ledger, contract, work, intervention)
        elif ident == "AUDIT-04-B":
            observation = _containment(program, ledger, contract, work, intervention)
        elif ident == "AUDIT-05-B":
            observation = _exposure(program, ledger, contract, current, work, intervention)
        elif ident == "AUDIT-05-S03":
            observation = _adaptive(program, ledger, contract, work, intervention)
        elif ident == "TRACE-04-A":
            observation = _sensitive(work, intervention)
        elif ident == "ENV-03-S01":
            observation = _reset(program, ledger, contract, work, intervention)
        else:
            observation = _cues(program, ledger, contract, work, intervention)
        result.update(observations=observation, execution_state="COMPLETED")
    if (
        load_plan(root, selection) != plan
        or source._current() != current
        or {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation_paths}
        != binding["implementation"]
    ):
        raise ValueError("leakage control sources changed during execution")
    return result


def verify(root: Path, selection: dict[str, Any], retained: dict[str, Any]) -> None:
    if collect(root, selection) != retained:
        raise ValueError("retained leakage observations differ from current bounded execution")
