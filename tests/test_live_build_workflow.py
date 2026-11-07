# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import subprocess
import sys
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
    relocate = next(s for s in steps if "live_shard.py relocate" in s.get("run", ""))
    prepare = next(s for s in steps if "live_shard.py prepare" in s.get("run", ""))
    live = next(s for s in steps if "live_shard.py run " in s.get("run", ""))
    assert steps.index(downloads[0]) < steps.index(relocate) < steps.index(prepare) < steps.index(live)
    assert '--download "$RUNNER_TEMP/downloaded-opencode"' in relocate["run"]
    assert not any("build_patched_opencode.py" in s.get("run", "") for s in steps)


@pytest.mark.parametrize("job_name", ["preparation-timing", "evaluate"])
def test_download_finishes_before_any_idle_boundary_lease(job_name):
    steps = workflow()["jobs"][job_name]["steps"]
    download = next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))
    create = next(s for s in steps if "live_shard.py create " in s.get("run", ""))
    clock = next(s for s in steps if "EA_LIVE_JOB_DEADLINE=" in s.get("run", ""))
    assert steps.index(clock) < steps.index(download) < steps.index(create)
    assert download["timeout-minutes"] == 2
    assert download["with"]["name"] == "patched-opencode-linux-amd64-${{ github.run_id }}-${{ github.run_attempt }}"


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
    assert "live_shard.py run " not in text
    assert "live_shard.py relocate" in text and "live_shard.py prepare" in text
    live_steps = jobs["evaluate"]["steps"]
    for name in (
        "Create disposable shard boundary and pinned execution image",
        "Install isolated evaluator runtime",
        "Verify downloaded OpenCode runtime",
    ):
        actual = next(s for s in timing["steps"] if s.get("name") == name)
        production = next(s for s in live_steps if s.get("name") == name)
        assert actual["run"] == production["run"]
    assert timing["env"]["SHARD_TIER"] == "3"
    assert timing["env"]["SHARD_CHECK"] == "live"
    assert timing["env"]["SHARD_SKILL"] == "eval-author-audit"


def test_diagnostic_checkpoint_is_opt_in_and_always_retained_without_private_output():
    jobs = workflow()["jobs"]
    timing = jobs["preparation-timing"]
    prepare = next(step for step in timing["steps"] if step.get("id") == "prepare")
    assert '--timing-checkpoint "$RUNNER_TEMP/preparation-operation-timing.json"' in prepare["run"]
    production = next(step for step in jobs["evaluate"]["steps"] if "live_shard.py prepare" in step.get("run", ""))
    assert "--timing-checkpoint" not in production["run"]
    uploads = [step for step in timing["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    assert set(uploads[0]["with"]["path"].splitlines()) == {
        "${{ runner.temp }}/preparation-timing-summary.json",
        "${{ runner.temp }}/preparation-operation-timing.json",
    }
    assert prepare["timeout-minutes"] == 15
    assert timing["timeout-minutes"] == 33
    assert jobs["evaluate"]["timeout-minutes"] == 80


def test_outer_budget_covers_declared_steps_without_raising_native_caps():
    sys.path.insert(0, str(ROOT / "tools"))
    import live_shard

    jobs = workflow()["jobs"]
    evaluate = jobs["evaluate"]
    assert evaluate["timeout-minutes"] == 80
    initialize = next(s for s in evaluate["steps"] if "live_ci.py initialize" in s.get("run", ""))
    assert "started + LIVE_JOB_SECONDS" in initialize["run"]
    assert evaluate["steps"].index(initialize) == 1  # No hidden setup before T0.
    assert live_shard.LIVE_JOB_SECONDS == 4500
    reserve = live_shard.RUN_PHASE_SECONDS
    assert reserve == 2700 + 60 + 60 + 60
    assert live_shard.SETUP_SECONDS == 4500 - reserve == 1620
    prepare = next(s for s in evaluate["steps"] if "live_shard.py prepare" in s.get("run", ""))
    run = next(s for s in evaluate["steps"] if "live_shard.py run " in s.get("run", ""))
    assert run["timeout-minutes"] * 60 == reserve
    assert prepare["timeout-minutes"] == 15
    before_prepare = evaluate["steps"][1 : evaluate["steps"].index(prepare) + 1]
    # Per-step maxima overlap: the shared setup deadline is stricter than their sum.
    effective_setup = min(sum(s["timeout-minutes"] for s in before_prepare) * 60, live_shard.SETUP_SECONDS)
    outside = [evaluate["steps"][0], *evaluate["steps"][evaluate["steps"].index(run) + 1 :]]
    assert effective_setup + reserve + sum(s["timeout-minutes"] for s in outside) * 60 == 80 * 60
    runtime = (ROOT / "tools/patched_opencode_runtime.py").read_text()
    for name, value in {
        "LIVE_EXECUTION_SECONDS": 2700,
        "LIVE_HANDOFF_SECONDS": 60,
        "PREPARATION_TIMEOUT_SECONDS": 900,
    }.items():
        assert re.search(rf"^{name} = {value}$", runtime, re.M)


def test_production_and_pr_share_contained_phase_runner_and_close_before_publish():
    jobs = workflow()["jobs"]
    for job_name in ("evaluate", "preparation-timing"):
        steps = jobs[job_name]["steps"]
        phases = {
            phase: next(s for s in steps if f"tools/live_shard.py {phase} " in s.get("run", ""))
            for phase in ("create", "runtime", "relocate", "prepare", "close")
        }
        assert [phases[p]["timeout-minutes"] for p in ("create", "runtime", "relocate", "prepare")] == [4, 5, 1, 15]
        assert [steps.index(phases[p]) for p in ("create", "runtime", "relocate", "prepare", "close")] == sorted(
            steps.index(s) for s in phases.values()
        )
        assert "always()" in phases["close"]["if"]
        assert all(
            "secrets." not in json.dumps(phases[p]) for p in ("create", "runtime", "relocate", "prepare", "close")
        )
        assert all("sudo" in phases[p]["run"] for p in phases)
    stage = next(s for s in jobs["evaluate"]["steps"] if "live_ci.py stage" in s.get("run", ""))
    assert stage["run"].index("live_shard.py close") < stage["run"].index("live_ci.py stage")
    assert '"$RUNNER_TEMP/live-shard/report"' in stage["run"]


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
    "outcome,elapsed,expected",
    [
        ("success", 0, "complete"),
        ("failure", 0, "incomplete"),
        ("PRIVATE task text", 0, "incomplete"),
        ("success", 1619.999, "complete"),
        ("success", 1620, "incomplete"),
        ("success", 1740, "incomplete"),
    ],
)
def test_actual_timing_summary_script_projects_only_typed_aggregates(tmp_path, outcome, elapsed, expected):
    job = workflow()["jobs"]["preparation-timing"]
    step = next(s for s in job["steps"] if s.get("name", "").startswith("Summarize hosted"))
    script = step["run"].split("python3 - <<'PY'\n")[1].rsplit("PY", 1)[0]
    env = {
        "PATH": os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_SHA": "a" * 40,
        "EA_PREPARATION_STARTED": "1000.0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update({name: "success" for name in step["env"]})
    env["PREPARE_OUTCOME"] = outcome
    import hashlib

    binary = tmp_path / "opencode"
    binary.write_text("synthetic contract fixture, not a measured build")
    binary.chmod(0o755)
    manifest = tmp_path / "live-shard/workspace/patched-opencode/binary-manifest.json"
    manifest.parent.mkdir(parents=True)
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
    script = f"import time; time.monotonic = lambda: {1000 + elapsed!r}\n" + script
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
    assert summary["setup_and_preparation_budget_seconds"] == 1620
    assert summary["binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert summary["model_execution_enabled"] is False
    assert "PRIVATE" not in json.dumps(summary) + result.stdout + result.stderr
    assert sum(s["timeout-minutes"] for s in job["steps"]) == job["timeout-minutes"]
