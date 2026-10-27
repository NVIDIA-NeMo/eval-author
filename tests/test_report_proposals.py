# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Saved creation designs appear before artifacts without supplying readiness evidence."""

import json
import shutil

import pytest
import test_first_eval_state as creation
from test_html_report import report, task, write

modules = creation.modules


@pytest.fixture
def design():
    return [
        {
            "id": "refund",
            "example_ids": ["refund-1"],
            "behavior": "Require approval before saving a refund.",
            "expected_result": "Only an approved refund is saved.",
            "grading": "Check saved refunds against the approval record.",
        },
        {
            "id": "policy",
            "example_ids": ["policy-1"],
            "behavior": "Refuse refunds outside the policy window.",
            "expected_result": "No unauthorized refund is saved.",
            "grading": "Check that no refund was saved.",
        },
    ]


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("approved", [False, True])
def test_saved_design_is_reported_before_authoring(modules, tmp_path, design, provider, approved):
    value = creation.start(modules, tmp_path, provider, design)
    if approved:
        creation.approve(modules, tmp_path, value)
    before = modules[0].state_path(tmp_path).read_bytes()

    data = report.refresh(tmp_path)

    rows = {row["plan_task_id"]: row for row in data["tasks"]}
    assert set(rows) == {"refund", "policy"}
    for planned in design:
        row = rows[planned["id"]]
        assert row["provider"] == provider
        assert row["display_id"] == planned["id"]
        assert row["status"] == row["readiness"] == "proposed"
        assert row["readiness_reason"] == (
            "Design approved; task has not been authored."
            if approved
            else "Awaiting design approval; task has not been authored."
        )
        assert row["behavior"] == planned["behavior"]
        assert row["expected_outcome"] == planned["expected_result"]
        assert row["proposed_grading"] == planned["grading"]
        assert row["path"] is row["readme_path"] is row["readme_html"] is None
    assert data["plan_counts"] == {"proposed": 2, "authored": 0, "validated": 0, "executed": 0}
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert "Proposed" in markdown
    assert design[0]["behavior"] in markdown
    assert design[0]["grading"] in markdown
    assert "[Task README]" not in markdown
    assert "[View task]" not in markdown
    assert "[NeMo Gym source]" not in markdown
    assert "[Review saved artifacts]" not in markdown
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert report.refresh(tmp_path) == data


@pytest.mark.parametrize("provider", ["harbor", "gym"])
@pytest.mark.parametrize("readiness", ["draft", "accepted", "unknown"])
def test_recorded_artifact_replaces_proposal_and_readme_supplies_its_content(
    modules, tmp_path, design, provider, readiness
):
    value = creation.start(modules, tmp_path, provider, design)
    report.refresh(tmp_path)
    creation.approve(modules, tmp_path, value)
    root = ".eval-author/tasks/native-refund"
    if provider == "harbor":
        task(tmp_path, name="native-refund")
    else:
        write(tmp_path, root + "/environments/native-case/manifest.yaml", "name: native-case\n")
    source = root if provider == "harbor" else root + "/environments/native-case/manifest.yaml"
    write(
        tmp_path,
        root + "/README.md",
        "# Authored refund\n## Behavior under test\nRead the approval ledger.\n"
        "## Expected result\nThe ledger contains the authorized refund.\n"
        "## Grading\nType: Custom code\nCheck the saved refund against the ledger.\n",
    )
    value["tasks"]["refund"] = {
        "path": root,
        "sha256": modules[0].task_digest(tmp_path, root),
        "review": None,
        "environment": None,
        "validation": None,
        "limitations": [],
    }
    creation.save(modules, tmp_path, value)
    if readiness != "unknown":
        receipt = creation.evidence(tmp_path, ".eval-author/.working/task-review.json")
        modules[0].record_task(
            tmp_path, task=root, status=readiness, source="Task lifecycle review", evidence=[receipt["path"]]
        )
    before = modules[0].state_path(tmp_path).read_bytes()

    data = report.refresh(tmp_path)

    assert len(data["tasks"]) == 2
    rows = {row["plan_task_id"]: row for row in data["tasks"]}
    authored = rows["refund"]
    assert authored["path"] == source
    assert authored["display_id"] == ("native-refund" if provider == "harbor" else "native-case")
    assert authored["title"] == "Authored refund"
    assert authored["behavior"] == "Read the approval ledger."
    assert authored["expected_outcome"] == "The ledger contains the authorized refund."
    assert authored["grading_kind"] == "Custom code"
    assert authored["grading_summary"] == "Check the saved refund against the ledger."
    assert authored["readiness"] == readiness
    assert authored["status"] == "found"
    assert authored["readme_path"] == root + "/README.md"
    assert rows["policy"]["status"] == "proposed"
    assert data["plan_counts"] == {"proposed": 1, "authored": 1, "validated": 0, "executed": 0}
    assert modules[0].state_path(tmp_path).read_bytes() == before


def test_inventory_name_alone_does_not_replace_a_proposal(modules, tmp_path):
    creation.start(modules, tmp_path)
    existing = task(tmp_path)

    data = report.refresh(tmp_path)

    assert len(data["tasks"]) == 3
    assert {row["plan_task_id"] for row in data["tasks"] if row["status"] == "proposed"} == {"refund", "policy"}
    assert next(row for row in data["tasks"] if row["path"] == existing)["status"] == "found"
    assert data["plan_counts"] == {"proposed": 2, "authored": 0, "validated": 0, "executed": 0}


def test_explicit_task_root_restricts_inventory_but_preserves_full_plan_counts(modules, tmp_path):
    value = creation.start(modules, tmp_path)
    creation.approve(modules, tmp_path, value)
    task(tmp_path)
    creation.author(modules, tmp_path, value, "refund")
    selected = task(tmp_path, name="unplanned")

    data = report.refresh(tmp_path, task_roots=[selected])

    assert {row["path"] for row in data["tasks"]} == {selected, None}
    assert {row.get("plan_task_id") for row in data["tasks"]} == {"policy", None}
    assert data["plan_counts"] == {"proposed": 1, "authored": 1, "validated": 0, "executed": 0}


def test_multiple_gym_candidates_bound_to_one_plan_task_have_one_row(modules, tmp_path):
    value = creation.start(modules, tmp_path, provider="gym")
    creation.approve(modules, tmp_path, value)
    root = ".eval-author/tasks/refund"
    write(tmp_path, root + "/tasks.jsonl", "{}\n")
    write(tmp_path, root + "/instruction.template.md", "Refund {amount}")
    write(tmp_path, root + "/environments/refund/manifest.yaml", "name: refund\n")
    creation.author(modules, tmp_path, value, "refund")

    data = report.refresh(tmp_path)

    assert len(data["tasks"]) == 2
    authored = [row for row in data["tasks"] if row.get("plan_task_id") == "refund"]
    assert len(authored) == 1
    assert authored[0]["path"] == root
    assert authored[0]["status"] == "found"
    assert data["plan_counts"] == {"proposed": 1, "authored": 1, "validated": 0, "executed": 0}


def test_removed_plan_task_loses_its_binding_on_retained_inventory(modules, tmp_path):
    value = creation.start(modules, tmp_path)
    creation.approve(modules, tmp_path, value)
    root = task(tmp_path)
    creation.author(modules, tmp_path, value, "refund")
    report.refresh(tmp_path, patch={"tasks": {root: {"display_id": "Native-42"}}})
    value["plan"]["tasks"] = value["plan"]["tasks"][1:]
    value["first_task"] = "policy"
    creation.save(modules, tmp_path, value)

    data = report.refresh(tmp_path, max_entries=1)

    retained = next(row for row in data["tasks"] if row["path"] == root)
    assert retained["status"] == "unverified"
    assert retained["display_id"] == "Native-42"
    assert "plan_task_id" not in retained
    assert data["plan_counts"] == {"proposed": 1, "authored": 0, "validated": 0, "executed": 0}


@pytest.mark.parametrize("remove", ["plan-task", "state"])
def test_missing_authored_placeholder_is_rebuilt_only_from_current_plan(modules, tmp_path, remove):
    value = creation.start(modules, tmp_path)
    creation.approve(modules, tmp_path, value)
    root = task(tmp_path)
    creation.author(modules, tmp_path, value, "refund")
    shutil.rmtree(tmp_path / root)
    initial = report.refresh(tmp_path)
    assert next(row for row in initial["tasks"] if row["plan_task_id"] == "refund")["status"] == "unverified"
    if remove == "plan-task":
        value["plan"]["tasks"] = value["plan"]["tasks"][1:]
        value["first_task"] = "policy"
        creation.save(modules, tmp_path, value)
    else:
        modules[0].state_path(tmp_path).unlink()

    data = report.refresh(tmp_path, max_entries=1)

    assert all(row["path"] != root for row in data["tasks"])
    assert {row.get("plan_task_id") for row in data["tasks"]} == ({"policy"} if remove == "plan-task" else set())


@pytest.mark.parametrize("initial_refresh", [False, True])
def test_missing_recorded_artifacts_remain_authored_and_unverified(modules, tmp_path, initial_refresh):
    value = creation.start(modules, tmp_path)
    creation.approve(modules, tmp_path, value)
    root = task(tmp_path)
    creation.author(modules, tmp_path, value, "refund")
    modules[0].record_task(tmp_path, task=root, status="draft", source="Awaiting native validation")
    if initial_refresh:
        report.refresh(tmp_path)
    shutil.rmtree(tmp_path / root)

    data = report.refresh(tmp_path)

    authored = next(row for row in data["tasks"] if row["plan_task_id"] == "refund")
    assert authored["path"] == root
    assert authored["status"] == "unverified"
    assert authored["readiness"] != "proposed"
    assert data["plan_counts"] == {"proposed": 1, "authored": 1, "validated": 0, "executed": 0}


def test_revised_plan_replaces_old_proposals_even_with_incomplete_inventory(modules, tmp_path, design):
    value = creation.start(modules, tmp_path, examples=design)
    report.refresh(tmp_path)
    value["plan"]["tasks"][0]["behavior"] = "Require two approval records."
    value["plan"]["tasks"][1] = {"id": "shipping", "example_ids": ["shipping-1"], "behavior": "Explain shipping."}
    creation.save(modules, tmp_path, value)
    before = modules[0].state_path(tmp_path).read_bytes()

    data = report.refresh(tmp_path, max_entries=1)

    assert not data["inventory"]["complete"]
    rows = {row["plan_task_id"]: row for row in data["tasks"]}
    assert set(rows) == {"refund", "shipping"}
    assert rows["refund"]["behavior"] == "Require two approval records."
    assert all(row["status"] == "proposed" for row in rows.values())
    assert data["plan_counts"] == {"proposed": 2, "authored": 0, "validated": 0, "executed": 0}
    assert "Refuse refunds outside the policy window." not in (tmp_path / ".eval-author/README.md").read_text()
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert report.refresh(tmp_path, max_entries=1) == data


@pytest.mark.parametrize("state_content", [None, "{", "{}"])
def test_absent_or_malformed_state_never_retains_old_proposals(modules, tmp_path, state_content):
    creation.start(modules, tmp_path)
    report.refresh(tmp_path)
    state_path = modules[0].state_path(tmp_path)
    if state_content is None:
        state_path.unlink()
    else:
        state_path.write_text(state_content)

    data = report.refresh(tmp_path, max_entries=1)

    assert data["tasks"] == []
    assert "plan_counts" not in data
    assert state_path.read_text() == state_content if state_content is not None else not state_path.exists()


def test_saved_design_resume_is_only_a_read_only_report_projection(modules, tmp_path):
    value = creation.start(modules, tmp_path)
    creation.saved_design_question(modules, tmp_path, value)
    before = modules[0].state_path(tmp_path).read_bytes()

    first = report.refresh(tmp_path)
    second = report.refresh(tmp_path)

    assert first == second
    assert all(row["readiness"] == "proposed" for row in first["tasks"])
    assert modules[0].state_path(tmp_path).read_bytes() == before
    assert creation.facts(modules, tmp_path)["saved_design_review_needed"]


def test_plan_progress_counts_require_current_validation_and_native_evidence(modules, tmp_path):
    for task_id in ("refund", "policy"):
        task(tmp_path, name=task_id)
    value = creation.ready(modules, tmp_path)
    creation.select(modules, tmp_path, value)
    creation.run(modules, tmp_path, value)
    creation.review_run(modules, tmp_path, value)
    task(tmp_path, name="unplanned")
    # The state fixture supplies a plain-text report receipt, not an importable report.
    (tmp_path / ".eval-author/report.html").unlink()

    data = report.refresh(tmp_path)

    assert len(data["tasks"]) == 3
    assert data["plan_counts"] == {"proposed": 0, "authored": 2, "validated": 2, "executed": 2}
    evidence_path = value["tasks"]["refund"]["validation"]["evidence"][0]["path"]
    (tmp_path / evidence_path).write_text("Changed validation evidence")
    attempt_path = value["runs"][0]["attempts"][0]["evidence"][0]["path"]
    (tmp_path / attempt_path).write_text("Changed native attempt evidence")
    before = modules[0].state_path(tmp_path).read_bytes()

    refreshed = report.refresh(tmp_path)

    assert refreshed["plan_counts"] == {"proposed": 0, "authored": 2, "validated": 1, "executed": 1}
    assert modules[0].state_path(tmp_path).read_bytes() == before


def test_saved_plan_summaries_are_literal_in_reports(modules, tmp_path, design):
    source = '<script>alert("saved design")</script> | [approve](https://example.test)\n# Injected heading'
    for field in ("behavior", "expected_result", "grading"):
        design[0][field] = source
    creation.start(modules, tmp_path, examples=design)

    data = report.refresh(tmp_path)

    row = next(row for row in data["tasks"] if row["plan_task_id"] == "refund")
    assert row["behavior"] == row["expected_outcome"] == row["proposed_grading"] == source
    markdown = (tmp_path / ".eval-author/README.md").read_text()
    assert report.markdown_text(source) in markdown
    assert "<script>alert" not in markdown
    html = (tmp_path / ".eval-author/report.html").read_text()
    assert "<script>alert" not in html
    assert report.previous_report(tmp_path / ".eval-author/report.html")[0]["tasks"] == data["tasks"]


@pytest.mark.parametrize("invalid", [None, {}, ["unexpected"], 42, "x" * 2001])
def test_optional_design_text_cannot_break_report_schema(modules, tmp_path, design, invalid):
    for field in ("title", "behavior", "expected_result", "grading"):
        design[0][field] = invalid
    creation.start(modules, tmp_path, examples=design)

    data = report.refresh(tmp_path)

    row = next(row for row in data["tasks"] if row["plan_task_id"] == "refund")
    for field in ("title", "behavior", "expected_outcome", "proposed_grading"):
        assert row[field] is None or isinstance(row[field], str) and len(row[field]) <= 2000
    assert row["status"] == "proposed"
    assert json.loads(json.dumps(data)) == data
