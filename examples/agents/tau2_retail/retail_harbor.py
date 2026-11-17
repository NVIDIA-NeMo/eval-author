# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shlex
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.protocols.acp import ACPAgentMixin
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.bridge import BridgeKind
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from session import MODEL, write_json


def connection(agent: BaseAgent) -> dict[str, str]:
    values = {
        name: agent.extra_env.get(name) or os.environ.get(name, "")
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE")
    }
    base = values["OPENAI_BASE_URL"] or values["OPENAI_API_BASE"]
    if not values["OPENAI_API_KEY"] or not base:
        raise ValueError("Set OPENAI_API_KEY and OPENAI_BASE_URL for this example")
    return {"OPENAI_API_KEY": values["OPENAI_API_KEY"], "OPENAI_BASE_URL": base, "OPENAI_API_BASE": base}


class RetailAgentHarbor(ACPAgentMixin, BaseAgent):
    capabilities = AgentCapabilities(bridges=frozenset({BridgeKind.ACP}))

    @staticmethod
    def name() -> str:
        return "tau2-retail-react"

    def version(self) -> str:
        return "c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4"

    async def setup(self, environment: BaseEnvironment) -> None:
        if self.model_name != MODEL:
            raise ValueError("The example preserves the baseline retail model")
        connection(self)
        result = await environment.exec("test -f /opt/retail-agent/acp_server.py")
        if result.return_code:
            raise RuntimeError("Use the prepared retail task environment")

    def acp_command(self) -> list[str]:
        return ["python", "-u", "/opt/retail-agent/acp_server.py"]

    def acp_env(self) -> dict[str, str]:
        return {**connection(self), "RETAIL_LOG_DIR": str(self.environment_logs_dir)}

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        raise RuntimeError("This adapter requires Harbor's user-agent ACP bridge")


def private_scenario(instruction: str) -> dict[str, Any]:
    marker = "## Private scenario\n\n"
    if marker not in instruction:
        raise ValueError("Use the Eval Author user-simulation translation")
    value, _ = json.JSONDecoder().raw_decode(instruction.split(marker, 1)[1])
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("start_prompt"), str)
    ):
        raise ValueError("The translated prompt has no valid opening request")
    return value


class NeutralUserHarbor(BaseAgent):
    @staticmethod
    def name() -> str:
        return "tau2-retail-neutral-user"

    def version(self) -> str:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        connection(self)
        if not self.model_name:
            raise ValueError("Select the simulator model explicitly")

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        scenario = private_scenario(instruction)
        access = connection(self)
        model = self.model_name.removeprefix("openai/") if self.model_name else ""
        history: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": instruction
                + "\n\nThe host sends your messages through acpx. Return JSON with message (string) and done (boolean). Do not return shell commands. Set done only when the scenario's stopping conditions are met.",
            }
        ]
        exchanges: list[dict[str, str]] = []
        message = scenario["start_prompt"]
        context.n_input_tokens = 0
        context.n_output_tokens = 0
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        async with AsyncOpenAI(
            api_key=access["OPENAI_API_KEY"], base_url=access["OPENAI_BASE_URL"], timeout=120
        ) as client:
            for _ in range(12):
                result = await environment.exec("acpx prompt " + shlex.quote(message))
                exchanges.append({"user": message, "target": result.stdout or ""})
                write_json(self.logs_dir / "conversation.json", {"exchanges": exchanges, "terminated": False})
                if result.return_code:
                    raise RuntimeError("The target ACP turn failed")
                history.extend(
                    [
                        {"role": "assistant", "content": json.dumps({"message": message, "done": False})},
                        {"role": "user", "content": result.stdout or ""},
                    ]
                )
                response = await client.chat.completions.create(
                    model=model,
                    messages=history,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                if response.usage:
                    context.n_input_tokens += response.usage.prompt_tokens
                    context.n_output_tokens += response.usage.completion_tokens
                decision = json.loads(response.choices[0].message.content or "")
                if (
                    not isinstance(decision, dict)
                    or type(decision.get("done")) is not bool
                    or not isinstance(decision.get("message"), str)
                ):
                    raise ValueError("The simulator must return a message and a Boolean stopping decision")
                if decision["done"]:
                    write_json(self.logs_dir / "conversation.json", {"exchanges": exchanges, "terminated": True})
                    context.metadata = {
                        "turns": len(exchanges),
                        "token_accounting": "simulator_only",
                        "target_model_evidence": "agent/llm_debug",
                        "termination": "simulator_stopped",
                        "target_success": "independently_graded",
                    }
                    return
                message = decision["message"]
                if not message.strip():
                    raise ValueError("The next simulated-user message is empty")
        raise RuntimeError("The simulator reached its 12-turn limit")
