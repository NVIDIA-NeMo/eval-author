# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find private task content that also appears in the agent's public files.

Findings name files, line ranges, and the comparison method. They never copy the
matched content. A match is a candidate for review: it can't tell an intended
reference from an unintended answer, which is what declared allowed overlap is for.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from rubric_identity import diagnostic, finish, read_selected, text
from rubric_layout import join, label_task

# Shorter content matches too much ordinary text to be worth a reviewer's time.
MIN_FULL_CHARS = 8
MIN_HINT_CHARS = 24
MIN_VALUE_CHARS = 6
# File names are short, so a shorter value still identifies an answer there.
MIN_NAME_VALUE_CHARS = 4
MAX_ANSWER_LINE_CHARS = 64
GENERIC_NAMES = frozenset({"test.sh", "solve.sh", "run.sh", "test.py", "tests.py", "conftest.py", "__init__.py"})
NORMALIZATION = (
    "Normalized comparisons collapse whitespace runs to one space and lowercase text. "
    "JSON comparisons use sorted keys and compact separators."
)

Kind = Literal["full", "hint", "reference", "filename"]
HiddenCases = Literal["protected", "exposed", "unknown"]


class Finding(TypedDict):
    kind: Kind
    method: str
    private: str
    public: str
    lines: list[int] | None
    allowed: bool
    allowed_reason: str | None


class TaskText(TypedDict):
    path: str
    relative: str
    content: str
    digest: str | None


def normalize(value: str) -> str:
    return " ".join(value.split()).lower()


def canonical_json(value: str) -> str | None:
    try:
        return json.dumps(json.loads(value), sort_keys=True, separators=(",", ":"))
    except (ValueError, RecursionError):
        return None


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _exact_lines(public: str, needle: str) -> list[int] | None:
    offset = public.find(needle)
    if offset < 0:
        return None
    return [_line_of(public, offset), _line_of(public, offset + len(needle) - 1)]


def _normalized_lines(public: str, private: str) -> list[int] | None:
    """Return the public line range of a whitespace- and case-insensitive match, if any."""
    target = normalize(private)
    if not target or target not in normalize(public):
        return None
    lines = public.splitlines()
    for start in range(len(lines)):
        joined = ""
        for end in range(start, len(lines)):
            joined = normalize(joined + " " + lines[end])
            if target in joined:
                return [start + 1, end + 1]
            if len(joined) > len(target) + len(lines[start]) + 1:
                break
    return None


def json_values(content: str, minimum: int = MIN_VALUE_CHARS) -> list[str]:
    """Return distinctive scalar values from a JSON document, as text."""
    try:
        document = json.loads(content)
    except (ValueError, RecursionError):
        return []
    values: list[str] = []
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, bool) or node is None:
            continue
        elif isinstance(node, (int, float, str)):
            value = str(node)
            if len(value.strip()) >= minimum:
                values.append(value.strip())
    return sorted(set(values))


def answer_values(private: TaskText) -> list[str]:
    """Return values short enough to appear in a file name: JSON scalars and a one-line plain answer."""
    values = set(json_values(private["content"], MIN_NAME_VALUE_CHARS))
    stripped = private["content"].strip()
    if "\n" not in stripped and MIN_NAME_VALUE_CHARS <= len(stripped) <= MAX_ANSWER_LINE_CHARS:
        values.add(stripped)
    return sorted(values)


def hint_lines(private: str) -> list[str]:
    return sorted({line for line in (normalize(row) for row in private.splitlines()) if len(line) >= MIN_HINT_CHARS})


def test_names(private: TaskText) -> list[str]:
    """Return the ``test_`` function names defined in a private Python file."""
    if not private["relative"].endswith(".py"):
        return []
    try:
        tree = ast.parse(private["content"])
    except (SyntaxError, ValueError, RecursionError):
        return []
    return sorted(
        {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        }
    )


def private_names(private: TaskText) -> list[str]:
    """Return names that point at a private file: its task path, a distinctive basename, and test names."""
    relative = PurePosixPath(private["relative"])
    names = {relative.as_posix(), "/" + relative.as_posix(), *test_names(private)}
    if relative.name not in GENERIC_NAMES:
        names.add(relative.name)
    return sorted(names)


def _compare(private: TaskText, public: TaskText) -> list[tuple[Kind, str, list[int] | None]]:
    content, target = private["content"], public["content"]
    stripped = content.strip()
    if len(stripped) >= MIN_FULL_CHARS:
        lines = _exact_lines(target, stripped)
        if lines:
            return [("full", "exact", lines)]
        lines = _normalized_lines(target, stripped)
        if lines:
            return [("full", "normalized", lines)]
        expected = canonical_json(content)
        if expected is not None and len(expected) >= MIN_FULL_CHARS and expected == canonical_json(target):
            return [("full", "json-canonical", [1, max(1, target.count("\n") + 1)])]
    matches: list[tuple[Kind, str, list[int] | None]] = []
    normalized_lines = [normalize(line) for line in target.splitlines()]
    for line in hint_lines(content):
        for index, candidate in enumerate(normalized_lines):
            if line in candidate:
                matches.append(("hint", "normalized-line", [index + 1, index + 1]))
                break
    for value in json_values(content):
        lines = _exact_lines(target, value)
        if lines:
            matches.append(("hint", "json-value", lines))
    return matches


def read_texts(root: Path, layout: dict) -> tuple[dict[str, list[TaskText]], list[str]]:
    """Read a labeled task's public and private UTF-8 files; return them and the paths that couldn't be read."""
    root = Path(root).resolve(strict=True)
    texts: dict[str, list[TaskText]] = {"public": [], "private": []}
    unscanned = []
    for record in layout["files"]:
        if record["visibility"] not in texts:
            continue
        data, _ = read_selected(root, record["path"])
        try:
            content = data.decode("utf-8") if data is not None else None
        except UnicodeDecodeError:
            content = None
        if content is None:
            unscanned.append(record["path"])
            continue
        texts[record["visibility"]].append(
            {
                "path": record["path"],
                "relative": record["relative"],
                "content": content,
                "digest": record["digest"],
            }
        )
    return texts, unscanned


def _allowed(selection: object, task: str) -> dict[tuple[str, str], str]:
    if not isinstance(selection, list):
        raise ValueError("invalid leakage selection")
    allowed = {}
    for entry in selection:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"private", "public", "reason"}
            or not all(text(entry[key]) for key in entry)
        ):
            raise ValueError("invalid leakage selection")
        allowed[(join(task, entry["private"]), join(task, entry["public"]))] = entry["reason"]
    return allowed


def _generated_digests(reconstruction: dict | None) -> set[str]:
    if not isinstance(reconstruction, dict):
        return set()
    return {
        record["artifact_digest"]
        for record in reconstruction.get("records", [])
        if record.get("declaration") == "generated" and text(record.get("artifact_digest"))
    }


def _is_test(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return parts[0] == "tests" or (parts[0] == "steps" and len(parts) > 3 and parts[2] == "tests")


def hidden_cases(layout: dict, unscanned: list[str], findings: list[Finding]) -> HiddenCases:
    """Classify whether the task's hidden tests stay private.

    ``protected``: the Harbor layout keeps every test file private and no public
    file holds a test's content. ``exposed``: a public file holds a full copy of a
    test file. ``unknown``: anything the scan couldn't settle, including layout
    exceptions, unreadable files, and partial matches awaiting review.
    """
    tests = {row["path"] for row in layout["files"] if row["visibility"] == "private" and _is_test(row["relative"])}
    findings = [row for row in findings if row["private"] in tests and not row["allowed"]]
    if any(row["kind"] == "full" for row in findings):
        return "exposed"
    if layout["format"] != "harbor" or layout["diagnostics"] or unscanned or not tests or findings:
        return "unknown"
    return "protected"


def scan_task(root: Path, selected: object, reconstruction: dict | None = None) -> dict:
    """Compare a Harbor task's private files with its public files.

    ``selected`` is ``{"task": <dir>, "allowed_overlap": [{"private", "public",
    "reason"}]}`` with paths relative to the task directory. A public file whose
    digest a reconstruction record declares ``generated`` can't be allowed overlap.
    """
    if not isinstance(selected, dict) or not {"task"} <= set(selected) <= {"task", "allowed_overlap"}:
        raise ValueError("invalid leakage selection")
    layout = label_task(root, {"task": selected["task"]})
    task = layout["task"]
    allowed = _allowed(selected.get("allowed_overlap", []), task)
    result: dict = {
        "task": task,
        "normalization": NORMALIZATION,
        "hidden_cases": "unknown",
        "generated_files_checked": reconstruction is not None,
        "findings": [],
        "unscanned": [],
        "counts": dict.fromkeys(
            ("public_files", "private_files", "unscanned_files", "full", "hint", "reference", "filename", "allowed"),
            0,
        ),
    }
    diagnostics = [
        {**row, "code": row["code"].replace("layout.", "leakage.layout_", 1)} for row in layout["diagnostics"]
    ]
    if layout["format"] != "harbor":
        return {**result, **_status(diagnostics)}
    texts, result["unscanned"] = read_texts(root, layout)
    generated = _generated_digests(reconstruction)
    findings: list[Finding] = []
    instructions = [row for row in texts["public"] if PurePosixPath(row["relative"]).name == "instruction.md"]
    public_paths = [row for row in layout["files"] if row["visibility"] == "public"]
    for private in texts["private"]:
        for public in texts["public"]:
            pair = (private["path"], public["path"])
            for kind, method, lines in _compare(private, public):
                reason = allowed.get(pair)
                if reason and public["digest"] in generated:
                    diagnostics.append(diagnostic("leakage.generated_overlap_not_allowed", public["path"]))
                    reason = None
                findings.append(
                    {
                        "kind": kind,
                        "method": method,
                        "private": private["path"],
                        "public": public["path"],
                        "lines": lines,
                        "allowed": reason is not None,
                        "allowed_reason": reason,
                    }
                )
        for instruction in instructions:
            for name in private_names(private):
                lines = _exact_lines(instruction["content"], name)
                if lines and bounded(instruction["content"], name):
                    findings.append(_finding("reference", "private-name", private["path"], instruction["path"], lines))
                    break
        values = answer_values(private)
        for record in public_paths:
            if any(_in_name(record["relative"], value) for value in values):
                findings.append(_finding("filename", "value-in-path", private["path"], record["path"], None))
    result["findings"] = findings
    result["counts"].update(
        public_files=sum(row["visibility"] == "public" for row in layout["files"]),
        private_files=sum(row["visibility"] == "private" for row in layout["files"]),
        unscanned_files=len(result["unscanned"]),
        allowed=sum(row["allowed"] for row in findings),
        **{
            kind: sum(row["kind"] == kind and not row["allowed"] for row in findings)
            for kind in ("full", "hint", "reference", "filename")
        },
    )
    result["hidden_cases"] = hidden_cases(layout, result["unscanned"], findings)
    for finding in findings:
        if finding["allowed"]:
            continue
        status = "fail" if finding["kind"] == "full" else "unknown"
        diagnostics.append(diagnostic(f"leakage.{finding['kind']}", finding["public"], status))
    if result["unscanned"]:
        diagnostics.append(diagnostic("leakage.unscanned_files", task, "unknown"))
    return {**result, **_status(diagnostics)}


def _finding(kind: Kind, method: str, private: str, public: str, lines: list[int] | None) -> Finding:
    return {
        "kind": kind,
        "method": method,
        "private": private,
        "public": public,
        "lines": lines,
        "allowed": False,
        "allowed_reason": None,
    }


def _in_name(path: str, value: str) -> bool:
    """True when ``value`` appears in a path between non-alphanumeric characters, as in ``total_4213.csv``."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", path, re.IGNORECASE) is not None


def bounded(content: str, name: str) -> bool:
    """True when ``name`` appears as a whole token, not inside a longer word."""
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", content) is not None


def _status(diagnostics: list[dict]) -> dict:
    unique = {(row["code"], row["subject"], row["status"]): row for row in diagnostics}
    summary = finish([], list(unique.values()))
    return {"status": summary["status"], "diagnostics": summary["diagnostics"]}
