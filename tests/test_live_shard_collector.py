# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fake-boundary collector routing controls; not Docker containment evidence."""

import importlib
import json
from pathlib import Path

import pytest
import test_live_skill_evaluations as fixtures

live = fixtures.live
repo = fixtures.repo


def test_opt_in_execution_uses_boundary_and_keeps_budget(live, tmp_path, monkeypatch):
    backend = importlib.import_module("live_shard_boundary")
    calls = []
    monkeypatch.setattr(backend, "execute_in_boundary", lambda *a, **k: calls.append((a, k)) or (23, None))
    monkeypatch.setattr(live, "execute", lambda *a: pytest.fail("host execution forbidden"))
    state = tmp_path / "private-state"
    result = live.execute_with_progress(
        ["native"], Path("/repo"), {"KEY": "private"}, tmp_path / "native.log", 1, boundary_state=state, final=True
    )
    assert result == (23, None)
    assert calls[0][0][0] == state
    assert calls[0][1] == {"final": True}


@pytest.mark.parametrize("tier", ["2", "3"])
def test_collector_closes_before_parsing_and_routes_validation(live, repo, tmp_path, monkeypatch, tier):
    backend = importlib.import_module("live_shard_boundary")
    calls = []
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setattr(backend, "close_boundary", lambda state: calls.append("close"))

    def execute(state, argv, cwd, env, log, timeout, *, final=False):
        calls.append((argv, final))
        log.write_text("private fixture")
        return (5, None)

    monkeypatch.setattr(backend, "execute_in_boundary", execute)
    monkeypatch.setattr(live, "execute", lambda *a: pytest.fail("host execution forbidden"))
    report = live.collect(
        repo,
        tmp_path / "output",
        tier=tier,
        skill_name="eval-author",
        check_name="live" if tier == "3" else "context",
        run=True,
        boundary_state=tmp_path / "state",
    )
    assert calls[-1] == "close"
    assert isinstance(calls[0], tuple)
    if tier == "3":
        assert calls[0][0][1:3] == ["tier3", "validate"]
        assert calls[0][1] is False
    else:
        assert calls[0][1] is True
    assert any(row["status"] == "incomplete" for row in report["observations"])


@pytest.mark.parametrize("failure", ["budget", "admission", "backend", "validation"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_failure_closes_before_diagnostic_reads(live, repo, tmp_path, monkeypatch, failure, close_fails):
    backend = live.live_shard_boundary
    events = []
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    original = backend.BoundaryError("synthetic admission failure")
    close_error = OSError("synthetic uncertain close")

    def close(state):
        events.append("close")
        if close_fails:
            raise close_error

    def budget(required):
        events.append("budget")
        if failure == "budget" and "validate" in events:
            raise TimeoutError("synthetic exhausted budget")
        return required

    def execute(state, argv, cwd, env, log, timeout, *, final=False):
        events.append("evaluate" if final else "validate")
        if failure == "admission":
            raise original
        if failure == "backend":
            raise OSError("synthetic backend failure")
        return (5 if failure == "validation" else 0), None

    def capture(*args):
        events.append("diagnostic-read")
        return live.live_agent_diagnostics.unavailable()

    monkeypatch.setattr(live, "live_job_remaining", budget)
    monkeypatch.setattr(backend, "execute_in_boundary", execute)
    monkeypatch.setattr(backend, "close_boundary", close)
    monkeypatch.setattr(live.live_agent_diagnostics, "capture", capture)
    kwargs = dict(tier="3", skill_name="eval-author", check_name="live", run=True, boundary_state=tmp_path / "state")
    if close_fails:
        with pytest.raises(OSError) as caught:
            live.collect(repo, tmp_path / "output", **kwargs)
        assert caught.value is close_error
        assert "diagnostic-read" not in events
        persisted = json.loads((tmp_path / "output/live-skillevaluator-summary.json").read_text())
        row = next(row for row in persisted["observations"] if row["check"] == "live" and row["skill"] == "eval-author")
        assert row["reason"] == "interrupted"
        assert "agent_diagnostics" not in row
        assert "finished_at" not in row
    elif failure == "admission":
        with pytest.raises(backend.BoundaryError) as caught:
            live.collect(repo, tmp_path / "output", **kwargs)
        assert caught.value is original
    else:
        report = live.collect(repo, tmp_path / "output", **kwargs)
        row = next(row for row in report["observations"] if row["check"] == "live" and row["skill"] == "eval-author")
        assert (
            row["reason"]
            == {
                "budget": "insufficient_job_budget",
                "backend": "tool_or_report_error",
                "validation": "dataset_validation_failed",
            }[failure]
        )
    if not close_fails:
        assert events.index("close") < events.index("diagnostic-read")
    if failure == "budget":
        assert events[:3] == ["budget", "validate", "budget"]
        assert "evaluate" not in events


@pytest.mark.parametrize("tier", ["2", "3"])
@pytest.mark.parametrize("close_error", [None, "boundary", "os", "transient"])
def test_native_reads_require_confirmed_close(live, repo, tmp_path, monkeypatch, tier, close_error):
    events = []
    backend = live.live_shard_boundary
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setattr(live, "live_job_remaining", lambda required: required)
    failure = (
        OSError("synthetic close failure")
        if close_error in ("os", "transient")
        else backend.BoundaryError("synthetic uncertain close")
    )

    def close(state):
        events.append("close")
        if close_error and (close_error != "transient" or events.count("close") == 1):
            raise failure

    def execute(state, argv, cwd, env, log, timeout, *, final=False):
        events.append("execute")
        if final:
            path = (
                log.parent / "reports/result.json"
                if tier == "2"
                else log.parent / "results/eval-author/run/result.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(fixtures.tier2() if tier == "2" else fixtures.tier3()))
        return 0, None

    def record(owner, name):
        original = getattr(owner, name)

        def wrapped(*args, **kwargs):
            events.append("read:" + name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    for name in ("read_runtime_failure", "native_run", "read_regular", "capture"):
        record(live.live_agent_diagnostics, name)
    for name in ("summarize_tier2", "summarize_tier3", "diagnostics"):
        record(live, name)
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.name == "result.json":
            events.append("read:report")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(backend, "execute_in_boundary", execute)
    monkeypatch.setattr(backend, "close_boundary", close)
    output = tmp_path / "output"
    kwargs = dict(
        tier=tier,
        skill_name="eval-author",
        check_name="live" if tier == "3" else "context",
        run=True,
        boundary_state=tmp_path / "state",
    )
    if close_error:
        with pytest.raises(type(failure)) as caught:
            live.collect(repo, output, **kwargs)
        assert caught.value is failure
        assert not any(event.startswith("read:") for event in events)
        persisted = json.loads((output / "live-skillevaluator-summary.json").read_text())
        row = next(
            row for row in persisted["observations"] if row["skill"] == "eval-author" and row["tier"] == int(tier)
        )
        assert row["status"] == "incomplete"
        assert row["reason"] == "interrupted"
        assert (
            not {"report_digest", "agent_diagnostics", "diagnostics", "arms", "severity_counts", "finished_at"}
            & row.keys()
        )
    else:
        report = live.collect(repo, output, **kwargs)
        reads = [index for index, event in enumerate(events) if event.startswith("read:")]
        assert reads and events.index("close") < min(reads)
        assert any(row["status"] == ("passed" if tier == "2" else "completed") for row in report["observations"])


def test_early_collector_exception_closes(live, tmp_path, monkeypatch):
    backend = importlib.import_module("live_shard_boundary")
    closed = []
    monkeypatch.setattr(backend, "close_boundary", closed.append)
    state = tmp_path / "state"
    with pytest.raises(ValueError):
        live.collect(tmp_path, tmp_path / "output", boundary_state=state)
    assert closed == [state]


@pytest.mark.parametrize("tier", ["2", "3"])
def test_native_allowlist_forces_safety_flags(live, repo, tmp_path, monkeypatch, tier):
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "0")
    monkeypatch.setenv("HARBOR_TELEMETRY", "1")
    monkeypatch.setenv("UNRELATED_HOST_VALUE", "synthetic-private")
    monkeypatch.setattr(live, "live_job_remaining", lambda required: required)
    monkeypatch.setattr(live.live_shard_boundary, "close_boundary", lambda state: None)
    environments = []

    def execute(state, argv, cwd, env, log, timeout, *, final=False):
        environments.append(dict(env))
        return 0, None

    monkeypatch.setattr(live.live_shard_boundary, "execute_in_boundary", execute)
    live.collect(
        repo,
        tmp_path / "output",
        tier=tier,
        skill_name="eval-author",
        check_name="live" if tier == "3" else "context",
        run=True,
        boundary_state=tmp_path / "state",
    )
    assert len(environments) == (2 if tier == "3" else 1)
    for env in environments:
        assert env.get("PYTHON_DOTENV_DISABLED") == "1"
        assert env.get("HARBOR_TELEMETRY") == "0"
        assert "UNRELATED_HOST_VALUE" not in env
    _, keyless_env, missing_key = live.configuration("inference_hub", read_credentials=False)
    assert missing_key
    assert keyless_env["PYTHON_DOTENV_DISABLED"] == "1"
    assert keyless_env["HARBOR_TELEMETRY"] == "0"
    assert "SKILL_EVAL_LLM_API_KEY" not in keyless_env


def test_private_native_output_is_separate_from_public_summary(live, repo, tmp_path, monkeypatch):
    backend = importlib.import_module("live_shard_boundary")
    native = tmp_path / "workspace/native"
    public = tmp_path / "public"
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setattr(backend, "close_boundary", lambda state: None)

    def execute(state, argv, cwd, env, log, timeout, *, final=False):
        assert log.is_relative_to(native)
        assert Path(env["HOME"]).is_relative_to(native)
        log.write_text("private native output")
        return 5, None

    monkeypatch.setattr(backend, "execute_in_boundary", execute)
    live.collect(
        repo,
        public,
        tier="3",
        skill_name="eval-author",
        check_name="live",
        run=True,
        boundary_state=tmp_path / "state",
        native_output=native,
    )
    assert (public / "live-skillevaluator-summary.json").exists()
    assert not list(public.rglob("*.log"))
