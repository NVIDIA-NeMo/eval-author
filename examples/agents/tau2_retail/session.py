# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MODEL = "openai/azure/openai/gpt-4.1-mini"
MAX_STEPS = 100
MAX_ERRORS = 10
OPENING = "Hi! How can I help you today?"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class RetailSession:
    def __init__(self, directory: Path, *, restore: bool = False) -> None:
        from pydantic import TypeAdapter
        from react_agent import create_react_agent  # ty: ignore[unresolved-import]
        from tau2.data_model.message import APICompatibleMessage, AssistantMessage  # ty: ignore[unresolved-import]
        from tau2.domains.retail.data_model import RetailDB  # ty: ignore[unresolved-import]
        from tau2.domains.retail.environment import get_environment  # ty: ignore[unresolved-import]
        from tau2.utils.llm_utils import set_llm_log_dir, set_llm_log_mode  # ty: ignore[unresolved-import]

        self.directory = directory
        self.state_path = Path(os.environ.get("RETAIL_STATE", "/app/state/db.json"))
        self.transcript_path = Path(os.environ.get("RETAIL_TRANSCRIPT", "/app/output/transcript.json"))
        self.log_dir = Path(os.environ.get("RETAIL_LOG_DIR", "/logs/agent"))
        set_llm_log_dir(str(self.log_dir / "llm_debug"))
        set_llm_log_mode("all")
        self.env = get_environment(db=RetailDB.load(self.state_path))
        self.agent = create_react_agent(
            tools=self.env.get_tools(), domain_policy=self.env.get_policy(), llm=MODEL, llm_args={"temperature": 0.0}
        )
        self.steps = 0
        self.errors = 0
        self.messages: list[dict[str, Any]] = []
        history = [AssistantMessage(role="assistant", content=OPENING, cost=0.0)]
        if restore:
            saved = json.loads((directory / "session.json").read_text())
            history = TypeAdapter(list[APICompatibleMessage]).validate_python(saved["history"])
            self.steps, self.errors = saved["steps"], saved["errors"]
            self.messages = saved["messages"]
        self.state = self.agent.get_init_state(message_history=history)

    def save(self) -> None:
        self.env.tools.db.dump(self.state_path)
        write_json(self.transcript_path, {"messages": self.messages})
        write_json(
            self.directory / "session.json",
            {
                "history": [message.model_dump(mode="json") for message in self.state.messages],
                "messages": self.messages,
                "steps": self.steps,
                "errors": self.errors,
            },
        )

    def respond(self, text: str) -> str:
        from tau2.data_model.message import MultiToolMessage, UserMessage  # ty: ignore[unresolved-import]

        self.messages.append({"role": "user", "content": text})
        message = UserMessage(role="user", content=text)
        while self.steps < MAX_STEPS and self.errors < MAX_ERRORS:
            reply, self.state = self.agent.generate_next_message(message, self.state)
            self.steps += 1
            if not reply.tool_calls:
                self.messages.append({"role": "assistant", "content": reply.content or ""})
                self.save()
                return reply.content or ""
            self.messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments} for call in reply.tool_calls
                    ],
                }
            )
            results = []
            for call in reply.tool_calls:
                result = self.env.get_response(call)
                self.errors += bool(result.error)
                results.append(result)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result.content,
                        "error": bool(result.error),
                    }
                )
                self.env.tools.db.dump(self.state_path)
            self.save()
            message = results[0] if len(results) == 1 else MultiToolMessage(role="tool", tool_messages=results)
        self.save()
        raise RuntimeError("The retail conversation reached its step or tool-error limit")
