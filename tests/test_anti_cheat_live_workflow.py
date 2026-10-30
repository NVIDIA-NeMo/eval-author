# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    path = ROOT / ".github/workflows/anti-cheat-live.yml"
    assert path.is_file(), "missing separate live anti-cheat workflow"
    return yaml.safe_load(path.read_text())


def test_live_experiment_automatically_runs_on_main_like_semantic_regression():
    config = workflow()
    triggers = config.get("on", config.get(True))
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["workflow_dispatch"]["inputs"]["run_live"]["default"] is False
    assert "pull_request_target" not in triggers
    live = config["jobs"]["live"]
    assert live["needs"] == "plan"
    assert "github.ref == 'refs/heads/main'" in live["if"]
    assert "github.event_name == 'push'" in live["if"]
    assert "github.event_name == 'schedule'" in live["if"]
    semantic = yaml.safe_load((ROOT / ".github/workflows/semantic-regression.yml").read_text())
    assert " ".join(live["if"].split()) == " ".join(semantic["jobs"]["live"]["if"].split())
    assert "vars." not in live["if"]
    assert "github.event_name == 'workflow_dispatch' && inputs.run_live" in live["if"]
    assert live["environment"] == "semantic-regression"
    assert live["continue-on-error"] is True
    assert live["timeout-minutes"] <= 60
    assert config["permissions"] == {"contents": "read"}
    assert config["concurrency"]["cancel-in-progress"] is False


def test_plan_has_no_credentials_and_live_publishes_only_allowlisted_summaries():
    config = workflow()
    plan = config["jobs"]["plan"]
    assert "secrets." not in str(plan)
    assert any("--mode plan" in step.get("run", "") for step in plan["steps"])
    live = config["jobs"]["live"]
    steps = live["steps"]
    assert all(
        step["with"]["persist-credentials"] is False
        for step in steps
        if step.get("uses", "").startswith("actions/checkout@")
    )
    run = next(step for step in steps if "--mode live" in step.get("run", ""))
    assert run["env"] == {"INFERENCE_HUB_API_KEY": "${{ secrets.INFERENCE_HUB_API_KEY }}"}
    assert '--private-output "$RUNNER_TEMP/anti-cheat-private"' in run["run"]
    assert not any("secrets." in str(step) for step in steps if step is not run)
    install_index = next(i for i, step in enumerate(steps) if step.get("run") == "uv sync --locked")
    fallback_index = next(i for i, step in enumerate(steps) if "--mode plan" in step.get("run", ""))
    assert fallback_index < install_index
    for job in config["jobs"].values():
        upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        assert upload["if"] == "always()"
        assert upload["with"]["if-no-files-found"] == "error"
        assert upload["with"]["retention-days"] == 90
        paths = upload["with"]["path"].splitlines()
        assert len(paths) == 2
        assert paths[0].endswith("/anti-cheat-live-summary.json")
        assert paths[1].endswith("/anti-cheat-live-summary.md")
        assert all("private" not in path and "*" not in path for path in paths)
