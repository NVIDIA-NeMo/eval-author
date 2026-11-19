# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rubric simulation demo: one refund task scored 3 on every applicable rubric row,
then mutated to show each mutation scores consistently lower.

Executable evidence is real: the bundled refund scorer, socket ledger and Harbor
task parser run locally. Independent review comes from restricted-input `claude -p`
agents (see agents.py), cached in agent_cache/. Owner decisions come from
policy.json, a SIMULATED owner's one-time declaration. Each rubric row is scored
by exactly one tool in checks.py. This is a demonstration, not qualification.
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
sys.path.insert(0, str(HERE))
import agents  # noqa: E402
import example_fixtures as fx  # noqa: E402
from checks import CHECKS, NA, PARTITION, U, rank  # noqa: E402

EXPECTED = json.loads((fx.EXAMPLE / "expectations/controls.json").read_text())
CONTROLS = json.loads((fx.EXAMPLE / "controls.json").read_text())["cases"]
POLICY = json.loads((HERE / "policy.json").read_text())
HELDOUT_BYTES = (HERE / "heldout_attacks.json").read_bytes()
FREEZE = json.loads((HERE / "heldout.freeze.json").read_text())

RECIPE_MUTATIONS = [
    "completion-only",
    "approval-after-effect",
    "ledger-omission",
    "hidden-completion",
    "tax-drift",
    "missing-contract",
    "disabled-ledger",
]
DEMO_MUTATIONS = [
    "trace-secret",
    "answer-exposure",
    "unpinned-image",
    "invalid-manifest",
    "contradictory-contract",
    "contradictory-contract-reworded",
    "ambiguous-contract",
    "style-advantage",
    "heldout-tuned",
    "stale-evidence",
]
EVIDENCE_MUTATIONS = ["withhold-reference", "withhold-heldout", "no-independent-review", "no-owner-review"]
ALL_CASES = ["base", *RECIPE_MUTATIONS, *DEMO_MUTATIONS, *EVIDENCE_MUTATIONS]
FAKE_KEY = "AKIA" + "Q" * 16  # synthetic credential shape for the trace-secret mutation

# Intended change and the row it targets, for the summary table. Expectations are presentation only;
# scores always come from the checks in checks.py.
MUTATIONS: dict[str, tuple[str, str, str]] = {
    "completion-only": ("task", "verify.py selects the completion-only grader", "AUDIT-03"),
    "approval-after-effect": ("task", "scorer accepts approval after the refund", "AUDIT-03"),
    "ledger-omission": ("task", "scorer stops reconciling the ledger", "AUDIT-03"),
    "hidden-completion": (
        "task",
        "scorer makes the optional completion point mandatory",
        "task-contract.discoverable-requirements",
    ),
    "tax-drift": ("task", "public tax policy changes after expectations were authored", "provenance.external-state"),
    "missing-contract": ("task", "contract.json removed from the task", "provenance.task-artifact-access"),
    "disabled-ledger": (
        "task",
        "image entrypoint sleeps instead of starting the ledger",
        "provenance.observed-configuration",
    ),
    "trace-secret": ("task", "a fake AWS key appears in an agent trace", "TRACE-04-A"),
    "answer-exposure": ("task", "instruction states the answer", "AUDIT-05-B"),
    "unpinned-image": ("task", "base image no longer pinned by digest", "provenance.declared-configuration"),
    "invalid-manifest": ("task", "task.toml carries an invalid network mode", "provenance.runtime-route"),
    "contradictory-contract": (
        "task",
        "amendment forbids any refund while refunds stay mandatory",
        "task-contract.clear-contract",
    ),
    "contradictory-contract-reworded": (
        "task",
        "the same contradiction in different words",
        "task-contract.clear-contract",
    ),
    "ambiguous-contract": (
        "task",
        "amendment leaves optional vs mandatory completion unresolved",
        "task-contract.clear-contract",
    ),
    "style-advantage": (
        "task",
        "scorer gives the completion point only when the ledger action comes last",
        "provenance.comparison-fairness",
    ),
    "heldout-tuned": ("evidence", "held-out set edited after results, still presented as frozen", "AUDIT-05-S03"),
    "stale-evidence": (
        "evidence",
        "base-revision results reused on a changed scorer",
        "provenance.evidence-revision-consistency",
    ),
    "withhold-reference": ("evidence", "reference control result removed", "task-contract.scoring-authority"),
    "withhold-heldout": ("evidence", "independently authored held-out cases not run", "AUDIT-03"),
    "no-independent-review": ("review", "restricted-input agents disabled", "AUDIT-03"),
    "no-owner-review": ("review", "owner policy file absent", "AUDIT-03"),
}
# Only the contract-reviewer agent reads the contract text for contradictions or ambiguity.
NEEDS_AGENTS = {"contradictory-contract", "contradictory-contract-reworded", "ambiguous-contract"}
CONTRADICTION = (
    "\nBinding amendment: Do not create any refund effect. Full credit requires zero refund effects. "
    "This amendment and all prior mandatory requirements apply simultaneously.\n"
)
CONTRADICTION_REWORDED = (
    "\nPolicy update for this run: leave the ledger exactly as you found it. Any refund action forfeits all "
    "credit. Every earlier rule in this document still applies in full.\n"
)
AMBIGUITY = (
    "\nUnresolved policy amendment: the completion signal may be a mandatory acceptance gate rather than "
    "optional credit. The two interpretations remain open; neither takes precedence until the owner resolves "
    "this amendment. Other refund requirements remain binding.\n"
)
SCORER_FAIR = '"completion_signal": candidate["success"] is True,'
SCORER_ADVANTAGED = (
    '"completion_signal": candidate["success"] is True and bool(events) and events[-1]["kind"] == "ledger",'
)

SECRET = re.compile(
    r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{30,}|xox[bp]-[A-Za-z0-9-]+|sk-[A-Za-z0-9]{20,})"
)
STDLIB = set(sys.stdlib_module_names)


# --------------------------------------------------------------------------- scorer execution


def _verify(files: dict, contract: dict, candidate: dict, work: Path) -> dict:
    work.mkdir(parents=True)
    trusted = work / "trusted"
    trusted.mkdir()
    for name in ("verify.py", "refund_scorer.py"):
        (trusted / name).write_bytes(files[f"tests/{name}"][0])
    (work / "contract.json").write_text(json.dumps(contract))
    (work / "submission.json").write_text(json.dumps(candidate))
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(trusted / "verify.py"),
            "--contract",
            str(work / "contract.json"),
            "--submission",
            str(work / "submission.json"),
            "--output",
            str(work / "logs"),
        ],
        capture_output=True,
        timeout=10,
        cwd=trusted,
        env={"PATH": os.defpath, "HOME": str(work), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return json.loads((work / "logs/checks.json").read_text())


def _compare(obs: dict, exp: dict) -> dict:
    exp_total, obs_total = exp.get("total_credit") or 0, obs.get("total_credit") or 0
    mismatch_total = obs.get("total_credit") != exp.get("total_credit") or obs.get("accepted") != exp.get("accepted")
    full = "checks" in exp
    return {
        "observed": obs,
        "mismatch_total": mismatch_total,
        "mismatch": mismatch_total
        or (full and (obs.get("checks") != exp["checks"] or obs.get("health") != exp["health"])),
        "forbidden": exp_total == 0 and obs_total > 0 and obs.get("health") == "ok",
        "lost": exp_total > 0 and obs_total < exp_total,
    }


def _panel(files, contract, cases: list[dict], tmp: Path, *, order=None, parallel: bool | None = None) -> dict:
    # Cases are independent subprocesses; run them concurrently unless the order is the point of the panel.
    parallel = order is None if parallel is None else parallel

    def one(i: int) -> tuple[str, dict]:
        row = cases[i]
        c = copy.deepcopy(contract)
        fx._merge(c, row.get("contract_variant", {}))
        obs = _verify(files, c, row["candidate"], tmp / f"{i:02d}-{row['id']}")
        res = _compare(obs, row.get("expect") or EXPECTED[row["id"]])
        res["class"] = row.get("class")
        return row["id"], res

    idx = order if order is not None else list(range(len(cases)))
    if parallel:
        with ThreadPoolExecutor(8) as pool:
            return dict(pool.map(one, idx))
    return dict(one(i) for i in idx)


def _strip(panel: dict) -> dict:
    return {k: v["observed"] for k, v in panel.items()}


def oracle_total(contract: dict) -> int:
    src, req = contract["sources"], contract["request"]
    order, policy = src["order"], src["policy"]
    total = order["merchandise_cents"] - order["discount_cents"]
    total += order["tax_cents"] if policy["refund_tax"] else 0
    total += order["shipping_cents"] if policy["refund_shipping"] else 0
    return total - sum(r["amount_cents"] for r in src["initial_state"]["refunds"] if r["order_id"] == req["order_id"])


def _ref_events() -> list[dict]:
    return next(r for r in CONTROLS if r["id"] == "reference")["candidate"]["events"]


def matched_setups() -> list[dict]:
    """Equivalent correct solutions in different setups; all must earn identical credit."""
    ev = _ref_events()
    approve, refund, ledger = ev
    alt_mgr = {**approve, "actor": "manager-2"}
    half = refund["refund"]["amount_cents"] // 2
    split = [
        approve,
        {"kind": "refund", "refund": {**refund["refund"], "id": "m-1", "amount_cents": half}},
        {
            "kind": "refund",
            "refund": {**refund["refund"], "id": "m-2", "amount_cents": refund["refund"]["amount_cents"] - half},
        },
        {
            "kind": "ledger",
            "entries": [
                {"refund_id": "m-2", "amount_cents": refund["refund"]["amount_cents"] - half},
                {"refund_id": "m-1", "amount_cents": half},
            ],
        },
    ]
    return [
        {
            "id": f"matched-{n}",
            "candidate": {"success": True, "events": e},
            "expect": {"total_credit": 9, "accepted": True},
        }
        for n, e in [
            ("as-written", ev),
            ("ledger-first", [approve, ledger, refund]),
            ("alternate-approver", [alt_mgr, refund, ledger]),
            ("split-batched", split),
        ]
    ]


CUE_ONLY = [
    {
        "id": "cue-success-only",
        "candidate": {"success": True, "events": []},
        "expect": {"total_credit": 0, "accepted": False},
    },
    {
        "id": "cue-success-read-only",
        "candidate": {"success": True, "events": [], "note": "claims refund done"},
        "expect": {"total_credit": 0, "accepted": False},
    },
]


def load_heldout(raw: bytes) -> list[dict]:
    cases = json.loads(raw)["cases"]
    for c in cases:
        if not c["candidate"].get("events"):
            c["class"] = "cue_only"  # structural rule: no actions means only a success cue
    return cases


# --------------------------------------------------------------------------- static inspection


def _toml(path: Path) -> tuple[bool, dict]:
    import tomllib

    try:
        return True, tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return False, {}


def _harbor(task_toml: Path) -> dict:
    code = "import sys;from harbor.models.task.config import TaskConfig as T;T.model_validate_toml(open(sys.argv[1]).read())"
    py = sys.executable
    if subprocess.run([py, "-c", "import harbor.models.task.config"], capture_output=True).returncode != 0:
        return {"valid": None, "invalid_manifest_rejected": None}
    valid = subprocess.run([py, "-c", code, str(task_toml)], capture_output=True).returncode == 0
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "task.toml"
        bad.write_text(task_toml.read_text().replace("cpus = 1", 'cpus = "one"', 1))
        rejected = subprocess.run([py, "-c", code, str(bad)], capture_output=True).returncode != 0
    return {"valid": valid, "invalid_manifest_rejected": rejected}


def inspect(task: Path, files: dict) -> dict:
    docker = (task / "environment/Dockerfile").read_text()
    verify_src = files["tests/verify.py"][0].decode()
    scorer_src = files["tests/refund_scorer.py"][0].decode()
    contract_path = task / "environment/contract.json"
    contract = json.loads(contract_path.read_text()) if contract_path.is_file() else None
    copies = [s for line in re.findall(r"^COPY (.+)$", docker, re.M) for s in line.split()[:-1]]
    missing = [s for s in copies if not (task / "environment" / s).is_file()]
    local = {p.stem for p in (task / "tests").glob("*.py")}
    imports = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", verify_src, re.M))
    env_imports = set()
    for p in (task / "environment").glob("*.py"):
        env_imports |= set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", p.read_text(), re.M))
    env_local = {p.stem for p in (task / "environment").glob("*.py")}
    ok, toml = _toml(task / "task.toml")
    grader = re.search(r'^GRADER = "(\w+)"', verify_src, re.M)
    return {
        "contract": contract,
        "contract_present": contract is not None,
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest() if contract else None,
        "copy_sources": copies,
        "copy_sources_exist": not missing and "contract.json" in copies,
        "missing_copy_sources": missing,
        "grader": grader.group(1) if grader else None,
        "verifier_local_imports": sorted(imports & local),
        "verifier_imports_resolve": not (imports - STDLIB - local),
        "image_pinned": bool(re.search(r"^FROM \S+@sha256:[0-9a-f]{64}", docker, re.M)),
        "entrypoint_service": 'ENTRYPOINT ["python", "-B", "/app/server.py"]' in docker,
        "toml_parses": ok,
        "toml_fields_complete": all(
            toml.get(s, {}).get("network_mode") and (s == "environment" or toml[s].get("timeout_sec"))
            for s in ("environment", "agent", "verifier")
        ),
        "network_modes": {s: toml.get(s, {}).get("network_mode") for s in ("environment", "agent", "verifier")},
        "components_present": all((task / "environment" / n).is_file() for n in ("ledger.py", "server.py")),
        "env_non_stdlib": sorted(env_imports - STDLIB - env_local),
        "scorer_nondeterminism": sorted(
            {
                m
                for m in ("time", "random", "socket", "urllib", "requests", "datetime")
                if re.search(rf"^\s*(?:import|from)\s+{m}\b", scorer_src, re.M)
            }
        ),
    }


def contract_lint(contract: dict, instruction: str) -> list[str]:
    issues = []
    sc = contract["scoring"]
    for key in sc["weights"]:
        if key not in contract["requirements"]:
            issues.append(f"weight {key} has no requirement text")
    if not set(sc["mandatory"]) <= set(sc["weights"]):
        issues.append("mandatory check without weight")
    for kind in ("approve", "refund", "ledger"):
        if f'"kind": "{kind}"' not in instruction:
            issues.append(f"instruction does not define {kind} events")
    return issues


# --------------------------------------------------------------------------- collection


def _apply_demo_mutation(case: str, task: Path, files: dict | None = None) -> None:
    appended = {
        "contradictory-contract": CONTRADICTION,
        "contradictory-contract-reworded": CONTRADICTION_REWORDED,
        "ambiguous-contract": AMBIGUITY,
    }
    if case in appended:
        p = task / "instruction.md"
        p.write_text(p.read_text() + appended[case])
    elif case == "style-advantage":
        p = task / "tests/refund_scorer.py"
        raw = p.read_text()
        assert raw.count(SCORER_FAIR) == 1, "scorer completion rule moved"
        p.write_text(raw.replace(SCORER_FAIR, SCORER_ADVANTAGED))
        if files is not None:
            files["tests/refund_scorer.py"] = (p.read_bytes(), files["tests/refund_scorer.py"][1])
    elif case == "answer-exposure":
        p = task / "instruction.md"
        p.write_text(p.read_text() + "\nFor reference, the correct total refund is 8000 cents.\n")
    elif case == "unpinned-image":
        p = task / "environment/Dockerfile"
        p.write_text(re.sub(r"^FROM \S+", "FROM python:3.13-slim", p.read_text(), count=1, flags=re.M))
    elif case == "invalid-manifest":
        p = task / "task.toml"
        p.write_text(p.read_text().replace('network_mode = "no-network"', 'network_mode = "sometimes"', 1))


def collect(case: str, work: Path, *, use_agents: bool, offline: bool) -> dict:
    recipe = case if case in RECIPE_MUTATIONS else "approval-after-effect" if case == "stale-evidence" else "base"
    selected = work / "selected"
    manifest = fx.materialize(selected, recipe)
    task = selected / "assessment/task"
    files, _ = fx._selected(recipe)

    # Ledger probes run on the materialized recipe before demo-level byte edits; they never build the image.
    try:
        led = fx.run_ledger_controls(selected, work / "ledger")["cases"]
        valid = ["reference", "split-alternative", "below-approval-threshold", "missing-completion"]
        rejects = [
            "helper-error",
            "malformed-events",
            "ordering-evidence-unavailable",
            "protected-mutation",
            "historical-mutation",
        ]
        ledger = {
            "ran": True,
            "valid_actions_ok": all(
                led[k]["status"] == "completed" and led[k]["action_errors"] == 0 and led[k]["result"]["health"] == "ok"
                for k in valid
            ),
            "boundary_rejected": all(led[k]["action_errors"] >= 1 for k in rejects),
            "responses": {k: v["responses"] for k, v in led.items()},
            "totals": {k: led[k]["result"]["total_credit"] for k in valid},
        }
    except (ValueError, KeyError, OSError) as exc:
        ledger = {"ran": False, "reason": str(exc) or type(exc).__name__}

    _apply_demo_mutation(case, task, files)
    task_sha = fx.tree_identity(task)
    s = inspect(task, files)
    contract = s.pop("contract")
    instruction = (task / "instruction.md").read_text()
    heldout_bytes = HELDOUT_BYTES
    if case == "heldout-tuned":  # drop the hardest attack after seeing results, keep presenting it as frozen
        doc = json.loads(HELDOUT_BYTES)
        doc["cases"] = doc["cases"][1:]
        heldout_bytes = json.dumps(doc, indent=1, sort_keys=True).encode() + b"\n"
    heldout_cases = load_heldout(heldout_bytes)

    ev: dict[str, Any] = {
        "case": case,
        "task_sha256": task_sha,
        "static": s,
        "ledger": ledger,
        "harbor": _harbor(task / "task.toml"),
        "policy": None if case == "no-owner-review" else POLICY,
        "agents_enabled": use_agents and case != "no-independent-review",
        "agents": {},
        "controls": None,
        "heldout": None,
        "blind": None,
        "cue_only": None,
        "matched": None,
        "lint": [],
        "oracle_total": None,
        "reference_total": _ref_events()[1]["refund"]["amount_cents"],
        "repeat_identical": None,
        "order_independent": None,
        "parallel_identical": None,
        "heldout_order_independent": None,
        "heldout_frozen": hashlib.sha256(heldout_bytes).hexdigest() == FREEZE["sha256"],
        "heldout_provenance": json.loads(HELDOUT_BYTES)["author"],
        "drift_probe_detected": None,
        "exposure_hits": [],
        "trace_hits": 0,
    }

    traces: list[str] = [json.dumps(ledger.get("responses", {}), sort_keys=True)]
    if contract is not None:
        ev["oracle_total"] = oracle_total(contract)
        ev["lint"] = contract_lint(contract, instruction)
        controls = [r for r in CONTROLS if not (case == "withhold-reference" and r["id"] == "reference")]
        panel = _panel(files, contract, controls, work / "c")
        ev["controls"] = panel
        ev["repeat_identical"] = _strip(_panel(files, contract, controls, work / "c-repeat", parallel=False)) == _strip(
            panel
        )
        ev["order_independent"] = _strip(
            _panel(files, contract, controls, work / "c-rev", order=list(reversed(range(len(controls)))))
        ) == _strip(panel)
        ev["parallel_identical"] = _strip(_panel(files, contract, controls, work / "c-par", parallel=True)) == _strip(
            panel
        )
        ev["cue_only"] = _panel(files, contract, CUE_ONLY, work / "cue")
        m = _panel(files, contract, matched_setups(), work / "matched")
        ev["matched"] = {k: v["observed"].get("total_credit") for k, v in m.items()}
        if case != "withhold-heldout":
            h = _panel(files, contract, heldout_cases, work / "held")
            ev["heldout"] = h
            ev["heldout_order_independent"] = _strip(
                _panel(
                    files, contract, heldout_cases, work / "held-rev", order=list(reversed(range(len(heldout_cases))))
                )
            ) == _strip(h)
        drifted = copy.deepcopy(contract)
        drifted["sources"]["policy"]["refund_tax"] = not drifted["sources"]["policy"]["refund_tax"]
        ref = [r for r in CONTROLS if r["id"] == "reference"]
        ev["drift_probe_detected"] = _panel(files, drifted, ref, work / "drift")["reference"]["mismatch"]

    # Independent agents: restricted inputs only.
    public = (
        {"instruction.md": instruction, "contract.json": (task / "environment/contract.json").read_text()}
        if contract
        else None
    )
    if ev["agents_enabled"]:
        A = lambda role, inputs: agents.ask(role, inputs, offline=offline).output  # noqa: E731
        listing = "\n".join(
            sorted(str(p.relative_to(task)) for p in task.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        )
        env_src = {f"environment/{p.name}": p.read_text() for p in sorted((task / "environment").glob("*.py"))}
        ev["agents"]["independent-reviewer"] = A(
            "independent-reviewer",
            {
                "file-listing.txt": listing,
                "environment/Dockerfile": (task / "environment/Dockerfile").read_text(),
                "task.toml": (task / "task.toml").read_text(),
                "tests/verify.py": files["tests/verify.py"][0].decode(),
                **env_src,
            },
        )
        if public:
            blind = A("blind-solver", public)
            traces.append(json.dumps(blind, sort_keys=True))
            cands = [
                {
                    "id": "blind-as-submitted",
                    "candidate": {"success": blind["complete_success"], "events": blind["events"]},
                    "expect": {"total_credit": 9 if blind["complete_success"] else 8, "accepted": True},
                },
                {
                    "id": "blind-without-signal",
                    "candidate": {"success": False, "events": blind["events"]},
                    "expect": {"total_credit": 8, "accepted": True},
                },
            ]
            ev["blind"] = _panel(files, contract, cands, work / "blind")
            ev["agents"]["adjudicator"] = A(
                "adjudicator",
                {
                    "contract.json": public["contract.json"],
                    "reference-candidate.json": json.dumps({"events": _ref_events(), "success": True}),
                },
            )
            ev["agents"]["contract-reviewer"] = A("contract-reviewer", public)
    if case == "trace-secret":
        traces.append(f"tool_output: export AWS_ACCESS_KEY_ID={FAKE_KEY}")
    ev["trace_hits"] = sum(len(SECRET.findall(t)) for t in traces)
    if ev["agents_enabled"]:
        ev["agents"]["trace-labeler"] = agents.ask(
            "trace-labeler", {f"trace-{i}.txt": t for i, t in enumerate(traces)}, offline=offline
        ).output

    surfaces = {"instruction.md": instruction}
    for src in s["copy_sources"]:
        p = task / "environment" / src
        if p.is_file():
            surfaces[f"/app/{src}"] = p.read_text()
    answer = ev["oracle_total"]
    solution = {p.name: p.read_text() for p in (task / "solution").glob("*") if p.is_file()}
    if answer is not None:
        ev["exposure_hits"] = sorted(
            n
            for n, t in surfaces.items()
            if re.search(rf"\b{answer}\b", t) or any(sol.strip() and sol.strip() in t for sol in solution.values())
        )
        if ev["agents_enabled"]:
            ev["agents"]["exposure-reviewer"] = agents.ask(
                "exposure-reviewer",
                {
                    **{f"surface:{k}": v for k, v in surfaces.items()},
                    "private-answer.txt": f"remaining refund total = {answer} cents",
                },
                offline=offline,
            ).output

    bound = ev["task_sha256"]
    if case == "stale-evidence":  # reuse base-revision executed evidence on a changed task
        (work / "base-for-stale").mkdir(mode=0o700)
        base = collect("base", work / "base-for-stale", use_agents=use_agents, offline=offline)
        for k in (
            "controls",
            "heldout",
            "blind",
            "cue_only",
            "matched",
            "repeat_identical",
            "order_independent",
            "parallel_identical",
            "heldout_order_independent",
            "ledger",
        ):
            ev[k] = base[k]
        bound = base["task_sha256"]
    ev["evidence_bindings"] = {k: bound for k in ("controls", "heldout", "blind", "ledger")} | {
        "static": ev["task_sha256"]
    }

    inj = work / "inject"
    shutil.copytree(task, inj)
    p = inj / "tests/refund_scorer.py"
    p.write_bytes(p.read_bytes() + b"\n# injected\n")
    ev["change_injection_invalidates"] = fx.tree_identity(inj) != fx.tree_identity(task)
    del manifest
    return ev


def score(ev: dict) -> dict[str, dict]:
    out = {}
    for c in CHECKS:
        value, reason = c.fn(ev)
        out[c.criterion] = {"score": value, "tool": c.tool, "reason": reason}
    return out


# --------------------------------------------------------------------------- driver and table


def _cell(value: Any) -> str:
    return str(value).replace("|", "/")


def render(runs: list[dict], out: Path, *, capped: bool = False) -> dict:
    ids = [c.criterion for c in CHECKS]
    cases = list(runs[0])
    vec = {c: [runs[0][c]["scores"][k]["score"] for k in ids] for c in cases}
    consistent = all([r[c]["scores"][k]["score"] for k in ids] == vec[c] for r in runs for c in cases)
    base = dict(zip(ids, vec["base"]))
    reasons = {c: {k: runs[0][c]["scores"][k]["reason"] for k in ids} for c in cases}
    pref: dict[str, dict[str, Any]] = {}
    for c in cases:
        if c == "base":
            continue
        row = dict(zip(ids, vec[c]))
        lowered = [k for k in ids if row[k] != U and rank(row[k]) < rank(base[k])]
        unassessed = [k for k in ids if row[k] == U and base[k] != U]
        pref[c] = {
            "threes": sum(v == 3 for v in vec[c]),
            "dominated": all(rank(row[k]) <= rank(base[k]) for k in ids) and bool(lowered or unassessed),
            "lowered": lowered,
            "unassessed": unassessed,
            "dropped": lowered + unassessed,
        }
    top = (2, 3) if capped else (3,)  # --no-agents caps independence-dependent rows at 2 by design
    levels = {c.criterion: c.level for c in CHECKS}
    # Task rows are the per-eval score. Suite rows need other tasks; in a one-task demo they may only
    # be Unassessed or N/A unless a defect is observed.
    base_ok = all(
        (v in top or (k == "AUDIT-04-B" and v == NA)) if levels[k] == "task" else v in (U, NA) for k, v in base.items()
    )
    summary: dict[str, Any] = {
        "criteria": ids,
        "runs": len(runs),
        "consistent_across_runs": consistent,
        "base_ok": base_ok,
        "every_mutation_lower": all(p["dominated"] for c, p in pref.items() if not (capped and c in NEEDS_AGENTS)),
        "undetectable_without_agents": sorted(c for c in pref if capped and c in NEEDS_AGENTS),
        "scores": vec,
        "preference": pref,
        "partition": {k: list(v) for k, v in PARTITION.items()},
        "reasons": reasons,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "runs.json").write_text(json.dumps(runs, indent=1, default=str))
    mutations = [c for c in cases if c != "base"]
    lines = [
        "# Rubric simulation demo",
        "",
        f"Runs: {len(runs)} · consistent: **{consistent}** · "
        + (
            "base at its capped target (2 or 3, no agents)"
            if capped
            else "base task rows 18×3 + AUDIT-04-B N/A, suite rows U or N/A"
        )
        + f": **{base_ok}** "
        f"· {len(mutations)} mutations, every one ranks below the base: **{summary['every_mutation_lower']}**"
        + (
            f" (excluding {len(summary['undetectable_without_agents'])} that only the contract-reviewer agent can detect)"
            if summary["undetectable_without_agents"]
            else ""
        ),
        "",
        "Executable evidence is real. Independent review is restricted-input agents. Owner decisions are a "
        "SIMULATED policy file. `U` = Unassessed (evidence missing, unrun or blocked), never a low score.",
        "",
        "## Per mutation",
        "",
        "| Mutation | Kind | Intended change | Criterion | Level | Base | Observed | Reason | Collateral changes |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]
    for c in mutations:
        kind, change, target = MUTATIONS.get(c, ("", "", ""))
        row = dict(zip(ids, vec[c]))
        collateral = [f"{k} {_cell(base[k])}→{_cell(row[k])}" for k in pref[c]["dropped"] if k != target]
        lines.append(
            f"| `{c}` | {kind} | {change} | {target} | {levels.get(target, '')} "
            f"| {_cell(base.get(target, ''))} | {_cell(row.get(target, ''))} "
            f"| {_cell(reasons[c].get(target, ''))}"
            + (" (not detectable without agents)" if c in summary["undetectable_without_agents"] else "")
            + f" | {'; '.join(collateral) or 'none'} |"
        )
    for level, title in (
        ("task", "Task-level rows (the per-eval score)"),
        ("suite", "Suite-level rows (need other tasks)"),
    ):
        lines += [
            "",
            f"## {title}",
            "",
            "| Criterion | Tool | " + " | ".join(cases) + " |",
            "|---|---|" + "---:|" * len(cases),
        ]
        for j, check in enumerate(CHECKS):
            if check.level == level:
                cells = " | ".join(_cell(vec[k][j]) for k in cases)
                lines.append(f"| {check.criterion} | {check.tool} | {cells} |")
        lines.append(
            "| **rows at 3** | | "
            + " | ".join(f"**{sum(v == 3 for v, ch in zip(vec[k], CHECKS) if ch.level == level)}**" for k in cases)
            + " |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", nargs="*", choices=ALL_CASES)
    ap.add_argument("--no-agents", action="store_true", help="skip independent agents (rows cap at 2)")
    ap.add_argument("--offline", action="store_true", help="fail on agent cache miss instead of calling claude")
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = args.only or ALL_CASES
    if "base" not in cases:
        cases = ["base", *cases]
    if args.no_agents:
        # Without agents the base already equals the no-independent-review mutation.
        cases = [c for c in cases if c != "no-independent-review"]
    runs = []
    with tempfile.TemporaryDirectory(prefix="rubric-sim-") as tmp:
        for _ in range(args.runs):
            run = {}
            for case in cases:
                ev = collect(
                    case,
                    Path(tempfile.mkdtemp(prefix=f"{case}-", dir=tmp)),
                    use_agents=not args.no_agents,
                    offline=args.offline,
                )
                run[case] = {"scores": score(ev), "evidence": ev}
            runs.append(run)
    summary = render(runs, args.output, capped=args.no_agents)
    print((args.output / "summary.md").read_text())
    return 0 if summary["base_ok"] and summary["every_mutation_lower"] and summary["consistent_across_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
