# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed, credential-free checks of the installed live Python runtime."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GATE_SECONDS = 45
CHILD_MARKER = b"native-child-ok\n"
CHILD_CHECK = """
import importlib
import sys

names = (
    'harbor.agents.installed.opencode',
    'skillevaluator.tier3.harbor.runner',
    'skillevaluator.tier3.harbor.runtime_preflight',
)
modules = [importlib.import_module(name) for name in names]
for name, module in zip(names, modules):
    parent, _, attribute = name.rpartition('.')
    if (importlib.import_module(name) is not module or sys.modules[name] is not module
            or getattr(importlib.import_module(parent), attribute) is not module):
        raise RuntimeError('noncanonical native import')
agent, runner, preflight = modules
install = agent.OpenCode.install
if not getattr(install, '_ea_patched_opencode', False):
    raise RuntimeError('inactive native install')
if importlib.import_module(names[0]).OpenCode.install is not install:
    raise RuntimeError('native install identity changed')
if preflight._harbor_launch_environment is not runner._harbor_launch_environment:
    raise RuntimeError('native launch helper identity changed')
print('native-child-ok')
"""


class GateFailure(Exception):
    pass


def private_stream(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")


def environment(output, manifest):
    home = output / "home"
    temporary = output / "tmp"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    result = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "HARBOR_TELEMETRY": "0",
        "EA_REQUIRE_PATCHED_OPENCODE": "1",
        "EA_PATCHED_OPENCODE_MANIFEST": str(manifest),
    }
    if "EA_LIVE_JOB_DEADLINE" in os.environ:
        result["EA_LIVE_JOB_DEADLINE"] = os.environ["EA_LIVE_JOB_DEADLINE"]
    return result


def run_private(argv, env, output, label, deadline, *, process_group=True):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GateFailure("timeout")
    with private_stream(output / (label + ".stdout")) as stdout, private_stream(output / (label + ".stderr")) as stderr:
        process = subprocess.Popen(
            argv, env=env, cwd=env["HOME"], stdout=stdout, stderr=stderr, start_new_session=process_group
        )
        try:
            return process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if process_group:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            raise GateFailure("timeout") from None


def probe_children(output, deadline):
    from importlib import import_module

    import patched_opencode_runtime as runtime
    import run_pinned_live_evaluator as wrapper

    runtime.live_job_remaining(runtime.LIVE_EXECUTION_SECONDS + runtime.LIVE_HANDOFF_SECONDS)
    sys.argv = [str(TOOLS / "run_pinned_live_evaluator.py"), "tier3", "evaluate", "--help"]
    wrapper.configure_runtime()
    runner = import_module("skillevaluator.tier3.harbor.runner")
    preflight = import_module("skillevaluator.tier3.harbor.runtime_preflight")
    if runner._harbor_launch_environment is not preflight._harbor_launch_environment:
        raise GateFailure("children_failed")
    # Only the launch-environment seams are exercised: no evaluator dispatch,
    # agent setup, Docker lifecycle or provider call is reachable here.
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PYTHON_DOTENV_DISABLED",
        "HARBOR_TELEMETRY",
        "EA_REQUIRE_PATCHED_OPENCODE",
        runtime.MANIFEST_ENV,
        runtime.LIVE_JOB_DEADLINE_ENV,
    }
    base = {key: value for key, value in os.environ.items() if key in allowed}
    positive = negative = 0
    for index, module in enumerate((runner, preflight)):
        env = module._harbor_launch_environment(base)
        label = f"child-{index}"
        code = run_private([sys.executable, "-c", CHILD_CHECK], env, output, label, deadline, process_group=False)
        if code != 0 or (output / (label + ".stdout")).read_bytes() != CHILD_MARKER:
            raise GateFailure("children_failed")
        positive += 1
        for mutation, key in enumerate((runtime.TOKEN_ENV, runtime.DIGEST_ENV, runtime.MANIFEST_DIGEST_ENV)):
            corrupted = {**env, key: "invalid"}
            label = f"negative-{index}-{mutation}"
            code = run_private(
                [sys.executable, "-c", "print('main-must-not-run')"],
                corrupted,
                output,
                label,
                deadline,
                process_group=False,
            )
            if code != 78 or (output / (label + ".stdout")).stat().st_size != 0:
                raise GateFailure("negative_control_failed")
            negative += 1
    return {"positive_children": positive, "negative_children": negative}


def verify(manifest, output):
    started = time.monotonic()
    summary = {"reason": "internal_failure", "help_checks": 0, "positive_children": 0, "negative_children": 0}
    created = False
    try:
        if not output.is_absolute() or any(part.is_symlink() for part in (output, *output.parents)):
            raise ValueError("unsafe private output")
        output.mkdir(mode=0o700)
        created = True
        env = environment(output, manifest)
        code = run_private(
            [sys.executable, str(TOOLS / "run_pinned_live_evaluator.py"), "tier3", "evaluate", "--help"],
            env,
            output,
            "wrapper",
            started + GATE_SECONDS,
        )
        if code != 0:
            raise GateFailure("wrapper_failed")
        summary["help_checks"] = 1
        code = run_private(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--probe-children",
                str(started + GATE_SECONDS),
                "--binary-manifest",
                str(manifest),
                "--output",
                str(output),
            ],
            env,
            output,
            "children",
            started + GATE_SECONDS,
        )
        receipt = output / "children.stdout"
        if code != 0 or receipt.stat().st_size > 1024:
            raise GateFailure("children_failed")
        counts = json.loads(receipt.read_text())
        if counts != {"positive_children": 2, "negative_children": 6} or any(
            type(value) is not int for value in counts.values()
        ):
            raise GateFailure("children_failed")
        summary.update(counts)
        summary["reason"] = "passed"
    except GateFailure as exc:
        reason = str(exc)
        if reason in {"wrapper_failed", "children_failed", "timeout"}:
            summary["reason"] = reason
    except Exception:
        # Raw exceptions never enter the public projection, even before setup.
        try:
            if created:
                with private_stream(output / "failure.stderr") as stream:
                    stream.write(traceback.format_exc().encode())
        except OSError:
            pass
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-children", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe_children is not None:
        try:
            print(json.dumps(probe_children(args.output, args.probe_children), sort_keys=True))
            return 0
        except Exception:
            try:
                with private_stream(args.output / "probe-failure.stderr") as stream:
                    stream.write(traceback.format_exc().encode())
            except OSError:
                pass
            return 1
    summary = verify(args.binary_manifest.absolute(), args.output.absolute())
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["reason"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
