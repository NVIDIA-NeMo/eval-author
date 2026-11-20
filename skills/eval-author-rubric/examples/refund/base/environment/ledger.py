# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
from typing import Any

MAX_BYTES = 1048576


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    if len(raw) > MAX_BYTES:
        raise ValueError("request_too_large")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


def validate_event(event: dict[str, Any]) -> None:
    def keys(row: Any, fields: set[str]) -> None:
        if type(row) is not dict or set(row) != fields:
            raise ValueError("invalid_event")

    def money(value: Any) -> None:
        if type(value) is not int or not 0 <= value <= 10**12:
            raise ValueError("invalid_amount")

    def texts(row: dict[str, Any], fields: set[str]) -> None:
        if any(type(row[key]) is not str or not row[key].strip() for key in fields):
            raise ValueError("invalid_text")

    if type(event) is not dict:
        raise ValueError("invalid_event")
    kind = event.get("kind")
    if kind == "approve":
        fields = {"run_id", "customer_id", "order_id", "actor"}
        keys(event, fields | {"kind", "amount_cents"})
        texts(event, fields)
        money(event["amount_cents"])
    elif kind == "refund":
        keys(event, {"kind", "refund"})
        fields = {"id", "run_id", "customer_id", "order_id", "destination"}
        keys(event["refund"], fields | {"amount_cents"})
        texts(event["refund"], fields)
        money(event["refund"]["amount_cents"])
    elif kind == "ledger":
        keys(event, {"kind", "entries"})
        if type(event["entries"]) is not list or not 1 <= len(event["entries"]) <= 10000:
            raise ValueError("invalid_entries")
        for row in event["entries"]:
            keys(row, {"refund_id", "amount_cents"})
            texts(row, {"refund_id"})
            money(row["amount_cents"])
    else:
        raise ValueError("unsupported_action")


class Ledger:
    def __init__(self, contract: dict[str, Any], record: Path) -> None:
        self.contract = copy.deepcopy(contract)
        self.record = record
        self.record.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = os.open(self.record.with_suffix(".lock"), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(lock)
        self.reset()

    def reset(self) -> None:
        self._save(
            {
                "candidate": {"events": [], "success": False},
                "state": copy.deepcopy(self.contract["sources"]["initial_state"]),
            }
        )

    def read(self) -> dict[str, Any]:
        return copy.deepcopy(self.value)

    def _save(self, value: dict[str, Any]) -> None:
        raw = (json.dumps(value, sort_keys=True) + "\n").encode()
        if len(json.dumps(value["candidate"]).encode()) > MAX_BYTES:
            raise ValueError("candidate_too_large")
        # The verifier closes admission under this same lock before reading state.
        with self.record.with_suffix(".lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.record.with_suffix(".closed").exists():
                raise ValueError("ledger_closed")
            temporary = self.record.with_suffix(".pending")
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(raw)
            temporary.replace(self.record)
            self.value = value

    def act(self, event: dict[str, Any]) -> None:
        validate_event(event)
        value = self.read()
        if len(value["candidate"]["events"]) >= 1000:
            raise ValueError("event_limit")
        value["candidate"]["events"].append(copy.deepcopy(event))
        if event["kind"] == "refund":
            value["state"]["refunds"].append(copy.deepcopy(event["refund"]))
        elif event["kind"] == "ledger":
            value["state"]["ledger"].extend(copy.deepcopy(event["entries"]))
        self._save(value)

    def complete(self, success: bool) -> None:
        if type(success) is not bool:
            raise ValueError("invalid_success")
        value = self.read()
        value["candidate"]["success"] = success
        self._save(value)
