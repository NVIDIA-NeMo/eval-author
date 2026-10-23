# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import TrialResult
from metric_benchmark.config import load_benchmark
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import RawResults
from metric_benchmark.validation import agent_config, task_checksum, validate_results
from test_metric_benchmark_config import LAB, prepared_benchmark


def native_trial(task_path, directory, agent, reward):
    return TrialResult.model_validate(
        {
            "task_name": HarborTask(task_path).name,
            "trial_name": directory.name,
            "trial_uri": directory.as_uri(),
            "task_id": {"path": str(task_path)},
            "task_checksum": task_checksum(HarborTask(task_path)),
            "config": {
                "task": {"path": str(task_path)},
                "agent": agent.model_dump(mode="json"),
                "trial_name": directory.name,
                "trials_dir": str(directory.parent),
            },
            "agent_info": {"name": agent.name or agent.import_path, "version": "test"},
            "verifier_result": {"rewards": {"reward": reward}},
            "finished_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )


def result_example(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    case = loaded.definition.cases[0]
    root = tmp_path / "results"
    rows = []
    for i, agent in enumerate(case.agents()):
        for task in case.task_inventory.tasks:
            relative = f"agents/agent-{i:04d}/run-0001/{task.task_id}__native"
            folder = root / relative
            folder.mkdir(parents=True)
            native = native_trial(loaded.root / task.task_id, folder, agent_config(agent, loaded.root), float(1 - i))
            write_json(folder / "result.json", native)
            rows.append(
                {
                    "run_id": case.selection().run_ids[agent.agent_bundle_id],
                    "agent_bundle_id": agent.agent_bundle_id,
                    "evaluation": {"evaluation_id": f"{i}/{task.task_id}", "execution": 1, "task": task.model_dump()},
                    "status": "succeeded",
                    "metrics": {"reward": {"value": float(1 - i)}},
                    "native_results_path": relative,
                    "native_results_format": "directory",
                }
            )
    raw = RawResults.model_validate(
        {"experiment_id": "run-1", "task_inventory": case.task_inventory.model_dump(), "results": rows}
    )
    write_json(root / "results.json", raw)
    return loaded, case, raw, root, path


def test_contract_and_cli_parity(tmp_path):
    loaded, case, raw, root, path = result_example(tmp_path)
    report = validate_results(raw, case, root, loaded.root)
    assert report["expected_pairs"] == 6
    out = tmp_path / "validation.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "validate_results.py"),
            "--benchmark",
            str(path),
            "--case",
            "case",
            "--results-root",
            str(root),
            "--output",
            str(out),
        ],
        check=True,
    )
    assert read_json(out) == report


@pytest.mark.parametrize(
    "change", ["missing", "score", "task", "checksum", "agent", "unfinished", "symlink", "repetition"]
)
def test_inconsistent_matrix_or_evidence_rejected(tmp_path, change):
    loaded, case, raw, root, _ = result_example(tmp_path)
    native_path = root / raw.results[0].native_results_path / "result.json"
    native = json.loads(native_path.read_text())
    if change == "missing":
        raw.results.pop()
    if change == "score":
        raw.results[0].metrics["reward"].value = 0.2
    if change == "task":
        native["task_name"] = "other"
    if change == "checksum":
        native["task_checksum"] = "changed"
    if change == "agent":
        native["config"]["agent"]["name"] = "nop"
    if change == "unfinished":
        native["finished_at"] = None
    if change == "symlink":
        (root / "escape").symlink_to(tmp_path)
    if change == "repetition":
        raw.results.append(raw.results[0].model_copy(deep=True))
    native_path.write_text(json.dumps(native))
    with pytest.raises(ValueError):
        validate_results(raw, case, root, loaded.root)


def test_explicit_missing_observation_is_not_zero(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path)
    row = raw.results[0]
    row.native_results_path = "unavailable/task-0"
    row.status = "missing"
    row.metrics["reward"].value = None
    row.metrics["reward"].reason = "worker never started"
    row.unavailable_evidence = {"native_results": "worker never started"}
    report = validate_results(raw, case, root, loaded.root)
    assert report["unavailable"][0]["reason"] == "worker never started"
