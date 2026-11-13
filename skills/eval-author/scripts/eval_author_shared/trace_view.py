# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only views over one NeMo Compass ``Trace`` (contract v3).

Eval Author's single source for turn order, turn text, tool-result error status,
tool-definition scope, citation lookup, and the identifier classification the safe-copy
scrubber follows. Everything here is built only from the landed trace contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trace_ingest import Span, ToolDefinition, ToolResult, Trace, Turn

ERROR_STATUSES = ("error", "not_error", "invalid", "conflicting", "unrecorded")

# How the safe copy treats every field of every public Compass model. Roles:
#   keep         structural value, copied verbatim
#   text         free text; deterministic redaction applies, and source identifiers that
#                recur in it are pseudonymized
#   json         free-form source JSON; identifier-like keys (``id`` included) are
#                pseudonymized and other strings handled as text
#   replay_json  tool arguments, results and schemas; a generic ``id`` key stays, explicit
#                identifier keys are pseudonymized and other strings handled as text. Values
#                that lost source content are not offered for exact replay.
#   span_io      span input or output: replay_json on a TOOL span, json elsewhere
#   identifier   pseudonymized consistently
#   span_ref     span or turn id; loader positions stay, anything else is pseudonymized
#   url          source link; replaced with an opaque file URL
#   drop         source-specific attributes; removed
#   null         token ids, which decode back to unredacted text; set to null
#   omitted      media part; replaced with a text placeholder
#   content      turn content: text, or ordered parts
#   loss_detail  structured ``Loss.detail``
#   result_attributes  only the explicit error-status record survives
#   <Model>      nested Compass model(s)
# ``check_field_roles`` fails when Compass adds a model or field this table does not
# classify, so a new identifier can never reach the safe copy unnoticed.
FIELD_ROLES: dict[str, dict[str, str]] = {
    "Trace": {
        "id": "identifier",
        "schema_version": "keep",
        "session_id": "identifier",
        "session_basis": "keep",
        "case_id": "identifier",
        "source_url": "url",
        "root_spans": "Span",
        "aggregate": "TraceAggregate",
        "evaluator_results": "json",
        "attributes": "drop",
        "losses": "Loss",
    },
    "Span": {
        "id": "span_ref",
        "source_url": "url",
        "kind": "keep",
        "children": "Span",
        "start_time": "keep",
        "end_time": "keep",
        "input": "span_io",
        "output": "span_io",
        "cost_usd": "keep",
        "token_counts": "TokenCounts",
        "model": "keep",
        "llm_params": "json",
        "agent_id": "identifier",
        "tool_name": "keep",
        "tool_call": "ToolCall",
        "error": "text",
        "attributes": "drop",
        "tool_definitions": "ToolDefinition",
        "agent": "AgentInfo",
        "run": "RunInfo",
        "turns": "Turn",
        "llm_call": "LLMCallInfo",
    },
    "ToolCall": {
        "call_id": "identifier",
        "index": "keep",
        "result_id": "identifier",
        "result_count": "keep",
        "instrumentation_alias_of": "keep",
        "prior_user_text": "text",
        "returned_data": "keep",
        "results": "ToolResult",
        "server": "keep",
        "initiator": "keep",
        "extra": "json",
    },
    "ToolResult": {
        "content": "replay_json",
        "is_error": "keep",
        "subagent_refs": "SubagentRef",
        "extra": "json",
        "attributes": "result_attributes",
    },
    "SubagentRef": {
        "span_id": "span_ref",
        "trajectory_id": "identifier",
        "session_id": "identifier",
        "trajectory_path": "identifier",
        "extra": "json",
    },
    "ToolDefinition": {
        "name": "keep",
        "server": "keep",
        "description": "text",
        "input_schema": "replay_json",
        "raw": "replay_json",
    },
    "TokenCounts": {
        "input_total": "keep",
        "input_tokens": "keep",
        "cached_input_tokens": "keep",
        "output_tokens": "keep",
        "cache_write_tokens": "keep",
        "cache_writes_by_ttl": "keep",
        "source": "keep",
    },
    "TextPart": {"type": "keep", "text": "text"},
    "ImagePart": {"type": "omitted", "media_type": "omitted", "path": "omitted"},
    "AudioPart": {"type": "omitted", "media_type": "omitted", "path": "omitted", "duration_sec": "omitted"},
    "Turn": {
        "id": "span_ref",
        "index": "keep",
        "role": "keep",
        "authorship": "keep",
        "content": "content",
        "reasoning": "text",
        "timestamp": "keep",
        "llm_span_ids": "span_ref",
        "dispatch_span_id": "span_ref",
        "tool_call_span_ids": "span_ref",
        "unlinked_results": "ToolResult",
        "extra": "json",
    },
    "AgentInfo": {"name": "keep", "version": "keep", "model_name": "keep", "extra": "json"},
    "RunInfo": {
        "schema_version": "keep",
        "session_id": "identifier",
        "trajectory_id": "identifier",
        "notes": "text",
        "continued_trajectory_ref": "identifier",
        "total_steps": "keep",
        "final_metrics_extra": "json",
        "extra": "json",
    },
    "LLMCallInfo": {
        "call_count": "keep",
        "prompt_token_ids": "null",
        "completion_token_ids": "null",
        "logprobs": "keep",
        "metrics_extra": "json",
    },
    "Loss": {"code": "keep", "detail": "loss_detail", "span_id": "span_ref", "turn_id": "span_ref"},
    "TraceAggregate": {"cost_usd": "keep", "latency_ms": "keep", "token_counts": "TokenCounts"},
}


class TraceContractDrift(RuntimeError):
    """The installed Compass trace models differ from the fields Eval Author classifies."""


def check_field_roles() -> None:
    """Fail loudly when a public Compass model or field has no safe-copy classification."""
    import trace_ingest
    from pydantic import BaseModel

    models = {
        name: value
        for name in trace_ingest.__all__
        if isinstance(value := getattr(trace_ingest, name), type) and issubclass(value, BaseModel)
    }
    problems = [f"unclassified model {name}" for name in sorted(set(models) - set(FIELD_ROLES))]
    problems += [f"classified model {name} is not in Compass" for name in sorted(set(FIELD_ROLES) - set(models))]
    for name in sorted(set(models) & set(FIELD_ROLES)):
        fields = set(models[name].model_fields)
        problems += [f"unclassified field {name}.{field}" for field in sorted(fields - set(FIELD_ROLES[name]))]
        problems += [f"classified field {name}.{field} is gone" for field in sorted(set(FIELD_ROLES[name]) - fields)]
    if problems:
        raise TraceContractDrift(
            "the installed Compass trace contract differs from Eval Author's identifier table: " + "; ".join(problems)
        )


def _tree(trace: Trace) -> tuple[dict[str, Span], dict[str, str | None], dict[str, str | None]]:
    """Every span by id in depth-first tree order, its parent id, and its nearest AGENT ancestor id."""
    from trace_ingest import SpanKind

    spans: dict[str, Span] = {}
    parents: dict[str, str | None] = {}
    owners: dict[str, str | None] = {}
    pending: list[tuple[Span, str | None, str | None]] = [(span, None, None) for span in reversed(trace.root_spans)]
    while pending:
        span, parent, owner = pending.pop()
        spans[span.id], parents[span.id], owners[span.id] = span, parent, owner
        inner = span.id if span.kind is SpanKind.AGENT else owner
        pending.extend((child, span.id, inner) for child in reversed(span.children))
    return spans, parents, owners


def spans_by_id(trace: Trace) -> dict[str, Span]:
    """Every span keyed by id, in depth-first tree order."""
    return _tree(trace)[0]


def agent_spans(trace: Trace) -> list[Span]:
    """AGENT spans in depth-first tree order."""
    from trace_ingest import SpanKind

    return [span for span in _tree(trace)[0].values() if span.kind is SpanKind.AGENT]


def _turns(agent: Span) -> list[Turn]:
    from trace_ingest import UNSET

    return [] if agent.turns is UNSET else list(agent.turns)


def iter_turns(trace: Trace) -> Iterator[tuple[Span, Turn]]:
    """Each AGENT span's turns, AGENT spans in tree order; turn order holds within one agent only."""
    for agent in agent_spans(trace):
        for turn in _turns(agent):
            yield agent, turn


def _anchors(agent: Span, spans: dict[str, Span]) -> dict[str, str]:
    """Spans (and subagent AGENT spans named by its tool results) each turn of one agent issued."""
    anchors: dict[str, str] = {}
    for turn in _turns(agent):
        dispatch = [turn.dispatch_span_id] if turn.dispatch_span_id is not None else []
        results = [result for span_id in turn.tool_call_span_ids for result in tool_results(spans[span_id])]
        for span_id in (*turn.llm_span_ids, *dispatch, *turn.tool_call_span_ids):
            anchors.setdefault(span_id, turn.id)
        for result in (*results, *turn.unlinked_results):
            for ref in result.subagent_refs:
                if ref.span_id is not None:
                    anchors.setdefault(ref.span_id, turn.id)
    return anchors


def turns_in_execution_order(trace: Trace) -> list[tuple[Span, Turn]]:
    """Every turn in the order it ran across agents.

    A subagent's turns follow the parent turn that issued the span the subagent AGENT
    span hangs under (its TOOL or step span), or the tool result that names it.
    Subagents that no parent turn issued follow all of their parent agent's turns.
    """
    from trace_ingest import SpanKind

    spans, parents, owners = _tree(trace)
    agents = [span for span in spans.values() if span.kind is SpanKind.AGENT]
    anchors = {agent.id: _anchors(agent, spans) for agent in agents}
    placed: dict[str, dict[str | None, list[Span]]] = {agent.id: {} for agent in agents}
    for agent in agents:
        owner = owners[agent.id]
        if owner is None:
            continue
        issued = anchors[owner]
        turn_id: str | None = None
        current: str | None = agent.id
        while current is not None and current != owner:
            if current in issued:
                turn_id = issued[current]
                break
            current = parents[current]
        placed[owner].setdefault(turn_id, []).append(agent)
    ordered: list[tuple[Span, Turn]] = []

    def emit(agent: Span) -> None:
        children = placed[agent.id]
        for turn in _turns(agent):
            ordered.append((agent, turn))
            for child in children.get(turn.id, []):
                emit(child)
        for child in children.get(None, []):
            emit(child)

    for agent in agents:
        if owners[agent.id] is None:
            emit(agent)
    return ordered


def turn_text(turn: Turn) -> str | None:
    """The turn's text: its string content, or its text parts joined; None when unrecorded."""
    from trace_ingest import UNSET, TextPart

    if turn.content is UNSET:
        return None
    if isinstance(turn.content, str):
        return turn.content
    return "\n".join(part.text for part in turn.content if isinstance(part, TextPart))


def error_status(result: ToolResult) -> str:
    """The source-recorded error status of one tool result; result text is never status."""
    if result.is_error is not None:
        return "error" if result.is_error else "not_error"
    recorded = result.attributes.get("is_error_status")
    return recorded if isinstance(recorded, str) and recorded in ("invalid", "conflicting") else "unrecorded"


def tool_spans(trace: Trace) -> list[tuple[str | None, Span]]:
    """TOOL spans in tree order, each with its owning AGENT span id (its tool scope)."""
    from trace_ingest import SpanKind

    spans, _, owners = _tree(trace)
    return [(owners[span.id], span) for span in spans.values() if span.kind is SpanKind.TOOL]


def tool_results(span: Span) -> list[ToolResult]:
    """A TOOL span's recorded results; empty when the call or its results were not recorded."""
    from trace_ingest import UNSET

    call = span.tool_call
    return [] if call is None or call.results is UNSET else list(call.results)


def _declared(span: Span) -> list[ToolDefinition] | None:
    """A span's tool declarations; None when it recorded none (an empty list is a declaration)."""
    definitions = span.tool_definitions
    return list(definitions) if isinstance(definitions, list) else None


def _issuing_turns(trace: Trace) -> dict[str, Turn]:
    return {span_id: turn for _, turn in iter_turns(trace) for span_id in turn.tool_call_span_ids}


def tool_definitions_in_scope(trace: Trace) -> dict[str, list[ToolDefinition] | None]:
    """Definitions in strict scope for each TOOL span; None when nothing in scope declared any.

    The scope is the LLM span(s) that declared tools for the issuing turn, else the nearest
    LLM ancestor that declared them, else the owning AGENT span. Declarations are never
    borrowed across an AGENT boundary.
    """
    from trace_ingest import SpanKind

    spans, parents, _ = _tree(trace)
    issuing = _issuing_turns(trace)
    scoped: dict[str, list[ToolDefinition] | None] = {}
    for owner, span in tool_spans(trace):
        declaring: list[Span] = []
        turn = issuing.get(span.id)
        if turn is not None:
            declaring = [spans[span_id] for span_id in turn.llm_span_ids if _declared(spans[span_id]) is not None]
        if not declaring:
            current = parents[span.id]
            while current is not None and current != owner:
                candidate = spans[current]
                if candidate.kind is SpanKind.LLM and _declared(candidate) is not None:
                    declaring = [candidate]
                    break
                current = parents[current]
        if declaring:
            scoped[span.id] = [item for declarer in declaring for item in _declared(declarer) or []]
        else:
            scoped[span.id] = _declared(spans[owner]) if owner is not None else None
    return scoped


def suggested_tool_definitions(trace: Trace) -> dict[str, list[ToolDefinition]]:
    """For TOOL spans with nothing declared in scope, the nearest enclosing agent's declarations.

    These cross an AGENT boundary, so they are only ever offered as a suggestion that a
    reviewer must accept explicitly.
    """
    spans, _, owners = _tree(trace)
    suggested: dict[str, list[ToolDefinition]] = {}
    for span_id, definitions in tool_definitions_in_scope(trace).items():
        owner = owners[span_id]
        if definitions is not None or owner is None:
            continue
        ancestor = owners[owner]
        while ancestor is not None:
            declared = _declared(spans[ancestor])
            if declared is not None:
                suggested[span_id] = declared
                break
            ancestor = owners[ancestor]
    return suggested


class TraceLookupError(ValueError):
    """A citation names no turn or span in the trace, or a span value no turn observed."""


# TOOL span fields known once the call is issued. Every other field (results, output, error,
# end time and the rest) is known only after the call, and any subagent it ran, completes.
_ISSUED_FIELDS = frozenset(
    {
        "id",
        "source_url",
        "kind",
        "start_time",
        "input",
        "model",
        "llm_params",
        "agent_id",
        "tool_name",
        "tool_definitions",
    }
)
_ISSUED_CALL_FIELDS = frozenset(
    {"call_id", "index", "instrumentation_alias_of", "prior_user_text", "server", "initiator"}
)


def _issued(pointer: object) -> bool:
    """Whether an RFC 6901 pointer into a TOOL span's JSON reaches only fields known at issue."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False
    tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
    if tokens[0] == "tool_call":
        return len(tokens) > 1 and tokens[1] in _ISSUED_CALL_FIELDS
    return tokens[0] in _ISSUED_FIELDS


class TraceIndex:
    """Turn and span lookups that citations and construction pointers resolve against."""

    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self.turns: dict[str, tuple[Span, Turn]] = {}
        # Positions follow execution, so cutoff and ordering comparisons hold across agents.
        self.position: dict[str, int] = {}
        self.span_turn: dict[str, str] = {}
        for position, (agent, turn) in enumerate(turns_in_execution_order(trace)):
            self.turns[turn.id] = (agent, turn)
            self.position[turn.id] = position
            dispatch = [turn.dispatch_span_id] if turn.dispatch_span_id is not None else []
            for span_id in (*turn.llm_span_ids, *dispatch, *turn.tool_call_span_ids):
                self.span_turn.setdefault(span_id, turn.id)
        self.spans: dict[str, Span] = _tree(trace)[0]

    def _completed(self, span: Span, turn_id: str) -> int:
        """The position of the issuing agent's first turn after the call and every subagent it ran.

        A call that no later turn of its agent follows completes after every recorded turn.
        """
        from trace_ingest import SpanKind

        pending = [span]
        pending += [
            self.spans[ref.span_id] for result in tool_results(span) for ref in result.subagent_refs if ref.span_id
        ]
        after = self.position[turn_id]
        while pending:
            current = pending.pop()
            pending.extend(current.children)
            if current.kind is SpanKind.AGENT:
                after = max([after, *(self.position[turn.id] for turn in _turns(current))])
        agent = self.turns[turn_id][0]
        return next(
            (self.position[turn.id] for turn in _turns(agent) if self.position[turn.id] > after), len(self.position)
        )

    def document(self, reference: dict[str, Any]) -> tuple[Any, int]:
        """The JSON of the cited turn or span (without children) and the position from which it was observed.

        A span is observed at its issuing turn, except a TOOL span's completed fields (its
        results and output): the issuing agent observes those at its next turn after the call
        and every subagent the call ran.
        """
        from trace_ingest import SpanKind

        if "turn_id" in reference:
            entry = self.turns.get(reference["turn_id"])
            if entry is None:
                raise TraceLookupError("source turn is not in the trace")
            return entry[1].model_dump(mode="json"), self.position[entry[1].id]
        span = self.spans.get(reference["span_id"])
        if span is None:
            raise TraceLookupError("source span is not in the trace")
        turn_id = self.span_turn.get(span.id)
        if turn_id is None:
            raise TraceLookupError("source span belongs to no trace turn")
        position = self.position[turn_id]
        if span.kind is SpanKind.TOOL and not _issued(reference.get("pointer")):
            position = self._completed(span, turn_id)
        return span.model_dump(mode="json", exclude={"children"}), position
