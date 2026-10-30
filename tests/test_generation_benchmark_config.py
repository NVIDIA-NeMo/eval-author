# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import shutil
import sys
from pathlib import Path

import pytest
import yaml
from generation_benchmark.config import (
    GenerationAgentManifest,
    GenerationConfig,
    draft_dir_from_settings,
    generation_process_sha256,
    load_generation_benchmark,
)
from generation_benchmark.eval_author.identity import tree_digest
from generation_benchmark.generation import validate_generation_request
from generation_benchmark.signing import sign_generation_benchmark
from metric_benchmark.config import input_digest
from metric_benchmark.eval_author import source_digest

LAB = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures/generation_benchmark"
GENERATION_AGENT_DIR = Path("agents/gen-agent")
TASK_DIR = Path("tasks/workspace")
DESCRIPTION_PATH = Path("agents/gen-agent/description.json")
SUBSETS_PATH = Path("tasks/workspace/subsets.json")
TASK_IDS = ["gen-task-a", "gen-task-b"]


def _synthesize_task_dir(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "instruction.md").write_text("Synthetic test task.\n")
    (folder / "task.toml").write_text('version = "1.0"\n')
    (folder / "environment").mkdir(exist_ok=True)
    (folder / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
    (folder / "tests").mkdir(exist_ok=True)
    (folder / "tests/test.sh").write_text("#!/bin/bash\necho 0 > /logs/verifier/reward.txt\n")
    (folder / "solution").mkdir(exist_ok=True)
    (folder / "solution/solve.sh").write_text("#!/bin/bash\necho solved\n")


def prepared_generation_benchmark(root: Path) -> tuple[Path, dict]:
    """Copy the generation CI fixture, synthesize task trees, wire adapters, and sign."""
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    path = root / "experiment.yml"
    for task_id in TASK_IDS:
        _synthesize_task_dir(root / TASK_DIR / task_id)
    coverage_adapter = root / "adapter.py"
    coverage_adapter.write_text("# synthetic coverage adapter\n", encoding="utf-8")
    generation_adapter = root / "generation-adapter.py"
    generation_adapter.write_text("# synthetic generation adapter\n", encoding="utf-8")
    eval_root = LAB
    generation = {
        "system_id": "eval-author",
        "system_revision": "1",
        "command": [sys.executable, str(generation_adapter.resolve())],
        "settings": {
            "eval_author_root": str(eval_root),
            "source_sha256": source_digest(eval_root),
            "generation_budget": {"max_new_tasks": 1, "seed": 0},
            "draft_dir": ".eval-author/task-drafts",
        },
    }
    generation["settings"]["process_sha256"] = generation_process_sha256(
        GenerationConfig.model_validate(generation)
    )
    generation["settings"]["task_create_skill_sha256"] = tree_digest(
        eval_root / "skills/eval-author-task-create"
    )
    definition = yaml.safe_load(path.read_text(encoding="utf-8"))
    analysis_path = root / definition["analysis"]["path"]
    analysis = yaml.safe_load(analysis_path.read_text(encoding="utf-8"))
    analysis["coverage"]["command"] = [sys.executable, str(coverage_adapter.resolve())]
    analysis_path.write_text(yaml.safe_dump(analysis, sort_keys=False), encoding="utf-8")
    generation["inputs"] = {
        "agent_config": {
            "path": "configs/agent-oracle.yaml",
            "sha256": input_digest(root / "configs/agent-oracle.yaml"),
            "kind": "file",
        },
        "generator_config": {
            "path": "configs/generator.yaml",
            "sha256": input_digest(root / "configs/generator.yaml"),
            "kind": "file",
        },
    }
    generation_path = root / definition["generation"]["path"]
    generation_path.write_text(yaml.safe_dump(generation, sort_keys=False), encoding="utf-8")
    sign_generation_benchmark(path, resign=True)
    return path, yaml.safe_load(path.read_text(encoding="utf-8"))


def generation_experiment(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    return path


def test_generation_agent_rejects_mutants_key():
    with pytest.raises(ValueError, match="omit mutants"):
        GenerationAgentManifest.model_validate(
            {
                "baseline": {
                    "agent_bundle_id": "baseline",
                    "revision": "1",
                    "harbor": {"name": "oracle"},
                },
                "mutants": [],
            }
        )


def test_draft_dir_must_be_relative():
    assert draft_dir_from_settings({"draft_dir": ".eval-author/task-drafts"}) == Path(
        ".eval-author/task-drafts"
    )
    with pytest.raises(ValueError, match="draft_dir is required"):
        draft_dir_from_settings({})
    with pytest.raises(ValueError, match="relative path"):
        draft_dir_from_settings({"draft_dir": "/tmp/drafts"})
    with pytest.raises(ValueError, match="relative path"):
        draft_dir_from_settings({"draft_dir": "tasks/../drafts"})


def test_load_generation_benchmark(tmp_path):
    path = generation_experiment(tmp_path)
    loaded = load_generation_benchmark(path, allow_unsigned=True)
    assert loaded.definition.experiment_kind == "generation"
    assert loaded.generation.system_id == "eval-author"
    resolved = loaded.resolved_generation_settings()
    assert Path(resolved["agent_config"]).is_file()
    assert Path(resolved["generator_config"]).is_file()


def test_load_generation_benchmark_rejects_generator_pin_drift(tmp_path):
    path = generation_experiment(tmp_path)
    loaded = load_generation_benchmark(path, allow_unsigned=True)
    generator_path = Path(loaded.resolved_generation_settings()["generator_config"])
    generator_path.write_text(generator_path.read_text() + "# drift\n")
    with pytest.raises(ValueError, match="input digest differs"):
        load_generation_benchmark(path, allow_unsigned=True)


def test_generation_request_rejects_full_benchmark_metric():
    with pytest.raises(ValueError, match="baseline_full_metric"):
        validate_generation_request(
            {
                "schema_version": 1,
                "case_description": {},
                "eval_subset_id": "s",
                "tasks": [],
                "settings": {"baseline_full_metric": 1.0},
            }
        )


def test_generation_request_names_staged_paths():
    validate_generation_request(
        {
            "schema_version": 1,
            "case_description": {},
            "eval_subset_id": "s",
            "tasks": [],
            "settings": {},
            "coverage": {"value": 0.0, "reason": None},
            "inputs": {
                "suite": "input/tasks",
                "coverage_work": "input/coverage",
                "output_suite": "output/tasks-generated",
            },
        }
    )
    with pytest.raises(ValueError, match="relative path"):
        validate_generation_request(
            {
                "schema_version": 1,
                "case_description": {},
                "eval_subset_id": "s",
                "tasks": [],
                "settings": {},
                "coverage": {"value": 0.0, "reason": None},
                "inputs": {
                    "suite": "input/tasks",
                    "coverage_work": "/tmp/coverage",
                    "output_suite": "output/tasks-generated",
                },
            }
        )


def test_generation_request_rejects_unexpected_top_level_keys():
    with pytest.raises(ValueError, match="unexpected keys"):
        validate_generation_request(
            {
                "schema_version": 1,
                "case_description": {},
                "eval_subset_id": "s",
                "tasks": [],
                "settings": {},
                "baseline_full_metric": 1.0,
            }
        )


def test_prepared_generation_fixture_loads_twice(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    first = load_generation_benchmark(path)
    second = load_generation_benchmark(path)
    assert first.identity == second.identity
    assert len(first.analysis.load_subsets("gen-agent").eval_subsets) == 2


def test_prepared_generation_task_tamper_rejected(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path)
    (tmp_path / TASK_DIR / "gen-task-a/instruction.md").write_text("changed\n")
    with pytest.raises(ValueError, match="digest differs"):
        load_generation_benchmark(path)
