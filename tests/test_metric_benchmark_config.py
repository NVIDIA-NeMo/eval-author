# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import input_digest, load_analysis, load_benchmark, load_execution
from metric_benchmark.io import read_json, write_json
from metric_benchmark.models import Score, Subsets, Tasks

LAB = Path(__file__).resolve().parents[1] / "labs/metric-benchmark"
FIXTURE = Path(__file__).parent / "fixtures/metric_benchmark"
AGENT_DIR = Path("agents/rho-agent")
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
    manifest = yaml.safe_load((experiment.parent / "cases.yml").read_text())[0]
    agent = yaml.safe_load((experiment.parent / manifest["agent"]["path"]).read_text())
    tasks = yaml.safe_load((experiment.parent / manifest["tasks"]["path"]).read_text())
    settings = yaml.safe_load((experiment.parent / "analysis.yml").read_text())["case_settings"][manifest["case_id"]]
    return {
        "case_id": manifest["case_id"],
        "subsets": settings["subsets"],
        "baseline": agent["baseline"],
        "mutants": agent["mutants"],
        "case_description": settings["description"],
        "task_inventory": {
            "tasks": [
                {key: value for key, value in task.items() if key in ("task_id", "revision", "name")}
                for task in tasks["tasks"]
            ]
        },
        "task_sources": {
            task["task_id"]: {"path": task["path"], "sha256": task.get("sha256"), "kind": "directory"}
            for task in tasks["tasks"]
        },
    }


def write_definition(experiment, name, data):
    target = experiment.parent / name
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    definition = yaml.safe_load(experiment.read_text())
    for source in (definition["cases"], definition["execution"], definition["analysis"]):
        source["sha256"] = input_digest(experiment.parent / source["path"])
    experiment.write_text(yaml.safe_dump(definition, sort_keys=False))


def write_case(experiment, case):
    cases_path = experiment.parent / "cases.yml"
    cases = yaml.safe_load(cases_path.read_text())
    manifest = cases[0]
    original_id = manifest["case_id"]
    parts = {
        "agent": {"baseline": case["baseline"], "mutants": case["mutants"]},
        "tasks": {
            "tasks": [
                {**task, **{k: v for k, v in case["task_sources"][task["task_id"]].items() if k != "kind"}}
                for task in case["task_inventory"]["tasks"]
            ]
        },
    }
    for name, data in parts.items():
        target = experiment.parent / manifest[name]["path"]
        target.write_text(yaml.safe_dump(data, sort_keys=False))
        manifest[name]["sha256"] = input_digest(target)
    manifest["case_id"] = case["case_id"]
    cases_path.write_text(yaml.safe_dump(cases, sort_keys=False))
    analysis = yaml.safe_load((experiment.parent / "analysis.yml").read_text())
    analysis["case_settings"].pop(original_id)
    analysis["case_settings"][case["case_id"]] = {"description": case["case_description"], "subsets": case["subsets"]}
    write_definition(experiment, "analysis.yml", analysis)


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


def test_stage_loading_and_fixed_memberships(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    benchmark = load_benchmark(path)
    execution = load_execution(tmp_path / "cases.yml", tmp_path / "execution.yml")
    analysis = load_analysis(tmp_path / "cases.yml", tmp_path / "analysis.yml")
    assert benchmark.execution == execution
    assert benchmark.analysis == analysis
    assert analysis.load_subsets("rho-agent").sampling_parameters == {"seed": 42}
    assert analysis.load_description("rho-agent")
    assert set(
        execution.inputs.case("rho-agent")
        .case.selection({"baseline": "a", "mutant": "b", "mutant-no-search": "c"}, analysis.settings.scoring)
        .run_ids
    ) == {"baseline", "mutant", "mutant-no-search"}
    (tmp_path / TASK_DIR / "inspect-files/instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="case rho-agent: task inspect-files: input digest differs"):
        load_execution(tmp_path / "cases.yml", tmp_path / "execution.yml")


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate-case", "agent-pin", "source-missing", "empty-cases", "analysis-cases"]
)
def test_bad_definition(tmp_path, change):
    path, config = prepared_benchmark(tmp_path)
    cases = yaml.safe_load((tmp_path / "cases.yml").read_text())
    if change == "unknown":
        config["surprise"] = True
        path.write_text(yaml.safe_dump(config))
    elif change in ("duplicate-case", "empty-cases"):
        write_definition(path, "cases.yml", cases * 2 if change == "duplicate-case" else [])
    elif change == "agent-pin":
        (tmp_path / AGENT_DIR / "agent.yml").write_text("changed")
    elif change == "analysis-cases":
        analysis = yaml.safe_load((tmp_path / "analysis.yml").read_text())
        analysis["case_settings"]["unknown"] = analysis["case_settings"].pop("rho-agent")
        write_definition(path, "analysis.yml", analysis)
    else:
        case = case_definition(path)
        case["task_sources"]["inspect-files"]["path"] = "missing-task"
        write_case(path, case)
    with pytest.raises(ValueError):
        loaded = load_benchmark(path)
        loaded.execution.inputs.verify_execution_inputs()


def test_duplicate_yaml_keys(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    path.write_text(path.read_text() + "benchmark_id: duplicate\n")
    with pytest.raises(ValueError, match="unique strings"):
        load_benchmark(path)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), date(2020, 1, 1)])
@pytest.mark.parametrize("field", ["harbor", "settings"])
def test_non_json_native_settings_rejected(tmp_path, value, field):
    path, _ = prepared_benchmark(tmp_path)
    if field == "harbor":
        case = case_definition(path)
        case["baseline"]["harbor"]["nested"] = {"values": [value]}
        write_case(path, case)
    else:
        analysis = yaml.safe_load((tmp_path / "analysis.yml").read_text())
        analysis["coverage"]["settings"] = {"nested": [value]}
        write_definition(path, "analysis.yml", analysis)
    with pytest.raises(ValueError):
        load_benchmark(path)


def test_yaml_string_dates_and_null_preserved(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    settings = {"when": "2020-01-01", "limit": None}
    analysis = yaml.safe_load((tmp_path / "analysis.yml").read_text())
    analysis["coverage"]["settings"] = settings
    write_definition(path, "analysis.yml", analysis)
    assert load_benchmark(path).analysis.settings.coverage.settings == settings


def test_analysis_does_not_open_executable_inputs_or_execution_settings(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    for source in case_definition(path)["task_sources"].values():
        shutil.rmtree(tmp_path / source["path"])
    (tmp_path / "execution.yml").unlink()
    analysis = load_analysis(tmp_path / "cases.yml", tmp_path / "analysis.yml")
    assert len(analysis.load_subsets("rho-agent").eval_subsets) == 3
    assert analysis.load_description("rho-agent")
    with pytest.raises(ValueError, match="unknown case: absent"):
        analysis.inputs.case("absent")


def test_execution_does_not_open_analysis_inputs_or_settings(tmp_path):
    path, _ = prepared_benchmark(tmp_path)
    for name in ("analysis.yml", SUBSETS_PATH, DESCRIPTION_PATH):
        (tmp_path / name).unlink()
    assert load_execution(tmp_path / "cases.yml", tmp_path / "execution.yml").inputs.cases


@pytest.mark.parametrize("field", ["subsets", "case_description"])
@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_case_artifact_errors_have_context(tmp_path, field, failure):
    path, _ = prepared_benchmark(tmp_path)
    case = case_definition(path)
    source = case[field]
    artifact = tmp_path / source["path"]
    if failure == "missing":
        artifact.unlink()
    else:
        data = [] if field == "case_description" else json.loads(artifact.read_text())
        if field == "subsets":
            assert isinstance(data, dict)
            data["eval_subsets"][0]["task_ids"] = ["unknown-task"]
        artifact.write_text(json.dumps(data))
        source["sha256"] = input_digest(artifact)
        write_case(path, case)
    loaded = load_benchmark(path).analysis
    with pytest.raises(ValueError, match=f"case rho-agent, {field}:"):
        (loaded.load_subsets if field == "subsets" else loaded.load_description)("rho-agent")


def test_permission_error_is_not_reported_as_missing(tmp_path, monkeypatch):
    path, _ = prepared_benchmark(tmp_path)
    analysis = load_benchmark(path).analysis

    def denied(root, value):
        raise PermissionError("permission denied for prepared input")

    monkeypatch.setattr("metric_benchmark.config.resolve_evidence", denied)
    with pytest.raises(ValueError, match="case rho-agent, subsets: permission denied"):
        analysis.load_subsets("rho-agent")


def test_unpacked_bundle_resolves_from_cases_file(tmp_path, monkeypatch):
    path, _ = prepared_benchmark(tmp_path / "original")
    original = load_benchmark(path)
    original_analysis_identity = original.analysis.identity
    destination = tmp_path / "unpacked"
    shutil.copytree(path.parent, destination)
    shutil.rmtree(path.parent)
    monkeypatch.chdir(tmp_path)
    moved = load_benchmark(destination / path.name)
    assert moved.analysis.identity == original_analysis_identity
    assert moved.execution.inputs.identity == original.execution.inputs.identity
    prepared = moved.execution.inputs.case("rho-agent")
    assert prepared.root == destination
    assert (
        prepared.case.task_sources["inspect-files"].resolve(prepared.root) == destination / TASK_DIR / "inspect-files"
    )


@pytest.mark.parametrize("level", ["cases", "execution", "analysis", "agent", "tasks", "subsets", "description"])
@pytest.mark.parametrize("failure", ["absolute", "traversal", "symlink", "missing"])
def test_manifest_references_stay_inside_package(tmp_path, level, failure):
    path, definition = prepared_benchmark(tmp_path)
    if level in ("cases", "execution", "analysis"):
        data, owner = definition, "experiment.yml"
        source = data[level]
    elif level in ("agent", "tasks"):
        owner = "cases.yml"
        data = yaml.safe_load((tmp_path / owner).read_text())
        source = data[0][level]
    else:
        owner = "analysis.yml"
        data = yaml.safe_load((tmp_path / owner).read_text())
        source = data["case_settings"]["rho-agent"][level]
    target = tmp_path / source["path"]
    if failure == "absolute":
        source["path"] = str(target)
    elif failure == "traversal":
        source["path"] = "../" + source["path"]
    elif failure == "symlink":
        (tmp_path / "alias").symlink_to(target)
        source["path"] = "alias"
    else:
        target.unlink()
    if owner == "experiment.yml" or failure == "missing":
        (tmp_path / owner).write_text(yaml.safe_dump(data)) if failure != "missing" else None
    else:
        write_definition(path, owner, data)
    with pytest.raises(ValueError, match="relative|symlink|missing pinned input"):
        loaded = load_benchmark(path)
        loaded.analysis.load_subsets("rho-agent")
        loaded.analysis.load_description("rho-agent")
