# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "skills/eval-author-rubric/examples"
BASE = EXAMPLES / "refund/base"


def current_control_expectations():
    return json.loads((EXAMPLES / "refund/expectations/controls.json").read_text())


def test_ledger_action_path_exports_real_state_and_resets(tmp_path):
    import importlib.util

    path = BASE / "environment/ledger.py"
    assert path.is_file(), "The base needs a real ledger action path"
    spec = importlib.util.spec_from_file_location("refund_ledger_test", path)
    assert spec is not None and spec.loader is not None
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    contract = json.loads((BASE / "environment/contract.json").read_text())
    state = tmp_path / "private/record.json"
    engine = ledger.Ledger(contract, state)
    controls = json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]
    candidate = next(row["candidate"] for row in controls if row["id"] == "reference")
    initial = engine.read()
    assert initial["state"] == contract["sources"]["initial_state"]
    for event in candidate["events"]:
        engine.act(event)
    engine.complete(True)
    exported = json.loads(state.read_text())
    assert exported["candidate"] == candidate
    assert len(exported["state"]["refunds"]) == 2
    assert exported["state"]["refunds"][-1]["amount_cents"] == 8000
    assert exported["state"]["ledger"][-1]["amount_cents"] == 8000
    assert exported["state"]["protected"] == initial["state"]["protected"]
    engine.reset()
    assert engine.read() == initial
    assert json.loads(state.read_text()) == initial
    state.with_suffix(".closed").touch()
    for operation in (lambda: engine.act(candidate["events"][0]), lambda: engine.complete(True), engine.reset):
        with pytest.raises(ValueError, match="ledger_closed"):
            operation()
        assert engine.read() == initial
        assert json.loads(state.read_text()) == initial
    with pytest.raises(ValueError, match="ledger_closed"):
        ledger.Ledger(contract, state)
    assert json.loads(state.read_text()) == initial


def test_oracle_uses_ledger_service_and_verifier_ignores_forged_submission(tmp_path, monkeypatch):
    import socket
    import time

    server = BASE / "environment/server.py"
    assert server.is_file(), "The Oracle needs the solver action interface"
    monkeypatch.chdir(tmp_path)
    endpoint = Path("ledger.sock")
    record = tmp_path / "private/record.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(server),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--socket",
            str(endpoint),
            "--record",
            str(record),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert endpoint.exists(), process.communicate(timeout=1)
        initial = json.loads(record.read_text())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(endpoint))
            client.sendall(b'{"operation":"reset"}\n')
            response = json.loads(client.makefile("rb").readline())
        assert response == {"ok": False, "error": "invalid_request"}
        assert json.loads(record.read_text()) == initial
        subprocess.run(
            [
                sys.executable,
                str(BASE / "solution/reference.py"),
                str(BASE / "environment/contract.json"),
                "--socket",
                str(endpoint),
            ],
            check=True,
        )
        assert len(json.loads(record.read_text())["state"]["refunds"]) == 2
        forged = tmp_path / "submission.json"
        forged.write_text('{"events": [], "success": false}')
        result = subprocess.run(
            [
                sys.executable,
                str(BASE / "tests/verify.py"),
                "--contract",
                str(BASE / "environment/contract.json"),
                "--record",
                str(record),
                "--submission",
                str(forged),
                "--output",
                str(tmp_path / "logs"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / "logs/checks.json").read_text())["accepted"] is True
    finally:
        process.terminate()
        process.communicate(timeout=5)


@pytest.mark.parametrize("control", ["reference", "split-alternative"])
@pytest.mark.parametrize("schedule", ["late", "inflight", "committed"])
def test_verifier_closes_ledger_before_scoring_and_repeats(tmp_path, monkeypatch, control, schedule):
    import socket
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.chdir(tmp_path)
    record = Path("private/record.json")
    endpoint = Path("ledger.sock")
    # Pause an already processed action before its protected commit, not its validation.
    service_hook = """
import pathlib, sys, time
sys.path.insert(0, sys.argv.pop(1))
import server
original = server.Ledger._save
def paused(self, value):
    if pathlib.Path("hold-write").exists():
        pathlib.Path("write-prepared").touch()
        deadline = time.monotonic() + 10
        while not pathlib.Path("release-write").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("scheduling_timeout")
            time.sleep(0.01)
    return original(self, value)
server.Ledger._save = paused
server.main()
"""
    service = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            service_hook,
            str(BASE / "environment"),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--record",
            str(record),
            "--socket",
            str(endpoint),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def wait_for(path, process):
        deadline = time.monotonic() + 5
        while not path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists(), f"Missing synchronization point: {path}"

    def call(request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(endpoint))
            client.sendall((json.dumps(request) + "\n").encode())
            return json.loads(client.makefile("rb").readline())

    verifier = None
    pool = ThreadPoolExecutor(max_workers=1)
    pending = None
    try:
        wait_for(endpoint, service)
        controls = json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]
        candidate = next(row["candidate"] for row in controls if row["id"] == control)
        initial = record.read_bytes()
        for operation in ("reset", "freeze", "close"):
            assert call({"operation": operation}) == {"ok": False, "error": "invalid_request"}
        assert record.read_bytes() == initial
        for event in candidate["events"]:
            assert call({"operation": "act", "event": event}) == {"ok": True}
        assert call({"operation": "complete", "success": True}) == {"ok": True}
        before = record.read_bytes()
        duplicate = next(event for event in candidate["events"] if event["kind"] == "refund")
        if schedule == "committed":
            assert call({"operation": "act", "event": duplicate}) == {"ok": True}
            before = record.read_bytes()
        accepted = schedule != "committed"
        reward = "1.0" if accepted else "0.0"
        if schedule == "inflight":
            Path("hold-write").touch()
            pending = pool.submit(call, {"operation": "act", "event": duplicate})
            wait_for(Path("write-prepared"), service)
            assert not pending.done()
        # Pause the real verifier after it acquires its input, without replacing scoring.
        hook = """
import pathlib, sys, time
sys.path.insert(0, sys.argv.pop(1))
import verify
original = verify.evaluate
def paused(*args, **kwargs):
    pathlib.Path("snapshot-loaded").touch()
    deadline = time.monotonic() + 10
    while not pathlib.Path("resume").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("scheduling_timeout")
        time.sleep(0.01)
    return original(*args, **kwargs)
verify.evaluate = paused
raise SystemExit(verify.main())
"""
        argv = [
            "--contract",
            str(BASE / "environment/contract.json"),
            "--record",
            str(record),
            "--output",
            "logs",
        ]
        verifier = subprocess.Popen(
            [sys.executable, "-B", "-c", hook, str(BASE / "tests"), *argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        wait_for(Path("snapshot-loaded"), verifier)
        Path("release-write").touch()
        late = pending.result(timeout=5) if pending else call({"operation": "act", "event": duplicate})
        completion = call({"operation": "complete", "success": False})
        Path("resume").touch()
        _, stderr = verifier.communicate(timeout=5)
        assert verifier.returncode == 0, stderr
        assert late == {"ok": False, "error": "invalid_request"}
        assert completion == {"ok": False, "error": "invalid_request"}
        assert record.read_bytes() == before
        assert json.loads(Path("logs/checks.json").read_text())["accepted"] is accepted
        assert Path("logs/reward.txt").read_text().strip() == reward
        first = Path("logs/checks.json").read_bytes()
        repeated = subprocess.run(
            [sys.executable, "-B", str(BASE / "tests/verify.py"), *argv],
            capture_output=True,
            timeout=5,
        )
        assert repeated.returncode == 0, repeated.stderr
        assert Path("logs/checks.json").read_bytes() == first
        assert Path("logs/reward.txt").read_text().strip() == reward
        assert call({"operation": "act", "event": duplicate})["ok"] is False
        assert record.read_bytes() == before
    finally:
        Path("resume").touch()
        Path("release-write").touch()
        if verifier is not None and verifier.poll() is None:
            verifier.kill()
            verifier.communicate(timeout=5)
        service.terminate()
        service.communicate(timeout=5)
        pool.shutdown(wait=True)


def test_harbor_entrypoints_use_actions_and_protected_record():
    dockerfile = (BASE / "environment/Dockerfile").read_text()
    assert 'ENTRYPOINT ["python", "-B", "/app/server.py"]' in dockerfile
    assert "COPY ledger.py server.py /app/" in dockerfile
    assert "--socket /run/refund/ledger.sock" in (BASE / "solution/solve.sh").read_text()
    assert "--record /var/lib/refund/record.json" in (BASE / "tests/test.sh").read_text()
    assert "--submission" not in (BASE / "tests/test.sh").read_text()


@pytest.mark.parametrize("attack", ["path", "startup", "shadow"])
def test_verifier_shell_isolates_startup_and_keeps_trusted_helper(tmp_path, attack):
    import os
    import re
    import shutil

    trusted = tmp_path / "trusted"
    shutil.copytree(BASE / "tests", trusted)
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    marker = tmp_path / "injected"
    payload = f"from pathlib import Path; Path({str(marker)!r}).touch(); raise RuntimeError('injected')\n"
    if attack == "path":
        fake = hostile / "python"
        fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 91\n")
        fake.chmod(0o700)
    elif attack == "startup":
        (hostile / "sitecustomize.py").write_text(payload)
        (hostile / "usercustomize.py").write_text(payload)
    else:
        for name in ("argparse", "json", "refund_scorer"):
            (hostile / f"{name}.py").write_text(payload)
    contract = json.loads((BASE / "environment/contract.json").read_text())
    record = tmp_path / "private/record.json"
    record.parent.mkdir()
    record.write_text(
        json.dumps({"candidate": {"events": [], "success": False}, "state": contract["sources"]["initial_state"]})
    )
    record.with_suffix(".lock").touch()
    logs = tmp_path / "logs"
    # Relocate only native absolute paths; preserve the real shell launch and flags.
    paths = {
        "/usr/local/bin/python": sys.executable,
        "/tests": str(trusted),
        "/app/contract.json": str(BASE / "environment/contract.json"),
        "/var/lib/refund/record.json": str(record),
        "/logs/verifier": str(logs),
    }
    shell = re.sub(
        "|".join(re.escape(key) for key in paths), lambda match: paths[match[0]], (BASE / "tests/test.sh").read_text()
    )
    entry = trusted / "test.sh"
    entry.write_text(shell)
    env = {"PATH": str(hostile) + os.pathsep + os.environ["PATH"], "HOME": str(hostile), "PYTHONPATH": str(hostile)}
    completed = subprocess.run(
        ["/bin/sh", str(entry)], cwd=hostile, env=env, capture_output=True, text=True, timeout=10
    )
    assert not marker.exists(), f"Verifier executed {attack} injection"
    assert completed.returncode == 0, completed.stderr
    assert json.loads((logs / "checks.json").read_text())["health"] == "ok"
    assert (logs / "reward.txt").read_text().strip() == "0.0"
    assert record.with_suffix(".closed").exists()
    assert not list(trusted.rglob("*.pyc"))


def test_record_state_disagreement_is_an_error(tmp_path):
    controls = json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]
    candidate = next(row["candidate"] for row in controls if row["id"] == "reference")
    record = tmp_path / "record.json"
    record.write_text(json.dumps({"candidate": candidate, "state": {}}))
    record.with_suffix(".lock").touch()
    completed = subprocess.run(
        [
            sys.executable,
            str(BASE / "tests/verify.py"),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--record",
            str(record),
            "--output",
            str(tmp_path / "logs"),
        ],
        capture_output=True,
    )
    assert completed.returncode == 2
    assert json.loads((tmp_path / "logs/checks.json").read_text())["accepted"] is None
    assert not (tmp_path / "logs/reward.txt").exists()


def test_ledger_controls_run_actions_and_preserve_input_errors(tmp_path):
    module = fixture_module()
    module.materialize(tmp_path / "selected")
    assert hasattr(module, "run_ledger_controls"), "Run controls through the real action service"
    panel = module.run_ledger_controls(tmp_path / "selected", tmp_path / "actions")
    assert panel["execution_kind"] == "local_ledger_service_not_native"
    expected = current_control_expectations()
    assert set(panel["cases"]) == set(expected)
    for name, row in panel["cases"].items():
        if name in {
            "protected-mutation",
            "historical-mutation",
            "malformed-events",
            "helper-error",
            "ordering-evidence-unavailable",
        }:
            assert row["action_errors"] == 1
            assert row["status"] == "input_rejected"
        else:
            assert row["action_errors"] == 0
            assert row["status"] == "completed"
            assert row["result"] == expected[name]
    assert panel["cases"]["no-work"]["result"]["total_credit"] == 0
    assert panel["cases"]["permitted-zero-balance-inaction"]["result"]["accepted"] is True


def test_ledger_cli_runs_without_model_or_docker(tmp_path):
    script = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"
    module = fixture_module()
    module.materialize(tmp_path / "selected")
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "ledger-controls",
            "--input",
            str(tmp_path / "selected"),
            "--output",
            str(tmp_path / "results"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["execution_kind"] == "local_ledger_service_not_native"
    assert (
        json.loads((tmp_path / "results/ledger-results.json").read_text())["cases"]["reference"]["result"]["accepted"]
        is True
    )


def test_configuration_and_missing_input_mutations_change_conditions(tmp_path):
    module = fixture_module()
    cases = {
        "missing-contract": "environment/contract.json",
        "disabled-ledger": "environment/Dockerfile",
        "tax-drift": "environment/contract.json",
    }
    for case, changed in cases.items():
        manifest = module.materialize(tmp_path / case, case)
        assert manifest["changed_paths"] == [changed]
        assert module.verify_materialization(tmp_path / case) == manifest
        task = tmp_path / case / "assessment/task"
        if case == "missing-contract":
            assert not (task / changed).exists()
            assert "COPY contract.json" in (task / "environment/Dockerfile").read_text()
        elif case == "disabled-ledger":
            assert 'ENTRYPOINT ["sh", "-c", "sleep infinity"]' in (task / changed).read_text()
            assert "/run/refund/ledger.sock" in (task / "solution/solve.sh").read_text()
        else:
            panel = module.run_local_controls(tmp_path / case, tmp_path / "tax-results")
            assert panel["cases"]["reference"]["result"]["checks"]["amount"] == 0
        restored = module.restore_to_base(tmp_path / case, tmp_path / f"repair-{case}")
        assert restored["task_sha256"] == module.tree_identity(BASE)


def test_evidence_mutation_withholds_real_control_without_editing_source(tmp_path):
    module = fixture_module()
    module.materialize(tmp_path / "selected")
    original = module.run_local_controls(tmp_path / "selected", tmp_path / "evidence")
    assert hasattr(module, "mutate_evidence"), "Evidence mutation needs a separate output"
    reduced = module.mutate_evidence(
        tmp_path / "selected", tmp_path / "evidence", tmp_path / "reduced", "withhold-reference"
    )
    assert "reference" not in reduced["cases"]
    assert reduced["cases"] == {k: v for k, v in original["cases"].items() if k != "reference"}
    assert json.loads((tmp_path / "evidence/local-results.json").read_text()) == original
    assert module.verify_materialization(tmp_path / "selected")["case"] == "base"
    import pytest

    with pytest.raises((FileExistsError, ValueError)):
        module.mutate_evidence(
            tmp_path / "selected", tmp_path / "evidence", tmp_path / "evidence", "withhold-reference"
        )
    fresh = module.run_local_controls(tmp_path / "selected", tmp_path / "repair-evidence")
    assert fresh == original


def test_evidence_cli_withholds_actual_reference(tmp_path):
    module = fixture_module()
    module.materialize(tmp_path / "selected")
    module.run_local_controls(tmp_path / "selected", tmp_path / "evidence")
    script = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "mutate-evidence",
            "--input",
            str(tmp_path / "selected"),
            "--evidence",
            str(tmp_path / "evidence"),
            "--output",
            str(tmp_path / "reduced"),
            "--mutation",
            "withhold-reference",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "reference" not in json.loads(completed.stdout)["cases"]


def test_outputs_cannot_modify_source_or_selected_package(tmp_path):
    import pytest

    module = fixture_module()
    module.materialize(tmp_path / "selected")
    before = module.tree_identity(tmp_path / "selected")
    with pytest.raises(ValueError, match="output_overlaps_input"):
        module.run_local_controls(tmp_path / "selected", tmp_path / "selected/assessment/logs")
    assert module.tree_identity(tmp_path / "selected") == before
    with pytest.raises(ValueError, match="output_overlaps_input"):
        module.restore_to_base(tmp_path / "selected", tmp_path / "selected/repair")
    assert module.tree_identity(tmp_path / "selected") == before


def test_ledger_rejects_invalid_actions_without_changing_state(tmp_path):
    import importlib.util

    import pytest

    spec = importlib.util.spec_from_file_location("ledger_errors", BASE / "environment/ledger.py")
    assert spec is not None and spec.loader is not None
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    contract = json.loads((BASE / "environment/contract.json").read_text())
    engine = ledger.Ledger(contract, tmp_path / "record.json")
    before = (tmp_path / "record.json").read_bytes()
    for event in (
        {"kind": "change_protected", "value": {}},
        {"kind": "ledger", "entries": []},
        {"kind": "refund", "refund": {}},
        {"kind": "approve", "run_id": "x", "customer_id": "x", "order_id": "x", "actor": "x", "amount_cents": True},
    ):
        with pytest.raises(ValueError):
            engine.act(event)
        assert (tmp_path / "record.json").read_bytes() == before
    with pytest.raises(ValueError):
        engine.complete(1)
    assert (tmp_path / "record.json").read_bytes() == before


def test_action_mutation_repair_and_fresh_policy_solution(tmp_path):
    module = fixture_module()
    module.materialize(tmp_path / "bad", "completion-only")
    bad = module.run_ledger_controls(tmp_path / "bad", tmp_path / "bad-controls")
    assert bad["cases"]["no-work"]["result"]["accepted"] is True
    module.restore_to_base(tmp_path / "bad", tmp_path / "repaired")
    repaired = module.run_ledger_controls(tmp_path / "repaired", tmp_path / "repair-controls")
    assert repaired["cases"]["no-work"]["result"]["accepted"] is False
    assert repaired["cases"]["split-alternative"]["result"]["accepted"] is True
    module.materialize(tmp_path / "drift", "tax-drift")
    task = tmp_path / "drift/assessment/task"
    subprocess.run(
        [
            sys.executable,
            str(task / "solution/reference.py"),
            str(task / "environment/contract.json"),
            str(tmp_path / "fresh.json"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(task / "tests/verify.py"),
            "--contract",
            str(task / "environment/contract.json"),
            "--submission",
            str(tmp_path / "fresh.json"),
            "--output",
            str(tmp_path / "fresh-results"),
        ],
        check=True,
    )
    assert json.loads((tmp_path / "fresh-results/checks.json").read_text())["accepted"] is True


def test_local_action_runner_refuses_disabled_startup(tmp_path):
    import pytest

    module = fixture_module()
    module.materialize(tmp_path / "disabled", "disabled-ledger")
    with pytest.raises(ValueError, match="native_route_required"):
        module.run_ledger_controls(tmp_path / "disabled", tmp_path / "logs")
    assert not (tmp_path / "logs").exists()


def test_verifier_rejects_record_symlink_and_malformed_record(tmp_path):
    record = tmp_path / "record.json"
    record.write_text('{"state": {}}')
    for source in (record, tmp_path / "alias.json"):
        if source != record:
            source.symlink_to(record)
        completed = subprocess.run(
            [
                sys.executable,
                str(BASE / "tests/verify.py"),
                "--contract",
                str(BASE / "environment/contract.json"),
                "--record",
                str(source),
                "--output",
                str(tmp_path / "logs"),
            ],
            capture_output=True,
        )
        assert completed.returncode == 2
        assert json.loads((tmp_path / "logs/checks.json").read_text())["health"] == "error"
        assert not (tmp_path / "logs/reward.txt").exists()


def test_base_reference_runs_through_local_verifier(tmp_path):
    required = (
        "instruction.md",
        "task.toml",
        "environment/Dockerfile",
        "environment/contract.json",
        "solution/solve.sh",
        "solution/reference.py",
        "tests/test.sh",
        "tests/verify.py",
        "tests/refund_scorer.py",
    )
    assert all((BASE / name).is_file() for name in required), "Complete Harbor base is missing"
    config = TaskConfig.model_validate(__import__("tomllib").loads((BASE / "task.toml").read_text()))
    assert config.schema_version == "1.3"
    submission = tmp_path / "submission.json"
    subprocess.run(
        [
            sys.executable,
            str(BASE / "solution/reference.py"),
            str(BASE / "environment/contract.json"),
            str(submission),
        ],
        check=True,
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(BASE / "tests/verify.py"),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--submission",
            str(submission),
            "--output",
            str(tmp_path / "logs"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads((tmp_path / "logs/checks.json").read_text())
    assert result["health"] == "ok"
    assert result["accepted"] is True
    assert result["total_credit"] == result["max_credit"] == 9
    assert (tmp_path / "logs/reward.txt").read_text().strip() == "1.0"


def test_environment_builds_public_contract():
    import tomllib

    config = TaskConfig.model_validate(tomllib.loads((BASE / "task.toml").read_text()))
    assert config.environment.docker_image is None, "A prebuilt image skips the contract COPY"
    assert "COPY contract.json /app/contract.json" in (BASE / "environment/Dockerfile").read_text()


def test_declared_controls_use_actual_local_verifier(tmp_path):
    catalog = EXAMPLES / "refund/controls.json"
    assert catalog.is_file(), "Deterministic control definitions are missing"
    controls = json.loads(catalog.read_text())["cases"]
    expected = current_control_expectations()
    assert {row["id"] for row in controls} == set(expected)
    assert {
        "reference",
        "split-alternative",
        "permitted-zero-balance-inaction",
        "no-work",
        "all-failure",
        "wrong-amount",
        "missing-approval",
    } <= set(expected)
    for row in controls:
        folder = tmp_path / row["id"]
        folder.mkdir()
        contract = json.loads((BASE / "environment/contract.json").read_text())

        def merge(target, override):
            for key, value in override.items():
                if isinstance(value, dict):
                    merge(target[key], value)
                else:
                    target[key] = value

        merge(contract, row.get("contract_variant", {}))
        (folder / "contract.json").write_text(json.dumps(contract))
        (folder / "submission.json").write_text(json.dumps(row["candidate"]))
        completed = subprocess.run(
            [
                sys.executable,
                str(BASE / "tests/verify.py"),
                "--contract",
                str(folder / "contract.json"),
                "--submission",
                str(folder / "submission.json"),
                "--output",
                str(folder / "logs"),
            ],
            capture_output=True,
            text=True,
        )
        observed = json.loads((folder / "logs/checks.json").read_text())
        assert observed == expected[row["id"]], row["id"]
        assert completed.returncode == (0 if observed["health"] == "ok" else 2)
        if observed["health"] != "ok":
            assert not (folder / "logs/reward.txt").exists()


def fixture_module():
    import importlib.util

    path = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"
    assert path.is_file(), "Installed-skill fixture materializer is missing"
    spec = importlib.util.spec_from_file_location("rubric_example_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_materializer_keeps_expectations_outside_assessment(tmp_path):
    module = fixture_module()
    first = module.materialize(tmp_path / "first")
    second = module.materialize(tmp_path / "second")
    assert first == second
    assert first["evidence_status"] == "not_run"
    assert first["task_sha256"] == first["base_sha256"]
    assert module.tree_identity(tmp_path / "first/assessment/task") == module.tree_identity(BASE)
    assert module.verify_materialization(tmp_path / "first") == first
    names = {str(p.relative_to(tmp_path / "first/assessment")) for p in (tmp_path / "first/assessment").rglob("*")}
    assert not any("expectation" in n or "mutation" in n or "controls.json" in n for n in names)
    assert (tmp_path / "first/assessment/request.md").read_text() == module.ASSESSMENT_REQUEST
    assert first["invalidated_evidence"] == ["all_source_bound_observations"]


def test_materializer_refuses_overwrite_and_unsafe_paths(tmp_path):
    import pytest

    module = fixture_module()
    output = tmp_path / "existing"
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises((FileExistsError, ValueError)):
        module.materialize(output)
    assert (output / "sentinel").read_text() == "keep"
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_path"):
        module.materialize(alias / "escape")
    with pytest.raises(ValueError, match="unsafe_path"):
        module.materialize(tmp_path / ".." / "escape")
    assert not (output / "escape").exists()


def test_identity_rejects_symlink_sources_and_binds_modes(tmp_path):
    import pytest

    module = fixture_module()
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "file").write_text("safe")
    before = module.tree_identity(folder)
    (folder / "file").chmod(0o700)
    assert module.tree_identity(folder) != before
    (folder / "linked").symlink_to(folder / "file")
    with pytest.raises(ValueError, match="unsafe"):
        module.tree_identity(folder)
    (folder / "linked").unlink()
    alias = tmp_path / "root-alias"
    alias.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        module.tree_identity(alias)


def test_completion_only_mutation_changes_real_verifier_and_restores(tmp_path):
    module = fixture_module()
    manifest = module.materialize(tmp_path / "bad", "completion-only")
    assert manifest["task_sha256"] != manifest["base_sha256"]
    assert manifest["changed_paths"] == ["tests/verify.py"]
    assert manifest["rerun_controls"] == ["all_local_controls", "all_source_bound_native_controls"]
    assert module.verify_materialization(tmp_path / "bad") == manifest
    restored = module.restore_to_base(tmp_path / "bad", tmp_path / "repaired")
    assert restored["task_sha256"] == manifest["base_sha256"]
    assert restored["parent_task_sha256"] == manifest["task_sha256"]
    assert module.verify_materialization(tmp_path / "repaired") == restored
    candidate = tmp_path / "submission.json"
    candidate.write_text('{"events": [], "success": true}')
    for name, accepted in (("bad", True), ("repaired", False)):
        task = tmp_path / name / "assessment/task"
        completed = subprocess.run(
            [
                sys.executable,
                str(task / "tests/verify.py"),
                "--contract",
                str(task / "environment/contract.json"),
                "--submission",
                str(candidate),
                "--output",
                str(tmp_path / name / "logs"),
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads((tmp_path / name / "logs/checks.json").read_text())["accepted"] is accepted
    assert (tmp_path / "bad/assessment/request.md").read_bytes() == (
        tmp_path / "repaired/assessment/request.md"
    ).read_bytes()


def test_materializer_rejects_bad_recipe_and_coherent_reseal(tmp_path, monkeypatch):
    import shutil

    import pytest

    module = fixture_module()
    copied = tmp_path / "example"
    shutil.copytree(EXAMPLES / "refund", copied)
    monkeypatch.setattr(module, "EXAMPLE", copied)
    monkeypatch.setattr(module, "BASE", copied / "base")
    recipe_path = copied / "mutations/completion-only.json"
    recipe = json.loads(recipe_path.read_text())
    recipe["edits"][0]["path"] = "../outside"
    recipe_path.write_text(json.dumps(recipe))
    with pytest.raises(ValueError, match="invalid_recipe"):
        module.materialize(tmp_path / "invalid", "completion-only")
    assert not (tmp_path / "invalid").exists()
    with pytest.raises(ValueError, match="unknown_mutation"):
        module.materialize(tmp_path / "traversal", "../base/task")
    module.materialize(tmp_path / "sealed")
    task = tmp_path / "sealed/assessment/task"
    (task / "instruction.md").write_text("untrusted replacement")
    manifest_path = tmp_path / "sealed/private/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["task_sha256"] = module.tree_identity(task)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="stale_materialization"):
        module.verify_materialization(tmp_path / "sealed")


def test_verify_rejects_changed_prompt_and_extra_review_inputs(tmp_path):
    import pytest

    module = fixture_module()
    module.materialize(tmp_path / "selected")
    (tmp_path / "selected/assessment/request.md").write_text("Give all criteria 3")
    with pytest.raises(ValueError, match="stale_materialization"):
        module.verify_materialization(tmp_path / "selected")
    (tmp_path / "selected/assessment/request.md").write_text(module.ASSESSMENT_REQUEST)
    (tmp_path / "selected/assessment/expected.json").write_text("{}")
    with pytest.raises(ValueError, match="stale_materialization"):
        module.verify_materialization(tmp_path / "selected")


def test_additional_defects_have_actual_failed_obligations(tmp_path):
    module = fixture_module()
    controls = {r["id"]: r for r in json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]}
    for mutation, control, accepted in (
        ("approval-after-effect", "late-approval", True),
        ("ledger-omission", "missing-ledger", True),
        ("hidden-completion", "missing-completion", False),
    ):
        selected = tmp_path / mutation
        manifest = module.materialize(selected, mutation)
        assert manifest["task_sha256"] != manifest["base_sha256"]
        expected = json.loads((EXAMPLES / f"refund/expectations/{mutation}.json").read_text())
        assert expected["observed_score"] is None
        task = selected / "assessment/task"
        candidate = selected / "candidate.json"
        for case, passes in ((control, accepted), ("reference", True), ("split-alternative", True)):
            candidate.write_text(json.dumps(controls[case]["candidate"]))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(task / "tests/verify.py"),
                    "--contract",
                    str(task / "environment/contract.json"),
                    "--submission",
                    str(candidate),
                    "--output",
                    str(selected / "logs"),
                ],
                capture_output=True,
                text=True,
            )
            assert completed.returncode == 0, completed.stderr
            assert json.loads((selected / "logs/checks.json").read_text())["accepted"] is passes
        repaired = module.restore_to_base(selected, tmp_path / f"repair-{mutation}")
        assert repaired["task_sha256"] == manifest["base_sha256"]


def test_cli_local_controls_and_repeat_verification(tmp_path):
    module = fixture_module()
    selected = tmp_path / "selected"
    script = ROOT / "skills/eval-author-rubric/scripts/example_fixtures.py"
    completed = subprocess.run(
        [sys.executable, str(script), "materialize", "--output", str(selected)], capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    assert (selected / "assessment/task/task.toml").is_file(), "CLI did not materialize the task"
    output = tmp_path / "local"
    result = module.run_local_controls(selected, output)
    assert result["execution_kind"] == "local_verifier_subprocess_not_native"
    assert result["task_sha256"] == module.tree_identity(selected / "assessment/task")
    assert len(result["cases"]) == 22
    assert result["cases"]["reference"]["result"]["accepted"] is True
    assert result["cases"]["all-failure"]["result"]["accepted"] is False
    assert result["cases"]["helper-error"]["result"]["health"] == "error"
    assert result == module.run_local_controls(selected, tmp_path / "repeat")
    verified = subprocess.run(
        [sys.executable, str(script), "verify", "--input", str(selected)], capture_output=True, text=True
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["task_sha256"] == result["task_sha256"]


def test_local_controls_reject_changed_task_code_before_execution(tmp_path):
    import pytest

    module = fixture_module()
    module.materialize(tmp_path / "selected")
    verifier = tmp_path / "selected/assessment/task/tests/verify.py"
    verifier.write_text("raise RuntimeError('untrusted')")
    with pytest.raises(ValueError, match="stale_materialization"):
        module.run_local_controls(tmp_path / "selected", tmp_path / "logs")
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("control", ["all-failure", "reference", "helper-error"])
@pytest.mark.parametrize("shape", ["file", "symlink", "directory"])
def test_verifier_admits_only_current_reward_formats(tmp_path, control, shape):
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier

    logs = tmp_path / "trial/verifier"
    logs.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text('{"reward":1}')
    for name in ("reward.json", "reward.txt", "checks.json"):
        target = logs / name
        if shape == "symlink":
            target.symlink_to(outside)
        elif shape == "directory":
            target.mkdir()
            (target / "payload").write_text("keep out")
        else:
            target.write_text('{"reward":1}' if name.endswith(".json") else "1")
    controls = json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]
    spec = next(row for row in controls if row["id"] == control)
    submission = tmp_path / "candidate.json"
    submission.write_text(json.dumps(spec["candidate"]))
    completed = subprocess.run(
        [
            sys.executable,
            str(BASE / "tests/verify.py"),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--submission",
            str(submission),
            "--output",
            str(logs),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == (2 if control == "helper-error" else 0), completed.stderr
    assert outside.read_text() == '{"reward":1}'
    assert not (logs / "reward.json").exists(), "Harbor prioritizes this solver-authored format"
    checks = json.loads((logs / "checks.json").read_text())
    if control == "helper-error":
        assert checks["health"] == "error"
        assert not (logs / "reward.txt").exists()
    else:
        consumer = object.__new__(Verifier)
        consumer.trial_paths = TrialPaths(trial_dir=tmp_path / "trial")
        assert consumer._parse_reward_text() == {"reward": checks["total_credit"] / checks["max_credit"]}


def test_verifier_drops_stale_reward_after_missing_submission(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "reward.txt").write_text("1.0")
    (logs / "checks.json").write_text('{"accepted":true}')
    completed = subprocess.run(
        [
            sys.executable,
            str(BASE / "tests/verify.py"),
            "--contract",
            str(BASE / "environment/contract.json"),
            "--submission",
            str(tmp_path / "absent.json"),
            "--output",
            str(logs),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert not (logs / "reward.txt").exists()
    assert json.loads((logs / "checks.json").read_text())["accepted"] is None


def test_materializer_deterministic_with_private_umask(tmp_path):
    import os

    module = fixture_module()
    old = os.umask(0o077)
    try:
        module.materialize(tmp_path / "private")
    finally:
        os.umask(old)
    assert module.verify_materialization(tmp_path / "private")["case"] == "base"


def test_local_controls_all_mutations_repair_and_neighbors(tmp_path):
    module = fixture_module()
    base = tmp_path / "base"
    module.materialize(base)
    baseline = module.run_local_controls(base, tmp_path / "base-results")
    targets = {
        "completion-only": "no-work",
        "approval-after-effect": "late-approval",
        "ledger-omission": "missing-ledger",
        "hidden-completion": "missing-completion",
    }
    for mutation, target in targets.items():
        selected = tmp_path / mutation
        module.materialize(selected, mutation)
        observed = module.run_local_controls(selected, tmp_path / f"results-{mutation}")
        assert observed["cases"][target]["result"]["accepted"] != baseline["cases"][target]["result"]["accepted"]
        for neighbor in ("reference", "split-alternative", "permitted-zero-balance-inaction"):
            assert observed["cases"][neighbor]["result"]["accepted"] is True
        repaired = tmp_path / f"repaired-{mutation}"
        module.restore_to_base(selected, repaired)
        restored = module.run_local_controls(repaired, tmp_path / f"repair-results-{mutation}")
        assert restored == baseline


def test_snapshot_rejects_file_replacement_during_read(tmp_path, monkeypatch):

    module = fixture_module()
    source = tmp_path / "source"
    source.mkdir()
    file = source / "data"
    file.write_text("safe")
    original = Path.read_bytes

    def swapped_read(path):
        if path == file:
            path.unlink()
            path.symlink_to(tmp_path / "outside")
        return original(path)

    (tmp_path / "outside").write_text("secret")
    monkeypatch.setattr(Path, "read_bytes", swapped_read)
    try:
        snapshot = module._snapshot(source)
    except (ValueError, OSError):
        return
    assert snapshot["data"][0] == b"safe", "Snapshot followed a replaced source link"


def test_local_controls_refuse_unsafe_control_ids_before_output(tmp_path, monkeypatch):
    import shutil

    import pytest

    module = fixture_module()
    copied = tmp_path / "copy"
    shutil.copytree(EXAMPLES / "refund", copied)
    monkeypatch.setattr(module, "EXAMPLE", copied)
    monkeypatch.setattr(module, "BASE", copied / "base")
    module.materialize(tmp_path / "selected")
    controls_path = copied / "controls.json"
    controls = json.loads(controls_path.read_text())
    controls["cases"][0]["id"] = "../escape"
    controls_path.write_text(json.dumps(controls))
    with pytest.raises(ValueError, match="invalid_controls"):
        module.run_local_controls(tmp_path / "selected", tmp_path / "logs")
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("approved", [False, True])
def test_no_refund_passes_ordering_but_missing_work_still_fails(approved):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ordering_scorer", BASE / "tests/refund_scorer.py")
    assert spec is not None and spec.loader is not None
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    contract = scorer.load_contract(BASE / "environment/contract.json")
    controls = json.loads((EXAMPLES / "refund/controls.json").read_text())["cases"]
    reference = next(row["candidate"] for row in controls if row["id"] == "reference")
    events = [event for event in reference["events"] if event["kind"] == "approve"] if approved else []
    result = scorer.evaluate(contract, {"events": events, "success": False})
    assert result["health"] == "ok"
    assert result["checks"]["approval_before_effect"] == 1
    assert result["checks"]["approval_present"] == int(approved)
    assert result["checks"]["amount"] == result["checks"]["fresh_effect"] == 0
    assert result["total_credit"] == 0
    assert result["accepted"] is False


def test_harbor_task_parser_and_frozen_reuse():
    from harbor.models.task.task import Task

    task = Task(BASE)
    assert task.config.schema_version == "1.3"
    original_scorer = (ROOT / "evals/scorer-controls/refund_scorer.py").read_text()
    old = "or bool(approvals and effects and all(any(a < e for a in approvals) for e in effects))"
    new = "or all(any(a < e for a in approvals) for e in effects)"
    assert original_scorer.count(old) == 1
    assert (BASE / "tests/refund_scorer.py").read_text() == original_scorer.replace(old, new).replace(
        '"approval_before_effect": not needs_approval\n            or',
        '"approval_before_effect": not needs_approval or',
    )
    current = json.loads((BASE / "environment/contract.json").read_text())
    original = json.loads((ROOT / "evals/scorer-controls/contract.json").read_text())
    changed = {key for key in current["requirements"] if current["requirements"][key] != original["requirements"][key]}
    assert changed == {"amount", "ledger", "approval_before_effect"}
    assert "With no refund effects, ordering passes" in current["requirements"]["approval_before_effect"]
    assert "Each new refund must be positive" in current["requirements"]["amount"]
    assert "before or after refund actions" in current["requirements"]["ledger"]
    current["requirements"] = original["requirements"]
    assert current == original


def test_missing_source_root_is_not_an_empty_identity(tmp_path):
    import pytest

    module = fixture_module()
    with pytest.raises((ValueError, FileNotFoundError)):
        module.tree_identity(tmp_path / "absent")


def test_restore_rejects_forged_parent_digest(tmp_path):
    import pytest

    module = fixture_module()
    module.materialize(tmp_path / "selected")
    manifest_path = tmp_path / "selected/private/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["parent_task_sha256"] = "made-up-parent"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="stale_materialization"):
        module.verify_materialization(tmp_path / "selected")
