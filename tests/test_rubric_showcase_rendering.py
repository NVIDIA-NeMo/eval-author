# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import html

import pytest
from test_rubric_showcase import showcase_module


def render_current(ident: str, rows: list[dict]) -> str:
    module = showcase_module()
    current, _ = module.current_modules()
    definitions = current.canonical_definitions()
    label_ids = (
        "provenance.task-artifact-access",
        "provenance.grading-path",
        "provenance.grading-dependency-access",
        "task-contract.clear-contract",
        "task-contract.discoverable-requirements",
        "task-contract.scoring-authority",
    )
    arm: dict = dict.fromkeys(
        (
            "score",
            "evidence_strength",
            "false_accepts",
            "invalid_denominator",
            "false_rejects",
            "valid_denominator",
            "health_errors",
            "alternatives_accepted",
            "alternatives_total",
        ),
        0,
    )
    arm["remaining_gaps"] = []
    summary = {
        "criterion_inventory": [{"id": ident, "title": definitions[ident]["title"], "level": rows[0]["level"]}],
        "historical_criterion_inventory": [{"id": key, "title": definitions[key]["title"]} for key in label_ids],
        "current_assessments": [
            {"id": f"case-{index}", "evidence": f"case-{index}/report.json", "report": {"items": {ident: row}}}
            for index, row in enumerate(rows)
        ],
        "current_numeric_criteria": [ident],
        "pending_owner_requests": {"exact_request_count": 0},
        "scorer": {
            "baseline": arm,
            "reviewed": arm,
            "matched_controls": 0,
            "attempted_controls": 0,
            "replay_equal": True,
        },
        "integrity": {"anchors": [], "fresh_replay_equal": True},
        "task_contract": {"ethos": [], "assessments": [], "matched": 0, "cases": 0},
        "identity": {"counts": {"evidence_cases": 0}, "assessments": [], "evidence_cases": []},
        "provenance": {"matched": 0, "cases": 0},
        "receipt_authenticity": {},
        "production_gaps": [],
        "criterion_demonstration": {
            "criterion_count": 0,
            "vector_count_per_root": 0,
            "results": {
                variant: dict.fromkeys(label_ids) for variant in ("score-0", "score-1", "score-2", "score-3", "missing")
            },
        },
        "determinism": {"fresh_run_equal": True},
    }
    return (
        module.render(summary, {"cases": []})
        .split("<h2>Current canonical criteria</h2>")[1]
        .split("<h2>Supporting local evidence</h2>")[0]
    )


def test_numeric_groups_preserve_simulation_scope_and_limitations():
    row = {
        "level": "task",
        "score": 1,
        "evidence_strength": "E2",
        "status": "ASSESSED",
        "assessment_scope": "synthetic-local-refund-controls",
        "limitations": ["Local simulation, not native/model evidence."],
    }
    page = render_current("ENV-03-S01", [row, {**row, "assessment_scope": "other<scope>"}])
    introduction, table = page.split('<div class="scroll">', 1)
    assert "simulation" in introduction
    scores = table.split("<td>")[3].split("</td>")[0]
    assert "synthetic-local-refund-controls" in scores
    assert "other&lt;scope&gt;" in scores
    assert "Local simulation, not native/model evidence." in scores
    assert scores.count("(1 assessments)") == 2
    assert "other<scope>" not in page


@pytest.mark.parametrize("ident, level", [("ENV-03-S01", "task"), ("AUDIT-05-S03", "suite")])
def test_details_preserve_owning_execution_and_operational_gaps(ident: str, level: str):
    fault = "Executed operational fault; no healthy control result. Not a successful defense or numeric zero."
    row = {
        "level": level,
        "score": None,
        "status": "NOT_APPLICABLE" if level == "suite" else "NOT_ASSESSED",
        "execution_state": "NOT_APPLICABLE" if level == "suite" else "ERROR",
        "remaining_gaps": ["Supply the suite ledger." if level == "suite" else fault, "Missing <receipt>."],
        "findings": ["Unrelated attention finding."],
    }
    if level == "suite":
        row["suite_evidence"] = {"score": None, "remaining_gaps": [fault]}
    neighbor = {**row, "execution_state": "NOT_RUN", "remaining_gaps": ["Not executed."]}
    if level == "suite":
        neighbor["suite_evidence"] = {"score": None, "remaining_gaps": ["Suite control not executed."]}
    page = render_current(ident, [row, neighbor])
    details = page.split("<details>")[1].split("</details>")[0]
    groups = details.split("</ul></li>")
    owning = next(group for group in groups if ">case-0</a>" in group)
    assert f"Execution: {row['execution_state']}" in owning
    assert fault in owning
    assert "Unrelated attention finding." in owning
    for gap in row["remaining_gaps"]:
        assert html.escape(gap) in owning
    assert ">case-1</a>" not in owning
    assert "Missing <receipt>." not in page
