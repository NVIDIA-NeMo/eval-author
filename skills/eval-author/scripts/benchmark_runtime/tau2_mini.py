# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute supplied airline Tasks through a selected Tau mini, preserving native evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from working_paths import atomic_write, json_bytes

from benchmark_runtime.authoring import InfrastructureError, UnsupportedCapability
from benchmark_runtime.tau2 import Service, _activate

MODES = {"primary_unhinted", "hinted_feasibility", "reference_control"}
TASK_SERIALIZATION = "working_paths.json_bytes(Task.model_dump(mode='json')); UTF-8, indent=2, ensure_ascii=False, allow_nan=False, trailing newline, original key order"
HINT_PREFIX = "\n\n<private_feasibility_guidance>\n"
HINT_SUFFIX = "\n</private_feasibility_guidance>"


def binding(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_bindings(entries):
    for entry in entries:
        if binding(entry["path"]) != entry:
            raise InfrastructureError(f"Input bytes changed: {entry['path']}")


def runtime_bindings(root):
    root = Path(root).resolve()
    paths = [root / name for name in ("bootstrap.py", "client.py", "harness.py", "run.py")]
    paths += sorted((root / "vendor/tau2").rglob("*.py"))
    paths += [root / "prompts" / name for name in ("agent.txt", "airline.txt", "judge.txt")]
    paths += [root / "data/tau2/domains/airline" / name for name in ("db.json", "policy.md")]
    paths += [root / "data/tau2/user_simulator/simulation_guidelines.md"]
    return {str(path.relative_to(root)): binding(path) for path in paths}


def adapter_bindings():
    scripts = Path(__file__).resolve().parents[1]
    return {
        name: binding(scripts / name)
        for name in (
            "benchmark_runtime/tau2_mini.py",
            "benchmark_runtime/tau2.py",
            "benchmark_runtime/authoring.py",
            "working_paths.py",
        )
    }


def load_selected_runtime(root):
    root = Path(root).resolve()
    sys.dont_write_bytecode = True
    for name in ("bootstrap", "client", "harness", "run"):
        loaded = sys.modules.get(name)
        if loaded is not None and Path(loaded.__file__ or "").resolve() != root / f"{name}.py":
            raise InfrastructureError(f"Conflicting module {name}; run the mini bridge in a fresh process")
    _activate({"tau2_root": str(root), "data_dir": str(root / "data")})
    sys.path.insert(0, str(root))
    modules = {name: importlib.import_module(name) for name in ("bootstrap", "client", "harness", "run")}
    for name, module in modules.items():
        if not module.__file__ or Path(module.__file__).resolve() != root / f"{name}.py":
            raise InfrastructureError(f"Selected runtime mismatch: {name}")
    utils = importlib.import_module("tau2.utils")
    if Path(utils.DATA_DIR).resolve() != root / "data" or modules["bootstrap"].ROOT.resolve() != root:
        raise InfrastructureError("The mini bridge requires the selected root's original data directory")
    return modules


def read_config(path):
    config = json.loads(Path(path).read_text())
    if set(config) - {"agent", "user", "max_steps", "max_errors", "timeout_seconds"}:
        raise ValueError("Unsupported native config fields")
    fields = {"base_url", "model", "temperature", "max_tokens", "api_key_env", "extra_body", "token_parameter"}
    for side in ("agent", "user"):
        endpoint = config[side]
        if not isinstance(endpoint, dict) or set(endpoint) - fields or endpoint.get("extra_body"):
            raise ValueError(
                "Endpoint overrides/unknown fields are unsupported; credentials must stay in the environment"
            )
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
            raise ValueError("Each endpoint requires an explicit model")
        if endpoint.get("token_parameter", "max_tokens") not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token parameter")
        if type(endpoint.get("max_tokens", 4096)) is not int or endpoint.get("max_tokens", 4096) < 1:
            raise ValueError("max_tokens must be a positive integer")
    for name, default in (("max_steps", 200), ("max_errors", 10), ("timeout_seconds", 300)):
        value = config.get(name, default)
        if type(value) not in (int, float) or value <= 0 or (name != "timeout_seconds" and type(value) is not int):
            raise ValueError(f"Invalid native limit: {name}")
    return config


def load_tasks_file(path):
    Task = importlib.import_module("tau2.data_model.tasks").Task

    source = Path(path)
    rows = (
        [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
        if source.suffix == ".jsonl"
        else json.loads(source.read_text())
    )
    if not isinstance(rows, list) or not rows:
        raise ValueError("Native task input must be a nonempty JSON array or JSONL")
    tasks = [Task.model_validate(row) for row in rows]
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("Native task IDs must be unique")
    for task in tasks:
        criteria = task.evaluation_criteria
        if criteria is None or not criteria.reward_basis:
            raise ValueError(f"Explicit native evaluation criteria required: {task.id}")
    return tasks


class EffectiveEndpoint:
    def __init__(self, endpoint, *, participant, record, hints=None):
        if participant not in {"agent", "user"} or (hints is not None and participant != "agent"):
            raise ValueError("Private hints may be attached only to the evaluated agent")
        if endpoint.extra_body:
            raise UnsupportedCapability("Native endpoint overrides are unsupported")
        self.endpoint, self.participant, self.record, self.hints = endpoint, participant, record, hints
        self.calls = 0

    def complete(self, messages, tools, *, seed, timeout):
        original = copy.deepcopy(messages)
        effective = copy.deepcopy(messages)
        if self.hints is not None:
            if (
                not effective
                or effective[0].get("role") != "system"
                or not isinstance(effective[0].get("content"), str)
            ):
                raise UnsupportedCapability("Private hints require the native agent system message")
            effective[0]["content"] += HINT_PREFIX + self.hints + HINT_SUFFIX
        body = {
            "model": self.endpoint.model,
            "messages": effective,
            "temperature": self.endpoint.temperature,
            self.endpoint.token_parameter: self.endpoint.max_tokens,
            "seed": seed,
        }
        if tools:
            body.update(tools=copy.deepcopy(tools), tool_choice="auto")
        index = self.calls
        self.calls += 1
        self.record(
            {
                "event": "effective_request",
                "participant": self.participant,
                "call_index": index,
                "original_messages": original,
                "request": body,
                "timeout_seconds": timeout,
                "hints_applied": self.hints is not None,
            }
        )
        try:
            request, response = self.endpoint.complete(
                copy.deepcopy(effective), copy.deepcopy(tools), seed=seed, timeout=timeout
            )
            if request != body:
                raise InfrastructureError("Native Endpoint returned a different effective request")
        except Exception as error:
            self.record(
                {
                    "event": "request_failed",
                    "participant": self.participant,
                    "call_index": index,
                    "error_type": type(error).__name__,
                }
            )
            raise
        self.record(
            {
                "event": "effective_response",
                "participant": self.participant,
                "call_index": index,
                "request_sha256": hashlib.sha256(json_bytes(request)).hexdigest(),
                "response": response,
            }
        )
        return request, response


def _append(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _private_stream(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8")


def run_native_task(
    *,
    root,
    task,
    config_path,
    output,
    run_id,
    seed,
    mode,
    selection_path,
    hint_receipt=None,
    trace_python=None,
    endpoint_factory=None,
):
    if mode not in MODES or not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Explicit supported evidence class and nonempty run_id required")
    if mode == "hinted_feasibility" and hint_receipt is None:
        raise ValueError("Hinted feasibility requires an exact hint receipt")
    if mode == "primary_unhinted" and hint_receipt is not None:
        raise ValueError("Hints cannot enter primary unhinted runs")
    if mode == "primary_unhinted" and not trace_python:
        raise ValueError("Primary runs require --trace-python for the native Compass trace exporter")
    if endpoint_factory is not None and mode != "reference_control":
        raise ValueError("Scripted endpoints are permitted only for explicit reference controls")
    config = read_config(config_path)
    modules = load_selected_runtime(root)
    Task = importlib.import_module("tau2.data_model.tasks").Task
    task = Task.model_validate(task.model_dump(mode="json"))
    if task.evaluation_criteria is None or not task.evaluation_criteria.reward_basis:
        raise ValueError("Explicit native evaluation criteria required")
    if mode == "primary_unhinted":
        history = task.initial_state.message_history if task.initial_state else []
        if task.user_tools or any(
            message.role == "tool" or message.tool_calls or message.is_audio for message in history or []
        ):
            raise UnsupportedCapability(
                "Primary trace export supports text-only initial history without tools and no simulator tools"
            )
    sources = runtime_bindings(root)
    adapters = adapter_bindings()
    inputs = {"config": binding(config_path), "selection": binding(selection_path)}
    selection = json.loads(Path(selection_path).read_text())
    if selection.get("seed") != seed or selection.get("tasks", []).count({"domain": "airline", "id": task.id}) != 1:
        raise ValueError("Task/seed does not occur exactly once in the supplied selection")
    hints = None
    if hint_receipt is not None:
        inputs["hint_receipt"] = binding(hint_receipt)
        hints = json.loads(Path(hint_receipt).read_text())["hints"]
        if not isinstance(hints, str) or not hints.strip():
            raise ValueError("The hint receipt must contain nonempty hints text")
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    atomic_write(output / "task.json", json_bytes(task.model_dump(mode="json")))
    atomic_write(output / "config.json", Path(config_path).read_bytes())
    atomic_write(output / "selection.json", Path(selection_path).read_bytes())
    if hint_receipt is not None:
        atomic_write(output / "hint-receipt.json", Path(hint_receipt).read_bytes())
    receipt = {
        "schema": "tau-mini-execution.v1",
        "status": "started",
        "run_id": run_id,
        "task_id": task.id,
        "domain": "airline",
        "seed": seed,
        "evidence_class": mode,
        "task_serialization": TASK_SERIALIZATION,
        "native_task": binding(output / "task.json"),
        "inputs": inputs,
        "runtime_sources": sources,
        "adapter_sources": adapters,
        "bridge": binding(__file__),
        "network_policy": "native Endpoint; no bridge retries",
        "hint_sha256": hashlib.sha256(hints.encode()).hexdigest() if hints is not None else None,
        "scripted_control": endpoint_factory is not None,
    }
    atomic_write(output / "execution-started.json", json_bytes(receipt))
    factory = endpoint_factory or (lambda side: modules["client"].Endpoint(**config[side]))
    try:
        with (
            _private_stream(output / "trace.jsonl") as trace,
            _private_stream(output / "effective-requests.jsonl") as requests,
        ):
            agent = EffectiveEndpoint(
                factory("agent"), participant="agent", record=lambda row: _append(requests, row), hints=hints
            )
            user = EffectiveEndpoint(factory("user"), participant="user", record=lambda row: _append(requests, row))
            result = modules["harness"].run_task(
                "airline",
                task,
                agent=agent,
                user=user,
                seed=seed,
                emit=lambda row: _append(trace, row),
                max_steps=config.get("max_steps", 200),
                max_errors=config.get("max_errors", 10),
                timeout_seconds=config.get("timeout_seconds", 300),
            )
        atomic_write(output / "result.json", json_bytes(result))
        receipt.update(
            result=binding(output / "result.json"),
            trace=binding(output / "trace.jsonl"),
            effective_requests=binding(output / "effective-requests.jsonl"),
            status="native_complete",
            termination=result["termination"],
            native_reward=result["reward"],
            failure_class=(result.get("failure_class") or "unclassified_native_error")
            if result["termination"] == "error"
            else None,
        )
        verify_bindings([*inputs.values(), *sources.values(), *adapters.values(), receipt["native_task"]])
        if mode == "primary_unhinted" or (mode == "reference_control" and trace_python and hints is None):
            manifest = {
                "schema": "tau-mini-trace-input.v1",
                "run_id": run_id,
                "evidence_class": mode,
                "task_sha256": receipt["native_task"]["sha256"],
                "result": receipt["result"],
                "trace": receipt["trace"],
                "config": binding(output / "config.json"),
                "selection": binding(output / "selection.json"),
                "runtime_sources": sources,
            }
            atomic_write(output / "trace-input.json", json_bytes(manifest))
            command = [
                str(trace_python),
                "-B",
                "-m",
                "benchmark_runtime.tau2_trace",
                "--result",
                str(output / "result.json"),
                "--trace",
                str(output / "trace.jsonl"),
                "--run-manifest",
                str(output / "trace-input.json"),
                "--out-dir",
                str(output / "normalized_trace"),
            ]
            exported = subprocess.run(command, env=_child_env(), capture_output=True, timeout=120)
            atomic_write(output / "trace-stdout.txt", exported.stdout)
            atomic_write(output / "trace-stderr.txt", exported.stderr)
            if exported.returncode:
                raise InfrastructureError(
                    f"Native evidence retained; Compass trace export failed (exit {exported.returncode})"
                )
            receipt["trace_conversion"] = binding(output / "normalized/conversion-receipt.json")
            receipt["normalized_trace"] = binding(output / "normalized/trace.jsonl")
        receipt["status"] = "technical_failure" if result["termination"] == "error" else "complete"
    except BaseException as error:
        receipt.update(
            status="interrupted" if not isinstance(error, Exception) else "technical_failure",
            bridge_error_type=type(error).__name__,
            bridge_error=str(error),
        )
        for field, name in (("trace", "trace.jsonl"), ("effective_requests", "effective-requests.jsonl")):
            if (output / name).is_file():
                receipt[field] = binding(output / name)
        atomic_write(output / "execution-receipt.json", json_bytes(receipt))
        raise
    atomic_write(output / "execution-receipt.json", json_bytes(receipt))
    return receipt


def _child_env():
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return environment


def prepare_options(args):
    root = Path(args.native_root).resolve()
    if args.domain != "airline" or (args.data_dir and Path(args.data_dir).resolve() != root / "data"):
        raise UnsupportedCapability("Mini integration supports original airline data in the selected root")
    if not args.native_python or not args.mini_config:
        raise ValueError("Mini preparation requires --native-python and --mini-config")
    python = Path(args.native_python).absolute()
    if not python.is_file():
        raise ValueError("Selected native interpreter does not exist")
    config = read_config(args.mini_config)
    options = {
        "native_python": str(python),
        "mini_config": str(Path(args.mini_config).resolve()),
        "data_dir": str(root / "data"),
        "max_steps": config.get("max_steps", 200),
        "max_errors": config.get("max_errors", 10),
    }
    return options, {
        "config": binding(args.mini_config),
        "runtime_sources": runtime_bindings(root),
        "adapter_sources": adapter_bindings(),
    }


class MiniService(Service):
    def __init__(self, config):
        sys.dont_write_bytecode = True
        if (
            config.domain != "airline"
            or config.options.get("environment_options")
            or config.options.get("environment_module", "tau2.domains.airline.environment")
            != "tau2.domains.airline.environment"
            or config.options.get("environment_function", "get_environment") != "get_environment"
        ):
            raise UnsupportedCapability("Mini service requires the canonical native airline environment")
        super().__init__(config)
        context = config.context.get("mini_runtime")
        if not context:
            raise InfrastructureError("Explicit fresh mini preparation required")
        verify_bindings([context["config"], *context["runtime_sources"].values(), *context["adapter_sources"].values()])
        if Path(config.options["mini_config"]).resolve() != Path(context["config"]["path"]):
            raise InfrastructureError("Mini config differs from prepared configuration")

    def run_conversation(self, task, *, hints, settings):
        config = read_config(self.config.options["mini_config"])
        if settings["model"] != config["agent"]["model"]:
            raise InfrastructureError("Solver model differs from recorded native config")
        for name, default in (("max_steps", 200), ("max_errors", 10)):
            if settings.get(name, config.get(name, default)) != config.get(name, default):
                raise InfrastructureError(f"{name} differs from recorded native config")
        context = self.config.context["mini_runtime"]
        verify_bindings([context["config"], *context["runtime_sources"].values(), *context["adapter_sources"].values()])
        parent = Path(self.config.options["evidence_dir"]).resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        attempt = parent / f"mini-{uuid.uuid4().hex}"
        attempt.mkdir(mode=0o700)
        atomic_write(
            attempt / "tasks.jsonl", (json.dumps(task.model_dump(mode="json"), ensure_ascii=False) + "\n").encode()
        )
        # The scripted service receives text only. The caller owns the canonical RNG/reference receipt.
        atomic_write(attempt / "hints.json", json_bytes({"hints": hints, "source": "caller_supplied_text_only"}))
        command = [
            self.config.options["native_python"],
            "-B",
            "-m",
            "benchmark_runtime.tau2_mini",
            "run",
            "--native-root",
            self.config.options["tau2_root"],
            "--tasks",
            str(attempt / "tasks.jsonl"),
            "--config",
            self.config.options["mini_config"],
            "--output",
            str(attempt / "run"),
            "--run-id",
            attempt.name,
            "--seed",
            str(self.config.options.get("seed", 300)),
            "--mode",
            "hinted_feasibility",
            "--hint-receipt",
            str(attempt / "hints.json"),
        ]
        completed = subprocess.run(command, env=_child_env(), capture_output=True)
        atomic_write(attempt / "stdout.txt", completed.stdout)
        atomic_write(attempt / "stderr.txt", completed.stderr)
        if completed.returncode:
            raise InfrastructureError(f"Mini conversation failed; retained evidence: {attempt}")
        receipt_path = attempt / "run/task-0000/execution-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        verify_bindings([receipt["result"], receipt["trace"], receipt["native_task"]])
        return {
            "native_result": json.loads(Path(receipt["result"]["path"]).read_text()),
            "execution_receipt": binding(receipt_path),
        }

    @staticmethod
    def conversation_result(simulation):
        result = simulation["native_result"]
        return {
            "termination": "unexpected_error" if result["termination"] == "error" else result["termination"],
            "reward": result["reward"],
            "payload": simulation,
        }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Config: native agent/user Endpoint objects (base_url, model, api_key_env, temperature, max_tokens, "
            "token_parameter) plus max_steps/max_errors/timeout_seconds. Tasks: native Task JSON array or JSONL, "
            "unique IDs, explicit evaluation_criteria.reward_basis. Hints: exact canonical receipt with hints text; "
            "only that field is injected into the agent system input. Simulator/judge prompts remain native; "
            "public assistant utterances still follow native history. Output: selection.json, "
            "batch-receipt.json and private task-NNNN/{task,config,result,execution-receipt}.json, trace.jsonl, "
            "effective-requests.jsonl; primary runs also retain validated normalized/. No retries or overwritten attempts."
        ),
    )
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--native-root", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--mode", choices=("primary_unhinted", "hinted_feasibility"), default="primary_unhinted")
    parser.add_argument(
        "--hint-receipt", help="One exact hint receipt for one hinted Task; never a shared multi-task hint"
    )
    parser.add_argument(
        "--trace-python", help="Analysis interpreter with NeMo Compass required for primary unhinted export"
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    load_selected_runtime(args.native_root)
    read_config(args.config)
    tasks = load_tasks_file(args.tasks)
    if args.mode == "hinted_feasibility" and (len(tasks) != 1 or not args.hint_receipt):
        parser.error("Hinted feasibility requires one Task and --hint-receipt")
    if args.mode == "primary_unhinted" and (args.hint_receipt or not args.trace_python):
        parser.error("Primary runs require --trace-python and forbid --hint-receipt")
    output = Path(args.output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    atomic_write(output / "tasks.source.json", Path(args.tasks).read_bytes())
    selection = {
        "schema": "tau-mini-selection.v1",
        "seed": args.seed,
        "tasks": [{"domain": "airline", "id": task.id} for task in tasks],
        "task_source": binding(args.tasks),
        "run_id": args.run_id,
        "evidence_class": args.mode,
    }
    atomic_write(output / "selection.json", json_bytes(selection))
    receipts = []

    def launch(index_task):
        index, task = index_task
        return run_native_task(
            root=args.native_root,
            task=task,
            config_path=args.config,
            output=output / f"task-{index:04d}",
            run_id=f"{args.run_id}/{index}/{task.id}",
            seed=args.seed,
            mode=args.mode,
            selection_path=output / "selection.json",
            hint_receipt=args.hint_receipt,
            trace_python=args.trace_python,
        )

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(launch, pair) for pair in enumerate(tasks)]
        for index, future in enumerate(futures):
            try:
                result = future.result()
                receipts.append(
                    {
                        "task_id": tasks[index].id,
                        "status": result["status"],
                        "receipt": binding(output / f"task-{index:04d}/execution-receipt.json"),
                    }
                )
            except Exception as error:
                receipts.append(
                    {"task_id": tasks[index].id, "status": "technical_failure", "error_type": type(error).__name__}
                )
            atomic_write(
                output / "batch-receipt.json",
                json_bytes(
                    {
                        "schema": "tau-mini-batch.v1",
                        "runs": receipts,
                        "selection": binding(output / "selection.json"),
                        "concurrency": args.concurrency,
                    }
                ),
            )
    failed = any(receipt["status"] != "complete" for receipt in receipts)
    print(json.dumps({"output": str(output), "tasks": len(tasks), "technical_failure": failed}))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
