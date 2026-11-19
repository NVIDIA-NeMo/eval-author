# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shlex
from copy import deepcopy
from typing import Any

from harbor.agents.base import BaseAgent

CASES = (
    "ledger-first-cent-split",
    "optional-false-cent-split",
    "approval-off-by-one",
    "ledger-cent-transfer",
    "zero-plus-full",
    "approval-between-splits",
    "cross-order-one-cent",
    "boolean-rejection-valid-neighbor",
    "base-no-action",
    "declared-zero-inaction",
)


def payload(case: str) -> dict[str, Any]:
    if case not in CASES:
        raise ValueError("unknown held-out case")
    association = {"run_id": "run-current", "customer_id": "customer-A", "order_id": "order-A"}
    approval = {"kind": "approve", **association, "amount_cents": 8000, "actor": "manager-2"}
    amounts = [0, 8000] if case == "zero-plus-full" else [1, 7999]
    refunds: list[dict[str, Any]] = [
        {
            "kind": "refund",
            "refund": {
                "id": "heldout-" + str(i),
                **association,
                "destination": "original-payment-A",
                "amount_cents": amount,
            },
        }
        for i, amount in enumerate(amounts)
    ]
    entries = [{"refund_id": r["refund"]["id"], "amount_cents": r["refund"]["amount_cents"]} for r in refunds]
    if case == "ledger-cent-transfer":
        entries[0]["amount_cents"], entries[1]["amount_cents"] = 2, 7998
    ledger = {"kind": "ledger", "entries": list(reversed(entries))}
    if case == "approval-off-by-one":
        approval["amount_cents"] = 7999
    if case == "cross-order-one-cent":
        refunds[0]["refund"]["order_id"] = "order-B"
    events = [approval, ledger, *refunds]
    if case in ("ledger-cent-transfer", "zero-plus-full", "cross-order-one-cent"):
        events = [approval, *refunds, ledger]
    if case == "approval-between-splits":
        events = [ledger, refunds[0], approval, refunds[1]]
    if case in ("base-no-action", "declared-zero-inaction"):
        events = []
    invalid: list[dict[str, Any]] = []
    if case == "boolean-rejection-valid-neighbor":
        malformed = deepcopy(refunds[0])
        malformed["refund"]["amount_cents"] = True
        invalid = [{"operation": "complete", "success": 1}, {"operation": "act", "event": malformed}]
    return {
        "case": case,
        "candidate": {"events": events, "success": case not in ("optional-false-cent-split", "declared-zero-inaction")},
        "invalid_requests": invalid,
    }


PROBE = r"""
import json, os, socket, sys
p = json.loads(sys.argv[1])
assert os.getuid() == 65534
def send(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect('/run/refund/ledger.sock')
        client.sendall((json.dumps(request) + '\n').encode())
        return json.loads(client.makefile('rb').readline())
before = send({'operation':'read'})
assert before['ok'] is True
observed = {'case':p['case'], 'uid':os.getuid(), 'before':before['value'], 'rejections':[], 'replies':[]}
for request in p['invalid_requests']:
    reply = send(request)
    state = send({'operation':'read'})
    observed['rejections'].append({'request':request,'reply':reply,'state':state})
    assert reply == {'ok':False,'error':'invalid_request'}
    assert json.dumps(state,sort_keys=True) == json.dumps(before,sort_keys=True)
for event in p['candidate']['events']:
    reply = send({'operation':'act','event':event})
    observed['replies'].append(reply)
    assert reply == {'ok':True}
reply = send({'operation':'complete','success':p['candidate']['success']})
observed['replies'].append(reply)
assert reply == {'ok':True}
observed['after'] = send({'operation':'read'})['value']
print(json.dumps(observed,sort_keys=True))
"""


class RefundHoldoutAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "refund-heldout-control"

    def version(self) -> str:
        return "1.0.0"

    def __init__(self, *args: Any, case: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.payload = payload(case)

    async def setup(self, environment: Any) -> None:
        pass

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        result = await environment.exec(
            command="/usr/local/bin/python -I -S -B -c "
            + shlex.quote(PROBE)
            + " "
            + shlex.quote(json.dumps(self.payload)),
            timeout_sec=20,
            user="65534",
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "holdout-exec.json").write_text(
            json.dumps(
                {
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                sort_keys=True,
            )
        )
        if result.return_code != 0:
            raise RuntimeError("held-out control execution failed")
        context.metadata = json.loads(result.stdout)
