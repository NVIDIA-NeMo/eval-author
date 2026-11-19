# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check target settings that Harbor's stock ACP paths cannot preserve."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harbor.agents.base import BaseAgent
    from harbor.models.trial.config import AgentConfig


def validate_acp_target_settings(
    target: AgentConfig, agent_class: type[BaseAgent], *, task_mcp_servers: bool = False
) -> None:
    from harbor.agents.installed.claude_code import ClaudeCode
    from harbor.agents.installed.codex import Codex
    from harbor.agents.installed.gemini_cli import GeminiCli
    from harbor.agents.installed.opencode import OpenCode
    from harbor.utils.env import resolve_env_vars

    # Harbor 0.24 skips run() in ACP mode, including its prompt decorator and
    # these CLI/config hooks. Custom ACP implementations need their own proof.
    unsupported = {
        ClaudeCode: (
            "config",
            "append_system_prompt",
            "allowed_tools",
            "disallowed_tools",
            "disable_web_search",
            "max_turns",
            "max_budget_usd",
            "fallback_model",
            "thinking_display",
            "permission_mode",
            "memory_dir",
        ),
        Codex: (),
        GeminiCli: ("sandbox",),
        OpenCode: ("variant",),
    }
    for stock_class, options in unsupported.items():
        if (
            not issubclass(agent_class, stock_class)
            or agent_class.acp_install is not stock_class.acp_install
            or agent_class.acp_command is not stock_class.acp_command
            or agent_class.acp_env is not stock_class.acp_env
        ):
            continue
        try:
            effective = agent_class.parse_options(target.kwargs, resolve_env_vars(target.env))
        except ValueError as error:
            raise ValueError("Could not validate the target's effective ACP options") from error
        settings = effective.model_dump(exclude_unset=True) if effective is not None else target.kwargs
        ignored = [
            f"options.{key}"
            for key in ("prompt_template_path", *options)
            if settings.get(key) is not None and not (key == "disable_web_search" and settings[key] is False)
        ]
        if stock_class is ClaudeCode and (target.mcp_servers or target.kwargs.get("mcp_servers") or task_mcp_servers):
            ignored.append("mcp_servers")
        if stock_class in (ClaudeCode, GeminiCli) and (target.skills or target.kwargs.get("skills_dir")):
            ignored.append("skills")
        if ignored:
            raise ValueError(
                f"The stock {stock_class.name()} ACP target does not apply: {', '.join(ignored)}. "
                "Use an interactive adapter that preserves these settings and verify its effective policy and tools. "
                "Do not drop target settings to make preflight pass."
            )
        return


def validate_acpx_config_file(path: Path) -> None:
    from harbor.bridges.acp import validate_acpx_config

    overrides = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(overrides, dict):
        raise ValueError("ACPX overrides must contain an object")
    validate_acpx_config(overrides)
