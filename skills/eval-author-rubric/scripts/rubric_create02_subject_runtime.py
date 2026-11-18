# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Finite refund-decision benchmark; the published scorer has a stale key cache."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def entitlement(evidence: dict[str, Any]) -> dict[str, Any]:
    order = evidence["order"]
    amount = order["merchandise_cents"] - order["discount_cents"]
    if evidence["refund_tax"]:
        amount += order["tax_cents"]
    amount -= sum(evidence["prior_refunds_cents"])
    return {"amount_cents": amount, "destination": evidence["original_payment"]}


def consume(directory: Path, policy: str) -> dict[str, Any]:
    access: list[dict[str, Any]] = []

    def read(name: str) -> dict[str, Any]:
        import hashlib

        raw = (directory / name).read_bytes()
        access.append(
            {"sequence": len(access), "operation": "read", "path": name, "sha256": hashlib.sha256(raw).hexdigest()}
        )
        return json.loads(raw)

    presentation = read("presentation.json")
    if policy == "cue-only":
        option = next(row for row in presentation["options"] if row["id"] == presentation["template_label"])
        answer = {key: option[key] for key in ("amount_cents", "destination")}
    elif policy == "evidence-first":
        answer = entitlement(read("evidence.json"))
    else:
        raise ValueError("unsupported subject policy")
    return {
        "answer": answer,
        "access_trace": access,
        "policy": policy,
        "selection_basis": "template_label" if policy == "cue-only" else "order-policy-payment-evidence",
        "boundary": "Instrumented actual file reads by a deterministic policy; not OS isolation or model inference.",
    }


def published_score(task: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    # Defect under assessment: template ID aliases distinct held-out orders.
    expected = task["development_key_cache"][task["template_id"]]
    checks = {key: answer.get(key) == expected[key] for key in ("amount_cents", "destination")}
    return {"checks": checks, "credit": int(all(checks.values())), "maximum": 1, "health": "ok"}


def evidence_score(task: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    expected = entitlement(task["evidence"])
    checks = {key: answer.get(key) == expected[key] for key in ("amount_cents", "destination")}
    return {"checks": checks, "credit": int(all(checks.values())), "maximum": 1, "health": "ok"}


def feasible(task: dict[str, Any], answer: dict[str, Any]) -> bool:
    return (
        type(answer.get("amount_cents")) is int
        and 0 <= answer["amount_cents"] <= task["authorized_limit_cents"]
        and answer.get("destination") in task["available_destinations"]
    )


def instance(index: int) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "order": {
            "id": f"heldout-order-{index}",
            "merchandise_cents": 12000 + index * 1700,
            "discount_cents": 900 + index * 100,
            "tax_cents": 400 + index * 30,
        },
        "refund_tax": index == 0,
        "prior_refunds_cents": [300 + index * 200],
        "original_payment": f"payment-heldout-{index}",
    }
    development = deepcopy(evidence)
    development["order"]["id"] = f"development-order-{index}"
    development["order"]["merchandise_cents"] -= 2500
    development["original_payment"] = f"payment-development-{index}"
    cached, truth = entitlement(development), entitlement(evidence)
    template_id = f"refund-template-{index}"
    return {
        "template_id": template_id,
        "development_evidence": development,
        "development_key_cache": {template_id: cached},
        "evidence": evidence,
        "authorized_limit_cents": 20000,
        "available_destinations": [cached["destination"], truth["destination"]],
        "presentation": {
            "template_label": "draft-default",
            "options": [{"id": "draft-default", **cached}, {"id": "current-order", **truth}],
            "formatting": "plain",
        },
    }
