# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic observation coverage and end-to-end replay regressions."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fixture_compiler as fc  # noqa: E402
from evidence_audit import audit_evidence  # noqa: E402


def trace():
    return {
        "schema_version": "ATIF-v1.7",
        "agent": {"name": "synthetic", "tool_definitions": [{"name": "read", "inputSchema": {"type": "object"}}]},
        "steps": [
            {"step_id": 1, "source": "user", "message": "Inspect the supplied file."},
            {
                "step_id": 2,
                "source": "agent",
                "message": "Inspection completed successfully.",
                "tool_calls": [{"tool_call_id": "call", "function_name": "read", "arguments": {"path": "file"}}],
                "observation": {"results": [{"source_call_id": "call", "content": "private observation text"}]},
            },
            {"step_id": 3, "source": "agent", "message": "All done."},
        ],
    }


def observation(data):
    return data["steps"][1]["observation"]["results"][0]


def plan(data):
    inventory = fc.derive_tool_call_inventory(data, safe_atif_sha256="sha256:synthetic")
    return inventory, fc.derive_tool_call_plan(inventory)


def fixtures(data):
    inventory, proposed = plan(data)
    requested = {
        "schema": fc.DECISIONS_SCHEMA,
        "decisions": [{"name": "read", "access": "mock", "adapter": "mcp", "note": "Synthetic replay test"}],
    }
    access = fc.resolve_tool_access(inventory, proposed, requested, reviewer_kind="agent")
    return fc.build_call_fixtures(inventory, access)


@pytest.mark.parametrize("container", ["root", "extra"])
@pytest.mark.parametrize("key", ["isError", "is_error"])
@pytest.mark.parametrize("value", [True, False])
def test_explicit_status_survives_compilation_and_stdio(tmp_path, container, key, value):
    data = trace()
    result = observation(data)
    target = result if container == "root" else result.setdefault("extra", {})
    target[key] = value
    scenario = fc.build_mcp_scenario(fixtures(data))
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario))
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
    assert replayed["content"][0]["text"] == result["content"]


@pytest.mark.parametrize("flag", [None, 0, 1, "false", [], {}])
def test_malformed_status_blocks_replay(flag):
    data = trace()
    observation(data)["isError"] = flag
    _, proposed = plan(data)
    assert "result_error_status_invalid" in proposed["tools"][0]["reason_codes"]
    with pytest.raises(ValueError):
        fixtures(data)


def test_conflicting_aliases_block_replay():
    data = trace()
    observation(data).update({"isError": True, "extra": {"is_error": False}})
    _, proposed = plan(data)
    assert "result_error_status_conflicting" in proposed["tools"][0]["reason_codes"]


@pytest.mark.parametrize("first,second", [(True, False), (None, False)])
def test_identical_text_with_different_status_is_not_deterministic(first, second):
    data = trace()
    if first is not None:
        observation(data)["isError"] = first
    later = deepcopy(data["steps"][1])
    later["step_id"] = 3
    later["observation"]["results"][0]["isError"] = second
    data["steps"][2] = later
    _, proposed = plan(data)
    assert "conflicting_results_for_identical_arguments" in proposed["tools"][0]["reason_codes"]
    assert audit_evidence(data, safe_atif_sha256="x")["counts"]["conflicting_input_groups"] == 1


def test_legacy_shape_and_unknown_status_are_preserved():
    data = trace()
    case = fixtures(data)["tools"][0]["cases"][0]
    assert set(case) == {"input", "output", "evidence_steps", "evidence"}
    assert fc.build_mcp_scenario(fixtures(data))["tools"][0]["cases"][0]["result"]["isError"] is False
    report = audit_evidence(data, safe_atif_sha256="x")
    assert report["counts"]["unrecorded_status_observations"] == 1
    assert report["counts"]["not_error_observations"] == 0
    assert report["counts"]["agent_steps_without_observations"] == 1
    assert report["execution_verified"] is False
    assert "private observation text" not in json.dumps(report)
    assert "successfully" not in json.dumps(report)


def test_pairing_uses_step_and_child_scope_not_reused_call_id():
    data = trace()
    child = trace()
    observation(child)["is_error"] = True
    data["subagent_trajectories"] = [child]
    data["steps"][2]["tool_calls"] = deepcopy(data["steps"][1]["tool_calls"])
    report = audit_evidence(data, safe_atif_sha256="x")
    assert report["counts"]["calls"] == 3
    assert report["counts"]["paired_calls"] == 2
    assert report["counts"]["missing_observation_calls"] == 1
    assert report["counts"]["conflicting_input_groups"] == 0
    assert any(item["path"].startswith("$.subagent_trajectories[0]") for item in report["findings"])


def test_parallel_results_pair_by_id_and_unlinked_results_stay_unlinked():
    data = trace()
    step = data["steps"][1]
    step["tool_calls"].append({"tool_call_id": "other", "function_name": "read", "arguments": {"path": "other"}})
    step["observation"]["results"].insert(0, {"source_call_id": "other", "content": "other result", "is_error": True})
    step["observation"]["results"].append({"content": "not evidence for either call"})
    report = audit_evidence(data, safe_atif_sha256="x")
    assert report["counts"]["paired_calls"] == 2
    assert report["counts"]["unlinked_observations"] == 1
    step["observation"]["results"].append(deepcopy(observation(data)))
    assert audit_evidence(data, safe_atif_sha256="x")["counts"]["multiple_observation_calls"] == 1


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
    source.write_text(json.dumps(trace()))
    code, prepared = cli("prepare", "--task-dir", root, "--atif", source, "--source-kind", "atif")
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
    report.write_text("{}")
    assert cli("audit-evidence", "--task-dir", root)[0] == 1
    assert report.read_text() == "{}"


def test_prepare_takes_only_atif_but_recorded_source_kinds_still_load(tmp_path):
    root = workspace(tmp_path)
    source = tmp_path / "source.json"
    code, created = cli("init", "--root", tmp_path / ".eval-author/work", "--task-id", "gym-case")
    assert code == 0, created
    code, refused = cli("prepare", "--task-dir", created["task_dir"], "--atif", source, "--source-kind", "gym")
    assert code == 1 and "--source-kind must be 'atif'" in refused["error"]
    # Workspaces prepared before the ATIF-only limit recorded their provider kind.
    summary = json.loads((root / "summary.json").read_text())
    summary["source"]["kind"] = "gym"
    (root / "summary.json").write_text(json.dumps(summary))
    assert cli("audit-evidence", "--task-dir", root)[0] == 0


@pytest.mark.parametrize("kind", ["source_changed", "private_symlink", "report_symlink"])
def test_command_rejects_changed_source_and_linked_output(tmp_path, kind):
    root = workspace(tmp_path)
    if kind == "source_changed":
        (root / "safe/trace.atif.json").write_text("{}")
    elif kind == "private_symlink":
        (root / "private").rename(root / "retained")
        (root / "private").symlink_to(root / "retained", target_is_directory=True)
    else:
        (root / "private/evidence-audit.json").symlink_to(tmp_path / "absent")
    assert cli("audit-evidence", "--task-dir", root)[0] == 1
