# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the credential-free native Gym panel and retain fail-closed CI evidence."""

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nemo.eval_author.gym_evidence.v1"
PROTOCOL = "gym-ledger-controls-v1"
ROUTES = ("audit", "trace")
NATIVE_TESTS = ("test_native_scaffold_and_validation", "test_native_http_execution_controls")
# Preserve the audit protocol's published check names for existing consumers.
TESTS = ("test_native_proposal_scaffold_and_validation", "test_native_http_execution_controls")
EXPECTED = {
    "controls": 8,
    "native_cli_rollouts": 4,
    "verifier_cases": 6,
    "reference_passes": 4,
    "negative_zero_rewards": 4,
}


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def git(*args, root=ROOT):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def check_local_http():
    """Detect sandbox infrastructure restrictions before interpreting test failures."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))


def checks_for(route):
    return TESTS if route == "audit" else NATIVE_TESTS


def test_results(path, route="audit"):
    """Never count skipped, missing, duplicated or unexpected tests as passes."""
    rows = {name: "missing" for name in checks_for(route)}
    names_to_checks = {f"{name}[{route}]": check for name, check in zip(NATIVE_TESTS, checks_for(route), strict=True)}
    try:
        nodes = ET.parse(path).findall(".//testcase")
        names = [node.get("name", "") for node in nodes]
        if sorted(names) != sorted(names_to_checks):
            return rows
        for node in nodes:
            name = names_to_checks[node.get("name", "")]
            rows[name] = next(
                (
                    state
                    for tag, state in (("error", "error"), ("failure", "failed"), ("skipped", "skipped"))
                    if node.find(tag) is not None
                ),
                "passed",
            )
    except (OSError, ET.ParseError, TypeError):
        pass
    return rows


def summarize(output, exit_code, route="audit"):
    checks = test_results(output / "junit.xml", route)
    status = "failed" if "failed" in checks.values() else "incomplete"
    metrics, health = None, None
    if checks[TESTS[1]] == "passed":
        try:
            native = json.loads((output / "native" / route / "summary.json").read_text())
            quality = json.loads((output / "native" / route / "quality_summary.json").read_text())["run"]
            if native["model_performance_measured"] is not False:
                raise ValueError("unexpected live measurement")
            metrics = {key: native[key] for key in EXPECTED}
            if any(type(value) is not int or value != EXPECTED[key] for key, value in metrics.items()):
                raise ValueError("unexpected panel accounting")
            health = quality["verdicts"]
            if (
                health != {"healthy": 0, "unhealthy": 0, "unobserved": 4}
                or quality["ignored_checks"] != []
                or quality["artifacts"]["records"] != 4
            ):
                raise ValueError("unexpected rollout health")
            if exit_code == 0 and all(state == "passed" for state in checks.values()):
                status = "passed"
        except (OSError, ValueError, KeyError, TypeError):
            metrics, health = None, None
    return {"status": status, "checks": checks, "metrics": metrics, "rollout_health": health}


def run(output, gym_python, gym_root, gym_revision, timeout, source_root=ROOT, route="audit"):
    if route not in ROUTES:
        raise ValueError("unsupported source route")
    output.mkdir(parents=True, exist_ok=False)
    files = ["tests/test_gym_native.py", "tests/test_gym_task_evidence.py", "tools/collect_gym_evidence.py", "uv.lock"]
    files += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests/fixtures/gym_ledger").glob("*")) if p.is_file()]
    inputs = {name: digest((ROOT / name).read_bytes()) for name in files}
    report = {
        "schema": SCHEMA,
        "protocol": PROTOCOL if route == "audit" else f"gym-{route}-ledger-controls-v1",
        "source_route": route,
        "repository": "NVIDIA-NeMo/eval-author",
        "source_revision": git("rev-parse", "HEAD", root=source_root),
        "inputs_clean": not bool(git("status", "--porcelain", root=source_root)),
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "ci": {key: os.environ.get(key) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME")},
        "panel_digest": digest(json.dumps(inputs, sort_keys=True).encode()),
        "inputs": inputs,
        "runtime": {
            "expected_gym_revision": gym_revision,
            "gym_revision": None,
            "gym_clean": None,
            "python": None,
            "gym_version": None,
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "model_performance_measured": False,
        "exit_code": None,
        "reason": "execution_incomplete",
        "status": "incomplete",
        "checks": dict.fromkeys(checks_for(route), "missing"),
        "metrics": None,
        "rollout_health": None,
        "artifacts": {},
    }
    summary = output / "gym-summary.json"
    summary.write_text(json.dumps(report, indent=2) + "\n")
    try:
        check_local_http()
        runtime = json.loads(
            subprocess.check_output(
                [
                    gym_python,
                    "-c",
                    "import json,platform,importlib.metadata,nemo_gym; print(json.dumps(dict("
                    "python=platform.python_version(),gym_version=importlib.metadata.version('nemo-gym'),"
                    "module_path=nemo_gym.__file__)))",
                ],
                text=True,
                timeout=30,
            )
        )
        module_path = Path(runtime.pop("module_path")).resolve()
        report["runtime"].update(runtime)
        report["runtime"]["gym_revision"] = git("rev-parse", "HEAD", root=gym_root)
        report["runtime"]["gym_clean"] = not bool(git("status", "--porcelain", root=gym_root))
        if (
            report["runtime"]["gym_revision"] != gym_revision
            or not report["runtime"]["gym_clean"]
            or not module_path.is_relative_to(gym_root.resolve())
        ):
            report["reason"] = "runtime_identity_mismatch"
        else:
            with (output / "pytest.log").open("w") as log:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "-rs",
                        *(f"tests/test_gym_native.py::{name}[{route}]" for name in NATIVE_TESTS),
                        f"--junitxml={output / 'junit.xml'}",
                        f"--basetemp={output / 'pytest'}",
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={
                        **os.environ,
                        "EVAL_AUTHOR_GYM_PYTHON": gym_python,
                        "EVAL_AUTHOR_SOURCE_ROOT": str(source_root),
                        "EVAL_AUTHOR_GYM_EVIDENCE": str(output / "native"),
                    },
                    timeout=timeout,
                )
            report["exit_code"] = result.returncode
            report.update(summarize(output, result.returncode, route))
            report["reason"] = {
                "passed": "controls_passed",
                "failed": "native_test_failure",
                "incomplete": "missing_or_invalid_evidence",
            }[report["status"]]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        # Detailed logs stay in the evidence artifact, not the dashboard summary.
        report["reason"] = "runtime_or_test_execution_error"
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["artifacts"] = {
            str(p.relative_to(output)): digest(p.read_bytes())
            for p in sorted(output.rglob("*"))
            if p.is_file() and p != summary
        }
        summary.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Gym controls: {report['status']} ({report['reason']})")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gym-python", required=True)
    parser.add_argument("--gym-root", type=Path, required=True)
    parser.add_argument("--gym-revision", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--route", choices=ROUTES, default="audit", help="Input route to exercise and retain")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT,
        help="Source under test; the current checkout still supplies the frozen harness",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.output.resolve(),
            args.gym_python,
            args.gym_root,
            args.gym_revision,
            args.timeout,
            args.source_root.resolve(),
            args.route,
        )
    )
