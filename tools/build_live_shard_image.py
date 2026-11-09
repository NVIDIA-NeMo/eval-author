# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a pinned tooling image only on an explicitly disposable Linux VM."""

import argparse
import hashlib
import json
import os
import platform as system_platform
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

RECIPE = Path(__file__).with_name("live_shard.Dockerfile")
BUILD_SECONDS = 180
BASE_IMAGES = {
    "BASE_IMAGE": "ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852",
    "DOCKER_IMAGE": "docker:29.7.2-dind@sha256:3ef33f2e220b79ed3ef3b99d81746f06f306cd6340e2cb7331d17ae996e74cb6",
    "UV_IMAGE": "ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc",
}
COMPOSE = {
    "linux/amd64": ("x86_64", "db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576"),
    "linux/arm64": ("aarch64", "732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7"),
}


class CommandFailed(ValueError):
    def __init__(self, code):
        super().__init__("execution image command failed")
        self.exit_code = code


def require_disposable_vm():
    from live_shard_boundary import require_disposable_vm as check

    check()


def host_platform():
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(system_platform.machine())
    return "linux/" + architecture if system_platform.system() == "Linux" and architecture else None


def command(argv, env, log, timeout, *, capture=False):
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        process = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
    if code:
        raise CommandFailed(code)
    if not capture:
        return b""
    if log.stat().st_size > 65_536:
        raise ValueError("oversized image identity")
    return log.read_bytes()


def build_image(output: Path, platform: str) -> str:
    if platform not in COMPOSE:
        raise ValueError("unsupported platform")
    require_disposable_vm()
    if host_platform() != platform:
        raise ValueError("execution image must match VM architecture")
    if not output.is_absolute() or any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("unsafe build output")
    started = time.monotonic()
    output.mkdir(mode=0o700)
    from tooling_build_diagnostics import Diagnostics

    diagnostics = Diagnostics(output)

    def cancelled(signum, frame):
        raise KeyboardInterrupt

    previous = (
        signal.signal(signal.SIGTERM, cancelled) if threading.current_thread() is threading.main_thread() else None
    )
    error = None
    try:
        return _build_image(output, platform, started, diagnostics)
    except BaseException as caught:
        error = caught
        raise
    finally:
        try:
            diagnostics.finish(error)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)


def _build_image(output, platform, started, diagnostics):
    context = output / "context"
    context.mkdir(mode=0o700)
    home = output / "home"
    home.mkdir(mode=0o700)
    temporary = output / "tmp"
    temporary.mkdir(mode=0o700)
    if RECIPE.is_symlink():
        raise ValueError("unsafe execution recipe")
    recipe = RECIPE.read_bytes()
    with (context / "Dockerfile").open("xb") as stream:
        stream.write(recipe)
    (context / "Dockerfile").chmod(0o600)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "DOCKER_CONFIG": str(home / ".docker"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    docker = ["docker", "--host", "unix:///var/run/docker.sock"]
    argv = [
        *docker,
        "build",
        "--force-rm",
        "--platform",
        platform,
        "--iidfile",
        str(output / "image.id"),
    ]
    build_arguments = {**BASE_IMAGES, "COMPOSE_ARCH": COMPOSE[platform][0], "COMPOSE_SHA256": COMPOSE[platform][1]}
    for key, value in build_arguments.items():
        argv.extend(["--build-arg", key + "=" + value])
    argv.append(str(context))
    diagnostics.value["phase"] = "build"
    diagnostics.save()
    phase_started = time.perf_counter()
    try:
        command(argv, env, output / "build.log", BUILD_SECONDS)
    finally:
        diagnostics.value["build_seconds"] = round(time.perf_counter() - phase_started, 3)
    diagnostics.value["phase"] = "identity"
    image_file = output / "image.id"
    if image_file.is_symlink() or image_file.stat().st_size > 128:
        raise ValueError("invalid built image identity")
    image = image_file.read_text().strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("invalid built image identity")
    remaining = BUILD_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("execution image build deadline exceeded")
    diagnostics.value["phase"] = "inspect"
    diagnostics.save()
    phase_started = time.perf_counter()
    try:
        inspected = json.loads(
            command([*docker, "image", "inspect", image], env, output / "inspect.json", remaining, capture=True)
        )
    finally:
        diagnostics.value["inspect_seconds"] = round(time.perf_counter() - phase_started, 3)
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or inspected[0].get("Id") != image
        or inspected[0].get("Os") != "linux"
        or inspected[0].get("Architecture") != platform.split("/")[1]
    ):
        raise ValueError("built image identity mismatch")
    diagnostics.value["phase"] = "receipt"
    receipt = {
        "schema_version": 1,
        "image": image,
        "platform": platform,
        "base_images": BASE_IMAGES,
        "recipe_sha256": hashlib.sha256(recipe).hexdigest(),
        "compose_sha256": COMPOSE[platform][1],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    descriptor = os.open(output / "image.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=tuple(COMPOSE), required=True)
    args = parser.parse_args()
    try:
        build_image(args.output, args.platform)
    except Exception:
        print('{"status":"incomplete","reason":"execution_image_build_failed"}')
        return 1
    print('{"status":"complete","reason":null}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
