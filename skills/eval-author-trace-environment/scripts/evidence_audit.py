# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic evidence coverage for validated ATIF; never a correctness judge."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fixture_compiler import canonical_json, result_error_status, result_signature

SCHEMA = "nemo.eval_author.trace_environment_evidence_audit.v1"


def audit_evidence(trajectory: dict[str, Any], *, safe_atif_sha256: str) -> dict[str, Any]:
    """Use scoped indices, not trace text or user-chosen identifiers, in the report."""
    counts = dict.fromkeys(
        (
            "trajectories",
            "steps",
            "calls",
            "observations",
            "paired_calls",
            "missing_observation_calls",
            "multiple_observation_calls",
            "unlinked_observations",
            "error_observations",
            "not_error_observations",
            "unrecorded_status_observations",
            "invalid_status_observations",
            "conflicting_status_observations",
            "agent_steps_without_observations",
            "conflicting_input_groups",
        ),
        0,
    )
    findings: list[dict[str, Any]] = []

    def visit(current: dict[str, Any], path: str) -> None:
        counts["trajectories"] += 1
        # Scope is per trajectory AND explicit tool server. Reused call IDs in
        # other steps or children are never used to join observations.
        groups: dict[str, list[tuple[str, str]]] = {}
        for index, step in enumerate(current["steps"]):
            counts["steps"] += 1
            step_path = f"{path}.steps[{index}]"
            calls = step.get("tool_calls") or []
            results = (step.get("observation") or {}).get("results", [])
            counts["calls"] += len(calls)
            counts["observations"] += len(results)
            if step["source"] == "agent" and not results:
                counts["agent_steps_without_observations"] += 1
                findings.append({"code": "agent_step_without_observation", "path": step_path})
            paired: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for result_index, result in enumerate(results):
                result_path = f"{step_path}.observation.results[{result_index}]"
                source = result.get("source_call_id")
                if source is None:
                    counts["unlinked_observations"] += 1
                    findings.append({"code": "unlinked_observation", "path": result_path})
                else:
                    paired.setdefault(source, []).append((result_index, result))
                status = result_error_status(result)
                counter = (
                    f"{status}_observations" if status in {"error", "not_error"} else f"{status}_status_observations"
                )
                counts[counter] += 1
                if status != "not_error":
                    findings.append({"code": f"result_status_{status}", "path": result_path})
            for call_index, call in enumerate(calls):
                call_path = f"{step_path}.tool_calls[{call_index}]"
                matches = paired.get(call["tool_call_id"], [])
                if len(matches) != 1:
                    code = "missing_observation" if not matches else "multiple_observations"
                    counts["missing_observation_calls" if not matches else "multiple_observation_calls"] += 1
                    findings.append({"code": code, "path": call_path})
                    continue
                counts["paired_calls"] += 1
                extra = call.get("extra")
                scope = extra.get("tool_scope") if isinstance(extra, dict) else None
                server = scope.get("server") if isinstance(scope, dict) else None
                server = server if isinstance(server, str) and server else None
                name, arguments = call.get("function_name"), call.get("arguments")
                if not isinstance(name, str) or not name.strip() or not isinstance(arguments, dict):
                    continue  # the tool-access plan reports schema/name problems
                key = canonical_json([server, name.strip(), arguments])
                groups.setdefault(key, []).append((call_path, result_signature(matches[0][1])))
        for entries in groups.values():
            if len({signature for _, signature in entries}) > 1:
                counts["conflicting_input_groups"] += 1
                findings.append(
                    {"code": "conflicting_results_for_identical_arguments", "paths": [p for p, _ in entries]}
                )
        for index, child in enumerate(current.get("subagent_trajectories") or []):
            visit(child, f"{path}.subagent_trajectories[{index}]")

    visit(trajectory, "$")
    return {
        "schema": SCHEMA,
        "safe_atif_sha256": safe_atif_sha256,
        "scope": "recorded_observation_coverage",
        "execution_verified": False,
        "counts": counts,
        "finding_counts": dict(sorted(Counter(item["code"] for item in findings).items())),
        "findings": findings,
    }
