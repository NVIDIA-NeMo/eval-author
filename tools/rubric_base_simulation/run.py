# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Score a simulated refund base and its mutations through the unchanged measure.py CLI.

Each case materializes the refund Harbor task (base or one mutation recipe),
runs the real refund verifier on every declared control, and observes the
task bytes. Criterion facts are derived from those observations. Inputs that
need a native run, a reviewer or an independent actor are simulated premises,
listed per fact in ``simulated``. Nothing here assigns a score: the unchanged
``skills/eval-author-rubric/scripts/measure.py`` derives every number from the
facts through ``criterion_evidence`` in ``synthetic-scoring-demonstration`` scope.

Usage: run.py --output NEW_DIRECTORY [--case NAME ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
from rubric_demo_common import artifact_record, run_measure, write_json  # noqa: E402
from rubric_evidence_scoring import (  # noqa: E402
    ROBUSTNESS_OUTCOMES,
    ROBUSTNESS_POLICIES,
    canonical_definitions,
    definitions_digest,
    value_digest,
)

EXAMPLE = ROOT / "skills/eval-author-rubric/examples/refund"
FIXTURES = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"
LEAKAGE = ROOT / "skills/eval-author-rubric/references/leakage-and-credit.json"

TASK_MUTATIONS = sorted(p.stem for p in (EXAMPLE / "mutations").glob("*.json"))
EVIDENCE_MUTATIONS = {
    "no-independent-corroboration": "Every corroborating control is run by the original author.",
    "heldout-attacks-unrun": "Held-out attack controls are withheld (execution unrun).",
    "no-native-run": "No retained native run: observed configuration has no run identity.",
    "stale-evidence-combined": "A result from an older task revision is combined with current results.",
    "blind-discovery-failed": "A blind solver could not discover the ledger-reconciliation requirement.",
}
CASES = ["base", *TASK_MUTATIONS, *EVIDENCE_MUTATIONS]

# Declared by the task author for the base design; mutations are compared against it.
DECLARED_GRADER = "reviewed"
PROTECTED = ("tests", "solution")
# Public wording for each scored check (task author crosswalk; verified against the text).
PUBLIC_PHRASES = {
    "completion_signal": "The completion signal earns one optional point",
    "association": "Use the current run, requested customer, and requested order",
    "amount": "The sum must equal the remaining balance",
    "destination": "requested original payment destination",
    "approval_present": "a listed manager must approve it",
    "approval_before_effect": "Required approval must still precede every refund action",
    "ledger": "The final ledger must reconcile every new refund exactly once",
    "protected_state": "Preserve unrelated state and all historical rows",
    "fresh_effect": "a unique fresh ID",
}
# Control classes for credit-control facts. Health controls are excluded from credit.
CONTROL_CLASS = {
    "reference": "reference",
    "split-alternative": "valid_alternative",
    "permitted-zero-balance-inaction": "valid_alternative",
    "below-approval-threshold": "valid_alternative",
    "missing-completion": "valid_alternative",
    "no-work": "attack",
    "protected-mutation": "attack",
    "historical-mutation": "attack",
    "missing-ledger": "attack",
    "duplicate-ledger": "attack",
    "late-approval": "attack",
    "missing-approval": "attack",
    "approval-threshold-inclusive": "attack",
    "wrong-customer": "incorrect",
    "wrong-order": "incorrect",
    "wrong-amount": "incorrect",
    "wrong-destination": "incorrect",
}
HELDOUT = ("all-failure", "stale-carryover")
AUTHOR, INDEPENDENT = "task-author-sim", "independent-reviewer-sim"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fixtures(*args: str) -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-B", str(FIXTURES), *args], check=True, capture_output=True, env=env, timeout=300)


def observe(case_dir: Path, mutation: str | None) -> dict[str, Any]:
    """Materialize the task and run every declared control through the real verifier."""
    case_dir.mkdir(exist_ok=True)
    fixtures("materialize", "--output", str(case_dir / "case"), *(["--mutation", mutation] if mutation else []))
    expected = json.loads((EXAMPLE / "expectations/controls.json").read_bytes())
    try:
        fixtures("local-controls", "--input", str(case_dir / "case"), "--output", str(case_dir / "controls"))
        results = json.loads((case_dir / "controls/local-results.json").read_bytes())["cases"]
    except subprocess.CalledProcessError:
        # The verifier could not run at all (for example, no contract): every control is an execution error.
        results = {name: {"result": {"health": "error"}} for name in expected}
    task = case_dir / "case/assessment/task"
    files = {str(p.relative_to(task)): sha(p.read_bytes()) for p in sorted(task.rglob("*")) if p.is_file()}
    dockerfile = (task / "environment/Dockerfile").read_text()
    verify = (task / "tests/verify.py").read_text()
    contract_path = task / "environment/contract.json"
    return {
        "task_sha256": sha(json.dumps(files, sort_keys=True).encode()),
        "files": files,
        "task_toml": tomllib.loads((task / "task.toml").read_text()),
        "dockerfile": {
            "from": re.search(r"^FROM (\S+)", dockerfile, re.M).group(1),
            "copies": re.findall(r"^COPY (.+?) /app", dockerfile, re.M),
            "entrypoint": re.search(r"^ENTRYPOINT (.+)$", dockerfile, re.M).group(1),
        },
        "grader": re.search(r'^GRADER = "(\w+)"', verify, re.M).group(1),
        "instruction": (task / "instruction.md").read_text(),
        "contract": json.loads(contract_path.read_bytes()) if contract_path.is_file() else None,
        "controls": {
            name: {
                "health": row["result"].get("health"),
                "accepted": row["result"].get("accepted"),
                "total_credit": row["result"].get("total_credit"),
            }
            for name, row in results.items()
        },
        "expected": {
            name: {k: row.get(k) for k in ("health", "accepted", "total_credit")} for name, row in expected.items()
        },
    }


def robust(ident: str, observed: dict[str, bool | None], evidence: str) -> list[dict[str, Any]]:
    """Corroborating controls. ``observed`` maps role -> success (True), failure (False) or unrun (None)."""
    _, roles = ROBUSTNESS_POLICIES[ident]
    rows = []
    for role in roles:
        success, failure = ROBUSTNESS_OUTCOMES[ident][role]
        outcome = observed.get(role, True)
        unrun = outcome is None or (evidence == "heldout-attacks-unrun" and ident == "AC-02")
        rows.append(
            {
                "id": role,
                "role": role,
                "expected": success,
                "observed": None if unrun else success if outcome else failure,
                "original_actor": AUTHOR,
                "corroborating_actor": AUTHOR if evidence == "no-independent-corroboration" else INDEPENDENT,
                "source_identity": f"sim-{ident}-{role}",
                "execution": "unrun" if unrun else "completed",
                "scope": "same-task-revision",
            }
        )
    return rows


def derive(o: dict[str, Any], base: dict[str, Any], evidence: str | None) -> tuple[dict, dict]:
    """Return (facts by criterion, simulated-premise notes by criterion)."""
    f: dict[str, dict[str, Any]] = {}
    sim: dict[str, str] = {}
    files, ctl, exp = o["files"], o["controls"], o["expected"]
    toml, docker = o["task_toml"], o["dockerfile"]
    ledger_up = "server.py" in docker["entrypoint"]
    contract_ok = o["contract"] is not None

    def matches(name: str) -> bool:
        return ctl[name]["health"] == exp[name]["health"] and ctl[name]["accepted"] == exp[name]["accepted"]

    required = sorted(base["files"])
    f["provenance.task-artifact-access"] = {
        "task_artifacts": [
            {
                "id": path,
                "access": "accessible" if path in files else "missing",
                "revision": files.get(path),
                "inspection_effect": "none" if path in files else "blocks",
            }
            for path in required
        ]
    }
    f["provenance.grading-path"] = {
        "grading_routes": [
            {
                "id": "tests/verify.py",
                "declared_scorer": DECLARED_GRADER,
                "selected_scorer": o["grader"],
                "configuration": files["tests/verify.py"],
                "branch": "resolved",
            }
        ]
    }
    f["provenance.grading-dependency-access"] = {
        "grading_dependencies": [
            {
                "id": path,
                "access": "accessible" if path in files else "missing",
                "revision": files.get(path),
                "inspection_effect": "none" if path in files else "blocks",
                "reference_review": "resolved" if path in files else "unresolved",
            }
            for path in ("tests/verify.py", "tests/refund_scorer.py", "environment/contract.json")
        ]
    }
    flat = {f"{s}.{k}": str(v) for s, table in toml.items() if isinstance(table, dict) for k, v in table.items()}
    f["provenance.declared-configuration"] = {
        "configuration_fields": [
            {
                "id": key,
                "value": value,
                "source_identity": files["task.toml"],
                "availability": "sourced",
                "claim_limit": "none",
                "reported_value": value,
            }
            for key, value in sorted(flat.items())
        ]
    }
    run = None if evidence == "no-native-run" else "sim-harbor-run-" + o["task_sha256"][:12]
    f["provenance.observed-configuration"] = {
        "run_settings": [
            {"id": key, "declared": value, "observed": value, "run_identity": run, "disposition": "agrees"}
            for key, value in sorted(flat.items())
        ]
    }
    sim["provenance.observed-configuration"] = "Harbor run settings are simulated as the task.toml values."
    f["provenance.runtime-route"] = {
        "provider_validation": [
            {
                "id": "harbor",
                "provider": "harbor",
                "adapter_revision": "harbor-0.24.0",
                "manifest_identity": files["task.toml"],
                "validation": "accepted"
                if {"task.toml", "tests/test.sh", "environment/Dockerfile"} <= set(files)
                else "error",
                "adapter": "compatible" if ledger_up and contract_ok else "incompatible",
            }
        ]
    }
    sim["provenance.runtime-route"] = (
        "Harbor manifest acceptance is simulated; ledger entrypoint and inputs are observed."
    )
    f["provenance.comparison-fairness"] = {
        "setup_differences": [
            {
                "id": "reference-vs-split",
                "left": "reference",
                "right": "split-alternative",
                "rationale": "Both policies run the same contract, verifier and limits; only the refund split differs.",
                "disposition": "justified",
                "advantage": "none" if matches("split-alternative") else "demonstrated",
            }
        ]
    }
    sim["provenance.comparison-fairness"] = "Matched-setup adjudication is a simulated review."
    f["provenance.solver-tool-access"] = {
        "tool_probes": [
            {
                "id": op,
                "action": op,
                "solver_identity": toml["agent"]["user"],
                "expected": "ok",
                "actual": "ok" if ledger_up and contract_ok else "socket_unavailable",
                "probe": "call",
                "execution": "completed",
            }
            for op in ("read", "act", "complete")
        ]
    }
    sim["provenance.solver-tool-access"] = "Socket calls are simulated from the observed image entrypoint and inputs."
    pinned = "@sha256:" in docker["from"]
    f["provenance.dependency-substitution"] = {
        "components": [
            {
                "id": "python-base-image",
                "form": "real",
                "observed_revision": docker["from"] if pinned else None,
                "approved_revision": None,
                "authorization": "not_required",
                "availability": "available",
            }
        ]
    }
    contract_sha = files.get("environment/contract.json")
    base_contract = base["files"]["environment/contract.json"]
    drift = contract_sha != base_contract
    f["provenance.external-state"] = {
        "external_state": [
            {
                "id": "environment/contract.json",
                "captured_identity": contract_sha,
                "mode": "pinned",
                "live_bound": None,
                "observation": "drifted" if drift else "within_bound",
                "outcome_relation": "interpretable" if matches("reference") else "invalidated",
            }
        ]
    }
    stale = evidence == "stale-evidence-combined"
    f["provenance.evidence-revision-consistency"] = {
        "result_revisions": [
            {
                "id": f"control:{name}",
                # Simulated older revision: the reference result was graded before the current task bytes.
                "recorded_revision": sha(b"refund-task-prior-revision")
                if stale and name == "reference"
                else o["task_sha256"],
                "current_revision": o["task_sha256"],
                "change": "material" if stale and name == "reference" else "unchanged",
                "disposition": "combined" if stale and name == "reference" else "current",
                "lineage": "fresh local verifier run",
            }
            for name in sorted(ctl)
        ]
    }
    # Public contract versus actual scorer behavior. The missing-completion control isolates
    # whether the scorer treats the optional completion signal as mandatory.
    mandatory = set(o["contract"]["scoring"]["mandatory"]) if contract_ok else set()
    completion_hidden = ctl["missing-completion"]["accepted"] is False
    rows = []
    for check, phrase in PUBLIC_PHRASES.items():
        public = phrase if phrase in o["instruction"] else None
        hidden = check == "completion_signal" and completion_hidden
        rows.append(
            {
                "id": check,
                "public_requirement": public,
                "scorer_requirement": check + (" (mandatory)" if check in mandatory or hidden else " (optional)"),
                "interpretation": "public" if public else "unresolved",
                "resolution": "prevents_grading" if hidden else "resolved" if public else "ambiguous",
            }
        )
    f["task-contract.clear-contract"] = {"contract_requirements": rows}
    f["task-contract.discoverable-requirements"] = {
        "scored_conditions": [
            {
                "id": check,
                "condition": PUBLIC_PHRASES[check],
                "authority": "/app/contract.json scoring" if contract_ok else None,
                "solver_access": "accessible"
                if contract_ok and "contract.json" in " ".join(docker["copies"])
                else "denied",
                "mapping": "disputed" if check == "completion_signal" and completion_hidden else "supported",
            }
            for check in PUBLIC_PHRASES
        ]
    }
    f["task-contract.scoring-authority"] = {
        "scoring_authorities": [
            {
                "id": name,
                "expected": json.dumps(exp[name], sort_keys=True),
                "authoritative": json.dumps(ctl[name], sort_keys=True),
                "obligation": "declared control outcome",
                "semantics": "resolved",
            }
            for name in sorted(ctl)
        ]
    }
    copied = " ".join(docker["copies"])
    f["AC-01"] = {
        "information_surfaces": [
            {
                "id": surface,
                "contract_role": "protected",
                "solver_read": "readable" if surface in copied else "denied",
                "permitted_neighbor": "readable" if contract_ok else "denied",
                "exposure": "unintended" if surface in copied else "none",
                "review": "complete",
            }
            for surface in PROTECTED
        ]
    }
    sim["AC-01"] = "Solver read denial is simulated from the observed image COPY list."
    f["AC-02"] = {
        "credit_controls": [
            {
                "id": name,
                "class": cls,
                "expected_credit": 1 if exp[name]["accepted"] else 0,
                "actual_credit": 1 if ctl[name]["accepted"] else 0,
                "health": "healthy" if ctl[name]["health"] == "ok" else "error",
                "reach": "reached",
                "applicability": "applicable",
            }
            for name, cls in CONTROL_CLASS.items()
        ]
    }
    f["AC-03"] = {
        "receipt_controls": [
            {
                "id": control,
                "receipt_kind": kind,
                "consumer": "credited" if ctl[control]["accepted"] else "rejected",
                "native_validation": "valid" if kind == "current" else "invalid",
                "health": "healthy",
                "reach": "reached",
            }
            for control, kind in (("reference", "current"), ("no-work", "forged"), ("stale-carryover", "stale"))
        ]
    }
    sim["AC-03"] = "Receipt kinds map to real controls: a completion claim with no events is the forged receipt."
    f["AC-04"] = {
        "reset_surfaces": [
            {
                "id": "ledger-record",
                "sharing": "isolated",
                "expected_state": "initial",
                "reset_state": "initial",
                "target_alone": str(ctl["reference"]["accepted"]),
                "target_after": str(ctl["reference"]["accepted"]),
                "consequence": "none",
                "inspection": "complete",
            }
        ]
    }
    sim["AC-04"] = "Predecessor/target ordering on a fresh service is simulated."

    heldout_ok = all(matches(name) for name in HELDOUT)
    corroboration = {
        "provenance.grading-path": {"selection": o["grader"] == DECLARED_GRADER},
        "provenance.runtime-route": {"clean_setup": ledger_up and contract_ok},
        "provenance.external-state": {"deliberate_drift": True},
        "task-contract.clear-contract": {
            "independent_solution": matches("reference"),
            "valid_boundary": matches("split-alternative") and matches("missing-completion"),
        },
        "task-contract.discoverable-requirements": {
            "blind_discovery": evidence != "blind-discovery-failed",
            "solver_access": contract_ok,
        },
        "task-contract.scoring-authority": {
            "reference": matches("reference"),
            "scoring_contract": all(matches(n) for n in ctl),
        },
        "AC-02": {"heldout_attack": heldout_ok, "valid_alternative": matches("below-approval-threshold")},
        "AC-03": {"invalid_rejection": matches("no-work"), "valid_acceptance": matches("reference")},
    }
    for ident in f:
        name, _ = ROBUSTNESS_POLICIES[ident]
        f[ident][name] = robust(ident, corroboration.get(ident, {}), evidence or "")
    sim["*"] = (
        "Corroborating controls by an independent actor are simulated premises unless derived from controls above."
    )
    return f, sim


def build(case_dir: Path, facts: dict[str, dict[str, Any]], o: dict[str, Any]) -> dict[str, Any]:
    observations = case_dir / "observations.json"
    write_json(
        observations,
        {k: o[k] for k in ("task_sha256", "files", "grader", "dockerfile", "controls", "expected")},
        sort_keys=True,
    )
    task = artifact_record(
        case_dir, "task", "observations.json", "task", revision=o["task_sha256"], source="refund-simulation"
    )
    subject = {"artifact_id": "task", "digest": task["digest"]}
    flat = {name: value for criterion in facts.values() for name, value in criterion.items()}
    write_json(
        case_dir / "facts.json",
        {
            "schema": "rubric-fact-source-v1",
            "subject": subject,
            "definitions_digest": definitions_digest(),
            "provenance": {
                "kind": "synthetic",
                "actor_id": "refund-base-simulation",
                "activity": "observation",
                "limitations": ["Simulated rubric evidence derived from real task bytes and real verifier controls."],
            },
            "facts": flat,
        },
        sort_keys=True,
    )
    source = artifact_record(
        case_dir, "facts", "facts.json", "evidence", revision=o["task_sha256"], source="refund-simulation"
    )
    support = {"artifact_id": "task", "pointer": "/task_sha256", "digest": value_digest(o["task_sha256"])}
    items = [
        {
            "item_id": ident,
            "facts": {
                name: {
                    "basis": "json_observation",
                    "source": {"artifact_id": "facts", "pointer": f"/facts/{name}", "digest": value_digest(value)},
                    "support": [support],
                }
                for name, value in criterion.items()
            },
        }
        for ident, criterion in facts.items()
    ]
    write_json(
        case_dir / "packet.json",
        {
            "schema": "rubric-criterion-evidence-v1",
            "subject": subject,
            "definitions_digest": definitions_digest(),
            "assessment_scope": "synthetic-scoring-demonstration",
            "items": items,
        },
        sort_keys=True,
    )
    packet = artifact_record(
        case_dir, "packet", "packet.json", "evidence", revision=o["task_sha256"], source="refund-simulation"
    )
    write_json(
        case_dir / "selection.json",
        {"artifacts": [task, source, packet], "criterion_evidence": {"artifact_id": "packet"}},
        sort_keys=True,
    )
    report, _ = run_measure(
        case_dir, record_invocation=True, sort_keys=True, error_message=f"measure.py rejected {case_dir.name}"
    )
    run_measure(case_dir, verify=True, error_message=f"measure.py replay differs for {case_dir.name}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    out = args.output.absolute()
    if out.is_relative_to(ROOT):
        parser.error("output must be outside the checkout")
    out.mkdir(mode=0o700)
    criteria = list(canonical_definitions())
    current = [item["id"] for item in json.loads(LEAKAGE.read_bytes())["items"]]
    base_obs = observe(out / "_base-observation", None)
    scores: dict[str, dict[str, int | None]] = {}
    for case in args.case or CASES:
        case_dir = out / case
        case_dir.mkdir()
        mutation = case if case in TASK_MUTATIONS else None
        evidence = case if case in EVIDENCE_MUTATIONS else None
        o = observe(case_dir, mutation)
        facts, sim = derive(o, base_obs, evidence)
        write_json(case_dir / "simulated-premises.json", sim, sort_keys=True)
        report = build(case_dir, facts, o)
        rows = report["criterion_demonstration"]
        scores[case] = {ident: rows[ident]["score"] for ident in criteria}
        for ident in current:
            scores[case][ident] = None  # no criterion_evidence predicate exists for this canonical row
    base = scores.get("base")
    summary = {
        "scorer": "skills/eval-author-rubric/scripts/measure.py (unchanged), criterion_evidence synthetic scope",
        "scored_criteria": criteria,
        "unscorable_current_criteria": current,
        "scores": scores,
        "base_perfect_on_scorable": base is not None and all(base[i] == 3 for i in criteria),
        "mutations_below_base": {
            case: all((row[i] or -1) <= 3 for i in criteria) and any(row[i] != 3 for i in criteria)
            for case, row in scores.items()
            if case != "base"
        },
    }
    write_json(out / "summary.json", summary, sort_keys=True)
    short = {i: i.replace("provenance.", "p.").replace("task-contract.", "tc.") for i in criteria + current}
    header = "| case | " + " | ".join(short[i] for i in criteria + current) + " | sum/" + str(3 * len(criteria)) + " |"
    lines = [header, "|" + "---|" * (len(criteria) + len(current) + 2)]
    for case, row in scores.items():
        cells = ["U" if row[i] is None else str(row[i]) for i in criteria + current]
        lines.append(f"| {case} | " + " | ".join(cells) + f" | {sum(row[i] or 0 for i in criteria)} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(json.dumps({k: summary[k] for k in ("base_perfect_on_scorable", "mutations_below_base")}, indent=1))
    return 0 if summary["base_perfect_on_scorable"] and all(summary["mutations_below_base"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
