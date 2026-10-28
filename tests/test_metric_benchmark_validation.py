# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import TrialResult
from metric_benchmark.config import ScoringPolicy, load_benchmark
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import RawResults
from metric_benchmark.validation import agent_config, task_checksum, validate_results
from test_metric_benchmark_config import FIXTURE, LAB, case_definition, prepared_benchmark, write_case

RESULT_FIXTURE = FIXTURE.parent / "metric_benchmark_results"


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


def result_example(tmp_path, *, bundle=False):
    path, definition = prepared_benchmark(tmp_path / "inputs")
    if bundle:
        from metric_benchmark.config import input_digest

        source = path.parent / "bundle.txt"
        source.write_text("prepared agent input")
        case_data = case_definition(path)
        for agent in [case_data["baseline"], *case_data["mutants"]]:
            agent["inputs"] = {"bundle": {"path": "bundle.txt", "sha256": input_digest(source)}}
            agent["harbor"]["kwargs"] = {"bundle": "{input:bundle}"}
        write_case(path, case_data)
    loaded = load_benchmark(path)
    case = loaded.case("rho-agent").case
    root = tmp_path / "results"
    rows = []
    for i, agent in enumerate(case.agents()):
        for task in case.task_inventory.tasks:
            relative = f"agents/agent-{i:04d}/run-0001/{task.task_id}__native"
            folder = root / relative
            folder.mkdir(parents=True)
            native = native_trial(
                case.task_sources[task.task_id].resolve(loaded.case(case.case_id).root),
                folder,
                agent_config(agent, loaded.case(case.case_id).root),
                float(1 - i),
            )
            write_json(folder / "result.json", native)
            rows.append(
                {
                    "agent_run_id": f"agent-{i:04d}/run-0001",
                    "agent_bundle_id": agent.agent_bundle_id,
                    "evaluation": {"evaluation_id": f"{i}/{task.task_id}", "execution": 1, "task": task.model_dump()},
                    "status": "succeeded",
                    "metrics": {"reward": {"value": float(1 - i)}},
                    "native_results_path": relative,
                    "native_results_format": "directory",
                }
            )
    raw = RawResults.model_validate(
        {
            "run_id": "run-1",
            "task_inventory": case.task_inventory.model_dump(),
            "execution_results": rows,
        }
    )
    write_json(root / "results.json", raw)
    return loaded, case, raw, root, path


def test_contract_and_cli_parity(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    case = loaded.case("rho-agent").case
    root = tmp_path / "execution-results"
    shutil.copytree(RESULT_FIXTURE / "execution-results", root)
    raw = RawResults.model_validate(read_json(root / "results.json"))
    report = validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)
    assert report["expected_pairs"] == 9
    assert report == read_json(RESULT_FIXTURE / "validation.json")
    out = tmp_path / "validation.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "validate_results.py"),
            "--benchmark",
            str(path),
            "--case",
            "rho-agent",
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
    native_path = root / raw.execution_results[0].native_results_path / "result.json"
    native = json.loads(native_path.read_text())
    if change == "missing":
        raw.execution_results.pop()
    if change == "score":
        raw.execution_results[0].metrics["reward"].value = 0.2
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
        raw.execution_results.append(raw.execution_results[0].model_copy(deep=True))
    native_path.write_text(json.dumps(native))
    with pytest.raises(ValueError):
        validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)


def test_explicit_missing_observation_is_not_zero(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path)
    row = raw.execution_results[0]
    row.native_results_path = "unavailable/inspect-files"
    row.status = "missing"
    row.metrics["reward"].value = None
    row.metrics["reward"].reason = "worker never started"
    row.unavailable_evidence = {"native_results": "worker never started"}
    report = validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)
    assert report["unavailable"][0]["reason"] == "worker never started"


def test_relocated_bundle_and_native_evidence_validate_without_original_paths(tmp_path):
    original = tmp_path / "producer"
    loaded, case, raw, root, path = result_example(original, bundle=True)
    before = validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)
    moved = tmp_path / "consumer"
    shutil.move(original, moved)
    loaded = load_benchmark(moved / "inputs/experiment.yml")
    case = loaded.case("rho-agent").case
    assert (
        validate_results(raw, case, moved / "results", loaded.case(case.case_id).root, loaded.definition.scoring)
        == before
    )
    native_path = moved / "results" / raw.execution_results[0].native_results_path / "result.json"
    native = json.loads(native_path.read_text())
    native["config"]["agent"]["kwargs"]["bundle"] = "/unrelated/bundle.txt"
    native_path.write_text(json.dumps(native))
    with pytest.raises(ValueError, match="case rho-agent, agent baseline, task inspect-files.*agent configuration"):
        validate_results(raw, case, moved / "results", loaded.case(case.case_id).root, loaded.definition.scoring)


def test_empty_agent_does_not_default_to_oracle(tmp_path):
    from metric_benchmark.config import Agent

    with pytest.raises(ValueError, match="exactly one"):
        agent_config(Agent(agent_bundle_id="mutant", revision="1", harbor={}), tmp_path)


def test_executor_run_names_are_not_directory_conventions(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path)
    for row in raw.execution_results:
        row.agent_run_id = "external-job:" + row.agent_bundle_id
    assert (
        validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)["expected_pairs"]
        == 9
    )


@pytest.mark.parametrize("unexpected", [False, True])
def test_agent_set_errors_name_case_and_agents(tmp_path, unexpected):
    from metric_benchmark.validation import selection_for_results

    _, case, raw, _, _ = result_example(tmp_path)
    if unexpected:
        for row in raw.execution_results:
            if row.agent_bundle_id == "mutant":
                row.agent_bundle_id = "unconfigured"
        message = "case rho-agent:.*missing agents:.*mutant.*unexpected agents:.*unconfigured"
    else:
        raw.execution_results = [row for row in raw.execution_results if row.agent_bundle_id == "baseline"]
        message = "case rho-agent:.*missing agents:.*mutant"
    with pytest.raises(ValueError, match=message):
        selection_for_results(case, raw, ScoringPolicy())


def test_distinct_worker_roots_are_not_experiment_identity(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path, bundle=True)
    for i, row in enumerate(raw.execution_results):
        path = root / row.native_results_path / "result.json"
        trial = json.loads(path.read_text())
        producer = f"/worker-{i}/prepared"
        trial["config"]["task"]["path"] = producer + "/" + case.task_sources[row.evaluation.task.task_id].path
        trial["config"]["agent"]["kwargs"]["bundle"] = producer + "/bundle.txt"
        path.write_text(json.dumps(trial))
    assert (
        validate_results(raw, case, root, loaded.case(case.case_id).root, loaded.definition.scoring)["expected_pairs"]
        == 9
    )
