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
import re
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
    f"{grader}--{control}": int(
        (grader == "vulnerable" and control != "hang") or (grader == "sound" and control == "reference")
    )
    for grader in ("sound", "vulnerable", "always-fail")
    for control in ("reference", "early-exit", "fake-success", "hidden-case-read", "hang", "boolean-output")
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
        agent["uid"] == 65534
        and agent["verifier_writable"] is False
        and agent["tests_writable"] is False
        and agent["tests_readable"] is False,
        "verifier ownership violated",
    )
    checks = load_json(trial_path / "verifier/checks.json")
    require(checks["grader"] == grader and checks["uid"] == 0 and checks["reward"] == reward, "wrong verifier receipt")
    require(
        type(checks["cases"]) is int and checks["cases"] == 3,
        "missing actual case execution",
    )
    require(
        type(checks["semantic_passes"]) is int
        and checks["semantic_passes"] == (3 if control == "reference" else 1 if control == "boolean-output" else 0),
        "unexpected semantic work",
    )
    require(checks["tests_uid"] == 0 and checks["tests_mode"] == 0o700, "hidden cases not private")
    expected_codes = [None if control == "hang" else 0] * 3
    require(checks["exit_codes"] == expected_codes, "unexpected submission exit codes")
    require(
        type(checks["timeouts"]) is int and checks["timeouts"] == (3 if control == "hang" else 0), "unexpected timeouts"
    )
    rows = checks["rows"]
    require(len(rows) == 3, "missing case rows")
    for index, row in enumerate(rows):
        require(row["timed_out"] is (control == "hang"), "incorrect timeout evidence")
        require(
            row["return_code"] is None
            if control == "hang"
            else type(row["return_code"]) is int and row["return_code"] == 0,
            "incorrect exit evidence",
        )
        require(
            row["semantic_pass"] is (control == "reference" or (control == "boolean-output" and index == 1)),
            "incorrect case semantics",
        )
        if control == "hidden-case-read":
            require(row["stderr"] == "hidden-cases-blocked\n", "hidden case access not denied")
    return {
        "name": name,
        "reward": reward,
        "expected_reward": PANEL[name],
        "status": "passed" if reward == PANEL[name] else "failed",
        "trial_result": str(paths[0]),
        "task_digest": lock["task"]["digest"],
        "semantic_passes": checks["semantic_passes"],
        "timeouts": checks["timeouts"],
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
        "sound_exploits_rejected": sum(
            name in passed for name in PANEL if name.startswith("sound--") and name != "sound--reference"
        ),
        "vulnerable_exploits_rewarded": sum(
            name in passed
            for name in PANEL
            if name.startswith("vulnerable--") and name != "vulnerable--reference" and PANEL[name] == 1
        ),
        "submission_timeouts_rejected": sum(name in passed for name in PANEL if name.endswith("--hang")),
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


def public_summary(report):
    def token(value, pattern):
        return value if isinstance(value, str) and re.fullmatch(pattern, value) else None

    runtime = report.get("runtime", {})
    image = runtime.get("image", [{}])
    metrics = report.get("metrics", {})
    public = {
        "schema": "nemo.eval_author.verifier_cheat_evidence.v2",
        "protocol": "harbor-verifier-cheats-v2",
        "status": report["status"] if report["status"] in ("passed", "failed", "incomplete") else "incomplete",
        "model_performance_measured": False,
        "skill_authoring_measured": False,
        "scope": "fixed normalizer fixture; scripted policies; paired grader mutations",
        "source_revision": token(report.get("source_revision"), r"[a-f0-9]{40}"),
        "inputs_clean": report.get("inputs_clean") is True,
        "panel_digest": token(report.get("panel_digest"), r"sha256:[a-f0-9]{64}"),
        "expected_rewards": PANEL,
        "execution_failed": report.get("execution_error") is not None,
        "error_count": len(report.get("errors", [])),
        "runtime": {
            "python": token(runtime.get("python"), r"\d+\.\d+\.\d+"),
            "harbor": token(runtime.get("harbor"), r"\d+\.\d+\.\d+"),
            "docker": token(runtime.get("docker", {}).get("Server", {}).get("Version"), r"\d+\.\d+\.\d+"),
            "image_id": token(image[0].get("Id") if image else None, r"sha256:[a-f0-9]{64}"),
        },
        "metrics": {
            key: metrics[key]
            for key in (
                "native_trials",
                "passed_checks",
                "expected_checks",
                "sound_reference_accepted",
                "sound_exploits_rejected",
                "vulnerable_exploits_rewarded",
                "submission_timeouts_rejected",
                "always_fail_reference_rejected",
            )
            if type(metrics.get(key)) is int and 0 <= metrics[key] <= len(PANEL)
        },
        "checks": [],
    }
    for check in report.get("checks", []):
        if check.get("name") not in PANEL or check.get("status") not in ("passed", "failed"):
            continue
        safe = {"name": check["name"], "status": check["status"], "expected_reward": PANEL[check["name"]]}
        for key, maximum in (("reward", 1), ("semantic_passes", 3), ("timeouts", 3)):
            value = check.get(key)
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum and int(value) == value:
                safe[key] = int(value)
        public["checks"].append(safe)
    return public


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
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    summary = output / "verifier-cheat-summary.json"
    private = output / "verifier-cheat-private.json"
    report = {
        "schema": "nemo.eval_author.verifier_cheat_evidence.v2",
        "protocol": "harbor-verifier-cheats-v2",
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
    save_json(summary, public_summary(report))
    save_json(private, report)
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
        if path.is_file() and path not in (summary, private)
    }
    save_json(private, report)
    save_json(summary, public_summary(report))
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
