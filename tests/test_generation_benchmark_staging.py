# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest
from generation_benchmark.models import (
    GenerationAgentRef,
    GenerationAttempt,
    GenerationRequest,
    GenerationResponse,
    Task,
    write_model,
)
from generation_benchmark.validation import validate_generation_output
from pydantic import ValidationError


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
