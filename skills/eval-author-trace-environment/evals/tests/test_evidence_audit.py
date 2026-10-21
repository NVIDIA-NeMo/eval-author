# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic result coverage and end-to-end replay regressions on Compass Traces."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fixture_compiler as fc  # noqa: E402
from evidence_audit import audit_evidence  # noqa: E402
from trace_source import load_trace  # noqa: E402

READ = {"name": "read", "inputSchema": {"type": "object"}}
CALL_SPAN = "trajectory/step/2/tool/0"


# ATIF source builders: the input files Compass loads, never asserted on directly.
def atif_call(call_id="call", path="file"):
    return {"tool_call_id": call_id, "function_name": "read", "arguments": {"path": path}}


def atif_result(call_id="call", content="private observation text", **status):
    result = {"content": content, **status}
    if call_id is not None:
        result["source_call_id"] = call_id
    return result


def atif_agent_step(step, message, calls=(), results=None):
    payload = {"step_id": step, "source": "agent", "message": message}
    if calls:
        payload["tool_calls"] = list(calls)
    if results is not None:
        payload["observation"] = {"results": list(results)}
    return payload


def atif_source(*, results=None, calls=None, last=None, trajectory_id="parent", subagents=()):
    payload = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session",
        "trajectory_id": trajectory_id,
        "agent": {"name": "synthetic", "version": "1", "tool_definitions": [READ]},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Inspect the supplied file."},
            atif_agent_step(
                2,
                "Inspection completed successfully.",
                calls if calls is not None else [atif_call()],
                results if results is not None else [atif_result()],
            ),
            last if last is not None else atif_agent_step(3, "All done."),
        ],
    }
    if subagents:
        payload["subagent_trajectories"] = list(subagents)
    return payload


def load(tmp_path, source):
    path = tmp_path / "source.atif.json"
    path.write_text(json.dumps(source))
    return load_trace(path, "atif")


def plan(trace):
    inventory = fc.derive_tool_call_inventory(trace, safe_trace_sha256="synthetic")
    return inventory, fc.derive_tool_call_plan(inventory)


def fixtures(trace):
    inventory, proposed = plan(trace)
    requested = {
        "schema": fc.DECISIONS_SCHEMA,
        "decisions": [{"name": "read", "access": "mock", "adapter": "mcp", "note": "Synthetic replay test"}],
    }
    access = fc.resolve_tool_access(inventory, proposed, requested, reviewer_kind="agent")
    return fc.build_call_fixtures(inventory, access)


def audit(trace):
    return audit_evidence(trace, safe_trace_sha256="synthetic")


@pytest.mark.parametrize("container", ["root", "extra"])
@pytest.mark.parametrize("key", ["isError", "is_error"])
@pytest.mark.parametrize("value", [True, False])
def test_explicit_status_survives_compilation_and_stdio(tmp_path, container, key, value):
    status = {key: value} if container == "root" else {"extra": {key: value}}
    trace = load(tmp_path, atif_source(results=[atif_result(**status)]))
    compiled = fixtures(trace)
    [case] = compiled["tools"][0]["cases"]
    assert case["is_error"] is value
    assert case["evidence_turns"] == ["trajectory/turn/2"]
    assert case["evidence"] == [{"span_id": CALL_SPAN, "call_id": "call"}]
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(fc.build_mcp_scenario(compiled)))
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "read", "arguments": {"path": "file"}},
    }
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "replay_mcp_server.py"), "--scenario", str(path)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    replayed = json.loads(run.stdout)["result"]
    assert replayed["isError"] is value
    assert replayed["content"][0]["text"] == "private observation text"
    expected = "error" if value else "not_error"
    assert audit(trace)["counts"][f"{expected}_results"] == 1


@pytest.mark.parametrize("flag", [None, 0, 1, "false", [], {}])
@pytest.mark.parametrize("container", ["root", "extra"])
def test_malformed_status_blocks_replay(tmp_path, flag, container):
    status = {"isError": flag} if container == "root" else {"extra": {"is_error": flag}}
    trace = load(tmp_path, atif_source(results=[atif_result(**status)]))
    _, proposed = plan(trace)
    assert "result_error_status_invalid" in proposed["tools"][0]["reason_codes"]
    with pytest.raises(ValueError):
        fixtures(trace)
    report = audit(trace)
    assert report["counts"]["invalid_status_results"] == 1
    assert {"code": "result_status_invalid", "span_id": CALL_SPAN, "result": 0} in report["findings"]


def test_conflicting_aliases_block_replay(tmp_path):
    trace = load(tmp_path, atif_source(results=[atif_result(isError=True, extra={"is_error": False})]))
    _, proposed = plan(trace)
    assert "result_error_status_conflicting" in proposed["tools"][0]["reason_codes"]
    with pytest.raises(ValueError):
        fixtures(trace)
    assert audit(trace)["counts"]["conflicting_status_results"] == 1


@pytest.mark.parametrize("first,second", [(True, False), (None, False)])
def test_identical_text_with_different_status_is_not_deterministic(tmp_path, first, second):
    status = {} if first is None else {"isError": first}
    later = atif_agent_step(3, "Checked again.", [atif_call()], [atif_result(isError=second)])
    trace = load(tmp_path, atif_source(results=[atif_result(**status)], last=later))
    _, proposed = plan(trace)
    assert "conflicting_results_for_identical_arguments" in proposed["tools"][0]["reason_codes"]
    report = audit(trace)
    assert report["counts"]["conflicting_input_groups"] == 1
    assert {
        "code": "conflicting_results_for_identical_arguments",
        "span_ids": [CALL_SPAN, "trajectory/step/3/tool/0"],
    } in report["findings"]


def test_fixture_shape_and_unrecorded_status_are_preserved(tmp_path):
    trace = load(tmp_path, atif_source())
    case = fixtures(trace)["tools"][0]["cases"][0]
    assert set(case) == {"input", "output", "evidence_turns", "evidence"}
    assert fc.build_mcp_scenario(fixtures(trace))["tools"][0]["cases"][0]["result"]["isError"] is False
    report = audit(trace)
    assert report["schema"] == "nemo.eval_author.trace_environment_evidence_audit.v2"
    assert report["safe_trace_sha256"] == "synthetic"
    assert report["counts"]["unrecorded_status_results"] == 1
    assert report["counts"]["not_error_results"] == 0
    assert report["counts"]["agent_turns_without_results"] == 1
    assert {"code": "agent_turn_without_results", "turn_id": "trajectory/turn/3"} in report["findings"]
    assert report["execution_verified"] is False
    assert "private observation text" not in json.dumps(report)
    assert "successfully" not in json.dumps(report)


def test_pairing_is_scoped_to_turn_and_agent_not_reused_call_id(tmp_path):
    child = atif_source(results=[atif_result(is_error=True)], trajectory_id="child")
    reused = atif_agent_step(3, "Reading again.", [atif_call()])
    trace = load(tmp_path, atif_source(last=reused, subagents=[child]))
    report = audit(trace)
    assert report["counts"]["agents"] == 2
    assert report["counts"]["calls"] == 3
    assert report["counts"]["paired_calls"] == 2
    assert report["counts"]["missing_result_calls"] == 1
    assert {"code": "missing_result", "span_id": "trajectory/step/3/tool/0"} in report["findings"]
    # Identical arguments in another agent never join this agent's conflict group.
    assert report["counts"]["conflicting_input_groups"] == 0
    assert {"code": "result_status_error", "span_id": "trajectory/subagent/0/step/2/tool/0", "result": 0} in report[
        "findings"
    ]


def test_parallel_results_pair_by_id_and_unlinked_results_stay_unlinked(tmp_path):
    calls = [atif_call(), atif_call("other", "other")]
    results = [
        atif_result("other", "other result", is_error=True),
        atif_result(),
        atif_result(None, "not evidence for either call"),
    ]
    report = audit(load(tmp_path, atif_source(calls=calls, results=results)))
    assert report["counts"]["paired_calls"] == 2
    assert report["counts"]["unlinked_results"] == 1
    assert {"code": "unlinked_result", "turn_id": "trajectory/turn/2", "unlinked_result": 0} in report["findings"]
    assert {"code": "result_status_error", "span_id": "trajectory/step/2/tool/1", "result": 0} in report["findings"]
    report = audit(load(tmp_path, atif_source(calls=calls, results=[*results, atif_result()])))
    assert report["counts"]["multiple_result_calls"] == 1
    assert {"code": "multiple_results", "span_id": CALL_SPAN} in report["findings"]


def cli(*args):
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "trace_environment.py"), *map(str, args)], capture_output=True, text=True
    )
    return run.returncode, json.loads(run.stdout)


def workspace(tmp_path):
    code, created = cli("init", "--root", tmp_path / ".eval-author/work", "--task-id", "evidence-case")
    assert code == 0, created
    root = Path(created["task_dir"])
    source = tmp_path / "source.json"
    source.write_text(json.dumps(atif_source()))
    code, prepared = cli("prepare", "--task-dir", root, "--source", source, "--source-kind", "atif")
    assert code == 0, prepared
    return root


def test_command_is_private_idempotent_and_does_not_change_summary(tmp_path):
    root = workspace(tmp_path)
    summary = (root / "summary.json").read_bytes()
    first = cli("audit-evidence", "--task-dir", root)
    assert first[0] == 0, first
    assert first == cli("audit-evidence", "--task-dir", root)
    assert (root / "summary.json").read_bytes() == summary
    report = root / "private/evidence-audit.json"
    if os.name == "posix":
        assert report.stat().st_mode & 0o777 == 0o600
    assert "findings" not in first[1]
    written = json.loads(report.read_text())
    safe_sha256 = json.loads(summary)["source"]["safe_sha256"]
    assert written["safe_trace_sha256"] == safe_sha256
    assert written["counts"]["paired_calls"] == 1
    assert "private observation text" not in report.read_text()
    report.write_text("{}")
    assert cli("audit-evidence", "--task-dir", root)[0] == 1
    assert report.read_text() == "{}"


@pytest.mark.parametrize("kind", ["source_changed", "private_symlink", "report_symlink"])
def test_command_rejects_changed_source_and_linked_output(tmp_path, kind):
    root = workspace(tmp_path)
    if kind == "source_changed":
        (root / "safe/trace.json").write_text("{}")
    elif kind == "private_symlink":
        (root / "private").rename(root / "retained")
        (root / "private").symlink_to(root / "retained", target_is_directory=True)
    else:
        (root / "private/evidence-audit.json").symlink_to(tmp_path / "absent")
    assert cli("audit-evidence", "--task-dir", root)[0] == 1
