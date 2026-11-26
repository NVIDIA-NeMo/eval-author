# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from metric_benchmark import recovery

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "labs/metric-benchmark/recovery_trials.py"
CATALOG = ROOT / "skills/eval-author-environment/scenarios/recovery.json"
SCENARIO_IDS = (
    "timeout",
    "rate-limit",
    "partial-result",
    "stale-result",
    "prerequisite-dependency",
    "distinguishable-distractor",
)
EXPECTED_TOTALS = {"invoice-total": 1262, "stock-total": 171}


@pytest.fixture(scope="module")
def scenarios() -> dict[str, recovery.Scenario]:
    return {scenario.id: scenario for scenario in recovery.load_catalog(CATALOG)}


@pytest.mark.parametrize("task", EXPECTED_TOTALS)
@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
@pytest.mark.parametrize("severity", [1, 2, 3])
def test_recovery_and_naive_paired_outcomes(
    scenarios: dict[str, recovery.Scenario], task: str, scenario_id: str, severity: int
) -> None:
    scenario = replace(scenarios[scenario_id], severity=severity)
    results = {
        (target, arm): recovery.trial(task, scenario, arm, 17, recovery.Budget(), target, [])
        for target in ("reference", "naive")
        for arm in ("baseline", "perturbed")
    }
    for (target, arm), result in results.items():
        assert result["status"] == "completed", result["error"]
        assert result["task_passed"] is (target == "reference" or arm == "baseline")
        assert result["evidence_kind"] == "scripted-control"
        assert result["initial_state"]["submission"] is None
        assert result["initial_state"]["events"] == []
        assert result["initial_state"]["source_records"] == result["final_state"]["source_records"]
        if result["task_passed"]:
            assert result["final_state"]["submission"] == {"total": EXPECTED_TOTALS[task]}
        if arm == "perturbed":
            assert result["observation"]["perturbation_fired"] is True
            assert result["observation"]["response_delivered"] is True
    for target in ("reference", "naive"):
        baseline, perturbed = results[target, "baseline"], results[target, "perturbed"]
        assert baseline["instruction"] == perturbed["instruction"]
        assert baseline["tools"] == perturbed["tools"]
    if severity == 1:
        assert results["naive", "perturbed"]["observation"]["observed_signature"] == scenario.failure_signature


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_replay_resets_events_submission_dependencies_and_clock(
    scenarios: dict[str, recovery.Scenario], scenario_id: str
) -> None:
    scenario = replace(scenarios[scenario_id], severity=3)
    first = recovery.trial("invoice-total", scenario, "perturbed", 17, recovery.Budget(), "reference", [])
    second = recovery.trial("invoice-total", scenario, "perturbed", 17, recovery.Budget(), "reference", [])
    assert first["final_state"]["submission"] == {"total": 1262}
    assert first["final_state"]["events"]
    assert second["initial_state"]["submission"] is None
    assert second["initial_state"]["events"] == []
    assert second["initial_state"]["logical_tick"] == 0
    assert second["initial_state"]["dependencies_remaining"] == 0
    assert second["initial_state"]["prepared"] is True
    first.pop("elapsed_seconds")
    second.pop("elapsed_seconds")
    assert second == first


def catalog_entry() -> dict[str, Any]:
    return {
        "id": "timeout",
        "category": "timeout",
        "severity": 1,
        "seed": 101,
        "trigger": {"tool": "search_records"},
        "schedule": [1],
        "expected_behavior": "Retry the timeout.",
        "failure_signature": "abandoned_after_tool_error",
        "provenance": "synthetic",
        "source_refs": [],
    }


def test_catalog_preserves_trace_source_references(tmp_path: Path) -> None:
    entry = catalog_entry()
    entry.update(provenance="trace-observed", source_refs=["trace-17/events/3", "trace-18/events/7"])
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    loaded = recovery.load_catalog(path)
    assert len(loaded) == 1
    assert loaded[0].id == "timeout"
    assert loaded[0].schedule == (1,)
    assert loaded[0].source_refs == ("trace-17/events/3", "trace-18/events/7")
    assert loaded[0].provenance == "trace-observed"


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../escape"},
        {"id": "/absolute"},
        {"id": "UPPERCASE"},
        {"id": "contains space"},
        {"id": ""},
        {"id": "a" * 65},
        {"unexpected": True},
        {"provenance": "trace-observed", "source_refs": []},
        {"source_refs": "trace-17"},
        {"source_refs": [""]},
        {"source_refs": ["   "]},
        {"source_refs": [17]},
    ],
)
def test_catalog_rejects_invalid_identifiers_fields_and_sources(tmp_path: Path, changes: dict[str, Any]) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([{**catalog_entry(), **changes}]), encoding="utf-8")
    with pytest.raises(ValueError):
        recovery.load_catalog(path)


@pytest.mark.parametrize("duplicate", ["scenario", "field"])
def test_catalog_rejects_duplicates(tmp_path: Path, duplicate: str) -> None:
    entry = catalog_entry()
    payload = (
        json.dumps([entry, entry]) if duplicate == "scenario" else "[" + json.dumps(entry)[:-1] + ', "seed": 999}]'
    )
    path = tmp_path / "catalog.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        recovery.load_catalog(path)


def cli(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture(scope="module")
def retained_comparison(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("recovery-export") / "comparison"
    result = cli(
        "--output",
        output,
        "--target",
        "naive",
        "--repetitions",
        "2",
        "--seed",
        "17",
        "--max-tool-calls",
        "24",
        "--max-turns",
        "26",
        "--wall-seconds",
        "10",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "output": str(output),
        "rows": 12,
        "target_trials": 48,
        "evidence_kind": "scripted-control",
    }
    return output


def test_cli_exports_paired_trials_with_fixed_budgets(retained_comparison: Path) -> None:
    inputs = json.loads((retained_comparison / "inputs.json").read_text())
    report = json.loads((retained_comparison / "difficulty-matrix.json").read_text())
    expected_budget = {"max_tool_calls": 24, "max_turns": 26, "wall_seconds": 10, "max_response_bytes": 65536}
    assert inputs["budget"] == expected_budget
    assert inputs["repetitions"] == 2
    assert inputs["target"] == "naive"
    assert inputs["combination_policy"] == "isolated-only"
    assert report["evidence_kind"] == "scripted-control"
    rows = report["difficulty_matrix"]
    assert len(rows) == 12
    assert {(row["task_id"], row["scenario_id"]) for row in rows} == {
        (task, scenario) for task in EXPECTED_TOTALS for scenario in SCENARIO_IDS
    }
    for row in rows:
        assert row["paired_trials"] == 2
        assert row["unmeasured_pairs"] == 0
        assert row["baseline_passes"] == 2
        assert row["perturbed_passes"] == 0
        assert row["pass_rate_difference"] == -1.0
        assert row["intended_failures_observed"] == 2
        assert row["rubric_score"] is None
        assert len(row["reference_controls"]) == 2
        for reference in row["reference_controls"]:
            control = json.loads((retained_comparison / reference["path"]).read_text())
            assert control["target"] == "reference"
            assert control["task_passed"] is True
            assert control["status"] == "completed"
            assert control["budget"] == expected_budget
        assert [pair["repetition"] for pair in row["pairs"]] == [0, 1]
        for pair in row["pairs"]:
            assert pair["seed"] == 17
            records = {
                arm: json.loads((retained_comparison / pair[arm]["path"]).read_text())
                for arm in ("baseline", "perturbed")
            }
            for arm, record in records.items():
                assert record["arm"] == arm
                assert record["target"] == "naive"
                assert record["budget"] == expected_budget
                assert record["seed"] == 17
                assert record["initial_state"]["events"] == []
                assert record["initial_state"]["submission"] is None
            assert records["baseline"]["instruction"] == records["perturbed"]["instruction"]
            assert records["baseline"]["tools"] == records["perturbed"]["tools"]
    verified = cli("--verify", "--output", retained_comparison)
    assert verified.returncode == 0, verified.stderr
    assert "retained comparison verified" in verified.stdout


@pytest.mark.parametrize("repetitions", ["0", "1"])
def test_cli_rejects_insufficient_repetitions(tmp_path: Path, repetitions: str) -> None:
    output = tmp_path / "comparison"
    result = cli("--output", output, "--repetitions", repetitions)
    assert result.returncode == 2
    assert "at least two repetitions" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation,diagnostic",
    [
        ("aggregate", "difficulty aggregate differs"),
        ("missing-row", "missing difficulty rows"),
        ("trial", "trial evidence changed"),
    ],
)
def test_retained_verification_rejects_edited_evidence(
    tmp_path: Path, retained_comparison: Path, mutation: str, diagnostic: str
) -> None:
    output = tmp_path / "comparison"
    shutil.copytree(retained_comparison, output)
    report_path = output / "difficulty-matrix.json"
    report = json.loads(report_path.read_text())
    if mutation == "aggregate":
        report["difficulty_matrix"][0]["baseline_passes"] = 0
        report_path.write_text(json.dumps(report), encoding="utf-8")
    elif mutation == "missing-row":
        report["difficulty_matrix"].pop()
        report_path.write_text(json.dumps(report), encoding="utf-8")
    else:
        trial_path = output / report["difficulty_matrix"][0]["pairs"][0]["baseline"]["path"]
        record = json.loads(trial_path.read_text())
        record["final_state"]["submission"]["total"] = -1
        trial_path.write_text(json.dumps(record), encoding="utf-8")
    result = cli("--verify", "--output", output)
    assert result.returncode == 2
    assert diagnostic in result.stderr


@pytest.fixture(scope="module")
def retained_recovery(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("recovery-reference") / "comparison"
    result = cli(
        "--output",
        output,
        "--target",
        "reference",
        "--task",
        "invoice-total",
        "--scenario",
        "timeout",
        "--repetitions",
        "2",
    )
    assert result.returncode == 0, result.stderr
    return output


@pytest.mark.parametrize(
    "field,value",
    [
        ("intended_failure_observed", True),
        ("observed_signature", "abandoned_after_tool_error"),
        ("perturbation_fired", False),
        ("response_delivered", False),
        ("fired_event_indices", []),
        ("delivered_event_indices", []),
    ],
)
def test_verification_recomputes_coordinated_observation_edits(
    tmp_path: Path, retained_recovery: Path, field: str, value: Any
) -> None:
    output = tmp_path / "comparison"
    shutil.copytree(retained_recovery, output)
    report_path = output / "difficulty-matrix.json"
    report = json.loads(report_path.read_text())
    row = report["difficulty_matrix"][0]
    ref = row["pairs"][0]["perturbed"]
    path = output / ref["path"]
    record = json.loads(path.read_text())
    assert record["task_passed"] is True
    record["observation"][field] = value
    ref["observation"] = record["observation"]
    row["intended_failures_observed"] = int(record["observation"]["intended_failure_observed"] is True)
    path.write_text(json.dumps(record), encoding="utf-8")
    ref["sha256"] = recovery.digest_file(path)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="observation"):
        recovery.verify(output)


@pytest.mark.parametrize(
    "mutation",
    [
        "response",
        "fired",
        "call-index",
        "event-clock",
        "final-clock",
        "history",
        "tool-calls",
        "turns",
    ],
)
def test_verification_replays_coordinated_event_edits(
    tmp_path: Path, retained_recovery: Path, scenarios: dict[str, recovery.Scenario], mutation: str
) -> None:
    output = tmp_path / "comparison"
    shutil.copytree(retained_recovery, output)
    report_path = output / "difficulty-matrix.json"
    report = json.loads(report_path.read_text())
    row = report["difficulty_matrix"][0]
    ref = row["pairs"][0]["perturbed"]
    path = output / ref["path"]
    record = json.loads(path.read_text())
    event = record["final_state"]["events"][0]
    if mutation == "response":
        event["response"]["error"]["code"] = "invented_error"
        record["history"][0]["result"] = event["response"]
    elif mutation == "fired":
        event["perturbation_applied"] = False
    elif mutation == "call-index":
        event["call_index"] = 99
    elif mutation == "event-clock":
        event["logical_tick"] = 99
    elif mutation == "final-clock":
        record["final_state"]["logical_tick"] = 99
    elif mutation == "history":
        record["history"].pop()
    else:
        record["tool_calls" if mutation == "tool-calls" else "turns"] += 1
    record["observation"] = recovery.retained_failure_observation(
        scenarios["timeout"], record["final_state"], True, record["status"]
    )
    ref["observation"] = record["observation"]
    path.write_text(json.dumps(record), encoding="utf-8")
    ref["sha256"] = recovery.digest_file(path)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="replay|history|counter"):
        recovery.verify(output)


@pytest.mark.parametrize("alias", ["dot", "parent", "directory-symlink", "copy"])
def test_verification_rejects_reused_repetition_evidence(tmp_path: Path, retained_comparison: Path, alias: str) -> None:
    output = tmp_path / "comparison"
    shutil.copytree(retained_comparison, output)
    report_path = output / "difficulty-matrix.json"
    report = json.loads(report_path.read_text())
    pairs = report["difficulty_matrix"][0]["pairs"]
    first = pairs[0]["baseline"]
    replacement = dict(first)
    if alias == "dot":
        replacement["path"] = "./" + first["path"]
    elif alias == "parent":
        replacement["path"] = "trials/../" + first["path"]
    elif alias == "directory-symlink":
        (output / "alias").symlink_to(output / "trials", target_is_directory=True)
        replacement["path"] = first["path"].replace("trials/", "alias/", 1)
    else:
        replacement["path"] = pairs[1]["baseline"]["path"]
        shutil.copyfile(output / first["path"], output / replacement["path"])
    pairs[1]["baseline"] = replacement
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate|repetition"):
        recovery.verify(output)


@pytest.mark.parametrize(
    "mutation",
    [
        "report-kind",
        "trial-kind",
        "control-kind",
        "target",
        "category",
        "severity",
        "provenance",
        "scenario-trigger",
        "scenario-schedule",
        "scenario-seed",
        "scenario-behavior",
        "scenario-signature",
        "scenario-sources",
        "scenario-severity",
        "initial-scenario",
        "final-scenario",
        "control-repetition",
        "missing-repetition",
        "boolean-repetition",
    ],
)
def test_verification_binds_metadata_to_inputs(tmp_path: Path, retained_comparison: Path, mutation: str) -> None:
    output = tmp_path / "comparison"
    shutil.copytree(retained_comparison, output)
    report_path = output / "difficulty-matrix.json"
    report = json.loads(report_path.read_text())
    row = report["difficulty_matrix"][0]
    ref = row["reference_controls"][0] if mutation.startswith("control-") else row["pairs"][0]["perturbed"]
    path = output / ref["path"]
    record = json.loads(path.read_text())
    if mutation == "report-kind":
        report["evidence_kind"] = "external-agent"
    elif mutation in {"trial-kind", "control-kind"}:
        record["evidence_kind"] = "external-agent"
    elif mutation == "target":
        inputs_path = output / "inputs.json"
        inputs = json.loads(inputs_path.read_text())
        inputs["target"] = "unknown"
        inputs_path.write_text(json.dumps(inputs), encoding="utf-8")
        report["inputs_sha256"] = recovery.digest_file(inputs_path)
    elif mutation in {"category", "severity", "provenance"}:
        row[mutation] = {"category": "stale-result", "severity": 3, "provenance": "trace-observed"}[mutation]
    elif mutation.startswith("scenario-"):
        field, value = {
            "scenario-trigger": ("trigger", {"tool": "read_record"}),
            "scenario-schedule": ("schedule", [2]),
            "scenario-seed": ("seed", 999),
            "scenario-behavior": ("expected_behavior", "Different behavior."),
            "scenario-signature": ("failure_signature", "different_signature"),
            "scenario-sources": ("source_refs", ["another-trace"]),
            "scenario-severity": ("severity", True),
        }[mutation]
        record["scenario"][field] = value
    elif mutation in {"initial-scenario", "final-scenario"}:
        record[mutation.split("-")[0] + "_state"]["scenario"]["seed"] = 999
    elif mutation == "missing-repetition":
        record.pop("repetition")
    else:
        record["repetition"] = 0 if mutation == "control-repetition" else False
    path.write_text(json.dumps(record), encoding="utf-8")
    ref["sha256"] = recovery.digest_file(path)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        recovery.verify(output)


@pytest.mark.parametrize("arm,target", [("typo", "reference"), ("perturbed", "typo")])
def test_trial_rejects_invalid_selection_before_execution(
    scenarios: dict[str, recovery.Scenario], monkeypatch: pytest.MonkeyPatch, arm: str, target: str
) -> None:
    def unexpected_environment(*args: Any) -> None:
        pytest.fail("invalid trial selection reached environment execution")

    monkeypatch.setattr(recovery, "Environment", unexpected_environment)
    with pytest.raises(ValueError, match="arm|target"):
        recovery.trial("invoice-total", scenarios["timeout"], arm, 17, recovery.Budget(), target, [])


@pytest.mark.parametrize("category", ["timeout", "rate-limit"])
@pytest.mark.parametrize("ending", ["fault", "unrelated-error", "undelivered"])
def test_abandonment_matches_the_delivered_terminal_fault(
    scenarios: dict[str, recovery.Scenario], category: str, ending: str
) -> None:
    scenario = replace(scenarios[category], trigger={"tool": "read_record"}, schedule=(2,))
    session = recovery.Session(recovery.Environment("invoice-total", scenario, 17), recovery.Budget())
    session.call("search_records", {"page": 1})
    session.call("read_record", {"id": "record-1"})
    if ending == "unrelated-error":
        session.call("unknown_tool", {})
    elif ending == "undelivered":
        session.environment.events[-1]["response_delivered"] = False
    observation = recovery.failure_observation(scenario, session, False)
    assert observation["observed_signature"] == ("abandoned_after_tool_error" if ending == "fault" else None)
    assert observation["intended_failure_observed"] is (None if ending == "undelivered" else ending == "fault")


@pytest.mark.parametrize("read", ["unrelated", "failed", "affected", "undelivered", "after-submit", "none"])
@pytest.mark.parametrize("total", ["stale", "unrelated"])
def test_stale_failure_requires_missing_authority_on_affected_records(
    scenarios: dict[str, recovery.Scenario], read: str, total: str
) -> None:
    scenario = scenarios["stale-result"]
    session = recovery.Session(recovery.Environment("invoice-total", scenario, 17), recovery.Budget())
    response = session.call("search_records", {"page": 1})
    affected = next(row["id"] for row in response["records"] if row["version"] == 1)
    unrelated = next(row["id"] for row in response["records"] if row["version"] == 2)
    if read not in {"none", "after-submit"}:
        session.call(
            "read_record", {"id": unrelated if read == "unrelated" else "missing" if read == "failed" else affected}
        )
        if read == "undelivered":
            session.environment.events[-1]["response_delivered"] = False
    session.call("submit", {"total": sum(row["value"] for row in response["records"]) if total == "stale" else -1})
    if read == "after-submit":
        session.call("read_record", {"id": affected})
    observation = recovery.failure_observation(scenario, session, False)
    expected = total == "stale" and read != "affected"
    assert observation["intended_failure_observed"] is expected
    assert observation["observed_signature"] == ("submitted_without_authoritative_read" if expected else None)


def scripted_command(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "adapter.py"
    path.write_text("import json, sys, time\nrequest = json.load(sys.stdin)\n" + body, encoding="utf-8")
    return [sys.executable, str(path)]


def test_replay_preserves_delivery_claim_without_inferring_attendance(
    scenarios: dict[str, recovery.Scenario],
) -> None:
    scenario = scenarios["timeout"]
    record = recovery.trial("invoice-total", scenario, "perturbed", 17, recovery.Budget(), "reference", [])
    record["final_state"]["events"][0]["response_delivered"] = False
    recovery.verify_trial_replay(record, scenario)
    observation = recovery.retained_failure_observation(scenario, record["final_state"], True)
    assert observation["response_delivered"] is False
    assert observation["intended_failure_observed"] is None


def test_replay_accepts_wall_deadline_between_execution_and_history(
    scenarios: dict[str, recovery.Scenario],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remaining = recovery.Session.remaining

    def expire_after_execution(session: recovery.Session) -> float:
        if session.environment.events:
            raise recovery.BudgetExceeded("wall_seconds")
        return remaining(session)

    monkeypatch.setattr(recovery.Session, "remaining", expire_after_execution)
    scenario = scenarios["timeout"]
    record = recovery.trial("invoice-total", scenario, "perturbed", 17, recovery.Budget(), "reference", [])
    assert record["history"] == []
    assert record["tool_calls"] == 1
    assert record["status"] == "budget_exhausted"
    recovery.verify_trial_replay(record, scenario)


@pytest.mark.parametrize("mode", ["crash-first", "crash-after-tool", "last-turn", "finish"])
def test_external_delivery_requires_a_successful_followup_turn(
    tmp_path: Path, scenarios: dict[str, recovery.Scenario], mode: str
) -> None:
    seen = tmp_path / "requests.jsonl"
    body = (
        "from pathlib import Path\n"
        f"with Path({str(seen)!r}).open('a') as stream:\n"
        "    stream.write(json.dumps(request) + '\\n')\n"
    )
    if mode == "crash-first":
        body += "sys.exit(9)\n"
    else:
        body += (
            "if not request['history']:\n    print(json.dumps({'tool': 'search_records', 'arguments': {'page': 1}}))\n"
        )
        body += "else:\n    " + (
            "sys.exit(9)\n" if mode == "crash-after-tool" else "print(json.dumps({'finish': True}))\n"
        )
    result = recovery.trial(
        "invoice-total",
        scenarios["timeout"],
        "perturbed",
        17,
        recovery.Budget(max_turns=1 if mode == "last-turn" else 3),
        "external",
        scripted_command(tmp_path, body),
    )
    requests = [json.loads(line) for line in seen.read_text().splitlines()]
    recovery.verify_trial_replay(result, scenarios["timeout"])
    assert result["evidence_kind"] == "external-agent"
    assert requests[0]["history"] == []
    assert "source_records" not in requests[0]
    assert "scenario" not in requests[0]
    assert requests[0]["remaining_tool_calls"] == 48
    observation = result["observation"]
    assert observation["perturbation_fired"] is (mode != "crash-first")
    assert observation["response_delivered"] is (mode == "finish")
    assert result["tool_calls"] == (0 if mode == "crash-first" else 1)
    assert [event["response_delivered"] for event in result["final_state"]["events"]] == (
        [] if mode == "crash-first" else [mode == "finish"]
    )
    if mode in {"crash-first", "crash-after-tool"}:
        assert result["status"] == "execution_error"
        assert result["task_passed"] is None
    elif mode == "last-turn":
        assert result["status"] == "budget_exhausted"
        assert result["error"] == "max_turns"
        assert result["task_passed"] is False
    else:
        assert result["status"] == "completed"
        assert result["task_passed"] is False
        assert requests[1]["history"] == [
            {"tool": "search_records", "arguments": {"page": 1}, "result": {"ok": False, "error": {"code": "timeout"}}}
        ]
        assert requests[1]["remaining_tool_calls"] == 47
    assert observation["observed_signature"] == ("abandoned_after_tool_error" if mode == "finish" else None)
    assert observation["intended_failure_observed"] is (True if mode == "finish" else None)


@pytest.mark.parametrize("stream", ["stdout", "stderr", "combined"])
def test_external_output_limit_bounds_both_streams(
    tmp_path: Path, scenarios: dict[str, recovery.Scenario], stream: str
) -> None:
    streams = ("stdout", "stderr") if stream == "combined" else (stream,)
    size = 80 if stream == "combined" else 129
    body = "".join(f"sys.{name}.write('x' * {size})\nsys.{name}.flush()\n" for name in streams)
    body += "time.sleep(10)\n"
    result = recovery.trial(
        "invoice-total",
        scenarios["timeout"],
        "perturbed",
        17,
        recovery.Budget(wall_seconds=5, max_response_bytes=128),
        "external",
        scripted_command(tmp_path, body),
    )
    assert result["status"] == "execution_error"
    assert result["error"] == "RuntimeError"
    assert result["task_passed"] is None
    assert result["tool_calls"] == 0
    assert result["elapsed_seconds"] < 5
    assert result["observation"]["perturbation_fired"] is False


def test_external_wall_budget_terminates_a_sleeping_adapter(
    tmp_path: Path, scenarios: dict[str, recovery.Scenario]
) -> None:
    result = recovery.trial(
        "invoice-total",
        scenarios["timeout"],
        "perturbed",
        17,
        recovery.Budget(wall_seconds=1),
        "external",
        scripted_command(tmp_path, "time.sleep(10)\nprint(json.dumps({'finish': True}))\n"),
    )
    assert result["status"] == "budget_exhausted"
    assert result["error"] == "wall_seconds"
    assert result["task_passed"] is False
    assert result["tool_calls"] == 0
    assert result["elapsed_seconds"] < 5
    assert result["observation"]["intended_failure_observed"] is None
    recovery.verify_trial_replay(result, scenarios["timeout"])


@pytest.mark.parametrize(
    "budget,error",
    [
        (recovery.Budget(max_tool_calls=1), "max_tool_calls"),
        (recovery.Budget(max_turns=1), "max_turns"),
    ],
)
def test_reference_cannot_exceed_tool_or_turn_budget(
    scenarios: dict[str, recovery.Scenario], budget: recovery.Budget, error: str
) -> None:
    result = recovery.trial("invoice-total", scenarios["timeout"], "baseline", 17, budget, "reference", [])
    assert result["status"] == "budget_exhausted"
    assert result["error"] == error
    assert result["task_passed"] is False
    assert result["tool_calls"] == 1
    assert result["turns"] <= budget.max_turns
    assert result["final_state"]["submission"] is None
    recovery.verify_trial_replay(result, scenarios["timeout"])
