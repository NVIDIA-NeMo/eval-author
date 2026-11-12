# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic receipt-boundary controls; no model execution or benchmark qualification claims."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))
bridge = importlib.import_module("benchmark_runtime.mini_evidence")
native_evidence = importlib.import_module("eval_author_shared.native_evidence")
evidence = importlib.import_module("eval_author_shared.task_evidence")
json_bytes = importlib.import_module("working_paths").json_bytes


def _write(path, value):
    path.write_bytes((json.dumps(value) + "\n").encode() if "root_spans" in value else json_bytes(value))
    return native_evidence.binding(path)


def _emit(task_dir, output, benchmark, agent, mutation):
    task_dir, output = Path(task_dir), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    task = evidence.read(task_dir / "task.json")
    config = evidence.read(task_dir / "config.json")
    task_entry = _write(output / "task.json", {**task, **({"changed": True} if mutation == "task" else {})})
    config_entry = _write(output / "config.json", config)
    selection = _write(output / "selection.json", {"seed": 300})
    result = {
        "task_id": task["id"],
        "seed": 300,
        "termination": "assistant_final",
        "failure_class": None,
        "error": None,
        "reward": 0,
        "native_reward": 0,
        "split": "generated",
        "domain": "airline",
        "task_sha256": "synthetic-hash",
        "initial_fixture_sha256": "synthetic-fixture",
        "final_state_sha256": "synthetic-state-after",
        "tool_dispatch_exceptions": 0,
        "messages": [],
        "actions": [],
    }
    if mutation == "raw_error":
        result.update(failure_class="model_server_unavailable", reward=1, native_reward=1)
    result_entry = _write(output / "result.json", result)
    start = {
        "event": "start",
        "task": task,
        "split": "generated",
        "task_sha256": "synthetic-hash",
        "initial_fixture_sha256": "synthetic-fixture",
        "initial_state_sha256": "synthetic-state-before",
    }
    (output / "trace.jsonl").write_text(json.dumps(start) + "\n")
    request = {
        "event": "effective_request",
        "hints_applied": False,
        "scripted_control": not agent,
        "request": {"model": "other" if mutation == "model" else config["agent"]["model"]},
    }
    (output / "effective-requests.jsonl").write_text(json.dumps(request) + "\n")
    module = "tau2_mini.py" if benchmark == "tau2" else "workspace_assistant_mini.py"
    sources = {"harness.py": native_evidence.binding(Path(__file__))}
    sidecar = {
        "schema": "tau-mini-execution.v1" if benchmark == "tau2" else "workspace-assistant-mini-execution.v1",
        "status": "complete",
        "task_id": task["id"],
        "run_id": "synthetic-run",
        "seed": 300,
        "evidence_class": "primary_unhinted" if agent else "reference_control",
        "termination": "assistant_final",
        "failure_class": None,
        "scripted_control": not agent or mutation == "scripted_primary",
        "hint_sha256": "private" if mutation == "hinted" else None,
        "native_task": task_entry,
        "native_task_sha256": "synthetic-hash",
        "initial_fixture_sha256": "synthetic-fixture",
        "split": "generated",
        "domain": "airline",
        "inputs": {"config": config_entry, "selection": selection},
        "runtime_sources": sources,
        "adapter_sources": [native_evidence.binding(SCRIPTS / "benchmark_runtime" / module)],
        "result": result_entry,
        "trace": native_evidence.binding(output / "trace.jsonl"),
        "effective_requests": native_evidence.binding(output / "effective-requests.jsonl"),
        "control_health": "healthy",
    }
    if benchmark == "workspace_assistant":
        for label, phase in (("state_before", "before"), ("state_after", "after")):
            state = task["initial_state"]
            if phase == "after" and mutation == "missing_table":
                state = {**state, "tables": {}}
            snapshot = {
                "schema": "workspace-assistant-native-state.v1",
                "state": state,
                "state_sha256": "wrong" if mutation == "state_hash" else f"synthetic-state-{phase}",
            }
            sidecar[label] = _write(output / f"state-{phase}.json", snapshot)
        if mutation == "missing_snapshot":
            del sidecar["state_before"]
    if mutation in {"answer_primary", "answer_mismatch"}:
        (output / "control-answer.txt").write_text("Explicit control answer")
        sidecar["inputs"]["control_answer"] = sidecar["control_answer"] = native_evidence.binding(
            output / "control-answer.txt"
        )
    if agent:
        trajectory = {
            "schema_version": "3",
            "id": "synthetic-run",
            "session_id": "synthetic-run",
            "session_basis": "execution.run_id",
            "aggregate": {},
            "root_spans": [{"id": "observed", "kind": "CHAIN", "output": "Synthetic answer"}],
        }
        atif = _write(output / "trajectory.atif.json", trajectory)
        converter = native_evidence.binding(Path(__file__))
        if benchmark == "tau2":
            manifest = _write(output / "atif-input.json", {"task_sha256": task_entry["sha256"]})
            conversion = {
                "schema": "tau-mini-trace-conversion.v1",
                "status": "converted",
                "run_id": "synthetic-run",
                "evidence_class": "primary_unhinted",
                "sources": {
                    "result": result_entry,
                    "trace": sidecar["trace"],
                    "config": config_entry,
                    "run_manifest": manifest,
                },
                "converter": converter,
                "normalized_trace": {"path": "trajectory.atif.json", "sha256": atif["sha256"]},
                "runtime_sources": sources,
                "retained_inputs": {},
            }
            key = "trace_conversion"
        else:
            original = _write(output / "trajectory.native.atif.json", {**trajectory, "session_id": "original-native"})
            conversion = {
                "schema": "workspace-assistant-mini-trace-loading.v1",
                "run_id": "synthetic-run",
                "evidence_class": "primary_unhinted",
                "native_result": result_entry,
                "converter": converter,
                "task_sha256": task_entry["sha256"],
                "normalized_trace": atif,
                "native_export": original,
                "trace_id": "synthetic-run",
                "loading": _write(
                    output / "loading.json",
                    {
                        "normalized_sha256": atif["sha256"],
                        "source_sha256": {"source.atif.json": original["sha256"]},
                    },
                ),
            }
            key = "conversion"
        if mutation == "conversion":
            conversion["run_id"] = "other-run"
        sidecar.update(normalized_trace=atif, **{key: _write(output / "conversion-receipt.json", conversion)})
    _write(output / "execution-receipt.json", sidecar)
    if mutation.startswith("supplemental"):
        report = {
            "schema": "nemo.eval_author.native_supplemental.v1",
            "task_id": task["id"],
            "run_id": "synthetic-run",
            "native_result": result_entry,
            "task_sha256": task_entry["sha256"],
            "assessor_sources": [native_evidence.binding(Path(__file__))],
            "inputs": {"observations": sidecar["trace"]},
            "checks": {"answer": False},
        }
        if mutation == "supplemental_identity":
            report["run_id"] = "other"
        elif mutation == "supplemental_result":
            report["native_result"] = task_entry
        elif mutation == "supplemental_task":
            report["task_sha256"] = "wrong"
        elif mutation == "supplemental_source":
            report["assessor_sources"] = [task_entry]
        elif mutation == "supplemental_stale_input":
            report["inputs"]["observations"] = {**sidecar["trace"], "sha256": "wrong"}
        _write(output / "supplemental.json", report)
    if mutation == "extra_output":
        (output / "unlisted.txt").write_text("unlisted")


def _prepared(tmp_path, benchmark, *, agent=True, mutation=None, native_task=None, config=None, extra_bindings=()):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    _write(
        task_dir / "task.json",
        native_task
        or {
            "id": "synthetic-task",
            "initial_state": {
                "version": "synthetic-state-v1",
                "tables": {"synthetic.table": {"columns": ["id"], "index": [], "data": []}},
            },
        },
    )
    if native_task is not None:
        (task_dir / "tasks.jsonl").write_text(json.dumps(native_task) + "\n")
    _write(task_dir / "config.json", config or {"agent": {"model": "synthetic-solver"}})
    module = "tau2_mini.py" if benchmark == "tau2" else "workspace_assistant_mini.py"
    bindings = [
        *bridge.writer_bindings(),
        native_evidence.binding(Path(__file__)),
        native_evidence.binding(SCRIPTS / "benchmark_runtime" / module),
        *extra_bindings,
    ]
    bindings = list({entry["path"]: entry for entry in bindings}.values())
    if mutation == "writer":
        bindings = [entry for entry in bindings if entry["path"] != str(Path(bridge.__file__).resolve())]
    _write(task_dir / "runtime.json", {"files": bindings})
    reset = bridge.reset_for_task(task_dir / "task.json", benchmark=benchmark, seed=300)
    if mutation == "reset":
        reset["seed"] = 301
    _write(task_dir / "reset.json", reset)
    contract = {
        "task_id": "synthetic-task",
        "provider": "native",
        "native_run_id": "/native_run_id",
        "native": {name: name + ".json" for name in native_evidence.INPUTS},
        "alternative_not_applicable": "Synthetic protocol test only",
        "side_effect_not_applicable": "Synthetic protocol test only",
        "cases": {
            kind: {
                "kind": kind,
                "requirement": "Synthetic protocol test only",
                "health": [{"pointer": "/native/error", "equals": None}],
                "checks": [{"pointer": "/native/reward", "min": 1, "max": 1}],
            }
            for kind in ("reference", "incorrect", "empty", "agent")
        },
    }
    _write(tmp_path / "contract.json", contract)
    manifest = tmp_path / "manifest.json"
    evidence.prepare(task_dir, tmp_path / "contract.json", manifest)
    native_output = tmp_path / "native"
    names = [
        "task.json",
        "config.json",
        "selection.json",
        "result.json",
        "trace.jsonl",
        "effective-requests.jsonl",
        "execution-receipt.json",
    ]
    if benchmark == "workspace_assistant":
        names += ["state-before.json", "state-after.json"]
    if mutation in {"answer_primary", "answer_mismatch"}:
        names += ["control-answer.txt"]
    if mutation and mutation.startswith("supplemental"):
        names += ["supplemental.json"]
    if agent:
        names += [
            "trajectory.atif.json",
            "conversion-receipt.json",
            "atif-input.json" if benchmark == "tau2" else "trajectory.native.atif.json",
        ]
        if benchmark == "workspace_assistant":
            names += ["loading.json"]
    execution = tmp_path / "execution.json"
    converted = tmp_path / "normalized-conversion.json" if agent else None
    descriptor = {
        "execution": str(execution),
        "artifacts": [str(native_output / name) for name in names] + ([str(converted)] if converted else []),
    }
    _write(tmp_path / "source.json", descriptor)
    command = [
        sys.executable,
        str(Path(__file__)),
        "--emit",
        str(task_dir),
        str(native_output),
        benchmark,
        "agent" if agent else "control",
        mutation or "none",
    ]
    options = dict(
        manifest_path=manifest,
        case_id="agent" if agent else "reference",
        source_sidecar=native_output / "execution-receipt.json",
        output=execution,
        native_source=tmp_path / "source.json",
        native_output=native_output,
        command=command,
        conversion_path=converted,
    )
    if mutation and mutation.startswith("supplemental"):
        options["supplemental_result"] = native_output / "supplemental.json"
    return options


@pytest.mark.parametrize("benchmark", ["tau2", "workspace_assistant"])
@pytest.mark.parametrize("agent", [False, True])
def test_known_sidecars_normalize_without_changing_native_or_claiming_reward_is_health(tmp_path, benchmark, agent):
    options = _prepared(tmp_path, benchmark, agent=agent)
    execution = bridge.run(**options)
    assert execution["status"] == "complete"
    assert execution["evidence_class"] == ("primary_unhinted" if agent else "reference_control")
    result = evidence.read(Path(execution["result"]["path"]))
    assert result["reward"] == 0 and result["failure_class"] is None
    sidecar = evidence.read(options["source_sidecar"])
    assert native_evidence.binding(Path(execution["result"]["path"])) == sidecar["result"]
    assert execution["mini_sidecar"] in execution["artifacts"]
    if agent:
        assert execution["normalized_trace"] == sidecar["normalized_trace"]
        conversion = evidence.read(options["conversion_path"])
        assert conversion["native_conversion"] == sidecar["trace_conversion" if benchmark == "tau2" else "conversion"]
        assert conversion["task_sha256"] == execution["inputs"]["task"]["sha256"]
    else:
        assert "normalized_trace" not in execution


@pytest.mark.parametrize(
    "mutation",
    [
        "hinted",
        "raw_error",
        "task",
        "reset",
        "model",
        "scripted_primary",
        "conversion",
        "writer",
        "extra_output",
        "state_hash",
        "missing_table",
        "missing_snapshot",
        "answer_primary",
    ],
)
def test_misbound_or_failed_execution_cannot_be_normalized(tmp_path, mutation):
    options = _prepared(tmp_path, "workspace_assistant", mutation=mutation)
    with pytest.raises(evidence.EvidenceError):
        bridge.run(**options)
    assert not options["output"].exists()


def test_control_answer_must_match_actual_native_final_message(tmp_path):
    options = _prepared(tmp_path, "workspace_assistant", agent=False, mutation="answer_mismatch")
    with pytest.raises(evidence.EvidenceError, match="control answer"):
        bridge.run(**options)


def test_supplemental_report_retained_without_changing_native_reward(tmp_path):
    options = _prepared(tmp_path, "workspace_assistant", mutation="supplemental")
    execution = bridge.run(**options)
    assert execution["supplemental"]["checks"]["answer"] is False
    assert execution["supplemental_source"] in execution["artifacts"]
    assert evidence.read(Path(execution["result"]["path"]))["native_reward"] == 0


@pytest.mark.parametrize("mutation", ["identity", "result", "task", "source", "stale_input"])
def test_supplemental_binding_failures_rejected(tmp_path, mutation):
    options = _prepared(tmp_path, "workspace_assistant", mutation="supplemental_" + mutation)
    with pytest.raises(evidence.EvidenceError):
        bridge.run(**options)
    assert not options["output"].exists()


def test_supplemental_must_be_predeclared_before_producer_runs(tmp_path):
    options = _prepared(tmp_path, "workspace_assistant")
    with pytest.raises(evidence.EvidenceError, match="predeclared"):
        bridge.run(**options, supplemental_result=options["native_output"] / "undeclared.json")
    assert not options["native_output"].exists()


def test_existing_output_rejected_before_command(tmp_path):
    options = _prepared(tmp_path, "workspace_assistant")
    options["native_output"].mkdir()
    with pytest.raises(evidence.EvidenceError, match="fresh native output"):
        bridge.run(**options)
    assert list(options["native_output"].iterdir()) == []


def test_actual_native_inputs_require_canonical_bytes(tmp_path):
    task = tmp_path / "task.json"
    task.write_text('{"id":"x"}')
    with pytest.raises(evidence.EvidenceError, match="serialization"):
        bridge.reset_for_task(task, benchmark="workspace_assistant", seed=300)


def _wrapper_command(options):
    command = [
        sys.executable,
        "-m",
        "benchmark_runtime.mini_evidence",
        "--manifest",
        str(options["manifest_path"]),
        "--case",
        options["case_id"],
        "--source-sidecar",
        str(options["source_sidecar"]),
        "--output",
        str(options["output"]),
        "--native-source",
        str(options["native_source"]),
        "--native-output",
        str(options["native_output"]),
    ]
    if options["conversion_path"] is not None:
        command += ["--conversion", str(options["conversion_path"])]
    if options.get("supplemental_result") is not None:
        command += ["--supplemental-result", str(options["supplemental_result"])]
    return command + ["--command", *options["command"]]


def test_outer_recorder_retains_healthy_primary_behavior_failure(tmp_path, monkeypatch):
    options = _prepared(tmp_path, "workspace_assistant")
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    receipt = evidence.run(
        options["manifest_path"],
        "agent",
        "synthetic-run",
        tmp_path / "receipt.json",
        tmp_path / "normalized.json",
        options["native_output"] / "trajectory.atif.json",
        _wrapper_command(options),
        purpose="baseline",
        native_source=options["native_source"],
    )
    assert receipt["status"] == "behavior_failed"
    normalized = evidence.read(tmp_path / "normalized.json")
    assert normalized["native"]["reward"] == 0
    assert normalized["execution"]["status"] == "complete"


def test_actual_workspace_control_through_outer_recorder(tmp_path, monkeypatch):
    root = os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_ROOT")
    python = os.environ.get("EVAL_AUTHOR_TASTE_WORKSPACE_ASSISTANT_PYTHON")
    if not root or not python:
        pytest.skip("Selected Workspace Assistant ROOT and PYTHON required for actual native evidence control")
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    code = """
import json, sys
from benchmark_runtime.workspace_assistant_mini import load_selected_runtime
modules = load_selected_runtime(sys.argv[1])
env = modules['environment'].make_environment()
action = {'name': 'calendar_create_event', 'arguments': json.dumps({'event_name': 'Synthetic receipt check',
    'participant_email': 'synthetic@example.com', 'event_start': '2026-11-04 10:00:00', 'duration': '30'})}
print(json.dumps({'id': 'synthetic-task', 'category': 'offline-receipt-control', 'environment_name': 'workplace_assistant',
    'responses_create_params': {'input': [{'role': 'system', 'content': 'Use native workspace tools.'},
        {'role': 'user', 'content': 'Create the synthetic receipt check meeting.'}], 'tools': env['schemas']},
    'ground_truth': [action], 'initial_state': modules['fixture_state'].export_initial_state(env)}))
"""
    row = json.loads(subprocess.check_output([python, "-B", "-c", code, root], text=True))
    mini = importlib.import_module("benchmark_runtime.workspace_assistant_mini")
    before_sources = mini.runtime_bindings(root)
    sources = list(before_sources.values())
    sources.append(native_evidence.binding(SCRIPTS / "benchmark_runtime/authoring.py"))
    destination = Path(os.environ.get("EVAL_AUTHOR_MINI_EVIDENCE_CONTROL_OUTPUT", str(tmp_path / "actual")))
    destination.mkdir(parents=True, exist_ok=False)
    options = _prepared(
        destination,
        "workspace_assistant",
        agent=False,
        native_task=row,
        config={
            "agent": {
                "base_url": "https://invalid.example/v1",
                "model": "offline-native-control",
                "token_parameter": "max_completion_tokens",
                "max_tokens": 100,
                "temperature": 0.0,
            },
            "max_steps": 6,
            "timeout_seconds": 300,
        },
        extra_bindings=sources,
    )
    task_dir = destination / "task"
    answer = destination / "control-answer.txt"
    answer.write_bytes(b"Synthetic task completed.\r\n")
    options["command"] = [
        python,
        "-B",
        "-m",
        "benchmark_runtime.workspace_assistant_mini",
        "--native-root",
        root,
        "--tasks",
        str(task_dir / "tasks.jsonl"),
        "--task-id",
        row["id"],
        "--config",
        str(task_dir / "config.json"),
        "--output",
        str(options["native_output"]),
        "--run-id",
        "synthetic-native-reference",
        "--seed",
        "300",
        "--mode",
        "reference_control",
        "--control-answer",
        str(answer),
    ]
    descriptor = evidence.read(options["native_source"])
    descriptor["artifacts"] += [
        str(options["native_output"] / name)
        for name in ["control-actions.json", "control-answer.txt", "execution-started.json"]
    ]
    _write(options["native_source"], descriptor)
    receipt = evidence.run(
        options["manifest_path"],
        "reference",
        "synthetic-native-reference",
        destination / "receipt.json",
        destination / "normalized.json",
        None,
        _wrapper_command(options),
        purpose="baseline",
        native_source=options["native_source"],
    )
    assert receipt["status"] == "passed"
    normalized = evidence.read(destination / "normalized.json")
    assert normalized["native"]["native_reward"] == 1
    assert normalized["native"]["tool_dispatch_exceptions"] == 0
    assert normalized["execution"]["evidence_class"] == "reference_control"
    assert normalized["native"]["messages"][-1]["content"] == answer.read_bytes().decode()
    before = evidence.read(Path(normalized["execution"]["state_before"]["path"]))
    after = evidence.read(Path(normalized["execution"]["state_after"]["path"]))
    assert before["state"] == row["initial_state"]
    assert before["state"] != after["state"]
    assert after["state_sha256"] == normalized["native"]["final_state_sha256"]
    _write(
        destination / "readiness.json",
        {
            "model_calls": 0,
            "actual_native_harness": True,
            "outer_recorder_status": receipt["status"],
            "receipt": native_evidence.binding(destination / "receipt.json"),
            "native_sources_unchanged": mini.runtime_bindings(root) == before_sources,
        },
    )


if __name__ == "__main__":
    assert sys.argv[1] == "--emit"
    _emit(*sys.argv[2:5], agent=sys.argv[5] == "agent", mutation=sys.argv[6])
