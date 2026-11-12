# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline isolation/provenance controls; optional controls use the selected native airline runtime."""

import copy
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("benchmark_runtime.tau2_mini")


class Endpoint:
    model = "solver"
    temperature = 0.0
    max_tokens = 4096
    token_parameter = "max_completion_tokens"
    extra_body = None

    def __init__(self, *, fail=False):
        self.fail = fail
        self.seen = []

    def complete(self, messages, tools, *, seed, timeout):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            self.token_parameter: self.max_tokens,
            "seed": seed,
        }
        if tools:
            body.update(tools=tools, tool_choice="auto")
        self.seen.append(copy.deepcopy(body))
        if self.fail:
            raise TimeoutError("offline control")
        return body, {"choices": [{"message": {"content": "done"}}], "usage": {"completion_tokens": 1}}


def test_only_agent_system_input_gets_hint_and_original_is_retained(bridge):
    messages = [{"role": "system", "content": "native policy"}, {"role": "user", "content": "public request"}]
    tools = [{"type": "function", "function": {"name": "read"}}]
    before = copy.deepcopy((messages, tools))
    events = []
    raw = Endpoint()
    agent = bridge.EffectiveEndpoint(raw, participant="agent", hints="private hint", record=events.append)
    agent.complete(messages, tools, seed=300, timeout=300)
    assert (messages, tools) == before
    assert events[0]["original_messages"] == messages
    assert (
        raw.seen[0]["messages"][0]["content"]
        == "native policy" + bridge.HINT_PREFIX + "private hint" + bridge.HINT_SUFFIX
    )
    assert raw.seen[0]["messages"][1:] == messages[1:]
    assert raw.seen[0]["tools"] == tools
    assert events[1]["response"]["usage"]["completion_tokens"] == 1
    user = Endpoint()
    wrapper = bridge.EffectiveEndpoint(user, participant="user", record=events.append)
    wrapper.complete(messages, [], seed=300, timeout=300)
    wrapper.complete([{"role": "system", "content": "judge"}], [], seed=300, timeout=300)
    assert "private hint" not in json.dumps(user.seen)
    assert events[2]["hints_applied"] is False


def test_failed_request_still_retains_effective_input(bridge):
    events = []
    endpoint = bridge.EffectiveEndpoint(Endpoint(fail=True), participant="agent", hints="private", record=events.append)
    with pytest.raises(TimeoutError):
        endpoint.complete([{"role": "system", "content": "policy"}], [], seed=9, timeout=12)
    assert [event["event"] for event in events] == ["effective_request", "request_failed"]
    assert events[0]["request"]["messages"][0]["content"].endswith("private" + bridge.HINT_SUFFIX)
    assert events[1]["error_type"] == "TimeoutError"


def test_hint_actor_and_override_rejections(bridge):
    with pytest.raises(ValueError, match="only"):
        bridge.EffectiveEndpoint(Endpoint(), participant="user", hints="private", record=lambda row: None)
    endpoint = Endpoint()
    endpoint.extra_body = {"messages": []}
    with pytest.raises(bridge.UnsupportedCapability, match="overrides"):
        bridge.EffectiveEndpoint(endpoint, participant="agent", record=lambda row: None)
    wrapper = bridge.EffectiveEndpoint(Endpoint(), participant="agent", hints="x", record=lambda row: None)
    with pytest.raises(bridge.UnsupportedCapability, match="system"):
        wrapper.complete([{"role": "user", "content": "request"}], [], seed=0, timeout=1)


@pytest.mark.parametrize(
    "change",
    [
        {"extra_body": {"messages": []}},
        {"api_key": "should never be recorded"},
        {"base_url": "https://user:secret@example.test/v1"},
        {"base_url": "https://example.test/v1?key=secret"},
        {"token_parameter": "other"},
        {"max_tokens": -1},
    ],
)
def test_reject_unsupported_or_credential_config(bridge, tmp_path, change):
    config = {side: {"base_url": "https://example.test/v1", "model": side} for side in ("agent", "user")}
    config["agent"].update(change)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        bridge.read_config(path)


def test_mode_gates_precede_native_imports(bridge, tmp_path):
    common = dict(
        root=tmp_path,
        task=SimpleNamespace(id="control"),
        config_path=tmp_path / "absent",
        output=tmp_path / "out",
        run_id="run",
        seed=300,
        selection_path=tmp_path / "selection",
    )
    with pytest.raises(ValueError, match="Hints cannot"):
        bridge.run_native_task(**common, mode="primary_unhinted", hint_receipt=tmp_path / "hints")
    with pytest.raises(ValueError, match="exact hint"):
        bridge.run_native_task(**common, mode="hinted_feasibility")
    with pytest.raises(ValueError, match="trace-python"):
        bridge.run_native_task(**common, mode="primary_unhinted")
    with pytest.raises(ValueError, match="Scripted"):
        bridge.run_native_task(
            **common, mode="primary_unhinted", trace_python=sys.executable, endpoint_factory=lambda side: Endpoint()
        )
    assert not (tmp_path / "out").exists()


def test_changed_source_rejected_and_result_adapters_remain_distinct(bridge, tmp_path):
    path = tmp_path / "input"
    path.write_text("before")
    bound = bridge.binding(path)
    path.write_text("after")
    with pytest.raises(bridge.InfrastructureError, match="changed"):
        bridge.verify_bindings([bound])
    simulation = SimpleNamespace(
        termination_reason="user_stop",
        reward_info=SimpleNamespace(reward=1),
        model_dump=lambda **kwargs: {"native_full_runtime": True},
    )
    assert bridge.Service.conversation_result(simulation) == {
        "termination": "user_stop",
        "reward": 1,
        "payload": {"native_full_runtime": True},
    }
    mini = {"native_result": {"termination": "error", "reward": 0, "failure_class": "model_server_unavailable"}}
    adapted = bridge.MiniService.conversation_result(mini)
    assert adapted["termination"] == "unexpected_error" and adapted["payload"] is mini


def _native_controls(root, seed_file, output, trace_python):
    import copy
    import importlib
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any

    from benchmark_runtime import tau2
    from benchmark_runtime import tau2_mini as bridge

    root, output = Path(root), Path(output)
    modules = bridge.load_selected_runtime(root)
    Task = importlib.import_module("tau2.data_model.tasks").Task

    rows = [json.loads(line) for line in Path(seed_file).read_text().splitlines()]
    candidate = copy.deepcopy(
        next(row["native"] for row in rows if any(a["name"] == "cancel_reservation" for a in row["actions"]))
    )
    candidate["task"]["id"] = "offline-control/custom-id"
    candidate["task"]["evaluation_criteria"]["reward_basis"] = ["DB"]
    config: dict[str, Any] = {
        side: {"base_url": "http://localhost.invalid/v1", "model": side, "token_parameter": "max_completion_tokens"}
        for side in ("agent", "user")
    }
    config.update(max_steps=20, max_errors=10, timeout_seconds=300)
    output.mkdir()
    (output / "config.json").write_text(json.dumps(config))
    service = tau2.Service(
        SimpleNamespace(domain="airline", options={"tau2_root": str(root), "data_dir": str(root / "data")})
    )
    task = service.build(candidate, {})
    actions = candidate["reference_actions"]
    reservation_id = next(a["arguments"]["reservation_id"] for a in actions if a["name"] == "cancel_reservation")
    reservation = copy.deepcopy(service.environment().tools.db.model_dump(mode="json")["reservations"][reservation_id])
    fixture = {"agent_data": {"reservations": {reservation_id: reservation}}}
    task = service.build(candidate, fixture)
    assert task.initial_state.initialization_data.agent_data == fixture["agent_data"]
    assert service.grade(task, actions)["reward"] == 1
    assert service.grade(task, [])["reward"] == 0
    first, second = service.open(task), service.open(task)
    initial = first.snapshot()
    for action in actions:
        assert first.execute(action).error is None
    assert first.snapshot() != initial and second.snapshot() == initial
    first.reset()
    assert first.snapshot() == initial
    (output / "tasks.jsonl").write_text(json.dumps(task.model_dump(mode="json")) + "\n")
    assert bridge.load_tasks_file(output / "tasks.jsonl")[0] == task
    (output / "selection.json").write_text(json.dumps({"seed": 300, "tasks": [{"domain": "airline", "id": task.id}]}))
    (output / "hints.json").write_text(
        json.dumps({"hints": "PRIVATE_HINT_SENTINEL", "reference": "FULL_REFERENCE_SENTINEL"})
    )

    class Scripted(modules["client"].Endpoint):
        def __init__(self, side, positive=True, fail=False):
            super().__init__(**config[side])
            self.side, self.positive, self.fail, self.count = side, positive, fail, 0

        def complete(self, messages, tools, *, seed, timeout):
            self.count += 1
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                self.token_parameter: self.max_tokens,
                "seed": seed,
            }
            if tools:
                body.update(tools=tools, tool_choice="auto")
            if self.fail:
                raise modules["client"].ModelServerUnavailable("offline transport failure")
            if self.side == "user" and "expectedOutcomes:" in messages[-1]["content"]:
                raw = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "results": [
                                {"expectedOutcome": assertion, "metExpectation": True}
                                for assertion in task.evaluation_criteria.nl_assertions
                            ]
                        }
                    ),
                }
            elif self.side == "user":
                content = "Please perform the requested cancellation." if self.count == 1 else "###STOP###"
                raw = {"role": "assistant", "content": content}
            elif self.count == 1 and self.positive:
                raw = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"control-{i}",
                            "type": "function",
                            "function": {"name": a["name"], "arguments": json.dumps(a["arguments"])},
                        }
                        for i, a in enumerate(actions)
                    ],
                }
            else:
                raw = {"role": "assistant", "content": "Finished."}
            return body, {
                "model": self.model,
                "choices": [{"message": raw, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }

    def run(name, *, positive=True, hints=False, fail=False, export=False):
        return bridge.run_native_task(
            root=root,
            task=task,
            config_path=output / "config.json",
            output=output / name,
            run_id=name,
            seed=300,
            mode="reference_control",
            selection_path=output / "selection.json",
            hint_receipt=output / "hints.json" if hints else None,
            endpoint_factory=lambda side: Scripted(side, positive=positive, fail=fail and side == "agent"),
            trace_python=trace_python if export else None,
        )

    positive = run("positive", export=True)
    negative = run("negative", positive=False)
    hinted = run("hinted", hints=True)
    failed = run("transport", fail=True)
    assert positive["native_reward"] == hinted["native_reward"] == 1 and negative["native_reward"] == 0
    assert failed["status"] == "technical_failure" and failed["failure_class"] == "model_server_unavailable"
    assert first.snapshot() == initial and service.open(task).snapshot() == initial
    original_task = task
    judge_task = copy.deepcopy(task.model_dump(mode="json"))
    judge_task["evaluation_criteria"]["reward_basis"] = ["DB", "NL_ASSERTION"]
    judge_task["evaluation_criteria"]["nl_assertions"] = ["The assistant acknowledged completion."]
    task = Task.model_validate(judge_task)
    judged = run("hinted-judge", hints=True)
    judge_events = [json.loads(line) for line in (output / "hinted-judge/trace.jsonl").read_text().splitlines()]
    assert judged["native_reward"] == 1
    assert len([e for e in judge_events if e["event"] == "judge_completion"]) == 1
    assert "PRIVATE_HINT_SENTINEL" not in json.dumps([e for e in judge_events if e["event"].startswith("judge_")])
    task = original_task
    events = [json.loads(line) for line in (output / "hinted/trace.jsonl").read_text().splitlines()]
    effective = [json.loads(line) for line in (output / "hinted/effective-requests.jsonl").read_text().splitlines()]
    assert events[0]["task"] == task.model_dump(mode="json")
    assert "FULL_REFERENCE_SENTINEL" not in json.dumps(events) + json.dumps(effective)
    assert "PRIVATE_HINT_SENTINEL" not in json.dumps(
        [e for e in events if e.get("participant") == "user" or e["event"] == "request"]
    )
    assert all(
        "PRIVATE_HINT_SENTINEL" in e["request"]["messages"][0]["content"]
        for e in effective
        if e["event"] == "effective_request" and e["participant"] == "agent"
    )
    bridge.verify_bindings(
        [*positive["runtime_sources"].values(), positive["native_task"], positive["normalized_trace"]]
    )
    exported = json.loads(Path(positive["normalized_trace"]["path"]).read_text())
    assert exported["attributes"]["evidence_class"] == "reference_control"
    bad = copy.deepcopy(task.model_dump(mode="json"))
    bad["evaluation_criteria"] = None
    (output / "invalid.json").write_text(json.dumps([bad]))
    try:
        bridge.load_tasks_file(output / "invalid.json")
        raise AssertionError("missing criteria accepted")
    except ValueError:
        pass
    (output / "duplicate.json").write_text(json.dumps([task.model_dump(mode="json"), task.model_dump(mode="json")]))
    try:
        bridge.load_tasks_file(output / "duplicate.json")
        raise AssertionError("duplicate native IDs accepted")
    except ValueError:
        pass
    invalid_history = copy.deepcopy(task.model_dump(mode="json"))
    invalid_history["initial_state"]["message_history"] = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "unresolved", "name": "calculate", "arguments": {"expression": "1+1"}, "requestor": "assistant"}
            ],
        }
    ]
    task = Task.model_validate(invalid_history)
    try:
        run("initialization-error")
        raise AssertionError("unresolved initial call accepted")
    except ValueError:
        pass
    initialization = json.loads((output / "initialization-error/execution-receipt.json").read_text())
    assert initialization["status"] == "technical_failure" and "result" not in initialization
    assert not (output / "initialization-error/trace.jsonl").read_text()
    task = original_task
    prepared = tau2.prepare(
        SimpleNamespace(
            native_root=str(root),
            domain="airline",
            data_dir=str(root / "data"),
            seed_path=str(output / "tasks.jsonl"),
            split="offline-control",
            output=str(output / "prepared"),
            dialogue_runner="mini",
            native_python=str(root / ".venv/bin/python"),
            mini_config=str(output / "config.json"),
        )
    )
    spec = json.loads(prepared.read_text())
    metadata = json.loads((prepared.parent / "tool_spec.json").read_text())
    toolkit = service.environment().tools
    assert all(metadata[name]["type"] == toolkit.tool_type(name).value.upper() for name in metadata)
    assert spec["task_service"] == "benchmark_runtime.tau2_mini:MiniService"
    restored = Task.model_validate(json.loads((output / "positive/task.json").read_text()))
    assert restored == task
    return {
        "positive": positive["native_reward"],
        "negative": negative["native_reward"],
        "hinted": hinted["native_reward"],
        "transport": failed["failure_class"],
        "native_reset": True,
        "native_task_fixture_roundtrip": True,
        "hint_isolation": True,
        "judge_isolation": True,
        "initialization_failure_retained": True,
        "full_reference_not_sent": True,
        "native_source_parity": True,
        "metadata_types": {
            kind: sum(tool["type"] == kind for tool in metadata.values()) for kind in ("READ", "WRITE", "GENERIC")
        },
        "normalized_trace": positive["normalized_trace"],
        "output": str(output),
    }


def test_selected_native_airline_controls(tmp_path):
    root = os.environ.get("TAU_MINI_TEST_ROOT")
    seeds = os.environ.get("TAU_MINI_TEST_SEEDS")
    if not root or not seeds:
        pytest.skip("Set TAU_MINI_TEST_ROOT and TAU_MINI_TEST_SEEDS to the explicitly selected airline inputs")
    output = Path(os.environ.get("TAU_MINI_TEST_OUTPUT", str(tmp_path / "native-controls")))
    source = (
        inspect.getsource(_native_controls) + "\nimport sys, json\nprint(json.dumps(_native_controls(*sys.argv[1:])))\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SCRIPTS)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(Path(root) / ".venv/bin/python"), "-B", "-c", source, root, seeds, str(output), sys.executable],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.splitlines()[-1])
    assert receipt["positive"] == receipt["hinted"] == 1 and receipt["negative"] == 0
    assert receipt["metadata_types"] == {"READ": 6, "WRITE": 6, "GENERIC": 2}
    (output / "control-summary.json").write_text(json.dumps(receipt, indent=2) + "\n")
