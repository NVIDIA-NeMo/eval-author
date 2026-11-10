# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared host-side phases for one disposable Linux VM evaluation shard."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from live_agent_diagnostics import read_regular
from live_shard_boundary import attach_boundary, close_boundary, create_boundary, execute_in_boundary, handoff_boundary
from patched_opencode_runtime import (
    LIVE_EXECUTION_SECONDS,
    LIVE_HANDOFF_SECONDS,
    PREPARATION_TIMEOUT_SECONDS,
    PREPARATION_TIMING_MAX_BYTES,
    validate_preparation_timing,
)

LIVE_JOB_SECONDS = 4500
# The collector validates before launching the wrapper. Wrapper activation then
# repeats the full execution+handoff guard, so startup cannot spend that handoff.
VALIDATION_SECONDS = 60
STARTUP_SECONDS = 60
RUN_ADMISSION_SECONDS = VALIDATION_SECONDS + LIVE_EXECUTION_SECONDS + LIVE_HANDOFF_SECONDS
RUN_PHASE_SECONDS = RUN_ADMISSION_SECONDS + STARTUP_SECONDS
SETUP_SECONDS = LIVE_JOB_SECONDS - RUN_PHASE_SECONDS


def require_disposable_vm():
    from live_shard_boundary import require_disposable_vm as require

    require()


def build_image(output, platform):
    from build_live_shard_image import build_image as build

    return build(output, platform)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("create", "runtime", "relocate", "prepare", "run", "close"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tier", choices=("2", "3"), default=os.environ.get("SHARD_TIER", "3"))
    parser.add_argument(
        "--check", choices=("live", "context", "similarity"), default=os.environ.get("SHARD_CHECK", "live")
    )
    parser.add_argument("--skill", default=os.environ.get("SHARD_SKILL", "eval-author-audit"))
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), default="linux/amd64")
    parser.add_argument("--download", type=Path)
    parser.add_argument("--timing-checkpoint", type=Path)
    return parser.parse_args(argv)


def publish_timing(root, output):
    source = root / "workspace/preparation-operation-timing.json"
    if not source.exists() and not source.is_symlink():
        return
    output = output.absolute()
    if output.is_relative_to(root) or any(p.is_symlink() for path in (source, output) for p in (path, *path.parents)):
        raise ValueError("unsafe timing path")
    data = validate_preparation_timing(json.loads(read_regular(source, PREPARATION_TIMING_MAX_BYTES)))
    with output.open("x") as stream:
        json.dump(data, stream, allow_nan=False)


def run_phase(args):
    if args.phase != "create":
        return _run_phase(args)
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise ValueError("phase requires its own bootstrap timer")

    def expired(signum, frame):
        raise TimeoutError("bootstrap deadline exceeded")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 240)
    try:
        return _run_phase(args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _run_phase(args):
    root = args.root.absolute()
    state = root / "control/boundary.json"
    if args.phase == "close":
        if state.exists():
            close_boundary(state)
            if args.timing_checkpoint:
                publish_timing(root, args.timing_checkpoint)
        return 0
    if args.phase == "create":
        require_disposable_vm()
        repo = args.repo.absolute()
        if (
            root.is_relative_to(repo)
            or repo.is_relative_to(root)
            or any(p.is_symlink() for path in (root, repo) for p in (path, *path.parents))
        ):
            raise ValueError("unsafe shard paths")
        deadline = float(os.environ["EA_LIVE_JOB_DEADLINE"])
        remaining = deadline - time.monotonic()
        if not math.isfinite(remaining) or not RUN_PHASE_SECONDS < remaining <= LIVE_JOB_SECONDS:
            raise ValueError("insufficient_job_budget")
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        state.parent.mkdir(mode=0o700)
        config = {
            "repo": str(args.repo.absolute()),
            "workspace": str(workspace),
            "deadline": deadline,
            "tier": args.tier,
            "check": args.check,
            "skill": args.skill,
            "phase": "create",
        }
        image = build_image(root / "control/image", args.platform)
        if deadline - time.monotonic() <= RUN_PHASE_SECONDS:
            raise ValueError("insufficient_job_budget")
        try:
            create_boundary(
                state,
                repo=args.repo.absolute(),
                workspace=workspace,
                image=image,
                deadline=time.time() + deadline - time.monotonic(),
            )
            (root / "control/phase.json").write_text(json.dumps(config))
            (root / "control/phase.json").chmod(0o600)
            handoff_boundary(state)
        except BaseException:
            if state.exists():
                close_boundary(state)
            raise
        return 0
    config_path = root / "control/phase.json"
    try:
        config = json.loads(config_path.read_text())
        workspace = Path(config["workspace"])
        repo = Path(config["repo"])
        python = str(workspace / "live-runtime/bin/python")
        expected = {
            "runtime": "create",
            "relocate": "runtime",
            "prepare": "relocate",
            "run": "prepare" if config["check"] == "live" else "runtime",
        }
        if config["phase"] != expected.get(args.phase):
            raise ValueError("invalid phase order")
        until = min(
            time.monotonic()
            + {"runtime": 300, "relocate": 60, "prepare": PREPARATION_TIMEOUT_SECONDS, "run": RUN_PHASE_SECONDS}[
                args.phase
            ],
            config["deadline"] - (0 if args.phase == "run" else RUN_PHASE_SECONDS),
        )
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(workspace / "home"),
            "UV_PYTHON_INSTALL_DIR": str(workspace / "managed-python"),
            "UV_CACHE_DIR": str(workspace / "uv-cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "EA_LIVE_JOB_DEADLINE": str(config["deadline"]),
        }
        (workspace / "home").mkdir(mode=0o700, exist_ok=True)

        def execute(argv, label):
            remaining = until - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise ValueError("insufficient_job_budget")
            code, error = execute_in_boundary(state, argv, repo, env, root / "control" / (label + ".log"), remaining)
            if code != 0 or error:
                raise RuntimeError("native phase failed")

        if args.phase == "runtime":
            execute(["uv", "venv", "--managed-python", "--python", "3.12", str(workspace / "live-runtime")], "venv")
            extras = "tier3" if config["check"] == "live" else "tier2"
            execute(
                [
                    python,
                    str(repo / "tools/install_evaluator.py"),
                    "--python",
                    python,
                    "--extra",
                    extras,
                ],
                "install",
            )
        elif args.phase == "relocate":
            if args.download is None:
                raise ValueError("download required")
            downloaded = workspace / "downloaded-opencode"
            downloaded.mkdir(mode=0o700)
            for name in ("opencode", "binary-manifest.json"):
                source = args.download.absolute() / name
                if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
                    raise ValueError("unsafe downloaded artifact")
                shutil.copyfile(source, downloaded / name)
            execute(
                [
                    "/usr/bin/python3",
                    str(repo / "tools/build_patched_opencode.py"),
                    "--relocate-manifest",
                    str(downloaded / "binary-manifest.json"),
                    "--binary",
                    str(downloaded / "opencode"),
                    "--output",
                    str(workspace / "patched-opencode"),
                ],
                "relocate",
            )
            execute(
                [
                    python,
                    str(repo / "tools/verify_live_runtime.py"),
                    "--binary-manifest",
                    str(workspace / "patched-opencode/binary-manifest.json"),
                    "--output",
                    str(workspace / "live-runtime-gate-private"),
                ],
                "gate",
            )
        elif args.phase == "prepare":
            argv = [
                python,
                str(repo / "tools/prepare_live_images.py"),
                "--skill",
                str(repo / "skills" / config["skill"]),
                "--binary-manifest",
                str(workspace / "patched-opencode/binary-manifest.json"),
                "--output",
                str(workspace / "prepared-live-images"),
                "--private-diagnostics",
                str(workspace / "preparation-docker-failure.private.json"),
            ]
            if args.timing_checkpoint:
                argv.extend(["--timing-checkpoint", str(workspace / "preparation-operation-timing.json")])
            execute(argv, "prepare")
        elif args.phase == "run":
            if not math.isfinite(config["deadline"]) or config["deadline"] - time.monotonic() <= RUN_PHASE_SECONDS:
                raise ValueError("insufficient_job_budget")
            attach_boundary(state)
            home = root / "control/collector-home"
            home.mkdir(mode=0o700)
            collector_env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "GIT_CONFIG_VALUE_0": str(repo),
                "PYTHONDONTWRITEBYTECODE": "1",
                "EA_LIVE_JOB_DEADLINE": str(config["deadline"]),
            }
            if config["deadline"] - time.monotonic() <= RUN_ADMISSION_SECONDS:
                raise ValueError("insufficient_job_budget")
            for name in (
                "INFERENCE_HUB_API_KEY",
                "SKILL_EVAL_LLM_MODEL",
                "SKILL_EVAL_EMBEDDING_MODEL",
                "SKILL_EVAL_AGENT_MODEL",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "GITHUB_EVENT_NAME",
            ):
                if name in os.environ:
                    collector_env[name] = os.environ[name]
            if config["check"] == "live":
                collector_env.update(
                    EA_REQUIRE_PATCHED_OPENCODE="1",
                    EA_REQUIRE_PREPARED_IMAGES="1",
                    EA_PATCHED_OPENCODE_MANIFEST=str(workspace / "patched-opencode/binary-manifest.json"),
                    EA_PREPARED_IMAGES_MANIFEST=str(workspace / "prepared-live-images/prepared-images.json"),
                )
            argv = [
                sys.executable,
                str(repo / "tools/collect_live_skill_evaluations.py"),
                "--run",
                "--ci-context-budget",
                "--repo",
                str(repo),
                "--tier",
                config["tier"],
                "--skill",
                config["skill"],
                "--check",
                config["check"],
                "--evaluator",
                str(workspace / "live-runtime/bin/skillevaluator"),
                "--evaluator-python",
                python,
                "--output",
                str(root / "report"),
                "--native-output",
                str(workspace / "skill-live"),
                "--boundary-state",
                str(state),
            ]
            try:
                with (root / "control/collector.log").open("xb") as log:
                    os.chmod(log.name, 0o600)
                    result = subprocess.run(
                        argv,
                        cwd=repo,
                        env=collector_env,
                        stdout=log,
                        stderr=log,
                        timeout=until - time.monotonic(),
                    )
            finally:
                close_boundary(state)
            return result.returncode
        config["phase"] = args.phase
        config_path.write_text(json.dumps(config))
        handoff_boundary(state)
        return 0
    except BaseException:
        close_boundary(state)
        raise


def main():
    try:
        return run_phase(parse_args())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        print("live shard phase failed; private evidence retained", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
