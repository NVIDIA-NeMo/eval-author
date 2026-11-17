# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any


def emit(value: object) -> None:
    print(json.dumps(value), flush=True)


def dispatch(method: str, params: dict[str, Any], root: Path) -> dict[str, Any]:
    if method == "initialize":
        return {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}, "authMethods": []}
    if method == "session/new":
        session_id = str(uuid.uuid4())
        (root / session_id).write_text("0")
        return {"sessionId": session_id}
    session_id = params["sessionId"]
    if str(uuid.UUID(session_id)) != session_id:
        raise ValueError("Invalid session")
    path = root / session_id
    if method == "session/load":
        int(path.read_text())
        return {}
    if method == "session/prompt":
        count = int(path.read_text()) + 1
        path.write_text(str(count))
        message = "".join(block["text"] for block in params["prompt"] if block["type"] == "text")
        emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": f"turn={count} received={message}"},
                    },
                },
            }
        )
        return {"stopReason": "end_turn"}
    raise ValueError("Unsupported method")


def main() -> None:
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            result = dispatch(request["method"], request.get("params", {}), root)
            emit({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except (KeyError, ValueError, OSError):
            emit({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": "Invalid request"}})


if __name__ == "__main__":
    main()
