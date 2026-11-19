# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate a portable user-simulation scenario without running either agent."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal, TypeAlias, cast

import yaml
from yaml.nodes import MappingNode, Node

MAX_SCENARIO_BYTES = 256 * 1024
MAX_DEPTH = 64
MAX_VALUES = 10000
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
SCHEMA_VERSION: Literal[1] = 1
NEUTRAL_USER_TYPE: Literal["neutral"] = "neutral"


class UserSimError(ValueError):
    """The supplied scenario cannot be used as a user-simulation input."""


@dataclass(frozen=True)
class UserSimScenario:
    schema_version: Literal[1]
    task_id: str
    goal: str
    start_prompt: str
    user_type: Literal["neutral"] = NEUTRAL_USER_TYPE
    user_facts: dict[str, JSONValue] = field(default_factory=dict)
    disclosure_rules: tuple[str, ...] = ()
    stopping_conditions: tuple[str, ...] = ()
    conversation_prompt: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["disclosure_rules"] = list(self.disclosure_rules)
        result["stopping_conditions"] = list(self.stopping_conditions)
        if self.conversation_prompt is None:
            result.pop("conversation_prompt")
        return result


FIELDS = frozenset(item.name for item in fields(UserSimScenario))


class _ScenarioLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._scenario_depth = 0
        self._scenario_nodes = 0

    def compose_node(self, parent: Node | None, index: object) -> Node:
        self._scenario_depth += 1
        self._scenario_nodes += 1
        try:
            if self._scenario_depth > MAX_DEPTH or self._scenario_nodes > MAX_VALUES:
                raise UserSimError("Scenario exceeds the nesting or value limit")
            return super().compose_node(parent, index)
        finally:
            self._scenario_depth -= 1

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[str, object]:
        if not isinstance(node, MappingNode):
            raise UserSimError("Expected a YAML mapping")
        result: dict[str, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise UserSimError("Mapping keys must be strings")
            if key in result:
                raise UserSimError("Duplicate mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass
class _FactBudget:
    values: int = 0
    ancestors: set[int] = field(default_factory=set)


def _json_value(value: object, budget: _FactBudget, depth: int = 0) -> JSONValue:
    budget.values += 1
    if depth > MAX_DEPTH or budget.values > MAX_VALUES:
        raise UserSimError("user_facts exceeds the nesting or value limit")
    if type(value) is int:
        try:
            str(value)
        except ValueError as error:
            raise UserSimError("user_facts integers must be JSON-serializable") from error
        return value
    if value is None or type(value) in {str, bool}:
        return cast(JSONValue, value)
    if type(value) is float:
        if not math.isfinite(value):
            raise UserSimError("user_facts numbers must be finite")
        return value
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in budget.ancestors:
            raise UserSimError("Recursive aliases are not supported")
        budget.ancestors.add(identity)
        try:
            if isinstance(value, list):
                return [_json_value(item, budget, depth + 1) for item in value]
            if any(not isinstance(key, str) for key in value):
                raise UserSimError("user_facts mapping keys must be strings")
            return {key: _json_value(item, budget, depth + 1) for key, item in value.items()}
        finally:
            budget.ancestors.remove(identity)
    raise UserSimError("user_facts must contain only JSON-compatible values")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UserSimError(f"{name} must be a nonempty string")
    return value


def _rules(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise UserSimError(f"{name} must be a list of nonempty strings")
    return tuple(_text(item, name) for item in value)


def validate_scenario(value: object) -> UserSimScenario:
    if not isinstance(value, dict):
        raise UserSimError("Scenario must be a mapping")
    if any(not isinstance(key, str) or key not in FIELDS for key in value):
        raise UserSimError("Scenario contains an unsupported top-level field")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise UserSimError("schema_version must be the integer 1")
    if value.get("user_type", NEUTRAL_USER_TYPE) != NEUTRAL_USER_TYPE:
        raise UserSimError("user_type must be neutral")
    facts = value.get("user_facts", {})
    if not isinstance(facts, dict):
        raise UserSimError("user_facts must be a mapping")
    checked_facts = _json_value(facts, _FactBudget())
    assert isinstance(checked_facts, dict)
    return UserSimScenario(
        schema_version=SCHEMA_VERSION,
        task_id=_text(value.get("task_id"), "task_id"),
        goal=_text(value.get("goal"), "goal"),
        start_prompt=_text(value.get("start_prompt"), "start_prompt"),
        user_facts=checked_facts,
        disclosure_rules=_rules(value.get("disclosure_rules", []), "disclosure_rules"),
        stopping_conditions=_rules(value.get("stopping_conditions", []), "stopping_conditions"),
        conversation_prompt=(
            _text(value["conversation_prompt"], "conversation_prompt") if "conversation_prompt" in value else None
        ),
    )


def load_scenario(path: Path) -> UserSimScenario:
    with path.open("rb") as source:
        data = source.read(MAX_SCENARIO_BYTES + 1)
    if len(data) > MAX_SCENARIO_BYTES:
        raise UserSimError(f"Scenario exceeds {MAX_SCENARIO_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UserSimError("Scenario must be UTF-8") from error
    try:
        value = yaml.load(text, Loader=_ScenarioLoader)
    except UserSimError:
        raise
    except (yaml.YAMLError, ValueError, OverflowError, KeyError, AttributeError, TypeError, IndexError) as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise UserSimError(f"Invalid or unsupported YAML{location}") from error
    return validate_scenario(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a user-sim.yaml scenario")
    validate.add_argument("--path", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        scenario = load_scenario(args.path)
    except (UserSimError, OSError) as error:
        print(json.dumps({"valid": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({"valid": True, "schema_version": scenario.schema_version, "task_id": scenario.task_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
