# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect fixed-fixture Harbor verifier controls, not model or skill measurements."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/verifier-cheats"
PANEL = {
    f"{grader}--{control}": int(grader == "vulnerable" or (grader == "sound" and control == "reference"))
    for grader in ("sound", "vulnerable", "always-fail")
    for control in ("reference", "early-exit", "fake-success")
}


def load_json(path):
    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    return json.loads(path.read_text(), parse_constant=reject_constant)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def native_check(job, name):
    grader, control = name.split("--")
    aggregate = load_json(job / "result.json")
    require(type(aggregate["n_total_trials"]) is int and aggregate["n_total_trials"] == 1, "incorrect total trials")
    expected_counts = {
        "n_completed_trials": 1,
        "n_errored_trials": 0,
        "n_pending_trials": 0,
        "n_running_trials": 0,
        "n_cancelled_trials": 0,
        "n_retries": 0,
    }
    require(
        all(
            type(aggregate["stats"][key]) is int and aggregate["stats"][key] == value
            for key, value in expected_counts.items()
        ),
        "incorrect native counts",
    )
    require(bool(aggregate["finished_at"]), "unfinished job")
    paths = list(job.glob("*/result.json"))
    require(len(paths) == 1, "missing or duplicate trial")
    trial_path = paths[0].parent
    trial = load_json(paths[0])
    require(trial["task_name"] == "normalizer" and trial["trial_name"] == trial_path.name, "wrong trial identity")
    require(trial["exception_info"] is None and bool(trial["finished_at"]), "failed or unfinished native trial")
    require(
        trial["agent_info"]["name"] == "verifier-cheat-control" and trial["agent_info"]["model_info"] is None,
        "unexpected agent",
    )
    for phase in ("agent_execution", "verifier"):
        require(bool(trial[phase]["started_at"]) and bool(trial[phase]["finished_at"]), f"missing {phase} timing")
    rewards = trial["verifier_result"]["rewards"]
    require(set(rewards) == {"reward"}, "unexpected reward keys")
    reward = rewards["reward"]
    require(type(reward) in (int, float) and math.isfinite(reward) and reward in (0, 1), "invalid reward")
    require(float((trial_path / "verifier/reward.txt").read_text()) == reward, "native reward disagreement")
    require((trial_path / "verifier/test-stdout.txt").is_file(), "missing verifier stdout")
    lock = load_json(trial_path / "lock.json")
    require(bool(lock["task"]["digest"]) and bool(trial["task_checksum"]), "missing task digest")
    agent = load_json(trial_path / "agent/control.json")
    require(
        agent["control"] == control and type(agent["return_code"]) is int and agent["return_code"] == 0,
        "control failed",
    )
    require(
        agent["uid"] == 65534 and agent["verifier_writable"] is False and agent["tests_writable"] is False,
        "verifier ownership violated",
    )
    checks = load_json(trial_path / "verifier/checks.json")
    require(checks["grader"] == grader and checks["uid"] == 0 and checks["reward"] == reward, "wrong verifier receipt")
    require(
        type(checks["cases"]) is int and checks["cases"] == 3 and checks["exit_codes"] == [0, 0, 0],
        "missing actual case execution",
    )
    require(
        type(checks["semantic_passes"]) is int and checks["semantic_passes"] == (3 if control == "reference" else 0),
        "unexpected semantic work",
    )
    return {
        "name": name,
        "reward": reward,
        "expected_reward": PANEL[name],
        "status": "passed" if reward == PANEL[name] else "failed",
        "trial_result": str(paths[0]),
        "task_digest": lock["task"]["digest"],
        "semantic_passes": checks["semantic_passes"],
    }


def summarize(output, exit_code):
    errors, checks = [], []
    jobs = output / "jobs"
    if {path.name for path in jobs.iterdir()} != set(PANEL) if jobs.is_dir() else True:
        errors.append("missing or unexpected jobs")
    for name in PANEL:
        try:
            checks.append(native_check(jobs / name, name))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{name}: {exc}")
    if exit_code != 0:
        errors.append(f"native execution failed: {exit_code}")
    passed = {check["name"] for check in checks if check["status"] == "passed"}
    metrics = {
        "native_trials": len(checks),
        "passed_checks": len(passed),
        "expected_checks": len(PANEL),
        "sound_reference_accepted": int("sound--reference" in passed),
        "sound_exploits_rejected": len(passed & {"sound--early-exit", "sound--fake-success"}),
        "vulnerable_exploits_rewarded": len(passed & {"vulnerable--early-exit", "vulnerable--fake-success"}),
        "always_fail_reference_rejected": int("always-fail--reference" in passed),
    }
    return {
        "status": "incomplete" if errors else "passed" if len(passed) == len(PANEL) else "failed",
        "metrics": metrics,
        "checks": checks,
        "errors": errors,
    }


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def execute(command, output, label, timeout=120, env=None):
    record = {
        "argv": command,
        "cwd": str(ROOT),
        "timeout_sec": timeout,
        "started_at": datetime.now(UTC).isoformat(),
        "return_code": None,
        "timed_out": False,
    }
    started = time.monotonic()
    with (output / f"{label}.log").open("w") as log:
        try:
            with subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            ) as process:
                try:
                    record["return_code"] = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    record["timed_out"] = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise
        finally:
            record["duration_sec"] = time.monotonic() - started
            save_json(output / f"{label}.command.json", record)
    if record["return_code"] != 0:
        raise RuntimeError(f"{label} exited {record['return_code']}; see {label}.log")
    return (output / f"{label}.log").read_text()


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = output / "verifier-cheat-summary.json"
    report = {
        "schema": "nemo.eval_author.verifier_cheat_evidence.v1",
        "protocol": "harbor-verifier-cheats-v1",
        "status": "incomplete",
        "model_performance_measured": False,
        "skill_authoring_measured": False,
        "scope": "fixed normalizer fixture; scripted policies; paired grader mutations",
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "runtime": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine()},
        "expected_rewards": PANEL,
        "execution_error": None,
    }
    save_json(summary, report)
    exit_code = 1
    try:
        report["source_revision"] = execute(["git", "rev-parse", "HEAD"], output, "revision", 10).strip()
        report["inputs_clean"] = not bool(execute(["git", "status", "--porcelain"], output, "status", 10).strip())
        files = [ROOT / "uv.lock", Path(__file__), ROOT / "tests/test_verifier_cheat_evidence.py"]
        files += sorted(path for path in FIXTURES.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        report["inputs"] = {str(path.relative_to(ROOT)): digest(path) for path in files}
        report["panel_digest"] = (
            "sha256:" + hashlib.sha256(json.dumps(report["inputs"], sort_keys=True).encode()).hexdigest()
        )
        harbor_version = importlib.metadata.version("harbor")
        require(harbor_version == "0.20.0", "requires locked Harbor 0.20.0")
        report["runtime"]["harbor"] = harbor_version
        report["runtime"]["docker"] = json.loads(
            execute(["docker", "version", "--format", "{{json .}}"], output, "docker-version", 30)
        )
        config = tomllib.loads((FIXTURES / "normalizer/task.toml").read_text())
        image = config["environment"]["docker_image"]
        execute(["docker", "pull", image], output, "image-pull", 180)
        report["runtime"]["image"] = json.loads(
            execute(["docker", "image", "inspect", image], output, "image-inspect", 30)
        )
        shutil.copytree(FIXTURES, output / "panel", ignore=shutil.ignore_patterns("__pycache__"))
        env = dict(os.environ, PYTHONPATH=str(output / "panel"), PYTHONDONTWRITEBYTECODE="1")
        harbor = str(Path(sys.executable).parent / "harbor")
        failures = []
        for name in PANEL:
            grader, control = name.split("--")
            command = [
                harbor,
                "run",
                "--path",
                str(output / "panel/normalizer"),
                "--agent",
                "control_agent:ControlAgent",
                "--agent-kwarg",
                f"control={control}",
                "--verifier-env",
                f"GRADER={grader}",
                "--env",
                "docker",
                "--delete",
                "--n-concurrent",
                "1",
                "--n-attempts",
                "1",
                "--max-retries",
                "0",
                "--jobs-dir",
                str(output / "jobs"),
                "--job-name",
                name,
                "--quiet",
            ]
            try:
                execute(command, output, name, timeout=90, env=env)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                failures.append(str(exc))
        require(not failures, "; ".join(failures))
        exit_code = 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        importlib.metadata.PackageNotFoundError,
    ) as exc:
        report["execution_error"] = f"{type(exc).__name__}: {exc}"
    report.update(summarize(output, exit_code))
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["artifacts"] = {
        str(path.relative_to(output)): digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path != summary
    }
    save_json(summary, report)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for exact native evidence")
    args = parser.parse_args()
    try:
        code = run(args.output)
    except FileExistsError:
        parser.error("output directory already exists; use a fresh path")
    print(json.dumps({"summary": str(args.output.resolve() / "verifier-cheat-summary.json"), "exit_code": code}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
