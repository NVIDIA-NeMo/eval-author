# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/rubric_table.py"


def table_cli(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_table_assesses_materialized_refund_without_manual_selection(tmp_path):
    prepared = subprocess.run(
        [sys.executable, str(SCRIPT.parent / "example_fixtures.py"), "materialize", "--output", str(tmp_path / "case")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    result = table_cli(tmp_path / "case/assessment", "--task", "task/task.toml", "--output", tmp_path / "review")
    assert result.returncode == 1, result.stdout + result.stderr
    assert table_cli(tmp_path / "case/assessment", "--verify-existing", tmp_path / "review").returncode == 1


def test_complete_task_selector_reports_missing_contract(tmp_path):
    prepared = subprocess.run(
        [
            sys.executable,
            str(SCRIPT.parent / "example_fixtures.py"),
            "materialize",
            "--mutation",
            "missing-contract",
            "--output",
            str(tmp_path / "case"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    root = tmp_path / "case/assessment"
    output = tmp_path / "review"
    result = table_cli(root, "--task", "task/task.toml", "--output", output)
    assert result.returncode == 1, result.stdout
    saved = json.loads((output / "assessment.json").read_text())
    assert saved["selection"]["example_assessment"]["reference_scope"] == "task_directory"
    assessment = saved["report"]["measurements"]["example_assessment"]
    observations = assessment["task_artifacts"]["observations"]
    contract = next(row for row in observations if row["path"] == "task/environment/contract.json")
    assert contract["access"] == "missing"
    assert "task/environment/contract.json" in result.stdout
    assert table_cli(root, "--verify-existing", output).returncode == 1


def test_table_accepts_task_without_manual_selection(tmp_path):
    from test_rubric_example_assessment import package

    package(tmp_path)
    result = table_cli(tmp_path, "--task", "task.toml")
    assert result.returncode == 1, result.stderr
    assert "Task artifact access" in result.stdout
    assert "Coverage and diversity are outside the populated rubric." in result.stdout
    assert len([line for line in result.stdout.splitlines() if line.startswith("| ")]) == 19
    assert "| Expected | Observed rubric score | Task credit |" in result.stdout
    assert "Not supplied" in result.stdout
    assert "not run" in result.stdout.lower() or "blocked" in result.stdout.lower()
    assert not (tmp_path / "selection.json").exists()
    assert result.stderr == ""


@pytest.mark.parametrize("unsafe", ["symlink", "overlap", "traversal", "existing"])
def test_table_rejects_unsafe_sources_and_output(tmp_path, unsafe):
    from test_rubric_example_assessment import package

    task = tmp_path / "task"
    task.mkdir()
    package(task)
    output = tmp_path / "review"
    selected = "task/task.toml"
    if unsafe == "symlink":
        (task / "link").symlink_to(task / "instruction.md")
    elif unsafe == "overlap":
        output = task / "review"
    elif unsafe == "traversal":
        selected = "task/../task/task.toml"
    else:
        output.mkdir()
        (output / "keep.txt").write_text("Keep this file.")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = table_cli(tmp_path, "--task", selected, "--output", output)
    assert result.returncode == 2
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert result.stderr == ""


def test_table_retains_report_and_rejects_tampering(tmp_path):
    from test_rubric_example_assessment import package

    task = tmp_path / "task"
    task.mkdir()
    package(task)
    output = tmp_path / "review"
    result = table_cli(tmp_path, "--task", "task/task.toml", "--output", output)
    assert result.returncode == 1, result.stderr
    saved = json.loads((output / "assessment.json").read_text())
    assert saved["report"]["items"]["provenance.task-artifact-access"]["score"] == 2
    assert (output / "results.md").read_text() == result.stdout
    verified = table_cli(tmp_path, "--verify-existing", output)
    assert verified.returncode == 1, verified.stdout
    saved["report"]["items"].pop("provenance.grading-path")
    (output / "assessment.json").write_text(json.dumps(saved))
    assert table_cli(tmp_path, "--verify-existing", output).returncode == 2


def test_table_cli_assesses_sources_and_reports_missing_evidence(tmp_path):
    assert SCRIPT.is_file(), "The table entry point is missing"
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({}))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), "--selection", str(selection)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "| Expected | Observed rubric score | Task credit |" in result.stdout
    assert "Unassessed" in result.stdout
    assert "Task artifact access" in result.stdout
    assert "Aggregate" not in result.stdout
    assert result.stderr == ""


def test_table_escapes_source_prose_and_preserves_input():
    assert SCRIPT.is_file()
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {
        "score": 0,
        "source": {"skill_id": "audit"},
        "evidence_strength": "E2",
        "check_results": [{"outcome": "FAIL"}, {"outcome": "NOT_RUN"}],
        "remaining_gaps": ["<script>bad</script> | [link](https://example.com)\nextra"],
        "next_action": "Inspect the failed control.",
    }
    report = {"items": {"provenance.comparison-fairness": row}}
    before = json.dumps(report, sort_keys=True)
    table = module.render(report)
    assert "<script>" not in table
    assert "[link]" not in table
    assert "&#124;" in table
    assert "FAIL: 1, NOT_RUN: 1" in table
    assert "| 0 | Not measured here |" in table
    assert "## Evidence details" in table
    main_table = table.split("## Evidence details")[0]
    assert "&#91;link&#93;" not in main_table
    assert "[Evidence 1](#evidence-1)" in main_table
    assert "Confirmed defect" in main_table
    assert json.dumps(report, sort_keys=True) == before


@pytest.mark.parametrize("case", ["duplicate", "unknown", "wrong-source", "empty"])
def test_table_rejects_invalid_row_identity(case):
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {"score": None, "check_results": [{"check_id": "AC-01:Probe", "outcome": "NOT_RUN"}]}
    report = {"items": {"AC-01": row}}
    if case == "duplicate":
        row["check_results"] *= 2
    elif case == "unknown":
        report["items"] = {"not-a-criterion": row}
    elif case == "wrong-source":
        row["item_id"] = "AC-02"
    else:
        report["items"] = {}
    with pytest.raises(ValueError):
        module.render(report)


def test_table_keeps_not_applicable_separate_from_unassessed():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {
        "score": None,
        "status": "NOT_APPLICABLE",
        "applicability_reason": "The contract requires no external service.",
    }
    output = module.render({"items": {"provenance.external-state": row}})
    assert "| Not applicable |" in output
    row.pop("applicability_reason")
    with pytest.raises(ValueError):
        module.render({"items": {"provenance.external-state": row}})


@pytest.mark.parametrize("outcomes", [[], ["NOT_ASSESSED"], ["UNKNOWN"]])
def test_table_never_claims_unobserved_checks_ran(outcomes):
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {"items": {"AC-01": {"score": None, "check_results": [{"outcome": outcome} for outcome in outcomes]}}}
    output = module.render(report)
    assert "Checks ran" not in output
    assert "No completed check evidence" in output


@pytest.mark.parametrize("mutation", ["row", "check", "source"])
def test_table_does_not_fill_missing_emitted_rows(tmp_path, monkeypatch, mutation):
    from test_rubric_example_assessment import package

    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = package(tmp_path)
    report = module.measure(tmp_path, selected)
    if mutation == "row":
        report["items"].pop("AC-01")
    elif mutation == "check":
        report["items"]["AC-01"]["check_results"].pop()
    else:
        report["items"]["provenance.task-artifact-access"]["source"]["id"] = "changed"
    monkeypatch.setattr(module, "measure", lambda *args: report)
    with pytest.raises(ValueError, match="inventory"):
        module.assess_table(tmp_path, selected, "task.toml")


def test_all_populated_request_has_one_skill_path():
    text = " ".join((SCRIPT.parents[1] / "SKILL.md").read_text().split())
    assert '"all populated areas" selects every populated criterion' in text
    assert "Do not ask another area-selection question" in text
    assert "--task task/task.toml" in text
    assert "--verify-existing" in text
    assert "Keep the rubric definitions and assessment prompt fixed" in text
    assert "mutation recipes and expected findings outside the assessment context" in text


@pytest.mark.parametrize("score", [True, False, -1, 4, 1.0, "3"])
def test_table_rejects_invalid_score_types(score):
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="score"):
        module.render({"items": {"AC-01": {"score": score}}})


@pytest.mark.parametrize(
    "change", ["score", "count", "observation", "source", "missing", "extra", "markdown", "expectation", "definition"]
)
def test_retained_table_rejects_changes(tmp_path, change):
    from test_rubric_example_assessment import package

    task = tmp_path / "task"
    task.mkdir()
    package(task)
    output = tmp_path / "review"
    assert table_cli(tmp_path, "--task", "task/task.toml", "--output", output).returncode == 1
    record = output / "assessment.json"
    saved = json.loads(record.read_text())
    row = saved["report"]["items"]["provenance.task-artifact-access"]
    if change == "score":
        row["score"] = 3
    elif change == "count":
        row["check_results"].pop()
    elif change == "observation":
        row["check_results"][0]["outcome"] = "FAIL"
    elif change == "source":
        (task / "instruction.md").write_text("Changed instruction")
    elif change == "missing":
        (task / "tests/helper.py").unlink()
    elif change == "extra":
        (task / "new.txt").write_text("Additional task input")
    elif change == "markdown":
        (output / "results.md").write_text("All passed")
    elif change == "expectation":
        saved["expected"] = {"AC-01": 3}
    else:
        saved["definitions_sha256"] = "0" * 64
    record.write_text(json.dumps(saved))
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    result = table_cli(tmp_path, "--verify-existing", output)
    assert result.returncode == 2, result.stdout
    assert "Traceback" not in result.stderr
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}


def test_table_does_not_accept_unknown_check_outcomes():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import pytest

    with pytest.raises(ValueError, match="check outcome"):
        module.render({"items": {"AC-01": {"score": 3, "check_results": [{"outcome": "MADE_UP"}]}}})


def test_accepted_source_review_has_no_pending_owner_action():
    table = load_table()
    row = {
        "score": 3,
        "source_review_acceptance": {"status": "accepted", "local_operator_decision_recorded": True},
        "source_review_pre_acceptance": {"remaining_gaps": ["Pending owner qualification"]},
        "check_results": [
            {
                "id": "review",
                "method": "human_review",
                "outcome": "PASS",
                "actual_method": "qualified_agent_review",
                "next_action": "Ask owner again",
            }
        ],
        "remaining_gaps": [],
        "next_action": "Ask owner again",
    }
    report = {"items": {"provenance.task-artifact-access": row}, "measurements": {}}
    text = table.render(report)
    headline, details = text.split("## Evidence details")
    assert "Ask owner again" not in headline
    assert "Local operator" in headline
    assert "Historical pre-acceptance record" in details
    assert "Pending owner qualification" in details
    assert row["check_results"][0]["next_action"] == "Ask owner again"


def test_accepted_source_review_preserves_other_pending_review():
    table = load_table()
    row = {
        "source_review_acceptance": {"status": "accepted", "local_operator_decision_recorded": True},
        "check_results": [
            {"id": "accepted", "method": "human_review", "outcome": "PASS", "actual_method": "qualified_agent_review"},
            {
                "id": "disputed",
                "method": "human_review",
                "outcome": "NOT_RUN",
                "next_action": "Resolve separate dispute",
            },
        ],
    }
    assert "Resolve separate dispute" in table.human_action(row)
    assert "Local operator" in table.human_action(row)


def test_compact_report_retains_evidence_within_reader_limit(tmp_path, monkeypatch):
    table = load_table()
    selected = tmp_path / "selection.json"
    selected.write_text("{}")
    report = {
        "items": {"AC-01": {"score": None, "check_results": []}},
        "measurements": {
            "example_assessment": {
                "status": "unknown",
                "configuration": {"fields": [{"path": "source", "value": n} for n in range(18000)]},
            }
        },
    }
    monkeypatch.setattr(table, "assess_table", lambda *args: report)
    destination = tmp_path / "report"
    assert table.main(["--root", str(tmp_path), "--selection", str(selected), "--output", str(destination)]) == 1
    saved = json.loads(table.read_private(destination / "assessment.json"))
    assert saved["report"] == report
    assert ": " not in (destination / "assessment.json").read_text()
    assert (
        "assessment.json#/report/measurements/example_assessment/configuration"
        in (destination / "results.md").read_text()
    )
    assert table.main(["--root", str(tmp_path), "--verify-existing", str(destination)]) == 1


def load_table():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("rubric_table", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_table_names_incomplete_check_and_keeps_report_level_unknowns():
    module = load_table()
    report = {
        "items": {
            "AC-01": {
                "score": None,
                "check_results": [
                    {
                        "outcome": "PARTIAL",
                        "action": "Inventory protected files and their readers.",
                        "remaining_gap": "Selected evidence does not establish the complete criterion.",
                    }
                ],
            }
        },
        "measurements": {"review": {"status": "unknown", "binding_status": "unavailable"}},
    }
    output = module.render(report)
    assert "PARTIAL: Inventory protected files and their readers." in output.split("## Evidence details")[0]
    assert "binding_status" in output and "unavailable" in output


def test_table_surfaces_unauthenticated_contradictions_and_required_actions():
    module = load_table()
    row = {
        "score": None,
        "subject_id": "source:" + "a" * 64,
        "check_results": [
            {
                "method": "human_review",
                "outcome": "NOT_RUN",
                "action": "Resolve the refund destination with the task owner.",
                "remaining_gap": "The destination has no authority citation.",
            }
        ],
        "recorded_source_reviews": [
            {
                "reviewer": {"kind": "human", "identity": "Synthetic reviewer"},
                "authority_authenticated": False,
                "independence_established": False,
                "decision": {"status": "contradicted", "rationale": "Recorded destination conflict."},
            }
        ],
        "findings": [{"message": "Destination differs from the cited contract."}],
        "approval_refs": ["approval-only-a-reference"],
        "method_qualification_refs": ["qualification-only-a-reference"],
    }
    report = {"items": {"task-contract.clear-contract": row}}
    before = json.dumps(report, sort_keys=True)
    output = module.render(report)
    headline = output.split("## Evidence details")[0]
    assert "contradicted" in headline
    assert "Unauthenticated" in headline
    assert "Resolve the refund destination" in headline
    assert "Recorded destination conflict." in output
    assert "Destination differs from the cited contract." in output
    assert "approval-only-a-reference" in output
    assert "qualification-only-a-reference" in output
    assert "authority_authenticated" in output and "false" in output
    assert row["subject_id"] not in headline
    assert row["subject_id"] in output
    assert '<details id="evidence-1">' in output
    assert "<dl>" in output and "<dt>subject_id</dt>" in output
    assert json.dumps(report, sort_keys=True) == before


@pytest.mark.parametrize("outcome", ["PASS", "FAIL"])
def test_completed_review_keeps_pending_review_and_independent_requirements(outcome):
    module = load_table()
    row = {
        "score": 2,
        "evidence_strength": "E2",
        "check_results": [
            {
                "check_id": "AC-02:Review",
                "method": "Review",
                "outcome": outcome,
                "action": "Select the applicable controls.",
            },
            {
                "check_id": "independent-review",
                "method": "human_review",
                "outcome": "NOT_RUN",
                "action": "Review the held-out attacks.",
            },
        ],
        "remaining_gaps": ["Independent held-out attacks and alternatives are absent."],
    }
    before = json.dumps(row, sort_keys=True)
    output = module.render({"items": {"AC-02": row}})
    human = next(line for line in output.splitlines() if line.startswith("| Anticheat")).split(" | ")[8]
    assert f"Completed review checks: AC-02:Review ({outcome})." in human
    assert "Required review: Review the held-out attacks." in human
    assert "Select the applicable controls." not in human
    assert "Remaining requirements: Independent held-out attacks and alternatives are absent." in human
    assert json.dumps(row, sort_keys=True) == before
    row["check_results"].pop()
    row["next_action"] = "Supply authorized, task-bound native and review evidence."
    output = module.render({"items": {"AC-02": row}})
    next_action = next(line for line in output.splitlines() if line.startswith("| Anticheat")).split(" | ")[-1]
    assert "Address remaining requirements: Independent held-out attacks and alternatives are absent." in next_action
    assert row["next_action"] not in next_action
    human = module.human_action(row)
    assert "Required review" not in human
    assert "Completed review checks" in human
    assert "Independent held-out attacks" in human


def test_table_shows_native_credit_range_and_per_case_checks_without_an_aggregate():
    module = load_table()
    controls: list[dict[str, Any]] = [
        {
            "case": case,
            "status": "passed",
            "checks": {"total_credit": total, "raw_credit": raw, "max_credit": 9, "checks": {"association": 0}},
            "reward": total / 9,
            "control": {"result": f"trials/{case}/result.json"},
        }
        for case, total, raw in [("reference", 9, 9), ("wrong-customer", 0, 8), ("missing-completion", 8, 8)]
    ]
    native = {"index_digest": "a" * 64, "native_execution": True, "controls": controls}
    row = {
        "score": 2,
        "evidence_strength": "E2",
        "evidence_refs": [{"measurement": "example_assessment.native", "digest": "a" * 64}],
    }
    report = {"items": {"AC-02": row}, "measurements": {"example_assessment": {"native": native}}}
    before = json.dumps(report, sort_keys=True)
    output = module.render(report)
    headline = output.split("## Evidence details")[0]
    assert "| 2 | 0/9 to 9/9 across 3 native controls. No aggregate." in headline
    assert "[Per-case task credit](#task-credit-1)" in headline
    assert "E2. [Evidence 1]" in headline
    assert '<details id="task-credit-1">' in output
    for number, control in enumerate(controls, 1):
        assert (
            f'<a href="#task-credit-1-case-{number}">{control["case"]}: {control["checks"]["total_credit"]}/9</a>'
            in output
        )
        assert f'<details id="task-credit-1-case-{number}">' in output
    assert "<dt>raw_credit</dt>" in output
    assert json.dumps(report, sort_keys=True) == before
    page = module.render_html(output)
    assert '<a href="#task-credit-1">Per-case task credit</a>' in page
    row["evidence_refs"] = []
    assert "| 2 | Not measured here |" in module.render(report)


@pytest.mark.parametrize("credit", [None, True, float("inf"), "9"])
def test_task_credit_keeps_unmeasured_cases_out_of_range(credit):
    module = load_table()
    native = {
        "index_digest": "a" * 64,
        "native_execution": True,
        "controls": [
            {"case": "observed", "checks": {"total_credit": 8, "max_credit": 9}},
            {"case": "unmeasured", "checks": {"total_credit": credit, "max_credit": credit}},
        ],
    }
    row = {"evidence_refs": [{"measurement": "example_assessment.native", "digest": "a" * 64}]}
    report = {"measurements": {"example_assessment": {"native": native}}}
    summary, details = module.task_credit(report, row, 1)
    assert summary == "1/2 native controls have task credit. See per-case scales. No aggregate."
    assert "unmeasured: Unmeasured</a>" in "".join(details)
    native["controls"][1]["checks"] = {"total_credit": 1, "max_credit": 1}
    assert module.task_credit(report, row, 1)[0] == (
        "2/2 native controls have task credit. See per-case scales. No aggregate."
    )
    row["evidence_refs"][0]["digest"] = "b" * 64
    assert module.task_credit(report, row, 1) == ("Not measured here", [])


def test_table_writes_responsive_html_and_verifies_it(tmp_path):
    from test_rubric_example_assessment import package

    task = tmp_path / "task"
    task.mkdir()
    package(task)
    output = tmp_path / "review"
    result = table_cli(tmp_path, "--task", "task/task.toml", "--output", output)
    assert result.returncode == 1, result.stdout
    page = (output / "results.html").read_text()
    assert 'name="viewport"' in page
    assert "@media" in page and "table-layout: fixed" in page
    assert '<a href="#evidence-1">' in page
    assert '<details id="evidence-1">' in page
    assert "Observed rubric score" in page
    assert table_cli(tmp_path, "--verify-existing", output).returncode == 1
    (output / "results.html").write_text(page.replace("Unassessed", "Passed"))
    assert table_cli(tmp_path, "--verify-existing", output).returncode == 2


@pytest.mark.parametrize("measurement", ["example_assessment.native", "example_assessment.native.environment"])
def test_runtime_task_credit_uses_retained_reward_without_inventing_check_scale(measurement):
    module = load_table()
    native = {
        "scope": "eval-author.refund-runtime-observation.v1",
        "index_digest": "a" * 64,
        "native_execution": True,
        "controls": [
            {"case": case, "reward": reward, "status": "passed"}
            for case, reward in [("nop", 0), ("actions", 1), ("reset", 0)]
        ],
    }
    row = {"score": 2, "evidence_refs": [{"measurement": measurement, "digest": "a" * 64}]}
    report = {"measurements": {"example_assessment": {"native": native}}}
    before = json.dumps(report, sort_keys=True)
    summary, details = module.task_credit(report, row, 1)
    assert summary == "Native reward 0 to 1 across 3 native controls. No aggregate."
    for case, reward in [("nop", 0), ("actions", 1), ("reset", 0)]:
        assert f"{case}: Native reward {reward}</a>" in "".join(details)
    assert "Task-check totals are not recorded for this panel." in "".join(details)
    assert "0/3 native controls have task credit" not in summary
    assert json.dumps(report, sort_keys=True) == before


@pytest.mark.parametrize("mutation", ["decision", "citation", "reviewer", "html", "markdown"])
def test_recorded_contradiction_survives_real_cli_and_replay(tmp_path, mutation):
    from test_rubric_example_assessment import add_review, package, refresh

    module = load_table()
    selected = package(tmp_path)
    before = module.measure(tmp_path, selected)
    document = add_review(tmp_path, selected, before)
    document["decisions"][0]["status"] = "contradicted"
    document["decisions"][0]["rationale"] = "Synthetic contradiction preservation control, not an actual finding."
    refresh(tmp_path, selected, "review.json", json.dumps(document))
    before = module.measure(tmp_path, {**selected, "example_assessment": {"task_config_id": "task.toml"}})
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(selected))
    output = tmp_path / "output"
    result = table_cli(tmp_path, "--selection", selection, "--output", output)
    assert result.returncode == 1, result.stdout
    saved = json.loads((output / "assessment.json").read_text())
    for ident, row in before["items"].items():
        assert saved["report"]["items"][ident]["score"] == row["score"]
        assert saved["report"]["items"][ident]["check_results"] == row["check_results"]
    for name in ("results.md", "results.html"):
        text = (output / name).read_text()
        assert document["decisions"][0]["rationale"] in text
        assert "Unauthenticated recorded review: contradicted" in text
    assert table_cli(tmp_path, "--verify-existing", output).returncode == 1
    review = saved["report"]["items"]["task-contract.clear-contract"]["recorded_source_reviews"][0]
    if mutation == "decision":
        review["decision"]["status"] = "supported"
    elif mutation == "citation":
        review["decision"]["citations"][0]["quote"] = "Forged quote"
    elif mutation == "reviewer":
        review["authority_authenticated"] = True
    else:
        path = output / ("results.html" if mutation == "html" else "results.md")
        path.write_text(path.read_text().replace("contradicted", "supported"))
    (output / "assessment.json").write_text(json.dumps(saved))
    frozen = {path.name: path.read_bytes() for path in output.iterdir()}
    assert table_cli(tmp_path, "--verify-existing", output).returncode == 2
    assert frozen == {path.name: path.read_bytes() for path in output.iterdir()}
