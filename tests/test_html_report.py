# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline suite reporting with synthetic native evidence; never run evaluations."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author/scripts"
_spec = importlib.util.spec_from_file_location("eval_html_report", SCRIPTS / "report.py")
assert _spec and _spec.loader
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
    return path


def task(root, name="refund", readme=None):
    path = f".eval-author/tasks/{name}"
    write(root, path + "/task.toml", f'[task]\nname = "org/{name}"\n')
    write(root, path + "/instruction.md", "SYNTHETIC_REQUEST")
    if readme is not None:
        write(root, path + "/README.md", readme)
    return path


def trial(root, path, *, reward=1, error=None, finished=True, agent="test-agent", trial_id=None):
    data = {
        "id": trial_id or path,
        "task_name": "org/refund",
        "task_id": {"path": ".eval-author/tasks/refund"},
        "task_checksum": "synthetic-checksum",
        "agent_info": {"name": agent, "model_info": {"name": "synthetic"}},
        "exception_info": error,
        "verifier_result": {"rewards": {"reward": reward}},
        "started_at": "2026-10-01T10:00:00+00:00",
        "finished_at": "2026-10-01T10:01:00+00:00" if finished else None,
    }
    write(root, path + "/result.json", data)
    return data


def job(root, path=".eval-author/jobs/baseline", total=2):
    write(
        root,
        path + "/result.json",
        {
            "id": "job-id",
            "n_total_trials": total,
            "stats": {},
            "started_at": "2026-10-01T10:00:00+00:00",
            "finished_at": "2026-10-01T10:01:00+00:00",
        },
    )
    return path


def rule(path):
    return {
        "runs": {path: {"role": "agent", "reward_key": "reward", "pass_value": 1, "reward_min": 0, "reward_max": 1}}
    }


def test_bootstrap_and_documentation_are_deterministic(tmp_path):
    path = task(
        tmp_path,
        readme="# Refund\n## Behavior under test\nRefund authorization.\n## Expected result\nOnly approved refunds are stored.\n",
    )
    first = report.refresh(tmp_path, patch={"tasks": {path: {"display_id": "R1"}}})
    assert first["tasks"][0]["behavior"] == "Refund authorization."
    assert first["tasks"][0]["expected_outcome"] == "Only approved refunds are stored."
    assert first["tasks"][0]["name"] == "org/refund"
    assert first["tasks"][0]["title"] == "Refund"
    assert first["runs"] == []
    output = tmp_path / ".eval-author/report.html"
    before = output.read_bytes()
    modified = output.stat().st_mtime_ns
    assert report.refresh(tmp_path) == first
    assert output.read_bytes() == before
    assert output.stat().st_mtime_ns == modified
    assert sorted(p.name for p in output.parent.iterdir()) == [".working", "README.md", "report.html", "tasks"]
    write(
        tmp_path,
        path + "/README.md",
        "## Behavior under test\nUpdated behavior.\n## Expected result\nUpdated result.\n",
    )
    updated = report.refresh(tmp_path)
    assert updated["tasks"][0]["behavior"] == "Updated behavior."
    assert updated["tasks"][0]["display_id"] == "R1"
    assert updated["tasks"][0]["title"] is None
    assert updated["tasks"][0]["readme_revision"] != first["tasks"][0]["readme_revision"]


@pytest.mark.parametrize(
    "readme,title",
    [
        ("# Approve **refunds** with `policy` & [checks](checks.md)\n", "Approve refunds with policy & checks"),
        ("Refund authorization\n====================\n", "Refund authorization"),
        ("```md\n# Example title\n```\n# Actual task\n# Later heading\n", "Actual task"),
        ("~~~md\n# Example title\n~~~\n# Actual task\n", "Actual task"),
        ("## Behavior under test\nDocumented behavior.\n", None),
        ("# \n", None),
        ("# " + "x" * 2001 + "\n", None),
        (None, None),
    ],
)
def test_task_titles_use_readme_headings(tmp_path, readme, title):
    task(tmp_path, readme=readme)
    item = report.refresh(tmp_path)["tasks"][0]
    assert item["title"] == title
    assert item["name"] == "org/refund"


@pytest.mark.parametrize(
    "heading,display_id",
    [
        (
            "T5 (Title): Reject instructions inside thread titles",
            "T5",
        ),
        (
            "N7: Attribute repeated feedback to one participant",
            None,
        ),
        ("T5 (Title): Reject title injection", "Review-5"),
        ("IMP-01: bare docs link", "IMP-01"),
    ],
)
def test_task_heading_preserves_complete_readme_title(tmp_path, heading, display_id):
    path = task(tmp_path, readme="# " + heading)
    patch = {"tasks": {path: {"display_id": display_id}}} if display_id else {}
    item = report.refresh(tmp_path, patch=patch)["tasks"][0]
    assert "heading_id" not in item
    assert item["title"] == heading
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert "| <strong><code>" + report.markdown_text(heading) + "</code></strong><br>" in markdown
    assert "<small>" not in markdown
    assert "<code>tasks/refund</code>" in markdown
    assert "<br>harbor<br>" not in markdown


@pytest.mark.parametrize("kind", ["Custom code", "LLM-as-judge", "Custom code + LLM-as-judge"])
@pytest.mark.parametrize("separator", ["\n", "\n\n"])
def test_metadata_grader_type_wins_and_readme_type_is_fallback(tmp_path, kind, separator):
    summary = "Checks saved sources, catalog, chat history and tool requests."
    path = task(tmp_path, readme=f"# Import task\n\n## Grading\n\nType: {kind}{separator}{summary}")
    item = report.refresh(tmp_path)["tasks"][0]
    assert item["grading_kind"] == kind
    assert item["grading_summary"] == summary
    assert f"{kind}<br>{summary}" in (tmp_path / ".eval-author/README.md").read_text()
    # A subsequent refresh must read the edited README rather than retain the old type.
    (tmp_path / path / "README.md").write_text("## Grading\n\nType: Custom code\n\n" + summary)
    assert report.refresh(tmp_path)["tasks"][0]["grading_kind"] == "Custom code"


def test_metadata_grader_type_overrides_readme_type(tmp_path):
    summary = "Checks saved sources, catalog, chat history and tool requests."
    path = task(tmp_path, readme=f"# Import task\n\n## Grading\n\nType: Custom code\n\n{summary}")
    item = report.refresh(tmp_path, patch={"tasks": {path: {"grading_kind": "LLM-as-judge"}}})["tasks"][0]
    assert item["grading_kind"] == "LLM-as-judge"
    assert item["grading_summary"] == summary
    # Metadata persists, so a later README edit does not change the recorded type.
    (tmp_path / path / "README.md").write_text("## Grading\n\nType: Custom code\n\n" + summary + " More.")
    assert report.refresh(tmp_path)["tasks"][0]["grading_kind"] == "LLM-as-judge"
    # An explicit "Not documented" record does not mask a README declaration.
    item = report.refresh(tmp_path, patch={"tasks": {path: {"grading_kind": "unknown"}}})["tasks"][0]
    assert item["grading_kind"] == "Custom code"


@pytest.mark.parametrize(
    "source,expected",
    [
        ("## Grading\nType: hybrid", "Custom code + LLM-as-judge"),
        ("## Grading\n**Type:** LLM-as-judge", "LLM-as-judge"),
        ("## Grading\nType: unsupported", "Not documented"),
        ("## Grading\nType: Custom code\nType: LLM-as-judge", "Not documented"),
        ("## Grading\nType: Custom code\n## Grading\nType: Custom code", "Not documented"),
        ("## Input\nType: LLM-as-judge\n## Grading\nChecks state.", None),
        ("## Grading\n```text\nType: LLM-as-judge\n```", None),
        ("## Grading\n| Type | Value |\n| --- | --- |\n| Type: LLM-as-judge | Example |", None),
        ("## Grading\nA deterministic offline verifier checks state.", None),
    ],
)
def test_readme_grader_type_requires_unambiguous_declaration(source, expected):
    assert report.readme_grading_kind(source) == expected


@pytest.mark.parametrize(
    "source,summary",
    [
        (
            "## Grading\nChecks saved refund state against policy rules.",
            "Checks saved refund state against policy rules.",
        ),
        (
            "## Grading\nChecks **saved state** against [policy rules](tests/policy.py).",
            "Checks saved state against policy rules.",
        ),
        (
            "## Grading ([Verifier](tests/test.sh))\nChecks saved state against the documented success criteria.",
            "Checks saved state against the documented success criteria.",
        ),
        (
            "## Grading\nChecks whether the saved report quotes match the original source text and meet all criteria.",
            "Checks whether the saved report quotes match the original source…",
        ),
        (
            "## Expected outcome and grading\nChecks exact author filtering and summary presence.",
            "Checks exact author filtering and summary presence.",
        ),
        (
            "```md\n## Grading\nIgnore this example of grading prose.\n```\n## Grading\nChecks the actual saved state against expected outcomes.",
            "Checks the actual saved state against expected outcomes.",
        ),
        (
            "## Grading\nVerifier: [tests/verify.py](tests/verify.py).\n\nChecks the saved answer against reference data.",
            "Checks the saved answer against reference data.",
        ),
        ("## Grading\n```python\nprint('Never use sample code as grading prose')\n```", None),
        (
            "## Grading\n| Check | Description |\n| --- | --- |\n| Saved | Grader checks saved state against expected outcome. |",
            None,
        ),
        ("## Grading\nChecks saved state.", None),
        ("# Existing task\nNo grading documentation.", None),
        (
            "## Grading\nChecks saved state against all documented criteria.\n## Grading\nChecks saved state against different criteria.",
            None,
        ),
        (None, None),
    ],
)
def test_grader_summary_is_derived_from_readme(tmp_path, source, summary):
    path = task(tmp_path, readme=source)
    item = report.refresh(tmp_path)["tasks"][0]
    assert item["grading_summary"] == summary
    if summary:
        assert 5 <= len(summary.split()) <= 10
        assert report.markdown_text(summary) in (tmp_path / ".eval-author/README.md").read_text()
    else:
        assert report.NO_GRADING_SUMMARY in (tmp_path / ".eval-author/README.md").read_text()
    if source is not None:
        assert (tmp_path / path / "README.md").read_text() == source


def test_imported_grader_summary_requires_readme_and_yields_to_canonical_summary(tmp_path):
    path = task(tmp_path, readme="# Existing task\n## Grading\nChecks saved state against all documented criteria.")
    fields = {"grading_summary": "Checks exact author filtering and summary presence."}
    with pytest.raises(report.ReportError, match="source_refs"):
        report.refresh(tmp_path, patch={"tasks": {path: fields}})
    fields["source_refs"] = [path + "/instruction.md"]
    with pytest.raises(report.ReportError, match="task README"):
        report.refresh(tmp_path, patch={"tasks": {path: fields}})
    fields["source_refs"] = [path + "/README.md"]
    assert (
        report.refresh(tmp_path, patch={"tasks": {path: fields}})["tasks"][0]["grading_summary"]
        == fields["grading_summary"]
    )
    write(tmp_path, path + "/README.md", "## Grader summary\nChecks saved refund state against policy rules.")
    assert report.refresh(tmp_path)["tasks"][0]["grading_summary"] == "Checks saved refund state against policy rules."
    write(tmp_path, path + "/README.md", "## Grader summary\nToo short.\n## Grader summary\nAlso ambiguous.")
    assert report.refresh(tmp_path)["tasks"][0]["grading_summary"] is None
    (tmp_path / path / "README.md").unlink()
    assert report.refresh(tmp_path)["tasks"][0]["grading_summary"] is None


@pytest.mark.parametrize("legacy,kind", list(report.LEGACY_GRADING_KINDS.items()))
@pytest.mark.parametrize("storage", ["html", "state"])
def test_legacy_grading_types_are_normalized_on_refresh(tmp_path, legacy, kind, storage):
    path = task(tmp_path)
    data = report.refresh(tmp_path, patch={"tasks": {path: {"grading_kind": legacy}}})
    assert data["tasks"][0]["grading_kind"] == kind
    assert data["metadata"]["tasks"][path]["grading_kind"] == kind
    # Migrate both legacy HTML imports and existing JSON state when inventory is incomplete.
    data["metadata"]["tasks"][path]["grading_kind"] = legacy
    data["tasks"][0]["grading_kind"] = legacy
    save_report_snapshot(tmp_path, data, storage)
    retained = report.refresh(tmp_path, max_entries=1)
    assert retained["tasks"][0]["status"] == "unverified"
    assert retained["tasks"][0]["grading_kind"] == kind
    assert retained["metadata"]["tasks"][path]["grading_kind"] == kind


@pytest.mark.parametrize("storage", ["html", "state"])
def test_existing_reports_without_titles_can_refresh_and_retain_tasks(tmp_path, storage):
    task(tmp_path, readme="# Refund authorization\n")
    legacy = report.refresh(tmp_path)
    del legacy["tasks"][0]["title"]
    del legacy["tasks"][0]["grading_summary"]
    save_report_snapshot(tmp_path, legacy, storage)
    retained = report.refresh(tmp_path, max_entries=1)["tasks"][0]
    assert retained["status"] == "unverified"
    assert "title" not in retained
    assert report.refresh(tmp_path)["tasks"][0]["title"] == "Refund authorization"
    # A report that previously split the ID restores the original saved H1.
    legacy["tasks"][0].update(heading_id="R1", title="authorization")
    save_report_snapshot(tmp_path, legacy, storage)
    retained = report.refresh(tmp_path, max_entries=1)["tasks"][0]
    assert retained["title"] == "Refund authorization"
    assert "heading_id" not in retained


def save_report_snapshot(root, data, storage):
    internal = root / ".eval-author/.working/report/state.json"
    if storage == "state":
        internal.write_text(json.dumps(data), encoding="utf-8")
    else:
        internal.unlink(missing_ok=True)
        template = (SCRIPTS / "report-template.html").read_text()
        write(root, ".eval-author/report.html", template.replace(report.MARKER, json.dumps(data)))


@pytest.mark.parametrize(
    "body,expected",
    [
        ("# Title\n", None),
        ("## Behavior under test\n\n## Expected result\nSuccess", None),
        ("## Behavior under test\nA\n## Behavior under test\nB", None),
        ("```md\n## Behavior under test\nFake\n```\n## Behavior under test\nReal", "Real"),
        ("~~~md\n## Behavior under test\nFake\n~~~\n## Behavior under test\nReal", "Real"),
        ("## Behavior under test\nOne paragraph.\n# Stop\nIgnored", "One paragraph."),
        ("## Behavior under test\n" + "x" * 2001, None),
    ],
)
def test_readme_section_boundaries(body, expected):
    assert report.readme_sections(body).get("Behavior under test") == expected


def test_import_fields_and_canonical_readme(tmp_path):
    path = task(tmp_path, readme="# Existing documentation\nOther sections.")
    metadata = {
        "tasks": {
            path: {
                "behavior": "Documented import.",
                "expected_outcome": "Known result.",
                "source_refs": [path + "/README.md"],
            }
        }
    }
    assert report.refresh(tmp_path, patch=metadata)["tasks"][0]["behavior"] == "Documented import."
    write(tmp_path, path + "/README.md", "## Behavior under test\nCanonical.\n## Expected result\nCanonical result.")
    assert report.refresh(tmp_path)["tasks"][0]["behavior"] == "Canonical."


def test_task_paths_keep_same_named_parent_directories_distinct(tmp_path):
    original = task(tmp_path)
    paths = [original]
    for suite in ["import", "chat"]:
        path = f".eval-author/suites/{suite}/tasks/refund"
        shutil.copytree(tmp_path / original, tmp_path / path)
        paths.append(path)
    first = report.refresh(tmp_path)
    assert [t["path"] for t in first["tasks"]] == sorted(paths)
    assert len({report.task_sort(t)[0] for t in first["tasks"]}) == 3
    # Search scope and overlapping roots do not define sections or duplicate rows.
    second = report.refresh(tmp_path, task_roots=[".", ".eval-author/suites/chat/tasks"])
    assert second["tasks"] == first["tasks"]


def test_runs_group_attempts_without_double_counting(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one")
    trial(tmp_path, path + "/two", reward=0)
    standalone = ".eval-author/jobs/checks/single"
    trial(tmp_path, standalone, reward=0)
    metadata = rule(path)
    metadata["runs"].update(rule(standalone)["runs"])
    result = report.refresh(tmp_path, patch=metadata)
    assert len(result["runs"]) == 1
    runs = {r["path"]: r for r in result["runs"]}
    assert runs[path]["score_label"] == "1 / 2 reward (50.0%) · 2/2 scored"
    assert len(runs[path]["attempts"]) == 2
    assert runs[path]["route"] == "jobs/baseline"
    legacy = result["standalone_trials"][0]
    assert legacy["path"] == standalone
    assert legacy["score_label"] == "0 / 1 reward (0.0%) · 1/1 scored"
    assert legacy["route"].endswith("/org%2Frefund/trials/single")
    assert all(run["kind"] == "job" for run in result["runs"])


def test_missing_nonbinary_errors_and_invalid_are_not_passes(tmp_path):
    path = job(tmp_path, total=5)
    trial(tmp_path, path + "/pass")
    trial(tmp_path, path + "/fail", reward=0)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, path + "/invalid", trial_id="invalid-1")
    metadata = rule(path)
    metadata["runs"][path]["invalid_attempts"] = {"invalid-1": "Synthetic invalid grader evidence."}
    run = report.refresh(tmp_path, patch=metadata)["runs"][0]
    assert (run["passed"], run["failed"], run["errors"], run["invalid"], run["unknown"]) == (1, 1, 1, 1, 1)
    assert run["status"] == "incomplete"
    assert run["attempts"][-1]["rewards"]  # Native rewards retained.


def test_unknown_pass_rule_preserves_native_reward(tmp_path):
    path = ".eval-author/jobs/checks/graded"
    trial(tmp_path, path, reward=0.65)
    run = report.refresh(tmp_path)["standalone_trials"][0]
    assert run["passed"] is None
    assert "0.65" in run["score_label"]
    assert run["attempts"][0]["rewards"]["reward"] == 0.65


def test_control_role_and_config_only_run(tmp_path):
    trial(tmp_path, ".eval-author/jobs/controls/nop", agent="nop", reward=0)
    write(tmp_path, ".eval-author/jobs/pending/config.json", {"job_name": "pending", "agents": []})
    result = report.refresh(tmp_path)
    runs = {r["name"]: r for r in result["runs"]}
    assert result["standalone_trials"][0]["role"] == "control"
    assert runs["pending"]["status"] == "unknown"
    assert runs["pending"]["total"] is None


def test_malformed_native_result_visible_without_fabricated_score(tmp_path):
    write(tmp_path, ".eval-author/jobs/bad/trial/result.json", "{broken")
    run = report.refresh(tmp_path)["standalone_trials"][0]
    assert run["status"] == "unknown"
    assert run["passed"] is None
    assert run["issues"]


def test_duplicate_trial_identity_disables_score(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one", trial_id="duplicate")
    trial(tmp_path, path + "/two", trial_id="duplicate")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["passed"] is None
    assert run["score_label"].startswith("Score unavailable (duplicate attempt IDs)")
    assert run["reward_sum"] is None
    assert any("Duplicate" in issue for issue in run["issues"])


def test_job_rollup_preserves_fractional_credit_and_counts_trials(tmp_path):
    path = job(tmp_path, total=3)
    for name, reward in [("one", 1), ("two", 0.65), ("three", 0)]:
        trial(tmp_path, path + "/" + name, reward=reward)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["reward_sum"] == pytest.approx(1.65)
    assert (run["score_min"], run["score_max"], run["scored"], run["completed"]) == (0, 3, 3, 3)
    assert (run["passed"], run["failed"]) == (1, 2)
    assert run["score_label"] == "1.65 / 3 reward (55.0%) · 3/3 scored"


def test_named_reward_without_pass_rule_and_explicit_scale(tmp_path):
    path = job(tmp_path)
    for name, quality in [("one", 3), ("two", 5)]:
        data = trial(tmp_path, path + "/" + name)
        data["verifier_result"]["rewards"] = {"quality": quality, "reward": 0}
        write(tmp_path, path + "/" + name + "/result.json", data)
    patch = {"runs": {path: {"reward_key": "quality", "reward_min": 0, "reward_max": 5}}}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert run["reward_sum"] == 8
    assert run["passed"] is None
    assert run["score_label"] == "8 / 10 quality (80.0%) · 2/2 scored"


@pytest.mark.parametrize("bounds", [None, (0, 1), (-1, 1)])
def test_graph_needs_a_documented_range_and_preserves_out_of_range_reward(tmp_path, bounds):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one", reward=2)
    fields = {"reward_key": "reward"}
    if bounds:
        fields.update(reward_min=bounds[0], reward_max=bounds[1])
    run = report.refresh(tmp_path, patch={"runs": {path: fields}})["runs"][0]
    assert run["reward_sum"] == 2
    assert run["score_min"] is None and run["score_max"] is None
    assert "2 total" in run["score_label"]


def test_nonzero_reward_minimum_sets_the_meter_range(tmp_path):
    path = job(tmp_path)
    trial(tmp_path, path + "/one", reward=-1)
    trial(tmp_path, path + "/two", reward=0)
    patch = {"runs": {path: {"reward_min": -1, "reward_max": 1}}}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert (run["reward_sum"], run["score_min"], run["score_max"]) == (-1, -2, 2)
    assert "25.0%" in run["score_label"]


def test_partial_job_separates_reward_from_completion_and_missing_data(tmp_path):
    path = job(tmp_path, total=6)
    trial(tmp_path, path + "/scored", reward=0.65)
    trial(tmp_path, path + "/missing", reward=None)
    trial(tmp_path, path + "/pending", finished=False)
    trial(tmp_path, path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, path + "/invalid", trial_id="invalid")
    patch = rule(path)
    patch["runs"][path]["invalid_attempts"] = {"invalid": "Known verifier defect."}
    run = report.refresh(tmp_path, patch=patch)["runs"][0]
    assert run["status"] == "incomplete"
    assert (run["reward_sum"], run["score_max"], run["scored"]) == (0.65, 1, 1)
    assert (run["errors"], run["invalid"], run["unknown"]) == (1, 1, 3)
    assert "1/6 scored; partial" in run["score_label"]
    assert "65.0%" in run["score_label"]


def test_no_scored_trials_is_unavailable_even_with_a_pass_rule(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/pending", finished=False)
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["reward_sum"] is None and run["passed"] is None
    assert run["score_max"] is None
    assert run["score_label"] == "Score unavailable · 0/1 scored; partial"


def test_unfinished_job_is_partial_even_when_all_trial_scores_exist(tmp_path):
    path = job(tmp_path, total=1)
    native = tmp_path / path / "result.json"
    result = json.loads(native.read_text())
    result["finished_at"] = None
    write(tmp_path, path + "/result.json", result)
    trial(tmp_path, path + "/one")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert run["status"] == "incomplete"
    assert run["reward_sum"] == 1
    assert "1/1 scored; partial" in run["score_label"]


def test_retry_or_conflicting_total_cannot_inflate_reward(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/first")
    trial(tmp_path, path + "/retry")
    run = report.refresh(tmp_path, patch=rule(path))["runs"][0]
    assert len(run["attempts"]) == 2
    assert run["reward_sum"] is None and run["score_max"] is None
    assert run["passed"] is None
    assert "conflicting trial total or retries" in run["score_label"]


def test_job_identity_uses_disk_name_and_full_path_with_overlapping_roots(tmp_path):
    paths = [job(tmp_path, f".eval-author/jobs/{suite}/same", total=1) for suite in ["a", "b"]]
    for path in paths:
        trial(tmp_path, path + "/one")
        write(tmp_path, path + "/config.json", {"job_name": "different-config-name"})
    data = report.refresh(tmp_path, jobs_roots=[".eval-author/jobs", ".eval-author/jobs/a"])
    assert {run["path"] for run in data["runs"]} == set(paths)
    assert all(run["name"] == "same" and run["reward_sum"] == 1 for run in data["runs"])
    assert all("on-disk" in " ".join(run["issues"]) for run in data["runs"])
    assert data["standalone_trials"] == []


def test_new_job_owns_previously_standalone_trial_without_ghost_rows(tmp_path):
    parent = ".eval-author/jobs/becomes-job"
    trial(tmp_path, parent + "/one")
    report.refresh(tmp_path)
    job(tmp_path, parent, total=1)
    updated = report.refresh(tmp_path)
    assert len(updated["runs"]) == 1
    assert len(updated["runs"][0]["attempts"]) == 1
    assert updated["standalone_trials"] == []


def test_job_replacing_standalone_at_same_path_has_no_legacy_duplicate(tmp_path):
    path = ".eval-author/jobs/reclassified"
    trial(tmp_path, path)
    report.refresh(tmp_path)
    job(tmp_path, path, total=1)
    trial(tmp_path, path + "/one")
    updated = report.refresh(tmp_path)
    assert [run["path"] for run in updated["runs"]] == [path]
    assert updated["standalone_trials"] == []


def test_browser_renders_job_reward_meter_and_separate_legacy_trials(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise report rendering")
    path = job(tmp_path, total=3)
    for name, reward in [("one", 1), ("two", 0.65), ("three", 0)]:
        trial(tmp_path, path + "/" + name, reward=reward)
    trial(tmp_path, ".eval-author/jobs/checks/pending", finished=False)
    data = report.refresh(tmp_path, patch=rule(path))
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function runRow(") : source.index("byId('search').addEventListener")]
    program = r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element{
  constructor(tag,text,className){this.tag=tag;this.textContent=text||'';this.className=className;this.children=[];this.style={};this.dataset={};this.attrs={};}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  setAttribute(key,value){this.attrs[key]=value;}
}
const ids={};
const byId=id=>ids[id]||(ids[id]=new Element('div'));
const context={data:input.data,byId,element:(...args)=>new Element(...args),viewerAction:label=>new Element('button',label)};
vm.createContext(context);vm.runInContext(input.helper+'\ndrawRuns();',context);
const descendants=node=>[node,...node.children.flatMap(descendants)];
assert.equal(ids['run-rows'].children.length,1);
assert.equal(ids['legacy-rows'].children.length,1);
assert.equal(ids['legacy-trials'].hidden,false);
assert.equal(ids['legacy-summary'].textContent,'Legacy trials (1)');
const job=ids['run-rows'].children[0];
assert.equal(job.dataset.kind,'job');
const nodes=descendants(job);
assert(nodes.some(n=>n.className==='run-path'&&n.textContent===input.data.runs[0].path));
const meter=nodes.find(n=>n.attrs.role==='meter');
assert.equal(meter.attrs['aria-valuenow'],'1.65');
assert.equal(meter.attrs['aria-valuemax'],'3');
assert.equal(nodes.find(n=>n.className==='score-fill').style.width,'55%');
assert(!descendants(ids['legacy-rows']).some(n=>n.attrs.role==='meter'));
input.data.runs[0].status='unverified';
vm.runInContext('drawRuns();',context);
assert(!descendants(ids['run-rows']).some(n=>n.attrs.role==='meter'));
assert(descendants(ids['run-rows']).some(n=>n.textContent==='Inventory unverified'));
input.data.runs=[];input.data.standalone_trials=[];
vm.runInContext('drawRuns();',context);
assert.equal(ids['no-runs'].hidden,false);
assert.equal(ids['legacy-trials'].hidden,true);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "data": data}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_markdown_legacy_trials_mirrors_the_separate_html_section(tmp_path):
    path = job(tmp_path, total=1)
    trial(tmp_path, path + "/one")
    legacy = ".eval-author/jobs/checks/single"
    trial(tmp_path, legacy)
    report.refresh(tmp_path, patch=rule(path))
    raw = (tmp_path / ".eval-author/README.md").read_text()
    jobs, standalone = raw.split("### Legacy trials")
    assert "| Job | Reward |" in jobs and "| Trial | Reward |" in standalone
    assert legacy not in jobs and path not in standalone
    assert legacy in standalone and path in jobs


def test_incomplete_search_preserves_old_rows(tmp_path):
    task(tmp_path)
    report.refresh(tmp_path)
    data = report.refresh(tmp_path, max_entries=1)
    assert not data["inventory"]["complete"]
    assert data["tasks"][0]["status"] == "unverified"
    assert data["warnings"]


def test_stable_tasks_visible_and_legacy_copies_require_explicit_selection(tmp_path):
    final = task(tmp_path)
    draft = ".eval-author/task-drafts/refund"
    shutil.copytree(tmp_path / final, tmp_path / draft)
    first = report.refresh(tmp_path)
    assert {item["path"] for item in first["tasks"]} == {final}
    later = ".eval-author/tasks/cancel"
    shutil.copytree(tmp_path / final, tmp_path / later)
    updated = report.refresh(tmp_path)
    assert {item["path"] for item in updated["tasks"]} == {final, later}
    assert updated["metadata"]["task_roots"] == ["."]
    assert updated["inventory"]["complete"]
    explicit = report.refresh(tmp_path, task_roots=[".eval-author/task-drafts"])
    assert {item["path"] for item in explicit["tasks"]} == {draft}


def test_harbor_and_gym_workloads_share_reports_without_harbor_links_for_gym(tmp_path):
    harbor = task(tmp_path)
    draft = ".eval-author/tasks/gym-refund"
    readme = draft + "/README.md"
    write(
        tmp_path,
        readme,
        "# NeMo Gym refund policy\n## Behavior under test\nNeMo Gym refunds.\n## Expected result\nRefund saved.",
    )
    manifest = draft + "/environments/refund/manifest.yaml"
    write(tmp_path, manifest, "name: refund\n")
    write(tmp_path, draft + "/environments/refund/config.yaml", "components: {}\n")
    parameterized = ".eval-author/tasks/row-task"
    write(tmp_path, parameterized + "/tasks.jsonl", "{}\n")
    write(tmp_path, parameterized + "/instruction.template.md", "Refund {amount}")
    write(tmp_path, parameterized + "/README.md", "## Behavior under test\nRows.\n## Expected result\nRow graded.")
    data = report.refresh(tmp_path)
    found = {item["path"]: item for item in data["tasks"]}
    assert set(found) == {harbor, manifest, parameterized}
    assert found[harbor]["provider"] == "harbor"
    assert found[manifest]["provider"] == found[parameterized]["provider"] == "gym"
    assert found[manifest]["behavior"] == "NeMo Gym refunds."
    assert found[manifest]["title"] == "NeMo Gym refund policy"
    assert found[manifest]["readme_path"] == readme
    assert "Refund saved." in found[manifest]["readme_html"]
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "[NeMo Gym source](tasks/gym-refund/environments/refund/manifest.yaml)" in raw
    assert "[Task README](tasks/gym-refund/README.md)" in raw
    assert "task-definitions/refund" in raw
    assert "task-definitions/row-task" not in raw
    assert report.refresh(tmp_path) == data


@pytest.mark.parametrize("grading_kind", ["Custom code", "LLM-as-judge", "Custom code + LLM-as-judge"])
def test_browser_task_titles_graders_and_gym_actions(tmp_path, grading_kind):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise task rendering")
    harbor_path = task(
        tmp_path,
        readme="# R1 (Policy): Approve **refunds**\n## Grading\nChecks saved refund state against policy rules.\n",
    )
    source_path = "environments/gym case/manifest.yaml"
    write(tmp_path, source_path, "name: example\n")
    write(tmp_path, "environments/gym case/README.md", "## Behavior under test\nNeMo Gym.\n## Expected result\nSaved.")
    data = report.refresh(
        tmp_path,
        patch={"tasks": {harbor_path: {"grading_kind": grading_kind, "grading_rule": "All state checks pass."}}},
    )
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function drawCases()") : source.index("function runRow(")]
    program = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element {
  constructor(tag,text,className){this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.dataset={};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs=this.attrs||{};this.attrs[key]=value;}
}
const ids={'case-tables':new Element('div'),search:{value:''},'no-cases':{}};
const viewers=[],readmes=[];
const context={data:input.data,byId:id=>ids[id],
  element:(tag,text,className)=>new Element(tag,text,className),
  parent:path=>path.includes('/')?path.slice(0,path.lastIndexOf('/')):'.',
  displayTaskPath:path=>path.replace(/^\.eval-author\//,''),
  viewerAction:(...args)=>{viewers.push(args);return new Element('button');},
  readmeAction:task=>{readmes.push(task.readme_path);return new Element('button');}
};
vm.createContext(context);vm.runInContext(input.helper+'drawCases();',context);
function descendants(node){return [node,...node.children.flatMap(descendants)];}
function taskRows(){return descendants(ids['case-tables']).filter(node=>node.tag==='tr'&&node.dataset.task);}
for(const table of descendants(ids['case-tables']).filter(node=>node.tag==='table')){
  assert.deepEqual(table.children[0].children[0].children.map(node=>node.textContent),
    ['Task','Status','Behavior','Expected outcome','Grader','Actions']);
}
for(const row of taskRows()){
  const task=input.data.tasks.find(task=>task.id===row.dataset.task);
  assert.equal(row.children.length,6);
  assert.equal(row.children[0].children[0].textContent,task.title||task.name);
  assert(!descendants(row.children[0]).some(node=>node.textContent===task.provider));
  assert.equal(row.children[1].children[0].textContent,task.readiness[0].toUpperCase()+task.readiness.slice(1));
  assert.equal(row.children[1].children[0].className,'badge badge-'+task.readiness);
  assert.equal(row.children[1].children[1].attrs.role,'tooltip');
  assert.equal(row.children[1].children[0].attrs['aria-describedby'],row.children[1].children[1].id);
  assert.equal(row.children[4].children[0].textContent,task.grading_kind);
  assert.equal(row.children[4].children[1].textContent,task.grading_summary||'No grader description documented in README.');
}
assert(!descendants(ids['case-tables']).some(node=>String(node.textContent).includes('All state checks pass.')));
const links=descendants(ids['case-tables']).filter(node=>node.tag==='a');
assert.equal(viewers.length,1);
assert.equal(viewers[0][0],'View task');
assert.equal(links.length,1);
assert.equal(links[0].textContent,'NeMo Gym source');
assert.equal(links[0].href,'../environments/gym%20case/manifest.yaml');
assert(readmes.includes('environments/gym case/README.md'));
ids.search.value='gym case';viewers.length=0;
vm.runInContext('drawCases();',context);
assert.equal(viewers.length,0);
assert.equal(ids['no-cases'].hidden,true);
assert.equal(taskRows()[0].children[0].children[0].textContent,'gym case');
ids.search.value='approve refunds';vm.runInContext('drawCases();',context);
assert.equal(taskRows().length,1);
assert.equal(taskRows()[0].children[0].children[0].textContent,'R1 (Policy): Approve refunds');
const harbor=input.data.tasks.find(task=>task.provider==='harbor');
ids.search.value=harbor.grading_kind;vm.runInContext('drawCases();',context);
assert.equal(taskRows().length,1);
delete harbor.title;
vm.runInContext('drawCases();',context);
assert.equal(taskRows()[0].children[0].children[0].textContent,'org/refund');
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "data": data}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_browser_task_status_is_separate_from_inventory_status(tmp_path):
    import journey_state

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise task rendering")
    draft, accepted, unknown = [task(tmp_path, name=name) for name in ("alpha", "beta", "gamma")]
    evidence = ".eval-author/jobs/reviewed-baseline.json"
    write(tmp_path, evidence, {"reward": 0, "validation": "passed"})
    journey_state.record_task(tmp_path, task=draft, status="draft", source="Authoring is underway")
    journey_state.record_task(
        tmp_path, task=accepted, status="accepted", source="Workflow review complete", evidence=[evidence]
    )
    report.refresh(tmp_path)
    data = report.refresh(tmp_path, max_entries=1)
    assert not data["inventory"]["complete"]
    assert all(item["status"] == "unverified" for item in data["tasks"])
    expected = {
        "harbor:" + path: status for path, status in ((draft, "Draft"), (accepted, "Accepted"), (unknown, "Unknown"))
    }
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function drawCases()") : source.index("function runRow(")]
    program = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class Element {
  constructor(tag,text,className){this.tag=tag;this.textContent=text;this.className=className;this.children=[];this.dataset={};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs=this.attrs||{};this.attrs[key]=value;}
}
const ids={'case-tables':new Element('div'),search:{value:''},'no-cases':{}};
const context={data:input.data,byId:id=>ids[id],
  element:(tag,text,className)=>new Element(tag,text,className),
  parent:path=>path.includes('/')?path.slice(0,path.lastIndexOf('/')):'.',
  displayTaskPath:path=>path.replace(/^\.eval-author\//,''),
  viewerAction:()=>new Element('button'),readmeAction:()=>new Element('button')
};
function descendants(node){return [node,...node.children.flatMap(descendants)];}
function taskRows(){return descendants(ids['case-tables']).filter(node=>node.dataset.task);}
vm.createContext(context);vm.runInContext(input.helper+'drawCases();',context);
const headers=descendants(ids['case-tables']).filter(node=>node.tag==='th');
assert.deepEqual(headers.map(node=>node.textContent),['Task','Status','Behavior','Expected outcome','Grader','Actions']);
assert.equal(taskRows().length,3);
for(const row of taskRows()){
  assert.equal(row.children.length,6);
  const inventory=descendants(row.children[0]);
  assert(inventory.some(node=>node.textContent==='Inventory unverified'));
  const status=row.children[1];
  assert.equal(status.children[0].tag,'strong');
  assert.equal(status.children[0].textContent,input.expected[row.dataset.task]);
  assert(status.children[1].textContent.length>0);
  assert(!descendants(status).some(node=>node.textContent==='Inventory unverified'));
}
ids.search.value='draft';vm.runInContext('drawCases();',context);
assert.equal(taskRows().length,1);
assert.equal(input.expected[taskRows()[0].dataset.task],'Draft');
assert.equal(ids['no-cases'].hidden,true);
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "data": data, "expected": expected}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_unreadable_run_entry_does_not_hide_readable_siblings(tmp_path, monkeypatch):
    write(tmp_path, "results/bad/config.json", {})
    write(tmp_path, "results/config.json", {"job_name": "readable"})
    scandir = report.os.scandir

    class Entry:
        def __init__(self, entry):
            self.name, self.path, self.entry = entry.name, entry.path, entry

        def stat(self, **kwargs):
            if self.name == "bad":
                raise PermissionError("Synthetic inaccessible entry")
            return self.entry.stat(**kwargs)

    class Listing:
        def __enter__(self):
            with scandir(tmp_path / "results") as listing:
                return iter([Entry(entry) for entry in listing])

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(report.os, "scandir", lambda _: Listing())
    files, complete = report.run_files(tmp_path, ["results"], 12, 50000)
    assert not complete
    assert files == [tmp_path / "results/config.json"]


def test_report_lock_blocks_concurrent_writer_and_releases_after_process_death(tmp_path):
    directory = tmp_path / ".eval-author/.working/report"
    directory.mkdir(parents=True)
    ready = tmp_path / "ready"
    program = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import report
with report.report_lock(Path(sys.argv[2])):
    Path(sys.argv[3]).touch()
    sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(SCRIPTS), str(directory / ".report.lock"), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Child process did not acquire the report lock"
        with pytest.raises(report.ReportError, match="already running"):
            report.refresh(tmp_path)
        assert not (tmp_path / ".eval-author/report.html").exists()
    finally:
        process.kill()
        process.communicate(timeout=10)
    assert report.refresh(tmp_path)["inventory"]["complete"]
    assert report.refresh(tmp_path)["inventory"]["complete"]


def test_nested_checkout_exclusion_and_explicit_selection(tmp_path):
    task(tmp_path)
    nested = tmp_path / "nested"
    task(nested, "nested-refund")
    write(nested, ".git", "gitdir: elsewhere")
    result = report.refresh(tmp_path)
    assert len(result["tasks"]) == 1
    assert result["inventory"]["excluded"] == [".eval-author/.working", "nested"]
    assert len(report.refresh(tmp_path, task_roots=[".", "nested"])["tasks"]) == 2


def test_no_symlink_following(tmp_path):
    path = task(tmp_path)
    alias = tmp_path / ".eval-author/alias"
    try:
        alias.symlink_to(tmp_path / path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    assert len(report.refresh(tmp_path)["tasks"]) == 1
    with pytest.raises(report.ReportError, match="Symlink"):
        report.refresh(tmp_path, task_roots=[".eval-author/alias"])


@pytest.mark.parametrize(
    "patch",
    [
        {"summary": "Unrequested narrative"},
        {"tasks": {"../escape": {}}},
        {"runs": {".eval-author/jobs/a": {"pass_value": 1}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 0}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 1, "reward_max": 1}}},
        {"runs": {".eval-author/jobs/a": {"reward_min": 5, "reward_max": 0}}},
        {"tasks": {".eval-author/tasks/a": {"behavior": "Unsourced"}}},
    ],
)
def test_bad_metadata_preserves_report(tmp_path, patch):
    report.refresh(tmp_path)
    output = tmp_path / ".eval-author/report.html"
    before = output.read_bytes()
    readme_before = (tmp_path / ".eval-author/README.md").read_bytes()
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path, patch=patch)
    assert output.read_bytes() == before
    assert (tmp_path / ".eval-author/README.md").read_bytes() == readme_before


def test_html_escape_and_embedded_payload(tmp_path):
    path = task(
        tmp_path,
        readme="## Behavior under test\n</script><img src=x onerror=alert(1)>\n## Expected result\n<script>bad()</script>",
    )
    report.refresh(tmp_path, patch={"tasks": {path: {"display_id": '<img onerror="bad()">'}}})
    output = tmp_path / ".eval-author/report.html"
    raw = output.read_text()
    assert "</script><img src=x" not in raw
    data, _ = report.previous_report(output)
    assert "<img" in data["tasks"][0]["behavior"]
    assert ".innerHTML" not in raw
    preview = data["tasks"][0]["readme_html"]
    assert "<script>" not in preview
    assert "<img" not in preview
    assert "&lt;script&gt;" in preview


def test_readme_preview_preserves_markdown_and_refreshes(tmp_path):
    path = task(
        tmp_path,
        readme=(
            "# Task\n\n> Quoted input\n\n**Expected** `state`\n\n"
            "| Check | Result |\n| --- | --- |\n| Saved | Yes |\n\n"
            '```json\n{"reward": 1}\n```\n\n'
            "[Verifier](tests/verify.py)\n\n[Unsafe](javascript:alert(1))\n"
        ),
    )
    first = report.refresh(tmp_path)
    preview = first["tasks"][0]["readme_html"]
    for fragment in ("<h1>Task</h1>", "<blockquote>", "<strong>Expected</strong>", "<table>", 'class="language-json"'):
        assert fragment in preview
    assert "Verifier</a>" in preview
    assert "href=" not in preview
    assert 'href="javascript:' not in preview
    readme = tmp_path / path / "README.md"
    readme.write_text("# Updated\n", encoding="utf-8")
    assert report.refresh(tmp_path)["tasks"][0]["readme_html"] == "<h1>Updated</h1>\n"
    readme.unlink()
    assert report.refresh(tmp_path)["tasks"][0]["readme_html"] is None


def test_readme_preview_is_bounded(tmp_path):
    task(tmp_path, readme="x" * (report.MAX_README_BYTES + 1))
    data = report.refresh(tmp_path)
    assert data["tasks"][0]["readme_html"] is None
    assert "Task README could not be read." in data["tasks"][0]["issues"]


def test_legacy_readme_is_previewed_without_authoring_missing_descriptions(tmp_path):
    source = "# Existing task\n\n## Grading\nThe verifier checks saved state.\n"
    path = task(tmp_path, readme=source)
    item = report.refresh(tmp_path)["tasks"][0]
    assert item["behavior"] is None
    assert item["expected_outcome"] is None
    assert item["grading_rule"] is None
    assert "The verifier checks saved state." in item["readme_html"]
    assert (tmp_path / path / "README.md").read_text() == source


def test_corrupt_existing_report_preserves_output(tmp_path):
    output = write(tmp_path, ".eval-author/report.html", "<html>not a generated report</html>")
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path)
    assert output.read_text() == "<html>not a generated report</html>"


def test_copied_script_and_template_installation(tmp_path):
    installed = tmp_path / "copied-skill/scripts"
    installed.mkdir(parents=True)
    for name in [
        "report.py",
        "report-template.html",
        "search_tasks.py",
        "working_paths.py",
        "journey_state.py",
        "runtime_policy.py",
        "runtime-policy.json",
    ]:
        shutil.copy2(SCRIPTS / name, installed / name)
    project = tmp_path / "project"
    project.mkdir()
    task(project)
    result = subprocess.run(
        [sys.executable, "-I", str(installed / "report.py"), "refresh", "--repo", str(project)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tasks"] == 1
    assert (project / ".eval-author/report.html").is_file()
    assert json.loads(result.stdout)["readme"] == ".eval-author/README.md"
    assert (project / ".eval-author/README.md").is_file()


def test_task_readiness_is_projected_without_changing_tasks_or_suite_selection(tmp_path):
    import journey_state

    path = task(tmp_path, readme="# Reviewed task\n")
    configured = write(tmp_path, ".eval-author/run.yaml", "tasks: []\n")
    journey_state.initialize(tmp_path, agent="assistant", journey="create", setup="harbor")
    journey_state.record_task(tmp_path, task=path, status="draft", source="Case creation in progress")
    draft = report.refresh(tmp_path)
    assert draft["tasks"][0]["readiness"] == "draft"
    assert draft["tasks"][0]["status"] == "found"
    before_task = (tmp_path / path / "README.md").read_bytes()
    evidence = ".eval-author/jobs/reviewed-baseline.json"
    write(tmp_path, evidence, {"reward": 0, "validation": "passed", "review": "complete"})
    journey_state.record_task(
        tmp_path, task=path, status="accepted", source="Existing workflow review completed", evidence=[evidence]
    )
    before_state = journey_state.state_path(tmp_path).read_bytes()
    accepted = report.refresh(tmp_path)
    row = accepted["tasks"][0]
    assert row["readiness"] == "accepted"
    assert row["path"] == path
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert "| Status |" in markdown
    assert "Accepted<br>Existing workflow review completed" in markdown
    assert report.previous_report(tmp_path / ".eval-author/report.html")[0]["tasks"] == accepted["tasks"]
    assert (tmp_path / path / "README.md").read_bytes() == before_task
    assert configured.read_text() == "tasks: []\n"
    assert journey_state.state_path(tmp_path).read_bytes() == before_state
    assert report.refresh(tmp_path) == accepted


def test_repository_root_task_keeps_unknown_readiness_without_hiding_other_tasks(tmp_path):
    import journey_state

    write(tmp_path, "task.toml", '[task]\nname = "root-task"\n')
    write(tmp_path, "instruction.md", "Repository-root task")
    path = task(tmp_path)
    journey_state.initialize(tmp_path)
    journey_state.record_task(tmp_path, task=path, status="draft", source="Case creation in progress")
    state_path = journey_state.state_path(tmp_path)
    before_state = state_path.read_bytes()

    data = report.refresh(tmp_path)

    rows = {row["path"]: row for row in data["tasks"]}
    assert rows["."]["readiness"] == "unknown"
    assert rows["."]["status"] == "found"
    assert "readiness" in rows["."]["readiness_reason"].lower()
    assert rows[path]["readiness"] == "draft"
    assert rows[path]["readiness_reason"] == "Case creation in progress"
    assert report.previous_report(tmp_path / ".eval-author/report.html")[0]["tasks"] == data["tasks"]
    assert "Unknown<br>" in (tmp_path / ".eval-author/README.md").read_text()
    assert state_path.read_bytes() == before_state
    assert report.refresh(tmp_path) == data


@pytest.mark.parametrize("change", ["task", "evidence", "missing-evidence"])
def test_report_reopens_changed_acceptance_without_rewriting_provenance(tmp_path, change):
    import journey_state

    path = task(tmp_path)
    evidence = ".eval-author/jobs/acceptance.json"
    write(tmp_path, evidence, {"accepted": True})
    journey_state.initialize(tmp_path)
    journey_state.record_task(tmp_path, task=path, status="accepted", source="Reviewed result", evidence=[evidence])
    assert report.refresh(tmp_path)["tasks"][0]["readiness"] == "accepted"
    before_state = journey_state.state_path(tmp_path).read_bytes()
    if change == "task":
        write(tmp_path, path + "/instruction.md", "Changed scenario")
    elif change == "evidence":
        write(tmp_path, evidence, {"accepted": False})
    else:
        (tmp_path / evidence).unlink()
    row = report.refresh(tmp_path)["tasks"][0]
    assert row["readiness"] == "draft"
    assert row["status"] == "found"
    assert row["readiness_reason"] != "Reviewed result"
    assert journey_state.state_path(tmp_path).read_bytes() == before_state


@pytest.mark.parametrize("change", ["changed-evidence", "missing-evidence"])
def test_incomplete_inventory_recomputes_retained_task_readiness(tmp_path, change):
    import journey_state

    path = task(tmp_path)
    evidence = write(tmp_path, ".eval-author/jobs/acceptance.json", {"accepted": True})
    journey_state.record_task(
        tmp_path,
        task=path,
        status="accepted",
        source="Workflow review complete",
        evidence=[evidence.relative_to(tmp_path).as_posix()],
    )
    assert report.refresh(tmp_path)["tasks"][0]["readiness"] == "accepted"
    before_state = journey_state.state_path(tmp_path).read_bytes()
    if change == "changed-evidence":
        evidence.write_text('{"accepted": false}')
    else:
        evidence.unlink()
    data = report.refresh(tmp_path, max_entries=1)
    assert not data["inventory"]["complete"]
    assert len(data["tasks"]) == 1
    row = data["tasks"][0]
    assert row["path"] == path
    assert row["status"] == "unverified"
    assert row["readiness"] == "draft"
    assert row["readiness_reason"] != "Workflow review complete"
    assert report.previous_report(tmp_path / ".eval-author/report.html")[0]["tasks"] == data["tasks"]
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert "Inventory unverified" in markdown
    assert "Draft<br>" in markdown
    assert "Accepted<br>" not in markdown
    assert journey_state.state_path(tmp_path).read_bytes() == before_state


def test_native_gym_manifest_uses_explicit_task_root_not_readme_parent(tmp_path):
    import journey_state

    root = ".eval-author/tasks/gym-case"
    manifest = root + "/environments/case/manifest.yaml"
    write(tmp_path, manifest, "name: case\n")
    write(tmp_path, root + "/README.md", "# Root documentation\n")
    write(tmp_path, root + "/environments/README.md", "# Nested documentation\n")
    journey_state.initialize(tmp_path, setup="gym")
    journey_state.record_task(tmp_path, task=root, status="draft", source="Native case not yet validated")
    row = report.refresh(tmp_path)["tasks"][0]
    assert row["path"] == manifest
    assert row["readiness"] == "draft"
    assert row["readiness_reason"] == "Native case not yet validated"


def test_run_results_and_legacy_report_cannot_infer_task_acceptance(tmp_path):
    path = task(tmp_path)
    run = job(tmp_path, total=1)
    trial(tmp_path, run + "/pass", reward=1)
    data = report.refresh(tmp_path)
    assert data["tasks"][0]["readiness"] == "unknown"
    # Simulate a report generated before task status fields existed.
    del data["tasks"][0]["readiness"]
    del data["tasks"][0]["readiness_reason"]
    internal = tmp_path / ".eval-author/.working/report/state.json"
    write(tmp_path, internal.relative_to(tmp_path), data)
    assert report.refresh(tmp_path)["tasks"][0]["readiness"] == "unknown"
    # Even an imported snapshot claiming acceptance is not workflow authority.
    data["tasks"][0].update(readiness="accepted", readiness_reason="Legacy display label")
    write(tmp_path, internal.relative_to(tmp_path), data)
    refreshed = report.refresh(tmp_path)
    assert refreshed["tasks"][0]["readiness"] == "unknown"
    assert refreshed["tasks"][0]["path"] == path
    assert not (tmp_path / ".eval-author/.working/journey-state.json").exists()
    with pytest.raises(report.ReportError):
        report.refresh(tmp_path, patch={"tasks": {path: {"readiness": "accepted"}}})


@pytest.mark.parametrize("missing", [False, True])
def test_invalid_or_missing_workflow_state_cannot_reuse_report_acceptance(tmp_path, missing):
    import journey_state

    path = task(tmp_path)
    evidence = ".eval-author/jobs/acceptance.json"
    write(tmp_path, evidence, {"accepted": True})
    journey_state.initialize(tmp_path)
    journey_state.record_task(tmp_path, task=path, status="accepted", source="Reviewed result", evidence=[evidence])
    assert report.refresh(tmp_path)["tasks"][0]["readiness"] == "accepted"
    state_path = journey_state.state_path(tmp_path)
    if missing:
        state_path.unlink()
    else:
        state_path.write_text('{"invalid": true}')
    refreshed = report.refresh(tmp_path)
    assert refreshed["tasks"][0]["readiness"] == "unknown"
    if missing:
        assert not state_path.exists()
    else:
        assert "workflow state" in " ".join(refreshed["warnings"])
        assert state_path.read_text() == '{"invalid": true}'


def test_runtime_paths_are_portable(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    task(original)
    before = report.refresh(original)
    relocated = tmp_path / "relocated"
    shutil.copytree(original, relocated)
    assert report.refresh(relocated) == before


def test_native_harbor_viewer_routes_match_pinned_manifest():
    harbor = pytest.importorskip("harbor")
    static = Path(harbor.__file__).parent / "viewer/static/assets"
    manifests = list(static.glob("manifest-*.js"))
    if not manifests:
        pytest.skip("Native Harbor frontend assets unavailable")
    source = manifests[0].read_text()
    assert '"path":"jobs/:jobName"' in source
    assert '"path":"task-definitions/:taskName"' in source
    assert "tasks/:source/:agent/:modelProvider/:modelName/:taskName/trials/:trialName" in source


@pytest.mark.parametrize("complete", [True, False])
def test_removed_evidence_requires_incomplete_inventory_to_be_retained(tmp_path, complete):
    task_path = task(tmp_path)
    run_path = ".eval-author/jobs/checks/one"
    trial(tmp_path, run_path)
    job_path = job(tmp_path, total=1)
    trial(tmp_path, job_path + "/one")
    report.refresh(tmp_path, patch=rule(run_path))
    shutil.rmtree(tmp_path / run_path)
    shutil.rmtree(tmp_path / job_path)
    shutil.rmtree(tmp_path / task_path)
    write(tmp_path, ".eval-author/jobs/note-a.txt", "Inventory only")
    write(tmp_path, ".eval-author/jobs/note-b.txt", "Inventory only")
    data = report.refresh(tmp_path, max_entries=50000 if complete else 1)
    if complete:
        assert data["runs"] == []
        assert data["standalone_trials"] == []
        assert data["tasks"] == []
    else:
        assert data["runs"][0]["status"] == "unverified"
        assert data["standalone_trials"][0]["status"] == "unverified"
        assert data["tasks"][0]["status"] == "unverified"
    other = tmp_path / "other"
    other.mkdir()
    data = report.refresh(tmp_path, task_roots=["other"], jobs_roots=["other"])
    assert data["tasks"] == []
    assert data["runs"] == []
    assert data["standalone_trials"] == []


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("task", "ok"),
        ("job", "ok"),
        ("trial", "ok"),
        ("missing", "missing"),
        ("wrong-mode", "mode"),
        ("different-task", "different"),
        ("different-job", "different"),
        ("non-harbor", "response"),
        ("server-error", "response"),
        ("blocked", "unreachable"),
        ("timeout", "unreachable"),
    ],
)
def test_viewer_checks_mode_item_and_identity_before_navigation(scenario, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise the browser's viewer check")
    source = (SCRIPTS / "report-template.html").read_text()
    helper = source[source.index("function viewerFailure(") : source.index("let retryViewer=null;")]
    program = r"""
const assert=require('node:assert/strict');
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const scenario=input.scenario;
const jobs=['job','trial','different-job'].includes(scenario);
const mode=jobs?'jobs':'tasks';
const record=jobs?{id:'native-id',path:'.eval-author/jobs/example'}:{path:'.eval-author/tasks/example'};
const route=jobs?'jobs/example'+(scenario==='trial'?'/tasks/_/agent/_/model/task/trials/attempt':''):'task-definitions/example';
const calls=[];
const context={AbortController,clearTimeout,setTimeout:(fn,ms)=>{
  assert.equal(ms,3000);return setTimeout(fn,scenario==='timeout'?10:ms);
},fetch:async(url,options)=>{
  calls.push(url);assert.equal(options.credentials,'omit');assert.equal(options.mode,'cors');
  assert.equal(options.cache,'no-store');
  if(scenario==='blocked')throw new TypeError('Browser blocked request');
  if(scenario==='timeout')return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))));
  const config=url.endsWith('/api/config');
  const status=scenario==='server-error'?500:(!config&&scenario==='missing'?404:200);
  return {ok:status===200,status,json:async()=>{
    if(scenario==='non-harbor')throw new SyntaxError('HTML response');
    if(config)return {mode:scenario==='wrong-mode'?'jobs':mode,folder:'/project/.eval-author'};
    if(jobs)return {id:scenario==='different-job'?'other-id':'native-id'};
    return {name:'example',task_dir:scenario==='different-task'?'/another/tasks/example':'/project/.eval-author/tasks/example'};
  }};
}};
vm.createContext(context);vm.runInContext(input.helper,context);
(async()=>{
  let actual='ok';try{await context.checkViewer('http://localhost:8082',mode,route,record);}catch(error){actual=error.kind;}
  assert.equal(actual,input.expected);
  assert.equal(calls[0],'http://localhost:8082/api/config');
  if(scenario==='trial')assert.equal(calls[1],'http://localhost:8082/api/jobs/example/trials/attempt');
  if(['blocked','timeout','wrong-mode','non-harbor','server-error'].includes(scenario))assert.equal(calls.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", program],
        input=json.dumps({"helper": helper, "scenario": scenario, "expected": expected}),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def markdown_tokens(tmp_path):
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark").enable("table").parse((tmp_path / ".eval-author/README.md").read_text())


def test_markdown_mirrors_report_tabs_rows_and_links(tmp_path):
    path = task(
        tmp_path,
        readme="# Refund policy\n## Behavior under test\nRefund authorization.\n## Expected result\nOnly approved refunds are stored.\n",
    )
    other = "evals/other suite/tasks/with # fragment"
    shutil.copytree(tmp_path / path, tmp_path / other)
    run_path = job(tmp_path)
    trial(tmp_path, run_path + "/pass")
    trial(tmp_path, run_path + "/fail", reward=0)
    patch = rule(run_path)
    patch["tasks"] = {path: {"grading_kind": "deterministic", "grading_rule": "All state checks pass."}}
    data = report.refresh(tmp_path, patch=patch)
    tokens = markdown_tokens(tmp_path)
    headings = [(token.tag, tokens[i + 1].content) for i, token in enumerate(tokens) if token.type == "heading_open"]
    assert [(tag, text) for tag, text in headings if tag == "h2"] == [("h2", "Tasks"), ("h2", "Runs")]
    assert len([token for token in tokens if token.type == "table_open"]) == 3
    assert ("h3", "tasks") in headings
    assert ("h3", "evals/other suite/tasks") in headings
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "Settings" not in raw
    assert "http://127.0.0.1:8081/task-definitions/with%20%23%20fragment" in raw
    assert "[Task README](../evals/other%20suite/tasks/with%20%23%20fragment/README.md)" in raw
    assert "[Task README](tasks/refund/README.md)" in raw
    assert "http://127.0.0.1:8080/jobs/baseline" in raw
    # Compare rendered text to the HTML report's source records, not independently authored content.
    text = " ".join(child.content for token in tokens for child in token.children or [] if child.type == "text")
    for item in data["tasks"]:
        for field in ("title", "behavior", "expected_outcome"):
            assert item[field] in text
    assert "| Task | Status | Behavior | Expected outcome | Grader | Actions |" in raw
    assert "| Custom code<br>" in raw
    assert "All state checks pass." not in text
    assert "| Not documented<br>" in raw
    assert "1/2 passed" in text
    assert "agent · completed" in text
    assert all(part in text for part in data["runs"][0]["date"].split("T"))
    template = (SCRIPTS / "report-template.html").read_text()
    html_tabs = re.findall(r'<button role="tab"[^>]*>([^<]+)</button>', template)
    assert [text for tag, text in headings if tag == "h2"] == [name for name in html_tabs if name != "Settings"]
    assert headings[0] == ("h1", "NeMo Eval Author Report")


def test_markdown_refresh_repairs_missing_or_edited_mirror_without_touching_html(tmp_path):
    path = task(tmp_path, readme="## Behavior under test\nOriginal.\n## Expected result\nSaved.")
    plan = write(tmp_path, ".eval-author/first-eval.md", "Existing planning and run summary.")
    report.refresh(tmp_path)
    html_path = tmp_path / ".eval-author/report.html"
    md_path = tmp_path / ".eval-author/README.md"
    expected = md_path.read_bytes()
    stamp = md_path.stat().st_mtime_ns
    html_stamp = html_path.stat().st_mtime_ns
    report.refresh(tmp_path)
    assert md_path.stat().st_mtime_ns == stamp
    for edit in (None, "A hand-edited generated README"):
        if edit is None:
            md_path.unlink()
        else:
            md_path.write_text(edit)
        report.refresh(tmp_path)
        if edit is None:
            assert md_path.read_bytes() == expected
        else:
            assert md_path.read_bytes() == edit.encode() + b"\n\n" + expected
        assert html_path.stat().st_mtime_ns == html_stamp
    write(tmp_path, path + "/README.md", "## Behavior under test\nUpdated.\n## Expected result\nSaved.")
    report.refresh(tmp_path)
    assert "Updated" in md_path.read_text()
    assert "Original" not in md_path.read_text()
    assert plan.read_text() == "Existing planning and run summary."


def test_refresh_preserves_authored_readme_bytes_before_and_after_report(tmp_path):
    path = task(tmp_path, readme="## Behavior under test\nBefore.\n## Expected result\nSaved.")
    prefix = b"# My NeMo Gym suite\r\n\r\nRun: ng_run --input rows.jsonl\r\n\r\n"
    markdown = tmp_path / ".eval-author/README.md"
    markdown.write_bytes(prefix)
    report.refresh(tmp_path)
    assert markdown.read_bytes().startswith(prefix)
    suffix = b"\r\n## Local setup\r\nKeep these instructions.\r\n"
    markdown.write_bytes(markdown.read_bytes() + suffix)
    write(tmp_path, path + "/README.md", "## Behavior under test\nAfter.\n## Expected result\nSaved.")
    data = report.refresh(tmp_path)
    raw = markdown.read_bytes()
    assert raw.startswith(prefix) and raw.endswith(suffix)
    assert raw.count(report.README_BEGIN) == raw.count(report.README_END) == 1
    assert b"Before." not in raw and b"After." in raw
    stamp = markdown.stat().st_mtime_ns
    assert report.refresh(tmp_path) == data
    assert markdown.read_bytes() == raw
    assert markdown.stat().st_mtime_ns == stamp


@pytest.mark.parametrize(
    "content",
    [
        report.README_BEGIN + b"\nUnclosed section",
        report.README_END + b"\nUnexpected end",
        report.README_END + b"\n" + report.README_BEGIN,
        (report.README_BEGIN + b"\n" + report.README_END + b"\n") * 2,
    ],
)
def test_ambiguous_readme_markers_preserve_both_outputs(tmp_path, content):
    report.refresh(tmp_path)
    html = tmp_path / ".eval-author/report.html"
    markdown = tmp_path / ".eval-author/README.md"
    before = html.read_bytes()
    markdown.write_bytes(content)
    with pytest.raises(report.ReportError, match="markers"):
        report.refresh(tmp_path)
    assert markdown.read_bytes() == content
    assert html.read_bytes() == before


def test_readme_sections_preserve_paragraphs_and_lists_in_both_reports(tmp_path):
    behavior = "First paragraph.\n\n- Check one.\n- Check two."
    task(tmp_path, readme=f"## Behavior under test\n{behavior}\n\n## Expected result\nSaved.")
    data = report.refresh(tmp_path)
    assert data["tasks"][0]["behavior"] == behavior
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "First paragraph.<br><br>- Check one.<br>- Check two." in raw


def test_markdown_text_cannot_inject_tables_headings_links_or_html(tmp_path):
    dangerous = (
        "| row\n## forged\n<script>alert(1)</script> [bad](javascript:alert(1)) ![remote](https://example.test/pixel)"
    )
    path = task(tmp_path)
    report.refresh(
        tmp_path, patch={"tasks": {path: {"behavior": dangerous, "source_refs": [path + "/instruction.md"]}}}
    )
    tokens = markdown_tokens(tmp_path)
    assert len([token for token in tokens if token.type == "tr_open"]) == 2  # Header and one task.
    assert not any(
        token.type == "heading_open" and token.tag == "h2" and tokens[i + 1].content == "forged"
        for i, token in enumerate(tokens)
    )
    children = [child for token in tokens for child in token.children or []]
    assert not any(child.type == "image" for child in children)
    assert not any(child.type == "link_open" and child.attrGet("href").startswith("javascript:") for child in children)
    assert not any(child.type == "html_inline" and "script" in child.content for child in children)
    assert "<script>alert(1)</script>" in "".join(child.content for child in children if child.type == "text")


def test_markdown_preserves_warnings_missing_data_and_stale_status(tmp_path):
    path = task(tmp_path, readme="# Existing task")
    run_path = job(tmp_path, total=1)
    trial(tmp_path, run_path + "/one", reward=0.65)
    report.refresh(tmp_path)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "Not documented" in raw
    assert "reward: 0.65 total" in raw
    shutil.rmtree(tmp_path / path)
    shutil.rmtree(tmp_path / run_path)
    data = report.refresh(tmp_path, max_entries=1)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    for warning in data["warnings"]:
        assert report.markdown_text(warning) in raw
    assert "Inventory unverified" in raw
    assert "Saved Task README snapshot" in raw
    assert "Review saved artifacts" in raw
    assert "http://127.0.0.1" not in raw
    assert "Source artifacts were not found in this scan" in raw
    report.refresh(tmp_path, task_roots=["empty"], jobs_roots=["empty"])
    raw = (tmp_path / ".eval-author/README.md").read_text()
    assert "No tasks match." in raw
    assert "No recorded evaluation jobs." in raw


def test_markdown_runs_include_error_invalid_pending_and_dates(tmp_path):
    run_path = job(tmp_path, total=4)
    trial(tmp_path, run_path + "/error", error={"exception_type": "Timeout"})
    trial(tmp_path, run_path + "/invalid", trial_id="invalid")
    trial(tmp_path, run_path + "/pending", finished=False)
    metadata = rule(run_path)
    metadata["runs"][run_path]["invalid_attempts"] = {"invalid": "Invalid evidence"}
    report.refresh(tmp_path, patch=metadata)
    raw = (tmp_path / ".eval-author/README.md").read_text()
    for label in ("1 execution errors", "1 invalid", "2 pending or unscored", "incomplete", "Score unavailable"):
        assert label in raw
    assert "0/4 passed" not in raw


def test_markdown_render_failure_preserves_both_outputs(tmp_path, monkeypatch):
    path = task(tmp_path)
    report.refresh(tmp_path)
    paths = [tmp_path / ".eval-author" / name for name in ("report.html", "README.md")]
    before = [p.read_bytes() for p in paths]
    write(tmp_path, path + "/README.md", "## Behavior under test\nNew content")

    def fail(_):
        raise report.ReportError("Synthetic Markdown rendering failure")

    monkeypatch.setattr(report, "render_markdown", fail)
    with pytest.raises(report.ReportError, match="Synthetic"):
        report.refresh(tmp_path)
    assert [p.read_bytes() for p in paths] == before
    with report.report_lock(tmp_path / ".eval-author/.working/report/.report.lock"):
        pass


def test_readme_output_symlink_is_not_followed(tmp_path):
    report.refresh(tmp_path)
    output = tmp_path / ".eval-author/README.md"
    original = (tmp_path / ".eval-author/report.html").read_bytes()
    outside = write(tmp_path, "untouched.md", "Do not overwrite")
    output.unlink()
    try:
        output.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(report.ReportError, match="Symlink"):
        report.refresh(tmp_path)
    assert outside.read_text() == "Do not overwrite"
    assert (tmp_path / ".eval-author/report.html").read_bytes() == original


def test_interrupted_publication_is_repaired_on_next_refresh(tmp_path, monkeypatch):
    path = task(tmp_path, readme="## Behavior under test\nBefore")
    report.refresh(tmp_path)
    html_path = tmp_path / ".eval-author/report.html"
    md_path = tmp_path / ".eval-author/README.md"
    html_before = html_path.read_bytes()
    write(tmp_path, path + "/README.md", "## Behavior under test\nAfter")
    replace = report.os.replace

    def fail_html(source, destination):
        if destination == html_path:
            raise OSError("Synthetic interrupted HTML publication")
        replace(source, destination)

    with monkeypatch.context() as m:
        m.setattr(report.os, "replace", fail_html)
        with pytest.raises(OSError, match="Synthetic"):
            report.refresh(tmp_path)
    assert html_path.read_bytes() == html_before
    assert "After" in md_path.read_text()
    assert not list(html_path.parent.glob(".report-*"))
    report.refresh(tmp_path)
    data, _ = report.previous_report(html_path)
    assert data["tasks"][0]["behavior"] == "After"
    assert md_path.read_text() == report.render_markdown(data)


def test_concurrent_markdown_edit_is_not_overwritten(tmp_path, monkeypatch):
    task(tmp_path)
    report.refresh(tmp_path)
    html = tmp_path / ".eval-author/report.html"
    markdown = tmp_path / ".eval-author/README.md"
    before = html.read_bytes()
    render = report.render_markdown

    def concurrent_edit(data):
        markdown.write_text("Concurrent edit")
        return render(data)

    monkeypatch.setattr(report, "render_markdown", concurrent_edit)
    with pytest.raises(report.ReportError, match="changed during refresh"):
        report.refresh(tmp_path)
    assert markdown.read_text() == "Concurrent edit"
    assert html.read_bytes() == before


def test_legacy_html_imports_once_and_rebuilds_from_internal_state(tmp_path):
    path = task(tmp_path)
    original = report.refresh(tmp_path, patch={"tasks": {path: {"display_id": "reviewed-id"}}})
    internal = tmp_path / ".eval-author/.working/report/state.json"
    internal.unlink()  # A legacy installation has only its HTML projection.
    assert report.refresh(tmp_path) == original
    assert json.loads(internal.read_text())["metadata"]["tasks"][path]["display_id"] == "reviewed-id"
    imported = internal.with_name("legacy-import.json")
    assert json.loads(imported.read_text()) == original
    imported_bytes = imported.read_bytes()
    html = tmp_path / ".eval-author/report.html"
    html.write_text("An edited projection is not authoritative metadata.")
    assert report.refresh(tmp_path) == original
    html.unlink()
    assert report.refresh(tmp_path) == original
    assert report.previous_report(html)[0] == original
    assert imported.read_bytes() == imported_bytes


def test_internal_snapshot_retains_incomplete_inventory_without_html(tmp_path):
    path = task(tmp_path)
    report.refresh(tmp_path)
    (tmp_path / ".eval-author/report.html").unlink()
    shutil.rmtree(tmp_path / path)
    incomplete = report.refresh(tmp_path, max_entries=1)
    assert incomplete["tasks"][0]["path"] == path
    assert incomplete["tasks"][0]["status"] == "unverified"
    assert not incomplete["inventory"]["complete"]
    assert report.refresh(tmp_path)["tasks"] == []


def test_oversized_html_projection_is_repaired_from_internal_state(tmp_path, monkeypatch):
    path = task(tmp_path)
    original = report.refresh(tmp_path, patch={"tasks": {path: {"display_id": "retained"}}})
    html = tmp_path / ".eval-author/report.html"
    limit = html.stat().st_size + 100
    monkeypatch.setattr(report, "MAX_REPORT_BYTES", limit)
    html.write_bytes(b"x" * (limit + 1))
    assert report.refresh(tmp_path) == original
    assert report.previous_report(html)[0] == original


def test_internal_run_evidence_requires_explicit_root(tmp_path):
    path = ".eval-author/.working/trace-environments/case/private/run"
    trial(tmp_path, path)
    assert report.refresh(tmp_path, jobs_roots=["."])["standalone_trials"] == []
    explicit = report.refresh(tmp_path, jobs_roots=[path])
    assert explicit["standalone_trials"][0]["path"] == path
    incomplete = report.refresh(tmp_path, jobs_roots=["."], max_entries=1)
    assert incomplete["standalone_trials"] == []


@pytest.mark.parametrize("directory", ["archives", "task-drafts"])
def test_historical_run_evidence_remains_in_selected_scope(tmp_path, directory):
    path = f".eval-author/{directory}/case/run"
    trial(tmp_path, path)
    complete = report.refresh(tmp_path, jobs_roots=["."])
    assert [item["path"] for item in complete["standalone_trials"]] == [path]
    incomplete = report.refresh(tmp_path, jobs_roots=["."], max_entries=1)
    assert [item["path"] for item in incomplete["standalone_trials"]] == [path]
    assert incomplete["standalone_trials"][0]["status"] == "unverified"


def test_incomplete_scan_does_not_restore_legacy_copies_into_default_scope(tmp_path):
    original = task(tmp_path, "legacy")
    path = ".eval-author/task-drafts/legacy"
    (tmp_path / path).parent.mkdir(parents=True)
    shutil.move(tmp_path / original, tmp_path / path)
    retained = report.refresh(tmp_path, task_roots=[".eval-author/task-drafts"])
    assert retained["tasks"][0]["path"] == path
    restored = report.refresh(tmp_path, task_roots=["."], max_entries=1)
    assert not restored["inventory"]["complete"]
    assert restored["tasks"] == []


def test_legacy_import_retains_unfinished_task_and_directs_explicit_resume(tmp_path):
    original = task(tmp_path, "unfinished")
    path = ".eval-author/task-drafts/unfinished"
    (tmp_path / path).parent.mkdir(parents=True)
    shutil.move(tmp_path / original, tmp_path / path)
    retained = report.refresh(tmp_path, task_roots=[".eval-author/task-drafts"])
    state = tmp_path / ".eval-author/.working/report/state.json"
    state.unlink()
    refreshed = report.refresh(tmp_path, task_roots=["."])
    assert refreshed["tasks"] == []
    assert any("--task-root .eval-author/task-drafts" in warning for warning in refreshed["warnings"])
    assert json.loads(state.with_name("legacy-import.json").read_text()) == retained
    assert report.refresh(tmp_path, task_roots=[path])["tasks"][0]["path"] == path
