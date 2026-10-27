# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import input_digest, load_benchmark, read_benchmark
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import Score, Subsets, Tasks

LAB = Path(__file__).resolve().parents[1] / "labs/metric-benchmark"
BENCHMARK_YAML = Path(__file__).parent / "fixtures/metric_benchmark/benchmark.yml"


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
    definition = yaml.safe_load(BENCHMARK_YAML.read_text(encoding="utf-8"))
    tasks = Tasks.model_validate(definition["cases"][0]["task_inventory"])
    for task in tasks.tasks:
        folder = root / task.task_id
        folder.mkdir()
        (folder / "instruction.md").write_text("Synthetic test task.\n")
        (folder / "task.toml").write_text('version = "1.0"\n')
        (folder / "environment").mkdir()
        (folder / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
        (folder / "tests").mkdir()
        (folder / "tests/test.sh").write_text("#!/bin/bash\necho 0 > /logs/verifier/reward.txt\n")
    write_json(root / "subsets.json", fixed_subsets(tasks))
    write_json(root / "description.json", {"required": ["task-0", "task-1"]})
    path = root / "benchmark.yaml"
    path.write_bytes(BENCHMARK_YAML.read_bytes())
    return path, definition


def test_prepared_inputs_and_fixed_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    loaded = load_benchmark(path)
    assert loaded == load_benchmark(path)
    assert loaded.subsets["case"].sampling_parameters == {"seed": 42}
    assert loaded.definition.cases[0].selection({"baseline": "baseline-job", "mutant": "mutant-job"}).run_ids == {
        "baseline": "baseline-job",
        "mutant": "mutant-job",
    }
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


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["harbor", "settings"])
def test_nonfinite_native_settings_rejected_before_serialization(tmp_path, value, field):
    path, definition = prepared_benchmark(tmp_path)
    case = definition["cases"][0]
    target = case["baseline"]["harbor"] if field == "harbor" else case["coverage"].setdefault("settings", {})
    target["nested"] = {"values": [value]}
    path.write_text(yaml.safe_dump(definition))
    with pytest.raises(
        ValueError, match=rf"cases\.0\.{'baseline.harbor' if field == 'harbor' else 'coverage.settings'}"
    ):
        load_benchmark(path)


def test_yaml_date_rejected_but_explicit_string_and_null_preserved(tmp_path):
    path, definition = prepared_benchmark(tmp_path)
    settings = {"when": date(2020, 1, 1)}
    definition["cases"][0]["coverage"]["settings"] = settings
    path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError, match=r"cases\.0\.coverage\.settings\.when"):
        load_benchmark(path)
    settings.update(when="2020-01-01", limit=None)
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert load_benchmark(path).definition.cases[0].coverage.settings == settings


def test_changed_task_input_names_case_and_task(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    (tmp_path / "task-0/instruction.md").unlink()
    with pytest.raises(ValueError, match="case case: task task-0: input digest differs"):
        load_benchmark(path)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), date(2020, 1, 1)])
def test_library_configuration_rejects_non_json_values(value):
    from metric_benchmark.config import Agent, CoverageConfig, LocalExecution

    for model, fields in [
        (Agent, {"agent_bundle_id": "agent", "revision": "1", "harbor": {"nested": [value]}}),
        (CoverageConfig, {"system_id": "tool", "system_revision": "1", "command": ["tool"], "settings": {"v": value}}),
        (LocalExecution, {"environment": {"v": value}}),
    ]:
        with pytest.raises(ValueError):
            model.model_validate(fields)


def test_mutated_configuration_cannot_serialize_invalid_json():
    from metric_benchmark.config import CoverageConfig
    from metric_benchmark.io import json_bytes

    config = CoverageConfig(system_id="tool", system_revision="1", command=["tool"])
    for value in [float("inf"), date(2020, 1, 1)]:
        config.settings["v"] = value  # ty: ignore[invalid-assignment] -- exercise an invalid mutable caller
        with pytest.raises((ValueError, TypeError)):
            json_bytes(config)


def test_analysis_definition_and_subsets_do_not_need_execution_inputs(tmp_path):
    import shutil

    path, _ = prepared_benchmark(tmp_path)
    for task in tmp_path.glob("task-*"):
        shutil.rmtree(task)
    (tmp_path / "description.json").unlink()
    definition = read_benchmark(path)
    case = definition.case("case")
    assert len(case.load_subsets(tmp_path, definition.benchmark_id).eval_subsets) == 3
    (tmp_path / "subsets.json").write_text("{}")
    with pytest.raises(ValueError, match="input digest differs"):
        case.load_subsets(tmp_path, definition.benchmark_id)
    with pytest.raises(ValueError, match="unknown case: absent"):
        definition.case("absent")


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field", ["subsets", "case_description"])
@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_case_artifact_errors_have_one_context(tmp_path, standalone, field, failure):
    path, definition = prepared_benchmark(tmp_path)
    source = definition["cases"][0][field]
    artifact = tmp_path / source["path"]
    if failure == "missing":
        artifact.unlink()
        detail = f"missing pinned input: {source['path']}"
    else:
        if field == "subsets":
            data = json.loads(artifact.read_text())
            data["benchmark_id"] = "wrong"
            detail = "subset manifest does not match benchmark identity and task inventory"
        else:
            data = []
            detail = "case description must be a JSON object"
        artifact.write_text(json.dumps(data))
        source["sha256"] = input_digest(artifact)
        path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError) as error:
        if standalone:
            benchmark = read_benchmark(path)
            case = benchmark.case("case")
            if field == "subsets":
                case.load_subsets(tmp_path, benchmark.benchmark_id)
            else:
                case.load_description(tmp_path)
        else:
            load_benchmark(path)
    assert str(error.value) == f"case case, {field}: {detail}"


def test_permission_error_is_not_reported_as_missing(tmp_path, monkeypatch):
    path, _ = prepared_benchmark(tmp_path)
    case = read_benchmark(path).case("case")

    def denied(root, value):
        raise PermissionError("permission denied for prepared input")

    monkeypatch.setattr("metric_benchmark.config.resolve_evidence", denied)
    with pytest.raises(ValueError, match="case case, subsets: permission denied for prepared input"):
        case.load_subsets(tmp_path, "example")
