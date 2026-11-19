# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute finite local controls. No rubric score/expected-score input or output.
Actors/OS chronology belong only to scenario-premises.json, never these measurements.
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT / "prior-package"
TASK = PRIOR / "perfect-task"


def packed(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(x):
    return hashlib.sha256(x).hexdigest()


def save(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("xb") as f:
        f.write(packed(x) + b"\n")


def load(p, name=None):
    m = types.ModuleType(name or p.stem)
    m.__file__ = str(p)
    if name:
        sys.modules[name] = m
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def oracle(c, candidate):
    """Separate specification implementation, no call/import of the task scorer.
    Legal gateway events only. Test expected vectors are task credit, never rubric scores.
    """
    request, src = c["request"], c["sources"]
    initial = src["initial_state"]
    balance = src["order"]["merchandise_cents"] - src["order"]["discount_cents"]
    balance += src["order"]["tax_cents"] if src["policy"]["refund_tax"] else 0
    balance -= sum(r["amount_cents"] for r in initial["refunds"] if r["order_id"] == request["order_id"])
    refunds = []
    rows = []
    approvals = []
    ordering = True
    needs = balance >= src["policy"]["approval_threshold_cents"]
    for event in candidate["events"]:
        if event["kind"] == "approve":
            matches = event["actor"] in src["policy"]["approvers"] and event["amount_cents"] == balance
            matches = matches and all(event[k] == request[k] for k in ("run_id", "customer_id", "order_id"))
            if matches:
                approvals.append(event)
        elif event["kind"] == "refund":
            refunds.append(event["refund"])
            ordering = ordering and (not needs or bool(approvals))
        elif event["kind"] == "ledger":
            rows.extend(event["entries"])
        else:
            raise ValueError("oracle only accepts public gateway vocabulary")

    def matching(key: str) -> bool:
        return all(r[key] == request[key] for r in refunds)

    wanted = sorted((r["id"], r["amount_cents"]) for r in refunds)
    got = sorted((r["refund_id"], r["amount_cents"]) for r in rows)
    historical = {r["id"] for r in initial["refunds"]}
    ids = [r["id"] for r in refunds]
    fresh = (not refunds and balance == 0 and src["policy"]["allow_zero_balance_no_action"]) or (
        bool(refunds) and len(set(ids)) == len(ids) and not historical.intersection(ids) and matching("run_id")
    )
    truth = dict(
        completion_signal=candidate["success"],
        association=matching("customer_id") and matching("order_id"),
        amount=all(r["amount_cents"] > 0 for r in refunds) and sum(r["amount_cents"] for r in refunds) == balance,
        destination=matching("destination"),
        approval_present=not needs or bool(approvals),
        approval_before_effect=ordering,
        ledger=wanted == got,
        protected_state=True,
        fresh_effect=bool(fresh),
    )
    checks = {k: c["scoring"]["weights"][k] if v else 0 for k, v in truth.items()}
    accepted = all(truth[k] for k in c["scoring"]["mandatory"])
    return dict(
        health="ok",
        reason=None,
        checks=checks,
        raw_credit=sum(checks.values()),
        max_credit=sum(c["scoring"]["weights"].values()),
        total_credit=sum(checks.values()) if accepted else 0,
        accepted=accepted,
    )


# Captured fixed policies read task inputs; the comparator is deliberately stale.
POLICY = """import json
BASE = BASE_LITERAL

def solve(c):
    q=c['request'];s=c['sources'];o=s['order'];p=s['policy']
    n=o['merchandise_cents']-o['discount_cents']+(o['tax_cents'] if p['refund_tax'] else 0)-sum(r['amount_cents'] for r in s['initial_state']['refunds'] if r['order_id']==q['order_id'])
    ev=[]
    if n:
        if n>=p['approval_threshold_cents']:ev.append(dict(kind='approve',run_id=q['run_id'],customer_id=q['customer_id'],order_id=q['order_id'],amount_cents=n,actor=p['approvers'][-1]))
        used={r['id'] for r in s['initial_state']['refunds']}; i=0
        while 'policy-'+str(i) in used:i+=1
        rid='policy-'+str(i)
        ev += [dict(kind='refund',refund=dict(id=rid,**q,amount_cents=n)),dict(kind='ledger',entries=[dict(refund_id=rid,amount_cents=n)])]
    return dict(events=ev,success=True)

def evaluate(raw):
    item=json.loads(raw);c=item['case'];f=item['family'];p=item['policy']
    if f=='refund':answer=solve(c if p=='evidence' else BASE)
    else:
        options=c['options']
        if p=='fixed-template':chosen=options[0]['id']
        elif f=='interval-reservation':chosen=next(o['id'] for o in options if all(o['interval'][1]<=a or b<=o['interval'][0] for a,b in c['occupied']))
        else:chosen=next(o['id'] for o in options if all(c['states'].get(r)=='passed' for r in o['requires']))
        answer=dict(choice=chosen,success=True)
    return json.dumps(answer,sort_keys=True).encode()
"""


def validate_route(manifest):
    if type(manifest) is not dict or set(manifest) != {"provider", "grader", "subject", "max_actions"}:
        return "INVALID_MANIFEST"
    if manifest["provider"] != "local-ledger-source-v1":
        return "UNSUPPORTED_ROUTE"
    if manifest["grader"] != "reviewed" or manifest["subject"] != "perfect-task/refund":
        return "UNSUPPORTED_ADAPTER"
    if type(manifest["max_actions"]) is not int or manifest["max_actions"] != 1000:
        return "INVALID_MANIFEST"
    return "COMPATIBLE"


def run(out):
    out.mkdir(mode=0o700)
    ledger = load(TASK / "refund/environment/ledger.py")
    scorer = load(TASK / "refund/tests/refund_scorer.py")
    ref = load(TASK / "refund/solution/reference.py")
    base = json.loads((TASK / "refund/environment/contract.json").read_bytes())
    policy_bytes = POLICY.replace("BASE_LITERAL", repr(base)).encode()
    policy = types.ModuleType("frozen_policy")
    exec(compile(policy_bytes, "<frozen_policy>", "exec"), policy.__dict__)
    (out / "policy-frozen.py").write_bytes(policy_bytes)
    observations = []

    def refund(c, candidate, label, forge=False, inert=False, styled=False):
        d = out / "refund-controls" / label
        d.mkdir(parents=True)
        save(d / "contract.json", c)
        ledger_instance = ledger.Ledger(c, d / "record.json")
        candidate_wire = json.dumps(candidate, sort_keys=True, indent=2) if styled else packed(candidate).decode()
        candidate = ledger.decode(candidate_wire.encode())
        rejections = []
        for e in candidate["events"]:
            before = ledger_instance.read()
            try:
                ledger_instance.act(e)
            except (ValueError, TypeError, KeyError) as error:
                rejections.append({"event": e, "reason": str(error), "unchanged": ledger_instance.read() == before})
        ledger_instance.complete(candidate["success"])
        if inert:
            (d / "inert-copied-answer.json").write_bytes(packed(ref.solve(c)))
        reward = d / "verifier"
        reward.mkdir()
        if forge:
            for name in ("reward.txt", "reward.json", "checks.json"):
                (reward / name).write_text("999")
        # Actual unchanged verifier entry point and scorer, captured-source imports.
        code = "import sys,types;from pathlib import Path;ns=types.ModuleType('refund_scorer');ns.__file__=sys.argv[1];sys.modules['refund_scorer']=ns;exec(compile(Path(sys.argv[1]).read_bytes(),sys.argv[1],'exec'),ns.__dict__);v={'__name__':'__main__','__file__':sys.argv[2]};sys.argv=sys.argv[2:];exec(compile(Path(v['__file__']).read_bytes(),v['__file__'],'exec'),v)"
        cmd = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            code,
            str(TASK / "refund/tests/refund_scorer.py"),
            str(TASK / "refund/tests/verify.py"),
            "--contract",
            str(d / "contract.json"),
            "--record",
            str(d / "record.json"),
            "--output",
            str(reward),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10
        )
        result = json.loads((reward / "checks.json").read_bytes())
        actual_candidate = ledger_instance.read()["candidate"]
        expected = oracle(c, actual_candidate)
        row = {
            "id": label,
            "family": "refund",
            "kind": "ACTUAL_LOCAL_LEDGER_UNCHANGED_VERIFIER",
            "case": c,
            "requested_candidate": candidate,
            "candidate": actual_candidate,
            "candidate_wire": candidate_wire,
            "candidate_consumer": "ledger.decode -> Ledger.act/complete",
            "result": result,
            "oracle": expected,
            "oracle_match": result == expected,
            "rejections": rejections,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "reward": float((reward / "reward.txt").read_text()) if (reward / "reward.txt").exists() else None,
            "forged_reward_removed": not (reward / "reward.json").exists(),
            "record_replay_matches": ledger_instance.read()["state"]
            == scorer._apply_events(c["sources"]["initial_state"], actual_candidate["events"])[0],
        }
        try:
            ledger_instance.complete(False)
        except ValueError as e:
            row["after_close"] = str(e)
        else:
            row["after_close"] = "INCORRECTLY_ALLOWED"
        save(d / "observation.json", row)
        observations.append(row)
        return row

    mods = {
        f: load(TASK / "companions" / f / "tests/verify.py") for f in ("interval-reservation", "dependency-release")
    }

    def choice(f, c, candidate, label, styled=False):
        d = out / "choice-controls" / label
        d.mkdir(parents=True)
        save(d / "case.json", c)
        candidate_wire = json.dumps(candidate, sort_keys=True, indent=2) if styled else packed(candidate).decode()
        (d / "candidate.json").write_text(candidate_wire)
        result = mods[f].grade_paths(d / "case.json", d / "candidate.json")
        row = {
            "id": label,
            "family": f,
            "kind": "ACTUAL_LOCAL_STRICT_FILE_SCORER",
            "case": c,
            "candidate": candidate,
            "candidate_wire": candidate_wire,
            "candidate_consumer": "grade_paths -> JSON decoder",
            "result": result,
        }
        save(d / "observation.json", row)
        observations.append(row)
        return row

    def execute(job, label):
        candidate = json.loads(policy.evaluate(packed(job)))
        return (
            refund(job["case"], candidate, label)
            if job["family"] == "refund"
            else choice(job["family"], job["case"], candidate, label)
        )

    # Fresh component controls only; historical attempts are not bundled or replayed.
    prior_summary = load(PRIOR / "run_simulation.py").run(out / "prior-execution")
    save(out / "prior-execution-summary.json", prior_summary)
    good = ref.solve(base)
    controls = []

    def r(cand, label, c=base, **kw):
        row = refund(c, cand, label, **kw)
        controls.append(row)
        return row

    r(good, "valid")
    optional = copy.deepcopy(good)
    optional["success"] = False
    r(optional, "optional-false")
    r({"events": [], "success": True}, "no-work-forged-success", forge=True)
    r({"events": [], "success": False}, "early-exit-inert-copy", inert=True)
    bad = copy.deepcopy(good)
    for e in bad["events"]:
        if e["kind"] == "refund":
            e["refund"]["destination"] = "wrong"
    r(bad, "wrong-destination")
    bad = copy.deepcopy(good)
    bad["events"] = [e for e in bad["events"] if e["kind"] != "approve"]
    r(bad, "missing-approval")
    bad = copy.deepcopy(good)
    bad["events"] = bad["events"][1:] + bad["events"][:1]
    r(bad, "late-approval")
    bad = copy.deepcopy(good)
    for e in bad["events"]:
        if e["kind"] == "refund":
            e["refund"].update(
                amount_cents=1, customer_id="wrong", order_id="wrong", destination="wrong", run_id="stale"
            )
        if e["kind"] == "approve":
            e["actor"] = "unauthorized"
    r(bad, "all-failure-reachable")
    # A split valid solution differs in manager, ID, amounts and ordering from reference.
    split = copy.deepcopy(good)
    approval = split["events"][0]
    approval["actor"] = "manager-2"
    original = split["events"][1]["refund"]
    a = copy.deepcopy(original)
    b = copy.deepcopy(original)
    a.update(id="split-a", amount_cents=1)
    b.update(id="split-b", amount_cents=original["amount_cents"] - 1)
    entries = [{"refund_id": x["id"], "amount_cents": x["amount_cents"]} for x in (b, a)]
    split["events"] = [
        {"kind": "ledger", "entries": entries},
        approval,
        {"kind": "refund", "refund": a},
        {"kind": "refund", "refund": b},
    ]
    r(split, "split-preledger-alternative")
    for amount in (4999, 5000):
        c = copy.deepcopy(base)
        c["sources"]["order"]["merchandise_cents"] += amount - original["amount_cents"]
        r(ref.solve(c), "threshold-" + str(amount), c)
    zero = copy.deepcopy(base)
    zero["sources"]["order"]["merchandise_cents"] -= original["amount_cents"]
    zero["sources"]["policy"]["allow_zero_balance_no_action"] = True
    r({"events": [], "success": False}, "valid-zero-inaction", zero)
    invalid_events = [
        {"kind": "helper_error"},
        {"kind": "change_protected", "value": {}},
        {"kind": "refund", "refund": dict(original, amount_cents=True)},
        dict(good["events"][0], extra=1),
    ]
    r({"events": invalid_events, "success": False}, "gateway-schema-errors")
    repeated = copy.deepcopy(good)
    repeated["events"] += copy.deepcopy(good["events"][1:])
    r(repeated, "repeated-effect")
    # Drift means old candidates can lose credit; newly correct ones retain it.
    changed = copy.deepcopy(base)
    changed["sources"]["policy"]["refund_tax"] = False
    old = r(good, "drift-old-tax-answer", changed)
    new = r(ref.solve(changed), "drift-new-tax-answer", changed)
    repeat = r(copy.deepcopy(good), "independent-role-repeat")
    # Explicit permuted predecessor schedules, retained state/grade equality.
    permutations = []
    for order in (("U", "V"), ("V", "U")):
        for predecessor in order:
            case = copy.deepcopy(base)
            case["request"]["run_id"] = "permuted-" + predecessor
            refund(case, ref.solve(case), "order-" + "".join(order) + "-" + predecessor)
        target = refund(base, good, "order-" + "".join(order) + "-T")
        permutations.append(
            {
                "predecessors": list(order),
                "target": target["id"],
                "same_result": target["result"] == controls[0]["result"],
                "same_state": target["record_replay_matches"],
            }
        )
    save(out / "permuted-order.json", permutations)
    # Actual malformed private input: verifier operational error, not resistance proof.
    broken = out / "operational-error"
    broken.mkdir()
    (broken / "contract.json").write_bytes(packed(base))
    (broken / "candidate.json").write_text("{")
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(TASK / "refund/tests/verify.py"),
            "--contract",
            str(broken / "contract.json"),
            "--submission",
            str(broken / "candidate.json"),
            "--output",
            str(broken / "result"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.defpath},
    )
    operational = {
        "exit_code": proc.returncode,
        "result": json.loads((broken / "result/checks.json").read_bytes()),
        "reward_exists": (broken / "result/reward.txt").exists(),
    }
    save(broken / "receipt.json", operational)

    # Dev uses distinct content. Confirmation not re-labelled development reuse.
    dev_refund = copy.deepcopy(base)
    dev_refund["sources"]["order"]["tax_cents"] = 450
    development = [
        {"family": "refund", "case": dev_refund},
        {
            "family": "interval-reservation",
            "case": {
                "id": "dev-i",
                "occupied": [[0, 10]],
                "options": [{"id": "a", "interval": [2, 4]}, {"id": "b", "interval": [10, 12]}],
            },
        },
        {
            "family": "dependency-release",
            "case": {
                "id": "dev-d",
                "states": {"lint": "failed", "build": "passed"},
                "options": [{"id": "a", "requires": ["lint"]}, {"id": "b", "requires": ["build"]}],
            },
        },
    ]
    confirmation: list[dict[str, Any]] = []
    for ix, (tax, threshold) in enumerate(((325, 5000), (700, 10000))):
        c = copy.deepcopy(base)
        c["sources"]["order"]["tax_cents"] = tax
        c["sources"]["policy"]["approval_threshold_cents"] = threshold
        c["request"]["run_id"] = "confirmation-" + str(ix)
        confirmation.append({"family": "refund", "case": c})
    confirmation += [
        {
            "family": "interval-reservation",
            "case": {
                "id": "confirm-i0",
                "occupied": [[-10, -4]],
                "options": [{"id": "a", "interval": [-7, -5]}, {"id": "b", "interval": [-4, 0]}],
            },
        },
        {
            "family": "interval-reservation",
            "case": {
                "id": "confirm-i1",
                "occupied": [[21, 27]],
                "options": [{"id": "a", "interval": [15, 20]}, {"id": "b", "interval": [23, 24]}],
            },
        },
        {
            "family": "dependency-release",
            "case": {
                "id": "confirm-d0",
                "states": {"audit": "pending", "sign": "passed"},
                "options": [{"id": "a", "requires": ["audit"]}, {"id": "b", "requires": ["sign"]}],
            },
        },
        {
            "family": "dependency-release",
            "case": {
                "id": "confirm-d1",
                "states": {"package": "passed", "scan": "passed", "ship": "pending"},
                "options": [{"id": "a", "requires": ["package", "scan"]}, {"id": "b", "requires": ["ship"]}],
            },
        },
    ]
    lineage: list[dict[str, Any]] = []
    for i, item in enumerate(development):
        for p in ("evidence", "fixed-template"):
            row = execute(dict(item, policy=p), "development-" + str(i) + "-" + p)
            lineage.append(
                {
                    "candidate": p,
                    "family": item["family"],
                    "case_sha256": sha(packed(item["case"])),
                    "feedback": row["result"]["accepted"],
                    "observation": row["id"],
                    "revealed": "all per-check credit",
                    "partition": "development",
                    "actor": "actual exposed builder; D only in supplied fiction",
                    "modification": "none; finite candidate roster frozen before feedback",
                    "selection_reason": "maximum accepted development cases; lexical tie break",
                }
            )
    totals = {
        p: sum(x["feedback"] is True for x in lineage if x["candidate"] == p) for p in ("evidence", "fixed-template")
    }
    selected = sorted(totals, key=lambda p: (-totals[p], p))[0]
    analysis = {
        "rule": "paired accepted correctness selected minus fixed-template on all six cases; positive required; no reroll",
        "exposure_sensitivity": "exclude first case of each family; report remaining difference even if null/negative",
        "population": "finite six-case cohort only; no sampling CI",
        "selected": selected,
        "comparator": "fixed-template",
        "lineage": lineage,
        "development_totals": totals,
    }
    save(out / "selection-and-analysis-freeze.json", analysis)
    save(out / "confirmation-cases.json", confirmation)
    (out / "confirmation-custody").mkdir(mode=0o700)
    cm = load(PRIOR / "simulated_custody.py")
    cc = cm.Controller(out / "confirmation-custody")
    jobs = []
    inputs = {}
    for i, item in enumerate(confirmation):
        for p in ("evidence", "fixed-template"):
            name = str(i) + "-" + p
            job = dict(item, policy=p)
            inputs[name] = job
            cc.put(cc.handles["A"], name, "view", packed(job))
            jobs.append({"role_id": name, "view": name})
    cc.put(
        cc.handles["A"],
        "oracle",
        "oracle",
        b"Public specification oracle; independent authorship is only a supplied fictional premise.",
    )
    cc.put(cc.handles["A"], "author", "author", b"Actual exposed builder; A is fictional.")
    cc.seal(cc.handles["K"], "FICTIONAL-CHRONOLOGY-ONLY")
    frozen = cc.freeze(
        cc.handles["K"], policy_bytes, jobs, packed(analysis), packed(analysis["rule"]), lambda: policy_bytes
    )
    cc.open_once(cc.handles["K"], frozen)
    raw = cc.read(cc.handles["V"], "observations")
    frozen_outputs = json.loads(raw)["outputs"]
    outcomes: list[dict[str, Any]] = []
    for record in frozen_outputs:
        name = record["role_id"]
        job = inputs[name]
        candidate = json.loads(bytes.fromhex(record["output_hex"]))
        row = (
            refund(job["case"], candidate, "confirmation-" + name)
            if job["family"] == "refund"
            else choice(job["family"], job["case"], candidate, "confirmation-" + name)
        )
        outcomes.append(
            {
                "case_index": int(name.split("-")[0]),
                "policy": job["policy"],
                "family": job["family"],
                "observation": row["id"],
                "result": row["result"],
            }
        )
    cc.release_simulated(
        cc.handles["V"],
        {
            "observation_sha256": sha(raw),
            "findings": ["All candidate outputs frozen before any scorer result."],
            "dissent": ["Actual builder exposed; no real independent confirmation."],
        },
    )
    cc.export_receipt()
    save(out / "confirmation-outcomes.json", outcomes)
    pairs: list[dict[str, Any]] = []
    for i, item in enumerate(confirmation):
        arms = {x["policy"]: x for x in outcomes if x["case_index"] == i}
        a, b = arms[selected]["result"]["accepted"], arms["fixed-template"]["result"]["accepted"]
        difference = int(a) - int(b) if type(a) is bool and type(b) is bool else None
        pairs.append({"index": i, "family": item["family"], "arms": arms, "difference": difference})
    kept = [p for p in pairs if p["index"] % 2 == 1]

    def mean(rows: list[dict[str, Any]]) -> float | None:
        return (
            sum(p["difference"] for p in rows) / len(rows) if all(p["difference"] is not None for p in rows) else None
        )

    gain = {
        "pairs": pairs,
        "difference": mean(pairs),
        "n": len(pairs),
        "sensitivity_difference": mean(kept),
        "sensitivity_n": len(kept),
        "dev_confirmation_overlap": sorted(
            {x["case_sha256"] for x in lineage} & {sha(packed(x["case"])) for x in confirmation}
        ),
        "independence": "SUPPLIED FICTIONAL PREMISE ONLY",
        "actual_unexposed": False,
    }
    save(out / "gain.json", gain)

    # Four paired cue classes, all families, both policies see same public view.
    cue_pairs = []
    for i, item in enumerate([{"family": "refund", "case": base}] + confirmation):
        f = item["family"]
        c = item["case"]
        for mode in ("cue-only", "nuisance", "misleading", "style"):
            transformed = copy.deepcopy(c)
            if mode == "nuisance" and f != "refund":
                transformed["options"].reverse()
            if mode == "nuisance" and f == "refund":
                transformed["sources"]["policy"]["approvers"].reverse()
            outputs: dict[str, Any] = {}
            for arm in ("baseline", "intervention"):
                body = c if arm == "baseline" else transformed
                # Explicit view annotations do reach policy input. Policy ignores incidental metadata by design.
                view = dict(
                    family=f,
                    case=body,
                    policy="evidence",
                    filename="case.json",
                    template_label="generic",
                    style="plain",
                )
                if arm == "intervention" and mode == "misleading":
                    view.update(filename="answer-A.json", template_label="choose-A")
                input_wire = (
                    json.dumps(view, sort_keys=True, indent=2)
                    if mode == "style" and arm == "intervention"
                    else packed(view).decode()
                )
                if mode == "cue-only":
                    # No task work is inferred from a final copied file. For action task use stale actions.
                    view = {
                        "family": f,
                        "incidental_cue": arm,
                        "options": [] if f == "refund" else [o["id"] for o in c["options"]],
                    }
                    if f == "refund":
                        candidate = copy.deepcopy(good)
                        if view["incidental_cue"] == "intervention":
                            for event in candidate["events"]:
                                if event["kind"] == "refund":
                                    event["refund"]["amount_cents"] += 1
                    else:
                        candidate = {
                            "choice": view["options"][0 if view["incidental_cue"] == "baseline" else -1],
                            "success": True,
                        }
                else:
                    candidate = json.loads(policy.evaluate(input_wire.encode()))
                label = "cue-" + str(i) + "-" + mode + "-" + arm
                styled = mode == "style" and arm == "intervention"
                row = (
                    refund(body, candidate, label, styled=styled)
                    if f == "refund"
                    else choice(f, body, candidate, label, styled=styled)
                )
                outputs[arm] = {
                    "view": view,
                    "policy_input_wire": input_wire if mode != "cue-only" else None,
                    "candidate_wire": row["candidate_wire"],
                    "candidate_consumer": row["candidate_consumer"],
                    "case_sha256": sha(packed(body)),
                    "candidate_sha256": sha(packed(candidate)),
                    "observation": label,
                    "result": row["result"],
                }
            cue_pairs.append(
                {
                    "family": f,
                    "case_index": i,
                    "class": mode,
                    "arms": outputs,
                    "transition": [outputs[a]["result"]["accepted"] for a in ("baseline", "intervention")],
                    "requirements_unchanged": c["requirements"] == transformed["requirements"]
                    if f == "refund"
                    else True,
                }
            )
    save(out / "cue-pairs.json", cue_pairs)
    # Matched setup sensitivity: same full evidence/config, reversed cohort order.
    fairness = []
    for index, item in reversed(list(enumerate(confirmation))):
        for p in ("evidence", "fixed-template"):
            row = execute(dict(item, policy=p), "fairness-reverse-" + str(index) + "-" + p)
            original = next(x for x in outcomes if x["case_index"] == index and x["policy"] == p)
            fairness.append(
                {
                    "index": index,
                    "policy": p,
                    "observation": row["id"],
                    "same_result": row["result"] == original["result"],
                }
            )
    save(out / "fairness.json", fairness)
    manifest = {
        "provider": "local-ledger-source-v1",
        "grader": "reviewed",
        "subject": "perfect-task/refund",
        "max_actions": 1000,
    }
    invalid = [
        dict(manifest, provider="Harbor"),
        dict(manifest, grader="baseline"),
        dict(manifest, max_actions=True),
        dict(manifest, max_actions="1000"),
    ]
    runtime = {
        "declared": manifest,
        "actual_interpreter": sys.executable,
        "version": sys.version,
        "flags": {
            "isolated": sys.flags.isolated,
            "no_site": sys.flags.no_site,
            "dont_write_bytecode": sys.flags.dont_write_bytecode,
        },
        "validation": [{"input": m, "status": validate_route(m)} for m in [manifest, *invalid]],
        "models": None,
        "prompts": None,
        "sampling": None,
        "unknown_provider_internals": "INAPPLICABLE: no external provider",
        "policies": {"source_sha256": sha(policy_bytes), "names": ["evidence", "fixed-template"]},
        "grading_route": "refund Ledger -> unchanged verify.py --record -> evaluate(reviewed); companions grade_paths",
        "permissions": "Supplied fictional OS only; actual direct private-file operator access unrestricted",
    }
    save(out / "runtime.json", runtime)
    # Public condition spans are bound to exact current bytes, not inferred inaccessible authority.
    text = (TASK / "refund/instruction.md").read_text().splitlines()
    ranges = {
        "completion_signal": (56, 59),
        "association": (41, 44),
        "amount": (41, 44),
        "destination": (46, 50),
        "approval_present": (41, 44),
        "approval_before_effect": (46, 52),
        "ledger": (46, 50),
        "protected_state": (51, 54),
        "fresh_effect": (46, 57),
    }
    crosswalk = {
        k: {
            "contract_pointer": "/requirements/" + k,
            "requirement": base["requirements"][k],
            "instruction_path": "prior-package/perfect-task/refund/instruction.md",
            "lines": [a, b],
            "quoted": "\n".join(text[a - 1 : b]),
            "mandatory": k in base["scoring"]["mandatory"],
            "weight": base["scoring"]["weights"][k],
            "public_access": "SUPPLIED FICTIONAL solver-read ACL; actual file readable",
        }
        for k, (a, b) in ranges.items()
    }
    save(out / "condition-crosswalk.json", crosswalk)
    # Per-source roles/access/import origins. Full Python interpreter and imported stdlib sources pinned.
    inventory = {}
    for p in sorted(TASK.rglob("*")):
        if p.is_file():
            inventory[str(p.relative_to(ROOT))] = {
                "sha256": sha(p.read_bytes()),
                "bytes": p.stat().st_size,
                "role": "reference-control"
                if "solution" in p.parts
                else "scoring"
                if "tests" in p.parts
                else "task-or-runtime",
                "actual_reviewer_read": "READ",
                "solver_access": "FICTIONAL_DENIED" if "solution" in p.parts else "FICTIONAL_PUBLIC_OR_GATEWAY",
            }
    imports = {}
    for name, m in list(sys.modules.items()):
        location = getattr(m, "__file__", None)
        if location and Path(location).is_file():
            imports[name] = {"path": location, "sha256": sha(Path(location).read_bytes())}
    save(
        out / "inventory.json",
        {
            "task_files": inventory,
            "runtime_import_origins": imports,
            "interpreter_sha256": sha(Path(sys.executable).read_bytes()),
            "external_dependencies": [],
            "omissions": ["Native image is inventoried by task declaration, not executed or claimed compatible."],
        },
    )
    save(out / "observations.json", observations)
    summary = {
        "actual_observations": len(observations),
        "refund_observations": sum(x["family"] == "refund" for x in observations),
        "oracle_mismatches": [x["id"] for x in observations if x["family"] == "refund" and not x["oracle_match"]],
        "unhealthy_observations": [x["id"] for x in observations if x["result"]["health"] != "ok"],
        "operational_error": operational,
        "gain": gain["difference"],
        "sensitivity_gain": gain["sensitivity_difference"],
        "fairness_equal": all(x["same_result"] for x in fairness),
        "repeat_equal": repeat["result"] == controls[0]["result"],
        "drift_old_accepted": old["result"]["accepted"],
        "drift_new_accepted": new["result"]["accepted"],
        "crosswalk_complete": set(crosswalk) == set(scorer.CHECKS),
        "frozen_candidates": len(frozen_outputs),
        "native_runs": 0,
        "model_calls": 0,
        "real_independent_actors": 0,
    }
    save(out / "collection-summary.json", summary)
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: collect.py NEW_PRIVATE_OUTPUT")
    target = Path(sys.argv[1]).resolve()
    if target.parent != ROOT:
        raise SystemExit("output must be a new direct child of private package")
    print(json.dumps(run(target), sort_keys=True))
