# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/examples/workplace"
spec = importlib.util.spec_from_file_location("workplace_harness_check", ROOT / "check_harness.py")
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_protocol_read_write_discovery(tmp_path):
    report = harness.run(tmp_path / "run", False, None, None)
    assert report["protocol"]["unseen_write_read"] is True
    assert report["protocol"]["unsupported_rejected"] is True
    assert report["live_agent"] == "unrun"


def test_auth_removed_on_pre_execution_failure(tmp_path, monkeypatch):
    auth = tmp_path / "synthetic-auth.json"
    auth.write_text('{"synthetic": true}')
    original = subprocess.run

    def fail_git(command, *args, **kwargs):
        if command[:2] == ["git", "init"]:
            raise OSError("synthetic setup failure")
        return original(command, *args, **kwargs)

    monkeypatch.setattr(harness.subprocess, "run", fail_git)
    output = tmp_path / "run"
    with pytest.raises(OSError, match="synthetic setup failure"):
        harness.run(output, True, auth, None)
    assert not (output / "home/.codex/auth.json").exists()
    assert not (output / "home").exists()


def test_paired_protocol_uses_production_replay(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    report = harness.run_pair(before, after, False, None, None)
    assert report["read_after_write"] == {"exact_replay": False, "stateful_simulation": True}
    assert report["evidence"] == "credential-free protocol only"
    baseline = json.loads((before / "summary.json").read_text())
    assert baseline["calibration"]["heldout_read"] is False
    assert baseline["calibration"]["basis"] == "runtime-generated calibration, not source-corpus conformance"
    scenario = json.loads((before / "calibration/scenario.json").read_text())
    reply = next(tool for tool in scenario["tools"] if tool["definition"]["name"] == "email_reply_email")
    assert [case["arguments"] for case in reply["cases"]] == [
        {"email_id": "1", "body": "Confirmed for Tuesday at 10:00."}
    ]
    assert all("heldout" not in source["path"] for source in baseline["calibration"]["training_sources"])
    assert json.loads((after / "comparison.json").read_text()) == report


def probe_events(replay=False):
    values = [
        "No emails found.",
        "Email replied successfully.",
        {"emails": [{"email_id": "5", "body": "native-live-new-body", "inbox/outbox": "outbox"}]},
    ]
    if replay:
        values = ["No emails found.", None, "No emails found."]
    events = []
    for index, ((tool, arguments), value) in enumerate(
        zip(
            [
                ("email_search_emails", {"query": "native-live-new-body"}),
                ("email_reply_email", {"email_id": "1", "body": "native-live-new-body"}),
                ("email_search_emails", {"query": "native-live-new-body"}),
            ],
            values,
            strict=True,
        )
    ):
        rejected = replay and index == 1
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": str(index),
                    "type": "mcp_tool_call",
                    "server": "workplace-stateful-simulation",
                    "tool": tool,
                    "arguments": arguments,
                    "status": "failed" if rejected else "completed",
                    "error": {"message": "MCP tool error: No recorded fixture matched this request."}
                    if rejected
                    else None,
                    "result": None if rejected else {"content": [{"type": "text", "text": json.dumps(value)}]},
                },
            }
        )
    return events


@pytest.mark.parametrize("replay", [False, True])
def test_inspect_native_result_and_error(replay):
    mode = "exact_replay" if replay else "stateful_simulation"
    report = harness.inspect_events(probe_events(replay), mode)
    assert report["read_after_write"] is not replay
    assert report["native_calls"] == 3


@pytest.mark.parametrize("fault", ["shell", "wrong_arguments", "wrong_result", "transport_error", "incomplete"])
def test_inspection_rejects_false_native_evidence(fault):
    events = probe_events(True)
    if fault == "shell":
        events.append({"type": "item.started", "item": {"type": "command_execution"}})
    elif fault == "wrong_arguments":
        events[1]["item"]["arguments"]["email_id"] = "2"
    elif fault == "wrong_result":
        events[2]["item"]["result"]["content"][0]["text"] = '"Email replied successfully."'
    elif fault == "transport_error":
        events[1]["item"]["error"]["message"] = "connection failed"
    else:
        events.append({"type": "item.started", "item": {"type": "mcp_tool_call", "id": "unfinished"}})
    with pytest.raises(ValueError):
        harness.inspect_events(events, "exact_replay")


@pytest.mark.parametrize("failure", ["copy", "execution", "inspection", "success"])
def test_live_cleanup_entire_home(tmp_path, monkeypatch, failure):
    auth = tmp_path / "synthetic-auth.json"
    auth.write_text('{"synthetic": true}')
    output = tmp_path / "output"
    output.mkdir()
    original_copy = harness.shutil.copyfile

    def copy(source, target):
        original_copy(source, target)
        if failure == "copy":
            raise OSError("synthetic copy failure")

    def execute(command, **kwargs):
        if command[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(command, 0)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-cli synthetic-test", "")
        assert "shell_tool" in command and "unified_exec" in command
        assert kwargs["env"]["CODEX_HOME"] == str(output / "home/.codex")
        (output / "home/.codex/refreshed-token").write_text("synthetic")
        if failure == "execution":
            raise subprocess.TimeoutExpired(command, 240)
        events = probe_events()
        if failure == "inspection":
            events[0]["item"]["status"] = "failed"
        return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(event) for event in events), "")

    monkeypatch.setattr(harness.shutil, "copyfile", copy)
    monkeypatch.setattr(harness.shutil, "which", lambda name: "/synthetic/codex")
    monkeypatch.setattr(harness.subprocess, "run", execute)
    config = {"mcp_servers": {"workplace-stateful-simulation": {"command": "synthetic"}}}
    if failure == "success":
        report = harness.live_codex(output, auth, "synthetic-model", config, "stateful_simulation")
        assert report["model_requested"] == "synthetic-model"
        assert report["cli_version"] == "codex-cli synthetic-test"
    else:
        with pytest.raises((OSError, subprocess.TimeoutExpired, ValueError)):
            harness.live_codex(output, auth, None, config, "stateful_simulation")
    assert not (output / "home").exists()
    assert auth.read_text() == '{"synthetic": true}'


def test_pair_requires_two_new_outputs(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    after.mkdir()
    with pytest.raises(FileExistsError):
        harness.run_pair(before, after, False, None, None)
    assert not before.exists()
    with pytest.raises(ValueError):
        harness.run_pair(before, before / "nested", False, None, None)
