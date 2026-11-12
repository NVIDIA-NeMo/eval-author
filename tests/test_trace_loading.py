# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise eval-author's shared NeMo Compass loading boundary with small synthetic traces."""

import copy
import functools
import hashlib
import importlib
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author/scripts/trace_loading.py"
ATIF_FIXTURE = ROOT / "skills/eval-author-trace-environment/evals/files/traces/lookup.atif.json"
MLFLOW_FIXTURE = ROOT / "skills/eval-author-trace-environment/evals/files/traces/mlflow.json"
PIN = "nemo-compass[mlflow]==0.2.1"
INTAKE_ARGS = [
    "--intake-base-url",
    "https://intake.example.test",
    "--workspace",
    "fixture-ws",
    "--started-after",
    "2026-09-01T00:00:00+00:00",
    "--started-before",
    "2026-09-02T00:00:00+00:00",
]


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


def second_mlflow_record():
    """The fixture trace under another trace id, so an export can hold two distinct traces."""
    second = mlflow_record()
    second["info"]["trace_id"] = "tr-second"
    for span in second["data"]["spans"]:
        span["trace_id"] = "AQEBAQEBAQEBAQEBAQEBAQ=="
        span["attributes"]["mlflow.traceRequestId"] = json.dumps("tr-second")
    return second


def intake_page(records: list[dict]) -> dict:
    count = len(records)
    pagination = {"page": 1, "page_size": 100, "current_page_size": count, "total_pages": 1, "total_results": count}
    return {"data": records, "pagination": pagination}


def intake_records(session_id: str = "session-1") -> dict[str, list[dict]]:
    """One Intake trace with an agent root, a tool span, and one evaluator result."""
    common = {"session_id": session_id, "workspace": "fixture-ws", "trace_id": "intake-1"}
    trace = {
        "id": "intake-1",
        "root_span_id": "s1",
        "session_id": session_id,
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
    result = {
        "evaluator_result_id": "er-1",
        "span_id": "s1",
        "session_id": session_id,
        "workspace": "fixture-ws",
        "name": "correctness",
        "value": 1.0,
        "data_type": "NUMERIC",
        "created_at": "2026-09-01T00:00:04Z",
        "ingested_at": "2026-09-01T00:00:05Z",
    }
    return {"traces": [trace], "spans": spans, "evaluator-results": [result]}


@pytest.fixture
def intake(monkeypatch):
    """Serve Intake API pages from memory; returns the recorded requests."""
    from trace_ingest.loaders import intake as module

    records = intake_records()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=intake_page(records[endpoint]))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, "IntakeTraceLoader", functools.partial(module.IntakeTraceLoader, transport=transport))
    return SimpleNamespace(records=records, requests=requests)


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def sources(kind, directory: Path) -> list[str | Path]:
    """CLI arguments that load one trace of the given kind."""
    if kind == "atif":
        return ["--input", ATIF_FIXTURE]
    if kind == "mlflow":
        return ["--input", MLFLOW_FIXTURE]
    rollouts = directory / "rollouts.jsonl"
    rollouts.write_text('{"unrelated":"private"}\n' + json.dumps(gym_record()) + "\n")
    return ["--input", rollouts, "--row", "2"]


def normalized(output: Path) -> list[dict]:
    return [json.loads(line) for line in (output / "traces.normalized.jsonl").read_text().splitlines()]


def expected_receipt(output: Path, kind: str, **values) -> dict:
    digest = hashlib.sha256((output / "traces.normalized.jsonl").read_bytes()).hexdigest()
    return {
        "schema": "nemo.eval_author.trace_load.v2",
        "source_kind": kind,
        "loader": {"distribution": "nemo-compass", "version": "0.2.1"},
        "trace_schema_version": "3",
        "normalized_sha256": "sha256:" + digest,
        "trace_count": 1,
        "max_traces": 1000,
        "row": None,
        "trace_id": None,
        "intake": None,
        **values,
    }


@pytest.mark.parametrize(("kind", "trace_id"), [("mlflow", "tr-synthetic-ci"), ("gym", "rollout-1"), ("atif", None)])
def test_each_file_kind_loads_with_private_outputs_and_receipt_digests(kind, trace_id, tmp_path):
    output = tmp_path / "loaded"
    args = sources(kind, tmp_path)
    result = invoke("--source-kind", kind, *args, "--output-dir", output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"status": "loaded", "traces": 1}
    assert result.stderr == ""
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    [trace] = normalized(output)
    assert trace["schema_version"] == "3"
    if trace_id is not None:
        assert trace["id"] == trace_id

    receipt = json.loads((output / "loading.json").read_text())
    name = f"source.{kind}{Path(args[1]).suffix}"
    copy_bytes = (output / name).read_bytes()
    assert copy_bytes == Path(args[1]).read_bytes()
    assert receipt == expected_receipt(
        output,
        kind,
        source_sha256={name: "sha256:" + hashlib.sha256(copy_bytes).hexdigest()},
        row=2 if kind == "gym" else None,
    )


def test_atif_json_file_loads_as_one_trajectory_whatever_its_formatting(loading, tmp_path):
    record = json.loads(ATIF_FIXTURE.read_text())
    pretty = tmp_path / "pretty.json"
    pretty.write_text(json.dumps(record, indent=2))
    minified = tmp_path / "minified.json"
    minified.write_text(json.dumps(record, separators=(",", ":")))
    jsonl = tmp_path / "trajectory.jsonl"
    jsonl.write_text(json.dumps(record) + "\n")
    [from_pretty] = loading.load_traces(pretty, "atif")
    [from_minified] = loading.load_traces(minified, "atif")
    [from_jsonl] = loading.load_traces(jsonl, "atif")
    # The trace cites the input file, and whitespace does not change its identity.
    assert from_pretty.source_url == pretty.resolve().as_uri()
    assert from_pretty.model_dump(exclude={"source_url"}) == from_minified.model_dump(exclude={"source_url"})
    # Without a trajectory_id, Compass derives the id from the line bytes, so only that differs from JSONL.
    assert from_pretty.model_dump(exclude={"source_url", "id"}) == from_jsonl.model_dump(exclude={"source_url", "id"})
    assert from_pretty.session_id == record["session_id"]
    [turn, *_] = from_pretty.root_spans[0].turns
    assert turn.role.value == "user"


def test_intake_selection_loads_live_with_evaluator_results(loading, intake, tmp_path, capsys):
    output = tmp_path / "loaded"
    assert loading.main(["--source-kind", "intake", *INTAKE_ARGS, "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "loaded", "traces": 1}
    [trace] = normalized(output)
    assert trace["session_id"] == "session-1"
    # Records that share a session merge; each keeps its evaluator results under its own trace id.
    [correctness] = trace["evaluator_results"]["correctness"]
    assert correctness["trace_id"] == "intake-1" and correctness["value"]["value"] == 1.0
    assert sorted(path.name for path in output.iterdir()) == ["loading.json", "traces.normalized.jsonl"]
    receipt = json.loads((output / "loading.json").read_text())
    assert receipt == expected_receipt(
        output,
        "intake",
        source_sha256={},
        intake={
            "base_url": "https://intake.example.test",
            "workspace": "fixture-ws",
            "started_after": "2026-09-01T00:00:00+00:00",
            "started_before": "2026-09-02T00:00:00+00:00",
            "session_id": None,
        },
    )
    [traces_request, *_] = intake.requests
    assert traces_request.url.path == "/apis/intake/v2/workspaces/fixture-ws/traces"


def test_intake_session_filter_reaches_the_service(loading, intake, tmp_path):
    output = tmp_path / "loaded"
    args = ["--source-kind", "intake", *INTAKE_ARGS, "--session-id", "session-1", "--output-dir", str(output)]
    assert loading.main(args) == 0
    assert all(
        request.url.params.get("filter[session_id]") == "session-1"
        for request in intake.requests
        if request.url.path.endswith("/traces")
    )


@pytest.mark.parametrize(
    "args",
    [
        ["--source-kind", "intake", "--input", str(ATIF_FIXTURE), *INTAKE_ARGS],
        ["--source-kind", "intake", *INTAKE_ARGS[:4]],
        ["--source-kind", "atif", "--input", str(ATIF_FIXTURE), "--workspace", "fixture-ws"],
        ["--source-kind", "atif"],
    ],
    ids=["intake-with-input", "intake-without-window", "selection-for-file-kind", "file-kind-without-input"],
)
def test_source_arguments_must_match_the_kind(loading, args, tmp_path):
    output = tmp_path / "loaded"
    assert loading.main([*args, "--output-dir", str(output)]) == 2
    assert not output.exists()


def test_intake_timestamps_require_a_timezone(tmp_path):
    output = tmp_path / "loaded"
    naive = [*INTAKE_ARGS[:6], "2026-09-01T00:00:00", *INTAKE_ARGS[7:]]
    assert invoke("--source-kind", "intake", *naive, "--output-dir", output).returncode == 2
    assert not output.exists()


def test_intake_failure_is_content_free(loading, monkeypatch, tmp_path, capsys):
    from trace_ingest.loaders import intake as module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="private-fixture-value")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, "IntakeTraceLoader", functools.partial(module.IntakeTraceLoader, transport=transport))
    output = tmp_path / "loaded"
    assert loading.main(["--source-kind", "intake", *INTAKE_ARGS, "--output-dir", str(output)]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "status": "error",
        "message": "intake loading failed: Intake selection could not be loaded",
    }
    assert "private-fixture-value" not in captured.out + captured.err
    assert not output.exists()


def test_gym_row_selects_one_record_and_keeps_missing_results(tmp_path):
    output = tmp_path / "loaded"
    assert invoke("--source-kind", "gym", *sources("gym", tmp_path), "--output-dir", output).returncode == 0
    [trace] = normalized(output)
    assert trace["attributes"]["gym"] == gym_record()
    # The trace cites the selected line of the retained copy, not a staging file.
    assert trace["source_url"] == (output / "source.gym.jsonl").resolve().as_uri() + "#L2"
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


@pytest.mark.parametrize("kind", ["atif", "mlflow", "gym", "trace"])
def test_errors_are_content_free_and_leave_no_evidence(kind, tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"secret":"private-fixture-value"}')
    output = tmp_path / "loaded"
    result = invoke("--source-kind", kind, "--input", source, "--output-dir", output)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "error",
        "message": f"{kind} loading failed; check input and pinned dependencies",
    }
    assert "private-fixture-value" not in result.stdout + result.stderr
    assert result.stderr == ""
    assert not output.exists()


def test_gym_jsonl_requires_explicit_row(tmp_path):
    rollouts = tmp_path / "rollouts.jsonl"
    rollouts.write_text(json.dumps(gym_record()) + "\n")
    output = tmp_path / "loaded"
    result = invoke("--source-kind", "gym", "--input", rollouts, "--output-dir", output)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "status": "error",
        "message": "gym loading failed: JSONL gym sources require a positive one-based row",
    }
    assert not output.exists()


def test_trace_id_keeps_one_trace_of_a_larger_source(tmp_path):
    export = tmp_path / "two.json"
    export.write_text(json.dumps({"traces": [mlflow_record(), second_mlflow_record()]}))
    output = tmp_path / "loaded"
    result = invoke("--source-kind", "mlflow", "--input", export, "--trace-id", "tr-second", "--output-dir", output)
    assert result.returncode == 0, result.stdout
    assert [trace["id"] for trace in normalized(output)] == ["tr-second"]
    receipt = json.loads((output / "loading.json").read_text())
    assert receipt["trace_id"] == "tr-second" and receipt["trace_count"] == 1
    # The retained copy and its digest still cover the whole source, both traces included.
    assert (output / "source.mlflow.json").read_bytes() == export.read_bytes()

    missing = tmp_path / "missing"
    result = invoke("--source-kind", "mlflow", "--input", export, "--trace-id", "tr-absent", "--output-dir", missing)
    assert result.returncode == 2
    assert json.loads(result.stdout)["message"] == (
        "mlflow loading failed: --trace-id must match exactly one trace in the source"
    )
    assert not missing.exists()


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
    mlflow = tmp_path / "two.json"
    mlflow.write_text(json.dumps({"traces": [mlflow_record(), second_mlflow_record()]}))
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
        ({}, f"install {PIN}"),
        ({"nemo-compass": ("0.2.0", None)}, f"install {PIN}"),
        (
            {
                "nemo-compass": (
                    "0.2.1",
                    {"url": "https://github.com/NVIDIA-NeMo/nemo-compass.git", "vcs_info": {"commit_id": "other"}},
                )
            },
            f"install {PIN}",
        ),
        (
            {"nemo-compass": ("0.2.1", {"url": "file:///src/nemo-compass", "dir_info": {"editable": True}})},
            f"install {PIN}",
        ),
        (
            {"nemo-compass": ("0.2.1", None), "trace-ingest": ("0.1.0", None)},
            f"remove trace-ingest and reinstall {PIN}",
        ),
    ],
    ids=["missing", "older-release", "git-checkout", "editable", "trace-ingest"],
)
def test_unpinned_or_shadowing_install_is_rejected_before_writing(loading, installed, message, tmp_path, monkeypatch):
    metadata = loading.importlib.metadata

    def distribution(name):
        if name not in installed:
            raise metadata.PackageNotFoundError(name)
        version, provenance = installed[name]
        return SimpleNamespace(version=version, read_text=lambda _: json.dumps(provenance) if provenance else None)

    monkeypatch.setattr(metadata, "distribution", distribution)
    with pytest.raises(loading.TraceLoadError, match=f"^{re.escape(message)}$"):
        loading.check_installation()
    output = tmp_path / "loaded"
    assert loading.main(["--source-kind", "atif", "--input", str(ATIF_FIXTURE), "--output-dir", str(output)]) == 2
    assert not output.exists()


def _repeated_key_source(loading, kind: str, suffix: str, directory: Path) -> Path:
    """A source Compass alone would load, keeping only the last value of a repeated key."""
    if kind == "atif":
        record = json.dumps(json.loads(ATIF_FIXTURE.read_text()))
        text = record[:-1] + ', "session_id": "shadowing-value"}\n'
    elif kind == "mlflow":
        record = mlflow_record() if suffix == ".jsonl" else {"traces": [mlflow_record()]}
        text = json.dumps(record).replace('"info": {', '"info": {"trace_id": "shadowed-first", ', 1) + "\n"
    else:
        [trace] = loading.load_traces(MLFLOW_FIXTURE, "mlflow")
        text = trace.model_dump_json()[:-1] + ', "id": "shadowing-value"}\n'
    source = directory / f"source{suffix}"
    source.write_text(text)
    return source


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("atif", ".json"), ("atif", ".jsonl"), ("mlflow", ".json"), ("mlflow", ".jsonl"), ("trace", ".jsonl")],
)
def test_repeated_json_key_is_refused_not_collapsed(loading, kind, suffix, tmp_path):
    source = _repeated_key_source(loading, kind, suffix, tmp_path)
    with pytest.raises(loading.TraceLoadError, match="repeats a key"):
        loading.load_traces(source, kind)


def _numbered_source(loading, kind: str, suffix: str, number: str, directory: Path) -> Path:
    """A source carrying ``number`` as a raw JSON token in a free-form field."""
    if kind == "atif":
        record = json.loads(ATIF_FIXTURE.read_text())
        record["extra"] = {"review_number": "NUMBER"}
        text = json.dumps(record, indent=2 if suffix == ".json" else None)
    else:
        [trace] = loading.load_traces(MLFLOW_FIXTURE, "mlflow")
        record = trace.model_dump(mode="json", exclude_none=True)
        record["attributes"] = {**record.get("attributes", {}), "review_number": "NUMBER"}
        text = json.dumps(record)
    source = directory / f"source{suffix}"
    source.write_text(text.replace('"NUMBER"', number) + "\n")
    return source


@pytest.mark.parametrize(("kind", "suffix"), [("atif", ".json"), ("atif", ".jsonl"), ("trace", ".jsonl")])
def test_overflowing_json_number_is_refused_not_nulled(loading, kind, suffix, tmp_path):
    source = _numbered_source(loading, kind, suffix, "1e400", tmp_path)
    with pytest.raises(loading.TraceLoadError, match="^non-finite JSON number$"):
        loading.load_traces(source, kind)


def test_large_finite_json_number_keeps_its_value(loading, tmp_path):
    [trace] = loading.load_traces(_numbered_source(loading, "trace", ".jsonl", "1e300", tmp_path), "trace")
    assert trace.attributes["review_number"] == 1e300
    atif = _numbered_source(loading, "atif", ".json", "1e300", tmp_path)
    assert len(loading.load_traces(atif, "atif")) == 1


def test_load_snapshot_digests_the_bytes_it_loads_and_cites_the_source(loading, tmp_path):
    source = tmp_path / "rollouts.jsonl"
    source.write_text('{"unrelated":"private"}\n' + json.dumps(gym_record()) + "\n")
    [trace], digest = loading.load_snapshot(source, "gym", row=2)
    assert digest == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    assert trace.source_url == source.resolve().as_uri() + "#L2"


@pytest.mark.parametrize("source_kind", ["atif", "mlflow", "gym", "trace"])
def test_load_snapshot_uses_selected_bytes_without_reopening_source(loading, tmp_path, monkeypatch, source_kind):
    if source_kind == "atif":
        raw = ATIF_FIXTURE.read_bytes()
    elif source_kind == "mlflow":
        raw = MLFLOW_FIXTURE.read_bytes()
    elif source_kind == "gym":
        raw = json.dumps(gym_record()).encode()
    else:
        [trace] = loading.load_traces(ATIF_FIXTURE, "atif")
        raw = (trace.model_dump_json() + "\n").encode()
    source = tmp_path / ("selected.jsonl" if source_kind == "trace" else "selected.json")
    source.write_bytes(raw)
    expected, _ = loading.load_snapshot(source, source_kind)
    source.write_text("replaced after the selected read")
    original_open = Path.open

    def selected_must_not_reopen(path, *args, **kwargs):
        assert path != source, "selected source must not be reopened"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", selected_must_not_reopen)
    traces, digest = loading.load_snapshot(source, source_kind, raw=raw)
    assert digest == "sha256:" + hashlib.sha256(raw).hexdigest()
    for trace in [*traces, *expected]:
        pending = [trace]
        while pending:
            item = pending.pop()
            # Compass keeps the private MLflow staging path in provider metadata.
            pointer = item.attributes.get("source_pointer", {})
            if "export_path" in pointer:
                pointer["export_path"] = "<snapshot>"
            pending.extend(item.root_spans if hasattr(item, "root_spans") else item.children)
    assert [trace.model_dump() for trace in traces] == [trace.model_dump() for trace in expected]


def _with_losses(directory: Path, *losses: list[dict]) -> Path:
    """A normalized source holding one copy of the MLflow fixture trace per entry of ``losses``."""
    first = directory / "first"
    assert invoke("--source-kind", "mlflow", "--input", MLFLOW_FIXTURE, "--output-dir", first).returncode == 0
    [trace] = normalized(first)
    lines = [json.dumps({**trace, "id": f"trace-{index}", "losses": entries}) for index, entries in enumerate(losses)]
    source = directory / "lossy.jsonl"
    source.write_text("\n".join(lines) + "\n")
    return source


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("merge_incomplete", "a session could not be loaded completely"),
        ("continued_trajectory_not_followed", "an ATIF trajectory continues in a file that was not loaded"),
        ("future_loss", "the trace records an unrecognized loss"),
    ],
)
def test_incomplete_trace_is_refused(loading, code, reason, tmp_path):
    source = _with_losses(tmp_path, [{"code": code, "detail": {}}])
    with pytest.raises(loading.TraceLoadError, match=f"^source is incomplete: {re.escape(reason)}$"):
        loading.load_traces(source, "trace")


@pytest.mark.parametrize(
    ("code", "detail"),
    [
        ("llm_calls_aggregated", {"llm_call_count": 2}),
        # Compass never reads external trajectory_path files, such as Terminus-2 summarization subagents.
        ("unresolved_subagent_ref", {"ref": {"trajectory_path": "trajectory.summarization-1-summary.json"}}),
        ("steps_outside_agent_not_in_turns", {"agent_span_id": "root", "span_ids": ["nested"]}),
    ],
)
def test_losses_that_keep_the_run_whole_still_load(loading, code, detail, tmp_path):
    source = _with_losses(tmp_path, [{"code": code, "detail": detail}])
    [trace] = loading.load_traces(source, "trace")
    assert [loss.code for loss in trace.losses] == [code]


def test_trace_id_selects_a_complete_trace_beside_an_incomplete_one(loading, tmp_path):
    source = _with_losses(tmp_path, [{"code": "merge_incomplete", "detail": {}}], [])
    with pytest.raises(loading.TraceLoadError, match="incomplete"):
        loading.load_traces(source, "trace")
    [trace] = loading.load_traces(source, "trace", trace_id="trace-1")
    assert trace.id == "trace-1"


def test_unfollowed_atif_continuation_is_refused(loading, tmp_path):
    trajectory = json.loads(ATIF_FIXTURE.read_text())
    trajectory["continued_trajectory_ref"] = "part-2.json"
    source = tmp_path / "trajectory.json"
    source.write_text(json.dumps(trajectory))
    output = tmp_path / "loaded"
    result = invoke("--source-kind", "atif", "--input", source, "--output-dir", output)
    assert result.returncode == 2
    assert "continues in a file that was not loaded" in json.loads(result.stdout)["message"]
    assert not output.exists()
