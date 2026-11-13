# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/request-nvskills-ci.yml").read_text())


def test_cancellation_workflow_waits_for_successful_permission_check():
    data = workflow()
    gate = data["jobs"]["authorize"]
    request = data["jobs"]["request"]
    assert request["needs"] == "authorize"
    assert request["if"] == "needs.authorize.outputs.allowed == 'true'"
    assert gate["outputs"]["allowed"] == "${{ steps.permission.outputs.allowed }}"
    assert "concurrency" not in data and "concurrency" not in gate
    assert gate["permissions"] == {"contents": "read"}
    assert "secrets" not in gate and "environment" not in gate
    assert all("uses" not in step for step in gate["steps"])
    assert "github.event.issue.pull_request" in gate["if"]
    assert "NVSKILLS_SIGNATURE_PUSH_ACTOR" in gate["if"]
    assert "NVSKILLS_SIGNATURE_COMMIT_TITLE" in gate["if"]
    assert request["uses"].endswith("@3687569c216eb7e15712231e2615dfa1b79084a5")


@pytest.mark.parametrize(
    "event,actor,message,pull_request,comment,variables,expected",
    [
        ("issue_comment", "reader", "", True, "/nvskills-ci", {}, True),
        ("issue_comment", "reader", "", False, "/nvskills-ci", {}, False),
        ("issue_comment", "reader", "", True, "ordinary comment", {}, False),
        ("push", "nv-skills-ci[bot]", "Attach NVSkills validation signatures", False, "", {}, True),
        ("push", "other", "Attach NVSkills validation signatures", False, "", {}, False),
        ("push", "nv-skills-ci[bot]", "Other change", False, "", {}, False),
        (
            "push",
            "custom-bot",
            "Custom signatures",
            False,
            "",
            {"NVSKILLS_SIGNATURE_PUSH_ACTOR": "custom-bot", "NVSKILLS_SIGNATURE_COMMIT_TITLE": "Custom signatures"},
            True,
        ),
    ],
)
def test_authorization_job_preserves_request_event_selection(
    event, actor, message, pull_request, comment, variables, expected
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for workflow expression probe")
    github = {
        "event_name": event,
        "actor": actor,
        "event": {
            "issue": {"pull_request": pull_request},
            "comment": {"body": comment},
            "head_commit": {"message": message},
        },
    }
    # This expression uses only boolean operators, equality, and startsWith.
    script = (
        "const [expr, github, vars] = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const result = new Function('github', 'vars', 'startsWith', 'return Boolean(' + expr + ')')"
        "(github, vars, (a,b) => a.startsWith(b)); console.log(JSON.stringify(result));"
    )
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps([workflow()["jobs"]["authorize"]["if"], github, variables]),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout) is expected


@pytest.mark.parametrize(
    "event,role,api_status,allowed",
    [
        ("issue_comment", "admin", 0, True),
        ("issue_comment", "maintain", 0, True),
        ("issue_comment", "write", 0, False),
        ("issue_comment", "read", 0, False),
        ("issue_comment", "", 0, False),
        ("issue_comment", "admin", 1, False),
        ("issue_comment", "admin\nallowed=true", 0, False),
        ("push", "", 1, True),
    ],
)
def test_permission_step_fails_closed_before_upstream_call(tmp_path, event, role, api_status, allowed):
    step = workflow()["jobs"]["authorize"]["steps"][0]
    assert step["env"]["ACTOR"] == "${{ github.actor }}"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["shell"] == "bash"
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CALL_LOG"\nprintf "%s\\n" "$ROLE"\nexit "$API_STATUS"\n')
    gh.chmod(0o700)
    output = tmp_path / "output"
    output.touch()
    log = tmp_path / "call"
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "EVENT_NAME": event,
        "REPO": "owner/repo",
        "ACTOR": "test-actor",
        "GH_TOKEN": "synthetic-only",
        "GITHUB_OUTPUT": str(output),
        "CALL_LOG": str(log),
        "ROLE": role,
        "API_STATUS": str(api_status),
    }
    result = subprocess.run(["/bin/bash", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=5)
    assert (output.read_text() == "allowed=true\n") == allowed
    if allowed:
        assert result.returncode == 0
    if event == "issue_comment":
        assert log.read_text().splitlines() == [
            "api",
            "repos/owner/repo/collaborators/test-actor/permission",
            "--jq",
            '.role_name // ""',
        ]
    else:
        assert not log.exists()
