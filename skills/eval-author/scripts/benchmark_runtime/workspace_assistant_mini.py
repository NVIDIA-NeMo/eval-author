# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run generated Workspace Assistant tasks through the selected external mini harness."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from working_paths import atomic_write, json_bytes

from benchmark_runtime.authoring import InfrastructureError

MODES = {"primary_unhinted", "hinted_feasibility", "reference_control", "noop_control", "negative_control"}
CONTROL_MODES = {"reference_control", "noop_control", "negative_control"}
HINT_PREFIX = "\n\n<private_feasibility_guidance>\n"
HINT_SUFFIX = "\n</private_feasibility_guidance>"
_RUN_LOCK = threading.Lock()
LIMITATIONS = [
    "Native state reward does not grade the final chat answer.",
    "Native plot-state equality does not prove a rendered file exists or its visual correctness.",
    "Explicit initial_state uses the mini's experimental fixture scorer with native comparison rules.",
]


def binding(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_bindings(entries):
    for entry in entries:
        if binding(entry["path"]) != entry:
            raise InfrastructureError(f"Input bytes changed: {entry['path']}")


def runtime_bindings(root):
    root = Path(root).resolve()
    paths = [
        root / name
        for name in (
            "bootstrap.py",
            "client.py",
            "harness.py",
            "environment.py",
            "fixture_state.py",
            "tasks.py",
            "scoring.py",
            "scripts/export_atif.py",
            "requirements.txt",
            "vendor-manifest.json",
        )
    ]
    paths += sorted(path for path in (root / "vendor/workplace_assistant").rglob("*") if path.suffix in {".py", ".csv"})
    return {str(path.relative_to(root)): binding(path) for path in paths}


def load_selected_runtime(root):
    root = Path(root).resolve()
    sys.dont_write_bytecode = True
    names = ("bootstrap", "client", "environment", "fixture_state", "tasks", "scoring", "harness")
    for name in names:
        loaded = sys.modules.get(name)
        if loaded is not None and Path(loaded.__file__ or "").resolve() != root / f"{name}.py":
            raise InfrastructureError(f"Conflicting module {name}; use a fresh process for this native runtime")
    for name, loaded in tuple(sys.modules.items()):
        if name == "workplace_assistant" or name.startswith("workplace_assistant."):
            if not Path(loaded.__file__ or "").resolve().is_relative_to(root / "vendor/workplace_assistant"):
                raise InfrastructureError("Workspace Assistant package was imported from another runtime")
    sys.path.insert(0, str(root))
    modules = {name: importlib.import_module(name) for name in names}
    for name, module in modules.items():
        if Path(module.__file__ or "").resolve() != root / f"{name}.py":
            raise InfrastructureError(f"Selected runtime mismatch: {name}")
    if modules["bootstrap"].ROOT.resolve() != root:
        raise InfrastructureError("Selected native fixture root differs")
    return modules


def read_config(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or set(config) - {"agent", "max_steps", "timeout_seconds"}:
        raise ValueError("Unsupported native config fields")
    endpoint = config["agent"]
    fields = {"base_url", "model", "temperature", "max_tokens", "api_key_env", "extra_body", "token_parameter"}
    if not isinstance(endpoint, dict) or set(endpoint) - fields:
        raise ValueError("Unsupported native endpoint fields")
    address = urlsplit(endpoint["base_url"])
    if (
        address.scheme not in {"http", "https"}
        or not address.hostname
        or address.username
        or address.password
        or address.query
        or address.fragment
    ):
        raise ValueError("Endpoint must be a credential-free HTTP(S) URL")
    if not isinstance(endpoint.get("model"), str) or not endpoint["model"]:
        raise ValueError("Explicit model required")
    if endpoint.get("token_parameter", "max_tokens") not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Unsupported token parameter")
    extras = endpoint.get("extra_body") or {}
    if not isinstance(extras, dict) or set(extras) - {"parallel_tool_calls", "top_p"}:
        raise ValueError("Only native parallel_tool_calls/top_p endpoint options are supported")
    for key, value in (("max_steps", config.get("max_steps", 6)), ("max_tokens", endpoint.get("max_tokens", 4096))):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    timeout = config.get("timeout_seconds", 300)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    return config


def load_tasks_file(modules, path, *, split="generated"):
    tasks = modules["tasks"].load_tasks(split, path=Path(path))
    if not tasks:
        raise ValueError("At least one native task is required")
    for task in tasks:
        modules["tasks"].model_input(task)
    return tasks


def _append(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _private_stream(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")


def _capture_state(modules, environment, path):
    state_hash = modules["environment"].state_hash(environment)
    state = modules["fixture_state"].export_initial_state(environment)
    tables = {
        f"{name}.{field}"
        for name, container in environment["containers"].items()
        for field, value in vars(container).items()
        if hasattr(value, "to_json")
    }
    if set(state["tables"]) != tables:
        raise InfrastructureError("Native fixture export omitted live environment tables")
    if modules["environment"].state_hash(environment) != state_hash:
        raise InfrastructureError("Native state export changed the live environment")
    snapshot = {
        "schema": "workspace-assistant-native-state.v1",
        "state_sha256": state_hash,
        "serialization": "fixture_state.export_initial_state; native fixture JSON encoding",
        "state": state,
    }
    atomic_write(path, json_bytes(snapshot))
    return snapshot


def _endpoint_type(base, *, hints, record, control_actions=None, control_answer=None):
    class RecordedEndpoint(base):
        def complete(self, messages, tools, *, seed, timeout):
            original = copy.deepcopy(messages)
            effective = copy.deepcopy(messages)
            if hints is not None:
                if (
                    not effective
                    or effective[0].get("role") != "system"
                    or not isinstance(effective[0].get("content"), str)
                ):
                    raise ValueError("Private hints require an initial native system message")
                effective[0]["content"] += HINT_PREFIX + hints + HINT_SUFFIX
            request = {
                "model": self.model,
                "messages": effective,
                "temperature": self.temperature,
                self.token_parameter: self.max_tokens,
                "seed": seed,
            }
            if tools:
                request.update(tools=copy.deepcopy(tools), tool_choice="auto")
            request.update(self.extra_body or {})
            record(
                {
                    "event": "effective_request",
                    "original_messages": original,
                    "request": request,
                    "hints_applied": hints is not None,
                    "scripted_control": control_actions is not None,
                    "control_answer_sha256": hashlib.sha256(control_answer.encode()).hexdigest()
                    if control_answer is not None
                    else None,
                    "timeout_seconds": timeout,
                }
            )
            if control_actions is not None:
                initial = not any(message["role"] == "assistant" for message in messages)
                message = {"role": "assistant", "content": "Deterministic native control."}
                if initial and control_actions:
                    message["tool_calls"] = [
                        {"id": f"control-{index}", "type": "function", "function": action}
                        for index, action in enumerate(control_actions)
                    ]
                elif control_answer is not None:
                    message["content"] = control_answer
                response = {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {}}
            else:
                try:
                    actual, response = super().complete(effective, copy.deepcopy(tools), seed=seed, timeout=timeout)
                    if actual != request:
                        raise InfrastructureError("Native endpoint returned a different effective request")
                except Exception as error:
                    record({"event": "request_failed", "error_type": type(error).__name__})
                    raise
            return request, response

    return RecordedEndpoint


def export_atif(*, root, result_path, output, trace_python, run_id, mode, task_binding):
    if mode not in {"primary_unhinted", *CONTROL_MODES}:
        raise ValueError("Private hinted traces cannot be exported as primary or control ATIF")
    output = Path(output)
    original = output / "trajectory.native.atif.json"
    command = [
        str(trace_python),
        "-B",
        str(Path(root).resolve() / "scripts/export_atif.py"),
        "--source",
        str(result_path),
        "--output",
        str(original),
        "--validate",
    ]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    atomic_write(output / "atif-stdout.txt", completed.stdout)
    atomic_write(output / "atif-stderr.txt", completed.stderr)
    if completed.returncode:
        raise InfrastructureError(f"Native ATIF conversion failed; native outputs retained at {output}")
    loaded = output / "loaded"
    # The analysis interpreter owns Compass; the native runtime need not install it.
    loaded_result = subprocess.run(
        [
            str(trace_python),
            "-B",
            str(Path(__file__).resolve().parents[1] / "trace_loading.py"),
            "--source-kind",
            "atif",
            "--input",
            str(original),
            "--output-dir",
            str(loaded),
        ],
        capture_output=True,
        timeout=120,
    )
    atomic_write(output / "loading-stdout.txt", loaded_result.stdout)
    atomic_write(output / "loading-stderr.txt", loaded_result.stderr)
    if loaded_result.returncode:
        raise InfrastructureError("Native trace could not be loaded through NeMo Compass")
    loading = json.loads((loaded / "loading.json").read_text())
    if loading["trace_count"] != 1:
        raise InfrastructureError("Native export must contain exactly one trace")
    final = loaded / "traces.normalized.jsonl"
    trace_id = json.loads(final.read_text())["id"]
    receipt = {
        "schema": "workspace-assistant-mini-trace-loading.v1",
        "run_id": run_id,
        "evidence_class": mode,
        "native_result": binding(result_path),
        "task_sha256": task_binding["sha256"],
        "normalized_trace": binding(final),
        "trace_id": trace_id,
        "native_export": binding(original),
        "loading": binding(loaded / "loading.json"),
        "converter": binding(Path(root) / "scripts/export_atif.py"),
        "transformations": ["External native ATIF loaded unchanged through the shared Compass loader"],
    }
    atomic_write(output / "conversion-receipt.json", json_bytes(receipt))
    return binding(final), binding(output / "conversion-receipt.json")


def run_native_task(
    *,
    root,
    task,
    task_source,
    config_path,
    output,
    run_id,
    seed,
    mode,
    hint_receipt=None,
    trace_python=None,
    control_actions_path=None,
    control_answer_path=None,
):
    if mode not in MODES or not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Explicit supported mode and nonempty run_id required")
    if (mode == "hinted_feasibility") != (hint_receipt is not None):
        raise ValueError("Only hinted feasibility requires and accepts a hint receipt")
    if (mode == "negative_control") != (control_actions_path is not None):
        raise ValueError("Only negative controls require and accept explicit control actions")
    if control_answer_path is not None and mode not in CONTROL_MODES:
        raise ValueError("Only control modes accept an explicit control answer")
    if mode == "primary_unhinted" and not trace_python:
        raise ValueError("Primary runs require --trace-python for loaded native traces")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    sources = runtime_bindings(root)
    inputs = {"config": binding(config_path), "task_source": binding(task_source)}
    config = read_config(config_path)
    modules = load_selected_runtime(root)
    selected = [item for item in load_tasks_file(modules, task_source, split=task.split) if item.id == task.id]
    if len(selected) != 1 or selected[0].row != task.row:
        raise ValueError("Executed task must match exactly one selected source row")
    task = selected[0]
    verify_bindings([*inputs.values(), *sources.values()])
    adapter_sources = [
        binding(__file__),
        binding(Path(__file__).resolve().parents[1] / "working_paths.py"),
        binding(Path(__file__).with_name("authoring.py")),
    ]
    hints = None
    control_answer = None
    if control_answer_path is not None:
        inputs["control_answer"] = binding(control_answer_path)
        control_answer = Path(control_answer_path).read_bytes().decode("utf-8")
    if hint_receipt is not None:
        inputs["hint_receipt"] = binding(hint_receipt)
        hints = json.loads(Path(hint_receipt).read_text())["hints"]
        if not isinstance(hints, str) or not hints.strip():
            raise ValueError("Hint receipt must contain nonempty hints text")
    actions = None
    if mode == "reference_control":
        actions = task.ground_truth
    elif mode == "noop_control":
        actions = []
    elif mode == "negative_control":
        assert control_actions_path is not None
        inputs["control_actions"] = binding(control_actions_path)
        actions = json.loads(Path(control_actions_path).read_text())
    if actions is not None:
        if not isinstance(actions, list) or any(
            not isinstance(a, dict)
            or set(a) != {"name", "arguments"}
            or not isinstance(a["name"], str)
            or not isinstance(a["arguments"], str)
            or not isinstance(json.loads(a["arguments"]), dict)
            for a in actions
        ):
            raise ValueError("Control actions must be native name/JSON arguments records")
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    atomic_write(output / "task.json", json_bytes(task.row))
    atomic_write(output / "config.json", Path(config_path).read_bytes())
    selection = {
        "split": task.split,
        "seed": seed,
        "repeats": 1,
        "tasks": [{"id": task.id, "sha256": task.sha256}],
        "runtime_hashes": {name: entry["sha256"] for name, entry in sources.items()},
    }
    atomic_write(output / "selection.json", json_bytes(selection))
    if hint_receipt is not None:
        atomic_write(output / "hint-receipt.json", Path(hint_receipt).read_bytes())
    if actions is not None:
        atomic_write(output / "control-actions.json", json_bytes(actions))
    if control_answer is not None:
        atomic_write(output / "control-answer.txt", control_answer.encode("utf-8"))
    receipt = {
        "schema": "workspace-assistant-mini-execution.v1",
        "status": "started",
        "run_id": run_id,
        "task_id": task.id,
        "split": task.split,
        "seed": seed,
        "mode": mode,
        "evidence_class": "reference_control" if mode in CONTROL_MODES else mode,
        "native_task": binding(output / "task.json"),
        "native_task_sha256": task.sha256,
        "inputs": inputs,
        "runtime_sources": sources,
        "adapter_sources": adapter_sources,
        "scripted_control": actions is not None,
        "control_answer": binding(output / "control-answer.txt") if control_answer_path is not None else None,
        "hints_applied": hints is not None,
        "hint_sha256": hashlib.sha256(hints.encode()).hexdigest() if hints is not None else None,
        "interaction": "native fixed initial request; no user simulator",
        "grading_limits": LIMITATIONS,
        "initial_fixture_sha256": modules["fixture_state"].initial_state_hash(task.initial_state)
        if task.initial_state is not None
        else None,
    }
    atomic_write(output / "execution-started.json", json_bytes(receipt))
    try:
        with (
            _RUN_LOCK,
            _private_stream(output / "trace.jsonl") as trace,
            _private_stream(output / "effective-requests.jsonl") as requests,
        ):
            harness = modules["harness"]
            original_endpoint = harness.Endpoint
            original_environment = harness.make_environment
            captured = {}

            def capture_environment(*args, **kwargs):
                if captured:
                    raise InfrastructureError("Native harness unexpectedly created more than one live environment")
                environment = original_environment(*args, **kwargs)
                captured["environment"] = environment
                captured["before"] = _capture_state(modules, environment, output / "state-before.json")
                return environment

            def emit(event):
                if event.get("event") == "start":
                    if event["initial_state_sha256"] != captured["before"]["state_sha256"]:
                        raise InfrastructureError("Native start state differs from captured initial state")
                    captured["start_seen"] = True
                _append(trace, {"timestamp": time.time(), **event})

            harness.Endpoint = _endpoint_type(
                original_endpoint,
                hints=hints,
                record=lambda row: _append(requests, row),
                control_actions=actions,
                control_answer=control_answer,
            )
            harness.make_environment = capture_environment
            result = None
            try:
                result = harness.run_task(
                    task,
                    config=copy.deepcopy(config),
                    seed=seed,
                    emit=emit,
                    cancelled=threading.Event(),
                )
                atomic_write(output / "result.json", json_bytes(result))
            finally:
                harness.Endpoint = original_endpoint
                harness.make_environment = original_environment
                if "environment" in captured:
                    captured["after"] = _capture_state(modules, captured["environment"], output / "state-after.json")
            if not captured.get("start_seen") or captured["after"]["state_sha256"] != result["final_state_sha256"]:
                raise InfrastructureError("Native final state differs from captured final state")
        receipt.update(
            result=binding(output / "result.json"),
            trace=binding(output / "trace.jsonl"),
            effective_requests=binding(output / "effective-requests.jsonl"),
            state_before=binding(output / "state-before.json"),
            state_after=binding(output / "state-after.json"),
            termination=result["termination"],
            native_reward=result["native_reward"],
            failure_class=result["failure_class"],
            status="native_complete",
        )
        verify_bindings([*inputs.values(), *sources.values(), *adapter_sources, receipt["native_task"]])
        if trace_python and mode != "hinted_feasibility":
            receipt["normalized_trace"], receipt["conversion"] = export_atif(
                root=root,
                result_path=output / "result.json",
                output=output,
                trace_python=trace_python,
                run_id=run_id,
                mode=mode,
                task_binding=receipt["native_task"],
            )
        control_failed = mode in CONTROL_MODES and (
            result["termination"] != "assistant_final" or result["tool_dispatch_exceptions"] != 0
        )
        receipt["control_health"] = ("unhealthy" if control_failed else "healthy") if mode in CONTROL_MODES else None
        receipt["status"] = "technical_failure" if result["failure_class"] or control_failed else "complete"
    except BaseException as error:
        receipt.update(
            status="interrupted" if not isinstance(error, Exception) else "technical_failure",
            bridge_error_type=type(error).__name__,
            bridge_error=str(error),
        )
        for field, filename in (
            ("trace", "trace.jsonl"),
            ("effective_requests", "effective-requests.jsonl"),
            ("state_before", "state-before.json"),
            ("state_after", "state-after.json"),
            ("result", "result.json"),
        ):
            if (output / filename).is_file():
                receipt[field] = binding(output / filename)
        atomic_write(output / "execution-receipt.json", json_bytes(receipt))
        raise
    atomic_write(output / "execution-receipt.json", json_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", required=True)
    parser.add_argument("--tasks", required=True, help="Generated native JSONL; source checkout is not modified")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--split", default="generated")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="Fresh private output directory")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--mode", choices=sorted(MODES), default="primary_unhinted")
    parser.add_argument("--hint-receipt", help="Only its hints text enters the solver request")
    parser.add_argument("--control-actions", help="Native actions JSON array; negative_control only")
    parser.add_argument("--control-answer", help="UTF-8 final assistant answer file; control modes only")
    parser.add_argument("--trace-python", help="Selected analysis Python with NeMo Compass; required for primary runs")
    args = parser.parse_args()
    modules = load_selected_runtime(args.native_root)
    selected = [task for task in load_tasks_file(modules, args.tasks, split=args.split) if task.id == args.task_id]
    if len(selected) != 1:
        parser.error("--task-id must select exactly one source task")
    receipt = run_native_task(
        root=args.native_root,
        task=selected[0],
        task_source=args.tasks,
        config_path=args.config,
        output=args.output,
        run_id=args.run_id,
        seed=args.seed,
        mode=args.mode,
        hint_receipt=args.hint_receipt,
        trace_python=args.trace_python,
        control_actions_path=args.control_actions,
        control_answer_path=args.control_answer,
    )
    print(json.dumps(receipt, indent=2))
    return int(receipt["status"] != "complete")


if __name__ == "__main__":
    raise SystemExit(main())
