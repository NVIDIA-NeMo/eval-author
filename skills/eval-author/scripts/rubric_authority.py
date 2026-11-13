# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a construction plan's grading-authority references against selected artifacts."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath

from eval_author_shared.task_evidence import loads
from rubric_identity import diagnostic, finish, local_file, read_selected, resolve_pointer, text

BASES = {"specification", "user_decision", "independent_reference", "unresolved"}
# A locator is a whitespace-free path, optionally followed by `#fragment`.
LOCATOR = re.compile(r"(?P<path>[^\s#]+)(?:#(?P<fragment>\S.*))?")
LINES = re.compile(r"L([1-9][0-9]*)(?:-L([1-9][0-9]*))?")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
ATX = re.compile(r" {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*")
SETEXT = re.compile(r" {0,3}(?:=+|-+)[ \t]*")
STATUS = {
    "resolved": None,
    "prose": ("authority.reference_prose", "unknown"),
    "unresolved": ("authority.unresolved", "unknown"),
    "unselected": ("authority.reference_unselected", "unknown"),
    "unreadable": ("authority.reference_unreadable", "unknown"),
    "invalid": ("authority.reference_invalid", "fail"),
    "changed": ("authority.reference_changed", "fail"),
    "broken": ("authority.reference_broken", "fail"),
}


def _visible(heading):
    """Heading text as rendered: links, code spans, emphasis, and HTML reduced to their text."""
    heading = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"(`+)(.+?)\1", r"\2", heading)
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"(\*{1,3})(\S(?:.*?\S)?)\1", r"\2", heading)
    heading = re.sub(r"(?<!\w)(_{1,3})(\S(?:.*?\S)?)\1(?!\w)", r"\2", heading)
    return heading.strip()


def _headings(content):
    """ATX and setext heading texts outside front matter and fenced code."""
    lines = content.split("\n")
    start = 0
    if lines[0] == "---":
        start = next((index + 1 for index, line in enumerate(lines[1:], 1) if line == "---"), 0)
    headings, fence, paragraph = [], None, None
    for line in lines[start:]:
        opened = FENCE.match(line)
        if fence:
            if (
                opened
                and opened[1][0] == fence[0]
                and len(opened[1]) >= len(fence)
                and not line[opened.end() :].strip()
            ):
                fence = None
            continue
        if opened:
            fence, paragraph = opened[1], None
            continue
        atx = ATX.fullmatch(line)
        if atx:
            headings.append(atx[1] or "")
            paragraph = None
        elif paragraph is not None and SETEXT.fullmatch(line):
            headings.append(paragraph)
            paragraph = None
        else:
            paragraph = line.strip() if line.strip() and not line.startswith("    ") else None
    return [heading.strip() for heading in headings if heading.strip()]


def _slug(heading):
    """GitHub heading anchor, before duplicate suffixes."""
    kept = re.sub(r"[^\w\- ]", "", heading.lower())
    return kept.replace(" ", "-")


def _anchors(headings):
    """GitHub heading anchors, with `-1`, `-2` suffixes for repeated headings."""
    seen, anchors = {}, set()
    for heading in headings:
        base = _slug(_visible(heading))
        count = seen.get(base, 0)
        anchors.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return anchors


def _locator(reference, selected):
    """Return (path, fragment) for a locator, or None for prose.

    A whitespace-free path is a locator when it names a selected artifact or looks
    like a file path (contains `/` or `.`).
    """
    match = LOCATOR.fullmatch(reference.strip())
    if match is None:
        return None
    path = PurePosixPath(match["path"]).as_posix()
    if path not in selected and (not re.search(r"[/.]", path) or path.endswith(".")):
        return None
    return path, match["fragment"]


def _fragment_found(data, fragment):
    try:
        if fragment.startswith("/"):
            return resolve_pointer(loads(data), fragment)[1]
        # Match editors and the Ethos validator: ignore a BOM and accept any newline style.
        content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except (ValueError, UnicodeDecodeError, RecursionError):
        return False
    lines = LINES.fullmatch(fragment)
    if lines:
        first, last = int(lines[1]), int(lines[2] or lines[1])
        count = len(content.split("\n")) - content.endswith("\n") if content else 0
        return first <= last <= count
    headings = _headings(content)
    return (
        fragment in headings
        or fragment in {_visible(heading) for heading in headings}
        or fragment in _anchors(headings)
    )


class _Sources:
    """Read each selected path once, checking its selected digest."""

    def __init__(self, root, artifacts):
        self.root = root
        self.records = {row["path"]: row for row in artifacts if isinstance(row, dict) and text(row.get("path"))}
        self.cache = {}

    def data(self, path):
        if path not in self.cache:
            data, reason = None, None
            if self.records[path].get("access") == "available":
                data, reason = read_selected(self.root, path)
            self.cache[path] = data, reason
        return self.cache[path]

    def status(self, path, fragment):
        record = self.records.get(path)
        if record is None:
            try:
                missing = not os.path.lexists(local_file(self.root, path))
            except ValueError:  # Absolute, escaping, or symlinked paths can't be checked without a selection.
                missing = False
            return "broken" if missing else "unselected"
        data, reason = self.data(path)
        if data is None:
            return "invalid" if reason == "reference_invalid" else "unreadable"
        if record.get("digest") is not None and record["digest"] != hashlib.sha256(data).hexdigest():
            return "changed"
        return "resolved" if fragment is None or _fragment_found(data, fragment) else "broken"


def _plan(data):
    """Return (requirements, checks) or None; full validation belongs to construction_contract.py."""
    try:
        plan = loads(data)
    except (ValueError, RecursionError):
        return None
    if not isinstance(plan, dict) or not isinstance(plan.get("requirements"), list):
        return None
    requirements, checks = plan["requirements"], plan.get("checks", [])
    if not isinstance(checks, list):
        return None
    for row in requirements:
        authority = row.get("authority") if isinstance(row, dict) else None
        if (
            not text(row.get("id") if isinstance(row, dict) else None)
            or not isinstance(authority, dict)
            or authority.get("basis") not in BASES
            or not text(authority.get("reference"))
        ):
            return None
    for row in checks:
        if (
            not isinstance(row, dict)
            or not text(row.get("id"))
            or not isinstance(row.get("requirements"), list)
            or not all(text(ref) for ref in row["requirements"])
        ):
            return None
    return requirements, checks


def check_authority(root, artifacts, selected):
    """Resolve each requirement's authority reference in one selected construction plan.

    A reference written as a locator (`path`, `path#Heading`, `path#L3-L5`, or
    `path#/json/pointer`) must name a selected artifact whose current bytes contain
    it. Prose references are reported as not checkable, never as broken; the
    checker does not infer what they mean. Resolution shows the cited source
    exists, not that it supports the requirement.
    """
    if (
        not isinstance(selected, dict)
        or set(selected) != {"plan_artifact_id"}
        or not text(selected["plan_artifact_id"])
    ):
        raise ValueError("invalid authority selection")
    ident = selected["plan_artifact_id"]
    matches = [row for row in artifacts if isinstance(row, dict) and row.get("id") == ident]
    if len(matches) != 1 or matches[0].get("role") != "construction" or not text(matches[0].get("path")):
        raise ValueError("invalid authority selection")
    root = Path(root).resolve()
    sources = _Sources(root, artifacts)
    result = {"plan_artifact_id": ident, "requirements": [], "counts": None}
    path = matches[0]["path"]
    status = sources.status(path, None)
    found = STATUS[status]
    if found:
        return _done(result, [diagnostic(found[0].replace("reference", "plan"), ident, found[1])])
    plan = _plan(sources.data(path)[0])
    if plan is None:
        return _done(result, [diagnostic("authority.plan_malformed", ident)])
    requirements, checks = plan
    declared = {row["id"] for row in requirements}
    diagnostics = [
        diagnostic("authority.check_dangling", row["id"]) for row in checks if not set(row["requirements"]) <= declared
    ]
    for row in requirements:
        basis, reference = row["authority"]["basis"], row["authority"]["reference"]
        locator = _locator(reference, sources.records)
        if basis == "unresolved":
            state = "unresolved"
        elif locator is None:
            state = "prose"
        else:
            state = sources.status(*locator)
        mapped = [check["id"] for check in checks if row["id"] in check["requirements"]]
        result["requirements"].append(
            {"id": row["id"], "basis": basis, "reference": reference, "status": state, "checks": mapped}
        )
        found = STATUS[state]
        if found:
            diagnostics.append(diagnostic(found[0], row["id"], found[1]))
        if not mapped:
            diagnostics.append(diagnostic("authority.requirement_unmapped", row["id"], "unknown"))
    rows = result["requirements"]
    result["counts"] = {
        "requirements": len(rows),
        "checks": len(checks),
        "mapped": sum(bool(row["checks"]) for row in rows),
        "by_basis": {basis: sum(row["basis"] == basis for row in rows) for basis in sorted(BASES)},
        "by_status": {state: sum(row["status"] == state for row in rows) for state in STATUS},
    }
    return _done(result, diagnostics)


def _done(result, diagnostics):
    summary = finish([], diagnostics)
    return {**result, "status": summary["status"], "diagnostics": summary["diagnostics"]}
