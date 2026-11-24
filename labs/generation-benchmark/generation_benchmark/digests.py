# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Content digests for directories, globs, commands, and benchmark implementation."""

from __future__ import annotations

import os
import re
import subprocess
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

from metric_benchmark.io import digest_bytes, digest_file, json_bytes

GENERATION_BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
METRIC_BENCHMARK_ROOT = GENERATION_BENCHMARK_ROOT.parent / "metric-benchmark"

SKIPPED_COMPONENTS = frozenset({".git", "__pycache__"})
SKIPPED_SUFFIXES = (".pyc",)
SKIPPED_NAMES = frozenset({".DS_Store"})


def is_skipped_path(parts: tuple[str, ...]) -> bool:
    if not parts:
        return False
    if any(part in SKIPPED_COMPONENTS for part in parts):
        return True
    name = parts[-1]
    return name in SKIPPED_NAMES or name.endswith(SKIPPED_SUFFIXES)


def pythonpath_env(extra_dirs: list[Path] | None = None, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return environment with generation/metric benchmark roots appended to PYTHONPATH."""
    env = dict(os.environ if base_env is None else base_env)
    entries = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    for candidate in [GENERATION_BENCHMARK_ROOT, METRIC_BENCHMARK_ROOT, *(extra_dirs or [])]:
        text = str(candidate)
        if text not in entries:
            entries.append(text)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def iter_tree_files(root: Path, *, exclude_top_level: set[str] | None = None) -> list[tuple[str, Path]]:
    """List sorted (rel_posix, path) for ordinary files, skipping ignored entries and rejecting symlinks."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"evidence root must be an ordinary directory: {root}")
    excluded = exclude_top_level or set()
    entries: list[tuple[str, Path]] = []
    for current_dir, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_dir)
        rel_dir = current.relative_to(root)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            parts = (*rel_dir.parts, dirname) if rel_dir.parts != (".",) else (dirname,)
            if is_skipped_path(parts) or (len(parts) == 1 and parts[0] in excluded):
                continue
            candidate = current / dirname
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError(f"evidence must contain only ordinary files and directories: {candidate}")
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            parts = (*rel_dir.parts, filename) if rel_dir.parts != (".",) else (filename,)
            if is_skipped_path(parts) or (len(parts) == 1 and parts[0] in excluded):
                continue
            candidate = current / filename
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"evidence must contain only ordinary files and directories: {candidate}")
            entries.append((PurePosixPath(*parts).as_posix(), candidate))
    entries.sort(key=lambda item: item[0])
    return entries


def directory_manifest(root: Path, *, exclude_top_level: set[str] | None = None) -> dict[str, str]:
    """Hash ordinary files in *root*, skipping .git, __pycache__, *.pyc, and .DS_Store."""
    return {
        rel_posix: digest_file(path) for rel_posix, path in iter_tree_files(root, exclude_top_level=exclude_top_level)
    }


def directory_digest(root: Path, *, exclude_top_level: set[str] | None = None) -> str:
    return digest_bytes(json_bytes(directory_manifest(root, exclude_top_level=exclude_top_level)))


def path_digest(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    if path.is_dir():
        return directory_digest(path)
    if path.is_file():
        return digest_file(path)
    raise ValueError(f"missing or special input path: {path}")


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    norm = pattern.lstrip("./")
    parts: list[str] = ["^"]
    i = 0
    n = len(norm)
    while i < n:
        if norm[i : i + 3] == "**/":
            parts.append("(?:.+/)?")
            i += 3
        elif norm[i : i + 2] == "**":
            parts.append(".*")
            i += 2
        elif norm[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif norm[i] == "?":
            parts.append("[^/]")
            i += 1
        elif norm[i] == "[":
            end = norm.find("]", i + 1)
            if end == -1:
                parts.append(re.escape(norm[i]))
                i += 1
            else:
                inner = norm[i + 1 : end]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append(f"[{inner}]")
                i = end + 1
        else:
            parts.append(re.escape(norm[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def _is_excluded(rel_posix: str, excludes: list[str]) -> bool:
    name = PurePosixPath(rel_posix).name
    for pattern in excludes:
        norm = pattern.strip()
        if not norm:
            continue
        if norm.endswith("/"):
            norm = norm + "**"
        if "/" not in norm.lstrip("./"):
            if _glob_to_regex(norm).match(name) or _glob_to_regex(norm).match(rel_posix):
                return True
        else:
            if _glob_to_regex(norm).match(rel_posix):
                return True
    return False


def _relative_from_base(candidate: Path, base_dir: Path) -> str:
    rel_text = os.path.relpath(candidate, base_dir)
    return Path(rel_text).as_posix()


def _has_symlink_component(candidate: Path, stop_dir: Path) -> bool:
    current = candidate
    stop = stop_dir.resolve()
    while True:
        if current.is_symlink():
            return True
        if current.resolve() == stop or current.parent == current:
            return False
        current = current.parent


def _leading_parent_depth(pattern: str) -> int:
    norm = PurePosixPath(pattern)
    depth = 0
    for part in norm.parts:
        if part == "..":
            depth += 1
        elif part != ".":
            break
    return depth


def _expand_glob_pattern(pattern: str) -> str:
    # In Python 3.12, trailing `/**` matches directories only; `**/*` matches files identically on 3.12 and 3.13.
    if pattern == "**" or pattern.endswith("/**"):
        return f"{pattern}/*"
    return pattern


def glob_digest(patterns: list[str], excludes: list[str], base_dir: Path) -> str:
    """Compute a content digest over files matched by *patterns* minus *excludes* and skipped names."""
    base = base_dir.resolve()
    up_depth = max((_leading_parent_depth(p) for p in patterns), default=0)
    if up_depth > len(base.parts) - 1:
        raise ValueError(f"glob pattern escapes filesystem root from {base}")
    key_root = base.parents[up_depth - 1] if up_depth > 0 else base

    matched: dict[str, tuple[str, Path]] = {}
    for pattern in patterns:
        if Path(pattern).is_absolute():
            raise ValueError(f"glob pattern must be relative: {pattern}")
        expanded = _expand_glob_pattern(pattern)
        pattern_hits = 0
        for candidate in sorted(base_dir.glob(expanded)):
            norm_candidate = Path(os.path.normpath(candidate))
            rel_base = _relative_from_base(norm_candidate, base_dir)
            rel_key = _relative_from_base(norm_candidate, key_root)
            parts = PurePosixPath(rel_base).parts
            if is_skipped_path(parts):
                continue
            if _has_symlink_component(norm_candidate, key_root):
                raise ValueError(f"glob matched symlink: {norm_candidate}")
            if not norm_candidate.is_file():
                continue
            pattern_hits += 1
            matched[rel_key] = (rel_base, norm_candidate)
        if pattern_hits == 0:
            raise ValueError(f"glob pattern matched no files under {base}: {pattern}")

    filtered = {
        rel_key: file_path
        for rel_key, (rel_base, file_path) in matched.items()
        if not _is_excluded(rel_base, excludes) and (key_root == base or not _is_excluded(rel_key, excludes))
    }
    if not filtered:
        raise ValueError("glob digest matched no files after excludes")
    manifest = {rel_key: digest_file(file_path) for rel_key, file_path in sorted(filtered.items())}
    return digest_bytes(json_bytes(manifest))


def command_digest(command: list[str], base_dir: Path) -> str:
    """Run *command* in *base_dir* and return the sha256 digest of its stdout."""
    result = subprocess.run(
        command,
        cwd=base_dir,
        env=pythonpath_env(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"digest command exited {result.returncode} ({command[0]}): {stderr}")
    return digest_bytes(result.stdout)


def implementation_info() -> dict[str, Any]:
    """Return pinned source hashes under labs/generation-benchmark/**/*.py plus Harbor and pydantic versions."""
    sources: dict[str, str] = {}
    for rel_posix, path in iter_tree_files(GENERATION_BENCHMARK_ROOT):
        if rel_posix.endswith(".py"):
            sources[rel_posix] = digest_file(path)
    payload: dict[str, Any] = {
        "sources": sources,
        "harbor": version("harbor"),
        "pydantic": version("pydantic"),
    }
    return {**payload, "sha256": digest_bytes(json_bytes(payload))}


def implementation_digest() -> str:
    return str(implementation_info()["sha256"])
