# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline native-error capture and public-artifact boundary tests."""

import importlib
import json
from types import SimpleNamespace

import pytest
from test_live_skill_evaluations import live as live
from test_live_skill_evaluations import repo as repo
from test_live_skill_evaluations import tier3
from test_live_skill_evaluations import tier3_candidate as tier3_candidate


@pytest.fixture
def diagnostic(live):
    return importlib.import_module("live_agent_diagnostics")


@pytest.fixture
def native(tmp_path):
    target = tmp_path / "check"
    run = target / "results/eval-author/run"
    trial = run / "_harbor-jobs/eval-author-opencode-with/task__native"
    (trial / "agent").mkdir(parents=True)
    return target, run, trial


# Synthetic minimal Bun/Node runtime block, not a copied native receipt.
SYNTHETIC_EPIPE = (
    "7 |     throw er;\n"
    "                ^\n"
    "error: EPIPE: broken pipe, write\n"
    "      at emitError (node:events:7:2)\n"
    "      at emitErrorNT (internal:streams/destroy:8:3)\n"
    "      at emitErrorCloseNT (internal:streams/destroy:9:4)\n"
)


def test_mixed_native_output_retains_observed_signature_without_causal_classification(diagnostic, native):
    target, _, trial = native
    (trial / "result.json").write_text(json.dumps({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"}}))
    (trial / "agent/opencode.txt").write_text(
        json.dumps({"type": "tool_use", "part": {"output": "PRIVATE"}})
        + "\n"
        + SYNTHETIC_EPIPE
        + json.dumps({"type": "step_finish", "part": {"reason": "stop"}})
        + "\n"
    )
    result = diagnostic.capture({}, target, "eval-author")
    assert result["events"] == [
        {
            "source": "harbor_trial",
            "phase": "with_skill",
            "trial": 0,
            "category": "agent_command_failed",
            "exception_type": "NonZeroAgentExitCodeError",
            "observed_signature": "bun_node_epipe",
        }
    ]
    assert result["capture_status"] == "captured"
    assert result["non_json_lines"] == len(SYNTHETIC_EPIPE.splitlines())
    assert diagnostic.public_projection(result) == result
    assert "PRIVATE" not in diagnostic.render(result)
    assert "broken pipe" not in diagnostic.render(result)


@pytest.mark.parametrize(
    "text",
    [
        "EPIPE\n",
        "error: EPIPE: broken pipe, write\n",
        "const example = 'EPIPE: broken pipe, write';\n",
        "```text\n" + SYNTHETIC_EPIPE + "```\n",
        "Example output:\n" + SYNTHETIC_EPIPE,
        SYNTHETIC_EPIPE.replace("at emitErrorNT", "at unrelated"),
        json.dumps({"type": "tool_use", "part": {"output": SYNTHETIC_EPIPE}}),
        json.dumps({"type": "text", "part": {"text": SYNTHETIC_EPIPE}}),
        json.dumps({"prompt": SYNTHETIC_EPIPE}, indent=2),
        json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": SYNTHETIC_EPIPE}}}),
        json.dumps({"nested": [{"output": SYNTHETIC_EPIPE}]}, indent=2),
        '{"type":"tool_use","output":"\n' + SYNTHETIC_EPIPE + '"}\n',
        '{\n"nested": [\n' + SYNTHETIC_EPIPE + "]}\n",
        "[" * 2000 + "\n" + SYNTHETIC_EPIPE,
        SYNTHETIC_EPIPE.replace("\n", "\\n"),
    ],
)
def test_epipe_tokens_and_json_payloads_are_not_runtime_signatures(diagnostic, native, text):
    target, _, trial = native
    (trial / "result.json").write_text(json.dumps({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"}}))
    (trial / "agent/opencode.txt").write_text(text)
    result = diagnostic.capture({}, target, "eval-author")
    assert all("observed_signature" not in event for event in result["events"])
    assert all(event["category"] in ("unknown", "agent_command_failed") for event in result["events"])


@pytest.mark.parametrize(
    "exception", [None, {}, {"exception_type": "AgentTimeoutError"}, {"exception_type": "PRIVATE"}]
)
def test_epipe_requires_same_trial_native_nonzero_evidence(diagnostic, native, exception):
    target, _, trial = native
    (trial / "result.json").write_text(json.dumps({"exception_info": exception}))
    (trial / "agent/opencode.txt").write_text(SYNTHETIC_EPIPE)
    result = diagnostic.capture(tier3(), target, "eval-author")
    assert all("observed_signature" not in event for event in result["events"])


def test_successful_trial_with_epipe_text_is_not_promoted_to_a_failure(live, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "fixture-only")

    def execute(argv, cwd, env, log, timeout):
        if "validate" in argv:
            return 0, None
        run = log.parent / "results/eval-author/run"
        trial = run / "_harbor-jobs/eval-author-opencode-with/task__native"
        (trial / "agent").mkdir(parents=True)
        (trial / "agent/opencode.txt").write_text(SYNTHETIC_EPIPE)
        (trial / "result.json").write_text('{"exception_info":null}')
        (run / "result.json").write_text(json.dumps(tier3()))
        return 0, None

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "success", run=True, tier="3", skill_name="eval-author")
    row = next(r for r in report["observations"] if r["status"] != "skipped")
    assert row["status"] == "completed" and row["reason"] is None and row["exit_code"] == 0
    assert row["agent_diagnostics"]["events"] == []


@pytest.mark.parametrize("receipt", ["missing", "malformed", "symlink"])
def test_epipe_requires_readable_native_failure_receipt(diagnostic, native, tmp_path, receipt):
    target, _, trial = native
    path = trial / "result.json"
    if receipt == "malformed":
        path.write_text("{")
    elif receipt == "symlink":
        outside = tmp_path / "unowned.json"
        outside.write_text(json.dumps({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"}}))
        path.symlink_to(outside)
    (trial / "agent/opencode.txt").write_text(SYNTHETIC_EPIPE)
    assert diagnostic.capture({}, target, "eval-author")["events"] == []


@pytest.mark.parametrize("signature", ["PRIVATE", "epipe", "bun_node_epipe PRIVATE", None, [], {}, True])
def test_reprojection_rejects_arbitrary_observed_signatures(diagnostic, signature):
    evidence = diagnostic.unavailable()
    evidence["events"] = [
        {
            "source": "harbor_trial",
            "phase": "with_skill",
            "trial": 0,
            "exception_type": "NonZeroAgentExitCodeError",
            "observed_signature": signature,
        }
    ]
    result = diagnostic.public_projection(evidence)
    assert "observed_signature" not in result["events"][0]
    assert result == diagnostic.public_projection(result)


@pytest.mark.parametrize(
    "source,name",
    [("harbor_trial", "Unknown"), ("harbor_trial", "AgentTimeoutError"), ("opencode_error_event", "UnknownError")],
)
def test_reprojection_does_not_attach_signature_to_unrelated_errors(diagnostic, source, name):
    evidence = diagnostic.unavailable()
    evidence["events"] = [
        {
            "source": source,
            "phase": "with_skill",
            "trial": 0,
            "exception_type": name,
            "name": name,
            "observed_signature": "bun_node_epipe",
        }
    ]
    assert "observed_signature" not in diagnostic.public_projection(evidence)["events"][0]


@pytest.mark.parametrize(
    "value,category",
    [
        ({"name": "APIError", "data": {"statusCode": 429, "isRetryable": True}}, "rate_limited"),
        ({"name": "APIError", "data": {"statusCode": 403}}, "permission_denied"),
        ({"name": "APIError", "data": {"statusCode": 503}}, "provider_unavailable"),
        ({"name": "APIError", "data": {"statusCode": 402}}, "unknown"),
        ({"name": "APIError", "data": {"statusCode": "401", "isRetryable": "true"}}, "unknown"),
        ({"name": "APIError", "data": {"statusCode": True}}, "unknown"),
        ({"name": "UnknownError", "data": {"statusCode": 401, "message": "401 auth failed"}}, "unknown"),
        ({"name": "PRIVATE", "data": {"statusCode": 401}}, "unknown"),
        ({"name": ["PRIVATE"], "data": []}, "unknown"),
        ({"name": "ProviderAuthError", "data": {"message": "PRIVATE"}}, "authentication_failed"),
        ({"name": "ContextOverflowError"}, "context_limit"),
        ("PRIVATE 401", "unknown"),
        (None, "unknown"),
        (False, "unknown"),
    ],
)
def test_only_typed_allowlisted_error_fields_classify(diagnostic, value, category):
    result = diagnostic.error_event({"type": "error", "error": value})
    assert result["category"] == category
    assert "PRIVATE" not in json.dumps(result)
    if category == "unknown" and not (
        isinstance(value, dict) and value.get("name") == "APIError" and value.get("data", {}).get("statusCode") == 402
    ):
        assert "status_code" not in result and "retryable" not in result


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        401,
        "401 auth failed",
        False,
        {"type": "tool_use", "error": {"name": "APIError", "data": {"statusCode": 401}}},
        {"type": "text", "part": {"text": "401 auth failed"}},
        {"type": "tool_result", "part": {"type": "error", "error": {"name": "ProviderAuthError"}}},
    ],
)
def test_nested_tool_errors_and_task_text_are_not_provider_evidence(diagnostic, value):
    assert diagnostic.error_event(value) is None


@pytest.mark.parametrize(
    "layout",
    [
        "file_link",
        "agent_link",
        "trial_link",
        "job_link",
        "jobs_link",
        "run_link",
        "skill_link",
        "results_link",
        "target_link",
        "hardlink",
        "oversize",
        "fifo",
    ],
)
@pytest.mark.parametrize("stream", ["structured", "epipe"])
def test_native_capture_rejects_unsafe_files_and_ancestors(diagnostic, native, tmp_path, layout, stream):
    import os

    target, run, trial = native
    path = trial / "agent/opencode.txt"
    text = (
        SYNTHETIC_EPIPE if stream == "epipe" else json.dumps({"type": "error", "error": {"name": "ProviderAuthError"}})
    )
    path.write_text(text)
    (trial / "result.json").write_text(json.dumps({"exception_info": {"exception_type": "NonZeroAgentExitCodeError"}}))
    if layout == "oversize":
        path.write_text(text + "x" * diagnostic.MAX_FILE_BYTES)
    elif layout == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif layout == "hardlink":
        os.link(path, tmp_path / "unowned")
    else:
        selected = {
            "file_link": path,
            "agent_link": path.parent,
            "trial_link": trial,
            "job_link": trial.parent,
            "jobs_link": trial.parent.parent,
            "run_link": run,
            "skill_link": run.parent,
            "results_link": run.parent.parent,
            "target_link": target,
        }[layout]
        outside = tmp_path / "unowned"
        selected.rename(outside)
        selected.symlink_to(outside, target_is_directory=outside.is_dir())
    result = diagnostic.capture({}, target, "eval-author")
    assert all(event["source"] == "harbor_trial" and "observed_signature" not in event for event in result["events"])
    assert result["capture_status"] != "captured"


def test_missing_native_tree_is_unavailable_not_partial(diagnostic, tmp_path):
    assert diagnostic.capture({}, tmp_path / "missing", "eval-author")["capture_status"] == "unavailable"


@pytest.mark.parametrize("value", [None, [], False, 1, "PRIVATE"])
def test_scalar_trial_receipts_do_not_claim_complete_capture(diagnostic, native, value):
    target, _, trial = native
    (trial / "result.json").write_text(json.dumps(value))
    result = diagnostic.capture({}, target, "eval-author")
    assert result["capture_status"] == "partial" and result["events"] == []


def test_malformed_scalar_and_large_event_stream_is_bounded(diagnostic, native):
    target, _, trial = native
    (trial / "result.json").write_text("[null]")
    path = trial / "agent/opencode.txt"
    path.write_text("bad-json\nnull\n401\n[]\n" + (json.dumps({"type": "error", "error": None}) + "\n") * 100)
    result = diagnostic.capture({}, target, "eval-author")
    assert len(result["events"]) == diagnostic.MAX_EVENTS
    assert result["capture_status"] == "partial" and len(diagnostic.render(result)) < 13_000
    assert all(e["category"] == "unknown" for e in result["events"])


def test_capture_limits_directory_and_total_bytes(diagnostic, native, monkeypatch):
    target, _, trial = native
    for index in range(diagnostic.MAX_ENTRIES + 1):
        (trial.parent / str(index)).mkdir()
    assert diagnostic.capture({}, target, "eval-author")["capture_status"] == "partial"
    monkeypatch.setattr(diagnostic, "MAX_ENTRIES", 100)
    monkeypatch.setattr(diagnostic, "MAX_TOTAL_BYTES", 1)
    (trial / "agent/opencode.txt").write_text("{}")
    assert diagnostic.capture({}, target, "eval-author")["files_read"] == 0


@pytest.mark.parametrize("bad", ["PRIVATE", "1.2.3-PRIVATE", None, {}, True])
def test_native_version_is_numeric_release_only(diagnostic, native, bad):
    target, _, trial = native
    (trial / "result.json").write_text(json.dumps({"agent_info": {"name": "opencode", "version": bad}}))
    assert diagnostic.capture({}, target, "eval-author")["opencode_reported_versions"] == []


@pytest.mark.parametrize("outcome", ["nonzero", "timeout", "missing", "malformed", "success", "validation"])
def test_diagnostic_parser_failure_never_changes_outcome(live, repo, tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "fixture")

    def execute(argv, cwd, env, log, timeout):
        if "validate" in argv:
            return (1 if outcome == "validation" else 0), None
        if outcome != "missing":
            path = log.parent / "results/eval-author/run/result.json"
            path.parent.mkdir(parents=True)
            data = tier3()
            if outcome == "nonzero":
                data.update(execution_status="failed", execution_errors=["NonZeroAgentExitCodeError"])
            path.write_text("[" if outcome == "malformed" else json.dumps(data))
        return (0 if outcome == "success" else 1), ("timeout" if outcome == "timeout" else None)

    monkeypatch.setattr(live, "execute", execute)

    def broken(*args):
        raise RuntimeError("PRIVATE parser failure")

    monkeypatch.setattr(live.live_agent_diagnostics, "capture", broken)
    report = live.collect(repo, tmp_path / "out", run=True, tier="3", skill_name="eval-author")
    row = next(r for r in report["observations"] if r["status"] != "skipped")
    expected = {
        "nonzero": "agent_command_failed",
        "timeout": "timeout",
        "missing": "report_missing_or_ambiguous",
        "malformed": "tool_or_report_error",
        "success": None,
        "validation": "dataset_validation_failed",
    }
    assert row["reason"] == expected[outcome]
    assert row["status"] == ("completed" if outcome == "success" else "incomplete")
    assert row["agent_diagnostics"]["capture_status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(report)


def test_structured_facts_survive_stage_and_aggregate(live, diagnostic, repo, tmp_path, monkeypatch):
    import live_ci

    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("SKILL_EVAL_AGENT_MODEL", "fixture/chat")
    args = dict(repo=repo, tier="3", skill="eval-author", check="live", models_resolved=True)
    source = tmp_path / "source"
    candidate = live.collect(
        repo, source, tier="3", skill_name="eval-author", check_name="live", ci_context_budget=True
    )
    candidate["mode"] = "run"
    row = next(r for r in candidate["observations"] if r["status"] != "skipped")
    row.update(status="incomplete", reason="agent_command_failed", exit_code=1)
    evidence = diagnostic.unavailable(tier3())
    evidence.update(
        capture_status="captured",
        files_read=1,
        non_json_lines=6,
        events=[
            {
                "source": "opencode_error_event",
                "phase": "preflight",
                "trial": 0,
                "category": "rate_limited",
                "name": "APIError",
                "status_code": 429,
                "retryable": True,
            },
            {
                "source": "harbor_trial",
                "phase": "with_skill",
                "trial": 0,
                "category": "agent_command_failed",
                "exception_type": "NonZeroAgentExitCodeError",
                "observed_signature": "bun_node_epipe",
            },
        ],
    )
    row.update(agent_diagnostics=evidence, diagnostics=diagnostic.render(evidence))
    live.persist_report(candidate, source)
    staged = tmp_path / "artifacts/skill-shard-1-1-live-eval-author"
    live_ci.initialize(SimpleNamespace(**args, command="initialize", output=staged))
    live_ci.merge(SimpleNamespace(command="stage", source=source, output=staged))
    aggregate = tmp_path / "aggregate"
    live_ci.initialize(SimpleNamespace(**args, command="initialize", output=aggregate))
    result = live_ci.merge(SimpleNamespace(command="aggregate", source=staged.parent, output=aggregate))
    actual = next(r for r in result["observations"] if r["status"] != "skipped")
    assert actual.get("agent_diagnostics") == evidence
    assert actual["reason"] == "agent_command_failed" and result["counts"]["incomplete"] == 1
    assert "rate_limited" in (aggregate / "live-skillevaluator-summary.md").read_text()
    assert "bun_node_epipe" in (aggregate / "live-skillevaluator-summary.md").read_text()
    assert diagnostic.public_projection(actual["agent_diagnostics"]) == evidence


def test_aggregate_reprojects_structured_facts_and_ignores_raw_text(diagnostic, tier3_candidate):
    live_ci, candidate, base, expected = tier3_candidate
    row = candidate["observations"][0]
    row.update(status="incomplete", reason="agent_command_failed", diagnostics="PRIVATE RAW CONSOLE")
    evidence = diagnostic.unavailable()
    evidence["PRIVATE"] = "PRIVATE"
    evidence["events"] = [
        {
            "source": "opencode_error_event",
            "phase": "preflight",
            "trial": 0,
            "category": "authentication_failed",
            "name": "PRIVATE",
            "status_code": "401",
            "message": "PRIVATE",
            "retryable": "PRIVATE",
        }
    ]
    row["agent_diagnostics"] = evidence
    result = live_ci.validated_row(candidate, base, expected)
    assert "PRIVATE" not in json.dumps(result)
    assert result["agent_diagnostics"]["events"][0]["category"] == "unknown"


@pytest.mark.parametrize(
    "origin,reason", [("wrapper", "patched_runtime_activation_failed"), ("startup_hook", "prepared_image_mismatch")]
)
def test_owned_failure_channel_ignores_console_and_binds_attempt(diagnostic, tmp_path, monkeypatch, origin, reason):
    path = tmp_path / "failure.json"
    token = "a" * 64
    monkeypatch.setenv("_EA_LIVE_FAILURE_PATH", str(path))
    monkeypatch.setenv("_EA_LIVE_FAILURE_TOKEN", token)
    diagnostic.record_runtime_failure(origin, reason)
    assert diagnostic.read_runtime_failure(path, token) == {"source": origin, "reason": reason}
    assert diagnostic.read_runtime_failure(path, "b" * 64) == {
        "source": "unresolved",
        "reason": "runtime_failure_evidence_invalid",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE" not in json.dumps(diagnostic.read_runtime_failure(path, token))


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("wrapper", "patched_runtime_activation_failed"),
        ("prepared", "prepared_image_mismatch"),
        ("prepared_validation", "prepared_image_mismatch"),
        ("activation_validation", "patched_runtime_activation_failed"),
        ("handoff", "insufficient_job_budget"),
        ("startup_hook", "patched_runtime_activation_failed"),
        ("hook_prepared", "prepared_image_mismatch"),
    ],
)
def test_real_failure_producers_reach_collector_without_raw_output(
    live, diagnostic, repo, tmp_path, monkeypatch, mode, reason, capsys
):
    import hashlib
    import sys
    from pathlib import Path

    if mode == "prepared_validation":
        pytest.importorskip("skillevaluator", reason="prepared validation follows the pinned native package guard")

    configuration = live.configuration

    def keyless(provider, **kwargs):
        models, env, _ = configuration(provider, read_credentials=False)
        return models, env, False

    monkeypatch.setattr(live, "configuration", keyless)
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("SKILL_EVAL_AGENT_MODEL", "fixture/chat")
    monkeypatch.setenv("EA_REQUIRE_PATCHED_OPENCODE", "1")
    monkeypatch.delenv("EA_PATCHED_OPENCODE_MANIFEST", raising=False)
    if mode == "prepared":
        monkeypatch.setenv("EA_REQUIRE_PREPARED_IMAGES", "1")
    if mode in ("prepared_validation", "activation_validation"):
        monkeypatch.setenv("EA_PATCHED_OPENCODE_MANIFEST", str(tmp_path / "PRIVATE-missing-binary.json"))
    if mode == "prepared_validation":
        monkeypatch.setenv("EA_PREPARED_IMAGES_MANIFEST", str(tmp_path / "PRIVATE-missing-prepared.json"))
    original = live.execute

    def execute(argv, cwd, env, log, timeout):
        assert not any("API_KEY" in key for key in env)
        if "validate" in argv:
            return 0, None  # Dataset validation boundary, never a model call.
        if mode == "handoff":
            env = dict(env, EA_LIVE_JOB_DEADLINE="0")
        if mode in ("startup_hook", "hook_prepared"):
            env = dict(
                env,
                PYTHONPATH=str(Path(live.__file__).parent / "patched_opencode_hook"),
                EA_PATCHED_OPENCODE_MANIFEST="PRIVATE-missing-manifest",
            )
            argv = [sys.executable, "-c", "raise AssertionError('startup hook did not fail closed')"]
            if mode == "hook_prepared":
                manifest = tmp_path / "binary.json"
                prepared = tmp_path / "prepared.json"
                manifest.write_text("{}")
                prepared.write_text("{}")
                env.update(
                    _EA_PATCHED_OPENCODE_HOOK="pinned-v1",
                    _EA_PATCHED_OPENCODE_RUNTIME_SHA256=hashlib.sha256(
                        Path(live.__file__).with_name("patched_opencode_runtime.py").read_bytes()
                    ).hexdigest(),
                    EA_PATCHED_OPENCODE_MANIFEST=str(manifest),
                    _EA_PATCHED_OPENCODE_MANIFEST_SHA256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    EA_PREPARED_IMAGES_MANIFEST=str(prepared),
                    _EA_PREPARED_IMAGES_SHA256="0" * 64,
                )
        return original(argv, cwd, env, log, timeout)

    monkeypatch.setattr(live, "execute", execute)
    output = tmp_path / "failure-collection"
    report = live.collect(
        repo, output, tier="3", skill_name="eval-author", check_name="live", ci_context_budget=True, run=True
    )
    row = next(row for row in report["observations"] if row["status"] != "skipped")
    assert row["status"] == "incomplete" and row["reason"] == reason
    assert row["agent_diagnostics"]["runtime_failure"] == {
        "source": "startup_hook" if mode in ("startup_hook", "hook_prepared") else "wrapper",
        "reason": reason,
    }
    public = json.dumps(report) + (output / "live-skillevaluator-summary.md").read_text() + capsys.readouterr().out
    assert "PRIVATE" not in public and "Traceback" not in public and str(tmp_path) not in public
    assert diagnostic.public_projection(row["agent_diagnostics"]) == row["agent_diagnostics"]
    import live_ci

    args = dict(repo=repo, tier="3", skill="eval-author", check="live", models_resolved=True)
    staged = tmp_path / "artifacts/skill-shard-1-1-live-eval-author"
    live_ci.initialize(SimpleNamespace(**args, command="initialize", output=staged))
    live_ci.merge(SimpleNamespace(command="stage", source=output, output=staged))
    aggregate = tmp_path / "aggregate"
    live_ci.initialize(SimpleNamespace(**args, command="initialize", output=aggregate))
    result = live_ci.merge(SimpleNamespace(command="aggregate", source=staged.parent, output=aggregate))
    actual = next(r for r in result["observations"] if r["status"] != "skipped")
    assert actual["reason"] == reason and actual["agent_diagnostics"] == row["agent_diagnostics"]


@pytest.mark.parametrize(
    "reason", ["PRIVATE prompt", "patched_runtime_activation_failed PRIVATE", {}, ["prepared_image_mismatch"]]
)
def test_public_tier3_reason_is_allowlisted_at_producer_and_stage(live, diagnostic, tier3_candidate, tmp_path, reason):
    ci, candidate, base, expected = tier3_candidate
    row = candidate["observations"][0]
    row.update(status="incomplete", reason=reason)
    result = ci.validated_row(candidate, base, expected)
    assert result["reason"] == "execution_incomplete"
    live.persist_report(candidate, tmp_path)
    saved = json.loads((tmp_path / "live-skillevaluator-summary.json").read_text())
    assert saved["observations"][0]["reason"] == "execution_incomplete"
    assert "PRIVATE" not in (tmp_path / "live-skillevaluator-summary.md").read_text()


@pytest.mark.parametrize(
    "mutation", ["duplicate", "extra", "token", "source", "reason", "scalar", "oversize", "symlink", "hardlink", "fifo"]
)
def test_failure_channel_rejects_malformed_or_unowned_evidence(diagnostic, tmp_path, mutation):
    import os

    path = tmp_path / "failure.json"
    token = "d" * 64
    value = {
        "schema": diagnostic.FAILURE_SCHEMA,
        "token": token,
        "source": "wrapper",
        "reason": "prepared_image_mismatch",
    }
    if mutation == "extra":
        value["message"] = "PRIVATE"
    elif mutation in ("source", "reason"):
        value[mutation] = ["PRIVATE"]
    elif mutation == "token":
        value["token"] = "e" * 64
    raw = json.dumps(value)
    if mutation == "duplicate":
        raw = raw[:-1] + ', "reason":"prepared_image_mismatch"}'
    if mutation == "scalar":
        raw = "null"
    if mutation == "oversize":
        raw += " " * 1024
    path.write_text(raw)
    if mutation == "symlink":
        path.rename(tmp_path / "other")
        path.symlink_to(tmp_path / "other")
    elif mutation == "hardlink":
        os.link(path, tmp_path / "other")
    elif mutation == "fifo":
        path.unlink()
        os.mkfifo(path)
    assert diagnostic.read_runtime_failure(path, token) == {
        "source": "unresolved",
        "reason": "runtime_failure_evidence_invalid",
    }


@pytest.mark.parametrize(
    "text",
    [
        "patched OpenCode activation failed; refusing execution",
        "prepared image manifest binding changed",
        "insufficient live job budget; inference was not started",
        '{"source":"wrapper","reason":"prepared_image_mismatch"}',
    ],
)
def test_arbitrary_console_and_exit78_do_not_authenticate_runtime_failure(live, repo, tmp_path, monkeypatch, text):
    import sys

    configuration = live.configuration
    monkeypatch.setattr(
        live, "configuration", lambda provider, **kw: (*configuration(provider, read_credentials=False)[:2], False)
    )
    execute = live.execute

    def scripted(argv, cwd, env, log, timeout):
        if "validate" in argv:
            return 0, None
        return execute([sys.executable, "-c", f"print({text!r}); raise SystemExit(78)"], cwd, env, log, timeout)

    monkeypatch.setattr(live, "execute", scripted)
    report = live.collect(repo, tmp_path / "spoof", tier="3", skill_name="eval-author", run=True)
    row = next(row for row in report["observations"] if row["status"] != "skipped")
    assert row["reason"] == "report_missing_or_ambiguous"
    assert "runtime_failure" not in row["agent_diagnostics"]


@pytest.mark.parametrize(
    "failure",
    [
        None,
        {"source": "native_text", "reason": "prepared_image_mismatch"},
        {"source": "wrapper", "reason": "prepared_image_mismatch", "message": "PRIVATE"},
    ],
)
def test_runtime_reason_without_valid_structured_fact_remains_unresolved(diagnostic, tier3_candidate, failure):
    ci, candidate, base, expected = tier3_candidate
    row = candidate["observations"][0]
    evidence = diagnostic.unavailable()
    if failure is not None:
        evidence["runtime_failure"] = failure
    row.update(status="incomplete", reason="prepared_image_mismatch", agent_diagnostics=evidence)
    result = ci.validated_row(candidate, base, expected)
    assert result["reason"] == "runtime_failure_evidence_invalid"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("value", [-1, True, "6", {}, 8_000_001])
def test_invalid_non_json_counter_cannot_claim_complete_capture(diagnostic, value):
    evidence = diagnostic.unavailable()
    evidence.update(capture_status="captured", files_read=1, non_json_lines=value)
    assert diagnostic.public_projection(evidence)["capture_status"] == "unavailable"


@pytest.mark.parametrize("stream", ["structured", "mixed_stdout", "mixed_stderr", "unretained_stderr"])
def test_pinned_native_opencode_nonzero_bypasses_error_parser_but_capture_survives(
    live,
    repo,
    tmp_path,
    monkeypatch,
    stream,
    capsys,
):
    import asyncio
    import os
    from importlib.metadata import version
    from pathlib import PurePosixPath

    pytest.importorskip("harbor.agents.installed.opencode")
    native_collector = pytest.importorskip("skillevaluator.tier3.harbor.collector")
    if version("harbor") != "0.24.0" or version("skillevaluator") != "0.7.0":
        pytest.skip("requires pinned SkillEvaluator 0.7.0 / Harbor 0.24.0 runtime")
    from harbor.agents.installed.base import NonZeroAgentExitCodeError
    from harbor.agents.installed.opencode import OpenCode
    from harbor.models.agent.context import AgentContext
    from harbor.models.trial.result import ExceptionInfo

    _trial_exception_details = native_collector._trial_exception_details

    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "fixture-only")
    event = {
        "type": "error",
        "error": {
            "name": "APIError",
            "data": {"statusCode": 429, "isRetryable": True, "message": "PRIVATE provider response"},
        },
    }

    def execute(argv, cwd, env, log, timeout):
        if "validate" in argv:
            return 0, None
        run = log.parent / "results/eval-author/run"
        trial = run / "_harbor-jobs/runtime-preflight-opencode/task__native"
        logs = trial / "agent"
        logs.mkdir(parents=True)

        class ScriptedEnvironment:
            async def exec(self, *, command, **kwargs):
                if " run --format=json " in command:
                    output = (
                        json.dumps(event) + "\n"
                        if stream == "structured"
                        else (
                            '{"type":"step_start"}\n'
                            + SYNTHETIC_EPIPE
                            + '{"type":"step_finish","part":{"reason":"stop"}}\n'
                        )
                    )
                    (logs / "opencode.txt").write_text("{}\n" if stream == "unretained_stderr" else output)
                    stderr = stream in ("mixed_stderr", "unretained_stderr")
                    return SimpleNamespace(
                        return_code=1, stdout="" if stderr else output, stderr=output if stderr else ""
                    )
                return SimpleNamespace(return_code=0, stdout="", stderr="")

        with monkeypatch.context() as isolated:
            isolated.setattr(os, "environ", {"PYTHONDONTWRITEBYTECODE": "1"})
            agent = OpenCode(
                logs_dir=logs,
                model_name="openai/fixture",
                version="1.2.3",
                environment_logs_dir=PurePosixPath(str(logs)),
            )

            def parser_must_not_run():
                pytest.fail("nonzero exec must raise before OpenCode's later error parser")

            isolated.setattr(agent, "_error_messages", parser_must_not_run)
            try:
                asyncio.run(agent.run("PRIVATE instruction " * 100, ScriptedEnvironment(), AgentContext()))
            except NonZeroAgentExitCodeError as exc:
                receipt = {
                    "exception_info": ExceptionInfo.from_exception(exc).model_dump(mode="json"),
                    "agent_info": agent.to_agent_info().model_dump(mode="json"),
                }
            else:
                pytest.fail("native exec did not fail")
        (trial / "result.json").write_text(json.dumps(receipt))
        name, detail = _trial_exception_details(trial)
        assert name == "NonZeroAgentExitCodeError" and len(detail) == 600
        assert "statusCode" not in detail
        data = tier3()
        data.update(execution_status="failed", execution_errors=[detail])
        (run / "result.json").write_text(json.dumps(data))
        return 1, None

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "native-producer", run=True, tier="3", skill_name="eval-author")
    row = next(r for r in report["observations"] if r["status"] != "skipped")
    assert row["reason"] == "agent_command_failed" and row["status"] == "incomplete"
    events = row["agent_diagnostics"]["events"]
    if stream == "structured":
        assert events[-1]["category"] == "rate_limited"
    else:
        assert len(events) == 1 and events[0]["category"] == "agent_command_failed"
        assert events[0].get("observed_signature") == (None if stream == "unretained_stderr" else "bun_node_epipe")
    assert row["exit_code"] == 1 and "arms" not in row
    assert row["agent_diagnostics"]["opencode_reported_versions"] == ["1.2.3"]
    assert "PRIVATE" not in json.dumps(report)
    assert "broken pipe" not in json.dumps(report) + capsys.readouterr().out
