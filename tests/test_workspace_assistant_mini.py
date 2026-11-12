# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline boundary tests and optional controls against the designated native mini."""

import copy
import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("benchmark_runtime.workspace_assistant_mini")


@dataclass
class Endpoint:
    model: str = "offline-control"
    temperature: float = 0.0
    max_tokens: int = 30
    token_parameter: str = "max_completion_tokens"
    extra_body: dict | None = None

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
        body.update(self.extra_body or {})
        return body, {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def test_private_hints_preserve_public_input_and_native_options(bridge):
    messages = [{"role": "system", "content": "native policy"}, {"role": "user", "content": "public request"}]
    tools = [{"type": "function", "function": {"name": "read"}}]
    before = copy.deepcopy((messages, tools))
    events = []
    wrapped = bridge._endpoint_type(Endpoint, hints="private hints text", record=events.append)
    endpoint = replace(wrapped(), extra_body={"parallel_tool_calls": False, "top_p": 0.9})
    request, _ = endpoint.complete(messages, tools, seed=300, timeout=7)
    assert (messages, tools) == before
    assert events[0]["original_messages"] == messages
    assert (
        request["messages"][0]["content"]
        == "native policy" + bridge.HINT_PREFIX + "private hints text" + bridge.HINT_SUFFIX
    )
    assert request["messages"][1:] == messages[1:]
    assert request["parallel_tool_calls"] is False and request["top_p"] == 0.9
    assert request["max_completion_tokens"] == 30
    ordinary = bridge._endpoint_type(Endpoint, hints=None, record=events.append)()
    unhinted, _ = ordinary.complete(messages, tools, seed=300, timeout=7)
    assert unhinted["messages"] == messages and "private hints" not in json.dumps(unhinted)


def test_effective_request_retained_on_transport_failure(bridge):
    class FailingEndpoint(Endpoint):
        def complete(self, *args, **kwargs):
            raise TimeoutError("offline failure")

    events = []
    wrapped = bridge._endpoint_type(FailingEndpoint, hints="private", record=events.append)()
    with pytest.raises(TimeoutError):
        wrapped.complete([{"role": "system", "content": "policy"}], [], seed=5, timeout=2)
    assert [row["event"] for row in events] == ["effective_request", "request_failed"]
    assert events[0]["request"]["messages"][0]["content"].endswith("private" + bridge.HINT_SUFFIX)


def test_control_answer_enters_only_final_assistant_response(bridge):
    answer = "Exact answer.\r\nSecond line.\n"
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "request"}]
    events = []
    actions = [{"name": "read", "arguments": "{}"}]
    endpoint = bridge._endpoint_type(
        Endpoint, hints=None, record=events.append, control_actions=actions, control_answer=answer
    )()
    _, first = endpoint.complete(messages, [], seed=300, timeout=1)
    action_message = first["choices"][0]["message"]
    assert action_message["content"] == "Deterministic native control."
    assert "Exact answer" not in json.dumps(events[0]["request"])
    _, second = endpoint.complete([*messages, action_message], [], seed=300, timeout=1)
    assert second["choices"][0]["message"] == {"role": "assistant", "content": answer}
    assert all(row["scripted_control"] and row["control_answer_sha256"] for row in events)
    empty = bridge._endpoint_type(
        Endpoint, hints=None, record=events.append, control_actions=[], control_answer=answer
    )()
    assert empty.complete(messages, [], seed=300, timeout=1)[1]["choices"][0]["message"]["content"] == answer


@pytest.mark.parametrize(
    "changes",
    [
        {"extra_body": {"messages": []}},
        {"base_url": "https://user:secret@example.com/v1"},
        {"base_url": "https://example.com/v1?key=secret"},
        {"max_tokens": True},
        {"token_parameter": "other"},
    ],
)
def test_config_rejects_identity_overrides_and_bad_limits(bridge, tmp_path, changes):
    endpoint = {"base_url": "https://example.com/v1", "model": "solver", **changes}
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"agent": endpoint, "max_steps": 6, "timeout_seconds": 300}))
    with pytest.raises(ValueError):
        bridge.read_config(path)


def test_private_and_control_inputs_cannot_enter_primary(bridge, tmp_path):
    common = dict(
        root=tmp_path,
        task=None,
        task_source=tmp_path / "tasks.jsonl",
        config_path=tmp_path / "config.json",
        output=tmp_path / "out",
        run_id="run",
        seed=300,
        mode="primary_unhinted",
        trace_python="python",
    )
    with pytest.raises(ValueError, match="hint receipt"):
        bridge.run_native_task(**common, hint_receipt=tmp_path / "hints.json")
    with pytest.raises(ValueError, match="control actions"):
        bridge.run_native_task(**common, control_actions_path=tmp_path / "actions.json")
    for mode in ("primary_unhinted", "hinted_feasibility"):
        options = {**common, "mode": mode}
        if mode == "hinted_feasibility":
            options["hint_receipt"] = tmp_path / "hints.json"
        with pytest.raises(ValueError, match="control answer"):
            bridge.run_native_task(**options, control_answer_path=tmp_path / "answer.txt")
    with pytest.raises(ValueError, match="Private hinted"):
        bridge.export_atif(
            root=tmp_path,
            result_path=tmp_path / "result.json",
            output=tmp_path,
            trace_python="python",
            run_id="run",
            mode="hinted_feasibility",
            task_binding={},
        )
    assert not (tmp_path / "out").exists()


def test_native_controls_and_validated_atif(tmp_path):
    native_root = os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_ROOT")
    native_python = os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_PYTHON")
    trace_python = os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_TRACE_PYTHON")
    if not all((native_root, native_python, trace_python)):
        pytest.skip("Set Workspace Assistant ROOT, PYTHON and TRACE_PYTHON for selected native controls")
    assert native_root is not None and native_python is not None and trace_python is not None
    destination = Path(os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_CONTROL_OUTPUT", str(tmp_path / "native")))
    script = r"""
import copy, json, sys
from pathlib import Path
from benchmark_runtime import workspace_assistant_mini as bridge

root, output, trace_python = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
output.mkdir(parents=True, exist_ok=False)
modules = bridge.load_selected_runtime(root)
before_sources = bridge.runtime_bindings(root)
env = modules['environment'].make_environment()
fixture = modules['fixture_state'].export_initial_state(env)
action = {'name': 'calendar_create_event', 'arguments': json.dumps({
    'event_name': 'Synthetic bridge control meeting', 'participant_email': 'synthetic@example.com',
    'event_start': '2026-11-04 10:00:00', 'duration': '30'})}
row = {'id': 'synthetic-control', 'category': 'offline-bridge-control', 'environment_name': 'workplace_assistant',
       'responses_create_params': {'input': [{'role': 'system', 'content': 'Use native workspace tools.'},
           {'role': 'user', 'content': 'Create the synthetic bridge control meeting.'}], 'tools': env['schemas'],
           'parallel_tool_calls': False}, 'ground_truth': [action], 'initial_state': fixture}
source = output / 'tasks.jsonl'
source.write_text(json.dumps(row) + '\n')
config = output / 'config.json'
config.write_text(json.dumps({'agent': {'base_url': 'https://invalid.example/v1', 'model': 'offline-native-control',
    'token_parameter': 'max_completion_tokens', 'max_tokens': 100, 'temperature': 0.0}, 'max_steps': 6, 'timeout_seconds': 300}))
task = bridge.load_tasks_file(modules, source)[0]
wrong = copy.deepcopy(action)
arguments = json.loads(wrong['arguments'])
arguments['event_name'] = 'Wrong meeting'
wrong['arguments'] = json.dumps(arguments)
negative = output / 'negative-actions.json'
negative.write_text(json.dumps([wrong]))
broken = output / 'broken-actions.json'
broken.write_text(json.dumps([{'name': 'missing_tool', 'arguments': '{}'}]))
receipts = {}
original_endpoint = modules['harness'].Endpoint
original_environment = modules['harness'].make_environment
answer = output / 'answer.txt'
answer.write_bytes(b'Exact synthetic native control answer.\r\nSecond line.\n')
for name, mode, controls in [('reference', 'reference_control', None), ('noop', 'noop_control', None),
                              ('negative', 'negative_control', negative), ('repeat', 'reference_control', None),
                              ('broken', 'negative_control', broken)]:
    receipt = bridge.run_native_task(root=root, task=task, task_source=source, config_path=config,
        output=output / name, run_id='synthetic-' + name, seed=300, mode=mode,
        control_actions_path=controls, control_answer_path=answer if name != 'repeat' else None,
        trace_python=trace_python if name == 'reference' else None)
    result = json.loads(Path(receipt['result']['path']).read_text())
    assert modules['harness'].Endpoint is original_endpoint
    assert modules['harness'].make_environment is original_environment
    assert result['native_reward'] == modules['scoring'].score(result['actions'], task.ground_truth, initial_state=fixture)['native_reward']
    assert result['initial_fixture_sha256'] == receipt['initial_fixture_sha256']
    assert result['final_answer_graded'] is False
    if name != 'repeat':
        assert result['messages'][-1]['content'] == answer.read_bytes().decode()
        assert Path(receipt['control_answer']['path']).read_bytes() == answer.read_bytes()
        assert receipt['inputs']['control_answer'] == bridge.binding(answer)
    else:
        assert result['messages'][-1]['content'] == 'Deterministic native control.'
    receipts[name] = receipt
assert [receipts[name]['native_reward'] for name in ['reference', 'noop', 'negative', 'repeat']] == [1.0, 0.0, 0.0, 1.0]
assert all(receipts[name]['status'] == 'complete' for name in ['reference', 'noop', 'negative', 'repeat'])
assert receipts['broken']['status'] == 'technical_failure' and receipts['broken']['control_health'] == 'unhealthy'
starts = [json.loads(Path(value['trace']['path']).read_text().splitlines()[0]) for value in receipts.values()]
assert len({row['initial_state_sha256'] for row in starts}) == 1
results = {name: json.loads(Path(value['result']['path']).read_text()) for name, value in receipts.items()}
assert results['reference']['final_state_sha256'] == results['repeat']['final_state_sha256']
assert results['noop']['final_state_sha256'] == starts[0]['initial_state_sha256']
snapshots = {}
for name, receipt in receipts.items():
    snapshots[name] = {}
    for label in ['state_before', 'state_after']:
        assert bridge.binding(receipt[label]['path']) == receipt[label]
        snapshots[name][label] = json.loads(Path(receipt[label]['path']).read_text())
    assert snapshots[name]['state_before']['state'] == fixture
    assert snapshots[name]['state_before']['state_sha256'] == starts[0]['initial_state_sha256']
    assert snapshots[name]['state_after']['state_sha256'] == results[name]['final_state_sha256']
    assert set(snapshots[name]['state_after']['state']['tables']) == set(fixture['tables'])
assert snapshots['noop']['state_before'] == snapshots['noop']['state_after']
assert snapshots['broken']['state_before'] == snapshots['broken']['state_after']
assert snapshots['reference']['state_after'] == snapshots['repeat']['state_after']
changed = [key for key, table in fixture['tables'].items()
           if table != snapshots['reference']['state_after']['state']['tables'][key]]
assert changed == ['calendar._calendar_events']
assert bridge.runtime_bindings(root) == before_sources
assert json.loads((output/'reference/task.json').read_text()) == task.row
normalized = json.loads(Path(receipts['reference']['normalized_trace']['path']).read_text())
assert normalized['schema_version'] == '3' and normalized['root_spans']
loading = json.loads((output/'reference/loaded/loading.json').read_text())
assert loading['normalized_sha256'] == receipts['reference']['normalized_trace']['sha256']
original_atif = json.loads((output/'reference/trajectory.native.atif.json').read_text())
assert any(step.get('tool_calls') for step in original_atif['steps'])
assert any(step.get('observation') for step in original_atif['steps'])
assert 'initial_state' not in json.dumps(results['reference']['messages'])
receipt = {'schema': 'workspace-assistant-mini-readiness.v1', 'model_calls': 0,
           'source_files_unchanged': True, 'controls': receipts,
           'checks': ['actual native harness', 'reference/noop/incorrect rewards', 'fixture reset and exact native score',
                      'tool-error control fails closed', 'native ATIF conversion validated', 'actual tool observations retained',
                      'complete before/after native state snapshots', 'explicit control answer exact bytes', 'factory restored']}
(output/'readiness.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'readiness': str(output/'readiness.json'), 'model_calls': 0}))
"""
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(SCRIPTS), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [native_python, "-B", "-c", script, native_root, str(destination), trace_python],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads((destination / "readiness.json").read_text())
    assert receipt["model_calls"] == 0
    assert receipt["source_files_unchanged"] is True


def test_analysis_loading_does_not_import_compass_in_native_runtime(bridge, tmp_path, monkeypatch):
    root = tmp_path / "native"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/export_atif.py").write_text(
        "import argparse,json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('--source'); p.add_argument('--output'); "
        "p.add_argument('--validate',action='store_true'); a=p.parse_args()\n"
        "Path(a.output).write_text(json.dumps({'schema_version':'ATIF-v1.7','session_id':'native',"
        "'agent':{'name':'control','version':'1'},'steps':[{'step_id':1,'source':'agent',"
        "'message':'Observed control'}]}))\n"
    )
    output = tmp_path / "output"
    output.mkdir()
    result = tmp_path / "result.json"
    result.write_text("{}")
    monkeypatch.setitem(sys.modules, "trace_loading", None)
    trace, receipt = bridge.export_atif(
        root=root,
        result_path=result,
        output=output,
        trace_python=sys.executable,
        run_id="run",
        mode="reference_control",
        task_binding={"sha256": "synthetic-task"},
    )
    normalized = json.loads(Path(trace["path"]).read_text())
    assert normalized["schema_version"] == "3" and normalized["root_spans"]
    recorded = json.loads(Path(receipt["path"]).read_text())
    assert recorded["trace_id"] == normalized["id"]
    assert (output / "loaded/loading.json").is_file()
