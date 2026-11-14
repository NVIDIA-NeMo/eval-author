# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run and validate real Harbor trials for missing suite traces."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from generation_benchmark.eval_author.ports.atif import is_atif, load_json_object, validate_atif
from generation_benchmark.eval_author.ports.errors import ExperimentError

from .identity import bytes_digest, file_digest, tree_digest

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_FAILURE_MARKERS = (
    "authenticationerror",
    "incorrect api key",
    "key_model_access_denied",
    "badrequesterror",
    "ratelimiterror",
    "apierror:",
    "traceback (most recent call last)",
)


@dataclass(frozen=True)
class AgentConfig:
    """Pinned Harbor invocation used for freshly collected traces."""

    path: Path
    name: str
    model: str
    trace_agent_label: str
    command: tuple[str, ...]
    cwd: Path | None
    agent: str
    environment: str
    agent_kwargs: tuple[tuple[str, str], ...]
    env_passthrough: tuple[str, ...]
    clear_env: tuple[str, ...]
    timeout_seconds: float
    setup_timeout_seconds: float
    source_digest: str | None
    digest: str


@dataclass(frozen=True)
class HarborTrace:
    """A validated trajectory emitted by a completed Harbor trial."""

    trial_dir: Path
    trajectory: Path
    result: Path
    model: str
    reward: float | None


def load_agent_config(path: Path | None) -> AgentConfig | None:
    """Load the explicit YAML/JSON contract for missing-trace Harbor runs."""
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ExperimentError(f"agent config does not exist: {resolved}")
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ExperimentError(f"cannot read agent config {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExperimentError("agent config must be an object")
    schema = payload.get("schema")
    if schema not in ("nemo.eval_author.agent.v1", "nemo.eval_author.experiment_2.agent.v1"):
        raise ExperimentError(
            "agent config schema must be nemo.eval_author.agent.v1 "
            "(legacy alias nemo.eval_author.experiment_2.agent.v1)"
        )
    name = _required_string(payload, "name")
    model = _required_string(payload, "model")
    harbor = payload.get("harbor")
    if not isinstance(harbor, dict):
        raise ExperimentError("agent config harbor field must be an object")
    raw_command = harbor.get("command")
    if not isinstance(raw_command, list) or not raw_command:
        raise ExperimentError("agent config harbor.command must be a non-empty list")
    if not all(isinstance(item, str) and item for item in raw_command):
        raise ExperimentError("agent config harbor.command entries must be non-empty strings")
    command = tuple(_expand(item) for item in raw_command)
    agent = _required_string(harbor, "agent")
    environment = str(harbor.get("environment", "docker")).strip()
    if not environment:
        raise ExperimentError("agent config harbor.environment must not be empty")
    raw_kwargs = harbor.get("agent_kwargs", {})
    if not isinstance(raw_kwargs, dict):
        raise ExperimentError("agent config harbor.agent_kwargs must be an object")
    agent_kwargs = tuple(
        (str(key), _expand(_scalar(value, f"agent_kwargs.{key}")))
        for key, value in sorted(raw_kwargs.items())
    )
    env_passthrough = _string_list(harbor.get("env_passthrough", []), "env_passthrough")
    clear_env = _string_list(
        harbor.get("clear_env", ["OPENAI_MODEL", "RHO_AGENT_MODEL"]), "clear_env"
    )
    raw_cwd = harbor.get("cwd")
    cwd = Path(_expand(raw_cwd)).expanduser().resolve() if isinstance(raw_cwd, str) else None
    if cwd is not None and not cwd.is_dir():
        raise ExperimentError(f"agent config Harbor cwd does not exist: {cwd}")
    try:
        timeout_seconds = float(harbor.get("agent_timeout_seconds", 7200))
    except (TypeError, ValueError) as exc:
        raise ExperimentError("agent_timeout_seconds must be numeric") from exc
    if timeout_seconds <= 0:
        raise ExperimentError("agent_timeout_seconds must be positive")
    try:
        setup_timeout_seconds = float(harbor.get("agent_setup_timeout_seconds", 1200))
    except (TypeError, ValueError) as exc:
        raise ExperimentError("agent_setup_timeout_seconds must be numeric") from exc
    if setup_timeout_seconds <= 0:
        raise ExperimentError("agent_setup_timeout_seconds must be positive")
    trace_agent_label = str(payload.get("trace_agent_label", name)).strip()
    if not trace_agent_label:
        raise ExperimentError("trace_agent_label must not be empty")
    source_dir_value = dict(agent_kwargs).get("source_dir")
    source_digest = (
        tree_digest(Path(source_dir_value))
        if source_dir_value and Path(source_dir_value).is_dir()
        else None
    )
    runtime_identity = {
        "config": file_digest(resolved),
        "name": name,
        "model": model,
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "agent": agent,
        "environment": environment,
        "agent_kwargs": agent_kwargs,
        "env_passthrough": env_passthrough,
        "clear_env": clear_env,
        "timeout_seconds": timeout_seconds,
        "setup_timeout_seconds": setup_timeout_seconds,
        "source_digest": source_digest,
    }
    digest = bytes_digest(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return AgentConfig(
        path=resolved,
        name=name,
        model=model,
        trace_agent_label=trace_agent_label,
        command=command,
        cwd=cwd,
        agent=agent,
        environment=environment,
        agent_kwargs=agent_kwargs,
        env_passthrough=env_passthrough,
        clear_env=clear_env,
        timeout_seconds=timeout_seconds,
        setup_timeout_seconds=setup_timeout_seconds,
        source_digest=source_digest,
        digest=digest,
    )


def run_harbor_trial(
    task_dir: Path,
    *,
    task_name: str,
    config: AgentConfig,
    trials_dir: Path,
) -> HarborTrace:
    """Run one Harbor trial and reject false-success or unusable outputs."""
    run_identity = bytes_digest(
        json.dumps([task_name, tree_digest(task_dir), config.digest]).encode("utf-8")
    )
    base_trial_name = f"{_trial_name(task_name)}-{run_identity[-12:]}"
    previous_trials = [
        trials_dir / base_trial_name,
        *sorted(trials_dir.glob(f"{base_trial_name}-retry-*")),
    ]
    previous_trials = [path for path in previous_trials if path.is_dir()]
    previous_errors: list[str] = []
    for previous in previous_trials:
        try:
            return validate_harbor_trial(previous, expected_model=config.model)
        except ExperimentError as exc:
            previous_errors.append(f"{previous.name}: {exc}")
    missing = [name for name in config.env_passthrough if not os.environ.get(name)]
    if missing:
        raise ExperimentError(
            "agent config requires unset environment variables: " + ", ".join(missing)
        )
    trial_name = (
        base_trial_name
        if not previous_trials
        else f"{base_trial_name}-retry-{len(previous_trials):03d}"
    )
    trial_dir = trials_dir / trial_name
    trials_dir.mkdir(parents=True, exist_ok=True)
    command = [
        *config.command,
        "trial",
        "start",
        "--path",
        str(task_dir),
        "--agent",
        config.agent,
        "--model",
        config.model,
        "--env",
        config.environment,
        "--agent-timeout",
        str(config.timeout_seconds),
        "--agent-setup-timeout",
        str(config.setup_timeout_seconds),
        "--trial-name",
        trial_name,
        "--trials-dir",
        str(trials_dir),
    ]
    for key, value in config.agent_kwargs:
        command.extend(("--agent-kwarg", f"{key}={value}"))
    for name in config.env_passthrough:
        value = os.environ.get(name)
        if value is None:
            continue
        command.extend(("--agent-env", f"{name}={value}"))
    environment = os.environ.copy()
    for name in config.clear_env:
        environment.pop(name, None)
    completed = subprocess.run(
        command,
        cwd=config.cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    receipt = {
        "schema": "nemo.eval_author.experiment_2.harbor_run.v1",
        "task": task_name,
        "returncode": completed.returncode,
        "command": _redact_command(command, config.env_passthrough),
        "stdout": _redact_text(completed.stdout, config.env_passthrough),
        "stderr": _redact_text(completed.stderr, config.env_passthrough),
        "rejected_previous_trials": previous_errors,
    }
    receipt_path = trials_dir / f"{trial_name}.worker-receipt.json"
    from generation_benchmark.eval_author.ports.io import write_json

    write_json(receipt_path, receipt)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        detail = _redact_text(detail, config.env_passthrough)
        raise ExperimentError(
            f"Harbor failed for {task_name} with exit {completed.returncode}: {detail[-2000:]}"
        )
    return validate_harbor_trial(trial_dir, expected_model=config.model)


def harbor_trial_command(
    task_dir: Path,
    config: AgentConfig,
    *,
    trials_dir: Path,
    trial_name: str,
    harbor_agent: str | None = None,
    include_model: bool = True,
    extra_agent_kwargs: tuple[tuple[str, str], ...] = (),
    extra_agent_env: tuple[tuple[str, str], ...] = (),
) -> list[str]:
    """Build a ``harbor trial start`` argv list for task-evidence recording."""
    resolved_agent = harbor_agent or config.agent
    command = [
        *config.command,
        "trial",
        "start",
        "--path",
        str(task_dir),
        "--agent",
        resolved_agent,
        "--env",
        config.environment,
        "--agent-timeout",
        str(config.timeout_seconds),
        "--agent-setup-timeout",
        str(config.setup_timeout_seconds),
        "--trial-name",
        trial_name,
        "--trials-dir",
        str(trials_dir),
    ]
    if include_model:
        command.extend(["--model", config.model])
    merged_kwargs = dict(config.agent_kwargs)
    for key, value in extra_agent_kwargs:
        merged_kwargs[key] = value
    for key, value in sorted(merged_kwargs.items()):
        command.extend(("--agent-kwarg", f"{key}={value}"))
    for name in config.env_passthrough:
        value = os.environ.get(name)
        if value is None:
            continue
        command.extend(("--agent-env", f"{name}={value}"))
    for key, value in extra_agent_env:
        command.extend(("--agent-env", f"{key}={value}"))
    return command


def run_named_harbor_trial(
    task_dir: Path,
    *,
    task_name: str,
    config: AgentConfig,
    trials_dir: Path,
    trial_name: str,
    harbor_agent: str | None = None,
    include_model: bool = True,
    expected_model: str | None = None,
    extra_agent_kwargs: tuple[tuple[str, str], ...] = (),
) -> HarborTrace:
    """Run Harbor with a fixed ``trials_dir`` / ``trial_name`` layout for generation proofs."""
    resolved_agent = harbor_agent or config.agent
    model_check = expected_model if expected_model is not None else config.model
    trial_dir = trials_dir / trial_name
    if trial_dir.is_dir():
        try:
            if resolved_agent == "oracle":
                return _validate_named_oracle_trial(trial_dir, task_name=task_name)
            return _validate_named_repeat_trial(
                trial_dir, expected_model=model_check, task_name=task_name
            )
        except ExperimentError:
            pass
    needs_inference_env = include_model and resolved_agent not in ("oracle", "nop")
    if needs_inference_env:
        missing = [name for name in config.env_passthrough if not os.environ.get(name)]
        if missing:
            raise ExperimentError(
                "agent config requires unset environment variables: " + ", ".join(missing)
            )
    trials_dir.mkdir(parents=True, exist_ok=True)
    command = harbor_trial_command(
        task_dir,
        config,
        trials_dir=trials_dir,
        trial_name=trial_name,
        harbor_agent=harbor_agent,
        include_model=include_model,
        extra_agent_kwargs=extra_agent_kwargs,
    )
    environment = os.environ.copy()
    for name in config.clear_env:
        environment.pop(name, None)
    completed = subprocess.run(
        command,
        cwd=config.cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    receipt = {
        "schema": "nemo.eval_author.experiment_2.harbor_run.v1",
        "task": task_name,
        "trial_name": trial_name,
        "trials_dir": str(trials_dir),
        "returncode": completed.returncode,
        "command": _redact_command(command, config.env_passthrough),
        "stdout": _redact_text(completed.stdout, config.env_passthrough),
        "stderr": _redact_text(completed.stderr, config.env_passthrough),
    }
    from generation_benchmark.eval_author.ports.io import write_json

    write_json(trials_dir / f"{trial_name}.worker-receipt.json", receipt)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        detail = _redact_text(detail, config.env_passthrough)
        raise ExperimentError(
            f"Harbor failed for {task_name} ({trial_name}) with exit {completed.returncode}: "
            f"{detail[-2000:]}"
        )
    if resolved_agent == "oracle":
        return _validate_named_oracle_trial(trial_dir, task_name=task_name)
    return _validate_named_repeat_trial(
        trial_dir, expected_model=model_check, task_name=task_name
    )


def _validate_named_oracle_trial(trial_dir: Path, *, task_name: str) -> HarborTrace:
    trace = validate_harbor_trial(trial_dir, expected_model=None)
    try:
        result = json.loads(trace.result.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read Oracle result {trace.result}: {exc}") from exc
    if result.get("task_name") != task_name:
        raise ExperimentError("Oracle task identity differs from the draft")
    if trace.reward != 1.0:
        raise ExperimentError(f"Oracle verifier reward is {trace.reward!r}, expected 1.0")
    return trace


def _validate_named_repeat_trial(
    trial_dir: Path, *, expected_model: str, task_name: str
) -> HarborTrace:
    trace = validate_harbor_trial(trial_dir, expected_model=expected_model)
    try:
        result = json.loads(trace.result.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read repeat result {trace.result}: {exc}") from exc
    if result.get("task_name") != task_name:
        raise ExperimentError("repeat task identity differs from the draft")
    if trace.reward != 1.0:
        raise ExperimentError(f"repeat verifier reward is {trace.reward!r}, expected 1.0")
    return trace


def validate_harbor_trial(trial_dir: Path, *, expected_model: str | None) -> HarborTrace:
    """Require a healthy result, real agent step, and provider-clean logs."""
    resolved = trial_dir.expanduser().resolve()
    result_path = resolved / "result.json"
    trajectory_path = resolved / "agent" / "trajectory.json"
    if not result_path.is_file() or not trajectory_path.is_file():
        raise ExperimentError("Harbor trial is missing result.json or agent/trajectory.json")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read Harbor result {result_path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ExperimentError("Harbor result must be an object")
    if result.get("exception_info") is not None:
        raise ExperimentError(f"Harbor recorded an exception: {result['exception_info']}")
    raw = trajectory_path.read_bytes()
    payload = load_json_object(raw, source=str(trajectory_path))
    if is_atif(payload):
        payload = validate_atif(payload, source=str(trajectory_path))
    elif not isinstance(payload.get("steps"), list):
        raise ExperimentError("Harbor trajectory is neither ATIF nor legacy Harbor JSON")
    agent_steps = [step for step in payload.get("steps", []) if step.get("source") == "agent"]
    if not agent_steps:
        raise ExperimentError("Harbor trajectory has no agent step")
    found_markers: list[str] = []
    for path in (resolved / "agent" / "stdout.txt", resolved / "trial.log"):
        if not path.is_file():
            continue
        lowered = path.read_text(encoding="utf-8", errors="replace").lower()
        found_markers.extend(marker for marker in _FAILURE_MARKERS if marker in lowered)
    if found_markers:
        raise ExperimentError(
            "Harbor logs contain provider/runtime failure markers: "
            + ", ".join(sorted(set(found_markers)))
        )
    model_info = (result.get("agent_info") or {}).get("model_info") or {}
    model = str(model_info.get("name", "")).strip()
    if expected_model and _effective_model(expected_model) != _effective_model(model):
        raise ExperimentError(
            f"Harbor result model {model!r} does not match configured model {expected_model!r}"
        )
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    raw_reward = rewards.get("reward")
    reward = float(raw_reward) if isinstance(raw_reward, (int, float)) else None
    return HarborTrace(
        trial_dir=resolved,
        trajectory=trajectory_path,
        result=result_path,
        model=model,
        reward=reward,
    )


def _effective_model(model: str) -> str:
    value = model.strip()
    if value.startswith("openai/"):
        return value.split("/", 1)[1]
    return value


def _trial_name(task_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", task_name).strip("-.") or "task"
    import hashlib

    suffix = hashlib.sha256(task_name.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:70]}-{suffix}"


def _redact_command(command: list[str], secret_names: tuple[str, ...]) -> list[str]:
    return [_redact_text(argument, secret_names) for argument in command]


def _redact_text(value: str, secret_names: tuple[str, ...]) -> str:
    for name in secret_names:
        secret = os.environ.get(name, "")
        if secret:
            value = value.replace(secret, f"<redacted:{name}>")
    return value


def _expand(value: str) -> str:
    missing = sorted({name for name in _VARIABLE.findall(value) if not os.environ.get(name)})
    if missing:
        raise ExperimentError("agent config references unset variables: " + ", ".join(missing))
    return _VARIABLE.sub(lambda match: os.environ[match.group(1)], value)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"agent config {key} must be a non-empty string")
    return value.strip()


def _scalar(value: object, label: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ExperimentError(f"agent config {label} must be a scalar")


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ExperimentError(f"agent config harbor.{label} must be a list of strings")
    return tuple(value)
