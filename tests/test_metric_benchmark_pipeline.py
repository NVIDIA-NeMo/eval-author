# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys

import pytest
import yaml
from metric_benchmark.config import load_benchmark
from metric_benchmark.execution import case_root, execute_benchmark
from metric_benchmark.io import read_json
from metric_benchmark.pipeline import run_experiment
from test_metric_benchmark_config import LAB, SUBSETS_PATH, prepared_benchmark
from test_metric_benchmark_execution import fake_harbor
from test_metric_benchmark_signing import remove_pins


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
    config["coverage"]["command"] = [sys.executable, str(adapter)]
    if two_cases:
        config["cases"].append({**config["cases"][0], "case_id": "another-case"})
    path.write_text(yaml.safe_dump(config))
    return path, adapter


def test_end_to_end_libraries_then_cli_replay(tmp_path):
    path, adapter = experiment(tmp_path, two_cases=True)
    output = tmp_path / "out"
    result = run_experiment(path, "run", output, runner=fake_harbor)
    assert set(result["cases"]) == {"rho-agent", "another-case"}
    assert (output / "analysis/case-0000/subsets.json").read_bytes() == (path.parent / SUBSETS_PATH).read_bytes()
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


@pytest.mark.parametrize("case_id,ordinal", [("rho-agent", 0), ("another-case", 1)])
def test_manual_stage_clis_match_orchestration(tmp_path, case_id, ordinal):
    path, _ = experiment(tmp_path, two_cases=True)
    output = tmp_path / "out"
    run_experiment(path, "run", output, runner=fake_harbor)
    analysis = output / "analysis" / f"case-{ordinal:04d}"
    native = output / "execution"
    manual = tmp_path / "manual"
    manual.mkdir()

    def cli(name, *args):
        subprocess.run([sys.executable, str(LAB / name), *map(str, args)], check=True)

    cli(
        "validate_results.py",
        "--benchmark",
        path,
        "--results-root",
        native,
        "--output",
        manual / "validation.json",
    )
    cli(
        "compute_mutant_scores.py",
        "--benchmark",
        path,
        "--case",
        case_id,
        "--results-root",
        native,
        "--output",
        manual / "mutant-scores.json",
    )
    cli(
        "compute_coverage.py",
        "--benchmark",
        path,
        "--case",
        case_id,
        "--results-root",
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
    assert (manual / "validation.json").read_bytes() == (output / "validation.json").read_bytes()
    for name in ["mutant-scores.json", "coverage-scores.json", "correlation.json"]:
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


def test_shipped_smoke_definition_pins_are_current():
    loaded = load_benchmark(LAB / "examples/smoke/experiment.yml")
    assert len(loaded.cases) == 1
    assert len(loaded.subsets[next(iter(loaded.cases))].eval_subsets) == 3


def test_changed_analysis_version_requires_new_output_but_reuses_execution(tmp_path, monkeypatch):
    import metric_benchmark.pipeline as pipeline

    path, _ = experiment(tmp_path)
    out = tmp_path / "out"
    run_experiment(path, "run", out, runner=fake_harbor)
    monkeypatch.setattr(pipeline, "implementation", lambda: {"sources": {"test": "changed"}})
    with pytest.raises(ValueError, match="new --output directory and --results"):
        run_experiment(path, "run", out, results_root=out / "execution", cached_only=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("saved execution must not be rerun")

    monkeypatch.setattr(pipeline, "execute_benchmark", forbidden)
    assert run_experiment(path, "run", tmp_path / "new-analysis", results_root=out / "execution")["cases"]


def test_post_analysis_evidence_change_prevents_completion(tmp_path):
    path, adapter = experiment(tmp_path)
    native = tmp_path / "native"
    execute_benchmark(load_benchmark(path), "run", native, runner=fake_harbor)
    changed = case_root(native, 0) / "changed-during-analysis.txt"
    adapter.write_text(adapter.read_text() + f"\nPath({str(changed)!r}).write_text('accidental modification')\n")
    output = tmp_path / "analysis"
    with pytest.raises(ValueError, match="execution evidence changed during analysis"):
        run_experiment(path, "run", output, results_root=native)
    assert changed.exists()
    assert not (output / "experiment.json").exists()


def test_saved_execution_manifest_rejects_another_run(tmp_path):
    path, _ = experiment(tmp_path)
    native = tmp_path / "native"
    execute_benchmark(load_benchmark(path), "run", native, runner=fake_harbor)
    with pytest.raises(ValueError, match="execution manifest does not match benchmark/run identity"):
        run_experiment(path, "another-run", tmp_path / "analysis", results_root=native)


def test_unsigned_opt_in_reaches_execution_and_each_benchmark_cli(tmp_path):
    path, _ = experiment(tmp_path)
    remove_pins(path.parent)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="missing SHA"):
        run_experiment(path, "run", output, runner=fake_harbor)
    run_experiment(path, "run", output, allow_unsigned=True, runner=fake_harbor)
    native = output / "execution"
    analysis = output / "analysis/case-0000"
    commands = [
        ("run_evals.py", ["--run-id", "run", "--output", str(native)]),
        (
            "validate_results.py",
            ["--case", "rho-agent", "--results-root", str(native), "--output", str(tmp_path / "validation.json")],
        ),
        (
            "compute_mutant_scores.py",
            ["--case", "rho-agent", "--results-root", str(native), "--output", str(tmp_path / "mutants.json")],
        ),
        (
            "compute_coverage.py",
            [
                "--case",
                "rho-agent",
                "--results-root",
                str(native),
                "--output",
                str(tmp_path / "coverage.json"),
                "--work-root",
                str(analysis / "coverage-evidence"),
                "--cached-only",
            ],
        ),
        ("run_experiment.py", ["--run-id", "run", "--output", str(output), "--results", str(native), "--cached-only"]),
    ]
    for tool, arguments in commands:
        command = [sys.executable, str(LAB / tool), "--benchmark", str(path), *arguments]
        strict = subprocess.run(command, capture_output=True, text=True)
        assert strict.returncode == 2 and "missing SHA" in strict.stderr, (tool, strict.stderr)
        unsigned = subprocess.run([*command, "--allow-unsigned"], capture_output=True, text=True)
        assert unsigned.returncode == 0, (tool, unsigned.stderr)
    assert read_json(tmp_path / "mutants.json") == read_json(analysis / "mutant-scores.json")
    assert read_json(tmp_path / "coverage.json") == read_json(analysis / "coverage-scores.json")


@pytest.mark.parametrize("during_analysis", [False, True])
def test_unsigned_input_changes_reject_saved_execution(tmp_path, during_analysis):
    path, adapter = experiment(tmp_path)
    remove_pins(path.parent)
    native = tmp_path / "native"
    execute_benchmark(load_benchmark(path, allow_unsigned=True), "run", native, runner=fake_harbor)
    changed = path.parent / SUBSETS_PATH
    if during_analysis:
        adapter.write_text(adapter.read_text() + f"\nwith Path({str(changed)!r}).open('a') as f: f.write('\\n')\n")
        message = "benchmark inputs changed during execution"
    else:
        with changed.open("a") as stream:
            stream.write("\n")
        adapter.write_text("raise AssertionError('changed inputs must fail before coverage')")
        message = "execution manifest does not match benchmark/run identity"
    output = tmp_path / "analysis"
    with pytest.raises(ValueError, match=message):
        run_experiment(path, "run", output, results_root=native, allow_unsigned=True)
    assert not (output / "experiment.json").exists()


def test_shared_validator_rejects_reused_case_directory_before_coverage(tmp_path):
    import json

    path, adapter = experiment(tmp_path, two_cases=True)
    native = tmp_path / "native"
    execute_benchmark(load_benchmark(path), "run", native, runner=fake_harbor)
    manifest_path = native / "execution.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"]["another-case"] = manifest["cases"]["rho-agent"]
    manifest_path.write_text(json.dumps(manifest))
    adapter.write_text("raise AssertionError('invalid results must fail before coverage')")
    with pytest.raises(ValueError, match="results directory reused by multiple cases"):
        run_experiment(path, "run", tmp_path / "analysis", results_root=native)
