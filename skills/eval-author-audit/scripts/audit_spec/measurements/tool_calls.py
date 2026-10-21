#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic audit coverage from trace tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

from measurements.trace_tools import collect_tool_calls, tool_call_counts

if TYPE_CHECKING:
    from trace_ingest import Trace

JsonObject: TypeAlias = dict[str, Any]

METHOD_NAME = "tool_calls"
ITEM_KIND = "tool"
DETAILS_SCHEMA = "nemo.eval_author.audit_tool_calls_details.v2"


def measure(audit: JsonObject, trace: Trace) -> JsonObject:
    """Measure audit tool items against the tool calls present in a trace."""
    audit_tools = [item["name"] for item in audit["items"] if item["kind"] == ITEM_KIND]
    observed_tool_calls = collect_tool_calls(trace)
    matches = {tool: [call for call in observed_tool_calls if call["tool"] == tool] for tool in audit_tools}
    covered = [tool for tool in audit_tools if matches[tool]]
    missing = [tool for tool in audit_tools if tool not in covered]
    return {
        "item_kind": ITEM_KIND,
        "covered": covered,
        "details": {
            "schema": DETAILS_SCHEMA,
            "item_kind": ITEM_KIND,
            "audit_tools": audit_tools,
            "covered": covered,
            "missing": missing,
            "observed_tool_calls": observed_tool_calls,
            "tool_call_counts": tool_call_counts(observed_tool_calls),
            "matches": matches,
        },
    }
