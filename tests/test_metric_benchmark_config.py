# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import input_digest, load_benchmark, read_benchmark
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import Score, Subsets, Tasks

LAB = Path(__file__).resolve().parents[1] / "labs/metric-benchmark"
FIXTURE = Path(__file__).parent / "fixtures/metric_benchmark"
CASE_DIR = Path("cases/rho-agent")
TASK_DIR = Path("tasks/workspace")
SUBSETS_PATH = Path("tasks/workspace/subsets.json")
DESCRIPTION_PATH = Path("agents/rho-agent/description.json")
TASK_IDS = ["inspect-files", "read-config", "search-workspace"]


def inventory(n=3):
    return Tasks.model_validate(
        {
            "tasks": [
                {"task_id": TASK_IDS[i] if i < len(TASK_IDS) else f"additional-task-{i}", "revision": 1}
                for i in range(n)
            ],
        }
    )


def fixed_subsets(tasks):
    return Subsets.model_validate(
        {
            "sampling_parameters": {"seed": 42},
            "eval_subsets": [
                {"eval_subset_id": f"subset-{i + 1:06d}", "task_ids": [t.task_id]} for i, t in enumerate(tasks.tasks)
            ],
        }
    )


def prepared_benchmark(root):
    """Unpack fixed manifests and create their synthetic task sources."""
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    path = root / "experiment.yml"
    definition = yaml.safe_load(path.read_text())
    case = case_definition(path)
    for source in case["task_sources"].values():
        folder = root / source["path"]
        folder.mkdir(parents=True)
        (folder / "instruction.md").write_text("Synthetic test task.\n")
        (folder / "task.toml").write_text('version = "1.0"\n')
        (folder / "environment").mkdir()
        (folder / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
        (folder / "tests").mkdir()
        (folder / "tests/test.sh").write_text("#!/bin/bash\necho 0 > /logs/verifier/reward.txt\n")
    return path, definition


def case_definition(experiment):
    manifest = yaml.safe_load((experiment.parent / CASE_DIR / "case.yml").read_text())
    agent = yaml.safe_load((experiment.parent / manifest["agent"]["path"]).read_text())
    tasks = yaml.safe_load((experiment.parent / manifest["tasks"]["path"]).read_text())
    return {
        "case_id": manifest["case_id"],
        "subsets": manifest["subsets"],
        "baseline": agent["baseline"],
        "mutants": agent["mutants"],
        "case_description": agent["description"],
        **tasks,
    }


def write_case(experiment, case):
    manifest_path = experiment.parent / CASE_DIR / "case.yml"
    manifest = yaml.safe_load(manifest_path.read_text())
    parts = {
        "agent": {"baseline": case["baseline"], "mutants": case["mutants"], "description": case["case_description"]},
        "tasks": {"task_inventory": case["task_inventory"], "task_sources": case["task_sources"]},
    }
    for name, data in parts.items():
        target = experiment.parent / manifest[name]["path"]
        target.write_text(yaml.safe_dump(data, sort_keys=False))
        manifest[name]["sha256"] = input_digest(target)
    manifest.update(case_id=case["case_id"], subsets=case["subsets"])
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    definition = yaml.safe_load(experiment.read_text())
    definition["cases"][0]["sha256"] = input_digest(manifest_path)
    experiment.write_text(yaml.safe_dump(definition, sort_keys=False))


def test_prepared_inputs_and_fixed_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    loaded = load_benchmark(path)
    assert loaded == load_benchmark(path)
    assert loaded.subsets["rho-agent"].sampling_parameters == {"seed": 42}
    assert loaded.case("rho-agent").case.selection(
        {"baseline": "baseline-job", "mutant": "mutant-job", "mutant-no-search": "search-mutant-job"},
        loaded.definition.scoring,
    ).run_ids == {
        "baseline": "baseline-job",
        "mutant": "mutant-job",
        "mutant-no-search": "search-mutant-job",
    }
    (tmp_path / TASK_DIR / "inspect-files/instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="digest differs"):
        load_benchmark(path)


@pytest.mark.parametrize("change", ["unknown", "duplicate-case", "case-pin", "source-missing"])
def test_bad_definition(tmp_path, change):
    path, config = prepared_benchmark(tmp_path)
    if change == "unknown":
        config["surprise"] = True
    elif change == "duplicate-case":
        config["cases"] *= 2
    elif change == "case-pin":
        (tmp_path / CASE_DIR / "case.yml").write_text("changed")
    else:
        case = case_definition(path)
        del case["task_sources"]["inspect-files"]
        write_case(path, case)
        config = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError):
        load_benchmark(path)


def test_duplicate_yaml_and_paths(tmp_path):
    path, config = prepared_benchmark(tmp_path)
    path.write_text(path.read_text() + "benchmark_id: duplicate\n")
    with pytest.raises(ValueError, match="unique strings"):
        load_benchmark(path)
    config["cases"][0]["path"] = "../outside.yml"
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


def test_manifest_allows_duplicate_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    manifest = json.loads((tmp_path / SUBSETS_PATH).read_text())
    manifest["eval_subsets"][1]["task_ids"] = manifest["eval_subsets"][0]["task_ids"]
    subsets = Subsets.model_validate(manifest)
    assert subsets.eval_subsets[0].task_ids == subsets.eval_subsets[1].task_ids


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["harbor", "settings"])
def test_nonfinite_native_settings_rejected_before_serialization(tmp_path, value, field):
    path, definition = prepared_benchmark(tmp_path)
    case = case_definition(path)
    target = case["baseline"]["harbor"] if field == "harbor" else definition["coverage"].setdefault("settings", {})
    target["nested"] = {"values": [value]}
    if field == "harbor":
        write_case(path, case)
    else:
        path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError, match="baseline.harbor" if field == "harbor" else "coverage.settings"):
        load_benchmark(path)


def test_yaml_date_rejected_but_explicit_string_and_null_preserved(tmp_path):
    path, definition = prepared_benchmark(tmp_path)
    settings = {"when": date(2020, 1, 1)}
    definition["coverage"]["settings"] = settings
    path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError, match=r"coverage\.settings\.when"):
        load_benchmark(path)
    settings.update(when="2020-01-01", limit=None)
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert load_benchmark(path).definition.coverage.settings == settings


def test_changed_task_input_names_case_and_task(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    (tmp_path / TASK_DIR / "inspect-files/instruction.md").unlink()
    with pytest.raises(ValueError, match="case rho-agent: task inspect-files: input digest differs"):
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
    path, _ = prepared_benchmark(tmp_path)
    for source in case_definition(path)["task_sources"].values():
        shutil.rmtree(tmp_path / source["path"])
    (tmp_path / DESCRIPTION_PATH).unlink()
    loaded = read_benchmark(path)
    prepared = loaded.case("rho-agent")
    case = prepared.case
    assert len(case.load_subsets(prepared.root).eval_subsets) == 3
    (tmp_path / SUBSETS_PATH).write_text("{}")
    with pytest.raises(ValueError, match="input digest differs"):
        case.load_subsets(prepared.root)
    with pytest.raises(ValueError, match="unknown case: absent"):
        loaded.case("absent")


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field", ["subsets", "case_description"])
@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_case_artifact_errors_have_one_context(tmp_path, standalone, field, failure):
    path, definition = prepared_benchmark(tmp_path)
    case_data = case_definition(path)
    source = case_data[field]
    artifact = tmp_path / source["path"]
    if failure == "missing":
        artifact.unlink()
        detail = f"missing pinned input: {source['path']}"
    else:
        if field == "subsets":
            data = json.loads(artifact.read_text())
            data["eval_subsets"][0]["task_ids"] = ["unknown-task"]
            detail = "subset subset-000001 contains unknown task IDs: ['unknown-task']"
        else:
            data = []
            detail = "case description must be a JSON object"
        artifact.write_text(json.dumps(data))
        source["sha256"] = input_digest(artifact)
        write_case(path, case_data)
    with pytest.raises(ValueError) as error:
        if standalone:
            benchmark = read_benchmark(path)
            prepared = benchmark.case("rho-agent")
            case = prepared.case
            if field == "subsets":
                case.load_subsets(prepared.root)
            else:
                case.load_description(prepared.root)
        else:
            load_benchmark(path)
    assert str(error.value) == f"case rho-agent, {field}: {detail}"


def test_permission_error_is_not_reported_as_missing(tmp_path, monkeypatch):
    path, _ = prepared_benchmark(tmp_path)
    prepared = read_benchmark(path).case("rho-agent")
    case = prepared.case

    def denied(root, value):
        raise PermissionError("permission denied for prepared input")

    monkeypatch.setattr("metric_benchmark.config.resolve_evidence", denied)
    with pytest.raises(ValueError, match="case rho-agent, subsets: permission denied for prepared input"):
        case.load_subsets(prepared.root)


def test_fixed_case_can_be_reused_by_different_experiments(tmp_path):
    path, definition = prepared_benchmark(tmp_path)
    first = load_benchmark(path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / CASE_DIR).rglob("*") if p.is_file()}
    definition["benchmark_id"] = "rho-alternative-metric"
    definition["coverage"]["system_id"] = "another-metric"
    definition["scoring"] = {"direction": "lower_is_better"}
    alternative = tmp_path / "alternative.yml"
    alternative.write_text(yaml.safe_dump(definition))
    second = load_benchmark(alternative)
    assert first.identity != second.identity
    assert first.cases == second.cases
    assert first.subsets == second.subsets
    assert first.definition.cases == second.definition.cases
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / CASE_DIR).rglob("*") if p.is_file()}


def test_unpacked_bundle_resolves_relative_to_bundle_root(tmp_path, monkeypatch):
    path, _ = prepared_benchmark(tmp_path / "original")
    original = load_benchmark(path)
    destination = tmp_path / "unpacked"
    shutil.copytree(path.parent, destination)
    shutil.rmtree(path.parent)
    monkeypatch.chdir(tmp_path)
    moved = load_benchmark(destination / path.name)
    assert moved.identity == original.identity
    assert moved.subsets == original.subsets
    prepared = moved.case("rho-agent")
    assert prepared.root == destination
    assert (
        prepared.case.task_sources["inspect-files"].resolve(prepared.root) == prepared.root / TASK_DIR / "inspect-files"
    )


@pytest.mark.parametrize("level", ["case", "agent", "tasks", "subsets"])
@pytest.mark.parametrize("failure", ["absolute", "traversal", "symlink", "missing"])
def test_manifest_references_stay_inside_their_package(tmp_path, level, failure):
    path, definition = prepared_benchmark(tmp_path)
    manifest_path = tmp_path / CASE_DIR / "case.yml"
    manifest = yaml.safe_load(manifest_path.read_text())
    source = definition["cases"][0] if level == "case" else manifest[level]
    root = tmp_path
    target = root / source["path"]
    if failure == "absolute":
        source["path"] = str(target)
    elif failure == "traversal":
        source["path"] = "../" + source["path"]
    elif failure == "symlink":
        link = root / "alias"
        link.symlink_to(target)
        source["path"] = "alias"
    else:
        target.unlink()
    if level != "case":
        manifest_path.write_text(yaml.safe_dump(manifest))
        definition["cases"][0]["sha256"] = input_digest(manifest_path)
    path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError, match="relative|symlink|missing pinned input"):
        load_benchmark(path)


def test_distinct_manifest_paths_cannot_duplicate_case_ids(tmp_path):
    path, definition = prepared_benchmark(tmp_path)
    original = tmp_path / CASE_DIR / "case.yml"
    duplicate = tmp_path / "other.yml"
    duplicate.write_bytes(original.read_bytes())
    definition["cases"].append({"path": duplicate.name, "sha256": input_digest(duplicate)})
    path.write_text(yaml.safe_dump(definition))
    with pytest.raises(ValueError, match="duplicate case ID: rho-agent"):
        read_benchmark(path)
