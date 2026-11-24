# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted Harbor agent for generation-benchmark smoke verification."""

from __future__ import annotations

import re
from typing import ClassVar

from harbor.agents.base import AgentContext, BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.environments.base import BaseEnvironment
from harbor.models.trajectories import Agent as TrajectoryAgent
from harbor.models.trajectories import Step, ToolCall, Trajectory

_TOOL_LINE_RE = re.compile(r"TOOL:\s*([A-Za-z][A-Za-z0-9_.:/-]*)")


class SmokeAgent(BaseAgent):
    """Reads ``TOOL: <name>`` from instruction, writes ``/app/answer``, and emits ATIF."""

    capabilities: ClassVar[AgentCapabilities] = AgentCapabilities(atif=True, windows=False)

    @staticmethod
    def name() -> str:
        return "smoke"

    def version(self) -> str:
        return "1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        match = _TOOL_LINE_RE.search(instruction)
        if match is None:
            raise ValueError("SmokeAgent expected 'TOOL: <name>' in task instruction")
        tool_name = match.group(1)

        exec_result = await environment.exec("mkdir -p /app && printf ready > /app/answer")
        if exec_result.return_code != 0:
            raise RuntimeError(f"SmokeAgent command failed ({exec_result.return_code}): {exec_result.stderr}")

        session_id = self.session_id or self.logs_dir.parent.name
        trajectory = Trajectory(
            schema_version="ATIF-v1.8",
            session_id=session_id,
            agent=TrajectoryAgent(name=self.name(), version=self.version()),
            steps=[
                Step(step_id=1, source="user", message=instruction),
                Step(
                    step_id=2,
                    source="agent",
                    message=f"Executed smoke tool {tool_name}.",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="call-1",
                            function_name=tool_name,
                            arguments={},
                        )
                    ],
                ),
            ],
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "trajectory.json").write_text(
            trajectory.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        context.metadata = {"smoke_tool": tool_name}
