# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harbor.models.job.config import JobConfig
from harbor.trial.simulated_user import render_user_prompt

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))

from harbor_user_sim import NEUTRAL_BEHAVIOR, HarborTranslationError, translate  # noqa: E402


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, dict]:
    monkeypatch.chdir(tmp_path)
    task = tmp_path / "tasks/return-headphones"
    (task / "tests").mkdir(parents=True)
    (task / "environment").mkdir()
    (task / "task.toml").write_text('version = "1.0"\n')
    (task / "instruction.md").write_text("Help the customer return their headphones.")
    (task / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    scenario = task / "user-sim.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "task_id": "return-headphones",
                "goal": "Return the customer's headphones.",
                "start_prompt": "Please return my headphones. {{ not_a_template_variable }}",
                "user_facts": {"order_id": "R31", "preference": "original payment"},
                "disclosure_rules": ["Give the order identifier when asked."],
                "conversation_prompt": "Confirm only the correct proposal.",
                "stopping_conditions": ["End after the reported return."],
            }
        )
    )
    config = tmp_path / "base.yaml"
    data = {
        "job_name": "neutral-return",
        "jobs_dir": "jobs",
        "n_attempts": 3,
        "n_concurrent_trials": 1,
        "tasks": [{"path": "tasks/return-headphones"}],
        "agents": [{"name": "claude-code", "model_name": "selected-target", "override_timeout_sec": 120}],
        "user_agent": {"name": "codex", "model_name": "selected-simulator", "env": {"TOKEN": "${TOKEN}"}},
        "environment": {"type": "docker", "force_build": True},
        "verifier": {"override_timeout_sec": 60},
    }
    config.write_text(yaml.safe_dump(data))
    return scenario, config, tmp_path / "private/run-one", data


def test_native_translation_preserves_selection_and_scenario(inputs: tuple) -> None:
    scenario, config, output, original = inputs
    source_bytes = scenario.read_bytes(), config.read_bytes()
    result = translate(scenario, config, output)
    generated = yaml.safe_load(result.run_config_path.read_text())
    native = JobConfig.model_validate(generated)
    for key, value in original.items():
        if key != "user_agent":
            assert generated[key] == value
    for key, value in original["user_agent"].items():
        assert generated["user_agent"][key] == value
    assert native.user_agent is not None
    assert native.user_agent.bridge.kind == "acp"
    assert json.loads((output / "persona.txt").read_text())["user_facts"] == {
        "order_id": "R31",
        "preference": "original payment",
    }
    prompt = render_user_prompt(
        "Public task context",
        (output / "bridge-prompt.md").read_text(),
        native.user_agent.user_prompt_template_path,
        native.user_agent.user_persona_path,
    )
    assert NEUTRAL_BEHAVIOR.read_text() in prompt
    assert "{{ not_a_template_variable }}" in prompt
    assert "Public task context" in prompt
    assert "Give the order identifier when asked." in prompt
    assert "End after the reported return." in prompt
    assert "Confirm only the correct proposal." in prompt
    assert "same session" in prompt
    assert (scenario.read_bytes(), config.read_bytes()) == source_bytes
    assert result.working_directory == Path.cwd()
    assert result.to_dict()["runtime_ready"] is False
    assert set(result.input_paths) == {
        scenario,
        config,
        *(output / name for name in ("persona.txt", "user-prompt.md", "bridge-prompt.md", "run.yaml")),
    }
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "change",
    [
        {"tasks": []},
        {"tasks": [{"path": "tasks/return-headphones"}, {"path": "tasks/another"}]},
        {"tasks": [{"name": "example/return-headphones"}]},
        {"tasks": [{"path": "tasks/return-headphones", "git_url": "https://example.invalid/repo"}]},
        {"datasets": [{"path": "tasks"}]},
        {"install_only": True},
        {"source_job": {"type": "local", "path": "old-job", "action": "regrade"}},
        {"agents": []},
        {"agents": [{}]},
        {"agents": [{"name": "oracle"}]},
        {"agents": [{"name": "nop"}]},
        {"agents": [{"name": "claude-code"}, {"name": "codex"}]},
        {"agents": [{"name": "claude-code", "load_trajectory": "prior.json"}]},
        {"user_agent": {}},
        {"user_agent": {"name": "nop"}},
        {"user_agent": {"name": "codex", "user_persona_path": "old.txt"}},
        {"user_agent": {"name": "codex", "user_prompt_template_path": "old.md"}},
        {"user_agent": {"name": "codex", "kwargs": {"prompt_template_path": "old.md"}}},
        {"user_agent": {"name": "codex", "bridge": {"kind": "unknown"}}},
        {"user_agent": {"name": "codex", "bridge": {"kind": "acp", "prompt_path": "old.md"}}},
        {"user_agent": {"name": "codex", "bridge": {"kind": "acp", "kwargs": {"start_prompt": "wrong"}}}},
    ],
)
def test_conflicting_or_unsupported_selection_fails_before_writing(inputs: tuple, change: dict) -> None:
    scenario, config, output, original = inputs
    config.write_text(yaml.safe_dump({**original, **change}))
    with pytest.raises(ValueError):
        translate(scenario, config, output)
    assert not output.exists()


def test_scenario_identity_and_multistep_are_checked(inputs: tuple) -> None:
    scenario, config, output, _ = inputs
    task_config = scenario.parent / "task.toml"
    task_config.write_text('version = "1.0"\n[task]\nname = "example/different-task"\n')
    with pytest.raises(ValueError, match="task_id"):
        translate(scenario, config, output)
    task_config.write_text('version = "1.0"\n[[steps]]\nname = "first"\n')
    step = scenario.parent / "steps/first"
    step.mkdir(parents=True)
    (step / "instruction.md").write_text("First request")
    (step / "tests").mkdir()
    (step / "tests/test.sh").write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="multi-step"):
        translate(scenario, config, output)
    assert not output.exists()


def test_private_inputs_cannot_be_written_into_task(inputs: tuple) -> None:
    scenario, config, _, _ = inputs
    with pytest.raises(HarborTranslationError, match="outside"):
        translate(scenario, config, scenario.parent / "environment/private")


def test_existing_output_and_symlink_are_not_overwritten(inputs: tuple) -> None:
    scenario, config, output, _ = inputs
    translate(scenario, config, output)
    saved = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(HarborTranslationError, match="new output"):
        translate(scenario, config, output)
    assert saved == {path.name: path.read_bytes() for path in output.iterdir()}
    link = output.parent / "output-link"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(HarborTranslationError, match="new output"):
        translate(scenario, config, link)
    dangling = output.parent / "dangling"
    dangling.symlink_to(output.parent / "missing", target_is_directory=True)
    with pytest.raises(HarborTranslationError, match="new output"):
        translate(scenario, config, dangling)
    assert not (output.parent / "missing").exists()


def test_external_inputs_remain_bound_and_preserved(inputs: tuple) -> None:
    scenario, config, output, original = inputs
    data = copy.deepcopy(original)
    for name in ("extra.md", "target.md"):
        Path(name).write_text("Extra target context")
    Path("acpx.json").write_text('{"timeout": 17}')
    data["extra_instruction_paths"] = ["extra.md"]
    data["agents"][0]["kwargs"] = {"prompt_template_path": "target.md", "unchanged_option": "value"}
    data["user_agent"]["bridge"] = {"kind": "acp", "kwargs": {"acpx_config_path": "acpx.json"}}
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    assert {Path(name).resolve() for name in ("extra.md", "target.md", "acpx.json")} <= set(result.input_paths)
    generated = yaml.safe_load(result.run_config_path.read_text())
    assert generated["agents"] == data["agents"]
    assert generated["extra_instruction_paths"] == ["extra.md"]
    assert generated["user_agent"]["bridge"]["kwargs"] == {"acpx_config_path": "acpx.json"}


@pytest.mark.parametrize("content", ['{"agents": {}}', '{"defaultAgent": "other"}', "[]"])
def test_acpx_cannot_replace_the_selected_target(inputs: tuple, content: str) -> None:
    scenario, config, output, original = inputs
    Path("acpx.json").write_text(content)
    original["user_agent"]["bridge"] = {"kind": "acp", "kwargs": {"acpx_config_path": "acpx.json"}}
    config.write_text(yaml.safe_dump(original))
    with pytest.raises(ValueError):
        translate(scenario, config, output)
    assert not output.exists()


def test_wrong_harbor_version_is_explicit(inputs: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, config, output, _ = inputs
    monkeypatch.setattr("harbor_user_sim.version", lambda _: "0.23.0")
    with pytest.raises(HarborTranslationError, match="0.24.0"):
        translate(scenario, config, output)


def test_cli_has_safe_summary_and_sanitized_errors(inputs: tuple) -> None:
    scenario, config, output, original = inputs
    command = [
        sys.executable,
        str(SCRIPTS / "harbor_user_sim.py"),
        "translate",
        "--scenario",
        str(scenario),
        "--config",
        str(config),
        "--output",
        str(output),
    ]
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0
    summary = json.loads(success.stdout)
    assert summary["translated"] and not summary["runtime_ready"]
    assert summary["run_config_path"] == str(output / "run.yaml")
    assert "R31" not in success.stdout
    original["user_agent"]["model_name"] = {"secret": "DO_NOT_ECHO_SECRET"}
    config.write_text(yaml.safe_dump(original))
    command[-1] = str(output.parent / "invalid")
    failure = subprocess.run(command, capture_output=True, text=True, check=False)
    assert failure.returncode == 2
    assert not json.loads(failure.stderr)["translated"]
    assert "DO_NOT_ECHO_SECRET" not in failure.stdout + failure.stderr
    config.write_text("user_agent: [DO_NOT_ECHO_YAML_SECRET\n")
    malformed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert malformed.returncode == 2
    assert not json.loads(malformed.stderr)["translated"]
    assert "DO_NOT_ECHO_YAML_SECRET" not in malformed.stdout + malformed.stderr
