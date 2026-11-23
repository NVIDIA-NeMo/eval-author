# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.models.task.config import TaskConfig

TASK = Path(__file__).resolve().parents[1] / "skills/eval-author-trace-environment/examples/workplace/task"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TASK / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed():
    return json.loads((TASK / "seed.json").read_text())


@pytest.fixture
def correct(seed):
    return seed + [
        {
            "email_id": "generated-id",
            "inbox/outbox": "outbox",
            "sender/recipient": "alex@example.test",
            "subject": "RE: Schedule confirmation",
            "sent_datetime": "2023-11-30 23:59:00",
            "body": "Confirmed for Tuesday at 10:00.",
        }
    ]


def envelope(rows):
    return {
        "access_mode": "stateful_simulation",
        "initial_state_basis": "reconstructed",
        "state_basis": "observed",
        "closed": True,
        "initial_state": [],
        "state": rows,
    }


def test_grader_accepts_state_without_action_trace(correct):
    grader = load("grade")
    assert grader.grade(envelope(correct)) == 1
    assert grader.grade(envelope(list(reversed(correct)))) == 1
    correct[-1]["email_id"] = "arbitrary-fresh-id"
    assert grader.grade(envelope(correct)) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "noop",
        "wrong-recipient",
        "wrong-body",
        "wrong-subject",
        "wrong-date",
        "wrong-folder",
        "duplicate",
        "extra-reply",
        "original-change",
        "original-delete",
        "original-id-change",
    ],
)
def test_grader_rejects_wrong_final_state(correct, mutation):
    if mutation == "noop":
        correct.pop()
    elif mutation in {"duplicate", "extra-reply"}:
        row = copy.deepcopy(correct[-1])
        row["email_id"] = "another-id"
        if mutation == "extra-reply":
            row["sender/recipient"] = "billing@example.test"
        correct.append(row)
    elif mutation == "original-change":
        correct[1]["body"] = "Changed"
    elif mutation == "original-delete":
        correct.pop(1)
    elif mutation == "original-id-change":
        correct[1]["email_id"] = "changed"
    else:
        field = {
            "wrong-recipient": "sender/recipient",
            "wrong-body": "body",
            "wrong-subject": "subject",
            "wrong-date": "sent_datetime",
            "wrong-folder": "inbox/outbox",
        }[mutation]
        correct[-1][field] = "wrong"
    assert load("grade").grade(envelope(correct)) == 0


@pytest.mark.parametrize("bad", [None, {}, {"emails": None}, [{"email_id": "1"}], "invalid"])
def test_corrupt_snapshot_is_infrastructure_failure(bad):
    grader = load("grade")
    with pytest.raises(grader.SnapshotError):
        grader.grade(bad)


@pytest.mark.parametrize(
    "kind",
    [
        "bare",
        "emails",
        "old",
        "extra",
        "missing-basis",
        "missing-initial-basis",
        "unclosed",
        "numeric-closed",
        "reconstructed-final",
        "wrong-mode",
        "bad-state",
        "bad-initial",
    ],
)
def test_snapshot_requires_exact_observed_envelope(correct, kind):
    grader = load("grade")
    snapshot = envelope(correct)
    if kind == "bare":
        snapshot = correct
    elif kind == "emails":
        snapshot = {"emails": correct}
    elif kind == "old":
        snapshot.pop("initial_state_basis")
        snapshot["state_basis"] = "reconstructed"
    elif kind == "extra":
        snapshot["unknown"] = True
    elif kind == "missing-basis":
        snapshot.pop("state_basis")
    elif kind == "missing-initial-basis":
        snapshot.pop("initial_state_basis")
    elif kind == "unclosed":
        snapshot["closed"] = False
    elif kind == "numeric-closed":
        snapshot["closed"] = 1
    elif kind == "reconstructed-final":
        snapshot["state_basis"] = "reconstructed"
    elif kind == "wrong-mode":
        snapshot["access_mode"] = "replay"
    elif kind == "bad-state":
        snapshot["state"] = [{"email_id": "1"}]
    else:
        snapshot["initial_state"] = None
    with pytest.raises(grader.SnapshotError):
        grader.grade(snapshot)


@pytest.mark.parametrize(
    "payload", ['{"state":[],"state":[]}', '{"nested":{"key":1,"key":2}}', "NaN", "Infinity", "-Infinity", "1e999"]
)
def test_strict_json_rejects_duplicate_and_nonfinite_values(payload):
    grader = load("grade")
    with pytest.raises(grader.SnapshotError):
        grader.parse_json(payload)


def test_trusted_seed_uses_row_validator(seed):
    grader = load("grade")
    assert grader.validate_rows(grader.parse_json((TASK / "seed.json").read_text())) == seed
    with pytest.raises(grader.SnapshotError):
        grader.rows_from_snapshot(seed)


def test_closed_export_uses_trusted_seed(correct):
    grader = load("grade")
    exported = envelope(correct)
    assert grader.grade(exported) == 1
    correct[1]["body"] = "Mutated original"
    exported["initial_state"] = copy.deepcopy(correct[:-1])
    assert grader.grade(exported) == 0
    exported["closed"] = False
    with pytest.raises(grader.SnapshotError):
        grader.grade(exported)


def test_cli_writes_fresh_reward(tmp_path, correct):
    snapshot = tmp_path / "state.json"
    snapshot.write_text(json.dumps(envelope(correct)))
    rewards = tmp_path / "logs"
    result = subprocess.run(
        [sys.executable, str(TASK / "grade.py"), "--state", str(snapshot), "--reward-dir", str(rewards)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (rewards / "reward.txt").read_text() == "1\n"
    snapshot.write_text(json.dumps(envelope(correct[:-1])))
    result = subprocess.run(
        [sys.executable, str(TASK / "grade.py"), "--state", str(snapshot), "--reward-dir", str(rewards)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (rewards / "reward.txt").read_text() == "0\n"


def test_duplicate_ids_and_nonstring_fields_are_corrupt(correct):
    grader = load("grade")
    correct[-1]["email_id"] = "1"
    with pytest.raises(grader.SnapshotError):
        grader.grade(envelope(correct))
    correct[-1]["email_id"] = 4
    with pytest.raises(grader.SnapshotError):
        grader.grade(envelope(correct))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "{",
        '{"emails":null}',
        json.dumps(envelope([])).replace('"closed": true', '"closed": false, "closed": true'),
        json.dumps(envelope([])).replace('"initial_state": []', '"initial_state": NaN'),
    ],
)
def test_cli_export_failure_clears_rewards(tmp_path, payload):
    snapshot = tmp_path / "state.json"
    if payload is not None:
        snapshot.write_text(payload)
    rewards = tmp_path / "logs"
    rewards.mkdir()
    for name in ("reward.txt", "reward.json"):
        (rewards / name).write_text("1")
    result = subprocess.run(
        [sys.executable, str(TASK / "grade.py"), "--state", str(snapshot), "--reward-dir", str(rewards)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "infrastructure" in result.stderr.lower()
    assert not (rewards / "reward.txt").exists()
    assert not (rewards / "reward.json").exists()


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "service.py").write_text("import backend\n")
    (root / "backend.py").write_text("VALUE = 1\n")
    (root / "client.py").write_text("import public\n")
    (root / "mcp_server.py").write_text("import public\n")
    (root / "public.py").write_text("VALUE = 2\n")
    (root / "unrelated.py").write_text("VALUE = 3\n")
    return root


def test_generator_harbor_models_and_isolation(tmp_path, runtime):
    build = load("build")
    output = tmp_path / "task"
    build.build(output, runtime_dir=runtime)
    config = TaskConfig.model_validate_toml((output / "task.toml").read_text())
    assert config.environment.network_mode == "no-network"
    assert config.agent.user == "solver"
    assert config.verifier.environment_mode == "separate"
    assert config.verifier.network_mode == "no-network"
    assert config.verifier.environment is not None
    assert config.verifier.environment.network_mode == "no-network"
    assert config.verifier.collect[0].service == "workplace"
    assert "service.py export --state-dir /state --output /export/state.json" in config.verifier.collect[0].command
    assert not isinstance(config.artifacts[0], str)
    assert config.artifacts[0].service == "workplace"
    assert config.artifacts[0].source == "/export/state.json"
    main = output / "environment" / "main"
    assert {p.name for p in main.glob("*.py")} == {"client.py", "mcp_server.py", "public.py"}
    assert not (main / "seed.json").exists()
    sidecar = output / "environment" / "workplace"
    assert {p.name for p in sidecar.glob("*.py")} == {"service.py", "backend.py"}
    assert (sidecar / "seed.json").read_bytes() == (TASK / "seed.json").read_bytes()
    assert (output / "tests" / "seed.json").read_bytes() == (TASK / "seed.json").read_bytes()
    compose = (output / "environment" / "docker-compose.yaml").read_text()
    assert "volumes:" not in compose
    assert "docker.sock" not in compose
    assert "ports:" not in compose
    assert "expose:" not in compose


@pytest.mark.parametrize(
    ("network_mode", "expected_url"),
    [("no-network", "http://127.0.0.1:8080"), ("public", "http://workplace:8080")],
)
def test_generator_topology_urls(tmp_path, runtime, network_mode, expected_url):
    builder = load("build")
    for control in builder.CONTROLS:
        output = tmp_path / control
        builder.build(output, control=control, runtime_dir=runtime, network_mode=network_mode)
        config = TaskConfig.model_validate_toml((output / "task.toml").read_text())
        assert config.environment.env["WORKPLACE_URL"] == expected_url
        mcp = config.environment.mcp_servers[0]
        assert mcp.name == "workplace-stateful-simulation"
        assert mcp.args == ["/opt/workplace/mcp_server.py", "--url", expected_url]
        assert expected_url in (output / "instruction.md").read_text()
        script = (output / "solution" / "solve.sh").read_text()
        if control != "noop":
            assert expected_url in script
        assert "@WORKPLACE_URL@" not in script
        compose = (output / "environment" / "docker-compose.yaml").read_text()
        assert "expose:" not in compose
        assert "ports:" not in compose


def test_variants_change_only_solution(tmp_path, runtime):
    build = load("build")
    controls = json.loads((TASK / "controls" / "expected.json").read_text())
    assert controls == {
        "reference": 1,
        "alternative": 1,
        "noop": 0,
        "wrong-body": 0,
        "extra-reply": 0,
        "wrong-id": 0,
        "duplicate-reply": 0,
    }
    manifests = []
    solutions = []
    for control in controls:
        output = tmp_path / control
        build.build(output, control=control, runtime_dir=runtime)
        manifests.append(
            {
                str(p.relative_to(output)): p.read_bytes()
                for p in output.rglob("*")
                if p.is_file() and "solution" not in p.parts
            }
        )
        solutions.append((output / "solution" / "solve.sh").read_text())
    assert all(m == manifests[0] for m in manifests)
    assert len(set(solutions)) == len(controls)


def test_generator_rejects_nonempty_destination(tmp_path, runtime):
    build = load("build")
    with pytest.raises(FileExistsError):
        build.build(runtime, runtime_dir=runtime)
