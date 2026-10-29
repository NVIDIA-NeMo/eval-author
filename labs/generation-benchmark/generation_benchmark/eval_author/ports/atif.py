# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Convert pre-ATIF Harbor trajectories to canonical ATIF v1.7."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from harbor.models.trajectories import Trajectory

from .errors import ExperimentError
from .io import prepare_output, write_json


def is_atif(payload: object) -> bool:
    """Return whether a JSON payload declares an ATIF v1 schema."""
    return isinstance(payload, dict) and str(payload.get("schema_version", "")).startswith(
        "ATIF-v1."
    )


def load_json_object(raw: bytes, *, source: str) -> dict[str, Any]:
    """Load one UTF-8 JSON object with a useful source label."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read trajectory JSON from {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExperimentError(f"trajectory JSON from {source} must be an object")
    return payload


def validate_atif(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Validate and normalize a payload with Harbor's current ATIF model."""
    try:
        model = Trajectory.model_validate(payload)
    except ValueError as exc:
        raise ExperimentError(f"{source} is not valid ATIF: {exc}") from exc
    return model.model_dump(mode="json", exclude_none=True)


def convert_legacy_harbor(
    payload: dict[str, Any],
    *,
    source: str,
    agent_name: str = "unknown-agent",
    agent_version: str = "legacy-harbor",
    session_id: str | None = None,
    trajectory_id: str | None = None,
) -> dict[str, Any]:
    """Convert the trajectory shape emitted before Harbor's ATIF export."""
    if is_atif(payload):
        return validate_atif(payload, source=source)

    legacy_steps = payload.get("steps")
    if not isinstance(legacy_steps, list) or not legacy_steps:
        raise ExperimentError(f"legacy Harbor trajectory from {source} has no non-empty steps list")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]

    steps = [
        _convert_step(step, index=index, source=source)
        for index, step in enumerate(legacy_steps, start=1)
    ]
    model_name = metadata.get("model")
    agent: dict[str, Any] = {"name": agent_name, "version": agent_version}
    if isinstance(model_name, str) and model_name:
        agent["model_name"] = model_name

    converted: dict[str, Any] = {
        "schema_version": "ATIF-v1.7",
        "session_id": session_id or f"legacy-{digest}",
        "trajectory_id": trajectory_id or f"legacy-{digest}",
        "agent": agent,
        "steps": steps,
        "final_metrics": _final_metrics(metadata, len(steps)),
        "extra": {
            "conversion": {
                "source_format": "legacy-harbor",
                "source": source,
            },
            "legacy_metadata": metadata,
        },
    }
    return validate_atif(converted, source=f"converted {source}")


def convert_file(
    source: Path,
    *,
    output: Path,
    agent_name: str,
    agent_version: str,
    session_id: str | None,
    trajectory_id: str | None,
    force: bool,
) -> dict[str, Any]:
    """Convert one file without replacing the source or an existing output."""
    resolved_source = source.expanduser().resolve()
    if not resolved_source.is_file():
        raise ExperimentError(f"trajectory does not exist: {resolved_source}")
    resolved_output = prepare_output(output, force=force)
    if resolved_source == resolved_output:
        raise ExperimentError("converted ATIF output must not replace its input")
    raw = resolved_source.read_bytes()
    payload = load_json_object(raw, source=str(resolved_source))
    already_atif = is_atif(payload)
    converted = convert_legacy_harbor(
        payload,
        source=str(resolved_source),
        agent_name=agent_name,
        agent_version=agent_version,
        session_id=session_id,
        trajectory_id=trajectory_id,
    )
    write_json(resolved_output, converted)
    return {
        "input": str(resolved_source),
        "output": str(resolved_output),
        "already_atif": already_atif,
        "schema_version": converted["schema_version"],
        "steps": len(converted["steps"]),
    }


def _convert_step(step: object, *, index: int, source: str) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ExperimentError(f"legacy Harbor step {index} from {source} must be an object")
    actor = step.get("source")
    if actor not in {"system", "user", "agent"}:
        raise ExperimentError(
            f"legacy Harbor step {index} from {source} has unsupported source {actor!r}"
        )
    message = step.get("message", "")
    if not isinstance(message, (str, list)):
        message = str(message)
    converted: dict[str, Any] = {"step_id": index, "source": actor, "message": message}

    tool_calls = step.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            raise ExperimentError(f"legacy Harbor step {index} tool_calls must be a list")
        converted["tool_calls"] = [
            _convert_tool_call(call, step_index=index, call_index=call_index)
            for call_index, call in enumerate(tool_calls, start=1)
        ]

    observations = step.get("observations")
    if observations is not None:
        if not isinstance(observations, list):
            raise ExperimentError(f"legacy Harbor step {index} observations must be a list")
        converted["observation"] = {
            "results": [
                _convert_observation(observation, step_index=index) for observation in observations
            ]
        }
    metrics = step.get("metrics")
    if isinstance(metrics, dict):
        converted["metrics"] = metrics

    known = {"source", "message", "tool_calls", "observations", "metrics"}
    extra = {key: value for key, value in step.items() if key not in known}
    if extra:
        converted["extra"] = {"legacy_fields": extra}
    return converted


def _convert_tool_call(call: object, *, step_index: int, call_index: int) -> dict[str, Any]:
    if not isinstance(call, dict):
        raise ExperimentError(f"legacy Harbor step {step_index} tool call must be an object")
    name = call.get("name") or call.get("function_name")
    if not isinstance(name, str) or not name:
        raise ExperimentError(f"legacy Harbor step {step_index} tool call has no name")
    call_id = (
        call.get("call_id") or call.get("tool_call_id") or f"step-{step_index}-call-{call_index}"
    )
    arguments = call.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {"legacy_value": arguments}
    known = {"name", "function_name", "call_id", "tool_call_id", "arguments"}
    extra = {key: value for key, value in call.items() if key not in known}
    converted: dict[str, Any] = {
        "tool_call_id": str(call_id),
        "function_name": name,
        "arguments": arguments,
    }
    if extra:
        converted["extra"] = {"legacy_fields": extra}
    return converted


def _convert_observation(observation: object, *, step_index: int) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {"content": str(observation)}
    source_call_id = observation.get("source_call_id")
    content = observation.get("content")
    converted: dict[str, Any] = {}
    if source_call_id is not None:
        converted["source_call_id"] = str(source_call_id)
    if isinstance(content, (str, list)) or content is None:
        converted["content"] = content
    else:
        converted["content"] = json.dumps(content, sort_keys=True)
    known = {"source_call_id", "content"}
    extra = {key: value for key, value in observation.items() if key not in known}
    if extra:
        converted["extra"] = {"legacy_fields": extra, "legacy_step": step_index}
    return converted


def _final_metrics(metadata: dict[str, Any], total_steps: int) -> dict[str, Any]:
    field_map = {
        "total_input_tokens": "total_prompt_tokens",
        "total_output_tokens": "total_completion_tokens",
        "total_cached_tokens": "total_cached_tokens",
        "total_cost_usd": "total_cost_usd",
    }
    metrics = {
        destination: metadata[source]
        for source, destination in field_map.items()
        if metadata.get(source) is not None
    }
    metrics["total_steps"] = total_steps
    return metrics
