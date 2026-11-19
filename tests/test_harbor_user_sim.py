# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from harbor.models.job.config import JobConfig
from harbor.models.trajectories.trajectory import Trajectory
from harbor.trial.simulated_user import render_user_prompt

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
sys.path.insert(0, str(SCRIPTS))

from harbor_user_sim import NEUTRAL_PERSONA, HarborTranslationError, translate  # noqa: E402


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
    assert NEUTRAL_PERSONA.read_text() in prompt
    assert "{{ not_a_template_variable }}" in prompt
    assert "Public task context" in prompt
    assert "Give the order identifier when asked." in prompt
    assert "End after the reported return." in prompt
    assert "Confirm only the correct proposal." in prompt
    assert "same session" in prompt
    assert (scenario.read_bytes(), config.read_bytes()) == source_bytes
    assert result.working_directory == Path.cwd()
    assert result.to_dict()["runtime_ready"] is False
    assert result.input_digests == {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in result.input_paths
    }
    assert result.to_dict()["input_digests"] == dict(result.input_digests)
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
        {"source_jobs": [{"type": "local", "path": "old-job", "action": "regrade"}]},
        {"source_jobs": [{"type": "local", "path": "old-job", "action": "diff"}]},
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


@pytest.mark.parametrize("target", ["claude-code", "codex"])
@pytest.mark.parametrize("symlink", [False, True])
def test_task_trajectory_fails_before_writing(inputs: tuple, target: str, symlink: bool) -> None:
    scenario, config, output, data = inputs
    data["agents"][0]["name"] = target
    config.write_text(yaml.safe_dump(data))
    trajectory = Trajectory.model_validate(
        {
            "agent": {"name": target, "version": "test"},
            "steps": [{"step_id": 1, "source": "user", "message": "Earlier request"}],
        }
    )
    path = scenario.parent / "trajectory.json"
    if symlink:
        source = Path.cwd() / "prior.json"
        source.write_text(trajectory.model_dump_json())
        path.symlink_to(source)
    else:
        path.write_text(trajectory.model_dump_json())
    source_bytes = path.read_bytes(), config.read_bytes()
    with pytest.raises(HarborTranslationError, match=r"Task-provided trajectory\.json"):
        translate(scenario, config, output)
    assert not output.exists()
    assert (path.read_bytes(), config.read_bytes()) == source_bytes


@pytest.mark.parametrize("target", ["claude-code", "codex"])
def test_target_prompt_template_fails_before_writing(inputs: tuple, target: str) -> None:
    scenario, config, output, data = inputs
    template = Path.cwd() / "target.md"
    template.write_text("Apply the selected target policy. {{ instruction }}")
    data["agents"][0]["name"] = target
    data["agents"][0]["kwargs"] = {"prompt_template_path": str(template)}
    config.write_text(yaml.safe_dump(data))
    source_bytes = template.read_bytes(), config.read_bytes()
    with pytest.raises(HarborTranslationError, match="Target prompt_template_path"):
        translate(scenario, config, output)
    assert not output.exists()
    assert (template.read_bytes(), config.read_bytes()) == source_bytes


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
    Path("extra.md").write_text("Extra target context")
    for name in ("target-config.json", "user-config.json"):
        Path(name).write_text('{"instructions": "Role-specific behavior"}')
    Path("overlay.yaml").write_text("services: {}\n")
    Path("acpx.json").write_text('{"timeout": 17}')
    data["extra_instruction_paths"] = ["extra.md"]
    data["agents"][0]["kwargs"] = {
        "config": "target-config.json",
        "unchanged_option": "value",
    }
    data["user_agent"]["kwargs"] = {"config": "user-config.json"}
    data["environment"]["extra_docker_compose"] = ["overlay.yaml"]
    data["user_agent"]["bridge"] = {"kind": "acp", "kwargs": {"acpx_config_path": "acpx.json"}}
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    assert {
        Path(name).resolve()
        for name in ("extra.md", "acpx.json", "target-config.json", "user-config.json", "overlay.yaml")
    } <= set(result.input_paths)
    generated = yaml.safe_load(result.run_config_path.read_text())
    assert generated["agents"] == data["agents"]
    assert generated["extra_instruction_paths"] == ["extra.md"]
    assert generated["user_agent"]["bridge"]["kwargs"] == {"acpx_config_path": "acpx.json"}
    assert generated["user_agent"]["kwargs"] == data["user_agent"]["kwargs"]
    assert generated["environment"] == data["environment"]


@pytest.mark.parametrize("role", ["target", "simulator"])
@pytest.mark.parametrize("source", ["./local-skills", "example/skills@commit"])
def test_skill_sources_are_deferred_until_tree_binding_exists(inputs: tuple, role: str, source: str) -> None:
    scenario, config, output, data = inputs
    Path("local-skills").mkdir()
    selected = data["agents"][0] if role == "target" else data["user_agent"]
    selected["skills"] = [source]
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(HarborTranslationError, match="tree binding"):
        translate(scenario, config, output)
    assert not output.exists()


def test_inline_role_configs_remain_in_native_configuration(inputs: tuple) -> None:
    scenario, config, output, data = inputs
    data["agents"][0]["kwargs"] = {"config": {"policy": "target"}}
    data["user_agent"]["kwargs"] = {"config": {"policy": "user"}}
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    generated = yaml.safe_load(result.run_config_path.read_text())
    assert generated["agents"] == data["agents"]
    assert generated["user_agent"]["kwargs"] == data["user_agent"]["kwargs"]


def test_native_expansion_preserves_original_config_strings(inputs: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, config, output, data = inputs
    native_expanduser = Path.expanduser

    def expanduser(path: Path) -> Path:
        return Path.cwd().joinpath(*path.parts[1:]) if path.parts and path.parts[0] == "~" else native_expanduser(path)

    monkeypatch.setattr(Path, "expanduser", expanduser)
    Path("extra.md").write_text("Extra task instructions")
    Path("overlay.yaml").write_text("services: {}\n")
    data["tasks"][0]["path"] = "~/tasks/return-headphones"
    data["extra_instruction_paths"] = ["~/extra.md"]
    data["environment"]["extra_docker_compose"] = ["~/overlay.yaml"]
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    generated = yaml.safe_load(result.run_config_path.read_text())
    assert generated["tasks"] == data["tasks"]
    assert generated["extra_instruction_paths"] == ["~/extra.md"]
    assert generated["environment"] == data["environment"]
    assert {Path("extra.md").resolve(), Path("overlay.yaml").resolve()} <= set(result.input_paths)


def test_disabled_native_verification_does_not_require_test_script(inputs: tuple) -> None:
    scenario, config, output, data = inputs
    (scenario.parent / "tests/test.sh").unlink()
    data["verifier"]["disable"] = True
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    generated = yaml.safe_load(result.run_config_path.read_text())
    assert generated["verifier"] == data["verifier"]
    assert result.to_dict()["runtime_ready"] is False


@pytest.mark.parametrize("parent_alias", [False, True])
def test_runtime_input_alias_cannot_hide_retargeted_bytes(inputs: tuple, parent_alias: bool) -> None:
    scenario, config, output, data = inputs
    for name in ("old", "new"):
        directory = Path(name)
        directory.mkdir()
        (directory / "config.json").write_text(json.dumps({"policy": name}))
    alias = Path("selected")
    suffix = "" if parent_alias else "/config.json"
    alias.symlink_to(Path.cwd() / f"old{suffix}", target_is_directory=parent_alias)
    data["agents"][0]["kwargs"] = {"config": "selected/config.json" if parent_alias else "selected"}
    config.write_text(yaml.safe_dump(data))
    for destination in (f"old{suffix}", f"new{suffix}"):
        alias.unlink()
        alias.symlink_to(Path.cwd() / destination, target_is_directory=parent_alias)
        with pytest.raises(HarborTranslationError, match="symlink"):
            translate(scenario, config, output)
    assert not output.exists()


def test_external_directory_alias_stays_visible_in_input_inventory(inputs: tuple, tmp_path: Path) -> None:
    scenario, config, output, data = inputs
    outside = tmp_path.parent / f"{tmp_path.name}-external"
    outside.mkdir()
    target = tmp_path / "external-target"
    target.mkdir()
    (target / "config.json").write_text('{"policy": "one"}')
    alias = outside / "alias"
    alias.symlink_to(target, target_is_directory=True)
    declared = alias / "config.json"
    data["user_agent"]["kwargs"] = {"config": str(declared)}
    config.write_text(yaml.safe_dump(data))
    result = translate(scenario, config, output)
    assert declared in result.input_paths
    assert declared.resolve() not in result.input_paths


def test_native_task_alias_is_not_hidden_by_path_resolution(inputs: tuple) -> None:
    scenario, config, output, data = inputs
    Path("selected-task").symlink_to(scenario.parent, target_is_directory=True)
    data["tasks"][0]["path"] = "selected-task"
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(HarborTranslationError, match="symlink"):
        translate(scenario, config, output)
    assert not output.exists()


def test_translation_snapshot_stays_frozen_when_source_changes(inputs: tuple) -> None:
    scenario, config, output, _ = inputs
    result = translate(scenario, config, output)
    original_digest = result.input_digests[str(scenario)]
    saved = result.to_dict()
    changed = yaml.safe_load(scenario.read_text())
    changed["start_prompt"] = "A different opening request"
    scenario.write_text(yaml.safe_dump(changed))
    assert result.input_digests[str(scenario)] == original_digest
    assert result.input_digests[str(scenario)] != hashlib.sha256(scenario.read_bytes()).hexdigest()
    assert saved["input_digests"] == result.to_dict()["input_digests"]
    assert json.loads((output / "persona.txt").read_text())["start_prompt"] != changed["start_prompt"]
    with pytest.raises(TypeError):
        cast(dict[str, str], result.input_digests)[str(scenario)] = "new"


@pytest.mark.parametrize("input_name", ["scenario", "base", "external"])
def test_mixed_source_versions_fail_before_return(
    inputs: tuple, monkeypatch: pytest.MonkeyPatch, input_name: str
) -> None:
    scenario, config, output, data = inputs
    external = Path.cwd() / "acpx.json"
    external.write_text('{"timeout": 10}')
    data["user_agent"]["bridge"] = {"kind": "acp", "kwargs": {"acpx_config_path": str(external)}}
    config.write_text(yaml.safe_dump(data))
    selected = {"scenario": scenario, "base": config, "external": external}[input_name]
    original_write = Path.write_text

    def write_text(path: Path, text: str) -> int:
        result = original_write(path, text)
        if path == output / "run.yaml":
            original_write(selected, selected.read_text() + "\n")
        return result

    monkeypatch.setattr(Path, "write_text", write_text)
    with pytest.raises(HarborTranslationError, match="changed during"):
        translate(scenario, config, output)


@pytest.mark.parametrize("content", ['{"agents": {}}', '{"defaultAgent": "other"}', "[]"])
def test_acpx_cannot_replace_the_selected_target(inputs: tuple, content: str) -> None:
    scenario, config, output, original = inputs
    Path("acpx.json").write_text(content)
    original["user_agent"]["bridge"] = {"kind": "acp", "kwargs": {"acpx_config_path": "acpx.json"}}
    config.write_text(yaml.safe_dump(original))
    with pytest.raises(ValueError):
        translate(scenario, config, output)
    assert not output.exists()


@pytest.mark.parametrize("installed_version", ["0.9.0", "0.23.0", "0.24.0rc1"])
def test_older_harbor_version_is_explicit(
    inputs: tuple, monkeypatch: pytest.MonkeyPatch, installed_version: str
) -> None:
    scenario, config, output, _ = inputs
    monkeypatch.setattr("harbor_user_sim.version", lambda _: installed_version)
    with pytest.raises(HarborTranslationError, match=r"requires harbor >=0\.24\.0"):
        translate(scenario, config, output)


@pytest.mark.parametrize("installed_version", ["0.24.0", "0.24.1", "0.25.0", "0.100.0", "1.0.0"])
def test_compatible_harbor_versions_translate(
    inputs: tuple, monkeypatch: pytest.MonkeyPatch, installed_version: str
) -> None:
    scenario, config, output, _ = inputs
    monkeypatch.setattr("harbor_user_sim.version", lambda _: installed_version)
    result = translate(scenario, config, output)
    assert result.run_config_path.is_file()
    assert result.to_dict()["runtime_ready"] is False


def test_invalid_harbor_version_is_explicit(inputs: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario, config, output, _ = inputs
    monkeypatch.setattr("harbor_user_sim.version", lambda _: "invalid")
    with pytest.raises(HarborTranslationError, match="requires harbor"):
        translate(scenario, config, output)
    assert not output.exists()


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


@pytest.mark.parametrize("role", ["target", "simulator"])
@pytest.mark.parametrize("field", ["name", "import_path"])
@pytest.mark.parametrize("control", ["harbor.agents.nop:NopAgent", "harbor.agents.oracle:OracleAgent"])
def test_control_aliases_are_rejected(inputs: tuple, role: str, field: str, control: str) -> None:
    scenario, config, output, data = inputs
    if role == "target":
        data["agents"] = [{field: control}]
    else:
        data["user_agent"] = {field: control}
    config.write_text(yaml.safe_dump(data))
    with pytest.raises(HarborTranslationError, match="control"):
        translate(scenario, config, output)
    assert not output.exists()


@pytest.mark.parametrize("which", ["scenario", "config"])
@pytest.mark.parametrize("parent", [False, True])
def test_selected_inputs_reject_symlink_aliases(inputs: tuple, which: str, parent: bool) -> None:
    scenario, config, output, _ = inputs
    source = scenario if which == "scenario" else config
    alias = Path.cwd() / "alias"
    alias.symlink_to(source.parent if parent else source, target_is_directory=parent)
    selected = alias / source.name if parent else alias
    with pytest.raises(HarborTranslationError, match="symlink"):
        translate(selected if which == "scenario" else scenario, selected if which == "config" else config, output)
    assert not output.exists()


def test_control_alias_check_follows_native_name_precedence(inputs: tuple) -> None:
    scenario, config, output, data = inputs
    data["agents"][0]["import_path"] = "harbor.agents.oracle:OracleAgent"
    config.write_text(yaml.safe_dump(data))
    assert translate(scenario, config, output).run_config_path.is_file()
