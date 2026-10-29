# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import TrialResult
from metric_benchmark.config import ScoringPolicy, load_cases
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import RawResults
from metric_benchmark.validation import agent_config, task_checksum, validate_experiment_results, validate_results
from test_metric_benchmark_config import FIXTURE, LAB, case_definition, prepared_benchmark, write_case, write_definition

RESULT_FIXTURE = FIXTURE.parent / "metric_benchmark_results"
CASE_RESULTS = Path("cases/case-0000/execution-results")


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
    loaded = load_cases(path.parent / "cases.yml")
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
            "task_inventory": [task.model_dump() for task in case.task_inventory.tasks],
            "execution_results": rows,
        }
    )
    write_json(root / "results.json", raw)
    return loaded, case, raw, root, path


@pytest.mark.parametrize("field", ["task_inventory", "execution_results"])
@pytest.mark.parametrize("shape", ["missing", "empty", "object"])
def test_results_arrays_are_required_and_nonempty(field, shape):
    data = read_json(RESULT_FIXTURE / "experiment-results" / CASE_RESULTS / "results.json")
    assert isinstance(data, dict)
    if shape == "missing":
        del data[field]
    elif shape == "empty":
        data[field] = []
    else:
        data[field] = {"tasks": data[field]}
    with pytest.raises(ValueError, match=field):
        RawResults.model_validate(data)


def test_results_task_inventory_rejects_duplicate_ids():
    data = read_json(RESULT_FIXTURE / "experiment-results" / CASE_RESULTS / "results.json")
    assert isinstance(data, dict)
    data["task_inventory"].append(dict(data["task_inventory"][0]))
    with pytest.raises(ValueError, match="duplicate task IDs"):
        RawResults.model_validate(data)


@pytest.mark.parametrize("case_id", [None, "rho-agent"])
def test_contract_and_cli_parity(tmp_path, case_id):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_cases(path.parent / "cases.yml")
    case = loaded.case("rho-agent").case
    root = tmp_path / "experiment-results"
    shutil.copytree(RESULT_FIXTURE / "experiment-results", root)
    raw = RawResults.model_validate(read_json(root / CASE_RESULTS / "results.json"))
    case_report = validate_results(raw, case, root / CASE_RESULTS, loaded.case(case.case_id).root)
    report = validate_experiment_results(loaded, root, case_id=case_id)
    assert case_report["expected_pairs"] == 9
    assert report["cases"]["rho-agent"] == case_report
    assert report == read_json(RESULT_FIXTURE / "validation.json")
    out = tmp_path / "validation.json"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "validate_results.py"),
            "--cases",
            str(path.parent / "cases.yml"),
            *(["--case", case_id] if case_id is not None else []),
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
        validate_results(raw, case, root, loaded.case(case.case_id).root)


def test_explicit_missing_observation_is_not_zero(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path)
    row = raw.execution_results[0]
    row.native_results_path = "unavailable/inspect-files"
    row.status = "missing"
    row.metrics["reward"].value = None
    row.metrics["reward"].reason = "worker never started"
    row.unavailable_evidence = {"native_results": "worker never started"}
    report = validate_results(raw, case, root, loaded.case(case.case_id).root)
    assert report["unavailable"][0]["reason"] == "worker never started"


def test_relocated_bundle_and_native_evidence_validate_without_original_paths(tmp_path):
    original = tmp_path / "producer"
    loaded, case, raw, root, path = result_example(original, bundle=True)
    before = validate_results(raw, case, root, loaded.case(case.case_id).root)
    moved = tmp_path / "consumer"
    shutil.move(original, moved)
    loaded = load_cases(moved / "inputs/cases.yml")
    case = loaded.case("rho-agent").case
    assert validate_results(raw, case, moved / "results", loaded.case(case.case_id).root) == before
    native_path = moved / "results" / raw.execution_results[0].native_results_path / "result.json"
    native = json.loads(native_path.read_text())
    native["config"]["agent"]["kwargs"]["bundle"] = "/unrelated/bundle.txt"
    native_path.write_text(json.dumps(native))
    with pytest.raises(ValueError, match="case rho-agent, agent baseline, task inspect-files.*agent configuration"):
        validate_results(raw, case, moved / "results", loaded.case(case.case_id).root)


def test_empty_agent_does_not_default_to_oracle(tmp_path):
    from metric_benchmark.config import Agent

    with pytest.raises(ValueError, match="exactly one"):
        agent_config(Agent(agent_bundle_id="mutant", revision="1", harbor={}), tmp_path)


def test_executor_run_names_are_not_directory_conventions(tmp_path):
    loaded, case, raw, root, _ = result_example(tmp_path)
    for row in raw.execution_results:
        row.agent_run_id = "external-job:" + row.agent_bundle_id
    assert validate_results(raw, case, root, loaded.case(case.case_id).root)["expected_pairs"] == 9


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
    assert validate_results(raw, case, root, loaded.case(case.case_id).root)["expected_pairs"] == 9


def test_all_cases_and_case_filter_use_the_same_root(tmp_path):
    path, definition = prepared_benchmark(tmp_path / "inputs")
    cases = yaml.safe_load((path.parent / "cases.yml").read_text())
    cases.append({**cases[0], "case_id": "second-case"})
    write_definition(path, "cases.yml", cases)
    loaded = load_cases(path.parent / "cases.yml")
    root = tmp_path / "experiment-results"
    shutil.copytree(RESULT_FIXTURE / "experiment-results", root)
    second = Path("cases/case-0001/execution-results")
    shutil.copytree(root / CASE_RESULTS, root / second)
    manifest = json.loads((root / "execution.json").read_text())
    manifest["cases_sha256"] = loaded.identity
    manifest["cases"]["second-case"] = (second / "results.json").as_posix()
    (root / "execution.json").write_text(json.dumps(manifest))
    report = validate_experiment_results(loaded, root)
    assert set(report["cases"]) == {"rho-agent", "second-case"}
    assert all(case["observations"] == 9 for case in report["cases"].values())
    (root / second / "results.json").unlink()
    assert validate_experiment_results(loaded, root, case_id="rho-agent") == {
        "run_id": report["run_id"],
        "cases": {"rho-agent": report["cases"]["rho-agent"]},
    }
    with pytest.raises(ValueError, match="case second-case"):
        validate_experiment_results(loaded, root)
    del manifest["cases"]["second-case"]
    (root / "execution.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="missing:.*second-case"):
        validate_experiment_results(loaded, root)
    with pytest.raises(ValueError, match="unknown case: absent"):
        validate_experiment_results(loaded, root, case_id="absent")


@pytest.mark.parametrize("change", ["unknown-case", "run-id", "traversal", "symlink"])
def test_invalid_experiment_index_rejected_without_report(tmp_path, change):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    root = tmp_path / "experiment-results"
    shutil.copytree(RESULT_FIXTURE / "experiment-results", root)
    manifest_path = root / "execution.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "unknown-case":
        manifest["cases"]["unknown"] = manifest["cases"]["rho-agent"]
    elif change == "run-id":
        manifest["run_id"] = "another-run"
    elif change == "traversal":
        manifest["cases"]["rho-agent"] = "../results.json"
    else:
        (root / "linked").symlink_to(root / CASE_RESULTS, target_is_directory=True)
        manifest["cases"]["rho-agent"] = "linked/results.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "validation.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(LAB / "validate_results.py"),
            "--cases",
            str(path.parent / "cases.yml"),
            "--results-root",
            str(root),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert not output.exists()
