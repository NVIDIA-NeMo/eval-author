# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Content identities used by Experiment 2 checkpoints."""

from __future__ import annotations

import hashlib
from pathlib import Path

from generation_benchmark.eval_author.ports.errors import ExperimentError


def file_digest(path: Path) -> str:
    """Return a sha256 identity for one regular file."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ExperimentError(f"file does not exist: {resolved}")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def bytes_digest(payload: bytes) -> str:
    """Return a sha256 identity for in-memory bytes."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def tree_digest(root: Path) -> str:
    """Hash relative paths and file contents for a directory tree."""
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise ExperimentError(f"directory does not exist: {resolved}")
    digest = hashlib.sha256()
    ignored_directories = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
    }
    files = sorted(
        path
        for path in resolved.rglob("*")
        if path.is_file()
        and not any(part in ignored_directories for part in path.relative_to(resolved).parts)
        and path.suffix != ".pyc"
    )
    if not files:
        raise ExperimentError(f"directory contains no files: {resolved}")
    for path in files:
        relative = path.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
