# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verifier_helper",
    Path(__file__).parents[1] / "skills/eval-author/scripts/verifier_helper.py",
)
assert SPEC is not None and SPEC.loader is not None
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def run(source, **kwargs):
    return helper.run_helper([sys.executable, "-I", "-B", "-c", source], **kwargs)


@pytest.mark.parametrize("value", [0, False, None, {"answer": "wrong"}, 42])
def test_transport_does_not_grade_values(value):
    response = {"status": "result", "value": value}
    assert run(f"print({json.dumps(response)!r})") == response


def test_explicit_submission_failure_is_distinct():
    response = {"status": "submission_error", "reason": "submitted program timed out"}
    assert run(f"print({json.dumps(response)!r})") == response


@pytest.mark.parametrize(
    "source",
    [
        "def broken(:",
        "import nonexistent_verifier_dependency_741",
        "print('not-json')",
        "print('not-json'); raise SystemExit(2)",
        'print(\'{"status":"result","value":1}\'); raise SystemExit(70)',
    ],
)
def test_helper_faults_raise_instead_of_returning_rejection(source):
    with pytest.raises(helper.VerifierHelperError):
        run(source)


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"status":"result"}',
        '{"status":"result","value":1,"extra":0}',
        '{"status":"result","status":"result","value":1}',
        '{"status":"result","value":NaN}',
        '{"status":"result","value":1e999}',
        '{"status":"result","value":0} trailing',
        '{"status":"submission_error","reason":" "}',
        '{"status":"submission_error","reason":false}',
    ],
)
def test_invalid_protocol_is_infrastructure_failure(raw):
    with pytest.raises(helper.VerifierHelperError):
        run(f"print({raw!r})")


def test_missing_helper_command():
    with pytest.raises(helper.VerifierHelperError, match="could not start"):
        helper.run_helper(["/nonexistent-verifier-helper-741"])


def test_missing_helper_script_is_not_a_submission_rejection(tmp_path):
    with pytest.raises(helper.VerifierHelperError, match="exited unsuccessfully"):
        helper.run_helper([sys.executable, "-I", str(tmp_path / "missing-driver.py")])


@pytest.mark.skipif(os.name != "posix", reason="POSIX execute permission")
def test_helper_permission_denied_is_not_a_submission_rejection(tmp_path):
    driver = tmp_path / "driver"
    driver.write_text("#!/bin/sh\nexit 0\n")
    driver.chmod(0o600)
    with pytest.raises(helper.VerifierHelperError, match="could not start"):
        helper.run_helper([str(driver)])


def test_helper_timeout():
    with pytest.raises(helper.VerifierHelperError, match="infrastructure timeout"):
        run("import time; time.sleep(30)", timeout_seconds=0.2)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_is_bounded_during_execution(stream):
    with pytest.raises(helper.VerifierHelperError, match="output limit"):
        run(f"import sys\nwhile True: sys.{stream}.write('x'*8192); sys.{stream}.flush()", max_output_bytes=1024)


def test_stderr_does_not_corrupt_valid_protocol():
    assert run('import sys; print("diagnostic", file=sys.stderr); print(\'{"status":"result","value":0}\')') == {
        "status": "result",
        "value": 0,
    }


@pytest.mark.skipif(os.name != "posix", reason="process group cleanup is POSIX-specific")
@pytest.mark.parametrize("inherit_pipes", [False, True])
def test_descendants_are_cleaned_up(tmp_path, inherit_pipes):
    marker = tmp_path / "survived"
    child = f"import time,pathlib; time.sleep(0.6); pathlib.Path({str(marker)!r}).touch()"
    redirects = "" if inherit_pipes else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    source = (
        f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]{redirects});"
        'print(\'{"status":"result","value":1}\')'
    )
    if inherit_pipes:
        with pytest.raises(helper.VerifierHelperError, match="timeout"):
            run(source, timeout_seconds=0.2)
    else:
        assert run(source)["value"] == 1
    time.sleep(0.7)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="process group cleanup is POSIX-specific")
def test_caller_cancellation_cleans_up(tmp_path):
    started = tmp_path / "started"
    survived = tmp_path / "survived"
    source = (
        f"import pathlib,time; pathlib.Path({str(started)!r}).touch(); "
        f"time.sleep(0.6); pathlib.Path({str(survived)!r}).touch()"
    )

    async def cancel():
        task = asyncio.create_task(helper.run_helper_async([sys.executable, "-c", source]))
        for _ in range(100):
            if started.exists():
                break
            await asyncio.sleep(0.01)
        assert started.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    time.sleep(0.7)
    assert not survived.exists()
