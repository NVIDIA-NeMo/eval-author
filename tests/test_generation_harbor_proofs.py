# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
from pathlib import Path

import pytest

from generation_benchmark.eval_author.harbor import AgentConfig, run_named_harbor_trial
from generation_benchmark.eval_author.ports.errors import ExperimentError


def _agent_config(tmp_path: Path) -> AgentConfig:
    config_path = tmp_path / "agent.yml"
    config_path.write_text(
        """
schema: nemo.eval_author.agent.v1
name: poc-agent
model: oracle
trace_agent_label: poc-agent
harbor:
  command: [harbor]
  agent: oracle
  environment: docker
  agent_timeout_seconds: 60
""".strip(),
        encoding="utf-8",
    )
    from generation_benchmark.eval_author.harbor import load_agent_config

    loaded = load_agent_config(config_path)
    assert loaded is not None
    return loaded


def _write_valid_trial(trial_dir: Path, *, task_name: str, model: str) -> None:
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent").mkdir()
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": task_name,
                "exception_info": None,
                "agent_info": {"model_info": {"name": model}},
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    (trial_dir / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "session_id": f"session-{trial_dir.name}",
                "trajectory_id": f"trajectory-{trial_dir.name}",
                "steps": [{"source": "agent", "content": "done"}],
            }
        ),
        encoding="utf-8",
    )


def test_run_named_harbor_trial_skips_valid_repeat(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "draft"
    task_dir.mkdir()
    config = _agent_config(tmp_path)
    trials_dir = tmp_path / "repeat-1"
    _write_valid_trial(trials_dir / "repeat-1", task_name="poc-agent/cover-tool", model="oracle")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("generation_benchmark.eval_author.harbor.subprocess.run", fake_run)
    trace = run_named_harbor_trial(
        task_dir,
        task_name="poc-agent/cover-tool",
        config=config,
        trials_dir=trials_dir,
        trial_name="repeat-1",
        expected_model="oracle",
    )
    assert trace.reward == 1.0
    assert not calls


def test_run_named_harbor_trial_invokes_harbor_for_oracle(tmp_path: Path, monkeypatch) -> None:
    task_dir = tmp_path / "draft"
    task_dir.mkdir()
    config = _agent_config(tmp_path)
    trials_dir = tmp_path / "oracle"
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        trial_dir = trials_dir / "oracle"
        _write_valid_trial(trial_dir, task_name="poc-agent/cover-tool", model="oracle")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "generation_benchmark.eval_author.harbor.subprocess.run",
        fake_run,
    )
    run_named_harbor_trial(
        task_dir,
        task_name="poc-agent/cover-tool",
        config=config,
        trials_dir=trials_dir,
        trial_name="oracle",
        harbor_agent="oracle",
        include_model=False,
    )
    command = captured["command"]
    assert "oracle" in command
    assert "--model" not in command
    assert command[command.index("--trial-name") + 1] == "oracle"
    assert command[command.index("--trials-dir") + 1] == str(trials_dir)


def test_run_named_harbor_trial_rejects_wrong_task_name(tmp_path: Path, monkeypatch) -> None:
    config = _agent_config(tmp_path)
    trials_dir = tmp_path / "repeat-1"
    _write_valid_trial(trials_dir / "repeat-1", task_name="other/task", model="oracle")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("generation_benchmark.eval_author.harbor.subprocess.run", fake_run)
    with pytest.raises(ExperimentError, match="task identity"):
        run_named_harbor_trial(
            tmp_path / "draft",
            task_name="poc-agent/cover-tool",
            config=config,
            trials_dir=trials_dir,
            trial_name="repeat-1",
            expected_model="oracle",
        )
