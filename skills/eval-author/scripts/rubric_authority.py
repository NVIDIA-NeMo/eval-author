# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve a construction plan's grading-authority references against selected artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from eval_author_shared.task_evidence import loads
from rubric_identity import diagnostic, finish, read_selected, resolve_pointer, text

BASES = {"specification", "user_decision", "independent_reference", "unresolved"}
# A locator is a whitespace-free path, optionally followed by `#fragment`.
LOCATOR = re.compile(r"(?P<path>[^\s#]+)(?:#(?P<fragment>\S.*))?")
LINES = re.compile(r"L([1-9][0-9]*)(?:-L([1-9][0-9]*))?")
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
STATUS = {
    "resolved": None,
    "prose": ("authority.reference_prose", "unknown"),
    "unresolved": ("authority.unresolved", "unknown"),
    "unselected": ("authority.reference_unselected", "unknown"),
    "unreadable": ("authority.reference_unreadable", "unknown"),
    "changed": ("authority.reference_changed", "fail"),
    "broken": ("authority.reference_broken", "fail"),
}


def _slug(heading):
    """GitHub-style heading anchor."""
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


def _locator(reference):
    match = LOCATOR.fullmatch(reference.strip())
    if match is None or not re.search(r"[/.]", match["path"]) or match["path"].endswith("."):
        return None
    return match["path"], match["fragment"]


def _fragment_found(data, fragment):
    try:
        if fragment.startswith("/"):
            return resolve_pointer(loads(data), fragment)[1]
        content = data.decode("utf-8")
    except (ValueError, UnicodeDecodeError, RecursionError):
        return False
    lines = LINES.fullmatch(fragment)
    if lines:
        first, last = int(lines[1]), int(lines[2] or lines[1])
        return first <= last <= len(content.splitlines())
    headings = HEADING.findall(content)
    return fragment in headings or fragment in {_slug(heading) for heading in headings}


class _Sources:
    """Read each selected path once, checking its selected digest."""

    def __init__(self, root, artifacts):
        self.root = root
        self.records = {row["path"]: row for row in artifacts if isinstance(row, dict) and text(row.get("path"))}
        self.cache = {}

    def status(self, path, fragment):
        record = self.records.get(path)
        if record is None:
            return "unselected"
        if path not in self.cache:
            data = None
            if record.get("access") == "available":
                data, _ = read_selected(self.root, path)
            self.cache[path] = data
        data = self.cache[path]
        if data is None:
            return "unreadable"
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
        if not isinstance(row, dict) or not text(row.get("id")) or not isinstance(row.get("requirements"), list):
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
    if status != "resolved":
        return _done(
            result, [diagnostic("authority.plan_" + status, ident, "fail" if status == "changed" else "unknown")]
        )
    plan = _plan(sources.cache[path])
    if plan is None:
        return _done(result, [diagnostic("authority.plan_invalid", ident)])
    requirements, checks = plan
    declared = {row["id"] for row in requirements}
    diagnostics = [
        diagnostic("authority.check_dangling", row["id"]) for row in checks if not set(row["requirements"]) <= declared
    ]
    for row in requirements:
        basis, reference = row["authority"]["basis"], row["authority"]["reference"]
        locator = _locator(reference)
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
