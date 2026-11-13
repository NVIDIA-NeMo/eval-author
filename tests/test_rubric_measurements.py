# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"


PANEL_RUNS = pytest.StashKey[tuple]()


@pytest.fixture(scope="session")
def rubric_panel_runs(tmp_path_factory, pytestconfig):
    if PANEL_RUNS in pytestconfig.stash:
        return pytestconfig.stash[PANEL_RUNS]
    home = tmp_path_factory.mktemp("rubric-panel-home")
    command = [sys.executable, "-B", str(ROOT / "tools/run_rubric_measurements.py")]
    runs = tuple(
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            cwd=home,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        for _ in range(2)
    )
    pytestconfig.stash[PANEL_RUNS] = runs
    return runs


@pytest.fixture(scope="session")
def rubric_panel(rubric_panel_runs):
    first, _ = rubric_panel_runs
    assert first.stderr == ""
    return json.loads(first.stdout)


@pytest.mark.parametrize("location", ["record", "evidence", "path", "unavailable-evidence"])
def test_cli_omits_invalid_artifact_payloads(tmp_path, monkeypatch, location):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, None)
    record = selection["artifacts"][0]
    marker = "PRIVATE-PAYLOAD-MUST-NOT-BE-EMITTED"
    if location == "record":
        record["private"] = marker
    elif location == "path":
        record["path"] = {"private": marker}
    else:
        record["evidence"][0]["private"] = marker
        if location == "unavailable-evidence":
            record["access"] = "missing"
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert marker not in result.stdout + result.stderr
    assert result.returncode == 1
    identity = json.loads(result.stdout)["measurements"]["identity"]
    assert identity["status"] == "fail"
    assert "task" not in {row["id"] for row in identity["records"]}
    assert len(identity["records"]) == len(selection["artifacts"]) - 1


def test_cli_root_alias_preserves_complete_assessment(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(fixture(root, None)))
    reports = []
    for path in (root, alias):
        result = subprocess.run(
            [sys.executable, "-B", str(CLI), "--root", str(path), "--selection", str(request)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert not result.stderr
        reports.append(json.loads(result.stdout))
    assert reports[0] == reports[1]


def test_measurement_cli_emits_unknowns_without_certifying_preflight(tmp_path):
    assert CLI.is_file(), "Rubric measurement integration is not implemented"
    request = tmp_path / "selection.json"
    request.write_text(
        json.dumps(
            {
                "artifacts": [],
                "route": None,
                "declared_config": {},
                "observed_config": {},
            }
        )
    )
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["schema"] == "rubric-measurements-v2"
    assert report["preflight_success"] is False
    assert all(row["score"] is None for row in report["items"].values())
    assert not any(key.startswith("ID-") for key in report["items"])
    assert len(report["measurements"]) == 5
    assert report["anti_cheat_scores"] is None


@pytest.mark.parametrize(
    "payload",
    [
        '{"unused":{"nested":[1e999]}}',
        '{"artifacts":[{"id":"artifact","extra":-1e999}]}',
        '{"declared_config":{"provider":1e999}}',
    ],
)
def test_cli_rejects_nonfinite_values_anywhere_without_traceback(tmp_path, payload):
    request = tmp_path / "selection.json"
    request.write_text(payload)
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "schema": "rubric-measurements-v2",
        "status": "invalid",
        "code": "selection.invalid",
        "preflight_success": False,
    }


def test_cli_sanitizes_output_serialization_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(CLI.parent))
    import measure

    request = tmp_path / "selection.json"
    request.write_text("{}")
    monkeypatch.setattr(measure, "measure", lambda root, selection, ethos_validator: {"unexpected": float("inf")})
    assert measure.main(["--root", str(tmp_path), "--selection", str(request)]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["code"] == "selection.invalid"


def test_mutation_panel_matches_independent_expectations(rubric_panel):
    report = rubric_panel
    assert report["status"] == "passed"
    assert report["measurement"] == "synthetic-local-mutations"
    assert report["counts"]["cases"] == len(report["cases"])
    assert all(row["matches_expectations"] for row in report["cases"])
    assert {
        "valid",
        "missing",
        "denied",
        "unresolved",
        "wrong-scorer",
        "config-difference",
        "valid-links",
        "broken-reference",
        "valid-approval",
        "stale-approval",
        "missing-decision",
    } <= {row["id"] for row in report["cases"]}


def test_demo_reports_canonical_outcomes_through_actual_cli(rubric_panel):
    report = rubric_panel
    assert len(report["cases"]) == 14
    expected = {
        "valid": (2, 2, 2),
        "wrong-scorer": (2, 0, 2),
        "ambiguous-route": (2, None, 2),
        "confirmed-missing": (0, 2, 2),
        "partial": (1, 1, 1),
    }
    supported = ("DISC-01-A", "DISC-01-B", "DISC-01-C")
    unsupported = {"DISC-02-A", "DISC-02-B", "DISC-03-A", "AUD-02", "ENV-01", "ENV-02-A", "ENV-03", "CREATE-07"}
    cases = {row["id"]: row for row in report["assessments"]}
    assert cases.keys() == expected.keys()
    assert report["counts"] == {"cases": 14, "assessments": len(expected), "evidence_cases": 14}
    assert len(report["evidence_cases"]) == 14
    assert {case["id"] for case in report["evidence_cases"]} == {
        "sourced-equal",
        "sourced-different",
        "sourced-provider-hidden",
        "fresh-bindings",
        "material-task-change",
        "material-scorer-change",
        "material-dependency-change",
        "unknown-ethos-change",
        "ethos-confirmed",
        "ethos-malformed",
        "ethos-changed-after-confirmation",
        "authority-resolved",
        "authority-broken-reference",
        "authority-prose",
    }
    assert all(case["matches_expectations"] for case in report["evidence_cases"])
    canonical = json.loads(
        (ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json").read_text()
    )
    check_ids = {check["id"] for item in canonical["items"] for check in item["checks"]}
    assert report["canonical_checks_per_case"] == len(check_ids)
    for case in report["evidence_cases"]:
        checks = [check for item in case["observed"]["items"].values() for check in item["check_results"]]
        assert {check["check_id"] for check in checks} == check_ids
        assert len(checks) == len(check_ids)
    observed_values = set()
    for ident, scores in expected.items():
        case = cases[ident]
        observed = case["observed"]
        items = observed["items"]
        assert observed["schema"] == "rubric-measurements-v2"
        assert tuple(items[key]["score"] for key in supported) == scores
        assert set(items) == set(supported) | unsupported
        assert case["matches_expectations"] is True
        assert case["cli_exit_code"] == (0 if ident == "valid" else 1)
        for key, row in items.items():
            observed_values.add(row["score"])
            assert row["status"] == ("NOT_ASSESSED" if row["score"] is None else "ASSESSED")
            assert row["limitations"]
            if key in unsupported:
                assert row["score"] is None
            if row["score"] is not None:
                assert type(row["score"]) is int
                assert row["evidence_strength"] == "E2"
                assert row["evidence_refs"]
        assert observed["preflight_success"] is False
        assert observed["anti_cheat_scores"] is None
        assert observed["measurements"]["route"]["executed_scorer"] is None
    assert observed_values == {0, 1, 2, None}
    assert report["native_execution"] is False
    assert report["agent_exploitation"] is False
    assert report["workflow_integration"] is False


def test_demo_text_summary_is_concise_and_keeps_limits(rubric_panel, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import summary

    result = subprocess.CompletedProcess([], 0, stdout=summary(rubric_panel))
    assert result.stdout.startswith("Rubric handcrafted assessment demo:")
    assert "DISC-01-A" in result.stdout
    assert "DISC-01-B" in result.stdout
    assert "DISC-01-C" in result.stdout
    if rubric_panel["status"] == "passed":
        assert "weak-route defect, not demonstrated exploitation" in result.stdout
    else:
        assert "Mismatched cases" in result.stdout
    assert "null = NOT_ASSESSED" in result.stdout
    assert "No native execution" in result.stdout
    assert len(result.stdout.splitlines()) < 20


def test_demo_output_is_byte_identical_across_runs(rubric_panel_runs):
    first, second = rubric_panel_runs
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == ""


@pytest.mark.parametrize("panel", ["cases", "assessments", "evidence_cases", "package_acceptance"])
def test_demo_summary_reports_mismatches_without_success_claims(rubric_panel, monkeypatch, panel):
    from copy import deepcopy

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import summary

    report = deepcopy(rubric_panel)
    rows = report[panel]["cases"] if panel == "package_acceptance" else report[panel]
    rows[0]["matches_expectations"] = False
    rows[-1]["matches_expectations"] = False
    report["status"] = "failed"
    text = summary(report)
    assert f"Mismatched cases ({panel}): {rows[0]['id']}, {rows[-1]['id']}" in text
    claims = {
        "cases": "wrong routes cannot succeed",
        "assessments": "Scores are bounded 0/1/2",
        "evidence_cases": "neighbor stays current",
        "package_acceptance": "wrong routes cannot succeed",
    }
    assert claims[panel] not in text


def test_demo_isolates_every_cli_call(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import run_rubric_measurements as demo

    real_run = subprocess.run
    calls = []
    monkeypatch.setenv("DEMO_PRIVATE_CREDENTIAL", "must-not-leak")

    def isolated_run(command, **kwargs):
        assert "-B" in command
        assert 0 < kwargs["timeout"] <= 30
        assert kwargs["env"] == {
            "HOME": str(kwargs["cwd"]),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        assert Path(kwargs["env"]["HOME"]).is_dir()
        calls.append(command)
        return real_run(command, **kwargs)

    real_loads = demo.loads

    def selected_cases(value):
        data = real_loads(value)
        if "raw_measurements" in data:
            data["raw_measurements"] = []
            data["assessments"] = data["assessments"][:1]
            data["evidence_cases"] = data["evidence_cases"][:1]
            data["package_acceptance"] = []
        return data

    monkeypatch.setattr(demo, "loads", selected_cases)
    monkeypatch.setattr(demo.subprocess, "run", isolated_run)
    demo.run_panel()
    assert len(calls) == 2


@pytest.mark.parametrize("mutation", [None, "wrong-scorer", "partial", "missing-dependency"])
def test_demo_route_dependencies_match_retained_dispatch(tmp_path, monkeypatch, mutation):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, mutation)
    entry = next(row for row in selection["artifacts"] if row["id"] == "entry")
    retained = json.loads((tmp_path / entry["path"]).read_text())
    assert retained.get("dependencies") == selection["route"]["dependencies"]
    assert retained.get("expected_scorer") == selection["route"]["expected_scorer"]


def test_valid_demo_has_source_bound_inventories_and_retained_review(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, None)
    records = {row["id"]: row for row in selection["artifacts"]}
    for name in ("task_artifacts", "grading_dependencies"):
        scope = selection["rubric_scope"][name]
        source = records[scope["source"]]
        inventory = json.loads((tmp_path / source["path"]).read_text())[name]
        assert inventory["required"] == scope["required"]
        assert inventory["unresolved_material"] == scope["unresolved_material"] == []
        assert all(records[ident]["source"] == source["path"] for ident in scope["required"])
    scope = selection["rubric_scope"]["grading_dependencies"]
    review = records[scope["review_source"]]
    assert review["role"] == "review"
    assert review["source"] == records[scope["source"]]["path"]
    retained = json.loads((tmp_path / review["path"]).read_text())
    assert retained == {
        "source": scope["source"],
        "required": scope["required"],
        "unresolved_material": [],
        "complete": True,
    }


@pytest.mark.parametrize("mutation", ["missing", "denied", "unresolved"])
def test_declared_unavailable_task_is_not_a_confirmed_zero(tmp_path, monkeypatch, mutation):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, mutation)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    row = json.loads(result.stdout)["items"]["DISC-01-A"]
    assert row["score"] is None
    assert row["status"] == "NOT_ASSESSED"
    assert row["legacy_measurements"] == {"required": 1, "accessible": 0, "missing": 0, "denied": 0, "unresolved": 1}
    assert {entry["name"]: entry["value"] for entry in row["measurements"]} == {
        "accessible": 0,
        "missing": 0,
        "denied": 0,
        "unresolved": 1,
    }


def test_cli_consumes_reference_and_approval_checks(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, None)
    selection["nodes"] = [{"id": "run", "role": "run"}]
    selection["links"] = []
    selection["approvals"] = [
        {
            "id": "a",
            "decision_id": "absent",
            "scope": "task:task/substitution",
            "substitution": {"from": "other", "to": "scorer"},
            "bindings": {},
        }
    ]
    selection["decisions"] = []
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(path)], capture_output=True, text=True
    )
    report = json.loads(result.stdout)
    assert report["measurements"]["links"]["status"] == "fail"
    assert report["measurements"]["approvals"]["status"] == "fail"


def test_cli_accounts_for_every_canonical_check_and_result_field(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from run_rubric_measurements import fixture

    selection = fixture(tmp_path, None)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    canonical = json.loads(
        (ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json").read_text()
    )
    assert set(report["items"]) == {item["id"] for item in canonical["items"]}
    for definition in canonical["items"]:
        row = report["items"][definition["id"]]
        assert set(canonical["result_template"]) <= set(row)
        assert {check["check_id"] for check in row["check_results"]} == {check["id"] for check in definition["checks"]}
        assert len(row["check_results"]) == len(definition["checks"])
        assert row["applicability_reason"]
        assert row["next_action"]
        if row["score"] is not None:
            assert row["subject_id"]
            assert row["subject_revision"]


@pytest.mark.parametrize(
    "helper,selection",
    [
        ("sourced_config", {"declared": {}, "observed": {}}),
        ("freshness", []),
    ],
)
def test_cli_consumes_selected_evidence_helpers(tmp_path, helper, selection):
    request = tmp_path / "selection.json"
    request.write_text(json.dumps({helper: selection}))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert helper in report["measurements"]
    assert report["measurements"][helper]["status"] == "unknown"
    assert report["preflight_success"] is False
