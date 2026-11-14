# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from metric_benchmark.config import load_benchmark, read_yaml
from metric_benchmark.generation import Selection, generate_experiment
from metric_benchmark.io import file_manifest, input_digest
from metric_benchmark.signing import check_benchmark, sign_benchmark
from metric_benchmark.validation import agent_config

LAB = Path(__file__).resolve().parents[1] / "labs/metric-benchmark"
EXAMPLE = LAB / "examples/smoke"


def write_yaml(path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False))


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(EXAMPLE, root)
    return root


@pytest.fixture
def selection():
    return Selection.model_validate(
        {
            "source_case_id": "smoke-case",
            "case_id": "reduced",
            "mutant_ids": ["nop"],
            "task_ids": ["answer"],
            "subsets": [{"eval_subset_id": "selected", "task_ids": ["answer"]}],
        }
    )


def generate(source, tmp_path, **kwargs):
    return generate_experiment(
        source, tmp_path / "generated", execution=source / "execution.yml", benchmark_id="example", **kwargs
    )


def test_full_generation_needs_no_experiment_or_checkout_metadata(source, tmp_path, monkeypatch):
    (source / "experiment.yml").unlink()
    original = file_manifest(source)

    def no_execution(*args, **kwargs):
        pytest.fail("generation must not start subprocesses")

    monkeypatch.setattr(subprocess, "Popen", no_execution)
    result = generate(source, tmp_path)
    assert (result.cases, result.agents, result.tasks, result.subsets, result.trials) == (1, 3, 2, 3, 6)
    check_benchmark(Path(result.experiment))
    loaded = load_benchmark(Path(result.experiment))
    assert loaded.analysis.settings.coverage == load_benchmark(EXAMPLE / "experiment.yml").analysis.settings.coverage
    assert loaded.analysis.load_subsets("smoke-case").model_dump() == json.loads(
        (source / "tasks/smoke-tasks/subsets.json").read_text()
    )
    assert file_manifest(source) == original
    assert not (tmp_path / "generated/provenance.json").exists()


def test_selected_inputs_are_copied_pinned_and_independent(source, tmp_path, selection):
    result = generate(source, tmp_path, selection=selection)
    assert (result.agents, result.tasks, result.subsets, result.trials) == (2, 1, 1, 2)
    path = Path(result.experiment)
    loaded = load_benchmark(path)
    case = loaded.execution.inputs.case("reduced").case
    assert case.baseline.agent_bundle_id == "oracle"
    assert [agent.agent_bundle_id for agent in case.mutants] == ["nop"]
    assert list(case.task_sources) == ["answer"]
    original = source / "tasks/smoke-tasks/answer"
    copied = case.task_sources["answer"].resolve(path.parent)
    assert input_digest(copied) == input_digest(original)
    assert not (path.parent / "inputs/tasks/smoke-tasks/control").exists()
    shutil.rmtree(source)
    check_benchmark(path)


def test_multiple_cases_and_case_filter(source, tmp_path):
    cases = read_yaml(source / "cases.yml")
    assert isinstance(cases, list)
    cases.append(copy.deepcopy(cases[0]) | {"case_id": "second"})
    write_yaml(source / "cases.yml", cases)
    settings = read_yaml(source / "analysis.yml")
    assert isinstance(settings, dict)
    settings["case_settings"]["second"] = copy.deepcopy(settings["case_settings"]["smoke-case"])
    write_yaml(source / "analysis.yml", settings)
    result = generate(source, tmp_path)
    assert (result.cases, result.trials) == (2, 12)
    result = generate_experiment(
        source, tmp_path / "one-case", execution=source / "execution.yml", benchmark_id="one", case_ids=["second"]
    )
    assert (result.cases, result.trials) == (1, 6)
    assert list(load_benchmark(Path(result.experiment)).execution.inputs.cases) == ["second"]


def test_adapter_kwargs_and_named_inputs_are_generic(source, tmp_path):
    assets = source / "custom-assets"
    assets.mkdir()
    (assets / "settings.txt").write_text("custom data")
    agent_path = source / "agents/smoke-agent/agent.yml"
    agents = read_yaml(agent_path)
    assert isinstance(agents, dict)
    for agent in [agents["baseline"], *agents["mutants"]]:
        agent["harbor"] = {
            "import_path": "custom_adapter:Agent",
            "kwargs": {"asset": "{input:assets}", "nested": {"keep": True, "change": "old"}, "list": [1, 2]},
        }
        agent["inputs"] = {"assets": {"path": "custom-assets", "kind": "directory", "sha256": input_digest(assets)}}
    write_yaml(agent_path, agents)
    sign_benchmark(source / "experiment.yml", resign=True)
    overrides = tmp_path / "agent-settings.yml"
    write_yaml(
        overrides,
        {"model_name": "provider/model", "kwargs": {"nested": {"change": "new"}, "list": [3], "install_source": False}},
    )
    result = generate(source, tmp_path, agent_settings=overrides)
    loaded = load_benchmark(Path(result.experiment))
    for agent in loaded.execution.inputs.case("smoke-case").case.agents():
        configured = agent_config(agent, Path(result.experiment).parent)
        assert configured.model_name == "provider/model"
        assert configured.kwargs["nested"] == {"keep": True, "change": "new"}
        assert configured.kwargs["list"] == [3]
        assert configured.kwargs["install_source"] is False
        assert (Path(configured.kwargs["asset"]) / "settings.txt").read_text() == "custom data"
    assert read_yaml(agent_path) == agents


@pytest.mark.parametrize(
    "field,value",
    [
        ("mutant_ids", []),
        ("task_ids", []),
        ("mutant_ids", ["nop", "nop"]),
        ("task_ids", ["answer", "answer"]),
        ("subsets", []),
        ("task_ids", "answer"),
    ],
)
def test_invalid_selection_schema(selection, field, value):
    with pytest.raises(ValueError):
        Selection.model_validate(selection.model_dump() | {field: value})


@pytest.mark.parametrize(
    "update",
    [
        {"source_case_id": "unknown"},
        {"case_id": "smoke-case"},
        {"mutant_ids": ["unknown"]},
        {"task_ids": ["unknown"]},
        {"subsets": "parent"},
        {"subsets": [{"eval_subset_id": "bad", "task_ids": ["control"]}]},
        {"subsets": [{"eval_subset_id": "duplicate", "task_ids": ["answer"]}] * 2},
    ],
)
def test_invalid_selection_leaves_no_output(source, tmp_path, selection, update):
    request = Selection.model_validate(selection.model_dump() | update)
    with pytest.raises(ValueError):
        generate(source, tmp_path, selection=request)
    assert not (tmp_path / "generated").exists()
    assert not list(tmp_path.glob(".experiment-*"))


def test_source_pin_mismatch_is_not_resigned(source, tmp_path, selection):
    (source / "tasks/smoke-tasks/answer/instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="digest differs"):
        generate(source, tmp_path, selection=selection)
    assert not (tmp_path / "generated").exists()


def test_unselected_task_contents_need_not_be_available(source, tmp_path, selection):
    shutil.rmtree(source / "tasks/smoke-tasks/control")
    check_benchmark(Path(generate(source, tmp_path, selection=selection).experiment))


def test_override_analysis_uses_source_relative_references(source, tmp_path):
    override = tmp_path / "analysis.yml"
    settings = read_yaml(source / "analysis.yml")
    assert isinstance(settings, dict)
    settings["coverage"]["system_id"] = "different-tool"
    write_yaml(override, settings)
    result = generate(source, tmp_path, analysis=override)
    assert load_benchmark(Path(result.experiment)).analysis.settings.coverage.system_id == "different-tool"


def test_author_snapshot_is_opt_in_and_does_not_execute(source, tmp_path):
    author = tmp_path / "author.py"
    author.write_text("raise RuntimeError('must not execute')\n")
    analysis = read_yaml(source / "analysis.yml")
    assert isinstance(analysis, dict)
    analysis["coverage"].update(
        system_revision=input_digest(author), settings={"author_command": [sys.executable, str(author)]}
    )
    override = tmp_path / "analysis.yml"
    write_yaml(override, analysis)
    result = generate(source, tmp_path, analysis=override, coverage_author=author)
    generated = Path(result.experiment).parent
    frozen = generated / "coverage/author.py"
    assert frozen.read_bytes() == author.read_bytes()
    generated_analysis = read_yaml(generated / "analysis.yml")
    assert isinstance(generated_analysis, dict)
    assert generated_analysis["coverage"]["settings"]["author_command"] == [
        sys.executable,
        str(frozen),
    ]
    receipt = json.loads((generated / "generation.json").read_text())
    assert receipt["coverage_author"]["sha256"] == input_digest(author)
    author.write_text("changed")
    assert input_digest(frozen) == receipt["coverage_author"]["sha256"]
    with pytest.raises(ValueError, match="system_revision"):
        generate_experiment(
            source,
            tmp_path / "bad-author",
            execution=source / "execution.yml",
            benchmark_id="bad",
            analysis=override,
            coverage_author=author,
        )
    assert not (tmp_path / "bad-author").exists()


def test_existing_output_and_nested_output_rejected(source, tmp_path):
    generate(source, tmp_path)
    before = file_manifest(tmp_path / "generated")
    with pytest.raises(ValueError, match="already exists"):
        generate(source, tmp_path)
    assert file_manifest(tmp_path / "generated") == before
    with pytest.raises(ValueError, match="outside"):
        generate_experiment(source, source / "nested", execution=source / "execution.yml", benchmark_id="bad")


def test_unknown_harbor_field_rejected(source, tmp_path):
    override = tmp_path / "agent.yml"
    write_yaml(override, {"unknown_field": True})
    with pytest.raises(ValueError, match="unknown Harbor"):
        generate(source, tmp_path, agent_settings=override)
    assert not (tmp_path / "generated").exists()


def test_cli_generates_signed_experiment(source, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(LAB / "prepare_experiment.py"),
            "--benchmark-source",
            str(source),
            "--execution",
            str(source / "execution.yml"),
            "--benchmark-id",
            "cli-example",
            "--output",
            str(tmp_path / "cli"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["trials"] == 6
    check_benchmark(Path(summary["experiment"]))
