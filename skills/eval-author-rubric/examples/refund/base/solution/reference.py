# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path


def solve(contract: dict) -> dict:
    request = contract["request"]
    sources = contract["sources"]
    order = sources["order"]
    policy = sources["policy"]
    prior = sum(
        row["amount_cents"] for row in sources["initial_state"]["refunds"] if row["order_id"] == request["order_id"]
    )
    amount = order["merchandise_cents"] - order["discount_cents"] - prior
    if policy["refund_tax"]:
        amount += order["tax_cents"]
    events = []
    if amount:
        if amount >= policy["approval_threshold_cents"]:
            events.append(
                {
                    "kind": "approve",
                    **{k: request[k] for k in ("run_id", "customer_id", "order_id")},
                    "amount_cents": amount,
                    "actor": policy["approvers"][0],
                }
            )
        used = {row["id"] for row in sources["initial_state"]["refunds"]}
        index = 0
        while f"refund-{index}" in used:
            index += 1
        refund_id = f"refund-{index}"
        events.extend(
            [
                {"kind": "refund", "refund": {"id": refund_id, **request, "amount_cents": amount}},
                {"kind": "ledger", "entries": [{"refund_id": refund_id, "amount_cents": amount}]},
            ]
        )
    return {"events": events, "success": True}


if __name__ == "__main__":
    candidate = solve(json.loads(Path(sys.argv[1]).read_text()))
    if sys.argv[2] == "--socket":
        import socket

        requests = [{"operation": "act", "event": event} for event in candidate["events"]]
        requests.append({"operation": "complete", "success": candidate["success"]})
        for request in requests:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5)
                client.connect(sys.argv[3])
                client.sendall((json.dumps(request) + "\n").encode())
                response = json.loads(client.makefile("rb").readline())
                if response != {"ok": True}:
                    raise RuntimeError("ledger_action_failed")
    else:
        Path(sys.argv[2]).write_text(json.dumps(candidate) + "\n")
