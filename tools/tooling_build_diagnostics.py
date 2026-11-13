# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded tooling-image observations; no Docker text crosses this boundary."""

import argparse
import json
import math
import os
import re
import secrets
import stat
import time
from pathlib import Path

from live_agent_diagnostics import directory, read_regular

MAX_SCAN_BYTES = 262_144
MAX_PUBLIC_BYTES = 4096
STAGE_ORDER = (
    "apt_index",
    "apt_bootstrap",
    "apt_full_index",
    "apt_acquire",
    "apt_plan_validate",
    "apt_staging",
    "apt_download",
    "apt_promote",
    "apt_install",
    "apt_cleanup",
    "compose_download",
    "compose_verify",
    "versions",
    "complete",
)
STAGES = {"unknown", *STAGE_ORDER}
MARKER = re.compile(
    rb"(?:#[0-9]+ [0-9]+\.[0-9]+ )?EA_TOOLING_STAGE=("
    + "|".join(sorted(STAGES - {"unknown"})).encode("ascii")
    + rb")(?: uptime=(.*))?"
)


class CommandFailed(ValueError):
    def __init__(self, code):
        super().__init__("execution image command failed")
        self.exit_code = code


def empty_timing(status="unavailable"):
    return dict(
        stage_timing_status=status,
        timing_origin_stage="unknown",
        stage_start_offsets_seconds={},
        completed_stage_seconds={},
        open_stage="unknown",
    )


def empty(category="checkpoint_unavailable") -> dict:
    return dict(
        schema_version=2,
        **empty_timing(),
        status="unavailable",
        failure_category=category,
        phase="unknown",
        elapsed_seconds=None,
        build_seconds=None,
        inspect_seconds=None,
        exit_code=None,
        observed_stage="unknown",
        log_scan="unavailable",
        scanned_bytes=0,
    )


def project(value):
    if isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 1:
        if set(value) != set(empty()) - set(empty_timing()):
            raise ValueError("invalid build diagnostics")
        value = {**value, "schema_version": 2, **empty_timing()}
    if not isinstance(value, dict) or set(value) != set(empty()):
        raise ValueError("invalid build diagnostics")
    enums = {
        "status": {"unavailable", "running", "complete", "failed", "cancelled"},
        "failure_category": {
            "none",
            "checkpoint_unavailable",
            "checkpoint_invalid",
            "command_failed",
            "timeout",
            "cancelled",
            "os_error",
            "invalid_image",
            "unknown",
        },
        "phase": {"unknown", "setup", "build", "identity", "inspect", "receipt"},
        "observed_stage": STAGES,
        "timing_origin_stage": STAGES,
        "open_stage": STAGES - {"complete"},
        "stage_timing_status": {"unavailable", "invalid", "observed"},
        "log_scan": {"unavailable", "complete", "truncated"},
    }
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("invalid build diagnostics")
    for key, allowed in enums.items():
        if not isinstance(value[key], str) or value[key] not in allowed:
            raise ValueError("invalid build diagnostics")
    for key in ("elapsed_seconds", "build_seconds", "inspect_seconds"):
        number = value[key]
        if number is not None and (
            type(number) not in (float, int) or not 0 <= number <= 86400 or not math.isfinite(number)
        ):
            raise ValueError("invalid build diagnostics")
    code = value["exit_code"]
    if code is not None and (type(code) is not int or not -255 <= code <= 255):
        raise ValueError("invalid build diagnostics")
    if type(value["scanned_bytes"]) is not int or not 0 <= value["scanned_bytes"] <= MAX_SCAN_BYTES:
        raise ValueError("invalid build diagnostics")
    starts = value["stage_start_offsets_seconds"]
    completed = value["completed_stage_seconds"]
    if not isinstance(starts, dict) or not isinstance(completed, dict):
        raise ValueError("invalid build diagnostics")
    for mapping in (starts, completed):
        if not set(mapping) <= set(STAGE_ORDER):
            raise ValueError("invalid build diagnostics")
        for number in mapping.values():
            if (
                type(number) not in (float, int)
                or not 0 <= number <= 86400
                or not math.isfinite(number)
                or round(number, 2) != number
            ):
                raise ValueError("invalid build diagnostics")
    if value["stage_timing_status"] != "observed":
        if any(value[key] != expected for key, expected in empty_timing(value["stage_timing_status"]).items()):
            raise ValueError("invalid build diagnostics")
    else:
        ordered = [stage for stage in STAGE_ORDER if stage in starts]
        if not ordered or value["timing_origin_stage"] != ordered[0] or starts[ordered[0]] != 0:
            raise ValueError("invalid build diagnostics")
        if any(starts[left] > starts[right] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("invalid build diagnostics")
        expected = {
            left: round(starts[right] - starts[left], 2)
            for left, right in zip(ordered, ordered[1:])
            if STAGE_ORDER.index(right) == STAGE_ORDER.index(left) + 1
        }
        if completed != expected or value["open_stage"] != (ordered[-1] if ordered[-1] != "complete" else "unknown"):
            raise ValueError("invalid build diagnostics")
        if value["observed_stage"] != ordered[-1]:
            raise ValueError("invalid build diagnostics")
    return dict(value)


def write(path, value, mode):
    payload = json.dumps(project(value), sort_keys=True, allow_nan=False) + "\n"
    with directory(path.parent) as parent:
        temporary = ".tooling-diagnostic-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def scan(path):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe build log")
            # Tail-only bounded scan: an evicted marker is unknown, not inferred.
            offset = max(0, info.st_size - MAX_SCAN_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_SCAN_BYTES)
    lines = raw.split(b"\n")
    if offset:
        lines = lines[1:]
    stage = "unknown"
    starts = {}
    seen = set()
    invalid = False
    untimed = False
    for line in lines[:-1]:
        match = MARKER.fullmatch(line)
        if match:
            stage = match[1].decode("ascii")
            token = match[2]
            if token is None:
                untimed = True
            elif not re.fullmatch(rb"[0-9]{1,9}\.[0-9]{2}", token):
                invalid = True
            else:
                number = float(token)
                if stage in seen or (
                    starts
                    and (
                        STAGE_ORDER.index(stage) <= STAGE_ORDER.index(next(reversed(starts)))
                        or number < starts[next(reversed(starts))]
                        or number - next(iter(starts.values())) > 86400
                    )
                ):
                    invalid = True
                if not invalid:
                    starts[stage] = number
            seen.add(stage)
    invalid = invalid or bool(starts and untimed)
    timing = empty_timing("invalid" if invalid else "unavailable")
    if starts and not invalid:
        origin = next(iter(starts))
        timing.update(
            stage_timing_status="observed",
            timing_origin_stage=origin,
            stage_start_offsets_seconds={name: round(value - starts[origin], 2) for name, value in starts.items()},
            completed_stage_seconds={
                left: round(starts[right] - starts[left], 2)
                for left, right in zip(starts, list(starts)[1:])
                if STAGE_ORDER.index(right) == STAGE_ORDER.index(left) + 1
            },
            open_stage=stage if stage != "complete" else "unknown",
        )
    return dict(observed_stage=stage, log_scan="truncated" if offset else "complete", scanned_bytes=len(raw), **timing)


class Diagnostics:
    def __init__(self, output):
        self.output = output
        self.started = time.perf_counter()
        self.value = empty("none")
        self.value.update(status="running", phase="setup")
        self.save()

    def save(self):
        try:
            self.value["elapsed_seconds"] = round(time.perf_counter() - self.started, 3)
            write(self.output / "build-diagnostics.json", self.value, 0o600)
        except Exception:
            pass  # Diagnostics cannot replace the build or cleanup outcome.

    def finish(self, error):
        import subprocess

        category = "none"
        status = "complete"
        if error is not None:
            status = "failed"
            if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
                category = "timeout"
            elif not isinstance(error, Exception):
                category, status = "cancelled", "cancelled"
            elif isinstance(error, OSError):
                category = "os_error"
            elif isinstance(error, CommandFailed):
                category = "command_failed"
            elif (
                isinstance(error, ValueError)
                and not isinstance(error, json.JSONDecodeError)
                and self.value["phase"] in {"identity", "inspect"}
            ):
                category = "invalid_image"
            else:
                category = "unknown"
        self.value.update(
            status=status,
            failure_category=category,
            exit_code=error.exit_code if isinstance(error, CommandFailed) else None,
        )
        try:
            self.value.update(scan(self.output / "build.log"))
        except Exception:
            pass
        self.save()


def publish(source, output):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate diagnostic field")
        return value

    try:
        value = project(json.loads(read_regular(source, MAX_PUBLIC_BYTES), object_pairs_hook=unique))
    except FileNotFoundError:
        value = empty()
    except (OSError, ValueError, TypeError, RecursionError):
        value = empty("checkpoint_invalid")
    # Initialization may have failed before creating the public artifact root.
    with directory(output.parent.parent) as ancestor:
        try:
            os.mkdir(output.parent.name or ".", mode=0o755, dir_fd=ancestor)
        except FileExistsError:
            pass  # write() still opens every ancestor with O_NOFOLLOW.
    write(output, value, 0o644)
    print("Tooling image diagnostics: " + json.dumps(value, sort_keys=True, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        publish(args.source, args.output)
    except Exception:
        print("Tooling image diagnostics: projection_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
