#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline conversion of one Gym Responses record. Never fetch embedded paths/URLs.

Prefer --source-atif for Harbor-backed records. Otherwise NeMo Compass loads the
record and exports ATIF. Output is private evidence, not publication approval,
proof, or a claim of lossless round-trip conversion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_ATIF_BYTES = 25 * 1024 * 1024
RECEIPT_SCHEMA = "nemo.eval_author.gym_to_atif.v2"
AGENT_NAME = "unknown-gym-agent"
AGENT_VERSION = "unknown"
ATIF_VERSION = re.compile(r"ATIF-v1\.[0-7]")


def _validate_message(message: Any, *, location: str) -> tuple[int, bool]:
    """Same message boundary eval-author-trace-environment's prepare enforces."""
    if isinstance(message, str):
        return 0, bool(message.strip())
    if not isinstance(message, list):
        raise ConversionError(f"{location} must be text or a list of ATIF content parts")
    image_count = 0
    has_text = False
    for index, part in enumerate(message):
        if not isinstance(part, dict):
            raise ConversionError(f"{location}[{index}] must be an object")
        part_type = part.get("type")
        text = part.get("text")
        if part_type == "text" and isinstance(text, str):
            has_text = has_text or bool(text.strip())
        elif part_type == "image" and isinstance(part.get("source"), dict):
            image_count += 1
        else:
            raise ConversionError(f"{location}[{index}] is not a supported text or image content part")
    return image_count, has_text


def _validate_trajectory(payload: Any, *, location: str = "trajectory", depth: int = 0) -> dict[str, Any]:
    """Same structural boundary eval-author-trace-environment's prepare enforces.

    Kept as a local copy so this skill stands alone when copied without its
    siblings; dev tests additionally validate projected ATIF against Harbor's
    own models.
    """
    if depth > 8:
        raise ConversionError("embedded ATIF trajectories exceed the depth limit of 8")
    if not isinstance(payload, dict):
        raise ConversionError(f"{location} must be an object")
    version = payload.get("schema_version")
    if not isinstance(version, str) or ATIF_VERSION.fullmatch(version) is None:
        raise ConversionError(f"{location}.schema_version must identify ATIF v1.x")
    agent = payload.get("agent")
    if not isinstance(agent, dict) or not isinstance(agent.get("name"), str) or not agent["name"].strip():
        raise ConversionError(f"{location}.agent.name must be nonempty text")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ConversionError(f"{location}.steps must be a nonempty list")

    image_count = 0
    image_only_user_steps: list[int] = []
    user_step_count = 0
    for index, step in enumerate(steps, start=1):
        step_location = f"{location}.steps[{index - 1}]"
        if not isinstance(step, dict):
            raise ConversionError(f"{step_location} must be an object")
        if step.get("step_id") != index:
            raise ConversionError(f"{step_location}.step_id must be sequential from 1")
        source = step.get("source")
        if source not in {"user", "agent", "system"}:
            raise ConversionError(f"{step_location}.source must be user, agent, or system")
        if source == "user":
            user_step_count += 1
        step_images, has_text = _validate_message(step.get("message", ""), location=f"{step_location}.message")
        image_count += step_images
        if source == "user" and step_images and not has_text:
            image_only_user_steps.append(index)

        calls = step.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ConversionError(f"{step_location}.tool_calls must be a list")
        call_ids: set[str] = set()
        for call_index, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ConversionError(f"{step_location}.tool_calls[{call_index}] must be an object")
            call_id = call.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id or call_id in call_ids:
                raise ConversionError(f"{step_location} has a missing or duplicate tool_call_id")
            call_ids.add(call_id)
        observation = step.get("observation")
        if observation is not None:
            if not isinstance(observation, dict):
                raise ConversionError(f"{step_location}.observation must be an object")
            results = observation.get("results", [])
            if not isinstance(results, list):
                raise ConversionError(f"{step_location}.observation.results must be a list")
            for result in results:
                if not isinstance(result, dict):
                    raise ConversionError(f"{step_location}.observation results must be objects")
                source_call_id = result.get("source_call_id")
                if source_call_id is not None and source_call_id not in call_ids:
                    raise ConversionError(
                        f"{step_location} observation references unknown tool call {source_call_id!r}"
                    )
                result_images, _ = _validate_message(result.get("content", ""), location="observation content")
                image_count += result_images

    subagents = payload.get("subagent_trajectories") or []
    if not isinstance(subagents, list):
        raise ConversionError(f"{location}.subagent_trajectories must be a list")
    for index, subagent in enumerate(subagents):
        child = _validate_trajectory(subagent, location=f"{location}.subagent_trajectories[{index}]", depth=depth + 1)
        image_count += child["image_count"]
        image_only_user_steps.extend(child["image_only_user_steps"])
    if depth == 0 and user_step_count == 0:
        raise ConversionError("trajectory must contain at least one root user step")
    return {"image_count": image_count, "image_only_user_steps": image_only_user_steps}


class ConversionError(ValueError):
    """Content-free conversion failure."""


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConversionError("duplicate JSON keys are unsupported")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ConversionError("non-finite JSON numbers are unsupported")


def decode(data: bytes | str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ConversionError("source must be complete, bounded UTF-8 JSON; select --row for JSONL") from error


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def read_source(path: Path, row: int | None = None) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ConversionError("source must be an explicitly supplied regular file")
    if row is None:
        if path.suffix.lower() == ".jsonl":
            raise ConversionError("JSONL requires an explicit one-based --row; episodes are never merged")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ConversionError("source exceeds the raw-byte limit")
        data = path.read_bytes()
    else:
        if row < 1:
            raise ConversionError("--row must be positive")
        with path.open("rb") as stream:
            for _ in range(row):
                data = stream.readline(MAX_SOURCE_BYTES + 1)
                if not data:
                    raise ConversionError("selected JSONL row does not exist")
                if len(data) > MAX_SOURCE_BYTES:
                    raise ConversionError("JSONL record exceeds the raw-byte limit")
    if len(data) > MAX_SOURCE_BYTES or not data.strip():
        raise ConversionError("source is empty or exceeds the raw-byte limit")
    return data


def envelope(record: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(record, dict):
        raise ConversionError("one Gym rollout/response object is required")
    if "response" in record:
        response = record["response"]
        request = record.get("responses_create_params")
        if not isinstance(request, dict):
            raise ConversionError("rollout requires responses_create_params")
    else:
        response, request = record, {}
        if record.get("object") != "response":
            raise ConversionError("expected a Gym rollout envelope or standalone Responses object")
    if not isinstance(response, dict) or not isinstance(response.get("output"), list):
        raise ConversionError("Gym response.output must be an array")
    return request, response


def _trace_loading() -> ModuleType:
    """Import the shared Compass loader from the sibling eval-author skill."""
    scripts = str(Path(__file__).resolve().parents[2] / "eval-author/scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    try:
        import trace_loading
    except ModuleNotFoundError as exc:
        if exc.name != "trace_loading":
            raise
        raise SystemExit(
            "Incomplete skill installation: install the sibling eval-author skill alongside gym-to-atif."
        ) from None
    return trace_loading


def project(source: Path) -> tuple[dict[str, Any], list[str], str]:
    """Load one private Gym record file through NeMo Compass and export ATIF."""
    trace_loading = _trace_loading()
    try:
        (trace,) = trace_loading.load_traces(source, "gym", max_traces=1)
        payload = trace_loading.export_atif(trace, agent_name=AGENT_NAME, agent_version=AGENT_VERSION)
    except trace_loading.TraceLoadError as error:
        raise ConversionError(str(error)) from None
    except ValueError as error:
        from trace_ingest.exporters.atif import ATIFExportError
        from trace_ingest.loaders.gym import GymTraceLoadError

        # Compass load/export messages are structural; pydantic validation text can echo values.
        if isinstance(error, (ATIFExportError, GymTraceLoadError)):
            reason = str(error).replace(str(source), source.name)
            raise ConversionError(f"NeMo Compass rejected the Gym record: {reason}") from None
        raise ConversionError("NeMo Compass could not load or export the Gym record") from None
    losses = payload["extra"]["compass"]["loss_codes"]
    return payload, list(losses), trace_loading.COMPASS_VERSION


def normalize(
    source: Path, source_atif: bytes | None = None, *, allow_projection: bool = False, selected_line: int | None = None
) -> tuple[bytes, dict[str, Any]]:
    """Convert the exact record retained at ``source``; return ATIF bytes and the receipt."""
    record_bytes = source.read_bytes()
    if len(record_bytes) > MAX_SOURCE_BYTES or (source_atif is not None and len(source_atif) > MAX_SOURCE_BYTES):
        raise ConversionError("source exceeds the raw-byte limit")
    record = decode(record_bytes)
    envelope(record)
    bridge = record.get("atif_conversion")
    if bridge is not None:
        if not isinstance(bridge, dict):
            raise ConversionError("atif_conversion metadata must be an object")
        for field in ("trajectories", "source_trajectory_paths"):
            if field in bridge and not isinstance(bridge[field], list):
                raise ConversionError("atif_conversion trajectory metadata must be arrays")
    # No path from atif_conversion is ever dereferenced. Only a caller-supplied
    # --source-atif can authorize reading an original trajectory.
    loader_version = None
    if source_atif is not None:
        payload = decode(source_atif)
        if not isinstance(payload, dict):
            raise ConversionError("supplied source ATIF must be an object")
        descriptions = bridge.get("trajectories", []) if isinstance(bridge, dict) else []
        known_ids = {
            d.get("session_id") for d in descriptions if isinstance(d, dict) and isinstance(d.get("session_id"), str)
        }
        if known_ids and payload.get("session_id") not in known_ids:
            raise ConversionError("supplied ATIF session does not match advertised Gym trajectory metadata")
        canonical = source_atif
        basis = "provided_atif"
        losses: list[str] = []
    else:
        if bridge is not None and not allow_projection:
            raise ConversionError(
                "Gym advertises source ATIF; supply --source-atif or explicitly opt into --allow-projection"
            )
        if isinstance(bridge, dict) and (
            len(bridge.get("trajectories", [])) > 1 or len(bridge.get("source_trajectory_paths", [])) > 1
        ):
            raise ConversionError("multi-step Harbor projection is ambiguous; explicitly supply one original ATIF")
        payload, losses, loader_version = project(source)
        canonical = encode(payload)
        if len(canonical) > MAX_ATIF_BYTES:
            raise ConversionError("projected ATIF exceeds the canonical byte limit")
        basis = "gym_projection"
    # Same structural boundary prepare enforces; dev tests also validate against Harbor.
    try:
        _validate_trajectory(payload)
    except (ValueError, TypeError, RecursionError):
        raise ConversionError("ATIF structural validation failed; inspect the private source evidence") from None
    return canonical, {
        "schema": RECEIPT_SCHEMA,
        "basis": basis,
        "source_kind": "atif" if source_atif is not None else "gym",
        "gym_sha256": digest(record_bytes),
        "atif_sha256": digest(canonical),
        "source_atif_sha256": digest(source_atif) if source_atif is not None else None,
        "atif_bytes_preserved": source_atif is not None,
        "lossless_round_trip_claimed": False,
        "losses": losses,
        "media_fetched": False,
        "selected_line": selected_line,
        "loader_version": loader_version,
    }


def write_output(output: Path, raw: bytes, convert: Callable[[Path], tuple[bytes, dict[str, Any]]]) -> dict[str, Any]:
    """Retain ``raw`` as source.gym.json, convert that file, and write ATIF plus receipt.

    Nothing is left behind when any step fails.
    """
    if output.exists() or output.is_symlink():
        raise ConversionError("output directory already exists; use a new private directory")
    old_umask = os.umask(0o077)
    written: list[Path] = []
    created = False

    def write(name: str, data: bytes) -> Path:
        path = output / name
        with path.open("xb") as stream:
            written.append(path)
            stream.write(data)
        return path

    try:
        output.mkdir(parents=True, mode=0o700)
        created = True
        atif, receipt = convert(write("source.gym.json", raw))
        write("trace.atif.json", atif)
        write("conversion.json", encode(receipt))
        return receipt
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        if created:
            output.rmdir()
        raise
    finally:
        os.umask(old_umask)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--row", type=int, help="One-based physical JSONL line; never merges records")
    parser.add_argument(
        "--source-atif", type=Path, help="Explicit original trajectory; never read from embedded path metadata"
    )
    parser.add_argument(
        "--allow-projection",
        action="store_true",
        help="Allow a lossy Gym projection when a source ATIF is advertised but unavailable",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = read_source(args.input, args.row)
        original = read_source(args.source_atif) if args.source_atif else None
        receipt = write_output(
            args.output_dir,
            raw,
            lambda source: normalize(source, original, allow_projection=args.allow_projection, selected_line=args.row),
        )
        print(
            json.dumps(
                {
                    "status": "normalized",
                    "basis": receipt["basis"],
                    "source_kind": receipt["source_kind"],
                    "output_dir": str(args.output_dir),
                    "loss_count": len(receipt["losses"]),
                }
            )
        )
        return 0
    except (ConversionError, OSError, ValueError, TypeError, RecursionError) as error:
        # Source values, token strings, URLs, and decoded provider errors stay private.
        message = str(error) if isinstance(error, ConversionError) else "normalization failed structural or file checks"
        print(json.dumps({"status": "error", "message": message}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
