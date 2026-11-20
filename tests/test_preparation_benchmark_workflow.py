# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import itertools
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_live_build_workflow import condition, workflow

ROOT = Path(__file__).resolve().parents[1]
WORKERS = ("1", "2", "4", "8")


@pytest.mark.parametrize(
    "event,branch,run_live,benchmark",
    list(
        itertools.product(
            ("pull_request", "push", "workflow_dispatch"),
            ("refs/heads/main", "refs/heads/feature"),
            (False, True),
            (False, True),
        )
    ),
)
def test_benchmark_event_gates_preserve_ordinary_model_and_preparation_scope(event, branch, run_live, benchmark):
    jobs = workflow()["jobs"]
    context = {
        "github.event_name": event,
        "github.ref": branch,
        "inputs.run_live": run_live,
        "inputs.prep_benchmark": benchmark if event == "workflow_dispatch" else "",
        "inputs.tier": "3",
        "vars.SKILL_EVALUATION_LIVE_ENABLED": "true",
        "needs.plan.result": "success",
        "needs.build-opencode.result": "success",
    }
    live_requested = branch == "refs/heads/main" and (event == "push" or (event == "workflow_dispatch" and run_live))
    benchmark_requested = event == "workflow_dispatch" and benchmark
    authorized = live_requested and not benchmark_requested
    assert condition(jobs["authorize"], context) == authorized
    assert condition(jobs["aggregate"], context) == authorized
    context["needs.authorize.result"] = "success" if authorized else "skipped"
    # Configured outputs cannot override the skipped authorization dependency.
    for model in ("chat", "embedding", "agent"):
        context[f"needs.authorize.outputs.{model}"] = "configured"
    assert condition(jobs["evaluate"], context) == authorized
    preparation_requested = event == "pull_request" or (event == "workflow_dispatch" and not run_live)
    assert condition(jobs["preparation-timing"], context) == preparation_requested
    assert condition(jobs["build-opencode"], context) == (preparation_requested or live_requested)
    prepare = next(step for step in jobs["preparation-timing"]["steps"] if step.get("id") == "prepare")
    expression = prepare["env"]["PREP_BENCHMARK"].removeprefix("${{").removesuffix("}}")
    assert condition({"if": expression}, context) == (benchmark_requested and not run_live)


@pytest.mark.parametrize("benchmark", ("true", "false"))
@pytest.mark.parametrize("workers", (*WORKERS, "3", "8; bad"))
def test_actual_prepare_bash_forwards_only_valid_opt_in_workers(tmp_path, benchmark, workers):
    prepare = next(step for step in workflow()["jobs"]["preparation-timing"]["steps"] if step.get("id") == "prepare")
    recorder = tmp_path / "argv"
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$RECORDER"\n')
    sudo.chmod(0o700)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", prepare["run"]],
        cwd=ROOT,
        env={
            "PATH": str(tmp_path) + os.pathsep + os.defpath,
            "PREP_BENCHMARK": benchmark,
            "PREP_WORKERS": workers,
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(tmp_path),
            "RECORDER": str(recorder),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    invalid = benchmark == "true" and workers not in WORKERS
    assert result.returncode == (1 if invalid else 0), result.stderr
    assert recorder.exists() == (not invalid)
    if invalid:
        assert result.stderr == "invalid preparation worker selection\n"
        assert not summary.exists()
        return
    assert result.stderr == ""
    assert recorder.read_text().splitlines() == [
        "python3",
        "tools/live_shard.py",
        "prepare",
        "--root",
        str(tmp_path / "live-shard"),
        "--timing-checkpoint",
        str(tmp_path / "preparation-operation-timing.json"),
        *(["--workers", workers] if benchmark == "true" else []),
    ]
    if benchmark == "true":
        assert summary.read_text() == f"Credential-free full Audit preparation benchmark; workers: {workers}.\n"
    else:
        assert not summary.exists()


def test_benchmark_inputs_are_opt_in_and_worker_choice_is_step_scoped():
    definition = workflow()
    triggers = definition.get("on", definition.get(True))
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["prep_benchmark"]["type"] == "boolean"
    assert inputs["prep_benchmark"]["default"] is False
    assert inputs["prep_workers"]["type"] == "choice"
    assert inputs["prep_workers"]["options"] == list(WORKERS)
    assert inputs["prep_workers"]["default"] == "1"
    timing = definition["jobs"]["preparation-timing"]
    assert timing["env"] == {"SHARD_TIER": "3", "SHARD_CHECK": "live", "SHARD_SKILL": "eval-author-audit"}
    assert "environment" not in timing
    assert "secrets." not in json.dumps(timing)
    assert "live_shard.py run " not in json.dumps(timing)
    prepare = next(step for step in timing["steps"] if step.get("id") == "prepare")
    assert prepare["env"]["PREP_WORKERS"] == "${{ inputs.prep_workers || '1' }}"
    for job in definition["jobs"].values():
        for step in job["steps"]:
            if step is not prepare:
                assert "--workers" not in step.get("run", "")
                assert "PREP_WORKERS" not in json.dumps(step)
    production = next(
        step for step in definition["jobs"]["evaluate"]["steps"] if "live_shard.py prepare" in step.get("run", "")
    )
    assert production["run"] == 'sudo python3 tools/live_shard.py prepare --root "$RUNNER_TEMP/live-shard"\n'


def test_benchmark_retains_existing_artifact_name_and_exact_public_scope():
    steps = workflow()["jobs"]["preparation-timing"]["steps"]
    uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "preparation-timing-${{ github.run_id }}-${{ github.run_attempt }}"
    assert upload["with"]["path"].splitlines() == [
        "${{ runner.temp }}/preparation-timing-summary.json",
        "${{ runner.temp }}/preparation-operation-timing.json",
        "${{ runner.temp }}/tooling-build-diagnostics.json",
    ]
    assert upload["with"]["retention-days"] == 30
    assert upload["with"]["if-no-files-found"] == "error"
