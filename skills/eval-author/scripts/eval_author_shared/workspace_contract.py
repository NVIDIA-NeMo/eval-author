# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared artifact contracts and integrity checks; no CLI or execution orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .fixture_compiler import (  # noqa: E402
    DECISIONS_SCHEMA,
    derive_tool_call_inventory,
    derive_tool_call_plan,
    resolve_tool_access,
)
from .verifier_lint import uses_submission_capture  # noqa: E402

SCHEMA = "nemo.eval_author.trace_environment_summary.v2"

QUALIFIED_SUMMARY_SCHEMA = "nemo.eval_author.trace_environment_summary.v3"

VALIDATION_SCHEMA = "nemo.eval_author.trace_environment_validation.v6"

RUN_INPUT_SCHEMA = "nemo.eval_author.trace_environment_run_input.v1"

QUALIFIED_RUN_INPUT_SCHEMA = "nemo.eval_author.trace_environment_run_input.v2"

CANONICAL_RUN_INPUT_SCHEMA = "nemo.eval_author.trace_environment_run_input.v3"

QUALIFIED_VALIDATION_SCHEMA = "nemo.eval_author.trace_environment_validation.v7"

PROBE_SCHEMA = "nemo.eval_author.trace_environment_probes.v1"

REPAIR_SCHEMA = "nemo.eval_author.trace_environment_repair.v1"

REPRODUCIBILITY_SCHEMA = "nemo.eval_author.trace_environment_reproducibility.v3"

MAX_CANONICAL_BYTES = 25 * 1024 * 1024

TASK_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

ATIF_VERSION = re.compile(r"ATIF-v1\.[0-7]")

_CHECK_ROW = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)\t(PASS|FAIL)")

_REPAIR_REASON_CODES = frozenset(
    {
        "verifier_defect",
        "environment_build_failure",
        "instruction_ambiguity",
        "oracle_failure",
        "nop_contamination",
        "negative_control_failure",
        "probe_mismatch",
        "runtime_incompatible",
        "other",
    }
)

_MAX_REPAIRS = 3

_MAX_PROBES_PER_REVISION = 2

_SUMMARY_KEYS = frozenset(
    {
        "schema",
        "task_id",
        "status",
        "source",
        "privacy",
        "candidate",
        "environment",
        "worked_well",
        "did_not_work",
        "reasons",
    }
)

_SOURCE_KEYS = frozenset(
    {
        "kind",
        "original_path",
        "original_sha256",
        "original_size_bytes",
        "canonical_path",
        "canonical_sha256",
        "canonical_size_bytes",
        "safe_path",
        "safe_sha256",
        "safe_size_bytes",
        "normalizations",
    }
)

_PRIVACY_KEYS = frozenset(
    {
        "path",
        "audit_path",
        "deterministic_redactions",
        "images_omitted",
        "contextual_review_required",
        "contextual_review_complete",
        "reviewer_kind",
        "blocking_reasons",
    }
)

_ENVIRONMENT_KEYS = frozenset(
    {
        "path",
        "status",
        "technical_status",
        "review_status",
        "validation",
        "verifier_environment_mode",
        "isolation_status",
    }
)

_TASK_README_SECTIONS = (
    "Difficulty explanation",
    "Environment and software requirements",
    "Ground-truth provenance",
    "Solution explanation",
    "Verification explanation",
    "Relevant experience",
)

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

_TASK_CHECKSUM = re.compile(r"[0-9a-f]{64}")

_IMAGE_DIGEST = re.compile(r".+@sha256:[0-9a-f]{64}$")

_PRIVATE_KEY_BYTES = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")

_BEARER_BYTES = re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")

_CREDENTIALED_URL_BYTES = re.compile(rb"(?i)https?://[^\s/:@]+:[^\s/@]+@")

_MIN_VALIDATION_RUNS = {"nop": 2, "oracle": 2, "negative": 1, "copy": 0}


class ContractError(ValueError):
    """A source or generated artifact violates the skill contract."""


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return f"sha256:{hashlib.file_digest(stream, 'sha256').hexdigest()}"


def _chmod_private(path: Path, *, directory: bool = False) -> None:
    if os.name == "posix":
        path.chmod(0o700 if directory else 0o600)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_private(path, directory=True)


def _write_bytes_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    _chmod_private(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        _chmod_private(temporary)
        os.replace(temporary, path)
        _chmod_private(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, *, label: str, max_bytes: int = MAX_CANONICAL_BYTES) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"{label} must be a regular file: {path}")
    size = path.stat().st_size
    if size > max_bytes:
        raise ContractError(f"{label} exceeds the {max_bytes}-byte limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"could not read {label} as UTF-8 JSON: {error}") from error


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = _load_json(path, label=label)
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must contain one JSON object")
    return payload


def _summary_path(task_dir: Path) -> Path:
    return task_dir / "summary.json"


def _load_summary(task_dir: Path) -> dict[str, Any]:
    summary = _load_object(_summary_path(task_dir), label="summary")
    marker = task_dir / "private/workspace.json"
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or _load_object(marker, label="workspace marker") != {
            "schema": "nemo.eval_author.workspace.v1",
            "summary_schema": QUALIFIED_SUMMARY_SCHEMA,
        }:
            raise ContractError("invalid workspace creation marker")
        if summary.get("schema") != QUALIFIED_SUMMARY_SCHEMA:
            raise ContractError("workspace creation marker forbids summary schema downgrade")
    if set(summary) != _SUMMARY_KEYS:
        raise ContractError("summary fields do not match the versioned contract")
    if summary.get("schema") not in (SCHEMA, QUALIFIED_SUMMARY_SCHEMA):
        raise ContractError(f"summary.schema must be {SCHEMA!r}")
    if summary.get("task_id") != task_dir.name:
        raise ContractError("summary task_id does not match its directory")
    if summary.get("status") not in ("pending", "candidate", "no_candidate"):
        raise ContractError("summary status is not recognized")
    for name in ("worked_well", "did_not_work", "reasons"):
        value = summary.get(name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ContractError(f"summary.{name} must be a list of strings")
    source = summary.get("source")
    if source is not None and (
        not isinstance(source, dict)
        or set(source) != _SOURCE_KEYS
        or source.get("kind") not in ("atif", "intake", "mlflow", "otel", "gym")
        or source.get("original_path") != "private/source.atif.json"
        or source.get("canonical_path") != "private/canonical.atif.json"
        or source.get("safe_path") != "safe/trace.atif.json"
        or any(
            not isinstance(source.get(name), str) or _DIGEST.fullmatch(source[name]) is None
            for name in ("original_sha256", "canonical_sha256", "safe_sha256")
        )
        or any(
            type(source.get(name)) is not int or source[name] < 0
            for name in ("original_size_bytes", "canonical_size_bytes", "safe_size_bytes")
        )
        or not isinstance(source.get("normalizations"), list)
        or any(not isinstance(item, dict) for item in source["normalizations"])
    ):
        raise ContractError("summary source does not match the versioned task workspace contract")
    privacy = summary.get("privacy")
    if privacy is not None and (
        not isinstance(privacy, dict)
        or set(privacy) != _PRIVACY_KEYS
        or privacy.get("path") != "safe/privacy.json"
        or privacy.get("audit_path") != "private/privacy-audit.json"
        or not isinstance(privacy.get("deterministic_redactions"), dict)
        or not isinstance(privacy.get("images_omitted"), int)
        or privacy.get("contextual_review_required") is not True
        or not isinstance(privacy.get("contextual_review_complete"), bool)
        or privacy.get("reviewer_kind") not in (None, "agent", "human")
        or not isinstance(privacy.get("blocking_reasons"), list)
    ):
        raise ContractError("summary privacy data does not match the versioned task workspace contract")
    environment = summary.get("environment")
    if (
        not isinstance(environment, dict)
        or set(environment)
        != (
            _ENVIRONMENT_KEYS | {"qualification"}
            if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA
            else _ENVIRONMENT_KEYS
        )
        or environment.get("path") != "task"
        or environment.get("status") not in ("ready", "failed", "unproven", "not_attempted")
        or environment.get("technical_status") not in ("passed", "failed", "not_run")
        or environment.get("review_status") not in ("human_reviewed", "unreviewed")
        or environment.get("validation") not in (None, "validation.json")
        or environment.get("verifier_environment_mode") not in (None, "separate")
        or environment.get("isolation_status") not in ("isolated", "not_run")
    ):
        raise ContractError("summary environment does not match the versioned task workspace contract")
    if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA and environment["qualification"] not in (
        "not_run",
        "not_applicable",
        "passed",
        "failed",
    ):
        raise ContractError("invalid construction qualification status")
    if privacy is not None and source is None:
        raise ContractError("summary privacy data requires prepared source evidence")
    return summary


def _ensure_task_dir(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ContractError(f"task directory must be a regular directory: {path}")
    if TASK_ID.fullmatch(path.name) is None:
        raise ContractError("task directory name must be lowercase kebab-case")
    return path.resolve()


def _validate_message(message: Any, *, location: str) -> tuple[int, bool]:
    if isinstance(message, str):
        return 0, bool(message.strip())
    if not isinstance(message, list):
        raise ContractError(f"{location} must be text or a list of ATIF content parts")
    image_count = 0
    has_text = False
    for index, part in enumerate(message):
        if not isinstance(part, dict):
            raise ContractError(f"{location}[{index}] must be an object")
        part_type = part.get("type")
        text = part.get("text")
        if part_type == "text" and isinstance(text, str):
            has_text = has_text or bool(text.strip())
        elif part_type == "image" and isinstance(part.get("source"), dict):
            image_count += 1
        else:
            raise ContractError(f"{location}[{index}] is not a supported text or image content part")
    return image_count, has_text


def _validate_trajectory(payload: Any, *, location: str = "trajectory", depth: int = 0) -> dict[str, Any]:
    if depth > 8:
        raise ContractError("embedded ATIF trajectories exceed the depth limit of 8")
    if not isinstance(payload, dict):
        raise ContractError(f"{location} must be an object")
    version = payload.get("schema_version")
    if not isinstance(version, str) or ATIF_VERSION.fullmatch(version) is None:
        raise ContractError(f"{location}.schema_version must identify ATIF v1.x")
    agent = payload.get("agent")
    if not isinstance(agent, dict) or not isinstance(agent.get("name"), str) or not agent["name"].strip():
        raise ContractError(f"{location}.agent.name must be nonempty text")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ContractError(f"{location}.steps must be a nonempty list")

    image_count = 0
    image_only_user_steps: list[int] = []
    user_step_count = 0
    for index, step in enumerate(steps, start=1):
        step_location = f"{location}.steps[{index - 1}]"
        if not isinstance(step, dict):
            raise ContractError(f"{step_location} must be an object")
        if step.get("step_id") != index:
            raise ContractError(f"{step_location}.step_id must be sequential from 1")
        source = step.get("source")
        if source not in {"user", "agent", "system"}:
            raise ContractError(f"{step_location}.source must be user, agent, or system")
        if source == "user":
            user_step_count += 1
        step_images, has_text = _validate_message(step.get("message", ""), location=f"{step_location}.message")
        image_count += step_images
        if source == "user" and step_images and not has_text:
            image_only_user_steps.append(index)

        calls = step.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ContractError(f"{step_location}.tool_calls must be a list")
        call_ids: set[str] = set()
        for call_index, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ContractError(f"{step_location}.tool_calls[{call_index}] must be an object")
            call_id = call.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id or call_id in call_ids:
                raise ContractError(f"{step_location} has a missing or duplicate tool_call_id")
            call_ids.add(call_id)
        observation = step.get("observation")
        if observation is not None:
            if not isinstance(observation, dict):
                raise ContractError(f"{step_location}.observation must be an object")
            results = observation.get("results", [])
            if not isinstance(results, list):
                raise ContractError(f"{step_location}.observation.results must be a list")
            for result in results:
                if not isinstance(result, dict):
                    raise ContractError(f"{step_location}.observation results must be objects")
                source_call_id = result.get("source_call_id")
                if source_call_id is not None and source_call_id not in call_ids:
                    raise ContractError(f"{step_location} observation references unknown tool call {source_call_id!r}")
                result_images, _ = _validate_message(result.get("content", ""), location="observation content")
                image_count += result_images

    subagents = payload.get("subagent_trajectories") or []
    if not isinstance(subagents, list):
        raise ContractError(f"{location}.subagent_trajectories must be a list")
    for index, subagent in enumerate(subagents):
        child = _validate_trajectory(subagent, location=f"{location}.subagent_trajectories[{index}]", depth=depth + 1)
        image_count += child["image_count"]
        image_only_user_steps.extend(child["image_only_user_steps"])
    if depth == 0 and user_step_count == 0:
        raise ContractError("trajectory must contain at least one root user step")
    return {"image_count": image_count, "image_only_user_steps": image_only_user_steps}


def _tool_call_source(task_dir: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    summary = _load_summary(task_dir)
    source = summary.get("source")
    if not isinstance(source, dict):
        raise ContractError("prepare safe ATIF evidence before inventorying interactions")
    safe_path = task_dir / source["safe_path"]
    if safe_path.is_symlink() or not safe_path.is_file():
        raise ContractError("safe ATIF must be a retained regular file")
    if safe_path.stat().st_size > MAX_CANONICAL_BYTES:
        raise ContractError(f"safe ATIF exceeds the {MAX_CANONICAL_BYTES}-byte limit")
    with safe_path.open("rb") as stream:
        safe_bytes = stream.read(MAX_CANONICAL_BYTES + 1)
    if len(safe_bytes) > MAX_CANONICAL_BYTES:
        raise ContractError(f"safe ATIF exceeds the {MAX_CANONICAL_BYTES}-byte limit")
    safe_sha256 = _sha256(safe_bytes)
    if safe_sha256 != source["safe_sha256"]:
        raise ContractError("safe ATIF digest does not match the task summary")
    try:
        safe = json.loads(safe_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("could not read safe ATIF as UTF-8 JSON") from error
    if not isinstance(safe, dict):
        raise ContractError("safe ATIF must contain one JSON object")
    _validate_trajectory(safe)
    return summary, safe, safe_sha256


def _current_tool_call_inventory(task_dir: Path) -> dict[str, Any]:
    _, safe, safe_sha256 = _tool_call_source(task_dir)
    return derive_tool_call_inventory(safe, safe_atif_sha256=safe_sha256)


def _current_tool_call_inventory_file(task_dir: Path) -> dict[str, Any]:
    inventory = _load_object(task_dir / "private/tool-call-inventory.json", label="tool-call inventory")
    if inventory != _current_tool_call_inventory(task_dir):
        raise ContractError("tool-call-inventory.json differs from the current safe ATIF")
    return inventory


def _current_tool_call_plan(task_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = _current_tool_call_inventory_file(task_dir)
    plan = _load_object(task_dir / "private/tool-call-plan.json", label="tool-call plan")
    overrides_path = task_dir / "private/tool-schema-overrides.json"
    overrides = _load_object(overrides_path, label="reviewed schema overrides") if overrides_path.exists() else None
    try:
        expected = derive_tool_call_plan(inventory, overrides)
    except ValueError as error:
        raise ContractError(str(error)) from error
    if plan != expected:
        raise ContractError("tool-call-plan.json differs from the current tool-call inventory")
    return inventory, plan


def _current_tool_access(task_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    inventory, plan = _current_tool_call_plan(task_dir)
    access = _load_object(task_dir / "private/tool-access.json", label="tool access")
    decisions = [
        {key: decision.get(key) for key in ("tool_id", "name", "access", "adapter", "note")}
        for decision in access.get("decisions", [])
        if isinstance(decision, dict)
    ]
    requested = {"schema": DECISIONS_SCHEMA, "decisions": decisions}
    reviewer_kind = access.get("reviewer_kind")
    if not isinstance(reviewer_kind, str):
        raise ContractError("tool-access.json reviewer_kind must be agent or human")
    try:
        expected = resolve_tool_access(
            inventory,
            plan,
            requested,
            reviewer_kind=reviewer_kind,
        )
    except ValueError as error:
        raise ContractError(f"tool-access.json is invalid: {error}") from error
    if access != expected:
        raise ContractError("tool-access.json differs from the current tool-call plan")
    return inventory, plan, access


def _validate_mock_tool_call_integration(task_dir: Path, config: dict[str, Any]) -> None:
    access_path = task_dir / "private/tool-access.json"
    if not access_path.exists():
        return
    _, _, access = _current_tool_access(task_dir)
    mocked = [decision for decision in access["decisions"] if decision["access"] == "mock"]
    if not mocked:
        return
    environment = config.get("environment")
    servers = environment.get("mcp_servers") if isinstance(environment, dict) else None
    if not isinstance(servers, list):
        raise ContractError("mock tool-call access requires [[environment.mcp_servers]] in task/task.toml")
    matching = [
        server for server in servers if isinstance(server, dict) and server.get("name") == "trace-tool-call-replay"
    ]
    if len(matching) != 1:
        raise ContractError("mock tool-call access requires exactly one trace-tool-call-replay MCP server")
    server = matching[0]
    if (
        server.get("transport") != "stdio"
        or server.get("command") != "/opt/tool-call-fixtures/launch-replay.sh"
        or server.get("args") != []
    ):
        raise ContractError("trace-tool-call-replay MCP server differs from the generated integration contract")


def _task_tree_info(root: Path) -> dict[str, Any]:
    """Hash a task tree with unambiguous framing and executable-bit coverage."""

    if root.is_symlink() or not root.is_dir():
        raise ContractError("task must be a regular directory")
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda candidate: candidate.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            raise ContractError(f"task tree refuses symlink: {path.relative_to(root)}")
        if path.is_dir():
            kind = b"directory"
            executable = b"0"
            payload_size = 0
        elif path.is_file():
            kind = b"file"
            executable = b"1" if stat.S_IMODE(path.stat().st_mode) & 0o111 else b"0"
            payload_size = path.stat().st_size
            file_count += 1
            total_bytes += payload_size
        else:
            raise ContractError(f"task tree contains an unsupported entry: {path.relative_to(root)}")
        for field in (kind, relative, executable):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
        digest.update(payload_size.to_bytes(8, "big"))
        if path.is_file():
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
    return {
        "task_tree_sha256": f"sha256:{digest.hexdigest()}",
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _dockerfile_images(task_dir: Path) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for path in sorted((task_dir / "task").rglob("Dockerfile")):
        stages: set[str] = set()
        stage_count = 0
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise ContractError(f"{path.relative_to(task_dir)} must be UTF-8 text") from error
        logical_line = ""
        first_line = 1
        escape = "\\"
        parser_directives = True
        frontend_seen = False
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if parser_directives:
                directive = re.fullmatch(r"#\s*(syntax|escape|check)\s*=\s*(.*?)\s*", stripped, re.IGNORECASE)
                if directive is None:
                    parser_directives = False
                elif directive[1].casefold() == "syntax":
                    if frontend_seen or not directive[2] or len(directive[2].split()) != 1:
                        raise ContractError(
                            f"{path.relative_to(task_dir)}:{line_number} has an invalid syntax directive"
                        )
                    frontend_seen = True
                    frontend = _image_reference(path, task_dir, line_number, "frontend", directive[2], set())
                    frontend["immutable"] = _IMAGE_DIGEST.fullmatch(directive[2]) is not None
                    images.append(frontend)
            if stripped.lower().startswith("# escape="):
                escape = stripped.split("=", 1)[1].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not logical_line:
                first_line = line_number
            if escape and stripped.endswith(escape):
                logical_line += stripped[: -len(escape)] + " "
                continue
            logical_line += stripped
            instruction = logical_line.split(maxsplit=1)[0].casefold()
            if instruction not in ("from", "copy", "run"):
                logical_line = ""
                continue
            try:
                parts = shlex.split(logical_line)
            except ValueError as error:
                raise ContractError(f"cannot inventory Dockerfile line {first_line}: {error}") from error
            logical_line = ""
            reference = None
            if instruction == "from":
                references = [part for part in parts[1:] if not part.startswith("--")]
                if not references:
                    raise ContractError(f"{path.relative_to(task_dir)}:{first_line} has an invalid FROM instruction")
                reference = references[0]
            else:
                for index, part in enumerate(parts[1:], start=1):
                    if part.startswith("--from="):
                        reference = part.split("=", 1)[1]
                    elif part == "--from" and index + 1 < len(parts):
                        reference = parts[index + 1]
                    elif instruction == "run" and part.startswith("--mount="):
                        for option in part.split("=", 1)[1].split(","):
                            if option.startswith("from="):
                                images.append(
                                    _image_reference(path, task_dir, first_line, "run_mount", option[5:], stages)
                                )
            if reference is None:
                continue
            images.append(_image_reference(path, task_dir, first_line, instruction, reference, stages))
            if instruction == "from":
                stages.add(str(stage_count))
                stage_count += 1
                lowered = [part.casefold() for part in parts]
                if "as" in lowered:
                    alias_index = lowered.index("as") + 1
                    if alias_index >= len(parts):
                        raise ContractError(f"{path.relative_to(task_dir)}:{first_line} has an invalid stage alias")
                    stages.add(parts[alias_index].casefold())
        if logical_line:
            raise ContractError(f"{path.relative_to(task_dir)} has an unfinished continuation")
    return images


def _image_reference(
    path: Path, task_dir: Path, line: int, instruction: str, reference: str, stages: set[str]
) -> dict[str, Any]:
    internal = reference.casefold() in stages
    return {
        "path": str(path.relative_to(task_dir)),
        "line": line,
        "instruction": instruction,
        "reference": reference,
        "internal_stage": internal,
        "immutable": bool(
            "$" not in reference and (internal or reference == "scratch" or _IMAGE_DIGEST.fullmatch(reference))
        ),
    }


def _configured_images(config: dict[str, Any]) -> list[dict[str, Any]]:
    environments = [("environment", config["environment"]), ("verifier.environment", config["verifier"]["environment"])]
    for index, step in enumerate(config.get("steps", [])):
        if isinstance(step.get("environment"), dict):
            environments.append((f"steps[{index}].environment", step["environment"]))
        verifier = step.get("verifier")
        if isinstance(verifier, dict) and isinstance(verifier.get("environment"), dict):
            environments.append((f"steps[{index}].verifier.environment", verifier["environment"]))
    images = []
    for location, environment in environments:
        if "image" in environment:
            raise ContractError(f"{location}.image is not a Harbor image field; use docker_image")
        reference = environment.get("docker_image")
        if reference is None:
            continue
        if not isinstance(reference, str) or not reference.strip():
            raise ContractError(f"{location}.docker_image must be nonempty text")
        images.append(
            {
                "path": "task/task.toml",
                "field": f"{location}.docker_image",
                "reference": reference,
                "immutable": "$" not in reference and _IMAGE_DIGEST.fullmatch(reference) is not None,
            }
        )
    return images


def _contamination_findings(task_dir: Path) -> tuple[list[dict[str, str]], int]:
    task = task_dir / "task"
    environment = task / "environment"
    findings: list[dict[str, str]] = []
    scanned_files = 0

    for path in sorted(environment.rglob("*")):
        relative = path.relative_to(task)
        if path.name == ".git":
            findings.append(
                {
                    "code": "git_metadata_in_agent_context",
                    "path": str(relative),
                    "detail": "agent build context contains Git metadata or object history",
                }
            )

    protected_digests: dict[str, str] = {}
    for protected_dir, code in (
        (task / "solution", "solution_in_agent_context"),
        (task / "tests", "tests_in_agent_context"),
    ):
        for path in protected_dir.rglob("*") if protected_dir.is_dir() else ():
            if path.name != "Dockerfile" and path.is_file() and not path.is_symlink() and path.stat().st_size:
                protected_digests.setdefault(_sha256_file(path), code)
    for path in environment.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        code = protected_digests.get(_sha256_file(path)) if path.stat().st_size else None
        if code is not None:
            findings.append(
                {
                    "code": code,
                    "path": str(path.relative_to(task)),
                    "detail": "agent build context duplicates a protected task file",
                }
            )

    for path in sorted(task.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        scanned_files += 1
        relative = str(path.relative_to(task))
        matched_codes: set[str] = set()
        overlap = b""
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                sample = overlap + chunk
                for code, pattern, detail in (
                    ("private_key_material", _PRIVATE_KEY_BYTES, "task contains private key material"),
                    ("bearer_credential", _BEARER_BYTES, "task contains a bearer credential"),
                    (
                        "credentialed_url",
                        _CREDENTIALED_URL_BYTES,
                        "task contains credentials embedded in a URL",
                    ),
                ):
                    if code not in matched_codes and pattern.search(sample):
                        findings.append({"code": code, "path": relative, "detail": detail})
                        matched_codes.add(code)
                overlap = sample[-4096:]
    findings.sort(key=lambda finding: (finding["code"], finding["path"]))
    return findings, scanned_files


def _derive_reproducibility(task_dir: Path) -> dict[str, Any]:
    task_contract = _validate_task(task_dir)
    task_info = _task_tree_info(task_dir / "task")
    images = _dockerfile_images(task_dir)
    config = task_contract["config"]
    configured_images = _configured_images(config)
    metadata = config.get("metadata")
    source_revision = None
    if isinstance(metadata, dict):
        for key in ("source_commit", "source_revision"):
            if isinstance(metadata.get(key), str) and metadata[key].strip():
                source_revision = metadata[key]
                break
    environment = config.get("environment")
    configured_image = environment.get("docker_image") if isinstance(environment, dict) else None
    has_agent_recipe = any(image["path"] == "task/environment/Dockerfile" for image in images)
    all_immutable = all(image["immutable"] for image in [*images, *configured_images])
    if all_immutable and isinstance(configured_image, str) and _IMAGE_DIGEST.fullmatch(configured_image):
        portability_state = "immutable_image"
    elif has_agent_recipe and all_immutable:
        portability_state = "image_pinned_recipe"
    else:
        portability_state = "local_only"
    findings, scanned_files = _contamination_findings(task_dir)
    return {
        "schema": REPRODUCIBILITY_SCHEMA,
        **task_info,
        "source_revision": source_revision,
        "portability": {
            "state": portability_state,
            "dependency_closure": "unverified",
            "configured_image": configured_image,
            "container_images": images,
            "configured_images": configured_images,
        },
        "network": {
            "agent": task_contract["agent_network_mode"],
            "verifier": "no-network",
        },
        "contamination": {
            "passed": not findings,
            "scanned_files": scanned_files,
            "findings": findings,
        },
    }


def _validate_reproducibility(task_dir: Path) -> dict[str, Any]:
    recorded = _load_object(task_dir / "reproducibility.json", label="environment reproducibility")
    derived = _derive_reproducibility(task_dir)
    if recorded != derived:
        raise ContractError("reproducibility.json differs from the current task tree or integrity scan")
    if not recorded["contamination"]["passed"]:
        codes = sorted({finding["code"] for finding in recorded["contamination"]["findings"]})
        raise ContractError(f"task contamination scan failed: {', '.join(codes)}")
    return recorded


def _validate_task(task_dir: Path) -> dict[str, Any]:
    environment = task_dir / "task"
    required_files = (
        environment / "README.md",
        environment / "task.toml",
        environment / "instruction.md",
        environment / "tests" / "test.sh",
        environment / "solution" / "solve.sh",
    )
    required_directories = (environment / "environment",)
    missing = [
        str(path.relative_to(task_dir))
        for path in required_files
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0
    ] + [str(path.relative_to(task_dir)) for path in required_directories if path.is_symlink() or not path.is_dir()]
    if missing:
        raise ContractError(f"candidate environment is missing: {', '.join(missing)}")
    readme_path = environment / "README.md"
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ContractError("task/README.md must be UTF-8 text") from error
    if re.search(r"(?m)^#\s+\S", readme) is None:
        raise ContractError("task/README.md must have a level-one title")
    headings = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", readme))
    sections = {
        match.group(1): readme[match.end() : headings[index + 1].start() if index + 1 < len(headings) else None].strip()
        for index, match in enumerate(headings)
    }
    missing_sections = [section for section in _TASK_README_SECTIONS if not sections.get(section)]
    if missing_sections:
        raise ContractError(f"task/README.md is missing substantive sections: {', '.join(missing_sections)}")
    try:
        config = tomllib.loads((environment / "task.toml").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ContractError(f"task/task.toml must be valid UTF-8 TOML: {error}") from error
    verifier = config.get("verifier")
    if not isinstance(verifier, dict):
        raise ContractError("task/task.toml must contain a [verifier] table")
    mode = verifier.get("environment_mode")
    if mode != "separate":
        raise ContractError("task/task.toml must explicitly set [verifier].environment_mode to separate")
    if verifier.get("network_mode") != "no-network":
        raise ContractError("[verifier].network_mode must be no-network")
    verifier_environment = verifier.get("environment")
    if not isinstance(verifier_environment, dict):
        raise ContractError("task/task.toml must contain an explicit [verifier.environment] table")
    if verifier_environment.get("network_mode") != "no-network":
        raise ContractError("[verifier.environment].network_mode must be no-network")
    agent_environment = config.get("environment")
    if not isinstance(agent_environment, dict) or agent_environment.get("network_mode") != "no-network":
        raise ContractError("[environment].network_mode must be no-network")
    steps = config.get("steps", [])
    if not isinstance(steps, list):
        raise ContractError("task/task.toml steps must be an array of tables")

    def check_artifact_tables(table, location=()):
        if isinstance(table, dict):
            if (
                "artifacts" in table
                and location != ()
                and not (len(location) == 2 and location[0] == "steps" and isinstance(location[1], int))
            ):
                raise ContractError("artifact handoff belongs at top-level or step root; nested artifacts are ignored")
            for key, value in table.items():
                check_artifact_tables(value, (*location, key))
        elif isinstance(table, list):
            for index, value in enumerate(table):
                check_artifact_tables(value, (*location, index))

    check_artifact_tables(config)
    handoffs = [config.get("artifacts"), *(step.get("artifacts") for step in steps if isinstance(step, dict))]
    try:
        capture = uses_submission_capture(task_dir)
    except (ValueError, OSError) as error:
        raise ContractError(f"submission capture inspection failed: {error}") from error
    if capture and not any(isinstance(value, list) and value for value in handoffs):
        raise ContractError("submission capture requires a nonempty top-level or step artifacts handoff in task.toml")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ContractError(f"task/task.toml step {index} must be a table")
        step_agent_environment = step.get("environment")
        if step_agent_environment is not None and (
            not isinstance(step_agent_environment, dict)
            or step_agent_environment.get("network_mode") not in {None, "no-network"}
        ):
            raise ContractError(f"task/task.toml step {index} agent environment network_mode must be no-network")
        step_verifier = step.get("verifier")
        if step_verifier is None:
            continue
        if not isinstance(step_verifier, dict):
            raise ContractError(f"task/task.toml step {index} verifier must be a table")
        step_mode = step_verifier.get("environment_mode")
        if step_mode not in {None, "separate"}:
            raise ContractError(f"task/task.toml step {index} verifier must not override separate mode")
        step_network_mode = step_verifier.get("network_mode")
        if step_network_mode not in {None, "no-network"}:
            raise ContractError(f"task/task.toml step {index} verifier network_mode must be no-network")
        step_environment = step_verifier.get("environment")
        if step_environment is not None and (
            not isinstance(step_environment, dict) or step_environment.get("network_mode") != "no-network"
        ):
            raise ContractError(f"[steps.verifier.environment] for step {index} must set network_mode to no-network")
    _validate_mock_tool_call_integration(task_dir, config)
    return {
        "verifier_environment_mode": mode,
        "isolation_status": "isolated",
        "agent_network_mode": "no-network",
        "config": config,
    }


_REPAIR_RECEIPT = re.compile(r"repair-(\d+)\.json")
