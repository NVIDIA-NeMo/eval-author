# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure derivation helpers for tool-call fixtures backed by a safe Compass Trace."""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from collections import Counter
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from trace_source import TraceIndex, tool_spans

if TYPE_CHECKING:
    from trace_ingest import ToolDefinition, ToolResult, Trace

INVENTORY_SCHEMA = "nemo.eval_author.trace_environment_tool_calls.v3"
PLAN_SCHEMA = "nemo.eval_author.trace_environment_tool_call_plan.v3"
DECISIONS_SCHEMA = "nemo.eval_author.trace_environment_tool_access_decisions.v1"
ACCESS_SCHEMA = "nemo.eval_author.trace_environment_tool_access.v3"
CALL_FIXTURES_SCHEMA = "nemo.eval_author.trace_environment_call_fixtures.v3"
MCP_SCENARIO_SCHEMA = "nemo.eval_author.trace_environment_mcp_scenario.v2"
_UNUSABLE_MARKERS = ("<redacted:", "[image omitted]")
_ACCESS_STATES = frozenset({"real", "mock", "none"})
_MOCK_ADAPTERS = frozenset({"mcp"})
OVERRIDES_SCHEMA = "nemo.eval_author.trace_environment_schema_overrides.v2"
_EVIDENCE_KEYS = ("span_id", "call_id")


def _tool_id(agent_span_id: str | None, server: str | None, name: str) -> str:
    digest = hashlib.sha256(canonical_json([agent_span_id, server, name]).encode()).hexdigest()
    return f"tool-{digest[:24]}"


def evidence(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Content-free references to the cited TOOL spans and their recorded call ids."""
    return [{key: call[key] for key in _EVIDENCE_KEYS} for call in calls]


def evidence_turns(calls: list[dict[str, Any]]) -> list[str]:
    """Turn ids that issued the calls, in trace order without repeats."""
    return list(dict.fromkeys(call["turn_id"] for call in calls if call["turn_id"] is not None))


def _schema_problem(schema: dict[str, Any], calls: list[dict[str, Any]]) -> str | None:
    """Validate MCP object schemas and evidence offline, without resolving URLs."""
    from jsonschema import Draft202012Validator, validators
    from jsonschema.exceptions import SchemaError, ValidationError
    from referencing import Registry
    from referencing.exceptions import NoSuchResource, Unresolvable

    def deny_remote(uri: str) -> Any:
        raise NoSuchResource(ref=uri)

    if schema.get("type") != "object":
        return "input_schema_invalid"
    if "$schema" in schema and not isinstance(schema["$schema"], str):
        return "input_schema_invalid"
    try:
        # jsonschema warns before falling back on an unknown dialect. Treat that
        # provider signal as unsupported instead of accepting fallback semantics.
        with warnings.catch_warnings(action="error", category=DeprecationWarning):
            validator_type = validators.validator_for(schema) if "$schema" in schema else Draft202012Validator
    except DeprecationWarning:
        return "input_schema_dialect_unsupported"
    try:
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=Registry(retrieve=deny_remote))
        for call in calls:
            if call["arguments_status"] == "object":
                validator.validate(call["arguments"])
    except SchemaError:
        return "input_schema_invalid"
    except ValidationError:
        return "arguments_schema_mismatch"
    except Unresolvable:
        return "input_schema_reference_unavailable"
    return None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def result_entry(result: ToolResult) -> dict[str, Any]:
    """A recorded result: its content (null when absent) and Compass's explicit error status."""
    from trace_ingest import UNSET

    return {
        "content": None if result.content is UNSET else result.content,
        "error_status": result.error_status.value,
    }


def result_signature(result: dict[str, Any]) -> str:
    return canonical_json([result["content"], result["error_status"]])


def json_sha256(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _contains_unusable_marker(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in _UNUSABLE_MARKERS)
    if isinstance(value, list):
        return any(_contains_unusable_marker(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_unusable_marker(key) or _contains_unusable_marker(item) for key, item in value.items())
    return False


def _unusable_replay_values(tool: dict[str, Any]) -> bool:
    return _contains_unusable_marker(tool["normalized_definition"]) or any(
        _contains_unusable_marker(call["arguments"])
        or any(_contains_unusable_marker(result["content"]) for result in call["results"])
        for call in tool["calls"]
    )


def _definition_annotations(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    annotations = raw.get("annotations")
    if isinstance(annotations, dict):
        return annotations
    function = raw.get("function")
    if isinstance(function, dict) and isinstance(function.get("annotations"), dict):
        return function["annotations"]
    return {}


def _normalized_definition(definition: ToolDefinition) -> dict[str, Any] | None:
    name = definition.name.strip()
    if not name or not isinstance(definition.input_schema, dict):
        return None
    normalized: dict[str, Any] = {"name": name, "inputSchema": definition.input_schema}
    if definition.description and definition.description.strip():
        normalized["description"] = definition.description.strip()
    annotations = _definition_annotations(definition.raw)
    if annotations:
        normalized["annotations"] = annotations
    return normalized


def _distinct(definitions: list[ToolDefinition]) -> list[ToolDefinition]:
    """Keep one of each definition: complete ones by meaning, incomplete ones by recorded shape."""
    distinct: dict[str, ToolDefinition] = {}
    for definition in definitions:
        normalized = _normalized_definition(definition)
        key = canonical_json(normalized if normalized is not None else definition.model_dump(mode="json"))
        distinct.setdefault(key, definition)
    return list(distinct.values())


def _matching(definitions: list[ToolDefinition], server: str | None, name: str) -> list[ToolDefinition]:
    return _distinct([item for item in definitions if item.name.strip() == name and (item.server or None) == server])


def derive_tool_call_inventory(trace: Trace, *, safe_trace_sha256: str) -> dict[str, Any]:
    """Inventory every TOOL span, scoped to its owning AGENT span and explicit server."""
    from trace_ingest import UNSET
    from trace_ingest.catalog import definitions_in_scope, suggested_definitions

    scoped = definitions_in_scope(trace)
    suggested = suggested_definitions(trace)
    span_turn = TraceIndex(trace).span_turn
    tools: dict[str, dict[str, Any]] = {}
    unresolved_calls: list[dict[str, Any]] = []
    for agent_span_id, span in tool_spans(trace):
        call = span.tool_call
        call_id = call.call_id if call is not None else None
        name = span.tool_name.strip() if isinstance(span.tool_name, str) else ""
        if not name:
            unresolved_calls.append({"span_id": span.id, "call_id": call_id, "reason": "tool_name_missing"})
            continue
        # Only explicit server identity scopes a tool; never infer shared scope from a name.
        server = call.server if call is not None and call.server else None
        tool_id = _tool_id(agent_span_id, server, name)
        if tool_id not in tools:
            matches = _matching(scoped.get(span.id, []), server, name)
            complete = [item for item in map(_normalized_definition, matches) if item is not None]
            if not matches:
                definition_status = "missing"
            elif len(matches) == 1:
                definition_status = "complete" if complete else "incomplete"
            else:
                definition_status = "ambiguous"
            normalized_definition = complete[0] if definition_status == "complete" else None
            # A definition declared beyond the agent boundary is only ever a suggestion.
            outer = _matching(suggested.get(span.id, []), server, name) if not matches else []
            suggestion = _normalized_definition(outer[0]) if len(outer) == 1 else None
            annotations = normalized_definition.get("annotations", {}) if normalized_definition else {}
            read_only = annotations.get("readOnlyHint")
            tools[tool_id] = {
                "tool_id": tool_id,
                "scope": {"agent_span_id": agent_span_id, "server": server},
                "name": name,
                "definition_status": definition_status,
                "definition": matches[0].model_dump(mode="json") if len(matches) == 1 else None,
                "normalized_definition": normalized_definition,
                "suggested_definition": suggestion,
                "read_only": read_only if type(read_only) is bool else None,
                "read_only_evidence": "mcp_annotation" if type(read_only) is bool else None,
                "calls": [],
                "uncertainties": [],
            }
        arguments = None if span.input is UNSET else span.input
        tools[tool_id]["calls"].append(
            {
                "span_id": span.id,
                "turn_id": span_turn.get(span.id),
                "call_id": call_id,
                "initiator": call.initiator.value if call is not None and call.initiator is not None else None,
                "arguments": arguments,
                "arguments_status": "object" if isinstance(arguments, dict) else "missing_or_non_object",
                "results": [result_entry(result) for result in (call.results if call is not None else [])],
            }
        )

    inventory_tools: list[dict[str, Any]] = []
    for tool in tools.values():
        uncertainties = tool["uncertainties"]
        if tool["definition_status"] != "complete":
            uncertainties.append(f"tool_definition_{tool['definition_status']}")
        if any(call["arguments_status"] != "object" for call in tool["calls"]):
            uncertainties.append("arguments_missing_or_non_object")
        if any(len(call["results"]) != 1 for call in tool["calls"]):
            uncertainties.append("observation_pairing_incomplete")
        if _unusable_replay_values(tool):
            uncertainties.append("redacted_value_required")
        inventory_tools.append(tool)

    return {
        "schema": INVENTORY_SCHEMA,
        "safe_trace_sha256": safe_trace_sha256,
        "tool_count": len(inventory_tools),
        "call_count": sum(len(tool["calls"]) for tool in inventory_tools) + len(unresolved_calls),
        "unresolved_call_count": len(unresolved_calls),
        "unresolved_calls": unresolved_calls,
        "tools": inventory_tools,
    }


def _plan_tool(tool: dict[str, Any]) -> dict[str, Any]:
    reason_codes: list[str] = []
    warnings: list[str] = []
    calls = tool["calls"]
    if tool["definition_status"] != "complete":
        reason_codes.append(f"tool_definition_{tool['definition_status']}")
    elif problem := _schema_problem(tool["normalized_definition"]["inputSchema"], calls):
        reason_codes.append(problem)
    if any(call["arguments_status"] != "object" for call in calls):
        reason_codes.append("arguments_missing_or_non_object")
    if any(len(call["results"]) != 1 for call in calls):
        reason_codes.append("observation_pairing_incomplete")
    if any(len(call["results"]) == 1 and not isinstance(call["results"][0]["content"], str) for call in calls):
        reason_codes.append("non_text_result_unsupported")
    if _unusable_replay_values(tool):
        reason_codes.append("redacted_value_required")
    statuses = {result["error_status"] for call in calls for result in call["results"]}
    if "invalid" in statuses:
        reason_codes.append("result_error_status_invalid")
    if "conflicting" in statuses:
        reason_codes.append("result_error_status_conflicting")

    responses_by_arguments: dict[str, set[str]] = {}
    for call in calls:
        if call["arguments_status"] != "object" or len(call["results"]) != 1:
            continue
        if not isinstance(call["results"][0]["content"], str):
            continue
        key = canonical_json(call["arguments"])
        responses_by_arguments.setdefault(key, set()).add(result_signature(call["results"][0]))
    if any(len(responses) > 1 for responses in responses_by_arguments.values()):
        reason_codes.append("conflicting_results_for_identical_arguments")

    if tool["read_only"] is False:
        warnings.append("tool_declared_mutating")
    elif tool["read_only"] is None:
        warnings.append("side_effects_unproven")

    suggestion = tool["suggested_definition"] if tool["definition_status"] != "complete" else None
    return {
        "tool_id": tool["tool_id"],
        "scope": tool["scope"],
        "name": tool["name"],
        "definition": tool["normalized_definition"],
        "definition_provenance": tool.get("definition_provenance", {"kind": "trace"}),
        # A definition declared beyond the agent boundary, offered as an override entry
        # the reviewer may accept by adding a note; it is never applied automatically.
        "suggested_override": (
            {"tool_id": tool["tool_id"], "input_schema": suggestion["inputSchema"], "evidence": evidence(calls)}
            if suggestion is not None
            else None
        ),
        "mock_support": "exact_replay" if not reason_codes else "unsupported",
        "reason_codes": reason_codes,
        "warnings": warnings,
        "evidence_turns": evidence_turns(calls),
        "evidence": evidence(calls),
        "call_count": len(calls),
        "case_count": len(responses_by_arguments),
    }


def derive_tool_call_plan(inventory: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    effective = deepcopy(inventory["tools"])
    if overrides is not None:
        if (
            set(overrides) != {"schema", "inventory_sha256", "reviewer_kind", "entries"}
            or overrides.get("schema") != OVERRIDES_SCHEMA
            or overrides.get("inventory_sha256") != json_sha256(inventory)
            or overrides.get("reviewer_kind") not in ("agent", "human")
            or not isinstance(overrides.get("entries"), list)
        ):
            raise ValueError("schema overrides must be reviewed and bound to the current inventory")
        by_id = {tool["tool_id"]: tool for tool in effective}
        seen = set()
        for entry in overrides["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"tool_id", "input_schema", "note", "evidence"}:
                raise ValueError("schema override fields do not match the versioned contract")
            tool_id = entry["tool_id"]
            if not isinstance(tool_id, str) or tool_id not in by_id or tool_id in seen:
                raise ValueError("schema override has an unknown or duplicate tool_id")
            seen.add(tool_id)
            tool = by_id[tool_id]
            if (
                entry["evidence"] != evidence(tool["calls"])
                or not isinstance(entry["note"], str)
                or not entry["note"].strip()
            ):
                raise ValueError("schema override requires a review note and every scoped call reference")
            schema = entry["input_schema"]
            if not isinstance(schema, dict) or _contains_unusable_marker(entry):
                raise ValueError("schema override requires an unredacted input schema")
            if problem := _schema_problem(schema, tool["calls"]):
                raise ValueError(f"schema override is unusable: {problem}")
            # An override is an author-owned adapter contract, never trace evidence.
            tool["normalized_definition"] = {"name": tool["name"], "inputSchema": schema}
            tool["definition_status"] = "complete"
            tool["definition_provenance"] = {
                "kind": "reviewed_override",
                "reviewer_kind": overrides["reviewer_kind"],
                **entry,
            }
    tools = [_plan_tool(tool) for tool in effective]
    name_counts = Counter(tool["name"] for tool in tools)
    used_names = {
        name for name, count in name_counts.items() if count == 1 and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
    }
    for tool in tools:
        name = tool["name"]
        if name_counts[name] > 1 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            base = f"replay_{tool['tool_id'].replace('-', '_')}"
            name = base
            suffix = 0
            while name in used_names:
                suffix += 1
                name = f"{base}_{suffix}"
            used_names.add(name)
        tool["replay_name"] = name
    counts: dict[str, int] = {}
    for tool in tools:
        support = tool["mock_support"]
        counts[support] = counts.get(support, 0) + 1
    return {
        "schema": PLAN_SCHEMA,
        "inventory_sha256": json_sha256(inventory),
        "schema_overrides_sha256": json_sha256(overrides) if overrides is not None else None,
        "tools": tools,
        "mock_support_counts": dict(sorted(counts.items())),
    }


def resolve_tool_access(
    inventory: dict[str, Any],
    plan: dict[str, Any],
    requested: dict[str, Any],
    *,
    reviewer_kind: str,
) -> dict[str, Any]:
    if set(requested) != {"schema", "decisions"} or requested.get("schema") != DECISIONS_SCHEMA:
        raise ValueError("tool access decisions do not match the versioned contract")
    decisions = requested.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("tool access decisions must contain a decisions list")
    if reviewer_kind not in {"agent", "human"}:
        raise ValueError("tool access reviewer_kind must be agent or human")

    if inventory["unresolved_call_count"]:
        raise ValueError("unresolved tool calls require corrected source evidence or no_candidate")
    inventory_order = [tool["tool_id"] for tool in inventory["tools"]]
    plan_by_name = {tool["tool_id"]: tool for tool in plan["tools"]}
    requested_by_name: dict[str, dict[str, Any]] = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or set(decision) not in (
            {"name", "access", "adapter", "note"},
            {"tool_id", "name", "access", "adapter", "note"},
        ):
            raise ValueError(f"tool access decision {index} fields do not match the versioned contract")
        name = decision.get("name")
        matching = [tool for tool in plan["tools"] if tool["name"] == name]
        tool_id = decision.get("tool_id")
        if tool_id is None and len(matching) == 1:
            tool_id = matching[0]["tool_id"]
        if not isinstance(tool_id, str) or tool_id not in plan_by_name or plan_by_name[tool_id]["name"] != name:
            raise ValueError(f"tool access decision {index} requires an unambiguous scoped tool_id and name")
        access = decision.get("access")
        adapter = decision.get("adapter")
        note = decision.get("note")
        if not isinstance(name, str) or tool_id in requested_by_name:
            raise ValueError(f"tool access decision {index} has an unknown or duplicate name")
        if not isinstance(access, str) or access not in _ACCESS_STATES:
            raise ValueError(f"tool access decision for {name} must select real, mock, or none")
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"tool access decision for {name} requires a non-empty note")
        if access == "mock":
            if not isinstance(adapter, str) or adapter not in _MOCK_ADAPTERS:
                raise ValueError(f"mock access for {name} requires a supported adapter")
            if plan_by_name[tool_id]["mock_support"] != "exact_replay":
                reasons = ", ".join(plan_by_name[tool_id]["reason_codes"])
                raise ValueError(f"mock access for {name} is unsupported: {reasons}")
        elif adapter is not None:
            raise ValueError(f"{access} access for {name} must not select a mock adapter")
        requested_by_name[tool_id] = {
            "tool_id": tool_id,
            "name": name,
            "access": access,
            "adapter": adapter,
            "note": note.strip(),
            **{
                key: plan_by_name[tool_id][key]
                for key in (
                    "warnings",
                    "evidence_turns",
                    "evidence",
                    "scope",
                    "definition",
                    "definition_provenance",
                    "replay_name",
                )
            },
        }
    missing = [name for name in inventory_order if name not in requested_by_name]
    if missing:
        raise ValueError(f"tool access decisions are missing: {', '.join(missing)}")

    counts: dict[str, int] = {}
    resolved = [requested_by_name[name] for name in inventory_order]
    for decision in resolved:
        access = decision["access"]
        counts[access] = counts.get(access, 0) + 1
    return {
        "schema": ACCESS_SCHEMA,
        "inventory_sha256": json_sha256(inventory),
        "plan_sha256": json_sha256(plan),
        "reviewer_kind": reviewer_kind,
        "decisions": resolved,
        "access_counts": dict(sorted(counts.items())),
    }


def build_call_fixtures(inventory: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
    access_by_name = {decision["tool_id"]: decision for decision in access["decisions"]}
    fixture_tools: list[dict[str, Any]] = []
    for tool in inventory["tools"]:
        decision = access_by_name[tool["tool_id"]]
        if decision["access"] != "mock":
            continue
        cases_by_input: dict[str, dict[str, Any]] = {}
        for call in tool["calls"]:
            key = canonical_json(call["arguments"])
            result = call["results"][0]
            case = cases_by_input.setdefault(
                key,
                {"input": call["arguments"], "output": result["content"], "evidence_turns": [], "evidence": []},
            )
            status = result["error_status"]
            if status in {"invalid", "conflicting"}:
                raise ValueError("cannot generate fixtures with ambiguous result error status")
            if status != "unrecorded":
                case["is_error"] = status == "error"
            if call["turn_id"] is not None and call["turn_id"] not in case["evidence_turns"]:
                case["evidence_turns"].append(call["turn_id"])
            case["evidence"].extend(evidence([call]))
        fixture_tools.append(
            {
                "name": tool["name"],
                "tool_id": tool["tool_id"],
                "scope": tool["scope"],
                "replay_name": decision["replay_name"],
                "definition": decision["definition"],
                "definition_provenance": decision["definition_provenance"],
                "adapter": decision["adapter"],
                "cases": list(cases_by_input.values()),
            }
        )
    return {
        "schema": CALL_FIXTURES_SCHEMA,
        "inventory_sha256": json_sha256(inventory),
        "access_sha256": json_sha256(access),
        "matching": "tool_name_and_canonical_json_input",
        "unknown_call_policy": "error",
        "tools": fixture_tools,
    }


def build_mcp_scenario(call_fixtures: dict[str, Any]) -> dict[str, Any]:
    tools = []
    for fixture in call_fixtures["tools"]:
        if fixture["adapter"] != "mcp":
            continue
        tools.append(
            {
                "definition": {**fixture["definition"], "name": fixture["replay_name"]},
                "cases": [
                    {
                        "arguments": case["input"],
                        "result": {
                            "content": [{"type": "text", "text": case["output"]}],
                            "isError": case.get("is_error", False),
                        },
                        "evidence_turns": case["evidence_turns"],
                        "evidence": case["evidence"],
                    }
                    for case in fixture["cases"]
                ],
            }
        )
    return {
        "schema": MCP_SCENARIO_SCHEMA,
        "call_fixtures_sha256": json_sha256(call_fixtures),
        "unknown_call_policy": call_fixtures["unknown_call_policy"],
        "tools": tools,
    }
