# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Record every tool call in validated ATIF verbatim and in order; never infer, merge, or deduplicate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fixture_compiler import (
    _definition_name,
    _definitions_equivalent,
    _server_scope,
    _trajectories,
    canonical_json,
    json_sha256,
    result_error_status,
)

MEMBERS_SCHEMA = "nemo.eval_author.tool_corpus_members.v1"
MANIFEST_SCHEMA = "nemo.eval_author.tool_corpus.v1"
CALLS_SCHEMA = "nemo.eval_author.tool_corpus_calls.v1"


def _tool_definitions(agent: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = agent.get("tool_definitions")
    return [item for item in definitions if isinstance(item, dict)] if isinstance(definitions, list) else []


def _schema(definitions: list[dict[str, Any]], name: Any, server: str | None) -> tuple[str, str | None]:
    """Resolve a call's definition within its own trajectory and server scope only."""
    if not isinstance(name, str) or not name.strip():
        return "undeclared", None
    distinct: list[dict[str, Any]] = []
    for definition in definitions:
        if _definition_name(definition) != name.strip() or _server_scope(definition) != server:
            continue
        if all(not _definitions_equivalent(known, definition) for known in distinct):
            distinct.append(definition)
    if not distinct:
        return "undeclared", None
    if len(distinct) > 1:
        return "ambiguous", None
    return "declared", json_sha256(distinct[0])


def extract_source(
    trajectory: dict[str, Any],
    *,
    source_sha256: str,
    path: str,
    source_kind: str,
    selection: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return one source entry and one row per tool call for an already validated trajectory."""
    trajectories: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for trajectory_path, current in _trajectories(trajectory):
        agent: dict[str, Any] = current["agent"]
        definitions = _tool_definitions(agent)
        trajectories.append(
            {
                "trajectory_path": trajectory_path,
                "session_id": current.get("session_id"),
                "trajectory_id": current.get("trajectory_id"),
                "agent": {key: value for key, value in agent.items() if key != "tool_definitions"},
                "tool_definitions": {json_sha256(item): item for item in definitions},
            }
        )
        ordinal = 0
        for step in current["steps"]:
            results = (step.get("observation") or {}).get("results") or []
            for call_index, call in enumerate(step.get("tool_calls") or []):
                ordinal += 1
                server = _server_scope(call)
                schema_status, schema_sha256 = _schema(definitions, call.get("function_name"), server)
                calls.append(
                    {
                        "source_sha256": source_sha256,
                        "trajectory_path": trajectory_path,
                        "ordinal": ordinal,
                        "step_id": step["step_id"],
                        "call_index": call_index,
                        "timestamp": step.get("timestamp"),
                        "tool_call_id": call["tool_call_id"],
                        "function_name": call.get("function_name"),
                        "server": server,
                        "schema_status": schema_status,
                        "schema_sha256": schema_sha256,
                        "arguments": call.get("arguments"),
                        "call_extra": call.get("extra"),
                        # Same-step ID matches as recorded: zero, one, or several results; never a cross-step guess.
                        "results": [
                            {"result_index": index, "error_status": result_error_status(result), "result": result}
                            for index, result in enumerate(results)
                            if result.get("source_call_id") == call["tool_call_id"]
                        ],
                    }
                )
    source = {
        "source_sha256": source_sha256,
        "path": path,
        "source_kind": source_kind,
        "selection": selection,
        "atif_version": trajectory["schema_version"],
        "trajectories": trajectories,
    }
    return source, calls


def corpus_files(sources: list[dict[str, Any]], calls: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Serialize deterministically: identical sources and extractor code produce identical bytes."""
    calls_bytes = "".join(canonical_json(row) + "\n" for row in calls).encode()
    scripts = Path(__file__).resolve().parent
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "calls_schema": CALLS_SCHEMA,
        "calls_sha256": f"sha256:{hashlib.sha256(calls_bytes).hexdigest()}",
        "extractor": {
            name: f"sha256:{hashlib.sha256((scripts / name).read_bytes()).hexdigest()}"
            for name in ("tool_corpus.py", "fixture_compiler.py")
        },
        "counts": {
            "sources": len(sources),
            "trajectories": sum(len(source["trajectories"]) for source in sources),
            "calls": len(calls),
        },
        "sources": sources,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    return manifest, {"manifest.json": manifest_bytes, "calls.jsonl": calls_bytes}
