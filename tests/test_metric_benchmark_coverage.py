# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys

import pytest
from metric_benchmark.coverage import CoverageConfig, invoke_coverage
from metric_benchmark.io import json_bytes
from test_metric_benchmark_scoring import example


def coverage_example(tmp_path):
    raw, subsets, selection = example()
    for row in raw.execution_results:
        folder = tmp_path / "record" / row.native_results_path
        folder.mkdir(parents=True)
        (folder / "trace.json").write_text(json.dumps({"visited": [row.evaluation.task.task_id]}))
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--request')
p.add_argument('--output')
a = p.parse_args()
r = json.loads(Path(a.request).read_text())
assert not any(s in json.dumps(r) for s in ['mutant', 'run-baseline', 'reward'])
visited = set()
for e in r['baseline_evidence']:
    visited.update(json.loads((Path(e['path']) / 'trace.json').read_text())['visited'])
required = set(r['case_description']['required'])
Path(a.output).write_text(json.dumps({'value': len(required & visited) / len(required)}))
""")
    config = CoverageConfig(system_id="synthetic", system_revision="v1", command=[sys.executable, str(adapter)])
    description = {"required": ["inspect-files", "read-config"]}
    return raw, subsets, selection, config, description, tmp_path / "record", tmp_path / "work"


def test_baseline_only_protocol_and_verified_resume(tmp_path):
    args = coverage_example(tmp_path)
    first = invoke_coverage(*args)
    assert [s.value for s in first.scores.values()] == [0.5, 0.5, 0.0]
    # A completed request reuses verified evidence without invoking the external system.
    adapter = args[3].command[1]
    from pathlib import Path

    Path(adapter).write_text("raise RuntimeError('must not rerun')")
    assert json_bytes(invoke_coverage(*args)) == json_bytes(first)
    args[-1].joinpath("subset-000001/response.json").write_text('{"value": 99}')
    with pytest.raises(ValueError, match="evidence changed"):
        invoke_coverage(*args)


def test_changed_request_or_configuration_refuses_reuse(tmp_path):
    args = coverage_example(tmp_path)
    invoke_coverage(*args)
    args[4]["required"].append("search-workspace")
    with pytest.raises(ValueError, match="request differs"):
        invoke_coverage(*args)


@pytest.mark.parametrize("mode", ["failure", "nonfinite", "modified", "symlink"])
def test_bad_system_outputs_cannot_complete_stage(tmp_path, mode):
    args = coverage_example(tmp_path)
    from pathlib import Path

    program = {
        "failure": "raise SystemExit(4)",
        "nonfinite": "from pathlib import Path; Path('response.json').write_text('{\"value\": NaN}')",
        "modified": "from pathlib import Path; Path('request.json').write_text('{}'); Path('response.json').write_text('{\"value\": 1}')",
        "symlink": "from pathlib import Path; Path('response.json').symlink_to('/etc/hosts')",
    }[mode]
    Path(args[3].command[1]).write_text(program)
    with pytest.raises(ValueError):
        invoke_coverage(*args)
    assert not (args[-1] / "subset-000001").exists()


def test_missing_or_escaping_baseline_evidence_fails(tmp_path):
    args = coverage_example(tmp_path)
    path = args[-2] / args[0].execution_results[0].native_results_path / "escape"
    path.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="ordinary"):
        invoke_coverage(*args)


def test_cached_only_requires_completed_coverage(tmp_path):
    args = coverage_example(tmp_path)
    with pytest.raises(ValueError, match="request is missing"):
        invoke_coverage(*args, cached_only=True)


def test_failed_subset_resumes_without_replaying_completed_subset(tmp_path):
    from pathlib import Path

    args = coverage_example(tmp_path)
    adapter = Path(args[3].command[1])
    source = adapter.read_text()
    adapter.write_text(
        source.replace(
            "visited = set()", "if r['eval_subset_id'] == 'subset-000002': raise SystemExit(3)\nvisited = set()"
        )
    )
    with pytest.raises(ValueError, match="exited"):
        invoke_coverage(*args)
    assert (args[-1] / "subset-000001/receipt.json").exists()
    adapter.write_text(
        source.replace("visited = set()", "assert r['eval_subset_id'] != 'subset-000001'\nvisited = set()")
    )
    assert len(invoke_coverage(*args).scores) == 3


def test_timeout_preserves_diagnostics_and_no_completion(tmp_path):
    from pathlib import Path

    args = coverage_example(tmp_path)
    Path(args[3].command[1]).write_text("import time\ntime.sleep(60)\n")
    args[3].timeout_seconds = 1
    with pytest.raises(ValueError, match="timed out"):
        invoke_coverage(*args)
    assert len(list(args[-1].glob("failed-*/stderr.log"))) == 1
    assert not (args[-1] / "subset-000001").exists()


def test_benchmark_cli_runs_cold_without_selection_or_coverage_config_files(tmp_path):
    import subprocess

    import yaml
    from metric_benchmark.config import read_benchmark
    from metric_benchmark.io import write_json
    from test_metric_benchmark_config import LAB, case_definition, prepared_benchmark, write_case

    args = coverage_example(tmp_path)
    path, definition = prepared_benchmark(tmp_path / "inputs")
    case = case_definition(path)
    case["mutants"] = [agent for agent in case["mutants"] if agent["agent_bundle_id"] in args[2].mutant_bundle_ids]
    write_case(path, case)
    definition = yaml.safe_load(path.read_text())
    definition["coverage"] = args[3].model_dump(mode="json")
    path.write_text(yaml.safe_dump(definition))
    write_json(args[-2] / "results.json", args[0])
    write_json(
        tmp_path / "execution.json",
        {
            "benchmark_sha256": read_benchmark(path).identity,
            "run_id": args[0].run_id,
            "cases": {"rho-agent": (args[-2].relative_to(tmp_path) / "results.json").as_posix()},
        },
    )
    expected = invoke_coverage(*args)
    import shutil
    from pathlib import Path

    for source in case_definition(path)["task_sources"].values():
        shutil.rmtree(path.parent / source["path"])
    output = tmp_path / "cli-coverage.json"
    command = [
        sys.executable,
        str(LAB / "compute_coverage.py"),
        "--benchmark",
        str(path),
        "--case",
        "rho-agent",
        "--results-root",
        str(tmp_path),
        "--work-root",
        str(tmp_path / "cold-cli"),
        "--output",
        str(output),
    ]
    subprocess.run(command, check=True)
    assert output.read_bytes() == json_bytes(expected)
    Path(args[3].command[1]).write_text("raise AssertionError('cached CLI must not invoke adapter')")
    subprocess.run([*command, "--cached-only"], check=True)
    assert output.read_bytes() == json_bytes(expected)


@pytest.mark.parametrize("change", ["subset", "selection", "configuration"])
def test_mutable_coverage_inputs_fail_before_adapter_work(tmp_path, change):
    args = coverage_example(tmp_path)
    if change == "subset":
        args[1].eval_subsets.append(args[1].eval_subsets[0].model_copy(deep=True))
    elif change == "selection":
        args[2].run_ids.pop("mutant")
    else:
        args[3].settings["invalid"] = float("inf")
    with pytest.raises(ValueError):
        invoke_coverage(*args)
    assert not args[-1].exists()
