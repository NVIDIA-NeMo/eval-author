# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_content as content  # noqa: E402
import report_layout as layout  # noqa: E402

VERSION = 1
OUTPUT = ".eval-author/audit-coverage-report.md"
HEADER = "[eval-author-audit-report]: urn:eval-author:audit-report:"


def literal(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#!|~<>&])", r"\\\1", value)


def cell(value: str) -> str:
    return " ".join(literal(value).splitlines())


def heading_slug(title: str) -> str:
    return re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")


def md_link(label: str, target: str) -> str:
    """Every report link: escaped label and a percent-encoded target, never a title."""
    return f"[{literal(label)}]({target})"


def path_target(path: str) -> str:
    return quote(posixpath.relpath(path, posixpath.dirname(OUTPUT)), safe="/.")


def anchor_target(heading: str) -> str:
    return "#" + heading_slug(heading)


def task_identifier(model: content.Content, identifier: str) -> str:
    task = model.tasks[identifier]
    name = model.source(task["source"]).name
    if name == identifier or name.startswith(identifier + "-"):
        return name
    return identifier if identifier.endswith("-" + name) else identifier + "-" + name


TASK_ENTRY_FILES = ("README.md", "instruction.md", "task.toml")


def task_link(model: content.Content, identifier: str) -> str:
    task = model.tasks[identifier]
    path = model.sources[task["source"]]["path"]
    entry = next((name for name in TASK_ENTRY_FILES if (model.source(task["source"]) / name).is_file()), None)
    return md_link(task_identifier(model, identifier), path_target(posixpath.join(path, entry) if entry else path))


def task_aliases(model: content.Content) -> dict[str, str]:
    return {name: identifier for identifier in model.tasks for name in (identifier, task_identifier(model, identifier))}


def plain_task_references(model: content.Content, value: str) -> str:
    identifiers = task_aliases(model)
    if not identifiers:
        return value
    names = "|".join(re.escape(name) for name in sorted(identifiers, key=len, reverse=True))
    pattern = r"\\?\[(" + names + r')\\?\]\\?\((?:\\.|[^()\n"\\]|"(?:\\.|[^"\\])*")*\)'
    return re.sub(pattern, lambda match: match[1], value)


def task_mentions(model: content.Content, value: str):
    identifiers = task_aliases(model)
    if not identifiers:
        return
    pattern = (
        r"(?<![\w./:-])("
        + "|".join(re.escape(name) for name in sorted(identifiers, key=len, reverse=True))
        + r")(?![\w/-])"
    )
    for match in re.finditer(pattern, value):
        yield match, identifiers[match[0]]


FINDING_ID = content.FINDING_REFERENCE
GAP = "coverage_gaps"


def is_advisory(row: dict) -> bool:
    return row["category"] == "verifier_cheating" and row["status"] != "unproven"


def has_coverage_gaps(model: content.Content) -> bool:
    """A coverage-gaps finding exists only when an item lacks demonstrated coverage or a mapped task."""
    if "_has_gaps" not in model.__dict__:
        covered, measured, _ = measurements(model)
        items = model.spec["items"]
        mapped = {row["item"] for row in model.data["task_mappings"] if row["tasks"]}
        model.__dict__["_has_gaps"] = (
            any(row["category"] == "missing_task" for row in model.data["findings"])
            or any(item["name"] not in mapped for item in items)
            or any(item["name"] not in covered for item in items if item["kind"] in measured)
        )
    return model.__dict__["_has_gaps"]


def finding_labels(model: content.Content) -> dict[str, str]:
    """Map content finding IDs to report labels: Advisory 1..n, then Finding 1 (coverage gaps, when any) and the rest."""
    data = model.data
    labels = {row["id"]: f"Advisory {index}" for index, row in enumerate(filter(is_advisory, data["findings"]), 1)}
    count = 0
    if has_coverage_gaps(model):
        labels[GAP] = "Finding 1"
        count = 1
    for row in data["findings"]:
        if row["id"] in labels:
            continue
        if row["category"] == "missing_task":
            labels[row["id"]] = labels[GAP]
        else:
            count += 1
            labels[row["id"]] = f"Finding {count}"
    return labels


def relabel(model: content.Content, value: str) -> str:
    labels = finding_labels(model)
    return FINDING_ID.sub(lambda match: labels.get(match[1], match[0]), value)


def display_text(model: content.Content, value: str) -> str:
    value = plain_task_references(model, relabel(model, value))
    parts, start = [], 0
    for match, identifier in task_mentions(model, value):
        parts.extend([value[start : match.start()], task_identifier(model, identifier)])
        start = match.end()
    return "".join([*parts, value[start:]])


def prose(model: content.Content, value: str) -> str:
    value = plain_task_references(model, relabel(model, value))
    parts, start = [], 0
    for match, identifier in task_mentions(model, value):
        parts.extend([literal(value[start : match.start()]), task_link(model, identifier)])
        start = match.end()
    return " ".join("".join([*parts, literal(value[start:])]).splitlines())


def advisory_status(model: content.Content, row: dict) -> str:
    if row["status"] == "resolved":
        return " (resolved)"
    if any(ref["revision"] != model.tasks[ref["task_id"]]["revision"] for ref in row["tasks"]):
        return " (historical, unresolved)"
    return ""


def finding_heading(model: content.Content, identifier: str) -> str:
    labels = finding_labels(model)
    if identifier == GAP or labels[identifier] == labels.get(GAP):
        return labels[GAP] + " - " + layout.COPY["coverage_gap_title"]
    row = next(row for row in model.data["findings"] if row["id"] == identifier)
    suffix = advisory_status(model, row) if is_advisory(row) else ""
    return labels[identifier] + " - " + finding_title(model, row) + suffix


def finding_title(model: content.Content, row: dict) -> str:
    value = plain_task_references(model, row["title"])
    parts, start = [], 0
    for match, _ in task_mentions(model, value):
        parts.append(value[start : match.start()])
        start = match.end()
    title = " ".join("".join([*parts, value[start:]]).split()).strip(" ,:&/-")
    title = re.sub(r"^(?:and|or)\s+", "", title, flags=re.I)
    title = title or layout.FINDING_CATEGORIES[row["category"]]
    return title[0].upper() + title[1:]


def task_list(model: content.Content, identifiers: list[str]) -> str:
    names = [task_link(model, identifier) for identifier in dict.fromkeys(identifiers)]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


def finding_explanation(model: content.Content, row: dict) -> str:
    value = plain_task_references(model, row["explanation"])
    named = {identifier for _, identifier in task_mentions(model, value)}
    unnamed = [ref["task_id"] for ref in row["tasks"] if ref["task_id"] not in named]
    prefix = task_list(model, unnamed) + ": " if unnamed else ""
    return prefix + prose(model, value)


def reference(model: content.Content, identifier: str, label: str | None = None) -> str:
    if identifier in model.tasks:
        return task_link(model, identifier)
    labels = finding_labels(model)
    if identifier not in labels:
        recommendation = next((row for row in model.data["recommendations"] if row["id"] == identifier), None)
        if recommendation is None:
            raise ValueError(f"No report anchor for {identifier!r}")
        identifier = recommendation["finding_id"]
    heading = finding_heading(model, identifier)
    return md_link(label or labels[identifier], anchor_target(heading))


def link(model: content.Content, identifier: str, label: str | None = None) -> str:
    source = model.sources[identifier]
    if label is None and source["kind"] == "task":
        task_id = next((row["id"] for row in model.data["tasks"] if row["source"] == identifier), None)
        if task_id is not None:
            return task_link(model, task_id)
    return md_link(label or posixpath.basename(source["path"].rstrip("/")), path_target(source["path"]))


def common_dir(paths: list[str]) -> str:
    return posixpath.commonpath([posixpath.dirname(path.rstrip("/")) or "." for path in paths]) or "."


def run_roots(model: content.Content) -> str:
    """Selected runs: run records plus bound measured traces, one entry per run ID."""
    paths = {row["id"]: model.sources[row["source"]]["path"].rstrip("/") for row in model.data["runs"]}
    for entry in measurements(model)[2]:
        trace = content.trace_path(model, entry).relative_to(model.repo).as_posix()
        paths.setdefault(entry["subject"]["run_id"], trace)
    return run_roots_text(list(paths.values()))


def run_roots_text(paths: list[str]) -> str:
    """Where the selected runs live: their deepest shared directory, split by top-level tree when that is too broad."""
    if not paths:
        return layout.COPY["no_runs"]
    report_dir = posixpath.dirname(OUTPUT)
    root = common_dir(paths)
    if root in {".", ""} or report_dir == root or report_dir.startswith(root + "/"):
        groups: dict[str, list[str]] = {}
        for path in paths:
            relative = posixpath.relpath(path, root).split("/")
            groups.setdefault(relative[0] if len(relative) > 1 else "", []).append(path)
        roots = [(common_dir(group), len(group)) for _, group in sorted(groups.items())]
    else:
        roots = [(root, len(paths))]
    word = layout.COPY["run_word"]
    parts = [
        f"{count} in `{posixpath.relpath(path, report_dir)}/`"
        if len(roots) > 1
        else f"`{posixpath.relpath(path, report_dir)}/`"
        for path, count in roots
    ]
    total = len(paths)
    return (
        f"{total} selected {word if total == 1 else word + 's'}"
        + (": " if len(roots) > 1 else " in ")
        + ", ".join(parts)
    )


SCOPE_SOURCES = {"audit", "ethos"}


def evidence(model: content.Content, references: list[str]) -> str:
    """Cite each source once; files sharing a name are cited once, linked to the first."""
    labels: dict[str, str] = {}
    for identifier in dict.fromkeys(references):
        source = model.sources[identifier]
        key = posixpath.basename(source["path"].rstrip("/")) if source["kind"] != "task" else identifier
        labels.setdefault(key, link(model, identifier))
    return ", ".join(labels.values())


def intended_check(model: content.Content, item: dict) -> str:
    parts = []
    for key, label in layout.INTENDED_FIELDS:
        value = item.get(key)
        if value:
            parts.append(label + ": " + prose(model, ", ".join(value) if isinstance(value, list) else value))
    for evidence_item in item["evidence_required"]:
        description = "Required evidence: " + prose(model, evidence_item["description"])
        if evidence_item["kind"] == "tool_call":
            description += " Tool: " + literal(evidence_item["tool"])
        parts.append(description)
    return " ".join(part if part.endswith((".", "!", "?")) else part + "." for part in parts)


def table(kind: str, rows: list[list[str]]) -> list[str]:
    headings = layout.TABLES[kind]
    return [
        "| " + " | ".join(headings) + " |",
        "| " + " | ".join("---" for _ in headings) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
        "",
    ]


def measurements(model: content.Content) -> tuple[set[str], set[str], list[dict]]:
    report = model.aggregate
    if report is None:
        return set(), set(), []
    # Only inputs explicitly labeled as the agent under test establish coverage. The aggregate's
    # agent bucket also folds in unlabeled inputs, so coverage is recomputed from the labeled ones.
    if "agent_under_test" not in report.get("coverage_by_evidence_class", {}):
        return set(), set(), []
    inputs = [row for row in report["input_reports"] if row["subject"].get("evidence_class") == "agent_under_test"]
    covered = {name for row in inputs for name in row["covered"]}
    return covered, {row["item_kind"] for row in inputs}, inputs


def coverage_summary(model: content.Content) -> str:
    """The coverage measurement state; the definition follows in its own paragraph."""
    _, measured, _ = measurements(model)
    declared = {row["kind"] for row in model.spec["items"]}
    labels = {kind: title.lower() for kind, title in layout.KINDS}

    def names(kinds):
        values = [labels[kind] for kind, _ in layout.KINDS if kind in kinds]
        return ", ".join(values[:-1]) + " and " + values[-1] if len(values) > 1 else "".join(values)

    assessed = declared & measured
    remaining = declared - measured
    if not declared:
        key = "summary_coverage_empty"
    elif not assessed:
        key = "summary_coverage_unmeasured"
    elif remaining:
        key = "summary_coverage_partial"
    else:
        key = "summary_coverage_measured"
    return layout.COPY[key].format(measured=names(assessed), unmeasured=names(remaining))


def capability_coverage(model: content.Content, covered: set[str], measured: set[str]) -> tuple[int, int, str, bool]:
    """Covered count, total, ratio, and basis: demonstrated in runs once measured, otherwise mapped to a task."""
    capabilities = [row["name"] for row in model.spec["items"] if row["kind"] == "capability"]
    demonstrated = "capability" in measured
    hits = covered if demonstrated else {row["item"] for row in model.data["task_mappings"] if row["tasks"]}
    count = sum(name in hits for name in capabilities)
    ratio = f"{count / len(capabilities):.0%}" if capabilities else layout.COPY["no_items"]
    return count, len(capabilities), ratio, demonstrated


def capability_ratio(model: content.Content, covered: set[str], measured: set[str]) -> str | None:
    count, total, ratio, demonstrated = capability_coverage(model, covered, measured)
    if not total:
        return None
    key = "summary_ratio_measured" if demonstrated else "summary_ratio_mapped"
    return layout.COPY[key].format(covered=count, count=total, ratio=ratio)


def verifier_summary(model: content.Content) -> str:
    findings = [row for row in model.data["findings"] if row["category"] in layout.VERIFIER_CATEGORIES]
    if any(row["category"] == "verifier_cheating" and row["status"] == "confirmed" for row in findings):
        return layout.COPY["summary_cheating"]
    if any(row["status"] == "confirmed" for row in findings):
        return layout.COPY["summary_verifier_issues"]
    if any(row["status"] == "unproven" for row in findings):
        return layout.COPY["summary_verifier_unproven"]
    if findings:
        return layout.COPY["summary_verifier_resolved"]
    if any(
        row["classification"] in {"failed_control", "false_rejection"}
        or row.get("historical_classification") in {"failed_control", "false_rejection"}
        for row in model.controls
    ):
        return layout.COPY["summary_verifier_issues"]
    if model.tasks and model.data["verifier_review"]["checks"]:
        return layout.COPY["summary_no_tampering"]
    return layout.COPY["not_assessed"]


def finding_paragraph(model: content.Content, row: dict) -> str:
    basis = "code inspection" if row["basis"] == "inspection" else "executed controls"
    named = {model.tasks[ref["task_id"]]["source"] for ref in row["tasks"]}
    cited = [
        identifier
        for identifier in dict.fromkeys(row["evidence"])
        if identifier not in named and model.sources[identifier]["kind"] not in SCOPE_SOURCES
    ]
    own_task_cited = any(identifier in named for identifier in row["evidence"])
    sentences = layout.FINDING_SENTENCES if cited or own_task_cited else layout.FINDING_SENTENCES_UNCITED
    citation = sentences[row["status"]].format(
        basis=basis,
        basis_subject=basis.capitalize(),
        category=layout.FINDING_CATEGORIES[row["category"]],
        evidence=evidence(model, cited) if cited else layout.COPY["task_files"],
    )
    return finding_explanation(model, row) + " " + citation


def proposal_lead(model: content.Content, row: dict, jump) -> str:
    """The finding link, plus the revised task's link, which the proposal text cannot repeat."""
    lead = jump(row["id"])
    if row["action"] != "revise_task":
        return lead
    finding = next(item for item in model.data["findings"] if item["id"] == row["finding_id"])
    task_ids = list(dict.fromkeys(ref["task_id"] for ref in finding["tasks"]))
    return lead + (" (" + ", ".join(task_link(model, task_id) for task_id in task_ids) + ")" if task_ids else "")


def proposed_tasks(model: content.Content, finding_ids: set[str]) -> list[str]:
    lines = []
    for row in model.data["recommendations"]:
        if row["finding_id"] in finding_ids and row["proposed_task"] is not None:
            lines.extend([layout.COPY["proposed_task"] + " " + prose(model, row["proposed_task"]), ""])
    return lines


def _finding(model: content.Content, row: dict, level: int = 3) -> list[str]:
    lines = ["#" * level + " " + literal(finding_heading(model, row["id"])), "", finding_paragraph(model, row), ""]
    if is_advisory(row):
        consequence = (
            row["advisory"]
            if row["advisory"] is not None
            else layout.COPY["resolved_advisory"]
            if row["status"] == "resolved"
            else layout.COPY["unresolved_advisory"]
        )
        lines.extend([prose(model, consequence), ""])
    return lines + proposed_tasks(model, {row["id"]})


def count_phrase(counts: list[tuple[int, int, str]]) -> str:
    values = [f"{missing} of {total} {label}" for missing, total, label in counts]
    return ", ".join(values[:-1]) + " and " + values[-1] if len(values) > 1 else "".join(values)


def coverage_gap_finding(model: content.Content, covered: set[str], measured: set[str]) -> list[str]:
    items = model.spec["items"]
    mapped = {row["item"] for row in model.data["task_mappings"] if row["tasks"]}
    labels = {kind: title.lower() for kind, title in layout.KINDS}
    declared = [kind for kind, _ in layout.KINDS if any(item["kind"] == kind for item in items)]

    def counts(kinds, missing):
        rows = []
        for kind in kinds:
            kind_items = [item for item in items if item["kind"] == kind]
            rows.append((sum(missing(item) for item in kind_items), len(kind_items), labels[kind]))
        return rows

    sentences = []
    if not declared:
        sentences.append(layout.COPY["summary_coverage_empty"])
    else:
        assessed = [kind for kind in declared if kind in measured]
        if assessed:
            phrase = count_phrase(counts(assessed, lambda item: item["name"] not in covered))
            sentences.append(layout.COPY["gap_undemonstrated"].format(counts=phrase[0].upper() + phrase[1:]))
        unmapped = counts(declared, lambda item: item["name"] not in mapped)
        if any(missing for missing, _, _ in unmapped):
            phrase = count_phrase(unmapped)
            sentences.append(layout.COPY["gap_unmapped"].format(counts=phrase[0].upper() + phrase[1:]))
        else:
            sentences.append(layout.COPY["gap_all_mapped"])
        sentences.append(
            layout.COPY["gap_reference"].format(
                coverage_report=md_link(layout.SECTIONS[5][1], anchor_target(layout.SECTIONS[5][1]))
            )
        )
    gaps = [row for row in model.data["findings"] if row["category"] == "missing_task"]
    lines = ["### " + literal(finding_heading(model, GAP)), "", " ".join(sentences), ""]
    for row in gaps:
        lead = "**" + literal(finding_title(model, row).rstrip(".")) + ".** "
        lines.append("- " + lead + finding_explanation(model, row))
        lines.extend("  - " + line for line in proposed_tasks(model, {row["id"]}) if line)
    return lines + ([""] if gaps else [])


def build(model: content.Content) -> tuple[str, dict[int, str]]:
    data = model.data
    identity = data["identity"]
    covered, measured, inputs = measurements(model)
    items = model.spec["items"]
    mappings = {row["item"]: row for row in data["task_mappings"]}
    task_for_run = {row["id"]: row["task_id"] for row in data["runs"]}
    declared_kinds = {row["kind"] for row in items}

    def jump(identifier: str, label: str | None = None) -> str:
        return reference(model, identifier, label)

    lines: list[str] = []
    owners: dict[int, str] = {}

    def add(parts: list[str], field: str):
        for part in parts:
            owners[len(lines)] = field
            lines.append(part)

    def section(index: int, field: str):
        level, title = layout.SECTIONS[index]
        add(["#" * level + " " + title, ""], field)

    add(
        [
            f"# Audit coverage report: {literal(model.spec['agent'])}",
            "",
            f"- **Date:** {identity['date']}",
            "- **Prepared by:** " + md_link("Eval Author", layout.EVAL_AUTHOR_URL),
            f"- **Repository:** {cell(identity['repository'])}",
            f"- **Audited commit:** {identity['commit']}" + (" (dirty working tree)" if identity["dirty"] else ""),
            "",
        ],
        "identity",
    )
    section(0, "summary")
    cheats = [row for row in data["findings"] if is_advisory(row)]
    key_findings = [row for row in data["findings"] if not is_advisory(row) and row["category"] != "missing_task"]
    gaps_present = has_coverage_gaps(model)
    finding_count = len(key_findings) + (1 if gaps_present else 0)
    task_proposals = [row for row in data["recommendations"] if row["proposed_task"] is not None]
    counts = layout.SUMMARY_COUNTS.format(
        advisories=len(cheats),
        advisory_word="advisory" if len(cheats) == 1 else "advisories",
        findings=finding_count,
        finding_word="finding" if finding_count == 1 else "findings",
        tasks=len(task_proposals),
        task_word="task" if len(task_proposals) == 1 else "tasks",
    )
    found, limits = data["summary"]
    ratio = capability_ratio(model, covered, measured)
    add([" ".join(filter(None, [counts, ratio, verifier_summary(model), prose(model, found)])), ""], "summary[0]")
    add([coverage_summary(model) + " " + prose(model, limits), ""], "summary[1]")
    section(1, "inputs.measurement")
    count_covered, count, ratio, demonstrated = capability_coverage(model, covered, measured)
    add(
        table(
            "current",
            [
                ["Capabilities", str(count)],
                ["Capabilities covered", str(count_covered)],
                ["Coverage ratio", ratio],
                ["Coverage basis", layout.COPY["basis_demonstrated" if demonstrated else "basis_mapped"]],
            ],
        ),
        "inputs.measurement",
    )
    if cheats:
        add(["### " + layout.SUBHEADINGS["advisory"], ""], "findings")
        for row in cheats:
            add(_finding(model, row, level=4), f"findings[{data['findings'].index(row)}]")
    section(2, "findings")
    if gaps_present:
        add(coverage_gap_finding(model, covered, measured), "findings")
    elif not key_findings:
        add([layout.COPY["no_findings"], ""], "findings")
    for row in key_findings:
        add(_finding(model, row), f"findings[{data['findings'].index(row)}]")
    section(3, "recommendations")
    add(
        [
            layout.COPY["recommended_with_ethos" if data["inputs"]["ethos"] else "recommended"],
            "",
        ],
        "recommendations",
    )
    if data["recommendations"]:
        add(["### " + layout.SUBHEADINGS["next_actions"], ""], "recommendations")
        ranked = sorted(data["recommendations"], key=lambda row: row["order"])
        add(
            [
                f"{rank}. {layout.PRIORITIES[row['priority']]} priority - {layout.ACTIONS[row['action']]}: "
                + jump(row["finding_id"])
                for rank, row in enumerate(ranked, 1)
            ],
            "recommendations",
        )
        add([""], "recommendations")
    for kind, title in layout.KINDS:
        add(["### " + title, ""], "recommendations")
        rows = []
        for item in items:
            proposals = [
                row
                for row in data["recommendations"]
                if item["name"] in row["items"] and row["proposed_task"] is not None
            ]
            if item["kind"] == kind and proposals:
                rows.append(
                    [
                        literal(item["name"]) + " - " + prose(model, item["description"]),
                        " ".join(proposal_lead(model, row, jump) + ": " + prose(model, row["proposed_task"]) for row in proposals),
                    ]
                )
        add(table("recommended", rows) if rows else [layout.COPY["no_proposals"], ""], "recommendations")
    section(4, "tasks")
    add([layout.COPY["catalog"], ""], "tasks")
    task_rows = []
    for index, task in enumerate(data["tasks"]):
        capabilities = []
        for mapping in data["task_mappings"]:
            if next(item["kind"] for item in items if item["name"] == mapping["item"]) != "capability":
                continue
            for ref in mapping["tasks"]:
                if ref["task_id"] == task["id"]:
                    capabilities.append(
                        literal(mapping["item"]) + (" (boundary only)" if ref["relationship"] == "boundary" else "")
                    )
        observed = sorted(
            {
                name
                for entry in inputs
                if entry["subject"]["run_id"] in task["run_ids"] and entry["item_kind"] == "tool"
                for name in entry["covered"]
            }
        )
        relevant = [row for row in data["findings"] if any(ref["task_id"] == task["id"] for ref in row["tasks"])]
        issues = "; ".join(
            jump(row["id"], finding_heading(model, row["id"]))
            + " ("
            + row["status"]
            + (
                ", historical"
                if any(ref["task_id"] == task["id"] and ref["revision"] != task["revision"] for ref in row["tasks"])
                else ""
            )
            + ")"
            for row in relevant
        )
        assessed = any(row["task_id"] == task["id"] for row in data["verifier_review"]["checks"])
        tools = (
            ", ".join(literal(name) for name in observed) or layout.COPY["none_observed"]
            if "tool" in measured
            else layout.COPY["unmeasured"]
        )
        task_rows.append(
            [
                task_link(model, task["id"]) + ": " + cell(display_text(model, task["title"])),
                prose(model, task["behavior"]),
                "Capabilities: "
                + (", ".join(capabilities) or "No intended mappings")
                + ". Tools: "
                + tools
                + (". " + " ".join(prose(model, note) for note in task["notes"]) if task["notes"] else ""),
                issues or (layout.COPY["no_issues"] if assessed else "Not assessed"),
            ]
        )
    add(table("tasks", task_rows) if task_rows else [layout.COPY["no_tasks"], ""], "tasks")
    section(5, "task_mappings")
    add([layout.COPY["coverage"], "", layout.COPY["coverage_evidence"], ""], "task_mappings")
    for kind, _ in layout.KINDS:
        add(["### " + layout.COVERAGE_HEADINGS[kind], ""], "task_mappings")
        rows = []
        for item in items:
            if item["kind"] != kind:
                continue
            mapping = mappings[item["name"]]
            matches = [entry for entry in inputs if item["name"] in entry["covered"]]
            task_ids = sorted(
                {
                    task_for_run[entry["subject"]["run_id"]]
                    for entry in matches
                    if entry["subject"]["run_id"] in task_for_run
                }
            )
            if item["name"] in covered:
                runs = len({entry["subject"]["run_id"] for entry in matches})
                status = layout.COPY["demonstrated"].format(runs=runs, run_word="run" if runs == 1 else "runs")
                if task_ids:
                    status += ": " + ", ".join(jump(task_id) for task_id in task_ids)
            else:
                status = layout.COPY["not_demonstrated"] if kind in measured else layout.COPY["unmeasured"]
            intended = ", ".join(
                jump(ref["task_id"]) + (" (boundary only)" if ref["relationship"] == "boundary" else "")
                for ref in mapping["tasks"]
            )
            rows.append(
                [
                    literal(item["name"]) + " - " + prose(model, item["description"]),
                    intended_check(model, item),
                    intended or layout.COPY["no_coverage"],
                    status,
                ]
            )
        add(table("coverage", rows) if rows else [layout.COPY["empty_kind"], ""], "task_mappings")
    section(6, "verifier_review")
    add([layout.COPY["screening_overview"], ""], "verifier_review")
    for family, description in layout.SCREENING_CHECKS.items():
        add(["- **" + layout.FAMILIES[family] + ":** " + description], "verifier_review")
    add([""], "verifier_review")
    if not data["verifier_review"]["checks"]:
        add([layout.COPY["not_assessed"], ""], "verifier_review")
    section(7, "scope_notes")
    add(
        [
            "- **Audit specification:** "
            + link(model, data["inputs"]["audit"])
            + ". Specification status: "
            + model.spec["status"]
            + ". Workflow state owns review decisions.",
            "- **Ethos:** "
            + (link(model, data["inputs"]["ethos"]) if data["inputs"]["ethos"] else "Not selected")
            + ".",
            "- **Run Evidence JSON:** "
            + (
                link(model, data["inputs"]["measurement"])
                if data["inputs"]["measurement"]
                else "Unavailable. Coverage is unmeasured"
            )
            + "."
            + (" " + layout.COPY["measure_coverage"] if declared_kinds - measured else ""),
            "- **Evaluations inspected:** " + (", ".join(jump(task["id"]) for task in data["tasks"]) or "None") + ".",
            "- **Run Evidence:** " + run_roots(model) + ".",
            "",
        ],
        "inputs",
    )
    historical: dict[tuple[str, str], list[str]] = {}
    for row in data["findings"]:
        for ref in row["tasks"]:
            if ref["revision"] != model.tasks[ref["task_id"]]["revision"]:
                historical.setdefault((ref["task_id"], ref["revision"]), []).append(row["id"])
    for (task_id, _), finding_ids in historical.items():
        add(
            [
                "- **Earlier task revision:** "
                + jump(task_id)
                + " changed after the evidence for "
                + ", ".join(jump(identifier) for identifier in finding_ids)
                + ".",
                "",
            ],
            "findings",
        )
    for index, note in enumerate(data["verifier_review"]["notes"]):
        add(["- **Verifier review:** " + prose(model, note), ""], f"verifier_review.notes[{index}]")
    for index, note in enumerate(data["scope_notes"]):
        add(
            [
                "- **"
                + prose(model, note["title"])
                + ":** "
                + prose(model, note["explanation"])
                + " "
                + evidence(model, note["evidence"]),
                "",
            ],
            f"scope_notes[{index}]",
        )
    for warning in model.warnings:
        if warning["rule"] == "measurement_warning":
            add(["- **Evidence warning:** " + literal(warning["fix"]), ""], "inputs.measurement")
    section(8, "definitions")
    add([f"- **{name}:** {description}" for name, description in layout.DEFINITIONS.items()] + [""], "definitions")
    return "\n".join(lines).rstrip() + "\n", owners


def task_mention_errors(model: content.Content) -> list[dict]:
    """Each task is named once per finding, across its explanation, advisory, and proposed tasks."""
    errors = []
    for index, row in enumerate(model.data["findings"]):
        fields = [(f"content.findings[{index}].explanation", row["explanation"])]
        if row["advisory"] is not None:
            fields.append((f"content.findings[{index}].advisory", row["advisory"]))
        fields.extend(
            (f"content.recommendations[{position}].proposed_task", recommendation["proposed_task"])
            for position, recommendation in enumerate(model.data["recommendations"])
            if recommendation["finding_id"] == row["id"] and recommendation["proposed_task"] is not None
        )
        mentions: dict[str, list[str]] = {}
        for field, value in fields:
            for _, identifier in task_mentions(model, plain_task_references(model, value)):
                mentions.setdefault(identifier, []).append(field)
        explanation = f"content.findings[{index}].explanation"
        for ref in row["tasks"]:
            if explanation not in mentions.get(ref["task_id"], []):
                # The renderer opens the explanation with the task when the explanation does not name it.
                mentions.setdefault(ref["task_id"], []).insert(0, explanation)
        for identifier, places in mentions.items():
            if len(places) > 1:
                errors.extend(
                    content.issue(
                        field,
                        "repeated_task_reference",
                        layout.COPY["repeated_task_fix"].format(task=identifier),
                    )
                    for field in dict.fromkeys(places[1:])
                )
    return errors


def render(model: content.Content) -> tuple[bytes, dict[int, str]]:
    errors = task_mention_errors(model)
    if errors:
        raise content.ContentError(errors)
    body, owners = build(model)
    metadata = {
        "renderer_version": VERSION,
        "validator_version": content.VERSION,
        "content_sha256": model.digest,
        "body_sha256": content.sha(body.encode()),
        "sources_sha256": content.sha(json.dumps(model.data["sources"], sort_keys=True).encode()),
    }
    return (HEADER + quote(json.dumps(metadata, sort_keys=True), safe="") + "\n\n" + body).encode(), owners


def refresh(model: content.Content, *, convert_legacy: str | None = None, discard_edits: str | None = None) -> dict:
    output = content.local(model.repo, OUTPUT)
    if output == model.path or any(
        output == path or path.is_dir() and output.is_relative_to(path) for path in model.paths.values()
    ):
        raise content.ContentError(
            [
                content.issue(
                    "content.sources",
                    "output_is_input",
                    "Store content and retained evidence separately from the generated report",
                )
            ]
        )
    rendered, owners = render(model)
    previous = content.read_bytes(output) if output.exists() else None
    backup_kind = None
    if previous is not None:
        first, _, body = previous.partition(b"\n\n")
        if not first.startswith(HEADER.encode()):
            backup_kind = "legacy-report"
            if convert_legacy != content.sha(previous):
                raise content.ContentError(
                    [
                        content.issue(
                            "content",
                            "legacy_report",
                            "Keep the legacy report. After explicit conversion is requested, author validated report-content.json and pass --convert-legacy "
                            + content.sha(previous),
                        )
                    ]
                )
        else:
            try:
                header = json.loads(unquote(first.decode()[len(HEADER) :]))
                modified = header["body_sha256"] != content.sha(body)
            except (ValueError, KeyError):
                modified = True
            if modified and discard_edits == content.sha(previous):
                backup_kind = "edited-report"
            elif modified:
                diff = list(
                    difflib.unified_diff(
                        rendered.decode().splitlines(),
                        previous.decode().splitlines(),
                        fromfile="generated",
                        tofile="edited",
                        lineterm="",
                    )
                )
                positions = [
                    i
                    for tag, i, _, _, _ in difflib.SequenceMatcher(
                        None, rendered.decode().splitlines()[2:], body.decode().splitlines()
                    ).get_opcodes()
                    if tag != "equal"
                ]
                fields = sorted({owners.get(position, "summary") for position in positions}) or ["summary"]
                raise content.ContentError(
                    [
                        {
                            **content.issue(
                                "content." + field,
                                "generated_output_modified",
                                "Edit report-content.json at this field and preserve the generated output. "
                                "To discard the edits instead, rerun with --discard-edits " + content.sha(previous),
                            ),
                            "diff": "\n".join(diff),
                        }
                        for field in fields
                    ]
                )
    if previous == rendered:
        current = content.load(model.path, model.repo)
        if current.digest != model.digest:
            raise content.ContentError(
                [content.issue("content", "concurrent_change", "Reload content before rendering")]
            )
        return {**model.result(), "written": False, "report": OUTPUT, "report_sha256": content.sha(rendered)}
    # Revalidate input bytes immediately before publication, including source bindings.
    current = content.load(model.path, model.repo)
    if current.digest != model.digest:
        raise content.ContentError([content.issue("content", "concurrent_change", "Reload content before rendering")])
    output.parent.mkdir(parents=True, exist_ok=True)
    if backup_kind:
        assert previous is not None
        backup = content.local(model.repo, f".eval-author/.working/audit/{backup_kind}-{content.sha(previous)}.md")
        if backup.exists() and content.read_bytes(backup) != previous:
            raise ValueError("Backup changed. Preserve it before replacing the report")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(previous)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".audit-report-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        actual = content.read_bytes(output) if output.exists() else None
        if actual != previous:
            raise ValueError("Report changed during refresh. Reload before retrying")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**model.result(), "written": True, "report": OUTPUT, "report_sha256": hashlib.sha256(rendered).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--content", type=Path, default=Path(content.CONTENT_PATH))
    parser.add_argument("--lint-prose", action="store_true")
    parser.add_argument("--convert-legacy", metavar="SHA256")
    parser.add_argument("--discard-edits", metavar="SHA256", help="Replace a hand-edited report; the edited copy is kept")
    args = parser.parse_args(argv)
    try:
        result = refresh(
            content.load(args.content, args.repo, lint=args.lint_prose),
            convert_legacy=args.convert_legacy,
            discard_edits=args.discard_edits,
        )
    except content.ContentError as exc:
        print(json.dumps({"valid": False, "written": False, "errors": exc.errors}, indent=2))
        return 1
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(
            json.dumps(
                {"valid": False, "written": False, "errors": [content.issue("content", "render", str(exc))]}, indent=2
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
