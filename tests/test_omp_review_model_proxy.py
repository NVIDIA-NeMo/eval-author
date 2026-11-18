# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import http.client
import json
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import omp_review_model_proxy as proxy

MODEL = "aws/anthropic/test-model"
# One dollar per output token keeps reservations (max_tokens dollars here) and costs easy to read.
DOLLAR_PER_OUTPUT_TOKEN = "input=0,output=1000000,cache_write_5m=0,cache_write_1h=0,cache_read=0"


class Upstream:
    """Fake Inference Hub: records requests and answers with a scripted response."""

    def __init__(self, respond: Callable[[BaseHTTPRequestHandler, dict[str, Any]], None]) -> None:
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.requests.append((self.path, dict(self.headers.items()), body))
                respond(self, body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()


def json_reply(output_tokens: int) -> Callable[[BaseHTTPRequestHandler, dict[str, Any]], None]:
    def respond(handler: BaseHTTPRequestHandler, _: dict[str, Any]) -> None:
        data = json.dumps({"type": "message", "usage": {"input_tokens": 1, "output_tokens": output_tokens}}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    return respond


def sse_reply(events: list[dict[str, Any]]) -> Callable[[BaseHTTPRequestHandler, dict[str, Any]], None]:
    def respond(handler: BaseHTTPRequestHandler, _: dict[str, Any]) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        for event in events:
            handler.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())

    return respond


@pytest.fixture
def start() -> Iterator[Callable[..., tuple[Any, Upstream]]]:
    servers: list[Any] = []

    def run(respond: Callable[..., None], budget: float = 100, prices: str = DOLLAR_PER_OUTPUT_TOKEN):
        upstream = Upstream(respond)
        config = proxy.load_config(
            {
                "OMP_REVIEW_INFERENCE_HUB_API_KEY": "sk-real",
                "OMP_REVIEW_MODEL": MODEL,
                "OMP_REVIEW_MAX_TOKENS": "64",
                "OMP_REVIEW_BUDGET_USD": str(budget),
                "OMP_REVIEW_PRICES": prices,
                "OMP_REVIEW_UPSTREAM": f"http://127.0.0.1:{upstream.server.server_address[1]}",
            }
        )
        server = proxy.ProxyServer(("127.0.0.1", 0), config)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.extend([server, upstream.server])
        return server, upstream

    yield run
    for server in servers:
        server.shutdown()


def call(server: Any, path: str = "/v1/messages", method: str = "POST", **body: Any) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    payload = json.dumps({"model": MODEL, "max_tokens": 8, "messages": [], **body}).encode()
    headers = {"Content-Type": "application/json", "x-api-key": "proxy-injected", "x-litellm-api-key": "sk-other"}
    connection.request(method, path, body=payload if method == "POST" else None, headers=headers)
    response = connection.getresponse()
    return response.status, response.read()


def test_streams_response_and_meters_every_usage_category(start):
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 1000,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 300,
                    "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 0},
                    "output_tokens": 1,
                }
            },
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "usage": {"output_tokens": 300}},
        {"type": "message_stop"},
    ]
    prices = "input=1,output=2,cache_write_5m=3,cache_write_1h=4,cache_read=0.5"
    server, upstream = start(sse_reply(events), prices=prices)

    status, body = call(server, "/v1/messages?beta=true")

    assert status == 200 and body.count(b"data: ") == len(events)
    # 200 written tokens are attributed to the 5-minute TTL; the unattributed 100 are charged the 1-hour rate.
    expected = (1000 * 1 + 300 * 2 + 200 * 3 + 100 * 4 + 500 * 0.5) / 1_000_000
    assert server.meter.snapshot()["spent_usd"] == pytest.approx(expected)
    path, headers, _ = upstream.requests[0]
    assert path == "/v1/messages?beta=true"
    assert headers["Authorization"] == "Bearer sk-real"
    assert not {"x-api-key", "x-litellm-api-key"} & {name.lower() for name in headers}


@pytest.mark.parametrize(
    ("path", "method", "body", "status"),
    [
        ("/v1/models", "GET", {}, 403),
        ("/v1/models/../messages", "POST", {}, 403),
        ("/v1/chat/completions", "POST", {}, 403),
        ("/v1/messages", "POST", {"model": "aws/anthropic/pricier-model"}, 400),
        ("/v1/messages", "POST", {"max_tokens": 65}, 400),
        ("/v1/messages", "POST", {"max_tokens": True}, 400),
        ("/v1/messages", "POST", {"fallbacks": ["aws/anthropic/pricier-model"]}, 400),
        ("/v1/messages", "POST", {"mcp_servers": [{"type": "url", "url": "https://evil.example"}]}, 400),
        ("/v1/messages", "POST", {"tools": [{"type": "web_fetch_20250910", "name": "web_fetch"}]}, 400),
        ("/v1/messages", "POST", {"metadata": {"user_id": "u", "tags": ["x"]}}, 400),
        ("/v1/messages", "POST", {"context_management": {"edits": [{"type": "compact_20260112"}]}}, 400),
    ],
)
def test_rejects_other_paths_models_limits_and_routing_fields(start, path, method, body, status):
    server, upstream = start(json_reply(1))
    assert call(server, path, method, **body)[0] == status
    assert upstream.requests == []


def test_forwards_the_parameters_omp_sends(start):
    server, upstream = start(json_reply(1))
    body: dict[str, Any] = {
        "system": [{"type": "text", "text": "sys"}],
        "tools": [{"name": "read", "description": "Read", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "metadata": {"user_id": "session"},
        "stream": False,
        "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    }
    assert call(server, **body)[0] == 200 and len(upstream.requests) == 1


def test_reservation_prices_input_at_the_dearest_input_side_rate(start):
    # Base input is free, but a 1-hour cache write costs $1 per token, so a ~30-token prompt cannot fit $5.
    prices = "input=0,output=0,cache_write_5m=0,cache_write_1h=1000000,cache_read=0"
    server, upstream = start(json_reply(1), budget=5, prices=prices)
    assert call(server)[0] == 402
    assert upstream.requests == []


def test_refuses_calls_that_no_longer_fit_the_budget(start):
    server, upstream = start(json_reply(output_tokens=9), budget=10)

    assert call(server, max_tokens=9)[0] == 200
    status, body = call(server, max_tokens=9)

    assert status == 402 and json.loads(body)["error"]["type"] == "billing_error"
    assert len(upstream.requests) == 1
    snapshot = server.meter.snapshot()
    assert (snapshot["spent_usd"], snapshot["reserved_usd"], snapshot["refused"]) == (9, 0, 1)


def test_incomplete_stream_is_charged_its_reservation(start):
    start_only = [{"type": "message_start", "message": {"usage": {"input_tokens": 1, "output_tokens": 1}}}]
    server, _ = start(sse_reply(start_only))

    assert call(server, max_tokens=8)[0] == 200
    assert server.meter.snapshot()["spent_usd"] == 8


def test_concurrent_calls_wait_instead_of_jointly_overshooting(start):
    release = threading.Event()
    active, peak, lock = [0], [0], threading.Lock()

    def respond(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        with lock:
            active[0] -= 1
        json_reply(2)(handler, body)

    # Each call reserves $6 against a $10 budget, so only one may be in flight until the other settles at $2.
    server, upstream = start(respond, budget=10)
    results: list[int] = []
    threads = [threading.Thread(target=lambda: results.append(call(server, max_tokens=6)[0])) for _ in range(2)]
    for thread in threads:
        thread.start()
    threading.Timer(0.5, release.set).start()
    for thread in threads:
        thread.join(10)

    assert results == [200, 200] and peak[0] == 1 and len(upstream.requests) == 2
    assert server.meter.snapshot()["spent_usd"] == 4
