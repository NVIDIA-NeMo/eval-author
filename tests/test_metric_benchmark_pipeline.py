# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import subprocess
import sys

import pytest
import yaml
from metric_benchmark.config import load_benchmark
from metric_benchmark.execution import case_root, execute_benchmark
from metric_benchmark.io import read_json
from metric_benchmark.pipeline import run_experiment
from test_metric_benchmark_config import LAB, prepared_benchmark
from test_metric_benchmark_execution import fake_harbor


def experiment(tmp_path, *, two_cases=False):
    path, config = prepared_benchmark(tmp_path / "inputs")
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""import argparse, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--output');a=p.parse_args()
r=json.loads(Path(a.request).read_text())
assert 'mutant' not in json.dumps(r)
covered = {e['task']['task_id'] for e in r['baseline_evidence']}
required = set(r['case_description']['required'])
Path(a.output).write_text(json.dumps({'value': len(covered & required)/len(required)}))
""")
    config["cases"][0]["coverage"]["command"] = [sys.executable, str(adapter)]
    if two_cases:
        other = copy.deepcopy(config["cases"][0])
        other["case_id"] = "another-case"
        config["cases"].append(other)
    path.write_text(yaml.safe_dump(config))
    return path, adapter


def test_end_to_end_libraries_then_cli_replay(tmp_path):
    path, adapter = experiment(tmp_path, two_cases=True)
    output = tmp_path / "out"
    result = run_experiment(path, "run", output, runner=fake_harbor)
    assert set(result["cases"]) == {"case", "another-case"}
    assert (output / "analysis/case-0000/subsets.json").read_bytes() == (path.parent / "subsets.json").read_bytes()
    adapter.write_text("raise AssertionError('must use receipts')")
    subprocess.run(
        [
            sys.executable,
            str(LAB / "run_experiment.py"),
            "--benchmark",
            str(path),
            "--run-id",
            "run",
            "--output",
            str(output),
            "--results",
            str(output / "execution"),
            "--cached-only",
        ],
        check=True,
    )
    assert read_json(output / "experiment.json") == result
    correlation = read_json(output / "analysis/case-0000/correlation.json")
    assert isinstance(correlation, dict)
    assert correlation["coefficient"]["value"] is None  # All Oracle-NOP losses equal one.


def test_manual_stage_clis_match_orchestration(tmp_path):
    path, _ = experiment(tmp_path)
    output = tmp_path / "out"
    run_experiment(path, "run", output, runner=fake_harbor)
    analysis = output / "analysis/case-0000"
    native = case_root(output / "execution", 0)
    manual = tmp_path / "manual"
    manual.mkdir()

    def cli(name, *args):
        subprocess.run([sys.executable, str(LAB / name), *map(str, args)], check=True)

    cli(
        "validate_results.py",
        "--benchmark",
        path,
        "--case",
        "case",
        "--results-root",
        native,
        "--output",
        manual / "validation.json",
    )
    cli(
        "compute_mutant_scores.py",
        "--results",
        native / "results.json",
        "--subsets",
        path.parent / "subsets.json",
        "--config",
        analysis / "selection.json",
        "--output",
        manual / "mutant-scores.json",
    )
    cli(
        "compute_coverage.py",
        "--results",
        native / "results.json",
        "--subsets",
        path.parent / "subsets.json",
        "--selection",
        analysis / "selection.json",
        "--config",
        analysis / "coverage-config.json",
        "--case-description",
        path.parent / "description.json",
        "--record-root",
        native,
        "--work-root",
        manual / "coverage-evidence",
        "--output",
        manual / "coverage-scores.json",
    )
    cli(
        "correlate.py",
        "--coverage",
        manual / "coverage-scores.json",
        "--mutant-scores",
        manual / "mutant-scores.json",
        "--output",
        manual / "correlation.json",
    )
    for name in ["validation.json", "mutant-scores.json", "coverage-scores.json", "correlation.json"]:
        assert (manual / name).read_bytes() == (analysis / name).read_bytes()


def test_malformed_later_case_fails_before_any_coverage(tmp_path):
    path, adapter = experiment(tmp_path, two_cases=True)
    native = tmp_path / "native"
    execute_benchmark(load_benchmark(path), "run", native, runner=fake_harbor)
    (case_root(native, 1) / "results.json").write_text("{}")
    marker = tmp_path / "called"
    adapter.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError):
        run_experiment(path, "run", tmp_path / "out", results_root=native)
    assert not marker.exists()


def test_changed_evidence_and_config_refuse_resume(tmp_path):
    path, _ = experiment(tmp_path)
    out = tmp_path / "out"
    run_experiment(path, "run", out, runner=fake_harbor)
    with pytest.raises(ValueError, match="different contents"):
        run_experiment(path, "different-run", out, results_root=out / "execution")
    (case_root(out / "execution", 0) / "new-file").write_text("changed")
    with pytest.raises(ValueError, match="different contents"):
        run_experiment(path, "run", out, results_root=out / "execution", cached_only=True)


def test_cached_only_never_executes_agents(tmp_path):
    path, _ = experiment(tmp_path)
    with pytest.raises(ValueError, match="existing results_root"):
        run_experiment(path, "run", tmp_path / "out", cached_only=True)
