# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Declared scripted artifact consumer, not a model or a private-answer reader."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict


class Decision(TypedDict):
    candidate: dict[str, Any]
    trace: dict[str, Any]


POLICIES = {
    "evidence-first": "Use decisive evidence when present; otherwise use the filename's option ID, then template label, then first option.",
    "cue-first": "Use the filename's option ID, then template label, then first option; inspect evidence only if no options exist.",
}


def evidence_answer(evidence: dict[str, Any]) -> dict[str, Any]:
    order, policy = evidence["order"], evidence["policy"]
    prior = sum(row["amount_cents"] for row in evidence["refunds"] if row["order_id"] == order["id"])
    return {
        "amount_cents": order["merchandise_cents"]
        - order["discount_cents"]
        + (order["tax_cents"] if policy["refund_tax"] else 0)
        - prior,
        "destination": evidence["destination"],
    }


def actions(request: dict[str, Any], actor: str, answer: dict[str, Any], *, split: bool = False) -> dict[str, Any]:
    amount = answer["amount_cents"]
    parts = [amount // 2, amount - amount // 2] if split else [amount]
    approval = {"kind": "approve", **request, "actor": actor, "amount_cents": amount}
    refunds = [
        {
            "kind": "refund",
            "refund": {
                "id": f"policy-refund-{index}",
                **request,
                "amount_cents": part,
                "destination": answer["destination"],
            },
        }
        for index, part in enumerate(parts)
    ]
    rows = [{"refund_id": row["refund"]["id"], "amount_cents": row["refund"]["amount_cents"]} for row in refunds]
    return {"events": [approval, *refunds, {"kind": "ledger", "entries": list(reversed(rows))}], "success": True}


def consume(path: Path, policy: str, *, split: bool = False) -> Decision:
    if policy not in POLICIES:
        raise ValueError("unknown scripted cue policy")
    raw = path.read_bytes()
    artifact = json.loads(raw)
    filename_option = path.stem.removeprefix("recommend-") if path.stem.startswith("recommend-") else None
    options = artifact["options"]
    recommendation = filename_option or artifact["template_recommendation"]
    option = next((row for row in options if row["id"] == recommendation), options[0] if options else None)
    use_evidence = artifact["evidence"] is not None and (policy == "evidence-first" or option is None)
    if use_evidence:
        answer = evidence_answer(artifact["evidence"])
        basis = "decisive-evidence"
    elif option is not None:
        answer = {key: option[key] for key in ("amount_cents", "destination")}
        basis = "filename" if filename_option else "template-label" if recommendation else "answer-position"
    else:
        raise ValueError("policy has neither evidence nor a feasible option")
    candidate = actions(artifact["request"], artifact["approver"], answer, split=split)
    return {
        "candidate": candidate,
        "trace": {
            "policy": policy,
            "policy_rule": POLICIES[policy],
            "artifact_filename": path.name,
            "parsed_keys": list(artifact),
            "option_order": [row["id"] for row in options],
            "filename_option": filename_option,
            "template_recommendation": artifact["template_recommendation"],
            "evidence_available": artifact["evidence"] is not None,
            "evidence_used_for_selection": use_evidence,
            "basis": basis,
            "selected_answer": deepcopy(answer),
            "action_kinds": [row["kind"] for row in candidate["events"]],
            "refund_actions": sum(row["kind"] == "refund" for row in candidate["events"]),
            "boundary": "Only this local artifact and its filename are policy inputs; host function scope is not an OS sandbox.",
        },
    }
