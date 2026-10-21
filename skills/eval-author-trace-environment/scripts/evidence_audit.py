# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic evidence coverage for a safe Compass Trace; never a correctness judge."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from fixture_compiler import canonical_json, result_entry, result_signature
from trace_source import tool_spans

if TYPE_CHECKING:
    from trace_ingest import Trace

SCHEMA = "nemo.eval_author.trace_environment_evidence_audit.v2"


def audit_evidence(trace: Trace, *, safe_trace_sha256: str) -> dict[str, Any]:
    """Locate findings by safe-trace span and turn ids, never by trace text."""
    from trace_ingest import Initiator, TurnRole
    from trace_ingest.turns import iter_turns

    counts = dict.fromkeys(
        (
            "agents",
            "turns",
            "calls",
            "results",
            "paired_calls",
            "missing_result_calls",
            "multiple_result_calls",
            "unlinked_results",
            "harness_initiated_calls",
            "error_results",
            "not_error_results",
            "unrecorded_status_results",
            "invalid_status_results",
            "conflicting_status_results",
            "agent_turns_without_results",
            "conflicting_input_groups",
        ),
        0,
    )
    findings: list[dict[str, Any]] = []

    def record_status(status: str, location: dict[str, Any]) -> None:
        counts[f"{status}_results" if status in {"error", "not_error"} else f"{status}_status_results"] += 1
        if status != "not_error":
            findings.append({"code": f"result_status_{status}", **location})

    results_by_span = {
        span.id: len(span.tool_call.results) if span.tool_call is not None else 0 for _, span in tool_spans(trace)
    }
    agents: set[str] = set()
    for agent, turn in iter_turns(trace):
        agents.add(agent.id)
        counts["turns"] += 1
        result_count = len(turn.unlinked_results) + sum(results_by_span[span_id] for span_id in turn.tool_call_span_ids)
        if turn.role is TurnRole.AGENT and not result_count:
            counts["agent_turns_without_results"] += 1
            findings.append({"code": "agent_turn_without_results", "turn_id": turn.id})
        for index, result in enumerate(turn.unlinked_results):
            counts["results"] += 1
            counts["unlinked_results"] += 1
            location = {"turn_id": turn.id, "unlinked_result": index}
            findings.append({"code": "unlinked_result", **location})
            record_status(result.error_status.value, location)
    counts["agents"] = len(agents)

    # Scope is the owning AGENT span AND the explicit tool server. Reused call ids in
    # other turns or agents never join results; Compass pairs them per call.
    groups: dict[str, list[tuple[str, str]]] = {}
    for agent_span_id, span in tool_spans(trace):
        counts["calls"] += 1
        call = span.tool_call
        results = call.results if call is not None else []
        if call is not None and call.initiator is Initiator.HARNESS:
            counts["harness_initiated_calls"] += 1
        for index, result in enumerate(results):
            counts["results"] += 1
            record_status(result.error_status.value, {"span_id": span.id, "result": index})
        if len(results) != 1:
            code = "missing_result" if not results else "multiple_results"
            counts["missing_result_calls" if not results else "multiple_result_calls"] += 1
            findings.append({"code": code, "span_id": span.id})
            continue
        counts["paired_calls"] += 1
        name = span.tool_name.strip() if isinstance(span.tool_name, str) else ""
        if not name or not isinstance(span.input, dict):
            continue  # the tool-access plan reports schema and name problems
        server = call.server if call is not None and call.server else None
        key = canonical_json([agent_span_id, server, name, span.input])
        groups.setdefault(key, []).append((span.id, result_signature(result_entry(results[0]))))
    for entries in groups.values():
        if len({signature for _, signature in entries}) > 1:
            counts["conflicting_input_groups"] += 1
            findings.append(
                {"code": "conflicting_results_for_identical_arguments", "span_ids": [span_id for span_id, _ in entries]}
            )
    return {
        "schema": SCHEMA,
        "safe_trace_sha256": safe_trace_sha256,
        "scope": "recorded_result_coverage",
        "execution_verified": False,
        "counts": counts,
        "finding_counts": dict(sorted(Counter(item["code"] for item in findings).items())),
        "findings": findings,
    }
