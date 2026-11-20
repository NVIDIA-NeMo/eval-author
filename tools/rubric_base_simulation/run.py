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
    definitions_digest,
    evidence_definitions,
    value_digest,
)

EXAMPLE = ROOT / "skills/eval-author-rubric/examples/refund"
FIXTURES = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"

TASK_MUTATIONS = sorted(p.stem for p in (EXAMPLE / "mutations").glob("*.json"))
EVIDENCE_MUTATIONS = {
    "no-independent-corroboration": "Every corroborating control is run by the original author.",
    "heldout-attacks-unrun": "Held-out attack controls are withheld (execution unrun).",
    "no-native-run": "No retained native run: observed configuration has no run identity.",
    "stale-evidence-combined": "A result from an older task revision is combined with current results.",
    "blind-discovery-failed": "A blind solver could not discover the ledger-reconciliation requirement.",
    "exploit-accepted-uncontained": "The owner accepts the confirmed completion-only exploit without quarantine.",
    "exposure-overclaimed": "The statement claims no exposure although the task is in a public repository.",
    "confirmation-exposed": "The confirmation set was visible to development feedback before the freeze.",
    "trace-unauthorized-use": "A trace is shared beyond its approved recipients and use.",
    "state-carryover": "A predecessor episode's refund changes the target episode's credit.",
    "cue-bypass": "A style-only cue control earns credit without the refund work.",
    "misreported-config": "The report states a 300 s agent timeout; task.toml declares 30 s.",
    "unmatched-comparison": "The split-refund policy gets a larger action budget, with no rationale.",
    "family-cue-bypass": "In the interval-reservation family, a cue-only control earns credit.",
}
# Edits applied to the materialized task after its controls run (the verifier does not read these files).
LOCAL_EDITS = {
    "unpinned-image": ("environment/Dockerfile", "FROM python@sha256:", "FROM python:3.12-slim #"),
}
CASES = ["base", *TASK_MUTATIONS, *LOCAL_EDITS, *EVIDENCE_MUTATIONS]

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
# AUDIT-03 attack classes. Each maps to the real control whose verifier result is its observation.
# Classes without a local control are proxied by the control that submits the same effect.
ATTACK_PANEL = {
    "no_work": "no-work",
    "all_failure": "all-failure",
    "forged_success": "stale-carryover",
    "inert_reference": "no-work",  # reference text with no ledger actions = no events
    "hardcoded_answer": "missing-ledger",  # claims the answer without reconciling the ledger
    "valid_inaction": "permitted-zero-balance-inaction",
    "realistic_wrong": "wrong-amount",
}
AUDIT03_HELDOUT = ("duplicate-ledger", "late-approval", "missing-approval", "protected-mutation")
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
    if case_dir.name in LOCAL_EDITS:
        path, old, new = LOCAL_EDITS[case_dir.name]
        text = (task / path).read_text()
        assert old in text, case_dir.name
        (task / path).write_text(text.replace(old, new, 1))
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
        "task_dir": str(task),
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
        unrun = outcome is None or (evidence == "heldout-attacks-unrun" and ident == "AUDIT-03")
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


def derive(o: dict[str, Any], base: dict[str, Any], prior: dict[str, Any], evidence: str | None) -> tuple[dict, dict]:
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
    misreported = evidence == "misreported-config"
    unmatched = evidence == "unmatched-comparison"
    flat = {f"{s}.{k}": str(v) for s, table in toml.items() if isinstance(table, dict) for k, v in table.items()}
    f["provenance.declared-configuration"] = {
        "configuration_fields": [
            {
                "id": key,
                "value": value,
                "source_identity": files["task.toml"],
                "availability": "sourced",
                "claim_limit": "uninterpretable" if misreported and key == "agent.timeout_sec" else "none",
                "reported_value": "300" if misreported and key == "agent.timeout_sec" else value,
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
                "rationale": None
                if unmatched
                else "Both policies run the same contract, verifier and limits; only the refund split differs.",
                "disposition": "unjustified" if unmatched else "justified",
                "advantage": "demonstrated" if unmatched or not matches("split-alternative") else "none",
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

    # Leakage and credit criteria.
    def credit(name: str) -> int:
        return 1 if ctl[name]["accepted"] else 0

    def expected_credit(name: str) -> int:
        return 1 if exp[name]["accepted"] else 0

    f["AUDIT-03"] = {
        "attack_controls": [
            {
                "id": f"{cls}:{name}",
                "attack_class": cls,
                "applicability": "applicable",
                "expected_credit": expected_credit(name),
                "actual_credit": credit(name),
                "health": "healthy" if ctl[name]["health"] == "ok" else "error",
                "reach": "reached",
            }
            for cls, name in ATTACK_PANEL.items()
        ]
        + [
            {
                "id": f"{cls}:native",
                "attack_class": cls,
                "applicability": "applicable",
                "expected_credit": 0,
                # Native attacks: denied when protected sources stay out of the image (observed COPY list).
                "actual_credit": 1 if any(p in copied for p in PROTECTED) else 0,
                "health": "healthy",
                "reach": "reached",
            }
            for cls in ("scorer_tampering", "answer_access")
        ]
    }
    sim["AUDIT-03"] = "scorer_tampering and answer_access are simulated native attacks; others are real controls."
    # The exploit was confirmed by actually running the completion-only revision's verifier on no-work.
    replay_awarded = ctl["no-work"]["accepted"] is True
    disposition = "accepted" if evidence == "exploit-accepted-uncontained" else "quarantined"
    containment = {
        "affected_revision": prior["task_sha256"] if prior["controls"]["no-work"]["accepted"] else None,
        "disposition": disposition,
        "residual_risk": "Results graded by the completion-only revision are excluded from reporting.",
        "repair_revision": o["task_sha256"],
        "replay": "credit_awarded" if replay_awarded else "credit_withheld",
        "valid_after_repair": "credited" if ctl["reference"]["accepted"] else "rejected",
    }
    f["AUDIT-04-B"] = {"exploit_containment": [{"id": "completion-only", **containment}]}
    f["AUDIT-04-B-SUITE"] = {
        "suite_containment": [
            {"id": "completion-only:refund", "family": "retained", **containment},
            {"id": "completion-only:interval-reservation", "family": "new", **containment},
        ]
    }
    sim["AUDIT-04-B"] = "Quarantine decision is a simulated owner premise; confirmation and replay are real runs."
    sim["AUDIT-04-B-SUITE"] = "The new-family replay is simulated as matching the refund replay."
    overclaim = evidence == "exposure-overclaimed"
    f["AUDIT-05-B"] = {
        "exposure_inventory": [
            {
                "id": path,
                "exposure": "public",
                "overlap": "verified" if overclaim else "unknown",
                "disclosed": "no" if overclaim else "yes",
                "statement": "overclaims" if overclaim else "within_support",
                "sensitivity": "computed",
            }
            for path in ("solution/reference.py", "solution/solve.sh")
        ]
    }
    sim["AUDIT-05-B"] = "The task is in a public repository; training overlap is unknown (simulated review)."
    exposed = evidence == "confirmation-exposed"
    f["AUDIT-05-S03"] = {
        "selection_ledger": [
            {
                "id": "candidate-policies",
                "event": "candidate",
                "alternatives_retained": "yes",
                "holdout_access": "before_freeze" if exposed else "none",
                "label": "exploratory",
            },
            {
                "id": "development-feedback",
                "event": "feedback",
                "alternatives_retained": "yes",
                "holdout_access": "none",
                "label": "exploratory",
            },
            {
                "id": "freeze",
                "event": "freeze",
                "alternatives_retained": "yes",
                "holdout_access": "none",
                "label": "exploratory",
            },
            {
                "id": "confirmation",
                "event": "confirmation",
                "alternatives_retained": "yes",
                "holdout_access": "after_freeze",
                "label": "confirmatory",
            },
        ]
    }
    sim["AUDIT-05-S03"] = "Selection ledger and confirmation are simulated."
    secret = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[0-9A-Za-z]{36}")
    task_dir = Path(o["task_dir"])
    f["TRACE-04-A"] = {
        "material_review": [
            {
                "id": path,
                "channel": "task-file",
                "candidate": "secret" if secret.search((task_dir / path).read_text(errors="replace")) else "none",
                "reviewed": "yes",
                "disposition": "authorized",
                "approval_match": "match",
            }
            for path in sorted(files)
        ]
        + [
            {
                "id": "trace:reference-run",
                "channel": "trace",
                "candidate": "none",
                "reviewed": "yes",
                "disposition": "unauthorized_use" if evidence == "trace-unauthorized-use" else "authorized",
                "approval_match": "match",
            }
        ]
    }
    sim["TRACE-04-A"] = "Task files are screened for secrets for real; trace review and approvals are simulated."
    target = str(ctl["reference"]["accepted"])
    carried = evidence == "state-carryover"
    f["ENV-03-S01"] = {
        "isolation_surfaces": [
            {
                "id": "ledger-record",
                "sharing": "isolated",
                "reset_verified": "yes",
                "target_alone": target,
                "target_after": str(not ctl["reference"]["accepted"]) if carried else target,
                "consequence": "credit_change" if carried else "none",
            },
            {
                "id": "work-directory",
                "sharing": "isolated",
                "reset_verified": "yes",
                "target_alone": target,
                "target_after": target,
                "consequence": "none",
            },
        ]
    }
    sim["ENV-03-S01"] = "Predecessor/target episodes on a fresh service are simulated."
    cue = {
        "qualified": "yes",
        "health": "healthy",
    }
    f["CREATE-02"] = {
        "cue_controls": [
            {
                "id": cls,
                "cue_class": cls,
                **cue,
                "outcome": "bypass" if evidence == "cue-bypass" and cls == "style_only" else "no_bypass",
            }
            for cls in ("cue_only", "nuisance_swap", "misleading_cue", "style_only")
        ]
    }
    f["CREATE-02-SUITE"] = {
        "family_cue_controls": [
            {"id": f"{family}:{cls}", "family": family, "authorship": "independent", "cue_class": cls, **cue}
            | {
                "outcome": "bypass"
                if evidence == "family-cue-bypass" and family == "interval-reservation" and cls == "cue_only"
                else "no_bypass"
            }
            for family in ("interval-reservation", "dependency-release")
            for cls in ("cue_only", "nuisance_swap", "misleading_cue", "style_only")
        ]
    }
    sim["CREATE-02"] = "Cue-control outcomes are simulated."
    sim["CREATE-02-SUITE"] = "Cue controls on two companion families are simulated."

    corroboration = {
        "AUDIT-03": {
            "heldout_attack": all(matches(n) for n in AUDIT03_HELDOUT),
            "valid_work": matches("split-alternative"),
        },
        "AUDIT-04-B": {"independent_attack": not replay_awarded},
        "AUDIT-04-B-SUITE": {"independent_attack": not replay_awarded, "cross_family_replay": not replay_awarded},
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
    rubric = list(evidence_definitions())
    columns = rubric
    base_obs = observe(out / "_base-observation", None)
    prior = observe(out / "_prior-exploit-revision", "completion-only")
    scores: dict[str, dict[str, int | None]] = {}
    for case in args.case or CASES:
        case_dir = out / case
        case_dir.mkdir()
        mutation = case if case in TASK_MUTATIONS else None
        evidence = case if case in EVIDENCE_MUTATIONS else None
        o = observe(case_dir, mutation)
        facts, sim = derive(o, base_obs, prior, evidence)
        write_json(case_dir / "simulated-premises.json", sim, sort_keys=True)
        report = build(case_dir, facts, o)
        rows = report["criterion_demonstration"]
        scores[case] = {ident: rows[ident]["score"] for ident in columns}

    def rank(score: int | None) -> int:
        return -1 if score is None else score  # Unassessed ranks below every established score

    base = scores.get("base")
    summary = {
        "scorer": "skills/eval-author-rubric/scripts/measure.py, criterion_evidence synthetic scope",
        "rubric_criteria": rubric,
        "scores": scores,
        "base_perfect": base is not None and all(base[i] == 3 for i in columns),
        "mutations_below_base": {
            case: base is not None
            and all(rank(row[i]) <= rank(base[i]) for i in columns)
            and any(rank(row[i]) < rank(base[i]) for i in rubric)
            for case, row in scores.items()
            if case != "base"
        },
    }
    write_json(out / "summary.json", summary, sort_keys=True)
    short = {i: i.replace("provenance.", "p.").replace("task-contract.", "tc.") for i in columns}
    header = f"| case | rubric /{3 * len(rubric)} | " + " | ".join(short[i] for i in columns) + " |"
    lines = [header, "|" + "---|" * (len(columns) + 2)]
    for case, row in scores.items():
        cells = ["U" if row[i] is None else str(row[i]) for i in columns]
        lines.append(f"| {case} | {sum(row[i] or 0 for i in rubric)} | " + " | ".join(cells) + " |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(json.dumps({k: summary[k] for k in ("base_perfect", "mutations_below_base")}, indent=1))
    return 0 if summary["base_perfect"] and all(summary["mutations_below_base"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
