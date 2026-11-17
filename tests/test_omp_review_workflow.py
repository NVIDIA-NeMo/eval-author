# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/omp-review.yml").read_text())
JOBS: dict[str, Any] = WORKFLOW["jobs"]
STEPS = [step for job in JOBS.values() for step in job["steps"]]


def test_only_trigger_is_pull_request_target():
    # PyYAML parses the bare `on` key as boolean True.
    assert WORKFLOW[True] == {"pull_request_target": {"types": ["opened", "reopened", "ready_for_review", "labeled"]}}


def test_permissions_are_least_privilege():
    assert WORKFLOW["permissions"] == {}
    assert JOBS["authorize"]["permissions"] == {"contents": "read"}
    assert JOBS["review"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert JOBS["review"]["environment"] == "omp-review"
    assert JOBS["post"]["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "secrets." not in json.dumps(JOBS["post"])


def test_checkouts_drop_credentials_and_only_pr_checkout_uses_head():
    checkouts = [step for step in STEPS if step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts and all(step["with"]["persist-credentials"] is False for step in checkouts)
    head = [step for step in checkouts if "github.event.pull_request.head.sha" in str(step["with"].get("ref", ""))]
    assert len(head) == 1 and head[0]["with"]["path"] == "pr"


def test_secrets_reach_only_the_sandbox_step():
    with_secrets = [step.get("name") for step in STEPS if "secrets." in json.dumps(step)]
    assert with_secrets == ["Run sandboxed review"]


def test_review_requires_authorized_same_repo_humans():
    condition = JOBS["authorize"]["if"]
    assert "head.repo.full_name == github.repository" in condition
    assert "pull_request.user.type == 'User'" in condition
    assert "sender.type == 'User'" in condition
    gate = JOBS["authorize"]["steps"][0]["run"]
    assert "^(admin|maintain)$" in gate and "^(admin|write)$" in gate
    assert JOBS["review"]["needs"] == "authorize"
    assert JOBS["review"]["if"] == "needs.authorize.outputs.allowed == 'true'"


def test_only_staged_review_json_is_uploaded():
    steps = JOBS["review"]["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    stage = next(step for step in steps if step.get("name") == "Stop the sandbox and stage review.json")
    assert upload["with"]["path"] == "${{ runner.temp }}/omp-review/upload/review.json"
    assert stage["if"] == upload["if"] == "always()"
    assert steps.index(stage) == steps.index(upload) - 1


def test_agent_runs_under_pinned_gvisor():
    steps = {step.get("name"): step for step in JOBS["review"]["steps"]}
    install = steps["Install gVisor"]
    assert "sha512sum -c" in install["run"] and len(install["env"]["GVISOR_SHA512"]) == 128
    assert steps["Run sandboxed review"]["env"]["OMP_REVIEW_RUNTIME"] == "runsc"


def test_host_run_steps_skip_pr_checkout_and_inline_expressions():
    for step in STEPS:
        run = step.get("run", "")
        assert "pr/" not in run.replace('--repo "$GITHUB_WORKSPACE/pr"', ""), step.get("name")
        assert "${{" not in run, step.get("name")
