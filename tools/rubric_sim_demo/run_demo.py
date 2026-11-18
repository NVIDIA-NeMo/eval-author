# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rubric simulation demo: one refund task scored 3 on all 21 populated criteria,
then mutated to show that every mutation scores consistently lower.

Executable evidence is real (the bundled refund scorer, socket ledger and Harbor
task parser run). Human and independent-review steps are SIMULATED personas whose
decisions are deterministic functions of the evidence they inspect. This is a
demonstration harness, not rubric qualification.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/eval-author-rubric"
sys.path.insert(0, str(SKILL / "scripts"))
import example_fixtures as fx  # noqa: E402

HERE = Path(__file__).resolve().parent
REFS = SKILL / "references"
EXPECTED = json.loads((fx.EXAMPLE / "expectations/controls.json").read_text())
# Same overlay as tests/test_rubric_example_fixtures.py: the frozen file predates the
# public "no refund effects => ordering passes" decision.
for _case in ("no-work", "all-failure"):
    EXPECTED[_case]["checks"]["approval_before_effect"] = 1
    EXPECTED[_case]["raw_credit"] += 1
CONTROLS = json.loads((fx.EXAMPLE / "controls.json").read_text())["cases"]
HELDOUT = json.loads((HERE / "heldout_attacks.json").read_text())["cases"]

TASK_MUTATIONS = [
    "completion-only",
    "approval-after-effect",
    "ledger-omission",
    "hidden-completion",
    "tax-drift",
    "missing-contract",
    "disabled-ledger",
]
EVIDENCE_MUTATIONS = ["withhold-reference", "withhold-heldout", "no-independent-review", "no-owner-review"]


def criteria() -> list[dict[str, Any]]:
    rows = []
    for name in ("leakage-and-credit", "task-contract-authority", "artifact-configuration-provenance"):
        rows.extend(json.loads((REFS / f"{name}.json").read_text())["items"])
    assert len(rows) == 21, len(rows)
    return rows


# --------------------------------------------------------------------------- execution


def _run_verifier(files: dict[str, tuple[bytes, int]], contract: dict, candidate: dict, work: Path) -> dict:
    work.mkdir(parents=True)
    trusted = work / "trusted"
    trusted.mkdir()
    for name in ("verify.py", "refund_scorer.py"):
        (trusted / name).write_bytes(files[f"tests/{name}"][0])
    (work / "contract.json").write_text(json.dumps(contract))
    (work / "submission.json").write_text(json.dumps(candidate))
    subprocess.run(
        [sys.executable, "-B", str(trusted / "verify.py"), "--contract", str(work / "contract.json"),
         "--submission", str(work / "submission.json"), "--output", str(work / "logs")],
        capture_output=True, timeout=10, cwd=trusted,
        env={"PATH": os.defpath, "HOME": str(work), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return json.loads((work / "logs/checks.json").read_text())


def _compare(observed: dict, expected: dict, *, checks: bool) -> dict:
    mismatch = observed.get("total_credit") != expected.get("total_credit") or observed.get("accepted") != expected.get("accepted")
    if checks:
        mismatch = mismatch or observed.get("checks") != expected.get("checks") or observed.get("health") != expected.get("health")
    exp_total = expected.get("total_credit") or 0
    obs_total = observed.get("total_credit") or 0
    return {
        "mismatch": mismatch,
        "forbidden": exp_total == 0 and obs_total > 0,
        "lost": exp_total > 0 and obs_total < exp_total,
    }


def blind_solve(contract: dict) -> list[dict]:
    """SIMULATED blind solver: sees only instruction.md + contract.json, never controls or scorer."""
    src, req = contract["sources"], contract["request"]
    order, policy = src["order"], src["policy"]
    total = order["merchandise_cents"] - order["discount_cents"]
    total += order["tax_cents"] if policy["refund_tax"] else 0
    total += order["shipping_cents"] if policy["refund_shipping"] else 0
    total -= sum(r["amount_cents"] for r in src["initial_state"]["refunds"] if r["order_id"] == req["order_id"])
    base = {"run_id": req["run_id"], "customer_id": req["customer_id"], "order_id": req["order_id"]}
    events: list[dict] = []
    if total >= policy["approval_threshold_cents"]:
        events.append({"kind": "approve", **base, "amount_cents": total, "actor": policy["approvers"][0]})
    events.append({"kind": "refund", "refund": {"id": "blind-1", **base, "amount_cents": total, "destination": req["destination"]}})
    events.append({"kind": "ledger", "entries": [{"refund_id": "blind-1", "amount_cents": total}]})
    return [
        {"id": "blind-with-completion", "candidate": {"success": True, "events": events}, "expect": {"total_credit": 9, "accepted": True}},
        # The instruction calls the completion point optional; a blind solver that skips it keeps 8/9.
        {"id": "blind-without-completion", "candidate": {"success": False, "events": events}, "expect": {"total_credit": 8, "accepted": True}},
    ]


def _panel(files, contract, cases, tmp: Path, *, checks: bool, order: list[int] | None = None) -> dict:
    out = {}
    idx = order if order is not None else list(range(len(cases)))
    for i in idx:
        row = cases[i]
        c = copy.deepcopy(contract)
        fx._merge(c, row.get("contract_variant", {}))
        observed = _run_verifier(files, c, row["candidate"], tmp / f"{i:02d}-{row['id']}")
        expect = row.get("expect") or EXPECTED[row["id"]]
        out[row["id"]] = {"observed": observed, **_compare(observed, expect, checks=checks)}
    return out


def _summ(panel: dict | None) -> dict:
    if panel is None:
        return {"ran": False}
    return {
        "ran": True,
        "n": len(panel),
        "mismatch": sorted(k for k, v in panel.items() if v["mismatch"]),
        "forbidden": sorted(k for k, v in panel.items() if v["forbidden"]),
        "lost": sorted(k for k, v in panel.items() if v["lost"]),
    }


def _harbor(task_toml: Path) -> dict:
    py = ROOT / ".venv/bin/python"
    code = ("import sys;from harbor.models.task.config import TaskConfig as T;"
            "T.model_validate_toml(open(sys.argv[1]).read())")
    ok = subprocess.run([str(py), "-c", code, str(task_toml)], capture_output=True).returncode == 0
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "task.toml"
        bad.write_text(task_toml.read_text().replace('network_mode = "no-network"', 'network_mode = "sometimes"', 1))
        rejected = subprocess.run([str(py), "-c", code, str(bad)], capture_output=True).returncode != 0
    return {"valid": ok, "invalid_manifest_rejected": rejected}


SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{30,}|xox[bp]-|sk-[A-Za-z0-9]{20,})")


@dataclass
class Evidence:
    case: str
    task_sha256: str
    static: dict = field(default_factory=dict)
    controls: dict = field(default_factory=dict)
    repeat_identical: bool | None = None
    order_independent: bool | None = None
    heldout: dict = field(default_factory=dict)
    blind: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    harbor: dict = field(default_factory=dict)
    drift_probe_detected: bool | None = None
    change_injection_invalidates: bool | None = None
    secret_hits: int = 0
    owner: dict = field(default_factory=dict)
    independent: dict = field(default_factory=dict)


def collect(case: str, work: Path) -> Evidence:
    task_mut = case if case in TASK_MUTATIONS else "base"
    selected = work / "selected"
    manifest = fx.materialize(selected, task_mut)
    task = selected / "assessment/task"
    files, _ = fx._selected(task_mut)
    ev = Evidence(case=case, task_sha256=manifest["task_sha256"])

    docker = (task / "environment/Dockerfile").read_text()
    instruction = (task / "instruction.md").read_text()
    verify_src = files["tests/verify.py"][0].decode()
    contract = json.loads(files["environment/contract.json"][0]) if "environment/contract.json" in files else None
    ev.static = {
        "contract_present": contract is not None,
        "dockerfile_copies_contract": "COPY contract.json" in docker,
        "entrypoint_starts_ledger": 'ENTRYPOINT ["python", "-B", "/app/server.py"]' in docker,
        "image_pinned": bool(re.search(r"^FROM \S+@sha256:[0-9a-f]{64}", docker, re.M)),
        "tests_solution_not_in_image": not re.search(r"COPY .*(tests|solution)", docker),
        "grader_matches_declared": 'GRADER = "reviewed"' in verify_src
        and (contract or {}).get("scoring", {}).get("acceptance") == "all_mandatory_checks_pass",
        "verifier_imports_present": "tests/refund_scorer.py" in files and "tests/verify.py" in files,
    }
    ev.secret_hits = sum(len(SECRET.findall(p.read_text(errors="ignore"))) for p in task.rglob("*") if p.is_file())
    ev.harbor = _harbor(task / "task.toml")

    if contract is not None:
        controls = [r for r in CONTROLS if not (case == "withhold-reference" and r["id"] == "reference")]
        panel = _panel(files, contract, controls, work / "controls", checks=True)
        ev.controls = _summ(panel)
        repeat = _panel(files, contract, controls, work / "controls-repeat", checks=True)
        rev = list(reversed(range(len(controls))))
        reordered = _panel(files, contract, controls, work / "controls-reordered", checks=True, order=rev)
        strip = lambda p: {k: v["observed"] for k, v in p.items()}  # noqa: E731
        ev.repeat_identical = strip(repeat) == strip(panel)
        ev.order_independent = strip(reordered) == strip(panel)
        ev.heldout = {"ran": False} if case == "withhold-heldout" else _summ(_panel(files, contract, HELDOUT, work / "heldout", checks=False))
        ev.blind = _summ(_panel(files, contract, blind_solve(contract), work / "blind", checks=False))
        # Deliberate drift probe on a private copy: flipping tax policy must break the reference expectation.
        drifted = copy.deepcopy(contract)
        drifted["sources"]["policy"]["refund_tax"] = not drifted["sources"]["policy"]["refund_tax"]
        ref = next(r for r in CONTROLS if r["id"] == "reference")
        ev.drift_probe_detected = _panel(files, drifted, [ref], work / "drift", checks=True)["reference"]["mismatch"]
    else:
        ev.controls = ev.heldout = ev.blind = {"ran": False, "reason": "contract_missing"}

    try:
        ledger = fx.run_ledger_controls(selected, work / "ledger")
        valid = ["reference", "split-alternative", "below-approval-threshold", "missing-completion"]
        rejects = ["helper-error", "malformed-events", "ordering-evidence-unavailable", "protected-mutation", "historical-mutation"]
        c = ledger["cases"]
        ev.ledger = {
            "ran": True,
            # Tool access only: every valid action was accepted and the verifier reached a healthy result.
            "valid_actions_ok": all(c[k]["status"] == "completed" and c[k]["action_errors"] == 0
                                    and c[k]["result"]["health"] == "ok" for k in valid),
            "boundary_rejected": all(c[k]["action_errors"] >= 1 for k in rejects),
        }
    except (ValueError, KeyError) as exc:
        ev.ledger = {"ran": False, "reason": str(exc)}

    # Change injection: a one-byte scorer edit on a private copy must change the bound task identity.
    inj = work / "inject"
    shutil.copytree(task, inj)
    p = inj / "tests/refund_scorer.py"
    p.write_bytes(p.read_bytes() + b"\n# injected\n")
    ev.change_injection_invalidates = fx.tree_identity(inj) != fx.tree_identity(task)

    simulate_reviews(ev, task)
    return ev


# --------------------------------------------------------------------------- SIMULATED people


def simulate_reviews(ev: Evidence, task: Path) -> None:
    """SIMULATED owner and independent reviewer. Decisions are deterministic functions of evidence."""
    if ev.case != "no-owner-review":
        attack_classes = {r["id"] for r in CONTROLS}
        ev.owner = {
            "persona": "SIMULATED scope owner",
            "attack_matrix_approved": {"no-work", "all-failure", "stale-carryover", "protected-mutation", "permitted-zero-balance-inaction"} <= attack_classes,
            "valid_inaction_declared": True,
            "substitution_approved": "offline ledger replaces payment provider; no money moves",
            "acceptable_drift": "none: policy is pinned in contract.json; any change requires new answers",
            "isolation_declared": "service record private (0700); /work solver-writable; reset by fresh service start",
            "exposure_statement": "synthetic task authored 2026; no public copies; training exposure unknown and disclosed",
            "selection_rule_frozen": "controls.json = development set; heldout_attacks.json = untouched confirmation set",
            "trace_disposition": "no secrets or proprietary data; synthetic identifiers only",
            "comparison": "single declared configuration; no model-specific accommodation",
            "containment": "confirmed exploit mechanisms (mutation catalog) quarantined; base repair replayed",
        }
    if ev.case != "no-independent-review":
        # Independent re-derivation through a different code path than collect().
        inventory = sorted(str(p.relative_to(task)) for p in task.rglob("*") if p.is_file())
        docker = (task / "environment/Dockerfile").read_text()
        copied = re.findall(r"^COPY (.+) /app/", docker, re.M)
        needed = {"contract.json", "ledger.py", "server.py"}
        have = {Path(x).name for x in inventory if x.startswith("environment/")}
        ev.independent = {
            "persona": "SIMULATED independent reviewer",
            "inventory": inventory,
            "inventory_agrees": needed <= have and all(any(n in c for c in copied) for n in needed),
            "grading_path_agrees": ev.static["grader_matches_declared"],
            "dependencies_agree": {"tests/verify.py", "tests/refund_scorer.py"} <= set(inventory),
            "manifest_reconstructed": ev.harbor["valid"] and ev.static["image_pinned"],
            "labels_agree_no_secrets": ev.secret_hits == 0,
        }


# --------------------------------------------------------------------------- scoring


def ladder(defect: bool, complete: bool, owner: bool, independent: bool) -> int:
    if defect:
        return 0
    if not complete or not owner:
        return 1
    return 3 if independent else 2


def score(ev: Evidence) -> dict[str, dict]:
    s, c, h, b, o, i = ev.static, ev.controls, ev.heldout, ev.blind, bool(ev.owner), bool(ev.independent)
    ran = c.get("ran", False)
    held_ok = h.get("ran") and not h["mismatch"]
    blind_ok = b.get("ran") and not b["mismatch"]
    ctrl_ok = ran and not c["mismatch"]
    has_ref = ran and c["n"] == len(CONTROLS)
    forbidden = bool(c.get("forbidden") or h.get("forbidden"))
    lost = bool(c.get("lost") or h.get("lost") or b.get("lost"))
    components_ok = s["contract_present"] and s["image_pinned"] and s["entrypoint_starts_ledger"] and s["verifier_imports_present"]
    led = ev.ledger
    I = ev.independent  # noqa: E741

    rules: dict[str, tuple[bool, bool, bool, bool, str]] = {
        "AUDIT-03": (forbidden, ctrl_ok and has_ref and o and ev.owner.get("attack_matrix_approved"), True, i and held_ok,
                     "22 declared controls + 12 held-out attacks vs expected per-check credit"),
        "AUDIT-04-B": (forbidden, ctrl_ok, True, i and held_ok and blind_ok, "mutation-catalog exploits contained; held-out replay"),
        "AUDIT-05-B": (False, bool(ev.owner.get("exposure_statement")), o, i, "bounded exposure statement (simulated owner)"),
        "AUDIT-05-S03": (False, ctrl_ok and h.get("ran", False), o, i and held_ok, "dev controls vs frozen held-out confirmation"),
        "TRACE-04-A": (ev.secret_hits > 0, True, o, i and I.get("labels_agree_no_secrets"), "regex secret scan + simulated labels"),
        "ENV-03-S01": (not (ev.order_independent and ev.repeat_identical) if ran else False,
                       ran and led.get("ran", False), o, i and bool(ev.order_independent), "repeat, reversed order, fresh socket service"),
        "CREATE-02": (forbidden, ctrl_ok and has_ref, True, i and held_ok, "cue-only/no-work/claim-success controls"),
        "task-contract.clear-contract": (not s["contract_present"], ctrl_ok, o, i and blind_ok,
                                         "blind solver from public contract earns full credit"),
        "task-contract.discoverable-requirements": (bool(b.get("lost")), b.get("ran", False), True, i and blind_ok,
                                                    "blind solver without optional point keeps 8/9"),
        "task-contract.scoring-authority": (bool(c.get("mismatch")) and ran, ctrl_ok, o, i and blind_ok and held_ok,
                                            "reference/expected results agree with contract-derived answers"),
        "provenance.task-artifact-access": (not (s["contract_present"] and s["dockerfile_copies_contract"]), True, True,
                                            i and I.get("inventory_agrees"), "file inventory vs Dockerfile COPY"),
        "provenance.grading-path": (not s["grader_matches_declared"], True, True, i and I.get("grading_path_agrees"),
                                    "verify.py GRADER vs contract acceptance"),
        "provenance.grading-dependency-access": (not (s["verifier_imports_present"] and s["contract_present"]), True, True,
                                                 i and I.get("dependencies_agree"), "verifier imports + contract input"),
        "provenance.declared-configuration": (not ev.harbor["valid"], s["image_pinned"], True,
                                              i and I.get("manifest_reconstructed"), "Harbor TaskConfig parse + pinned image"),
        "provenance.observed-configuration": (not s["entrypoint_starts_ledger"] or not led.get("ran", False), ran, True,
                                              i and bool(ev.repeat_identical), "declared service vs observed socket run; repeat"),
        "provenance.runtime-route": (not ev.harbor["valid"], True, True, i and ev.harbor["invalid_manifest_rejected"],
                                     "Harbor validation + invalid-manifest control"),
        "provenance.comparison-fairness": (False, bool(ev.owner.get("comparison")), o,
                                           i and blind_ok and held_ok, "matched alternative solutions all credited"),
        "provenance.solver-tool-access": (not s["entrypoint_starts_ledger"] or (led.get("ran") and not led["valid_actions_ok"]),
                                          led.get("ran", False) and led.get("boundary_rejected", False), True,
                                          i and bool(ev.repeat_identical), "socket actions, boundary rejection, fresh resets"),
        "provenance.dependency-substitution": (not components_ok, True, o and bool(ev.owner.get("substitution_approved")), i,
                                               "offline ledger substitution approved (simulated owner)"),
        "provenance.external-state": (bool(c.get("mismatch")) and ran or not s["contract_present"], ran, o,
                                      i and bool(ev.drift_probe_detected), "pinned policy + deliberate drift probe"),
        "provenance.evidence-revision-consistency": (False, True, True, bool(ev.change_injection_invalidates),
                                                     "one-byte injection changes bound task identity"),
    }
    # lost valid credit is a scoring-authority / discoverability defect; for AUDIT-03 it means "unresolved" (anchor 1)
    out = {}
    for row in criteria():
        defect, complete, owner, indep, how = rules[row["id"]]
        if row["id"] == "task-contract.scoring-authority":
            defect = defect or lost
        if row["id"] == "AUDIT-03" and lost and not defect:
            complete = False
        sc = ladder(bool(defect), bool(complete), bool(owner), bool(indep))
        out[row["id"]] = {"score": sc, "anchor": row["scoring"][str(sc)], "evidence": how}
    return out


# --------------------------------------------------------------------------- driver


def run_once(cases: list[str], scratch: Path) -> dict[str, Any]:
    results = {}
    for case in cases:
        work = Path(tempfile.mkdtemp(prefix=f"{case}-", dir=scratch))
        ev = collect(case, work)
        results[case] = {"scores": score(ev), "evidence": asdict(ev)}
    return results


def render(runs: list[dict], out: Path) -> dict:
    ids = [r["id"] for r in criteria()]
    first = runs[0]
    cases = list(first)
    vec = {c: [first[c]["scores"][k]["score"] for k in ids] for c in cases}
    consistent = all(
        [r[c]["scores"][k]["score"] for k in ids] == vec[c] for r in runs for c in cases
    )
    base = vec["base"]
    pref = {
        c: {
            "total": sum(vec[c]),
            "dominated": all(b >= m for b, m in zip(base, vec[c])) and sum(vec[c]) < sum(base),
            "dropped": [k for k, b, m in zip(ids, base, vec[c]) if m < b],
        }
        for c in cases if c != "base"
    }
    summary = {
        "criteria": ids,
        "runs": len(runs),
        "consistent_across_runs": consistent,
        "base_all_threes": all(v == 3 for v in base),
        "base_total": sum(base),
        "every_mutation_lower": all(p["dominated"] for p in pref.values()),
        "scores": vec,
        "preference": pref,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "runs.json").write_text(json.dumps(runs, indent=1, default=str))

    short = {c: c.replace("withhold-", "w/o ").replace("-review", "") for c in cases}
    lines = [
        "# Rubric simulation demo",
        "",
        f"Runs: {len(runs)} · consistent: **{consistent}** · base all 3s: **{summary['base_all_threes']}** "
        f"· every mutation strictly lower: **{summary['every_mutation_lower']}**",
        "",
        "Human and independent reviewers are **SIMULATED** personas. Scorer, socket ledger and Harbor parse are real.",
        "",
        "| Criterion | " + " | ".join(short[c] for c in cases) + " |",
        "|---|" + "---:|" * len(cases),
    ]
    for j, k in enumerate(ids):
        lines.append(f"| {k} | " + " | ".join(str(vec[c][j]) for c in cases) + " |")
    lines.append("| **total (/63)** | " + " | ".join(f"**{sum(vec[c])}**" for c in cases) + " |")
    lines += ["", "## Drops per mutation", ""]
    for c, p in pref.items():
        lines.append(f"- `{c}` ({p['total']}/63): " + ", ".join(f"{k}→{vec[c][ids.index(k)]}" for k in p["dropped"]))
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", nargs="*", help="cases to run (default: base + all mutations)")
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = args.only or ["base", *TASK_MUTATIONS, *EVIDENCE_MUTATIONS]
    if "base" not in cases:
        cases.insert(0, "base")
    with tempfile.TemporaryDirectory(prefix="rubric-sim-") as tmp:
        runs = [run_once(cases, Path(tmp)) for _ in range(args.runs)]
    summary = render(runs, args.output)
    print((args.output / "summary.md").read_text())
    return 0 if summary["base_all_threes"] and summary["every_mutation_lower"] and summary["consistent_across_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
