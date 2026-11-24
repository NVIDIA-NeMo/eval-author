# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from generation_benchmark.digests import (
    command_digest,
    directory_digest,
    glob_digest,
    implementation_digest,
)
from generation_benchmark.models import (
    DirectoryModel,
    GenerationExecution,
    GenerationExperiment,
    OutputSpec,
    Score,
    ScoreOutput,
    Stage1ExecutionRow,
    Stage1Results,
    execution_identity_form,
    field_is_identity,
    load_experiment,
)
from generation_benchmark.validation import (
    invoke_workspace_command,
    task_checksum,
    validate_directory_output,
    validate_stage0_setup,
    validate_stage1_execution,
)
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig
from harbor.models.trial.result import TrialResult
from metric_benchmark.io import write_json


class CustomScoreOutput(ScoreOutput):
    note: str

    @classmethod
    def load(cls, directory: Path) -> CustomScoreOutput:
        base = ScoreOutput.load(directory)
        note_file = directory / "note.txt"
        if not note_file.is_file():
            raise ValueError(f"missing note.txt in {directory}")
        return cls(score=base.score, note=note_file.read_text(encoding="utf-8"))


class NonScoreDirectoryOutput(DirectoryModel):
    marker: str

    @classmethod
    def load(cls, directory: Path) -> NonScoreDirectoryOutput:
        marker_file = directory / "marker.txt"
        if not marker_file.is_file():
            raise ValueError(f"missing marker.txt in {directory}")
        return cls(marker=marker_file.read_text(encoding="utf-8"))


def make_task_dir(task_dir: Path, slug: str) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "instruction.md").write_text(f"TOOL: {slug}\n", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        f'version = "1.0"\n[task]\nname = "smoke/{slug}"\n[agent]\ntimeout_sec = 30\n[verifier]\ntimeout_sec = 30\n',
        encoding="utf-8",
    )
    (task_dir / "environment").mkdir(exist_ok=True)
    (task_dir / "environment/Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (task_dir / "solution").mkdir(exist_ok=True)
    (task_dir / "solution/solve.sh").write_text("#!/bin/bash\nprintf ready > /app/answer\n", encoding="utf-8")
    (task_dir / "tests").mkdir(exist_ok=True)
    (task_dir / "tests/test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n", encoding="utf-8")
    (task_dir / "README.md").write_text(f"# {slug}\n", encoding="utf-8")


def write_sample_experiment(root: Path, *, with_setup: bool = True, case_id: str = "smoke") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    agent_dir = root / "agents" / case_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "smoke_agent.py").write_text("# agent script\n", encoding="utf-8")
    (agent_dir / "ethos.md").write_text("---\nname: smoke\n---\n# Ethos\n", encoding="utf-8")
    (agent_dir / "items.yaml").write_text("items: []\n", encoding="utf-8")
    (agent_dir / "agent.yml").write_text(
        yaml.safe_dump(
            {
                "agent_bundle_id": "smoke-agent",
                "revision": "1",
                "harbor": {"name": "oracle"},
                "inputs": {"source": {"path": f"agents/{case_id}/smoke_agent.py"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    task_dir = root / "tasks" / case_id
    make_task_dir(task_dir / "alpha", "alpha")
    make_task_dir(task_dir / "beta", "beta")
    (task_dir / "tasks.yml").write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {"task_id": "alpha", "revision": 1, "path": f"tasks/{case_id}/alpha"},
                    {"task_id": "beta", "revision": 1, "path": f"tasks/{case_id}/beta"},
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "subsets.json").write_text(
        json.dumps(
            {
                "artifact_type": "eval_subset_definitions",
                "sampling_parameters": {"method": "fixed", "seed": 1},
                "eval_subsets": [{"eval_subset_id": "s1", "task_ids": ["alpha"]}],
            }
        ),
        encoding="utf-8",
    )

    (root / "cases.yml").write_text(
        yaml.safe_dump(
            [
                {
                    "case_id": case_id,
                    "agent": {"path": f"agents/{case_id}/agent.yml"},
                    "tasks": {"path": f"tasks/{case_id}/tasks.yml"},
                    "subsets": {"path": f"tasks/{case_id}/subsets.json"},
                    "ethos": {"path": f"agents/{case_id}/ethos.md"},
                    "agent_source_excludes": [],
                    "settings": {"items": {"path": f"agents/{case_id}/items.yaml"}},
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    metric: dict = {
        "score": {
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 60,
        }
    }
    if with_setup:
        metric["setup"] = {
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 60,
            "output": {
                "python": sys.executable,
                "type": "test_generation_benchmark_models:NonScoreDirectoryOutput",
            },
        }

    exp = {
        "benchmark_id": "smoke",
        "revision": 1,
        "cases": {"path": "cases.yml"},
        "execution": {
            "backend": "local_harbor",
            "harbor_version": "0.24.0",
            "n_concurrent_trials": 1,
            "environment": {"type": "docker"},
        },
        "metric": metric,
        "generation": {
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 60,
            "settings": {},
        },
    }
    exp_path = root / "generation-experiment.yml"
    exp_path.write_text(yaml.safe_dump(exp, sort_keys=False), encoding="utf-8")
    return exp_path


def test_loader_rejects_bad_configs_with_case_context(tmp_path: Path) -> None:
    # Unknown field inside case
    exp = write_sample_experiment(tmp_path / "c1", case_id="case-alpha")
    cases_path = exp.parent / "cases.yml"
    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases[0]["unexpected"] = True
    cases_path.write_text(yaml.safe_dump(cases, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="case-alpha"):
        load_experiment(exp)

    # Malformed digest in agent.yml
    exp2 = write_sample_experiment(tmp_path / "c2", case_id="case-beta")
    agent_path = exp2.parent / "agents/case-beta/agent.yml"
    agent_data = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent_data["digest"] = {"method": "bogus"}
    agent_path.write_text(yaml.safe_dump(agent_data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="case-beta"):
        load_experiment(exp2)

    # Duplicate case id
    exp3 = write_sample_experiment(tmp_path / "c3", case_id="dup-case")
    c3_cases = yaml.safe_load((exp3.parent / "cases.yml").read_text(encoding="utf-8"))
    (exp3.parent / "cases.yml").write_text(yaml.safe_dump(c3_cases * 2, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="dup-case"):
        load_experiment(exp3)

    # Subset naming unknown task
    exp4 = write_sample_experiment(tmp_path / "c4", case_id="case-gamma")
    sub_path = exp4.parent / "tasks/case-gamma/subsets.json"
    sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
    sub_data["eval_subsets"][0]["task_ids"].append("missing-task")
    sub_path.write_text(json.dumps(sub_data), encoding="utf-8")
    with pytest.raises(ValueError, match="case-gamma"):
        load_experiment(exp4)


def test_loader_rejects_dollar_brace_anywhere_in_experiment(tmp_path: Path) -> None:
    exp = write_sample_experiment(tmp_path / "env_interp")
    exp.write_text(exp.read_text(encoding="utf-8") + "\n# ${LEAKED_VAR}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\$\{"):
        load_experiment(exp)


@pytest.mark.parametrize("filename", ["agent.yml", "tasks.yml"])
def test_loader_rejects_sha256_in_agent_or_tasks(tmp_path: Path, filename: str) -> None:
    exp = write_sample_experiment(tmp_path / "no_sha", case_id="case-pin")
    if filename == "agent.yml":
        target = exp.parent / "agents/case-pin/agent.yml"
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        data["inputs"]["source"]["sha256"] = "sha256:" + "a" * 64
    else:
        target = exp.parent / "tasks/case-pin/tasks.yml"
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        data["tasks"][0]["sha256"] = "sha256:" + "a" * 64
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=f"case-pin.*{filename}"):
        load_experiment(exp)


def test_score_null_requires_reason() -> None:
    with pytest.raises(ValueError, match="missing_score_reason"):
        Score(value=None)
    assert Score(value=None, missing_score_reason="timeout").value is None
    assert Score(value=0.5).value == 0.5


def test_directory_model_and_metric_output_checks(tmp_path: Path) -> None:
    # DirectoryModel ignores undeclared files
    out_dir = tmp_path / "score_out"
    out_dir.mkdir()
    (out_dir / "score.json").write_text('{"value": 0.75}\n', encoding="utf-8")
    (out_dir / "undeclared.log").write_text("noise\n", encoding="utf-8")
    loaded = ScoreOutput.load(out_dir)
    assert loaded.score.value == 0.75

    # Subprocess check passes with undeclared files
    tests_dir = Path(__file__).resolve().parent
    res = validate_directory_output(
        None,
        out_dir,
        case_id="smoke",
        require_score_output=True,
        extra_pythonpath=[tests_dir],
    )
    assert res["score"].value == 0.75

    # Absent metric.setup skips setup check
    exp = write_sample_experiment(tmp_path / "no_setup", with_setup=False)
    loaded_exp = load_experiment(exp)
    assert validate_stage0_setup(loaded_exp.experiment, "smoke", tmp_path / "nonexistent") is None

    # Missing score.json fails
    empty_dir = tmp_path / "empty_out"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="score.json"):
        validate_directory_output(None, empty_dir, case_id="smoke", require_score_output=True)

    # Null score without reason fails
    bad_dir = tmp_path / "bad_score"
    bad_dir.mkdir()
    (bad_dir / "score.json").write_text('{"value": null}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="smoke"):
        validate_directory_output(None, bad_dir, case_id="smoke", require_score_output=True)

    # Non-ScoreOutput rejected for score output
    non_score_dir = tmp_path / "non_score"
    non_score_dir.mkdir()
    (non_score_dir / "marker.txt").write_text("ok", encoding="utf-8")
    with pytest.raises(ValueError, match="ScoreOutput"):
        validate_directory_output(
            OutputSpec(python=sys.executable, type="test_generation_benchmark_models:NonScoreDirectoryOutput"),
            non_score_dir,
            case_id="smoke",
            require_score_output=True,
            extra_pythonpath=[tests_dir],
        )


def test_directory_hash_skips_git_pycache_pyc_and_ds_store(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    dirty = tmp_path / "dirty"
    for d in (clean, dirty):
        (d / "sub").mkdir(parents=True)
        (d / "a.txt").write_text("hello\n", encoding="utf-8")
        (d / "sub/b.py").write_text("x = 1\n", encoding="utf-8")

    (dirty / ".git/objects").mkdir(parents=True)
    (dirty / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (dirty / "__pycache__").mkdir()
    (dirty / "__pycache__/b.cpython-313.pyc").write_bytes(b"\x00\x01")
    (dirty / "sub/extra.pyc").write_bytes(b"\x02\x03")
    (dirty / ".DS_Store").write_bytes(b"\x04\x05")
    (dirty / "sub/.DS_Store").write_bytes(b"\x06")

    assert directory_digest(clean) == directory_digest(dirty)


def test_glob_command_and_implementation_digests(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.py").write_text("v = 1\n", encoding="utf-8")
    (src / "skip.tmp").write_text("ignored\n", encoding="utf-8")

    d1 = glob_digest(["src/**"], ["*.tmp"], tmp_path)
    (src / "skip.tmp").write_text("changed ignored\n", encoding="utf-8")
    assert glob_digest(["src/**"], ["*.tmp"], tmp_path) == d1
    (src / "keep.py").write_text("v = 2\n", encoding="utf-8")
    assert glob_digest(["src/**"], ["*.tmp"], tmp_path) != d1

    with pytest.raises(ValueError, match="matched no files"):
        glob_digest(["missing/*.py"], [], tmp_path)

    c1 = command_digest([sys.executable, "-c", "print('abc')"], tmp_path)
    c2 = command_digest([sys.executable, "-c", "print('def')"], tmp_path)
    assert c1 != c2
    with pytest.raises(ValueError, match="exited 3"):
        command_digest([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path)

    assert implementation_digest().startswith("sha256:")


def test_identity_fields_and_workspace_modification_guard(tmp_path: Path) -> None:
    assert not field_is_identity(GenerationExperiment, "benchmark_id")
    assert not field_is_identity(GenerationExperiment, "revision")
    assert not field_is_identity(GenerationExecution, "n_concurrent_trials")
    assert field_is_identity(GenerationExecution, "harbor_version")

    e1 = GenerationExecution(n_concurrent_trials=1)
    e2 = GenerationExecution(n_concurrent_trials=8)
    assert execution_identity_form(e1) == execution_identity_form(e2)

    inv_dir = tmp_path / "inv"
    ws = inv_dir / "workspace"
    ws.mkdir(parents=True)
    (ws / "input.txt").write_text("immutable\n", encoding="utf-8")
    (inv_dir / "request.json").write_text("{}\n", encoding="utf-8")
    (inv_dir / "output").mkdir()
    outcome = invoke_workspace_command(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('workspace/input.txt').write_text('mutated'); raise SystemExit(1)",
        ],
        invocation_dir=inv_dir,
        timeout_seconds=10,
        workspace_dir=ws,
        output_arg="output",
    )
    assert not outcome.succeeded
    assert outcome.workspace_modified
    assert outcome.error == "workspace_modified"


def test_stage1_completion_check_accepts_succeeded_with_trajectory_and_rejects_nop(tmp_path: Path) -> None:
    exp_path = write_sample_experiment(tmp_path / "stage1", case_id="smoke")
    loaded_exp = load_experiment(exp_path)
    loaded_case = loaded_exp.cases["smoke"]

    exec_root = tmp_path / "execution"
    run_dir = exec_root / "agents/agent/run-0001"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "result.json", {"finished_at": "2026-01-01T00:00:00Z"})

    agent_cfg = AgentConfig.model_validate(loaded_case.agent.harbor)
    rows: list[Stage1ExecutionRow] = []
    for task in loaded_case.tasks.task_inventory.tasks:
        task_path = loaded_case.task_paths[task.task_id]
        trial_dir = run_dir / f"{task.task_id}__trial"
        (trial_dir / "agent").mkdir(parents=True)
        (trial_dir / "agent/trajectory.json").write_text("{}\n", encoding="utf-8")
        native = TrialResult.model_validate(
            {
                "task_name": HarborTask(task_path).name,
                "trial_name": trial_dir.name,
                "trial_uri": trial_dir.as_uri(),
                "task_id": {"path": str(task_path)},
                "task_checksum": task_checksum(HarborTask(task_path)),
                "config": {
                    "task": {"path": str(task_path)},
                    "agent": agent_cfg.model_dump(mode="json"),
                    "trial_name": trial_dir.name,
                    "trials_dir": str(run_dir),
                },
                "agent_info": {"name": "oracle", "version": "1.0"},
                "verifier_result": {"rewards": {"reward": 1.0}},
                "finished_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        )
        write_json(trial_dir / "result.json", native)
        rel = trial_dir.relative_to(exec_root).as_posix()
        rows.append(
            Stage1ExecutionRow(
                agent_run_id="agent/run-0001",
                agent_bundle_id=loaded_case.agent.agent_bundle_id,
                task=task,
                status="succeeded",
                rewards={"reward": Score(value=1.0)},
                native_results_path=rel,
                trajectory_path=f"{rel}/agent/trajectory.json",
            )
        )

    write_json(
        exec_root / "results.json",
        Stage1Results(
            run_id="r1",
            case_id="smoke",
            task_inventory=loaded_case.tasks.task_inventory.tasks,
            execution_results=rows,
        ),
    )

    validated = validate_stage1_execution(exec_root, loaded_case)
    assert len(validated.execution_results) == 2

    # Adding a nop directory must fail
    (exec_root / "agents/nop").mkdir()
    with pytest.raises(ValueError, match="nop"):
        validate_stage1_execution(exec_root, loaded_case)
    (exec_root / "agents/nop").rmdir()

    # Missing agent/trajectory.json must fail naming the task
    (run_dir / "beta__trial/agent/trajectory.json").unlink()
    with pytest.raises(ValueError, match="beta"):
        validate_stage1_execution(exec_root, loaded_case)
