# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
from metric_benchmark.config import load_benchmark
from metric_benchmark.execution import case_root, execute_benchmark
from metric_benchmark.io import json_bytes, read_json, write_json
from test_metric_benchmark_config import case_definition, prepared_benchmark, write_case
from test_metric_benchmark_validation import native_trial


def fake_harbor(config):
    directory = config.jobs_dir / config.job_name
    directory.mkdir(parents=True)
    write_json(directory / "result.json", {"finished_at": "2026-01-01T00:00:00"})
    for task in config.tasks:
        native = directory / (task.path.name + "__arbitrary")
        native.mkdir()
        result = native_trial(task.path, native, config.agents[0], float(config.agents[0].name == "oracle"))
        write_json(native / "result.json", result)


def test_order_full_matrix_and_verified_resume(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    calls = []

    def runner(config):
        calls.append(config.agents[0].name)
        assert config.n_attempts == 1 and config.retry.max_retries == 0
        fake_harbor(config)

    output = tmp_path / "out"
    first = execute_benchmark(loaded, "run", output, runner=runner)
    assert calls == ["oracle", "nop", "nop"]
    assert execute_benchmark(loaded, "run", output, runner=runner) == first
    assert calls == ["oracle", "nop", "nop"]
    raw = read_json(case_root(output, 0) / "results.json")
    assert isinstance(raw, dict)
    assert len(raw["execution_results"]) == 9
    with pytest.raises(ValueError, match="changed; use a new output directory"):
        execute_benchmark(loaded, "other-run", output, runner=runner)
    evidence = case_root(output, 0) / raw["execution_results"][0]["native_results_path"] / "result.json"
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="job changed"):
        execute_benchmark(loaded, "run", output, runner=runner)


def test_two_cases_produce_distinct_results_and_resume(tmp_path):
    import yaml
    from metric_benchmark.io import input_digest
    from metric_benchmark.validation import validate_experiment_results

    path, definition = prepared_benchmark(tmp_path / "inputs")
    first = definition["cases"][0]
    agent_path = path.parent / "second-agent.yml"
    agents = yaml.safe_load((path.parent / first["agent"]["path"]).read_text())
    agents["baseline"]["agent_bundle_id"] = "second-baseline"
    agents["baseline"]["harbor"] = {"name": "nop"}
    agents["mutants"] = [{**agents["mutants"][0], "agent_bundle_id": "second-mutant", "harbor": {"name": "oracle"}}]
    agent_path.write_text(yaml.safe_dump(agents))
    definition["cases"].append(
        {**first, "case_id": "second-case", "agent": {"path": agent_path.name, "sha256": input_digest(agent_path)}}
    )
    path.write_text(yaml.safe_dump(definition))
    loaded = load_benchmark(path)
    output = tmp_path / "out"
    calls = []

    def runner(config):
        calls.append((config.jobs_dir.relative_to(output).parts[1], config.agents[0].name))
        fake_harbor(config)

    manifest = execute_benchmark(loaded, "run", output, runner=runner)
    assert calls == [
        ("case-0000", "oracle"),
        ("case-0000", "nop"),
        ("case-0000", "nop"),
        ("case-0001", "nop"),
        ("case-0001", "oracle"),
    ]
    assert manifest["cases"] == {
        first["case_id"]: "cases/case-0000/execution-results/results.json",
        "second-case": "cases/case-0001/execution-results/results.json",
    }
    for ordinal, prepared in enumerate(loaded.cases.values()):
        raw = read_json(case_root(output, ordinal) / "results.json")
        assert isinstance(raw, dict)
        rows = raw["execution_results"]
        assert {(row["agent_bundle_id"], row["evaluation"]["task"]["task_id"]) for row in rows} == {
            (agent.agent_bundle_id, task.task_id)
            for agent in prepared.case.agents()
            for task in prepared.case.task_inventory.tasks
        }
        assert [row["metrics"]["reward"]["value"] for row in rows] == (
            [1.0] * 3 + [0.0] * 6 if ordinal == 0 else [0.0] * 3 + [1.0] * 3
        )
    validate_experiment_results(loaded, output)
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert execute_benchmark(loaded, "run", output, runner=lambda _: pytest.fail("must not replay")) == manifest
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == before


def test_failure_between_agents_reuses_completed_job(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    output = tmp_path / "out"

    def failing(config):
        if config.agents[0].name == "nop":
            raise ValueError("worker unavailable")
        fake_harbor(config)

    with pytest.raises(ValueError, match="worker unavailable"):
        execute_benchmark(loaded, "run", output, runner=failing)
    calls = []

    def resume(config):
        calls.append(config.agents[0].name)
        fake_harbor(config)

    execute_benchmark(loaded, "run", output, runner=resume)
    assert calls == ["nop", "nop"]


def test_sealed_jobs_are_revalidated_after_validation_failure(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    output = tmp_path / "out"

    def invalid_harbor(config):
        fake_harbor(config)
        # Collection accepts this trial, but full validation must reject it after sealing.
        trial_path = next((config.jobs_dir / config.job_name).glob("*/result.json"))
        trial = read_json(trial_path)
        assert isinstance(trial, dict)
        trial["task_checksum"] = "0" * 64
        trial_path.write_bytes(json_bytes(trial))

    with pytest.raises(ValueError, match="native task identity or checksum differs from prepared input"):
        execute_benchmark(loaded, "run", output, runner=invalid_harbor)
    root = case_root(output, 0)
    assert len(list(root.glob("agents/*/receipt.json"))) == 3
    assert not (root / "results.json").exists()
    assert not (output / "execution.json").exists()
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}

    with pytest.raises(ValueError, match="native task identity or checksum differs from prepared input"):
        execute_benchmark(loaded, "run", output, runner=lambda _: pytest.fail("sealed jobs must not rerun"))
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == before


def test_partial_native_job_is_not_rerun(tmp_path):
    path, _ = prepared_benchmark(tmp_path / "inputs")
    loaded = load_benchmark(path)
    output = tmp_path / "out"

    def partial(config):
        folder = config.jobs_dir / config.job_name
        folder.mkdir(parents=True)
        write_json(folder / "result.json", {"finished_at": None})

    with pytest.raises(ValueError, match="not finished"):
        execute_benchmark(loaded, "run", output, runner=partial)

    def forbidden(config):
        raise AssertionError("must not replay")

    with pytest.raises(ValueError, match="not finished"):
        execute_benchmark(loaded, "run", output, runner=forbidden)


def test_native_harbor_cli_smoke(tmp_path):
    """Opt-in: nine Docker trials, no model calls or remote benchmark service."""
    import os
    import subprocess
    import sys

    from metric_benchmark.config import input_digest
    from test_metric_benchmark_config import LAB

    if os.environ.get("METRIC_BENCHMARK_DOCKER_TEST") != "1":
        pytest.skip("set METRIC_BENCHMARK_DOCKER_TEST=1 for local Docker smoke")
    path, definition = prepared_benchmark(tmp_path / "inputs")
    case_data = case_definition(path)
    for task_id, source in case_data["task_sources"].items():
        task = path.parent / source["path"]
        (task / "environment").mkdir(exist_ok=True)
        (task / "environment/Dockerfile").write_text("FROM python:3.12-slim\nWORKDIR /app\n")
        (task / "solution").mkdir()
        (task / "solution/solve.sh").write_text("#!/bin/bash\nprintf ready > /app/answer\n")
        (task / "tests").mkdir(exist_ok=True)
        (task / "tests/test.sh").write_text(
            "#!/bin/bash\nmkdir -p /logs/verifier\n"
            "if test -f /app/answer; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\n"
        )
        (task / "task.toml").write_text('version = "1.0"\n[agent]\ntimeout_sec = 30\n[verifier]\ntimeout_sec = 30\n')
        source["sha256"] = input_digest(task)
    write_case(path, case_data)
    output = tmp_path / "native-run"
    subprocess.run(
        [
            sys.executable,
            str(LAB / "run_evals.py"),
            "--benchmark",
            str(path),
            "--run-id",
            "docker-smoke",
            "--output",
            str(output),
        ],
        check=True,
        timeout=300,
    )
    loaded = load_benchmark(path)
    before = (case_root(output, 0) / "results.json").read_bytes()

    def forbidden(config):
        raise AssertionError("native jobs must be reused")

    execute_benchmark(loaded, "docker-smoke", output, runner=forbidden)
    assert (case_root(output, 0) / "results.json").read_bytes() == before
    raw = read_json(case_root(output, 0) / "results.json")
    assert isinstance(raw, dict)
    assert [r["metrics"]["reward"]["value"] for r in raw["execution_results"]] == [1.0] * 3 + [0.0] * 6
    import shutil

    relocated_inputs = tmp_path / "relocated-inputs"
    relocated_output = tmp_path / "relocated-output"
    shutil.move(path.parent, relocated_inputs)
    shutil.move(output, relocated_output)
    execute_benchmark(load_benchmark(relocated_inputs / path.name), "docker-smoke", relocated_output, runner=forbidden)
    assert (case_root(relocated_output, 0) / "results.json").read_bytes() == before


@pytest.mark.parametrize("unsigned", [False, True])
@pytest.mark.parametrize("move_inputs,move_output", [(False, False), (True, False), (False, True), (True, True)])
def test_move_completed_jobs_and_prepared_bundles_without_reexecution(tmp_path, move_inputs, move_output, unsigned):
    import shutil

    from metric_benchmark.config import input_digest

    original = tmp_path / "producer"
    path, definition = prepared_benchmark(original / "inputs")
    bundle = path.parent / "bundle.txt"
    bundle.write_text("prepared dependency")
    case_data = case_definition(path)
    for agent in [case_data["baseline"], *case_data["mutants"]]:
        agent["inputs"] = {"bundle": {"path": bundle.name, "sha256": input_digest(bundle)}}
        agent["harbor"]["kwargs"] = {"bundle": "{input:bundle}"}
    write_case(path, case_data)
    if unsigned:
        from test_metric_benchmark_signing import remove_pins

        remove_pins(path.parent)
    loaded = load_benchmark(path, allow_unsigned=unsigned)
    output = original / "out"
    execute_benchmark(loaded, "run", output, runner=fake_harbor)
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    moved = tmp_path / "consumer"
    moved.mkdir()
    inputs = path.parent
    if move_inputs:
        inputs = Path(shutil.move(inputs, moved / "inputs"))
    if move_output:
        output = Path(shutil.move(output, moved / "out"))

    def forbidden(config):
        raise AssertionError("relocation must reuse completed native jobs")

    execute_benchmark(
        load_benchmark(inputs / "experiment.yml", allow_unsigned=unsigned), "run", output, runner=forbidden
    )
    assert {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()} == before
    evidence = next(output.glob("cases/*/execution-results/agents/agent-0000/run-0001/*/result.json"))
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="case rho-agent, agent baseline: completed Harbor job changed"):
        execute_benchmark(
            load_benchmark(inputs / "experiment.yml", allow_unsigned=unsigned), "run", output, runner=forbidden
        )


def test_duplicate_task_sources_rejected_before_execution(tmp_path):

    path, definition = prepared_benchmark(tmp_path / "inputs")
    case_data = case_definition(path)
    sources = case_data["task_sources"]
    sources["read-config"] = sources["inspect-files"]
    write_case(path, case_data)
    with pytest.raises(ValueError, match="distinct paths"):
        execute_benchmark(
            load_benchmark(path), "run", tmp_path / "out", runner=lambda _: pytest.fail("must not execute")
        )


def test_logical_request_and_exact_nested_task_bindings(tmp_path):
    import shutil

    path, definition = prepared_benchmark(tmp_path / "inputs")
    case_data = case_definition(path)
    sources = case_data["task_sources"]
    # One source suffix is another's suffix; collection must use the exact saved binding.
    for task_id, relative in [("inspect-files", "tasks/a"), ("read-config", "x/tasks/a")]:
        destination = path.parent / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path.parent / sources[task_id]["path"], destination)
        sources[task_id]["path"] = relative
    write_case(path, case_data)
    loaded = load_benchmark(path)
    output = tmp_path / "out"

    def runner(config):
        directory = config.jobs_dir / config.job_name
        directory.mkdir(parents=True)
        write_json(directory / "result.json", {"finished_at": "2026-01-01T00:00:00"})
        for i, task in enumerate(config.tasks):
            native = directory / f"trial-{i}"
            native.mkdir()
            write_json(native / "result.json", native_trial(task.path, native, config.agents[0], float(i)))

    execute_benchmark(loaded, "run", output, runner=runner)
    parent = case_root(output, 0) / "agents/agent-0000"
    request = read_json(parent / "request.json")
    assert request == {
        "benchmark_sha256": loaded.identity,
        "case_id": "rho-agent",
        "agent_bundle_id": "baseline",
        "execution": loaded.definition.execution.model_dump(mode="json"),
    }
    assert str(tmp_path) not in (parent / "request.json").read_text()
    assert str(path.parent) in (parent / "native-request.json").read_text()
    raw = read_json(case_root(output, 0) / "results.json")
    assert isinstance(raw, dict)
    assert [
        (row["evaluation"]["task"]["task_id"], row["metrics"]["reward"]["value"])
        for row in raw["execution_results"][:3]
    ] == [("inspect-files", 0.0), ("read-config", 1.0), ("search-workspace", 2.0)]
    (parent / "native-request.json").write_text("{}")
    with pytest.raises(ValueError):
        execute_benchmark(loaded, "run", output, runner=lambda _: pytest.fail("must not execute"))


@pytest.mark.parametrize(
    "changed",
    [
        "agents/rho-agent/agent.yml",
        "tasks/workspace/tasks.yml",
        "tasks/workspace/subsets.json",
        "agents/rho-agent/description.json",
        "tasks/workspace/inspect-files/instruction.md",
        "bundle.txt",
    ],
)
def test_unsigned_input_changes_refuse_completed_job_reuse(tmp_path, changed):
    from test_metric_benchmark_signing import remove_pins

    path, _ = prepared_benchmark(tmp_path / "inputs")
    bundle = path.parent / "bundle.txt"
    bundle.write_text("original agent dependency")
    case = case_definition(path)
    case["baseline"]["inputs"] = {"bundle": {"path": bundle.name}}
    case["baseline"]["harbor"]["kwargs"] = {"bundle": "{input:bundle}"}
    write_case(path, case)
    remove_pins(path.parent)
    output = tmp_path / "out"
    execute_benchmark(load_benchmark(path, allow_unsigned=True), "run", output, runner=fake_harbor)
    with (path.parent / changed).open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="changed; use a new output directory"):
        execute_benchmark(
            load_benchmark(path, allow_unsigned=True),
            "run",
            output,
            runner=lambda _: pytest.fail("changed inputs must not resume old work"),
        )
