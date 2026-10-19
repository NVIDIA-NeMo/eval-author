# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verifier-side, bounded execution of declared setup and reachability probes.

Copy this file into the isolated verifier. It is a library, not an agent or CLI.
Probe semantics still require review against the task's public requirements.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

SCHEMA = "nemo.eval_author.construction_execution.v1"
MAX_OUTPUT_BYTES = 256 * 1024


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pointer(value: object, path: str) -> object:
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("invalid JSON pointer")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not part.isdigit() or str(int(part)) != part:
                raise ValueError("invalid list index")
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise ValueError("pointer does not address a container")
    return value


def run_probe(probe: dict) -> dict:
    """Execute argv without a shell; capture bounded output, never echo it."""
    started = time.monotonic()
    output = bytearray()
    failure = None
    code = None
    try:
        process = subprocess.Popen(
            probe["argv"],
            cwd=probe["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        failure = "start_failed"
    else:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() - started > probe["timeout_seconds"]:
                    failure = "timeout"
                    break
                for key, _ in selector.select(timeout=0.05):
                    data = os.read(key.fd, 4096)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(data)
                    if len(output) > MAX_OUTPUT_BYTES:
                        failure = "output_limit"
                        break
                if failure:
                    break
            if failure:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                code = process.wait(timeout=max(0.01, probe["timeout_seconds"] - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                failure = "timeout"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                code = process.wait()
            process.stdout.close()
    observation = None
    if failure is None and code == 0:
        try:
            observation = pointer(json.loads(output), probe["json_pointer"])
        except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
            failure = "invalid_observation"
    return {
        "id": probe["id"],
        "definition_sha256": digest(probe),
        "exit_code": code,
        "failure": failure,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "observation": observation,
    }


def run_probes(probes: list[dict], output: Path) -> None:
    """Write fresh evidence only inside the verifier-owned log directory."""
    if not 1 <= len(probes) <= 32:
        raise ValueError("expected 1..32 probes")
    for probe in probes:
        if not 0 < probe["timeout_seconds"] <= 30:
            raise ValueError("probe timeout must be in (0, 30]")
    # Opening exclusively before execution prevents stale success from surviving.
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"schema": SCHEMA, "probes": [run_probe(probe) for probe in probes]}, stream)
        stream.write("\n")
