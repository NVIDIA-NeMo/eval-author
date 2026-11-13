# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Turn order, tool scope, result status and the identifier table behind the safe trace copy."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import trace_ingest
from trace_ingest import (
    UNSET,
    AgentInfo,
    ImagePart,
    LLMCallInfo,
    RunInfo,
    Span,
    SpanKind,
    SubagentRef,
    TextPart,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Trace,
    TraceAggregate,
    Turn,
    TurnRole,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))

from eval_author_shared import safe_trace, trace_view  # noqa: E402

PRIVACY = ROOT / "skills/eval-author-inspect-trace/scripts/trace_privacy.py"


def _definition(name: str, description: str) -> ToolDefinition:
    schema = {"type": "object", "properties": {}}
    return ToolDefinition(name=name, description=description, input_schema=schema, raw={"name": name})


def _tool(span_id: str, *, children: list[Span] | None = None, result: ToolResult | None = None) -> Span:
    results = [result or ToolResult(content="ok")]
    return Span(
        id=span_id,
        kind=SpanKind.TOOL,
        tool_name="lookup",
        tool_call=ToolCall(call_id=f"call-{span_id}", results=results),
        children=children or [],
    )


def _agent(span_id: str, turns: list[Turn], children: list[Span], **fields: Any) -> Span:
    return Span(id=span_id, kind=SpanKind.AGENT, turns=turns, children=children, **fields)


def _turn(agent: str, index: int, role: TurnRole = TurnRole.AGENT, **fields: Any) -> Turn:
    return Turn(id=f"{agent}/turn/{index}", index=index, role=role, **fields)


def _delegating_trace() -> Trace:
    """Root turn 2 issues a TOOL span that runs ``sub``; ``tail`` hangs directly under the root agent;
    ``ref`` hangs under the root agent too, but an unlinked result of root turn 3 names it."""
    nested = _agent("nested", [_turn("nested", 1, TurnRole.USER, content="deep")], [])
    sub = _agent(
        "sub",
        [_turn("sub", 1, TurnRole.USER, content="sub task"), _turn("sub", 2, tool_call_span_ids=["sub/tool"])],
        [_tool("sub/tool", children=[nested])],
    )
    tail = _agent("tail", [_turn("tail", 1, TurnRole.USER, content="later")], [])
    ref = _agent("ref", [_turn("ref", 1, TurnRole.USER, content="referenced")], [])
    root = _agent(
        "root",
        [
            _turn("root", 1, TurnRole.USER, content="Do it."),
            _turn("root", 2, tool_call_span_ids=["root/tool"]),
            _turn(
                "root",
                3,
                content="done",
                unlinked_results=[ToolResult(content="x", subagent_refs=[SubagentRef(span_id="ref")])],
            ),
        ],
        [tail, _tool("root/tool", children=[sub]), ref],
    )
    return Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())


def test_iter_turns_follows_tree_order_and_execution_order_places_subagents():
    trace = _delegating_trace()
    assert [turn.id for _, turn in trace_view.iter_turns(trace)] == [
        "root/turn/1",
        "root/turn/2",
        "root/turn/3",
        "tail/turn/1",
        "sub/turn/1",
        "sub/turn/2",
        "nested/turn/1",
        "ref/turn/1",
    ]
    assert [turn.id for _, turn in trace_view.turns_in_execution_order(trace)] == [
        "root/turn/1",
        "root/turn/2",
        "sub/turn/1",
        "sub/turn/2",
        "nested/turn/1",
        "root/turn/3",
        "ref/turn/1",
        "tail/turn/1",
    ]
    index = trace_view.TraceIndex(trace)
    assert index.position["nested/turn/1"] < index.position["root/turn/3"]
    assert index.span_turn["sub/tool"] == "sub/turn/2"
    # A TOOL span's arguments are observed at its issuing turn; its results once the issuing
    # agent continues after every subagent the call ran.
    document, position = index.document({"span_id": "root/tool", "pointer": "/tool_name"})
    assert "children" not in document and position == index.position["root/turn/2"]
    _, position = index.document({"span_id": "root/tool", "pointer": "/tool_call/results/0/content"})
    assert position == index.position["root/turn/3"]
    with pytest.raises(trace_view.TraceLookupError, match="belongs to no trace turn"):
        index.document({"span_id": "tail"})
    # A call its agent never follows with another turn completes after every recorded turn.
    _, position = index.document({"span_id": "sub/tool", "pointer": "/tool_call/results/0/content"})
    assert position == len(index.position)


def test_subagent_named_by_a_linked_tool_result_runs_right_after_the_issuing_turn():
    child = _agent(
        "child",
        [_turn("child", 1, TurnRole.USER, content="sub task"), _turn("child", 2, content="sub answer")],
        [],
    )
    result = ToolResult(content="sub answer", subagent_refs=[SubagentRef(span_id="child")])
    root = _agent(
        "root",
        [
            _turn("root", 1, TurnRole.USER, content="Do it."),
            _turn("root", 2, tool_call_span_ids=["root/tool"]),
            _turn("root", 3, content="done"),
        ],
        [_tool("root/tool", result=result), child],
    )
    trace = Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())
    assert [turn.id for _, turn in trace_view.turns_in_execution_order(trace)] == [
        "root/turn/1",
        "root/turn/2",
        "child/turn/1",
        "child/turn/2",
        "root/turn/3",
    ]
    index = trace_view.TraceIndex(trace)
    assert index.position["root/turn/2"] < index.position["child/turn/1"] < index.position["root/turn/3"]
    _, position = index.document({"span_id": "root/tool", "pointer": "/tool_call/results/0/content"})
    assert position == index.position["root/turn/3"]


def test_turn_text_and_error_status_follow_recorded_values_only():
    assert trace_view.turn_text(_turn("a", 1, content="plain")) == "plain"
    parts = [TextPart(text="first"), ImagePart(media_type="image/png", path="x.png"), TextPart(text="second")]
    assert trace_view.turn_text(_turn("a", 1, content=parts)) == "first\nsecond"
    assert trace_view.turn_text(_turn("a", 1)) is None

    assert trace_view.error_status(ToolResult(is_error=True)) == "error"
    assert trace_view.error_status(ToolResult(is_error=False)) == "not_error"
    assert trace_view.error_status(ToolResult(content="Error: failed")) == "unrecorded"
    for status in ("invalid", "conflicting"):
        result = ToolResult(attributes={"is_error_status": status, "is_error_flags": {"is_error": "yes"}})
        assert trace_view.error_status(result) == status
    assert trace_view.error_status(ToolResult(attributes={"is_error_status": "other"})) == "unrecorded"


def test_tool_definitions_use_strict_scope_and_only_suggest_across_agents():
    llm = Span(id="root/llm", kind=SpanKind.LLM, tool_definitions=[_definition("lookup", "turn-declared")])
    silent = _agent("silent", [_turn("silent", 1, tool_call_span_ids=["silent/tool"])], [_tool("silent/tool")])
    empty = _agent(
        "empty", [_turn("empty", 1, tool_call_span_ids=["empty/tool"])], [_tool("empty/tool")], tool_definitions=[]
    )
    root = _agent(
        "root",
        [
            _turn("root", 1, TurnRole.USER, content="Go."),
            _turn("root", 2, llm_span_ids=["root/llm"], tool_call_span_ids=["root/declared"]),
            _turn("root", 3, tool_call_span_ids=["root/agent"]),
        ],
        [llm, _tool("root/declared"), _tool("root/agent", children=[silent, empty])],
        tool_definitions=[_definition("lookup", "agent-declared")],
    )
    trace = Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())
    scoped = trace_view.tool_definitions_in_scope(trace)
    descriptions = {
        span_id: None if items is None else [item.description for item in items] for span_id, items in scoped.items()
    }
    assert descriptions == {
        "root/declared": ["turn-declared"],
        "root/agent": ["agent-declared"],
        "silent/tool": None,
        "empty/tool": [],
    }
    suggested = trace_view.suggested_tool_definitions(trace)
    # Only the subagent that declared nothing is offered its parent's list, as a suggestion.
    assert {span_id: [item.description for item in items] for span_id, items in suggested.items()} == {
        "silent/tool": ["agent-declared"]
    }


def test_tool_scope_falls_back_to_a_declaring_llm_ancestor_when_turn_spans_declare_none():
    inner = Span(id="root/inner", kind=SpanKind.LLM)
    outer = Span(
        id="root/outer",
        kind=SpanKind.LLM,
        tool_definitions=[_definition("lookup", "ancestor-declared")],
        children=[inner, _tool("root/tool")],
    )
    root = _agent(
        "root",
        [
            _turn("root", 1, TurnRole.USER, content="Go."),
            _turn("root", 2, llm_span_ids=["root/inner"], tool_call_span_ids=["root/tool"]),
        ],
        [outer],
        tool_definitions=[_definition("lookup", "agent-declared")],
    )
    trace = Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())
    [definition] = trace_view.tool_definitions_in_scope(trace)["root/tool"] or []
    assert definition.description == "ancestor-declared"


def test_scrubber_keeps_values_shown_in_clear_and_drops_token_ids():
    session = "acme-billing-2026"
    llm = Span(
        id="root/llm",
        kind=SpanKind.LLM,
        llm_call=LLMCallInfo(prompt_token_ids=[101, 102], completion_token_ids=[7], logprobs=[-0.5]),
    )
    root = _agent(
        "root",
        [
            _turn("root", 1, TurnRole.USER, content=f"Ask support-agent about {session}."),
            _turn("root", 2, content="On it.", llm_span_ids=["root/llm"]),
        ],
        [llm],
        agent=AgentInfo(name="support-agent"),
        run=RunInfo(trajectory_id="support-agent", session_id=session),
    )
    trace = Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())
    safe, counts = safe_trace.scrub(trace)
    [agent] = safe.model_dump(mode="json")["root_spans"]
    # The agent name stays in clear, so the identical trajectory id hides nothing in text.
    assert agent["turns"][0]["content"] == f"Ask support-agent about {safe_trace.pseudonym(session)}."
    assert agent["run"]["trajectory_id"] == safe_trace.pseudonym("support-agent")
    # Token ids decode back to the unredacted prompt and completion.
    call = agent["children"][0]["llm_call"]
    assert (call["prompt_token_ids"], call["completion_token_ids"], call["logprobs"]) == (None, None, [-0.5])
    assert counts["token_ids"] == 2


def test_identifier_table_classifies_every_installed_compass_field(monkeypatch):
    trace_view.check_field_roles()
    span_roles = {name: role for name, role in trace_view.FIELD_ROLES["Span"].items() if name != "agent_id"}
    monkeypatch.setitem(trace_view.FIELD_ROLES, "Span", span_roles)
    with pytest.raises(trace_view.TraceContractDrift, match="unclassified field Span.agent_id"):
        trace_view.check_field_roles()
    monkeypatch.undo()

    class NewModel(trace_ingest.Trace):
        pass

    monkeypatch.setattr(trace_ingest, "NewModel", NewModel, raising=False)
    monkeypatch.setattr(trace_ingest, "__all__", [*trace_ingest.__all__, "NewModel"])
    with pytest.raises(trace_view.TraceContractDrift, match="unclassified model NewModel"):
        trace_view.check_field_roles()


def test_scrubber_refuses_fields_outside_the_identifier_table():
    turn = _turn("trajectory", 1, TurnRole.USER, content="Repair it.")
    agent = Span(id="trajectory", kind=SpanKind.AGENT, turns=[turn], new_identifier="RAW-NEW")
    trace = Trace(id="trace-1", root_spans=[agent], aggregate=TraceAggregate())
    with pytest.raises(trace_view.TraceContractDrift, match="new_identifier"):
        safe_trace.scrub(trace)


def test_safe_ids_keep_loader_positions_and_pseudonymize_source_ids():
    assert safe_trace.safe_id("trajectory/subagent/0/step/2/tool/1") == "trajectory/subagent/0/step/2/tool/1"
    assert safe_trace.safe_id("gym/response/model") == "gym/response/model"
    source = safe_trace.safe_id("0000000000000001")
    assert source.startswith("anon-")
    assert safe_trace.safe_id("0000000000000001/turn/2") == f"{source}/turn/2"


def _run(*args: object) -> tuple[int, dict[str, Any]]:
    result = subprocess.run([sys.executable, str(PRIVACY), *map(str, args)], capture_output=True, text=True)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def _prepare(tmp_path: Path, source: Path, kind: str, *extra: object) -> Path:
    code, created = _run("init", "--root", tmp_path / ".eval-author/.working/trace", "--task-id", f"case-{kind}")
    assert code == 0, created
    task_dir = Path(created["task_dir"])
    code, result = _run("prepare", "--task-dir", task_dir, "--source", source, "--source-kind", kind, *extra)
    assert code == 0, result
    return task_dir


def _safe_text(task_dir: Path) -> str:
    return "\n".join(path.read_text() for path in sorted((task_dir / "safe").iterdir()))


def _walk(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans, pending = [], list(trace["root_spans"])
    while pending:
        span = pending.pop()
        pending.extend(span["children"])
        spans.append(span)
    return spans


def test_no_raw_atif_identifier_reaches_the_safe_copy_and_loss_details_match(tmp_path):
    raw = ("RAW-SESSION", "RAW-TRAJECTORY", "RAW-CALL", "RAW-CHILD-ID", "RAW-CHILD-PATH")
    source = tmp_path / "source.atif.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "session_id": "RAW-SESSION",
                "trajectory_id": "RAW-TRAJECTORY",
                "agent": {"name": "agent", "version": "1.0"},
                "steps": [
                    {"step_id": 1, "source": "user", "message": "Repair the fixture for RAW-SESSION."},
                    {
                        "step_id": 2,
                        "source": "agent",
                        "message": "Delegating.",
                        "tool_calls": [{"tool_call_id": "RAW-CALL", "function_name": "task", "arguments": {}}],
                        "observation": {
                            "results": [
                                {
                                    "source_call_id": "RAW-CALL",
                                    "content": "done",
                                    "subagent_trajectory_ref": [
                                        {"trajectory_id": "RAW-CHILD-ID", "trajectory_path": "RAW-CHILD-PATH.json"}
                                    ],
                                    "extra": {"tool_call_id": "RAW-CALL"},
                                }
                            ]
                        },
                    },
                ],
            }
        )
    )
    task_dir = _prepare(tmp_path, source, "atif")
    canonical = (task_dir / "private/canonical.trace.json").read_text()
    assert all(value in canonical for value in raw)
    text = _safe_text(task_dir)
    assert [value for value in raw if value in text] == []
    assert str(tmp_path) not in text

    safe = json.loads((task_dir / "safe/trace.json").read_text())
    trace_ingest.Trace.model_validate(safe)
    losses = {loss["code"]: loss["detail"] for loss in safe["losses"]}
    spans = _walk(safe)
    [call] = [span["tool_call"] for span in spans if span.get("tool_call")]
    [ref] = [ref for result in call["results"] for ref in result["subagent_refs"]]
    # A source value gets the same pseudonym in a loss detail and in the field it mirrors.
    assert losses["unresolved_subagent_ref"] == {"ref": {k: ref[k] for k in ("trajectory_id", "trajectory_path")}}
    assert call["results"][0]["extra"] == {"tool_call_id": call["call_id"]}


def test_no_raw_gym_identifier_reaches_the_safe_copy(tmp_path):
    raw = ("resp_RAW", "fc_RAW", "msg_RAW", "call_RAW", "ops@example.com")
    record = {
        "responses_create_params": {
            "input": [{"role": "user", "content": "Sum [1, 2]; mail ops@example.com."}],
            "tools": [
                {
                    "type": "function",
                    "name": "total",
                    "description": "Sum integers",
                    "parameters": {"type": "object", "properties": {"amounts": {"type": "array"}}},
                }
            ],
        },
        "response": {
            "id": "resp_RAW",
            "object": "response",
            "model": "fixture-model",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_RAW",
                    "call_id": "call_RAW",
                    "name": "total",
                    "arguments": '{"amounts": [1, 2]}',
                    "status": "completed",
                },
                {"type": "function_call_output", "call_id": "call_RAW", "output": "3"},
                {
                    "type": "message",
                    "id": "msg_RAW",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "3", "annotations": []}],
                },
            ],
        },
    }
    source = tmp_path / "rollouts.jsonl"
    source.write_text("{}\n" + json.dumps(record) + "\n")
    task_dir = _prepare(tmp_path, source, "gym", "--row", 2)
    text = _safe_text(task_dir)
    assert [value for value in raw if value in text] == []
    safe = trace_ingest.Trace.model_validate_json((task_dir / "safe/trace.json").read_text())
    [(_, tool)] = trace_view.tool_spans(safe)
    # Replayed arguments and results stay exact; only identifiers change.
    assert tool.input == {"amounts": [1, 2]} and tool.tool_call is not None
    assert [result.content for result in trace_view.tool_results(tool)] == ["3"]
    summary = json.loads((task_dir / "summary.json").read_text())
    assert summary["source"]["original_path"] == "private/source.gym.jsonl"
    assert summary["source"]["row"] == 2


def test_identifier_under_a_payload_key_is_scrubbed_from_every_safe_file(tmp_path):
    raw = "req-customer-abc123"
    tool = Span(
        id="trajectory/step/2/tool/0",
        kind=SpanKind.TOOL,
        tool_name="lookup",
        input={"query": "status"},
        tool_call=ToolCall(call_id="call-1", results=[ToolResult(content=f"Found {raw}: active")]),
    )
    root = _agent(
        "trajectory",
        [
            _turn("trajectory", 1, TurnRole.USER, content=f"Look up {raw}."),
            _turn("trajectory", 2, tool_call_span_ids=[tool.id]),
            _turn("trajectory", 3, content="It is active."),
        ],
        [tool],
        input={"request_id": raw},
    )
    source = tmp_path / "traces.jsonl"
    source.write_text(Trace(id="trace-1", root_spans=[root], aggregate=TraceAggregate()).model_dump_json() + "\n")
    task_dir = _prepare(tmp_path, source, "trace")
    assert raw in (task_dir / "private/canonical.trace.json").read_text()
    assert raw not in _safe_text(task_dir)
    safe = json.loads((task_dir / "safe/trace.json").read_text())
    [agent] = safe["root_spans"]
    assert agent["input"] == {"request_id": safe_trace.pseudonym(raw)}
    assert agent["turns"][0]["content"] == f"Look up {safe_trace.pseudonym(raw)}."


def test_prepare_refuses_sources_without_turns_and_live_intake(tmp_path):
    code, created = _run("init", "--root", tmp_path / ".eval-author/.working/trace", "--task-id", "no-turns")
    assert code == 0, created
    task_dir = created["task_dir"]
    source = tmp_path / "rollouts.jsonl"
    invocation = {"invocation_id": "inv-1", "conversation": [{"type": "message", "role": "user", "content": "Sum."}]}
    rollout = {
        "ng_trajectory": {"schema_version": "1.0", "rollout_id": "r", "task_id": "t", "invocations": [invocation]}
    }
    source.write_text(json.dumps(rollout) + "\n")
    code, refused = _run("prepare", "--task-dir", task_dir, "--source", source, "--source-kind", "gym", "--row", 1)
    assert code == 1
    assert refused["error"] == "source kind gym does not record turns yet"
    code, refused = _run("prepare", "--task-dir", task_dir, "--source", source, "--source-kind", "intake")
    assert code == 1 and "--source-kind trace" in refused["error"]


def test_image_only_user_turn_is_reported_by_its_safe_id():
    image = ImagePart(media_type="image/png", path="data:image/png;base64,AAAA")
    root = _agent(
        "trajectory",
        [
            _turn("trajectory", 1, TurnRole.USER, content="Read it."),
            _turn("trajectory", 2, TurnRole.USER, content=[image]),
        ],
        [],
    )
    trace = Trace(id="trace", root_spans=[root], aggregate=TraceAggregate())
    assert safe_trace.image_only_user_turns(trace, "atif") == ["trajectory/turn/2"]
    safe, counts = safe_trace.scrub(trace)
    assert counts["image"] == 1
    assert safe.root_spans[0].turns is not UNSET
    assert safe.root_spans[0].turns[1].content == [TextPart(text=safe_trace.IMAGE_MARKER)]
