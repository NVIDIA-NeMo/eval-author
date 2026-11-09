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


@pytest.mark.parametrize(
    "check,ci_context_budget", [("context", False), ("context", True), ("similarity", False), ("live", False)]
)
def test_boundary_payload_restores_native_path_without_host_path(
    live, repo, tmp_path, monkeypatch, check, ci_context_budget
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    backend = live.live_shard_boundary
    runtime_bin = tmp_path / "workspace/live-runtime/bin"
    evaluator = str(runtime_bin / "skillevaluator")
    python = str(runtime_bin / "python")
    host_path = "/usr/local/bin:/usr/bin:/bin"
    monkeypatch.setenv("PATH", host_path + ":/ambient-writable-bin")
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setattr(live, "live_job_remaining", lambda required: required)
    monkeypatch.setattr(live, "execute", lambda *a: pytest.fail("native host execution forbidden"))
    data = dict(status="ready", owner=None, deadline=live.time.time() + 4500, docker_config="/host-only")
    monkeypatch.setattr(backend, "_lock", lambda state: nullcontext())
    monkeypatch.setattr(backend, "_read", lambda state: data.copy())
    monkeypatch.setattr(backend, "_save", lambda state, value: data.update(value))
    monkeypatch.setattr(backend, "_expired", lambda value: False)
    monkeypatch.setattr(backend, "_identity", lambda: {"pid": 123, "start": "fixture"})
    monkeypatch.setattr(backend, "_docker_exec", lambda value: ["not-executed"])
    monkeypatch.setattr(backend, "close_boundary", lambda state: data.update(status="closed"))
    payloads = []

    class Transport:
        returncode = 0

        def __init__(self, argv, **kwargs):
            assert kwargs["env"] == {"PATH": host_path, "HOME": "/host-only"}

        def communicate(self, payload, timeout):
            payloads.append((json.loads(payload), timeout))

    monkeypatch.setattr(backend, "subprocess", SimpleNamespace(Popen=Transport, PIPE=-1))
    live.collect(
        repo,
        tmp_path / "output",
        tier="3" if check == "live" else "2",
        skill_name="all" if check == "similarity" else "eval-author",
        check_name=check,
        ci_context_budget=ci_context_budget,
        evaluator=evaluator,
        evaluator_python=python,
        run=True,
        boundary_state=tmp_path / "state",
    )
    assert len(payloads) == (2 if check == "live" else 1)
    for payload, timeout in payloads:
        assert payload["env"]["PATH"] == host_path + ":" + str(runtime_bin)
        assert payload["env"]["SKILL_EVAL_LLM_API_KEY"] == "synthetic-key"
        assert payload["env"]["SKILL_EVAL_EMBEDDING_API_KEY"] == "synthetic-key"
        assert "OPENAI_API_KEY" not in payload["env"]
        assert live.HUB_KEY not in payload["env"]
        assert payload["cwd"] == str(repo)
    if check == "live":
        assert payloads[0][0]["argv"][:3] == [evaluator, "tier3", "validate"]
        assert payloads[0][1] == 60
        assert payloads[1][0]["argv"][0] == python
        assert payloads[1][0]["argv"][2:4] == ["tier3", "evaluate"]
        assert "--agent-runtime-preflight" in payloads[1][0]["argv"]
        assert payloads[1][1] == live.TIER3_TIMEOUT_SECONDS
    else:
        assert payloads[0][0]["argv"][0] == (python if ci_context_budget else evaluator)
        assert payloads[0][1] == 300
    assert data["status"] == "closed"
    assert live.os.environ["PATH"] == host_path + ":/ambient-writable-bin"


@pytest.mark.parametrize(
    "evaluator",
    [
        "skillevaluator",
        "relative/bin/skillevaluator",
        "/runtime/../bin/skillevaluator",
        "/runtime:extra/bin/skillevaluator",
        "/runtime/./bin/skillevaluator",
        "/runtime\x00/bin/skillevaluator",
    ],
)
def test_boundary_rejects_ambiguous_runtime_path_before_credentials(live, repo, tmp_path, monkeypatch, evaluator):
    closed = []
    monkeypatch.setattr(live, "configuration", lambda *a, **k: pytest.fail("credential configuration reached"))
    monkeypatch.setattr(live.live_shard_boundary, "execute_in_boundary", lambda *a, **k: pytest.fail("native reached"))
    monkeypatch.setattr(live.live_shard_boundary, "close_boundary", closed.append)
    with pytest.raises(ValueError, match="absolute evaluator path"):
        live.collect(
            repo,
            tmp_path / "output",
            tier="2",
            skill_name="eval-author",
            check_name="context",
            run=True,
            evaluator=evaluator,
            boundary_state=tmp_path / "state",
        )
    assert closed == [tmp_path / "state"]
    assert not (tmp_path / "output").exists()


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
        evaluator="/native/bin/skillevaluator",
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
    kwargs = dict(
        tier="3",
        skill_name="eval-author",
        check_name="live",
        run=True,
        evaluator="/native/bin/skillevaluator",
        boundary_state=tmp_path / "state",
    )
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
        evaluator="/native/bin/skillevaluator",
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


@pytest.mark.parametrize(("tier", "skill", "check"), [("2", "all", "context"), ("both", "eval-author", "all")])
def test_boundary_multirow_rejected_before_configuration(live, repo, tmp_path, monkeypatch, tier, skill, check):
    events = []
    monkeypatch.setattr(live, "configuration", lambda *a, **k: pytest.fail("credential configuration reached"))
    monkeypatch.setattr(live.live_shard_boundary, "execute_in_boundary", lambda *a, **k: pytest.fail("native reached"))
    monkeypatch.setattr(live.live_shard_boundary, "close_boundary", lambda state: events.append("close"))
    with pytest.raises(ValueError, match="single-use boundary.*one runnable observation"):
        live.collect(
            repo,
            tmp_path / "output",
            tier=tier,
            skill_name=skill,
            check_name=check,
            run=True,
            boundary_state=tmp_path / "state",
        )
    assert events == ["close"]
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("boundary_mode", [False, True])
def test_collection_similarity_is_one_runnable_row(live, repo, tmp_path, monkeypatch, boundary_mode):
    calls = []
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setattr(live.live_shard_boundary, "close_boundary", lambda state: None)

    def execute(argv, cwd, env, log, timeout, **kwargs):
        calls.append(argv)
        return 5, None

    monkeypatch.setattr(live, "execute_with_progress", execute)
    report = live.collect(
        repo,
        tmp_path / "output",
        tier="2",
        check_name="similarity",
        run=True,
        evaluator="/native/bin/skillevaluator",
        boundary_state=tmp_path / "state" if boundary_mode else None,
    )
    assert len(calls) == 1
    assert calls[0][1] == "similarity-check"
    assert sum(row["status"] != "skipped" for row in report["observations"]) == 1


def test_nonboundary_context_keeps_multiple_rows(live, repo, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin:/ordinary-runtime/bin")
    monkeypatch.setenv(live.HUB_KEY, "synthetic-key")
    monkeypatch.setattr(live, "execute_with_progress", lambda *a, **k: calls.append(a) or (5, None))
    report = live.collect(repo, tmp_path / "output", tier="2", check_name="context", run=True)
    expected = len(list((repo / "skills").glob("*/SKILL.md")))
    assert expected > 1
    assert len(calls) == expected
    assert all(call[2]["PATH"] == live.os.environ["PATH"] for call in calls)
    assert sum(row["status"] != "skipped" for row in report["observations"]) == expected


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
        evaluator="/native/bin/skillevaluator",
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
        evaluator="/native/bin/skillevaluator",
        boundary_state=tmp_path / "state",
        native_output=native,
    )
    assert (public / "live-skillevaluator-summary.json").exists()
    assert not list(public.rglob("*.log"))
