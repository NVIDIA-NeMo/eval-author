# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a saved starter configuration without starting provider execution."""

import asyncio
from pathlib import Path

import yaml
from harbor.job import Job
from harbor.models.job.config import JobConfig


def test_run_yaml_selects_only_agreed_tasks_and_preserves_attempts(tmp_path):
    selected = ["coding-request", "missing-repository"]
    for name in [*selected, "unrelated-draft"]:
        (tmp_path / ".eval-author/tasks" / name).mkdir(parents=True)
    run_config = tmp_path / ".eval-author/run.yaml"
    run_config.write_text(
        yaml.safe_dump(
            {
                "tasks": [{"path": str(tmp_path / ".eval-author/tasks" / name)} for name in selected],
                "datasets": [],
                "agents": [{"name": "terminus-2", "model_name": "configured-target", "override_timeout_sec": 180}],
                "n_attempts": 2,
                "jobs_dir": str(tmp_path / ".eval-author/jobs"),
            }
        )
    )
    config = JobConfig.model_validate(yaml.safe_load(run_config.read_text()))
    resolved = asyncio.run(Job._resolve_task_configs(config))
    assert [task.get_local_path().name for task in resolved] == selected
    assert config.n_attempts == 2
    assert config.agents[0].name == "terminus-2"
    assert config.agents[0].model_name == "configured-target"
    assert config.agents[0].override_timeout_sec == 180
    assert not (tmp_path / ".eval-author/jobs").exists()

    reference = (
        Path(__file__).resolve().parents[1] / "skills/eval-author-first-eval/references/harbor-first-eval.md"
    ).read_text()
    assert ".eval-author/run.yaml" in reference
    assert "first-eval.yaml" not in reference
