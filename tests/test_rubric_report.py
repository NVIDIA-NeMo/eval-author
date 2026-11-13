# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
CANONICAL = SCRIPTS.parent / "references/artifact-configuration-provenance.json"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/eval-author/scripts"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    path = SCRIPTS / "rubric_report.py"
    assert path.is_file(), "canonical per-check report is not implemented"
    spec = importlib.util.spec_from_file_location("rubric_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from rubric_scoring import assess

    return module, assess


def test_complete_canonical_crosswalk_preserves_assessment_decisions(modules, tmp_path):
    report, assess = modules
    definitions = json.loads(CANONICAL.read_text())
    items = assess({}, {})
    before = copy.deepcopy(items)
    rows = report.build_report(items, {}, {}, tmp_path)
    assert len(rows) == len(definitions["items"]) == 11
    expected = [check["id"] for item in definitions["items"] for check in item["checks"]]
    actual = [check["id"] for row in rows.values() for check in row["check_results"]]
    assert actual == expected
    assert len(actual) == len(set(actual)) == 38
    for item in definitions["items"]:
        row = rows[item["id"]]
        assert definitions["result_template"].keys() <= row.keys()
        assert row["skill_id"] == item["skill_id"]
        assert row["status"] == row["assessment_state"] == items[item["id"]]["status"]
        assert row["score"] == items[item["id"]]["score"]
        assert row["subject_id"] is row["subject_revision"] is None
        assert row["remaining_gaps"] and row["next_action"]
        for original, check in zip(item["checks"], row["check_results"], strict=True):
            assert original.items() <= check.items()
            assert check["condition"] == original["when"]
            assert check["source_ids"] == item["source_ids"]
            assert check["outcome"] in {"NOT_RUN", "BLOCKED"}
            assert check["remaining_gap"] and check["next_action"]
            assert check["evidence"] == []
            assert check["diagnostic_refs"] == []
    assert items == before


def packet(tmp_path):
    import hashlib

    payloads = {
        "inventory": {"task_artifacts": {"required": ["task"], "unresolved_material": []}},
        "task": {"instruction": "Synthetic local fixture"},
        "dispatch": {"selector": "fixed", "routes": {"fixed": "scorer"}, "dependencies": {"scorer": []}},
        "scorer": {"fixture": True},
    }
    records = []
    for ident, value in payloads.items():
        path = tmp_path / (ident + ".json")
        path.write_text(json.dumps(value))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "id": ident,
                "role": {"inventory": "manifest", "dispatch": "configuration"}.get(ident, ident),
                "path": path.name,
                "digest": digest,
                "revision": "unverified-v999",
                "access": "available",
                "selection_origin": "synthetic explicit fixture",
                "source": "inventory.json",
                "evidence": [{"path": path.name, "digest": digest}],
            }
        )
    return {
        "artifacts": records,
        "subject_id": "forged-subject",
        "subject_revision": "forged-revision",
        "route": {"entrypoint": "dispatch", "expected_scorer": "scorer", "dependencies": {"scorer": []}},
        "rubric_scope": {"task_artifacts": {"source": "inventory", "required": ["task"], "unresolved_material": []}},
    }


def measured(selection, tmp_path):
    from rubric_identity import identify, resolve_route

    return {
        "identity": identify(tmp_path, selection["artifacts"]),
        "route": resolve_route(tmp_path, selection["artifacts"], selection["route"]),
    }


@pytest.mark.parametrize("consumer", ["score", "check"])
def test_caller_only_dependency_graph_cannot_pass(modules, tmp_path, consumer):
    import hashlib

    report, assess = modules
    selection = packet(tmp_path)
    path = tmp_path / "dispatch.json"
    document = json.loads(path.read_text())
    del document["dependencies"]
    path.write_text(json.dumps(document))
    record = next(row for row in selection["artifacts"] if row["id"] == "dispatch")
    record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["evidence"][0]["digest"] = record["digest"]
    measurements = measured(selection, tmp_path)
    assert measurements["route"]["status"] == "pass"
    assert measurements["route"]["bound_dependency"] is False
    items = assess(measurements, selection, root=tmp_path)
    if consumer == "score":
        assert items["DISC-01-B"]["score"] is None
    else:
        row = report.build_report(items, measurements, selection, tmp_path)["DISC-01-B"]
        assert row["check_results"][0]["outcome"] == "PARTIAL"


def test_caller_expected_scorer_cannot_establish_wrong_scorer_merit(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    selection["route"]["expected_scorer"] = "caller-typo"
    measurements = measured(selection, tmp_path)
    assert any(row["code"] == "route.scorer_mismatch" for row in measurements["route"]["diagnostics"])
    row = assess(measurements, selection, root=tmp_path)["DISC-01-B"]
    assert (row["status"], row["score"]) == ("NOT_ASSESSED", None)


@pytest.mark.parametrize("mutation", ["role", "source"])
def test_inventory_access_cannot_pass_unbound_required_records(modules, tmp_path, mutation):
    report, assess = modules
    selection = packet(tmp_path)
    record = selection["artifacts"][1]
    record[mutation] = "scorer" if mutation == "role" else "unrelated.json"
    measurements = measured(selection, tmp_path)
    assert measurements["identity"]["status"] == "pass"
    items = assess(measurements, selection, root=tmp_path)
    assert items["DISC-01-A"]["score"] is None
    row = report.build_report(items, measurements, selection, tmp_path)["DISC-01-A"]
    assert row["check_results"][1]["outcome"] == "PARTIAL"
    assert row["check_results"][1]["evidence"][0]["value"][0]["access"] == "unresolved"


def test_scored_inventory_replaces_unmet_default_gap(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    row = report.build_report(items, measurements, selection, tmp_path)["DISC-01-A"]
    assert row["score"] == 2
    stale = "Required task-artifact selection and source-bound access evidence are needed."
    assert stale not in row["limitations"]
    assert stale not in row["remaining_gaps"]
    assert any("undeclared" in gap for gap in row["limitations"])
    assert any("solver permissions" in gap for gap in row["limitations"])


def test_inventory_access_revalidates_current_bytes(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    (tmp_path / "task.json").write_text("changed after measurement")
    row = report.build_report(items, measurements, selection, tmp_path)["DISC-01-A"]
    check = row["check_results"][1]
    assert check["outcome"] == "PARTIAL"
    assert check["evidence"][0]["value"][0]["access"] == "unresolved"


def test_caller_only_dependency_gap_cannot_assign_partial_merit(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    selection["route"]["dependencies"] = {}
    # Remove the retained graph so only the caller claims a missing edge list.
    import hashlib

    path = tmp_path / "dispatch.json"
    document = json.loads(path.read_text())
    del document["dependencies"]
    path.write_text(json.dumps(document))
    record = next(row for row in selection["artifacts"] if row["id"] == "dispatch")
    record["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["evidence"][0]["digest"] = record["digest"]
    measurements = measured(selection, tmp_path)
    assert any(row["code"] == "route.dependencies_unresolved" for row in measurements["route"]["diagnostics"])
    assert assess(measurements, selection, root=tmp_path)["DISC-01-B"]["score"] is None


@pytest.mark.parametrize("source", [[], {}, None, 1])
def test_malformed_inventory_source_stays_blocked(modules, tmp_path, source):
    report, assess = modules
    selection = packet(tmp_path)
    selection["rubric_scope"]["task_artifacts"]["source"] = source
    measurements = measured(selection, tmp_path)
    rows = report.build_report(assess(measurements, selection, root=tmp_path), measurements, selection, tmp_path)
    assert rows["DISC-01-A"]["score"] is None
    assert all(check["outcome"] != "PASS" for check in rows["DISC-01-A"]["check_results"])


def test_selected_identity_and_bounded_helper_checks(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    before = copy.deepcopy((items, measurements, selection))
    rows = report.build_report(items, measurements, selection, tmp_path)
    task = selection["artifacts"][1]
    for row in rows.values():
        assert row["subject_id"] == "task"
        assert row["subject_revision"] == "sha256:" + task["digest"]
        assert row["score"] == items[row["item_id"]]["score"]
        assert isinstance(row["measurements"], list)
    checks = {check["check_id"]: check for row in rows.values() for check in row["check_results"]}
    for ident in ("DISC-01-A-C01", "DISC-01-A-C02", "DISC-01-B-C01"):
        assert checks[ident]["outcome"] == "PASS"
        assert checks[ident]["evidence"] and checks[ident]["evidence_refs"]
        assert checks[ident]["evidence_boundary"]
    assert checks["DISC-01-B-C02"]["outcome"] == "BLOCKED"
    for check in checks.values():
        if check["method"] != "deterministic":
            assert check["outcome"] == "NOT_RUN"
    assert (items, measurements, selection) == before
    assert rows == report.build_report(items, measurements, selection, tmp_path)


@pytest.mark.parametrize("variant", ["changed", "duplicate", "unselected"])
def test_unvalidated_identity_cannot_supply_subject_or_passing_route(modules, tmp_path, variant):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    if variant == "changed":
        (tmp_path / "task.json").write_text("changed bytes")
        (tmp_path / "scorer.json").write_text("changed bytes")
    elif variant == "duplicate":
        selection["artifacts"].append(copy.deepcopy(selection["artifacts"][1]))
        selection["artifacts"].append(copy.deepcopy(selection["artifacts"][-2]))
    else:
        selection["artifacts"] = [record for record in selection["artifacts"] if record["id"] not in {"task", "scorer"}]
    rows = report.build_report(items, measurements, selection, tmp_path)
    assert rows["DISC-01-A"]["subject_id"] != "task"
    assert rows["DISC-01-B"]["check_results"][0]["outcome"] != "PASS"
    assert rows["DISC-01-A"]["score"] == items["DISC-01-A"]["score"]


@pytest.mark.parametrize(
    "access,expected", [("available", "missing"), ("missing", "unresolved"), ("denied", "unresolved")]
)
def test_actual_access_records_supply_counts_and_diagnostics(modules, tmp_path, access, expected):
    report, assess = modules
    selection = packet(tmp_path)
    selection["artifacts"][1]["access"] = access
    (tmp_path / "task.json").unlink()
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    row = report.build_report(items, measurements, selection, tmp_path)["DISC-01-A"]
    check = row["check_results"][1]
    assert check["evidence"][0]["value"][0]["access"] == expected
    count = next(value for value in check["measurements"] if value["name"] == expected)
    assert count["value"] == count["denominator"] == 1
    assert count["unit"] == "artifacts"
    assert count["missingness"] and count["uncertainty"]
    for diagnostic in check["diagnostic_refs"]:
        index = int(diagnostic["ref"].rsplit("/", 1)[1])
        assert measurements["identity"]["diagnostics"][index]["code"] == diagnostic["code"]
    assert check["outcome"] == ("FAIL" if access == "available" else "PARTIAL")


def test_raw_helpers_are_bounded_partial_evidence_not_native_success(modules, tmp_path):
    from rubric_identity import compare_config
    from rubric_links import check_approvals, check_links

    report, assess = modules
    selection = packet(tmp_path)
    selection.update(
        declared_config={"agent": {"model": "fixture-a"}},
        observed_config={"agent": {"model": "fixture-b"}},
        nodes=[{"id": "task", "role": "task"}],
        links=[],
        approvals=[],
        decisions=[],
    )
    measurements = measured(selection, tmp_path)
    measurements.update(
        config=compare_config(selection["declared_config"], selection["observed_config"]),
        links=check_links(selection["nodes"], selection["links"]),
        approvals=check_approvals([], [], selection["artifacts"]),
    )
    rows = report.build_report(assess(measurements, selection, root=tmp_path), measurements, selection, tmp_path)
    checks = {check["check_id"]: check for row in rows.values() for check in row["check_results"]}
    for ident in ("DISC-02-A-C02", "DISC-02-B-C01", "DISC-02-B-C02", "AUD-02-C01", "CREATE-07-C01", "CREATE-07-C02"):
        check = checks[ident]
        assert check["outcome"] == "PARTIAL"
        assert check["evidence"] and check["remaining_gap"]
        assert check["evidence_boundary"]
    assert "/agent/model" in checks["AUD-02-C01"]["evidence"][0]["value"]["differences"]
    for ident in ("DISC-01-B-C02", "DISC-03-A-C01", "DISC-03-A-C02", "ENV-01-C02", "CREATE-07-C03", "CREATE-07-C04"):
        assert checks[ident]["outcome"] == "BLOCKED"
        assert checks[ident]["remaining_gap"]
    assert checks["ENV-02-A-C03"]["diagnostic_refs"]
    assert rows["ENV-02-A"]["approval_refs"] == []
    assert all(row["score"] is None for ident, row in rows.items() if ident not in {"DISC-01-A", "DISC-01-B"})


def test_declarations_without_helper_observations_do_not_count_as_execution(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    selection.update(nodes=[{"id": "task", "role": "task"}], approvals=[{"id": "declared-only"}])
    rows = report.build_report(assess({}, {}), {}, selection, tmp_path)
    for row in rows.values():
        assert row["execution_state"] == "NOT_RUN"
        assert row["score"] is None
        for check in row["check_results"]:
            assert check["evidence"] == []
            assert check["outcome"] in {"BLOCKED", "NOT_RUN"}


@pytest.mark.parametrize("variant", ["unselected", "originless", "missing-reference"])
def test_inventory_does_not_pass_incomplete_or_unbound_reference_origins(modules, tmp_path, variant):
    report, assess = modules
    selection = packet(tmp_path)
    if variant == "originless":
        selection["artifacts"][1]["selection_origin"] = ""
    measurements = measured(selection, tmp_path)
    if variant == "unselected":
        selection["artifacts"].pop(1)
    elif variant == "missing-reference":
        measurements["identity"]["records"] = [
            record for record in measurements["identity"]["records"] if record["id"] != "task"
        ]
    row = report.build_report(assess(measurements, selection, root=tmp_path), measurements, selection, tmp_path)[
        "DISC-01-A"
    ]
    assert row["check_results"][0]["outcome"] != "PASS"
    access = row["check_results"][1]
    assert access["outcome"] != "PASS"
    unresolved = next(value for value in access["measurements"] if value["name"] == "unresolved")
    assert unresolved["value"] == unresolved["denominator"] == 1


def test_route_observation_cannot_pass_after_route_selection_changes(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    items = assess(measurements, selection, root=tmp_path)
    selection["route"]["dependencies"]["scorer"] = ["unavailable-new-dependency"]
    check = report.build_report(items, measurements, selection, tmp_path)["DISC-01-B"]["check_results"][0]
    assert check["outcome"] == "PARTIAL"
    assert check["remaining_gap"]


def test_missing_route_measurement_is_blocked_even_with_valid_identity(modules, tmp_path):
    report, assess = modules
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    del measurements["route"]
    check = report.build_report(assess(measurements, selection, root=tmp_path), measurements, selection, tmp_path)[
        "DISC-01-B"
    ]["check_results"][0]
    assert check["outcome"] == "BLOCKED"
    assert check["evidence"] == []


def test_canonical_method_changes_cannot_inherit_deterministic_passes(modules, tmp_path, monkeypatch):
    report, assess = modules
    definitions = json.loads(CANONICAL.read_text())
    definitions["items"][0]["checks"][0]["method"] = "human_review"
    definitions["items"][1]["checks"][0]["method"] = "human_review"
    definitions["items"][3]["checks"][1]["method"] = "human_review"
    definitions["result_template"]["future_field"] = []
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps(definitions))
    monkeypatch.setattr(report, "CANONICAL", canonical)
    selection = packet(tmp_path)
    measurements = measured(selection, tmp_path)
    measurements["config"] = {"differences": [], "unknowns": ["/agent"]}
    rows = report.build_report(assess(measurements, selection, root=tmp_path), measurements, selection, tmp_path)
    for row in rows.values():
        assert row["future_field"] == []
        for check in row["check_results"]:
            assert check["check_id"] == check["id"]
            if check["method"] == "human_review":
                assert check["outcome"] == "NOT_RUN"
                assert check["evidence"] == []


def test_unknown_criterion_is_rejected_and_explicit_subset_is_preserved(modules, tmp_path):
    report, assess = modules
    with pytest.raises(ValueError, match="unknown rubric item"):
        report.build_report({"made-up": {}}, {}, {}, tmp_path)
    items = assess({}, {})
    rows = report.build_report({"ENV-01": items["ENV-01"]}, {}, {}, tmp_path)
    assert list(rows) == ["ENV-01"]
    assert len(rows["ENV-01"]["check_results"]) == 3


def sourced_packet(tmp_path):
    from rubric_configuration import compare_sourced_config
    from test_rubric_configuration import artifact, ref

    values = {
        "agent": "PRIVATE-CONFIG-SENTINEL",
        "run": "fixture-run",
        "task": "fixture-task",
        "tool_permissions": ["read"],
        "provider": "fixture-provider",
        "runtime_pins": {"python": "fixture-version"},
        "environment": {"mode": "fixture-mode"},
    }
    declared = artifact(tmp_path, "declared", values)
    observed = artifact(tmp_path, "observed", values)
    config = {
        "declared": {"/" + key: ref("declared", "/" + key) for key in values},
        "observed": {"/" + key: ref("observed", "/" + key) for key in values},
    }
    selection = {"artifacts": [declared, observed], "sourced_config": config}
    measurement = compare_sourced_config(tmp_path, selection["artifacts"], **config)
    return selection, {"sourced_config": measurement}


def report_checks(modules, measurements, selection, root):
    report, assess = modules
    rows = report.build_report(assess(measurements, selection, root=root), measurements, selection, root)
    return rows, {check["id"]: check for row in rows.values() for check in row["check_results"]}


def predicates(check):
    return {row["id"]: row["outcome"] for row in check["predicate_results"]}


def counts(check):
    return {row["name"]: (row["value"], row["denominator"]) for row in check["measurements"]}


def assert_resolving_refs(checks, measurements, selection):
    document = {"measurements": measurements, "selection": selection}
    for check in checks.values():
        refs = check["evidence_refs"] + [row["ref"] for row in check["diagnostic_refs"]]
        refs += [ref for row in check.get("predicate_results", []) for ref in row["evidence_refs"]]
        for ref in refs:
            assert ref.startswith("#/"), ref
            value = document
            for token in ref[2:].split("/"):
                key = token.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]


def test_sourced_fields_supply_bounded_predicates_not_canonical_success(modules, tmp_path):
    selection, measurements = sourced_packet(tmp_path)
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    assert len(checks) == 38
    for ident in ("DISC-02-A-C01", "DISC-02-A-C02", "DISC-02-B-C01", "DISC-02-B-C02", "AUD-02-C01"):
        check = checks[ident]
        assert check["outcome"] == "PARTIAL"
        assert check["evidence"][0]["helper"] == "sourced_config"
        assert check["evidence_refs"] and check["remaining_gap"]
        assert counts(check)["declared_sourced"] == (7, 7)
        assert counts(check)["observed_sourced"] == (7, 7)
        assert counts(check)["run_bound"] == (0, 7)
    assert predicates(checks["DISC-02-A-C01"])["declared_fields_sourced"] == "PASS"
    assert predicates(checks["DISC-02-A-C02"])["unknown_states_classified"] == "PASS"
    assert predicates(checks["DISC-02-B-C01"])["selected_run_binding"] == "BLOCKED"
    assert predicates(checks["AUD-02-C01"])["selected_fields_compared"] == "PASS"
    assert all(row["score"] is None for row in rows.values())
    assert "PRIVATE-CONFIG-SENTINEL" not in json.dumps(rows)
    assert_resolving_refs(checks, measurements, selection)


@pytest.mark.parametrize("role,expected", [("task", "stale"), ("ethos", "unresolved")])
def test_freshness_reports_scoped_lineage_without_scoring_or_fixture_claims(modules, tmp_path, role, expected):
    from rubric_freshness import check_freshness
    from test_rubric_freshness import artifact, binding_source

    dependency = artifact(tmp_path, "dep", {"v": 1}, role=role)
    neighbor = artifact(tmp_path, "neighbor", {"v": 1})
    result = artifact(tmp_path, "result", {"reward": 1}, role="result")
    other = artifact(tmp_path, "other", {"reward": 0}, role="result")
    source, entry = binding_source(tmp_path, result, [dependency])
    other_source, other_entry = binding_source(tmp_path, other, [neighbor])
    dependency = artifact(tmp_path, "dep", {"v": 2}, role=role, revision="r2")
    selection = {
        "artifacts": [dependency, neighbor, result, other, source, other_source],
        "freshness": [entry, other_entry],
    }
    measurements = {"freshness": check_freshness(tmp_path, selection["artifacts"], selection["freshness"])}
    before = copy.deepcopy((selection, measurements))
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    for ident in ("CREATE-07-C01", "CREATE-07-C02"):
        check = checks[ident]
        assert check["outcome"] == "PARTIAL"
        assert check["evidence"][0]["value"] == measurements["freshness"]["records"]
        assert counts(check)["current"] == (1, 2)
        assert counts(check)[expected] == (1, 2)
        assert counts(check)["usable"] == (1, 2)
    assert predicates(checks["CREATE-07-C01"])["selected_evidence_lineage_bound"] == "PASS"
    assert predicates(checks["CREATE-07-C02"])["selected_evidence_current"] == ("FAIL" if role == "task" else "BLOCKED")
    assert predicates(checks["CREATE-07-C02"])["noncurrent_evidence_withheld"] == "PASS"
    assert checks["CREATE-07-C03"]["outcome"] == "BLOCKED"
    ethos = checks["CREATE-07-C04"]
    if role == "ethos":
        assert ethos["outcome"] == "PARTIAL"
        assert counts(ethos)["unresolved"] == (1, 1)
        assert predicates(ethos)["selected_ethos_identity_current"] == "BLOCKED"
    else:
        assert ethos["outcome"] == "BLOCKED"
    assert rows["CREATE-07"]["score"] is None
    assert_resolving_refs(checks, measurements, selection)
    assert (selection, measurements) == before


@pytest.mark.parametrize("failed_dependency", ["a-dep", "z-dep"])
def test_partial_dependency_lineage_cannot_pass_selected_evidence_binding(modules, tmp_path, failed_dependency):
    from rubric_freshness import check_freshness
    from test_rubric_freshness import artifact, binding_source

    dependencies = [artifact(tmp_path, ident, {"v": 1}) for ident in ("a-dep", "z-dep")]
    result = artifact(tmp_path, "result", {"private": "PRIVATE-LINEAGE-SENTINEL"}, role="result")
    source, entry = binding_source(tmp_path, result, dependencies)
    neighbor = artifact(tmp_path, "neighbor", {"reward": 0}, role="result")
    neighbor_source, neighbor_entry = binding_source(
        tmp_path, neighbor, [dep for dep in dependencies if dep["id"] != failed_dependency]
    )
    (tmp_path / (failed_dependency + ".json")).write_text('{"v": 2}')
    selection = {
        "artifacts": [*dependencies, result, source, neighbor, neighbor_source],
        "freshness": [entry, neighbor_entry],
    }
    measurements = {"freshness": check_freshness(tmp_path, selection["artifacts"], selection["freshness"])}
    measurement = measurements["freshness"]
    assert measurement["status"] == "fail"
    assert any(row["code"] == "freshness.binding_invalid" for row in measurement["diagnostics"])
    assert [dep["id"] for dep in measurement["records"][0]["lineage"]] == (
        ["a-dep"] if failed_dependency == "z-dep" else []
    )
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["CREATE-07-C01"]
    assert predicates(check)["selected_evidence_lineage_bound"] == "BLOCKED"
    assert counts(check)["unresolved"] == (1, 2)
    assert counts(check)["current"] == (1, 2)
    assert counts(check)["usable"] == (1, 2)
    assert any(row["code"] == "freshness.binding_invalid" for row in check["diagnostic_refs"])
    assert predicates(checks["CREATE-07-C02"])["selected_evidence_current"] == "BLOCKED"
    assert check["outcome"] == "PARTIAL"
    assert rows["CREATE-07"]["score"] is None
    assert "PRIVATE-LINEAGE-SENTINEL" not in json.dumps(rows)
    assert_resolving_refs(checks, measurements, selection)


@pytest.mark.parametrize("helper", ["sourced_config", "freshness"])
@pytest.mark.parametrize("defect", ["generic_success", "changed_source", "changed_selection", "forged_counts"])
def test_unverified_optional_measurements_cannot_supply_predicates(modules, tmp_path, helper, defect):
    from rubric_freshness import check_freshness
    from test_rubric_freshness import artifact, binding_source

    if helper == "sourced_config":
        selection, measurements = sourced_packet(tmp_path)
        target = "DISC-02-A-C01"
    else:
        dep = artifact(tmp_path, "dep", {"v": 1})
        result = artifact(tmp_path, "result", {"reward": 1}, role="result")
        source, entry = binding_source(tmp_path, result, [dep])
        selection: dict = {"artifacts": [dep, result, source], "freshness": [entry]}
        measurements = {helper: check_freshness(tmp_path, selection["artifacts"], selection[helper])}
        target = "CREATE-07-C01"
    if defect == "generic_success":
        measurements[helper] = {"status": "pass"}
    elif defect == "changed_source":
        (tmp_path / selection["artifacts"][0]["path"]).write_text("changed")
    elif defect == "changed_selection":
        selection[helper] = {} if helper == "sourced_config" else []
    elif helper == "sourced_config":
        measurements[helper]["counts"]["run_bound"] = 7
    else:
        measurements[helper]["records"][0]["usable"] = False
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    assert checks[target]["outcome"] == "BLOCKED"
    assert checks[target]["evidence"] == []
    assert not checks[target].get("predicate_results")
    assert "revalidation" in checks[target]["remaining_gap"]


def test_sourced_unknown_counts_preserve_reasons_and_source_references(modules, tmp_path):
    from rubric_configuration import compare_sourced_config

    selection, measurements = sourced_packet(tmp_path)
    config = selection["sourced_config"]
    config["declared"]["/provider"] = {"state": "provider_hidden", "reason": "provider_not_disclosed"}
    config["observed"]["/runtime_pins"] = {"state": "inaccessible", "reason": "access_denied"}
    config["observed"]["/agent"] = {"state": "unobservable", "reason": "not_recorded"}
    del config["declared"]["/run"]
    measurements["sourced_config"] = compare_sourced_config(tmp_path, selection["artifacts"], **config)
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["DISC-02-A-C02"]
    for name in ("declared_provider_hidden", "declared_omitted", "observed_inaccessible", "observed_unobservable"):
        assert counts(check)[name] == (1, 7)
    assert counts(check)["unknown"] == (4, 7)
    assert counts(check)["equal"] == (3, 7)
    assert "#/selection/artifacts/0" in check["evidence_refs"]
    assert "#/selection/artifacts/1" in check["evidence_refs"]
    assert_resolving_refs(checks, measurements, selection)


def test_native_freshness_does_not_promote_historical_control_to_current_binding(modules, tmp_path, monkeypatch):
    from rubric_freshness import check_freshness
    from test_audit_verifier_evidence import panel
    from test_rubric_freshness import native_selection

    fixture = getattr(panel, "__wrapped__")(tmp_path, monkeypatch)
    artifacts, entry = native_selection(fixture)
    selection = {"artifacts": artifacts, "freshness": [entry]}
    measurements = {"freshness": check_freshness(tmp_path, artifacts, [entry])}
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    bound = checks["CREATE-07-C01"]
    assert bound["evidence"][0]["value"][0]["native_classification"] == "passed"
    assert predicates(bound)["selected_evidence_lineage_bound"] == "BLOCKED"
    assert predicates(checks["CREATE-07-C02"])["selected_evidence_current"] == "BLOCKED"
    assert "#/selection/freshness/0" in bound["evidence_refs"]
    assert "#/selection/artifacts/0" in bound["evidence_refs"]
    assert checks["CREATE-07-C03"]["outcome"] == "BLOCKED"
    assert_resolving_refs(checks, measurements, selection)


@pytest.mark.parametrize("variant,expected", [("bound", "PASS"), ("partial", "PARTIAL"), ("wrong_run", "FAIL")])
def test_selected_config_binding_outcomes_do_not_attest_native_execution(modules, tmp_path, variant, expected):
    from rubric_configuration import compare_sourced_config
    from test_rubric_configuration import artifact, bound_case, ref

    artifacts, binding = bound_case(tmp_path)
    fields = ["agent", "run", "task", "tool_permissions", "provider", "runtime_pins", "environment"]
    declared = {"/" + field: ref("declared", "/agent") for field in fields}
    observed = {"/" + field: ref("observed", "/agent") for field in fields}
    if variant == "partial":
        declared = {"/agent": declared["/agent"]}
        observed = {"/agent": observed["/agent"]}
    elif variant == "wrong_run":
        payload = json.loads((tmp_path / artifacts[-1]["path"]).read_text())
        payload["run"] = "wrong-run"
        payload["agent"] = "different-agent"
        artifacts[-1] = artifact(tmp_path, "observed", payload)
    selection = {
        "artifacts": artifacts,
        "sourced_config": {"declared": declared, "observed": observed, "binding": binding},
    }
    measurements = {"sourced_config": compare_sourced_config(tmp_path, artifacts, **selection["sourced_config"])}
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["DISC-02-B-C01"]
    assert predicates(check)["selected_run_binding"] == expected
    assert check["outcome"] == "PARTIAL"
    assert counts(check)["run_bound"] == ((7 if variant == "bound" else 1 if variant == "partial" else 0), 7)
    if variant == "wrong_run":
        assert counts(check)["different"] == (7, 7)
        assert predicates(checks["AUD-02-C01"])["selected_fields_compared"] == "PASS"
    assert checks["DISC-02-B-C03"]["outcome"] == "NOT_RUN"
    assert_resolving_refs(checks, measurements, selection)


@pytest.mark.parametrize("malformed", [None, "PRIVATE-MALFORMED-SENTINEL", [], 7, {"role": "ethos"}])
@pytest.mark.parametrize("role", ["task", "ethos"])
def test_cli_malformed_artifact_neighbor_keeps_safe_negative_report(modules, tmp_path, malformed, role):
    from test_rubric_freshness import artifact, binding_source

    dependency = artifact(tmp_path, "dep", {"private": "PRIVATE-SOURCE-SENTINEL"}, role=role)
    result = artifact(tmp_path, "result", {"reward": 0}, role="result")
    source, entry = binding_source(tmp_path, result, [dependency])
    dependency = artifact(tmp_path, "dep", {"private": "PRIVATE-CHANGED-SENTINEL"}, role=role)
    selection = {"artifacts": [malformed, dependency, result, source], "freshness": [entry]}
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection))
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "measure.py"), "--root", str(tmp_path), "--selection", str(selection_path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.stderr == ""
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["preflight_success"] is False
    assert report["measurements"]["identity"]["status"] == "fail"
    assert report["measurements"]["identity"]["diagnostics"]
    row = report["items"]["CREATE-07"]
    checks = {check["id"]: check for check in row["check_results"]}
    freshness = checks["CREATE-07-C02"]
    assert predicates(freshness)["selected_evidence_current"] == ("FAIL" if role == "task" else "BLOCKED")
    assert counts(freshness)["stale" if role == "task" else "unresolved"] == (1, 1)
    assert counts(freshness)["usable"] == (0, 1)
    assert freshness["diagnostic_refs"]
    assert row["score"] is None
    if role == "ethos":
        assert predicates(checks["CREATE-07-C04"])["selected_ethos_identity_current"] == "BLOCKED"
        assert counts(checks["CREATE-07-C04"])["unresolved"] == (1, 1)
    else:
        assert checks["CREATE-07-C04"]["outcome"] == "BLOCKED"
    assert "PRIVATE-" not in completed.stdout + completed.stderr
    assert_resolving_refs(checks, report["measurements"], selection)


@pytest.mark.parametrize("invalid", [None, {}, "not-a-list"])
def test_malformed_freshness_selection_stays_blocked(modules, tmp_path, invalid):
    from rubric_freshness import check_freshness

    selection = {"artifacts": [], "freshness": invalid}
    measurements = {"freshness": check_freshness(tmp_path, [], invalid)}
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    assert checks["CREATE-07-C01"]["outcome"] == "BLOCKED"


@pytest.mark.parametrize("variant", ["current", "invalid", "empty"])
def test_freshness_current_invalid_and_empty_packets_keep_exact_denominators(modules, tmp_path, variant):
    from rubric_freshness import check_freshness
    from test_rubric_freshness import artifact, binding_source

    dep = artifact(tmp_path, "dep", {"v": 1})
    result = artifact(tmp_path, "result", {"reward": 0}, role="result")
    source, entry = binding_source(tmp_path, result, [dep])
    if variant == "invalid":
        (tmp_path / source["path"]).unlink()
    selection = {"artifacts": [dep, result, source], "freshness": [] if variant == "empty" else [entry]}
    measurements = {"freshness": check_freshness(tmp_path, selection["artifacts"], selection["freshness"])}
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["CREATE-07-C02"]
    assert check["outcome"] == "PARTIAL"
    assert counts(check)["current"] == (int(variant == "current"), int(variant != "empty"))
    assert counts(check)["unresolved"] == (int(variant == "invalid"), int(variant != "empty"))
    assert predicates(check)["selected_evidence_current"] == ("PASS" if variant == "current" else "BLOCKED")
    assert rows["CREATE-07"]["score"] is None
    assert_resolving_refs(checks, measurements, selection)


@pytest.fixture
def native_packet(modules, tmp_path, monkeypatch):
    from rubric_native import project_native
    from test_rubric_native import panel

    fixture = getattr(panel, "__wrapped__")(tmp_path, monkeypatch)
    selection = {"artifacts": fixture.identity["records"], "native": fixture.selection}
    measurements = {"native": project_native(tmp_path, fixture.identity, **fixture.selection)}
    return fixture, selection, measurements


def add_freshness_packet(tmp_path, selection, measurements, *, stale=False):
    from rubric_freshness import check_freshness
    from test_rubric_freshness import artifact, binding_source

    dep = artifact(tmp_path, "dep", {"v": 1})
    result = artifact(tmp_path, "historical", {"reward": 0}, role="result")
    source, entry = binding_source(tmp_path, result, [dep])
    if stale:
        dep = artifact(tmp_path, "dep", {"v": 2})
    selection["artifacts"].extend([dep, result, source])
    selection["freshness"] = [entry]
    measurements["freshness"] = check_freshness(tmp_path, selection["artifacts"], [entry])


@pytest.mark.parametrize("freshness", [None, "current", "stale"])
def test_native_qualified_refs_are_bounded_and_preserve_freshness(modules, native_packet, tmp_path, freshness):
    from test_rubric_native import run_rubric_cli

    fixture, selection, measurements = native_packet
    if freshness:
        add_freshness_packet(tmp_path, selection, measurements, stale=freshness == "stale")
    baseline_selection = {key: value for key, value in selection.items() if key != "native"}
    baseline_measurements = {key: value for key, value in measurements.items() if key != "native"}
    baseline, baseline_checks = report_checks(modules, baseline_measurements, baseline_selection, tmp_path)
    before = copy.deepcopy((selection, measurements))
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["CREATE-07-C01"]
    assert check["outcome"] == "PARTIAL"
    assert predicates(check)["selected_native_references_bound"] == "PASS"
    native = next(row["value"] for row in check["evidence"] if row["helper"] == "native")
    assert native == measurements["native"]
    assert {node["role"] for node in native["nodes"]} == {"task", "run", "trace", "tool_call", "result"}
    assert native["subjects"][0]["native_identity"] == "verified"
    assert native["controls"][0]["classification"] == "false_rejection"
    assert native["task_freshness"] == "unknown"
    assert native["native_execution"] is False
    assert "complete" in check["evidence_boundary"] and "freshness" in check["remaining_gap"]
    assert "#/measurements/native" in check["evidence_refs"]
    assert "#/selection/native" in check["evidence_refs"]
    assert {f"#/selection/artifacts/{index}" for index in range(4)} <= set(check["evidence_refs"])
    if freshness:
        previous = baseline_checks["CREATE-07-C01"]
        assert previous["evidence"][0] in check["evidence"]
        assert previous["predicate_results"][0] in check["predicate_results"]
        native_units = {"selected receipt claims", "selected required controls", "selected receipts"}
        assert [row for row in check["measurements"] if row["unit"] not in native_units] == previous["measurements"]
        assert set(previous["evidence_refs"]) <= set(check["evidence_refs"])
        assert all(row in check["diagnostic_refs"] for row in previous["diagnostic_refs"])
    for ident, other in checks.items():
        if ident != "CREATE-07-C01":
            assert other == baseline_checks[ident]
    for ident, row in rows.items():
        for key in ("score", "status", "assessment_state"):
            assert row[key] == baseline[ident][key]
    assert rows["CREATE-07"]["score"] is None
    assert "PRIVATE-" not in json.dumps(rows)
    assert_resolving_refs(checks, measurements, selection)
    assert (selection, measurements) == before
    cli = run_rubric_cli(fixture, selection)
    assert cli.returncode == 1 and cli.stderr == ""
    actual = json.loads(cli.stdout)
    actual_check = actual["items"]["CREATE-07"]["check_results"][0]
    for key in ("outcome", "evidence", "evidence_refs", "predicate_results"):
        assert actual_check[key] == check[key]
    assert actual["preflight_success"] is False


@pytest.mark.parametrize("with_freshness", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        "generic_success",
        "changed_source",
        "changed_selection",
        "unselected",
        "forged_binding",
        "forged_execution",
        "forged_freshness",
        "forged_identity",
        "type_confusion",
    ],
)
def test_unverified_native_output_never_supplies_predicates(modules, native_packet, tmp_path, defect, with_freshness):
    fixture, selection, measurements = native_packet
    if with_freshness:
        add_freshness_packet(tmp_path, selection, measurements)
    if defect == "generic_success":
        measurements["native"] = {"status": "pass"}
    elif defect == "changed_source":
        fixture.paths["trace"].write_text("changed")
    elif defect == "changed_selection":
        selection["native"]["trace_ids"] = []
    elif defect == "unselected":
        del selection["native"]
    elif defect == "forged_binding":
        measurements["native"]["subjects"][0]["run"] = "forged-run"
    elif defect == "forged_identity":
        measurements["identity"] = copy.deepcopy(fixture.identity)
        selection["artifacts"] = [row for row in selection["artifacts"] if row["id"] != "trace"]
    elif defect == "forged_freshness":
        measurements["native"]["task_freshness"] = "current"
    else:
        measurements["native"]["native_execution"] = 0 if defect == "type_confusion" else True
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["CREATE-07-C01"]
    assert check["outcome"] == ("PARTIAL" if with_freshness else "BLOCKED")
    assert not any(row["helper"] == "native" for row in check["evidence"])
    assert not any(row["id"] == "selected_native_references_bound" for row in check.get("predicate_results", []))
    assert "revalidation" in check["remaining_gap"]
    if with_freshness:
        assert predicates(check)["selected_evidence_lineage_bound"] == "PASS"
        assert counts(check)["current"] == (1, 1)


@pytest.mark.parametrize(
    "damage,expected", [("broken_tool", "FAIL"), ("missing_receipt", "FAIL"), ("empty", "BLOCKED")]
)
def test_native_reference_defects_do_not_pass_or_assign_merit(modules, native_packet, tmp_path, damage, expected):
    from rubric_native import project_native

    fixture, selection, measurements = native_packet
    if damage == "broken_tool":
        fixture.trace["steps"][0]["observation"]["results"][0]["source_call_id"] = "PRIVATE-MISSING"
        fixture.identity = fixture.seal()
        selection["artifacts"] = fixture.identity["records"]
    elif damage == "missing_receipt":
        selection["native"]["receipt_ids"] = ["missing"]
    else:
        selection["native"] = {key: [] for key in fixture.selection}
    measurements["native"] = project_native(tmp_path, fixture.identity, **selection["native"])
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["CREATE-07-C01"]
    assert check["outcome"] == "PARTIAL"
    assert predicates(check)["selected_native_references_bound"] == expected
    assert check["evidence"][0]["value"] == measurements["native"]
    assert rows["CREATE-07"]["score"] is None
    assert_resolving_refs(checks, measurements, selection)
    assert "PRIVATE-" not in json.dumps(rows)


def approval_packet(tmp_path):
    from rubric_links import check_sourced_approvals
    from test_rubric_approval_sources import fixture

    approvals, decisions, identity, sources = fixture(tmp_path)
    selection = {
        "artifacts": identity["records"],
        "approvals": approvals,
        "decisions": decisions,
        "approval_sources": sources,
    }
    measurements = {"approvals": check_sourced_approvals(tmp_path, approvals, decisions, identity, sources)}
    return selection, measurements


def test_sourced_approvals_retain_evidence_and_scoped_predicates_in_cli(modules, tmp_path):
    selection, measurements = approval_packet(tmp_path)
    before = copy.deepcopy((selection, measurements))
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["ENV-02-A-C03"]
    assert check["outcome"] == "PARTIAL"
    assert check["evidence"][0]["value"] == measurements["approvals"]
    assert predicates(check) == {
        "selected_approval_sources_match": "PASS",
        "selected_approval_scope_matches": "PASS",
        "selected_approval_bindings_current": "PASS",
    }
    assert "#/measurements/approvals" in check["evidence_refs"]
    assert "#/selection/approval_sources" in check["evidence_refs"]
    assert "#/selection/artifacts/5" in check["evidence_refs"]
    assert check["evidence"][0]["value"]["authority_authenticated"] is False
    assert "installed" in check["remaining_gap"] and "authority" in check["evidence_boundary"]
    assert checks["ENV-02-A-C02"]["outcome"] == "NOT_RUN"
    assert rows["ENV-02-A"]["score"] is None
    assert_resolving_refs({check["id"]: check}, measurements, selection)
    assert (selection, measurements) == before
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    command = [sys.executable, str(SCRIPTS / "measure.py"), "--root", str(tmp_path), "--selection", str(request)]
    cli = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert cli.returncode == 1 and cli.stderr == ""
    actual = json.loads(cli.stdout)
    assert actual["measurements"]["approvals"] == measurements["approvals"]
    assert actual["items"]["ENV-02-A"]["check_results"][2] == check
    assert actual["preflight_success"] is False
    assert subprocess.run(command, capture_output=True, text=True, timeout=15).stdout == cli.stdout


@pytest.mark.parametrize(
    "variant,expected,code",
    [
        ("source", ("FAIL", "BLOCKED", "BLOCKED"), "approvals.source_mismatch"),
        ("scope", ("PASS", "FAIL", "BLOCKED"), "approvals.scope_unbound"),
        ("stale", ("PASS", "PASS", "FAIL"), "approvals.digest_stale"),
        ("audit", ("BLOCKED", "BLOCKED", "BLOCKED"), "approvals.source_record_incomplete"),
        ("journey", ("BLOCKED", "BLOCKED", "BLOCKED"), "approvals.source_record_incomplete"),
    ],
)
def test_approval_predicates_follow_source_scope_and_freshness_not_declarations(
    modules, tmp_path, variant, expected, code
):
    from rubric_identity import identify
    from rubric_links import check_sourced_approvals
    from test_rubric_approval_sources import retain

    selection, measurements = approval_packet(tmp_path)
    decision = selection["decisions"][0]
    if variant == "source":
        retained = {**decision, "outcome": "denied"}
    elif variant == "audit":
        import audit_state

        document = audit_state.new_record()
        document["checks_approval"] = {"digest": "a" * 64, "source": decision["source"]}
        audit_state.validate(document)
        retained = document["checks_approval"]
    elif variant == "journey":
        import journey_state

        retained = {
            "status": "accepted",
            "source": decision["source"],
            "task_digest": "a" * 64,
            "evidence": [{"path": "proof.json", "digest": "b" * 64}],
            "updated_at": "retained timestamp",
        }
        journey_state._validate_tasks({"task": retained})
    else:
        if variant == "scope":
            selection["approvals"][0]["scope"] = decision["scope"] = "task:elsewhere/substitution"
        else:
            selection["artifacts"][2] = retain(tmp_path, "scorer", "scorer", {"new": True})
        retained = decision
    selection["artifacts"][-1] = retain(tmp_path, "decision", "decision", {"decisions": [retained]})
    measurements["approvals"] = check_sourced_approvals(
        tmp_path,
        selection["approvals"],
        selection["decisions"],
        identify(tmp_path, selection["artifacts"]),
        selection["approval_sources"],
    )
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["ENV-02-A-C03"]
    assert tuple(predicates(check).values()) == expected
    assert check["outcome"] == "PARTIAL" and rows["ENV-02-A"]["score"] is None
    assert check["evidence"][0]["value"] == measurements["approvals"]
    assert code in {row["code"] for row in check["diagnostic_refs"]}
    assert measurements["approvals"]["authority_authenticated"] is False
    assert checks["ENV-02-A-C02"]["outcome"] == "NOT_RUN"
    if variant in {"audit", "journey"}:
        assert measurements["approvals"]["status"] == "unknown"
    assert_resolving_refs({check["id"]: check}, measurements, selection)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    cli = subprocess.run(
        [sys.executable, str(SCRIPTS / "measure.py"), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert cli.returncode == 1 and cli.stderr == ""
    assert json.loads(cli.stdout)["items"]["ENV-02-A"]["check_results"][2] == check


@pytest.mark.parametrize(
    "defect",
    [
        "generic_success",
        "changed_source",
        "changed_selection",
        "forged_evidence",
        "forged_authority",
        "type_confusion",
        "unselected",
    ],
)
def test_unverified_sourced_approvals_cannot_supply_evidence_or_predicates(modules, tmp_path, defect):
    selection, measurements = approval_packet(tmp_path)
    if defect == "generic_success":
        measurements["approvals"] = {"status": "pass"}
    elif defect == "changed_source":
        (tmp_path / "decision.json").write_text("{}")
    elif defect == "changed_selection":
        selection["approval_sources"]["d1"]["pointer"] = "/missing"
    elif defect == "forged_evidence":
        measurements["approvals"]["evidence"][0]["digest"] = "a" * 64
    elif defect == "unselected":
        del selection["approval_sources"]
    else:
        measurements["approvals"]["authority_authenticated"] = 0 if defect == "type_confusion" else True
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["ENV-02-A-C03"]
    assert check["outcome"] == "BLOCKED"
    assert check["evidence"] == [] and not check.get("predicate_results")
    assert "revalidation" in check["remaining_gap"]


def test_sourced_approval_identity_failure_retains_safe_diagnostics(modules, tmp_path):
    from rubric_identity import identify
    from rubric_links import check_sourced_approvals

    selection, measurements = approval_packet(tmp_path)
    selection["artifacts"].append({"id": []})
    measurements["approvals"] = check_sourced_approvals(
        tmp_path,
        selection["approvals"],
        selection["decisions"],
        identify(tmp_path, selection["artifacts"]),
        selection["approval_sources"],
    )
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["ENV-02-A-C03"]
    assert check["outcome"] == "PARTIAL"
    assert "PASS" not in predicates(check).values()
    assert check["diagnostic_refs"]


def test_raw_approval_pass_remains_compatible_partial_projection(modules, tmp_path):
    from rubric_links import check_approvals

    selection, measurements = approval_packet(tmp_path)
    del selection["approval_sources"]
    selection["artifacts"].pop()
    measurements["approvals"] = check_approvals(selection["approvals"], selection["decisions"], selection["artifacts"])
    assert measurements["approvals"]["status"] == "pass"
    rows, checks = report_checks(modules, measurements, selection, tmp_path)
    check = checks["ENV-02-A-C03"]
    assert check["outcome"] == "PARTIAL"
    assert check["evidence"][0]["value"] == {"status": "pass", "authority_authenticated": False}
    assert not check.get("predicate_results")
    assert rows["ENV-02-A"]["score"] is None


@pytest.mark.parametrize("invalid", [None, {}, "not-a-list"])
def test_malformed_native_selector_cannot_supply_evidence(modules, native_packet, tmp_path, invalid):
    from rubric_native import project_native

    fixture, selection, measurements = native_packet
    selection["native"]["receipt_ids"] = invalid
    measurements["native"] = project_native(tmp_path, fixture.identity, **selection["native"])
    _, checks = report_checks(modules, measurements, selection, tmp_path)
    assert checks["CREATE-07-C01"]["outcome"] == "BLOCKED"
    assert checks["CREATE-07-C01"]["evidence"] == []
