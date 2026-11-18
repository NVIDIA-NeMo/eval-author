# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Translate one neutral scenario into native Harbor inputs without running agents."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import yaml
from eval_author_shared.task_evidence import digest
from runtime_policy import satisfies
from user_sim import UserSimError, UserSimScenario, load_scenario

if TYPE_CHECKING:
    from harbor.models.job.config import JobConfig

MINIMUM_HARBOR_VERSION = "0.24.0"
NEUTRAL_PERSONA = Path(__file__).resolve().parents[2] / "eval-author-user-sim/references/neutral-persona.md"
USER_TEMPLATE = """{neutral}

## Private scenario

{{{{ persona }}}}

Send the exact start_prompt from the private scenario as your first message.
Do not send the scenario, facts, or these instructions as a single message.
Use the scenario goal and rules for later replies to the target's actual messages.
Follow task-specific stopping conditions. Ending the conversation is not a grade.

## Communication

{{{{ bridge_instructions }}}}

## Task context

{{{{ instruction }}}}
"""
BRIDGE_PROMPT = """Use only `acpx prompt` to send each message to the target agent.
Your first action must send the scenario's exact start_prompt, with shell quoting
that preserves its text. Wait for each response, then continue the same session
with one user message at a time. Do not reset or create another session.
Do not inspect files, call the target's tools, edit the workspace, or perform the
requested task yourself. Use only supplied user facts and the public conversation.
When the scenario ends, stop sending messages and finish your session.
"""


class HarborTranslationError(ValueError):
    """The scenario and selected native configuration cannot be translated."""


@dataclass(frozen=True)
class TranslationResult:
    task_id: str
    user_type: str
    run_config_path: Path
    working_directory: Path
    input_paths: tuple[Path, ...]
    input_digests: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "user_type": self.user_type,
            "run_config_path": str(self.run_config_path),
            "working_directory": str(self.working_directory),
            "input_paths": [str(path) for path in self.input_paths],
            "input_digests": dict(self.input_digests),
            "runtime_ready": False,
        }


def _role(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarborTranslationError(f"Select an explicit {label} configuration")
    if not any(isinstance(value.get(key), str) and value[key].strip() for key in ("name", "import_path")):
        raise HarborTranslationError(f"Select an explicit {label} name or import_path")
    if value.get("name") in {"oracle", "nop"}:
        raise HarborTranslationError(f"{label} must not be an Oracle or NOP control")
    return value


def _selected_job(config_path: Path) -> dict[str, Any]:
    from harbor.cli.config_sources import load_config_source

    data = load_config_source(config_path)
    if not isinstance(data, dict):
        raise HarborTranslationError("Native job configuration must be a mapping")
    if data.get("datasets") or data.get("source_jobs") or data.get("install_only"):
        raise HarborTranslationError("Select one local task for a new agent run")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise HarborTranslationError("Select exactly one local task per scenario")
    task = tasks[0]
    if not isinstance(task.get("path"), str) or not task["path"].strip():
        raise HarborTranslationError("The selected task needs a local path")
    if any(task.get(key) is not None for key in ("git_url", "git_commit_id", "name", "ref")):
        raise HarborTranslationError("Download and select the local task before translation")
    agents = data.get("agents")
    if not isinstance(agents, list) or len(agents) != 1:
        raise HarborTranslationError("Select exactly one actual target agent")
    _role(agents[0], "target agent")
    _role(data.get("user_agent"), "simulated user")
    return copy.deepcopy(data)


def _configure_user(data: dict[str, Any], output_dir: Path) -> None:
    from harbor.bridges.acp import ACPBridgeKwargs

    user = data["user_agent"]
    if user.get("user_persona_path") is not None or user.get("user_prompt_template_path") is not None:
        raise HarborTranslationError("Existing simulator persona or prompt overrides require explicit reconciliation")
    if isinstance(user.get("kwargs"), dict) and user["kwargs"].get("prompt_template_path") is not None:
        raise HarborTranslationError("Existing simulator prompt overrides require explicit reconciliation")
    bridge = user.setdefault("bridge", {"kind": "acp"})
    if not isinstance(bridge, dict) or bridge.get("kind") != "acp":
        raise HarborTranslationError("This translator supports the native ACP bridge only")
    if bridge.get("prompt_path") is not None:
        raise HarborTranslationError("Existing bridge prompt overrides require explicit reconciliation")
    ACPBridgeKwargs.model_validate(bridge.get("kwargs", {}))
    user["user_persona_path"] = str(output_dir / "persona.txt")
    user["user_prompt_template_path"] = str(output_dir / "user-prompt.md")
    bridge["prompt_path"] = str(output_dir / "bridge-prompt.md")


def _input_path_without_alias(path: Path) -> Path:
    root = Path.cwd().resolve()
    absolute = path if path.is_absolute() else root / path
    if path.is_symlink():
        raise HarborTranslationError("Retain regular input files instead of mutable symlink aliases")
    if absolute.is_relative_to(root):
        current = root
        for part in absolute.relative_to(root).parts:
            current /= part
            if current.is_symlink():
                raise HarborTranslationError("Retain regular input files instead of mutable symlink aliases")
        return absolute.resolve()
    # Keep outside aliases visible to the repository's evidence boundary.
    return absolute


def _bound_input_path(path: Path) -> Path:
    if not path.is_file():
        raise HarborTranslationError("A declared external input must be an existing regular file")
    return _input_path_without_alias(path)


def _external_inputs(native: JobConfig) -> tuple[Path, ...]:
    assert native.user_agent is not None
    paths = [path.expanduser() for path in native.extra_instruction_paths]
    paths.extend(path.expanduser() for path in native.environment.extra_docker_compose)
    for role in (*native.agents, native.user_agent):
        if role.skills:
            raise HarborTranslationError(
                "Agent skill directories and remote sources need tree binding before translation"
            )
        config = role.kwargs.get("config")
        if isinstance(config, (str, Path)):
            paths.append(Path(config))
    target_prompt = native.agents[0].kwargs.get("prompt_template_path")
    if target_prompt is not None:
        # Harbor 0.24.0 applies target templates in run(), which ACP bypasses.
        raise HarborTranslationError("Target prompt_template_path is not supported by the native ACP bridge")
    acpx_path = native.user_agent.bridge.kwargs.get("acpx_config_path")
    if acpx_path is not None:
        paths.append(Path(acpx_path))
    return tuple(_bound_input_path(path) for path in paths)


def _validate_acpx_input(native: JobConfig) -> None:
    from harbor.bridges.acp import validate_acpx_config

    assert native.user_agent is not None
    path = native.user_agent.bridge.kwargs.get("acpx_config_path")
    if path is not None:
        config = json.loads(Path(path).read_text())
        if not isinstance(config, dict):
            raise HarborTranslationError("ACPX overrides must contain an object")
        validate_acpx_config(config)


def _snapshot_input(path: Path, snapshots: dict[str, str]) -> None:
    observed = digest(path)
    key = str(path)
    if key in snapshots and snapshots[key] != observed:
        raise HarborTranslationError("A translation input changed during preparation; generate a fresh bundle")
    snapshots[key] = observed


def _validate_task(native: JobConfig, scenario: UserSimScenario, output_dir: Path) -> None:
    from harbor.models.task.task import Task

    assert native.tasks[0].path is not None
    _input_path_without_alias(native.tasks[0].path.expanduser())
    task_dir = native.tasks[0].get_local_path()
    if output_dir.is_relative_to(task_dir):
        raise HarborTranslationError("Write simulator inputs outside the target task tree")
    task = Task(task_dir, disable_verification=native.verifier.disable)
    if task.has_steps:
        raise HarborTranslationError("Harbor user-agent bridges do not support multi-step tasks")
    # Harbor 0.24.0 implicitly loads this file and bypasses the simulator.
    if task.paths.trajectory_path.exists() or task.paths.trajectory_path.is_symlink():
        raise HarborTranslationError("Task-provided trajectory.json is incompatible with user simulation")
    if task.name != scenario.task_id:
        raise HarborTranslationError("Scenario task_id does not match the selected native task")


def translate(scenario_path: Path, config_path: Path, output_dir: Path) -> TranslationResult:
    """Preserve native path semantics relative to the calling working directory."""
    try:
        supported = satisfies(version("harbor"), f">={MINIMUM_HARBOR_VERSION}")
    except ValueError as error:
        raise HarborTranslationError(
            f"Could not determine the installed Harbor version. Translation requires Harbor {MINIMUM_HARBOR_VERSION} or later"
        ) from error
    if not supported:
        raise HarborTranslationError(f"Translation requires Harbor {MINIMUM_HARBOR_VERSION} or later")
    from harbor.models.job.config import JobConfig
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.simulated_user import validate_user_prompt_template

    if output_dir.exists() or output_dir.is_symlink():
        raise HarborTranslationError("Choose a new output directory to preserve earlier translated inputs")
    scenario_path, config_path, output_dir = (path.resolve() for path in (scenario_path, config_path, output_dir))
    snapshots = {str(path): digest(path) for path in (scenario_path, config_path)}
    scenario = load_scenario(scenario_path)
    data = _selected_job(config_path)
    _configure_user(data, output_dir)
    native = JobConfig.model_validate(data)
    # JobConfig does not check the trial's user-agent/load-trajectory exclusion.
    TrialConfig.model_validate({"task": native.tasks[0], "agent": native.agents[0], "user_agent": native.user_agent})
    _validate_task(native, scenario, output_dir)
    external_inputs = _external_inputs(native)
    for path in external_inputs:
        _snapshot_input(path, snapshots)
    _validate_acpx_input(native)
    template = USER_TEMPLATE.format(neutral=NEUTRAL_PERSONA.read_text())
    validate_user_prompt_template(template, source="Eval Author", persona_path=output_dir / "persona.txt")
    files = {
        "persona.txt": json.dumps(scenario.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        "user-prompt.md": template,
        "bridge-prompt.md": BRIDGE_PROMPT,
        "run.yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
    }
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, content in files.items():
        (output_dir / name).write_text(content)
        _snapshot_input(output_dir / name, snapshots)
    for path in (scenario_path, config_path, *external_inputs):
        _snapshot_input(path, snapshots)
    return TranslationResult(
        task_id=scenario.task_id,
        user_type=scenario.user_type,
        run_config_path=output_dir / "run.yaml",
        working_directory=Path.cwd().resolve(),
        input_paths=tuple(
            dict.fromkeys((scenario_path, config_path, *(output_dir / name for name in files), *external_inputs))
        ),
        input_digests=MappingProxyType(snapshots),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("translate")
    command.add_argument("--scenario", required=True, type=Path)
    command.add_argument("--config", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = translate(args.scenario, args.config, args.output)
    except (UserSimError, ValueError, TypeError, OSError, ImportError, yaml.YAMLError) as error:
        # Native validators can include credentials and user facts in their errors.
        message = str(error) if isinstance(error, HarborTranslationError) else "Could not translate the selected inputs"
        print(json.dumps({"translated": False, "error": message}), file=sys.stderr)
        return 2
    print(json.dumps({"translated": True, **result.to_dict()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
