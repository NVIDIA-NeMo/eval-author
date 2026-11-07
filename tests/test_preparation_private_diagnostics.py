# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monkeypatch.delenv("EA_LIVE_JOB_DEADLINE", raising=False)
    return importlib.import_module("patched_opencode_runtime")


@pytest.fixture
def paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return source, tmp_path / "prepared", tmp_path / "binary.json", tmp_path / "private.json"


def fake_docker(tmp_path, monkeypatch, body):
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))


def test_nonzero_retained_before_typed_failure_and_public_redacted(runtime, paths, tmp_path, monkeypatch, capsys):
    source, output, binary, private = paths
    checkpoint = tmp_path / "timing.json"
    fake_docker(
        tmp_path,
        monkeypatch,
        "import sys\nprint('PRIVATE stdout ::error:: synthetic')\n"
        "print('PRIVATE stderr synthetic', file=sys.stderr)\nsys.exit(17)\n",
    )
    argv = ["build", "ARGV_ONLY_PRIVATE"]
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(argv))
    original_event = runtime._timing_event
    observed = []

    def event(name, *args):
        if name == "operation_failed":
            observed.append(json.loads(private.read_text()))
        original_event(name, *args)

    monkeypatch.setattr(runtime, "_timing_event", event)
    with pytest.raises(runtime.PreparationError) as caught:
        runtime.prepare_images(source, output, binary, private_diagnostics=private, timing_checkpoint=checkpoint)
    record = json.loads(private.read_text())
    assert observed == [record]
    assert record == {
        "schema_version": 1,
        "category": "docker_failed",
        "phase": "budget",
        "operation": "build",
        "exit_code": 17,
        "stdout": "PRIVATE stdout ::error:: synthetic\n",
        "stderr": "PRIVATE stderr synthetic\n",
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    assert private.stat().st_mode & 0o777 == 0o600
    public = checkpoint.read_text() + "".join(traceback.format_exception(caught.value)) + str(capsys.readouterr())
    assert "PRIVATE" not in public and "::error::" not in public
    assert "ARGV_ONLY_PRIVATE" not in private.read_text()
    assert runtime.preparation_failure(caught.value) == dict(
        category="docker_failed", phase="budget", operation="build"
    )
    assert not (output / "prepared-images.json").exists()


@pytest.mark.parametrize("failure", ["exit", "timeout_bytes", "timeout_str"])
def test_large_failure_outputs_are_bounded(runtime, paths, monkeypatch, failure):
    source, output, binary, private = paths
    content = "\x00雪" * 100000
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        if failure.startswith("timeout"):
            value = content.encode() + b"\xff" if failure == "timeout_bytes" else content
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=value, stderr=value)
        return subprocess.CompletedProcess(argv, 7, content, content)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["run"], timeout=17))
    with pytest.raises(runtime.PreparationError) as caught:
        runtime.prepare_images(source, output, binary, private_diagnostics=private)
    record = json.loads(private.read_text())
    assert record["exit_code"] == (7 if failure == "exit" else None)
    assert record["category"] == ("docker_failed" if failure == "exit" else "deadline_exceeded")
    assert isinstance(caught.value, runtime.PreparationTimeoutError) == failure.startswith("timeout")
    for stream in ("stdout", "stderr"):
        assert record[stream] and len(record[stream].encode()) <= 16384
        assert record[stream + "_truncated"] is True
    assert private.stat().st_size <= 200000
    assert calls[0]["timeout"] == 17
    assert calls[0]["capture_output"] is True
    assert runtime._PREPARATION_PRIVATE_DIAGNOSTICS.get() is None


def test_real_timeout_partial_streams_are_retained(runtime, paths, tmp_path, monkeypatch):
    source, output, binary, private = paths
    fake_docker(
        tmp_path,
        monkeypatch,
        "import sys,time\nprint('PRIVATE partial', flush=True)\n"
        "print('PRIVATE error', file=sys.stderr, flush=True)\ntime.sleep(5)\n",
    )
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["run"], timeout=1))
    with pytest.raises(runtime.PreparationTimeoutError):
        runtime.prepare_images(source, output, binary, private_diagnostics=private)
    record = json.loads(private.read_text())
    assert record["stdout"] == "PRIVATE partial\n"
    assert record["stderr"] == "PRIVATE error\n"
    assert record["exit_code"] is None
    assert record["stdout_truncated"] is record["stderr_truncated"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "file",
        "directory",
        "symlink",
        "dangling",
        "ancestor",
        "dotdot",
        "source",
        "repository",
        "missing_parent",
        "timing",
    ],
)
def test_private_destination_rejected_before_preparation(runtime, paths, tmp_path, monkeypatch, attack):
    source, output, binary, private = paths
    target = tmp_path / "untouched"
    target.write_text("sentinel")
    timing = None
    if attack == "file":
        private.write_text("sentinel")
    elif attack == "directory":
        private.mkdir()
    elif attack in ("symlink", "dangling"):
        private.symlink_to(target if attack == "symlink" else tmp_path / "absent")
    elif attack in ("ancestor", "dotdot"):
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        private = alias / ("../escaped.json" if attack == "dotdot" else "private.json")
    elif attack == "source":
        private = source / "private.json"
    elif attack == "repository":
        private = ROOT / "private-diagnostics-must-not-create.json"
    elif attack == "missing_parent":
        private = tmp_path / "missing/private.json"
    elif attack == "timing":
        timing = private
    calls = []
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: calls.append("started"))
    with pytest.raises(runtime.PreparationError):
        runtime.prepare_images(source, output, binary, private_diagnostics=private, timing_checkpoint=timing)
    assert calls == []
    assert target.read_text() == "sentinel"
    assert not output.exists()


@pytest.mark.parametrize("attack", ["file", "symlink", "ancestor", "write_error"])
def test_write_failure_preserves_original_docker_error(runtime, paths, tmp_path, monkeypatch, attack):
    source, output, binary, _ = paths
    parent = tmp_path / "private-dir"
    parent.mkdir()
    private = parent / "failure.json"
    target = tmp_path / "untouched"
    target.write_text("sentinel")
    original_open = os.open

    def run(argv, **kwargs):
        if attack == "file":
            private.write_text("sentinel")
        elif attack == "symlink":
            private.symlink_to(target)
        elif attack == "ancestor":
            parent.rename(tmp_path / "moved")
            parent.symlink_to(source, target_is_directory=True)
        else:

            def fail_open(path, flags, *args, **kw):
                if flags & os.O_CREAT:
                    raise OSError("PRIVATE write error")
                return original_open(path, flags, *args, **kw)

            monkeypatch.setattr(runtime.os, "open", fail_open)
        return subprocess.CompletedProcess(argv, 17, "PRIVATE stdout", "PRIVATE stderr")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["run"]))
    with pytest.raises(runtime.PreparationError) as caught:
        runtime.prepare_images(source, output, binary, private_diagnostics=private)
    assert runtime.preparation_failure(caught.value) == dict(category="docker_failed", phase="budget", operation="run")
    assert "PRIVATE" not in str(caught.value)
    assert target.read_text() == "sentinel"
    assert not (source / "failure.json").exists()
    if attack == "file":
        assert private.read_text() == "sentinel"


def test_success_and_default_calls_do_not_create_private_file(runtime, paths, monkeypatch):
    source, output, binary, private = paths
    monkeypatch.setattr(runtime.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "ok", ""))
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["info"]))
    assert runtime.prepare_images(source, output, binary, private_diagnostics=private) == "ok"
    assert not private.exists()
    assert runtime.prepare_images(source, output, binary) == "ok"
    assert runtime._PREPARATION_PRIVATE_DIAGNOSTICS.get() is None


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_forwards_opt_in_and_keeps_default_signature(runtime, paths, monkeypatch, enabled, capsys):
    source, output, binary, private = paths
    cli = importlib.import_module("prepare_live_images")
    monkeypatch.setattr(os, "environ", {})
    argv = ["prepare_live_images.py", "--skill", str(source), "--output", str(output), "--binary-manifest", str(binary)]
    if enabled:
        argv += ["--private-diagnostics", str(private)]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []

    def prepare(skill, out, manifest, *, timing_checkpoint=None, **kwargs):
        calls.append((skill, out, manifest, timing_checkpoint, kwargs))

    monkeypatch.setattr(cli, "prepare_images", prepare)
    cli.main()
    assert calls == [(source, output, binary, None, {"private_diagnostics": private} if enabled else {})]
    assert capsys.readouterr().out == "prepared trial images verified\n"


def test_cli_retains_real_docker_failure_without_public_output(runtime, paths, tmp_path, monkeypatch, capsys):
    source, output, binary, private = paths
    cli = importlib.import_module("prepare_live_images")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    fake_docker(
        tmp_path,
        monkeypatch,
        "import sys\nprint('PRIVATE stdout')\nprint('PRIVATE stderr', file=sys.stderr)\nsys.exit(23)\n",
    )
    monkeypatch.setenv("SYNTHETIC_SECRET", "not-forwarded")
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["build"]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_live_images.py",
            "--skill",
            str(source),
            "--output",
            str(output),
            "--binary-manifest",
            str(binary),
            "--private-diagnostics",
            str(private),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    assert json.loads(private.read_text())["exit_code"] == 23
    console = capsys.readouterr()
    assert console.out == ""
    assert (
        console.err
        == "image preparation failed: category=docker_failed phase=budget operation=build; inference was not started\n"
    )
    assert "SYNTHETIC_SECRET" not in os.environ


def test_outer_preparation_aborts_first_failed_image(runtime, paths, tmp_path, monkeypatch):
    source, output, binary, private = paths
    tasks = []
    for number in range(2):
        task = tmp_path / f"task-{number}"
        (task / "environment").mkdir(parents=True)
        (task / "environment/Dockerfile").write_text("FROM python:3.12-slim\n")
        tasks.append(task)
    monkeypatch.setattr(runtime, "verify_contract", lambda: None)
    monkeypatch.setattr(runtime, "load_manifest", lambda p: {"sha256": "a" * 64})
    monkeypatch.setattr(runtime, "stage_preparation", lambda *a: {"with": tasks, "without": tasks})
    monkeypatch.setattr(runtime, "preparation_identity", lambda *a: "a" * 64)
    monkeypatch.setattr(runtime, "prepared_task_identity", lambda task, *a: ("b" if task == tasks[0] else "c") * 64)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        return (
            subprocess.CompletedProcess(argv, 0, "linux/arm64", "")
            if argv[1] == "info"
            else subprocess.CompletedProcess(argv, 9, "PRIVATE", "PRIVATE")
        )

    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(runtime.PreparationError) as caught:
        runtime.prepare_images(source, output, binary, private_diagnostics=private)
    assert calls == ["info", "build"]
    assert json.loads(private.read_text())["phase"] == "dependencies"
    assert runtime.preparation_failure(caught.value)["category"] == "docker_failed"
    assert not (output / "prepared-images.json").exists()
    assert not (output / ("c" * 64 + ".Dockerfile")).exists()


@pytest.mark.parametrize("umask", [0, 0o777])
def test_private_file_mode_does_not_depend_on_umask(runtime, paths, monkeypatch, umask):
    source, output, binary, private = paths
    monkeypatch.setattr(runtime.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""))
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["run"]))
    previous = os.umask(umask)
    try:
        with pytest.raises(runtime.PreparationError):
            runtime.prepare_images(source, output, binary, private_diagnostics=private)
    finally:
        os.umask(previous)
    assert private.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("value,expected", [(None, ""), (b"partial\xff", "partial�"), ("partial\ud800", "partial?")])
def test_timeout_empty_or_invalid_partial_output(runtime, paths, monkeypatch, value, expected):
    source, output, binary, private = paths

    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"], output=value, stderr=value)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "_prepare_images", lambda *a: runtime._docker(["run"]))
    with pytest.raises(runtime.PreparationTimeoutError):
        runtime.prepare_images(source, output, binary, private_diagnostics=private)
    record = json.loads(private.read_text())
    assert record["stdout"] == record["stderr"] == expected
    assert record["stdout_truncated"] is record["stderr_truncated"] is False
