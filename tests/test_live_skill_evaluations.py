# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check credential isolation, incomplete evidence, and paired live metrics without APIs."""

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monkeypatch.setenv("SKILL_EVAL_LLM_MODEL", "fixture/chat")
    monkeypatch.setenv("SKILL_EVAL_EMBEDDING_MODEL", "fixture/embedding")
    monkeypatch.delenv("SKILL_EVAL_AGENT_MODEL", raising=False)
    return importlib.import_module("collect_live_skill_evaluations")


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    for name in ("eval-author", "another-skill"):
        path = repo / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text("# Fixture\n")
    evals = repo / "skills/eval-author/evals"
    evals.mkdir()
    (evals / "evals.json").write_text(
        json.dumps(
            {"skill_name": "eval-author", "evals": [{"id": "one", "prompt": "Task", "expected_output": "Answer"}]}
        )
    )
    for args in (
        ["init", "-b", "test"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture"],
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return repo


def tier2(validator="Context Deduplication"):
    return {
        "results": [
            {
                "validator": validator,
                "status": "passed",
                "passed": True,
                "incomplete_scans": [],
                "summary": {"errors": 0, "critical_count": 0, "high_count": 0, "medium_count": 0, "low_count": 0},
                "findings": [],
                "legacy": {"messages": ["PRIVATE-SENTINEL"]},
            }
        ]
    }


def tier3():
    arm = {"execution_status": "succeeded", "execution_errors": [], "expected_attempts": 1, "scored_attempts": 1}
    metrics = ["security", "skill_execution", "skill_efficiency", "accuracy", "goal_accuracy", "behavior_check"]
    return {
        "execution_status": "succeeded",
        "execution_errors": [],
        "agents": {
            "opencode": {
                "execution_status": "succeeded",
                "conditions": {"with_skill": arm.copy(), "without_skill": arm.copy()},
                "metrics_with_skill": metrics.copy(),
                "metrics_without_skill": metrics.copy(),
                "with_skill": dict.fromkeys(metrics, 0.8),
                "without_skill": dict.fromkeys(metrics, 0.9),
                "not_applicable_metrics": {"with_skill": [], "without_skill": []},
                # Native dimension_scores() returns score/source objects, not scalars.
                "dimensions_with_skill": {
                    dimension: {"score": 0.8, "sources": {"PRIVATE-SENTINEL": 1}}
                    for dimension in ("security", "correctness", "discoverability", "effectiveness", "efficiency")
                },
                "dimensions_without_skill": {
                    dimension: {"score": 0.9, "sources": {"PRIVATE-SENTINEL": 1}}
                    for dimension in ("security", "correctness", "discoverability", "effectiveness", "efficiency")
                },
                "pass_at_k": {
                    "with_skill": {"passed_cases": 0, "total_cases": 1, "k": 1},
                    "without_skill": {"passed_cases": 1, "total_cases": 1, "k": 1},
                },
                "trajectory": "PRIVATE-SENTINEL",
            }
        },
    }


def test_tier2_distinguishes_duplicate_findings_from_missing_provider_evidence(live):
    report = tier2()
    row = report["results"][0]
    row.update(passed=False, status="failed", findings=[{"check_name": "duplicate"}])
    row["summary"].update(high_count=1, errors=1)
    assert live.summarize_tier2(report, 1, "Context Deduplication")["status"] == "failed"
    row["findings"][0]["check_name"] = "llm_error"
    assert live.summarize_tier2(report, 1, "Context Deduplication")["status"] == "incomplete"
    row["findings"] = []
    row["summary"]["high_count"] = 0
    assert live.summarize_tier2(report, 1, "Context Deduplication")["status"] == "incomplete"


@pytest.fixture
def license_report(live, tmp_path):
    from license_duplication_policy import HEADER

    skill = tmp_path / "skill"
    skill.mkdir()
    header = "\n".join(HEADER) + "\n\n"
    (skill / "SKILL.md").write_text("---\nname: example\n---\n" + header + "# Instructions\n")
    (skill / "reference.md").write_text(header + "# Reference\n")
    report = tier2()
    row = report["results"][0]
    row.update(
        passed=False,
        status="failed",
        findings=[
            {
                "severity": "high",
                "check_name": "duplicate",
                "file_path": "SKILL.md",
                "line_number": 4,
                "message": "Duplicate content found across SKILL.md and reference.md:\n"
                '  "(preamble)" in SKILL.md (lines 4-6)\n'
                '  vs "(preamble)" in reference.md (lines 1-3)',
            }
        ],
    )
    row["summary"].update(high_count=1, errors=1)
    return skill, report


def test_license_only_duplicate_passes_without_changing_raw_report(live, license_report):
    skill, report = license_report
    original = copy.deepcopy(report)
    result = live.summarize_tier2(report, 1, "Context Deduplication", skill)
    assert result["status"] == "passed"
    assert result["severity_counts"] == dict(critical=0, high=0, medium=0, low=0)
    assert result["raw_severity_counts"]["high"] == 1
    assert result["excluded_license_findings"] == 1
    assert "1 expected license-header" in result["reason"]
    assert report == original


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_license_exception_preserves_substantive_failures(live, license_report, severity):
    skill, report = license_report
    row = report["results"][0]
    row["findings"].append({"severity": severity, "check_name": "duplicate", "message": "Real duplication"})
    row["summary"][severity + "_count"] += 1
    row["summary"]["errors"] += 1
    original = copy.deepcopy(report)
    result = live.summarize_tier2(report, 1, "Context Deduplication", skill)
    assert result["status"] == "failed"
    assert result["severity_counts"][severity] == 1
    assert result["excluded_license_findings"] == 1
    assert report == original


@pytest.mark.parametrize(
    "mutation",
    [
        "substantive_span",
        "changed_license",
        "missing_file",
        "symlink",
        "traversal",
        "alias",
        "wrong_span",
        "out_of_bounds",
        "wrong_location",
        "unknown_message",
        "trailing_text",
        "non_preamble",
        "header_after_content",
    ],
)
def test_license_exception_fails_closed_on_unverified_evidence(live, license_report, mutation):
    skill, report = license_report
    finding = report["results"][0]["findings"][0]
    path = skill / "reference.md"
    if mutation == "substantive_span":
        path.write_text(path.read_text().replace("\n\n", "\nDo the task.\n"))
    elif mutation == "changed_license":
        path.write_text(path.read_text().replace("Apache-2.0", "MIT"))
    elif mutation == "missing_file":
        path.unlink()
    elif mutation == "symlink":
        external = skill.parent / "external.md"
        path.rename(external)
        path.symlink_to(external)
    elif mutation in ("traversal", "alias"):
        name = "../skill/reference.md" if mutation == "traversal" else "./reference.md"
        finding["message"] = finding["message"].replace("reference.md", name)
    elif mutation in ("wrong_span", "out_of_bounds"):
        finding["message"] = finding["message"].replace("1-3", "2-3" if mutation == "wrong_span" else "1-99")
    elif mutation == "wrong_location":
        finding["line_number"] = 5
    elif mutation == "unknown_message":
        finding["message"] = "Duplicate license preambles"
    elif mutation == "trailing_text":
        finding["message"] += "\nAdditional duplicated instructions"
    elif mutation == "non_preamble":
        finding["message"] = finding["message"].replace("(preamble)", "Instructions")
    elif mutation == "header_after_content":
        path.write_text("# Instructions\n" + path.read_text())
        finding["message"] = finding["message"].replace("1-3", "2-4")
    result = live.summarize_tier2(report, 1, "Context Deduplication", skill)
    assert result["status"] == "failed"
    assert result["severity_counts"]["high"] == 1
    assert "excluded_license_findings" not in result


@pytest.mark.parametrize("mutation", ["incomplete", "error", "counts", "exit", "negative", "unknown_check"])
def test_license_exception_never_promotes_incomplete_or_malformed_reports(live, license_report, mutation):
    skill, report = license_report
    row = report["results"][0]
    code = 1
    if mutation == "incomplete":
        row["incomplete_scans"] = ["provider unavailable"]
    elif mutation == "error":
        row["summary"]["errors"] = 2
    elif mutation == "counts":
        row["summary"].update(high_count=2, errors=2)
    elif mutation == "exit":
        code = 0
    elif mutation == "negative":
        row["summary"]["high_count"] = -1
    elif mutation == "unknown_check":
        row["findings"][0]["check_name"] = "llm_error"
    if mutation in ("exit", "negative"):
        with pytest.raises(ValueError):
            live.summarize_tier2(report, code, "Context Deduplication", skill)
    else:
        result = live.summarize_tier2(report, code, "Context Deduplication", skill)
        assert result["status"] == ("failed" if mutation == "counts" else "incomplete")
        assert "excluded_license_findings" not in result


def test_license_exception_requires_context_check_and_source(live, license_report):
    skill, report = license_report
    assert live.summarize_tier2(report, 1, "Context Deduplication")["status"] == "failed"
    report["results"][0]["validator"] = "Similarity Check"
    assert live.summarize_tier2(report, 1, "Similarity Check", skill)["status"] == "failed"


@pytest.mark.parametrize("source_changes", [False, True])
def test_collector_retains_excluded_findings_and_invalidates_changed_source(
    live, repo, tmp_path, monkeypatch, license_report, source_changes
):
    skill, raw = license_report
    for path in skill.iterdir():
        (repo / "skills/eval-author" / path.name).write_text(path.read_text())
    subprocess.run(["git", "add", "skills"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "headers"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")

    def execute(argv, cwd, env, log, timeout):
        data = raw if argv[1] == "context-optimization-check" else tier2("Similarity Check")
        path = Path(argv[-1]) / "result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        if source_changes and data is raw:
            source = repo / "skills/eval-author/reference.md"
            source.write_text(source.read_text() + "\nChanged during evaluation.\n")
        return (1 if data is raw else 0), None

    monkeypatch.setattr(live, "execute", execute)
    output = tmp_path / "collected"
    report = live.collect(repo, output, run=True, tier="2", skill_name="eval-author")
    row = next(row for row in report["observations"] if row["skill"] == "eval-author" and row["tier"] == 2)
    if source_changes:
        assert row["status"] == "incomplete"
        assert row["reason"] == "skill_changed_during_run"
        for key in ("severity_counts", "raw_severity_counts", "excluded_license_findings", "diagnostics"):
            assert key not in row
    else:
        assert row["status"] == "passed"
        assert row["excluded_license_findings"] == 1
        assert "Duplicate content found" in row["diagnostics"]
        assert "PRIVATE-SENTINEL" not in row["diagnostics"]
        markdown = (output / "live-skillevaluator-summary.md").read_text()
        assert "expected license-header" in markdown and "Duplicate content found" in markdown
        native = output / f"check-{report['observations'].index(row)}/reports/result.json"
        assert json.loads(native.read_text()) == raw


@pytest.mark.parametrize("failure", [None, "missing_na", "missing_map", "null_score", "scored_attempts"])
def test_cli_publishes_unmeasured_dimensions_without_losing_completed_attempts(
    live, repo, tmp_path, monkeypatch, failure
):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")
    monkeypatch.setenv("SKILL_EVAL_AGENT_MODEL", "fixture/chat")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    raw = tier3()
    for dimension in ("discoverability", "efficiency"):
        del raw["agents"]["opencode"]["dimensions_without_skill"][dimension]
    agent = raw["agents"]["opencode"]
    for metric in ("skill_execution", "skill_efficiency"):
        del agent["without_skill"][metric]
        if failure != "missing_na":
            agent["not_applicable_metrics"]["without_skill"].append(metric)
    if failure == "missing_map":
        del agent["dimensions_without_skill"]
    elif failure == "null_score":
        agent["dimensions_without_skill"]["security"]["score"] = None
    elif failure == "scored_attempts":
        agent["conditions"]["without_skill"]["scored_attempts"] = 0

    def execute(argv, cwd, env, log, timeout):
        if "evaluate" in argv:
            path = Path(argv[argv.index("--results-dir") + 1]) / "eval-author/run/result.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(raw))
        return 0, None

    monkeypatch.setattr(live, "execute", execute)
    output = tmp_path / "collected"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collector",
            "--repo",
            str(repo),
            "--output",
            str(output),
            "--tier",
            "3",
            "--skill",
            "eval-author",
            "--check",
            "live",
            "--ci-context-budget",
            "--run",
        ],
    )
    assert live.main() == int(failure is not None)
    published = (output / "live-skillevaluator-summary.json").read_text()
    report = json.loads(published)
    row = next(r for r in report["observations"] if r["status"] != "skipped")
    if failure:
        assert row["status"] == "incomplete" and row["reason"] == "tool_or_report_error"
        assert not {"arms", "dimension_lift", "pass_rate_lift"}.intersection(row)
        assert "PRIVATE-SENTINEL" not in published
        return
    assert row["status"] == "completed" and row["reason"] is None
    assert row["exit_code"] == 0
    assert row["pass_rate_lift"] == -1
    markdown = (output / "live-skillevaluator-summary.md").read_text()
    for dimension in live.DIMENSIONS:
        assert row["arms"]["with_skill"]["dimensions"][dimension] == 0.8
        if dimension in ("discoverability", "efficiency"):
            assert row["arms"]["without_skill"]["dimensions"][dimension] is None
            assert row["dimension_lift"][dimension] is None
            assert f"| {dimension} | 0.800 | unmeasured | unmeasured |" in markdown
        else:
            assert row["arms"]["without_skill"]["dimensions"][dimension] == 0.9
            assert row["dimension_lift"][dimension] == pytest.approx(-0.1)
            assert f"| {dimension} | 0.800 | 0.900 | -0.100 |" in markdown
    assert "PRIVATE-SENTINEL" not in published + markdown

    ci = importlib.import_module("live_ci")
    shards = tmp_path / "shards"
    args = SimpleNamespace(
        command="initialize",
        repo=repo,
        tier="3",
        skill="eval-author",
        check="live",
        models_resolved=True,
        output=shards / "skill-shard-123-1-live-eval-author",
        source=output,
    )
    ci.initialize(args)
    args.command = "stage"
    staged = ci.merge(args)
    assert next(r for r in staged["observations"] if r["tier"] == 3 and r["skill"] == "eval-author") == row
    args.command, args.check, args.output, args.source = "initialize", "all", tmp_path / "aggregate", shards
    ci.initialize(args)
    args.command = "aggregate"
    aggregated = ci.merge(args)
    assert next(r for r in aggregated["observations"] if r["tier"] == 3 and r["skill"] == "eval-author") == row
    assert "unmeasured" in (args.output / "live-skillevaluator-summary.md").read_text()


def test_negative_lift_is_a_completed_experiment_not_an_execution_failure(live):
    result = live.summarize_tier3(tier3(), 0, 1)
    assert result["status"] == "completed"
    assert result["pass_rate_lift"] == -1
    assert result["dimension_lift"]["correctness"] == pytest.approx(-0.1)
    assert "PRIVATE-SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize(
    "message,reason",
    [
        (
            "opencode runtime preflight failed: Docker compose command failed: docker compose build.",
            "docker_build_failed",
        ),
        ("opencode runtime preflight failed: invalid response", "agent_runtime_preflight_failed"),
        ("opencode runtime preflight failed: Docker compose command failed", "docker_runtime_failed"),
        ("opencode runtime preflight failed: NonZeroAgentExitCodeError", "agent_command_failed"),
        ("NonZeroAgentExitCodeError: OpenCode emitted error event(s)", "agent_api_failed"),
        ("NonZeroAgentExitCodeError: apt-get update", "agent_install_failed"),
        ("NonZeroAgentExitCodeError: nvm install 22", "agent_install_failed"),
        ("opencode runtime preflight failed: AgentSetupTimeoutError", "agent_install_timeout"),
        ("opencode runtime preflight failed: AgentTimeoutError", "agent_execution_timeout"),
        ("opencode runtime preflight failed: no agent artifacts", "agent_artifacts_missing"),
        ("unknown failure", "execution_incomplete"),
    ],
)
def test_failed_execution_exports_only_fixed_diagnostic_categories(live, message, reason):
    result = live.summarize_tier3(
        {"execution_status": "failed", "execution_errors": [message + " PRIVATE-SENTINEL"]}, 1, 4
    )
    assert result == {"status": "incomplete", "reason": reason}


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("field", ["requested", "scores", "na_map", "na_arm"])
@pytest.mark.parametrize("value", [None, False, 0, "security", ["security"]])
def test_native_metric_evidence_requires_typed_containers(live, arm, field, value):
    report = tier3()
    agent = report["agents"]["opencode"]
    if field == "requested":
        agent["metrics_" + arm] = {} if isinstance(value, list) else value
    elif field == "scores":
        agent[arm] = value
    elif field == "na_map":
        agent["not_applicable_metrics"] = value
    else:
        agent["not_applicable_metrics"][arm] = {} if isinstance(value, list) else value
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("field", ["requested", "scores", "na_map", "na_arm"])
def test_missing_native_metric_evidence_is_rejected(live, arm, field):
    report = tier3()
    agent = report["agents"]["opencode"]
    if field == "na_arm":
        del agent["not_applicable_metrics"][arm]
    else:
        del agent[{"requested": "metrics_" + arm, "scores": arm, "na_map": "not_applicable_metrics"}[field]]
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("field", ["requested", "na_arm"])
@pytest.mark.parametrize("entry", [None, 1, False, [], {}, "", " ", " accuracy", "accuracy ", "accuracy"])
def test_native_metric_name_lists_reject_malformed_or_duplicate_entries(live, arm, field, entry):
    report = tier3()
    agent = report["agents"]["opencode"]
    agent["not_applicable_metrics"][arm] = ["accuracy"]
    del agent[arm]["accuracy"]
    names = agent["metrics_" + arm] if field == "requested" else agent["not_applicable_metrics"][arm]
    names.append(entry)
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("marked_na", [False, True])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1, 2, True, "0.8", [], {}])
def test_native_source_scores_must_be_numeric_even_when_marked_na(live, arm, marked_na, value):
    report = tier3()
    agent = report["agents"]["opencode"]
    agent[arm]["skill_execution"] = value
    agent["not_applicable_metrics"][arm] = ["skill_execution"] if marked_na else []
    del agent["dimensions_" + arm]["discoverability"]
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("metric", [None, 0, "", " accuracy"])
def test_native_source_score_maps_require_metric_names(live, arm, metric):
    report = tier3()
    report["agents"]["opencode"][arm][metric] = 0.8
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("metric", ["unrequested", "accuracy"])
def test_native_na_metrics_cannot_be_unrequested_or_scored(live, arm, metric):
    report = tier3()
    report["agents"]["opencode"]["not_applicable_metrics"][arm] = [metric]
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("justified", [False, True])
def test_omitted_native_dimensions_require_explicit_na_for_missing_metrics(live, arm, justified):
    report = tier3()
    agent = report["agents"]["opencode"]
    del agent["dimensions_" + arm]["discoverability"]
    del agent[arm]["skill_execution"]
    if justified:
        agent["not_applicable_metrics"][arm] = ["skill_execution"]
        result = live.summarize_tier3(report, 0, 1)
        assert result["status"] == "completed"
        assert result["arms"][arm]["dimensions"]["discoverability"] is None
    else:
        with pytest.raises(ValueError, match="missing required metric"):
            live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("value", [None, [], "", 0, False])
def test_native_dimension_maps_must_be_objects(live, arm, value):
    report = tier3()
    report["agents"]["opencode"]["dimensions_" + arm] = value
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
def test_missing_native_dimension_map_is_not_an_unmeasured_arm(live, arm):
    report = tier3()
    del report["agents"]["opencode"]["dimensions_" + arm]
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("value", [None, {}, [], 0, "score", {"sources": {}}, {"score": None, "not_applicable": True}])
def test_present_native_dimension_entries_cannot_be_malformed(live, arm, value):
    report = tier3()
    report["agents"]["opencode"]["dimensions_" + arm]["security"] = value
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("arm", ["with_skill", "without_skill"])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), float("-inf"), -1, 2, True, False, "0.8", [], {}])
def test_invalid_live_scores_cannot_be_published(live, arm, value):
    report = tier3()
    report["agents"]["opencode"]["dimensions_" + arm]["security"]["score"] = value
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


@pytest.mark.parametrize("unmeasured_arms", [("with_skill",), ("without_skill",), ("with_skill", "without_skill")])
def test_empty_native_dimension_maps_preserve_zero_scores_and_attempts(live, unmeasured_arms):
    report = tier3()
    agent = report["agents"]["opencode"]
    for arm in ("with_skill", "without_skill"):
        dimensions = agent["dimensions_" + arm]
        if arm in unmeasured_arms:
            dimensions.clear()
            # Synthetic legacy-set boundary: default native security is never N/A.
            agent["metrics_" + arm].remove("security")
            agent["not_applicable_metrics"][arm] = agent["metrics_" + arm].copy()
            agent[arm].clear()
        else:
            agent[arm] = dict.fromkeys(agent["metrics_" + arm], 0)
            for entry in dimensions.values():
                entry["score"] = 0
    result = live.summarize_tier3(report, 0, 1)
    assert result["status"] == "completed"
    assert result["dimension_lift"] == dict.fromkeys(live.DIMENSIONS)
    assert result["pass_rate_lift"] == -1
    assert result["arms"]["with_skill"]["pass_rate"] == 0
    assert result["arms"]["without_skill"]["pass_rate"] == 1
    for arm in ("with_skill", "without_skill"):
        assert result["arms"][arm]["dimensions"] == dict.fromkeys(
            live.DIMENSIONS, None if arm in unmeasured_arms else 0
        )
        assert result["arms"][arm]["total_cases"] == 1


@pytest.fixture
def tier3_candidate(live):
    summary = live.summarize_tier3(tier3(), 0, 1)
    row = {
        **summary,
        "tier": 3,
        "skill": "eval-author",
        "check": "live",
        "tree_digest": "fixture",
        "cases": 1,
        "exit_code": 0,
        "report_digest": "sha256:" + "a" * 64,
    }
    ci = importlib.import_module("live_ci")
    report = {
        **dict.fromkeys(ci.BINDINGS, "fixture"),
        "inputs_clean": True,
        "mode": "run",
        "observations": [row],
        "policy": {"requested_tier": "3", "requested_skill": "eval-author", "requested_check": "live"},
    }
    return ci, report, copy.deepcopy(report), copy.deepcopy(row)


@pytest.mark.parametrize(
    "with_score,without_score,lift",
    [(0, 0, 0), (0, 1, -1), (1, 0, 1), (None, 0, None), (0, None, None), (None, None, None)],
)
def test_aggregate_validates_measured_and_unmeasured_lifts(tier3_candidate, with_score, without_score, lift):
    ci, report, base, expected = tier3_candidate
    row = report["observations"][0]
    row["arms"]["with_skill"]["dimensions"]["security"] = with_score
    row["arms"]["without_skill"]["dimensions"]["security"] = without_score
    row["dimension_lift"]["security"] = lift
    assert ci.validated_row(report, base, expected) == row


@pytest.mark.parametrize("field", ["with_skill", "without_skill", "lift"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "list", "null", "bool", "string", "nonfinite", "range"])
def test_aggregate_rejects_malformed_dimension_maps_and_values(tier3_candidate, field, mutation):
    ci, report, base, expected = tier3_candidate
    row = report["observations"][0]
    container, key = (row, "dimension_lift") if field == "lift" else (row["arms"][field], "dimensions")
    if mutation == "missing":
        del container[key]["security"]
    elif mutation == "extra":
        container[key]["unknown"] = 0
    elif mutation == "list":
        container[key] = list(container[key])
    elif mutation == "null":
        container[key] = None
    else:
        container[key]["security"] = {"bool": False, "string": "0", "nonfinite": float("nan"), "range": 2}[mutation]
    with pytest.raises((ValueError, KeyError, TypeError)):
        ci.validated_row(report, base, expected)


@pytest.mark.parametrize(
    "with_score,without_score,lift", [(None, 0, 0), (0, None, 0), (None, None, 0), (0, 0, None), (0, 1, 1)]
)
def test_aggregate_rejects_inconsistent_dimension_lift(tier3_candidate, with_score, without_score, lift):
    ci, report, base, expected = tier3_candidate
    row = report["observations"][0]
    row["arms"]["with_skill"]["dimensions"]["security"] = with_score
    row["arms"]["without_skill"]["dimensions"]["security"] = without_score
    row["dimension_lift"]["security"] = lift
    with pytest.raises(ValueError):
        ci.validated_row(report, base, expected)


@pytest.mark.parametrize(
    "mutation",
    [
        "report_error",
        "agent_error",
        "agent_status",
        "arm_error",
        "arm_status",
        "expected_attempts",
        "scored_attempts",
        "passed_cases",
        "total_cases",
        "k",
    ],
)
def test_unmeasured_dimensions_do_not_hide_incomplete_execution(live, mutation):
    report = tier3()
    agent = report["agents"]["opencode"]
    agent["dimensions_without_skill"] = {}
    agent["metrics_without_skill"].remove("security")
    agent["without_skill"] = {}
    agent["not_applicable_metrics"]["without_skill"] = agent["metrics_without_skill"].copy()
    if mutation == "report_error":
        report["execution_errors"] = ["synthetic error"]
    elif mutation == "agent_error":
        agent["execution_errors"] = ["synthetic error"]
    elif mutation == "agent_status":
        agent["execution_status"] = "failed"
    elif mutation == "arm_error":
        agent["conditions"]["without_skill"]["execution_errors"] = ["synthetic error"]
    elif mutation == "arm_status":
        agent["conditions"]["without_skill"]["execution_status"] = "failed"
    elif mutation in ("expected_attempts", "scored_attempts"):
        agent["conditions"]["without_skill"][mutation] = 0
    else:
        agent["pass_at_k"]["without_skill"][mutation] = 2
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)


def test_missing_baseline_and_changed_denominator_do_not_produce_lift(live):
    with pytest.raises(ValueError):
        live.summarize_tier3(tier3(), 0, 2)
    report = tier3()
    report["agents"]["opencode"]["conditions"]["without_skill"]["scored_attempts"] = 0
    with pytest.raises(ValueError):
        live.summarize_tier3(report, 0, 1)
    assert live.summarize_tier3(tier3(), 1, 1)["status"] == "incomplete"


def test_live_schema_v2_rejects_v1_shards_through_provenance_bindings(live, repo, tmp_path, monkeypatch):
    monkeypatch.delenv("INFERENCE_HUB_API_KEY", raising=False)
    base = live.collect(repo, tmp_path / "schema", tier="3", skill_name="eval-author", check_name="live")
    assert base["schema"] == "nemo.eval_author.live_skill_evaluations.v2"
    ci = importlib.import_module("live_ci")
    expected = next(row for row in base["observations"] if row["status"] == "not_run")
    candidate = copy.deepcopy(base)
    candidate["mode"] = "run"
    candidate["policy"] = ci.expected_policy(base, expected)
    row = next(row for row in candidate["observations"] if ci.key(row) == ci.key(expected))
    row.update(status="incomplete", reason="fixture")
    assert ci.validated_row(candidate, base, expected)["status"] == "incomplete"
    candidate["schema"] = "nemo.eval_author.live_skill_evaluations.v1"
    with pytest.raises(ValueError, match="report provenance mismatch"):
        ci.validated_row(candidate, base, expected)


def test_plan_and_missing_key_never_execute(live, repo, tmp_path, monkeypatch):
    def no_execute(*args, **kwargs):
        pytest.fail("plan or missing credentials launched a process")

    monkeypatch.setattr(live, "execute", no_execute)
    monkeypatch.delenv("INFERENCE_HUB_API_KEY", raising=False)
    report = live.collect(repo, tmp_path / "missing", run=True)
    assert sum(r["reason"] == "missing_dataset" for r in report["observations"]) == 1
    assert sum(r["reason"] == "missing_provider_key" for r in report["observations"]) == 4
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "PRIVATE-SENTINEL")
    report = live.collect(repo, tmp_path / "plan")
    assert report["mode"] == "plan"
    assert "PRIVATE-SENTINEL" not in (tmp_path / "plan/live-skillevaluator-summary.json").read_text()


def test_hub_configuration_ignores_ambient_providers_and_preserves_model_namespaces(live, monkeypatch):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "hub-fixture-key")
    for key in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "HUB_METADATA_API_KEY", "SKILL_EVAL_LLM_API_KEY"):
        monkeypatch.setenv(key, "UNRELATED-SECRET")
    for key in ("OPENAI_BASE_URL", "SKILL_EVAL_LLM_BASE_URL", "SKILL_EVAL_EMBEDDING_BASE_URL"):
        monkeypatch.setenv(key, "https://api.openai.com/v1")
    monkeypatch.setenv("SKILL_EVAL_AGENT_MODEL", "openai/hub-model")
    models, env, missing = live.configuration("inference_hub")
    assert not missing
    assert models["agent"] == "openai/openai/hub-model"
    assert env["SKILL_EVAL_LLM_BASE_URL"] == env["SKILL_EVAL_EMBEDDING_BASE_URL"] == live.HUB_BASE_URL
    assert env["SKILL_EVAL_LLM_API_KEY"] == env["SKILL_EVAL_EMBEDDING_API_KEY"] == "hub-fixture-key"
    assert "UNRELATED-SECRET" not in json.dumps(env)
    for provider in ("openai", "nv_build"):
        with pytest.raises(ValueError, match="only NVIDIA Inference Hub"):
            live.configuration(provider)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_hub_defaults_apply_to_unset_or_blank_model_variables(live, monkeypatch, value):
    for key in ("SKILL_EVAL_LLM_MODEL", "SKILL_EVAL_EMBEDDING_MODEL", "SKILL_EVAL_AGENT_MODEL"):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    models, env, _ = live.configuration("inference_hub")
    assert models == {
        "chat": "azure/openai/gpt-5.6-luna",
        "embedding": "azure/openai/text-embedding-3-small",
        "agent": "openai/azure/openai/gpt-5.6-luna",
    }
    assert env["SKILL_EVAL_LLM_BASE_URL"] == env["SKILL_EVAL_EMBEDDING_BASE_URL"] == live.HUB_BASE_URL


@pytest.mark.parametrize("latest_alias", [False, True])
def test_live_collection_is_bounded_and_retains_every_skill(live, repo, tmp_path, monkeypatch, latest_alias):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")
    monkeypatch.setenv("OPENAI_API_KEY", "UNRELATED-SECRET")
    monkeypatch.setenv("SKILL_EVAL_LLM_BASE_URL", "https://unintended.example")
    ambient_home = tmp_path / "ambient-home"
    ambient_home.mkdir()
    (ambient_home / ".docker").mkdir()
    (ambient_home / ".docker/config.json").write_text('{"auths":{"private":"UNRELATED-SECRET"}}')
    monkeypatch.setenv("HOME", str(ambient_home))
    calls = []

    def execute(argv, cwd, env, log, timeout):
        calls.append((argv, timeout))
        child_home = Path(env["HOME"])
        assert child_home.is_dir() and not child_home.is_relative_to(repo)
        assert child_home != ambient_home and child_home.stat().st_mode & 0o777 == 0o700
        assert not (child_home / ".docker/config.json").exists()
        assert "OPENAI_API_KEY" not in env and "NVIDIA_API_KEY" not in env
        assert env["SKILL_EVAL_LLM_BASE_URL"] == live.HUB_BASE_URL
        assert env["SKILL_EVAL_EMBEDDING_BASE_URL"] == live.HUB_BASE_URL
        assert env["SKILL_EVAL_LLM_API_KEY"] == "test-credential"
        assert env["SKILL_EVAL_EMBEDDING_API_KEY"] == "test-credential"
        assert env["SKILL_EVAL_LLM_PROVIDER"] == "openai-compatible"
        assert env["SKILL_EVAL_EMBEDDING_PROVIDER"] == "openai-compatible"
        assert "--autopilot" not in argv and "--skip-baseline" not in argv and "--copy-repo" not in argv
        if argv[1:3] == ["tier3", "validate"]:
            return 0, None
        if "evaluate" in argv:
            assert argv[argv.index("--n-attempts") + 1] == "1"
            assert argv[argv.index("--agent-model") + 1] == "opencode=openai/fixture/chat"
            path = Path(argv[argv.index("--results-dir") + 1]) / "eval-author/run/result.json"
            data = tier3()
        else:
            path = Path(argv[-1]) / "result.json"
            data = tier2("Similarity Check" if argv[1] == "similarity-check" else "Context Deduplication")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        if "evaluate" in argv and latest_alias:
            (path.parent.parent / "latest").symlink_to(path.parent.name, target_is_directory=True)
        return 0, None

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "live", run=True)
    assert report["counts"] == {"passed": 3, "skipped": 1, "completed": 1}
    assert len(calls) == 5
    policy = json.loads((tmp_path / "live/live-skillevaluator-summary.json").read_text())["policy"]
    assert policy["tier3_timeout_seconds"] == 2700
    assert policy["tier2_timeout_seconds"] == 300
    assert [timeout for argv, timeout in calls if argv[1:3] == ["tier3", "evaluate"]] == [
        policy["tier3_timeout_seconds"]
    ]
    assert [timeout for argv, timeout in calls if argv[1:3] == ["tier3", "validate"]] == [60]
    assert [timeout for argv, timeout in calls if argv[1] == "context-optimization-check"] == [300, 300]
    assert [timeout for argv, timeout in calls if argv[1] == "similarity-check"] == [300]
    assert "PRIVATE-SENTINEL" not in (tmp_path / "live/live-skillevaluator-summary.json").read_text()
    assert "correctness" in (tmp_path / "live/live-skillevaluator-summary.md").read_text()
    with pytest.raises(FileExistsError):
        live.collect(repo, tmp_path / "live", run=True)


@pytest.mark.parametrize("layout", ["two_runs", "linked_run", "linked_report"])
def test_ambiguous_or_linked_tier3_reports_are_not_published(live, repo, tmp_path, monkeypatch, layout):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")

    def execute(argv, cwd, env, log, timeout):
        if "evaluate" in argv:
            root = Path(argv[argv.index("--results-dir") + 1]) / "eval-author"
            root.mkdir(parents=True)
            external = tmp_path / "external"
            external.mkdir()
            (external / "result.json").write_text(json.dumps(tier3()))
            if layout == "linked_run":
                (root / "run").symlink_to(external, target_is_directory=True)
            else:
                run = root / "run"
                run.mkdir()
                if layout == "linked_report":
                    (run / "result.json").symlink_to(external / "result.json")
                else:
                    (run / "result.json").write_text(json.dumps(tier3()))
                    (root / "second-run").mkdir()
                    (root / "second-run/result.json").write_text(json.dumps(tier3()))
        return 0, None

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "live", run=True, tier="3", skill_name="eval-author")
    row = next(row for row in report["observations"] if row["tier"] == 3 and row["skill"] == "eval-author")
    assert row["status"] == "incomplete"
    assert row["reason"] == "report_missing_or_ambiguous"
    assert "arms" not in row


def test_timeouts_and_bad_reports_are_incomplete_and_do_not_stop_collection(live, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")

    def execute(argv, cwd, env, log, timeout):
        if argv[1] == "similarity-check":
            path = Path(argv[-1]) / "result.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"results": [null]}')
            return 0, None
        return -9, "timeout"

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "broken", run=True, tier="2")
    assert report["counts"] == {"incomplete": 3, "skipped": 2}


def test_input_mutation_invalidates_evidence_and_stops_further_calls(live, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "test-credential")
    calls = []

    def execute(*args):
        calls.append(args)
        (repo / "skills/eval-author/SKILL.md").write_text("changed")
        return -9, "timeout"

    monkeypatch.setattr(live, "execute", execute)
    report = live.collect(repo, tmp_path / "mutated", run=True)
    assert len(calls) == 1
    assert all(r["reason"] in ("skill_changed_during_run", "missing_dataset") for r in report["observations"])
    with pytest.raises(ValueError, match="clean"):
        live.collect(repo, tmp_path / "dirty", run=True)


def test_live_workflow_limits_inference_to_main_and_is_advisory():
    workflow = yaml.safe_load((ROOT / ".github/workflows/skill-evaluation-live.yml").read_text())
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert set(workflow[True]) == {"push", "pull_request", "workflow_dispatch"}
    for event in ("push", "pull_request"):
        assert workflow[True][event] == ci[True][event]
    assert workflow[True]["workflow_dispatch"]["inputs"]["run_live"]["default"] is False
    assert workflow[True]["workflow_dispatch"]["inputs"]["tier"]["default"] == "both"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["concurrency"]["group"] == (
        "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
    )
    plan = workflow["jobs"]["plan"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in plan["if"]
    assert "environment" not in plan and "secrets." not in json.dumps(plan)
    authorization = workflow["jobs"]["authorize"]
    job = workflow["jobs"]["evaluate"]
    assert {name: config["timeout-minutes"] for name, config in workflow["jobs"].items()} == {
        "plan": 5,
        "authorize": 5,
        "evaluate": 60,
        "aggregate": 10,
    }
    assert all(
        guard in authorization["if"]
        for guard in ("refs/heads/main", "SKILL_EVALUATION_LIVE_ENABLED", "inputs.run_live", "workflow_dispatch")
    )
    assert job["environment"] == "skill-evaluator" and job["continue-on-error"]
    assert "pull_request" not in authorization["if"]
    assert "github.ref == 'refs/heads/main' &&" in authorization["if"]
    assert not ci["jobs"]["test"].get("needs")
    upload = workflow["jobs"]["aggregate"]["steps"][-1]
    assert upload["if"] == "always()" and upload["with"]["path"].endswith("live-skillevaluator-summary.*")
    assert workflow["permissions"] == {"contents": "read"}
    assert "provider" not in workflow[True]["workflow_dispatch"]["inputs"]
    evaluation = next(step for step in job["steps"] if step.get("name") == "Run exactly one assigned check")
    # Installation/checksum failures must prevent secret-bearing execution.
    assert evaluation["if"] == "matrix.id != 'none'"  # Implicit success() gate remains.
    assert evaluation["env"]["INFERENCE_HUB_API_KEY"] == "${{ secrets.INFERENCE_HUB_API_KEY }}"
    assert workflow["env"]["EVALUATION_TIER"] == (
        "${{ github.event_name == 'pull_request' && '2' || inputs.tier || 'both' }}"
    )
    # Plan and execution must inherit the same policy; no step/job override may
    # accidentally re-enable Tier 3 on PRs.
    for configured_job in workflow["jobs"].values():
        assert "EVALUATION_TIER" not in configured_job.get("env", {})
        for step in configured_job["steps"]:
            assert "EVALUATION_TIER" not in step.get("env", {})
    compose = next(step for step in job["steps"] if step.get("name") == "Install verified Docker Compose runtime")
    assert compose["if"] == "matrix.check == 'live' && matrix.id != 'none'"
    assert workflow["env"]["EVALUATION_SKILL"] == "${{ inputs.skill || 'all' }}"
    assert not {"OPENAI_API_KEY", "NVIDIA_API_KEY", "HUB_METADATA_API_KEY"}.intersection(evaluation["env"])


def test_seed_datasets_are_bounded_and_have_negative_cases(live):
    for path in (
        "skills/eval-author",
        "skills/eval-author-first-eval",
        "skills/mlflow-to-atif",
        "skills/eval-author-task-create",
        "skills/eval-author-environment",
        "skills/eval-author-rubric",
        "team-skills/ethos/ethos-orchestrate",
        "team-skills/ethos/ethos-explore",
        "team-skills/ethos/ethos-write",
    ):
        skill = ROOT / path
        expected_count = 6 if skill.name in ("eval-author", "eval-author-first-eval", "eval-author-rubric") else 4
        assert live.dataset_cases(skill) == expected_count
        cases = json.loads((skill / "evals/evals.json").read_text())["evals"]
        expected_negative_count = 3 if skill.name == "eval-author-rubric" else 1
        assert sum(c["expected_skill"] is None for c in cases) == expected_negative_count
        for case in cases:
            for filename in case["files"]:
                assert (skill / "evals" / filename).is_file()


@pytest.mark.parametrize("count", [0, 1, 4, 6, 7])
def test_dataset_case_limit(live, repo, count):
    skill = repo / "skills/eval-author"
    path = skill / "evals/evals.json"
    data = json.loads(path.read_text())
    data["evals"] = [{"id": str(i), "prompt": "Task", "expected_output": "Answer"} for i in range(count)]
    path.write_text(json.dumps(data))
    if 1 <= count <= 6:
        assert live.dataset_cases(skill) == count
    else:
        with pytest.raises(ValueError, match="expected one to six authored cases"):
            live.dataset_cases(skill)


@pytest.mark.parametrize("failure", ["findings", "nested_execution", "timeout", "validation", "missing_report"])
def test_failure_details_survive_in_artifact_summary_and_console(live, repo, tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "fixture-secret-123")

    def execute(argv, cwd, env, log, timeout):
        log.write_text("Actionable failure: connection reset; Bearer fixture-secret-123\n")
        if "validate" in argv:
            return (1 if failure == "validation" else 0), None
        if failure == "timeout":
            return -9, "timeout"
        if failure == "missing_report":
            return 1, None
        if failure == "findings":
            data = tier2()
            row = data["results"][0]
            row.update(status="failed", passed=False)
            row["summary"].update(errors=1, high_count=1)
            row["findings"] = [
                {
                    "check_name": "duplicate",
                    "severity": "high",
                    "file_path": "SKILL.md",
                    "line_number": 42,
                    "message": "Repeated routing instructions <script>bad</script>",
                    "details": "Authorization: Bearer fixture-secret-123",
                    "unrelated_payload": "DO-NOT-EXPORT",
                }
            ]
            path = Path(argv[-1]) / "result.json"
        else:
            data = tier3()
            data["execution_status"] = "failed"
            arm = data["agents"]["opencode"]["conditions"]["without_skill"]
            arm.update(
                execution_status="failed",
                scored_attempts=0,
                execution_errors=["GraderTimeoutError: case one; token=other-secret"],
            )
            path = Path(argv[argv.index("--results-dir") + 1]) / "eval-author/run/result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        return 1, None

    monkeypatch.setattr(live, "execute", execute)
    output = tmp_path / "diagnostics"
    report = live.collect(repo, output, run=True, tier="2" if failure == "findings" else "3", skill_name="eval-author")
    row = next(r for r in report["observations"] if r["skill"] == "eval-author" and r["status"] != "skipped")
    published = (output / "live-skillevaluator-summary.json").read_text()
    markdown = (output / "live-skillevaluator-summary.md").read_text()
    console = capsys.readouterr().out
    for text in (published, markdown, console):
        assert "fixture-secret-123" not in text and "other-secret" not in text
        assert "DO-NOT-EXPORT" not in text
        assert "PRIVATE-SENTINEL" not in text
        assert "diagnostics" in text
    if failure == "findings":
        assert "Repeated routing instructions" in published and "SKILL.md" in published
        assert "<script>" not in markdown
        assert row["status"] == "failed"
    elif failure == "nested_execution":
        assert "GraderTimeoutError" in published and "without_skill" in published
        assert "scored_attempts: 0" in published
    else:
        assert "connection reset" in published
        assert row["status"] == "incomplete"


def test_context_guard_metadata_survives_collection(live, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", "fixture-only")
    metadata = {"pair_count": 130816, "vector_dimension": 2294, "scalar_work": 300091904, "limit": 300000000}

    def execute(argv, cwd, env, log, timeout):
        assert timeout == 300
        assert Path(argv[1]).name == "run_pinned_evaluator.py"
        data = tier2()
        row = data["results"][0]
        row.update(status="failed", passed=False)
        row["summary"].update(errors=1, critical_count=1)
        row["findings"] = [
            {
                "check_name": "scalar_comparison_limit",
                "severity": "critical",
                "metadata": {**metadata, "provider_payload": "DO-NOT-EXPORT"},
            }
        ]
        path = Path(argv[-1]) / "result.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        return 1, None

    monkeypatch.setattr(live, "execute", execute)
    output = tmp_path / "guard"
    report = live.collect(
        repo, output, run=True, tier="2", skill_name="eval-author", check_name="context", ci_context_budget=True
    )
    assert report["policy"]["context_comparison_policy"] == "full-pairwise-300m-v1"
    assert report["policy"]["context_scalar_limit"] == 300000000
    assert report["policy"]["tier2_timeout_seconds"] == 300
    row = next(r for r in report["observations"] if r["status"] != "skipped")
    assert row["status"] == "incomplete" and row["reason"] == "analysis_incomplete"
    for text in (
        (output / "live-skillevaluator-summary.json").read_text(),
        (output / "live-skillevaluator-summary.md").read_text(),
        capsys.readouterr().out,
    ):
        assert "DO-NOT-EXPORT" not in text
        for field, value in metadata.items():
            assert field in text and str(value) in text


@pytest.mark.parametrize(
    "check,metadata,expected",
    [
        (
            "scalar_comparison_limit",
            {"pair_count": 0, "vector_dimension": 65536, "scalar_work": 0, "limit": 1},
            {"pair_count": 0, "vector_dimension": 65536, "scalar_work": 0, "limit": 1},
        ),
        ("chunk_count_limit", {"actual": 513, "limit": 512, "source": "PRIVATE"}, {"actual": 513, "limit": 512}),
        ("chunk_count_limit", {"actual": 65537, "limit": 513}, {}),
        ("scalar_comparison_limit", {"vector_dimension": 0, "limit": 0}, {}),
        ("scalar_comparison_limit", {"vector_dimension": 2048, "scalar_work": "PRIVATE"}, {"vector_dimension": 2048}),
        (
            "scalar_comparison_limit",
            {"pair_count": 130816, "scalar_work": 130816 * 65536},
            {"pair_count": 130816, "scalar_work": 130816 * 65536},
        ),
        ("duplicate", {"pair_count": 1, "limit": 1}, {}),
        ("scalar_comparison_limit", None, {}),
        ("scalar_comparison_limit", [1, 2], {}),
        (
            "scalar_comparison_limit",
            {"pair_count": 130817, "vector_dimension": 65537, "scalar_work": 130816 * 65536 + 1, "limit": 300000001},
            {},
        ),
    ],
)
def test_diagnostics_only_export_observed_bounded_guard_metadata(live, check, metadata, expected):
    text = live.diagnostics({"results": [{"findings": [{"check_name": check, "metadata": metadata}]}]}, None)
    finding = json.loads(text.split(": ", 1)[1])
    assert finding == {"check_name": check, **({"metadata": expected} if expected else {})}


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "2048", {"secret": 1}, [1], None])
def test_diagnostics_reject_non_integer_or_negative_guard_metadata(live, value):
    metadata = dict.fromkeys(("pair_count", "vector_dimension", "scalar_work", "limit", "actual"), value)
    for check in ("scalar_comparison_limit", "chunk_count_limit"):
        text = live.diagnostics({"results": [{"findings": [{"check_name": check, "metadata": metadata}]}]}, None)
        assert json.loads(text.split(": ", 1)[1]) == {"check_name": check}


def test_diagnostics_redact_before_truncation_and_ignore_linked_logs(live, tmp_path, monkeypatch):
    import live_evaluation_diagnostics as diagnostic

    secret = 'fixture-secret-"unicode-\u2603'
    monkeypatch.setenv("INFERENCE_HUB_API_KEY", secret)
    report = {"execution_errors": ["x" * 11995 + secret + " trailing error"]}
    result = diagnostic.diagnostics(report, None)
    assert "fixture-secret" not in result and "[diagnostics truncated]" in result
    outside = tmp_path / "outside.log"
    outside.write_text("DO-NOT-EXPORT")
    target = tmp_path / "check"
    target.mkdir()
    (target / "console.log").symlink_to(outside)
    assert "DO-NOT-EXPORT" not in diagnostic.diagnostics({}, target)
    text = diagnostic.redact('password="unknown secret" https://user:pass@example.com/?key=xyz Bearer unknown-token')
    assert all(value not in text for value in ("unknown secret", "user:pass", "xyz", "unknown-token"))


def test_progress_heartbeat_does_not_stream_raw_logs(live, tmp_path, monkeypatch, capsys):
    class Event:
        calls = 0

        def wait(self, seconds):
            assert seconds == 30
            self.calls += 1
            return self.calls > 1

        def set(self):
            pass

    monkeypatch.setattr(live.threading, "Event", Event)

    # Use a synchronous thread double to make the heartbeat deterministic.
    class Thread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

        def join(self):
            pass

    monkeypatch.setattr(live.threading, "Thread", Thread)
    monkeypatch.setattr(live, "execute", lambda *args: (1, None))
    assert live.execute_with_progress([], tmp_path, {}, tmp_path / "console.log", 1800) == (1, None)
    text = capsys.readouterr().out
    assert "starting" in text and "running" in text and "finished" in text and "1800s" in text


@pytest.mark.parametrize("fixture,exit_code", [("valid.json", 0), ("missing-instruction.json", 1)])
def test_converter_seed_evidence_matches_expected_behavior(tmp_path, fixture, exit_code):
    skill = ROOT / "skills/mlflow-to-atif"
    output = tmp_path / "converted"
    result = subprocess.run(
        [
            sys.executable,
            str(skill / "scripts/convert_mlflow_to_atif.py"),
            "--input",
            str(skill / "evals/files" / fixture),
            "--output-dir",
            str(output),
            "--agent-name",
            "fixture-agent",
            "--agent-version",
            "1.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == exit_code, result.stderr
    trajectories = list(output.glob("*.atif.json"))
    if exit_code:
        assert not trajectories
    else:
        assert len(trajectories) == 1
        assert json.loads(trajectories[0].read_text())["schema_version"] == "ATIF-v1.7"
        assert output.stat().st_mode & 0o777 == 0o700
        assert trajectories[0].stat().st_mode & 0o777 == 0o600
