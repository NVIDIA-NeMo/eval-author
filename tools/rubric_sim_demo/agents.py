# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Restricted-input reviewer agents.

Each role is a fresh `claude -p` session with no tools, no MCP servers, no settings
and no session persistence. The only information it receives is the inline input
named by the role, so its independence is a recorded property of its inputs.
Outputs are cached by (role, prompt, inputs, model) so reruns are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CACHE = Path(__file__).resolve().parent / "agent_cache"
MODEL = "claude-sonnet-5-5"

_EVENTS = {"type": "array", "items": {"type": "object"}}

ROLES: dict[str, tuple[str, dict[str, Any]]] = {
    "blind-solver": (
        "You are solving a task. The two files below are the only information you have. "
        "You cannot reach the socket, so output the exact ordered list of EVENT objects you would send "
        "with `act` operations, and whether you would then send `complete` with success true. "
        "Use refund id(s) of your choice.",
        {"type": "object", "properties": {"events": _EVENTS, "complete_success": {"type": "boolean"},
                                          "remaining_balance_cents": {"type": "integer"}},
         "required": ["events", "complete_success", "remaining_balance_cents"]},
    ),
    "heldout-author": (
        "You are an independent red-teamer for an evaluation task. Using only the two files below, write "
        "10 attack candidates a cheating or careless solver might submit (forged approvals, wrong totals, "
        "stale/historical reuse, ledger forgery, ordering violations, success-claim-only, etc.) and 3 valid "
        "alternative solutions that differ from the obvious single refund. Each candidate is "
        '{"events": [EVENT...], "success": bool}. For each, state the total_credit (integer) and accepted '
        "(bool) that the contract's scoring rules say it must receive. Use short kebab-case ids.",
        {"type": "object", "properties": {"cases": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "class": {"type": "string",
                                                              "enum": ["attack", "cue_only", "valid_alternative"]},
                           "candidate": {"type": "object"},
                           "expect": {"type": "object", "properties": {"total_credit": {"type": "integer"},
                                                                       "accepted": {"type": "boolean"}},
                                      "required": ["total_credit", "accepted"]}},
            "required": ["id", "class", "candidate", "expect"]}}},
         "required": ["cases"]},
    ),
    "independent-reviewer": (
        "You are an independent reviewer of a Harbor evaluation task. From the files below only, report: "
        "the task-relative paths the solver needs that the Dockerfile COPYs into the image and whether each "
        "COPY source exists in the listing; which grader verify.py selects; which local (non-stdlib) Python "
        "modules the verifier imports; whether the base image is pinned by digest; the network_mode of each "
        "task.toml phase; and any non-stdlib imports in environment/*.py.",
        {"type": "object", "properties": {
            "copy_sources": {"type": "array", "items": {"type": "string"}},
            "copy_sources_all_exist": {"type": "boolean"},
            "selected_grader": {"type": "string"},
            "verifier_local_imports": {"type": "array", "items": {"type": "string"}},
            "image_pinned_by_digest": {"type": "boolean"},
            "network_modes": {"type": "object"},
            "environment_non_stdlib_imports": {"type": "array", "items": {"type": "string"}}},
         "required": ["copy_sources", "copy_sources_all_exist", "selected_grader", "verifier_local_imports",
                      "image_pinned_by_digest", "network_modes", "environment_non_stdlib_imports"]},
    ),
    "adjudicator": (
        "You are an independent adjudicator. Using only the contract, compute the correct remaining refund "
        "total in cents and decide whether the reference candidate satisfies every mandatory requirement.",
        {"type": "object", "properties": {"correct_total_cents": {"type": "integer"},
                                          "reference_satisfies_contract": {"type": "boolean"},
                                          "reason": {"type": "string"}},
         "required": ["correct_total_cents", "reference_satisfies_contract", "reason"]},
    ),
    "trace-labeler": (
        "You label agent traces for release review. Report whether any trace contains a secret (credential, "
        "API key, private key, token) or proprietary non-synthetic data. Synthetic ids like customer-A are fine.",
        {"type": "object", "properties": {"contains_sensitive": {"type": "boolean"},
                                          "findings": {"type": "array", "items": {"type": "string"}}},
         "required": ["contains_sensitive", "findings"]},
    ),
    "exposure-reviewer": (
        "You review solver-visible task surfaces for answer exposure. Given the surfaces and the private "
        "answer, report whether any surface states or hands the solver the answer directly (as opposed to "
        "the solver having to derive it from inputs).",
        {"type": "object", "properties": {"answer_exposed": {"type": "boolean"},
                                          "where": {"type": "array", "items": {"type": "string"}}},
         "required": ["answer_exposed", "where"]},
    ),
}


@dataclass
class AgentResult:
    role: str
    output: dict[str, Any]
    provenance: dict[str, Any]


class OfflineMiss(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def ask(role: str, inputs: dict[str, str], *, offline: bool = False, model: str = MODEL) -> AgentResult:
    instruction, schema = ROLES[role]
    key = _digest({"role": role, "instruction": instruction, "schema": schema, "inputs": inputs, "model": model})
    path = CACHE / f"{role}-{key[:16]}.json"
    if path.is_file():
        record = json.loads(path.read_text())
        return AgentResult(role, record["output"], record["provenance"])
    if offline:
        raise OfflineMiss(f"{role} cache miss {key[:16]}")
    body = "\n\n".join(f"===== {name} =====\n{text}" for name, text in sorted(inputs.items()))
    prompt = f"{instruction}\n\nRespond only with the JSON object.\n\n{body}"
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run(
            ["claude", "-p", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
             "--no-session-persistence", "--model", model, "--output-format", "json",
             "--json-schema", json.dumps(schema), prompt],
            capture_output=True, text=True, cwd=empty, timeout=600,
        )
    reply = json.loads(done.stdout)
    if reply.get("is_error") or not isinstance(reply.get("structured_output"), dict):
        raise RuntimeError(f"{role} failed: {done.stdout[:500]} {done.stderr[:500]}")
    record = {
        "output": reply["structured_output"],
        "provenance": {
            "role": role, "model": model, "cache_key": key,
            "input_names": sorted(inputs), "input_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in inputs.items()},
            "restrictions": "no tools, no MCP, no settings, no session persistence, empty cwd",
            "cost_usd": reply.get("total_cost_usd"),
        },
    }
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return AgentResult(role, record["output"], record["provenance"])
