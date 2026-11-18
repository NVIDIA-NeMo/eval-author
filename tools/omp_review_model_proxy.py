# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Model proxy for the omp review sandbox.

It is the only holder of the inference key. It forwards `POST /v1/messages` for one configured model to Inference
Hub, meters each response's token usage at configured prices, and refuses new calls once the run's spend would pass
its budget. Calls in flight are reserved against the budget at an estimated worst case (input priced at the dearest
input-side rate, plus the full output limit) and settled at their metered cost, so concurrent calls cannot jointly
overshoot it; the remaining error is the input-token estimate.

Configuration (environment): OMP_REVIEW_INFERENCE_HUB_API_KEY, OMP_REVIEW_MODEL, OMP_REVIEW_MAX_TOKENS,
OMP_REVIEW_BUDGET_USD, OMP_REVIEW_PRICES ("input=4,output=20,cache_write_5m=5,cache_write_1h=8,cache_read=0.2", USD
per million tokens), and optionally OMP_REVIEW_UPSTREAM (default https://inference-api.nvidia.com).
"""

from __future__ import annotations

import http.client
import json
import math
import os
import ssl
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MESSAGES_PATH = "/v1/messages"
SPEND_PATH = "/omp-review/spend"
PORT = 8080
MAX_REQUEST_BYTES = 64 * 1024 * 1024
# Underestimating bytes per token overestimates input tokens, keeping reservations conservative.
BYTES_PER_TOKEN = 3
ADMISSION_WAIT_SECONDS = 600
UPSTREAM_TIMEOUT_SECONDS = 900
PRICE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
FORWARDED_HEADERS = {"accept", "anthropic-beta", "anthropic-version", "content-type", "user-agent"}
# Anthropic Messages parameters. Anything else is refused: Inference Hub (LiteLLM) acts on extra body
# fields such as `fallbacks`, which would route to other models, and server tools or MCP servers would let the
# provider fetch URLs on the sandbox's behalf.
REQUEST_FIELDS = {
    "context_management",
    "max_tokens",
    "messages",
    "metadata",
    "model",
    "output_config",
    "output_format",
    "service_tier",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_p",
}
# Context edits that only drop content; compaction is excluded because its sampling pass is billed outside `usage`.
CONTEXT_EDITS = {"clear_thinking_20251015", "clear_tool_uses_20250919"}
SKIPPED_RESPONSE_HEADERS = {"connection", "content-encoding", "content-length", "keep-alive", "transfer-encoding"}
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
CACHE_SPLIT_FIELDS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


@dataclass(frozen=True)
class Prices:
    """USD per token."""

    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


@dataclass(frozen=True)
class Config:
    key: str
    model: str
    max_tokens: int
    budget_usd: float
    prices: Prices
    upstream_https: bool
    upstream_host: str
    upstream_port: int | None


def parse_prices(text: str) -> Prices:
    entries: dict[str, float] = {}
    for item in text.split(","):
        name, separator, value = item.strip().partition("=")
        if not separator or name not in PRICE_KEYS or name in entries:
            raise ValueError(f"OMP_REVIEW_PRICES needs exactly {', '.join(PRICE_KEYS)} as name=USD-per-million")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"OMP_REVIEW_PRICES {name} must be a non-negative number")
        entries[name] = number / 1_000_000
    if set(entries) != set(PRICE_KEYS):
        raise ValueError(f"OMP_REVIEW_PRICES needs exactly {', '.join(PRICE_KEYS)} as name=USD-per-million")
    return Prices(**entries)


def load_config(environ: dict[str, str]) -> Config:
    key, model = environ.get("OMP_REVIEW_INFERENCE_HUB_API_KEY", ""), environ.get("OMP_REVIEW_MODEL", "")
    if not key or not model:
        raise ValueError("OMP_REVIEW_INFERENCE_HUB_API_KEY and OMP_REVIEW_MODEL are required")
    max_tokens = int(environ.get("OMP_REVIEW_MAX_TOKENS", ""))
    budget = float(environ.get("OMP_REVIEW_BUDGET_USD", ""))
    if max_tokens < 1 or not math.isfinite(budget) or budget <= 0:
        raise ValueError("OMP_REVIEW_MAX_TOKENS and OMP_REVIEW_BUDGET_USD must be positive")
    upstream = urllib.parse.urlsplit(environ.get("OMP_REVIEW_UPSTREAM", "https://inference-api.nvidia.com"))
    if upstream.scheme not in {"http", "https"} or not upstream.hostname or upstream.path not in {"", "/"}:
        raise ValueError("OMP_REVIEW_UPSTREAM must be a bare http(s) origin")
    prices = parse_prices(environ.get("OMP_REVIEW_PRICES", ""))
    return Config(key, model, max_tokens, budget, prices, upstream.scheme == "https", upstream.hostname, upstream.port)


def _token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Usage:
    """Token usage merged across a response's events; Anthropic reports cumulative counts, so keep maxima."""

    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}
        self.complete = False

    def merge(self, usage: Any, *, final: bool) -> None:
        if not isinstance(usage, dict):
            return
        for field in USAGE_FIELDS:
            if _token_count(value := usage.get(field)):
                self.tokens[field] = max(self.tokens.get(field, 0), value)
        creation = usage.get("cache_creation")
        if isinstance(creation, dict):
            for field in CACHE_SPLIT_FIELDS:
                if _token_count(value := creation.get(field)):
                    self.tokens[field] = max(self.tokens.get(field, 0), value)
        self.complete = self.complete or final

    def feed_event(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        try:
            event = json.loads(line[5:])
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if event.get("type") == "message_start" and isinstance(event.get("message"), dict):
            self.merge(event["message"].get("usage"), final=False)
        elif event.get("type") == "message_delta":
            self.merge(event.get("usage"), final=True)

    def feed_body(self, body: bytes) -> None:
        try:
            message = json.loads(body)
        except ValueError:
            return
        if isinstance(message, dict):
            self.merge(message.get("usage"), final=True)

    def cost(self, prices: Prices) -> float:
        count = self.tokens.get
        split_5m, split_1h = count("ephemeral_5m_input_tokens", 0), count("ephemeral_1h_input_tokens", 0)
        # Cache writes not attributed to a TTL are charged at the dearer 1-hour rate.
        unsplit = max(count("cache_creation_input_tokens", 0) - split_5m - split_1h, 0)
        return (
            count("input_tokens", 0) * prices.input
            + count("output_tokens", 0) * prices.output
            + split_5m * prices.cache_write_5m
            + (split_1h + unsplit) * prices.cache_write_1h
            + count("cache_read_input_tokens", 0) * prices.cache_read
        )


class Meter:
    """Run budget: spent (settled) plus reserved (in flight) never exceeds the budget at admission."""

    def __init__(self, budget_usd: float) -> None:
        self.budget = budget_usd
        self.spent = 0.0
        self.reserved = 0.0
        self.requests = 0
        self.refused = 0
        self.hub_key_spend: list[float] = []
        self._condition = threading.Condition()

    def admit(self, amount: float, wait_seconds: float) -> bool:
        deadline = time.monotonic() + wait_seconds
        with self._condition:
            while True:
                if self.spent + amount > self.budget:
                    self.refused += 1
                    return False
                if self.spent + self.reserved + amount <= self.budget:
                    self.reserved += amount
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.refused += 1
                    return False
                self._condition.wait(remaining)

    def settle(self, reservation: float, cost: float, hub_key_spend: float | None) -> None:
        with self._condition:
            self.reserved -= reservation
            self.spent += cost
            self.requests += 1
            if hub_key_spend is not None:
                self.hub_key_spend.append(hub_key_spend)
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            hub = self.hub_key_spend
            # Inference Hub's running total for the key, seen before each call; approximate (it misses the last call
            # and includes other traffic on the key) and None when it never moved, which means no data.
            hub_delta = max(hub) - min(hub) if len(hub) > 1 else 0.0
            return {
                "spent_usd": round(self.spent, 6),
                "budget_usd": self.budget,
                "reserved_usd": round(self.reserved, 6),
                "requests": self.requests,
                "refused": self.refused,
                "hub_key_spend_delta_usd": round(hub_delta, 6) if hub_delta > 0 else None,
            }


class Handler(BaseHTTPRequestHandler):
    server: ProxyServer

    def do_GET(self) -> None:
        if self.path == SPEND_PATH:
            self._json(200, self.server.meter.snapshot())
        else:
            self._error(403, "permission_error", "only POST /v1/messages is allowed")

    def do_POST(self) -> None:
        path, _, query = self.path.partition("?")
        if path != MESSAGES_PATH:
            self._error(403, "permission_error", "only POST /v1/messages is allowed")
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > MAX_REQUEST_BYTES:
            self._error(
                413, "request_too_large", f"request bodies must declare a length of at most {MAX_REQUEST_BYTES}"
            )
            return
        body = self.rfile.read(int(length))
        config = self.server.config
        try:
            request = json.loads(body)
        except ValueError:
            self._error(400, "invalid_request_error", "request body must be JSON")
            return
        if not isinstance(request, dict) or request.get("model") != config.model:
            self._error(400, "invalid_request_error", f"only model {config.model} is allowed")
            return
        max_tokens = request.get("max_tokens")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= config.max_tokens:
            self._error(400, "invalid_request_error", f"max_tokens must be between 1 and {config.max_tokens}")
            return
        if problem := _unsupported(request):
            self._error(400, "invalid_request_error", problem)
            return
        prices = config.prices
        input_price = max(prices.input, prices.cache_write_5m, prices.cache_write_1h)
        reservation = math.ceil(len(body) / BYTES_PER_TOKEN) * input_price + max_tokens * prices.output
        if not self.server.meter.admit(reservation, ADMISSION_WAIT_SECONDS):
            self._error(402, "billing_error", f"the omp review budget of ${config.budget_usd:.2f} is spent")
            return
        try:
            cost, hub_key_spend = self._forward(body, query, reservation)
        except BaseException:
            self.server.meter.settle(reservation, reservation, None)
            raise
        self.server.meter.settle(reservation, cost, hub_key_spend)

    def _forward(self, body: bytes, query: str, reservation: float) -> tuple[float, float | None]:
        """Relay one call and return its metered cost; incomplete usage on a 200 is charged the reservation."""
        config = self.server.config
        connection: http.client.HTTPConnection
        if config.upstream_https:
            connection = http.client.HTTPSConnection(
                config.upstream_host,
                config.upstream_port,
                timeout=UPSTREAM_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                config.upstream_host, config.upstream_port, timeout=UPSTREAM_TIMEOUT_SECONDS
            )
        headers = {name: value for name, value in self.headers.items() if name.lower() in FORWARDED_HEADERS}
        headers |= {
            "Authorization": f"Bearer {config.key}",
            "Accept-Encoding": "identity",
            "Content-Length": str(len(body)),
        }
        usage = Usage()
        status = 0
        hub_key_spend = None
        try:
            connection.request("POST", MESSAGES_PATH + (f"?{query}" if query else ""), body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            hub_key_spend = _number(response.getheader("x-litellm-key-spend"))
            self.send_response(status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in SKIPPED_RESPONSE_HEADERS:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if "text/event-stream" in (response.getheader("Content-Type") or ""):
                while line := response.readline():
                    usage.feed_event(line)
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                data = response.read()
                usage.feed_body(data)
                self.wfile.write(data)
        except (OSError, http.client.HTTPException) as error:
            if not status:
                self._error(502, "api_error", f"model upstream failed ({type(error).__name__})")
        finally:
            connection.close()
        cost = usage.cost(config.prices)
        if status == 200 and not usage.complete:
            cost = max(cost, reservation)
        self.log_message("%s %d cost $%.4f tokens %s", MESSAGES_PATH, status, cost, usage.tokens)
        return cost, hub_key_spend

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, kind: str, message: str) -> None:
        self._json(status, {"type": "error", "error": {"type": kind, "message": message}})


def _unsupported(request: dict[str, Any]) -> str | None:
    """Why the request may not be forwarded, or None."""
    if extra := sorted(set(request) - REQUEST_FIELDS):
        return f"unsupported request fields: {', '.join(extra)}"
    metadata = request.get("metadata")
    if metadata is not None and (not isinstance(metadata, dict) or set(metadata) - {"user_id"}):
        return "metadata may only carry user_id"
    tools = request.get("tools", [])
    if not isinstance(tools, list) or any(
        not isinstance(tool, dict) or tool.get("type", "custom") != "custom" for tool in tools
    ):
        return "only custom tools are allowed"
    # Server-side compaction runs an extra sampling pass whose tokens are not in the top-level usage the meter reads.
    management = request.get("context_management")
    if management is not None and (
        not isinstance(management, dict)
        or set(management) - {"edits"}
        or not isinstance(edits := management.get("edits", []), list)
        or any(not isinstance(edit, dict) or edit.get("type") not in CONTEXT_EDITS for edit in edits)
    ):
        return f"context_management may only use the edits {', '.join(sorted(CONTEXT_EDITS))}"
    return None


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: Config) -> None:
        super().__init__(address, Handler)
        self.config = config
        self.meter = Meter(config.budget_usd)


def main() -> int:
    try:
        config = load_config(dict(os.environ))
    except ValueError as error:
        print(f"model_proxy: {error}", file=sys.stderr)
        return 2
    ProxyServer(("0.0.0.0", PORT), config).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
