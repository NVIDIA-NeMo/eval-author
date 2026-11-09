# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Credential-free gate controls; synthetic binaries are never executed."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/verify_live_runtime.py"


def test_missing_manifest_fails_closed_without_private_output(tmp_path):
    assert GATE.is_file(), "permanent precredential native gate is missing"
    output = tmp_path / "private"
    result = subprocess.run(
        [sys.executable, str(GATE), "--binary-manifest", str(tmp_path / "secret-missing"), "--output", str(output)],
        capture_output=True,
        timeout=55,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["reason"] == "wrapper_failed"
    assert summary["help_checks"] == 0
    assert summary["positive_children"] == summary["negative_children"] == 0
    assert result.stderr == b""
    assert b"secret-missing" not in result.stdout
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "wrapper.stdout").stat().st_mode & 0o777 == 0o600
    assert (output / "wrapper.stderr").stat().st_mode & 0o777 == 0o600


def test_real_pinned_gate_proves_children_without_executing_binary(tmp_path):
    pytest.importorskip("skillevaluator", reason="requires installed pinned native packages")
    # A manifest-only fixture: execution would create a sentinel and fail.
    binary = tmp_path / "opencode"
    sentinel = tmp_path / "binary-executed"
    binary.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n")
    binary.chmod(0o700)
    manifest = tmp_path / "manifest.json"
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
    output = tmp_path / "private"
    result = subprocess.run(
        [sys.executable, str(GATE), "--binary-manifest", str(manifest), "--output", str(output)],
        capture_output=True,
        timeout=55,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "INFERENCE_HUB_API_KEY": "synthetic-canary"},
    )
    summary = json.loads(result.stdout)
    assert result.returncode == 0, summary
    assert summary["reason"] == "passed"
    assert summary["help_checks"] == 1
    assert summary["positive_children"] == 2
    assert summary["negative_children"] == 6
    assert set(summary) == {"reason", "help_checks", "positive_children", "negative_children", "elapsed_seconds"}
    assert not sentinel.exists()
    assert result.stderr == b""
    for path in output.glob("*.stdout"):
        assert path.stat().st_mode & 0o777 == 0o600
    for path in output.glob("*.stderr"):
        assert path.stat().st_mode & 0o777 == 0o600


def test_shared_relocation_runs_gate_before_preparation_and_credentials():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())
    commands = []
    for name in ("preparation-timing", "evaluate"):
        steps = workflow["jobs"][name]["steps"]
        index, relocation = next(
            (index, step) for index, step in enumerate(steps) if "tools/live_shard.py relocate " in step.get("run", "")
        )
        # The phase's actual relocation/gate argv are exercised in test_live_shard.py.
        assert relocation["run"].strip() == (
            'sudo python3 tools/live_shard.py relocate --root "$RUNNER_TEMP/live-shard" '
            '--download "$RUNNER_TEMP/downloaded-opencode"'
        )
        assert relocation["timeout-minutes"] == 1
        assert "continue-on-error" not in relocation
        assert all("secrets." not in json.dumps(step) for step in steps[: index + 1])
        preparation = next(i for i, step in enumerate(steps) if "tools/live_shard.py prepare " in step.get("run", ""))
        assert preparation > index
        assert all("secrets." not in json.dumps(step) for step in steps[: preparation + 1])
        commands.append(relocation["run"])
    assert commands[0] == commands[1]


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("verify_live_runtime", GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_private_directory_is_not_modified(gate, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    assert gate.verify(tmp_path / "manifest", output)["reason"] == "internal_failure"
    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    "counts",
    [
        {"positive_children": 2.0, "negative_children": 6},
        {"positive_children": 2, "negative_children": 5},
        {"positive_children": 2, "negative_children": 6, "private": "synthetic-canary"},
    ],
)
def test_fake_child_receipts_cannot_claim_completion(gate, tmp_path, monkeypatch, counts):
    # Fake subprocess boundary only: production must validate actual worker counters.
    def fake_run(argv, env, output, label, deadline):
        if label == "children":
            (output / "children.stdout").write_text(json.dumps(counts))
        return 0

    monkeypatch.setattr(gate, "run_private", fake_run)
    summary = gate.verify(tmp_path / "manifest", tmp_path / "private")
    assert summary["reason"] == "children_failed"
    assert summary["positive_children"] == summary["negative_children"] == 0
    assert "synthetic-canary" not in json.dumps(summary)


def test_fake_boundary_exception_never_publishes_its_message(gate, tmp_path, monkeypatch):
    def fake_run(*args, **kwargs):
        raise gate.GateFailure("synthetic-canary")

    monkeypatch.setattr(gate, "run_private", fake_run)
    summary = gate.verify(tmp_path / "manifest", tmp_path / "private")
    assert summary["reason"] == "internal_failure"
    assert "synthetic-canary" not in json.dumps(summary)


def test_private_output_rejects_symlink_ancestors(gate, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert gate.verify(tmp_path / "manifest", link / "private")["reason"] == "internal_failure"
    assert list(target.iterdir()) == []


def test_fake_launch_boundary_receives_only_private_allowlist_and_unchanged_deadline(gate, tmp_path, monkeypatch):
    for key in (
        "INFERENCE_HUB_API_KEY",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "EA_REQUIRE_PREPARED_IMAGES",
        "EA_PREPARED_IMAGES_MANIFEST",
        "_EA_LIVE_FAILURE_PATH",
        "_EA_LIVE_FAILURE_TOKEN",
        "UNKNOWN_CANARY",
    ):
        monkeypatch.setenv(key, "synthetic-canary")
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", "123456.789")
    calls = []

    def fake_run(argv, env, output, label, deadline):
        calls.append((argv, env.copy(), deadline))
        if label == "children":
            (output / "children.stdout").write_text('{"positive_children":2,"negative_children":6}')
        return 0

    monkeypatch.setattr(gate, "run_private", fake_run)
    output = tmp_path / "private"
    manifest = tmp_path / "manifest"
    assert gate.verify(manifest, output)["reason"] == "passed"
    assert calls[0][0] == [
        sys.executable,
        str(ROOT / "tools/run_pinned_live_evaluator.py"),
        "tier3",
        "evaluate",
        "--help",
    ]
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert calls[0][2] == calls[1][2]
    env = calls[0][1]
    assert set(env) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PYTHON_DOTENV_DISABLED",
        "HARBOR_TELEMETRY",
        "EA_REQUIRE_PATCHED_OPENCODE",
        "EA_PATCHED_OPENCODE_MANIFEST",
        "EA_LIVE_JOB_DEADLINE",
    }
    assert env["EA_LIVE_JOB_DEADLINE"] == "123456.789"
    assert env["EA_REQUIRE_PATCHED_OPENCODE"] == "1"
    assert env["EA_PATCHED_OPENCODE_MANIFEST"] == str(manifest)
    assert env["HOME"] == str(output / "home")
    assert env["TMPDIR"] == str(output / "tmp")
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["HARBOR_TELEMETRY"] == "0"
    assert "synthetic-canary" not in json.dumps(calls)


def test_real_subprocess_timeout_is_bounded_and_keeps_raw_output_private(gate, tmp_path):
    env = gate.environment(tmp_path, tmp_path / "manifest")
    started = time.monotonic()
    with pytest.raises(gate.GateFailure, match="^timeout$"):
        gate.run_private(
            [sys.executable, "-c", "import time; print('synthetic-private-output', flush=True); time.sleep(10)"],
            env,
            tmp_path,
            "bounded",
            started + 0.5,
        )
    assert time.monotonic() - started < 3
    assert (tmp_path / "bounded.stdout").read_bytes() == b"synthetic-private-output\n"
    assert (tmp_path / "bounded.stdout").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("process_group", [False, True])
def test_real_timeout_contains_grandchild_only_with_process_group(gate, tmp_path, process_group):
    env = gate.environment(tmp_path, tmp_path / "manifest")
    child = """
import os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
(root / 'grandchild-ready').write_text(str(os.getpgrp()))
deadline = time.monotonic() + 10
while not (root / 'parent-reaped').exists():
    if time.monotonic() >= deadline:
        sys.exit(0)
    time.sleep(0.01)
(root / 'escaped').write_text('grandchild survived')
"""
    parent = """
import os, subprocess, sys
from pathlib import Path
(Path(sys.argv[2]) / 'parent-pid').write_text(str(os.getpid()))
subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]).wait()
"""
    with pytest.raises(gate.GateFailure, match="^timeout$"):
        gate.run_private(
            [sys.executable, "-c", parent, child, str(tmp_path)],
            env,
            tmp_path,
            "grandchild",
            time.monotonic() + 3,
            process_group=process_group,
        )
    try:
        assert (tmp_path / "grandchild-ready").is_file(), "timeout preceded the grandchild launch"
        if process_group:
            assert (tmp_path / "grandchild-ready").read_text() == (tmp_path / "parent-pid").read_text()
    finally:
        (tmp_path / "parent-reaped").write_text("release self-expiring negative control")
    deadline = time.monotonic() + 2
    while not (tmp_path / "escaped").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (tmp_path / "escaped").exists() is not process_group


@pytest.mark.parametrize("deadline", ["0", "nan", "inf", "synthetic-canary", "1000000000000"])
def test_live_deadline_controls_wrapper_dispatch(monkeypatch, deadline):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("deadline_wrapper", ROOT / "tools/run_pinned_live_evaluator.py")
    assert spec is not None and spec.loader is not None
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", deadline)
    for key in ("_EA_LIVE_FAILURE_PATH", "_EA_LIVE_FAILURE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(wrapper, "configure_runtime", lambda: calls.append("configure"))
    monkeypatch.setattr(wrapper, "install_failure_channel", lambda: calls.append("channel"))

    class CLI:
        @staticmethod
        def cli():
            calls.append("cli")

    def import_cli(name):
        assert name == "skillevaluator.cli"
        return CLI

    monkeypatch.setattr(wrapper, "import_module", import_cli)
    if deadline == "1000000000000":
        wrapper.main()
        assert calls == ["configure", "channel", "cli"]
    else:
        with pytest.raises(ValueError if deadline == "synthetic-canary" else TimeoutError):
            wrapper.main()
        assert calls == []


@pytest.mark.parametrize("reason, expected_code", [("timeout", 124), ("children_failed", 1), (None, 1)])
def test_worker_failure_keeps_exception_private_even_when_called_directly(
    gate, tmp_path, monkeypatch, capsys, reason, expected_code
):
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(GATE),
            "--binary-manifest",
            str(tmp_path / "manifest"),
            "--output",
            str(output),
            "--probe-children",
            str(time.monotonic() + 10),
        ],
    )

    def broken_probe(*args):
        if reason is not None:
            raise gate.GateFailure(reason)
        raise RuntimeError("synthetic-private-exception")

    monkeypatch.setattr(gate, "probe_children", broken_probe)
    assert gate.main() == expected_code
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    raw = output / "probe-failure.stderr"
    assert (reason or "synthetic-private-exception") in raw.read_text()
    assert raw.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("code, reason", [(124, "timeout"), (1, "children_failed"), (78, "children_failed")])
def test_parent_classifies_worker_timeout_before_reading_receipt(gate, tmp_path, monkeypatch, code, reason):
    def fake_run(argv, env, output, label, deadline):
        return code if label == "children" else 0

    monkeypatch.setattr(gate, "run_private", fake_run)
    output = tmp_path / "private"
    summary = gate.verify(tmp_path / "manifest", output)
    assert summary["reason"] == reason
    assert summary["help_checks"] == 1
    assert summary["positive_children"] == summary["negative_children"] == 0
    assert not (output / "children.stdout").exists()
