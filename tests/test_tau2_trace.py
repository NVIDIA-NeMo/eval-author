# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic mini events exercise export fidelity and fail-closed boundaries offline."""

import copy
import importlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from trace_ingest import Trace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
AUDIT = ROOT / "skills/eval-author-audit/scripts/audit_spec"


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from benchmark_runtime import tau2_trace

    return tau2_trace


def native_message(role, content, calls=None):
    return {
        "role": role,
        "content": content,
        "tool_calls": calls,
        "timestamp": "2026-11-02T10:00:00",
        "is_audio": False,
    }


def fixture():
    config = {"agent": {"model": "solver", "max_tokens": 99}, "user": {"model": "simulator", "max_tokens": 99}}
    greeting = native_message("assistant", "Hi! How can I help you today?")
    user = native_message("user", "Read both records.")
    calls = [{"id": f"call-{i}", "name": "read", "arguments": {"id": i}, "requestor": "assistant"} for i in [1, 2]]
    agent = native_message("assistant", "Checking both.", calls)
    done = native_message("assistant", "One read failed.")
    stop = native_message("user", "###STOP###")
    task = {"id": "7", "initial_state": None, "evaluation_criteria": {"private": "not public dialogue"}}
    events: list[dict[str, Any]] = [
        {"event": "start", "domain": "airline", "task_id": "7", "seed": 300, "task": task},
        {"event": "initial_history", "messages": [greeting]},
    ]

    def generation(step, participant, message, history):
        tools = (
            [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
            if participant == "agent"
            else []
        )
        prompt = [
            {"role": "system", "content": "agent policy" if participant == "agent" else "private simulator scenario"},
            *history,
        ]
        request = {
            "model": config[participant]["model"],
            "messages": prompt,
            "temperature": 0.0,
            "max_tokens": 99,
            "seed": 300,
        }
        if tools:
            request.update(tools=tools, tool_choice="auto")
        raw = {"role": "assistant", "content": message["content"]}
        if message["tool_calls"]:
            raw["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
                }
                for c in message["tool_calls"]
            ]
        response = {
            "model": config[participant]["model"],
            "choices": [{"message": raw, "finish_reason": "tool_calls" if message["tool_calls"] else "stop"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
        events.extend(
            [
                {"event": "request", "step": step, "participant": participant, "messages": prompt, "tools": tools},
                {
                    "event": "completion",
                    "step": step,
                    "participant": participant,
                    "request": request,
                    "response": response,
                },
                {"event": "message", "step": step, "participant": participant, "message": message},
            ]
        )
        return raw

    generation(0, "user", user, [{"role": "user", "content": greeting["content"]}])
    initial_agent_history = [
        {"role": "assistant", "content": greeting["content"]},
        {"role": "user", "content": user["content"]},
    ]
    raw_agent = generation(1, "agent", agent, initial_agent_history)
    tool_results = [
        {
            "id": c["id"],
            "role": "tool",
            "requestor": "assistant",
            "content": "record1" if i == 0 else "Error: missing record",
            "error": i == 1,
        }
        for i, c in enumerate(calls)
    ]
    for call, result in zip(calls, tool_results, strict=True):
        events.append({"event": "tool", "step": 2, "call": call, "result": result})
    history = [
        *initial_agent_history,
        raw_agent,
        *[{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in tool_results],
    ]
    generation(3, "agent", done, history)
    generation(
        4,
        "user",
        stop,
        [
            {"role": "user", "content": greeting["content"]},
            {"role": "assistant", "content": user["content"]},
            {"role": "user", "content": done["content"]},
        ],
    )
    result = {
        "domain": "airline",
        "task_id": "7",
        "seed": 300,
        "termination": "user_stop",
        "reward": 0.0,
        "components": {"environment": {"reward": 0.0}},
        "messages": [greeting, user, agent, *tool_results, done, stop],
    }
    events.append({"event": "result", **result})
    return copy.deepcopy((result, events, config))


def inputs(adapter, tmp_path, data=None, evidence_class="primary_unhinted"):
    result, events, config = data or fixture()
    source = tmp_path / "source"
    source.mkdir()
    payloads = {
        "result": adapter.json_bytes(result),
        "trace": b"".join(adapter.json_bytes(e).replace(b"\n", b" ").rstrip() + b"\n" for e in events),
        "config": adapter.json_bytes(config),
        "selection": adapter.json_bytes({"seed": 300, "tasks": [{"domain": "airline", "id": "7"}]}),
    }
    manifest = {
        "schema": "tau-mini-trace-input.v1",
        "run_id": "test/task7/seed300",
        "evidence_class": evidence_class,
        "task_sha256": adapter.sha(adapter.json_bytes(events[0]["task"])),
        "runtime_sources": {},
    }
    for name, payload in payloads.items():
        path = source / (name + ".json")
        path.write_bytes(payload)
        manifest[name] = {"path": str(path), "sha256": adapter.sha(payload)}
    for name in adapter.RUNTIME_FILES:
        path = source / name
        path.write_text("synthetic protocol source identity\n")
        manifest["runtime_sources"][name] = {"path": str(path), "sha256": adapter.sha(path.read_bytes())}
    path = source / "manifest.json"
    path.write_bytes(adapter.json_bytes(manifest))
    return source / "result.json", source / "trace.json", path, tmp_path / "export"


def export(adapter, tmp_path, data=None, evidence_class="primary_unhinted"):
    paths = inputs(adapter, tmp_path, data, evidence_class)
    receipt = adapter.convert_native_rollout(*paths)
    trajectory = json.loads((paths[-1] / "trace.jsonl").read_text())
    return {"steps": trajectory["attributes"]["projection"]["steps"], "extra": trajectory["attributes"]}, receipt, paths


def test_fidelity_and_canonical_tool_credit(adapter, tmp_path, monkeypatch):
    trajectory, receipt, paths = export(adapter, tmp_path)
    model = Trace.model_validate_json((paths[-1] / "trace.jsonl").read_bytes())
    monkeypatch.syspath_prepend(str(AUDIT))
    collect_tool_calls = importlib.import_module("measurements.trace_tools").collect_tool_calls

    assert [c["tool_call_id"] for c in collect_tool_calls(model)] == ["call-1", "call-2"]
    step = next(s for s in trajectory["steps"] if s.get("tool_calls"))
    assert step["message"] == "Checking both."
    assert [c["arguments"] for c in step["tool_calls"]] == [{"id": 1}, {"id": 2}]
    assert step["observation"]["results"][1]["content"] == "Error: missing record"
    assert step["observation"]["results"][1]["extra"]["error"] is True
    assert step["timestamp"] == "2026-11-02T10:00:00"
    assert receipt["projection"]["live_agent_tool_calls"] == 2
    assert receipt["projection"]["unresolved_call_ids"] == []
    assert trajectory["extra"]["native_result"]["reward"] == 0.0
    greeting = trajectory["steps"][1]
    assert greeting["llm_call_count"] == 0 and "metrics" not in greeting
    assert greeting["is_copied_context"] is False
    assert sum(s.get("metrics", {}).get("prompt_tokens", 0) for s in trajectory["steps"]) == 20
    assert all("metrics" not in s and "model_name" not in s for s in trajectory["steps"] if s["source"] == "user")
    assert "private simulator scenario" not in json.dumps(trajectory)
    assert "not public dialogue" not in json.dumps(trajectory)
    assert stat.S_IMODE(paths[-1].stat().st_mode) == 0o700
    for name, digest in receipt["retained_inputs"].items():
        assert adapter.sha((paths[-1] / name).read_bytes()) == digest
        assert stat.S_IMODE((paths[-1] / name).stat().st_mode) == 0o600


def test_unresolved_calls_are_not_success_observations(adapter, tmp_path):
    result, events, config = fixture()
    events = events[:8]
    result.update(termination="max_steps", messages=result["messages"][:3], reason="max_steps")
    events.append({"event": "result", **result})
    trajectory, receipt, _ = export(adapter, tmp_path, (result, events, config))
    step = trajectory["steps"][-1]
    assert len(step["tool_calls"]) == 2 and "observation" not in step
    assert receipt["projection"]["unresolved_call_ids"] == ["call-1", "call-2"]


@pytest.mark.parametrize("completion_exists", [True, False])
def test_native_failures_retain_raw_without_invented_calls(adapter, tmp_path, completion_exists):
    result, events, config = fixture()
    events = events[: 7 if completion_exists else 6]
    if completion_exists:
        events[-1]["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{broken"
    error = "ValueError: invalid model output" if completion_exists else "ModelServerUnavailable: offline"
    result.update(termination="error", error=error, messages=result["messages"][:2])
    events.extend([{"event": "error", "reward": 0.0, "error": error}, {"event": "result", **result}])
    trajectory, receipt, paths = export(adapter, tmp_path, (result, events, config))
    assert not any(s.get("tool_calls") for s in trajectory["steps"])
    assert len(receipt["projection"]["unprojected_completions"]) == int(completion_exists)
    if completion_exists:
        assert "{broken" in (paths[-1] / "source.trace.jsonl").read_text()
        assert receipt["projection"]["unprojected_completions"][0]["usage"]["total_tokens"] == 13


@pytest.mark.parametrize("evidence_class", sorted(["hinted_feasibility", "reference_control", "diagnostic"]))
def test_evidence_class_is_preserved_without_primary_promotion(adapter, tmp_path, evidence_class):
    trajectory, receipt, _ = export(adapter, tmp_path, evidence_class=evidence_class)
    assert trajectory["extra"]["evidence_class"] == receipt["evidence_class"] == evidence_class


@pytest.mark.parametrize(
    "change,match",
    [
        ("result_mismatch", "Terminal trace result"),
        ("changed_content", "content mismatch"),
        ("changed_arguments", "raw/native tool mismatch"),
        ("malformed_arguments", "Expecting property name"),
        ("duplicate_id", "duplicate/empty tool call ID"),
        ("orphan_result", "order or identity mismatch"),
        ("result_actor", "actor/ID mismatch"),
        ("reordered_results", "order or identity mismatch"),
        ("initial_tools", "preloaded tool history"),
        ("simulator_tools", "simulator tools"),
        ("wrong_model", "effective request differs"),
        ("wrong_response_model", "response model mismatch"),
        ("missing_completion", "message lacks request/completion"),
        ("unknown_event", "unsupported event"),
        ("bad_step", "nonsequential generation step"),
        ("bad_history", "model history differs"),
        ("other_domain", "Only airline"),
    ],
)
def test_protocol_corruption_rejected_before_output(adapter, tmp_path, change, match):
    result, events, config = fixture()
    if change == "result_mismatch":
        result["reward"] = 1
    elif change == "changed_content":
        events[6]["response"]["choices"][0]["message"]["content"] = "changed"
    elif change in {"changed_arguments", "malformed_arguments"}:
        events[6]["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
            '{"id":9}' if change == "changed_arguments" else "{broken"
        )
    elif change == "duplicate_id":
        events[6]["response"]["choices"][0]["message"]["tool_calls"][1]["id"] = "call-1"
        events[7]["message"]["tool_calls"][1]["id"] = "call-1"
    elif change == "orphan_result":
        events[8]["call"] = {**events[8]["call"], "id": "orphan"}
    elif change == "result_actor":
        events[8]["result"]["requestor"] = "user"
    elif change == "reordered_results":
        events[8], events[9] = events[9], events[8]
    elif change == "initial_tools":
        events[1]["messages"][0]["tool_calls"] = [events[8]["call"]]
    elif change == "simulator_tools":
        events[2]["tools"] = [{"name": "private_user_tool"}]
    elif change == "wrong_model":
        events[6]["request"]["model"] = "wrong-model"
    elif change == "wrong_response_model":
        events[6]["response"]["model"] = "wrong-model"
    elif change == "missing_completion":
        del events[6]
    elif change == "unknown_event":
        events.insert(2, {"event": "reference_replay"})
    elif change == "bad_step":
        events[5]["step"] = 99
    elif change == "bad_history":
        events[5]["messages"] = [{"role": "system", "content": "agent policy"}]
    elif change == "other_domain":
        events[0]["domain"] = "retail"
    paths = inputs(adapter, tmp_path, (result, events, config))
    with pytest.raises(ValueError, match=match):
        adapter.convert_native_rollout(*paths)
    assert not paths[-1].exists()


@pytest.mark.parametrize("source", ["result", "trace", "config", "selection", "harness.py"])
def test_changed_bound_bytes_rejected(adapter, tmp_path, source):
    paths = inputs(adapter, tmp_path)
    path = paths[0].parent / (source if source.endswith(".py") else source + ".json")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(adapter.ConversionError, match="hash mismatch"):
        adapter.convert_native_rollout(*paths)
    assert not paths[-1].exists()


def test_existing_output_and_missing_evidence_class_rejected(adapter, tmp_path):
    paths = inputs(adapter, tmp_path)
    manifest = json.loads(paths[2].read_text())
    del manifest["evidence_class"]
    paths[2].write_bytes(adapter.json_bytes(manifest))
    with pytest.raises(adapter.ConversionError, match="evidence_class"):
        adapter.convert_native_rollout(*paths)
    paths[-1].mkdir()
    marker = paths[-1] / "keep"
    marker.write_text("unchanged")
    with pytest.raises(adapter.ConversionError, match="already exists"):
        adapter.convert_native_rollout(*paths)
    assert marker.read_text() == "unchanged"


def test_cli_module_uses_real_harbor_validation(adapter, tmp_path, monkeypatch):
    paths = inputs(adapter, tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    command = [
        sys.executable,
        "-B",
        "-m",
        "benchmark_runtime.tau2_trace",
        "--result",
        str(paths[0]),
        "--trace",
        str(paths[1]),
        "--run-manifest",
        str(paths[2]),
        "--out-dir",
        str(paths[3]),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "converted"
    receipt = json.loads((paths[-1] / "conversion-receipt.json").read_text())
    assert receipt["validator"]["exact_serialized_bytes_validated"] is True
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 1 and json.loads(second.stdout)["status"] == "rejected"


def test_copied_text_is_marked_without_live_agent_credit(adapter, tmp_path):
    result, events, config = fixture()
    events[0]["task"]["initial_state"] = {"message_history": copy.deepcopy(events[1]["messages"])}
    trajectory, receipt, _ = export(adapter, tmp_path, (result, events, config))
    initial = next(s for s in trajectory["steps"] if s.get("is_copied_context"))
    assert initial["llm_call_count"] == 0 and "tool_calls" not in initial and "metrics" not in initial
    assert receipt["projection"]["live_agent_tool_calls"] == 2


def test_judge_usage_and_private_prompts_stay_outside_agent_steps(adapter, tmp_path):
    result, events, config = fixture()
    messages = [
        {"role": "system", "content": "private judge criteria"},
        {"role": "user", "content": "private expected outcomes"},
    ]
    request = {"model": "simulator", "messages": messages, "temperature": 0.0, "max_tokens": 99, "seed": 300}
    response = {"model": "simulator", "usage": {"prompt_tokens": 999, "completion_tokens": 50}, "choices": []}
    events[-1:-1] = [
        {"event": "judge_request", "messages": messages},
        {"event": "judge_completion", "request": request, "response": response},
    ]
    trajectory, receipt, _ = export(adapter, tmp_path, (result, events, config))
    assert sum(s.get("metrics", {}).get("prompt_tokens", 0) for s in trajectory["steps"]) == 20
    assert receipt["projection"]["auxiliary_events"][-1]["usage"]["prompt_tokens"] == 999
    assert "private judge criteria" not in json.dumps(trajectory)
    assert receipt["validator"]["schema_version"] == "3"


def test_cross_turn_reused_call_id_is_explicitly_unsupported(adapter, tmp_path):
    result, events, config = fixture()
    earlier = events[7]["message"]["tool_calls"][0]
    events[12]["message"]["tool_calls"] = [copy.deepcopy(earlier)]
    events[11]["response"]["choices"][0]["message"]["tool_calls"] = [
        copy.deepcopy(events[6]["response"]["choices"][0]["message"]["tool_calls"][0])
    ]
    paths = inputs(adapter, tmp_path, (result, events, config))
    with pytest.raises(adapter.ConversionError, match="duplicate/empty tool call ID"):
        adapter.convert_native_rollout(*paths)
    assert not paths[-1].exists()


@pytest.mark.parametrize("corruption", ["task_hash", "selection_identity", "source_path", "duplicate_json_key"])
def test_manifest_and_json_identity_fail_closed(adapter, tmp_path, corruption):
    paths = inputs(adapter, tmp_path)
    manifest = json.loads(paths[2].read_text())
    if corruption == "task_hash":
        manifest["task_sha256"] = "0" * 64
    elif corruption == "selection_identity":
        p = Path(manifest["selection"]["path"])
        p.write_text('{"seed":300,"tasks":[{"domain":"airline","id":"8"}]}')
        manifest["selection"]["sha256"] = adapter.sha(p.read_bytes())
    elif corruption == "source_path":
        manifest["result"]["path"] = str(paths[1])
    else:
        paths[0].write_bytes(paths[0].read_bytes().replace(b'"reward": 0.0,', b'"reward": 1.0, "reward": 0.0,', 1))
        manifest["result"]["sha256"] = adapter.sha(paths[0].read_bytes())
    paths[2].write_bytes(adapter.json_bytes(manifest))
    with pytest.raises(adapter.ConversionError):
        adapter.convert_native_rollout(*paths)
    assert not paths[-1].exists()


@pytest.mark.parametrize("resume_dialogue", [True, False])
def test_scoring_phase_cannot_restart_dialogue_or_judging(adapter, tmp_path, resume_dialogue):
    result, events, config = fixture()
    messages = [{"role": "system", "content": "judge"}]
    judge = [
        {"event": "judge_request", "messages": messages},
        {
            "event": "judge_completion",
            "request": {"model": "simulator", "messages": messages, "temperature": 0.0, "max_tokens": 99, "seed": 300},
            "response": {"model": "simulator"},
        },
    ]
    extra = copy.deepcopy(events[2]) if resume_dialogue else copy.deepcopy(judge[0])
    events[-1:-1] = [*judge, extra]
    paths = inputs(adapter, tmp_path, (result, events, config))
    with pytest.raises(adapter.ConversionError, match="dialogue resumed after scoring|duplicate judge request"):
        adapter.convert_native_rollout(*paths)
    assert not paths[-1].exists()
