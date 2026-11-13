# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""This simulator driver is root-owned. Expected fixture answers never enter this task."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType


def read_bytes(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("nonregular input")
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        raise ValueError("input limit")
    return raw


def decode(raw):
    return decode_response(b'{"status":"result","value":' + raw + b"}")["value"]


def failure(reason):
    return {"health": "error", "reason": reason, "checks": {}, "total_credit": None, "accepted": None}


def collect(tests, submissions):
    program = (
        f"_DRIVER_BYTES = {globals()['_DRIVER_BYTES']!r}\n"
        f"_DRIVER_DIGEST = {globals()['_DRIVER_DIGEST']!r}\n"
        f"_SOURCE_BYTES = {_SOURCE_BYTES!r}\n"
        f"__file__ = {__file__!r}\n"
        "exec(compile(_DRIVER_BYTES, __file__, 'exec'))"
    )
    command = [
        sys.executable,
        "-I",
        "-c",
        program,
        "--worker",
        str(tests / "manifest.json"),
        str(submissions),
    ]
    receipt = {
        "uid": os.getuid(),
        "tests_uid": tests.stat().st_uid,
        "tests_mode": tests.stat().st_mode & 0o777,
        "rows": [],
        "cases": 0,
        "health": "error",
        "total_credit": None,
        "accepted": None,
        "smoke": {},
        "operational_controls": {},
    }
    try:
        envelope = run_helper(command, timeout_seconds=10, max_output_bytes=1048576)
        rows = envelope["value"]["rows"]
        receipt["consumed"] = envelope["value"]["consumed"]
        if not isinstance(rows, list) or not rows:
            raise VerifierHelperError("empty collection")
        receipt.update(health="ok", rows=rows, cases=len(rows))
        receipt["smoke"] = {"python": True, "scorer_import": True, "helper": True}
    except (VerifierHelperError, KeyError):
        receipt["reason"] = "helper_failure"
    programs = {
        "helper-failure": "raise RuntimeError('intentional trusted helper failure')",
        "syntax-error": "def invalid(:",
        "empty-collection": 'print(\'{"status":"result","value":[]}\')',
        "malformed-output": "print('not-json')",
    }
    for name, program in programs.items():
        try:
            result = run_helper([sys.executable, "-I", "-c", program], timeout_seconds=2)
            if result.get("value") == []:
                raise VerifierHelperError("empty collection")
        except VerifierHelperError:
            receipt["operational_controls"][name] = failure(name.replace("-", "_"))
        else:
            receipt["operational_controls"][name] = {"health": "ok"}
    return receipt


def main():
    tests = Path("/tests")
    receipt = collect(tests, Path("/work/submissions.json"))
    # Harbor's scalar measures only transport health. Each row retains semantic credit.
    receipt["reward"] = int(receipt["health"] == "ok")
    output = Path("/logs/verifier")
    (output / "checks.json").write_text(json.dumps(receipt, allow_nan=False) + "\n")
    (output / "reward.txt").write_text(str(receipt["reward"]) + "\n")


def worker(manifest_path, submissions_path):
    raw = {"manifest.json": read_bytes(manifest_path), "submissions.json": read_bytes(submissions_path)}
    raw.update(_SOURCE_BYTES)
    raw["verify.py"] = globals()["_DRIVER_BYTES"]
    manifest, submissions = decode(raw["manifest.json"]), decode(raw["submissions.json"])
    if not manifest or [row["id"] for row in manifest] != [row["id"] for row in submissions]:
        raise ValueError("empty or mismatched collection")
    rows = [
        {
            "id": row["id"],
            "results": {
                grader: evaluate(row["contract"], submission["candidate"], grader=grader)
                for grader in ("baseline", "reviewed")
            },
        }
        for row, submission in zip(manifest, submissions, strict=True)
    ]
    return {
        "rows": rows,
        "consumed": {
            **{name: "sha256:" + hashlib.sha256(data).hexdigest() for name, data in raw.items()},
            "verify.py": globals()["_DRIVER_DIGEST"],
        },
    }


# The copy loaded from the path only bootstraps the driver.
# Before loading any scoring implementation, execute and identify the same
# immutable driver buffer.
if "_DRIVER_BYTES" not in globals():
    namespace = {"__name__": __name__, "__file__": __file__, "_DRIVER_BYTES": read_bytes(__file__)}
    namespace["_DRIVER_DIGEST"] = "sha256:" + hashlib.sha256(namespace["_DRIVER_BYTES"]).hexdigest()
    exec(compile(namespace["_DRIVER_BYTES"], __file__, "exec"), namespace)
    globals().update(namespace)
else:
    if "_SOURCE_BYTES" not in globals():
        _SOURCE_BYTES = {
            name: read_bytes(Path(__file__).parent / name) for name in ("refund_scorer.py", "verifier_helper.py")
        }
    scorer, helper = ModuleType("captured_scorer"), ModuleType("captured_helper")
    exec(compile(_SOURCE_BYTES["refund_scorer.py"], "refund_scorer.py", "exec"), scorer.__dict__)
    exec(compile(_SOURCE_BYTES["verifier_helper.py"], "verifier_helper.py", "exec"), helper.__dict__)
    evaluate = scorer.evaluate
    VerifierHelperError, decode_response, run_helper = (
        helper.VerifierHelperError,
        helper.decode_response,
        helper.run_helper,
    )
    if __name__ == "__main__":
        if len(sys.argv) == 4 and sys.argv[1] == "--worker":
            print(json.dumps({"status": "result", "value": worker(*sys.argv[2:])}, allow_nan=False))
        else:
            main()
