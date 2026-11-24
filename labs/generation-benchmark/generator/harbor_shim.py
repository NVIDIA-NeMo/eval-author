# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task-bundle and artifact archive helpers shared by the host Harbor sidecar and the generator's harbor shim.

The module uses only the standard library so it also runs as the shim inside the
generator container.
"""

from __future__ import annotations

import io
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

HARBOR_VERSION = "0.24.0"
METADATA_HEADER = "X-Harbor-Job"
MAX_ARCHIVE_BYTES = 1024**3
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _tarinfo(name: str, kind: bytes, mode: int, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode & 0o777
    info.size = size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def pack_tree(root: Path, arcname: str | None = None) -> bytes:
    """Return a deterministic tar of *root*, including dotfiles; reject anything but files and directories.

    With *arcname*, members live under that top directory; otherwise at the archive root.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"archive root must be an ordinary directory: {root}")
    entries: list[tuple[str, Path]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(current) / name
            entries.append((path.relative_to(root).as_posix(), path))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        if arcname is not None:
            tar.addfile(_tarinfo(arcname, tarfile.DIRTYPE, root.lstat().st_mode))
        for rel, path in sorted(entries):
            name = f"{arcname}/{rel}" if arcname is not None else rel
            info = path.lstat()
            if path.is_symlink():
                raise ValueError(f"archive must not contain symlinks: {rel}")
            if path.is_dir():
                tar.addfile(_tarinfo(name, tarfile.DIRTYPE, info.st_mode))
            elif path.is_file():
                data = path.read_bytes()
                tar.addfile(_tarinfo(name, tarfile.REGTYPE, info.st_mode, len(data)), io.BytesIO(data))
            else:
                raise ValueError(f"archive must contain only ordinary files and directories: {rel}")
    return buffer.getvalue()


def _member_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    parts = tuple(part for part in path.parts if part != ".")
    if path.is_absolute() or not parts or ".." in parts or any("\\" in part or "\0" in part for part in parts):
        raise ValueError(f"unsafe archive member: {name!r}")
    return parts


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def extract_tar(data: bytes, dest: Path, *, top: str | None = None, durable: bool = False) -> None:
    """Extract regular files and directories under *dest*; reject traversal, links, and special members.

    With *top*, every member must be that directory or lie under it. With *durable*,
    files and directories are fsynced before returning.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds the size limit")
    dest.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        raise ValueError(f"archive destination must not be a symlink: {dest}")
    written: list[Path] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar:
            parts = _member_parts(member.name)
            if top is not None and parts[0] != top:
                raise ValueError(f"archive member outside {top}/: {member.name!r}")
            target = dest.joinpath(*parts)
            for depth in range(1, len(parts) + 1):
                if dest.joinpath(*parts[:depth]).is_symlink():
                    raise ValueError(f"archive path crosses a symlink: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod((member.mode & 0o777) | 0o700)
                written.append(target)
            elif member.isreg():
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("archive exceeds the size limit")
                if target.is_dir():
                    raise ValueError(f"archive file collides with a directory: {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError(f"unreadable archive member: {member.name!r}")
                with target.open("wb") as stream:
                    stream.write(source.read())
                    if durable:
                        stream.flush()
                        os.fsync(stream.fileno())
                target.chmod(member.mode & 0o777)
                written.append(target)
            else:
                raise ValueError(f"archive must contain only regular files and directories: {member.name!r}")
    if durable:
        for directory in sorted({path if path.is_dir() else path.parent for path in written} | {dest}):
            _fsync(directory)
