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
        "${{ runner.temp }}/tooling-build-diagnostics.json",
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


def test_preparation_budget_retains_upload_headroom_with_shared_setup_deadline():
    sys.path.insert(0, str(ROOT / "tools"))
    import live_shard

    job = workflow()["jobs"]["preparation-timing"]
    steps = job["steps"]
    clock = next(s for s in steps if "EA_LIVE_JOB_DEADLINE=" in s.get("run", ""))
    prepare = next(s for s in steps if "live_shard.py prepare" in s.get("run", ""))
    projection = next(s for s in steps if "tooling_build_diagnostics.py" in s.get("run", ""))
    assert steps.index(clock) == 1 and "started + LIVE_JOB_SECONDS" in clock["run"]
    setup = steps[steps.index(clock) : steps.index(prepare) + 1]
    assert projection in setup
    effective_setup = min(sum(s["timeout-minutes"] for s in setup) * 60, live_shard.SETUP_SECONDS)
    assert effective_setup == live_shard.LIVE_JOB_SECONDS - live_shard.RUN_PHASE_SECONDS == 1620
    outside = [steps[0], *steps[steps.index(prepare) + 1 :]]
    assert [s["timeout-minutes"] for s in outside] == [2, 1, 2]
    assert outside[-1]["uses"].startswith("actions/upload-artifact@")
    assert effective_setup + sum(s["timeout-minutes"] for s in outside) * 60 == 32 * 60
    assert 32 * 60 < job["timeout-minutes"] * 60 == 33 * 60


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

    env = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_SHA": "a" * 40,
        "EA_PREPARATION_STARTED": "1000.0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEST_CLOSE": "success",
        "TEST_NOW": str(1000 + elapsed),
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
    summary_shell_fixture(tmp_path)
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=ROOT, env=env, capture_output=True, text=True
    )
    summary = json.loads((tmp_path / "preparation-timing-summary.json").read_text())
    assert summary["status"] == expected
    assert (tmp_path / "preparation-timing-summary.json").stat().st_mode & 0o777 == 0o644
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


def summary_shell_fixture(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    from patched_opencode_runtime import PreparationTiming

    state = tmp_path / "live-shard/control/boundary.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}")
    checkpoint = tmp_path / "live-shard/workspace/preparation-operation-timing.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    PreparationTiming(checkpoint).emit("started")
    shim = tmp_path / "sudo"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os,runpy,sys,time\n"
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
        "import live_shard, patched_opencode_runtime as runtime\n"
        "time.monotonic = lambda: float(os.environ['TEST_NOW'])\n"
        "def close(state):\n"
        "    mode = os.environ['TEST_CLOSE']\n"
        "    if mode == 'failure': raise RuntimeError('PRIVATE close error')\n"
        "    if mode == 'timeout':\n"
        "        from pathlib import Path\n"
        "        Path(os.environ['RUNNER_TEMP'], 'close-entered').touch()\n"
        "        time.sleep(30)\n"
        "live_shard.close_boundary = close\n"
        "original = runtime.load_manifest\n"
        "def manifest(path):\n"
        "    assert os.environ.get('CLOSE_OUTCOME') == 'success', 'workspace read before closure'\n"
        "    return original(path)\n"
        "runtime.load_manifest = manifest\n"
        "args = sys.argv[1:]\n"
        "if args[0].startswith('--preserve-env='):\n"
        "    preserved = args.pop(0).split('=',1)[1].split(',')\n"
        "else: preserved = []\n"
        "for key in ('CLOSE_OUTCOME',):\n"
        "    if key not in preserved: os.environ.pop(key, None)\n"
        "assert args.pop(0) == 'python3'\n"
        "if args == ['-']: exec(compile(sys.stdin.read(), '<workflow>', 'exec'))\n"
        "else:\n"
        "    assert args[0] == 'tools/live_shard.py' and args[1] == 'close'\n"
        "    sys.argv = args\n"
        "    raise SystemExit(live_shard.main())\n"
    )
    shim.chmod(0o700)


def test_evaluate_shell_never_stages_after_close_failure(tmp_path):
    step = next(s for s in workflow()["jobs"]["evaluate"]["steps"] if "live_ci.py stage" in s.get("run", ""))
    summary_shell_fixture(tmp_path)
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "TEST_NOW": "1000",
        "TEST_CLOSE": "failure",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert result.stdout == "live shard phase failed; private evidence retained\n"
    assert result.stderr == ""
    assert not (tmp_path / "skill-publish").exists()


@pytest.mark.parametrize("job_name", ["preparation-timing", "evaluate"])
def test_build_diagnostics_projection_is_always_independent_of_fatal_close(tmp_path, job_name):
    steps = workflow()["jobs"][job_name]["steps"]
    projections = [s for s in steps if "tooling_build_diagnostics.py" in s.get("run", "")]
    assert len(projections) == 1, "missing always-run safe build projection"
    projection = projections[0]
    close = next(s for s in steps if "live_shard.py close " in s.get("run", ""))
    assert steps.index(projection) < steps.index(close)
    assert "always()" in projection["if"] and "always()" in close["if"]
    assert "secrets." not in json.dumps(projection)
    assert '"$RUNNER_TEMP/live-shard/control/image/build-diagnostics.json"' in projection["run"]
    upload = next(s for s in steps if "tooling-build-diagnostics.json" in s.get("with", {}).get("path", ""))
    assert "always()" in upload["if"]
    public_path = (
        "skill-publish/tooling-build-diagnostics.json" if job_name == "evaluate" else "tooling-build-diagnostics.json"
    )
    assert "${{ runner.temp }}/" + public_path in upload["with"]["path"].splitlines()
    # Execute the YAML body, substituting only sudo/python; source stays absent.
    shim = tmp_path / "sudo"
    shim.write_text(f'#!/bin/sh\nshift\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", projection["run"]],
        cwd=ROOT,
        env={
            "PATH": str(tmp_path) + os.pathsep + os.defpath,
            "RUNNER_TEMP": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads((tmp_path / public_path).read_text())["failure_category"] == "checkpoint_unavailable"


@pytest.mark.parametrize(
    "failure",
    ["failure", "malformed", "missing_state", "missing_checkpoint", "missing_clock", "invalid_clock", "timeout"],
)
def test_full_summary_shell_retains_safe_incomplete_fallback(tmp_path, failure):
    import hashlib
    import signal
    import time

    step = next(
        s for s in workflow()["jobs"]["preparation-timing"]["steps"] if s.get("name", "").startswith("Summarize hosted")
    )
    summary_shell_fixture(tmp_path)
    binary = tmp_path / "binary"
    binary.write_bytes(b"synthetic, never executed")
    binary.chmod(0o755)
    manifest = tmp_path / "live-shard/workspace/patched-opencode/binary-manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            dict(
                schema_version=1,
                binary_path=str(binary),
                sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                expected_version="1.19.5+nodesink.e991596fecc9",
            )
        )
    )
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_SHA": "a" * 40,
        "EA_PREPARATION_STARTED": "1000",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEST_NOW": "1001",
        "TEST_CLOSE": failure if failure in ("failure", "timeout") else "success",
        **{name: "success" for name in step["env"]},
    }
    checkpoint = tmp_path / "live-shard/workspace/preparation-operation-timing.json"
    if failure == "malformed":
        checkpoint.write_text('{"PRIVATE": "secret"}')
    elif failure == "missing_state":
        (tmp_path / "live-shard/control/boundary.json").unlink()
    elif failure == "missing_checkpoint":
        checkpoint.unlink()
    elif failure == "missing_clock":
        env.pop("EA_PREPARATION_STARTED")
    elif failure == "invalid_clock":
        env["EA_PREPARATION_STARTED"] = "PRIVATE invalid clock"
    process = subprocess.Popen(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        if failure == "timeout":
            until = time.monotonic() + 5
            while not (tmp_path / "close-entered").exists() and process.poll() is None and time.monotonic() < until:
                time.sleep(0.01)
            assert (tmp_path / "close-entered").exists()
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    assert process.returncode != 0
    summary = json.loads((tmp_path / "preparation-timing-summary.json").read_text())
    assert summary["status"] == "incomplete"
    assert summary["binary_sha256"] == (
        hashlib.sha256(binary.read_bytes()).hexdigest() if failure in ("missing_clock", "invalid_clock") else None
    )
    assert summary["outcomes"]["close"] == (
        "failure" if failure in ("failure", "malformed") else "unknown" if failure == "timeout" else "success"
    )
    assert (tmp_path / "preparation-timing-summary.json").stat().st_mode & 0o777 == 0o644
    assert "PRIVATE" not in json.dumps(summary) + stdout + stderr
    if failure in ("failure", "malformed", "missing_state", "missing_checkpoint", "timeout"):
        assert not (tmp_path / "preparation-operation-timing.json").exists()
