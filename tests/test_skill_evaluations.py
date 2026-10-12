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
    assert upload["with"]["if-no-files-found"] == "warn"
    scan = next(
        step for step in jobs["skill-evaluator"]["steps"] if "collect_skill_evaluations.py" in step.get("run", "")
    )
    assert scan["if"] == "${{ !cancelled() }}"
    assert not jobs["test"].get("continue-on-error", False)


def test_skillspector_install_uses_pinned_upstream_source_and_publishes_path_first():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    steps = yaml.safe_load(workflow.read_text())["jobs"]["skill-evaluator"]["steps"]
    install = next(step["run"] for step in steps if step.get("name") == "Install pinned evaluation tools")
    assert (
        "skillspector @ git+https://github.com/NVIDIA/SkillSpector.git@69dcdfb74487d361ba4c811d088cfdea2ff3a9dc"
        in install
    )
    assert "skillspector==" not in install
    assert install.index('>> "$GITHUB_PATH"') < install.index("uv tool install")


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


def test_quality_survives_incomplete_security_without_becoming_a_pass():
    report = valid_report()
    report["incomplete_scans"] = ["skillspector"]
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
    for root, name in (("skills", "eval-author"), ("skills", "new-sub-skill"), ("ethos/skills", "ethos-write")):
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
    assert result["skills"][-1]["path"] == "ethos/skills/ethos-write"
    assert result["counts"] == {"incomplete": 1, "passed": 2}
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


def test_daily_ci_includes_static_checks_and_tests_without_cancelling_for_pushes():
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text())
    assert workflow[True]["schedule"] == [{"cron": "5 7 * * *"}]
    for name in ("skill-evaluator", "test", "gym-compatibility"):
        condition = workflow["jobs"][name]["if"]
        assert "github.event_name == 'schedule'" in condition
        assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event_name == 'schedule' && 'daily' || 'change'" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.event_name != 'schedule' }}"
