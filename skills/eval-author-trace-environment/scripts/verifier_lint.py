# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded, non-executing review leads for Python verifier source access."""

from __future__ import annotations

import ast
import os
from pathlib import Path

MAX_ENTRIES = 2048
MAX_FILES = 128
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FINDINGS = 64


def _module_file(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "__file__":
        return True
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        return name in {"Path", "PurePath", "str", "fspath"} and _module_file(node.args[0])
    return False


def _source_read_lines(tree: ast.AST) -> list[int]:
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(item.asname or item.name for item in node.names if item.name == "inspect")
        elif isinstance(node, ast.ImportFrom) and node.module == "inspect":
            functions.update(
                item.asname or item.name for item in node.names if item.name in {"getsource", "getsourcelines"}
            )
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        inspection = (isinstance(function, ast.Name) and function.id in functions) or (
            isinstance(function, ast.Attribute)
            and function.attr in {"getsource", "getsourcelines"}
            and isinstance(function.value, ast.Name)
            and function.value.id in modules
        )
        reads_file = (isinstance(function, ast.Name) and function.id == "open") or (
            isinstance(function, ast.Attribute) and function.attr in {"open", "read_text", "read_bytes"}
        )
        operands = [*node.args, *(item.value for item in node.keywords if item.arg == "file")]
        if isinstance(function, ast.Attribute):
            operands.append(function.value)
        if inspection or (reads_file and any(_module_file(operand) for operand in operands)):
            lines.add(node.lineno)
    return sorted(lines)


def verifier_advisories(task_dir: Path) -> list[dict[str, str]]:
    """Scan tests/*.py recursively; findings neither block nor establish proof."""
    root = task_dir / "task" / "tests"
    findings: list[dict[str, str]] = []

    def incomplete(path: Path, reason: str) -> None:
        findings.append(
            {
                "code": "verifier_scan_incomplete",
                "path": path.relative_to(task_dir).as_posix(),
                "hint": f"Python verifier scan incomplete: {reason}. Review this scope separately.",
            }
        )

    if any(path.is_symlink() for path in (root, *root.parents)):
        incomplete(root, "symlink path was not followed")
        return findings
    if not root.is_dir():
        return findings
    pending = [root]
    entries = files = total_bytes = 0
    while pending:
        if len(findings) >= MAX_FINDINGS:
            incomplete(root, "finding limit reached")
            return findings
        directory = pending.pop()
        try:
            with os.scandir(directory) as listing:
                children = []
                for entry in listing:
                    entries += 1
                    if entries > MAX_ENTRIES:
                        incomplete(root, "directory entry limit reached")
                        return findings
                    children.append(entry)
            for entry in sorted(children, key=lambda item: item.name):
                path = Path(entry.path)
                if len(findings) >= MAX_FINDINGS:
                    incomplete(root, "finding limit reached")
                    return findings
                if entry.is_symlink():
                    incomplete(path, "symlink was not followed")
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif path.suffix == ".py":
                    if not entry.is_file(follow_symlinks=False):
                        incomplete(path, "not a regular file")
                        continue
                    files += 1
                    if files > MAX_FILES:
                        incomplete(root, "Python file limit reached")
                        return findings
                    with path.open("rb") as stream:
                        data = stream.read(min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_bytes) + 1)
                    total_bytes += len(data)
                    if total_bytes > MAX_TOTAL_BYTES:
                        incomplete(root, "total byte limit reached")
                        return findings
                    if len(data) > MAX_FILE_BYTES:
                        incomplete(path, "file byte limit reached")
                        continue
                    try:
                        tree = ast.parse(data)
                        lines = _source_read_lines(tree)
                    except (SyntaxError, ValueError, RecursionError, MemoryError):
                        incomplete(path, "source could not be parsed")
                        continue
                    for line in lines:
                        if len(findings) >= MAX_FINDINGS:
                            incomplete(root, "finding limit reached")
                            return findings
                        findings.append(
                            {
                                "code": "verifier_source_inspection",
                                "path": path.relative_to(task_dir).as_posix(),
                                "hint": f"Line {line} reads Python implementation source. Check whether grading depends on "
                                "internal text rather than required behavior. Legitimate source-analysis tasks may keep it; "
                                "record the public requirement and review in private construction notes.",
                            }
                        )
        except OSError:
            incomplete(directory, "directory or file could not be read")
    return findings
