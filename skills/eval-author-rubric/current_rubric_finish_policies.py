# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

POLICIES = ("evidence", "first-template", "lexical")
FAMILIES = ("refund", "interval-reservation", "dependency-release")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _family_choice(policy: str, family: str, case: dict) -> tuple[Any, list]:
    if policy == "first-template":
        return case["options"][0]["id"], [{"operation": "first_option", "index": 0}]
    if policy == "lexical":
        ids = [option["id"] for option in case["options"]]
        return min(ids), [{"operation": "lexical_min", "ids": ids}]
    evidence = "occupied" if family == "interval-reservation" else "states"
    if case[evidence] is None:
        return None, [{"operation": "missing_evidence", "field": evidence}]
    good, steps = [], []
    for option in case["options"]:
        if family == "interval-reservation":
            left, right = option["interval"]
            tests = [right <= start or end <= left for start, end in case["occupied"]]
        else:
            tests = [case["states"].get(key) == "passed" for key in option["requires"]]
        eligible = all(tests)
        steps.append({"operation": "eligibility", "option": option["id"], "comparisons": tests, "eligible": eligible})
        if eligible:
            good.append(option["id"])
    if len(good) != 1:
        raise ValueError("nonunique_public_instance")
    return good[0], steps


def _refund_candidate(policy: str, case: dict) -> tuple[dict, list]:
    request, sources = case["request"], case["sources"]
    order, rules, state = sources["order"], sources["policy"], sources["initial_state"]
    if policy == "evidence":
        prior = sum(row["amount_cents"] for row in state["refunds"] if row["order_id"] == request["order_id"])
        amount = order["merchandise_cents"] - order["discount_cents"] - prior
        if rules["refund_tax"]:
            amount += order["tax_cents"]
        steps = [{"operation": "remaining_balance", "prior_cents": prior, "amount_cents": amount}]
    elif policy == "first-template":
        amount = 8000
        steps = [{"operation": "stale_template_amount", "amount_cents": amount}]
    else:
        field = min(key for key in order if key.endswith("_cents"))
        amount = order[field]
        steps = [{"operation": "lexical_first_amount_field", "field": field, "amount_cents": amount}]
    events = []
    if amount > 0:
        if amount >= rules["approval_threshold_cents"]:
            events.append(
                {
                    "kind": "approve",
                    **{key: request[key] for key in ("run_id", "customer_id", "order_id")},
                    "actor": rules["approvers"][0],
                    "amount_cents": amount,
                }
            )
        used = {row["id"] for row in state["refunds"]}
        index = 0
        while f"finish-refund-{index}" in used:
            index += 1
        refund_id = f"finish-refund-{index}"
        events.extend(
            [
                {"kind": "refund", "refund": {"id": refund_id, **request, "amount_cents": amount}},
                {"kind": "ledger", "entries": [{"refund_id": refund_id, "amount_cents": amount}]},
            ]
        )
    return {"events": events, "success": True}, steps


def run_policy(policy: str, family: str, raw_input: bytes) -> dict:
    if policy not in POLICIES or family not in FAMILIES:
        raise ValueError("unsupported_policy_or_family")
    if len(raw_input) > 65536:
        raise ValueError("input_limit")
    case = json.loads(raw_input)
    if family == "refund":
        candidate, steps = _refund_candidate(policy, case)
        actions = [{"operation": "act", "event": event} for event in candidate["events"]]
        actions.append({"operation": "complete", "success": candidate["success"]})
    else:
        choice, steps = _family_choice(policy, family, case)
        candidate = {"choice": choice, "success": True}
        actions = [{"operation": "write_candidate", "candidate": candidate}]
    return {
        "policy": policy,
        "family": family,
        "candidate": candidate,
        "candidate_sha256": digest(candidate),
        "reads": [
            {
                "channel": "public_input_bytes",
                "sha256": hashlib.sha256(raw_input).hexdigest(),
                "bytes": len(raw_input),
                "scope": "whole_document_parsed_not_restricted_access",
            }
        ],
        "decisions": steps,
        "actions": actions,
        "execution": "planned_not_submitted",
    }


def execute_policy(
    policy: str, family: str, input_path: Path, *, candidate_path: Path | None = None, socket_path: Path | None = None
) -> dict:
    trace = run_policy(policy, family, input_path.read_bytes())
    trace["action_receipts"] = []
    if family == "refund":
        if socket_path is None or candidate_path is not None:
            raise ValueError("refund_requires_action_socket_only")
        for action in trace["actions"]:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(str(socket_path))
                client.sendall(canonical(action) + b"\n")
                response = json.loads(client.makefile("rb").readline(65537))
            trace["action_receipts"].append({"request": action, "response": response})
            if response != {"ok": True}:
                trace["execution"] = "action_rejected"
                return trace
    else:
        if candidate_path is None or socket_path is not None:
            raise ValueError("family_requires_candidate_file_only")
        raw = canonical(trace["candidate"]) + b"\n"
        candidate_path.write_bytes(raw)
        trace["action_receipts"].append(
            {"operation": "write_candidate", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    trace["execution"] = "submitted"
    return trace


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic public-input policy; no remote calls")
    parser.add_argument("policy", choices=POLICIES)
    parser.add_argument("family", choices=FAMILIES)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args()
    trace = execute_policy(args.policy, args.family, args.input, candidate_path=args.candidate, socket_path=args.socket)
    with args.trace.open("x") as handle:
        json.dump(trace, handle, sort_keys=True)
        handle.write("\n")
    return 0 if trace["execution"] == "submitted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
