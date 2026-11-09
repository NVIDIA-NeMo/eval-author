# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import build_live_shard_image as builder
    import tooling_build_diagnostics as diagnostics

    monkeypatch.setattr(builder, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(builder, "host_platform", lambda: "linux/arm64")
    return builder, diagnostics


@pytest.mark.parametrize("mode", ["success", "timeout", "cancelled", "failure"])
def test_actual_create_producer_through_yaml_projection(tmp_path, monkeypatch, modules, mode):
    builder, diagnostics = modules
    import live_shard

    monkeypatch.setattr(live_shard, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(live_shard, "create_boundary", lambda *a, **kw: None)
    monkeypatch.setattr(live_shard, "handoff_boundary", lambda *a: None)
    monkeypatch.setattr(live_shard, "build_image", builder.build_image)
    monkeypatch.setenv("EA_LIVE_JOB_DEADLINE", str(live_shard.time.monotonic() + 4500))
    monkeypatch.setattr(builder, "BUILD_SECONDS", 0.5 if mode == "timeout" else 180)
    root = tmp_path / "live-shard"
    image = "sha256:" + "a" * 64
    popen = subprocess.Popen
    calls = []

    def fake_docker(argv, **kwargs):
        calls.append(argv[3])
        if argv[3] == "build":
            checkpoint = json.loads((root / "control/image/build-diagnostics.json").read_text())
            assert checkpoint["status"] == "running" and checkpoint["phase"] == "build"
            if mode == "cancelled":
                raise KeyboardInterrupt("PRIVATE interruption")
            program = (
                "print('#7 0.10 EA_TOOLING_STAGE=apt_install', flush=True); print('PRIVATE sentinel', flush=True); "
            )
            if mode == "timeout":
                program += "import time; time.sleep(20)"
            elif mode == "failure":
                program += "exit(8)"
            else:
                identity = argv[argv.index("--iidfile") + 1]
                program += f"from pathlib import Path; Path({identity!r}).write_text({image!r})"
        else:
            assert argv[3:5] == ["image", "inspect"]
            program = f"print({json.dumps([dict(Id=image, Os='linux', Architecture='arm64')])!r})"
        process = popen([sys.executable, "-c", program], **kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_docker)
    args = live_shard.parse_args(["create", "--root", str(root), "--platform", "linux/arm64"])
    if mode == "success":
        assert live_shard.run_phase(args) == 0
    else:
        expected = {"timeout": subprocess.TimeoutExpired, "cancelled": KeyboardInterrupt, "failure": ValueError}[mode]
        with pytest.raises(expected):
            live_shard.run_phase(args)
    assert calls == (["build", "image"] if mode == "success" else ["build"])
    monkeypatch.setattr(subprocess, "Popen", popen)
    steps = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())["jobs"]["evaluate"][
        "steps"
    ]
    body = next(s["run"] for s in steps if "tooling_build_diagnostics.py" in s.get("run", ""))
    shim = tmp_path / "sudo"
    shim.write_text(f'#!/bin/sh\nshift\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", body],
        cwd=ROOT,
        env={
            "PATH": str(tmp_path) + os.pathsep + os.defpath,
            "RUNNER_TEMP": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    raw = (tmp_path / "skill-publish/tooling-build-diagnostics.json").read_text()
    value = json.loads(raw)
    assert (
        value["status"]
        == {"success": "complete", "timeout": "failed", "cancelled": "cancelled", "failure": "failed"}[mode]
    )
    assert (
        value["failure_category"]
        == {"success": "none", "timeout": "timeout", "cancelled": "cancelled", "failure": "command_failed"}[mode]
    )
    assert value["observed_stage"] == ("unknown" if mode == "cancelled" else "apt_install")
    assert value["build_seconds"] >= 0
    if mode == "success":
        assert value["inspect_seconds"] >= 0
    assert "PRIVATE" not in raw + result.stdout + result.stderr
    assert diagnostics.project(value) == value


@pytest.mark.parametrize(
    "case",
    ["symlink", "ancestor", "fifo", "directory", "oversize", "malformed", "duplicate", "nan", "bool", "enum", "extra"],
)
def test_invalid_source_is_replaced_by_fixed_safe_unavailable(tmp_path, modules, capsys, case):
    _, diagnostics = modules
    source = tmp_path / "source.json"
    value = diagnostics.empty()
    if case == "symlink":
        other = tmp_path / "other"
        other.write_text("PRIVATE")
        source.symlink_to(other)
    elif case == "ancestor":
        real = tmp_path / "real"
        real.mkdir()
        (real / "source.json").write_text(json.dumps(value))
        link = tmp_path / "link"
        link.symlink_to(real)
        source = link / "source.json"
    elif case == "fifo":
        os.mkfifo(source)
    elif case == "directory":
        source.mkdir()
    else:
        if case == "nan":
            value["elapsed_seconds"] = float("nan")
        elif case == "bool":
            value["exit_code"] = True
        elif case == "enum":
            value["phase"] = ["PRIVATE"]
        elif case == "extra":
            value["PRIVATE"] = "PRIVATE"
        source.write_text(
            "PRIVATE" * 4096
            if case == "oversize"
            else "{PRIVATE"
            if case == "malformed"
            else '{"a":1,"a":2}'
            if case == "duplicate"
            else json.dumps(value)
        )
    output = tmp_path / "public.json"
    diagnostics.publish(source, output)
    assert json.loads(output.read_text()) == diagnostics.empty("checkpoint_invalid")
    assert "PRIVATE" not in output.read_text() + capsys.readouterr().out


@pytest.mark.parametrize(
    "payload,stage,truncated",
    [
        (b"PRIVATE DNS timeout\n", "unknown", False),
        (b"#2 [3/4] RUN printf 'EA_TOOLING_STAGE=apt_index\\n'\n", "unknown", False),
        (b"EA_TOOLING_STAGE=apt_index\n" + b"PRIVATE\n" * 40000, "unknown", True),
        (b"PRIVATE\n" * 40000 + b"#8 2.34 EA_TOOLING_STAGE=versions\n", "versions", True),
        (b"EA_TOOLING_STAGE=apt_install", "unknown", False),
    ],
)
def test_scan_is_bounded_and_never_infers_remote_causes(tmp_path, modules, payload, stage, truncated):
    _, diagnostics = modules
    log = tmp_path / "build.log"
    log.write_bytes(payload)
    result = diagnostics.scan(log)
    assert result["scanned_bytes"] <= diagnostics.MAX_SCAN_BYTES
    assert result["observed_stage"] == stage
    assert result["log_scan"] == ("truncated" if truncated else "complete")


def test_checkpoint_write_failure_preserves_original_build_failure(tmp_path, modules, monkeypatch):
    builder, diagnostics = modules
    original = RuntimeError("PRIVATE original")

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(builder, "command", fail)
    monkeypatch.setattr(diagnostics, "write", fail)
    with pytest.raises(RuntimeError) as caught:
        builder.build_image(tmp_path / "build", "linux/arm64")
    assert caught.value is original


def test_sigterm_cancels_real_build_and_preserves_private_checkpoint(tmp_path, modules, monkeypatch):
    builder, _ = modules
    popen = subprocess.Popen
    processes = []

    def fake_docker(argv, **kwargs):
        process = popen(
            [sys.executable, "-c", f"import os,signal,time; os.kill({os.getpid()}, signal.SIGTERM); time.sleep(20)"],
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_docker)

    def guard(*args):
        raise RuntimeError("PRIVATE missing cancellation handler")

    previous = signal.signal(signal.SIGTERM, guard)
    try:
        with pytest.raises(KeyboardInterrupt):
            builder.build_image(tmp_path / "build", "linux/arm64")
        value = json.loads((tmp_path / "build/build-diagnostics.json").read_text())
        assert value["status"] == "cancelled"
        assert signal.getsignal(signal.SIGTERM) is guard
    finally:
        signal.signal(signal.SIGTERM, previous)
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def test_projection_failure_is_fixed_and_cannot_skip_fatal_cleanup(tmp_path, modules):
    steps = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())["jobs"]["evaluate"][
        "steps"
    ]
    projection = next(s for s in steps if "tooling_build_diagnostics.py" in s.get("run", ""))
    close = next(s for s in steps if "live_ci.py stage" in s.get("run", ""))
    assert "always()" in close["if"]
    (tmp_path / "skill-publish/tooling-build-diagnostics.json").mkdir(parents=True)
    shim = tmp_path / "sudo"
    shim.write_text(
        f"#!{sys.executable}\nimport os,sys,subprocess\n"
        "from pathlib import Path\n"
        "if sys.argv[2:4] == ['tools/live_shard.py', 'close']:\n"
        "    Path(os.environ['RUNNER_TEMP'], 'closed').touch()\n"
        "    raise SystemExit(9)\n"
        "if sys.argv[2] == 'tools/live_ci.py': raise AssertionError('stage after fatal cleanup')\n"
        "raise SystemExit(subprocess.call([sys.executable, *sys.argv[2:]]))\n"
    )
    shim.chmod(0o700)
    env = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "RUNNER_TEMP": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    results = [
        subprocess.run(
            ["/bin/bash", "-e", "-o", "pipefail", "-c", s["run"]], cwd=ROOT, env=env, capture_output=True, text=True
        )
        for s in (projection, close)
    ]
    assert [r.returncode for r in results] == [1, 9]
    assert results[0].stdout == "Tooling image diagnostics: projection_failed\n"
    assert all(r.stderr == "" for r in results)
    assert (tmp_path / "closed").exists()


@pytest.mark.parametrize(
    "stage", ["apt_index", "apt_install", "compose_download", "compose_verify", "versions", "complete"]
)
def test_every_observed_marker_is_emitted_by_fixed_recipe(tmp_path, modules, stage):
    _, diagnostics = modules
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text()
    marker = "EA_TOOLING_STAGE=" + stage
    assert f"printf '{marker}\\n'" in recipe
    log = tmp_path / "build.log"
    log.write_text(f"#8 1.234 {marker}\n")
    assert diagnostics.scan(log)["observed_stage"] == stage


def test_oversized_integer_is_invalid_checkpoint_not_projection_failure(tmp_path, modules, capsys):
    _, diagnostics = modules
    value = diagnostics.empty()
    value["elapsed_seconds"] = 10**1000
    source = tmp_path / "source.json"
    source.write_text(json.dumps(value))
    output = tmp_path / "public.json"
    diagnostics.publish(source, output)
    assert json.loads(output.read_text()) == diagnostics.empty("checkpoint_invalid")


def test_publisher_creates_only_missing_leaf_parent(tmp_path, modules):
    _, diagnostics = modules
    output = tmp_path / "skill-publish/tooling-build-diagnostics.json"
    diagnostics.publish(tmp_path / "missing", output)
    assert json.loads(output.read_text()) == diagnostics.empty()
    assert output.stat().st_mode & 0o777 == 0o644
    with pytest.raises(FileNotFoundError):
        diagnostics.publish(tmp_path / "missing", tmp_path / "missing-ancestor/skill-publish/public.json")
    assert not (tmp_path / "missing-ancestor").exists()


def test_publisher_preserves_relative_output_support(tmp_path, modules, monkeypatch):
    _, diagnostics = modules
    monkeypatch.chdir(tmp_path)
    diagnostics.publish(Path("missing"), Path("public.json"))
    assert json.loads(Path("public.json").read_text()) == diagnostics.empty()


@pytest.mark.parametrize("leaf_link", [True, False])
def test_publisher_does_not_create_through_symlink_parents(tmp_path, modules, leaf_link):
    _, diagnostics = modules
    private = tmp_path / "private"
    private.mkdir()
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    output = link / "public.json" if leaf_link else link / "skill-publish/public.json"
    with pytest.raises(OSError):
        diagnostics.publish(tmp_path / "missing", output)
    assert not list(private.iterdir())


def test_publisher_survives_parent_path_replacement_without_following_link(tmp_path, modules, monkeypatch):
    _, diagnostics = modules
    parent = tmp_path / "parent"
    parent.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    renamed = tmp_path / "renamed"
    original = diagnostics.directory
    from contextlib import contextmanager

    @contextmanager
    def replaced(path):
        with original(path) as fd:
            parent.rename(renamed)
            parent.symlink_to(private, target_is_directory=True)
            yield fd

    monkeypatch.setattr(diagnostics, "directory", replaced)
    diagnostics.write(parent / "public.json", diagnostics.empty(), 0o644)
    assert not list(private.iterdir()), "publication followed a swapped symlink ancestor"
    assert (renamed / "public.json").is_file()
