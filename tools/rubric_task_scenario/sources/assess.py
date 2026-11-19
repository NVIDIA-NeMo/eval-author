# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Explicit fictional-world assessment. Never imports empirical measurement/intake.
Canonical checks/anchors are data, unchanged. Predicates below inspect actual measurements
and separately supplied semantic/independence premises. No score targets accepted.
"""

import ast
import copy
import hashlib
import html
import json
import re
import types
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

_dispositions = types.ModuleType("fictional_dispositions")
exec(compile((ROOT / "dispositions.py").read_bytes(), str(ROOT / "dispositions.py"), "exec"), _dispositions.__dict__)
NOTICE = "FICTIONAL-WORLD ASSESSMENT ONLY. Reviewer/owner/independence/history/OS facts are SUPPLIED SIMULATION PREMISES, not actual human approvals or empirical qualification."


def packed(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(x):
    return hashlib.sha256(x).hexdigest()


def read(p):
    return json.loads(p.read_bytes())


def save(p, x):
    with p.open("xb") as f:
        f.write(packed(x) + b"\n")


def definitions():
    docs = [
        read(ROOT / "prior-package/canonical" / name)
        for name in (
            "task-contract-authority.json",
            "leakage-and-credit.json",
            "artifact-configuration-provenance.json",
        )
    ]
    items = {x["id"]: x for d in docs for x in d["items"]}
    assert len(items) == sum(len(d["items"]) for d in docs)
    return items, docs[-1]["result_template"]


def source_manifest():
    paths = [
        ROOT / x
        for x in (
            "collect.py",
            "assess.py",
            "run.py",
            "scenario-premises.json",
            "ordinary-rubric-table.py.txt",
            "dispositions.py",
        )
    ]
    paths += [ROOT / "base_adapter.py"]
    paths += [p for p in (ROOT / "harbor-input").rglob("*") if p.is_file()]
    paths += [
        p
        for p in (ROOT / "prior-package").rglob("*")
        if p.is_file() and not any(part.startswith("executed-") for part in p.parts)
    ]
    return {
        str(p.relative_to(ROOT)): {"sha256": sha(p.read_bytes()), "mode": p.stat().st_mode & 0o777}
        for p in sorted(set(paths))
    }


def assess(evidence, premises, binding_valid=True):
    defs, template = definitions()
    if premises.get("schema") != "FICTIONAL_WORLD_PREMISES_ONLY-v1":
        raise ValueError("fictional scope tag required")
    for forbidden in ("expected_scores", "target_scores", "scores"):
        if forbidden in premises:
            raise ValueError("scores cannot be assessor inputs")
    obs = evidence["observations"]
    by = {x["id"]: x for x in obs}
    gain = evidence["gain"]
    runtime = evidence["runtime"]
    cross = evidence["crosswalk"]
    if len(by) != len(obs):
        raise ValueError("duplicate actual observation identity")
    # Reconstruct selection and confirmation from complete actual records, never trust a saved gain.
    actual_lineage = evidence["selection"]["lineage"]
    feedback_consistent = all(x["feedback"] is by[x["observation"]]["result"]["accepted"] for x in actual_lineage)
    totals = {
        p: sum(by[x["observation"]]["result"]["accepted"] is True for x in actual_lineage if x["candidate"] == p)
        for p in {x["candidate"] for x in actual_lineage}
    }
    actual_selected = sorted(totals, key=lambda p: (-totals[p], p))[0]
    actual_diffs: list[Any] = []
    pair_consistent = True
    for pair in gain["pairs"]:
        for arm in pair["arms"].values():
            pair_consistent = pair_consistent and arm["result"] == by[arm["observation"]]["result"]
        first = by[pair["arms"][actual_selected]["observation"]]["result"]["accepted"]
        second = by[pair["arms"]["fixed-template"]["observation"]]["result"]["accepted"]
        diff = int(first) - int(second) if type(first) is bool and type(second) is bool else None
        actual_diffs.append(diff)
        pair_consistent = pair_consistent and pair["difference"] == diff
    measured_gain = sum(actual_diffs) / len(actual_diffs) if all(x is not None for x in actual_diffs) else None
    gain_consistent = (
        pair_consistent
        and gain["difference"] == measured_gain
        and feedback_consistent
        and actual_selected == evidence["selection"]["selected"]
    )

    # Reconcile complete owning results before any cue-dependent interpretation.
    # Cross-arm style comparisons may omit byte digests; arm-to-owner comparisons must not.
    def cue_pair_consistent(pair):
        return pair["transition"] == [
            by[pair["arms"][arm]["observation"]]["result"]["accepted"] for arm in ("baseline", "intervention")
        ] and all(packed(arm["result"]) == packed(by[arm["observation"]]["result"]) for arm in pair["arms"].values())

    cue_consistent = all(cue_pair_consistent(pair) for pair in evidence["cues"])
    gain_consistent = gain_consistent and cue_consistent
    iso = evidence["isolation"]
    custody = evidence["custody"]
    cue = evidence["cues"]
    inventory = evidence["inventory"]
    lineage = evidence["selection"]["lineage"]
    checks = {}
    rules = {}
    summary = evidence["summary"]

    # References use stable artifact paths and JSON pointers, each digest-bound at output.
    def ref(file, pointer=""):
        return {"path": file, "pointer": pointer, "attribution": "ACTUAL_LOCAL_MEASUREMENT"}

    def premise(key):
        return {
            "path": "bound-premises.json",
            "pointer": "/reviews/" + key,
            "attribution": "SUPPLIED_FICTIONAL_PREMISE",
        }

    def gate(item, index, ok, detail, refs, review=None, na=None):
        definition = defs[item]["checks"][index]
        evidence_refs = list(refs)
        if review:
            evidence_refs.append(premise(review))
        exists = not review or _dispositions.accepted(
            premises.get("reviews", {}).get(review), review, evidence, source_manifest()
        )
        status = "NOT_APPLICABLE" if na else ("PASS" if ok and exists else "FAIL")
        value = {
            "check_id": definition["id"],
            "method": definition["method"],
            "action": definition["action"],
            "expected_evidence": definition["expected_evidence"],
            "when": definition["when"],
            "outcome": status,
            "actual_measurement_predicate": bool(ok),
            "premise_present": bool(exists),
            "interpretation": detail,
            "evidence_refs": evidence_refs,
            "attribution": "MIXED_ACTUAL_MEASUREMENT_AND_SUPPLIED_PREMISE" if review else "ACTUAL_LOCAL_MEASUREMENT",
            "method_qualification_refs": [
                {
                    "path": "bound-premises.json",
                    "pointer": "/method_qualification",
                    "attribution": "SUPPLIED_FICTIONAL_PREMISE",
                }
            ]
            if review
            else [],
        }
        if na:
            value["applicability_reason"] = na
        if status == "FAIL":
            value["remaining_gap"] = detail + " [predicate or required premise not satisfied]"
        checks.setdefault(item, []).append(value)

    def rule(item, base, top, description, review=None, defect=False, na=None):
        qualification = _dispositions.accepted(
            premises.get("method_qualification", {}).get("alternative"), "alternative", evidence, source_manifest()
        )
        if review:
            top = bool(
                top
                and _dispositions.accepted(premises.get("reviews", {}).get(review), review, evidence, source_manifest())
                and qualification
            )
        rules[item] = {
            "base": bool(base),
            "top": bool(top),
            "description": description,
            "review": review,
            "defect": bool(defect),
            "na": na,
        }

    all_refund = [x for x in obs if x["family"] == "refund"]
    oracle_ok = all(x["result"] == x["oracle"] and x["record_replay_matches"] for x in all_refund)
    boundary_ids = [
        "valid",
        "optional-false",
        "split-preledger-alternative",
        "threshold-4999",
        "threshold-5000",
        "valid-zero-inaction",
    ]
    boundaries = all(by[x]["result"]["accepted"] is True for x in boundary_ids)
    public = ref("condition-crosswalk.json")
    outcomes = ref("observations.json")
    for item, review in [
        ("task-contract.clear-contract", "contract"),
        ("task-contract.discoverable-requirements", "discovery"),
        ("task-contract.scoring-authority", "authority"),
    ]:
        coverage = set(cross) == set(by["valid"]["result"]["checks"]) and all(
            x.get("quoted") and x.get("requirement") for x in cross.values()
        )
        if item.endswith("clear-contract"):
            gate(
                item,
                0,
                coverage and oracle_ok,
                "Public nine-condition extraction and independent specification oracle have no measured contradiction.",
                [public, outcomes],
                review,
            )
            gate(
                item,
                1,
                coverage,
                "B/V independently resolve goal, action permission, boundaries and optional signal only as supplied fiction.",
                [public],
                review,
            )
            gate(
                item,
                2,
                boundaries,
                "Alternate manager, split positive amounts, pre-effect ledger and inclusive-threshold/zero-action boundaries actually pass.",
                [outcomes],
                review,
            )
            rule(
                item,
                coverage and oracle_ok,
                boundaries,
                "Independent interpretations plus distinct boundary solutions corroborate public semantics.",
                review,
                defect=not oracle_ok,
            )
        elif item.endswith("discoverable-requirements"):
            gate(
                item,
                0,
                coverage,
                "All nine checks mapped to public requirement pointers and exact instruction quotations; entailment is supplied fictional B/V review.",
                [public],
                review,
            )
            gate(
                item,
                1,
                coverage,
                "Check IDs, mandatory membership, weights and quoted spans cover the complete scorer inventory.",
                [public],
            )
            blind = bool(premises.get("actors", {}).get("B", {}).get("excluded_until_findings_freeze"))
            gate(
                item,
                2,
                coverage and blind,
                "Fictional B follows instruction -> public contract; supplied blind chronology and OS access are not actual blind execution.",
                [public],
                review,
            )
            rule(
                item,
                coverage,
                coverage and blind,
                "Fictional public-only B finds all requirements before scorer comparison.",
                review,
            )
        else:
            gate(
                item,
                0,
                oracle_ok,
                "Public arithmetic/relationships independently implemented in collect.oracle; fictional V supplies independent adjudication.",
                [outcomes, public],
                review,
            )
            gate(
                item,
                1,
                oracle_ok and boundaries,
                "Per-check gate, entity, destination, protected history, positive amounts and optional credit corroborated.",
                [outcomes, public],
                review,
            )
            gate(
                item,
                2,
                coverage,
                "All scoring obligations have explicit public fields, weights and mandatory status.",
                [public],
            )
            rule(
                item,
                coverage and oracle_ok,
                boundaries,
                "Independent public-specification authority and boundary adjudication, conditional on V premise.",
                review,
                defect=not oracle_ok,
            )
    item = "AUDIT-03"
    attacks = [
        "no-work-forged-success",
        "early-exit-inert-copy",
        "wrong-destination",
        "missing-approval",
        "late-approval",
        "all-failure-reachable",
        "gateway-schema-errors",
        "repeated-effect",
    ]
    attack_ok = all(
        by[x]["result"]["health"] == "ok"
        and by[x]["result"] == by[x]["oracle"]
        and by[x]["result"]["total_credit"] == 0
        for x in attacks
    )
    schema_rejections = by["gateway-schema-errors"]["rejections"]
    schema_ok = len(schema_rejections) == 4 and all(x["unchanged"] for x in schema_rejections)
    denied = all(x["passed"] for x in custody["checks"])
    gate(
        item,
        0,
        boundaries and len(attacks) == 8,
        "Attack register separates bad work, no work, forged/inert artifacts, gateway-denied diagnostic actions, legitimate optional credit and valid inaction.",
        [outcomes, ref("prior-execution/custody-controls/verification.json")],
        "attacks",
    )
    gate(
        item,
        1,
        attack_ok and schema_ok and denied,
        "Every applicable action control reaches unchanged verifier; runtime tamper/reference denial is explicitly simulated OS, not native defense. Private malformed input is separate operational error.",
        [outcomes, ref("operational-error/receipt.json"), ref("prior-execution/custody-controls/verification.json")],
        "attacks",
    )
    gate(
        item,
        2,
        attack_ok and oracle_ok,
        "All affected per-check vectors and totals compared, not only acceptance; rejected schemas do not count as successful semantic attacks.",
        [outcomes],
    )
    rule(
        item,
        attack_ok and schema_ok and denied,
        oracle_ok and boundaries,
        "A-held-out authorship is supplied fiction; actual vectors remain authoritative.",
        "attacks",
        defect=not oracle_ok,
    )
    confirmed = [
        x["id"]
        for x in all_refund
        if x["result"]["health"] == "ok" and x["result"]["total_credit"] > x["oracle"]["total_credit"]
    ]
    item = "AUDIT-04-B"
    na = (
        "No confirmed forbidden-credit mechanism in this selected refund revision: measured public-path attacks match semantic authority. Historical lower-score subjects and collector operational errors are not accepted affected results of this task."
        if not confirmed
        else None
    )
    for i in range(3):
        gate(item, i, not confirmed, "Confirmed affected inventory: " + repr(confirmed), [outcomes], na=na)
    rule(
        item,
        False,
        False,
        "Containment trigger is a confirmed exploit, not an attempted attack.",
        na=na,
        defect=bool(confirmed),
    )
    item = "AUDIT-04-B-SUITE"
    # Only observations with a semantic oracle can confirm forbidden credit; strict file-scorer
    # companions carry no oracle, so they neither confirm nor rule out an exploit here.
    confirmed_suite = [
        x["id"]
        for x in obs
        if "oracle" in x and x["result"]["health"] == "ok" and x["result"]["total_credit"] > x["oracle"]["total_credit"]
    ]
    na_suite = (
        "No confirmed forbidden-credit mechanism among oracle-checked observations in the suite; "
        "suite containment is trigger-based."
        if not confirmed_suite
        else None
    )
    for i in range(3):
        gate(
            item,
            i,
            not confirmed_suite,
            "Confirmed affected inventory across families: " + repr(confirmed_suite),
            [outcomes],
            na=na_suite,
        )
    rule(
        item,
        False,
        False,
        "Suite containment trigger is a confirmed exploit in any family.",
        na=na_suite,
        defect=bool(confirmed_suite),
    )
    item = "AUDIT-05-B"
    gate(
        item,
        0,
        bool(inventory["task_files"]) and len(lineage) == 6,
        "Task/reference identity inventory plus six development events; fictional logical chronology is separate from actual unrestricted builder exposure. Training provenance unknown; no model executed.",
        [ref("inventory.json"), ref("selection-and-analysis-freeze.json")],
        "exposure",
    )
    gate(
        item,
        1,
        True,
        "No cleanliness or broad population claim. Both actual full exposure and fictional untouched premise are disclosed without conflation.",
        [ref("gain.json")],
        "exposure",
    )
    expected_sensitivity = (
        sum(p["difference"] for p in gain["pairs"] if p["index"] % 2 == 1) / 3
        if all(p["difference"] is not None for p in gain["pairs"])
        else None
    )
    gate(
        item,
        2,
        gain["sensitivity_difference"] == expected_sensitivity,
        "Predeclared first-of-family exclusion recomputed; original six-case results remain, three-case sensitivity has no population CI.",
        [ref("gain.json"), ref("selection-and-analysis-freeze.json")],
    )
    rule(
        item,
        True,
        expected_sensitivity is not None,
        "Independent fictional provenance review plus computed exclusion sensitivity support only the bounded finite statement.",
        "exposure",
    )
    item = "AUDIT-05-S03"
    candidates = {x["candidate"] for x in lineage}
    complete = len(lineage) == 6 and candidates == {"evidence", "fixed-template"}
    pairs_ok = (
        gain_consistent
        and len(gain["pairs"]) == 6
        and all(type(a["result"]["accepted"]) is bool for p in gain["pairs"] for a in p["arms"].values())
    )
    no_overlap = not gain["dev_confirmation_overlap"]
    gate(
        item,
        0,
        complete and no_overlap,
        "All candidates, raw feedback, modifications, selection reason and family/partition identities retained. Dev/confirmation exact case overlap is empty; independence is a premise, not hash inference.",
        [ref("selection-and-analysis-freeze.json"), ref("gain.json")],
        "confirmation",
    )
    gate(
        item,
        1,
        complete and no_overlap,
        "Fixed comparator/selector/acceptance rule precede one opening; A/V separation and untouched history exist only in supplied fiction.",
        [ref("selection-and-analysis-freeze.json"), ref("confirmation-cases.json")],
        "confirmation",
    )
    gate(
        item,
        2,
        pairs_ok and denied and summary["frozen_candidates"] == 12,
        "All 12 captured candidate outputs frozen before grading; complete pairs and ties retained; local controls reject second opening and early feedback.",
        [ref("confirmation-outcomes.json"), ref("prior-execution/custody-controls/verification.json")],
        "confirmation",
    )
    rule(
        item,
        complete and no_overlap and pairs_ok,
        pairs_ok and gain["difference"] is not None and gain["difference"] > 0,
        "Positive full-cohort paired gain must be computed; null/zero/loss never earns the top anchor. Fictional independent confirmation only.",
        "confirmation",
    )
    item = "TRACE-04-A"
    trace = evidence["trace"]
    gate(
        item,
        0,
        trace["fields_screened"] > 0 and trace["files_screened"] > 0,
        "Recursive local field/channel inventory screens synthetic traces and records private metadata locations; regex alone does not clear release.",
        [ref("trace-review.json")],
    )
    gate(
        item,
        1,
        bool(trace["channels"]) and trace["disposition"] == "private-fictional-review-only",
        "V independently reviews contextual fields and negatives; O exact-use disposition is supplied fiction.",
        [ref("trace-review.json")],
        "trace",
    )
    gate(
        item,
        2,
        trace["release_digest"] == sha(packed(trace["release_manifest"]))
        and trace["recipients"] == ["I", "parent-reviewer"]
        and trace["use"] == "fictional assessment only"
        and trace["disposition"] == "private-fictional-review-only",
        "Exact material digest, private recipients and fictional-assessment use match supplied O authorization; no empirical approval record.",
        [ref("trace-review.json")],
        "trace",
    )
    rule(
        item,
        trace["fields_screened"] > 0,
        trace["release_digest"] == sha(packed(trace["release_manifest"])),
        "Independent labels and release audit are supplied fictional premises with actual digest matching.",
        "trace",
    )
    item = "ENV-03-S01"
    overlap = iso["overlap"]
    reset = iso["before_sha256"] == iso["after_sha256"]
    parallel = max(x["start_ns"] for x in overlap) < min(x["end_ns"] for x in overlap)
    standalone = iso["schedule"][0]
    parallel = (
        parallel
        and {x["id"] for x in overlap} == {"U", "T"}
        and len(overlap) == 2
        and all(
            x["result"] == standalone["result"] and x["state_sha256"] == standalone["state_sha256"] for x in overlap
        )
    )
    ordered = all(
        x["state_sha256"] == standalone["state_sha256"] and x["result"] == standalone["result"]
        for x in iso["schedule"]
        if "after" in x["id"]
    )
    gate(
        item,
        0,
        True,
        "Material surfaces: episode workspace, candidate/state record, closure marker. Immutable code shared. No task caches, credentials, backend, memory, rate limits or queues; clock not scored.",
        [ref("state-surfaces.json")],
        "isolation",
    )
    gate(
        item,
        1,
        reset and all(x["record_replay_matches"] for x in all_refund),
        "Exact candidate/state reset and persisted replay correspondence; fresh episode replaces closed record.",
        [ref("prior-execution/isolation.json"), outcomes],
    )
    ordered = (
        ordered
        and len(evidence["permutations"]) == 2
        and all(x["same_result"] and x["same_state"] for x in evidence["permutations"])
    )
    gate(
        item,
        2,
        ordered and parallel and iso["late_read_elapsed_ns"] >= 3_000_000_000,
        "Actual U/T overlap, targets after U and V and after U/V versus V/U, exact reset and delayed read; held-out/independent authorship is supplied fiction, not measured host independence.",
        [ref("prior-execution/isolation.json"), ref("permuted-order.json")],
        "isolation",
    )
    gate(
        item,
        3,
        True,
        "No stochastic agent or unresolved deterministic outcome; stochastic repeats not required.",
        [ref("runtime.json")],
        na="Deterministic fixed policies; no unresolved stochastic outcome question.",
    )
    rule(
        item,
        reset and ordered,
        parallel and iso["late_read_elapsed_ns"] >= 3_000_000_000,
        "Fictional V/A held-out repeat supports independently authored order/parallel/delay; actual deterministic observations retained.",
        "isolation",
    )
    item = "CREATE-02"
    register = evidence["cue_register"]
    families = {x["family"] for x in cue}

    def isolated_style(pair):
        left, right = (pair["arms"][arm] for arm in ("baseline", "intervention"))
        try:
            return (
                left["view"] == right["view"]
                and left["case_sha256"] == right["case_sha256"]
                and left["policy_input_wire"] != right["policy_input_wire"]
                and json.loads(left["policy_input_wire"]) == left["view"] == json.loads(right["policy_input_wire"])
                and left["candidate_wire"] != right["candidate_wire"]
                and json.loads(left["candidate_wire"]) == json.loads(right["candidate_wire"])
                and left["candidate_sha256"] == right["candidate_sha256"]
                and {k: v for k, v in left["result"].items() if k != "candidate_sha256"}
                == {k: v for k, v in right["result"].items() if k != "candidate_sha256"}
                and all(
                    arm["candidate_wire"] == by[arm["observation"]]["candidate_wire"]
                    and sha(packed(json.loads(arm["candidate_wire"]))) == arm["candidate_sha256"]
                    and json.loads(arm["candidate_wire"]) == by[arm["observation"]]["candidate"]
                    and arm["candidate_consumer"] == by[arm["observation"]]["candidate_consumer"]
                    for arm in (left, right)
                )
            )
        except (KeyError, TypeError, ValueError):
            return False

    def cue_predicates(pairs):
        styles = [p for p in pairs if p["class"] == "style"]
        consistent = all(cue_pair_consistent(p) for p in pairs)
        style_ok = (
            bool(styles)
            and {p["family"] for p in styles} == {p["family"] for p in pairs}
            and all(isolated_style(p) for p in styles)
        )
        stable = (
            consistent and style_ok and all(p["transition"] == [True, True] for p in pairs if p["class"] != "cue-only")
        )
        changed = all(
            p["arms"]["baseline"]["candidate_sha256"] != p["arms"]["intervention"]["candidate_sha256"]
            for p in pairs
            if p["class"] == "cue-only"
        )
        return consistent, style_ok, stable, changed

    # Task-level CREATE-02 reads only the refund pairs; companion families feed CREATE-02-SUITE.
    refund_cue = [p for p in cue if p["family"] == "refund"]
    refund_consistent, style_ok, stable, cue_changed = cue_predicates(refund_cue)
    gate(
        item,
        0,
        len(register["classes"]) == 8 and bool(register["case_counts"]),
        "All eight candidate cue classes registered; exact duplicate/answer-position counts and local answer artifacts retained, not interpreted as causal proof.",
        [ref("cue-register.json")],
    )
    gate(
        item,
        1,
        refund_consistent and all(x["requirements_unchanged"] for x in refund_cue) and boundaries and style_ok,
        "V qualifies semantics, feasibility, unchanged full authority and valid alternate actions. Lucky correct cue guesses do not contradict a correct-actions claim.",
        [ref("cue-pairs.json"), outcomes],
        "cues",
    )
    refund_classes = {p["class"] for p in cue if p["family"] == "refund"}
    gate(
        item,
        2,
        refund_consistent
        and refund_classes == {"cue-only", "nuisance", "misleading", "style"}
        and cue_changed
        and style_ok,
        "Actual four-class paired comparisons on the refund task; cue-only views omit decisive case data and cue flips affect selected candidates.",
        [ref("cue-pairs.json")],
        "cues",
    )
    gate(
        item,
        3,
        stable,
        "Full cue-arm results match owning observations, including checks and byte digests; full-evidence perturbations preserve correct completion. Cue-only guesses may win/tie/lose; finite families only, no generalization CI.",
        [ref("cue-pairs.json")],
    )
    rule(
        item,
        stable and cue_changed and boundaries,
        refund_classes == {"cue-only", "nuisance", "misleading", "style"},
        "Fictional independently authored held-out counterexamples on the refund task.",
        "cues",
    )
    item = "CREATE-02-SUITE"
    _, suite_style_ok, suite_stable, _ = cue_predicates(cue)
    new_families = families - {"refund"}
    per_family = {f: {p["class"] for p in cue if p["family"] == f} for f in new_families}
    gate(
        item,
        0,
        new_families == {"interval-reservation", "dependency-release"},
        "Two companion task families authored separately from the refund development family.",
        [ref("cue-pairs.json")],
        "cues",
    )
    gate(
        item,
        1,
        bool(per_family) and all(c == {"cue-only", "nuisance", "misleading", "style"} for c in per_family.values()),
        "The task-level four-class cue controls ran on each new family.",
        [ref("cue-pairs.json"), outcomes],
    )
    gate(
        item,
        2,
        suite_stable and suite_style_ok,
        "Paired transitions are stable across all three families; no bypass appears only outside the development family.",
        [ref("cue-pairs.json")],
    )
    rule(
        item,
        suite_stable and len(new_families) == 2,
        all(c == {"cue-only", "nuisance", "misleading", "style"} for c in per_family.values()) and len(per_family) == 2,
        "Actually scored new-family pairs plus fictional independent family authorship.",
        "cues",
    )

    item = "provenance.task-artifact-access"
    files = inventory["task_files"]
    access = bool(files) and all(v["actual_reviewer_read"] == "READ" and len(v["sha256"]) == 64 for v in files.values())
    gate(
        item,
        0,
        access,
        "Complete copied task directories with file roles, bytes, digests, public/reference origins.",
        [ref("inventory.json")],
    )
    gate(
        item,
        1,
        access,
        "Every inventoried task artifact actually read; reviewer access is not solver access.",
        [ref("inventory.json")],
    )
    gate(
        item,
        2,
        access,
        "B/V independent inventory and access reconstruction is supplied fiction, bound to the actual inventory.",
        [ref("inventory.json")],
        "artifact",
    )
    rule(item, access, access, "Complete task inventory plus supplied independent reconstruction.", "artifact")
    item = "provenance.grading-path"
    path_ok = all(x["exit_code"] == 0 for x in all_refund) and runtime["declared"]["grader"] == "reviewed"
    gate(
        item,
        0,
        path_ok,
        "Declared local route selects unchanged verify.py --record and reviewed scorer; companion grade_paths explicitly separate.",
        [ref("runtime.json"), outcomes],
    )
    gate(
        item,
        1,
        path_ok,
        "Observed local subprocess exits, raw checks and full semantic vectors agree with declared scorer dispatch.",
        [outcomes, ref("runtime.json")],
    )
    gate(
        item,
        2,
        True,
        "No unresolved dynamic dispatch branch in selected route.",
        [ref("runtime.json")],
        na="Selection explicit; no ambiguous branch requires additional interpretation.",
    )
    gate(
        item,
        3,
        True,
        "No optional LLM assistance needed for explicit dispatch.",
        [ref("runtime.json")],
        na="No ambiguous dispatch; optional assistance not invoked.",
    )
    gate(
        item,
        4,
        path_ok,
        "V independently resolves identical route from source packet, as supplied fiction.",
        [ref("runtime.json")],
        "path",
    )
    rule(item, path_ok, path_ok, "Independent fictional route reconstruction; no native route claim.", "path")
    item = "provenance.grading-dependency-access"
    deps = bool(inventory["runtime_import_origins"]) and access and bool(inventory["interpreter_sha256"])
    for i in range(2):
        gate(
            item,
            i,
            deps,
            "Selected task-specific sources and imported runtime source origins are accessible and hash-bound. No remote grader/model dependency.",
            [ref("inventory.json")],
        )
    gate(
        item,
        2,
        deps,
        "V resolves local imports/generated sources and explicitly excludes native image runtime from selected alternative.",
        [ref("inventory.json"), ref("runtime.json")],
        "dependencies",
    )
    gate(
        item,
        3,
        deps,
        "V independently verifies same dependency closure and exclusions, supplied fiction.",
        [ref("inventory.json")],
        "dependencies",
    )
    rule(item, deps, deps, "Independent dependency coverage/access reconstruction is a premise.", "dependencies")
    item = "provenance.declared-configuration"
    config = (
        runtime["declared"]
        == {
            "provider": "local-ledger-source-v1",
            "grader": "reviewed",
            "subject": "perfect-task/refund",
            "max_actions": 1000,
        }
        and runtime["models"] is None
    )
    gate(
        item,
        0,
        config,
        "Finite scripted policies, source identity, local provider/grader, flags and permissions explicit; no model/prompts/sampling.",
        [ref("runtime.json")],
    )
    gate(
        item,
        1,
        config,
        "All relevant local settings sourced; provider-hidden internals inapplicable, native resource controls unclaimed.",
        [ref("runtime.json")],
    )
    gate(
        item,
        2,
        config,
        "V limits claim to finite deterministic local alternative and fictional OS. No unmeasured deployment budget inferred.",
        [ref("runtime.json")],
        "configuration",
    )
    gate(
        item,
        3,
        config,
        "V reconstructs same declared configuration and its limits, supplied fiction.",
        [ref("runtime.json")],
        "configuration",
    )
    rule(item, config, config, "Independent fictional manifest reconstruction.", "configuration")
    item = "provenance.observed-configuration"
    repeat = by["valid"]["result"] == by["independent-role-repeat"]["result"]
    flags = all(runtime["flags"].values())
    gate(
        item,
        0,
        config and flags and path_ok,
        "Actual runtime flags, source manifest and subprocess result correspond to declared route.",
        [ref("runtime.json"), outcomes],
    )
    gate(
        item,
        1,
        True,
        "No remote/provider hidden values guessed. Actual interpreter is separate from simulated OS identity.",
        [ref("runtime.json")],
    )
    gate(
        item,
        2,
        repeat,
        "Second local execution matches all checks; independent operator status only supplied fictional V premise.",
        [outcomes],
        "repeat",
    )
    rule(
        item,
        config and flags and path_ok,
        repeat,
        "Same configuration/control repeat, fictional independent actor.",
        "repeat",
    )
    item = "provenance.runtime-route"
    statuses = [x["status"] for x in runtime["validation"]]
    compatible = statuses[0] == "COMPATIBLE"
    invalid = all(x != "COMPATIBLE" for x in statuses[1:])
    gate(
        item,
        0,
        compatible,
        "Provider-owned local-ledger-source-v1 validation; exact manifest/adapter checked, not Harbor.",
        [ref("runtime.json")],
        "runtime",
    )
    gate(
        item,
        1,
        compatible and invalid,
        "Typed unsupported route/adapter and invalid type outcomes kept distinct from solver behavior.",
        [ref("runtime.json")],
    )
    gate(
        item,
        2,
        compatible and invalid,
        "Clean supported manifest accepted; four wrong-route/type/grader manifests rejected; independent setup supplied fiction.",
        [ref("runtime.json")],
        "runtime",
    )
    rule(
        item,
        compatible,
        invalid,
        "Local alternative compatibility only, independent setup stipulated.",
        "runtime",
        defect=not compatible,
    )
    item = "provenance.comparison-fairness"
    fair = evidence["fairness"]
    matched = len(fair) == 12 and all(x["same_result"] for x in fair)
    gate(
        item,
        0,
        complete,
        "Only policy code differs; public views, scorer, action limit and budgets equal. No model accommodation.",
        [ref("runtime.json"), ref("selection-and-analysis-freeze.json")],
    )
    gate(
        item,
        1,
        complete,
        "V justifies evidence-use versus stale-template policy as experimental variable, not special information access.",
        [ref("runtime.json")],
        "fairness",
    )
    gate(
        item,
        2,
        matched and stable,
        "Matched reversed-order execution preserves all per-check results; full-evidence nuisance/style pairs preserve supported completion.",
        [ref("fairness.json"), ref("cue-pairs.json")],
    )
    rule(
        item,
        complete,
        matched and stable,
        "Matched sensitivity across ordering and presentation; bounded fixed-policy comparison.",
        "fairness",
    )
    item = "provenance.solver-tool-access"
    closed = all(x["after_close"] == "ledger_closed" for x in all_refund)
    gate(
        item,
        0,
        boundaries,
        "read/act/complete and approve/refund/ledger mapped; no solver reset; fictional OS grants exact gateway access.",
        [public, outcomes],
        "tools",
    )
    gate(
        item,
        1,
        schema_ok,
        "Supported action schema executes; boolean cents, extra field, diagnostic and protected writes rejected unchanged.",
        [outcomes],
        "tools",
    )
    gate(
        item,
        2,
        schema_ok and closed and reset,
        "Actual legal calls, schema errors, post-close denial and fresh-reset behavior; fictional independent solver permissions, not UID evidence.",
        [outcomes, ref("prior-execution/isolation.json")],
        "tools",
    )
    rule(
        item,
        boundaries and schema_ok,
        closed and reset,
        "Independent fictional clean reset/error/boundary corroboration.",
        "tools",
    )
    item = "provenance.dependency-substitution"
    gate(
        item,
        0,
        deps and config,
        "All local software present; ledger is declared offline target. Transport/container replacement explicitly declared local alternative.",
        [ref("inventory.json"), ref("runtime.json")],
    )
    gate(
        item,
        1,
        config,
        "O authorizes exact source-bound local alternative only inside fiction; no real owner approval or store event.",
        [ref("runtime.json")],
        "substitution",
    )
    gate(
        item,
        2,
        deps and config,
        "Actual route/dependency identities reconciled to supplied O scope and V inventory review.",
        [ref("inventory.json"), ref("runtime.json")],
        "substitution",
    )
    rule(
        item,
        deps and config,
        deps and config,
        "Independent provisioning/approval reconciliation only in supplied world.",
        "substitution",
    )
    item = "provenance.external-state"
    drift = not by["drift-old-tax-answer"]["result"]["accepted"] and by["drift-new-tax-answer"]["result"]["accepted"]
    gate(
        item,
        0,
        access,
        "All mutable order/policy/history snapshots recorded; code and interpreter identified; no live API or clock scoring.",
        [outcomes, ref("inventory.json")],
    )
    gate(
        item,
        1,
        True,
        "Public instruction says changed tax/policy needs new correct answer. V corroborates relation, not invariant reward.",
        [public, outcomes],
        "state",
    )
    gate(
        item,
        2,
        drift and repeat,
        "Old actions lose credit after tax drift; freshly computed/executed valid actions pass; exact repeat retained.",
        [outcomes],
        "state",
    )
    rule(
        item,
        access,
        drift and repeat,
        "Independent fictional replay and actual deliberate drift support interpretation.",
        "state",
    )
    item = "provenance.evidence-revision-consistency"
    inject = evidence["revision"]
    invalidation = all(x["rejected"] for x in inject["changes"]) and inject["unchanged_reuse"]
    gate(
        item,
        0,
        binding_valid,
        "Result bound to complete local source, canonical, premise, policy and runtime identities; changed content invalidates entire finite packet conservatively.",
        [ref("source-manifest.json"), ref("evidence-manifest.json")],
    )
    gate(
        item,
        1,
        binding_valid,
        "Current source/evidence identities checked; historical attempts retained, not resealed or given current authority.",
        [ref("source-manifest.json"), ref("revision-tests.json")],
    )
    gate(
        item,
        2,
        invalidation,
        "Each current material source hash changed in a separate in-memory fixture must invalidate; unchanged byte identity reuses; originals untouched.",
        [ref("revision-tests.json")],
    )
    gate(
        item,
        3,
        True,
        "No separate ETHOS artifact governs this task. Public requirements and supplied scope/intent remain bound and covered by content-change tests.",
        [public, ref("revision-tests.json")],
        na="No ETHOS dependency supplied; contract requirements and scenario intent are separately hash-bound.",
    )
    rule(
        item,
        binding_valid,
        invalidation,
        "Actual change-injection rejection and unchanged lineage reuse.",
        defect=not binding_valid,
    )

    rows = {}
    for ident, d in defs.items():
        rr = rules[ident]
        cc = checks[ident]
        assert [c["check_id"] for c in cc] == [c["id"] for c in d["checks"]]
        fail = [c["check_id"] for c in cc if c["outcome"] == "FAIL"]
        lower_checks = [c for c in cc if "For level 3" not in c["when"] and "For level 3," not in c["when"]]
        lower_ok = rr["base"] and all(c["outcome"] in ("PASS", "NOT_APPLICABLE") for c in lower_checks)
        # Scores are canonical predicate selection, never percent passed or desired mapping.
        if not binding_valid:
            score = None
            state = "NOT_ASSESSED"
        elif rr["na"]:
            score = None
            state = "NOT_APPLICABLE"
        elif lower_ok and rr["top"] and not rr["defect"] and not fail:
            score = 3
            state = "ASSESSED"
        else:
            score = None
            state = "NOT_ASSESSED"
        row = copy.deepcopy(template)
        row.update(
            item_id=ident,
            subject_id="perfect-task/refund"
            if d.get("level", "task") == "task"
            else "perfect-suite/refund+interval-reservation+dependency-release",
            level=d.get("level", "task"),
            assessment_state=state,
            status=state,
            execution_state="COMPLETED_FICTIONAL_WORLD_ASSESSMENT",
            score=score,
            target_score=None,
            source=d.get("source", {"skill_id": d.get("skill_id")}),
            evidence_strength="MIXED: real local measurements + explicitly supplied fictional premises",
            applicability_reason=rr["na"],
            check_results=cc,
            approval_refs=[],
            method_qualification_refs=[
                {
                    "path": "bound-premises.json",
                    "pointer": "/method_qualification",
                    "attribution": "SUPPLIED_FICTIONAL_PREMISE",
                }
            ],
            findings=[rr["description"], NOTICE],
            remaining_gaps=(
                ["Stale source/evidence binding: recollect and reassess this packet."]
                if not binding_valid
                else fail
                + (
                    []
                    if state in ("ASSESSED", "NOT_APPLICABLE")
                    else [
                        d["scoring"]["3"],
                        "This finite adapter implements criterion-specific top-anchor/N/A predicates only; a failed predicate requires separate lower-anchor assessment, never a passed-check fraction.",
                    ]
                )
            ),
            canonical_anchors=copy.deepcopy(d["scoring"]),
            scope=d["scope"],
            canonical_requirement=d["requirement"],
            canonical_boundary=d["boundary"],
            top_anchor_predicate={"satisfied": score == 3 and binding_valid, "interpretation": rr["description"]},
            next_action="Inspect the scoped fictional predicates and retained measurements; no empirical promotion.",
            display_review_summary="Supplied fictional B/V/O premises only. No actual human approval.",
            evidence_refs=[r for c in cc for r in c["evidence_refs"]],
            measurements=[],
        )
        if ident == "AUDIT-05-S03":
            row["measurements"] = [
                {
                    "name": "finite_paired_correctness_gain",
                    "value": gain["difference"],
                    "unit": "accepted fraction difference",
                    "denominator": gain["n"],
                    "population_inference": False,
                }
            ]
        if ident == "AUDIT-05-B":
            row["measurements"] = [
                {
                    "name": "exclusion_sensitivity",
                    "value": gain["sensitivity_difference"],
                    "denominator": gain["sensitivity_n"],
                }
            ]
        rows[ident] = row
    report = {
        "schema": "FICTIONAL_WORLD_CANONICAL_ASSESSMENT-v1",
        "notice": NOTICE,
        "empirical_qualification": False,
        "native_harbor_run": False,
        "items": rows,
        "measurements": {"actual_local_summary": summary},
        "current_inventory": list(defs),
        "source_versions": {
            n: read(ROOT / "prior-package/canonical" / n)["rubric_version"]
            for n in (
                "task-contract-authority.json",
                "leakage-and-credit.json",
                "artifact-configuration-provenance.json",
            )
        },
        "assessment_adapter": "Separate fictional predicate assessment, not empirical ordinary assessment CLI. Canonical checks and top-anchor text unchanged.",
    }
    if "harbor" in evidence:
        adapter = types.ModuleType("base_adapter")
        exec(compile((ROOT / "base_adapter.py").read_bytes(), str(ROOT / "base_adapter.py"), "exec"), adapter.__dict__)
        report = adapter.apply(report, evidence, premises, source_manifest(), _dispositions.accepted, binding_valid)
    return report


def gather(out):
    mapping = {
        "observations": "observations.json",
        "gain": "gain.json",
        "runtime": "runtime.json",
        "crosswalk": "condition-crosswalk.json",
        "isolation": "prior-execution/isolation.json",
        "custody": "prior-execution/custody-controls/verification.json",
        "cues": "cue-pairs.json",
        "inventory": "inventory.json",
        "selection": "selection-and-analysis-freeze.json",
        "summary": "collection-summary.json",
        "fairness": "fairness.json",
        "permutations": "permuted-order.json",
        "trace": "trace-review.json",
        "cue_register": "cue-register.json",
        "revision": "revision-tests.json",
    }
    if (out / "harbor.json").exists():
        mapping.update(harbor="harbor.json", containment="containment.json")
    return {k: read(out / v) for k, v in mapping.items()}


def supplement(out, manifest):
    observations = read(out / "observations.json")
    cue = read(out / "cue-pairs.json")
    body = {str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(out.rglob("*.json")) if p.is_file()}
    fields = 0
    strings = []

    def visit(x, path):
        nonlocal fields
        if isinstance(x, dict):
            for k, v in x.items():
                visit(v, path + "/" + k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                visit(v, path + "/" + str(i))
        else:
            fields += 1
            if isinstance(x, str):
                strings.append((path, x))

    malformed = []
    for p in sorted(out.rglob("*.json")):
        try:
            value = read(p)
        except (ValueError, UnicodeError):
            malformed.append(
                {
                    "path": str(p.relative_to(out)),
                    "sha256": sha(p.read_bytes()),
                    "disposition": "retained malformed-input control; scan raw text, never discard",
                }
            )
            value = p.read_text(errors="replace")
        visit(value, str(p.relative_to(out)))
    markers = [
        {"location": p, "category": "private-local-path" if "/Users/" in v else "synthetic-marker"}
        for p, v in strings
        if "/Users/" in v or "SYNTHETIC" in v or "SIMULATED" in v
    ]
    trace = {
        "malformed_controls_retained": malformed,
        "files_screened": len(body),
        "fields_screened": fields,
        "channels": [
            "case",
            "candidate/action",
            "state",
            "scorer result",
            "errors",
            "custody",
            "local runtime metadata",
        ],
        "candidate_locations": markers,
        "release_manifest": body,
        "release_digest": sha(packed(body)),
        "recipients": ["I", "parent-reviewer"],
        "use": "fictional assessment only",
        "disposition": "private-fictional-review-only",
        "independent_labels": "SUPPLIED FICTIONAL V premise; no actual independent classifier",
        "contextual_negative_review": "SUPPLIED FICTIONAL review of all emitted channels and sampled negative fields; no regex clearance claimed",
    }
    save(out / "trace-review.json", trace)
    unique = {}
    for row in observations:
        unique.setdefault(row["family"], set()).add(sha(packed(row["case"])))
    register = {
        "classes": {
            "filenames": "incidental annotation reaches policy view",
            "entity_ids": "request/order identity is legitimate authority; generic answer IDs not authoritative",
            "answer_positions": "companion A/B successes counterbalanced",
            "artifact_order": "option order and allowed ledger row order varied",
            "formatting": "style metadata reaches full policy view",
            "stale_outputs": "copied stale action policies and forged reward files",
            "template_labels": "misleading choose-A metadata",
            "answer_artifacts": "reference controls reviewer-only by fictional ACL; public verifier readable",
        },
        "case_counts": {f: len(v) for f, v in unique.items()},
        "exact_duplicate_observations": len(observations) - sum(len(x) for x in unique.values()),
        "answer_position_counts": dict(
            Counter(
                row["candidate"].get("choice")
                for row in observations
                if row["family"] != "refund" and row["result"]["accepted"]
            )
        ),
        "transitions_by_class": {
            cl: dict(Counter(str(p["transition"]) for p in cue if p["class"] == cl))
            for cl in sorted({p["class"] for p in cue})
        },
        "uncertainty": "finite dependent controls, no population CI; same-source functions are not real independent reviews",
    }
    save(out / "cue-register.json", register)
    save(
        out / "state-surfaces.json",
        {
            "isolated": [
                "workspace",
                "candidate/action record",
                "refund rows",
                "ledger rows",
                "protected state",
                "closure marker",
            ],
            "shared_immutable": ["task/scorer/policy code"],
            "absent": ["credential", "remote backend", "agent memory", "rate limit", "cache", "queued task effects"],
            "shared_nonscoring": ["wall clock"],
            "reset": "candidate events empty/success false and exact initial state; fresh directory clears closure marker",
            "independent_schedule": "SUPPLIED FICTIONAL A/V authorship of actual local probes, not actual hidden ordering",
        },
    )
    checks = []

    def valid(current):
        return current == manifest

    for name in manifest:
        mutated = copy.deepcopy(manifest)
        mutated[name]["sha256"] = "0" * 64 if mutated[name]["sha256"] != "0" * 64 else "1" * 64
        checks.append({"changed_source": name, "rejected": not valid(mutated), "original_preserved": valid(manifest)})
    save(
        out / "revision-tests.json",
        {
            "changes": checks,
            "unchanged_reuse": valid(copy.deepcopy(manifest)),
            "method": "Same exact-manifest equality gate as assess-and-verify. Each changed identity tested separately; no source modified.",
            "ETHOS": "no separate ETHOS dependency; public intent and scenario premises included in manifest",
        },
    )


def render(report, out):
    # Reuse byte-identical AST function bodies only; do not import empirical store consumers.
    raw = (ROOT / "ordinary-rubric-table.py.txt").read_text()
    tree = ast.parse(raw)
    names = {"cell", "structured", "render_html"}
    selected = ast.Module(
        body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]
    )
    namespace: dict[str, Any] = {"html": html, "json": json, "re": re, "Any": Any}
    exec(compile(selected, "<unchanged ordinary renderer functions>", "exec"), namespace)
    cell, structured = namespace["cell"], namespace["structured"]
    defs, _ = definitions()
    credit = "Actual local controls; see measurements"
    if report.get("native_harbor_run"):
        controls = report["measurements"]["actual_retained_harbor"]["controls"]
        oracle, nop = controls["oracle"], controls["nop"]
        credit = (
            f"Harbor Oracle {oracle['reward_text']} ({oracle['checks']['total_credit']}/{oracle['checks']['max_credit']}); "
            f"NOP {nop['reward_text']} ({nop['checks']['total_credit']}/{nop['checks']['max_credit']})"
        )
    lines = [
        "# Fictional-world canonical assessment",
        "",
        NOTICE,
        "",
        "| Workflow | Criterion | Case | Expected | Observed rubric score | Task credit | Controls run | Evidence | Human action | Reason | Next action |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    details = ["", "## Evidence details", "", NOTICE]
    for i, (ident, row) in enumerate(report["items"].items(), 1):
        outcomes = Counter(c["outcome"] for c in row["check_results"])
        score = (
            "Not applicable"
            if row["status"] == "NOT_APPLICABLE"
            else str(row["score"])
            if row["score"] is not None
            else "Unassessed"
        )
        values = [
            "Fictional",
            defs[ident]["title"],
            "one refund task",
            "Not supplied",
            score,
            credit,
            str(dict(outcomes)),
            f"Mixed measurement/premise. [Evidence {i}](#evidence-{i})",
            "SUPPLIED FICTIONAL PREMISES; no real approval",
            row["applicability_reason"] or row["findings"][0],
            row["next_action"],
        ]
        escaped = [cell(v) for v in values]
        escaped[7] = f"Mixed measurement/premise. [Evidence {i}](#evidence-{i})"
        lines.append("| " + " | ".join(escaped) + " |")
        details.extend(
            [f'<details id="evidence-{i}"><summary>{cell(ident)} — {score}</summary>', structured(row), "</details>"]
        )
    details += [
        "<h2>Actual execution and attribution</h2>",
        structured(report["measurements"]),
        "<p>" + NOTICE + "</p>",
    ]
    md = "\n".join(lines + details) + "\n"
    (out / "report.md").write_text(md)
    rendered = namespace["render_html"](md)
    rendered = rendered.replace("Experimental rubric assessment", "Fictional-world canonical assessment").replace(
        "Scores apply only to the selected evidence. This table does not establish full task acceptance.", NOTICE
    )
    (out / "report.html").write_text(rendered)
    save(
        out / "renderer-provenance.json",
        {
            "source": "../ordinary-rubric-table.py.txt",
            "sha256": sha(raw.encode()),
            "unchanged_function_bodies": sorted(names),
            "adaptations": [
                "Fictional-specific table projection using ordinary column contract",
                "heading/disclaimer replacement only after ordinary HTML render",
            ],
            "empirical_modules_imported": False,
        },
    )
