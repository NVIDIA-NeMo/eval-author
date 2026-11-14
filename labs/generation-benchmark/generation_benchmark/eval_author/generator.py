# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Configuration and execution for the local task-generation agent."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from generation_benchmark.eval_author.ports.errors import ExperimentError

from .identity import bytes_digest, file_digest

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class GeneratorConfig:
    """Pinned local coding-agent command used to drive task-create."""

    path: Path
    name: str
    model: str
    command: tuple[str, ...]
    version_command: tuple[str, ...]
    required_env: tuple[str, ...]
    clear_env: tuple[str, ...]
    timeout_seconds: float
    version: str
    digest: str


@dataclass(frozen=True)
class GeneratorRun:
    """One completed local generator-agent subprocess."""

    returncode: int
    stdout: Path
    stderr: Path
    prompt: Path
    timed_out: bool


def load_generator_config(path: Path) -> GeneratorConfig:
    """Load a generator config and include the executable version in its identity."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ExperimentError(f"generator config does not exist: {resolved}")
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ExperimentError(f"cannot read generator config {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExperimentError("generator config must be an object")
    schema = payload.get("schema")
    if schema not in ("nemo.eval_author.generator.v1", "nemo.eval_author.experiment_2.generator.v1"):
        raise ExperimentError(
            "generator config schema must be nemo.eval_author.generator.v1 "
            "(legacy alias nemo.eval_author.experiment_2.generator.v1)"
        )
    name = _required_string(payload, "name")
    model = _required_string(payload, "model")
    command = _command(payload.get("command"), "command")
    version_command = _command(payload.get("version_command"), "version_command")
    required_env = _string_list(payload.get("required_env", []), "required_env")
    clear_env = _string_list(
        payload.get("clear_env", ["OPENAI_MODEL", "RHO_AGENT_MODEL"]), "clear_env"
    )
    try:
        timeout_seconds = float(payload.get("timeout_seconds", 7200))
    except (TypeError, ValueError) as exc:
        raise ExperimentError("generator timeout_seconds must be numeric") from exc
    if timeout_seconds <= 0:
        raise ExperimentError("generator timeout_seconds must be positive")
    completed = subprocess.run(version_command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise ExperimentError(f"generator version command failed: {detail}")
    version = completed.stdout.strip() or completed.stderr.strip()
    if not version:
        raise ExperimentError("generator version command returned no version")
    runtime_identity = {
        "config": file_digest(resolved),
        "name": name,
        "model": model,
        "command": command,
        "version_command": version_command,
        "version": version,
        "required_env": required_env,
        "clear_env": clear_env,
        "timeout_seconds": timeout_seconds,
    }
    digest = bytes_digest(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return GeneratorConfig(
        path=resolved,
        name=name,
        model=model,
        command=command,
        version_command=version_command,
        required_env=required_env,
        clear_env=clear_env,
        timeout_seconds=timeout_seconds,
        version=version,
        digest=digest,
    )


def run_generator(
    config: GeneratorConfig,
    *,
    workspace: Path,
    target_slug: str,
    prompt: str,
    logs_dir: Path,
) -> GeneratorRun:
    """Invoke the real local coding agent with the task-create instructions."""
    missing = [name for name in config.required_env if not os.environ.get(name)]
    if missing:
        raise ExperimentError(
            "generator config requires unset environment variables: " + ", ".join(missing)
        )
    logs_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = logs_dir / f"{target_slug}.prompt.md"
    stdout_path = logs_dir / f"{target_slug}.stdout.txt"
    stderr_path = logs_dir / f"{target_slug}.stderr.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    environment = os.environ.copy()
    for name in config.clear_env:
        environment.pop(name, None)
    command = [*config.command, "-C", str(workspace), "-"]
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=workspace,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
        returncode = completed.returncode
        stdout = _redact(completed.stdout, config.required_env)
        stderr = _redact(completed.stderr, config.required_env)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = _redact(_text(exc.stdout), config.required_env)
        stderr = _redact(_text(exc.stderr), config.required_env)
        stderr += f"\ngenerator timed out after {config.timeout_seconds} seconds\n"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return GeneratorRun(
        returncode=returncode,
        stdout=stdout_path,
        stderr=stderr_path,
        prompt=prompt_path,
        timed_out=timed_out,
    )


def _command(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ExperimentError(f"generator config {label} must be a non-empty list")
    if not all(isinstance(item, str) and item for item in value):
        raise ExperimentError(f"generator config {label} entries must be non-empty strings")
    return tuple(_expand(item) for item in value)


def _expand(value: str) -> str:
    missing = sorted({name for name in _VARIABLE.findall(value) if not os.environ.get(name)})
    if missing:
        raise ExperimentError("generator config references unset variables: " + ", ".join(missing))
    return _VARIABLE.sub(lambda match: os.environ[match.group(1)], value)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"generator config {key} must be a non-empty string")
    return value.strip()


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ExperimentError(f"generator config {label} must be a list of strings")
    return tuple(value)


def _redact(value: str, secret_names: tuple[str, ...]) -> str:
    for name in secret_names:
        secret = os.environ.get(name, "")
        if secret:
            value = value.replace(secret, f"<redacted:{name}>")
    return value


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
