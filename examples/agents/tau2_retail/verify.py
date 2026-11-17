# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
from pathlib import Path

ORDER_ID = "#W5918442"
REASON = "ordered by mistake"
WRITE_TOOLS = {
    "cancel_pending_order",
    "exchange_delivered_order_items",
    "modify_pending_order_address",
    "modify_pending_order_items",
    "modify_pending_order_payment",
    "modify_user_address",
    "return_delivered_order_items",
}


def canonical(value: object) -> object:
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def check(transcript: dict, final: dict, seed: dict) -> dict[str, bool]:
    messages = transcript["messages"]
    calls = [(index, call) for index, message in enumerate(messages) for call in message.get("tool_calls", [])]
    writes = [(index, call) for index, call in calls if call["name"] in WRITE_TOOLS]
    correct_call = (
        len(writes) == 1
        and writes[0][1]["name"] == "cancel_pending_order"
        and writes[0][1]["arguments"] == {"order_id": ORDER_ID, "reason": REASON}
    )
    confirmed = False
    successful = False
    if correct_call:
        index, call = writes[0]
        customer = [(i, message) for i, message in enumerate(messages[:index]) if message["role"] == "user"]
        if customer:
            confirmation_index, confirmation = customer[-1]
            proposals = [
                message
                for message in messages[:confirmation_index]
                if message["role"] == "assistant" and message.get("content") and not message.get("tool_calls")
            ]
            confirmed = bool(
                proposals
                and confirmation["content"].strip().lower() == "yes"
                and ORDER_ID in proposals[-1]["content"]
                and "cancel" in proposals[-1]["content"].lower()
            )
        successful = any(
            message["role"] == "tool" and message.get("tool_call_id") == call["id"] and message.get("error") is False
            for message in messages[index + 1 :]
        )
    expected = copy.deepcopy(seed)
    order = expected["orders"][ORDER_ID]
    order["status"] = "cancelled"
    order["cancel_reason"] = REASON
    order["payment_history"] += [
        {**payment, "transaction_type": "refund"} for payment in list(order["payment_history"])
    ]
    return {
        "several_customer_turns": sum(message["role"] == "user" for message in messages) >= 3,
        "correct_write": correct_call,
        "confirmation_before_write": confirmed,
        "write_succeeded": successful,
        "independent_database_state": canonical(final) == canonical(expected),
    }


def main() -> int:
    try:
        result = check(
            json.loads(Path("/app/output/transcript.json").read_text()),
            json.loads(Path("/app/state/db.json").read_text()),
            json.loads(Path("/tests/seed.json").read_text()),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return 2
    output = Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "reward.txt").write_text("1" if all(result.values()) else "0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
