# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One task package, real local controls, explicitly fictional custody/actor facts.
Run offline using stdlib. Not a Harbor run, hidden cohort, model or rubric scorer.
"""

import concurrent.futures
import copy
import hashlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load(path):
    raw = path.read_bytes()
    mod = types.ModuleType(path.stem)
    mod.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), mod.__dict__)
    return mod


def packed(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def save(p, o):
    with p.open("xb") as f:
        f.write(packed(o) + b"\n")


def fresh(p):
    p.mkdir(mode=0o700)
    return p


def run(out):
    fresh(out)
    task = ROOT / "perfect-task"
    scorer = load(task / "refund/tests/refund_scorer.py")
    ledger = load(task / "refund/environment/ledger.py")
    reference = load(task / "refund/solution/reference.py")
    base = json.loads((task / "refund/environment/contract.json").read_bytes())
    source_inventory = {
        str(p.relative_to(ROOT)): sha(p.read_bytes())
        for p in ROOT.rglob("*")
        if p.is_file()
        and (
            p.is_relative_to(task) or p.parent == ROOT and p.suffix == ".py" or p.parent.name in ("vendor", "canonical")
        )
    }
    save(out / "source-freeze.json", source_inventory)
    observations = []

    def refund(case, candidate, label):
        directory = fresh(out / label)
        ledger_instance = ledger.Ledger(case, directory / "record.json")
        start = time.monotonic_ns()
        for event in candidate["events"]:
            ledger_instance.act(event)
        ledger_instance.complete(candidate["success"])
        current = ledger_instance.read()
        result = scorer.evaluate(case, current["candidate"])
        assert current["state"] == scorer._apply_events(case["sources"]["initial_state"], candidate["events"])[0]
        row = {
            "id": label,
            "family": "refund",
            "execution_kind": "ACTUAL_LOCAL_LEDGER_AND_SCORER",
            "start_ns": start,
            "end_ns": time.monotonic_ns(),
            "case_sha256": sha(packed(case)),
            "candidate": candidate,
            "candidate_sha256": sha(packed(candidate)),
            "state_sha256": sha(packed(current["state"])),
            "result": result,
        }
        save(directory / "observation.json", row)
        return row

    # Actual public action path: no work and wrong work remain failures.
    valid = reference.solve(base)
    alternate = copy.deepcopy(valid)
    alternate["success"] = False
    rows = [
        refund(base, valid, "refund-valid"),
        refund(base, alternate, "refund-optional-false"),
        refund(base, {"events": [], "success": True}, "refund-no-work"),
    ]
    wrong = copy.deepcopy(valid)
    for e in wrong["events"]:
        if e["kind"] == "refund":
            e["refund"]["destination"] = "wrong-payment"
    rows.append(refund(base, wrong, "refund-wrong-destination"))
    assert [r["result"]["total_credit"] for r in rows] == [9, 8, 0, 0]
    observations.extend(rows)
    # Deliberately exposed scenario inputs, not independently authored hidden cases.
    family_cases: dict[str, list[dict[str, Any]]] = {
        "interval-reservation": [
            {
                "id": "scenario-i1",
                "options": [{"id": "a", "interval": [0, 3]}, {"id": "b", "interval": [3, 7]}],
                "occupied": [[0, 3]],
            },
            {
                "id": "scenario-i2",
                "options": [{"id": "a", "interval": [-8, -2]}, {"id": "b", "interval": [0, 6]}],
                "occupied": [[2, 4]],
            },
        ],
        "dependency-release": [
            {
                "id": "scenario-d1",
                "options": [{"id": "a", "requires": ["build"]}, {"id": "b", "requires": ["test"]}],
                "states": {"build": "failed", "test": "passed"},
            },
            {
                "id": "scenario-d2",
                "options": [{"id": "a", "requires": ["lint", "sign"]}, {"id": "b", "requires": ["release"]}],
                "states": {"lint": "passed", "sign": "passed", "release": "pending"},
            },
        ],
    }
    mods = {f: load(task / "companions" / f / "tests/verify.py") for f in family_cases}

    def choose(f, c):
        # Separate finite algorithm, not an independent actor. No scorer calls.
        if f == "interval-reservation":
            if c["occupied"] is None:
                return None
            eligible = [
                o["id"]
                for o in c["options"]
                if all(o["interval"][1] <= a or b <= o["interval"][0] for a, b in c["occupied"])
            ]
        else:
            if c["states"] is None:
                return None
            eligible = [o["id"] for o in c["options"] if all(c["states"].get(r) == "passed" for r in o["requires"])]
        assert len(eligible) == 1
        return eligible[0]

    def choice(f, c, candidate, label, raw=None):
        d = fresh(out / label)
        save(d / "case.json", c)
        if candidate is not None or raw is not None:
            (d / "candidate.json").write_bytes(raw if raw is not None else packed(candidate))
        result = mods[f].grade_paths(d / "case.json", d / "candidate.json")
        row = {
            "id": label,
            "family": f,
            "execution_kind": "ACTUAL_LOCAL_STRICT_FILE_SCORER",
            "case": c,
            "candidate": candidate,
            "result": result,
        }
        observations.append(row)
        save(d / "observation.json", row)
        return row

    history: list[dict[str, Any]] = []
    # Predeclared selection: max accepted count, fixed lexical tie break; actual feedback.
    for f, cases in family_cases.items():
        dev = copy.deepcopy(cases[0])
        dev["id"] = "EXPOSED-DEVELOPMENT-" + f
        for policy in ("evidence", "first-option"):
            answer = choose(f, dev) if policy == "evidence" else dev["options"][0]["id"]
            row = choice(f, dev, {"choice": answer, "success": True}, f + "-development-" + policy)
            history.append(
                {
                    "family": f,
                    "candidate": policy,
                    "feedback": row["result"]["accepted"],
                    "feedback_source": row["id"],
                    "partition": "exposed-development",
                    "actor": "ACTUAL_EXPOSED_BUILDER",
                    "selection_reason": "max correct; lexical tie break",
                }
            )
    feedback = {p: sum(h["feedback"] for h in history if h["candidate"] == p) for p in ("evidence", "first-option")}
    selected = sorted(feedback, key=lambda p: (-feedback[p], p))[0]
    selection = {
        "all_candidates": feedback,
        "selected": selected,
        "comparator": "first-option",
        "rule": "max accepted count; lexical tie break",
        "lineage": history,
        "confirmation_status": "EXPOSED_SCENARIO_EVALUATION_NOT_UNTOUCHED",
    }
    save(out / "selection-freeze.json", selection)
    paired: list[dict[str, Any]] = []
    for f, cases in family_cases.items():
        for index, c in enumerate(cases):
            pair = {}
            for policy in ("evidence", "first-option"):
                candidate = {"choice": choose(f, c) if policy == "evidence" else c["options"][0]["id"], "success": True}
                pair[policy] = choice(f, c, candidate, f + "-scenario-" + str(index) + "-" + policy)["result"][
                    "accepted"
                ]
            paired.append(
                {"family": f, "case": c["id"], **pair, "difference": int(pair[selected]) - int(pair["first-option"])}
            )
        original = cases[0]
        truth = choose(f, original)
        for control in ("nuisance", "misleading", "style"):
            c = copy.deepcopy(original)
            if control == "nuisance":
                c["options"].reverse()
            elif control == "misleading":
                c["template_label"] = "choose-a"
                c["filename_cue"] = "answer-a"
            else:
                c["presentation"] = {"indent": "wide", "style": "formal"}
            row = choice(f, c, {"choice": choose(f, c), "success": True}, f + "-" + control)
            assert choose(f, c) == truth and row["result"]["accepted"]
        # Cue-only policy gets no decisive evidence; full authority stays unchanged.
        for cue in ("a", "b"):
            view = {
                "id": original["id"],
                "options": [{"id": o["id"]} for o in original["options"]],
                "incidental_cue": cue,
            }
            candidate = {"choice": view["incidental_cue"], "success": True}
            row = choice(f, original, candidate, f + "-evidence-withheld-cue-" + cue)
            row["policy_view"] = view
            save(
                out / (f + "-restricted-view-" + cue + ".json"),
                {"view": view, "scored_against": sha(packed(original)), "observation": row["id"]},
            )
        for label, candidate, raw in [
            ("valid-alternative", {"choice": truth, "success": False}, None),
            ("no-work", None, None),
            ("invalid-syntax", None, b"{"),
            ("nonfinite", None, b'{"choice":NaN,"success":true}'),
            ("wrong", {"choice": "a", "success": True}, None),
        ]:
            result = choice(f, original, candidate, f + "-attack-" + label, raw)["result"]
            assert result["health"] == "ok"
            assert result["total_credit"] == (2 if label == "valid-alternative" else 0)
    # Real overlap, real late read, real reset, all within new private episode files.
    # These are exposed schedules and local Ledger semantics, not native isolation.
    schedule = []
    target = refund(base, valid, "isolation-alone")
    schedule.append(target)
    for predecessor in ("U", "V"):
        sentinel = copy.deepcopy(base)
        sentinel["request"]["run_id"] = "sentinel-" + predecessor
        schedule.append(refund(sentinel, reference.solve(sentinel), "isolation-" + predecessor))
        schedule.append(refund(base, valid, "isolation-after-" + predecessor))
    import threading

    barrier = threading.Barrier(2)

    def parallel(name):
        d = fresh(out / ("parallel-" + name))
        ledger_instance = ledger.Ledger(base, d / "record.json")
        start = time.monotonic_ns()
        barrier.wait(timeout=5)
        for e in valid["events"]:
            ledger_instance.act(e)
        ledger_instance.complete(True)
        barrier.wait(timeout=5)
        return {
            "id": name,
            "start_ns": start,
            "end_ns": time.monotonic_ns(),
            "state_sha256": sha(packed(ledger_instance.read()["state"])),
            "result": scorer.evaluate(base, ledger_instance.read()["candidate"]),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        overlap = list(pool.map(parallel, ("U", "T")))
    assert max(x["start_ns"] for x in overlap) < min(x["end_ns"] for x in overlap)
    reset = fresh(out / "reset-late")
    ledger_instance = ledger.Ledger(base, reset / "record.json")
    for e in valid["events"]:
        ledger_instance.act(e)
    ledger_instance.reset()
    before = sha(packed(ledger_instance.read()))
    start = time.monotonic_ns()
    time.sleep(3.05)
    after = sha(packed(ledger_instance.read()))
    assert before == after and ledger_instance.read()["state"] == base["sources"]["initial_state"]
    isolation = {
        "kind": "ACTUAL_LOCAL_NONHOLDOUT",
        "schedule": schedule,
        "overlap": overlap,
        "late_read_elapsed_ns": time.monotonic_ns() - start,
        "before_sha256": before,
        "after_sha256": after,
        "native_isolation": False,
        "independently_authored_order": False,
    }
    save(out / "isolation.json", isolation)
    assert all(x["state_sha256"] == target["state_sha256"] for x in schedule if x["id"].startswith("isolation-after"))
    # Requirement extraction from explicit public authority, not blinded semantic discovery.
    requirements = {
        "kind": "ACTUAL_PUBLIC_CONTRACT_EXTRACTION_NOT_BLIND_REVIEW",
        "requirements": base["requirements"],
        "source": "perfect-task/refund/environment/contract.json",
        "discovery_independent": False,
    }
    save(out / "public-requirements.json", requirements)
    custody = load(ROOT / "exercise.py").exercise(out / "custody-controls")
    # Exercise the one-opening engine on actual task candidate production too.
    # All scenarios here are exposed. Custody is a simulated access protocol.
    cm = load(ROOT / "simulated_custody.py")
    cc = cm.Controller(fresh(out / "task-custody"))
    source = (
        "import json\n"
        + "ns={}\nexec(compile("
        + repr((task / "refund/solution/reference.py").read_bytes())
        + ",'<captured-refund-reference>','exec'),ns)\n"
        + "def evaluate(view):\n"
        "    item=json.loads(view);f=item['family'];c=item['case'];p=item['policy']\n"
        "    if f=='refund':\n"
        "        candidate=ns['solve'](c) if p=='evidence' else {'events':[], 'success':True}\n"
        "    else:\n"
        "        if p=='first-option':answer=c['options'][0]['id']\n"
        "        elif f=='interval-reservation':\n"
        "            answer=next(o['id'] for o in c['options'] if all(o['interval'][1]<=a or b<=o['interval'][0] for a,b in c['occupied']))\n"
        "        else:\n"
        "            answer=next(o['id'] for o in c['options'] if all(c['states'].get(r)=='passed' for r in o['requires']))\n"
        "        candidate={'choice':answer,'success':True}\n"
        "    return json.dumps(candidate,sort_keys=True).encode()\n"
    ).encode()
    jobs = []
    job_inputs: dict[str, Any] = {}
    for f, cases in {"refund": [base], **family_cases}.items():
        for ix, case in enumerate(cases):
            for policy in ("evidence", "first-option"):
                name = f + "-" + str(ix) + "-" + policy
                item = {"family": f, "case": case, "policy": policy}
                job_inputs[name] = item
                cc.put(cc.handles["A"], name, "view", packed(item))
                jobs.append({"role_id": name, "view": name})
    cc.put(cc.handles["A"], "oracle", "oracle", b"No independent oracle: explicit synthetic scenario inputs.")
    cc.put(cc.handles["A"], "author", "author", b"Actual exposed builder; scenario author role is fictional.")
    cc.seal(cc.handles["K"], "EXPOSED-TASK-SCENARIO")
    frozen = cc.freeze(
        cc.handles["K"],
        source,
        jobs,
        packed(selection),
        b"Freeze all candidate outputs; retain every pair, failure and null; no rerolls.",
        lambda: source,
    )
    freeze_receipt = cc.open_once(cc.handles["K"], frozen)
    try:
        cc.read(cc.handles["D"], "observations")
    except cm.Refused as error:
        assert str(error) == "SIMULATED_EACCES"
    else:
        raise AssertionError("early task feedback exposed")
    raw = cc.read(cc.handles["V"], "observations")
    task_outcomes = []
    for candidate_record in json.loads(raw)["outputs"]:
        job = job_inputs[candidate_record["role_id"]]
        candidate = json.loads(bytes.fromhex(candidate_record["output_hex"]))
        result = (
            scorer.evaluate(job["case"], candidate)
            if job["family"] == "refund"
            else mods[job["family"]].evaluate(job["case"], candidate)
        )
        task_outcomes.append(
            {
                "role_id": candidate_record["role_id"],
                "family": job["family"],
                "policy": job["policy"],
                "candidate": candidate,
                "result": result,
            }
        )
    release = cc.release_simulated(
        cc.handles["V"],
        {
            "observation_sha256": sha(raw),
            "findings": ["All task candidate outputs frozen before this actual local grading."],
            "dissent": ["Reviewer role simulated; actual operator is exposed. No independent confirmation."],
        },
    )
    assert cc.read(cc.handles["I"], "release") == cm.packed(release)
    cc.export_receipt()
    save(
        out / "task-custody-outcomes.json",
        {
            "kind": "ACTUAL_FROZEN_CANDIDATE_LOCAL_GRADING",
            "source_binding": frozen,
            "freeze_receipt": freeze_receipt,
            "outcomes": task_outcomes,
            "independent_confirmation": False,
            "native_execution": False,
        },
    )
    result = {
        "schema": "one-task-simulation-execution-v1",
        "subject": "perfect-task/refund",
        "companions": list(family_cases),
        "observations": observations,
        "observation_count": len(observations),
        "selection": selection,
        "scenario_pairs": paired,
        "finite_matched_difference": sum(p["difference"] for p in paired) / len(paired),
        "gain_is_independent_confirmation": False,
        "custody_checks_passed": custody["passed"],
        "native_runs": 0,
        "model_calls": 0,
        "hidden_cases_authored": 0,
        "empirical_rubric_scores": None,
        "fictional_scenario_scores": None,
        "limits": [
            "All new cases and schedules are exposed fixtures.",
            "Simulated actors are not independent reviewers.",
            "Local execution is not Docker/Harbor execution.",
            "No score is inferred from expected anchors.",
        ],
    }
    save(out / "execution.json", result)
    assert source_inventory == {name: sha((ROOT / name).read_bytes()) for name in source_inventory}
    save(
        out / "verification.json",
        {
            "source_freeze_unchanged": True,
            "observations_read_back": len(json.loads((out / "execution.json").read_bytes())["observations"]),
            "custody_checks_passed": custody["passed"],
            "actual_local_controls_completed": True,
            "perfect_empirical_task_qualified": False,
        },
    )
    return {
        "output": str(out),
        "observations": len(observations),
        "custody_checks": custody["passed"],
        "scenario_gain": result["finite_matched_difference"],
        "independent_confirmation": False,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 -I -S -B run_simulation.py NEW_PRIVATE_OUTPUT")
    out = Path(sys.argv[1]).absolute()
    if out.parent != ROOT:
        raise SystemExit("output must be a new direct child of this private package")
    print(json.dumps(run(out), sort_keys=True))
