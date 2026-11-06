# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve prepared agent inputs consistently for execution backends."""

from collections.abc import Callable

from harbor.models.trial.config import AgentConfig
from pydantic import JsonValue

from metric_benchmark.config import Agent


def resolved_agent_values(agent: Agent, resolve_input: Callable[[str], str]) -> dict[str, JsonValue]:
    def resolve(value: JsonValue) -> JsonValue:
        if isinstance(value, str) and value.startswith("{input:") and value.endswith("}"):
            name = value[7:-1]
            if name not in agent.inputs:
                raise ValueError(f"unknown agent input: {name}")
            return resolve_input(name)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    values = {key: resolve(value) for key, value in agent.harbor.items()}
    if set(values) - AgentConfig.model_fields.keys():
        raise ValueError("unknown Harbor agent configuration field")
    if bool(values.get("name")) == bool(values.get("import_path")):
        raise ValueError("agent must select exactly one Harbor name or import_path")
    AgentConfig.model_validate(values)
    return values
