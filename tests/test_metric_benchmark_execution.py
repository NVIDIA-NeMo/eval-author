# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from metric_benchmark.config import load_benchmark
from metric_benchmark.execution import case_root, execute_benchmark
from metric_benchmark.io import read_json, write_json
from test_metric_benchmark_config import prepared_benchmark
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
    assert calls == ["oracle", "nop"]
    assert execute_benchmark(loaded, "run", output, runner=runner) == first
    assert calls == ["oracle", "nop"]
    raw = read_json(case_root(output, 0) / "results.json")
    assert isinstance(raw, dict)
    assert len(raw["results"]) == 6
    with pytest.raises(ValueError, match="different contents"):
        execute_benchmark(loaded, "other-run", output, runner=runner)
    evidence = case_root(output, 0) / raw["results"][0]["native_results_path"] / "result.json"
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="job changed"):
        execute_benchmark(loaded, "run", output, runner=runner)


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
    assert calls == ["nop"]


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
    """Opt-in: six Docker trials, no model calls or remote benchmark service."""
    import os
    import subprocess
    import sys

    import yaml
    from metric_benchmark.config import input_digest
    from test_metric_benchmark_config import LAB

    if os.environ.get("METRIC_BENCHMARK_DOCKER_TEST") != "1":
        pytest.skip("set METRIC_BENCHMARK_DOCKER_TEST=1 for local Docker smoke")
    path, definition = prepared_benchmark(tmp_path / "inputs")
    for task_id, source in definition["cases"][0]["task_sources"].items():
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
    path.write_text(yaml.safe_dump(definition))
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
    assert [r["metrics"]["reward"]["value"] for r in raw["results"]] == [1.0] * 3 + [0.0] * 3
