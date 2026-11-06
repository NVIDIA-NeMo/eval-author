# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/eval-author-audit"
SCRIPTS = SKILL / "scripts/audit_spec"
EXAMPLE = SKILL / "examples"


@pytest.fixture
def audit_report(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    content = importlib.import_module("report_content")
    renderer = content.load_module("audit_markdown_renderer", SCRIPTS / "render_report.py")
    shutil.copytree(EXAMPLE / "report-sources", tmp_path / "report-sources")
    shutil.copy2(EXAMPLE / "report-content.json", tmp_path / "report-content.json")
    return content, renderer, tmp_path


def save(root, data):
    (root / "report-content.json").write_text(json.dumps(data, indent=2) + "\n")


def example_data(root):
    return json.loads((root / "report-content.json").read_text())


def load(fixture, data=None):
    content, _, root = fixture
    if data is not None:
        save(root, data)
    return content.load(Path("report-content.json"), root)


def test_example_schema_layout_and_authored_values_are_consistent(audit_report):
    content, renderer, _ = audit_report
    model = load(audit_report)
    rendered, _ = renderer.render(model)
    text = rendered.decode()
    golden = ROOT / "tests/fixtures/audit-report/example.md"
    assert text == golden.read_text().split("\n\n", 1)[1]
    assert not re.search(r"\b[0-9a-f]{64}\b", text.split("\n\n", 1)[1]), "revision digests stay out of the report body"
    for path, value in content.prose_fields(model.data, visible_only=True):
        assert renderer.prose(model, value) in text, path
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings == [
        "Summary",
        "Key findings",
        "Recommended Tasks by Capability & Tool",
        "Task catalog",
        "Coverage report",
        "Verifier cheat screening",
        "Scope and Execution Evidence",
        "Definitions",
    ]
    for header in [
        ("Functionality", "Intended check and required evidence"),
        ("Task", "Behavior", "Capabilities and tools", "Findings"),
        ("Functionality", "Intended check and required evidence", "Tasks", "Run evidence"),
    ]:
        assert "| " + " | ".join(header) + " |" in text
    assert "No comparable previous audit" not in text
    assert "Weak evaluations" not in text
    assert "| Coverage ratio | 100% |" in text
    assert "| Coverage basis | Mapped tasks (runs not measured) |" in text
    assert "[verify.py](../report-sources/tasks/source-answer/verify.py)" in text
    assert_markdown_links(text, renderer)


def markdown_tokens(text):
    pending = MarkdownIt("commonmark").enable("table").parse(text)
    while pending:
        token = pending.pop(0)
        yield token
        pending[0:0] = token.children or []


def assert_markdown_links(text, renderer):
    tokens = list(markdown_tokens(text))
    assert not any(token.type in {"html_block", "html_inline"} for token in tokens)
    unlinked_task_ids = []
    task_id = re.compile(r"\bT\d+-[a-z0-9]+(?:-[a-z0-9]+)*\b")
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        in_link = False
        for child in token.children:
            if child.type == "link_open":
                assert child.attrGet("title") is None, child.attrGet("href")
                assert " " not in child.attrGet("href"), child.attrGet("href")
                in_link = True
            elif child.type == "link_close":
                in_link = False
            elif child.type in {"text", "code_inline"} and not in_link:
                unlinked_task_ids.extend(task_id.findall(child.content))
    assert not unlinked_task_ids, f"task identifiers do not render as Markdown links: {unlinked_task_ids}"
    anchors = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        inline = tokens[index + 1]
        title = "".join(child.content for child in inline.children if child.type in {"text", "code_inline"})
        slug = renderer.heading_slug(title)
        candidate, suffix = slug, 0
        while candidate in anchors:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        anchors.add(candidate)
    fragments = {
        token.attrGet("href")[1:]
        for token in tokens
        if token.type == "link_open" and token.attrGet("href").startswith("#")
    }
    assert fragments <= anchors


def test_escaped_task_link_in_summary_renders_as_a_markdown_link(audit_report):
    _, renderer, _ = audit_report
    data = example_data(audit_report[2])
    data["summary"][1] = r"No retained run exists for \[T1-source-answer\]\(tasks/source-answer \"Source answer\"\)."
    model = load(audit_report, data)
    rendered, _ = renderer.render(model)
    text = rendered.decode()
    assert_markdown_links(text, renderer)
    assert renderer.task_link(model, "T1") in text
    assert renderer.task_link(model, "T1") == "[T1-source-answer](../report-sources/tasks/source-answer/instruction.md)"


@pytest.mark.parametrize(
    "title,slug",
    [
        (
            "Finding 1 - The verifier accepts an unchecked success response",
            "finding-1---the-verifier-accepts-an-unchecked-success-response",
        ),
        ("Capabilities audited", "capabilities-audited"),
        ("Finding 1 - Coverage gaps", "finding-1---coverage-gaps"),
    ],
)
def test_heading_links_follow_native_markdown_slugs(audit_report, title, slug):
    _, renderer, _ = audit_report
    assert renderer.heading_slug(title) == slug


def test_authored_html_and_multiline_cells_remain_markdown(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    data["summary"][0] = 'T1 exposes <script> text and <a id="example">markup</a> as literal text.'
    data["tasks"][0]["behavior"] = "Inspect <details> text.\nRequire A | B without a <br> tag."
    text = renderer.render(load(audit_report, data))[0].decode()
    assert_markdown_links(text, renderer)
    assert "A \\| B" in text
    assert "\\<br\\>" in text
    assert next(markdown_tokens(text)).type == "heading_open"


def test_summary_holds_coverage_history_then_advisories_before_key_findings(audit_report):
    _, renderer, _ = audit_report
    report = renderer.render(load(audit_report))[0].decode()
    summary, key = report.split("## Summary\n\n", 1)[1].split("## Key findings\n\n", 1)
    key = key.split("## Recommended Tasks", 1)[0]
    paragraphs = summary.split("### Evaluation coverage", 1)[0].strip().split("\n\n")
    assert len(paragraphs) == 2
    found, limits = paragraphs
    assert found.startswith("This audit includes ")
    assert "have at least one mapped task." in found
    assert found.index("The audit uncovered evidence of verifier cheating.") < found.index("The suite's only verifier")
    assert limits.startswith("Coverage was not measured for this audit because the audit measurement step was not run.")
    assert limits.endswith("All findings come from inspecting the verifier code and the task files.")
    assert "Coverage measurement compares" not in report
    assert "To measure coverage" not in summary
    assert "unmeasured" not in key.split("\n- ", 1)[0]
    assert "To measure coverage" not in key
    scope = report.split("## Scope and Execution Evidence\n\n", 1)[1].split("## Definitions", 1)[0]
    assert "Coverage is unmeasured. To measure coverage, agree on the audit checks" in scope
    assert summary.index("### Evaluation coverage") < summary.index("### ADVISORY")
    assert key.startswith("### Finding 1 - Coverage gaps\n")
    assert "\n- **The suite lacks a missing-evidence task.** " in key
    assert "\n  - Proposed task: Supply a source without an answer" in key


def test_coverage_task_column_retains_intended_mappings_before_measurement(audit_report):
    _, renderer, _ = audit_report
    report = renderer.render(load(audit_report))[0].decode()
    capabilities = report.split("### Capabilities audited\n\n", 1)[1].split("### Tools", 1)[0]
    row = next(line for line in capabilities.splitlines() if "cited\\_answers" in line)
    cells = row.split(" | ")
    assert cells[2].startswith("[T1-source-answer](../report-sources/tasks/source-answer")
    assert "Unmeasured" in cells[3]
    assert "Not established" not in cells[2]
    failure_cases = report.split("### Failure cases\n\n")[-1].split("## Verifier cheat screening", 1)[0]
    assert "No evaluation coverage" in failure_cases


@pytest.mark.parametrize(
    "task_text",
    [
        "T1-source-answer",
        '[T1-source-answer](tasks/wrong-path "Old title")',
        '[T1-source-answer]\\(tasks/wrong-path "Old title")',
    ],
)
def test_findings_have_plain_headings_and_canonical_task_links(audit_report, task_text):
    _, renderer, root = audit_report
    data = example_data(root)
    task_path = root / ".eval-author/tasks/source-answer"
    task_path.parent.mkdir(parents=True)
    (root / data["sources"][2]["path"]).rename(task_path)
    data["sources"][2]["path"] = ".eval-author/tasks/source-answer"
    data["sources"][3]["path"] = ".eval-author/tasks/source-answer/verify.py"
    data["findings"][0]["title"] = task_text + " accepts an agent-written reward file"
    data["findings"][0]["explanation"] = "The grading script for " + task_text + " accepts a submitted reward."
    report = renderer.render(load(audit_report, data))[0].decode()
    advisory = report.split("### ADVISORY\n\n", 1)[1].split("## Key findings", 1)[0]
    heading, paragraph = advisory.split("\n\n", 2)[:2]
    assert heading == "#### Advisory 1 - Accepts an agent-written reward file"
    assert paragraph.startswith("The grading script for [T1-source-answer](tasks/source-answer/instruction.md) accepts")
    key = report.split("## Key findings\n\n", 1)[1].split("## Recommended Tasks", 1)[0]
    assert "agent-written reward file" not in key
    tokens = list(markdown_tokens(advisory))
    assert not any("](" in token.content for token in tokens if token.type == "text")
    task_links = [
        token.attrGet("href")
        for token in tokens
        if token.type == "link_open" and token.attrGet("href").startswith("tasks/source-answer/instruction")
    ]
    assert task_links == ["tasks/source-answer/instruction.md"]
    assert all((root / ".eval-author" / target).is_file() for target in task_links)
    assert_markdown_links(report, renderer)


def test_multiple_affected_tasks_lead_the_finding_paragraph(audit_report):
    _, renderer, _ = audit_report
    model = load(audit_report)
    model.tasks["T2"] = {**model.tasks["T1"], "id": "T2"}
    row = model.data["findings"][0]
    row["tasks"].append({**row["tasks"][0], "task_id": "T2"})
    row["title"] = "T1 and T2 accept unchecked success responses"
    row["explanation"] = "Both verifiers accept an empty submission."
    assert renderer.finding_heading(model, row["id"]) == "Advisory 1 - Accept unchecked success responses"
    expected = renderer.task_link(model, "T1") + " and " + renderer.task_link(model, "T2")
    assert renderer.finding_explanation(model, row).startswith(expected + ": ")
    row["explanation"] = "T1 and T2 accept an empty submission."
    assert renderer.finding_explanation(model, row) == expected + " accept an empty submission."


def test_screening_describes_each_check_once_without_per_task_runs(audit_report):
    content, renderer, root = audit_report
    data = example_data(root)
    checks = data["verifier_review"]["checks"]
    checks.extend(
        {**row, "id": f"V{index}", "explanation": "Per-task rationale stays in the validation input."}
        for index, row in enumerate(copy.deepcopy(checks), 7)
    )
    model = load(audit_report, data)
    report = renderer.render(model)[0].decode()
    screening = report.split("## Verifier cheat screening\n\n", 1)[1].split("## Scope and Execution Evidence", 1)[0]
    assert "T1-source-answer" not in screening
    assert "| Check |" not in screening
    assert "### Retained control evidence" not in screening
    assert "Per-task rationale stays in the validation input." not in report
    schema = json.loads(content.SCHEMA_PATH.read_text())
    families = set(schema["$defs"]["review_check"]["properties"]["family"]["enum"])
    assert families == set(renderer.layout.FAMILIES) == set(renderer.layout.SCREENING_CHECKS)
    for name in renderer.layout.FAMILIES.values():
        assert screening.count(f"- **{name}:**") == 1
    assert len(model.data["verifier_review"]["checks"]) == 12


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "0 advisories, 1 finding, and 0 recommended tasks"),
        (1, "1 advisory, 1 finding, and 1 recommended task"),
        (4, "1 advisory, 3 findings, and 3 recommended tasks"),
        (5, "2 advisories, 3 findings, and 3 recommended tasks"),
    ],
)
def test_summary_counts_records_once_and_handles_plural_forms(audit_report, count, expected):
    _, renderer, root = audit_report
    data = example_data(root)
    if count < 4:
        data["findings"] = data["findings"][:count]
        data["recommendations"] = data["recommendations"][:count]
        for check in data["verifier_review"]["checks"]:
            check["finding_ids"] = [row["id"] for row in data["findings"]]
    if count == 5:
        data["findings"].append({**copy.deepcopy(data["findings"][0]), "id": "F5"})
    if data["recommendations"]:
        data["recommendations"][0]["items"] = [row["item"] for row in data["task_mappings"]]
    text = renderer.render(load(audit_report, data))[0].decode()
    assert f"This audit includes {expected}." in text


@pytest.mark.parametrize(
    "category,status,reviewed,expected",
    [
        (None, None, True, "The audit uncovered no evidence of verifier tampering in the reviewed tasks."),
        (None, None, False, "Verifier cheat screening has not been assessed because no review is available."),
        ("verifier_cheating", "confirmed", True, "The audit uncovered evidence of verifier cheating."),
        ("verifier_cheating", "unproven", True, "The audit recorded verifier concerns that remain unproven."),
        ("verifier_cheating", "resolved", True, "The audit retains resolved verifier findings for reference."),
        ("weak_verifier", "confirmed", True, "The audit identified verifier issues in the reviewed tasks."),
    ],
)
def test_summary_derives_verifier_conclusion_from_reviewed_findings(audit_report, category, status, reviewed, expected):
    _, renderer, root = audit_report
    data = example_data(root)
    data["summary"] = ["The audit inspected the selected task definitions.", "No runs were measured."]
    data["recommendations"] = []
    data["findings"] = data["findings"][:1] if category else []
    for finding in data["findings"]:
        finding.update(category=category, status=status)
        if category != "verifier_cheating" or status == "unproven":
            finding["advisory"] = None
    for check in data["verifier_review"]["checks"]:
        check["finding_ids"] = [row["id"] for row in data["findings"]]
    if not reviewed:
        data["verifier_review"] = {"checks": [], "notes": []}
    model = load(audit_report, data)
    report = renderer.render(model)[0].decode()
    summary = report.split("## Summary\n\n", 1)[1].split("### Evaluation coverage", 1)[0]
    assert expected in summary
    assert "No cheat controls were executed." not in summary
    assert ("no evidence of verifier tampering" in summary) is (reviewed and category is None)
    if reviewed and category is None:
        assert all(check["receipt"] is None for check in data["verifier_review"]["checks"])
        assert any(row["classification"] == "unproven" for row in model.controls)


def test_task_references_link_the_entire_full_identifier_in_every_section(audit_report):
    _, renderer, _ = audit_report
    model = load(audit_report)
    text = renderer.render(model)[0].decode()
    links = []
    current_link = None
    for token in markdown_tokens(text):
        if token.type == "link_open":
            current_link = [token.attrGet("href"), ""]
        elif token.type == "link_close":
            assert current_link is not None
            links.append(current_link)
            current_link = None
        elif token.type in {"text", "code_inline"}:
            if current_link is not None:
                current_link[1] += token.content
            else:
                assert not re.search(r"\bT1(?:\b|-source-answer)", token.content)
    task_links = [(target, label) for target, label in links if label.startswith("T1")]
    assert len(task_links) >= 5
    assert all(label == "T1-source-answer" for _, label in task_links)
    assert all(target == "../report-sources/tasks/source-answer/instruction.md" for target, _ in task_links)


def test_task_mentions_preserve_identifier_boundaries_and_existing_full_names(audit_report):
    _, renderer, _ = audit_report
    model = load(audit_report)
    model.tasks["T10"] = {**model.tasks["T1"], "id": "T10"}
    value = "T1, T10 and T1-source-answer are tasks. Keep T100, T1-other, /T1/path and file.T1 unchanged."
    result = renderer.prose(model, value)
    assert result == (
        renderer.task_link(model, "T1")
        + ", "
        + renderer.task_link(model, "T10")
        + " and "
        + renderer.task_link(model, "T1")
        + " are tasks. Keep T100, T1-other, /T1/path and file.T1 unchanged."
    )
    model.paths["S3"] = model.repo / "tasks/T1-source-answer"
    assert renderer.task_identifier(model, "T1") == "T1-source-answer"
    model.tasks["T1-source-answer"] = {**model.tasks["T1"], "id": "T1-source-answer"}
    model.paths["S3"] = model.repo / "tasks/source-answer"
    assert renderer.task_identifier(model, "T1-source-answer") == "T1-source-answer"


def test_key_findings_are_paragraphs_without_revision_or_recommendation_boilerplate(audit_report):
    _, renderer, _ = audit_report
    model = load(audit_report)
    report = renderer.render(model)[0].decode()
    key_findings, recommendations = report.split("## Key findings\n\n", 1)[1].split(
        "## Recommended Tasks by Capability & Tool\n\n", 1
    )
    recommendations = recommendations.split("## Task catalog\n\n", 1)[0]
    summary = report.split("## Summary\n\n", 1)[1].split("## Key findings", 1)[0]
    tokens = list(markdown_tokens(key_findings))
    headings = [tokens[index + 1].content for index, token in enumerate(tokens) if token.type == "heading_open"]
    assert headings == [
        "Finding 1 - Coverage gaps",
        "Finding 2 - Retained controls are unavailable",
        "Finding 3 - Quotation accuracy is unchecked",
    ]
    assert "This finding applies to" not in report
    assert "This recommendation has" not in report
    assert "The next action is" not in report
    for task in model.tasks.values():
        assert task["revision"] not in report
    for row in model.data["recommendations"]:
        if row["proposed_task"]:
            proposal = "Proposed task: " + renderer.prose(model, row["proposed_task"])
            assert (summary + key_findings).count(proposal) == 1
            assert recommendations.count(renderer.prose(model, row["proposed_task"])) == (1 if row["items"] else 0)


def test_key_findings_remain_visible_without_recommendations(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    data["recommendations"] = []
    model = load(audit_report, data)
    report = renderer.render(model)[0].decode()
    summary, key_findings = report.split("## Summary\n\n", 1)[1].split("## Key findings\n\n", 1)
    key_findings = key_findings.split("## Recommended Tasks", 1)[0]
    for row in data["findings"]:
        if row["category"] == "verifier_cheating":
            assert row["title"] in summary and row["title"] not in key_findings
        elif row["category"] == "missing_task":
            assert renderer.prose(model, row["explanation"]) in key_findings
            assert "confirmed a missing task" not in report
        else:
            assert row["title"] in key_findings
    assert_markdown_links(report, renderer)


def test_task_proposal_without_item_mappings_stays_with_its_finding(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    data["recommendations"][0]["items"] = []
    model = load(audit_report, data)
    report = renderer.render(model)[0].decode()
    recommendations = report.split("## Recommended Tasks by Capability & Tool\n\n", 1)[1].split(
        "## Task catalog\n\n", 1
    )[0]
    proposal = renderer.prose(model, data["recommendations"][0]["proposed_task"])
    assert report.count(proposal) == 1
    assert proposal not in recommendations
    advisory = report.split("### ADVISORY\n\n", 1)[1].split("## Key findings", 1)[0]
    assert "Proposed task: " + proposal in advisory
    assert_markdown_links(report, renderer)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("confirmed", "Code inspection confirmed verifier cheating, as documented in "),
        (
            "unproven",
            "The available code inspection evidence in ",
        ),
        (
            "resolved",
            "The audit records this finding as resolved and retains the code inspection evidence in ",
        ),
    ],
)
def test_finding_status_and_discovery_use_complete_sentences(audit_report, status, expected):
    _, renderer, root = audit_report
    data = example_data(root)
    data["findings"][0]["status"] = status
    if status == "unproven":
        data["findings"][0]["advisory"] = None
    text = renderer.render(load(audit_report, data))[0].decode()
    assert expected in text
    if status == "unproven":
        assert "leaves this finding unproven." in text
    assert "Verifier cheating. Confirmed. Found by inspection." not in text


@pytest.mark.parametrize("edited", [False, True])
def test_old_generated_header_upgrades_only_when_body_is_unchanged(audit_report, edited):
    content, renderer, root = audit_report
    model = load(audit_report)
    output = root / renderer.OUTPUT
    output.parent.mkdir()
    body = '<a id="old-finding"></a>\n\n# Earlier generated report\n'
    metadata = {"renderer_version": 1, "body_sha256": content.sha(body.encode())}
    original = renderer.LEGACY_HEADER + json.dumps(metadata) + " -->\n\n" + body
    if edited:
        original += "A direct edit.\n"
    output.write_text(original)
    if edited:
        with pytest.raises(content.ContentError, match="generated_output_modified"):
            renderer.refresh(model)
        assert output.read_text() == original
    else:
        assert renderer.refresh(model)["written"]
        assert output.read_text().startswith(renderer.HEADER)
        assert_markdown_links(output.read_text(), renderer)
        assert not renderer.refresh(model)["written"]


@pytest.mark.parametrize(
    "change,field",
    [
        ("count", "content"),
        ("dangling", "task_mappings"),
        ("missing_item", "task_mappings"),
        ("duplicate_item", "task_mappings"),
        ("duplicate_id", "findings"),
        ("revision", "findings"),
        ("explanation", "findings"),
        ("evidence", "findings"),
        ("basis", "findings"),
        ("priority", "recommendations"),
        ("order", "recommendations"),
        ("run", "runs"),
        ("empty", "summary"),
    ],
)
def test_each_invalid_content_rule_prevents_output(audit_report, change, field):
    content, renderer, root = audit_report
    data = example_data(root)
    if change == "count":
        data["covered_count"] = 42
    elif change == "dangling":
        data["task_mappings"][0]["evidence"] = ["S-absent"]
    elif change == "missing_item":
        data["task_mappings"].pop()
    elif change == "duplicate_item":
        data["task_mappings"].append(copy.deepcopy(data["task_mappings"][0]))
    elif change == "duplicate_id":
        data["findings"][1]["id"] = data["findings"][0]["id"]
    elif change == "revision":
        data["findings"][0]["tasks"][0]["revision"] = "f" * 64
    elif change == "explanation":
        data["findings"][0]["explanation"] = ""
    elif change == "evidence":
        data["findings"][0]["evidence"] = []
    elif change == "basis":
        del data["findings"][0]["basis"]
    elif change == "priority":
        data["recommendations"][0]["priority"] = "urgent"
    elif change == "order":
        data["recommendations"][1]["order"] = 1
    elif change == "run":
        data["runs"][0]["id"] = "missing-run"
        data["tasks"][0]["run_ids"] = ["missing-run"]
    elif change == "empty":
        data["summary"][0] = "   "
    with pytest.raises(content.ContentError) as failure:
        renderer.refresh(load(audit_report, data))
    assert field in str(failure.value)
    assert all(set(row) >= {"field", "rule", "fix"} for row in failure.value.errors)
    assert not (root / ".eval-author").exists()


def test_refresh_changes_only_generated_audit_and_is_idempotent(audit_report):
    _, renderer, root = audit_report
    untouched = {
        name: b"User content\n" for name in [".eval-author/report.html", ".eval-author/README.md", "README.md"]
    }
    for name, value in untouched.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    first = renderer.refresh(load(audit_report))
    output = root / first["report"]
    timestamp = output.stat().st_mtime_ns
    assert not renderer.refresh(load(audit_report))["written"]
    assert output.stat().st_mtime_ns == timestamp
    data = example_data(root)
    data["summary"][0] = "The revised summary retains the same measured limits."
    renderer.refresh(load(audit_report, data))
    assert "revised summary" in output.read_text()
    assert all((root / name).read_bytes() == value for name, value in untouched.items())
    assert not (root / ".eval-author/.working/audit/history").exists()


def test_direct_markdown_edits_refuse_refresh_show_diff_and_name_field(audit_report):
    content, renderer, root = audit_report
    model = load(audit_report)
    renderer.refresh(model)
    output = root / renderer.OUTPUT
    edited = output.read_text().replace(renderer.prose(model, model.data["summary"][0]), "An unsupported hand edit.")
    output.write_text(edited)
    with pytest.raises(content.ContentError) as failure:
        renderer.refresh(model)
    assert any(row["field"].startswith("content.summary") for row in failure.value.errors)
    assert "An unsupported hand edit." in failure.value.errors[0]["diff"]
    assert "report-content.json" in failure.value.errors[0]["fix"]
    assert output.read_text() == edited


def test_legacy_conversion_is_explicit_and_keeps_original_bytes(audit_report):
    content, renderer, root = audit_report
    output = root / renderer.OUTPUT
    output.parent.mkdir()
    original = b"# Existing audit\n\nUser corrections and [evidence](old.json).\n"
    output.write_bytes(original)
    with pytest.raises(content.ContentError, match="legacy_report"):
        renderer.refresh(load(audit_report))
    assert output.read_bytes() == original
    renderer.refresh(load(audit_report), convert_legacy=content.sha(original))
    backup = root / f".eval-author/.working/audit/legacy-report-{content.sha(original)}.md"
    assert backup.read_bytes() == original
    assert output.read_bytes().startswith(renderer.HEADER.encode())


def test_changed_source_and_symlinks_are_rejected(audit_report):
    content, _, root = audit_report
    path = root / "report-sources/tasks/source-answer/verify.py"
    original = path.read_bytes()
    path.write_bytes(original + b"\n")
    with pytest.raises(content.ContentError, match="source_binding"):
        load(audit_report)
    path.unlink()
    target = root / "outside.py"
    target.write_bytes(original)
    path.symlink_to(target)
    with pytest.raises(content.ContentError, match="symlink"):
        load(audit_report)


@pytest.mark.parametrize("status,shown", [("confirmed", True), ("unproven", False), ("resolved", True)])
def test_advisories_follow_specific_confirmed_cheating_findings(audit_report, status, shown):
    _, renderer, root = audit_report
    data = example_data(root)
    data["findings"][0]["status"] = status
    if status == "unproven":
        data["findings"][0]["advisory"] = None
    text = renderer.render(load(audit_report, data))[0].decode()
    assert ("### ADVISORY" in text) is shown
    assert "weak verifier" in text.lower()


def test_every_issue_is_listed_in_task_findings_with_a_compact_screening_overview(audit_report):
    _, renderer, _ = audit_report
    text = renderer.render(load(audit_report))[0].decode()
    row = next(line for line in text.splitlines() if line.startswith("| [T1-source-answer]"))
    assert all(identifier in row for identifier in ["Advisory 1", "Finding 2", "Finding 3"])
    assert "### Retained control evidence" not in text
    assert "Task-specific issues appear in the findings." in text


def test_empty_review_and_zero_item_kinds_have_honest_states(audit_report):
    content, renderer, root = audit_report
    data = example_data(root)
    data["verifier_review"] = {"checks": [], "notes": []}
    spec_path = root / "report-sources/audit.md"
    body = spec_path.read_text()
    import yaml

    spec = content.load_audit_spec(spec_path)
    spec["items"] = [item for item in spec["items"] if item["kind"] == "tool"]
    start = body.index("```yaml\n") + len("```yaml\n")
    end = body.index("```", start)
    spec_path.write_text(body[:start] + yaml.safe_dump(spec, sort_keys=False) + body[end:])
    data["sources"][0]["sha256"] = content.sha(spec_path.read_bytes())
    data["task_mappings"] = data["task_mappings"][:1]
    data["recommendations"] = []
    for finding in data["findings"]:
        finding["items"] = []
    text = renderer.render(load(audit_report, data))[0].decode()
    assert "Verifier cheat screening has not been assessed because no review is available." in text
    assert "| Coverage ratio | No items declared |" in text
    assert "The audit declares no items in this category." in text


def test_prose_lint_uses_schema_flags_without_rejecting_content(audit_report):
    content, renderer, root = audit_report
    data = example_data(root)
    data["summary"][0] = "Leverage the grader for this test case."
    data["identity"]["repository"] = "grader/test-case"
    save(root, data)
    model = content.load(Path("report-content.json"), root, lint=True)
    assert len(model.warnings) == 3
    assert all(row["field"] == "content.summary[0]" for row in model.warnings)
    plain = content.load(Path("report-content.json"), root)
    assert renderer.render(model)[0] == renderer.render(plain)[0]


def test_structured_checker_uses_explained_records_independently_of_markdown(audit_report):
    _, renderer, root = audit_report
    model = load(audit_report)
    renderer.refresh(model)
    checker = importlib.import_module("audit_verifier_evidence")
    result = checker.check(root, Path("unused.md"), [], [], [], content=Path("report-content.json"))
    assert result["valid"] and result["mode"] == "content"
    (root / renderer.OUTPUT).write_text("# Different headings\n\nNo evidence links.\n")
    assert checker.check(root, Path("unused.md"), [], [], [], content=Path("report-content.json"))["valid"]
    data = example_data(root)
    for finding in data["findings"]:
        finding["evidence"] = ["S4"]
    save(root, data)
    assert not checker.check(root, Path("unused.md"), [], [], [], content=Path("report-content.json"))["valid"]


def test_copied_skill_installation_validates_renders_and_checks_without_repo_files(audit_report, tmp_path):
    _, _, root = audit_report
    installed = root / "installed"
    for name in ["eval-author", "eval-author-audit"]:
        shutil.copytree(ROOT / "skills" / name, installed / name)
    for script, flags in [
        (installed / "eval-author-audit/scripts/audit_spec/report_content.py", []),
        (installed / "eval-author-audit/scripts/audit_spec/render_report.py", []),
        (installed / "eval-author/scripts/audit_verifier_evidence.py", []),
    ]:
        result = subprocess.run(
            [sys.executable, str(script), "--repo", str(root), "--content", "report-content.json", *flags],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["valid"]


def test_multiple_recommendations_for_one_finding_have_one_explanation_and_valid_heading_links(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    data["recommendations"][1]["finding_id"] = "F1"
    model = load(audit_report, data)
    report = renderer.render(model)[0].decode()
    assert report.count(renderer.prose(model, data["findings"][0]["explanation"])) == 1
    assert_markdown_links(report, renderer)
    assert "R2" not in report


def test_malformed_audit_has_a_named_input_error(audit_report):
    content, _, root = audit_report
    data = example_data(root)
    path = root / "report-sources/audit.md"
    body = path.read_text()
    start = body.index("```yaml\n") + len("```yaml\n")
    end = body.index("```", start)
    path.write_text(body[:start] + "null\n" + body[end:])
    data["sources"][0]["sha256"] = content.sha(path.read_bytes())
    with pytest.raises(content.ContentError, match="content.inputs"):
        load(audit_report, data)


def test_generated_output_cannot_overwrite_its_own_saved_source(audit_report):
    content, renderer, root = audit_report
    renderer.refresh(load(audit_report))
    data = example_data(root)
    output = root / renderer.OUTPUT
    data["sources"].append(
        {"id": "Sprevious", "kind": "document", "path": renderer.OUTPUT, "sha256": content.sha(output.read_bytes())}
    )
    data["summary"][0] = "A revised summary cannot overwrite its retained source."
    before = output.read_bytes()
    with pytest.raises(content.ContentError, match="output_is_input"):
        renderer.refresh(load(audit_report, data))
    assert output.read_bytes() == before


def test_unchanged_refresh_revalidates_saved_sources(audit_report):
    content, renderer, root = audit_report
    model = load(audit_report)
    renderer.refresh(model)
    path = root / "report-sources/ETHOS.md"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(content.ContentError, match="source_binding"):
        renderer.refresh(model)


@pytest.mark.parametrize(
    "field,value,flagged",
    [
        ("proposed_task", "Revise T1 to reject empty answers.", "content.recommendations[0].proposed_task"),
        ("advisory", "Until it is fixed, a passing T1 result is unreliable.", "content.findings[0].advisory"),
        ("explanation", "T1's verifier prints success, so T1 passes empty answers.", "content.findings[0].explanation"),
    ],
)
def test_a_task_is_named_once_per_finding(audit_report, field, value, flagged):
    content, renderer, root = audit_report
    data = example_data(root)
    if field == "proposed_task":
        data["recommendations"][0]["proposed_task"] = value
    else:
        data["findings"][0][field] = value
    with pytest.raises(content.ContentError) as failure:
        renderer.render(load(audit_report, data))
    assert [row["field"] for row in failure.value.errors] == [flagged]
    assert failure.value.errors[0]["rule"] == "repeated_task_reference"
    assert "Name T1 once per finding" in failure.value.errors[0]["fix"]


def test_unnamed_task_is_prefixed_once_and_not_repeated_in_its_citation(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    data["findings"][2]["explanation"] = "No supported control receipts exist in the selected material."
    report = renderer.render(load(audit_report, data))[0].decode()
    finding = report.split("### Finding 2 - Retained controls are unavailable\n\n", 1)[1].split("\n\n", 1)[0]
    assert finding.count("T1-source-answer") == 1
    assert finding.startswith("[T1-source-answer](")
    assert finding.endswith("The available code inspection evidence in the task files leaves this finding unproven.")


@pytest.mark.parametrize(
    "paths,expected",
    [
        ([], "No selected agent runs"),
        ([".eval-author/jobs/a/t1/result.json"], "1 selected agent run in `jobs/a/t1/`"),
        (
            [
                ".eval-author/jobs/chat/t1/result.json",
                ".eval-author/jobs/chat/t2/result.json",
                ".eval-author/jobs/src-1/t1/result.json",
            ],
            "3 selected agent runs in `jobs/`",
        ),
        (
            [".eval-author/jobs/chat/t1/result.json", ".eval-author/jobs/chat/t2/result.json", "traces/a.json"],
            "3 selected agent runs: 2 in `jobs/chat/`, 1 in `../traces/`",
        ),
    ],
)
def test_run_evidence_lists_shared_run_directories_not_each_file(audit_report, paths, expected):
    _, renderer, _ = audit_report
    assert renderer.run_roots_text(paths) == expected


def test_coverage_rows_lead_with_the_item_name(audit_report):
    _, renderer, _ = audit_report
    report = renderer.render(load(audit_report))[0].decode()
    coverage = report.split("## Coverage report\n\n", 1)[1].split("## Verifier cheat screening", 1)[0]
    assert "\n| cited\\_answers - Answer with exact quotations. |" in coverage
    assert "\n| read\\_file - Read project sources. |" in coverage


def test_audit_spec_and_ethos_are_linked_only_in_scope(audit_report):
    _, renderer, _ = audit_report
    report = renderer.render(load(audit_report))[0].decode()
    body, scope = report.split("## Scope and Execution Evidence\n\n", 1)
    for name in ("audit.md)", "ETHOS.md)"):
        assert name not in body.split("\n\n", 1)[1]
        assert name in scope


def test_finding_citations_dedupe_and_skip_scope_sources(audit_report):
    _, renderer, root = audit_report
    data = example_data(root)
    # F4: two files named verify.py, the task folder, the audit spec, and the Ethos.
    original = next(row for row in data["sources"] if row["id"] == "S4")
    copy_path = root / "report-sources/other/verify.py"
    copy_path.parent.mkdir()
    copy_path.write_bytes((root / original["path"]).read_bytes())
    data["sources"].append({**original, "id": "S9", "path": "report-sources/other/verify.py"})
    data["findings"][3]["evidence"] = ["S4", "S9", "S3", "S1", "S2"]
    # F3: only the audit spec and the Ethos.
    data["findings"][2]["evidence"] = ["S1", "S2"]
    report = renderer.render(load(audit_report, data))[0].decode()
    accuracy = report.split("### Finding 3 - Quotation accuracy is unchecked\n\n", 1)[1].split("\n\n", 1)[0]
    assert accuracy.endswith(
        "Code inspection confirmed a weak verifier, as documented in "
        "[verify.py](../report-sources/tasks/source-answer/verify.py)."
    )
    controls = report.split("### Finding 2 - Retained controls are unavailable\n\n", 1)[1].split("\n\n", 1)[0]
    assert controls.endswith("This finding is unproven.")


def _input(run_id, kind, covered, evidence_class="agent_under_test"):
    subject = {"run_id": run_id, "trace": f"jobs/{run_id}/trace.json"}
    if evidence_class:
        subject["evidence_class"] = evidence_class
    return {"subject": subject, "item_kind": kind, "covered": covered}


def test_unlabeled_measurements_do_not_establish_coverage(audit_report):
    _, renderer, _ = audit_report
    # The aggregator folds unlabeled inputs into the agent bucket when inputs are mixed.
    aggregate = {
        "coverage_by_evidence_class": {"agent_under_test": {"covered": ["read_file", "write_file"]}},
        "input_reports": [_input("labeled", "tool", ["read_file"]), _input("legacy", "tool", ["write_file"], None)],
    }
    covered, measured, inputs = renderer.measurements(SimpleNamespace(aggregate=aggregate))
    assert covered == {"read_file"}
    assert measured == {"tool"}
    assert [row["subject"]["run_id"] for row in inputs] == ["labeled"]


def test_bound_traces_without_run_records_count_as_selected_runs(audit_report, monkeypatch):
    _, renderer, root = audit_report
    model = load(audit_report)
    model.data["runs"] = []
    model.aggregate = {
        "coverage_by_evidence_class": {"agent_under_test": {"covered": []}},
        "input_reports": [
            _input("unattributed", "tool", ["read_file"]),
            _input("unattributed", "capability", []),
        ],
    }
    monkeypatch.setattr(
        renderer.content, "trace_path", lambda model, entry: model.repo / ".eval-author" / entry["subject"]["trace"]
    )
    assert renderer.run_roots(model) == "1 selected agent run in `jobs/unattributed/`"
