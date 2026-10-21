# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Integration checks against a separately installed, real Gym runtime."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("EVAL_AUTHOR_SOURCE_ROOT", HARNESS_ROOT))
GYM_PYTHON = os.environ.get("EVAL_AUTHOR_GYM_PYTHON")
pytestmark = pytest.mark.skipif(not GYM_PYTHON, reason="set EVAL_AUTHOR_GYM_PYTHON to an installed Gym v0.6.0+ runtime")


def scaffold_draft(tmp_path):
    assert GYM_PYTHON is not None
    proposals = tmp_path / ".eval-author/proposals"
    proposals.mkdir(parents=True)
    instruction = proposals / "cover-ledger-total-instruction.md"
    instruction.write_text("Use ledger_total to total the supplied ledger and report the amount.")
    report = tmp_path / "coverage.json"
    from audit_fixtures import bound_gap

    bound_gap(tmp_path, report, "ledger_total", source=ROOT)
    output = tmp_path / ".eval-author/task-drafts/cover-ledger-total"
    command = [
        sys.executable,
        str(ROOT / "skills/eval-author-task-create/scripts/task_pipeline.py"),
        "scaffold",
        "--provider",
        "gym",
        "--gym-python",
        GYM_PYTHON,
        "--report",
        str(report),
        "--target",
        "ledger_total",
        "--out",
        str(output),
        "--task-name",
        "example/cover-ledger-total",
        "--description",
        "Test ledger summation",
        "--author",
        "Eval Author test fixture",
        "--instruction-file",
        str(instruction),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    return output, instruction, command, receipt


def scaffold_trace_draft(tmp_path, *, gym_rollouts=None):
    """Follow the trace extension without creating an audit or selecting a coverage gap."""
    assert GYM_PYTHON is not None
    helper = ROOT / "skills/eval-author-trace-environment/scripts/trace_environment.py"

    def trace_command(*args):
        result = subprocess.run(
            [sys.executable, str(helper), *map(str, args)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    initialized = trace_command(
        "init", "--root", tmp_path / ".eval-author/trace-environments", "--task-id", "cover-ledger-total"
    )
    task_dir = Path(initialized["task_dir"])
    instruction_text = "Use ledger_total and report the total for each list: [7, -2, 13] and [0, 6, -10]."
    source = task_dir / "private/input.atif.json"
    synthetic_atif = json.dumps(
        {
            "schema_version": "ATIF-v1.7",
            "session_id": "synthetic-ledger",
            "agent": {"name": "fixture-agent", "version": "1"},
            "steps": [
                {"step_id": 1, "source": "user", "message": instruction_text},
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": "Compute the totals.",
                    "tool_calls": [
                        {
                            "tool_call_id": "call-1",
                            "function_name": "ledger_total",
                            "arguments": {"amounts": [7, -2, 13]},
                        }
                    ],
                    "observation": {"results": [{"source_call_id": "call-1", "content": '{"total":18}'}]},
                },
            ],
        }
    )
    if gym_rollouts is None:
        source.write_text(synthetic_atif)
    else:
        scripts = ROOT / "skills/gym-to-atif/scripts"
        loader = ROOT / "skills/eval-author/scripts/trace_loading.py"
        for command, directory in (
            ([str(scripts / "gym_to_atif.py")], "converted"),
            ([str(loader), "--source-kind", "gym"], "ng-evidence"),
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    *command,
                    "--input",
                    str(gym_rollouts),
                    "--row",
                    "1",
                    "--output-dir",
                    str(task_dir / "private" / directory),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        raw = gym_rollouts.read_bytes().splitlines(keepends=True)[0]
        converted = task_dir / "private/converted"
        assert (converted / "source.gym.json").read_bytes() == raw
        [normalized_line] = (task_dir / "private/ng-evidence/traces.normalized.jsonl").read_text().splitlines()
        normalized = json.loads(normalized_line)
        assert normalized["attributes"]["gym"]["ng_trajectory"] == json.loads(raw)["ng_trajectory"]
        source = converted / "trace.atif.json"
        atif = json.loads(source.read_text())
        instruction_text = next(step["message"] for step in atif["steps"] if step["source"] == "user")
        tool_names = [call["function_name"] for step in atif["steps"] for call in step.get("tool_calls", [])]
        assert "ledger_total" in tool_names
        # Attachment-only evidence loads, but is not silently promoted to an ATIF trajectory.
        attachment = task_dir / "private/attachment-only.json"
        attachment.write_text(json.dumps({"ng_trajectory": json.loads(raw)["ng_trajectory"]}))
        loaded = subprocess.run(
            [
                sys.executable,
                str(loader),
                "--source-kind",
                "gym",
                "--input",
                str(attachment),
                "--output-dir",
                str(task_dir / "private/attachment-evidence"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert loaded.returncode == 0, loaded.stdout + loaded.stderr
        assert json.loads(loaded.stdout)["atif_emitted"] is False
        rejected = subprocess.run(
            [
                sys.executable,
                str(scripts / "gym_to_atif.py"),
                "--input",
                str(attachment),
                "--output-dir",
                str(task_dir / "private/unsupported-atif"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rejected.returncode == 2
        assert not (task_dir / "private/unsupported-atif/trace.atif.json").exists()
    trace_command(
        "prepare", "--task-dir", task_dir, "--atif", source, "--source-kind", "gym" if gym_rollouts else "atif"
    )
    trace_command(
        "review-privacy", "--task-dir", task_dir, "--reviewer-kind", "agent", "--note", "Synthetic test data reviewed."
    )
    trace_command("inventory-tool-calls", "--task-dir", task_dir)
    trace_command("plan-tool-call-access", "--task-dir", task_dir)
    decisions = task_dir / "private/decisions.json"
    decisions.write_text(
        json.dumps(
            {
                "schema": "nemo.eval_author.trace_environment_tool_access_decisions.v1",
                "decisions": [
                    {
                        "name": "ledger_total",
                        "access": "real",
                        "adapter": None,
                        "note": "Implement the evidenced integer summation in the native resources server.",
                    }
                ],
            }
        )
    )
    trace_command(
        "resolve-tool-call-access", "--task-dir", task_dir, "--decisions", decisions, "--reviewer-kind", "agent"
    )
    candidate = {
        "schema": "nemo.eval_author.trace_environment_candidate.v2",
        "status": "candidate",
        "decision_basis": "safe_atif_only",
        "instruction": instruction_text,
        "requirements": [{"description": "Call ledger_total and return the integer sum.", "evidence_steps": [1, 2]}],
        "verification_mode": "execution",
        "evidence_steps": [1, 2],
        "state_basis": "reconstructed",
        "uncertainties": ["Starting application state was not captured; construct a stateless summation tool."],
        "reason_codes": [],
        "ground_truth": {
            "availability": "absent",
            "use": "none",
            "artifacts": [],
            "absence_reason": "No independent reference; integer summation is verifiable from the supplied operands.",
        },
        "software_requirements": [],
    }
    (task_dir / "candidate.json").write_text(json.dumps(candidate))
    checked = trace_command("check-candidate", "--task-dir", task_dir)
    assert checked["valid"] and not checked["execution_verified"]
    instruction = task_dir / "private/gym-instruction.md"
    instruction.write_text(candidate["instruction"])
    output = task_dir / "gym"
    command = [
        GYM_PYTHON,
        str(ROOT / "skills/eval-author-task-create/scripts/gym_scaffold.py"),
        "--out",
        str(output),
        "--name",
        "cover_ledger_total",
        "--instruction-file",
        str(instruction),
        "--description",
        "Trace-derived integer summation",
        "--author",
        "Eval Author test fixture",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90, umask=0o077)
    assert result.returncode == 0, result.stdout + result.stderr
    assert trace_command("check", "--task-dir", task_dir)["valid"]
    # Native drafts must not manufacture Harbor proof or finalize a pending preparation record.
    summary = json.loads((task_dir / "summary.json").read_text())
    assert summary["status"] == "pending"
    assert summary["source"]["kind"] == ("gym" if gym_rollouts else "atif")
    assert summary["environment"]["technical_status"] == "not_run"
    assert not (task_dir / "validation.json").exists()
    assert not (tmp_path / "coverage.json").exists()
    assert not (output / "task.toml").exists()
    return output, instruction, command, json.loads(result.stdout)


def scaffold_ng_trace_draft(tmp_path):
    """Produce a real Gym rollout attachment, then derive a new native task from one row."""
    assert GYM_PYTHON is not None
    resource = tmp_path / "source-resource"
    (resource / "tests").mkdir(parents=True)
    fixtures = HARNESS_ROOT / "tests/fixtures/gym_ledger"
    shutil.copyfile(fixtures / "app.py", resource / "app.py")
    shutil.copyfile(fixtures / "verifier_cases.jsonl", resource / "tests/verifier_cases.jsonl")
    output = tmp_path / "source-run"
    result = subprocess.run(
        [
            GYM_PYTHON,
            str(fixtures / "smoke.py"),
            "--output",
            str(output),
            "--resources-app",
            str(resource / "app.py"),
            "--dataset",
            str(fixtures / "data.jsonl"),
        ],
        capture_output=True,
        text=True,
        timeout=90,
        umask=0o077,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rollouts = output / "cli-rollouts.jsonl"
    row = json.loads(rollouts.read_text().splitlines()[0])
    assert row["ng_trajectory"]["schema_version"] == "1.0"
    assert row["ng_trajectory"]["invocations"]
    assert row["responses_create_params"]["input"]
    assert row["response"]["output"]
    return scaffold_trace_draft(tmp_path, gym_rollouts=rollouts)


@pytest.fixture(
    params=[scaffold_draft, scaffold_trace_draft, scaffold_ng_trace_draft], ids=["audit", "trace", "ng-trajectory"]
)
def draft_builder(request):
    # Build inside the test call so preparation regressions are JUnit failures,
    # while fixture/setup errors remain incomplete execution evidence.
    return request.param


def test_native_scaffold_and_validation(tmp_path, draft_builder):
    assert GYM_PYTHON is not None
    output, instruction, command, receipt = draft_builder(tmp_path)
    assert receipt["provider"] == "gym" and receipt["written"]
    assert not receipt["runnable"]
    assert (output / "instruction.md").read_text() == instruction.read_text()
    row = json.loads((output / "environments/cover_ledger_total/data/example.jsonl").read_text())
    assert row["responses_create_params"]["input"][0]["content"] == instruction.read_text()
    before = (output / "draft.json").read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (output / "draft.json").read_bytes() == before
    validation_command = [
        str(Path(GYM_PYTHON).with_name("gym")),
        "env",
        "validate",
        "--manifest",
        str(output / "environments/cover_ledger_total/manifest.yaml"),
        "--json",
    ]
    validation = subprocess.run(validation_command, cwd=output, capture_output=True, text=True, timeout=60)
    assert validation.returncode == 0, validation.stdout + validation.stderr
    assert json.loads(validation.stdout)["datasets"][0]["rows"] == 1
    (output / "environments/cover_ledger_total/data/example.jsonl").unlink()
    validation = subprocess.run(validation_command, cwd=output, capture_output=True, text=True, timeout=60)
    assert validation.returncode != 0


def test_native_http_execution_controls(tmp_path, draft_builder, request):
    """Exercise real tool HTTP calls, native rollouts, and positive and negative controls."""
    assert GYM_PYTHON is not None
    draft, _, _, _ = draft_builder(tmp_path)
    resource_app = draft / "resources_servers/cover_ledger_total/app.py"
    resource_app.write_bytes((HARNESS_ROOT / "tests/fixtures/gym_ledger/app.py").read_bytes())
    (resource_app.parent / "tests/verifier_cases.jsonl").write_bytes(
        (HARNESS_ROOT / "tests/fixtures/gym_ledger/verifier_cases.jsonl").read_bytes()
    )
    dataset = draft / "environments/cover_ledger_total/data/example.jsonl"
    dataset.write_bytes((HARNESS_ROOT / "tests/fixtures/gym_ledger/data.jsonl").read_bytes())
    validation = subprocess.run(
        [
            str(Path(GYM_PYTHON).with_name("gym")),
            "env",
            "validate",
            "--manifest",
            str(draft / "environments/cover_ledger_total/manifest.yaml"),
            "--json",
        ],
        cwd=draft,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    assert json.loads(validation.stdout)["datasets"][0]["rows"] == 2
    # The CI collector retains native evidence outside pytest's temporary tree.
    evidence = os.environ.get("EVAL_AUTHOR_GYM_EVIDENCE")
    output = Path(evidence) / request.node.callspec.id if evidence else tmp_path / "controls"
    result = subprocess.run(
        [
            GYM_PYTHON,
            str(HARNESS_ROOT / "tests/fixtures/gym_ledger/smoke.py"),
            "--output",
            str(output),
            "--resources-app",
            str(resource_app),
            "--dataset",
            str(dataset),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["native_cli_rollouts"] == 4
    assert summary["verifier_cases"] == 6
    assert not summary["model_performance_measured"]
    quality = json.loads((output / "quality_summary.json").read_text())["run"]
    assert quality["ignored_checks"] == []
    assert quality["artifacts"]["records"] == 4
    # Scripted policies do not supply model captures; preserve the resulting unknown health.
    assert quality["verdicts"] == {"healthy": 0, "unhealthy": 0, "unobserved": 4}
    rows = [json.loads(line) for line in (output / "rollouts.jsonl").read_text().splitlines()]
    assert len(rows) == 8
    assert [row["reward"] for row in rows] == [1, 1, 1, 1, 0, 0, 0, 0]
    assert len({row["ng_trajectory"]["rollout_id"] for row in rows}) == 8

    # Validate the maintained adapter against actual native fixture, rollout and health formats.
    # These are scripted controls; unobserved model health must never become closure evidence.
    from test_gym_task_evidence import check_native_artifacts

    check_native_artifacts(output, draft)
