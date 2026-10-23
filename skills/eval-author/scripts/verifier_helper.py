# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Optional bounded transport for trusted verifier helpers, not submitted programs."""

import asyncio
import contextlib
import json
import math
import os
import signal


class VerifierHelperError(RuntimeError):
    """Infrastructure or protocol failure; must not become a behavioral score."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite number")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite number")
    return number


def decode_response(raw):
    """Validate a transport envelope without grading its result value."""
    try:
        response = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise VerifierHelperError("invalid helper JSON") from exc
    if not isinstance(response, dict):
        raise VerifierHelperError("invalid helper envelope")
    if response.get("status") == "result" and set(response) == {"status", "value"}:
        return response
    if (
        response.get("status") == "submission_error"
        and set(response) == {"status", "reason"}
        and isinstance(response["reason"], str)
        and response["reason"].strip()
    ):
        return response
    raise VerifierHelperError("invalid helper envelope")


async def run_helper_async(argv, *, timeout_seconds=10.0, max_output_bytes=65536):
    """Run a trusted helper; its stdout is one JSON envelope, stderr is diagnostic.

    Limits cover combined stdout/stderr bytes. POSIX descendants in the helper's
    process group are killed on completion or failure. Other platforms kill only
    the immediate helper; use the native sandbox for descendant cleanup.
    """
    if not argv or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("argv and positive finite timeout required")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("positive integer output limit required")
    loop = asyncio.get_running_loop()

    class Output(asyncio.SubprocessProtocol):
        def __init__(self):
            self.done = loop.create_future()
            self.closed = loop.create_future()
            self.used = 0
            self.stdout = bytearray()

        def pipe_data_received(self, fd, data):
            if self.done.done():
                return
            self.used += len(data)
            if self.used > max_output_bytes:
                self.done.set_exception(VerifierHelperError("helper output limit exceeded"))
            elif fd == 1:
                self.stdout.extend(data)

        def connection_lost(self, exc):
            if not self.done.done():
                if exc is None:
                    self.done.set_result(bytes(self.stdout))
                else:
                    self.done.set_exception(VerifierHelperError("helper pipe failure"))
            if not self.closed.done():
                self.closed.set_result(None)

    protocol = Output()
    try:
        process, _ = await loop.subprocess_exec(
            lambda: protocol,
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise VerifierHelperError("helper could not start") from exc
    try:
        try:
            stdout = await asyncio.wait_for(protocol.done, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise VerifierHelperError("helper infrastructure timeout") from exc
        if process.get_returncode() != 0:
            raise VerifierHelperError("helper exited unsuccessfully")
        return decode_response(stdout)
    finally:
        with contextlib.suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.get_pid(), signal.SIGKILL)
            elif process.get_returncode() is None:
                process.kill()
        # Explicitly close the public transport API, including both pipe handles.
        process.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(protocol.closed, timeout=2)


def run_helper(argv, *, timeout_seconds=10.0, max_output_bytes=65536):
    """Synchronous entrypoint; async callers should await run_helper_async."""
    return asyncio.run(
        run_helper_async(
            argv,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    )
