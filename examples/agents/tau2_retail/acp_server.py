# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from session import RetailSession, write_json


class Server:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions: dict[str, RetailSession] = {}

    def session_path(self, session_id: str) -> Path:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError("Invalid session identifier")
        return self.root / session_id

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True},
                "authMethods": [],
                "agentInfo": {"name": "tau2-retail-react", "version": "c06b07d"},
            }
        if method == "session/new":
            if params.get("mcpServers"):
                raise ValueError("This reference agent uses its fixed retail toolkit")
            if any(self.root.glob("*/session.json")):
                raise ValueError("Create a fresh task environment for another retail session")
            session_id = str(uuid.uuid4())
            session = RetailSession(self.session_path(session_id))
            session.save()
            self.sessions[session_id] = session
            return {"sessionId": session_id}
        session_id = params["sessionId"]
        if method == "session/load":
            self.sessions[session_id] = RetailSession(self.session_path(session_id), restore=True)
            return {}
        if method == "session/prompt":
            blocks = params["prompt"]
            if not isinstance(blocks, list) or not blocks or any(block.get("type") != "text" for block in blocks):
                raise ValueError("The retail agent accepts text messages only")
            text = "\n".join(block["text"] for block in blocks)
            reply = self.sessions[session_id].respond(text)
            emit(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": reply}},
                    },
                }
            )
            return {"stopReason": "end_turn"}
        raise ValueError("Unsupported ACP method")


def emit(value: object) -> None:
    print(json.dumps(value), flush=True)


def main() -> int:
    server = Server(Path(os.environ.get("RETAIL_SESSION_DIR", "/app/state/sessions")))
    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            result = server.dispatch(request["method"], request.get("params", {}))
            emit({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except Exception as error:
            write_json(
                Path(os.environ.get("RETAIL_LOG_DIR", "/logs/agent")) / "adapter-error.json",
                {"type": type(error).__name__},
            )
            emit(
                {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": type(error).__name__}}
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
