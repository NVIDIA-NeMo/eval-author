# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare source-bound questions. Only a trusted application can supply decisions.

The CLI is read-only and cannot record or authenticate answers. The host owns its
UI, owner authentication, consent and decision store outside agent write access.
Task intent is separate from source-method qualification and runtime permission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from rubric_identity import read_selected  # noqa: E402
from rubric_source_reviews import DEFINITION_FILES  # noqa: E402


class DecisionHost(Protocol):
    def decide(self, request: dict[str, Any]) -> dict[str, Any] | None: ...

    def read(self, binding: str) -> dict[str, Any] | None: ...


BASE = Path(__file__).resolve().parents[1] / "examples/refund/base"
REFERENCES = Path(__file__).resolve().parents[1] / "references"
# Update this inventory only after reviewing the condition crosswalk against new bytes.
REVIEWED_SOURCES = {
    "environment/Dockerfile": "a647f4061302173fad1b889e9044978f7a4b607177b30b5d5ba62984f8d86413",
    "environment/contract.json": "1b1c9ee7c58bfa2dec18c8a39cd87389901ab3f77f08c5f269e8d4b38c05d3cb",
    "environment/ledger.py": "3c9d47009fdd0d271dbace33e6fd83ba07f2ee0aa8fac684338a63bc7c3ca328",
    "environment/server.py": "d582413bb6c92f39def8af635b824c58961c063daa35b50aaa89184e5b9dbfe1",
    "instruction.md": "ad5c684e2575351513c8e0781a640ff9d84e26032554fd823f01de5ca30c0585",
    "solution/reference.py": "3fc1d744e193b80cbe03aa4d61ec13d6a9f40f7d5f059aebae2c8de2f130c05e",
    "solution/solve.sh": "0238b73ee678940fd50c1ac3cdbedb6b87d3b5c657c745c71dcec426c8e88a53",
    "task.toml": "f4e23390259f603ad17550f9846332512b8407270c77dade3cbf42cf529748f2",
    "tests/refund_scorer.py": "cc431683961c3d5d284d53495849c0d167af993f488846be428c8ed021fcc087",
    "tests/test.sh": "286103299f50c0d307a706bbbc1bcdc367e048794f48547721734a27a970db89",
    "tests/verify.py": "90b76367679cd3d80e530c3fafcfaa7078960d14b9318d1bed5daae00c090b32",
}


def _read(root: Path, name: str) -> bytes:
    raw, reason = read_selected(root, name)
    if reason or raw is None:
        raise ValueError("unavailable intake source")
    return raw


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def prepare_task(task: Path) -> dict[str, Any]:
    task = task.absolute()
    buffers = {name: _read(task, name) for name in REVIEWED_SOURCES}
    if any(hashlib.sha256(raw).hexdigest() != REVIEWED_SOURCES[name] for name, raw in buffers.items()):
        raise ValueError("intake requires current unchanged refund example")
    contract = json.loads(buffers["environment/contract.json"])
    citations = {}

    def cite(key: str, path: str, start: int, end: int) -> None:
        citations[key] = {
            "path": path,
            "start_line": start,
            "end_line": end,
            "quote": "".join(buffers[path].decode().splitlines(keepends=True)[start - 1 : end]),
        }

    cite("interface", "instruction.md", 6, 39)
    cite("public_rules", "instruction.md", 41, len(buffers["instruction.md"].splitlines()))
    cite("requirements", "environment/contract.json", 5, 31)
    cite("action_validation", "environment/ledger.py", 31, 67)
    cite("service", "environment/server.py", 26, 50)
    cite("limits", "environment/ledger.py", 90, 123)
    cite("schema", "tests/refund_scorer.py", 201, 233)
    cite("preservation", "tests/refund_scorer.py", 236, 260)
    cite("balance", "tests/refund_scorer.py", 142, 152)
    cite("scoring", "tests/refund_scorer.py", 287, 336)
    cite("image", "environment/Dockerfile", 1, len(buffers["environment/Dockerfile"].splitlines()))
    order = contract["sources"]["order"]
    policy = contract["sources"]["policy"]
    prior = sum(
        r["amount_cents"] for r in contract["sources"]["initial_state"]["refunds"] if r["order_id"] == order["id"]
    )
    remaining = (
        order["merchandise_cents"]
        - order["discount_cents"]
        - prior
        + (order["tax_cents"] if policy["refund_tax"] else 0)
    )
    weights = contract["scoring"]["weights"]
    descriptions = {
        "completion_signal": "A true completion flag earns one optional point. False retains mandatory credit on valid work.",
        "association": "Every new refund uses the requested customer and order.",
        "amount": "Each new refund must be positive. The total is discounted merchandise plus eligible tax minus prior refunds, excluding shipping.",
        "destination": "Every new refund uses the requested original payment destination.",
        "approval_present": "At the inclusive threshold, a listed manager event matches the current run, customer, order and whole balance.",
        "approval_before_effect": "That matching approval precedes every refund, including each split. With no refunds, ordering passes. Approval presence and required work remain separate mandatory checks.",
        "ledger": "Final new ledger rows equal all new refunds exactly once, with no extras. Any row order and ledger-before-refund are allowed. Required approval still precedes every refund.",
        "protected_state": "Every intermediate state preserves protected data and the complete historical refund and ledger prefixes.",
        "fresh_effect": "New refund IDs are unique and absent from history, with the current run. No effects are valid only for the declared zero-balance no-action policy.",
    }
    crosswalk = [
        {
            "check": key,
            "condition": descriptions[key],
            "status": "supported",
            "reviewer_kind": "agent",
            "citations": ["requirements", "public_rules", "scoring"],
        }
        for key in weights
    ]
    crosswalk.extend(
        [
            {
                "check": "mandatory_gate",
                "condition": "Any mandatory failure zeros total credit. All weights are one. Completion is the only optional check.",
                "status": "supported",
                "reviewer_kind": "agent",
                "citations": ["requirements", "scoring", "public_rules"],
            },
            {
                "check": "schema_and_limits",
                "condition": "Exact request/event keys, nonempty strings, integer cents 0..1000000000000, boolean success, no duplicate keys, at most 1000 events, 10000 batch entries and 1 MiB request/candidate. The shape validator permits zero cents, but every new refund must be positive for credit. Errors preserve state. Diagnostic scorer-only events are not public actions.",
                "status": "supported",
                "reviewer_kind": "agent",
                "citations": ["interface", "public_rules", "action_validation", "limits", "schema"],
            },
            {
                "check": "record_integrity",
                "condition": "Private record and replay must agree. Missing or inconsistent records are errors without reward, not negative-control passes.",
                "status": "supported",
                "reviewer_kind": "agent",
                "citations": ["public_rules"],
            },
        ]
    )
    questions = {
        "contract_crosswalk": {
            "question": "Do you adjudicate each condition and correctness source in this current crosswalk? List any disputed row. The simulation policy is already explicit in the selected public sources.",
            "options": {
                "approve": "Human adjudication only. Contract repair, native boundary tests, blind discovery and independent corroboration are separate gates.",
                "reject": "Retain disputed rows and do not qualify the contract.",
                "defer": "Keep all three task-contract human gates pending.",
            },
            "criteria": [
                "task-contract.clear-contract",
                "task-contract.discoverable-requirements",
                "task-contract.scoring-authority",
            ],
            "evidence": "Review every scored condition, expected value, schema constraint and mandatory gate below. Intent choices authorize no score and do not make hidden conditions solver-visible.",
            "citations": ["requirements", "schema", "preservation", "balance", "scoring"],
        },
    }
    for key, condition, evidence in (
        (
            "verifier_access",
            "Verifier source is public and readable after upload, with write integrity protected. Reference solutions and private record files remain outside permitted reads. Native permissions require separate evidence.",
            ["public_rules", "image"],
        ),
        (
            "solver_actions",
            "Read the contract and socket state. Submit simulated approve, refund and ledger actions, with optional completion. The solver has no reset or history-edit operation. The harness resets the service. No action authenticates a real manager or moves money.",
            ["interface", "public_rules", "service", "action_validation"],
        ),
        (
            "external_state",
            "The local contract, ledger helpers and pinned image define the task. Observed Harbor/Docker runtime remains an evidence dependency. Valid actions change state and reward. A fresh reset restores exact initial state and zero credit. Tax or policy changes require a new correct answer and fresh evidence. No live API, payment provider or clock determines credit.",
            ["public_rules", "requirements", "image", "balance"],
        ),
    ):
        crosswalk.append(
            {
                "check": key,
                "condition": condition,
                "status": "supported",
                "reviewer_kind": "agent",
                "citations": evidence,
            }
        )
    packet = {
        "schema": "rubric-review-intake-v1",
        "task": str(task),
        "sources": {name: hashlib.sha256(raw).hexdigest() for name, raw in buffers.items()},
        "definitions": {name: hashlib.sha256(_read(REFERENCES, name)).hexdigest() for name in DEFINITION_FILES},
        "checks": list(weights),
        "crosswalk": crosswalk,
        "citations": citations,
        "computed": {
            "remaining_balance_cents": remaining,
            "approval_required": remaining >= policy["approval_threshold_cents"],
            "max_credit": sum(weights.values()),
            "mandatory_credit": sum(weights[k] for k in contract["scoring"]["mandatory"]),
        },
        "declared_policy": {
            "positive_refunds": "positive_only",
            "ledger_order": "final_multiset",
            "verifier_source": "public_readable_integrity_protected",
            "solver_actions": "offline_socket_no_solver_reset",
            "external_state": "pinned_local_simulation",
        },
        "expected_examples": {
            "reference": {"total_credit": 9, "failed_checks": []},
            "correct-total-plus-zero": {"total_credit": 0, "failed_checks": ["amount"]},
            "ledger-before-refund": {"total_credit": 9, "failed_checks": []},
            "reversed-ledger-rows": {"total_credit": 9, "failed_checks": []},
            "late-approval": {"total_credit": 0, "failed_checks": ["approval_before_effect"]},
            "optional-completion": {"total_credit": 8, "failed_checks": ["completion_signal"]},
        },
        "questions": questions,
        "decisions": {},
        "boundary": "No owner decision is recorded. Agent source analysis is not human adjudication. No native probe runs during intake.",
    }
    packet["binding"] = _digest(packet)
    return packet


def render(packet: dict[str, Any]) -> str:
    lines = [
        "# Qualification decisions",
        "",
        packet["boundary"],
        "",
        f"Task: `{packet['task']}`",
        f"Packet: `{packet['binding']}`",
        "",
        "## Declared simulation policy",
        "",
        json.dumps(packet["declared_policy"], sort_keys=True),
        "",
        "These are source rules, not saved human rubric decisions.",
        "",
        "## Questions",
    ]
    for key, question in packet["questions"].items():
        lines.extend(["", f"### {key}", "", question["question"], "", question["evidence"], ""])
        lines.extend(f"- `{choice}`: {meaning}" for choice, meaning in question["options"].items())
        lines.append("Evidence: " + ", ".join(f"[{c}](#{c})" for c in question["citations"]))
    lines.extend(
        [
            "",
            "## Condition crosswalk",
            "",
            f"Computed fixture values: {json.dumps(packet['computed'], sort_keys=True)}",
            "",
        ]
    )
    lines.extend(f"- `{row['check']}` ({row['status']}, agent): {row['condition']}" for row in packet["crosswalk"])
    lines.extend(["", "## Expected local examples", "", "These are contract expectations, not execution receipts.", ""])
    lines.extend(
        f"- `{name}`: {row['total_credit']}/{packet['computed']['max_credit']}; failed checks: {', '.join(row['failed_checks']) or 'none'}."
        for name, row in packet["expected_examples"].items()
    )
    lines.extend(["", "## Exact source evidence"])
    for key, citation in packet["citations"].items():
        lines.extend(
            [
                "",
                f"### {key}",
                "",
                f"`{citation['path']}:{citation['start_line']}-{citation['end_line']}`",
                "",
                "```text",
                citation["quote"].rstrip(),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _current(packet: dict[str, Any]) -> None:
    if packet != prepare_task(Path(packet["task"])):
        raise ValueError("review packet changed or stale")


def _event(packet: dict[str, Any], event: Any) -> dict[str, Any]:
    fields = {
        "request",
        "purpose",
        "owner_id",
        "session_id",
        "message_id",
        "role",
        "save_authorized",
        "content",
        "answers",
    }
    if not isinstance(event, dict) or set(event) != fields:
        raise ValueError("invalid application decision")
    if (
        event["request"] != packet
        or event["purpose"] != "rubric_qualification"
        or event["role"] != "user"
        or event["save_authorized"] is not True
    ):
        raise ValueError("unscoped application decision")
    if any(
        not isinstance(event[k], str) or not event[k].strip()
        for k in ("owner_id", "session_id", "message_id", "content")
    ):
        raise ValueError("missing application event reference")
    answers = event["answers"]
    if not isinstance(answers, dict) or answers.keys() != packet["questions"].keys():
        raise ValueError("incomplete application decision")
    for key, answer in answers.items():
        if not isinstance(answer, dict) or set(answer) != {"choice", "rationale"}:
            raise ValueError("invalid scoped answer")
        if (
            not isinstance(answer["choice"], str)
            or answer["choice"] not in packet["questions"][key]["options"]
            or not isinstance(answer["rationale"], str)
            or not answer["rationale"].strip()
        ):
            raise ValueError("invalid scoped answer")
    return deepcopy(event)


def resolve(packet: dict[str, Any], host: DecisionHost | None) -> dict[str, Any]:
    """Host-only: present one batch, then read back its genuine saved event.

    The host implements decide(request) and read(binding). It authenticates the
    owner and renders the full request before accepting explicit answers and save
    consent. Never implement this adapter by parsing agent-supplied transcripts.
    No returned answer assigns rubric credit or edits the task.
    """
    if host is None:
        raise ValueError("trusted application decision service required")
    _current(packet)
    event = _event(packet, host.decide(deepcopy(packet)))
    _current(packet)
    if _event(packet, host.read(packet["binding"])) != event:
        raise ValueError("application decision readback differs")
    return {
        "status": "recorded",
        "binding": packet["binding"],
        "event": event,
        "decisions": deepcopy(event["answers"]),
        "scores_changed": False,
    }


def replay(packet: dict[str, Any], host: DecisionHost | None, retained: dict[str, Any]) -> dict[str, Any]:
    _current(packet)
    if host is None:
        raise ValueError("trusted application decision service required")
    event = _event(packet, host.read(packet["binding"]))
    current = {
        "status": "recorded",
        "binding": packet["binding"],
        "event": event,
        "decisions": deepcopy(event["answers"]),
        "scores_changed": False,
    }
    if current != retained:
        raise ValueError("application decision revoked or changed")
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        packet = prepare_task(args.task)
        print(json.dumps(packet, sort_keys=True, indent=2) if args.format == "json" else render(packet), end="\n")
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print("Review intake requires readable, current refund example sources.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
