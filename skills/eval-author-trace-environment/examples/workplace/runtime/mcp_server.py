# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from backend import TOOLS
from client import TransportError, call_http

SERVER_NAME = "workplace-email-stateful-simulation"
PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


def rpc_error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def dispatch(message: Any, url: str, audit: Any = None) -> dict[str, Any] | None:
    if type(message) is not dict or message.get("jsonrpc") != "2.0" or type(message.get("method")) is not str:
        return rpc_error(None, -32600, "Invalid request")
    identifier = message.get("id")
    if identifier is not None and type(identifier) not in (str, int):
        return rpc_error(None, -32600, "Invalid request ID")
    method = message["method"]
    params = message.get("params", {})
    if type(params) is not dict:
        return rpc_error(identifier, -32602, "Parameters must be an object") if "id" in message else None
    if audit is not None:
        # Record only recognized protocol labels, never caller-controlled payloads.
        event = {
            "method": method
            if method in {"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}
            else "unknown"
        }
        if method == "tools/call":
            name = params.get("name")
            event["tool"] = name if isinstance(name, str) and name in {tool["name"] for tool in TOOLS} else "unknown"
        audit.write(json.dumps(event) + "\n")
        audit.flush()
    if "id" not in message:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {
            "protocolVersion": requested if isinstance(requested, str) and requested in PROTOCOLS else "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0"},
            "instructions": "Bounded reconstructed email simulation. Unsupported inputs are explicit. No replay fallback.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        if (
            set(params) - {"name", "arguments", "_meta"}
            or type(params.get("name")) is not str
            or type(params.get("arguments", {})) is not dict
        ):
            return rpc_error(identifier, -32602, "Invalid tool call parameters")
        try:
            envelope = call_http(url, params["name"], params.get("arguments", {}))
        except TransportError:
            return rpc_error(identifier, -32001, "Workplace HTTP transport failed")
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(envelope if envelope["unsupported"] else envelope["output"], ensure_ascii=True),
                }
            ],
            "structuredContent": envelope,
            "isError": envelope["unsupported"],
        }
    else:
        return rpc_error(identifier, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Workplace email stateful simulation MCP proxy")
    parser.add_argument("--url", required=True)
    parser.add_argument("--audit-log", type=Path)
    args = parser.parse_args()
    audit = None
    try:
        if args.audit_log:
            args.audit_log.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(args.audit_log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            audit = os.fdopen(descriptor, "a", encoding="utf-8")
        for line in sys.stdin:
            try:
                response = dispatch(json.loads(line), args.url, audit)
            except ValueError:
                response = rpc_error(None, -32700, "Parse error")
            if response is not None:
                print(json.dumps(response, ensure_ascii=True, allow_nan=False), flush=True)
        return 0
    finally:
        if audit is not None:
            audit.close()


if __name__ == "__main__":
    raise SystemExit(main())
