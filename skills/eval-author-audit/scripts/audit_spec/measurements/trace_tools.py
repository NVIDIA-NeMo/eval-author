#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared NeMo Compass trace helpers for deterministic audit measurement."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

from _values import optional_string

if TYPE_CHECKING:
    from trace_ingest import Span, Trace

JsonObject: TypeAlias = dict[str, Any]


def collect_tool_calls(trace: Trace) -> list[JsonObject]:
    """Return every named TOOL span in depth-first order, including nested agent spans."""
    from trace_ingest import SpanKind

    calls: list[JsonObject] = []
    trace_id = optional_string(trace.id)
    stack: list[Span] = list(reversed(trace.root_spans))
    while stack:
        span = stack.pop()
        stack.extend(reversed(span.children))
        if span.kind is not SpanKind.TOOL:
            continue
        tool = optional_string(span.tool_name)
        if tool is None:
            continue
        call: JsonObject = {"tool": tool, "span_id": span.id}
        tool_call_id = optional_string(span.tool_call.call_id) if span.tool_call is not None else None
        if tool_call_id is not None:
            call["tool_call_id"] = tool_call_id
        if trace_id is not None:
            call["trace_id"] = trace_id
        calls.append(call)
    return calls


def tool_call_counts(tool_calls: list[JsonObject]) -> dict[str, int]:
    """Return observed tool call counts keyed by tool name."""
    counts: dict[str, int] = {}
    for call in tool_calls:
        tool = call["tool"]
        counts[tool] = counts.get(tool, 0) + 1
    return counts
