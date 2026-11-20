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
import time
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
from native import FREEZE, native_facts  # noqa: E402
from rubric_authority import check_authority  # noqa: E402
from rubric_evidence_scoring import (  # noqa: E402
    ROBUSTNESS_OUTCOMES,
    ROBUSTNESS_POLICIES,
    canonical_definitions,
    definitions_digest,
    method_definitions,
    value_digest,
)
from rubric_leakage import scan_task  # noqa: E402

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
    "undisclosed-self-corroboration": "Claude's corroboration is presented as independent review.",
}
# Edits applied to the materialized task after its controls run (the verifier does not read these files).
LOCAL_EDITS = {
    "unpinned-image": ("environment/Dockerfile", "FROM python@sha256:", "FROM python:3.12-slim #"),
    "answer-in-instruction": (
        "instruction.md",
        "Exclude\nshipping.",
        "Exclude\nshipping. Compute it as:\n\n"
        '    amount = order["merchandise_cents"] - order["discount_cents"] - prior\n'
        '    if policy["refund_tax"]:\n        amount += order["tax_cents"]\n',
    ),
    # A fresh service start resumes the previous episode's record instead of resetting it.
    "no-reset-on-start": (
        "environment/ledger.py",
        "        self.reset()\n",
        "        if self.record.exists():\n"
        "            self.value = json.loads(self.record.read_text())\n"
        "        else:\n"
        "            self.reset()\n",
    ),
    "amount-cue-in-instruction": (
        "instruction.md",
        "Exclude\nshipping.",
        "Exclude\nshipping. The expected refund is 8000 cents.",
    ),
}
# Public lines shared with private files, reviewed by the independent reviewer: license headers,
# imports and the public event and scoring schema. None carries the refund computation.
REVIEWED_OVERLAP = {
    "# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.",
    "# SPDX-License-Identifier: Apache-2.0",
    "from pathlib import Path",
    'if __name__ == "__main__":',
    "parser = argparse.ArgumentParser()",
    'kind = event.get("kind")',
    'if event["kind"] == "refund":',
    'elif event["kind"] == "ledger":',
    'for row in event["entries"]:',
    "fcntl.flock(lock, fcntl.LOCK_EX)",
    '"mandatory": ["association", "amount", "destination", "approval_present", "approval_before_effect", '
    '"ledger", "protected_state", "fresh_effect"],',
}
LOCAL_EDIT_DESCRIPTIONS = {
    "unpinned-image": "The Dockerfile uses an unpinned python:3.12-slim base image.",
    "answer-in-instruction": "The instruction includes the reference solution's refund arithmetic.",
    "no-reset-on-start": "A fresh ledger start resumes the previous episode's record.",
    "amount-cue-in-instruction": "The instruction states the expected refund amount.",
}
CASES = ["base", *TASK_MUTATIONS, *LOCAL_EDITS, *EVIDENCE_MUTATIONS]


def describe(case: str) -> tuple[str, str]:
    """Return (kind, one-line description) for a case."""
    if case in TASK_MUTATIONS:
        return "task", json.loads((EXAMPLE / "expectations" / f"{case}.json").read_text())["finding"]
    if case in LOCAL_EDITS:
        return "task", LOCAL_EDIT_DESCRIPTIONS[case]
    if case in EVIDENCE_MUTATIONS:
        return "evidence", EVIDENCE_MUTATIONS[case]
    return "base", "The refund task with complete evidence."


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
# The task author is simulated. Claude corroborates where no panel or recorded review does.
AUTHOR, INDEPENDENT = "task-author-sim", "claude-opus-5-5-reviewer"


TABLE = ROOT / "skills/eval-author-rubric/scripts/rubric_table.py"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: Any, *, sort_keys: bool = True) -> None:
    path.write_text(json.dumps(value, sort_keys=sort_keys, indent=2, allow_nan=False) + "\n")


def artifact_record(directory: Path, ident: str, path: str, role: str, *, revision: str, source: str) -> dict:
    digest = sha((directory / path).read_bytes())
    return {
        "id": ident,
        "path": path,
        "digest": digest,
        "role": role,
        "revision": revision,
        "access": "available",
        "source": source,
        "selection_origin": "explicit",
        "evidence": [{"path": path, "digest": digest}],
    }


def table(case_dir: Path, *args: str) -> subprocess.CompletedProcess:
    command = [sys.executable, "-B", str(TABLE), "--root", str(case_dir), *args]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)


def fixtures(*args: str) -> None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-B", str(FIXTURES), *args], check=True, capture_output=True, env=env, timeout=300)


def grade(case_dir: Path, task: Path, name: str, candidate: dict[str, Any]) -> bool | None:
    """Grade one candidate with the task's own verifier, as the local controls do."""
    folder = case_dir / "cue-controls" / name
    folder.mkdir(parents=True)
    for source in ("tests/verify.py", "tests/refund_scorer.py", "environment/contract.json"):
        if not (task / source).is_file():
            return None
        (folder / Path(source).name).write_bytes((task / source).read_bytes())
    (folder / "submission.json").write_text(json.dumps(candidate))
    subprocess.run(
        [sys.executable, "-B", "verify.py", "--contract", "contract.json", "--submission", "submission.json"]
        + ["--output", "logs"],
        cwd=folder,
        capture_output=True,
        timeout=10,
        env={"PATH": os.defpath, "HOME": str(folder), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    checks = folder / "logs/checks.json"
    return json.loads(checks.read_bytes()).get("accepted") if checks.is_file() else None


def cue_controls(case_dir: Path, task: Path, instruction: str) -> dict[str, bool | None]:
    """Run the four CREATE-02 cue classes through the verifier. Returns accepted per class."""
    reference = next(c for c in json.loads((EXAMPLE / "controls.json").read_bytes())["cases"] if c["id"] == "reference")
    contract_path = task / "environment/contract.json"
    order = json.loads(contract_path.read_bytes())["sources"]["order"] if contract_path.is_file() else {}

    def with_amount(amount: int) -> dict[str, Any]:
        text = json.dumps(reference["candidate"]).replace('"amount_cents": 8000', f'"amount_cents": {amount}')
        return json.loads(text)

    cue = re.search(r"expected refund is (\d+) cents", instruction)
    renamed = json.loads(json.dumps(reference["candidate"]).replace('"new-0"', '"refund-renamed"'))
    candidates = {
        # Uses only a public cue for the amount, without computing it.
        "cue_only": with_amount(int(cue.group(1)) if cue else 0),
        # Semantics-preserving rename of the solver-chosen refund ID.
        "nuisance_swap": renamed,
        # Follows the order's merchandise total, which ignores discount, tax and prior refunds.
        "misleading_cue": with_amount(order.get("merchandise_cents", 0)),
        # Same events serialized with reversed key order.
        "style_only": json.loads(json.dumps(reference["candidate"], sort_keys=True)),
    }
    return {name: grade(case_dir, task, name, candidate) for name, candidate in candidates.items()}


CLIENT = (
    "import json,socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(3); "
    "s.connect('ledger.sock'); s.sendall((sys.argv[1]+'\\n').encode()); print(s.makefile('rb').readline().decode())"
)


def episode(folder: Path, record: Path, candidate: dict[str, Any]) -> None:
    """Start the task's ledger service fresh on ``record`` and perform one candidate's actions."""
    env = {"PATH": os.defpath, "HOME": str(folder), "PYTHONDONTWRITEBYTECODE": "1"}
    server = subprocess.Popen(
        [sys.executable, "-B", "server.py", "--contract", "contract.json", "--socket", "ledger.sock"]
        + ["--record", str(record)],
        cwd=folder,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not (folder / "ledger.sock").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        requests = [{"operation": "act", "event": event} for event in candidate["events"]]
        for request in [*requests, {"operation": "complete", "success": candidate["success"]}]:
            subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", CLIENT, json.dumps(request)],
                cwd=folder,
                env=env,
                capture_output=True,
                timeout=5,
            )
    finally:
        server.terminate()
        server.wait(timeout=5)
        (folder / "ledger.sock").unlink(missing_ok=True)


def reset_probe(case_dir: Path, task: Path) -> dict[str, bool | None]:
    """ENV-03-S01: grade the target alone and after a predecessor episode on the same record."""
    reference = next(c for c in json.loads((EXAMPLE / "controls.json").read_bytes())["cases"] if c["id"] == "reference")
    folder = case_dir / "reset-probe"
    folder.mkdir()
    sources = (
        "environment/ledger.py",
        "environment/server.py",
        "environment/contract.json",
        "tests/verify.py",
        "tests/refund_scorer.py",
    )
    if not all((task / source).is_file() for source in sources):
        return {"alone": None, "after": None}
    for source in sources:
        (folder / Path(source).name).write_bytes((task / source).read_bytes())
    result = {}
    for name, episodes in (("alone", 1), ("after", 2)):
        record = folder / f"{name}.json"
        for _ in range(episodes):
            episode(folder, record, reference["candidate"])
        logs = folder / f"{name}-logs"
        subprocess.run(
            [sys.executable, "-B", "verify.py", "--contract", "contract.json", "--record", str(record)]
            + ["--output", str(logs)],
            cwd=folder,
            capture_output=True,
            timeout=10,
            env={"PATH": os.defpath, "HOME": str(folder), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        checks = logs / "checks.json"
        result[name] = json.loads(checks.read_bytes()).get("accepted") if checks.is_file() else None
    return result


AUTHORITY_RECORD = ROOT / "evals/scorer-controls"
RECORD_REVIEWER = "human:recorded-review-2026-11-19"


def authority_check(case_dir: Path) -> dict[str, Any]:
    """Run the authority check on the refund authority record, with this case's reference verifier result."""
    work = case_dir / "authority"
    (work / "evals/scorer-controls").mkdir(parents=True)
    for name in ("authority.json", "contract.json"):
        (work / "evals/scorer-controls" / name).write_bytes((AUTHORITY_RECORD / name).read_bytes())
    artifacts = [
        artifact_record(
            work,
            "plan",
            "evals/scorer-controls/authority.json",
            "construction",
            revision="authority-record",
            source="authority-record",
        ),
        artifact_record(
            work,
            "spec",
            "evals/scorer-controls/contract.json",
            "specification",
            revision="authority-record",
            source="authority-record",
        ),
    ]
    selected = {"plan_artifact_id": "plan"}
    result = case_dir / "controls/reference/logs/checks.json"
    if result.is_file():
        (work / "result.json").write_bytes(result.read_bytes())
        artifacts.append(artifact_record(work, "result", "result.json", "result", revision="case", source="verifier"))
        selected["result_artifact_id"] = "result"
    report = check_authority(work, artifacts, selected)
    return {
        "requirements": {row["id"]: row["status"] for row in report["requirements"]},
        "checks": {row["id"]: row["status"] for row in report["checks"]},
    }


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
    scan = scan_task(task.parent, {"task": "task"})
    overlaps = sorted(
        {
            (f["kind"], f["private"], f["public"], line)
            for f in scan["findings"]
            for line in (
                [(task.parent / f["public"]).read_text().splitlines()[f["lines"][0] - 1].strip()]
                if f["lines"]
                else [""]
            )
        }
    )
    instruction = (task / "instruction.md").read_text()
    dockerfile = (task / "environment/Dockerfile").read_text()
    verify = (task / "tests/verify.py").read_text()
    contract_path = task / "environment/contract.json"
    return {
        "task_sha256": sha(json.dumps(files, sort_keys=True).encode()),
        "files": files,
        "task_toml": tomllib.loads((task / "task.toml").read_text()),
        "dockerfile": {
            "from": field(r"^FROM (\S+)", dockerfile),
            "copies": re.findall(r"^COPY (.+?) /app", dockerfile, re.M),
            "entrypoint": field(r"^ENTRYPOINT (.+)$", dockerfile),
        },
        "grader": field(r'^GRADER = "(\w+)"', verify),
        "task_dir": str(task),
        "instruction": instruction,
        "overlaps": overlaps,
        "cue_controls": cue_controls(case_dir, task, instruction),
        "reset_probe": reset_probe(case_dir, task),
        "authority": authority_check(case_dir),
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


def field(pattern: str, text: str) -> str:
    """First group of a required line in a task file."""
    found = re.search(pattern, text, re.M)
    if found is None:
        raise ValueError(f"task file lacks a line matching {pattern}")
    return found.group(1)


def robust(ident: str, observed: Mapping[str, bool | None], evidence: str, actor: str) -> list[dict[str, Any]]:
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
                "corroborating_actor": AUTHOR if evidence == "no-independent-corroboration" else actor,
                "source_identity": f"sim-{ident}-{role}",
                "execution": "unrun" if unrun else "completed",
                "scope": "same-task-revision",
            }
        )
    return rows


def derive(
    o: dict[str, Any],
    base: dict[str, Any],
    prior: dict[str, Any],
    evidence: str | None,
    native: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict, dict]:
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
                # The refund authority record resolves each scored condition to its specification.
                "authority": o["authority"]["requirements"].get(check) == "resolved" and contract_ok and check or None,
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
    # The task states that reference solutions are not solver-readable; overlap that is not reviewed
    # as benign contradicts that statement.
    f["AUDIT-05-B"] = {
        "exposure_inventory": [
            {
                "id": f"{private}->{public}:{index}",
                "exposure": "public",
                "overlap": "verified" if overclaim or kind == "full" or line not in REVIEWED_OVERLAP else "none",
                "disclosed": "no" if overclaim else "yes",
                "statement": "overclaims"
                if overclaim or kind == "full" or line not in REVIEWED_OVERLAP
                else "within_support",
                "sensitivity": "computed",
            }
            for index, (kind, private, public, line) in enumerate(o["overlaps"])
        ]
    }
    sim["AUDIT-05-B"] = "Overlap comes from the real private/public scan; benign lines are reviewer dispositions."
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
    probe = o["reset_probe"]
    carried = evidence == "state-carryover"
    alone = None if probe["alone"] is None else str(probe["alone"])
    after = None if probe["after"] is None else str(probe["after"])
    if carried and alone is not None:
        after = str(not probe["alone"])
    f["ENV-03-S01"] = {
        "isolation_surfaces": [
            {
                "id": "ledger-record",
                "sharing": "isolated",
                "reset_verified": "unrun" if alone is None else "yes" if alone == after else "no",
                "target_alone": alone,
                "target_after": after,
                "consequence": "none" if alone == after else "credit_change",
            }
        ]
    }
    sim["ENV-03-S01"] = "Real ledger service episodes; parallel and delayed-effect corroboration is simulated."
    cue = {
        "qualified": "yes",
        "health": "healthy",
    }
    accepted = o["cue_controls"]
    f["CREATE-02"] = {
        "cue_controls": [
            {
                "id": cls,
                "cue_class": cls,
                **cue,
                "health": "unrun" if accepted[cls] is None else "healthy",
                # Cue-only and misleading-cue controls must fail; semantics-preserving swaps must pass.
                "outcome": "bypass"
                if (evidence == "cue-bypass" and cls == "style_only")
                or (cls in ("cue_only", "misleading_cue") and accepted[cls])
                else "confounded"
                if cls in ("nuisance_swap", "style_only") and accepted[cls] is False
                else "no_bypass",
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
    sim["CREATE-02"] = "Cue controls are real verifier runs; companion families are simulated."
    sim["CREATE-02-SUITE"] = "Cue controls on two companion families are simulated."

    actors: dict[str, str] = {}
    native_outcomes: dict[str, bool | None] = {}
    if native is not None and o["task_sha256"] == base["task_sha256"]:
        # Native Harbor panels ran on these exact task bytes; their admitted controls replace local ones.
        primary, native_outcomes = native_facts(native)
        f["AUDIT-03"] = primary["AUDIT-03"]
        isolation = primary["ENV-03-S01"]["isolation_surfaces"]
        if carried:
            isolation[0].update(target_after=str(not float(isolation[0]["target_alone"])), consequence="credit_change")
        f["ENV-03-S01"] = {"isolation_surfaces": isolation}
        f["provenance.solver-tool-access"]["tool_probes"] += primary["provenance.solver-tool-access"]["tool_probes"]
        if run is not None:
            for row in f["provenance.observed-configuration"]["run_settings"]:
                row["run_identity"] = primary["run_identity"]
        freeze = json.loads(FREEZE.read_text())["author"]
        actors["AUDIT-03"] = f"heldout-author:{freeze['kind']}:{freeze['model']}"
        sim["AUDIT-03"] = "Native Harbor attack, access and boundary panels; held-out attacks from the frozen set."
        sim["ENV-03-S01"] = "Native Harbor sentinel/target/reset episodes; parallel and delayed effects are simulated."
        sim["provenance.solver-tool-access"] = "Native Harbor read-boundary probes plus simulated socket calls."
        sim["provenance.observed-configuration"] = "Run identity from the native Harbor panels."
    corroboration = {
        "AUDIT-03": {
            "heldout_attack": native_outcomes.get("heldout_attack", all(matches(n) for n in AUDIT03_HELDOUT)),
            "valid_work": native_outcomes.get("valid_work", matches("split-alternative")),
        },
        "ENV-03-S01": {"heldout_order": native_outcomes.get("heldout_order", True)},
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
            "scoring_contract": all(matches(n) for n in ctl)
            and all(status == "observed" for status in o["authority"]["checks"].values()),
        },
    }
    # The refund authority record carries a recorded human review of the scoring contract.
    for ident in ("task-contract.scoring-authority", "task-contract.discoverable-requirements"):
        actors.setdefault(ident, RECORD_REVIEWER)
    sim["task-contract.scoring-authority"] = (
        "Refund authority record checked by rubric_authority; recorded human review."
    )
    for ident in f:
        name, _ = ROBUSTNESS_POLICIES[ident]
        f[ident][name] = robust(ident, corroboration.get(ident, {}), evidence or "", actors.get(ident, INDEPENDENT))
    f["METHOD-J1"] = method_facts(f, evidence)
    sim["METHOD-J1"] = "Corroborating actors recorded from the evidence above; no method audit has run."
    sim["*"] = "Corroborating controls are simulated premises unless derived from controls above."
    return f, sim


def method_facts(f: dict[str, dict[str, Any]], evidence: str | None) -> dict[str, Any]:
    """METHOD-J1: one row per corroborating actor in the evidence, as the record describes it."""
    freeze = json.loads(FREEZE.read_text())["author"]
    profiles = {
        INDEPENDENT: ("agent", "shared_author", "exposed", "unknown"),  # Claude wrote this simulation
        RECORD_REVIEWER: ("human", "shared_author", "exposed", "qualified"),  # the task owner's recorded review
        AUTHOR: ("simulated", "shared_author", "exposed", "unknown"),
    }
    actors = sorted(
        {
            row["corroborating_actor"]
            for criterion in f.values()
            for rows in criterion.values()
            for row in rows or []
            if isinstance(row, dict) and "corroborating_actor" in row
        }
    )
    rows = []
    for actor in actors:
        kind, independence, blindness, qualification = profiles.get(
            actor,
            # Held-out attacks: a separate session that designed cases before opening the scorer.
            ("agent", "independent", "blind", "qualified" if freeze["independence_qualified"] else "unqualified"),
        )
        rows.append(
            {
                "id": actor,
                "actor_kind": kind,
                "independence": independence,
                "blindness": blindness,
                "claimed": "independent"
                if evidence == "undisclosed-self-corroboration" and actor == INDEPENDENT
                else "none",
                "disclosure": "disclosed",
                "qualification": qualification,
            }
        )
    return {"corroboration_methods": rows, "method_audit": robust("METHOD-J1", {"method_audit": None}, "", INDEPENDENT)}


def build(case_dir: Path, facts: dict[str, dict[str, Any]], o: dict[str, Any]) -> dict[str, Any]:
    observations = case_dir / "observations.json"
    write_json(
        observations,
        {
            k: o[k]
            for k in (
                "task_sha256",
                "files",
                "grader",
                "dockerfile",
                "controls",
                "expected",
                "overlaps",
                "cue_controls",
                "reset_probe",
                "authority",
            )
        },
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
    selection = str(case_dir / "selection.json")
    scored = table(case_dir, "--selection", selection, "--output", str(case_dir / "table"), "--title", case_dir.name)
    scored = scored if scored.returncode in (0, 1) else None
    if scored is None or table(case_dir, "--verify-existing", str(case_dir / "table")).returncode not in (0, 1):
        raise ValueError(f"rubric_table rejected or could not replay {case_dir.name}")
    return json.loads((case_dir / "table/assessment.json").read_text())["report"]


def report_html(out: Path, scores: dict[str, dict[str, int | None]], summary: dict[str, Any]) -> str:
    """One page: the base table, every mutation against the base, and the full score grid."""
    import html

    definitions = canonical_definitions()
    rubric = summary["rubric_criteria"]
    columns = rubric + summary["method_criteria"]
    definitions = {**definitions, **method_definitions()}
    esc = html.escape

    def cell(score: int | None) -> str:
        label = "U" if score is None else str(score)
        return f'<td class="s s{label}">{label}</td>'

    def total(row: dict[str, int | None]) -> int:
        return sum(row[i] or 0 for i in rubric)

    base = scores.get("base", {})
    premises = json.loads((out / "base/simulated-premises.json").read_text()) if base else {}
    base_rows = "".join(
        f"<tr><td><code>{esc(i)}</code></td><td>{esc(definitions[i]['title'])}</td>"
        f"<td>{esc(definitions[i].get('level', 'task'))}</td>{cell(base.get(i))}"
        f"<td>{esc(premises.get(i, 'Derived from the task bytes and real verifier runs.'))}</td></tr>"
        for i in columns
    )
    mutation_rows = ""
    for case, row in scores.items():
        if case == "base":
            continue
        kind, text = describe(case)
        drops = ", ".join(
            f"<code>{esc(i)}</code> {'U' if row[i] is None else row[i]}" for i in columns if row[i] != base.get(i)
        )
        lower = "yes" if summary["mutations_below_base"].get(case) else "NO"
        mutation_rows += (
            f'<tr><td><a href="{esc(case)}/table/results.html">{esc(case)}</a></td><td>{kind}</td>'
            f"<td>{esc(text)}</td><td>{total(row)}/{3 * len(rubric)}</td><td>{drops}</td><td>{lower}</td></tr>"
        )
    grid_head = "".join(f'<th title="{esc(i)}">{n + 1}</th>' for n, i in enumerate(rubric)) + "<th>M</th>"
    grid = "".join(
        f'<tr><td><a href="{esc(case)}/table/results.html">{esc(case)}</a></td><td>{total(row)}</td>'
        + "".join(cell(row[i]) for i in columns)
        + "</tr>"
        for case, row in scores.items()
    )
    key = "".join(f"<li><code>{n + 1}</code> {esc(i)}</li>" for n, i in enumerate(rubric)) + (
        "<li><code>M</code> METHOD-J1, not in the total</li>"
    )
    lowered = sum(summary["mutations_below_base"].values())
    style = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#666;--line:#ddd;--s3:#d9f2e3;--s2:#fff3c4;--s1:#ffe0c2;--s0:#ffd0d0;--su:#e8e8ee}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#a0a0a8;--line:#333;
--s3:#1f4d33;--s2:#5a4b12;--s1:#5c3414;--s0:#5e1f1f;--su:#2c2c34}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:24px auto;max-width:1200px;padding:0 16px}
h1{margin-bottom:4px}.lead{color:var(--muted)}table{border-collapse:collapse;width:100%;margin:12px 0 28px}
td,th{border:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}
.s{text-align:center;font-weight:600}.s3{background:var(--s3)}.s2{background:var(--s2)}.s1{background:var(--s1)}
.s0{background:var(--s0)}.sU{background:var(--su)}.scroll{overflow-x:auto}ul.key{columns:2;color:var(--muted)}a{color:inherit}
"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Rubric simulation</title>
<style>{style}</style></head><body>
<h1>Rubric simulation</h1>
<p class="lead">Base: <strong>{total(base)}/{3 * len(rubric)}</strong> on {len(rubric)} criteria.
METHOD-J1, the qualification of the corroboration behind those scores, is
<strong>{"U" if base.get("METHOD-J1") is None else base.get("METHOD-J1")}</strong>.
{lowered} of {len(scores) - 1} mutations score lower than the base. Every score comes from
<code>rubric_table.py</code>.</p>
<h2>Base task</h2>
<table><thead><tr><th>Criterion</th><th>Title</th><th>Level</th><th>Score</th><th>Evidence basis</th></tr></thead>
<tbody>{base_rows}</tbody></table>
<h2>Mutations</h2>
<table><thead><tr><th>Case</th><th>Kind</th><th>Change</th><th>Total</th><th>Criteria that changed</th>
<th>Below base</th></tr></thead><tbody>{mutation_rows}</tbody></table>
<h2>All scores</h2>
<div class="scroll"><table><thead><tr><th>Case</th><th>Total</th>{grid_head}</tr></thead><tbody>{grid}</tbody></table></div>
<ul class="key">{key}</ul>
</body></html>
"""


def main() -> int:
    # Native panels fingerprint their source trees; bytecode written mid-run would change them.
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES)
    native_mode = parser.add_mutually_exclusive_group()
    native_mode.add_argument(
        "--collect-native", action="store_true", help="Run the native Harbor panels in Docker for the base task."
    )
    native_mode.add_argument("--native", type=Path, help="Reuse native panels collected by an earlier run.")
    parser.add_argument("--docker-plugins", type=Path, help="Docker CLI plugin directory (Compose, Buildx).")
    args = parser.parse_args()
    out = args.output.absolute()
    if out.is_relative_to(ROOT):
        parser.error("output must be outside the checkout")
    out.mkdir(mode=0o700)
    rubric = list(canonical_definitions())
    columns = rubric + ["METHOD-J1"]
    native = None
    if args.collect_native:
        from native import collect

        native = collect(out / "native", args.docker_plugins)
    elif args.native:
        from native import load

        native = load(args.native.absolute())
    base_obs = observe(out / "_base-observation", None)
    prior = observe(out / "_prior-exploit-revision", "completion-only")
    scores: dict[str, dict[str, int | None]] = {}
    for case in args.case or CASES:
        case_dir = out / case
        case_dir.mkdir()
        mutation = case if case in TASK_MUTATIONS else None
        evidence = case if case in EVIDENCE_MUTATIONS else None
        o = observe(case_dir, mutation)
        facts, sim = derive(o, base_obs, prior, evidence, native)
        write_json(case_dir / "simulated-premises.json", sim, sort_keys=True)
        report = build(case_dir, facts, o)
        rows = report["items"]
        scores[case] = {ident: rows[ident]["score"] for ident in columns}

    def rank(score: int | None) -> int:
        return -1 if score is None else score  # Unassessed ranks below every established score

    base = scores.get("base")
    summary = {
        "scorer": "skills/eval-author-rubric/scripts/measure.py, criterion_evidence synthetic scope",
        "rubric_criteria": rubric,
        "method_criteria": ["METHOD-J1"],
        "scores": scores,
        "base_perfect": base is not None and all(base[i] == 3 for i in rubric),
        "mutations_below_base": {
            case: base is not None
            and all(rank(row[i]) <= rank(base[i]) for i in columns)
            and any(rank(row[i]) < rank(base[i]) for i in columns)
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
    (out / "summary.html").write_text(report_html(out, scores, summary))
    print("\n".join(lines))
    print(json.dumps({k: summary[k] for k in ("base_perfect", "mutations_below_base")}, indent=1))
    return 0 if summary["base_perfect"] and all(summary["mutations_below_base"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
