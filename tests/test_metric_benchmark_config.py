# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import input_digest, load_benchmark
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import Score, Subsets, Tasks

LAB = Path(__file__).resolve().parents[1] / "labs/metric-benchmark"


def inventory(n=3):
    return Tasks.model_validate(
        {
            "benchmark": {"deployment": "local", "benchmark_id": "case", "revision": 1},
            "tasks": [{"task_id": f"task-{i}", "revision": 1} for i in range(n)],
        }
    )


def fixed_subsets(tasks):
    return Subsets.model_validate(
        {
            "benchmark_id": "example",
            "task_inventory": tasks.model_dump(),
            "sampling_parameters": {"seed": 42},
            "eval_subsets": [
                {"eval_subset_id": f"subset-{i + 1:06d}", "tasks": [t.model_dump()]} for i, t in enumerate(tasks.tasks)
            ],
        }
    )


def prepared_benchmark(root):
    """Small prepared inputs for tests; no runtime subset-construction stage."""
    root.mkdir(parents=True, exist_ok=True)
    tasks = inventory()

    def pin(path):
        return {
            "path": path.relative_to(root).as_posix(),
            "sha256": input_digest(path),
            "kind": "directory" if path.is_dir() else "file",
        }

    sources = {}
    for task in tasks.tasks:
        folder = root / task.task_id
        folder.mkdir()
        (folder / "instruction.md").write_text("Synthetic test task.\n")
        (folder / "task.toml").write_text('version = "1.0"\n')
        (folder / "environment").mkdir()
        (folder / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
        (folder / "tests").mkdir()
        (folder / "tests/test.sh").write_text("#!/bin/bash\necho 0 > /logs/verifier/reward.txt\n")
        sources[task.task_id] = pin(folder)
    write_json(root / "subsets.json", fixed_subsets(tasks))
    write_json(root / "description.json", {"required": ["task-0", "task-1"]})
    definition = {
        "benchmark_id": "example",
        "revision": 1,
        "cases": [
            {
                "case_id": "case",
                "task_inventory": tasks.model_dump(),
                "task_sources": sources,
                "case_description": pin(root / "description.json"),
                "subsets": pin(root / "subsets.json"),
                "baseline": {"agent_bundle_id": "baseline", "revision": "1", "harbor": {"name": "oracle"}},
                "mutants": [{"agent_bundle_id": "mutant", "revision": "1", "harbor": {"name": "nop"}}],
                "coverage": {
                    "system_id": "synthetic",
                    "system_revision": "1",
                    "command": [sys.executable, "/adapter.py"],
                },
            }
        ],
    }
    path = root / "benchmark.yaml"
    path.write_text(yaml.safe_dump(definition))
    return path, definition


def test_prepared_inputs_and_fixed_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    loaded = load_benchmark(path)
    assert loaded == load_benchmark(path)
    assert loaded.subsets["case"].sampling_parameters == {"seed": 42}
    assert loaded.definition.cases[0].selection().run_ids["baseline"] == "agent-0000/run-0001"
    (tmp_path / "task-0/instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="digest differs"):
        load_benchmark(path)


@pytest.mark.parametrize("change", ["unknown", "duplicate-case", "subset-id", "source-missing"])
def test_bad_definition(tmp_path, change):
    path, config = prepared_benchmark(tmp_path)
    if change == "unknown":
        config["surprise"] = True
    if change == "duplicate-case":
        config["cases"] *= 2
    if change == "subset-id":
        config["benchmark_id"] = "other"
    if change == "source-missing":
        del config["cases"][0]["task_sources"]["task-0"]
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        load_benchmark(path)


def test_duplicate_yaml_and_paths(tmp_path):
    path, config = prepared_benchmark(tmp_path)
    path.write_text(path.read_text() + "benchmark_id: duplicate\n")
    with pytest.raises(ValueError, match="unique strings"):
        load_benchmark(path)
    config["cases"][0]["subsets"]["path"] = "../outside.json"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="relative"):
        load_benchmark(path)


def test_json_numbers_and_immutable_outputs(tmp_path):
    for value in [True, float("inf"), float("nan")]:
        with pytest.raises(ValueError):
            Score(value=value)
    with pytest.raises(ValueError):
        Score(value=None)
    out = tmp_path / "out.json"
    write_json(out, {"x": 1})
    write_json(out, {"x": 1})
    with pytest.raises(ValueError):
        write_json(out, {"x": 2})
    for content in ['{"x": 1, "x": 2}', '{"x": NaN}']:
        out.write_text(content)
        with pytest.raises(ValueError):
            read_json(out)


def test_manifest_rejects_duplicate_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    manifest = json.loads((tmp_path / "subsets.json").read_text())
    manifest["eval_subsets"][1]["tasks"] = manifest["eval_subsets"][0]["tasks"]
    with pytest.raises(ValueError, match="memberships"):
        Subsets.model_validate(manifest)
