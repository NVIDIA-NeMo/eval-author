# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Safe-trace scrubbing of Compass losses and the byte-repair boundary of trace-environment prepare."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import trace_ingest

_SCRIPT = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/scripts/trace_environment.py"
# Every source identifier and opaque value below must stay out of the safe copy.
_RAW = (
    "RAW-SESSION",
    "RAW-TRAJECTORY",
    "RAW-CONTINUED",
    "RAW-DEFINITION",
    "RAW-CALL",
    "RAW-CHILD-ID",
    "RAW-CHILD-SESSION",
    "RAW-CHILD-PATH",
    "RAW-REF-EXTRA",
    "RAW-EXTRA-CALL",
)


def _run(*args: object) -> tuple[int, dict[str, Any]]:
    result = subprocess.run([sys.executable, str(_SCRIPT), *map(str, args)], capture_output=True, text=True)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def _prepare(tmp_path: Path, source: Path, task_id: str) -> tuple[int, dict[str, Any], Path]:
    code, created = _run("init", "--root", tmp_path / ".eval-author/trace-environments", "--task-id", task_id)
    assert code == 0, created
    task_dir = Path(created["task_dir"])
    code, result = _run("prepare", "--task-dir", task_dir, "--source", source, "--source-kind", "atif")
    return code, result, task_dir


def _walk(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans, pending = [], list(trace["root_spans"])
    while pending:
        span = pending.pop()
        pending.extend(span["children"])
        spans.append(span)
    return spans


def test_safe_trace_scrubs_loss_details_like_the_fields_they_mirror(tmp_path: Path) -> None:
    source = tmp_path / "source.atif.json"
    call = {"tool_call_id": "RAW-CALL", "function_name": "task", "arguments": {}}
    source.write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "session_id": "RAW-SESSION",
                "trajectory_id": "RAW-TRAJECTORY",
                "continued_trajectory_ref": "RAW-CONTINUED",
                "agent": {"name": "agent", "version": "1.0", "tool_definitions": [{"description": "RAW-DEFINITION"}]},
                "steps": [
                    {"step_id": 1, "source": "user", "message": "Repair the fixture."},
                    {
                        "step_id": 2,
                        "source": "agent",
                        "message": "Delegating.",
                        "tool_calls": [call, call],
                        "observation": {
                            "results": [
                                {
                                    "source_call_id": "RAW-CALL",
                                    "content": "done",
                                    "subagent_trajectory_ref": [
                                        {
                                            "trajectory_id": "RAW-CHILD-ID",
                                            "session_id": "RAW-CHILD-SESSION",
                                            "trajectory_path": "RAW-CHILD-PATH.json",
                                            "extra": {"session_id": "RAW-REF-EXTRA"},
                                        }
                                    ],
                                    "extra": {"tool_call_id": "RAW-EXTRA-CALL"},
                                }
                            ]
                        },
                    },
                ],
            }
        )
    )

    code, result, task_dir = _prepare(tmp_path, source, "loss-details")

    assert code == 0, result
    canonical = json.loads((task_dir / "private/canonical.trace.json").read_text())
    assert {loss["code"] for loss in canonical["losses"]} >= {
        "unresolved_subagent_ref",
        "duplicate_tool_call_id",
        "continued_trajectory_not_followed",
        "tool_definition_skipped",
    }
    for path in (task_dir / "safe").iterdir():
        text = path.read_text()
        assert not [value for value in _RAW if value in text], path.name

    safe = json.loads((task_dir / "safe/trace.json").read_text())
    losses = {loss["code"]: loss["detail"] for loss in safe["losses"]}
    spans = _walk(safe)
    run = next(span["run"] for span in spans if span.get("run"))
    calls = [span["tool_call"] for span in spans if span.get("tool_call")]
    [ref] = [ref for call in calls for result in call["results"] for ref in result["subagent_refs"]]
    # The same source value gets the same pseudonym in the detail and in the field it mirrors.
    assert losses["continued_trajectory_not_followed"] == {"continued_trajectory_ref": run["continued_trajectory_ref"]}
    assert losses["duplicate_tool_call_id"] == {"tool_call_id": calls[0]["call_id"]}
    assert losses["unresolved_subagent_ref"] == {
        "ref": {name: ref[name] for name in ("trajectory_id", "session_id", "trajectory_path")}
    }
    assert losses["tool_definition_skipped"] == {}


def _repair_source(directory: Path, *, broken: bool) -> Path:
    child = directory / "child.atif.json"
    child.write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "agent": {"name": "child", "version": "1.0"},
                "steps": [{"step_id": 1, "source": "user", "message": "Child task."}],
            }
        )
    )
    quote = '"' if broken else '\\"'
    source = directory / "source.atif.json"
    source.write_text(
        '{"schema_version":"ATIF-v1.7","agent":{"name":"agent","version":"1.0"},"steps":['
        f'{{"step_id":1,"source":"user","message":"prefix \\"PASSWORD=<redacted>{quote} suffix"}},'
        '{"step_id":2,"source":"agent","message":"Delegating.",'
        '"tool_calls":[{"tool_call_id":"c1","function_name":"task","arguments":{}}],'
        '"observation":{"results":[{"source_call_id":"c1","content":"done",'
        '"subagent_trajectory_ref":[{"trajectory_path":"child.atif.json"}]}]}}]}'
    )
    return source


def test_repaired_source_with_external_subagent_files_is_refused(tmp_path: Path) -> None:
    code, result, task_dir = _prepare(tmp_path, _repair_source(tmp_path, broken=False), "unrepaired")
    assert code == 0, result
    canonical = json.loads((task_dir / "private/canonical.trace.json").read_text())
    # Unrepaired, the loader reads the sibling file and the subagent run is in the trace.
    assert sum(span["kind"] == "AGENT" for span in _walk(canonical)) == 2
    assert not canonical["losses"]
    # A repaired copy is loaded from a staging directory, where that sibling file is absent.
    code, result, task_dir = _prepare(tmp_path, _repair_source(tmp_path, broken=True), "repaired")
    assert code == 1
    assert "names subagent trajectory files by path" in result["error"]
    assert not (task_dir / "safe/trace.json").exists()


@pytest.fixture
def trace_source(monkeypatch):
    monkeypatch.syspath_prepend(str(_SCRIPT.parent))
    import trace_source

    return trace_source


def test_newly_marked_identifier_fields_are_pseudonymized_and_unknown_models_refused(trace_source, monkeypatch):
    from trace_ingest import Span, SpanKind, Trace, TraceAggregate, Turn, TurnRole

    turn = Turn(id="trajectory/turn/1", index=1, role=TurnRole.USER, content="Repair it.")
    agent = Span(id="trajectory", kind=SpanKind.AGENT, model="RAW-MODEL", turns=[turn])
    trace = Trace(id="trace-1", root_spans=[agent], aggregate=TraceAggregate())
    marked = trace_ingest.identifier_fields()
    monkeypatch.setattr(trace_ingest, "identifier_fields", lambda: {**marked, "Span": [*marked["Span"], "model"]})
    safe, _ = trace_source.scrub(trace)
    assert safe.root_spans[0].model == trace_source.safe_id("RAW-MODEL")

    monkeypatch.setattr(trace_ingest, "identifier_fields", lambda: {**marked, "AgentInfo": ["name"]})
    with pytest.raises(trace_source.TraceSourceError, match="does not visit Compass identifier models"):
        trace_source.scrub(trace)
