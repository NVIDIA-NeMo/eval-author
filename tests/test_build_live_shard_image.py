# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools/build_live_shard_image.py"


def load_builder():
    assert BUILDER.is_file(), "isolated execution image builder is missing"
    spec = importlib.util.spec_from_file_location("build_live_shard_image", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invalid_platform_cannot_create_output_or_contact_docker(tmp_path):
    builder = load_builder()
    output = tmp_path / "build"
    with pytest.raises(ValueError, match="unsupported platform"):
        builder.build_image(output, "darwin/arm64")
    assert not output.exists()


def test_build_uses_pinned_inputs_private_context_and_verified_image(tmp_path, monkeypatch):
    builder = load_builder()
    output = tmp_path / "build"
    image = "sha256:" + "a" * 64
    calls = []
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "synthetic-must-not-forward")
    monkeypatch.setenv("DOCKER_HOST", "unix:///synthetic-shared.sock")
    monkeypatch.setattr(builder, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(builder, "host_platform", lambda: "linux/arm64")

    def command(argv, env, log, timeout, *, capture=False):
        assert "synthetic-must-not-forward" not in json.dumps(env)
        assert "DOCKER_HOST" not in env and "DOCKER_CONTEXT" not in env
        assert argv[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
        assert 0 < timeout <= 180
        calls.append(argv)
        if "build" in argv:
            assert "--force-rm" in argv
            Path(argv[argv.index("--iidfile") + 1]).write_text(image + "\n")
            context = Path(argv[-1])
            assert {p.name for p in context.iterdir()} == {"Dockerfile"}
            assert "INFERENCE_HUB_API_KEY" not in (context / "Dockerfile").read_text()
        if "inspect" in argv:
            return json.dumps([{"Id": image, "Architecture": "arm64", "Os": "linux"}]).encode()
        return b""

    monkeypatch.setattr(builder, "command", command)
    assert builder.build_image(output, "linux/arm64") == image
    assert [argv[3] for argv in calls] == ["build", "image"]
    receipt = json.loads((output / "image.json").read_text())
    assert receipt["image"] == image
    assert receipt["platform"] == "linux/arm64"
    assert all("@sha256:" in value for value in receipt["base_images"].values())
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "image.json").stat().st_mode & 0o777 == 0o600


def test_system_dependencies_use_the_same_fixed_signed_snapshot():
    recipe = (ROOT / "tools/live_shard.Dockerfile").read_text()
    assert "ARG APT_SNAPSHOT=20261105T000000Z" in recipe
    assert "URIs: https://snapshot.ubuntu.com/ubuntu/%s/" in recipe
    assert "/etc/apt/sources.list.d/ubuntu.sources" in recipe
    assert "APT::Update::Error-Mode=any" in recipe
    assert "--allow-unauthenticated" not in recipe
    assert "COPY --from=engine /usr/local/bin/dind /usr/local/bin/docker-init /usr/local/bin/" in recipe
    assert 'CMD ["dind", "docker-init", "--", "dockerd",' in recipe


def test_disposable_vm_guard_precedes_output_creation(tmp_path, monkeypatch):
    builder = load_builder()

    def reject():
        raise ValueError("not disposable")

    monkeypatch.setattr(builder, "require_disposable_vm", reject)
    with pytest.raises(ValueError, match="not disposable"):
        builder.build_image(tmp_path / "build", "linux/arm64")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("case", ["architecture", "symlink", "existing"])
def test_invalid_build_location_or_platform_cannot_launch(tmp_path, monkeypatch, case):
    builder = load_builder()
    monkeypatch.setattr(builder, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(builder, "host_platform", lambda: "linux/amd64" if case == "architecture" else "linux/arm64")
    monkeypatch.setattr(builder, "command", lambda *a, **kw: pytest.fail("Docker must not be contacted"))
    output = tmp_path / "build"
    if case == "symlink":
        output.symlink_to(tmp_path, target_is_directory=True)
    if case == "existing":
        output.mkdir()
    with pytest.raises((ValueError, FileExistsError)):
        builder.build_image(output, "linux/arm64")


@pytest.mark.parametrize("case", ["mutable", "wrong_id", "wrong_architecture", "multiple", "oversized", "symlink"])
def test_invalid_image_receipt_never_publishes_success(tmp_path, monkeypatch, case):
    builder = load_builder()
    monkeypatch.setattr(builder, "require_disposable_vm", lambda: None)
    monkeypatch.setattr(builder, "host_platform", lambda: "linux/arm64")
    output = tmp_path / "build"
    image = "sha256:" + "a" * 64

    def command(argv, *args, **kwargs):
        if "build" in argv:
            path = output / "image.id"
            if case == "symlink":
                other = tmp_path / "synthetic-image-id"
                other.write_text(image)
                path.symlink_to(other)
            else:
                path.write_text("ubuntu:latest" if case == "mutable" else "a" * 200 if case == "oversized" else image)
            return b""
        item = {"Id": "sha256:" + "b" * 64 if case == "wrong_id" else image, "Os": "linux", "Architecture": "arm64"}
        if case == "wrong_architecture":
            item["Architecture"] = "amd64"
        return json.dumps([item, item] if case == "multiple" else [item]).encode()

    monkeypatch.setattr(builder, "command", command)
    with pytest.raises(ValueError):
        builder.build_image(output, "linux/arm64")
    assert not (output / "image.json").exists()


def test_real_command_keeps_failure_output_private(tmp_path):
    builder = load_builder()
    log = tmp_path / "private.log"
    with pytest.raises(ValueError, match="^execution image command failed$"):
        builder.command(
            [sys.executable, "-c", "print('synthetic-private-output'); raise SystemExit(7)"],
            {"PATH": os.defpath},
            log,
            10,
            capture=True,
        )
    assert log.read_text() == "synthetic-private-output\n"
    assert log.stat().st_mode & 0o777 == 0o600


def test_real_command_timeout_is_not_success(tmp_path):
    builder = load_builder()
    with pytest.raises(subprocess.TimeoutExpired):
        builder.command(
            [sys.executable, "-c", "import time; print('synthetic-private-output', flush=True); time.sleep(20)"],
            {"PATH": os.defpath},
            tmp_path / "private.log",
            3,
        )
    assert (tmp_path / "private.log").read_text() == "synthetic-private-output\n"
