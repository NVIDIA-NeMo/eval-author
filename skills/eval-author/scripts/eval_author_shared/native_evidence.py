# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bind an inspected native execution adapter to immutable task inputs and outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_evidence import EvidenceError, digest, read, require_binding

INPUTS = ("task", "config", "runtime", "reset")
SCHEMA = "nemo.eval_author.native_execution.v1"


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": digest(path)}


def checked(entry: dict[str, str]) -> Path:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        raise EvidenceError("native artifacts require path and sha256")
    path = Path(entry["path"])
    if not path.is_absolute():
        raise EvidenceError("native artifact paths must be absolute")
    require_binding(binding(path), entry, "native artifact changed")
    return path


def runtime_sources(path: Path) -> list[dict[str, str]]:
    files = read(path).get("files")
    if not isinstance(files, list) or not files:
        raise EvidenceError("native runtime manifest requires inspected source files")
    paths = [checked(entry) for entry in files]
    if len(set(paths)) != len(paths):
        raise EvidenceError("duplicate native runtime sources")
    return files


def snapshot(task: Path, contract: dict[str, Any]) -> dict[str, Any]:
    settings = contract.get("native")
    if not isinstance(settings, dict) or set(settings) != set(INPUTS):
        raise EvidenceError("native contract requires task, config, runtime and reset input paths")
    files = {}
    for name in INPUTS:
        relative = settings[name]
        if not isinstance(relative, str) or not relative:
            raise EvidenceError(f"native input path required: {name}")
        path = task / relative
        if not path.resolve().is_relative_to(task.resolve()):
            raise EvidenceError("native inputs must be inside the prepared task")
        files[name] = binding(path)
    return {"files": files, "runtime_sources": runtime_sources(Path(files["runtime"]["path"]))}


def source_paths(source: dict[str, Any]) -> list[Path]:
    if not isinstance(source, dict) or set(source) != {"execution", "artifacts"}:
        raise EvidenceError("native source requires execution and artifact output paths")
    artifacts = source["artifacts"]
    if not isinstance(artifacts, list) or not artifacts or any(not isinstance(p, str) or not p for p in artifacts):
        raise EvidenceError("native source must predeclare fresh artifact outputs")
    paths = [Path(source["execution"]), *(Path(p) for p in artifacts)]
    if len({path.resolve() for path in paths}) != len(paths):
        raise EvidenceError("native output paths must be distinct")
    return paths


def build(source: dict[str, Any], manifest: dict[str, Any], case: dict[str, Any], trace: Path | None) -> dict[str, Any]:
    path = source_paths(source)[0]
    execution = read(path)
    contract = manifest["contract"]
    expected = manifest["native_inputs"]
    require_binding(snapshot(Path(manifest["task_dir"]), contract), expected, "native prepared inputs changed")
    if execution.get("schema") != SCHEMA or execution.get("task_id") != contract["task_id"]:
        raise EvidenceError("native execution schema or task identity mismatch")
    identity = execution.get("run_id")
    if not isinstance(identity, str) or not identity.strip():
        raise EvidenceError("native execution requires a run identity")
    if execution.get("status") != "complete":
        raise EvidenceError("native execution did not complete")
    agent = case["kind"] == "agent"
    expected_class = "primary_unhinted" if agent else "reference_control"
    if execution.get("evidence_class") != expected_class:
        raise EvidenceError("hinted or wrong-kind execution cannot qualify as primary/control evidence")
    inputs = execution.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(INPUTS):
        raise EvidenceError("native execution must bind all executed inputs")
    artifacts = [binding(path)]
    for name in INPUTS:
        checked(inputs[name])
        require_binding(inputs[name]["sha256"], expected["files"][name]["sha256"], f"executed {name} differs")
        artifacts.append(inputs[name])
    native_path = checked(execution["result"])
    artifacts.append(execution["result"])
    retained = execution.get("artifacts")
    if not isinstance(retained, list) or not retained:
        raise EvidenceError("native execution must retain original runtime evidence")
    for entry in retained:
        checked(entry)
        artifacts.append(entry)
    if agent and trace is None:
        raise EvidenceError("native agent qualification requires an actual normalized trace")
    if trace is not None:
        require_binding(binding(trace), execution.get("normalized_trace"), "native normalized trace binding mismatch")
        from trace_loading import load_snapshot

        traces, _ = load_snapshot(trace, "trace")
        if len(traces) != 1 or not traces[0].root_spans:
            raise EvidenceError("native normalized trace requires exactly one observed run")
        normalized = traces[0]
        conversion_path = checked(execution["conversion"])
        conversion = read(conversion_path)
        require_binding(conversion.get("native_result"), execution["result"], "Trace native source mismatch")
        require_binding(conversion.get("task_sha256"), inputs["task"]["sha256"], "Trace task mismatch")
        require_binding(
            conversion.get("normalized_trace"), execution["normalized_trace"], "Trace conversion output mismatch"
        )
        if conversion.get("run_id") != identity or conversion.get("evidence_class") != expected_class:
            raise EvidenceError("Trace conversion identity or evidence class mismatch")
        if conversion.get("trace_id") != normalized.id:
            raise EvidenceError("native normalized trace identity mismatch")
        artifacts.extend([execution["normalized_trace"], execution["conversion"]])
    task = Path(manifest["task_dir"])
    outputs = [execution["result"], *retained] + (
        [execution["normalized_trace"], execution["conversion"]] if trace else []
    )
    if {str(Path(entry["path"]).resolve()) for entry in outputs} != {
        str(Path(p).resolve()) for p in source["artifacts"]
    }:
        raise EvidenceError("native outputs differ from predeclared fresh artifacts")
    for entry in outputs:
        if Path(entry["path"]).resolve().is_relative_to(task):
            raise EvidenceError("native outputs must remain outside the immutable task")
    return {
        "schema": "nemo.eval_author.native_evidence.v1",
        "native_run_id": identity,
        "inputs": expected,
        "execution": execution,
        "native": read(native_path),
        "artifacts": artifacts,
    }
