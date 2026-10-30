# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep incomplete scans, missing skills and private scanner details visible or excluded as appropriate."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location(
    "skill_evaluations", Path(__file__).resolve().parents[1] / "tools/collect_skill_evaluations.py"
)
assert spec is not None and spec.loader is not None
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_ci_evaluation_is_advisory_and_always_attempts_artifact_upload():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    assert jobs["skill-evaluator"]["continue-on-error"] is True
    upload = jobs["skill-evaluator"]["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["path"].endswith("/skillevaluator-summary.json")
    assert upload["with"]["if-no-files-found"] == "error"
    scan = next(
        step
        for step in jobs["skill-evaluator"]["steps"]
        if "collect_skill_evaluations.py" in step.get("run", "") and "--plan" not in step["run"]
    )
    assert scan["if"] == "${{ !cancelled() }}"
    assert not jobs["test"].get("continue-on-error", False)
    steps = jobs["skill-evaluator"]["steps"]
    fallback = next(i for i, step in enumerate(steps) if "--plan" in step.get("run", ""))
    install = next(i for i, step in enumerate(steps) if step.get("name") == "Install pinned evaluation tools")
    assert fallback < install
    stage = next(step for step in steps if step.get("name") == "Stage aggregate even after failure")
    assert stage["if"] == "always()" and "skill-fallback" in stage["run"]


def test_static_and_live_installs_match_the_reported_evaluator_revision():
    root = Path(__file__).resolve().parents[1]
    assert collector.EVALUATOR_REVISION == "3fd2cf57e4a2af01f282e001f7515b6e76f18ec7"
    for filename, job_name, step_name in (
        ("ci.yml", "skill-evaluator", "Install pinned evaluation tools"),
        ("skill-evaluation-live.yml", "evaluate", "Install isolated evaluator runtime"),
    ):
        workflow = yaml.safe_load((root / ".github/workflows" / filename).read_text())
        install = next(step["run"] for step in workflow["jobs"][job_name]["steps"] if step.get("name") == step_name)
        assert f"git+https://github.com/NVIDIA/SkillEvaluator.git@{collector.EVALUATOR_REVISION}" in install


def test_only_skillspector_security_is_skipped():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    steps = yaml.safe_load(workflow.read_text())["jobs"]["skill-evaluator"]["steps"]
    install = next(step["run"] for step in steps if step.get("name") == "Install pinned evaluation tools")
    assert "skillspector @" not in install
    assert "semgrep" in install and "gitleaks" in install
    assert collector.CHECKS == "schema,version,pii,license,code-integrity,unicode,quality,lint"
    assert {"Secrets Detection", "Code Risk Analysis", "PII Scan"} <= collector.VALIDATORS
    assert "Security Scan" not in collector.VALIDATORS
    result = collector.summarize(valid_report(), 0)
    assert result["status"] == "passed"
    assert {"name": "Security Scan", "status": "skipped"} in result["validators"]
    assert collector.SKIPPED_CHECKS == [
        {"check": "security", "validator": "Security Scan", "reason": "disabled_by_ci_policy"}
    ]


def valid_report():
    return {
        "overall_status": "passed",
        "overall_passed": True,
        "incomplete_scans": [],
        "policy": {"profile": "external", "digest": "sha256:" + "a" * 64},
        "severity_counts": {"critical": 0, "high": 0},
        "quality_summary": [{"overall_score": 91.2, "grade": "A"}],
        "results": [
            {
                "validator": name,
                "passed": True,
                "status": "passed",
                "incomplete_scans": [],
                "gating": {"tier": 1, "blocking": True},
                "findings": ["PRIVATE-SENTINEL"],
            }
            for name in sorted(collector.VALIDATORS)
        ],
    }


def test_passing_gate_requires_complete_evidence():
    report = valid_report()
    assert collector.summarize(report, 0)["status"] == "passed"
    assert collector.summarize(report, 1)["status"] == "failed"
    assert collector.summarize(report, 2)["status"] == "incomplete"
    report["results"].pop()
    assert collector.summarize(report, 0)["status"] == "incomplete"


def test_other_incomplete_scanners_still_prevent_a_pass():
    report = valid_report()
    report["incomplete_scans"] = ["semgrep"]
    result = collector.summarize(report, 1)
    assert result["status"] == "incomplete"
    assert result["quality"]["score"] == 91.2
    assert "PRIVATE-SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("field,value", [("severity_counts", []), ("results", [{"validator": []}] * 11)])
def test_malformed_nested_report_does_not_crash(field, value):
    report = valid_report()
    report[field] = value
    assert collector.summarize(report, 0)["status"] == "incomplete"


@pytest.mark.parametrize("value", [None, {}, [], {"results": [None]}, {"quality_summary": [{"overall_score": 90}]}])
def test_malformed_report_does_not_pass(value):
    assert collector.summarize(value, 0)["status"] == "incomplete"


def test_scans_every_skill_and_preserves_failed_collection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for root, name in (("skills", "eval-author"), ("skills", "new-sub-skill"), ("team-skills/ethos", "ethos-write")):
        skill = repo / root / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("test skill\n")
    for args in (
        ["init", "-b", "test"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    original_run = subprocess.run

    def version(argv, **kwargs):
        if argv[0] == "git":
            return original_run(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, "0.2.1", "")

    monkeypatch.setattr(collector.subprocess, "run", version)

    def execute(argv, cwd, env, log, timeout):
        assert "--no-llm" in argv and "--no-dedup" in argv
        assert argv[argv.index("--tiers") + 1] == "1"
        assert "security" not in argv[argv.index("--checks") + 1].split(",")
        assert "SECRET_PROVIDER_KEY" not in env
        if "eval-author" == Path(argv[2]).name:
            raise OSError("missing tool")
        reports = Path(argv[-1])
        reports.mkdir()
        (reports / "report.json").write_text(json.dumps(valid_report()))
        return 0, None

    monkeypatch.setenv("SECRET_PROVIDER_KEY", "PRIVATE-SENTINEL")
    monkeypatch.setattr(collector, "execute", execute)
    out = tmp_path / "results"
    result = collector.collect(repo, out, "skillevaluator", 5)
    assert [row["skill"] for row in result["skills"]] == ["eval-author", "new-sub-skill", "ethos-write"]
    assert result["skills"][-1]["path"] == "team-skills/ethos/ethos-write"
    assert result["counts"] == {"incomplete": 1, "passed": 2}
    assert result["policy"]["skipped_checks"] == collector.SKIPPED_CHECKS
    assert all({"name": "Security Scan", "status": "skipped"} in row["validators"] for row in result["skills"])
    assert "PRIVATE-SENTINEL" not in (out / "skillevaluator-summary.json").read_text()
    with pytest.raises(FileExistsError):
        collector.collect(repo, out, "skillevaluator", 5)


def test_unit_test_job_installs_public_dependencies_without_secrets():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    job = yaml.safe_load(workflow.read_text())["jobs"]["test"]
    assert "secrets." not in json.dumps(job)
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    assert any(step.get("run") == "uv sync --locked" for step in job["steps"])


@pytest.mark.parametrize("interrupt_at", ["plan", "version", "first_scan", "second_scan"])
def test_cancellation_keeps_complete_denominator_and_completed_scores(tmp_path, monkeypatch, interrupt_at):
    repo = tmp_path / "repo"
    for name in ("eval-author", "second"):
        skill = repo / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("test skill\n")
    monkeypatch.setattr(
        collector.subprocess, "check_output", lambda args, **kwargs: "a" * 40 if "rev-parse" in args else ""
    )

    def version(args, **kwargs):
        if interrupt_at == "plan":
            pytest.fail("plan must not invoke tool probes")
        if interrupt_at == "version":
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(args, 0, "0.2.1", "")

    monkeypatch.setattr(collector.subprocess, "run", version)
    calls = []

    def execute(args, *unused):
        calls.append(args)
        if interrupt_at == "first_scan" or len(calls) == 2:
            raise KeyboardInterrupt
        reports = Path(args[-1])
        reports.mkdir()
        (reports / "report.json").write_text(json.dumps(valid_report()))
        return 0, None

    monkeypatch.setattr(collector, "execute", execute)
    output = tmp_path / "output"
    if interrupt_at == "plan":
        collector.collect(repo, output, "skillevaluator", 5, plan=True)
        assert not calls
    else:
        with pytest.raises(KeyboardInterrupt):
            collector.collect(repo, output, "skillevaluator", 5)
    checkpoint = json.loads((output / "skillevaluator-summary.json").read_text())
    assert [row["skill"] for row in checkpoint["skills"]] == ["eval-author", "second"]
    assert checkpoint["status"] == "incomplete"
    assert set(checkpoint["tool_versions"]) == {"skillevaluator", "semgrep", "gitleaks"}
    completed = 1 if interrupt_at == "second_scan" else 0
    assert checkpoint["counts"]["incomplete"] == 2 - completed
    if completed:
        assert checkpoint["skills"][0]["status"] == "passed"
        assert checkpoint["skills"][0]["quality"]["score"] == 91.2
    assert all(row["reason"] == "not_run" and row["quality"] is None for row in checkpoint["skills"][completed:])
    assert "PRIVATE-SENTINEL" not in json.dumps(checkpoint)
    assert not (output / "skillevaluator-summary.json.tmp").exists()


def test_daily_ci_includes_static_checks_and_tests_without_cancelling_for_pushes():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text())
    assert workflow[True]["schedule"] == [{"cron": "5 7 * * *"}]
    for name in ("skill-evaluator", "test", "gym-compatibility"):
        condition = workflow["jobs"][name]["if"]
        assert "github.event_name == 'schedule'" in condition
        assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event_name == 'schedule' && 'daily' || 'change'" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.event_name != 'schedule' }}"
