# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Native-shaped evidence boundaries; the optional Gym integration uses real outputs."""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("EVAL_AUTHOR_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
SCRIPTS = ROOT / "skills/eval-author-task-create/scripts"
sys.path.insert(0, str(SCRIPTS))
evidence = importlib.import_module("task_evidence")
gym = importlib.import_module("gym_evidence")


def dump(path, value):
    path.write_text(json.dumps(value))
    return path


def line(path, value):
    path.write_text(json.dumps(value) + "\n")
    return path


@pytest.fixture
def inputs(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    original = {
        "responses_create_params": {"input": [{"role": "user", "content": "Add the ledger."}]},
        "amounts": [1, 2],
    }
    line(task / "dataset", original)
    for name in ("config", "runtime", "reset"):
        dump(task / name, {"identity": name})
    dump(task / "fixtures.jsonl", {"name": "correct"})
    checks = [{"pointer": "/reward", "min": 1, "max": 1}]
    case = {
        "kind": "agent",
        "requirement": "Return the correct total.",
        "health": [{"pointer": "/health/ignored_checks", "equals": []}],
        "checks": checks,
    }
    contract = {
        "task_id": "cover-read",
        "provider": "gym",
        "native_run_id": "/native_run_id",
        "gym": {**{name: name for name in gym.INPUTS}, "dataset_row": 1},
        "cases": {
            "agent": case,
            "reference": {**case, "kind": "reference"},
            "incorrect": {**case, "kind": "incorrect"},
            "empty": {**case, "kind": "empty"},
        },
        "alternative_not_applicable": "Single numeric answer.",
        "side_effect_not_applicable": "Read only.",
    }
    manifest_path = tmp_path / "manifest.json"
    evidence.prepare(task, dump(tmp_path / "contract.json", contract), manifest_path)
    native_inputs = {}
    for name in gym.INPUTS:
        path = tmp_path / f"actual-{name}"
        path.write_bytes((task / name).read_bytes())
        native_inputs[name] = str(path)
    rollout = {
        **original,
        "reward": 1.0,
        "failure_reason": None,
        "_ng_task_index": 0,
        "_ng_rollout_index": 0,
        "ng_trajectory": {"rollout_id": "native-one"},
    }
    line(tmp_path / "rollouts.jsonl", rollout)
    line(
        tmp_path / "verdicts.jsonl",
        {"rollout_id": "native-one", "_ng_task_index": 0, "_ng_rollout_index": 0, "verdict": "healthy"},
    )
    dump(
        tmp_path / "summary.json",
        {
            "run": {
                "ignored_checks": [],
                "artifacts": {"records": 1},
                "verdicts": {"healthy": 1, "unhealthy": 0, "unobserved": 0},
            }
        },
    )
    source = {
        "mode": "rollout",
        "rollouts": str(tmp_path / "rollouts.jsonl"),
        "row": 1,
        "verdicts": str(tmp_path / "verdicts.jsonl"),
        "summary": str(tmp_path / "summary.json"),
        "inputs": native_inputs,
    }
    trace = line(tmp_path / "traces.normalized.jsonl", {"id": "native-one"})
    loading = dump(
        tmp_path / "loading.json",
        {
            "schema": "nemo.eval_author.trace_load.v2",
            "source_kind": "gym",
            "loader_distribution": gym.pinned_loader()[0],
            "loader_version": gym.pinned_loader()[1],
            "source_sha256": {
                "source.gym.jsonl": "sha256:" + hashlib.sha256((tmp_path / "rollouts.jsonl").read_bytes()).hexdigest()
            },
            "normalized_sha256": "sha256:" + evidence.digest(trace),
            "trace_count": 1,
        },
    )
    source["loading"] = str(loading)
    return manifest_path, case, source, trace


def test_native_rollout_binds_input_health_and_trace_loading(inputs):
    manifest, case, source, trace = inputs
    result = gym.build(source, evidence.read(manifest), case, "closure", trace)
    assert result["native_run_id"] == "native-one"
    assert result["reward"] == 1 and result["health"]["verdict"] == "healthy"
    assert result["deployment_identity"] == "not_attested"


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("row", "selected dataset row"),
        ("config", "config differs"),
        ("runtime", "runtime differs"),
        ("reset", "reset differs"),
        ("health", "unhealthy"),
        ("missing_health", "missing or duplicate"),
        ("disabled", "disabled"),
        ("loading_source", "does not bind"),
        ("loading_version", "does not bind"),
        ("loading_schema_v1", "does not bind"),
        ("trace", "does not bind"),
        ("trace_identity", "does not identify the selected rollout"),
        ("duplicate_health", "missing or duplicate"),
        ("null_trajectory", "missing native Gym rollout ID"),
        ("summary_shape", "missing or disabled"),
        ("artifacts_shape", "does not match retained rollouts"),
    ],
)
def test_rejects_wrong_native_evidence(inputs, mutation, message):
    manifest, case, source, trace = inputs
    if mutation in ("config", "runtime", "reset"):
        Path(source["inputs"][mutation]).write_text("{}")
    elif mutation == "row":
        native = gym.jsonl(Path(source["rollouts"]))[0][1]
        native["amounts"] = [9]
        line(Path(source["rollouts"]), native)
    elif mutation == "null_trajectory":
        native = gym.jsonl(Path(source["rollouts"]))[0][1]
        line(Path(source["rollouts"]), {**native, "ng_trajectory": None})
    elif mutation in ("loading_source", "loading_version", "loading_schema_v1"):
        path = Path(source["loading"])
        value = evidence.read(path)
        if mutation == "loading_schema_v1":
            value["schema"] = "nemo.eval_author.trace_load.v1"
        elif mutation == "loading_version":
            value["loader_version"] = "0.0.0"
        else:
            value["source_sha256"] = {"source.gym.jsonl": "sha256:" + "0" * 64}
        dump(path, value)
    elif mutation == "trace":
        trace.write_text("{}")
    elif mutation == "trace_identity":
        line(trace, {"id": "native-other"})
        value = evidence.read(Path(source["loading"]))
        value["normalized_sha256"] = "sha256:" + evidence.digest(trace)
        dump(Path(source["loading"]), value)
    elif mutation in ("disabled", "summary_shape", "artifacts_shape"):
        path = Path(source["summary"])
        value = evidence.read(path)
        if mutation == "disabled":
            value["run"]["ignored_checks"] = ["some-check"]
        elif mutation == "summary_shape":
            value["run"] = []
        else:
            value["run"]["artifacts"] = []
        dump(path, value)
    else:
        path = Path(source["verdicts"])
        value = gym.jsonl(path)[0][1]
        if mutation == "missing_health":
            value["rollout_id"] = "wrong"
        elif mutation == "health":
            value["verdict"] = "unhealthy"
            summary = evidence.read(Path(source["summary"]))
            summary["run"]["verdicts"] = {"healthy": 0, "unhealthy": 1, "unobserved": 0}
            dump(Path(source["summary"]), summary)
        line(path, value)
        if mutation == "duplicate_health":
            path.write_text(path.read_text() * 2)
    with pytest.raises(evidence.EvidenceError, match=message):
        gym.build(source, evidence.read(manifest), case, "closure", trace)


def test_baseline_keeps_unobserved_health_without_trace(inputs):
    manifest, case, source, _ = inputs
    verdict = gym.jsonl(Path(source["verdicts"]))[0][1]
    verdict["verdict"] = "unobserved"
    line(Path(source["verdicts"]), verdict)
    summary = evidence.read(Path(source["summary"]))
    summary["run"]["verdicts"] = {"healthy": 0, "unhealthy": 0, "unobserved": 1}
    dump(Path(source["summary"]), summary)
    source.pop("loading")
    result = gym.build(source, evidence.read(manifest), case, "baseline", None)
    assert result["health"]["verdict"] == "unobserved"
    with pytest.raises(evidence.EvidenceError, match="unobserved Gym health"):
        gym.build(source, evidence.read(manifest), case, "closure", None)


def test_fixture_cases_have_no_rollout_or_trace_requirement(inputs, tmp_path):
    manifest, case, source, _ = inputs
    fixture = Path(evidence.read(manifest)["task_dir"]) / "fixtures.jsonl"
    report = dump(
        tmp_path / "fixture-report.json",
        {
            "fixture_path": str(fixture),
            "cases": [
                {"name": "correct", "kind": "full_reward", "observed_rewards": [1.0]},
                {"name": "bad-input", "kind": "malformed", "observed_rewards": []},
            ],
        },
    )
    source = {"mode": "fixture", "report": str(report), "inputs": source["inputs"]}
    case = {**case, "kind": "reference", "gym_case": "correct", "gym_kind": "full_reward"}
    result = gym.build(source, evidence.read(manifest), case, "closure", None)
    assert result["native_run_id"].startswith("fixture:")
    assert result["reward"] == 1
    case.update(gym_case="bad-input", gym_kind="malformed")
    result = gym.build(source, evidence.read(manifest), case, "closure", None)
    assert "reward" not in result and result["case"]["kind"] == "malformed"


def stage_native_outputs(source):
    # Stage native artifacts as command inputs, then produce fresh outputs during recording.
    copies = []
    for path in gym.source_paths(source):
        saved = path.with_suffix(path.suffix + ".saved")
        path.rename(saved)
        copies.append((saved, path))
    code = "from pathlib import Path\n"
    for src, dest in copies:
        code += f"Path({str(dest)!r}).write_bytes(Path({str(src)!r}).read_bytes())\n"
    return code


@pytest.mark.parametrize("via_cli", [False, True])
def test_recorder_baseline_can_run_without_trace(tmp_path, inputs, via_cli):
    manifest, case, source, _ = inputs
    source.pop("loading")
    code = stage_native_outputs(source)
    source_path = dump(tmp_path / "source.json", source)
    output = tmp_path / "receipt.json"
    if via_cli:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "task_evidence.py"),
                "run",
                "--manifest",
                str(manifest),
                "--case",
                "agent",
                "--run-id",
                "baseline",
                "--out",
                str(output),
                "--result",
                str(tmp_path / "normalized.json"),
                "--purpose",
                "baseline",
                "--gym-source",
                str(source_path),
                "--",
                sys.executable,
                "-c",
                code,
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        receipt = evidence.read(output)
    else:
        receipt = evidence.run(
            manifest,
            "agent",
            "baseline",
            output,
            tmp_path / "normalized.json",
            None,
            [sys.executable, "-c", code],
            purpose="baseline",
            gym_source=source_path,
        )
    assert receipt["status"] == "passed" and receipt["trace"] is None
    with pytest.raises(evidence.EvidenceError, match="baseline receipts"):
        evidence.verify_receipts([output], "cover-read")


def test_malformed_native_output_still_writes_a_receipt(tmp_path, inputs):
    manifest, _, source, _ = inputs
    source.pop("loading")
    native = gym.jsonl(Path(source["rollouts"]))[0][1]
    line(Path(source["rollouts"]), {**native, "ng_trajectory": None})
    code = stage_native_outputs(source)
    output = tmp_path / "receipt.json"
    evidence.run(
        manifest,
        "agent",
        "malformed",
        output,
        tmp_path / "normalized.json",
        None,
        [sys.executable, "-c", code],
        purpose="baseline",
        gym_source=dump(tmp_path / "source.json", source),
    )
    receipt = evidence.read(output)
    assert receipt["status"] == "infrastructure_error" and "rollout ID" in receipt["error"]


def check_native_artifacts(output, draft):
    """Called by the real Gym integration after native HTTP/CLI execution."""
    inputs = {
        "dataset": output / "executed-dataset.jsonl",
        "config": output / "executed-config.yaml",
        "runtime": output / "executed-runtime.json",
        "reset": output / "executed-reset.json",
    }
    manifest = {
        "task_dir": str(draft.resolve()),
        "gym_inputs": {
            "files": {key: {"path": str(path), "sha256": evidence.digest(path)} for key, path in inputs.items()},
            "dataset_row": 1,
        },
        "contract": {"provider": "gym"},
    }
    source = {
        "mode": "rollout",
        "inputs": {k: str(p) for k, p in inputs.items()},
        "rollouts": str(output / "cli-rollouts.jsonl"),
        "row": 1,
        "verdicts": str(output / "rollout_verdicts.jsonl"),
        "summary": str(output / "quality_summary.json"),
    }
    source["row"] = next(
        i for i, (_, r) in enumerate(gym.jsonl(Path(source["rollouts"])), 1) if r["_ng_task_index"] == 0
    )
    result = gym.build(source, manifest, {"kind": "agent"}, "baseline", None)
    assert result["reward"] == 1 and result["health"]["verdict"] == "unobserved"
    with pytest.raises(evidence.EvidenceError, match="unobserved Gym health"):
        gym.build(source, manifest, {"kind": "agent"}, "closure", None)
    report = evidence.read(output / "verifier-report.json")
    reference = next(c for c in report["cases"] if c["kind"] == "full_reward")
    fixture = gym.build(
        {"mode": "fixture", "report": str(output / "verifier-report.json"), "inputs": source["inputs"]},
        manifest,
        {"kind": "reference", "gym_case": reference["name"], "gym_kind": reference["kind"]},
        "closure",
        None,
    )
    assert fixture["reward"] == 1 and fixture["native_run_id"].startswith("fixture:")
    # Load this exact native row with eval-author's loader and bind its real receipt.
    loaded = output / "evidence-loaded"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS.parents[1] / "eval-author/scripts/trace_loading.py"),
            "--source-kind",
            "gym",
            "--input",
            source["rollouts"],
            "--row",
            str(source["row"]),
            "--output-dir",
            str(loaded),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    source["loading"] = str(loaded / "loading.json")
    gym.build(source, manifest, {"kind": "agent"}, "baseline", loaded / "traces.normalized.jsonl")
