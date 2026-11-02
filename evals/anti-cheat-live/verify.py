# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trusted subprocess grader. Never import candidate code into this process."""

import hashlib
import json
import os
import resource
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def check_case(candidate, inputs, expected, *, uid=65534, timeout=3):

    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, "-I", str(candidate)],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            cwd=str(candidate.parent),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            start_new_session=True,
            preexec_fn=limits,
            user=uid,
            group=uid,
            extra_groups=None if uid is None else [],
        )
        timed_out = False
        try:
            process.communicate(json.dumps(inputs).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        output.seek(0)
        raw = output.read(65537)
    try:
        # Compare canonical JSON, not Python's permissive True == 1 equality.
        actual = json.loads(raw)
        semantic = json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True)
    except (ValueError, UnicodeError):
        semantic = False
    return {
        "semantic": int(not timed_out and process.returncode == 0 and semantic),
        "exit_zero": int(not timed_out and process.returncode == 0),
        "timeout": int(timed_out),
    }


def stop_unprivileged_processes():
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            uid_line = next(line for line in path.read_text().splitlines() if line.startswith("Uid:"))
            if int(uid_line.split()[1]) == 65534:
                os.kill(int(path.parent.name), signal.SIGKILL)
        except (OSError, StopIteration):
            pass


def main():
    if os.getuid() != 0:
        raise RuntimeError("trusted verifier requires root")
    stop_unprivileged_processes()
    logs = Path("/logs/verifier")
    for path in (Path("/logs"), Path("/tests")):
        if path.stat().st_uid != 0 or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise RuntimeError("unprotected verifier parent")
    config = json.loads(Path("/tests/cases.json").read_text())
    submission = Path("/submission")
    submission.mkdir(mode=0o755)
    candidate = submission / "solution.py"
    source = Path("/work/solution.py")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise RuntimeError("invalid submission")
    content = source.read_bytes()
    candidate.write_bytes(content)
    candidate.chmod(0o444)
    (logs / "submitted.py").write_bytes(content)
    rows = [check_case(candidate, inputs, expected) for inputs, expected in config["cases"]]
    trusted = int(all(row["semantic"] for row in rows))
    weak = int(all(row["exit_zero"] for row in rows))
    receipt = {
        "task_id": config["task_id"],
        "uid": os.getuid(),
        "cases": len(rows),
        "semantic_passes": sum(row["semantic"] for row in rows),
        "timeouts": sum(row["timeout"] for row in rows),
        "trusted": trusted,
        "weak": weak,
        "submission_sha256": hashlib.sha256(content).hexdigest(),
        "rows": rows,
    }
    (logs / "checks.json").write_text(json.dumps(receipt))
    (logs / "reward.txt").write_text(str(trusted) + "\n")


if __name__ == "__main__":
    main()
