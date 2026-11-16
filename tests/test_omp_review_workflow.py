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


def test_review_runs_only_for_trusted_same_repo_humans():
    condition = JOBS["review"]["if"]
    assert "head.repo.full_name == github.repository" in condition
    assert "user.type == 'User'" in condition
    assert '"COLLABORATOR"' in condition


def test_host_steps_never_execute_pr_code():
    for step in STEPS:
        run = step.get("run", "")
        assert "pr/" not in run.replace('--repo "$GITHUB_WORKSPACE/pr"', ""), step.get("name")
        assert "${{" not in run, step.get("name")
