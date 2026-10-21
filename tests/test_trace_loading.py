# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise eval-author's shared NeMo Compass loading boundary with small synthetic traces."""

import copy
import hashlib
import importlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author/scripts/trace_loading.py"
ATIF_FIXTURE = ROOT / "skills/eval-author-trace-environment/evals/files/traces/lookup.atif.json"
MLFLOW_FIXTURE = ROOT / "skills/eval-author-trace-environment/evals/files/traces/mlflow.json"


@pytest.fixture
def loading(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    return importlib.import_module("trace_loading")


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
    return json.loads(MLFLOW_FIXTURE.read_text())


def intake_files(directory: Path) -> tuple[Path, Path]:
    """Write CLI-shaped `nemo intake traces get` / `spans list` JSON for one trace."""
    common = {"session_id": "session-1", "workspace": "fixture-ws", "trace_id": "intake-1"}
    trace = {
        "id": "intake-1",
        "root_span_id": "s1",
        "session_id": "session-1",
        "workspace": "fixture-ws",
        "name": "run",
        "input": "Where is Paris?",
        "output": "France",
        "started_at": "2026-09-01T00:00:00Z",
        "ended_at": "2026-09-01T00:00:03Z",
        "status": "success",
    }
    spans = [
        {
            **common,
            "span_id": "s1",
            "kind": "agent",
            "name": "agent",
            "source": "sdk",
            "started_at": "2026-09-01T00:00:00Z",
            "ended_at": "2026-09-01T00:00:03Z",
            "status": "success",
            "input": "Where is Paris?",
            "output": "France",
        },
        {
            **common,
            "span_id": "s2",
            "parent_span_id": "s1",
            "kind": "tool",
            "name": "lookup",
            "tool_name": "lookup",
            "source": "sdk",
            "started_at": "2026-09-01T00:00:01Z",
            "ended_at": "2026-09-01T00:00:02Z",
            "status": "success",
            "input": '{"q": "Paris"}',
            "output": "Paris is in France.",
        },
    ]
    trace_path = directory / "intake-trace.json"
    spans_path = directory / "intake-spans.json"
    trace_path.write_text(json.dumps({"data": [trace]}))
    spans_path.write_text(json.dumps({"data": spans}))
    return trace_path, spans_path


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def sources(kind, directory: Path) -> list[str | Path]:
    """CLI arguments that load one trace of the given kind."""
    if kind == "atif":
        return ["--input", ATIF_FIXTURE]
    if kind == "mlflow":
        return ["--input", MLFLOW_FIXTURE]
    if kind == "gym":
        rollouts = directory / "rollouts.jsonl"
        rollouts.write_text('{"unrelated":"private"}\n' + json.dumps(gym_record()) + "\n")
        return ["--input", rollouts, "--row", "2"]
    trace, spans = intake_files(directory)
    return ["--input", trace, "--spans", spans, "--workspace", "fixture-ws"]


def normalized(output: Path) -> list[dict]:
    return [json.loads(line) for line in (output / "traces.normalized.jsonl").read_text().splitlines()]


@pytest.mark.parametrize(
    ("kind", "trace_id"),
    [("atif", "synthetic-lookup-fixture"), ("mlflow", "tr-synthetic-ci"), ("gym", "rollout-1"), ("intake", "intake-1")],
)
def test_each_kind_loads_with_private_outputs_and_receipt_digests(kind, trace_id, tmp_path):
    output = tmp_path / "loaded"
    args = sources(kind, tmp_path)
    result = invoke("--source-kind", kind, *args, "--output-dir", output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "loaded", "traces": 1}
    assert result.stderr == ""
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    [trace] = normalized(output)
    assert trace["id"] == trace_id
    assert trace["schema_version"] == "2"

    receipt = json.loads((output / "loading.json").read_text())
    copies = {name: output / name for name in receipt["source_sha256"]}
    inputs = [Path(args[1])] + ([Path(args[3])] if kind == "intake" else [])
    assert [path.read_bytes() for path in copies.values()] == [path.read_bytes() for path in inputs]
    for name, path in copies.items():
        assert receipt["source_sha256"][name] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    expected_names = (
        ["source.intake-trace.json", "source.intake-spans.json"]
        if kind == "intake"
        else [f"source.{kind}{Path(args[1]).suffix}"]
    )
    assert list(copies) == expected_names
    digest = hashlib.sha256((output / "traces.normalized.jsonl").read_bytes()).hexdigest()
    assert receipt == {
        "schema": "nemo.eval_author.trace_load.v2",
        "source_kind": kind,
        "loader_distribution": "nemo-compass",
        "loader_version": "0.2.0",
        "trace_schema_version": "2",
        "source_sha256": receipt["source_sha256"],
        "normalized_sha256": "sha256:" + digest,
        "trace_count": 1,
        "max_traces": 1000,
    }


def test_gym_row_selects_one_record_and_keeps_missing_results(tmp_path):
    output = tmp_path / "loaded"
    assert invoke("--source-kind", "gym", *sources("gym", tmp_path), "--output-dir", output).returncode == 0
    [trace] = normalized(output)
    assert trace["attributes"]["gym"] == gym_record()
    assert trace["evaluator_results"] == {"gym.reward": 0}
    spans = trace["root_spans"][0]["children"]
    llm = next(span for span in spans if span["kind"] == "LLM")
    assert llm["token_counts"] == {"input_tokens": 15, "cached_input_tokens": 5, "output_tokens": 3}
    calls = {span["tool_call"]["call_id"]: span["tool_call"] for span in spans if span["kind"] == "TOOL"}
    assert calls["c1"]["result_count"] == len(calls["c1"]["results"]) == 1
    assert calls["c2"]["result_count"] == len(calls["c2"]["results"]) == 0


def test_normalized_output_reloads_as_trace_kind(tmp_path):
    first = tmp_path / "first"
    assert invoke("--source-kind", "mlflow", "--input", MLFLOW_FIXTURE, "--output-dir", first).returncode == 0
    second = tmp_path / "second"
    result = invoke("--source-kind", "trace", "--input", first / "traces.normalized.jsonl", "--output-dir", second)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (second / "source.trace.jsonl").read_bytes() == (first / "traces.normalized.jsonl").read_bytes()
    # The FS loader points source_url at the reloaded file; the trace content is otherwise unchanged.
    assert [{**trace, "source_url": None} for trace in normalized(second)] == [
        {**trace, "source_url": None} for trace in normalized(first)
    ]


def test_existing_output_directory_is_never_modified(tmp_path):
    output = tmp_path / "loaded"
    assert invoke("--source-kind", "atif", "--input", ATIF_FIXTURE, "--output-dir", output).returncode == 0
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    result = invoke("--source-kind", "atif", "--input", ATIF_FIXTURE, "--output-dir", output)
    assert result.returncode == 2
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


@pytest.mark.parametrize("kind", ["atif", "mlflow", "gym", "intake", "trace"])
def test_errors_are_content_free_and_leave_no_evidence(kind, tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"secret":"private-fixture-value"}')
    extra = ["--spans", source, "--workspace", "ws"] if kind == "intake" else []
    output = tmp_path / "loaded"
    result = invoke("--source-kind", kind, "--input", source, *extra, "--output-dir", output)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "error",
        "message": f"{kind} loading failed; check input and pinned dependencies",
    }
    assert "private-fixture-value" not in result.stdout + result.stderr
    assert result.stderr == ""
    assert not output.exists()


def test_intake_requires_spans(tmp_path):
    trace, _ = intake_files(tmp_path)
    output = tmp_path / "loaded"
    result = invoke("--source-kind", "intake", "--input", trace, "--workspace", "fixture-ws", "--output-dir", output)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "error",
        "message": "intake loading failed; check input and pinned dependencies",
    }
    assert "Paris" not in result.stdout + result.stderr
    assert not output.exists()


def test_gym_jsonl_requires_explicit_row(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    rollouts.write_text(json.dumps(gym_record()) + "\n")
    output = tmp_path / "loaded"
    assert invoke("--source-kind", "gym", "--input", rollouts, "--output-dir", output).returncode == 2
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["continuation", "parent", "duplicate"])
def test_mlflow_rejects_incomplete_or_ambiguous_exports(mutation, tmp_path):
    trace = mlflow_record()
    payload = {"traces": [trace]}
    if mutation == "continuation":
        payload["next_page_token"] = "private-token"
    elif mutation == "parent":
        trace["data"]["spans"][1]["parent_span_id"] = "AAAAAAAAAAk="
    else:
        payload["traces"].append(copy.deepcopy(trace))
    source = tmp_path / "input.json"
    source.write_text(json.dumps(payload))
    output = tmp_path / "loaded"
    result = invoke("--source-kind", "mlflow", "--input", source, "--output-dir", output)
    assert result.returncode == 2
    assert "private-token" not in result.stdout + result.stderr
    assert not output.exists()


def test_more_than_max_traces_is_rejected_not_truncated(loading, tmp_path):
    second = mlflow_record()
    second["info"]["trace_id"] = "tr-second"
    for span in second["data"]["spans"]:
        span["trace_id"] = "AQEBAQEBAQEBAQEBAQEBAQ=="
        span["attributes"]["mlflow.traceRequestId"] = json.dumps("tr-second")
    mlflow = tmp_path / "two.json"
    mlflow.write_text(json.dumps({"traces": [mlflow_record(), second]}))
    assert {trace.id for trace in loading.load_traces(mlflow, "mlflow", max_traces=2)} == {
        "tr-synthetic-ci",
        "tr-second",
    }
    with pytest.raises(loading.TraceLoadError, match="more than max_traces"):
        loading.load_traces(mlflow, "mlflow", max_traces=1)

    atif = tmp_path / "two.jsonl"
    record = json.loads(ATIF_FIXTURE.read_text())
    atif.write_text("".join(json.dumps({**record, "session_id": name}) + "\n" for name in ("one", "two")))
    with pytest.raises(loading.TraceLoadError, match="more than max_traces"):
        loading.load_traces(atif, "atif", max_traces=1)
    output = tmp_path / "loaded"
    assert invoke("--source-kind", "atif", "--input", atif, "--max-traces", "1", "--output-dir", output).returncode == 2
    assert not output.exists()


@pytest.mark.parametrize(
    ("installed", "message"),
    [
        ({}, "install nemo-compass==0.2.0"),
        ({"nemo-compass": ("0.1.0", {})}, "install nemo-compass==0.2.0"),
        (
            {"nemo-compass": ("0.2.0", {"vcs_info": {"commit_id": "other"}})},
            "install nemo-compass==0.2.0",
        ),
        (
            {"nemo-compass": ("0.2.0", {"dir_info": {"editable": True}})},
            "install nemo-compass==0.2.0",
        ),
        (
            {"nemo-compass": ("0.2.0", {}), "trace-ingest": ("0.1.0", {})},
            "remove trace-ingest and reinstall nemo-compass==0.2.0",
        ),
        (
            {"nemo-compass": ("0.2.0", {}), "insight-agent": ("0.1.0", {})},
            "remove insight-agent and reinstall nemo-compass==0.2.0",
        ),
    ],
    ids=["missing", "version", "vcs", "editable", "trace-ingest", "insight-agent"],
)
def test_unpinned_or_shadowing_install_is_rejected_before_writing(loading, installed, message, tmp_path, monkeypatch):
    metadata = loading.importlib.metadata

    def distribution(name):
        if name not in installed:
            raise metadata.PackageNotFoundError(name)
        version, provenance = installed[name]
        return SimpleNamespace(version=version, read_text=lambda _: json.dumps(provenance) if provenance else None)

    monkeypatch.setattr(metadata, "distribution", distribution)
    with pytest.raises(loading.TraceLoadError, match=f"^{message}$"):
        loading.check_installation()
    output = tmp_path / "loaded"
    assert loading.main(["--source-kind", "atif", "--input", str(ATIF_FIXTURE), "--output-dir", str(output)]) == 2
    assert not output.exists()


def test_release_wheel_install_is_accepted(loading, monkeypatch):
    metadata = loading.importlib.metadata
    original = metadata.distribution

    def distribution(name):
        if name != "nemo-compass":
            return original(name)
        provenance = {"archive_info": {"hashes": {"sha256": "fixture"}}}
        return SimpleNamespace(version="0.2.0", read_text=lambda _: json.dumps(provenance))

    monkeypatch.setattr(metadata, "distribution", distribution)
    loading.check_installation()
