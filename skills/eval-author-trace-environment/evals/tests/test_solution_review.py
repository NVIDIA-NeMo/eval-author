# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real capture execution with synthetic provider records; not blind-review evidence."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import review_runtime as runtime  # noqa: E402
import solution_review as review  # noqa: E402
import trace_environment as driver  # noqa: E402


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def setup(tmp_path, count=1):
    root = tmp_path.resolve()
    (root / "task/tests").mkdir(parents=True)
    shutil.copy(SKILL / "scripts/review_runtime.py", root / "task/tests/review_runtime.py")
    (root / "task/instruction.md").write_text("Return the word correct.")
    (root / "private").mkdir()
    (root / "private/prompt.txt").write_text("Solve the public task.")
    plan = {
        "author_id": "author",
        "author_context_id": "author-context",
        "attempts": [
            {
                "id": f"attempt-{i}",
                "purpose": "solution" if i == 0 else "adversarial",
                "job_dir": f"private/jobs/run-{i}",
                "agent": "scripted-test",
                "model_name": None,
                "context_id": f"solver-{i}",
                "exposure": "public_only",
                "prompt": "private/prompt.txt",
                "transcript": "agent/trajectory.txt",
            }
            for i in range(count)
        ],
    }
    save(root / "private/plan.json", plan)
    binding = {
        "task_tree_sha256": "synthetic",
        "task_checksum": "synthetic-checksum",
        "construction_sha256": "synthetic",
        "requirements": ["answer"],
        "checks": ["answer-correct"],
    }
    receipt = review.record(root, "private/plan.json", binding)
    cohort = json.loads((root / receipt["cohort"]).read_text())
    assessments = {
        "schema": review.ASSESSMENT_SCHEMA,
        "cohort_sha256": review.digest(cohort),
        "reviewer_id": "reviewer",
        "context_id": "reviewer-context",
        "exposure": "verdict_visible",
        "judgments": [],
    }
    return root, binding, receipt, assessments


def trial(root, assessments, *, reward=True, decision="met", number=0):
    relative = f"private/jobs/run-{number}/task__trial"
    directory = root / relative
    source = root / f"private/submission-{number}"
    source.write_bytes(b"correct" if decision == "met" else b"incorrect")
    runtime.grade_submission({"answer": source}, lambda data: {"answer-correct": reward}, directory / "verifier")
    save(
        directory / "result.json",
        {
            "task_checksum": "synthetic-checksum",
            "config": {"agent": {"name": "scripted-test", "model_name": None}},
            "exception_info": None,
            "verifier_environment_mode": "separate",
            "verifier_result": {"rewards": {"reward": int(reward)}},
        },
    )
    (directory / "agent").mkdir()
    (directory / "agent/trajectory.txt").write_text("Scripted test wrote the submitted answer.")
    assessments["judgments"].append(
        {
            "attempt_id": f"attempt-{number}",
            "requirements": [
                {
                    "id": "answer",
                    "decision": decision,
                    "rationale": "Compare the captured bytes to the public contract.",
                    "citations": ["task/instruction.md", f"{relative}/verifier/review-submission/answer"],
                }
            ],
        }
    )
    return directory


def run(root, binding, receipt, assessments):
    save(root / "private/assessments.json", assessments)
    return review.review(root, receipt["cohort"], "private/assessments.json", binding)


@pytest.mark.parametrize(
    "reward,decision,outcome",
    [
        (True, "met", "legitimate_pass"),
        (False, "unmet", "legitimate_fail"),
        (True, "unmet", "false_accept"),
        (False, "met", "false_reject"),
        (True, "uncertain", "uncertain_assessment"),
    ],
)
def test_classifications_and_existing_parser(tmp_path, reward, decision, outcome):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = trial(root, assessments, reward=reward, decision=decision)
    assert driver._job_check_rows(directory.parent) == [
        {"check_id": "answer-correct", "status": "PASS" if reward else "FAIL"}
    ]
    result = run(root, binding, receipt, assessments)
    assert result["counts"] == {outcome: 1}
    assert result["diagnostic_only"] is True
    assert "valid" not in result
    assert result == run(root, binding, receipt, assessments)


def test_missing_attempt_stays_in_denominator(tmp_path):
    root, binding, receipt, assessments = setup(tmp_path, count=2)
    trial(root, assessments)
    result = run(root, binding, receipt, assessments)
    assert result["counts"] == {"legitimate_pass": 1, "missing_job": 1}
    assert result["attempts"] == 2 and result["unresolved"] == 1 and result["status"] == "incomplete"


@pytest.mark.parametrize(
    "change,outcome",
    [
        ("snapshot", "submission_binding_unverified"),
        ("rows", "submission_binding_unverified"),
        ("receipt", "submission_binding_unverified"),
        ("reward", "submission_binding_unverified"),
        ("transcript", "missing_transcript"),
        ("assessment", "missing_assessment"),
        ("exception", "infrastructure_failure"),
        ("nonbinary", "missing_or_nonbinary_reward"),
    ],
)
def test_unresolved_evidence_never_becomes_false_reject(tmp_path, change, outcome):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = trial(root, assessments, reward=False)
    paths = {
        "snapshot": "verifier/review-submission/answer",
        "rows": "verifier/results",
        "receipt": "verifier/review-submission.json",
        "reward": "verifier/reward.txt",
    }
    if change in paths:
        (directory / paths[change]).write_text("tampered")
    elif change == "transcript":
        (directory / "agent/trajectory.txt").unlink()
    elif change == "assessment":
        assessments["judgments"] = []
    else:
        value = json.loads((directory / "result.json").read_text())
        if change == "exception":
            value["exception_info"] = {"exception_type": "ProviderFailure"}
        else:
            value["verifier_result"]["rewards"]["reward"] = 0.5
        save(directory / "result.json", value)
    assert run(root, binding, receipt, assessments)["counts"] == {outcome: 1}


def test_rederive_changed_evidence_and_assessments(tmp_path):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = trial(root, assessments)
    first = run(root, binding, receipt, assessments)
    (directory / "verifier/review-submission/answer").write_text("changed")
    second = run(root, binding, receipt, assessments)
    assert first["report"] != second["report"]
    assert second["status"] == "incomplete"
    assert (root / first["report"]).exists()


@pytest.mark.parametrize("change", ["author", "context", "citation", "requirement", "task", "agent", "cohort"])
def test_reject_misbound_review(tmp_path, change):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = trial(root, assessments)
    if change == "author":
        assessments["reviewer_id"] = "author"
    elif change == "context":
        assessments["context_id"] = "solver-0"
    elif change == "citation":
        assessments["judgments"][0]["requirements"][0]["citations"][1] = "private/other-answer"
    elif change == "requirement":
        assessments["judgments"][0]["requirements"] = []
    elif change == "task":
        binding = dict(binding, task_checksum="changed")
    elif change == "agent":
        value = json.loads((directory / "result.json").read_text())
        value["config"]["agent"]["name"] = "other-agent"
        save(directory / "result.json", value)
    else:
        value = json.loads((root / receipt["cohort"]).read_text())
        value["plan"]["author_id"] = "changed"
        save(root / receipt["cohort"], value)
    with pytest.raises(ValueError):
        run(root, binding, receipt, assessments)


def test_predeclare_unique_future_jobs(tmp_path):
    root, binding, _, assessments = setup(tmp_path, count=2)
    plan = json.loads((root / "private/plan.json").read_text())
    plan["attempts"][1]["job_dir"] = plan["attempts"][0]["job_dir"]
    save(root / "private/duplicate.json", plan)
    with pytest.raises(ValueError, match="duplicate job"):
        review.record(root, "private/duplicate.json", binding)
    trial(root, assessments)
    with pytest.raises(ValueError, match="before any job"):
        review.record(root, "private/plan.json", binding)


def test_wrapper_grades_captured_bytes_and_refuses_overwrite(tmp_path):
    root = tmp_path.resolve()
    source = root / "answer"
    source.write_bytes(b"correct")

    def assess(data):
        source.write_bytes(b"changed-after-capture")
        with pytest.raises(TypeError):
            data["answer"] = b"replacement"
        return {"answer-correct": data["answer"] == b"correct"}

    runtime.grade_submission({"answer": source}, assess, root / "logs")
    assert (root / "logs/review-submission/answer").read_bytes() == b"correct"
    assert (root / "logs/reward.txt").read_text() == "1"
    with pytest.raises(ValueError, match="fresh"):
        runtime.grade_submission({"answer": source}, assess, root / "logs")


def test_wrapper_rejects_symlinks_oversize_and_invalid_checks(tmp_path):
    root = tmp_path.resolve()
    source = root / "answer"
    source.write_bytes(b"x")
    link = root / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        runtime.grade_submission({"answer": link}, lambda _: {"check": True}, root / "logs")
    source.write_bytes(b"x" * (runtime.LIMIT + 1))
    with pytest.raises(ValueError, match="bounded"):
        runtime.grade_submission({"answer": source}, lambda _: {"check": True}, root / "logs")
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="boolean"):
        runtime.grade_submission({"answer": source}, lambda _: json.loads('{"check": 1}'), root / "logs")
    assert not (root / "logs/review-submission.json").exists()


def test_cli_binding_and_existing_readiness_unchanged(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "review_construction_helpers", Path(__file__).with_name("test_construction.py")
    )
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root, _ = helper.workspace(tmp_path)
    shutil.copy(SKILL / "scripts/review_runtime.py", root / "task/tests/review_runtime.py")
    plan_root, _, _, _ = setup(tmp_path / "plan-source")
    shutil.copy(plan_root / "private/plan.json", root / "private/plan.json")
    shutil.copy(plan_root / "private/prompt.txt", root / "private/prompt.txt")
    before = {name: (root / name).read_bytes() for name in ("summary.json", "candidate.json", "reproducibility.json")}
    import argparse

    result = driver._solution_review(
        argparse.Namespace(task_dir=root, command="record-review-inputs", plan="private/plan.json")
    )
    assert result["attempts"] == 1
    assert before == {name: (root / name).read_bytes() for name in before}
    assert "author-context" not in json.dumps(result)


def test_missing_output_remains_ordinary_negative_control(tmp_path):
    root = tmp_path.resolve()
    runtime.grade_submission(
        {"answer": root / "missing"},
        lambda data: {"answer-correct": data["answer"] == b"correct"},
        root / "task__nop/verifier",
    )
    assert (root / "task__nop/verifier/reward.txt").read_text() == "0"
    assert driver._job_check_rows(root) == [{"check_id": "answer-correct", "status": "FAIL"}]


def absent_trial(root, assessments, *, all_missing=False, reward=True, decision="met"):
    directory = trial(root, assessments, reward=reward, decision=decision)
    shutil.rmtree(directory / "verifier")
    sources = {"answer": root / "private/submission-0", "support": root / "missing-support"}
    if all_missing:
        sources["answer"] = root / "missing-answer"
    runtime.grade_submission(sources, lambda _: {"answer-correct": reward}, directory / "verifier")
    locator = f"{directory.relative_to(root).as_posix()}/verifier/review-submission.json#/submissions/support"
    assessments["judgments"][0]["requirements"][0]["citations"] = ["task/instruction.md", locator]
    return directory


@pytest.mark.parametrize("all_missing", [False, True])
@pytest.mark.parametrize(
    "reward,decision,outcome",
    [
        (True, "met", "legitimate_pass"),
        (False, "unmet", "legitimate_fail"),
        (True, "unmet", "false_accept"),
        (False, "met", "false_reject"),
        (True, "uncertain", "uncertain_assessment"),
    ],
)
def test_captured_absence_uses_review_judgment(tmp_path, all_missing, reward, decision, outcome):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = absent_trial(root, assessments, all_missing=all_missing, reward=reward, decision=decision)
    result = run(root, binding, receipt, assessments)
    assert result["counts"] == {outcome: 1}
    evidence = json.loads((root / result["report"]).read_text())["evidence"]
    relative = (directory / "verifier/review-submission.json").relative_to(root).as_posix()
    assert evidence[relative] == runtime.sha((root / relative).read_bytes())
    assert not any("#" in path for path in evidence)


@pytest.mark.parametrize(
    "change", ["contradiction", "dangling", "extra", "directory", "missing", "root", "root-link", "digest"]
)
def test_absence_requires_consistent_snapshot_inventory(tmp_path, change):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = absent_trial(root, assessments)
    snapshots = directory / "verifier/review-submission"
    if change == "contradiction":
        (snapshots / "support").write_text("unexpected")
    elif change == "dangling":
        (snapshots / "support").symlink_to(root / "absent")
    elif change == "extra":
        (snapshots / "undeclared").write_text("unexpected")
    elif change == "directory":
        (snapshots / "answer").unlink()
        (snapshots / "answer").mkdir()
    elif change == "missing":
        (snapshots / "answer").unlink()
    elif change in ("root", "root-link"):
        moved = directory / "verifier/moved"
        snapshots.rename(moved)
        if change == "root-link":
            snapshots.symlink_to(moved, target_is_directory=True)
    else:
        path = directory / "verifier/review-submission.json"
        value = json.loads(path.read_text())
        value["submissions"]["support"] = False
        save(path, value)
    assert run(root, binding, receipt, assessments)["counts"] == {"submission_binding_unverified": 1}


@pytest.mark.parametrize(
    "suffix", ["", "#/reward", "#/submissions/answer", "#/submissions/unknown", "#/submissions/support/extra"]
)
def test_absence_citations_only_allow_exact_null_entry(tmp_path, suffix):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = absent_trial(root, assessments)
    citation = f"{directory.relative_to(root).as_posix()}/verifier/review-submission.json{suffix}"
    assessments["judgments"][0]["requirements"][0]["citations"][1] = citation
    with pytest.raises(ValueError, match="citation must bind"):
        run(root, binding, receipt, assessments)


@pytest.mark.parametrize("agent", ["oracle", "harbor.agents.oracle:OracleAgent"])
def test_oracle_is_not_independent(tmp_path, agent):
    root, binding, _, _ = setup(tmp_path)
    plan = json.loads((root / "private/plan.json").read_text())
    plan["attempts"][0]["agent"] = agent
    save(root / "private/plan.json", plan)
    with pytest.raises(ValueError, match="Oracle"):
        review.record(root, "private/plan.json", binding)


def test_changed_embedded_plan_even_at_new_digest_rejected(tmp_path):
    root, binding, receipt, assessments = setup(tmp_path, count=2)
    cohort = json.loads((root / receipt["cohort"]).read_text())
    cohort["plan"]["attempts"].pop()
    relative = review.persist(root, "cohorts", cohort)
    with pytest.raises(ValueError, match="retained plan"):
        review.review(root, relative, "private/unused-assessment.json", binding)


def test_shared_verifier_cannot_confirm_mismatch(tmp_path):
    root, binding, receipt, assessments = setup(tmp_path)
    directory = trial(root, assessments, reward=False)
    value = json.loads((directory / "result.json").read_text())
    value["verifier_environment_mode"] = "shared"
    save(directory / "result.json", value)
    assert run(root, binding, receipt, assessments)["counts"] == {"verifier_isolation_unverified": 1}
