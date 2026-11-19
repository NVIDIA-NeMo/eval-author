# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preparation-only exact Responses wire seam. Stdlib; no Hermes initialization.
No credential discovery, networking, model defaults, retry, or approval creation.
A trusted custodian must supply an approved byte transport and count authority.
This module's CLI only runs synthetic canaries (see test_canaries.py).
"""

import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from pathlib import Path


class Refused(RuntimeError):
    pass


def require(ok, category):
    if not ok:
        raise Refused(category)


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        obj = {}
        for k, v in items:
            require(k not in obj, "duplicate_json_key")
            obj[k] = v
        return obj

    def constant(_):
        raise Refused("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def number(value):
    return type(value) is int and value >= 0


def private_root(path):
    """Filesystem hygiene only; does NOT establish developer-excluding custody."""
    path = Path(path).absolute()
    for component in (path, *path.parents):
        require(not component.is_symlink(), "symlink_ancestor")
    st = path.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid(), "root_owner")
    require(st.st_mode & 0o077 == 0, "root_permissions")
    return path


def exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        parent = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except BaseException:
        # Preserve partial/failed exclusive record; never retry by deleting it.
        raise


class Budget:
    """Fresh finite ledger. An interrupted reservation prevents all further calls.
    The custodian owns this file; a same-UID developer can bypass it, so local
    canaries are not authorization or adversarial accounting enforcement.
    """

    def __init__(self, root, limits):
        self.root = private_root(root)
        self.db = self.root / "budget.sqlite3"
        require(not self.db.exists(), "ledger_already_exists")
        require(
            limits
            and all(
                set(v) == {"requests", "tokens"} and all(number(x) and x > 0 for x in v.values())
                for v in limits.values()
            ),
            "limits",
        )
        exclusive(self.db, b"")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE lanes(lane TEXT PRIMARY KEY, max_requests INT, max_tokens INT);
                CREATE TABLE calls(id TEXT PRIMARY KEY, lane TEXT, reserved INT,
                    used INT, state TEXT, body_sha TEXT, reason TEXT);
            """)
            db.executemany(
                "INSERT INTO lanes VALUES(?,?,?)", [(k, v["requests"], v["tokens"]) for k, v in limits.items()]
            )

    def connect(self):
        require(not self.db.is_symlink(), "ledger_symlink")
        db = sqlite3.connect(self.db, timeout=1)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, lane, amount, body_sha):
        require(number(amount) and amount > 0, "reservation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require(
                not db.execute("SELECT 1 FROM calls WHERE state != 'settled' LIMIT 1").fetchone(),
                "unsettled_or_failed_attempt",
            )
            limit = db.execute("SELECT max_requests,max_tokens FROM lanes WHERE lane=?", (lane,)).fetchone()
            require(limit is not None, "unknown_lane")
            count, spent = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(used,reserved)),0) FROM calls WHERE lane=?", (lane,)
            ).fetchone()
            require(count < limit[0] and spent + amount <= limit[1], "budget_exhausted")
            call_id = uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES(?,?,?,NULL,?,?,NULL)", (call_id, lane, amount, "reserved", body_sha))
        return call_id

    def settle(self, call_id, used, reason=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,reserved FROM calls WHERE id=?", (call_id,)).fetchone()
            require(row and row[0] == "reserved", "reservation_not_open")
            require(used is None or number(used), "usage_invalid")
            if used is not None and used > row[1]:
                reason = "reservation_exceeded"
            db.execute(
                "UPDATE calls SET used=?,state=?,reason=? WHERE id=?",
                (used, "halted" if reason else "settled", reason, call_id),
            )

    def snapshot(self):
        with self.connect() as db:
            return [
                dict(zip(("id", "lane", "reserved", "used", "state", "body_sha", "reason"), r))
                for r in db.execute("SELECT * FROM calls ORDER BY rowid")
            ]


DESTINATION = "https://inference-api.nvidia.com/v1/responses"
OBSERVED_MODELS = {"azure/openai/gpt-6-astra", "azure/openai/gpt-6.1-sol"}


def envelope_check(envelope):
    require(
        type(envelope) is dict and set(envelope) == {"messages", "tools"} and envelope["tools"] == [], "envelope_keys"
    )
    messages = envelope["messages"]
    require(type(messages) is list and len(messages) >= 2 and len(messages) % 2 == 0, "message_sequence")
    for i, message in enumerate(messages):
        role = "system" if i == 0 else ("user" if i % 2 else "assistant")
        require(
            type(message) is dict
            and set(message) == {"role", "content"}
            and message["role"] == role
            and type(message["content"]) is str,
            "message_shape",
        )
    return strict(packed(envelope))


def response_text(raw, model):
    obj = strict(raw)
    require(
        type(obj) is dict and obj.get("model") == model and obj.get("status") == "completed",
        "response_identity_or_status",
    )
    require(type(obj.get("id")) is str and obj["id"], "response_id")
    usage = obj.get("usage")
    require(
        type(usage) is dict and all(number(usage.get(k)) for k in ("input_tokens", "output_tokens", "total_tokens")),
        "missing_usage",
    )
    require(usage["input_tokens"] + usage["output_tokens"] == usage["total_tokens"], "usage_mismatch")
    texts = []
    require(type(obj.get("output")) is list, "output_missing")
    for item in obj["output"]:
        require(type(item) is dict, "output_item")
        if item.get("type") == "reasoning":
            continue  # Captured verbatim but never exposed as user/assistant text.
        require(
            item.get("type") == "message" and item.get("role") == "assistant" and item.get("status") == "completed",
            "unexpected_tool_or_output",
        )
        for chunk in item.get("content", []):
            require(chunk.get("type") == "output_text" and type(chunk.get("text")) is str, "unexpected_content")
            texts.append(chunk["text"])
    require(len(texts) == 1, "single_text_required")
    return obj, texts[0]


class ExactSession:
    """Drop-in complete(envelope)->str for reviewer.fresh_review.

    authorize is an out-of-band trusted callback, never selected JSON consent.
    count(body,destination)-> {body_sha256,input_tokens,upper_bound_verified}
    requires an independently established exact-body count / enforced upper bound.
    post(destination,body,timeout)-> {sent_body:bytes,status:int,body:bytes,
    request_id:str}. It must be a reviewed single-send HTTP transport with redirects,
    SDK retries, proxies, extra prompts and hidden provider sessions disabled.
    Neither callback is supplied here: no live path is enabled by this package.
    """

    def __init__(self, root, budget, lane, opening, model, input_cap, output_cap, count, post, authorize, deadline):
        self.root = private_root(root)
        require(model in OBSERVED_MODELS, "unobserved_model")
        require(number(input_cap) and input_cap > 0 and number(output_cap) and output_cap > 0, "caps")
        self.budget, self.lane = budget, lane
        self.opening = envelope_check(opening)
        self.previous, self.answer = None, None
        self.model, self.input_cap, self.output_cap = model, input_cap, output_cap
        self.count, self.post, self.authorize, self.deadline = count, post, authorize, deadline
        self.session_id = uuid.uuid4().hex  # local capture identity, not provider attestation
        self.seq, self.failed = 0, False
        exclusive(
            self.root / "session.json",
            packed(
                {
                    "session_id": self.session_id,
                    "lane": lane,
                    "opening": self.opening,
                    "model": model,
                    "destination": DESTINATION,
                    "hidden_history_allowed": False,
                    "custody_established": False,
                }
            ),
        )

    def __call__(self, envelope):
        require(not self.failed, "session_halted")
        call_id = None
        used = None
        try:
            require(time.monotonic() < self.deadline, "deadline")
            env = envelope_check(envelope)
            if self.previous is None:
                require(env == self.opening, "opening_changed")
            else:
                old = self.previous["messages"]
                require(
                    len(env["messages"]) == len(old) + 2
                    and env["messages"][:-2] == old
                    and env["messages"][-2] == {"role": "assistant", "content": self.answer},
                    "history_changed",
                )
            body = packed(
                {
                    "model": self.model,
                    "input": env["messages"],
                    "tools": [],
                    "store": False,
                    "stream": False,
                    "max_output_tokens": self.output_cap,
                }
            )
            # Verify semantic projection of the EXACT bytes handed to transport.
            wire = strict(body)
            require(
                wire["input"] == env["messages"]
                and wire["tools"] == env["tools"]
                and set(wire) == {"model", "input", "tools", "store", "stream", "max_output_tokens"},
                "wire_changed",
            )
            scope = {
                "destination": DESTINATION,
                "model": self.model,
                "lane": self.lane,
                "body_sha256": digest(body),
                "input_cap": self.input_cap,
                "output_cap": self.output_cap,
            }
            require(self.authorize(dict(scope)) is True, "not_authorized")
            # Charge one attempt before even counting; crashes and failed counts burn it.
            call_id = self.budget.reserve(self.lane, self.input_cap + self.output_cap, digest(body))
            self.seq += 1
            prefix = self.root / f"{self.seq:03d}"
            exclusive(str(prefix) + ".envelope.json", packed(env))
            exclusive(str(prefix) + ".request.json", body)
            # Counting is an external request too. Persist a one-shot claim first.
            exclusive(str(prefix) + ".count-claim.json", packed(scope))
            bound = self.count(body, DESTINATION)
            exclusive(str(prefix) + ".count.json", packed(bound))
            require(
                type(bound) is dict
                and set(bound) == {"body_sha256", "input_tokens", "upper_bound_verified"}
                and bound["body_sha256"] == digest(body)
                and number(bound["input_tokens"])
                and bound["upper_bound_verified"] is True,
                "unverified_count_bound",
            )
            n = bound["input_tokens"]
            require(n <= self.input_cap, "input_cap")
            require(time.monotonic() < self.deadline, "deadline")
            result = self.post(DESTINATION, body, min(60.0, self.deadline - time.monotonic()))
            require(
                type(result) is dict and type(result.get("body")) is bytes and len(result["body"]) <= 2_000_000,
                "transport_result",
            )
            exclusive(str(prefix) + ".response.json", result["body"])
            exclusive(
                str(prefix) + ".transport.json",
                packed(
                    {
                        "status": result.get("status"),
                        "request_id": result.get("request_id"),
                        "sent_body_sha256": digest(result.get("sent_body", b"")),
                        "received_body_sha256": digest(result["body"]),
                    }
                ),
            )
            # Record parseable accounting even when identity/status/wire checks fail.
            tentative = strict(result["body"]).get("usage", {})
            if all(number(tentative.get(k)) for k in ("input_tokens", "output_tokens", "total_tokens")):
                used = max(tentative["total_tokens"], tentative["input_tokens"] + tentative["output_tokens"])
            require(
                result.get("sent_body") == body
                and result.get("status") == 200
                and type(result.get("request_id")) is str
                and result["request_id"],
                "wire_or_http_mismatch",
            )
            obj, text = response_text(result["body"], self.model)
            used = obj["usage"]["total_tokens"]
            require(obj["usage"]["input_tokens"] <= n, "input_count_underrun")
            require(obj["usage"]["output_tokens"] <= self.output_cap, "output_cap_exceeded")
            require(time.monotonic() < self.deadline, "late_response")
            self.budget.settle(call_id, used)
            call_id = None
            exclusive(
                str(prefix) + ".accepted.json",
                packed(
                    {
                        "provider_response_id": obj["id"],
                        "usage": obj["usage"],
                        "text_sha256": digest(text.encode()),
                        "session_id": self.session_id,
                    }
                ),
            )
            self.previous, self.answer = env, text
            return text
        except BaseException as exc:
            self.failed = True
            if call_id is not None:
                self.budget.settle(call_id, used, str(exc) if isinstance(exc, Refused) else "transport_or_io_failure")
            # Never print provider errors or exception bodies through a public channel.
            raise Refused(str(exc) if isinstance(exc, Refused) else "completion_failed") from None
