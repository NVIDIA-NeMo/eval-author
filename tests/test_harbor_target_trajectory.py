# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))
from harbor_target_trajectory import (  # noqa: E402
    TargetRecorder,
    ToolCall,
    ToolResult,
    Usage,
    check_target_export,
    main,
)

IDENTITY = {"agent_name": "research-agent", "run_id": "refund__abc123", "task_id": "refund"}


def recorder(agent_dir: Path, run_id: str = IDENTITY["run_id"]) -> TargetRecorder:
    return TargetRecorder(
        agent_dir,
        agent_name=IDENTITY["agent_name"],
        agent_version="1",
        run_id=run_id,
        task_id=IDENTITY["task_id"],
        model_name="selected-model",
    )


def conversation(agent_dir: Path) -> TargetRecorder:
    target = recorder(agent_dir)
    target.user("Refund order 42")
    target.agent(
        "Checking the order.",
        tool_calls=[ToolCall("call-1", "lookup_order", {"order_id": 42})],
        results=[ToolResult("call-1", '{"status": "delivered"}')],
        usage=Usage(prompt_tokens=10, completion_tokens=4),
    )
    target.agent("Your refund is approved.", usage=Usage(prompt_tokens=20, completion_tokens=6))
    return target


def test_recorded_conversation_is_valid_harbor_atif_bound_to_the_agent_under_test(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    conversation(agent_dir).finish("completed")
    saved = check_target_export(agent_dir, **IDENTITY).to_json_dict()
    assert [step["source"] for step in saved["steps"]] == ["user", "agent", "agent"]
    assert saved["steps"][1]["tool_calls"][0]["function_name"] == "lookup_order"
    assert saved["steps"][1]["observation"]["results"][0]["source_call_id"] == "call-1"
    assert saved["final_metrics"]["total_prompt_tokens"] == 30
    assert saved["final_metrics"]["total_completion_tokens"] == 10
    assert saved["extra"]["status"] == "completed"


def test_unknown_usage_stays_unknown_instead_of_summing_to_zero(tmp_path: Path) -> None:
    target = recorder(tmp_path)
    target.user("Hello")
    target.agent("Hi", usage=Usage(prompt_tokens=3))
    target.agent("Anything else?")
    saved = json.loads((tmp_path / "trajectory.json").read_text())
    assert "total_prompt_tokens" not in saved["final_metrics"]
    assert saved["final_metrics"]["total_steps"] == 3


def test_failure_keeps_partial_evidence_and_rejects_later_steps(tmp_path: Path) -> None:
    target = recorder(tmp_path)
    target.user("Refund order 42")
    target.finish("interrupted", error="Turn cancelled")
    saved = check_target_export(tmp_path, **IDENTITY).to_json_dict()
    assert len(saved["steps"]) == 1
    assert (saved["extra"]["status"], saved["extra"]["error"]) == ("interrupted", "Turn cancelled")
    with pytest.raises(ValueError, match="finished"):
        target.agent("late reply")


def test_restarted_adapter_continues_the_same_run_and_rejects_other_runs(tmp_path: Path) -> None:
    recorder(tmp_path).user("First message")
    resumed = recorder(tmp_path)
    resumed.agent("Reply after restart")
    saved = json.loads((tmp_path / "trajectory.json").read_text())
    assert [step["step_id"] for step in saved["steps"]] == [1, 2]
    assert saved["extra"]["adapter_restarts"] == 1
    with pytest.raises(FileExistsError, match="another run"):
        recorder(tmp_path, run_id="other-trial")
    resumed.finish("completed")
    with pytest.raises(FileExistsError, match="finished"):
        recorder(tmp_path)


def test_host_teardown_finishes_a_running_recording_without_counting_a_restart(tmp_path: Path, capsys) -> None:
    conversation(tmp_path)
    arguments = ["finish", "--agent-dir", str(tmp_path), "--run-id", "refund__abc123", "--task-id", "refund"]
    assert main([*arguments, "--status", "completed"]) == 0
    assert json.loads(capsys.readouterr().out) == {"finished": True, "status": "completed"}
    saved = check_target_export(tmp_path, **IDENTITY).to_json_dict()
    assert (saved["extra"]["status"], saved["extra"]["adapter_restarts"]) == ("completed", 0)
    assert saved["agent"] == {"name": "research-agent", "version": "1", "model_name": "selected-model"}
    assert main([*arguments, "--status", "failed"]) == 0
    assert json.loads(capsys.readouterr().out) == {"finished": False, "status": "completed"}
    assert main([*arguments[:2], str(tmp_path / "missing"), *arguments[3:], "--status", "failed"]) == 1


def test_tool_results_must_reference_calls_in_their_step(tmp_path: Path) -> None:
    target = recorder(tmp_path)
    with pytest.raises(ValueError, match="reference a tool call"):
        target.agent("Done", results=[ToolResult("missing", "x")])
    assert not (tmp_path / "trajectory.json").exists()


def _customer_substitution(agent_dir: Path) -> None:
    saved = json.loads((agent_dir / "trajectory.json").read_text())
    saved["extra"]["subject"] = "simulated_user"
    (agent_dir / "trajectory.json").write_text(json.dumps(saved))


def _edited_events(agent_dir: Path) -> None:
    events = agent_dir / "target-events.jsonl"
    events.write_text(events.read_text().replace("approved", "denied"))


@pytest.mark.parametrize(
    ("tamper", "check", "message"),
    [
        (_customer_substitution, {}, "simulator evidence cannot substitute"),
        (_edited_events, {}, "changed after export"),
        (lambda _: None, {"run_id": "another-trial"}, "different run or task"),
        (lambda _: None, {"agent_name": "other-agent"}, "different agent"),
        (lambda path: (path / "trajectory.json").unlink(), {}, "Missing target trajectory"),
    ],
)
def test_check_rejects_evidence_that_does_not_belong_to_the_target_run(tmp_path, tamper, check, message) -> None:
    conversation(tmp_path).finish("completed")
    tamper(tmp_path)
    with pytest.raises(ValueError, match=message):
        check_target_export(tmp_path, **{**IDENTITY, **check})


def test_check_command_reads_the_trial_agent_directory(tmp_path: Path, capsys) -> None:
    trial = tmp_path / "refund__abc123"
    conversation(trial / "agent").finish("completed")
    arguments = ["check", "--trial-dir", str(trial), "--agent-name", "research-agent", "--task-id", "refund"]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "steps": 3, "status": "completed"}
    assert main([*arguments[:-1], "other-task"]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_recorder_runs_without_site_packages_for_container_adapters(tmp_path: Path) -> None:
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from pathlib import Path; from harbor_target_trajectory import TargetRecorder;"
        "r = TargetRecorder(Path(sys.argv[2]), agent_name='research-agent', agent_version='1',"
        " run_id='refund__abc123', task_id='refund'); r.user('hi'); r.agent('hello'); r.finish('completed')"
    )
    subprocess.run([sys.executable, "-I", "-S", "-c", script, str(SCRIPTS), str(tmp_path)], check=True)
    check_target_export(tmp_path, **IDENTITY)
