# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from generation_benchmark.models import (
    GenerationAgentRef,
    GenerationAttempt,
    GenerationRequest,
    GenerationResponse,
    Task,
    load_experiment,
    write_model,
)
from generation_benchmark.signing import sign_experiment
from generation_benchmark.staging import build_generation_request, held_out_task_ids, stage_workspace
from generation_benchmark.validation import validate_generation_output
from pydantic import ValidationError
from test_generation_benchmark_pipeline import _write_pipeline_experiment

SKIPPED_ENTRIES = (".git", "__pycache__", "cached.pyc", ".DS_Store")


def _configure(
    exp: Path,
    *,
    generation: dict[str, object] | None = None,
    excludes: list[str] | None = None,
) -> Path:
    """Edit the generation block and agent_source_excludes of a test experiment, then re-sign."""
    if generation is not None:
        raw = yaml.safe_load(exp.read_text(encoding="utf-8"))
        raw["generation"] = {**raw["generation"], **generation}
        exp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    if excludes is not None:
        cases_path = exp.parent / "cases.yml"
        cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
        for case in cases:
            case["agent_source_excludes"] = excludes
        cases_path.write_text(yaml.safe_dump(cases, sort_keys=False), encoding="utf-8")
    sign_experiment(exp, resign=True)
    return exp


def _add_skipped_and_private_entries(directory: Path) -> None:
    (directory / ".git").mkdir()
    (directory / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (directory / "__pycache__").mkdir()
    (directory / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\0")
    (directory / "cached.pyc").write_bytes(b"\0")
    (directory / ".DS_Store").write_bytes(b"\0")


def _all_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _assert_no_held_out_text(paths: list[Path], extra: list[bytes], held_out: list[str]) -> None:
    for needle in held_out:
        encoded = needle.encode("utf-8")
        for path in paths:
            assert needle not in path.as_posix(), f"{needle} in path {path}"
            assert encoded not in path.read_bytes(), f"{needle} in {path}"
        for blob in extra:
            assert encoded not in blob


def _write_valid_output(output_dir: Path) -> None:
    task_dir = output_dir / "tasks" / "cover-gamma"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text('[task]\nname = "smoke/cover-gamma"\n', encoding="utf-8")
    log_dir = output_dir / "logs" / "cover-gamma"
    log_dir.mkdir(parents=True)
    (log_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")
    (output_dir / "baseline-report.json").write_text("{}\n", encoding="utf-8")
    write_model(
        output_dir / "response.json",
        GenerationResponse(
            attempts=[
                GenerationAttempt(
                    slug="cover-gamma",
                    tool="gamma",
                    status="accepted",
                    task_path="tasks/cover-gamma",
                    log_path="logs/cover-gamma",
                )
            ],
            unattempted=["delta"],
            baseline_report_path="baseline-report.json",
        ),
    )


def _rewrite_response(output_dir: Path, **changes: object) -> None:
    payload = json.loads((output_dir / "response.json").read_text(encoding="utf-8"))
    payload.update(changes)
    (output_dir / "response.json").write_text(json.dumps(payload), encoding="utf-8")


def test_generation_request_and_response_schemas_round_trip() -> None:
    request = GenerationRequest(
        case_id="smoke",
        eval_subset_id="s1",
        tasks=[Task(task_id="alpha", revision=1)],
        metric_output_dir="/work/cases/case-0000/metric-subsets/subset-000000/output",
        agent=GenerationAgentRef(agent_bundle_id="a", revision="1", harbor={"import_path": "{input:source}"}),
        settings={"mode": None},
    )
    payload = request.to_payload()
    assert payload["schema"] == "nemo.eval_author.generation_request.v1"
    assert payload["workspace"] == {"ethos": "ETHOS.md", "agent": "agent", "tasks": "tasks"}
    assert payload["agent"]["harbor"] == {"import_path": "{input:source}"}
    assert GenerationRequest.model_validate(payload) == request

    response_payload = GenerationResponse().to_payload()
    assert response_payload["schema"] == "nemo.eval_author.generation_response.v1"
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate({**payload, "max_gaps": 0})
    with pytest.raises(ValidationError):
        GenerationResponse.model_validate({**response_payload, "held_out": []})


def test_validate_generation_output_accepts_valid_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_valid_output(output_dir)
    response = validate_generation_output(output_dir, subset_id="s1")
    assert [a.slug for a in response.attempts] == ["cover-gamma"]
    assert response.error is None


def test_validate_generation_output_rejects_missing_response(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with pytest.raises(ValueError, match=r"subset s1, path .*response\.json: missing response\.json"):
        validate_generation_output(output_dir, subset_id="s1")


@pytest.mark.parametrize(
    ("changes", "path_text"),
    [
        ({"held_out": ["gamma"]}, "held_out"),
        ({"baseline_report_path": "/etc/passwd"}, "/etc/passwd"),
        ({"baseline_report_path": "../request.json"}, "../request.json"),
        ({"baseline_report_path": "missing.json"}, "missing.json"),
        (
            {"attempts": [{"slug": "x", "status": "accepted", "task_path": "../workspace/tasks/alpha"}]},
            "../workspace/tasks/alpha",
        ),
    ],
)
def test_validate_generation_output_rejects_bad_response(
    tmp_path: Path, changes: dict[str, object], path_text: str
) -> None:
    output_dir = tmp_path / "output"
    _write_valid_output(output_dir)
    _rewrite_response(output_dir, **changes)
    with pytest.raises(ValueError) as excinfo:
        validate_generation_output(output_dir, case_id="smoke", subset_id="s1")
    message = str(excinfo.value)
    assert "subset s1" in message
    assert path_text in message


def test_validate_generation_output_rejects_symlinks(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_valid_output(output_dir)
    (output_dir / "linked").symlink_to(output_dir / "tasks", target_is_directory=True)
    _rewrite_response(
        output_dir,
        attempts=[{"slug": "cover-gamma", "status": "accepted", "task_path": "linked/cover-gamma"}],
    )
    with pytest.raises(ValueError, match=r"subset s1, .*linked/cover-gamma.*symlinks"):
        validate_generation_output(output_dir, subset_id="s1")

    _rewrite_response(output_dir, attempts=[])
    with pytest.raises(ValueError, match=r"subset s1, path .*linked: generator output must not contain symlinks"):
        validate_generation_output(output_dir, subset_id="s1")

    (output_dir / "linked").unlink()
    (output_dir / "tasks" / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match=r"tasks/escape: generator output must not contain symlinks"):
        validate_generation_output(output_dir, subset_id="s1")


def test_stage_workspace_holds_only_agent_ethos_and_subset_tasks(tmp_path: Path) -> None:
    exp = _configure(_write_pipeline_experiment(tmp_path / "exp"), excludes=["items.yaml", "private/"])
    agent_dir = exp.parent / "agents" / "smoke"
    _add_skipped_and_private_entries(agent_dir)
    (agent_dir / "private").mkdir()
    (agent_dir / "private" / "notes.md").write_text("private notes\n", encoding="utf-8")
    _add_skipped_and_private_entries(exp.parent / "tasks" / "smoke" / "alpha")

    loaded = load_experiment(exp)
    lc = loaded.cases["smoke"]
    subset = next(s for s in lc.subsets.eval_subsets if s.eval_subset_id == "s1")
    workspace = tmp_path / "work" / "workspace"
    stage_workspace(lc, subset, workspace, work_root=tmp_path / "work")

    assert sorted(p.name for p in workspace.iterdir()) == ["ETHOS.md", "agent", "tasks"]
    assert (workspace / "ETHOS.md").read_bytes() == (agent_dir / "ethos.md").read_bytes()
    assert sorted(p.name for p in (workspace / "agent").iterdir()) == ["agent.yml", "ethos.md", "smoke_agent.py"]
    assert sorted(p.name for p in (workspace / "tasks").iterdir()) == ["alpha", "beta"]
    names = {part for p in workspace.rglob("*") for part in p.relative_to(workspace).parts}
    for skipped in (*SKIPPED_ENTRIES, "private", "items.yaml", "mod.cpython-312.pyc"):
        assert skipped not in names
    for forbidden in ("trials", "audit-coverage-report.json", "score.json", ".eval-author", "audit.md"):
        assert forbidden not in names

    request = build_generation_request(
        loaded,
        lc,
        subset,
        metric_output_dir=tmp_path / "work" / "cases" / "case-0000" / "metric-subsets" / "subset-000000" / "output",
        max_gaps=None,
    )
    request_bytes = json.dumps(request.to_payload()).encode("utf-8")
    held_out = held_out_task_ids(lc, subset)
    assert held_out == ["delta", "gamma"]
    held_out_dirs = [lc.task_paths[tid].name for tid in held_out]
    _assert_no_held_out_text(_all_files(workspace), [request_bytes], [*held_out, *held_out_dirs])
    assert [t.task_id for t in request.tasks] == ["alpha", "beta"]
    assert request.agent.harbor == lc.agent.harbor


def test_stage_workspace_refuses_held_out_task_inside_agent_tree(tmp_path: Path) -> None:
    exp = _write_pipeline_experiment(tmp_path / "exp")
    cases_path = exp.parent / "cases.yml"
    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases[0]["agent"] = {"path": "agent.yml"}
    shutil.copyfile(exp.parent / "agents" / "smoke" / "agent.yml", exp.parent / "agent.yml")
    raw_agent = yaml.safe_load((exp.parent / "agent.yml").read_text(encoding="utf-8"))
    raw_agent["inputs"]["source"]["path"] = "agents/smoke/smoke_agent.py"
    (exp.parent / "agent.yml").write_text(yaml.safe_dump(raw_agent), encoding="utf-8")
    cases_path.write_text(yaml.safe_dump(cases, sort_keys=False), encoding="utf-8")
    sign_experiment(exp, resign=True)

    lc = load_experiment(exp).cases["smoke"]
    subset = next(s for s in lc.subsets.eval_subsets if s.eval_subset_id == "s1")
    with pytest.raises(ValueError, match="held-out task delta .* agent_source_excludes"):
        stage_workspace(lc, subset, tmp_path / "ws-1")

    _configure(exp, excludes=["tasks", "agents/smoke/items.yaml", "gen_prompt.txt", "cases.yml"])
    lc = load_experiment(exp).cases["smoke"]
    with pytest.raises(ValueError, match="work root .* inside the agent source tree"):
        stage_workspace(lc, subset, tmp_path / "ws-2", work_root=exp.parent / "work")
    stage_workspace(lc, subset, tmp_path / "ws-3", work_root=tmp_path / "work")
    assert not (tmp_path / "ws-3" / "agent" / "tasks").exists()
    assert sorted(p.name for p in (tmp_path / "ws-3" / "tasks").iterdir()) == ["alpha", "beta"]
