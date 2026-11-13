# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find repeated-operation candidates deterministically in a safe Compass Trace. Do not judge correctness."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from .evidence_audit import call_key
from .trace_view import error_status, iter_turns, spans_by_id, tool_results, tool_spans

if TYPE_CHECKING:
    from trace_ingest import Span, Trace

SCHEMA = "nemo.eval_author.retry_audit.v1"
DETECTOR = "repeat-after-error-v1"
LIMITATIONS = (
    "The trace records no material artifact or environment state, so no repeat is a confirmed violation.",
    "Recorded results do not show which feedback reached the model before the repeat.",
    "The audit compares only exact repeats of the same scoped tool and canonical arguments.",
    "Recovery covers a later identical call with an explicit false error flag, not proof of correctness. "
    "Other remedies are outside scope.",
    "Token counts come from each source span. The audit infers no model window, estimate, or compaction boundary.",
)


def _status(span: Span) -> str:
    results = tool_results(span)
    return error_status(results[0]) if len(results) == 1 else "unobservable"


def _observed_input_tokens(spans: dict[str, Span], llm_span_ids: list[str]) -> list[int]:
    observed = []
    for span_id in llm_span_ids:
        counts = spans[span_id].token_counts if span_id in spans else None
        value = None if counts is None else counts.input_total
        if type(value) is int:
            observed.append(value)
    return observed


def audit_retries(trace: Trace, *, safe_trace_sha256: str) -> dict[str, Any]:
    """Match ordered scoped call keys one-to-one; emit only safe-trace citations and counts."""
    from trace_ingest import Initiator

    spans = spans_by_id(trace)
    owners = {span.id: owner for owner, span in tool_spans(trace)}
    ordered: dict[str | None, list[tuple[int, Any, Span]]] = {}
    placed = set()
    for agent, turn in iter_turns(trace):
        for span_id in turn.tool_call_span_ids:
            if span_id in owners and span_id not in placed:
                placed.add(span_id)
                ordered.setdefault(agent.id, []).append((turn.index, turn, spans[span_id]))
    feedback = Counter(_status(spans[span_id]) for span_id in owners)
    ineligible: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []
    unordered = [span_id for span_id in owners if span_id not in placed and _status(spans[span_id]) == "error"]
    ineligible["unordered"] += len(unordered)
    opportunities = candidates = no_repeat = 0
    for agent_id, calls in ordered.items():
        keys = [call_key(owners[span.id], span) for _, _, span in calls]
        attributed_repeats: set[int] = set()
        for index, (turn_index, turn, span) in enumerate(calls):
            if _status(span) != "error":
                continue
            call = span.tool_call
            if call is not None and call.initiator is Initiator.HARNESS:
                ineligible["harness_initiated"] += 1
                continue
            if keys[index] is None:
                ineligible["input_unavailable"] += 1
                continue
            opportunities += 1
            later = [
                position
                for position in range(index + 1, len(calls))
                if keys[position] == keys[index] and calls[position][0] > turn_index
            ]
            if not later:
                no_repeat += 1
                continue
            repeat = next((position for position in later if position not in attributed_repeats), None)
            if repeat is None:
                unknown["repeat_already_attributed"] += 1
                continue
            attributed_repeats.add(repeat)
            repeated_call = calls[repeat][2].tool_call
            if repeated_call is not None and repeated_call.initiator is Initiator.HARNESS:
                excluded["harness_initiated_repeat"] += 1
                continue
            if any(keys[position] != keys[index] for position in range(index + 1, repeat)):
                excluded["intervening_action"] += 1
                continue
            outcome = _status(calls[repeat][2])
            if outcome == "not_error":
                excluded["repeat_succeeded"] += 1
                continue
            if outcome != "error":
                unknown["repeat_status_" + outcome] += 1
                continue
            candidates += 1
            recovery = next(
                (
                    calls[position][2].id
                    for position in later
                    if position > repeat and _status(calls[position][2]) == "not_error"
                ),
                None,
            )
            repeated_turn = calls[repeat][1]
            findings.append(
                {
                    "code": "repeat_after_error",
                    "outcome": "review_candidate",
                    "agent_span_id": agent_id,
                    "span_ids": [span.id, calls[repeat][2].id],
                    "turn_ids": [turn.id, repeated_turn.id],
                    "later_success_span_id": recovery,
                    "observed_input_tokens": _observed_input_tokens(spans, list(repeated_turn.llm_span_ids)),
                }
            )
    for span_id in unordered:
        findings.append({"code": "error_call_unordered", "outcome": "ineligible", "span_ids": [span_id]})
    assessed = opportunities - sum(unknown.values()) - sum(excluded.values())
    return {
        "schema": SCHEMA,
        "safe_trace_sha256": safe_trace_sha256,
        "detector_version": DETECTOR,
        "scope": "recorded_tool_repeats",
        "execution_verified": False,
        "state_identity": "unavailable",
        "feedback_visibility": "unknown",
        "feedback": dict(sorted(feedback.items())),
        "counts": {
            "tool_calls": len(owners),
            "eligible_opportunities": opportunities,
            "assessed_opportunities": assessed,
            "review_candidates": candidates,
            "confirmed_violations": 0,
            "no_repeat": no_repeat,
            "excluded": sum(excluded.values()),
            "unknown": sum(unknown.values()),
            "ineligible": sum(ineligible.values()),
        },
        "ineligible": dict(sorted(ineligible.items())),
        "excluded": dict(sorted(excluded.items())),
        "unknown": dict(sorted(unknown.items())),
        "trials": {"observable": int(assessed > 0), "affected": int(candidates > 0)},
        "candidate_rate": candidates / assessed if assessed else None,
        "limitations": list(LIMITATIONS),
        "findings": findings,
    }
