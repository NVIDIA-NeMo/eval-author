# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise real pinned loaders and the private file boundary with synthetic traces."""

import base64
import copy
import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "gym": ROOT / "skills/gym-to-atif/scripts/load_gym_trace.py",
    "mlflow": ROOT / "skills/mlflow-to-atif/scripts/load_mlflow_trace.py",
}


def module(provider):
    path = SCRIPTS[provider]
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"load_{provider}_test", path)
        assert spec is not None and spec.loader is not None
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result
    finally:
        sys.path.pop(0)


def gym_record():
    return {
        "reward": 0,
        "ng_trajectory": {
            "schema_version": "1.0",
            "rollout_id": "rollout-1",
            "task_id": "task-1",
            "gaps": [{"code": "fixture_gap"}],
            "invocations": [
                {
                    "invocation_id": "agent",
                    "status": "completed",
                    "model_calls": [{"model_call_id": "m1"}],
                    "conversation": [
                        {"role": "user", "content": "Find the fixture."},
                        {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"id":1}'},
                        {"type": "function_call_output", "call_id": "c1", "output": None},
                        {"type": "function_call", "call_id": "c2", "name": "lookup", "arguments": '{"id":2}'},
                    ],
                }
            ],
            "model_calls": [
                {
                    "model_call_id": "m1",
                    "started_at": 100.0,
                    "completed_at": 101.0,
                    "request": {"input": "Find the fixture."},
                    "response": {"output": []},
                    "response_metadata": {"model": "fixture-model"},
                    "token_stats": {"prompt_tokens": 20, "cached_tokens": 5, "completion_tokens": 3},
                }
            ],
        },
    }


def mlflow_record():
    record = json.loads((ROOT / "skills/mlflow-to-atif/evals/files/valid.json").read_text())
    record["info"].update(
        {
            "trace_location": {"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": "1"}},
            "request_time": "2026-09-01T00:00:00Z",
            "state": "OK",
        }
    )
    root, child = record["data"]["spans"]
    for index, span in enumerate((root, child), 1):
        span["trace_id"] = base64.b64encode(bytes(16)).decode()
        span["span_id"] = base64.b64encode(index.to_bytes(8, "big")).decode()
        span["attributes"]["mlflow.traceRequestId"] = json.dumps(record["info"]["trace_id"])
    child["parent_span_id"] = root["span_id"]
    return record


def invoke(provider, source, output, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPTS[provider]), "--input", str(source), "--output-dir", str(output), *extra],
        capture_output=True,
        text=True,
    )


def test_gym_selected_record_retains_capture_and_missing_results(tmp_path):
    record = gym_record()
    raw = (json.dumps(record) + "\n").encode()
    source = tmp_path / "rollouts.jsonl"
    source.write_bytes(b'{"unrelated":"private"}\n' + raw)
    output = tmp_path / "loaded"
    receipt = module("gym").load(source, output, 2)
    trace = json.loads((output / "trace.normalized.json").read_text())
    assert (output / "source.gym.json").read_bytes() == raw
    assert receipt["selected_line"] == 2
    assert trace["attributes"]["gym"] == record
    assert trace["attributes"]["gym_gap_count"] == 1
    assert trace["evaluator_results"] == {"gym.reward": 0}
    spans = trace["root_spans"][0]["children"]
    llm = next(span for span in spans if span["kind"] == "LLM")
    assert llm["token_counts"] == {"input_tokens": 15, "cached_input_tokens": 5, "output_tokens": 3}
    assert llm["start_time"] and llm["end_time"]
    calls = {span["tool_call"]["call_id"]: span for span in spans if span["kind"] == "TOOL"}
    assert calls["c1"]["output"] is None
    assert calls["c1"]["tool_call"]["result_count"] == 1
    assert "output" not in calls["c2"]
    assert calls["c2"]["tool_call"]["result_count"] == 0
    assert receipt["atif_emitted"] is False
    assert not list(output.glob("*.atif.json"))


@pytest.mark.parametrize("provider", ["gym", "mlflow"])
def test_private_outputs_provenance_and_no_overwrite(provider, tmp_path):
    source = tmp_path / "input.json"
    raw = json.dumps(gym_record() if provider == "gym" else mlflow_record()).encode()
    source.write_bytes(raw)
    output = tmp_path / "loaded"
    args = () if provider == "gym" else ("--max-traces", "2")
    result = invoke(provider, source, output, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["atif_emitted"] is False
    assert result.stderr == ""
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    receipt = json.loads((output / "loading.json").read_text())
    from importlib.metadata import distribution

    installed = distribution("nemo-compass")
    assert receipt["loader_distribution"] == "nemo-compass"
    assert receipt["loader_version"] == installed.version
    assert receipt["loader_revision"] is None
    assert receipt["source_sha256"] == hashlib.sha256(raw).hexdigest()
    normalized = next(output.glob("*.normalized.json"))
    assert receipt["normalized_sha256"] == hashlib.sha256(normalized.read_bytes()).hexdigest()
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert invoke(provider, source, output, *args).returncode == 2
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


@pytest.mark.parametrize("mutation", ["continuation", "limit", "parent", "duplicate"])
def test_mlflow_rejects_incomplete_or_ambiguous_exports(mutation, tmp_path):
    trace = mlflow_record()
    payload = {"traces": [trace]}
    if mutation == "continuation":
        payload["next_page_token"] = "private-token"
    elif mutation == "limit":
        second = copy.deepcopy(trace)
        second["info"]["trace_id"] = "tr-second"
        for span in second["data"]["spans"]:
            span["trace_id"] = base64.b64encode(bytes([1]) * 16).decode()
            span["attributes"]["mlflow.traceRequestId"] = json.dumps("tr-second")
        payload["traces"].append(second)
    elif mutation == "parent":
        trace["data"]["spans"][1]["parent_span_id"] = base64.b64encode(bytes([2]) * 8).decode()
    else:
        payload["traces"].append(trace)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload))
    output = tmp_path / "loaded"
    result = invoke("mlflow", source, output, "--max-traces", "1" if mutation == "limit" else "10")
    assert result.returncode == 2
    assert not output.exists()
    assert "private-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("provider", ["gym", "mlflow"])
def test_errors_do_not_disclose_source_or_create_evidence(provider, tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"secret":"private-fixture-value"}')
    output = tmp_path / "loaded"
    result = invoke(provider, source, output, *(("--max-traces", "1") if provider == "mlflow" else ()))
    assert result.returncode == 2
    assert "private-fixture-value" not in result.stdout + result.stderr
    assert result.stderr == ""
    assert not output.exists()


def test_mlflow_normalization_retains_tree_and_source_pointer(tmp_path):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(mlflow_record()))
    output = tmp_path / "loaded"
    module("mlflow").load(source, output, 1)
    traces = json.loads((output / "traces.normalized.json").read_text())
    assert len(traces) == 1
    assert traces[0]["id"] == "tr-synthetic-ci"
    assert traces[0]["root_spans"][0]["children"][0]["kind"] == "RETRIEVER"
    assert str(output / "source.mlflow.json") in json.dumps(traces)
    assert "tmp" not in {p.name for p in output.iterdir()}


def test_gym_requires_explicit_jsonl_selection(tmp_path):
    source = tmp_path / "rollouts.jsonl"
    source.write_text(json.dumps(gym_record()) + "\n")
    output = tmp_path / "loaded"
    assert invoke("gym", source, output).returncode == 2
    assert not output.exists()


@pytest.mark.parametrize("provider", ["gym", "mlflow"])
@pytest.mark.parametrize("invalid_install", ["vcs", "version", "editable", "trace-ingest", "insight-agent"])
def test_unpinned_install_is_rejected_before_writing(provider, invalid_install, tmp_path, monkeypatch):
    from types import SimpleNamespace

    adapter = module(provider)
    provenance = {}
    if invalid_install == "vcs":
        provenance = {"vcs_info": {"commit_id": "other"}}
    elif invalid_install == "editable":
        provenance = {"dir_info": {"editable": True}}

    def distribution(name):
        if name in {"trace-ingest", "insight-agent"}:
            if invalid_install == name:
                return SimpleNamespace(version="0.1.0")
            raise adapter.importlib.metadata.PackageNotFoundError(name)
        assert name == "nemo-compass"
        return SimpleNamespace(
            version="0.0.0" if invalid_install == "version" else adapter.VERSION,
            read_text=lambda _: json.dumps(provenance),
        )

    monkeypatch.setattr(adapter.importlib.metadata, "distribution", distribution)
    output = tmp_path / "loaded"
    with pytest.raises(ValueError, match="nemo-compass"):
        adapter.load(tmp_path / "unused", output, 1)
    assert not output.exists()


@pytest.mark.parametrize("provider", ["gym", "mlflow"])
@pytest.mark.parametrize("provenance", [None, {"archive_info": {"hashes": {"sha256": "fixture"}}}])
def test_wheel_provenance_does_not_claim_a_git_revision(provider, provenance, tmp_path, monkeypatch):
    from types import SimpleNamespace

    adapter = module(provider)
    original_distribution = adapter.importlib.metadata.distribution

    def distribution(name):
        if name != "nemo-compass":
            return original_distribution(name)
        return SimpleNamespace(
            version=adapter.VERSION, read_text=lambda _: json.dumps(provenance) if provenance else None
        )

    monkeypatch.setattr(adapter.importlib.metadata, "distribution", distribution)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(gym_record() if provider == "gym" else mlflow_record()))
    receipt = adapter.load(source, tmp_path / "loaded", 1 if provider == "mlflow" else None)
    assert receipt["loader_distribution"] == "nemo-compass"
    assert receipt["loader_version"] == adapter.VERSION
    assert receipt["loader_revision"] is None
