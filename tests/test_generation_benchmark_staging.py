# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import generation_benchmark.staging as staging
import pytest
import yaml
from generation_benchmark.models import (
    GenerationAgentRef,
    GenerationAttempt,
    GenerationInputs,
    GenerationRequest,
    GenerationResponse,
    Stage3GenerationReceipt,
    Task,
    load_experiment,
    write_model,
)
from generation_benchmark.pipeline import run_experiment
from generation_benchmark.signing import check_experiment, sign_experiment, verify_lock_entries
from generation_benchmark.staging import build_generation_request, generate, held_out_task_ids, stage_workspace
from generation_benchmark.validation import validate_generation_output
from harbor.models.job.config import JobConfig
from metric_benchmark.io import digest_file, read_json
from pydantic import ValidationError
from test_generation_benchmark_pipeline import RUN_CLI, SMOKE_EXPERIMENT, _make_fake_runner, _write_pipeline_experiment

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


STUB_COMMAND = [sys.executable, "-m", "examples.smoke.stub_generator"]
ONLY_S1: list[dict[str, object]] = [{"eval_subset_id": "s1", "task_ids": ["alpha", "beta"]}]
STAGE_0_2_RECEIPTS = (
    "metric-setup/receipt.json",
    "execution/receipt.json",
    "metric-full/receipt.json",
    "metric-subsets/receipt.json",
    "metric-subsets/subset-000000/receipt.json",
)

SCRIPTED_GENERATOR = """
import argparse, json, os
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--request", required=True)
p.add_argument("--workspace", required=True)
p.add_argument("--output-dir", required=True)
a = p.parse_args()
script = json.loads(Path(a.request).read_text())["settings"]["script"]
out = Path(a.output_dir)
for name in script.get("tasks", []):
    (out / "tasks" / name).mkdir(parents=True)
    (out / "tasks" / name / "task.toml").write_text(f'[task]\\nname = "smoke/{name}"\\n')
for link, target in script.get("symlinks", {}).items():
    os.symlink(target, out / link)
(out / "response.json").write_text(json.dumps(script["response"]))
"""


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record each generation.command launch and its wall time; the command still runs."""
    calls: list[dict[str, Any]] = []
    real = staging.invoke_workspace_command

    def counting(command: list[str], **kwargs: Any) -> Any:
        started = time.time()
        result = real(command, **kwargs)
        calls.append({"command": list(command), "started": started, "seconds": time.time() - started})
        return result

    monkeypatch.setattr(staging, "invoke_workspace_command", counting)
    return calls


def _stub_experiment(
    root: Path,
    *,
    mode: str | None = None,
    timeout_seconds: int = 60,
    subsets: list[dict[str, object]] | None = None,
    command: list[str] | None = None,
    settings: dict[str, object] | None = None,
) -> Path:
    exp = _write_pipeline_experiment(root, subsets_by_case={"smoke": subsets} if subsets is not None else None)
    gen_settings: dict[str, object] = {"prompt": {"path": "gen_prompt.txt"}, **(settings or {})}
    if mode is not None:
        gen_settings["mode"] = mode
    return _configure(
        exp,
        generation={"command": command or STUB_COMMAND, "timeout_seconds": timeout_seconds, "settings": gen_settings},
        excludes=["items.yaml"],
    )


def _runner(exp: Path) -> tuple[dict[str, int], Callable[[JobConfig], None]]:
    counter = {"calls": 0}
    return counter, _make_fake_runner(counter=counter, loaded_exp_path=exp)


def _receipt(work: Path, subset_dir: str = "subset-000000") -> Stage3GenerationReceipt:
    path = work / "cases" / "case-0000" / "generation" / subset_dir / "receipt.json"
    return Stage3GenerationReceipt.model_validate(read_json(path))


def _stage_0_2_digests(work: Path) -> dict[str, str]:
    case_dir = work / "cases" / "case-0000"
    return {rel: digest_file(case_dir / rel) for rel in STAGE_0_2_RECEIPTS}


def test_stage3_stub_run_publishes_request_workspace_and_cover_task(
    tmp_path: Path, launches: list[dict[str, Any]]
) -> None:
    exp = _stub_experiment(tmp_path / "exp")
    work = tmp_path / "work"
    _, runner = _runner(exp)
    summary = run_experiment(exp, "run-1", work, runner=runner)

    assert summary["cases"]["smoke"]["generation"]["s1"] == {
        "status": "complete",
        "generated_tasks": ["cover-gamma"],
        "unavailable_reason": None,
    }
    assert len(launches) == 2
    case_dir = work / "cases" / "case-0000"
    loaded = load_experiment(exp)
    lc = loaded.cases["smoke"]
    for subset in lc.subsets.eval_subsets:
        subset_dir = {"s1": "subset-000000", "s2": "subset-000001"}[subset.eval_subset_id]
        published = case_dir / "generation" / subset_dir
        assert sorted(p.name for p in published.iterdir()) == ["output", "receipt.json", "request.json", "workspace"]
        assert not list((case_dir / "generation").glob("*.pending-*"))

        receipt = _receipt(work, subset_dir)
        assert receipt.status == "complete"
        assert receipt.request_sha256 == digest_file(published / "request.json")
        validate_generation_output(published / "output", subset_id=subset.eval_subset_id)

        request_bytes = (published / "request.json").read_bytes()
        request = GenerationRequest.model_validate(read_json(published / "request.json"))
        payload = json.loads(request_bytes)
        assert payload["schema"] == "nemo.eval_author.generation_request.v1"
        assert request.eval_subset_id == subset.eval_subset_id
        assert request.metric_output_dir == str((case_dir / "metric-subsets" / subset_dir / "output").resolve())
        assert [t.task_id for t in request.tasks] == sorted(subset.task_ids)
        assert sorted(payload) == [
            "agent",
            "case_id",
            "eval_subset_id",
            "max_gaps",
            "metric_output_dir",
            "schema",
            "settings",
            "tasks",
            "workspace",
        ]
        assert b"metric-full" not in request_bytes
        assert b"score" not in request_bytes

        workspace = published / "workspace"
        assert sorted(p.name for p in workspace.iterdir()) == ["ETHOS.md", "agent", "tasks"]
        assert sorted(p.name for p in (workspace / "tasks").iterdir()) == sorted(subset.task_ids)
        names = {part for p in workspace.rglob("*") for part in p.relative_to(workspace).parts}
        assert not names & {"trials", "audit-coverage-report.json", "score.json", "items.yaml", ".eval-author"}
        held_out = held_out_task_ids(lc, subset)
        held_out_dirs = [lc.task_paths[tid].name for tid in held_out]
        _assert_no_held_out_text(_all_files(workspace), [request_bytes], [*held_out, *held_out_dirs])

    s1 = _receipt(work)
    assert s1.generated_tasks == ["cover-gamma"]
    assert s1.attempt_summary["by_status"] == {"accepted": 1}
    cover = case_dir / "generation" / "subset-000000" / "output" / "tasks" / "cover-gamma"
    assert "TOOL: gamma" in (cover / "instruction.md").read_text(encoding="utf-8")
    assert not (case_dir / "generation" / "subset-000000" / "output" / "baseline-report.json").exists()
    s2 = _receipt(work, "subset-000001")
    assert s2.generated_tasks == []
    assert s2.attempt_summary["by_status"] == {"failed": 1}

    gen_inputs = GenerationInputs.model_validate(read_json(work / "generation-inputs.json"))
    assert {"generation", "generation.settings.prompt"} <= set(gen_inputs.entries)


@pytest.mark.parametrize(
    ("mode", "timeout_seconds", "status", "reason_parts", "tasks"),
    [
        (None, 60, "complete", None, ["cover-gamma"]),
        ("timeout", 3, "partial", None, ["cover-gamma"]),
        ("error", 60, "unavailable", ["simulated stub error"], []),
        ("no_response", 60, "unavailable", ["subset s1", "response.json", "missing response.json"], []),
        ("modify_input", 60, "unavailable", ["workspace_modified"], []),
    ],
)
def test_stage3_receipt_status_per_stub_mode(
    tmp_path: Path,
    launches: list[dict[str, Any]],
    mode: str | None,
    timeout_seconds: int,
    status: str,
    reason_parts: list[str] | None,
    tasks: list[str],
) -> None:
    exp = _stub_experiment(tmp_path / "exp", mode=mode, timeout_seconds=timeout_seconds, subsets=ONLY_S1)
    work = tmp_path / "work"
    _, runner = _runner(exp)
    run_experiment(exp, "run-1", work, runner=runner, kill_grace_seconds=1.0)

    receipt = _receipt(work)
    assert len(launches) == 1
    assert receipt.status == status
    assert receipt.generated_tasks == tasks
    if reason_parts is None:
        assert receipt.unavailable_reason is None
    else:
        assert receipt.unavailable_reason is not None
        for part in reason_parts:
            assert part in receipt.unavailable_reason
    output = work / "cases" / "case-0000" / "generation" / "subset-000000" / "output"
    if mode == "timeout":
        assert receipt.timed_out and receipt.returncode is None
        assert (output / "tasks" / "cover-gamma" / "task.toml").is_file()
    if mode == "modify_input":
        assert not output.exists()
        assert receipt.output_files == {}


def test_stage3_ignore_sigterm_gets_sigterm_then_sigkill_after_grace(
    tmp_path: Path, launches: list[dict[str, Any]]
) -> None:
    exp = _stub_experiment(tmp_path / "exp", mode="ignore_sigterm", timeout_seconds=2, subsets=ONLY_S1)
    work = tmp_path / "work"
    _, runner = _runner(exp)
    run_experiment(exp, "run-1", work, runner=runner, kill_grace_seconds=1.5)

    receipt = _receipt(work)
    assert receipt.status == "partial"
    assert receipt.timed_out and receipt.returncode is None
    marker = work / "cases" / "case-0000" / "generation" / "subset-000000" / "output" / "logs" / "sigterm-received"
    sigterm_at = float(marker.read_text(encoding="utf-8"))
    (launch,) = launches
    assert 2.0 <= sigterm_at - launch["started"] < 3.5
    assert 3.5 <= launch["seconds"] < 8.0


def test_stage3_reuse_settings_change_deletion_tamper_and_invalidation(
    tmp_path: Path, launches: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    exp = _stub_experiment(tmp_path / "exp", subsets=ONLY_S1)
    work = tmp_path / "work"
    harbor, runner = _runner(exp)
    published = work / "cases" / "case-0000" / "generation" / "subset-000000"

    run_experiment(exp, "run-1", work, runner=runner)
    assert len(launches) == 1
    first = _receipt(work)
    receipt_mtime = (published / "receipt.json").stat().st_mtime_ns
    upstream = _stage_0_2_digests(work)

    run_experiment(exp, "run-1", work, runner=runner)
    assert len(launches) == 1
    assert (published / "receipt.json").stat().st_mtime_ns == receipt_mtime

    # Same path, new contents, re-signed: new generation entry and configuration digest; stages 0-2 still match.
    (exp.parent / "gen_prompt.txt").write_text("prompt v2\n", encoding="utf-8")
    sign_experiment(exp, resign=True)
    run_experiment(exp, "run-1", work, runner=runner)
    assert len(launches) == 2
    second = _receipt(work)
    assert second.configuration_sha256 != first.configuration_sha256
    assert second.request_sha256 == first.request_sha256
    assert _stage_0_2_digests(work) == upstream
    assert harbor["calls"] == 1

    # A changed request digest does not reuse the old output.
    run_experiment(exp, "run-1", work, runner=runner, max_gaps=1)
    assert len(launches) == 3
    third = _receipt(work)
    assert third.request_sha256 != second.request_sha256
    assert GenerationRequest.model_validate(read_json(published / "request.json")).max_gaps == 1
    run_experiment(exp, "run-1", work, runner=runner, max_gaps=1)
    assert len(launches) == 3

    shutil.rmtree(published)
    stale = published.parent / "subset-000000.pending-999999"
    (stale / "workspace").mkdir(parents=True)
    run_experiment(exp, "run-1", work, runner=runner, max_gaps=1)
    assert len(launches) == 4
    assert _receipt(work).status == "complete"
    assert not stale.exists()

    response = published / "output" / "response.json"
    response.write_text(response.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match=r"subset-000000: published generation directory changed"):
        run_experiment(exp, "run-1", work, runner=runner, max_gaps=1)
    assert len(launches) == 4

    capsys.readouterr()
    run_experiment(exp, "run-1", work, runner=runner, max_gaps=1, invalidate_stages=["generate"])
    err = capsys.readouterr().err
    assert str(published.parent) in err
    assert "generate and every later stage will rerun" in err
    assert len(launches) == 5
    assert _stage_0_2_digests(work) == upstream
    assert harbor["calls"] == 1


def test_stage3_zero_gap_never_launches(tmp_path: Path, launches: list[dict[str, Any]]) -> None:
    all_tasks: list[dict[str, object]] = [{"eval_subset_id": "s1", "task_ids": ["alpha", "beta", "gamma", "delta"]}]
    exp = _stub_experiment(tmp_path / "exp", subsets=all_tasks)
    work = tmp_path / "work"
    _, runner = _runner(exp)
    summary = run_experiment(exp, "run-1", work, runner=runner)
    run_experiment(exp, "run-1", work, runner=runner)

    assert launches == []
    assert summary["cases"]["smoke"]["generation"]["s1"]["status"] == "zero_gap"
    receipt = _receipt(work)
    assert receipt.status == "zero_gap"
    assert receipt.request_sha256 is None
    assert receipt.generated_tasks == []
    published = work / "cases" / "case-0000" / "generation" / "subset-000000"
    assert sorted(p.name for p in published.iterdir()) == ["receipt.json"]


def test_stage3_keeps_generated_directory_named_like_subset_task(
    tmp_path: Path, launches: list[dict[str, Any]]
) -> None:
    script = tmp_path / "scripted_generator.py"
    script.write_text(SCRIPTED_GENERATOR, encoding="utf-8")
    response = {
        "schema": "nemo.eval_author.generation_response.v1",
        "attempts": [{"slug": "alpha", "status": "accepted", "task_path": "tasks/alpha"}],
        "unattempted": [],
        "error": None,
    }
    exp = _stub_experiment(
        tmp_path / "exp",
        subsets=ONLY_S1,
        command=[sys.executable, str(script)],
        settings={"script": {"tasks": ["alpha", "cover-x"], "response": response}},
    )
    work = tmp_path / "work"
    _, runner = _runner(exp)
    run_experiment(exp, "run-1", work, runner=runner)

    receipt = _receipt(work)
    assert receipt.status == "complete"
    assert receipt.unavailable_reason is None
    assert receipt.generated_tasks == ["alpha", "cover-x"]
    output = work / "cases" / "case-0000" / "generation" / "subset-000000" / "output"
    assert (output / "tasks" / "alpha" / "task.toml").is_file()
    assert "leak" not in json.dumps(receipt.to_payload())


@pytest.mark.parametrize(
    ("response_changes", "symlinks", "path_text"),
    [
        ({"held_out": []}, {}, "held_out"),
        ({"baseline_report_path": "../request.json"}, {}, "../request.json"),
        ({"baseline_report_path": "/etc/passwd"}, {}, "/etc/passwd"),
        (
            {"attempts": [{"slug": "cover-x", "status": "accepted", "task_path": "linked/cover-x"}]},
            {"linked": "tasks"},
            "linked/cover-x",
        ),
    ],
)
def test_stage3_invalid_response_makes_subset_unavailable(
    tmp_path: Path,
    launches: list[dict[str, Any]],
    response_changes: dict[str, object],
    symlinks: dict[str, str],
    path_text: str,
) -> None:
    script = tmp_path / "scripted_generator.py"
    script.write_text(SCRIPTED_GENERATOR, encoding="utf-8")
    response = {"schema": "nemo.eval_author.generation_response.v1", **response_changes}
    exp = _stub_experiment(
        tmp_path / "exp",
        subsets=ONLY_S1,
        command=[sys.executable, str(script)],
        settings={"script": {"tasks": ["cover-x"], "response": response, "symlinks": symlinks}},
    )
    work = tmp_path / "work"
    _, runner = _runner(exp)
    run_experiment(exp, "run-1", work, runner=runner)

    receipt = _receipt(work)
    assert len(launches) == 1
    assert receipt.status == "unavailable"
    assert receipt.unavailable_reason is not None
    assert "subset s1" in receipt.unavailable_reason
    assert path_text in receipt.unavailable_reason


def test_stage3_unloadable_stage2_output_is_unavailable_without_launch(
    tmp_path: Path, launches: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stage 2 output that does not load: stage 3 records unavailable and launches nothing.
    exp = _stub_experiment(tmp_path / "exp", subsets=ONLY_S1)
    score = exp.parent / "full_only_report.py"
    score.write_text(
        "import json, subprocess, sys\n"
        "from pathlib import Path\n"
        f"subprocess.run([sys.executable, {str(RUN_CLI.parent / 'reference_metric.py')!r}, *sys.argv[1:]], check=True)\n"
        "req = json.loads(Path(sys.argv[sys.argv.index('--request') + 1]).read_text())\n"
        "if req['scope'] == 'subset':\n"
        "    (Path(sys.argv[sys.argv.index('--output-dir') + 1]) / 'audit-coverage-report.json').unlink()\n",
        encoding="utf-8",
    )
    raw = yaml.safe_load(exp.read_text(encoding="utf-8"))
    raw["metric"]["score"]["command"] = [sys.executable, str(score)]
    exp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    sign_experiment(exp, resign=True)
    _, runner = _runner(exp)
    work = tmp_path / "work"
    run_experiment(exp, "run-1", work, runner=runner)
    receipt = _receipt(work)
    assert launches == []
    assert receipt.status == "unavailable"
    assert receipt.unavailable_reason is not None
    assert "audit-coverage-report.json" in receipt.unavailable_reason

    # The post-staging load check: a stage 2 output that stops loading as metric.score.output.
    exp2 = _stub_experiment(tmp_path / "exp2", subsets=ONLY_S1)
    (exp2.parent / "flaky_output.py").write_text(
        "import os\n"
        "from generation_benchmark.reference_metric import ToolCallsScoreOutput\n\n\n"
        "class FlakyOutput(ToolCallsScoreOutput):\n"
        "    @classmethod\n"
        "    def load(cls, directory):\n"
        "        if os.environ.get('FLAKY_OUTPUT_FAIL') == '1':\n"
        "            raise ValueError('flaky output refused to load')\n"
        "        return super().load(directory)\n",
        encoding="utf-8",
    )
    raw2 = yaml.safe_load(exp2.read_text(encoding="utf-8"))
    raw2["metric"]["score"]["output"] = {"python": sys.executable, "type": "flaky_output:FlakyOutput"}
    exp2.write_text(yaml.safe_dump(raw2, sort_keys=False), encoding="utf-8")
    sign_experiment(exp2, resign=True)
    _, runner2 = _runner(exp2)
    work2 = tmp_path / "work2"
    run_experiment(exp2, "run-1", work2, runner=runner2)
    assert len(launches) == 1
    case_dir = work2 / "cases" / "case-0000"
    shutil.rmtree(case_dir / "generation")

    monkeypatch.setenv("FLAKY_OUTPUT_FAIL", "1")
    computed, actual = check_experiment(exp2)
    verified = verify_lock_entries(actual, computed.lock, list(actual.entries))
    receipts = generate(computed.loaded, verified, computed.loaded.cases["smoke"], case_dir)
    assert len(launches) == 1
    assert receipts["s1"].status == "unavailable"
    assert receipts["s1"].unavailable_reason is not None
    assert "subset s1" in receipts["s1"].unavailable_reason
    assert "flaky output refused to load" in receipts["s1"].unavailable_reason
    assert not (case_dir / "generation" / "subset-000000" / "output").exists()


def test_max_gaps_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--max-gaps must be at least 1"):
        run_experiment(SMOKE_EXPERIMENT, "run-1", tmp_path / "work", max_gaps=0)
    proc = subprocess.run(
        [sys.executable, str(RUN_CLI), "--benchmark", str(SMOKE_EXPERIMENT), "--run-id", "r", "--work-root", "w"]
        + ["--max-gaps", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "must be at least 1" in proc.stderr
    assert not (tmp_path / "work").exists()


@pytest.mark.skipif(
    os.environ.get("GENERATION_BENCHMARK_DOCKER_TEST") != "1",
    reason="Docker Harbor 0.24.0 smoke test runs only when GENERATION_BENCHMARK_DOCKER_TEST=1",
)
def test_smoke_docker_stage3_generates_cover_gamma(tmp_path: Path) -> None:
    work_root = tmp_path / "smoke-work"
    proc = subprocess.run(
        [
            sys.executable,
            str(RUN_CLI),
            "--benchmark",
            str(SMOKE_EXPERIMENT),
            "--run-id",
            "smoke-stage3",
            "--work-root",
            str(work_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout[proc.stdout.index("{") :])
    assert summary["cases"]["smoke"]["generation"]["s1"] == {
        "status": "complete",
        "generated_tasks": ["cover-gamma"],
        "unavailable_reason": None,
    }
    receipt = _receipt(work_root)
    assert receipt.generated_tasks == ["cover-gamma"]
    validate_generation_output(
        work_root / "cases" / "case-0000" / "generation" / "subset-000000" / "output", subset_id="s1"
    )
