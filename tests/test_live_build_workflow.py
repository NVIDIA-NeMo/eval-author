# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())


def test_build_once_and_download_before_each_credential_bearing_shard():
    jobs = workflow()["jobs"]
    assert "build-opencode" in jobs, "upstream per-platform build missing"
    build = jobs["build-opencode"]
    assert build["strategy"]["matrix"]["platform"] == ["linux/amd64"]
    assert "environment" not in build and "secrets." not in json.dumps(build)
    commands = [s for s in build["steps"] if "build_patched_opencode.py" in s.get("run", "")]
    assert len(commands) == 1 and "--artifact-output" in commands[0]["run"]
    evaluate = jobs["evaluate"]
    assert "build-opencode" in evaluate["needs"]
    assert "always()" in evaluate["if"] and "needs.authorize.result == 'success'" in evaluate["if"]
    assert "inputs.tier == '2'" in evaluate["if"]  # A skipped build cannot block Tier 2.
    steps = evaluate["steps"]
    downloads = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]
    assert len(downloads) == 1
    assert downloads[0]["if"] == "matrix.check == 'live' && matrix.id != 'none'"
    relocate = next(s for s in steps if "--relocate-manifest" in s.get("run", ""))
    prepare = next(s for s in steps if "prepare_live_images.py" in s.get("run", ""))
    live = next(s for s in steps if "collect_live_skill_evaluations.py" in s.get("run", ""))
    assert steps.index(downloads[0]) < steps.index(relocate) < steps.index(prepare) < steps.index(live)
    assert "--binary" in relocate["run"]
    assert all("--relocate-manifest" in s["run"] for s in steps if "build_patched_opencode.py" in s.get("run", ""))


def test_keyless_pr_timing_uses_real_audit_production_preparation():
    jobs = workflow()["jobs"]
    assert "preparation-timing" in jobs, "credential-free hosted timing job missing"
    timing = jobs["preparation-timing"]
    assert timing["needs"] == ["plan", "build-opencode"]
    assert "pull_request" in timing["if"] and "!inputs.run_live" in timing["if"]
    assert timing["runs-on"] == "ubuntu-24.04"
    assert "environment" not in timing and "secrets." not in json.dumps(timing)
    assert "authorize" not in timing["needs"]
    text = json.dumps(timing)
    assert "skills/eval-author-audit" in text
    assert "collect_live_skill_evaluations.py" not in text
    assert "--relocate-manifest" in text and "prepare_live_images.py" in text
    live_steps = jobs["evaluate"]["steps"]
    for name in (
        "Install verified Docker Compose runtime",
        "Install isolated evaluator runtime",
        "Verify downloaded OpenCode runtime",
    ):
        actual = next(s for s in timing["steps"] if s.get("name") == name)
        production = next(s for s in live_steps if s.get("name") == name)
        assert actual["run"] == production["run"]
    assert timing["env"]["EVALUATOR_EXTRAS"] == "tier3"
    assert timing["env"]["SHARD_SKILL"] == "eval-author-audit"


def test_outer_budget_covers_declared_steps_without_raising_native_caps():
    jobs = workflow()["jobs"]
    evaluate = jobs["evaluate"]
    assert evaluate["timeout-minutes"] == 75
    assert sum(s["timeout-minutes"] for s in evaluate["steps"]) == evaluate["timeout-minutes"]
    initialize = next(s for s in evaluate["steps"] if "live_ci.py initialize" in s.get("run", ""))
    assert "time.monotonic() + 4200" in initialize["run"]
    assert evaluate["steps"].index(initialize) == 1  # No hidden setup before T0.
    reserve = 2700 + 60
    assert 4200 - reserve == 1440
    prepare = next(s for s in evaluate["steps"] if "prepare_live_images.py" in s.get("run", ""))
    assert prepare["timeout-minutes"] == 10
    before_prepare = evaluate["steps"][1 : evaluate["steps"].index(prepare) + 1]
    assert sum(s["timeout-minutes"] for s in before_prepare) * 60 <= 4200 - reserve
    runtime = (ROOT / "tools/patched_opencode_runtime.py").read_text()
    for name, value in {
        "LIVE_EXECUTION_SECONDS": 2700,
        "LIVE_HANDOFF_SECONDS": 60,
        "PREPARATION_TIMEOUT_SECONDS": 600,
    }.items():
        assert re.search(rf"^{name} = {value}$", runtime, re.M)


def test_artifacts_never_upload_raw_logs_or_native_payloads_even_private():
    jobs = workflow()["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if not step.get("uses", "").startswith("actions/upload-artifact@"):
                continue
            paths = step["with"]["path"]
            assert "-reports/" not in paths and "build.log" not in paths
            assert "private" not in step.get("if", "")
            assert not paths.rstrip().endswith("/")
            assert all("*" not in p or p.endswith("live-skillevaluator-summary.*") for p in paths.splitlines())
    uploads = [s for s in jobs["build-opencode"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 2
    assert any(s["if"] == "always()" and s["with"]["path"].endswith("build-summary.json") for s in uploads)
    binary = next(s for s in uploads if s["if"] == "success()")
    assert {Path(p).name for p in binary["with"]["path"].splitlines()} == {"opencode", "binary-manifest.json"}


def condition(job, context):
    expression = job.get("if", "True")
    if "always()" not in expression:
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        if any(context.get(f"needs.{name}.result") != "success" for name in needs):
            return False
    expression = expression.replace("always()", "True").replace("cancelled()", "False")
    expression = re.sub(
        r"\b(?:github|inputs|vars|needs)\.[A-Za-z0-9_.-]+", lambda m: repr(context.get(m[0], "")), expression
    )
    expression = re.sub(r"!(?!=)", " not ", expression.replace("&&", " and ").replace("||", " or "))
    return bool(eval(expression, {"__builtins__": {}}))


@pytest.mark.parametrize(
    "event,branch,run_live,tier,enabled,build,expected",
    [
        ("pull_request", "refs/pull/172/merge", False, "", True, "success", (True, True, False, False)),
        ("push", "refs/heads/main", False, "", True, "success", (True, False, True, True)),
        ("push", "refs/heads/main", False, "", False, "skipped", (False, False, False, False)),
        ("workflow_dispatch", "refs/heads/feature", False, "both", True, "success", (True, True, False, False)),
        ("workflow_dispatch", "refs/heads/feature", True, "both", True, "skipped", (False, False, False, False)),
        ("workflow_dispatch", "refs/heads/main", True, "2", True, "skipped", (False, False, True, True)),
        ("workflow_dispatch", "refs/heads/main", True, "3", True, "failure", (True, False, True, False)),
    ],
)
def test_event_gates_and_skipped_build_dependencies(event, branch, run_live, tier, enabled, build, expected):
    jobs = workflow()["jobs"]
    context = {
        "github.event_name": event,
        "github.ref": branch,
        "inputs.run_live": run_live,
        "inputs.tier": tier,
        "vars.SKILL_EVALUATION_LIVE_ENABLED": str(enabled).lower(),
        "needs.plan.result": "success",
        "needs.build-opencode.result": build,
    }
    authorized = condition(jobs["authorize"], context)
    context["needs.authorize.result"] = "success" if authorized else "skipped"
    for model in ("chat", "embedding", "agent"):
        context[f"needs.authorize.outputs.{model}"] = "configured" if authorized else ""
    assert (
        tuple(
            condition(jobs[name], context) for name in ("build-opencode", "preparation-timing", "authorize", "evaluate")
        )
        == expected
    )


@pytest.mark.parametrize(
    "outcome,expected", [("success", "complete"), ("failure", "incomplete"), ("PRIVATE task text", "incomplete")]
)
def test_actual_timing_summary_script_projects_only_typed_aggregates(tmp_path, outcome, expected):
    job = workflow()["jobs"]["preparation-timing"]
    step = next(s for s in job["steps"] if s.get("name", "").startswith("Summarize hosted"))
    script = step["run"].split("python - <<'PY'\n")[1].rsplit("PY", 1)[0]
    env = {
        "PATH": os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_SHA": "a" * 40,
        "EA_PREPARATION_STARTED": str(time.monotonic()),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update({name: "success" for name in step["env"]})
    env["PREPARE_OUTCOME"] = outcome
    import hashlib

    binary = tmp_path / "opencode"
    binary.write_text("synthetic contract fixture, not a measured build")
    binary.chmod(0o755)
    manifest = tmp_path / "patched-opencode/binary-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "binary_path": str(binary),
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "expected_version": "1.19.5+nodesink.e991596fecc9",
            }
        )
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    summary = json.loads((tmp_path / "preparation-timing-summary.json").read_text())
    assert summary["status"] == expected
    assert result.returncode == (0 if expected == "complete" else 1)
    assert set(summary) == {
        "schema_version",
        "status",
        "platform",
        "skill",
        "source_revision",
        "dataset_sha256",
        "runtime_sha256",
        "setup_and_preparation_seconds",
        "setup_and_preparation_budget_seconds",
        "image_preparation_cap_seconds",
        "outcomes",
        "model_execution_enabled",
        "binary_sha256",
    }
    assert type(summary["setup_and_preparation_seconds"]) is float
    assert summary["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert summary["model_execution_enabled"] is False
    assert "PRIVATE" not in json.dumps(summary) + result.stdout + result.stderr
    assert sum(s["timeout-minutes"] for s in job["steps"]) == job["timeout-minutes"]
