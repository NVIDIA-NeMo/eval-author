# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture and grade the same bytes in an isolated verifier (no CLI)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

SCHEMA = "nemo.eval_author.review_submission.v2"
LIMIT = 2 * 1024 * 1024
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path) -> bytes:
    """Bounded read; reject symbolic links, including parent directories."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("submission symlinks are forbidden")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError("submission must be a bounded regular file")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("submission exceeds byte limit")
    return data


def grade_submission(
    sources: Mapping[str, Path],
    assess: Callable[[Mapping[str, bytes | None]], Mapping[str, bool]],
    output_dir: Path = Path("/logs/verifier"),
) -> None:
    """Grade immutable captured bytes; retain evidence only after a successful assessment.

    The reviewed callback must use these bytes for submission-dependent checks.
    This is a provenance aid inside the trusted verifier, not a security boundary.
    """
    if not 1 <= len(sources) <= 32 or any(not ID.fullmatch(key) or len(key) > 80 for key in sources):
        raise ValueError("expected 1..32 safe submission IDs")
    if not all(path.is_absolute() for path in sources.values()):
        raise ValueError("submission source paths must be absolute")
    captured: dict[str, bytes | None] = {}
    invalid: dict[str, str] = {}
    for key, path in sources.items():
        try:
            captured[key] = read_regular(path)
        except FileNotFoundError:
            captured[key] = None
        except (ValueError, NotADirectoryError):
            captured[key] = None
            invalid[key] = "invalid_file"
    if sum(len(data) for data in captured.values() if data is not None) > LIMIT:
        invalid = {key: "combined_limit" for key in sources}
        captured = {key: None for key in sources}
    checks = dict(assess(MappingProxyType(captured)))
    if not 1 <= len(checks) <= 256 or any(
        not isinstance(key, str) or not ID.fullmatch(key) or len(key) > 80 or type(value) is not bool
        for key, value in checks.items()
    ):
        raise ValueError("assessment must return bounded named boolean checks")
    if invalid:
        # Preserve the declared check inventory but never reward malformed inputs,
        # even if a callback mistakenly accepts their missing-byte projection.
        checks = {key: False for key in checks}
    if any(p.is_symlink() for p in (output_dir, *output_dir.parents)):
        raise ValueError("verifier output symlinks are forbidden")
    output_dir.mkdir(parents=True, exist_ok=True)
    names = ("review-submission", "review-submission.json", "results", "reward.txt")
    if any((output_dir / name).exists() or (output_dir / name).is_symlink() for name in names):
        raise ValueError("review requires fresh verifier outputs")
    snapshots = output_dir / "review-submission"
    snapshots.mkdir()
    for key, data in captured.items():
        if data is None:
            continue
        with (snapshots / key).open("xb") as stream:
            stream.write(data)
    rows = "".join(f"{key}\t{'PASS' if value else 'FAIL'}\n" for key, value in sorted(checks.items()))
    reward = int(all(checks.values()))
    receipt = {
        "schema": SCHEMA,
        "producer_sha256": sha(read_regular(Path(__file__).resolve())),
        "submissions": {key: sha(data) if data is not None else None for key, data in captured.items()},
        "invalid": invalid,
        "checks": checks,
        "reward": reward,
    }
    # A receipt is the final completion marker. Interrupted writes cannot qualify.
    for name, text in (
        ("results", rows),
        ("reward.txt", str(reward)),
        ("review-submission.json", json.dumps(receipt, sort_keys=True) + "\n"),
    ):
        with (output_dir / name).open("x") as stream:
            stream.write(text)
