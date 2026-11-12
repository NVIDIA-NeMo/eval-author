# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bind one inspected Tau or Workspace Assistant mini execution to EA native evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

from eval_author_shared import native_evidence
from eval_author_shared import task_evidence as evidence
from working_paths import atomic_write, json_bytes

SCHEMAS = {"tau-mini-execution.v1": "tau2", "workspace-assistant-mini-execution.v1": "workspace_assistant"}
RESET_SCHEMA = "nemo.eval_author.mini_reset.v1"


def writer_bindings():
    scripts = Path(__file__).resolve().parents[1]
    return [
        native_evidence.binding(scripts / name)
        for name in (
            "benchmark_runtime/mini_evidence.py",
            "working_paths.py",
            "eval_author_shared/native_evidence.py",
            "eval_author_shared/task_evidence.py",
        )
    ]


def reset_for_task(task_path, *, benchmark, seed):
    if benchmark not in SCHEMAS.values() or type(seed) is not int:
        raise evidence.EvidenceError("Supported benchmark and integer reset seed required")
    task_path = Path(task_path)
    task = evidence.read(task_path)
    if task_path.read_bytes() != json_bytes(task):
        raise evidence.EvidenceError("Native task input must use working_paths.json_bytes serialization")
    return {
        "schema": RESET_SCHEMA,
        "benchmark": benchmark,
        "task_id": str(task["id"]),
        "seed": seed,
        "task_sha256": evidence.digest(task_path),
        "initial_state_json_sha256": hashlib.sha256(json_bytes(task.get("initial_state"))).hexdigest(),
        "strategy": "fresh_native_task",
    }


def _require(condition, message):
    if not condition:
        raise evidence.EvidenceError(message)


def _bindings(value):
    if isinstance(value, dict):
        value = list(value.values())
    _require(isinstance(value, list) and bool(value), "Nonempty runtime/adapter source bindings required")
    for entry in value:
        native_evidence.checked(entry)
    return value


def _registered(entries, registered):
    for entry in entries:
        native_evidence.checked(entry)
        _require(entry in registered, f"Executed source is absent from prepared runtime.files: {entry['path']}")


def _same_bytes(actual, expected, label):
    native_evidence.checked(actual)
    native_evidence.checked(expected)
    _require(actual["sha256"] == expected["sha256"], f"Executed {label} differs from prepared input")


def _jsonl(path):
    return [evidence.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _validate_conversion(sidecar, *, benchmark, registered, expected, result):
    atif_entry = sidecar["normalized_trace"]
    atif_path = native_evidence.checked(atif_entry)
    conversion_entry = sidecar["trace_conversion"] if benchmark == "tau2" else sidecar["conversion"]
    conversion_path = native_evidence.checked(conversion_entry)
    conversion = evidence.read(conversion_path)
    _require(conversion.get("run_id") == sidecar["run_id"], "Native conversion run identity mismatch")
    _require(conversion.get("evidence_class") == sidecar["evidence_class"], "Native conversion evidence class mismatch")
    _registered([conversion["converter"]], registered)
    if benchmark == "tau2":
        _require(
            conversion.get("schema") == "tau-mini-trace-conversion.v1" and conversion.get("status") == "converted",
            "Incomplete Tau native conversion",
        )
        for entry in conversion["sources"].values():
            native_evidence.checked(entry)
        for label in ("result", "trace"):
            _require(conversion["sources"][label] == sidecar[label], f"Tau conversion {label} source mismatch")
        _same_bytes(conversion["sources"]["config"], expected["config"], "conversion config")
        manifest = evidence.read(native_evidence.checked(conversion["sources"]["run_manifest"]))
        _require(manifest.get("task_sha256") == expected["task"]["sha256"], "Tau conversion task mismatch")
        converted = {
            "path": str((conversion_path.parent / conversion["normalized_trace"]["path"]).resolve()),
            "sha256": conversion["normalized_trace"]["sha256"],
        }
        _require(converted == atif_entry, "Tau conversion output mismatch")
        _registered(_bindings(conversion["runtime_sources"]), registered)
        for name, digest in conversion["retained_inputs"].items():
            retained = conversion_path.parent / name
            _require(retained.resolve().is_relative_to(conversion_path.parent), "Retained source escapes conversion")
            _require(evidence.digest(retained) == digest, "Retained Tau conversion source changed")
    else:
        _require(
            conversion.get("schema") == "workspace-assistant-mini-trace-loading.v1",
            "Unsupported Workspace Assistant conversion",
        )
        _require(conversion.get("native_result") == sidecar["result"], "Workspace conversion result mismatch")
        _require(conversion.get("task_sha256") == expected["task"]["sha256"], "Workspace conversion task mismatch")
        _require(conversion.get("normalized_trace") == atif_entry, "Workspace conversion output mismatch")
        native_evidence.checked(conversion["native_export"])
        loading = evidence.read(native_evidence.checked(conversion["loading"]))
        _require(loading.get("normalized_sha256") == atif_entry["sha256"], "Native loading output changed")
        _require(
            loading.get("source_sha256", {}).get("source.atif.json") == conversion["native_export"]["sha256"],
            "Native loading source changed",
        )
    from trace_loading import load_snapshot

    traces, _ = load_snapshot(atif_path, "trace")
    _require(len(traces) == 1 and bool(traces[0].root_spans), "Native normalized trace is empty")
    if benchmark == "tau2":
        _require(traces[0].session_id == sidecar["run_id"], "Native trace run identity mismatch")
    else:
        _require(traces[0].id == conversion["trace_id"], "Native loaded trace identity mismatch")
    return atif_entry, conversion_entry


def normalize(*, manifest, case, source_sidecar, descriptor, conversion_path, command, supplemental_result=None):
    expected = manifest["native_inputs"]["files"]
    registered = manifest["native_inputs"]["runtime_sources"]
    _registered(writer_bindings(), registered)
    sidecar = evidence.read(source_sidecar)
    benchmark = SCHEMAS.get(sidecar.get("schema"))
    _require(benchmark is not None, "Unsupported mini execution sidecar")
    _require(sidecar.get("status") == "complete", "Mini execution did not complete without technical failure")
    _require(sidecar.get("task_id") == manifest["contract"]["task_id"], "Mini task identity mismatch")
    _require(isinstance(sidecar.get("run_id"), str) and bool(sidecar["run_id"].strip()), "Missing mini run identity")
    agent = case["kind"] == "agent"
    expected_class = "primary_unhinted" if agent else "reference_control"
    _require(sidecar.get("evidence_class") == expected_class, "Hinted or wrong-kind mini execution")
    _require(
        sidecar.get("hint_sha256") is None
        and not sidecar.get("hints_applied")
        and "hint_receipt" not in sidecar["inputs"],
        "Private hinted execution cannot qualify",
    )
    _require(
        type(sidecar.get("scripted_control")) is bool and (not agent or not sidecar["scripted_control"]),
        "Scripted execution cannot qualify as an actual primary agent",
    )
    _require(
        not agent or ("control_answer" not in sidecar["inputs"] and sidecar.get("control_answer") is None),
        "A scripted control answer cannot enter primary evidence",
    )
    _same_bytes(sidecar["native_task"], expected["task"], "task")
    _same_bytes(sidecar["inputs"]["config"], expected["config"], "config")
    for entry in sidecar["inputs"].values():
        native_evidence.checked(entry)
    _registered(_bindings(sidecar["runtime_sources"]), registered)
    adapters = _bindings(sidecar["adapter_sources"])
    _registered(adapters, registered)
    mini_module = "tau2_mini.py" if benchmark == "tau2" else "workspace_assistant_mini.py"
    _require(
        native_evidence.binding(Path(__file__).with_name(mini_module)) in adapters,
        "Selected mini bridge source is not bound",
    )
    reset = evidence.read(Path(expected["reset"]["path"]))
    _require(
        reset == reset_for_task(Path(expected["task"]["path"]), benchmark=benchmark, seed=sidecar["seed"]),
        "Reset seed, task or fixture semantics differ from executed task",
    )
    result_path = native_evidence.checked(sidecar["result"])
    result = evidence.read(result_path)
    trace = _jsonl(native_evidence.checked(sidecar["trace"]))
    requests = _jsonl(native_evidence.checked(sidecar["effective_requests"]))
    _require(
        result.get("task_id") == sidecar["task_id"] and result.get("seed") == sidecar["seed"],
        "Native result task/seed mismatch",
    )
    _require(
        result.get("termination") == sidecar.get("termination")
        and result.get("failure_class") is None
        and result.get("error") is None
        and sidecar.get("failure_class") is None
        and result.get("termination") not in {None, "error", "unexpected_error"},
        "Native result reports an execution failure",
    )
    if not agent:
        _require(
            result["termination"] not in {"max_steps", "max_errors", "truncated", "content_filter"},
            "An incomplete control cannot establish qualification",
        )
    observed = [row for row in requests if row.get("event") == "effective_request"]
    _require(
        bool(observed) and all(row.get("hints_applied") is False for row in observed),
        "Actual unhinted effective requests are required",
    )
    _require(not any(row.get("event") == "request_failed" for row in requests), "A model request failed")
    config = evidence.read(Path(expected["config"]["path"]))
    for request in observed:
        participant = request.get("participant", "agent")
        _require(
            participant in config and request["request"].get("model") == config[participant]["model"],
            "Actual effective request model differs from prepared config",
        )
    _require(bool(trace) and trace[0].get("event") == "start", "Missing native start event")
    if benchmark == "workspace_assistant":
        _require(
            all(request.get("scripted_control") is sidecar["scripted_control"] for request in observed),
            "Effective native requests disagree with the declared control mode",
        )
        _require(
            "control_answer" in sidecar["inputs"]
            or (
                sidecar.get("control_answer") is None
                and all(request.get("control_answer_sha256") is None for request in observed)
            ),
            "Explicit control answer is not bound as an input",
        )
        initial_state = evidence.read(Path(expected["task"]["path"])).get("initial_state")
        fixture_hash = sidecar.get("initial_fixture_sha256")
        _require(
            (isinstance(fixture_hash, str) and bool(fixture_hash))
            if initial_state is not None
            else fixture_hash is None,
            "Native fixture selection is missing from execution evidence",
        )
        _require(
            result.get("task_sha256") == sidecar.get("native_task_sha256") == trace[0].get("task_sha256"),
            "Native Workspace task hash mismatch",
        )
        _require(result.get("split") == sidecar.get("split") == trace[0].get("split"), "Native split mismatch")
        _require(
            result.get("initial_fixture_sha256")
            == sidecar.get("initial_fixture_sha256")
            == trace[0].get("initial_fixture_sha256"),
            "Native fixture hash mismatch",
        )
        snapshots = {}
        for label, expected_hash in (
            ("state_before", trace[0].get("initial_state_sha256")),
            ("state_after", result.get("final_state_sha256")),
        ):
            _require(label in sidecar, "Complete native before/after snapshots are required")
            snapshot = evidence.read(native_evidence.checked(sidecar[label]))
            _require(
                snapshot.get("schema") == "workspace-assistant-native-state.v1"
                and bool(expected_hash)
                and snapshot.get("state_sha256") == expected_hash,
                "Native state snapshot does not match the observed execution",
            )
            state = snapshot.get("state")
            if not isinstance(state, dict):
                raise evidence.EvidenceError("Native state snapshot must contain complete fixture tables")
            _require(
                isinstance(state.get("tables"), dict) and bool(state["tables"]),
                "Native state snapshot must contain complete fixture tables",
            )
            snapshots[label] = state
        _require(
            snapshots["state_before"].get("version") == snapshots["state_after"].get("version")
            and set(snapshots["state_before"]["tables"]) == set(snapshots["state_after"]["tables"]),
            "Native before/after snapshot table schemas differ",
        )
        if initial_state is not None:
            _require(
                snapshots["state_before"] == initial_state,
                "Captured native initial state differs from the prepared task fixture",
            )
        if "control_answer" in sidecar["inputs"]:
            _same_bytes(sidecar["control_answer"], sidecar["inputs"]["control_answer"], "control answer")
            answer = Path(sidecar["control_answer"]["path"]).read_bytes().decode("utf-8")
            _require(
                result.get("messages")
                and result["messages"][-1].get("role") == "assistant"
                and result["messages"][-1].get("content") == answer,
                "Native final control answer differs from its explicit input",
            )
            _require(
                all(
                    request.get("scripted_control") is True
                    and request.get("control_answer_sha256") == sidecar["control_answer"]["sha256"]
                    for request in observed
                ),
                "Effective control requests do not bind the explicit final answer",
            )
        if not agent:
            _require(
                sidecar.get("control_health") == "healthy" and result.get("tool_dispatch_exceptions") == 0,
                "Control tool execution failed",
            )
    else:
        _require(
            json_bytes(trace[0].get("task")) == Path(expected["task"]["path"]).read_bytes(),
            "Native Tau start task differs from prepared input",
        )
        _require(result.get("domain") == sidecar.get("domain"), "Native Tau domain mismatch")
    outputs = [Path(value).resolve() for value in descriptor["artifacts"]]
    execution = {
        "schema": native_evidence.SCHEMA,
        "task_id": sidecar["task_id"],
        "run_id": sidecar["run_id"],
        "status": "complete",
        "evidence_class": expected_class,
        "inputs": expected,
        "result": sidecar["result"],
        "adapter": "benchmark_runtime.mini_evidence",
        "mini_sidecar": native_evidence.binding(source_sidecar),
        "command": command,
        "writer_sources": writer_bindings(),
        "health_basis": "Native completion and error fields; reward is not a health check",
    }
    excluded = {result_path.resolve()}
    if agent:
        _require(conversion_path is not None, "Primary normalization requires a fresh conversion receipt output")
        atif_entry, original_conversion = _validate_conversion(
            sidecar, benchmark=benchmark, registered=registered, expected=expected, result=result
        )
        from trace_loading import load_snapshot

        traces, _ = load_snapshot(Path(atif_entry["path"]), "trace")
        conversion = {
            "schema": "nemo.eval_author.mini_conversion_binding.v1",
            "trace_id": traces[0].id,
            "native_result": sidecar["result"],
            "task_sha256": expected["task"]["sha256"],
            "normalized_trace": atif_entry,
            "run_id": sidecar["run_id"],
            "evidence_class": expected_class,
            "native_conversion": original_conversion,
            "writer": native_evidence.binding(Path(__file__)),
            "transformations": "Receipt shape only; native result and normalized trace bytes unchanged",
        }
        conversion_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(conversion_path, json_bytes(conversion))
        execution.update(normalized_trace=atif_entry, conversion=native_evidence.binding(conversion_path))
        excluded.update({Path(atif_entry["path"]).resolve(), conversion_path.resolve()})
    else:
        _require(
            conversion_path is None, "Controls retain native conversion as raw evidence; no primary conversion output"
        )
    execution["artifacts"] = [native_evidence.binding(path) for path in outputs if path not in excluded]
    if supplemental_result is not None:
        report_binding = native_evidence.binding(supplemental_result)
        _require(report_binding in execution["artifacts"], "Supplemental report must be a retained raw artifact")
        report = evidence.read(supplemental_result)
        _require(isinstance(report, dict), "Native supplemental report must be a JSON object")
        _require(
            report.get("schema") == "nemo.eval_author.native_supplemental.v1", "Unsupported native supplemental report"
        )
        _require(
            report.get("task_id") == sidecar["task_id"] and report.get("run_id") == sidecar["run_id"],
            "Supplemental task or run identity mismatch",
        )
        _require(
            report.get("native_result") == sidecar["result"]
            and report.get("task_sha256") == expected["task"]["sha256"],
            "Supplemental native result or task binding mismatch",
        )
        _registered(_bindings(report.get("assessor_sources")), registered)
        _require(isinstance(report.get("inputs"), dict), "Supplemental input bindings are required")
        for entry in report["inputs"].values():
            native_evidence.checked(entry)
        execution.update(supplemental=report, supplemental_source=report_binding)
    if benchmark == "workspace_assistant":
        for label in ("state_before", "state_after"):
            _require(sidecar[label] in execution["artifacts"], "Native state snapshot must be retained")
            execution[label] = sidecar[label]
        if sidecar.get("control_answer") is not None:
            _require(sidecar["control_answer"] in execution["artifacts"], "Explicit control answer must be retained")
    _require(
        native_evidence.binding(source_sidecar) in execution["artifacts"], "Original mini sidecar must be retained"
    )
    return execution


def run(
    *,
    manifest_path,
    case_id,
    source_sidecar,
    output,
    native_source,
    native_output,
    command,
    conversion_path=None,
    supplemental_result=None,
    timeout=1800,
):
    manifest = evidence.manifest_at(manifest_path)
    manifest_binding = native_evidence.binding(manifest_path)
    descriptor = evidence.read(native_source)
    descriptor_binding = native_evidence.binding(native_source)
    case = manifest["contract"]["cases"].get(case_id)
    _require(
        manifest["contract"]["provider"] == "native" and case is not None and bool(command),
        "Native manifest, known case and actual command required",
    )
    _require(type(timeout) is int and timeout > 0, "Positive command timeout required")
    _require(Path(descriptor["execution"]).resolve() == output.resolve(), "Execution output differs from descriptor")
    task_dir = Path(manifest["task_dir"]).resolve()
    declared = native_evidence.source_paths(descriptor)
    for path in declared:
        _require(
            path.is_absolute()
            and not path.exists()
            and not path.is_symlink()
            and not path.resolve().is_relative_to(task_dir),
            "Declared native outputs must be fresh and outside the task",
        )
    _require(
        not native_output.exists() and not native_output.is_symlink(), "Mini requires a fresh native output directory"
    )
    _require(source_sidecar.resolve().is_relative_to(native_output.resolve()), "Sidecar must be inside native output")
    artifacts = {Path(value).resolve() for value in descriptor["artifacts"]}
    _require(source_sidecar.resolve() in artifacts, "Original sidecar must be predeclared")
    if supplemental_result is not None:
        _require(
            supplemental_result.resolve() in artifacts, "Supplemental report must be predeclared as a fresh raw output"
        )
    if conversion_path is not None:
        _require(
            conversion_path.resolve() in artifacts
            and not conversion_path.resolve().is_relative_to(native_output.resolve()),
            "Normalized conversion must be predeclared outside native output",
        )
    _require(
        output.resolve().is_relative_to(native_output.resolve()) is False, "Envelope must stay outside native output"
    )
    raw = artifacts - ({conversion_path.resolve()} if conversion_path is not None else set())
    _require(
        all(path.is_relative_to(native_output.resolve()) for path in raw),
        "Raw mini artifacts must be inside native output",
    )
    _registered(writer_bindings(), manifest["native_inputs"]["runtime_sources"])
    completed = subprocess.run(
        command, timeout=timeout, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    )
    _require(completed.returncode == 0, "Native mini command failed; retain raw outputs and outer attempt receipt")
    _require(
        evidence.manifest_at(manifest_path) == manifest and native_evidence.binding(manifest_path) == manifest_binding,
        "Prepared manifest changed during native execution",
    )
    _require(native_evidence.binding(native_source) == descriptor_binding, "Native descriptor changed during execution")
    actual = set()
    for path in native_output.rglob("*"):
        _require(not path.is_symlink(), "Native output must not contain symlinks")
        if path.is_file():
            actual.add(path.resolve())
    _require(actual == raw, "Native produced files differ from all predeclared raw outputs")
    execution = normalize(
        manifest=manifest,
        case=case,
        source_sidecar=source_sidecar,
        descriptor=descriptor,
        conversion_path=conversion_path,
        command=command,
        supplemental_result=supplemental_result,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output, json_bytes(execution))
    trace = Path(execution["normalized_trace"]["path"]) if case["kind"] == "agent" else None
    try:
        native_evidence.build(descriptor, manifest, case, trace)
    except ValueError as error:
        execution.update(status="technical_failure", evidence_error=str(error))
        atomic_write(output, json_bytes(execution))
        raise
    return execution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--source-sidecar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-source", type=Path, required=True)
    parser.add_argument("--native-output", type=Path, required=True)
    parser.add_argument("--conversion", type=Path)
    parser.add_argument("--supplemental-result", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    run(
        manifest_path=args.manifest,
        case_id=args.case,
        source_sidecar=args.source_sidecar,
        output=args.output,
        native_source=args.native_source,
        native_output=args.native_output,
        command=args.command,
        conversion_path=args.conversion,
        supplemental_result=args.supplemental_result,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
