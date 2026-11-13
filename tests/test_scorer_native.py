# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_staging_keeps_expected_results_host_only(tmp_path):
    native = load("tools/scorer_native.py")
    source = ROOT / "evals/scorer-controls"
    cases = json.loads((source / "cases.json").read_text())["cases"]
    staged = native.stage_panel(tmp_path / "panel")
    manifest = json.loads((staged / "refund/tests/manifest.json").read_text())
    submissions = json.loads((staged / "submissions.json").read_text())
    assert [row["id"] for row in manifest] == [row["id"] for row in cases]
    assert submissions == [{"id": row["id"], "candidate": row["candidate"]} for row in cases]
    assert all(set(row) == {"id", "contract"} for row in manifest)
    assert not (staged / "cases.json").exists()
    assert (staged / "refund/tests/refund_scorer.py").read_bytes() == (source / "refund_scorer.py").read_bytes()
    assert (staged / "refund/tests/verifier_helper.py").read_bytes() == (
        ROOT / "skills/eval-author/scripts/verifier_helper.py"
    ).read_bytes()
    import tomllib

    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task

    assert Task.is_valid_dir(staged / "refund")
    config = TaskConfig.model_validate(tomllib.loads((staged / "refund/task.toml").read_text()))
    normal = tomllib.loads((ROOT / "evals/verifier-cheats/normalizer/task.toml").read_text())
    assert config.environment.docker_image == normal["environment"]["docker_image"]
    assert config.environment.network_mode.value == "no-network"
    assert config.agent.user == "65534"
    assert config.verifier.user == "root"
    assert config.verifier.timeout_sec <= 30


def test_trusted_worker_runs_same_scorer_without_expected_answers(tmp_path):
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(staged / "refund/tests/verify.py"),
            "--worker",
            str(staged / "refund/tests/manifest.json"),
            str(staged / "submissions.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    response = json.loads(result.stdout)
    scorer = load("evals/scorer-controls/refund_scorer.py", "current_refund_scorer")
    manifest = json.loads((staged / "refund/tests/manifest.json").read_text())
    submissions = json.loads((staged / "submissions.json").read_text())
    assert response["status"] == "result"
    assert response["value"]["rows"] == [
        {
            "id": row["id"],
            "results": {
                grader: scorer.evaluate(row["contract"], item["candidate"], grader)
                for grader in ("baseline", "reviewed")
            },
        }
        for row, item in zip(manifest, submissions, strict=True)
    ]


def test_verifier_retains_operational_failures_as_null_credit(tmp_path):
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    sys.path.insert(0, str(staged / "refund/tests"))
    verifier = load(str(staged / "refund/tests/verify.py"), "refund_verifier")
    assert hasattr(verifier, "collect"), "trusted receipt collection is missing"
    receipt = verifier.collect(staged / "refund/tests", staged / "submissions.json")
    assert receipt["health"] == "ok"
    assert receipt["consumed"]["submissions.json"] == native.common.digest(staged / "submissions.json")
    for name in ("manifest.json", "refund_scorer.py", "verifier_helper.py", "verify.py"):
        assert receipt["consumed"][name] == native.common.digest(staged / "refund/tests" / name)
    assert receipt["cases"] > 0
    assert len(receipt["rows"]) == receipt["cases"]
    assert receipt["smoke"] == {"python": True, "scorer_import": True, "helper": True}
    assert set(receipt["operational_controls"]) == {
        "helper-failure",
        "syntax-error",
        "empty-collection",
        "malformed-output",
    }
    for outcome in receipt["operational_controls"].values():
        assert outcome["health"] == "error"
        assert outcome["accepted"] is None and outcome["total_credit"] is None
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    failed = verifier.collect(staged / "refund/tests", empty)
    assert failed["health"] == "error"
    assert failed["total_credit"] is None
    assert failed["rows"] == []


@pytest.mark.parametrize("name", ["refund_scorer.py", "verifier_helper.py", "verify.py"])
def test_worker_does_not_claim_post_import_replacement_bytes(tmp_path, name):
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    tests = staged / "refund/tests"
    sys.path.insert(0, str(tests))
    verifier = load(str(tests / "verify.py"), "swap_verifier")
    original = native.common.digest(tests / name)
    (tests / name).write_text("raise RuntimeError('replacement must not be credited')\n")
    response = verifier.worker(tests / "manifest.json", staged / "submissions.json")
    assert response["rows"]
    assert response["consumed"][name] == original
    assert response["consumed"][name] != native.common.digest(tests / name)


@pytest.mark.parametrize("name", ["manifest.json", "submissions.json"])
def test_worker_hashes_data_buffer_it_actually_consumes(tmp_path, monkeypatch, name):
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    tests = staged / "refund/tests"
    verifier = load(str(tests / "verify.py"), "data_swap_verifier")
    path = staged / "submissions.json" if name == "submissions.json" else tests / name
    original_hash = native.common.digest(path)
    read = verifier.worker.__globals__["read_bytes"]

    def swap(selected):
        raw = read(selected)
        if Path(selected) == path:
            path.write_text("[]")
        return raw

    monkeypatch.setitem(verifier.worker.__globals__, "read_bytes", swap)
    response = verifier.worker(tests / "manifest.json", staged / "submissions.json")
    assert len(response["rows"]) > 0
    assert response["consumed"][name] == original_hash
    assert response["consumed"][name] != native.common.digest(path)


def test_stage_panel_executes_captured_scorer_bytes(tmp_path, monkeypatch):
    import shutil

    native = load("tools/scorer_native.py")
    fixtures = tmp_path / "fixtures"
    shutil.copytree(native.FIXTURES, fixtures)
    monkeypatch.setattr(native, "FIXTURES", fixtures)
    original = native.identity.source_bytes
    changed = False

    def read(path, **kwargs):
        nonlocal changed
        raw = original(path, **kwargs)
        if path == fixtures / "refund_scorer.py" and not changed:
            changed = True
            path.write_text("raise RuntimeError('reopened scorer')\n")
        return raw

    monkeypatch.setattr(native.identity, "source_bytes", read)
    staged = native.stage_panel(tmp_path / "output/panel")
    assert json.loads((staged / "refund/tests/manifest.json").read_text())
    assert (staged / "refund/tests/refund_scorer.py").read_bytes() != (fixtures / "refund_scorer.py").read_bytes()


def test_agent_submits_as_unprivileged_user_and_probes_hidden_read(tmp_path):
    agent_module = load("evals/scorer-controls/control_agent.py", "refund_control_agent")
    fixture = ROOT / "evals/scorer-controls"
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    commands = []
    uploads = []

    class Environment:
        async def exec(self, command, **kwargs):
            commands.append((command, kwargs))
            if kwargs.get("user") == "root":
                return SimpleNamespace(return_code=0)
            return SimpleNamespace(
                return_code=0,
                stdout=json.dumps(
                    {
                        "uid": 65534,
                        "tests_readable": False,
                        "tests_writable": False,
                        "verifier_writable": False,
                        "hidden_read_blocked": True,
                        "submission_sha256": "a" * 64,
                    }
                ),
            )

        async def upload_file(self, **kwargs):
            uploads.append(kwargs)

    agent = agent_module.ControlAgent(logs_dir=tmp_path / "agent")
    agent_module.__file__ = str(staged / "control_agent.py")
    asyncio.run(agent.setup(Environment()))
    asyncio.run(agent.run("", Environment(), None))
    assert uploads[0]["source_path"].name == "submissions.json"
    assert uploads[0]["target_path"] == "/controls/submissions.json"
    assert "chmod 700 /tests /logs/verifier" in commands[0][0]
    assert commands[-1][1].get("user") == "65534"
    assert "/tests/manifest.json" in commands[-1][0] and "PermissionError" in commands[-1][0]
    receipt = json.loads((tmp_path / "agent/control.json").read_text())
    assert receipt["uid"] == 65534 and receipt["hidden_read_blocked"] is True
    assert fixture.is_dir()


def synthetic_job(tmp_path):
    native = load("tools/scorer_native.py")
    import platform
    import tomllib

    staged = native.stage_panel(
        tmp_path / "panel",
        runtime={
            "harbor": "0.24.0",
            "python": platform.python_version(),
            "image": tomllib.loads((native.FIXTURES / "refund/task.toml").read_text())["environment"]["docker_image"],
            "image_id": "sha256:" + "b" * 64,
            "runner_sha256": native.common.digest(Path(native.__file__)),
        },
    )
    sys.path.insert(0, str(staged / "refund/tests"))
    verifier = load(str(staged / "refund/tests/verify.py"), "refund_verifier")
    receipt = verifier.collect(staged / "refund/tests", staged / "submissions.json")
    receipt.update(uid=0, tests_uid=0, tests_mode=0o700, reward=1)
    job = tmp_path / "jobs/refund-panel"
    trial = job / "refund__unit"
    from harbor.models.task.task import Task
    from harbor.publisher.packager import Packager

    with pytest.warns(DeprecationWarning):
        checksum = Task(staged / "refund").checksum
    content_hash, _ = Packager.compute_content_hash(staged / "refund")
    assert checksum != content_hash  # Harbor 0.24 native and durable algorithms differ.

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    save(
        job / "result.json",
        {
            "n_total_trials": 1,
            "finished_at": "done",
            "stats": {
                "n_completed_trials": 1,
                "n_errored_trials": 0,
                "n_pending_trials": 0,
                "n_running_trials": 0,
                "n_cancelled_trials": 0,
                "n_retries": 0,
                "evals": {
                    "refund-scorer-control__adhoc": {
                        "n_trials": 1,
                        "n_errors": 0,
                        "metrics": [{"mean": 1.0}],
                        "pass_at_k": {},
                        "reward_stats": {"reward": {"1.0": [trial.name]}},
                        "exception_stats": {},
                    }
                },
            },
        },
    )
    save(
        trial / "result.json",
        {
            "task_name": "refund",
            "trial_name": trial.name,
            "task_checksum": checksum,
            "agent_info": {"name": "refund-scorer-control", "model_info": None},
            "exception_info": None,
            "finished_at": "done",
            "agent_execution": {"started_at": "start", "finished_at": "done"},
            "verifier": {"started_at": "start", "finished_at": "done"},
            "verifier_result": {"rewards": {"reward": 1}},
        },
    )
    save(
        trial / "lock.json",
        {
            "schema_version": 2,
            "task": {
                "name": "refund",
                "type": "local",
                "digest": "sha256:" + content_hash,
                "path": str(staged / "refund"),
            },
        },
    )
    save(
        trial / "agent/control.json",
        {
            "control": "refund-panel",
            "uid": 65534,
            "return_code": 0,
            "tests_readable": False,
            "tests_writable": False,
            "verifier_writable": False,
            "hidden_read_blocked": True,
            "submission_sha256": native.common.digest(staged / "submissions.json").removeprefix("sha256:"),
        },
    )
    save(trial / "verifier/checks.json", receipt)
    (trial / "verifier/reward.txt").write_text("1\n")
    (trial / "verifier/test-stdout.txt").write_text("")
    return native, trial


def test_native_receipts_require_complete_cases_and_host_comparison(tmp_path):
    native, trial = synthetic_job(tmp_path)
    assert hasattr(native, "summarize"), "refund receipt parser missing"
    report = native.summarize(tmp_path, 0)
    assert report["status"] == "passed"
    assert report["metrics"]["native_trials"] == 1
    assert report["metrics"]["expected_cases"] == len(report["checks"])
    checks = json.loads((trial / "verifier/checks.json").read_text())
    checks["rows"][0]["results"]["reviewed"]["total_credit"] = -99
    (trial / "verifier/checks.json").write_text(json.dumps(checks))
    assert native.summarize(tmp_path, 0)["status"] == "failed"
    checks["rows"] = []
    checks["cases"] = 0
    (trial / "verifier/checks.json").write_text(json.dumps(checks))
    assert native.summarize(tmp_path, 0)["status"] == "incomplete"


@pytest.mark.parametrize(
    "mutation",
    ["membership", "duplicate", "count", "errors", "exceptions", "reward", "mean", "extra", "checksum", "lock"],
)
def test_native_harbor_evidence_rejects_specific_tampering(tmp_path, mutation):
    native, trial = synthetic_job(tmp_path)
    assert native.summarize(tmp_path, 0)["status"] == "passed"
    if mutation in ("checksum", "lock"):
        path = trial / ("result.json" if mutation == "checksum" else "lock.json")
        data = json.loads(path.read_text())
        # A real digest of different staged bytes, not a missing/placeholder hash.
        from harbor.models.task.task import Task
        from harbor.publisher.packager import Packager

        task = tmp_path / "panel/refund"
        instruction = task / "instruction.md"
        original = instruction.read_bytes()
        instruction.write_bytes(original + b"\nchanged\n")
        if mutation == "checksum":
            with pytest.warns(DeprecationWarning):
                data["task_checksum"] = Task(task).checksum
        else:
            data["task"]["digest"] = "sha256:" + Packager.compute_content_hash(task)[0]
        instruction.write_bytes(original)
    else:
        path = trial.parent / "result.json"
        data = json.loads(path.read_text())
        evaluation = data["stats"]["evals"]["refund-scorer-control__adhoc"]
        if mutation == "membership":
            evaluation["reward_stats"]["reward"]["1.0"] = ["foreign__trial"]
        elif mutation == "duplicate":
            evaluation["reward_stats"]["reward"]["1.0"] *= 2
        elif mutation == "count":
            evaluation["n_trials"] = 2
        elif mutation == "errors":
            evaluation["n_errors"] = 1
        elif mutation == "exceptions":
            evaluation["exception_stats"] = {"RuntimeError": [trial.name]}
        elif mutation == "reward":
            evaluation["reward_stats"]["reward"] = {"0.0": [trial.name]}
        elif mutation == "mean":
            evaluation["metrics"] = [{"mean": 0.0}]
        elif mutation == "extra":
            data["stats"]["evals"]["foreign__adhoc"] = evaluation.copy()
    path.write_text(json.dumps(data))
    report = native.summarize(tmp_path, 0)
    assert report["status"] == "incomplete", report
    assert any(("digest" if mutation in ("checksum", "lock") else "aggregate") in e for e in report["errors"])


def test_native_cannot_promote_staging_only_runtime(tmp_path):
    native, _ = synthetic_job(tmp_path)
    (tmp_path / "inputs/runtime.json").write_text('{"execution":"staging-only"}')
    reseal(native, tmp_path)
    report = native.summarize(tmp_path, 0)
    assert report["status"] == "incomplete"
    assert any("runtime" in error for error in report["errors"]), report


def test_unselected_staged_input_cannot_be_added(tmp_path):
    native, _ = synthetic_job(tmp_path)
    (tmp_path / "panel/refund/tests/extra.py").write_text("# foreign source\n")
    with pytest.raises(ValueError, match="staged"):
        native.validate_snapshot(tmp_path)


def test_runner_uses_existing_execute_private_env_and_keeps_setup_failure(tmp_path, monkeypatch):
    native = load("tools/scorer_native.py")
    assert hasattr(native, "run"), "refund runner is missing"
    monkeypatch.setenv("OPENAI_API_KEY", "private-key-sentinel")
    calls = []

    def execute(command, output, label, timeout=120, env=None):
        calls.append((command, env))
        assert env is not None and "OPENAI_API_KEY" not in env
        assert Path(env["HOME"]).is_dir()
        raise OSError("private-error-sentinel")

    monkeypatch.setattr(native.common, "execute", execute)
    output = tmp_path / "native"
    assert native.run(output) == 1
    assert calls
    report = json.loads((output / "scorer-native-private.json").read_text())
    public = json.loads((output / "scorer-native-summary.json").read_text())
    assert report["status"] == public["status"] == "incomplete"
    assert "private-error-sentinel" in report["execution_error"]
    assert "private-error-sentinel" not in json.dumps(public)
    assert public["protocol"] == "harbor-refund-scorer-v1"
    assert public["task_qualification"] == "NOT_ASSESSED"
    assert public["external_payment_effect"] is False
    assert output.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        native.run(output)


def test_runner_stages_hash_bound_panel_and_delegates_leakage_controls(tmp_path, monkeypatch):
    native = load("tools/scorer_native.py")
    calls = []

    def execute(command, output, label, timeout=120, env=None):
        calls.append((command, label, timeout, env))
        if label == "revision":
            return "a" * 40
        if label == "status":
            return ""
        if label == "docker-version":
            return '{"Server":{"Version":"29.0.0"}}'
        if label == "image-inspect":
            return '[{"Id":"sha256:' + "b" * 64 + '"}]'
        return ""

    monkeypatch.setattr(native.common, "execute", execute)
    output = tmp_path / "native"
    assert native.run(output) == 1  # Fake transport creates no jobs: never native evidence.
    labels = [call[1] for call in calls]
    assert "refund-panel" in labels and "normalizer-controls" in labels
    command = next(row[0] for row in calls if row[1] == "refund-panel")
    assert command[command.index("--agent") + 1] == "control_agent:ControlAgent"
    assert command[command.index("--n-attempts") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    normalizer = next(row[0] for row in calls if row[1] == "normalizer-controls")
    assert normalizer[normalizer.index("--panel") + 1] == "normalizer"
    report = json.loads((output / "scorer-native-private.json").read_text())
    assert report["panel_digest"].startswith("sha256:")
    assert "evals/scorer-controls/refund_scorer.py" in report["inputs"]
    assert report["status"] == "incomplete"
    assert report["artifacts"]["panel/control_agent.py"] == native.common.digest(output / "panel/control_agent.py")
    assert (output / "panel/control_agent.py").is_file()
    assert report["identity_before"]["measurements"]["identity"]["status"] == "pass"
    runtime = json.loads((output / "inputs/runtime.json").read_text())
    assert runtime["harbor"] == "0.24.0"
    assert runtime["image_id"] == "sha256:" + "b" * 64
    assert runtime["runner_sha256"] == native.common.digest(Path(native.__file__))
    assert "runtime" in {row["id"] for row in json.loads((output / "selection.json").read_text())["artifacts"]}


def test_collector_refund_dispatch_does_not_run_normalizer_in_process(tmp_path, monkeypatch):
    common = load("tools/collect_verifier_cheat_evidence.py", "collector_dispatch")
    import scorer_native

    calls = []
    monkeypatch.setattr(scorer_native, "run", lambda output: calls.append(output) or 1)
    monkeypatch.setattr(common, "run", lambda output: pytest.fail("wrong panel dispatched"))
    monkeypatch.setattr(sys, "argv", ["collector", "--panel", "refund", "--output", str(tmp_path / "native")])
    assert common.main() == 1
    assert calls == [tmp_path / "native"]


def test_staged_snapshot_has_shared_identity_and_detects_mutation(tmp_path):
    native = load("tools/scorer_native.py")
    staged = native.stage_panel(tmp_path / "panel")
    snapshot = tmp_path / "inputs"
    assert snapshot.is_dir(), "host-only selected snapshot missing"
    import run_scorer_controls as runner

    selection = json.loads((tmp_path / "selection.json").read_text())
    measurement = runner.measure_identity(snapshot, selection)
    assert measurement["measurements"]["identity"]["status"] == "pass"
    assert measurement["measurements"]["route"]["bound_dependency"] is True
    assert (snapshot / "refund_scorer.py").read_bytes() == (staged / "refund/tests/refund_scorer.py").read_bytes()
    (snapshot / "cases.json").write_text("{}")
    assert runner.measure_identity(snapshot, selection)["measurements"]["identity"]["status"] != "pass"


@pytest.mark.parametrize(
    "mutation", ["root", "readable", "reward", "trial", "operation", "boolean", "duplicate", "snapshot", "stage"]
)
def test_native_tampering_never_passes(tmp_path, mutation):
    native, trial = synthetic_job(tmp_path)
    if mutation in ("snapshot", "stage"):
        path = tmp_path / ("inputs/cases.json" if mutation == "snapshot" else "panel/refund/tests/refund_scorer.py")
        path.write_text("{}")
    elif mutation == "trial":
        (trial / "result.json").unlink()
    elif mutation == "reward":
        (trial / "verifier/reward.txt").write_text("0")
    else:
        path = trial / ("agent/control.json" if mutation == "readable" else "verifier/checks.json")
        data = json.loads(path.read_text())
        if mutation == "root":
            data["uid"] = 65534
        if mutation == "readable":
            data["tests_readable"] = True
        if mutation == "operation":
            data["operational_controls"]["empty-collection"]["total_credit"] = 0
        if mutation == "boolean":
            data["rows"][0]["results"]["baseline"]["total_credit"] = True
        if mutation == "duplicate":
            data["rows"][1]["id"] = data["rows"][0]["id"]
        path.write_text(json.dumps(data))
    assert native.summarize(tmp_path, 0)["status"] != "passed"


@pytest.mark.parametrize(
    "relative",
    [
        "jobs/refund-panel/refund__unit/verifier/reward.txt",
        "jobs/refund-panel/refund__unit/verifier/test-stdout.txt",
        "jobs/refund-panel/refund__unit/verifier",
        "jobs/refund-panel",
        "inputs",
        "panel",
        "selection.json",
    ],
)
def test_native_read_rejects_symlink_ancestors(tmp_path, relative):
    native, _ = synthetic_job(tmp_path)
    assert native.summarize(tmp_path, 0)["status"] == "passed"
    path = tmp_path / relative
    moved = tmp_path / "moved"
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())
    report = native.summarize(tmp_path, 0)
    assert report["status"] == "incomplete", report


@pytest.mark.parametrize("name", ["reward.txt", "test-stdout.txt"])
def test_native_read_bounds_all_verifier_files(tmp_path, name):
    native, trial = synthetic_job(tmp_path)
    (trial / "verifier" / name).write_bytes(b" " * 1048577 + b"1")
    assert native.summarize(tmp_path, 0)["status"] == "incomplete"


def test_missing_job_is_incomplete_even_for_expected_errors(tmp_path):
    native = load("tools/scorer_native.py")
    assert native.summarize(tmp_path, 0)["status"] == "incomplete"


def test_private_home_exposes_only_required_docker_plugins(tmp_path):
    native = load("tools/scorer_native.py")
    env = native.private_env(tmp_path)
    plugins = Path(env["HOME"]) / ".docker/cli-plugins"
    installed = Path("/Applications/Docker.app/Contents/Resources/cli-plugins")
    if installed.is_dir():
        assert not plugins.is_symlink()
        assert {p.name for p in plugins.iterdir()} == {
            name for name in ("docker-compose", "docker-buildx") if (installed / name).is_file()
        }
        assert all(p.is_symlink() for p in plugins.iterdir())


def test_strict_native_json_rejects_duplicate_keys(tmp_path):
    native = load("tools/scorer_native.py")
    path = tmp_path / "duplicate.json"
    path.write_text('{"value":1,"value":2}')
    assert hasattr(native, "load_json")
    with pytest.raises(ValueError):
        native.load_json(path)


def test_receipt_consumed_hash_must_match_selected_bytes(tmp_path):
    native, trial = synthetic_job(tmp_path)
    path = trial / "verifier/checks.json"
    data = json.loads(path.read_text())
    data["consumed"]["submissions.json"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(data))
    assert native.summarize(tmp_path, 0)["status"] == "incomplete"


def test_self_resealed_subset_cannot_replace_fixed_panel(tmp_path):
    native, _ = synthetic_job(tmp_path)
    path = tmp_path / "inputs/cases.json"
    document = json.loads(path.read_text())
    document["cases"] = document["cases"][:1]
    path.write_text(json.dumps(document))
    selection_path = tmp_path / "selection.json"
    selection = json.loads(selection_path.read_text())
    for row in selection["artifacts"]:
        if row["id"] == "controls":
            digest = native.common.digest(path).removeprefix("sha256:")
            row.update(digest=digest, revision=digest)
            row["evidence"][0]["digest"] = digest
    selection_path.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="fixed source"):
        native.validate_snapshot(tmp_path)


def reseal(native, output, paths=None):
    selected = json.loads((output / "selection.json").read_text())
    if paths is None:
        paths = {row["id"]: row["path"] for row in selected["artifacts"] if row["id"] != "dispatch"}
    (output / "inputs/dispatch.json").unlink()
    selected = native.identity.select_inputs(output / "inputs", paths)
    (output / "selection.json").write_text(json.dumps(selected))


@pytest.mark.parametrize("mutation", ["drop", "agent", "host", "manifest", "submissions", "runtime", "graph"])
def test_resealed_native_selection_is_not_authority(tmp_path, mutation):
    native, _ = synthetic_job(tmp_path)
    assert native.summarize(tmp_path, 0)["status"] == "passed"
    paths = None
    if mutation == "drop":
        paths = {"contract": "contract.json", "controls": "cases.json", "scorer": "refund_scorer.py"}
        (tmp_path / "panel/control_agent.py").write_text("raise RuntimeError('changed')")
    elif mutation == "runtime":
        (tmp_path / "inputs/runtime.json").write_text(
            json.dumps(
                {
                    "harbor": "0.0.0",
                    "image": "untrusted:latest",
                    "python": "0.0.0",
                    "runner_sha256": "sha256:" + "0" * 64,
                    "image_id": "sha256:" + "0" * 64,
                }
            )
        )
    elif mutation != "graph":
        relative = {
            "agent": "control_agent.py",
            "manifest": "refund/tests/manifest.json",
            "submissions": "submissions.json",
            "host": "host/scorer_native.py",
        }[mutation]
        if mutation == "host":
            (tmp_path / "inputs" / relative).write_text("# changed host\n")
        else:
            for base in ("panel", "inputs/native"):
                path = tmp_path / base / relative
                path.write_bytes(path.read_bytes() + b"\n ")
    reseal(native, tmp_path, paths)
    if mutation == "graph":
        selection_path = tmp_path / "selection.json"
        selection = json.loads(selection_path.read_text())
        dispatch_path = tmp_path / "inputs/dispatch.json"
        dispatch = json.loads(dispatch_path.read_text())
        dispatch["dependencies"]["scorer"] = ["contract", "controls"]
        dispatch_path.write_text(json.dumps(dispatch))
        selection["route"]["dependencies"] = dispatch["dependencies"]
        record = next(r for r in selection["artifacts"] if r["id"] == "dispatch")
        digest = native.common.digest(dispatch_path).removeprefix("sha256:")
        record.update(digest=digest, revision=digest, evidence=[{"path": "dispatch.json", "digest": digest}])
        selection_path.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="selection|source|runtime|staged|dependency"):
        native.validate_snapshot(tmp_path)


def load(relative, name="scorer_native_test_module"):
    path = ROOT / relative
    assert path.is_file(), f"missing implementation: {relative}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collector_exposes_opt_in_refund_panel():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/collect_verifier_cheat_evidence.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--panel {normalizer,refund}" in result.stdout
