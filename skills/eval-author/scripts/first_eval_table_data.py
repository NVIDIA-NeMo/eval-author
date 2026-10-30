# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read first_eval table rows from saved design and refreshed authored task data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import first_eval_state
import journey_state
import report

VERIFIER_LABELS = {
    "Custom code": "Custom code",
    "LLM-as-judge": "LLMaJ",
    "Custom code + LLM-as-judge": "Custom code + LLMaJ",
    "deterministic": "Custom code",
    "model": "LLMaJ",
    "hybrid": "Custom code + LLMaJ",
    "Not documented": "Not documented",
}


def _report_snapshot(repo: Path) -> dict:
    path = report.local(repo, ".eval-author/.working/report/state.json")
    snapshot = report.decode_json(report.read_bytes(path, report.MAX_REPORT_BYTES))
    report.validate(snapshot, report.REPORT_SCHEMA)
    report.normalize_task_fields(snapshot)
    return snapshot


def _matches(item: dict, task: dict, provider: str) -> bool:
    if item["provider"] != provider or item["status"] != "found":
        return False
    if provider == "harbor":
        root = task["path"]
        return item["path"] == root and item["readme_path"] == root + "/README.md"
    return item["path"] == task["workload_path"]


def authored_rows(repo: Path, state: dict, task_ids: list[str]) -> list[dict]:
    record = first_eval_state.record(state)
    if record is None:
        raise ValueError("Save the first_eval design before reading authored task rows")
    snapshot = _report_snapshot(repo)
    observed = first_eval_state.observe(repo, state)
    rows = []
    for task_id in task_ids:
        saved = record["tasks"].get(task_id)
        if not saved or observed["tasks"].get(task_id) != saved["sha256"]:
            raise ValueError(f"Authored task {task_id} differs from its saved task revision")
        if record["provider"] == "gym" and not saved.get("workload_path"):
            raise ValueError(f"NeMo Gym task {task_id} needs its saved environment workload path")
        matches = [item for item in snapshot["tasks"] if _matches(item, saved, record["provider"])]
        if len(matches) != 1:
            raise ValueError(f"Expected one current report task for {task_id}, found {len(matches)}")
        item = matches[0]
        if item["display_id"] != task_id:
            raise ValueError(f"Report display ID for {task_id} differs from the proposed task ID")
        if not item["behavior"] or not item["expected_outcome"]:
            raise ValueError(f"Report task {task_id} needs authored behavior and expected outcome")
        if item.get("scoring_rule") and item.get("task_revision") != saved["sha256"]:
            raise ValueError(f"Report scoring rule for {task_id} is stale; refresh the report")
        readme = report.local(repo, item["readme_path"])
        current_readme = journey_state._file_digest(readme, report.MAX_README_BYTES)[0]
        if current_readme != item["readme_revision"]:
            raise ValueError(f"Report task {task_id} is stale; refresh the report")
        rows.append(
            {
                "task_id": task_id,
                "task_sha256": saved["sha256"],
                "behavior_tested": item["behavior"] or "Not documented",
                "expected_outcome": item["expected_outcome"] or "Not documented",
                "verifier_type": VERIFIER_LABELS[item["grading_kind"]],
                "scoring_rule": item.get("scoring_rule") or "Not documented",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--stage", choices=("review_first", "review_suite"), required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    state = journey_state.load_state(repo, state_only=True)
    record = first_eval_state.record(state)
    if record is None:
        raise ValueError("No saved first_eval design")
    task_ids = (
        [record["first_task"]] if args.stage == "review_first" else [item["id"] for item in record["plan"]["tasks"]]
    )
    print(json.dumps({"task_rows": authored_rows(repo, state, task_ids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
