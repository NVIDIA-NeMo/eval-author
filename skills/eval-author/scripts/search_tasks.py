#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find local Harbor task and Gym workload candidates without loading runtimes.

Uses filename/layout evidence only, including incomplete scaffolds. Never parses
or executes repository configuration. A complete search covers only the declared
scope; it does not prove that candidates are valid or that no other evals exist.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import stat
from pathlib import Path
from typing import Any

EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox"}
)


def _snapshot(root: Path, max_depth: int, max_entries: int) -> tuple[set[Path], dict]:
    files: set[Path] = set()
    pending = ["."]
    search: dict[str, Any] = {
        "complete": True,
        "max_depth": max_depth,
        "max_entries": max_entries,
        "entries_retained": 0,
        "directories_scanned": 0,
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORIES),
        "excluded": [],
        "errors": [],
        "limits_reached": [],
    }
    while pending:
        relative = Path(heapq.heappop(pending))
        directory = root / relative
        entries = []
        remaining = max_entries - search["entries_retained"]
        try:
            with os.scandir(directory) as listing:
                for entry in listing:
                    entries.append(entry)
                    if len(entries) > remaining:
                        break
        except OSError as exc:
            search["errors"].append({"path": relative.as_posix(), "errno": exc.errno})
            continue
        if len(entries) > remaining:
            # A filesystem can enumerate entries in any order. Never retain an
            # arbitrary prefix and then sort it: discard the partial directory.
            search["limits_reached"].append({"path": relative.as_posix(), "limit": "max_entries"})
            break
        search["entries_retained"] += len(entries)
        search["directories_scanned"] += 1
        for entry in sorted(entries, key=lambda item: item.name):
            path = relative / entry.name
            label = path.as_posix()
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                search["errors"].append({"path": label, "errno": exc.errno})
                continue
            if stat.S_ISLNK(mode):
                search["excluded"].append({"path": label, "reason": "symlink"})
            elif stat.S_ISDIR(mode):
                if entry.name in EXCLUDED_DIRECTORIES:
                    search["excluded"].append({"path": label, "reason": "excluded-directory"})
                elif len(path.parts) > max_depth:
                    search["limits_reached"].append({"path": label, "limit": "max_depth"})
                else:
                    heapq.heappush(pending, label)
            elif stat.S_ISREG(mode):
                files.add(path)
    for key in ("excluded", "errors", "limits_reached"):
        search[key].sort(key=lambda item: item["path"])
    search["complete"] = not (search["errors"] or search["limits_reached"])
    return files, search


def _catalog_tree(path: Path, root: Path) -> tuple[Path, str] | None:
    """Find the nearest conventional catalog ancestor within the selected root."""
    for parent in path.parents:
        name = root.name if parent == Path(".") else parent.name
        if name in {"environments", "benchmarks"}:
            return parent, "environment" if name == "environments" else "benchmark"
    return None


def _candidates(files: set[Path], root: Path) -> list[dict]:
    result = []

    def add(provider: str, kind: str, path: Path, markers: list[Path], **details) -> None:
        result.append(
            {
                "provider": provider,
                "kind": kind,
                "path": path.as_posix(),
                "evidence": sorted(marker.as_posix() for marker in markers if marker in files),
                "validation": "not_checked",
                **details,
            }
        )

    task_dirs = {path.parent for path in files if path.name == "task.toml"}
    task_dirs.update(
        path.parent
        for path in files
        if path.name == "instruction.template.md" and path.with_name("tasks.jsonl") in files
    )
    for directory in sorted(task_dirs):
        markers = [
            directory / name for name in ("task.toml", "instruction.md", "instruction.template.md", "tasks.jsonl")
        ]
        markers += [directory / "tests" / name for name in ("test.sh", "test.bat", "verifier.py")]
        if directory / "tasks.jsonl" in files and directory / "instruction.template.md" in files:
            add("gym", "parameterized-task", directory, markers)
        elif directory / "tests/verifier.py" in files:
            add("gym", "python-verifier-task", directory, markers)
        else:
            add("harbor", "task", directory, markers)

    for path in sorted(files):
        catalog = _catalog_tree(path, root)
        context = {"catalog_tree": None, "catalog_kind": None, "tombstone": None}
        if catalog:
            tree, kind = catalog
            context.update(catalog_tree=tree.as_posix(), catalog_kind=kind)
            parts = path.relative_to(tree).parts
            tombstone = tree / parts[0] / ".nemo_gym_tombstone"
            if kind == "environment" and len(parts) > 1 and tombstone in files:
                context["tombstone"] = tombstone.as_posix()
        if path.name == "manifest.yaml":
            add("gym", "manifest-candidate", path, [path, path.with_name("config.yaml")], **context)
        elif path.suffix == ".yaml":
            if path.name == "config.yaml" and path.with_name("manifest.yaml") in files:
                continue  # Listed as supporting evidence with the manifest.
            if catalog and (
                catalog[1] == "benchmark"
                or (path.name == "config.yaml" and len(path.relative_to(catalog[0]).parts) == 2)
            ):
                add("gym", "legacy-config-candidate", path, [path], **context)
            elif path.parent.name == "configs" and (
                path.parent.parent.parent.name == "resources_servers"
                or (path.parent.parent.parent == Path(".") and root.name == "resources_servers")
            ):
                add("gym", "resource-config-candidate", path, [path], **context)
    return sorted(result, key=lambda item: (item["path"], item["provider"], item["kind"]))


def search_tasks(
    repo: Path, *, provider: str = "both", query: str = "", max_depth: int = 12, max_entries: int = 50000
) -> dict:
    if provider not in {"harbor", "gym", "both"}:
        raise ValueError("provider must be harbor, gym, or both")
    if max_depth < 0 or max_entries < 1:
        raise ValueError("max_depth must be nonnegative and max_entries must be positive")
    root = repo.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    files, search = _snapshot(root, max_depth, max_entries)
    candidates = [
        item
        for item in _candidates(files, root)
        if (provider == "both" or item["provider"] == provider) and query.casefold() in item["path"].casefold()
    ]
    return {
        "schema_version": 1,
        "repo": str(root),
        "mode": "inventory",
        "filters": {"provider": provider, "query": query},
        "search": search,
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--provider", choices=("harbor", "gym", "both"), default="both")
    parser.add_argument("--query", default="", help="Case-insensitive substring of candidate paths.")
    parser.add_argument("--max-depth", type=int, default=12, help="Directory depth below the root; root is depth zero.")
    parser.add_argument("--max-entries", type=int, default=50000, help="Maximum filesystem entries collected.")
    args = parser.parse_args()
    try:
        report = search_tasks(
            args.repo, provider=args.provider, query=args.query, max_depth=args.max_depth, max_entries=args.max_entries
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema_version": 1, "error": str(exc), "search": {"complete": False}, "candidates": []}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["search"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
