# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test-only helpers for Harbor results trees (not used in production drivers)."""

import json
from pathlib import Path

from metric_benchmark.io import read_json
from metric_benchmark.models import RawResults, resolve_evidence


def synthesize_oracle_atif_for_results(results_root: Path) -> None:
    """Write minimal ATIF when oracle trials omit trajectory.json (hermetic tests only)."""
    raw = RawResults.model_validate(read_json(results_root / "results.json"))
    for row in raw.results:
        if row.status != "succeeded":
            continue
        native = resolve_evidence(results_root, row.native_results_path)
        trajectory = native / "agent" / "trajectory.json"
        if trajectory.is_file():
            continue
        task_id = row.evaluation.task.task_id
        prefix = "task-"
        tool = (
            f"tool_{task_id[len(prefix) :]}"
            if task_id.startswith(prefix) and task_id[len(prefix) :].isdigit()
            else task_id.replace("-", "_")
        )
        payload = {
            "schema_version": "ATIF-v1.7",
            "session_id": row.evaluation.evaluation_id,
            "trajectory_id": row.evaluation.evaluation_id,
            "agent": {"name": row.agent_bundle_id, "version": "1", "model_name": "oracle"},
            "steps": [
                {"step_id": 1, "source": "user", "message": "Complete the task."},
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": "Done.",
                    "tool_calls": [
                        {"tool_call_id": "call-1", "function_name": tool, "arguments": {}},
                    ],
                },
            ],
        }
        (native / "agent").mkdir(parents=True, exist_ok=True)
        trajectory.write_text(json.dumps(payload), encoding="utf-8")
