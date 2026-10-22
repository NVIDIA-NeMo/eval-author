# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare private, text-only ATIF evidence for one generated evaluation environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import ipaddress
import json
import os
import re
import shlex as shlex
import shutil
import stat
import subprocess as subprocess  # retained compatibility export
import sys
import tempfile
import tomllib as tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import (
    construction,  # noqa: E402
    solution_review,  # noqa: E402
)
from .evidence_audit import audit_evidence  # noqa: E402
from .fixture_compiler import (  # noqa: E402
    DECISIONS_SCHEMA as DECISIONS_SCHEMA,
)
from .fixture_compiler import (
    build_call_fixtures,
    build_mcp_scenario,
    derive_tool_call_plan,
    resolve_tool_access,
)
from .fixture_compiler import (
    derive_tool_call_inventory as derive_tool_call_inventory,
)
from .mock_registration import probe_registration  # noqa: E402

# Retained workspace API; the shared evidence module owns these operations.
from .task_evidence import (
    _agent_identity as _agent_identity,
)
from .task_evidence import (
    _arm_from_result as _arm_from_result,
)
from .task_evidence import (
    _construction_context as _construction_context,
)
from .task_evidence import (
    _copy_probe_summary as _copy_probe_summary,
)
from .task_evidence import (
    _harbor_task_checksum as _harbor_task_checksum,
)
from .task_evidence import (
    _job_check_rows as _job_check_rows,
)
from .task_evidence import (
    _job_result as _job_result,
)
from .task_evidence import (
    _load_probes as _load_probes,
)
from .task_evidence import (
    _probe as _probe,
)
from .task_evidence import (
    _probes_path as _probes_path,
)
from .task_evidence import (
    _proof_control as _proof_control,
)
from .task_evidence import (
    _record_probe as _record_probe,
)
from .task_evidence import (
    _record_repair as _record_repair,
)
from .task_evidence import (
    _record_run_inputs as _record_run_inputs,
)
from .task_evidence import (
    _record_validation as _record_validation,
)
from .task_evidence import (
    _repairs as _repairs,
)
from .task_evidence import (
    _run_input_path as _run_input_path,
)
from .task_evidence import (
    _validate_run_inputs as _validate_run_inputs,
)
from .task_evidence import (
    _validate_validation as _validate_validation,
)
from .task_evidence import (
    _validation_from_jobs as _validation_from_jobs,
)
from .verifier_lint import uses_submission_capture as uses_submission_capture
from .verifier_lint import verifier_advisories  # noqa: E402
from .workspace_contract import (
    _BEARER_BYTES as _BEARER_BYTES,
)
from .workspace_contract import (
    _CHECK_ROW as _CHECK_ROW,
)
from .workspace_contract import (
    _CREDENTIALED_URL_BYTES as _CREDENTIALED_URL_BYTES,
)
from .workspace_contract import (
    _DIGEST as _DIGEST,
)
from .workspace_contract import (
    _ENVIRONMENT_KEYS as _ENVIRONMENT_KEYS,
)
from .workspace_contract import (
    _IMAGE_DIGEST as _IMAGE_DIGEST,
)
from .workspace_contract import (
    _MAX_PROBES_PER_REVISION as _MAX_PROBES_PER_REVISION,
)
from .workspace_contract import (
    _MAX_REPAIRS as _MAX_REPAIRS,
)
from .workspace_contract import (
    _MIN_VALIDATION_RUNS as _MIN_VALIDATION_RUNS,
)
from .workspace_contract import (
    _PRIVACY_KEYS as _PRIVACY_KEYS,
)
from .workspace_contract import (
    _PRIVATE_KEY_BYTES as _PRIVATE_KEY_BYTES,
)
from .workspace_contract import (
    _REPAIR_REASON_CODES as _REPAIR_REASON_CODES,
)
from .workspace_contract import (
    _REPAIR_RECEIPT as _REPAIR_RECEIPT,
)
from .workspace_contract import (
    _SOURCE_KEYS as _SOURCE_KEYS,
)
from .workspace_contract import (
    _SUMMARY_KEYS as _SUMMARY_KEYS,
)
from .workspace_contract import (
    _TASK_CHECKSUM as _TASK_CHECKSUM,
)
from .workspace_contract import (
    _TASK_README_SECTIONS as _TASK_README_SECTIONS,
)
from .workspace_contract import (
    ATIF_VERSION as ATIF_VERSION,
)
from .workspace_contract import (
    CANONICAL_RUN_INPUT_SCHEMA as CANONICAL_RUN_INPUT_SCHEMA,
)
from .workspace_contract import (
    MAX_CANONICAL_BYTES as MAX_CANONICAL_BYTES,
)
from .workspace_contract import (
    PROBE_SCHEMA as PROBE_SCHEMA,
)
from .workspace_contract import (
    QUALIFIED_RUN_INPUT_SCHEMA as QUALIFIED_RUN_INPUT_SCHEMA,
)
from .workspace_contract import (
    QUALIFIED_SUMMARY_SCHEMA as QUALIFIED_SUMMARY_SCHEMA,
)
from .workspace_contract import (
    QUALIFIED_VALIDATION_SCHEMA as QUALIFIED_VALIDATION_SCHEMA,
)
from .workspace_contract import (
    REPAIR_SCHEMA as REPAIR_SCHEMA,
)
from .workspace_contract import (
    REPRODUCIBILITY_SCHEMA as REPRODUCIBILITY_SCHEMA,
)
from .workspace_contract import (
    RUN_INPUT_SCHEMA as RUN_INPUT_SCHEMA,
)
from .workspace_contract import (
    SCHEMA as SCHEMA,
)
from .workspace_contract import (
    TASK_ID as TASK_ID,
)
from .workspace_contract import (
    VALIDATION_SCHEMA as VALIDATION_SCHEMA,
)
from .workspace_contract import (
    ContractError as ContractError,
)
from .workspace_contract import (
    _chmod_private as _chmod_private,
)
from .workspace_contract import (
    _configured_images as _configured_images,
)
from .workspace_contract import (
    _contamination_findings as _contamination_findings,
)
from .workspace_contract import (
    _current_tool_access as _current_tool_access,
)
from .workspace_contract import (
    _current_tool_call_inventory as _current_tool_call_inventory,
)
from .workspace_contract import (
    _current_tool_call_inventory_file as _current_tool_call_inventory_file,
)
from .workspace_contract import (
    _current_tool_call_plan as _current_tool_call_plan,
)
from .workspace_contract import (
    _derive_reproducibility as _derive_reproducibility,
)
from .workspace_contract import (
    _dockerfile_images as _dockerfile_images,
)
from .workspace_contract import (
    _ensure_task_dir as _ensure_task_dir,
)
from .workspace_contract import (
    _image_reference as _image_reference,
)
from .workspace_contract import (
    _load_json as _load_json,
)
from .workspace_contract import (
    _load_object as _load_object,
)
from .workspace_contract import (
    _load_summary as _load_summary,
)
from .workspace_contract import (
    _mkdir_private as _mkdir_private,
)
from .workspace_contract import (
    _sha256 as _sha256,
)
from .workspace_contract import (
    _sha256_file as _sha256_file,
)
from .workspace_contract import (
    _summary_path as _summary_path,
)
from .workspace_contract import (
    _task_tree_info as _task_tree_info,
)
from .workspace_contract import (
    _tool_call_source as _tool_call_source,
)
from .workspace_contract import (
    _validate_message as _validate_message,
)
from .workspace_contract import (
    _validate_mock_tool_call_integration as _validate_mock_tool_call_integration,
)
from .workspace_contract import (
    _validate_reproducibility as _validate_reproducibility,
)
from .workspace_contract import (
    _validate_task as _validate_task,
)
from .workspace_contract import (
    _validate_trajectory as _validate_trajectory,
)
from .workspace_contract import (
    _write_bytes_once as _write_bytes_once,
)
from .workspace_contract import (
    _write_json as _write_json,
)
from .workspace_contract import (
    _write_text as _write_text,
)

CANDIDATE_SCHEMA = "nemo.eval_author.trace_environment_candidate.v2"
PRIVACY_AUDIT_SCHEMA = "nemo.eval_author.trace_environment_privacy_audit.v1"
PUBLICATION_REVIEW_SCHEMA = "nemo.eval_author.trace_environment_publication_review.v1"
TOOL_CALL_GENERATION_SCHEMA = "nemo.eval_author.trace_environment_tool_call_generation.v1"
EXPORT_SCHEMA = "nemo.eval_author.trace_environment_product.v4"
BATCH_SCHEMA = "nemo.eval_author.trace_environment_batch.v1"
TOOL_CALL_AUDIT_LOG = "/tmp/tool-call-fixture-audit.jsonl"  # nosec B108 - isolated task-container scratch data
MAX_RAW_SOURCE_BYTES = 128 * 1024 * 1024

_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{6,}\d)(?!\w)")
_HOME_PATH = re.compile(r"(?<![\w/])/(?:home|Users)/[^/\s]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)")
_INTERNAL_HOST = re.compile(r"(?i)\b[a-z0-9.-]+\.svc\.cluster\.local\b")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_STREET = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,4}\s+"
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr)\b"
)
_ORGANIZATION = re.compile(
    r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,5}\s+"
    r"(?:Corporation|Corp|Company|Inc|LLC|Ltd|University|Laboratories|Labs)\b"
)
_PERSON = re.compile(r"\b[A-Z][a-z]{2,20}\s+[A-Z][a-z]{2,20}\b")
_CHECK_WRITER = re.compile(r">>?\s*[\"']?/logs/verifier/results[\"']?")
_CHECK_ID_IN_FORMAT = re.compile(r"\b([a-z0-9]+(?:-[a-z0-9]+)*)\\t")
_SYNTAX_ONLY = re.compile(r"^(?:(?:ba)?sh\s+-n|node\s+--check|php\s+-l)\s+[^;|&]+$")
_QUOTED_LITERAL = re.compile(r"'([^'\\]{16,})'" + '|"([^"\\\\]{16,})"')
_RESULTS_PATH = "/logs/verifier/results"
_REDACTION_QUOTE = '<redacted>"'
_IMAGE_OBJECT_START = re.compile(r'\{\s*"type"\s*:\s*"image"')
_IDENTIFIER_KEYS = frozenset(
    {
        "parent_span_id",
        "session_id",
        "source_call_id",
        "span_id",
        "tool_call_id",
        "trace_id",
        "trajectory_id",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema",
        "status",
        "decision_basis",
        "instruction",
        "requirements",
        "verification_mode",
        "evidence_steps",
        "uncertainties",
        "reason_codes",
        "ground_truth",
        "software_requirements",
    }
)
_REQUIREMENT_KEYS = frozenset({"description", "evidence_steps"})
_GROUND_TRUTH_KEYS = frozenset({"availability", "use", "artifacts", "absence_reason"})
_GROUND_TRUTH_ARTIFACT_KEYS = frozenset({"kind", "path", "sha256", "provenance", "notes"})
_PROVENANCE_KEYS = frozenset({"kind", "step_ids", "uri", "revision", "source_id"})
_SOFTWARE_REQUIREMENT_KEYS = frozenset(
    {
        "name",
        "category",
        "required",
        "version",
        "license",
        "availability",
        "redistributable",
        "provenance",
        "notes",
    }
)
_PRIVACY_REPORT_KEYS = _PRIVACY_KEYS - {"path", "audit_path"}
_BATCH_MEMBER_KEYS = frozenset({"task_id", "atif", "source_kind"})


def _validate_task_id(value: str) -> str:
    if len(value) > 80 or TASK_ID.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("task id must be at most 80 lowercase kebab-case characters")
    return value


def _init(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    if ".eval-author" not in root.parts:
        raise ContractError("task workspace root must stay under a .eval-author directory")
    _mkdir_private(root)
    gitignore_path = root / ".gitignore"
    expected_gitignore = "*\n!.gitignore\n"
    if gitignore_path.exists():
        if gitignore_path.is_symlink() or gitignore_path.read_text(encoding="utf-8") != expected_gitignore:
            raise ContractError(f"workspace .gitignore must contain only the required exclusions: {gitignore_path}")
    else:
        _write_text(gitignore_path, expected_gitignore)

    task_dir = root / args.task_id
    if task_dir.exists():
        raise ContractError(f"task workspace already exists: {task_dir}")
    _mkdir_private(task_dir)
    _mkdir_private(task_dir / "private")
    _mkdir_private(task_dir / "safe")

    summary: dict[str, Any] = {
        "schema": QUALIFIED_SUMMARY_SCHEMA,
        "task_id": args.task_id,
        "status": "pending",
        "source": None,
        "privacy": None,
        "candidate": None,
        "environment": {
            "path": "task",
            "status": "not_attempted",
            "technical_status": "not_run",
            "review_status": "unreviewed",
            "validation": None,
            "verifier_environment_mode": None,
            "isolation_status": "not_run",
            "qualification": "not_run",
        },
        "worked_well": [],
        "did_not_work": [],
        "reasons": [],
    }
    _write_bytes_once(
        task_dir / "private/workspace.json",
        (
            json.dumps({"schema": "nemo.eval_author.workspace.v1", "summary_schema": QUALIFIED_SUMMARY_SCHEMA}) + "\n"
        ).encode(),
    )
    _write_json(_summary_path(task_dir), summary)
    return {"task_dir": str(task_dir), "status": "pending", "gitignored": True}


def _replace(pattern: re.Pattern[str], value: str, marker: str, counts: dict[str, int], kind: str) -> str:
    replaced, count = pattern.subn(marker, value)
    counts[kind] = counts.get(kind, 0) + count
    return replaced


def _redact_ipv4(value: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:
            return match.group(0)
        counts["ipv4"] = counts.get("ipv4", 0) + 1
        return "<redacted:ipv4>"

    return _IPV4.sub(replace, value)


def _redact_phone(value: str, counts: dict[str, int]) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            return candidate
        digits = sum(character.isdigit() for character in candidate)
        if not 7 <= digits <= 15:
            return candidate
        counts["phone"] = counts.get("phone", 0) + 1
        return "<redacted:phone>"

    return _PHONE.sub(replace, value)


def _redact_text(value: str, counts: dict[str, int]) -> str:
    value = _replace(_PRIVATE_KEY, value, "<redacted:private-key>", counts, "private_key")
    value = _replace(_BEARER, value, "<redacted:bearer-token>", counts, "bearer_token")
    value = _replace(_EMAIL, value, "<redacted:email>", counts, "email")
    value = _replace(_SSN, value, "<redacted:ssn>", counts, "ssn")
    value = _replace(_HOME_PATH, value, "/home/<redacted:user>", counts, "home_path")
    value = _replace(_INTERNAL_HOST, value, "<redacted:internal-host>", counts, "internal_host")
    value = _redact_ipv4(value, counts)
    return _redact_phone(value, counts)


def _scrub(value: Any, counts: dict[str, int], *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "image" and isinstance(value.get("source"), dict):
            counts["image"] = counts.get("image", 0) + 1
            return {"type": "text", "text": "[image omitted from text-only evidence]"}
        return {name: _scrub(item, counts, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, counts, key=key) for item in value]
    if isinstance(value, str):
        if key in _IDENTIFIER_KEYS and value:
            counts["identifier"] = counts.get("identifier", 0) + 1
            return f"anon-{hashlib.sha256(value.encode()).hexdigest()[:16]}"
        if key is not None and _SECRET_KEY.search(key):
            if value:
                counts["secret_field"] = counts.get("secret_field", 0) + 1
                return "<redacted:secret>"
            return value
        return _redact_text(value, counts)
    return value


def _parse_or_repair(raw: str) -> tuple[dict[str, Any], list[int]]:
    repaired = raw
    inserted_offsets: list[int] = []
    while True:
        try:
            payload = json.loads(repaired)
            if not isinstance(payload, dict):
                raise ContractError("ATIF root must be a JSON object")
            return payload, inserted_offsets
        except json.JSONDecodeError as error:
            candidates = [
                index + len(_REDACTION_QUOTE) - 1
                for index in range(len(repaired))
                if repaired.startswith(_REDACTION_QUOTE, index) and index + len(_REDACTION_QUOTE) - 1 < error.pos
            ]
            if not candidates:
                raise ContractError(
                    "invalid JSON is not attributable to a provider-redaction placeholder quote"
                ) from error
            quote_offset = candidates[-1]
            original_offset = quote_offset - len(inserted_offsets)
            repaired = repaired[:quote_offset] + "\\" + repaired[quote_offset:]
            inserted_offsets.append(original_offset)


def _bound_stringified_images(payload: dict[str, Any]) -> dict[str, int]:
    counts = {
        "count": 0,
        "omitted_encoded_characters": 0,
        "metadata_field_count": 0,
        "metadata_omitted_characters": 0,
    }

    def convert(content: str) -> tuple[list[dict[str, Any]] | None, int, int]:
        decoder = json.JSONDecoder()
        parts: list[dict[str, Any]] = []
        search_cursor = 0
        emitted_cursor = 0
        converted_count = 0
        converted_characters = 0
        while match := _IMAGE_OBJECT_START.search(content, search_cursor):
            start = match.start()
            try:
                part, end = decoder.raw_decode(content, start)
            except json.JSONDecodeError:
                search_cursor = match.end()
                continue
            source = part.get("source") if isinstance(part, dict) else None
            if part.get("type") != "image" or not isinstance(source, dict) or not isinstance(source.get("data"), str):
                search_cursor = end
                continue
            if start > emitted_cursor:
                parts.append({"type": "text", "text": content[emitted_cursor:start]})
            bounded_source = dict(source)
            encoded = bounded_source["data"]
            bounded_source["data"] = ""
            parts.append({"type": "image", "source": bounded_source})
            converted_count += 1
            converted_characters += len(encoded)
            search_cursor = end
            emitted_cursor = end
        if converted_count == 0:
            return None, 0, 0
        if emitted_cursor < len(content):
            parts.append({"type": "text", "text": content[emitted_cursor:]})
        return parts, converted_count, converted_characters

    def bound_metadata(value: Any, *, image_context: bool = False) -> Any:
        if isinstance(value, dict):
            image_like = (
                image_context
                or (isinstance(value.get("type"), str) and value["type"] in {"image", "base64"})
                or (isinstance(value.get("media_type"), str) and value["media_type"].startswith("image/"))
            )
            for key, nested in value.items():
                if (
                    isinstance(nested, str)
                    and nested
                    and ((key in {"base64", "data"} and image_like) or (key == "base64" and len(nested) > 100_000))
                ):
                    counts["metadata_field_count"] += 1
                    counts["metadata_omitted_characters"] += len(nested)
                    value[key] = ""
                else:
                    value[key] = bound_metadata(nested, image_context=image_like)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                value[index] = bound_metadata(nested, image_context=image_context)
        elif isinstance(value, str):
            # Tool observations may repeat image metadata as JSON inside a text
            # part, not as a structured ATIF image. Preserve surrounding prose
            # and nonbinary metadata; never decode or execute encoded contents.
            decoder = json.JSONDecoder()
            fragments: list[str] = []
            search_cursor = emitted_cursor = 0
            while match := _IMAGE_OBJECT_START.search(value, search_cursor):
                try:
                    metadata, end = decoder.raw_decode(value, match.start())
                except json.JSONDecodeError:
                    search_cursor = match.end()
                    continue
                previous_count = counts["metadata_field_count"]
                bound_metadata(metadata)
                if counts["metadata_field_count"] != previous_count:
                    fragments.extend((value[emitted_cursor : match.start()], json.dumps(metadata, ensure_ascii=False)))
                    emitted_cursor = end
                search_cursor = end
            if fragments:
                fragments.append(value[emitted_cursor:])
                return "".join(fragments)
        return value

    def bound_image_parts(value: Any) -> None:
        if isinstance(value, dict):
            source = value.get("source")
            if value.get("type") == "image" and isinstance(source, dict):
                encoded = source.get("data")
                if isinstance(encoded, str) and encoded:
                    counts["count"] += 1
                    counts["omitted_encoded_characters"] += len(encoded)
                    source["data"] = ""
            for nested in value.values():
                bound_image_parts(nested)
        elif isinstance(value, list):
            for nested in value:
                bound_image_parts(nested)

    def visit(trajectory: dict[str, Any]) -> None:
        steps = trajectory.get("steps")
        if isinstance(steps, list):
            for step in steps:
                observation = step.get("observation") if isinstance(step, dict) else None
                results = observation.get("results") if isinstance(observation, dict) else None
                if not isinstance(results, list):
                    continue
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    content = result.get("content")
                    if isinstance(content, str):
                        parts, converted_count, converted_characters = convert(content)
                        if parts is not None:
                            result["content"] = parts
                            counts["count"] += converted_count
                            counts["omitted_encoded_characters"] += converted_characters
                    bound_metadata(result.get("extra"))
        subagents = trajectory.get("subagent_trajectories")
        if isinstance(subagents, list):
            for subagent in subagents:
                if isinstance(subagent, dict):
                    visit(subagent)

    visit(payload)
    bound_image_parts(payload)
    bound_metadata(payload)
    return counts


def _record_normalization(
    payload: dict[str, Any], source_sha256: str, offsets: list[int], image_counts: dict[str, int]
) -> list[dict[str, Any]]:
    normalizations: list[dict[str, Any]] = []
    if offsets:
        normalizations.append(
            {
                "kind": "escape_provider_redaction_placeholder_quote",
                "count": len(offsets),
                "source_character_offsets": offsets,
            }
        )
    if image_counts["count"] or image_counts["metadata_field_count"]:
        normalizations.append({"kind": "bound_stringified_image_observations", **image_counts})
    extra = payload.setdefault("extra", {})
    if not isinstance(extra, dict):
        raise ContractError("ATIF extra field must be an object when present")
    normalization = extra.setdefault("normalization", {})
    if not isinstance(normalization, dict):
        raise ContractError("ATIF extra.normalization field must be an object when present")
    uncertainties = normalization.setdefault("uncertainties", [])
    losses = normalization.setdefault("losses", [])
    if not isinstance(uncertainties, list) or any(not isinstance(item, str) for item in uncertainties):
        raise ContractError("ATIF normalization uncertainties must be a string list")
    if not isinstance(losses, list) or any(not isinstance(item, str) for item in losses):
        raise ContractError("ATIF normalization losses must be a string list")
    if offsets:
        uncertainties.append(
            "A missing JSON escape immediately after a provider-redaction placeholder was inserted only where the parser implicated that quote."
        )
    if image_counts["count"] or image_counts["metadata_field_count"]:
        losses.append(
            "String-encoded image data was omitted from the bounded canonical ATIF; exact source bytes remain preserved and hashed."
        )
    normalization["source_sha256"] = source_sha256
    normalization["operations"] = normalizations
    return normalizations


def _walk_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        strings: list[tuple[str, str]] = []
        for key, nested in value.items():
            strings.extend(_walk_strings(nested, f"{path}.{key}"))
        return strings
    if isinstance(value, list):
        strings = []
        for index, nested in enumerate(value):
            strings.extend(_walk_strings(nested, f"{path}[{index}]"))
        return strings
    return [(path, value)] if isinstance(value, str) else []


def _privacy_audit(payload: dict[str, Any], safe_sha256: str) -> dict[str, Any]:
    strings = _walk_strings(payload)
    findings: list[dict[str, str]] = []
    url_hosts: set[str] = set()
    patterns = (("street_address", _STREET), ("organization", _ORGANIZATION), ("person_name", _PERSON))
    for path, value in strings:
        for url in _URL.findall(value):
            hostname = urlsplit(url).hostname
            if hostname:
                url_hosts.add(hostname)
        for kind, pattern in patterns:
            for match in pattern.finditer(value):
                findings.append({"kind": kind, "path": path, "value": match.group(0)})
    return {
        "schema": PRIVACY_AUDIT_SCHEMA,
        "safe_sha256": safe_sha256,
        "string_field_count": len(strings),
        "unique_string_count": len({value for _, value in strings}),
        "character_count": sum(len(value) for _, value in strings),
        "url_hosts": sorted(url_hosts),
        "candidate_findings": findings,
        "review": {"complete": False, "reviewer_kind": None, "note": None},
    }


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    summary = _load_summary(task_dir)
    if summary["source"] is not None:
        raise ContractError("this task workspace already has prepared source evidence")

    source_path = args.atif
    if source_path.is_symlink() or not source_path.is_file():
        raise ContractError(f"ATIF source must be a regular file: {source_path}")
    source_path = source_path.resolve()
    source_bytes = source_path.read_bytes()
    if len(source_bytes) > MAX_RAW_SOURCE_BYTES:
        raise ContractError(f"ATIF source exceeds the {MAX_RAW_SOURCE_BYTES}-byte raw-source limit")
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"could not read ATIF source as UTF-8: {error}") from error
    payload, repaired_offsets = _parse_or_repair(source_text)
    image_counts = _bound_stringified_images(payload)
    normalizations = _record_normalization(payload, _sha256(source_bytes), repaired_offsets, image_counts)
    validation = _validate_trajectory(payload)

    original_path = task_dir / "private" / "source.atif.json"
    canonical_path = task_dir / "private" / "canonical.atif.json"
    safe_path = task_dir / "safe" / "trace.atif.json"
    privacy_path = task_dir / "safe" / "privacy.json"
    audit_path = task_dir / "private" / "privacy-audit.json"
    if any(path.exists() for path in (original_path, canonical_path, safe_path, privacy_path, audit_path)):
        raise ContractError("refusing to replace an existing prepared artifact")
    canonical_bytes = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if len(canonical_bytes) > MAX_CANONICAL_BYTES:
        raise ContractError(f"canonical ATIF exceeds the {MAX_CANONICAL_BYTES}-byte limit after bounded normalization")
    counts: dict[str, int] = {}
    scrubbed = _scrub(payload, counts)
    _validate_trajectory(scrubbed)
    safe_bytes = (json.dumps(scrubbed, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if len(safe_bytes) > MAX_CANONICAL_BYTES:
        raise ContractError(f"safe ATIF exceeds the {MAX_CANONICAL_BYTES}-byte limit")
    blocking_reasons = [
        f"image_only_user_instruction:step-{step_id}" for step_id in validation["image_only_user_steps"]
    ]
    privacy = {
        "deterministic_redactions": dict(sorted(counts.items())),
        "images_omitted": counts.get("image", 0),
        "contextual_review_required": True,
        "contextual_review_complete": False,
        "reviewer_kind": None,
        "blocking_reasons": blocking_reasons,
    }
    audit = _privacy_audit(scrubbed, _sha256(safe_bytes))
    summary["source"] = {
        "kind": args.source_kind,
        "original_path": "private/source.atif.json",
        "original_sha256": _sha256(source_bytes),
        "original_size_bytes": len(source_bytes),
        "canonical_path": "private/canonical.atif.json",
        "canonical_sha256": _sha256(canonical_bytes),
        "canonical_size_bytes": len(canonical_bytes),
        "safe_path": "safe/trace.atif.json",
        "safe_sha256": _sha256(safe_bytes),
        "safe_size_bytes": len(safe_bytes),
        "normalizations": normalizations,
    }
    summary["privacy"] = {
        "path": "safe/privacy.json",
        "audit_path": "private/privacy-audit.json",
        **privacy,
    }
    privacy_bytes = (json.dumps(privacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    audit_bytes = (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    written: list[Path] = []
    try:
        _write_bytes_once(original_path, source_bytes)
        written.append(original_path)
        _write_bytes_once(canonical_path, canonical_bytes)
        written.append(canonical_path)
        _write_bytes_once(safe_path, safe_bytes)
        written.append(safe_path)
        _write_bytes_once(privacy_path, privacy_bytes)
        written.append(privacy_path)
        _write_bytes_once(audit_path, audit_bytes)
        written.append(audit_path)
        _write_json(_summary_path(task_dir), summary)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {
        "task_dir": str(task_dir),
        "status": "prepared",
        "redaction_count": sum(counts.values()),
        "images_omitted": privacy["images_omitted"],
        "blocking_reason_count": len(blocking_reasons),
        "normalization_count": len(normalizations),
        "privacy_candidate_count": len(audit["candidate_findings"]),
    }


def _string_list(payload: dict[str, Any], name: str, *, nonempty: bool = False) -> list[str]:
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(f"candidate.{name} must be a list of nonempty strings")
    if nonempty and not value:
        raise ContractError(f"candidate.{name} must not be empty")
    return value


def _review_privacy(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    summary = _load_summary(task_dir)
    privacy = summary.get("privacy")
    if not isinstance(privacy, dict):
        raise ContractError("prepare ATIF evidence before reviewing privacy")
    safe_path = task_dir / summary["source"]["safe_path"]
    audit_path = task_dir / privacy["audit_path"]
    report_path = task_dir / privacy["path"]
    audit = _load_object(audit_path, label="privacy audit")
    if audit.get("schema") != PRIVACY_AUDIT_SCHEMA or audit.get("safe_sha256") != _sha256(safe_path.read_bytes()):
        raise ContractError("privacy audit does not match the current safe ATIF")
    review = audit.get("review")
    if not isinstance(review, dict) or set(review) != {"complete", "reviewer_kind", "note"}:
        raise ContractError("privacy audit review fields do not match the versioned contract")
    if review["complete"]:
        raise ContractError("contextual privacy review has already been recorded")
    audit["review"] = {"complete": True, "reviewer_kind": args.reviewer_kind, "note": args.note}
    report = _load_object(report_path, label="privacy report")
    if set(report) != _PRIVACY_REPORT_KEYS:
        raise ContractError("privacy report fields do not match the versioned contract")
    report["contextual_review_complete"] = True
    report["reviewer_kind"] = args.reviewer_kind
    privacy.update(report)
    _write_json(audit_path, audit)
    _write_json(report_path, report)
    _write_json(_summary_path(task_dir), summary)
    return {
        "task_dir": str(task_dir),
        "contextual_review_complete": True,
        "reviewer_kind": args.reviewer_kind,
        "candidate_findings_reviewed": len(audit.get("candidate_findings", [])),
        "url_hosts_reviewed": len(audit.get("url_hosts", [])),
    }


def _audit_evidence(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    _, safe, digest = _tool_call_source(task_dir)
    report = audit_evidence(safe, safe_atif_sha256=digest)
    report_bytes = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    if len(report_bytes) > MAX_CANONICAL_BYTES:
        raise ContractError("evidence audit exceeds the bounded artifact size; no partial report was retained")
    private = task_dir / "private"
    if private.is_symlink() or not private.is_dir():
        raise ContractError("evidence audit requires a regular private directory")
    output = private / "evidence-audit.json"
    if output.is_symlink():
        raise ContractError("evidence audit must be a regular file")
    if output.exists():
        if not output.is_file() or _load_object(output, label="evidence audit") != report:
            raise ContractError("retained evidence audit differs; preserve it and use a fresh workspace")
    else:
        _write_bytes_once(output, report_bytes)
    return {
        "task_dir": str(task_dir),
        "report": str(output),
        "scope": report["scope"],
        "execution_verified": False,
        "counts": report["counts"],
        "finding_counts": report["finding_counts"],
    }


def _inventory_tool_calls(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    output = task_dir / "private/tool-call-inventory.json"
    if output.exists():
        raise ContractError("refusing to replace existing tool-call-inventory.json")
    inventory = _current_tool_call_inventory(task_dir)
    _write_bytes_once(output, (json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    return {
        "task_dir": str(task_dir),
        "tool_count": inventory["tool_count"],
        "call_count": inventory["call_count"],
        "inventory": str(output),
    }


def _plan_tool_call_access(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    output = task_dir / "private/tool-call-plan.json"
    if output.exists():
        raise ContractError("refusing to replace existing tool-call-plan.json")
    inventory = _current_tool_call_inventory_file(task_dir)
    overrides_path = task_dir / "private/tool-schema-overrides.json"
    if overrides_path.exists():
        raise ContractError("refusing to replace existing tool-schema-overrides.json")
    overrides = (
        _load_object(args.schema_overrides, label="reviewed schema overrides") if args.schema_overrides else None
    )
    try:
        plan = derive_tool_call_plan(inventory, overrides)
    except ValueError as error:
        raise ContractError(str(error)) from error
    if overrides is not None:
        _write_bytes_once(
            overrides_path, (json.dumps(overrides, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        )
    _write_bytes_once(output, (json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    return {
        "task_dir": str(task_dir),
        "tool_count": len(plan["tools"]),
        "mock_support_counts": plan["mock_support_counts"],
        "plan": str(output),
    }


def _resolve_tool_call_access(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    output = task_dir / "private/tool-access.json"
    if output.exists():
        raise ContractError("refusing to replace existing tool-access.json")
    inventory, plan = _current_tool_call_plan(task_dir)
    requested = _load_object(args.decisions, label="tool access decisions")
    try:
        access = resolve_tool_access(inventory, plan, requested, reviewer_kind=args.reviewer_kind)
    except ValueError as error:
        raise ContractError(str(error)) from error
    _write_bytes_once(output, (json.dumps(access, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    return {
        "task_dir": str(task_dir),
        "access_counts": access["access_counts"],
        "access": str(output),
    }


def _fixture_readme() -> str:
    return (
        "# Trace-derived tool-call mock\n\n"
        "`call-fixtures.json` is the transport-neutral set of tool-call inputs and outputs selected for mock "
        "access by the evaluation author. The included MCP adapter exposes those functions to Harbor agents, "
        "matches tool names and canonical JSON arguments exactly, and returns an error for every unmatched call.\n\n"
        "Copy this directory to `/opt/tool-call-fixtures` in the agent image, ensure Python 3 is present, and merge "
        "`integration.toml` into the task's `[environment]` config. The optional audit log is written to "
        f"`{TOOL_CALL_AUDIT_LOG}`. Finalization rejects a candidate task that selected mock access but "
        "did not configure this MCP adapter.\n\n"
        "The stdio process and fixture files are inspectable by a shell-capable agent. Do not use them to hold "
        "hidden verifier truth. Prefer a filesystem-isolated sidecar when fixture contents must remain hidden.\n"
    )


def _fixture_integration() -> str:
    return (
        "# Merge this array entry into the real task.toml after copying this directory\n"
        "# to /opt/tool-call-fixtures in the agent image.\n"
        "[[environment.mcp_servers]]\n"
        'name = "trace-tool-call-replay"\n'
        'transport = "stdio"\n'
        'command = "/opt/tool-call-fixtures/launch-replay.sh"\n'
        "args = []\n"
    )


def _fixture_launcher() -> str:
    # An argument-free executable also works with Harbor adapters that serialize
    # command + args as one executable name (notably Codex in Harbor 0.20.0).
    return (
        "#!/bin/sh\n"
        'exec python3 "$(dirname "$0")/mcp_replay.py" '
        '--scenario "$(dirname "$0")/mcp-scenario.json" '
        f'--audit-log "${{TRACE_TOOL_CALL_AUDIT_LOG:-{TOOL_CALL_AUDIT_LOG}}}"\n'
    )


def _generated_fixture_files(fixture_dir: Path, task_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(fixture_dir.rglob("*")):
        if path.is_symlink():
            raise ContractError("generated trace fixtures must not contain symlinks")
        if not path.is_file():
            continue
        files.append(
            {
                "path": str(path.relative_to(task_dir)),
                "sha256": _sha256_file(path),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
        )
    return files


def _generate_mock_tool_calls(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    summary, _, _ = _tool_call_source(task_dir)
    privacy = summary.get("privacy")
    if not isinstance(privacy, dict) or not privacy.get("contextual_review_complete"):
        raise ContractError("mock tool-call generation requires the review-privacy command")
    if privacy.get("blocking_reasons"):
        raise ContractError("mock tool-call generation is blocked by unresolved privacy evidence")
    inventory, _, access = _current_tool_access(task_dir)
    call_fixtures = build_call_fixtures(inventory, access)
    scenario = build_mcp_scenario(call_fixtures)
    if not call_fixtures["tools"]:
        raise ContractError("tool access decisions select no mock tool calls to generate")

    fixture_dir = task_dir / "task/environment/tool-call-fixtures"
    receipt_path = task_dir / "private/tool-call-generation.json"
    if fixture_dir.exists() or receipt_path.exists():
        raise ContractError("refusing to replace existing generated mock tool calls")
    fixture_dir.parent.mkdir(parents=True, exist_ok=True)
    from .runtime_assets import ASSETS

    runtime_source = ASSETS / "replay_mcp_server.py"
    with tempfile.TemporaryDirectory(prefix="tool-call-fixtures-", dir=task_dir / "private") as temporary:
        staged = Path(temporary) / "tool-call-fixtures"
        _mkdir_private(staged)
        _write_bytes_once(
            staged / "call-fixtures.json",
            (json.dumps(call_fixtures, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
        _write_bytes_once(
            staged / "mcp-scenario.json",
            (json.dumps(scenario, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
        _write_bytes_once(staged / "mcp_replay.py", runtime_source.read_bytes())
        _write_bytes_once(staged / "launch-replay.sh", _fixture_launcher().encode())
        _write_bytes_once(staged / "integration.toml", _fixture_integration().encode())
        _write_bytes_once(staged / "README.md", _fixture_readme().encode())
        if os.name == "posix":
            (staged / "mcp_replay.py").chmod(0o755)
            (staged / "launch-replay.sh").chmod(0o755)
            for name in ("call-fixtures.json", "mcp-scenario.json", "integration.toml", "README.md"):
                (staged / name).chmod(0o644)
        staged.rename(fixture_dir)

    files = _generated_fixture_files(fixture_dir, task_dir)
    receipt = {
        "schema": TOOL_CALL_GENERATION_SCHEMA,
        "safe_atif_sha256": inventory["safe_atif_sha256"],
        "inventory_sha256": _sha256_file(task_dir / "private/tool-call-inventory.json"),
        "plan_sha256": _sha256_file(task_dir / "private/tool-call-plan.json"),
        "access_sha256": _sha256_file(task_dir / "private/tool-access.json"),
        "call_fixtures_sha256": _sha256_file(fixture_dir / "call-fixtures.json"),
        "files": files,
    }
    _write_bytes_once(
        receipt_path,
        (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    return {
        "task_dir": str(task_dir),
        "generated_mock_tool_count": len(scenario["tools"]),
        "fixture_dir": str(fixture_dir),
        "receipt": str(receipt_path),
    }


def _validate_tool_call_pipeline(task_dir: Path, *, require_decisions: bool = False) -> None:
    inventory_path = task_dir / "private/tool-call-inventory.json"
    plan_path = task_dir / "private/tool-call-plan.json"
    access_path = task_dir / "private/tool-access.json"
    receipt_path = task_dir / "private/tool-call-generation.json"
    fixture_dir = task_dir / "task/environment/tool-call-fixtures"
    overrides_path = task_dir / "private/tool-schema-overrides.json"
    if require_decisions:
        current = _current_tool_call_inventory(task_dir)
        if current["unresolved_call_count"]:
            raise ContractError("candidate contains unresolved tool calls; correct source evidence or use no_candidate")
        if current["call_count"] and not all(path.is_file() for path in (inventory_path, plan_path, access_path)):
            raise ContractError("candidate tool calls require complete reviewed real/mock/none access decisions")
    if not any(
        path.exists() for path in (inventory_path, plan_path, access_path, receipt_path, fixture_dir, overrides_path)
    ):
        return
    if not inventory_path.is_file():
        raise ContractError("tool-call artifacts require private/tool-call-inventory.json")
    inventory = _current_tool_call_inventory_file(task_dir)
    if not plan_path.exists():
        if access_path.exists() or receipt_path.exists() or fixture_dir.exists() or overrides_path.exists():
            raise ContractError("tool access artifacts require private/tool-call-plan.json")
        return
    _current_tool_call_plan(task_dir)
    if not access_path.exists():
        if receipt_path.exists() or fixture_dir.exists():
            raise ContractError("generated mock tool calls require private/tool-access.json")
        return
    _, _, access = _current_tool_access(task_dir)
    has_mock_access = any(decision["access"] == "mock" for decision in access["decisions"])
    if not has_mock_access:
        if receipt_path.exists() or fixture_dir.exists():
            raise ContractError("generated mock tool calls require at least one mock access decision")
        return
    if receipt_path.exists() != fixture_dir.exists():
        raise ContractError("generated mock tool-call directory and receipt must exist together")
    if not receipt_path.exists():
        raise ContractError("mock tool-call access requires generated mock tool-call artifacts")
    receipt = _load_object(receipt_path, label="fixture generation receipt")
    expected_keys = {
        "schema",
        "safe_atif_sha256",
        "inventory_sha256",
        "plan_sha256",
        "access_sha256",
        "call_fixtures_sha256",
        "files",
    }
    if set(receipt) != expected_keys or receipt.get("schema") != TOOL_CALL_GENERATION_SCHEMA:
        raise ContractError("tool-call generation receipt fields do not match the versioned contract")
    call_fixtures = _load_object(fixture_dir / "call-fixtures.json", label="generated call fixtures")
    if call_fixtures != build_call_fixtures(inventory, access):
        raise ContractError("generated call fixtures differ from the current tool access decisions")
    scenario = _load_object(fixture_dir / "mcp-scenario.json", label="generated MCP scenario")
    if scenario != build_mcp_scenario(call_fixtures):
        raise ContractError("generated MCP scenario differs from the current fixture plan")
    expected = {
        "schema": TOOL_CALL_GENERATION_SCHEMA,
        "safe_atif_sha256": inventory["safe_atif_sha256"],
        "inventory_sha256": _sha256_file(inventory_path),
        "plan_sha256": _sha256_file(plan_path),
        "access_sha256": _sha256_file(access_path),
        "call_fixtures_sha256": _sha256_file(fixture_dir / "call-fixtures.json"),
        "files": _generated_fixture_files(fixture_dir, task_dir),
    }
    if receipt != expected:
        raise ContractError("tool-call generation receipt differs from the generated task files")


def _evidence_steps(value: Any, step_ids: set[int], *, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int for item in value)
        or len(value) != len(set(value))
    ):
        raise ContractError(f"{label} must be a nonempty list of unique integer ATIF step IDs")
    unknown_steps = sorted(set(value) - step_ids)
    if unknown_steps:
        raise ContractError(f"{label} references unknown evidence steps: {unknown_steps}")
    return value


def _validate_provenance(value: Any, step_ids: set[int], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_KEYS:
        raise ContractError(f"{label} fields do not match the versioned contract")
    kind = value.get("kind")
    provenance_steps = value.get("step_ids")
    if kind == "atif_step":
        _evidence_steps(provenance_steps, step_ids, label=f"{label}.step_ids")
        if any(value.get(name) is not None for name in ("uri", "revision", "source_id")):
            raise ContractError(f"{label} ATIF provenance must not include external identifiers")
    elif kind == "external":
        if provenance_steps != []:
            raise ContractError(f"{label}.step_ids must be empty for external provenance")
        for name in ("uri", "revision", "source_id"):
            item = value.get(name)
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ContractError(f"{label}.{name} must be null or nonempty text")
        if not any(value.get(name) for name in ("uri", "revision", "source_id")):
            raise ContractError(f"{label} external provenance needs a URI, revision, or source ID")
    else:
        raise ContractError(f"{label}.kind must be atif_step or external")
    return value


def _validate_requirements(value: Any, step_ids: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError("candidate.requirements must be a list")
    for index, requirement in enumerate(value):
        label = f"candidate.requirements[{index}]"
        if not isinstance(requirement, dict) or set(requirement) != _REQUIREMENT_KEYS:
            raise ContractError(f"{label} fields do not match the versioned contract")
        description = requirement.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ContractError(f"{label}.description must be nonempty text")
        _evidence_steps(requirement.get("evidence_steps"), step_ids, label=f"{label}.evidence_steps")
    return value


def _validate_ground_truth(task_dir: Path, value: Any, step_ids: set[int]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _GROUND_TRUTH_KEYS:
        raise ContractError("candidate.ground_truth fields do not match the versioned contract")
    availability = value.get("availability")
    if not isinstance(availability, str) or availability not in {"available", "partial", "absent", "unknown"}:
        raise ContractError("candidate.ground_truth.availability is not recognized")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError("candidate.ground_truth.artifacts must be a list")
    absence_reason = value.get("absence_reason")
    use = value.get("use")
    if not isinstance(use, str) or use not in {"none", "comparison_only", "verification"}:
        raise ContractError("candidate.ground_truth.use is not recognized")
    if absence_reason is not None and (not isinstance(absence_reason, str) or not absence_reason.strip()):
        raise ContractError("candidate.ground_truth.absence_reason must be null or nonempty text")
    if availability in {"available", "partial"} and not artifacts:
        raise ContractError("available or partial ground truth must name at least one artifact")
    if availability in {"absent", "unknown"} and (artifacts or absence_reason is None):
        raise ContractError("absent or unknown ground truth needs no artifacts and a reason")
    if availability == "available" and absence_reason is not None:
        raise ContractError("available ground truth must set absence_reason to null")
    if availability == "partial" and absence_reason is None:
        raise ContractError("partial ground truth must explain what is missing")
    if availability in {"absent", "unknown"} and use != "none":
        raise ContractError("absent or unknown ground truth must set use to none")
    if availability in {"available", "partial"} and use == "none":
        raise ContractError("available or partial ground truth must declare comparison_only or verification use")

    allowed_kinds = {
        "reference_trace",
        "expected_output",
        "dataset",
        "fixture",
        "verifier",
        "human_feedback",
        "other",
    }
    for index, artifact in enumerate(artifacts):
        label = f"candidate.ground_truth.artifacts[{index}]"
        if not isinstance(artifact, dict) or set(artifact) != _GROUND_TRUTH_ARTIFACT_KEYS:
            raise ContractError(f"{label} fields do not match the versioned contract")
        kind = artifact.get("kind")
        if not isinstance(kind, str) or kind not in allowed_kinds:
            raise ContractError(f"{label}.kind is not recognized")
        relative_path = artifact.get("path")
        if not isinstance(relative_path, str) or not relative_path.startswith("private/ground-truth/"):
            raise ContractError(f"{label}.path must stay under private/ground-truth/")
        artifact_path = task_dir / relative_path
        ground_truth_dir = (task_dir / "private" / "ground-truth").resolve()
        if not artifact_path.resolve().is_relative_to(ground_truth_dir):
            raise ContractError(f"{label}.path escapes private/ground-truth/")
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ContractError(f"{label}.path must name a retained regular file")
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ContractError(f"{label}.sha256 must be a SHA-256 digest")
        if _sha256(artifact_path.read_bytes()) != digest:
            raise ContractError(f"{label}.sha256 does not match the retained artifact")
        if os.name == "posix" and stat.S_IMODE(artifact_path.stat().st_mode) & 0o077:
            raise ContractError(f"{label}.path is not owner-private")
        _validate_provenance(artifact.get("provenance"), step_ids, label=f"{label}.provenance")
        notes = artifact.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ContractError(f"{label}.notes must be nonempty text")
    return value


def _validate_software_requirements(value: Any, step_ids: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError("candidate.software_requirements must be a list")
    names: set[str] = set()
    allowed_categories = {"library", "cli", "desktop_application", "service", "hardware", "other"}
    allowed_licenses = {"open_source", "proprietary", "commercial", "unknown", "not_applicable"}
    allowed_availability = {"available", "installable", "unavailable", "unknown"}
    for index, requirement in enumerate(value):
        label = f"candidate.software_requirements[{index}]"
        if not isinstance(requirement, dict) or set(requirement) != _SOFTWARE_REQUIREMENT_KEYS:
            raise ContractError(f"{label} fields do not match the versioned contract")
        name = requirement.get("name")
        if not isinstance(name, str) or not name.strip() or name.casefold() in names:
            raise ContractError(f"{label}.name must be nonempty and unique")
        names.add(name.casefold())
        category = requirement.get("category")
        if not isinstance(category, str) or category not in allowed_categories:
            raise ContractError(f"{label}.category is not recognized")
        if type(requirement.get("required")) is not bool:
            raise ContractError(f"{label}.required must be a boolean")
        version = requirement.get("version")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise ContractError(f"{label}.version must be null or nonempty text")
        license_class = requirement.get("license")
        if not isinstance(license_class, str) or license_class not in allowed_licenses:
            raise ContractError(f"{label}.license is not recognized")
        availability = requirement.get("availability")
        if not isinstance(availability, str) or availability not in allowed_availability:
            raise ContractError(f"{label}.availability is not recognized")
        redistributable = requirement.get("redistributable")
        if redistributable is not None and type(redistributable) is not bool:
            raise ContractError(f"{label}.redistributable must be true, false, or null")
        _validate_provenance(requirement.get("provenance"), step_ids, label=f"{label}.provenance")
        notes = requirement.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise ContractError(f"{label}.notes must be nonempty text")
    return value


def _validate_candidate(task_dir: Path, status: str, step_ids: set[int]) -> dict[str, Any]:
    candidate = _load_object(task_dir / "candidate.json", label="candidate")
    if set(candidate) not in (_CANDIDATE_KEYS, _CANDIDATE_KEYS | {"state_basis"}):
        raise ContractError("candidate fields do not match the versioned contract")
    if candidate.get("schema") != CANDIDATE_SCHEMA:
        raise ContractError(f"candidate.schema must be {CANDIDATE_SCHEMA!r}")
    state_basis = candidate.get("state_basis", "recorded")
    if state_basis not in ("recorded", "reconstructed"):
        raise ContractError("candidate.state_basis must be 'recorded' or 'reconstructed'")
    if candidate.get("status") != status:
        raise ContractError("candidate status does not match the requested summary status")
    if candidate.get("decision_basis") != "safe_atif_only":
        raise ContractError("candidate.decision_basis must be safe_atif_only")
    _evidence_steps(candidate.get("evidence_steps"), step_ids, label="candidate.evidence_steps")
    _string_list(candidate, "uncertainties")
    _string_list(candidate, "reason_codes", nonempty=status == "no_candidate")
    _validate_ground_truth(task_dir, candidate.get("ground_truth"), step_ids)
    software_requirements = _validate_software_requirements(candidate.get("software_requirements"), step_ids)
    if status == "candidate":
        if not isinstance(candidate.get("instruction"), str) or not candidate["instruction"].strip():
            raise ContractError("candidate.instruction must be nonempty text")
        requirements = _validate_requirements(candidate.get("requirements"), step_ids)
        if not requirements:
            raise ContractError("candidate.requirements must not be empty")
        if candidate.get("verification_mode") != "execution":
            raise ContractError("the basic flow accepts only deterministic execution candidates")
        unavailable = [
            requirement["name"]
            for requirement in software_requirements
            if requirement["required"] and requirement["availability"] == "unavailable"
        ]
        if unavailable:
            raise ContractError(f"candidate requires unavailable software: {', '.join(unavailable)}")
    else:
        if any(candidate.get(name) is not None for name in ("instruction", "verification_mode")):
            raise ContractError("no_candidate records must set instruction and verification_mode to null")
        if candidate.get("requirements") != []:
            raise ContractError("no_candidate records must set requirements to an empty list")
    return candidate


def _check_candidate(args: argparse.Namespace) -> dict[str, Any]:
    """Check authored metadata against its prepared evidence without changing it."""
    task_dir = _ensure_task_dir(args.task_dir)
    summary = _load_summary(task_dir)
    source = summary["source"]
    if source is None:
        raise ContractError("prepare ATIF evidence before checking candidate metadata")
    safe_path = task_dir / source["safe_path"]
    if safe_path.is_symlink() or not safe_path.is_file():
        raise ContractError("safe ATIF must be a retained regular file")
    if safe_path.stat().st_size > MAX_CANONICAL_BYTES:
        raise ContractError(f"safe ATIF exceeds the {MAX_CANONICAL_BYTES}-byte limit")
    safe_bytes = safe_path.read_bytes()
    if _sha256(safe_bytes) != source["safe_sha256"] or len(safe_bytes) != source["safe_size_bytes"]:
        raise ContractError("safe ATIF digest or size changed")
    safe_payload = _load_object(safe_path, label="safe ATIF")
    _validate_trajectory(safe_payload)
    status = _load_object(task_dir / "candidate.json", label="candidate").get("status")
    if status not in ("candidate", "no_candidate"):
        raise ContractError("candidate.status must be candidate or no_candidate")
    _validate_candidate(task_dir, status, {step["step_id"] for step in safe_payload["steps"]})
    return {
        "task_dir": str(task_dir),
        "valid": True,
        "scope": "candidate_metadata",
        "status": status,
        "execution_verified": False,
    }


def _record_reproducibility(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    output = task_dir / "reproducibility.json"
    if output.exists():
        raise ContractError("refusing to replace existing reproducibility.json")
    report = _derive_reproducibility(task_dir)
    _write_json(output, report)
    return {
        "task_dir": str(task_dir),
        "valid": report["contamination"]["passed"],
        "task_tree_sha256": report["task_tree_sha256"],
        "portability_state": report["portability"]["state"],
        "contamination_findings": len(report["contamination"]["findings"]),
    }


def _check_grammar_issues(task_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Static per-check grammar and triviality lint for task/tests/test.sh."""

    issues: list[dict[str, str]] = []
    advisories: list[dict[str, str]] = []
    test_path = task_dir / "task" / "tests" / "test.sh"
    if test_path.is_symlink() or not test_path.is_file():
        return issues, advisories  # reported by the required-file lint
    try:
        text = test_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(
            {
                "code": "test_not_utf8",
                "path": "task/tests/test.sh",
                "hint": "Write tests/test.sh as UTF-8 text.",
            }
        )
        return issues, advisories
    writer_lines = [line for line in text.splitlines() if _RESULTS_PATH in line]
    if not any(_CHECK_WRITER.search(line) for line in writer_lines):
        issues.append(
            {
                "code": "missing_check_rows",
                "path": "task/tests/test.sh",
                "hint": "Emit one '<check-id>\\tPASS|FAIL' row per scored check to /logs/verifier/results; see references/check-grammar.md.",
            }
        )
    check_ids: list[str] = []
    any_quoted = re.compile(r"'([^']*)'|\"([^\"]*)\"")
    for line in writer_lines:
        if not _CHECK_WRITER.search(line):
            continue
        for pair in any_quoted.findall(line):
            value = next(group for group in pair if group)
            check_ids.extend(_CHECK_ID_IN_FORMAT.findall(value))
    duplicates = sorted({check_id for check_id in check_ids if check_ids.count(check_id) > 1})
    if duplicates:
        issues.append(
            {
                "code": "duplicate_check_id",
                "path": "task/tests/test.sh",
                "hint": f"Check IDs must be unique and stable; duplicates: {', '.join(duplicates)}.",
            }
        )
    for match in re.finditer(r"(?m)^\s*if\s+([^;]+);\s*then", text):
        if _SYNTAX_ONLY.fullmatch(match.group(1).strip()):
            issues.append(
                {
                    "code": "syntax_only_check",
                    "path": "task/tests/test.sh",
                    "hint": "A syntax parse (sh -n, node --check, php -l) proves nothing about behavior; assert an observable outcome instead.",
                }
            )
    # Triviality: a long expected literal the agent can copy verbatim from its
    # own inputs is not evidence of work (the deterministic copy baseline).
    # Only assertion-context lines are scanned; a runnable command the
    # instruction names is a legitimate test target, not a leak.
    assertion_line = re.compile(r"\b(?:grep|cmp|diff|sha256sum|assert|jq|test)\b|==|=~")
    expected_literals: set[str] = set()
    for line in text.splitlines():
        if not assertion_line.search(line):
            continue
        for literal_pair in _QUOTED_LITERAL.findall(line):
            value = next(group for group in literal_pair if group)
            if value.startswith(("/", "task", "tests")) or value in check_ids or "\\t" in value:
                continue
            expected_literals.add(value)
    searchable: list[tuple[str, str]] = []
    for relative in ("task/instruction.md",):
        path = task_dir / relative
        if path.is_file() and not path.is_symlink():
            searchable.append((relative, path.read_text(encoding="utf-8", errors="replace")))
    environment = task_dir / "task" / "environment"
    if environment.is_dir():
        for path in sorted(environment.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024:
                relative = f"task/environment/{path.relative_to(environment).as_posix()}"
                searchable.append((relative, path.read_text(encoding="utf-8", errors="replace")))
    for value in sorted(expected_literals):
        for relative, content in searchable:
            if value in content:
                target = issues if len(value) >= 24 else advisories
                target.append(
                    {
                        "code": "copyable_literal",
                        "path": "task/tests/test.sh",
                        "hint": (
                            "An expected literal also appears verbatim in "
                            f"{relative}; derive the expectation or keep it verifier-private so copying cannot pass."
                        ),
                    }
                )
                break
    return issues, advisories


def _validate_task_issues(task_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Collect authoring-stage issues without failing fast (validate-task)."""

    issues: list[dict[str, str]] = []
    root = task_dir / "task"
    if root.is_symlink() or not root.is_dir():
        return (
            [
                {
                    "code": "missing_task_tree",
                    "path": "task",
                    "hint": "Create the Harbor task tree under <task-dir>/task before validating.",
                }
            ],
            [],
        )
    for relative in ("task.toml", "instruction.md", "tests/test.sh", "solution/solve.sh", "README.md"):
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            issues.append(
                {
                    "code": "missing_required_file",
                    "path": f"task/{relative}",
                    "hint": f"Author a nonempty task/{relative}; see SKILL.md step 6.",
                }
            )
    if not (root / "environment").is_dir() or (root / "environment").is_symlink():
        issues.append(
            {
                "code": "missing_required_directory",
                "path": "task/environment",
                "hint": "Create task/environment/ (empty is allowed) holding task prerequisites.",
            }
        )
    try:
        _validate_task(task_dir)
    except ContractError as error:
        message = str(error)
        code = "verifier_contract" if "network" in message or "verifier" in message else "task_contract"
        hint = (
            "Set [verifier].environment_mode='separate', both no-network tables, and [environment].network_mode "
            "='no-network'; see SKILL.md step 6."
            if code == "verifier_contract"
            else "Fix the reported contract error; see SKILL.md step 6 and references/environment-integrity.md."
        )
        issues.append({"code": code, "path": "task", "hint": f"{message} — {hint}"})
    grammar_issues, advisories = _check_grammar_issues(task_dir)
    try:
        _construction_context(task_dir)
    except ContractError as error:
        issues.append({"code": "construction_contract", "path": construction.PATH, "hint": str(error)})
    return issues + grammar_issues, advisories + verifier_advisories(task_dir)


def _validate_task_cmd(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    issues, advisories = _validate_task_issues(task_dir)
    return {
        "task_dir": str(task_dir),
        "ok": not issues,
        "valid": not issues,
        "issues": issues,
        "advisories": advisories,
    }


def _runtime_mode_roundtrip(model: Any) -> bool:
    """Probe a declared provider field, without constructing a proof result."""
    from pydantic import TypeAdapter

    field = model.model_fields.get("verifier_environment_mode")
    if field is None:
        return False
    adapter = TypeAdapter(field.annotation)
    for mode in ("shared", "separate"):
        value = adapter.validate_python(mode)
        # Other required trial fields are deliberately absent: this is only a
        # field serialization probe, never a validated or retained trial.
        probe = model.model_construct(verifier_environment_mode=value)
        if probe.model_dump(mode="json", exclude_unset=True).get("verifier_environment_mode") != mode:
            return False
    return True


def _check_runtime(args: argparse.Namespace) -> dict[str, Any]:
    """Read-only capability preflight; version labels are informational only."""
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema": "nemo.eval_author.trace_environment_runtime_check.v2",
        "valid": False,
        "scope": "single_step_proof",
        "harbor_version": None,
        "python_executable": sys.executable,
        "checks": checks,
        "execution_verified": False,
        "hint": "Use the same existing Harbor Python environment for this check, proof receipts and Harbor jobs. "
        "Do not install or upgrade Harbor automatically. Actual trial results must still prove separate mode.",
    }

    def check(name: str, passed: bool, message: str) -> None:
        checks.append({"name": name, "passed": passed, "message": message})

    try:
        report["harbor_version"] = importlib.metadata.version("harbor")
    except importlib.metadata.PackageNotFoundError:
        # Editable/source installations may have usable APIs without metadata.
        pass
    try:
        config_model = importlib.import_module("harbor.models.task.config").TaskConfig
        result_model = importlib.import_module("harbor.models.trial.result").TrialResult
        task_model = importlib.import_module("harbor.models.task.task").Task
    except Exception as error:
        # Provider import/validation exception types can change between releases.
        # Keep an incompatible installation a structured finding, not a traceback.
        check("provider_imports", False, f"Required Harbor APIs are unavailable ({type(error).__name__}).")
        return report
    check("provider_imports", True, "Required Harbor model APIs imported in this interpreter.")
    check("task_checksum_api", hasattr(task_model, "checksum"), "Proof receipts require Harbor's Task.checksum API.")
    try:
        config = config_model.model_validate(
            {
                "environment": {"network_mode": "no-network"},
                "verifier": {
                    "environment_mode": "separate",
                    "network_mode": "no-network",
                    "environment": {"network_mode": "no-network"},
                },
            }
        )
        for name, model, expected in (
            ("agent_network_config", config.environment, {"network_mode": "no-network"}),
            (
                "separate_verifier_config",
                config.verifier,
                {"environment_mode": "separate", "network_mode": "no-network"},
            ),
            ("verifier_environment_config", config.verifier.environment, {"network_mode": "no-network"}),
        ):
            declared = model is not None and all(key in type(model).model_fields for key in expected)
            dumped = model.model_dump(mode="json") if declared else {}
            check(
                name,
                all(dumped.get(key) == value for key, value in expected.items()),
                "Required isolation settings must be declared and retained by Harbor, not ignored extras.",
            )
    except Exception as error:
        check(
            "isolation_config", False, f"Harbor could not retain required isolation settings ({type(error).__name__})."
        )
    try:
        mode_supported = _runtime_mode_roundtrip(result_model)
        check(
            "trial_verifier_mode", mode_supported, "TrialResult must declare and serialize verifier_environment_mode."
        )
    except Exception as error:
        check("trial_verifier_mode", False, f"Verifier-mode serialization is incompatible ({type(error).__name__}).")
    if args.task_dir is not None:
        task_dir = _ensure_task_dir(args.task_dir)
        try:
            task = task_model(task_dir / "task")
            access_path = task_dir / "private/tool-access.json"
            if access_path.exists():
                _, _, access = _current_tool_access(task_dir)
                if any(item["access"] == "mock" for item in access["decisions"]):
                    _validate_mock_tool_call_integration(task_dir, task.config.model_dump(mode="json"))
                    agent = getattr(args, "mock_agent", None)
                    servers = [
                        server
                        for server in task.config.environment.mcp_servers
                        if server.name == "trace-tool-call-replay"
                    ]
                    registration = probe_registration(agent, servers)
                    report["mock_integration"] = {
                        "adapter": "mcp",
                        "registration": registration,
                        "execution": {"status": "unverified", "reason": "native_agent_run_required"},
                    }
                    check(
                        "mock_agent_registration",
                        registration["status"] != "unsupported",
                        f"Registration: {registration['status']} ({registration['reason']}). "
                        "Only a demonstrated mismatch blocks this preflight. Unverified registration needs "
                        "harness-specific evidence; valid does not prove native-agent mock access.",
                    )
            single_step = not task.config.steps
            check(
                "task_proof_shape",
                single_step,
                "The current proof reader requires trial-level verifier mode; multi-step proof is not certified by this preflight.",
            )
            checksum = task.checksum
            check(
                "task_checksum", isinstance(checksum, str) and bool(checksum), "Harbor must compute the task checksum."
            )
        except Exception as error:
            check("task_validation", False, f"Harbor could not validate this proof task ({type(error).__name__}).")
    if getattr(args, "mock_agent", None) and "mock_integration" not in report:
        check("mock_agent_scope", False, "--mock-agent requires --task-dir with resolved mock tool-call access.")
    report["valid"] = all(item["passed"] for item in checks)
    return report


def _summary_markdown(summary: dict[str, Any]) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- None recorded."

    candidate = summary["candidate"] or {}
    ground_truth = candidate.get("ground_truth") or {"availability": "unrecorded", "artifacts": []}
    software = candidate.get("software_requirements") or []
    qualification_line = (
        f"- Construction qualification: `{summary['environment']['qualification']}`\n"
        if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA
        else ""
    )
    software_lines = [
        (
            f"- {item['name']} ({item['category']}): required={str(item['required']).lower()}, "
            f"version={item['version'] or 'unknown'}, license={item['license']}, "
            f"availability={item['availability']}, "
            f"redistributable={str(item['redistributable']).lower()}"
        )
        for item in software
    ]
    return (
        f"# Trace environment: {summary['task_id']}\n\n"
        f"## Status\n\n`{summary['status']}`\n\n"
        f"## Evidence\n\n"
        f"- Safe ATIF: `{summary['source']['safe_path']}`\n"
        f"- State basis: `{candidate.get('state_basis', 'recorded')}`\n"
        f"- Evidence steps: {candidate.get('evidence_steps', [])}\n"
        f"- Environment: `{summary['environment']['status']}`\n"
        f"- Technical proof: `{summary['environment']['technical_status']}`\n"
        f"{qualification_line}"
        f"- Review: `{summary['environment']['review_status']}`\n"
        f"- Verifier isolation: `{summary['environment']['isolation_status']}`\n\n"
        f"## Ground truth\n\n"
        f"- Availability: `{ground_truth['availability']}`\n"
        f"- Retained artifacts: {len(ground_truth['artifacts'])}\n"
        f"- Absence or uncertainty: {ground_truth.get('absence_reason') or 'None'}\n\n"
        f"## Software requirements\n\n"
        f"{chr(10).join(software_lines) if software_lines else '- None recorded.'}\n\n"
        f"## What worked well\n\n{bullets(summary['worked_well'])}\n\n"
        f"## What did not work\n\n{bullets(summary['did_not_work'])}\n\n"
        f"## Reasons\n\n{bullets(summary['reasons'])}\n"
    )


def _environment_status(
    candidate: dict[str, Any], technical_status: str, isolation_status: str, review_status: str
) -> str:
    if technical_status == "failed":
        return "failed"
    unknown_required = any(
        item["required"] and item["availability"] == "unknown" for item in candidate["software_requirements"]
    )
    if (
        technical_status == "passed"
        and isolation_status == "isolated"
        and review_status == "human_reviewed"
        and not unknown_required
    ):
        return "ready"
    return "unproven"


def _finalize(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    summary = _load_summary(task_dir)
    if summary["source"] is None or summary["privacy"] is None:
        raise ContractError("prepare canonical ATIF evidence before finalizing")
    if summary["status"] != "pending":
        raise ContractError("this task workspace has already been finalized")

    safe_path = task_dir / summary["source"]["safe_path"]
    safe_payload = _load_object(safe_path, label="safe ATIF")
    _validate_trajectory(safe_payload)
    _validate_tool_call_pipeline(task_dir, require_decisions=args.status == "candidate")
    step_ids = {step["step_id"] for step in safe_payload["steps"]}
    candidate = _validate_candidate(task_dir, args.status, step_ids)

    privacy = summary["privacy"]
    if args.status == "candidate" and not privacy["contextual_review_complete"]:
        raise ContractError("candidate finalization requires the review-privacy command")
    if args.status == "candidate" and privacy["blocking_reasons"]:
        raise ContractError("candidate finalization is blocked by unresolved non-text evidence")

    validation: dict[str, Any] | None = None
    if args.status == "no_candidate":
        environment_status = "not_attempted"
        technical_status = "not_run"
        review_status = "unreviewed"
        verifier_mode = None
        isolation_status = "not_run"
        if (task_dir / "validation.json").exists():
            raise ContractError("no_candidate workspaces must not include validation.json")
    else:
        task_contract = _validate_task(task_dir)
        _construction_context(task_dir)
        _validate_reproducibility(task_dir)
        verifier_mode = task_contract["verifier_environment_mode"]
        isolation_status = task_contract["isolation_status"]
        review_status = "human_reviewed" if args.human_reviewed else "unreviewed"
        if (task_dir / "validation.json").exists():
            validation = _validate_validation(task_dir)
            technical_status = "passed" if validation["passed"] else "failed"
        else:
            technical_status = "not_run"
        environment_status = _environment_status(candidate, technical_status, isolation_status, review_status)

    reasons = list(args.reason)
    if args.status == "no_candidate" and not reasons:
        reasons = list(candidate["reason_codes"])
    summary.update(
        {
            "status": args.status,
            "candidate": {"path": "candidate.json", **candidate},
            "environment": {
                "path": "task",
                "status": environment_status,
                "technical_status": technical_status,
                "review_status": review_status,
                "validation": "validation.json" if validation is not None else None,
                "verifier_environment_mode": verifier_mode,
                "isolation_status": isolation_status,
            },
            "worked_well": list(args.worked_well),
            "did_not_work": list(args.did_not_work),
            "reasons": reasons,
        }
    )
    if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA:
        summary["environment"]["qualification"] = (
            "not_applicable"
            if args.status == "no_candidate"
            else validation["qualification"]["status"]
            if validation is not None
            else "not_run"
        )
    _write_json(_summary_path(task_dir), summary)
    _write_text(task_dir / "summary.md", _summary_markdown(summary))
    return {
        "task_dir": str(task_dir),
        "status": args.status,
        "environment_status": environment_status,
        "qualification": summary["environment"].get("qualification", "unmeasured"),
        "summary": str(task_dir / "summary.md"),
    }


def _check(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    summary = _load_summary(task_dir)
    errors: list[str] = []
    try:
        repairs = _repairs(task_dir)
        if len(repairs) > _MAX_REPAIRS:
            errors.append(f"repair ledger exceeds the {_MAX_REPAIRS}-repair budget")
    except ContractError as error:
        repairs = []
        errors.append(str(error))
    if repairs and summary.get("status") == "pending":
        validation_path = task_dir / "validation.json"
        reproducibility_path = task_dir / "reproducibility.json"
        if validation_path.is_file() and not reproducibility_path.is_file():
            errors.append("a post-repair proof set requires a fresh record-reproducibility first")
    source = summary.get("source")
    if isinstance(source, dict):
        for path_key, digest_key, size_key in (
            ("original_path", "original_sha256", "original_size_bytes"),
            ("canonical_path", "canonical_sha256", "canonical_size_bytes"),
            ("safe_path", "safe_sha256", "safe_size_bytes"),
        ):
            path = task_dir / source[path_key]
            if (
                not path.is_file()
                or _sha256(path.read_bytes()) != source[digest_key]
                or path.stat().st_size != source[size_key]
            ):
                errors.append(f"{path_key} is missing or its digest changed")
            elif os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                errors.append(f"{path_key} is not owner-private")
    else:
        errors.append("source has not been prepared")
    privacy = summary.get("privacy")
    if isinstance(privacy, dict) and isinstance(source, dict):
        try:
            privacy_payload = _load_object(task_dir / privacy["path"], label="privacy report")
            if privacy != {"path": privacy["path"], "audit_path": privacy["audit_path"], **privacy_payload}:
                errors.append("privacy report does not match the summary")
            audit = _load_object(task_dir / privacy["audit_path"], label="privacy audit")
            if audit.get("schema") != PRIVACY_AUDIT_SCHEMA or audit.get("safe_sha256") != source["safe_sha256"]:
                errors.append("privacy audit does not match the safe ATIF")
            review = audit.get("review", {})
            if review.get("complete") != privacy["contextual_review_complete"]:
                errors.append("privacy audit review does not match the summary")
            if review.get("reviewer_kind") != privacy["reviewer_kind"]:
                errors.append("privacy audit reviewer kind does not match the summary")
        except ContractError as error:
            errors.append(str(error))
    elif source is not None:
        errors.append("privacy report has not been prepared")
    if summary.get("status") in {"candidate", "no_candidate"} and isinstance(source, dict):
        safe = _load_object(task_dir / source["safe_path"], label="safe ATIF")
        step_ids = {step["step_id"] for step in safe.get("steps", []) if isinstance(step, dict) and "step_id" in step}
        try:
            candidate = _validate_candidate(task_dir, summary["status"], step_ids)
            if summary.get("candidate") != {"path": "candidate.json", **candidate}:
                errors.append("candidate record does not match the summary")
            environment = summary.get("environment", {})
            if summary["status"] == "candidate":
                if (
                    not isinstance(privacy, dict)
                    or not privacy["contextual_review_complete"]
                    or privacy["blocking_reasons"]
                ):
                    errors.append(
                        "finalized candidate lacks a completed contextual privacy review with no blocking findings"
                    )
                task_contract = _validate_task(task_dir)
                _construction_context(task_dir)
                _validate_reproducibility(task_dir)
                if environment.get("verifier_environment_mode") != task_contract["verifier_environment_mode"]:
                    errors.append("summary verifier mode does not match task/task.toml")
                if environment.get("isolation_status") != task_contract["isolation_status"]:
                    errors.append("summary isolation status does not match task/task.toml")
                validation_path = task_dir / "validation.json"
                if validation_path.exists():
                    validation = _validate_validation(task_dir)
                    if (
                        summary["schema"] == QUALIFIED_SUMMARY_SCHEMA
                        and environment["qualification"] != validation["qualification"]["status"]
                    ):
                        errors.append("summary qualification differs from retained evidence")
                    expected_technical = "passed" if validation["passed"] else "failed"
                    if environment.get("technical_status") != expected_technical:
                        errors.append("summary technical status does not match Harbor evidence")
                    expected_status = _environment_status(
                        candidate, expected_technical, task_contract["isolation_status"], environment["review_status"]
                    )
                    if environment.get("status") != expected_status:
                        errors.append(
                            "summary environment status does not match proof, review, isolation, and software"
                        )
                    if environment.get("validation") != "validation.json":
                        errors.append("summary omits retained Harbor validation")
                elif environment.get("technical_status") != "not_run":
                    errors.append("summary claims technical proof without validation.json")
                elif environment.get("status") != "unproven" or environment.get("validation") is not None:
                    errors.append("unrun candidate must remain unproven without validation")
                if (
                    not validation_path.exists()
                    and summary["schema"] == QUALIFIED_SUMMARY_SCHEMA
                    and environment["qualification"] != "not_run"
                ):
                    errors.append("summary claims construction qualification without execution evidence")
            elif environment != {
                "path": "task",
                "status": "not_attempted",
                "technical_status": "not_run",
                "review_status": "unreviewed",
                "validation": None,
                "verifier_environment_mode": None,
                "isolation_status": "not_run",
                **({"qualification": "not_applicable"} if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA else {}),
            }:
                errors.append("no_candidate environment status must remain not_attempted")
            markdown_path = task_dir / "summary.md"
            if not markdown_path.is_file() or markdown_path.read_text(encoding="utf-8") != _summary_markdown(summary):
                errors.append("summary.md is missing or does not match summary.json")
        except ContractError as error:
            errors.append(str(error))
    try:
        _validate_tool_call_pipeline(task_dir, require_decisions=summary["status"] == "candidate")
    except ContractError as error:
        errors.append(str(error))
    return {
        "task_dir": str(task_dir),
        "valid": not errors,
        "errors": errors,
        "repairs": len(repairs),
        "contract_changes": [
            {"repair": receipt["index"], "file": name}
            for receipt in repairs
            for name, path in (
                ("candidate.json", task_dir / "candidate.json"),
                ("construction.json", construction.active_path(task_dir)),
            )
            if receipt["superseded"].get(name) is not None
            and (not path.is_file() or _sha256_file(path) != receipt["superseded"][name]["sha256"])
        ],
        "qualification": summary["environment"].get("qualification", "unmeasured"),
    }


def _load_batch_manifest(path: Path) -> list[dict[str, Any]]:
    manifest = _load_object(path, label="batch manifest")
    if set(manifest) != {"schema", "members"} or manifest.get("schema") != BATCH_SCHEMA:
        raise ContractError("batch manifest fields do not match the versioned contract")
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise ContractError("batch manifest members must be a nonempty list")
    task_ids: set[str] = set()
    for index, member in enumerate(members):
        if not isinstance(member, dict) or set(member) != _BATCH_MEMBER_KEYS:
            raise ContractError(f"batch manifest member {index} fields do not match the versioned contract")
        task_id = member.get("task_id")
        if not isinstance(task_id, str) or len(task_id) > 80 or TASK_ID.fullmatch(task_id) is None:
            raise ContractError(f"batch manifest member {index} has an invalid task_id")
        if task_id in task_ids:
            raise ContractError(f"batch manifest contains duplicate task_id {task_id!r}")
        task_ids.add(task_id)
        atif = member.get("atif")
        if not isinstance(atif, str) or not atif.strip():
            raise ContractError(f"batch manifest member {index}.atif must be nonempty text")
        if member.get("source_kind") not in {"atif", "intake", "mlflow", "otel", "gym"}:
            raise ContractError(f"batch manifest member {index}.source_kind is not recognized")
    return members


def _batch_prepare(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    members = _load_batch_manifest(manifest_path)
    root = args.root.resolve()
    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for member in members:
        task_id = member["task_id"]
        task_dir = root / task_id
        source_path = Path(member["atif"])
        if not source_path.is_absolute():
            source_path = manifest_path.parent / source_path
        try:
            if not task_dir.exists():
                _init(argparse.Namespace(root=root, task_id=task_id))
            summary = _load_summary(task_dir)
            if summary["source"] is None:
                _prepare(
                    argparse.Namespace(
                        task_dir=task_dir,
                        atif=source_path,
                        source_kind=member["source_kind"],
                    )
                )
                outcome = "prepared"
            else:
                source_bytes = source_path.resolve().read_bytes()
                if summary["source"]["original_sha256"] != _sha256(source_bytes):
                    raise ContractError("existing workspace source digest differs from the batch manifest source")
                outcome = "existing"
            results.append({"task_id": task_id, "outcome": outcome})
        except (ContractError, OSError) as error:
            outcome = "failed"
            results.append({"task_id": task_id, "outcome": outcome, "error": str(error)})
        counts[outcome] = counts.get(outcome, 0) + 1
    return {
        "schema": BATCH_SCHEMA,
        "denominator": len(members),
        "counts": dict(sorted(counts.items())),
        "members": results,
        "valid": counts.get("failed", 0) == 0,
    }


def _batch_status(args: argparse.Namespace) -> dict[str, Any]:
    members = _load_batch_manifest(args.manifest.resolve())
    root = args.root.resolve()
    results: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for member in members:
        task_dir = root / member["task_id"]
        qualification = "unmeasured"
        if not task_dir.is_dir():
            status = "missing"
        else:
            try:
                summary = _load_summary(task_dir)
                qualification = summary["environment"].get("qualification", "unmeasured")
                if summary["schema"] == QUALIFIED_SUMMARY_SCHEMA and summary["status"] != "pending":
                    checked = _check(argparse.Namespace(task_dir=task_dir))
                    if not checked["valid"]:
                        raise ContractError("; ".join(checked["errors"]))
                if summary["status"] == "pending":
                    status = "pending_prepared" if summary["source"] is not None else "pending_unprepared"
                elif summary["status"] == "candidate":
                    status = f"candidate_{summary['environment']['status']}"
                else:
                    status = "no_candidate"
            except (ContractError, OSError, UnicodeError) as error:
                status = "invalid"
                results.append({"task_id": member["task_id"], "status": status, "error": str(error)})
                counts[status] = counts.get(status, 0) + 1
                continue
        counts[status] = counts.get(status, 0) + 1
        row: dict[str, Any] = {"task_id": member["task_id"], "status": status}
        if status != "missing":
            row["qualification"] = qualification
            row["qualified"] = qualification == "passed"
        try:
            repair_count = len(_repairs(task_dir))
        except ContractError:
            repair_count = 0
        if repair_count:
            row["repairs"] = repair_count
            if repair_count >= _MAX_REPAIRS and status != "candidate_failed":
                row["repair_budget"] = "exhausted"
        results.append(row)
    return {
        "schema": BATCH_SCHEMA,
        "denominator": len(members),
        "counts": dict(sorted(counts.items())),
        "members": results,
        "valid": counts.get("invalid", 0) == 0,
    }


def _reject_symlinks(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise ContractError(f"safe export refuses symlink: {path.relative_to(root.parent)}")


def _copy_publication_file(source: Path, destination: Path) -> None:
    """Copy only file bytes and normalized executable bits, never source metadata."""

    if source.is_symlink() or not source.is_file():
        raise ContractError("publication source must be a regular file")
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
    destination.chmod(0o644 | (stat.S_IMODE(source.stat().st_mode) & 0o111))


def _copy_publication_tree(source: Path, destination: Path) -> None:
    """Keep the new root private while copying; do not propagate directory xattrs."""

    _reject_symlinks(source)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path in sorted(source.iterdir()):
        target = destination / path.name
        if path.is_dir():
            _copy_publication_tree(path, target)
            target.chmod(0o755)
        else:
            _copy_publication_file(path, target)


def _write_publication(task_dir: Path, output_dir: Path) -> None:
    """Render the complete publication inside an owner-private staging directory."""

    summary = _load_summary(task_dir)
    if summary["status"] == "pending":
        raise ContractError("finalize the task workspace before export")
    checked = _check(argparse.Namespace(task_dir=task_dir))
    if not checked["valid"]:
        raise ContractError(f"task workspace check failed: {'; '.join(checked['errors'])}")
    _reject_symlinks(task_dir / "candidate.json")
    candidate = _load_object(task_dir / "candidate.json", label="candidate")
    _copy_publication_file(task_dir / "candidate.json", output_dir / "candidate.json")
    if summary["status"] == "candidate":
        _reject_symlinks(task_dir / "task")
        _reject_symlinks(task_dir / "reproducibility.json")
        _copy_publication_tree(task_dir / "task", output_dir / "task")
        _copy_publication_file(task_dir / "reproducibility.json", output_dir / "reproducibility.json")
        readme = task_dir / "README.md"
        if readme.exists() or readme.is_symlink():
            _copy_publication_file(readme, output_dir / "README.md")
    reproducibility_summary = None
    if summary["status"] == "candidate":
        reproducibility = _validate_reproducibility(task_dir)
        reproducibility_summary = {
            "task_tree_sha256": reproducibility["task_tree_sha256"],
            "file_count": reproducibility["file_count"],
            "total_bytes": reproducibility["total_bytes"],
            "source_revision": reproducibility["source_revision"],
            "portability_state": reproducibility["portability"]["state"],
            "dependency_closure": reproducibility["portability"]["dependency_closure"],
            "agent_network_mode": reproducibility["network"]["agent"],
            "contamination_passed": reproducibility["contamination"]["passed"],
        }
    validation_summary = None
    if summary["environment"]["validation"] is not None:
        validation = _validate_validation(task_dir)
        validation_summary = {
            "harbor_version": validation["harbor_version"],
            "task_checksum": validation["task_checksum"],
            "task_tree_sha256": validation["task_tree_sha256"],
            "verifier_environment_mode": validation["verifier_environment_mode"],
            "distinct_jobs": validation["distinct_jobs"],
            "container_freshness": validation["container_freshness"],
            "minimum_runs": validation["minimum_runs"],
            "check_evidence": validation["check_evidence"],
            "passed": validation["passed"],
            "qualification": validation.get("qualification", {"status": "unmeasured"}),
            "copy_probes": _copy_probe_summary(validation),
            "runs": {
                arm: [
                    {"reward": run["reward"], "exception_present": run["exception_present"]}
                    for run in validation["runs"][arm]
                ]
                for arm in validation["runs"]
            },
        }
    product = {
        "schema": EXPORT_SCHEMA,
        "task_id": summary["task_id"],
        "status": summary["status"],
        "reason_codes": candidate["reason_codes"],
        "candidate_evidence_steps": candidate["evidence_steps"],
        "state_basis": candidate.get("state_basis", "recorded"),
        "repairs": len(_repairs(task_dir)),
        "ground_truth": {
            "availability": candidate["ground_truth"]["availability"],
            "use": candidate["ground_truth"]["use"],
            "artifact_count": len(candidate["ground_truth"]["artifacts"]),
        },
        "privacy": {
            "redaction_count": sum(summary["privacy"]["deterministic_redactions"].values()),
            "images_omitted": summary["privacy"]["images_omitted"],
            "contextual_review_complete": summary["privacy"]["contextual_review_complete"],
            "reviewer_kind": summary["privacy"]["reviewer_kind"],
        },
        "environment": {
            name: summary["environment"][name]
            for name in (
                "status",
                "technical_status",
                "review_status",
                "verifier_environment_mode",
                "isolation_status",
            )
        },
        "construction_qualification": summary["environment"].get("qualification", "unmeasured"),
        "construction_qualified": summary["environment"].get("qualification") == "passed",
        "reproducibility": reproducibility_summary,
        "technical_validation": validation_summary,
    }
    _write_json(output_dir / "result.json", product)
    for path in output_dir.rglob("*"):
        if path.is_file():
            path.chmod(0o644 | (stat.S_IMODE(path.stat().st_mode) & 0o111))
        elif path.is_dir():
            path.chmod(0o755)


def _publication_inventory(root: Path) -> dict[str, Any]:
    tree = _task_tree_info(root)
    # The task digest tracks whether a file is executable; publication review
    # additionally binds exact permission bits for every entry (except the root).
    modes = {path.relative_to(root).as_posix(): stat.S_IMODE(path.stat().st_mode) for path in sorted(root.rglob("*"))}
    digest = _sha256(json.dumps({"tree": tree, "modes": modes}, sort_keys=True).encode())
    return {"sha256": digest, "file_count": tree["file_count"], "total_bytes": tree["total_bytes"]}


def _prepare_publication(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    publications = task_dir / "private" / "publications"
    if (task_dir / "private").is_symlink() or publications.is_symlink():
        raise ContractError("publication staging directories must not be symlinks")
    _mkdir_private(task_dir / "private")
    _mkdir_private(publications)
    # Keep each preview, including superseded ones, as private review evidence.
    with tempfile.TemporaryDirectory(prefix="staging-", dir=publications) as temporary:
        preview = Path(temporary) / "product"
        _mkdir_private(preview)
        _write_publication(task_dir, preview)
        inventory = _publication_inventory(preview)
        destination = publications / inventory["sha256"].removeprefix("sha256:")
        if destination.exists():
            if _publication_inventory(destination) != inventory:
                raise ContractError("retained publication preview changed; preserve it and prepare a new workspace")
        else:
            preview.rename(destination)
    return {"task_dir": str(task_dir), "preview_dir": str(destination), **inventory}


def _review_publication(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    if not args.note.strip():
        raise ContractError("publication review note must be nonempty")
    if (task_dir / "private").is_symlink() or (task_dir / "private/publications").is_symlink():
        raise ContractError("publication staging directories must not be symlinks")
    preview = args.preview_dir
    if not preview.is_absolute():
        preview = task_dir / preview
    if preview.is_symlink():
        raise ContractError("publication preview must not be a symlink")
    preview = preview.resolve()
    if preview.parent != task_dir / "private/publications":
        raise ContractError("review requires a retained private publication preview")
    inventory = _publication_inventory(preview)
    if inventory["sha256"] != args.sha256 or preview.name != args.sha256.removeprefix("sha256:"):
        raise ContractError("publication preview does not match the reviewed digest")
    with tempfile.TemporaryDirectory(dir=task_dir / "private") as temporary:
        current = Path(temporary)
        _write_publication(task_dir, current)
        if _publication_inventory(current) != inventory:
            raise ContractError("publication preview is stale; prepare and review the current product")
    _write_json(
        task_dir / "private/publication-review.json",
        {
            "schema": PUBLICATION_REVIEW_SCHEMA,
            "publication": inventory,
            "reviewer_kind": args.reviewer_kind,
            "note": args.note.strip(),
        },
    )
    return {"task_dir": str(task_dir), "reviewed": True, **inventory}


def _export(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    if (task_dir / "private").is_symlink():
        raise ContractError("publication staging directories must not be symlinks")
    _mkdir_private(task_dir / "private")
    output_dir = args.output_dir.resolve()
    if output_dir.is_relative_to(task_dir) or ".eval-author" in output_dir.parts:
        raise ContractError("export directory must be outside every private .eval-author workspace")
    if output_dir.exists():
        raise ContractError(f"refusing to replace existing export directory: {output_dir}")
    review_path = task_dir / "private/publication-review.json"
    if not review_path.is_file():
        raise ContractError("export requires prepare-publication and review-publication for every product")
    _reject_symlinks(review_path)
    review = _load_object(review_path, label="publication review")
    if (
        set(review) != {"schema", "publication", "reviewer_kind", "note"}
        or review.get("schema") != PUBLICATION_REVIEW_SCHEMA
        or review.get("reviewer_kind") not in ("agent", "human")
        or not isinstance(review.get("note"), str)
        or not review["note"].strip()
    ):
        raise ContractError("publication review does not match the versioned contract")
    with tempfile.TemporaryDirectory(dir=task_dir / "private") as temporary:
        staged = Path(temporary)
        _write_publication(task_dir, staged)
        if _publication_inventory(staged) != review["publication"]:
            raise ContractError("publication review is stale; prepare and review the current product")
        # Publish the checked snapshot, never reread mutable source files after review.
        _copy_publication_tree(staged, output_dir)
    output_dir.chmod(0o755)
    return {
        "task_id": task_dir.name,
        "output_dir": str(output_dir),
        "files": sorted(str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file()),
    }


def _solution_review(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = _ensure_task_dir(args.task_dir)
    try:
        context = _construction_context(task_dir)
        if context is None:
            raise ContractError("solution review requires a workspace with a valid construction contract")
        binding = {
            "task_tree_sha256": _task_tree_info(task_dir / "task")["task_tree_sha256"],
            "task_checksum": _harbor_task_checksum(task_dir),
            "construction_sha256": context["sha256"],
            "requirements": sorted(row["id"] for row in context["manifest"]["requirements"]),
            "checks": sorted(context["checks"]),
        }
        if args.command == "record-review-inputs":
            return solution_review.record(task_dir, args.plan, binding)
        return solution_review.review(task_dir, args.cohort, args.assessments, binding)
    except construction.ConstructionError as error:
        raise ContractError(f"solution review contract invalid: {error}") from error
    except (OSError, RecursionError) as error:
        raise ContractError("solution review contract invalid; inspect private inputs and retained evidence") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    runtime = subparsers.add_parser("check-runtime", help="probe the installed Harbor proof APIs without running jobs")
    runtime.add_argument(
        "--mock-agent", help="Harbor agent name or trusted module:Class to probe for mock registration"
    )
    runtime.add_argument("--task-dir", type=Path, help="also validate an authored task and its proof shape")
    runtime.set_defaults(run=_check_runtime)

    init = subparsers.add_parser("init", help="create one private, gitignored task workspace")
    init.add_argument("--root", type=Path, default=Path(".eval-author/trace-environments"))
    init.add_argument("--task-id", required=True, type=_validate_task_id)
    init.set_defaults(run=_init)

    prepare = subparsers.add_parser("prepare", help="validate and scrub one canonical ATIF trajectory")
    prepare.add_argument("--task-dir", required=True, type=Path)
    prepare.add_argument("--atif", required=True, type=Path)
    prepare.add_argument("--source-kind", choices=("atif", "intake", "mlflow", "otel", "gym"), required=True)
    prepare.set_defaults(run=_prepare)

    audit = subparsers.add_parser("audit-evidence", help="report observation coverage without inferring correctness")
    audit.add_argument("--task-dir", required=True, type=Path)
    audit.set_defaults(run=_audit_evidence)

    review = subparsers.add_parser("review-privacy", help="record contextual review of every safe ATIF string")
    review.add_argument("--task-dir", required=True, type=Path)
    review.add_argument("--reviewer-kind", choices=("agent", "human"), required=True)
    review.add_argument("--note", required=True)
    review.set_defaults(run=_review_privacy)

    inventory = subparsers.add_parser(
        "inventory-tool-calls", help="derive a private tool-call input and output inventory from safe ATIF"
    )
    inventory.add_argument("--task-dir", required=True, type=Path)
    inventory.set_defaults(run=_inventory_tool_calls)

    access_plan = subparsers.add_parser(
        "plan-tool-call-access", help="report which observed tool calls support deterministic mock replay"
    )
    access_plan.add_argument("--task-dir", required=True, type=Path)
    access_plan.add_argument(
        "--schema-overrides", type=Path, help="reviewed, inventory-bound input schemas; source ATIF stays unchanged"
    )
    access_plan.set_defaults(run=_plan_tool_call_access)

    access = subparsers.add_parser(
        "resolve-tool-call-access", help="record real, mock, or no access for every observed tool-call surface"
    )
    access.add_argument("--task-dir", required=True, type=Path)
    access.add_argument("--decisions", required=True, type=Path)
    access.add_argument("--reviewer-kind", choices=("agent", "human"), required=True)
    access.set_defaults(run=_resolve_tool_call_access)

    mock_generation = subparsers.add_parser(
        "generate-mock-tool-calls", help="materialize deterministic mocks selected by the tool access decision"
    )
    mock_generation.add_argument("--task-dir", required=True, type=Path)
    mock_generation.set_defaults(run=_generate_mock_tool_calls)

    candidate_check = subparsers.add_parser(
        "check-candidate",
        help="read-only metadata and evidence-reference check; does not prove execution or privacy review",
    )
    candidate_check.add_argument("--task-dir", required=True, type=Path)
    candidate_check.set_defaults(run=_check_candidate)

    reproducibility = subparsers.add_parser(
        "record-reproducibility", help="hash the task tree and record portability and contamination evidence"
    )
    reproducibility.add_argument("--task-dir", required=True, type=Path)
    reproducibility.set_defaults(run=_record_reproducibility)

    run_inputs = subparsers.add_parser(
        "record-run-inputs", help="bind a future Harbor job to the current task and proof arm"
    )
    run_inputs.add_argument("--task-dir", required=True, type=Path)
    run_inputs.add_argument("--job-dir", required=True, type=Path)
    run_inputs.add_argument(
        "--arm", required=True, choices=("nop", "oracle", "negative", "copy", "alternative", "bypass")
    )
    run_inputs.add_argument("--control-id")
    run_inputs.add_argument("--negative-agent", "--control-agent", dest="negative_agent")
    run_inputs.add_argument("--negative-source", "--control-source", dest="negative_source", type=Path)
    run_inputs.add_argument("--negative-rationale", "--control-rationale", dest="negative_rationale")
    run_inputs.set_defaults(run=_record_run_inputs)

    record = subparsers.add_parser("record-validation", help="derive proof only from retained Harbor results")
    record.add_argument("--task-dir", required=True, type=Path)
    record.add_argument("--nop-job-dir", required=True, action="append", type=Path)
    record.add_argument("--oracle-job-dir", required=True, action="append", type=Path)
    record.add_argument("--negative-job-dir", required=True, action="append", type=Path)
    record.add_argument("--copy-job-dir", action="append", type=Path, default=[])
    record.add_argument("--alternative-job-dir", action="append", type=Path, default=[])
    record.add_argument("--bypass-job-dir", action="append", type=Path, default=[])
    record.add_argument("--harbor-version", required=True)
    record.add_argument(
        "--allow-aggregate-only",
        action="store_true",
        help="accept retained proof without per-check rows (historical tasks; labeled in the report)",
    )
    record.set_defaults(run=_record_validation)

    review_inputs = subparsers.add_parser(
        "record-review-inputs", help="freeze a private solution-review cohort before execution"
    )
    review_inputs.add_argument("--task-dir", required=True, type=Path)
    review_inputs.add_argument("--plan", required=True, help="workspace-relative private plan JSON")
    review_inputs.set_defaults(run=_solution_review)

    solutions = subparsers.add_parser(
        "review-solutions", help="diagnose reviewed score mismatches without changing readiness"
    )
    solutions.add_argument("--task-dir", required=True, type=Path)
    solutions.add_argument("--cohort", required=True, help="workspace-relative recorded cohort")
    solutions.add_argument("--assessments", required=True, help="workspace-relative private judgments JSON")
    solutions.set_defaults(run=_solution_review)

    validate_task = subparsers.add_parser(
        "validate-task", help="lint the authored task tree during authoring; exits 1 until every issue resolves"
    )
    validate_task.add_argument("--task-dir", required=True, type=Path)
    validate_task.set_defaults(run=_validate_task_cmd)

    probe = subparsers.add_parser(
        "probe", help="run or adopt one diagnostic NOP/Oracle pair; probes are never accepted as proof"
    )
    probe.add_argument("--task-dir", required=True, type=Path)
    probe.add_argument("--arm", choices=("nop", "oracle", "both"), default="both")
    probe.add_argument(
        "--results-from",
        type=Path,
        help="adopt an existing Harbor job directory as diagnostic evidence instead of executing",
    )
    probe.set_defaults(run=_probe)

    repair = subparsers.add_parser(
        "record-repair", help="record one bounded repair, archiving the superseded proof it responds to"
    )
    repair.add_argument("--task-dir", required=True, type=Path)
    repair.add_argument("--reason-code", required=True, choices=sorted(_REPAIR_REASON_CODES))
    repair.add_argument("--note", required=True)
    repair.set_defaults(run=_record_repair)

    finalize = subparsers.add_parser("finalize", help="write the candidate decision and task summary")
    finalize.add_argument("--task-dir", required=True, type=Path)
    finalize.add_argument("--status", choices=("candidate", "no_candidate"), required=True)
    finalize.add_argument("--human-reviewed", action="store_true")
    finalize.add_argument("--worked-well", action="append", default=[])
    finalize.add_argument("--did-not-work", action="append", default=[])
    finalize.add_argument("--reason", action="append", default=[])
    finalize.set_defaults(run=_finalize)

    check = subparsers.add_parser("check", help="verify recorded digests and generated artifacts")
    check.add_argument("--task-dir", required=True, type=Path)
    check.set_defaults(run=_check)

    batch_prepare = subparsers.add_parser("batch-prepare", help="idempotently prepare every manifest member")
    batch_prepare.add_argument("--root", type=Path, default=Path(".eval-author/trace-environments"))
    batch_prepare.add_argument("--manifest", required=True, type=Path)
    batch_prepare.set_defaults(run=_batch_prepare)

    batch_status = subparsers.add_parser("batch-status", help="report the complete manifest denominator")
    batch_status.add_argument("--root", type=Path, default=Path(".eval-author/trace-environments"))
    batch_status.add_argument("--manifest", required=True, type=Path)
    batch_status.set_defaults(run=_batch_status)

    publication = subparsers.add_parser("prepare-publication", help="stage the exact public product for private review")
    publication.add_argument("--task-dir", required=True, type=Path)
    publication.set_defaults(run=_prepare_publication)

    publication_review = subparsers.add_parser(
        "review-publication", help="attest review of an exact publication preview"
    )
    publication_review.add_argument("--task-dir", required=True, type=Path)
    publication_review.add_argument("--preview-dir", required=True, type=Path)
    publication_review.add_argument("--sha256", required=True)
    publication_review.add_argument("--reviewer-kind", choices=("agent", "human"), required=True)
    publication_review.add_argument("--note", required=True)
    publication_review.set_defaults(run=_review_publication)

    export = subparsers.add_parser("export", help="publish only the reviewed candidate, task, and result summary")
    export.add_argument("--task-dir", required=True, type=Path)
    export.add_argument("--output-dir", required=True, type=Path)
    export.set_defaults(run=_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = args.run(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error), "error_type": "contract"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    sys.exit(main())
