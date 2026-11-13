# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private native transport for the fixed, synthetic refund scorer panel."""

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import collect_verifier_cheat_evidence as common
import run_scorer_controls as identity

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/scorer-controls"


def load_json(path, *, root=None):
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    from eval_author_shared.task_evidence import loads

    return loads(identity.source_bytes(path, root=root).decode("utf-8"))


def private_env(output):
    home = output / "home"
    home.mkdir(mode=0o700)
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT") if key in os.environ}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(output / "panel"))
    plugins = Path("/Applications/Docker.app/Contents/Resources/cli-plugins")
    if plugins.is_dir():
        (home / ".docker").mkdir(mode=0o700)
        (home / ".docker/cli-plugins").mkdir(mode=0o700)
        for name in ("docker-compose", "docker-buildx"):
            if (plugins / name).is_file():
                (home / ".docker/cli-plugins" / name).symlink_to(plugins / name)
    return env


def public_summary(report):
    def token(value, pattern):
        return value if isinstance(value, str) and re.fullmatch(pattern, value) else None

    public = {
        "schema": "nemo.eval_author.scorer_native.v1",
        "protocol": "harbor-refund-scorer-v1",
        "status": report.get("status", "incomplete")
        if report.get("status") in ("passed", "failed", "incomplete")
        else "incomplete",
        "task_qualification": "NOT_ASSESSED",
        "synthetic_events": True,
        "external_payment_effect": False,
        "model_performance_measured": False,
        "authenticated_approval_measured": False,
        "source_revision": token(report.get("source_revision"), r"[a-f0-9]{40}"),
        "panel_digest": token(report.get("panel_digest"), r"sha256:[a-f0-9]{64}"),
        "execution_failed": report.get("execution_error") is not None,
        "error_count": len(report.get("errors", [])),
        "metrics": {},
    }
    for key in ("native_trials", "expected_cases", "retained_cases", "matched_cases"):
        value = report.get("metrics", {}).get(key)
        if type(value) is int and 0 <= value <= 10000:
            public["metrics"][key] = value
    runtime = report.get("runtime", {})
    public["runtime"] = {key: token(runtime.get(key), r"\d+\.\d+\.\d+") for key in ("python", "harbor")}
    return public


def run(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    summary, private = output / "scorer-native-summary.json", output / "scorer-native-private.json"
    report = {
        "status": "incomplete",
        "execution_error": None,
        "started_at": datetime.now(UTC).isoformat(),
        "runtime": {"python": platform.python_version()},
    }
    common.save_json(summary, public_summary(report))
    common.save_json(private, report)
    exit_code = 1
    try:
        env = private_env(output)
        report["source_revision"] = common.execute(["git", "rev-parse", "HEAD"], output, "revision", 10, env).strip()
        report["inputs_clean"] = not bool(
            common.execute(["git", "status", "--porcelain"], output, "status", 10, env).strip()
        )
        files = [
            ROOT / "uv.lock",
            Path(__file__),
            Path(common.__file__),
            ROOT / "tests/test_scorer_native.py",
            ROOT / "skills/eval-author/scripts/verifier_helper.py",
        ]
        files += sorted(path for path in FIXTURES.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        report["inputs"] = {str(path.relative_to(ROOT)): common.digest(path) for path in files}
        report["panel_digest"] = (
            "sha256:" + common.hashlib.sha256(json.dumps(report["inputs"], sort_keys=True).encode()).hexdigest()
        )
        version = common.importlib.metadata.version("harbor")
        common.require(version == "0.24.0", "requires locked Harbor 0.24.0")
        report["runtime"]["harbor"] = version
        report["runtime"]["docker"] = json.loads(
            common.execute(["docker", "version", "--format", "{{json .}}"], output, "docker-version", 30, env)
        )
        image = common.tomllib.loads((FIXTURES / "refund/task.toml").read_text())["environment"]["docker_image"]
        common.execute(["docker", "pull", image], output, "image-pull", 180, env)
        report["runtime"]["image"] = json.loads(
            common.execute(["docker", "image", "inspect", image], output, "image-inspect", 30, env)
        )
        stage_panel(
            output / "panel",
            runtime={
                "harbor": version,
                "image": image,
                "image_id": report["runtime"]["image"][0]["Id"],
                "python": platform.python_version(),
                "runner_sha256": common.digest(Path(__file__)),
            },
        )
        report["identity_before"] = validate_snapshot(output)
        failures = []
        commands = [
            (
                "refund-panel",
                [
                    str(Path(sys.executable).parent / "harbor"),
                    "run",
                    "--path",
                    str(output / "panel/refund"),
                    "--agent",
                    "control_agent:ControlAgent",
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
                    "refund-panel",
                    "--quiet",
                ],
                180,
            ),
            (
                "normalizer-controls",
                [
                    sys.executable,
                    str(Path(common.__file__)),
                    "--panel",
                    "normalizer",
                    "--output",
                    str(output / "normalizer"),
                ],
                2100,
            ),
        ]
        for label, command, timeout in commands:
            try:
                common.execute(command, output, label, timeout, env)
            except (OSError, RuntimeError, common.subprocess.SubprocessError) as exc:
                failures.append(str(exc))
        report["identity_after"] = validate_snapshot(output)
        common.require(same_json(report["identity_before"], report["identity_after"]), "selected identity changed")
        report["normalizer"] = common.summarize(output / "normalizer", int(bool(failures)))
        common.require(report["normalizer"]["status"] == "passed", "leakage normalizer controls incomplete or failed")
        common.require(not failures, "; ".join(failures))
        common.require(
            report["inputs"] == {str(path.relative_to(ROOT)): common.digest(path) for path in files},
            "source inputs changed during native execution",
        )
        exit_code = 0
    except (OSError, ValueError, RuntimeError, common.subprocess.SubprocessError) as exc:
        report["execution_error"] = f"{type(exc).__name__}: {exc}"
    report.update(summarize(output, exit_code))
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["artifacts"] = {
        str(path.relative_to(output)): common.digest(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and path not in (summary, private)
        and "home" not in path.relative_to(output).parts
    }
    common.save_json(private, report)
    common.save_json(summary, public_summary(report))
    return 0 if report["status"] == "passed" else 1


def stage_panel(destination, runtime=None):
    destination.mkdir(parents=True, exist_ok=False)
    snapshot = destination.parent / "inputs"
    snapshot.mkdir(mode=0o700)
    paths = {"contract": "contract.json", "controls": "cases.json", "scorer": "refund_scorer.py"}
    captured = {}
    for relative in paths.values():
        captured[relative] = identity.source_bytes(FIXTURES / relative)
        (snapshot / relative).write_bytes(captured[relative])
    shutil.copytree(FIXTURES / "refund", destination / "refund", ignore=shutil.ignore_patterns("__pycache__"))
    source = identity.loads(captured["cases.json"])
    scorer = ModuleType("refund_native_scorer")
    exec(compile(captured["refund_scorer.py"], str(FIXTURES / "refund_scorer.py"), "exec"), scorer.__dict__)
    contract = identity.loads(captured["contract.json"])
    manifest = []
    for case in source["cases"]:
        selected = scorer.contract_for_case(contract, case)
        manifest.append({"id": case["id"], "contract": selected})
    common.require(bool(manifest), "empty panel")
    common.save_json(destination / "refund/tests/manifest.json", manifest)
    common.save_json(
        destination / "submissions.json",
        [{"id": case["id"], "candidate": case["candidate"]} for case in source["cases"]],
    )
    shutil.copyfile(snapshot / "refund_scorer.py", destination / "refund/tests/refund_scorer.py")
    shutil.copyfile(
        ROOT / "skills/eval-author/scripts/verifier_helper.py", destination / "refund/tests/verifier_helper.py"
    )
    shutil.copyfile(FIXTURES / "control_agent.py", destination / "control_agent.py")
    for index, path in enumerate(sorted(p for p in destination.rglob("*") if p.is_file())):
        relative = f"native/{path.relative_to(destination)}"
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        paths[f"native-{index}"] = relative
    common.save_json(snapshot / "runtime.json", runtime or {"execution": "staging-only"})
    paths["runtime"] = "runtime.json"
    for index, dependency in enumerate(
        (
            Path(__file__),
            Path(identity.__file__),
            Path(common.__file__),
            ROOT / "skills/eval-author/scripts/rubric_identity.py",
        )
    ):
        relative = f"host/{dependency.name}"
        (snapshot / "host").mkdir(exist_ok=True)
        (snapshot / relative).write_bytes(identity.source_bytes(dependency))
        paths[f"host-{index}"] = relative
    selection = identity.select_inputs(snapshot, paths)
    common.save_json(destination.parent / "selection.json", selection)
    return destination


def same_json(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def task_digests(output):
    from harbor.models.task.task import Task
    from harbor.publisher.packager import Packager

    common.require(common.importlib.metadata.version("harbor") == "0.24.0", "task digest requires pinned Harbor")
    task = output / "panel/refund"
    # Harbor's APIs reopen paths. Give both algorithms a private bounded copy.
    with tempfile.TemporaryDirectory(prefix="refund-digest-") as temporary:
        captured = Path(temporary) / "refund"
        for path in task.rglob("*"):
            common.require(not path.is_symlink(), "task digest symlink")
            if path.is_dir():
                continue
            destination = captured / path.relative_to(task)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(identity.source_bytes(path, root=output))
        with warnings.catch_warnings():
            # Native 0.24 receipts still use dirhash, not the durable lock hash.
            warnings.filterwarnings(
                "ignore", message="Task.checksum will be deprecated soon", category=DeprecationWarning
            )
            checksum = Task(captured).checksum
        content_hash, _ = Packager.compute_content_hash(captured)
    return checksum, "sha256:" + content_hash


def native_check(output, cases):
    require = common.require

    def read(path):
        return load_json(path, root=output)

    def digest(path):
        return "sha256:" + common.hashlib.sha256(identity.source_bytes(path, root=output)).hexdigest()

    job = output / "jobs/refund-panel"
    require({p.name for p in (output / "jobs").iterdir()} == {"refund-panel"}, "unexpected jobs")
    aggregate = read(job / "result.json")
    require(type(aggregate["n_total_trials"]) is int and aggregate["n_total_trials"] == 1, "wrong trial count")
    for key, value in {
        "n_completed_trials": 1,
        "n_errored_trials": 0,
        "n_pending_trials": 0,
        "n_running_trials": 0,
        "n_cancelled_trials": 0,
        "n_retries": 0,
    }.items():
        require(type(aggregate["stats"][key]) is int and aggregate["stats"][key] == value, "wrong job counts")
    require(bool(aggregate["finished_at"]), "unfinished job")
    paths = list(job.glob("*/result.json"))
    require(len(paths) == 1, "missing or duplicate trial")
    trial_dir = paths[0].parent
    trial = read(paths[0])
    require(
        same_json(
            aggregate["stats"]["evals"],
            {
                "refund-scorer-control__adhoc": {
                    "n_trials": 1,
                    "n_errors": 0,
                    "metrics": [{"mean": 1.0}],
                    "pass_at_k": {},
                    "reward_stats": {"reward": {"1.0": [trial_dir.name]}},
                    "exception_stats": {},
                }
            },
        ),
        "aggregate evidence disagreement",
    )
    require(trial["task_name"] == "refund" and trial["trial_name"] == trial_dir.name, "wrong trial identity")
    require(trial["exception_info"] is None and bool(trial["finished_at"]), "unfinished trial")
    require(
        trial["agent_info"]["name"] == "refund-scorer-control" and trial["agent_info"]["model_info"] is None,
        "unexpected agent",
    )
    for phase in ("agent_execution", "verifier"):
        require(bool(trial[phase]["started_at"]) and bool(trial[phase]["finished_at"]), "unfinished phase")
    rewards = trial["verifier_result"]["rewards"]
    require(
        set(rewards) == {"reward"} and type(rewards["reward"]) in (int, float) and rewards["reward"] == 1,
        "native transport failed",
    )
    require(float(identity.source_bytes(trial_dir / "verifier/reward.txt", root=output)) == 1, "reward disagreement")
    identity.source_bytes(trial_dir / "verifier/test-stdout.txt", root=output)
    lock = read(trial_dir / "lock.json")
    checksum, content_digest = task_digests(output)
    require(trial["task_checksum"] == checksum, "native task digest disagreement")
    require(lock["task"]["digest"] == content_digest, "lock task digest disagreement")
    agent = read(trial_dir / "agent/control.json")
    require(
        agent["control"] == "refund-panel" and type(agent["return_code"]) is int and agent["return_code"] == 0,
        "agent failed",
    )
    require(
        type(agent["uid"]) is int
        and agent["uid"] == 65534
        and agent["hidden_read_blocked"] is True
        and all(agent[key] is False for key in ("tests_readable", "tests_writable", "verifier_writable")),
        "agent privilege violation",
    )
    require(
        agent["submission_sha256"] == digest(output / "panel/submissions.json").removeprefix("sha256:"),
        "submission digest mismatch",
    )
    receipt = read(trial_dir / "verifier/checks.json")
    consumed_paths = {"submissions.json": output / "inputs/native/submissions.json"}
    consumed_paths.update(
        {
            name: output / "inputs/native/refund/tests" / name
            for name in ("manifest.json", "refund_scorer.py", "verifier_helper.py", "verify.py")
        }
    )
    require(
        same_json(receipt["consumed"], {name: digest(path) for name, path in consumed_paths.items()}),
        "verifier consumed different bytes",
    )
    require(
        receipt["health"] == "ok" and type(receipt["reward"]) is int and receipt["reward"] == 1,
        "verifier transport failed",
    )
    require(
        type(receipt["uid"]) is int
        and receipt["uid"] == 0
        and type(receipt["tests_uid"]) is int
        and receipt["tests_uid"] == 0
        and type(receipt["tests_mode"]) is int
        and receipt["tests_mode"] == 0o700,
        "root verifier not reached",
    )
    require(same_json(receipt["smoke"], {"python": True, "scorer_import": True, "helper": True}), "smoke failed")
    operations = receipt["operational_controls"]
    names = {"helper-failure", "syntax-error", "empty-collection", "malformed-output"}
    require(set(operations) == names, "missing operational controls")
    for name in names:
        require(
            same_json(
                operations[name],
                {
                    "health": "error",
                    "reason": name.replace("-", "_"),
                    "checks": {},
                    "total_credit": None,
                    "accepted": None,
                },
            ),
            "operational failure credited",
        )
    rows = receipt["rows"]
    require(
        bool(cases) and type(receipt["cases"]) is int and receipt["cases"] == len(cases) == len(rows),
        "missing actual cases",
    )
    require([row["id"] for row in rows] == [case["id"] for case in cases], "case inventory mismatch")
    return [
        {
            "id": case["id"],
            "status": "passed" if same_json(row["results"], case["expected"]) else "failed",
            "results": row["results"],
            "expected": case["expected"],
        }
        for row, case in zip(rows, cases, strict=True)
    ]


def validate_snapshot(output):
    for relative in ("contract.json", "cases.json", "refund_scorer.py"):
        common.require(
            identity.source_bytes(output / "inputs" / relative, root=output)
            == identity.source_bytes(FIXTURES / relative),
            "fixed source bytes changed",
        )
    selection = load_json(output / "selection.json", root=output)
    runtime = load_json(output / "inputs/runtime.json", root=output)
    if runtime != {"execution": "staging-only"}:
        expected = {
            "harbor": "0.24.0",
            "python": platform.python_version(),
            "image": common.tomllib.loads(identity.source_bytes(FIXTURES / "refund/task.toml").decode())["environment"][
                "docker_image"
            ],
            "runner_sha256": "sha256:" + common.hashlib.sha256(identity.source_bytes(Path(__file__))).hexdigest(),
        }
        common.require(
            set(runtime) == {*expected, "image_id"}
            and all(runtime[key] == value for key, value in expected.items())
            and isinstance(runtime["image_id"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", runtime["image_id"]),
            "selected runtime changed",
        )
    # Regenerate from trusted sources, never from the retained dependency graph.
    with tempfile.TemporaryDirectory(prefix="refund-selected-") as temporary:
        trusted = Path(temporary).resolve()
        stage_panel(trusted / "panel", runtime=runtime)
        expected_selection = load_json(trusted / "selection.json", root=trusted)
        expected_native = {
            row["path"].removeprefix("native/")
            for row in expected_selection["artifacts"]
            if row["path"].startswith("native/")
        }
        common.require(
            {p.relative_to(output / "panel").as_posix() for p in (output / "panel").rglob("*") if not p.is_dir()}
            == expected_native,
            "staged input inventory changed",
        )
        common.require(same_json(selection, expected_selection), "selected source or dependency selection changed")
        for record in expected_selection["artifacts"]:
            relative = record["path"]
            common.require(
                identity.source_bytes(output / "inputs" / relative, root=output)
                == identity.source_bytes(trusted / "inputs" / relative, root=trusted),
                "selected source changed",
            )
    measured = identity.measure_identity(output / "inputs", selection)
    common.require(
        measured["measurements"]["identity"]["status"] == "pass"
        and measured["measurements"]["route"]["bound_dependency"] is True,
        "selected identity failed",
    )
    for record in selection["artifacts"]:
        relative = record["path"]
        if relative.startswith("native/"):
            common.require(
                identity.source_bytes(output / "panel" / relative.removeprefix("native/"), root=output)
                == identity.source_bytes(output / "inputs" / relative, root=output),
                "staged input changed",
            )
    return measured


def summarize(output, exit_code):
    output = output.resolve()
    errors, checks, cases = [], [], []
    try:
        common.require(
            load_json(output / "inputs/runtime.json", root=output) != {"execution": "staging-only"},
            "native runtime missing",
        )
        validate_snapshot(output)
        cases = load_json(output / "inputs/cases.json", root=output)["cases"]
        checks = native_check(output, cases)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    if exit_code != 0:
        errors.append("native execution failed")
    passed = sum(row["status"] == "passed" for row in checks)
    return {
        "status": "incomplete" if errors else "passed" if passed == len(cases) else "failed",
        "errors": errors,
        "checks": checks,
        "metrics": {
            "native_trials": int(bool(checks)),
            "expected_cases": len(cases),
            "retained_cases": len(checks),
            "matched_cases": passed,
        },
    }
