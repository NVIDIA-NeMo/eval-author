# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read native Gym verifier reports and rollout/health artifacts without Gym imports."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any

from task_evidence import EvidenceError, digest, loads, read

INPUTS = ("dataset", "config", "runtime", "reset")
TRACE_LOAD_SCHEMA = "nemo.eval_author.trace_load.v2"
_TRACE_LOADING = Path(__file__).resolve().parents[2] / "eval-author/scripts/trace_loading.py"


def jsonl(path: Path) -> list[tuple[bytes, dict[str, Any]]]:
    digest(path)
    rows = []
    for raw in path.read_bytes().splitlines(keepends=True):
        if not raw.strip():
            raise EvidenceError("blank JSONL rows are not supported in evidence")
        # Use the same strict decoder as ordinary evidence, without temporary files.
        try:
            value = loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise EvidenceError("invalid native JSONL") from exc
        if not isinstance(value, dict):
            raise EvidenceError("native JSONL rows must be objects")
        rows.append((raw, value))
    if not rows:
        raise EvidenceError("empty native JSONL")
    return rows


def pinned_loader() -> tuple[str, str]:
    """Return the sibling eval-author loader's pinned Compass distribution and version."""
    spec = importlib.util.spec_from_file_location("_task_create_trace_loading", _TRACE_LOADING)
    if spec is None or spec.loader is None or not _TRACE_LOADING.is_file():
        raise EvidenceError("install the sibling eval-author skill to bind Gym trace evidence")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.COMPASS_DISTRIBUTION, module.COMPASS_VERSION


def snapshot(task: Path, contract: dict[str, Any]) -> dict[str, Any]:
    settings = contract.get("gym")
    if not isinstance(settings, dict):
        raise EvidenceError("Gym contract requires dataset, config, runtime and reset input paths")
    paths = {}
    for name in INPUTS:
        relative = settings.get(name)
        if not isinstance(relative, str) or not relative:
            raise EvidenceError(f"Gym contract requires {name}")
        path = task / relative
        if not path.resolve().is_relative_to(task.resolve()):
            raise EvidenceError("Gym inputs must be inside the prepared task")
        paths[name] = {"path": str(path.resolve()), "sha256": digest(path)}
    row = settings.get("dataset_row")
    if type(row) is not int or not 1 <= row <= len(jsonl(Path(paths["dataset"]["path"]))):
        raise EvidenceError("Gym dataset_row must select a one-based dataset row")
    return {"files": paths, "dataset_row": row}


def source_paths(source: dict[str, Any]) -> list[Path]:
    mode = source.get("mode")
    names = ("report",) if mode == "fixture" else ("rollouts", "verdicts", "summary") if mode == "rollout" else ()
    if not names:
        raise EvidenceError("Gym source mode must be fixture or rollout")
    paths = [Path(source[name]) for name in names]
    inputs = source.get("inputs", {})
    paths.extend(Path(inputs[name]) for name in INPUTS)
    if source.get("loading"):
        paths.append(Path(source["loading"]))
    return paths


def contains(actual: Any, expected: Any) -> bool:
    """Gym/Pydantic adds defaults; preserve every explicitly supplied request value."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(a, e) for a, e in zip(actual, expected))
        )
    return type(actual) is type(expected) and actual == expected


def build(
    source: dict[str, Any], manifest: dict[str, Any], case: dict[str, Any], purpose: str, trace: Path | None
) -> dict[str, Any]:
    paths = source_paths(source)
    artifacts = [{"path": str(p.resolve()), "sha256": digest(p)} for p in paths]
    expected = manifest["gym_inputs"]
    for name in INPUTS:
        if digest(Path(source["inputs"][name])) != expected["files"][name]["sha256"]:
            raise EvidenceError(f"Gym execution {name} differs from prepared inputs")
    result: dict[str, Any] = {
        "schema": "nemo.eval_author.gym_evidence.v1",
        "mode": source["mode"],
        "artifacts": artifacts,
        "inputs": expected,
        "deployment_identity": "not_attested",
    }
    if source["mode"] == "fixture":
        if case["kind"] == "agent":
            raise EvidenceError("verifier fixtures cannot substitute for an agent rollout")
        report = read(Path(source["report"]))
        name = case.get("gym_case")
        native_kind = case.get("gym_kind")
        if not isinstance(name, str) or not name or not isinstance(native_kind, str):
            raise EvidenceError("fixture controls require gym_case and gym_kind in the contract")
        cases = report.get("cases")
        if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
            raise EvidenceError("missing native verifier cases")
        matches = [c for c in cases if c.get("name") == name]
        if len(matches) != 1 or matches[0].get("kind") != native_kind:
            raise EvidenceError("native verifier case identity mismatch")
        native = matches[0]
        rewards = native.get("observed_rewards")
        if not isinstance(rewards, list) or any(type(r) not in (int, float) or not math.isfinite(r) for r in rewards):
            raise EvidenceError("invalid native verifier observations")
        if not rewards and native_kind != "malformed":
            raise EvidenceError("verifier case is missing observations")
        fixture_path = Path(report.get("fixture_path", ""))
        if not fixture_path.resolve().is_relative_to(Path(manifest["task_dir"])):
            raise EvidenceError("native verifier fixture is outside the prepared task")
        digest(fixture_path)
        result.update(
            native_run_id=f"fixture:{digest(Path(source['report']))}:{name}",
            case=native,
            health={"verdict": "healthy", "ignored_checks": []},
        )
        if rewards:
            result["reward"] = rewards[0]
        return result
    rows = jsonl(Path(source["rollouts"]))
    index = source.get("row")
    if type(index) is not int or not 1 <= index <= len(rows):
        raise EvidenceError("Gym rollout row must be one-based and present")
    _, native = rows[index - 1]
    trajectory = native.get("ng_trajectory")
    identity = trajectory.get("rollout_id") if isinstance(trajectory, dict) else None
    if not isinstance(identity, str) or not identity:
        raise EvidenceError("missing native Gym rollout ID")
    if native.get("_ng_rollout_id", identity) != identity:
        raise EvidenceError("inconsistent native Gym rollout identities")
    selected = jsonl(Path(expected["files"]["dataset"]["path"]))[expected["dataset_row"] - 1][1]
    # Gym's serializer omits verifier-only row fields and inserts request defaults.
    # Bind those omitted fields through the captured dataset digest and native index.
    if native.get("_ng_task_index") != expected["dataset_row"] - 1 or any(
        not contains(native.get(key), value)
        for key, value in selected.items()
        if key in native or key == "responses_create_params"
    ):
        raise EvidenceError("native rollout does not match the selected dataset row")
    if "failure_reason" not in native or native["failure_reason"] is not None:
        raise EvidenceError("native Gym rollout failed or lacks execution status")
    reward = native.get("reward")
    if type(reward) not in (int, float) or not math.isfinite(reward):
        raise EvidenceError("missing or nonfinite Gym reward")
    verdicts = [r for _, r in jsonl(Path(source["verdicts"]))]
    matches = [r for r in verdicts if r.get("rollout_id") == identity]
    if len(matches) != 1:
        raise EvidenceError("missing or duplicate native rollout health record")
    health = matches[0]
    for name in ("_ng_task_index", "_ng_rollout_index"):
        if name not in native or health.get(name) != native[name]:
            raise EvidenceError("health record selects a different task or repeat")
    summary = read(Path(source["summary"])).get("run")
    if not isinstance(summary, dict) or summary.get("ignored_checks") != []:
        raise EvidenceError("Gym health checks are missing or disabled")
    artifacts = summary.get("artifacts")
    counts = {v: sum(r.get("verdict") == v for r in verdicts) for v in ("healthy", "unhealthy", "unobserved")}
    if (
        summary.get("verdicts") != counts
        or sum(counts.values()) != len(verdicts)
        or not isinstance(artifacts, dict)
        or artifacts.get("records") != len(rows)
        or len(verdicts) != len(rows)
    ):
        raise EvidenceError("Gym health summary does not match retained rollouts")
    if health.get("verdict") not in ("healthy", "unobserved"):
        raise EvidenceError("native Gym rollout is unhealthy")
    if purpose == "closure" and case["kind"] == "agent" and health["verdict"] != "healthy":
        raise EvidenceError("unobserved Gym health cannot establish measured gap closure")
    if trace is not None:
        # The recorded command loads the same rollout file and row with eval-author's
        # trace_loading.py; its receipt binds the source bytes to the normalized trace.
        if not source.get("loading"):
            raise EvidenceError("Gym trace evidence requires the trace_loading.py receipt as loading")
        loading = read(Path(source["loading"]))
        source_digests = loading.get("source_sha256")
        if (
            loading.get("schema") != TRACE_LOAD_SCHEMA
            or loading.get("source_kind") != "gym"
            or (loading.get("loader_distribution"), loading.get("loader_version")) != pinned_loader()
            or loading.get("trace_count") != 1
            or not isinstance(source_digests, dict)
            or list(source_digests.values()) != ["sha256:" + digest(Path(source["rollouts"]))]
            or loading.get("normalized_sha256") != "sha256:" + digest(trace)
        ):
            raise EvidenceError("Gym trace loading receipt does not bind the rollouts to the trace")
        traces = jsonl(trace)
        same_identity = [
            row
            for _, row in rows
            if isinstance(row.get("ng_trajectory"), dict) and row["ng_trajectory"].get("rollout_id") == identity
        ]
        if len(traces) != 1 or traces[0][1].get("id") != identity or len(same_identity) != 1:
            raise EvidenceError("normalized Gym trace does not identify the selected rollout")
    elif purpose == "closure" and case["kind"] == "agent":
        raise EvidenceError("Gym closure requires the normalized trace from trace_loading.py")
    result.update(native_run_id=identity, reward=reward, health={**health, "ignored_checks": []}, row=index)
    return result
