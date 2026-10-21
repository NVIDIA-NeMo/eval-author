# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paths and atomic storage for Eval Author-owned work, without runtime imports."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def repository_root(repo: Path | str | None = None) -> Path:
    if repo is not None:
        root = Path(repo).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"Repository directory does not exist: {root}")
        return root
    current = Path.cwd().resolve()
    for root in (current, *current.parents):
        if (root / ".git").exists() or (root / ".eval-author").is_dir():
            return root
    return current


def _directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Working storage must not traverse a symlink: {path}")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"Working storage is not a directory: {path}")


def working_root(repo: Path | str | None = None) -> Path:
    root = repository_root(repo)
    _directory(root / ".eval-author")
    _directory(root / ".eval-author" / ".working")
    return root / ".eval-author" / ".working"


def working_path(repo: Path | str | None, *parts: str) -> Path:
    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Working paths must stay beneath .eval-author/.working")
    root = working_root(repo)
    parent = root
    for part in relative.parts[:-1]:
        parent /= part
        _directory(parent)
    path = root / relative
    if path.is_symlink():
        raise ValueError(f"Working file must not be a symlink: {path}")
    return path


def atomic_write(path: Path, payload: bytes) -> None:
    """Keep the prior file intact until a complete, flushed replacement is ready."""
    if path.is_symlink():
        raise ValueError(f"Refusing to replace a symlink: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    atomic_write(path, payload)


@contextmanager
def scratch_directory(repo: Path | str | None = None, *, prefix: str = "eval-author-"):
    """Yield private temporary storage inside the selected repository's working area."""
    if Path(prefix).name != prefix or "/" in prefix or "\\" in prefix:
        raise ValueError("Scratch prefix must be a filename prefix")
    parent = working_path(repo, "tmp", "placeholder").parent
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as directory:
        yield Path(directory)


@contextmanager
def working_lock(path: Path):
    """Nonblocking process lock; the persistent inode prevents replacement races."""
    if path.is_symlink():
        raise ValueError(f"Refusing a symlink lock: {path}")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)
