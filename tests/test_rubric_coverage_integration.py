# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
from pathlib import Path

import pytest
from test_skill_contract import _AUDIT_MEASURE, _AUDIT_REPORT, _run_json_script, _write_atif_trace, _write_audit


def selected_measurement(tmp_path):
    audit = _write_audit(tmp_path)
    trace = tmp_path / "trace.json"
    _write_atif_trace(trace, tool_calls=["customer.lookup"])
    dispatch = tmp_path / "dispatch.json"
    dispatch.write_text(
        json.dumps(
            {
                "selector": "selected",
                "routes": {"selected": "scorer"},
                "dependencies": {"scorer": ["trace"], "trace": []},
            }
        )
    )
    scorer = tmp_path / "scorer.txt"
    scorer.write_text("Selected declaration only; never execute this file.")
    records = []
    for key, role, path in [
        ("trace", "trace", trace),
        ("dispatch", "dispatch", dispatch),
        ("scorer", "scorer", scorer),
    ]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "id": key,
                "role": role,
                "path": path.name,
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "reviewed selection",
                "evidence": [{"path": path.name, "digest": digest}],
            }
        )
    selection = {
        "artifacts": records,
        "route": {
            "entrypoint": "dispatch",
            "expected_scorer": "scorer",
            "dependencies": {"scorer": ["trace"], "trace": []},
        },
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))
    args = [
        "--audit",
        str(audit),
        "--trace",
        str(trace),
        "--task-id",
        "task:/stable",
        "--run-id",
        "run:/stable",
        "--evidence-class",
        "agent_under_test",
        "--out-dir",
        str(tmp_path / "measurements"),
    ]
    return audit, path, selection, args


@pytest.mark.parametrize("consumer", ["measure", "report"])
def test_identity_consumer_requires_installed_sibling(tmp_path, consumer):
    import shutil

    audit, path, _, args = selected_measurement(tmp_path)
    code, measured, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or measured
    installed = tmp_path / "skills/eval-author-audit"
    shutil.copytree(_AUDIT_MEASURE.parents[2], installed, ignore=shutil.ignore_patterns("__pycache__"))
    if consumer == "measure":
        script = installed / "scripts/audit_spec/measure.py"
        argv = [*args, "--identity-root", str(tmp_path), "--identity-selection", str(path)]
    else:
        script = installed / "scripts/audit_spec/report.py"
        argv = [
            "--audit",
            str(audit),
            "--coverage",
            measured["measurements"][0]["coverage"],
            "--out",
            str(tmp_path / "report.json"),
        ]
    code, result, stderr = _run_json_script(script, *argv)
    assert stderr == ""
    assert code == 2 and result["error_type"] == "environment", result
    assert "Install the sibling eval-author skill" in result["error"]
    assert not result["written"]


@pytest.mark.parametrize("consumer", ["measure", "report"])
def test_identity_consumer_does_not_grow_sibling_import_path(tmp_path, consumer):
    import runpy
    import sys

    audit, path, _, args = selected_measurement(tmp_path)
    script = _AUDIT_MEASURE if consumer == "measure" else _AUDIT_REPORT
    module = runpy.run_path(str(script))
    if consumer == "measure":
        import argparse

        arguments = argparse.Namespace(identity_root=tmp_path, identity_selection=path, task_id="task", run_id="run")

        def run():
            return module["_identity_preflight"](arguments)
    else:
        code, result, stderr = _run_json_script(
            _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
        )
        assert code == 0, stderr or result
        coverage = json.loads(Path(result["measurements"][0]["coverage"]).read_text())

        def run():
            return module["_validate_selected_trace"](coverage, artifact_path=Path)

    sibling = str(script.resolve().parents[3] / "eval-author/scripts")
    run()
    before = sys.path.count(sibling)
    run()
    assert sys.path.count(sibling) == before


@pytest.mark.parametrize("schema_name", ["audit_coverage.schema.json", "audit_coverage_report.schema.json"])
def test_coverage_schema_requires_route_request_in_identity_envelope(schema_name):
    from jsonschema import Draft202012Validator

    schema = json.loads((_AUDIT_MEASURE.parents[2] / "schemas" / schema_name).read_text())
    envelope = (
        schema["properties"]["identity_preflight"]
        if schema_name == "audit_coverage.schema.json"
        else schema["$defs"]["inputReport"]["properties"]["identity_preflight"]
    )
    validator = Draft202012Validator(envelope)
    assert list(validator.iter_errors({}))
    assert list(validator.iter_errors({"route_request": None}))
    assert list(validator.iter_errors({"route_request": {}}))


@pytest.mark.parametrize("root_form", ["relative-parent", "dotdot", "symlink"])
def test_measurement_normalizes_identity_root_at_entry(tmp_path, root_form):
    import os

    _, path, _, args = selected_measurement(tmp_path)
    if root_form == "relative-parent":
        root = Path(os.path.relpath(tmp_path))
    elif root_form == "dotdot":
        (tmp_path / "child").mkdir()
        root = tmp_path / "child/.."
    else:
        root = tmp_path / "alias"
        root.symlink_to(tmp_path, target_is_directory=True)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(root), "--identity-selection", str(path)
    )
    assert code == 0 and stderr == "", result
    assert result["written"] and result["identity_preflight"]["measurement_allowed"]


def test_measurement_rejects_symlinks_below_normalized_root(tmp_path):
    _, path, selection, args = selected_measurement(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(tmp_path / "trace.json")
    selection["artifacts"][0]["path"] = alias.name
    selection["artifacts"][0]["evidence"][0]["path"] = alias.name
    path.write_text(json.dumps(selection))
    args[args.index("--trace") + 1] = str(alias)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == "" and not result["written"]
    assert result["identity_preflight"]["identity"]["status"] == "fail"


@pytest.mark.parametrize("damage", ["extra", "missing-artifacts", "missing-route"])
def test_measurement_requires_exact_identity_selection_keys(tmp_path, damage):
    _, path, selection, args = selected_measurement(tmp_path)
    if damage == "extra":
        selection["unexpected"] = True
    else:
        selection.pop(damage.removeprefix("missing-"))
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == "", result
    assert result["error"] == "identity.selection_invalid" and not result["written"]


def test_identity_help_describes_root_without_internal_workflow_label(capsys):
    import runpy

    main = runpy.run_path(str(_AUDIT_MEASURE))["main"]
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "W" + "02" not in help_text
    assert "paths are relative to" in " ".join(help_text.split())


def test_unbound_dependency_claim_cannot_authorize_measurement(tmp_path):
    _, path, selection, args = selected_measurement(tmp_path)
    dispatch_path = tmp_path / "dispatch.json"
    dispatch = json.loads(dispatch_path.read_text())
    del dispatch["dependencies"]
    dispatch_path.write_text(json.dumps(dispatch))
    record = selection["artifacts"][1]
    record["digest"] = hashlib.sha256(dispatch_path.read_bytes()).hexdigest()
    record["evidence"][0]["digest"] = record["digest"]
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == ""
    preflight = result["identity_preflight"]
    assert preflight["identity"]["status"] == preflight["route"]["status"] == "pass"
    assert preflight["route"]["bound_dependency"] is False
    assert preflight["measurement_allowed"] is False
    assert not result["written"] and not (tmp_path / "measurements").exists()


@pytest.mark.parametrize("missing_flag", ["--identity-root", "--identity-selection"])
def test_identity_flags_must_be_paired(tmp_path, missing_flag):
    _, path, _, args = selected_measurement(tmp_path)
    identity_args = ["--identity-root", str(tmp_path), "--identity-selection", str(path)]
    offset = identity_args.index(missing_flag)
    del identity_args[offset : offset + 2]
    code, result, stderr = _run_json_script(_AUDIT_MEASURE, *args, *identity_args)
    assert code == 1 and stderr == ""
    assert result["error"] == "--identity-root and --identity-selection must be supplied together"
    assert not result["written"] and not (tmp_path / "measurements").exists()


def test_changed_dependency_projection_cannot_authorize_measurement(tmp_path):
    _, path, selection, args = selected_measurement(tmp_path)
    selection["route"]["dependencies"] = {"scorer": []}
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == ""
    preflight = result["identity_preflight"]
    assert preflight["identity"]["status"] == "pass"
    assert preflight["route"]["diagnostics"] == [
        {"code": "route.dependency_source_mismatch", "subject": "dispatch", "status": "fail"}
    ]
    assert not preflight["measurement_allowed"] and not preflight["route"]["bound_dependency"]
    assert not result["written"] and not (tmp_path / "measurements").exists()


@pytest.mark.parametrize("source_kind", ["atif", "mlflow", "gym", "trace"])
def test_actual_measurement_consumes_common_identity_before_aggregation(tmp_path, monkeypatch, source_kind):
    from test_trace_loading import MLFLOW_FIXTURE, SCRIPT, gym_record

    audit, path, selection, args = selected_measurement(tmp_path)
    trace_path = tmp_path / "trace.json"
    if source_kind == "mlflow":
        trace_path.write_bytes(MLFLOW_FIXTURE.read_bytes())
    elif source_kind == "gym":
        trace_path.write_text(json.dumps(gym_record()))
    elif source_kind == "trace":
        monkeypatch.syspath_prepend(str(SCRIPT.parent))
        import trace_loading

        [trace] = trace_loading.load_traces(trace_path, "atif")
        trace_path.write_text(trace.model_dump_json() + "\n")
    digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    selection["artifacts"][0].update(digest=digest, evidence=[{"path": trace_path.name, "digest": digest}])
    path.write_text(json.dumps(selection))
    args.extend(["--source-kind", source_kind])
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    preflight = result["identity_preflight"]
    assert preflight["identity"] == {"status": "pass", "records": selection["artifacts"], "diagnostics": []}
    assert preflight["route"]["status"] == "pass"
    assert preflight["route"]["bound_dependency"] is True
    assert preflight["route"]["selected"] == ["scorer", "trace"]
    assert preflight["route"]["executed_scorer"] is None
    assert preflight["preflight_success"] is False
    assert preflight["subject"] == {"task_id": "task:/stable", "run_id": "run:/stable"}
    coverage = json.loads(Path(result["measurements"][0]["coverage"]).read_text())
    assert coverage["identity_preflight"] == preflight
    assert coverage["subject"]["task_id"] == "task:/stable"
    assert coverage["subject"]["run_id"] == "run:/stable"
    assert coverage["binding"]["trace_sha256"] == selection["artifacts"][0]["digest"]
    assert coverage["schema"] == "nemo.eval_author.audit_coverage.v2"
    assert coverage["subject"]["trace_format"] == "nemo-compass-trace"
    assert coverage["subject"]["source_kind"] == source_kind
    assert coverage["covered"] == (["customer.lookup"] if source_kind in {"atif", "trace"} else [])
    aggregate = tmp_path / "aggregate.json"
    code, summary, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage",
        result["measurements"][0]["coverage"],
        "--out",
        str(aggregate),
    )
    assert code == 0, stderr or summary
    report = json.loads(aggregate.read_text())
    assert report["input_reports"][0]["subject"] == coverage["subject"]
    assert report["input_reports"][0]["identity_preflight"] == preflight
    assert report["input_reports"][0]["item_kind_count"] == coverage["item_kind_count"]
    before = aggregate.read_bytes()
    code, result, stderr = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(aggregate))
    assert code == 0, stderr or result
    assert aggregate.read_bytes() == before


@pytest.mark.parametrize("relocate", [False, True])
def test_selected_aggregate_preserves_nested_source_root_and_evidence(tmp_path, relocate):
    import shutil

    root = tmp_path / "sources"
    root.mkdir()
    audit, path, selection, args = selected_measurement(root)
    nested = root / "nested"
    nested.mkdir()
    trace = nested / "trace.json"
    trace.write_bytes((root / "trace.json").read_bytes())
    other = json.loads(json.dumps(selection["artifacts"][0]))
    other["id"] = "other-trace"
    selection["artifacts"].append(other)
    selection["artifacts"][0]["path"] = "nested/trace.json"
    selection["artifacts"][0]["evidence"].append({"path": "nested/trace.json", "digest": other["digest"]})
    path.write_text(json.dumps(selection))
    args[args.index("--trace") + 1] = str(trace)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(root), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    aggregate = root / "reports/aggregate.json"
    code, summary, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage",
        result["measurements"][0]["coverage"],
        "--out",
        str(aggregate),
    )
    assert code == 0, stderr or summary
    if relocate:
        moved = tmp_path / "moved"
        shutil.move(root, moved)
        aggregate = moved / "reports/aggregate.json"
    before = aggregate.read_bytes()
    code, result, stderr = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(aggregate))
    assert code == 0, stderr or result
    assert aggregate.read_bytes() == before


@pytest.mark.parametrize("operation", ["aggregate", "verify-existing"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity.records", None),
        ("identity.records", []),
        ("identity.records", [None]),
        ("identity.records.0.evidence", []),
        ("identity.records.0.evidence", [None]),
        ("identity.records.0.evidence.0.digest", []),
        ("identity.records.0.path", "./trace.json"),
        ("identity.records.0.path", "nested/../trace.json"),
        ("identity.records.0.role", "scorer"),
        ("route", None),
        ("route_request", None),
        ("route_request", {}),
        ("route_request", []),
        ("route_request", {"entrypoint": "dispatch", "expected_scorer": "scorer", "dependencies": {}, "extra": True}),
        ("route_request.expected_scorer", "other-scorer"),
        ("route_request.dependencies", {"scorer": []}),
        ("route_request.dependency_source", {"artifact_id": "dispatch", "pointer": "/does/not/exist"}),
        ("route.records", None),
        ("route.records", [{}]),
        ("route.selected", "scorer"),
        ("route.selected", ["scorer", "scorer"]),
        ("route.selected", ["unknown"]),
        ("route.scorer", None),
        ("route.executed_scorer", "scorer"),
        ("route.dependency_source", None),
        ("route.dependency_source", {}),
        ("route.dependency_source.digest", "0" * 64),
        ("route.dependency_source.pointer", []),
        ("route.dependency_source.pointer", "not-a-pointer"),
        ("route.diagnostics", [{"code": "route.invalid", "subject": "route", "status": "fail"}]),
        ("identity.diagnostics", None),
        ("measurement_allowed", 1),
        ("preflight_success", 0),
    ],
)
def test_selected_preflight_envelope_is_validated(tmp_path, operation, field, value):
    _tampered_selected_report(tmp_path, operation, field=field, value=value)


@pytest.mark.parametrize("operation", ["aggregate", "verify-existing"])
@pytest.mark.parametrize("mutation", ["digest", "path", "evidence-digest", "evidence-path"])
def test_selected_trace_identity_cannot_be_laundered(tmp_path, operation, mutation):
    _tampered_selected_report(tmp_path, operation, mutation=mutation)


@pytest.mark.parametrize("operation", ["aggregate", "verify-existing"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("route.scorer", "other-scorer"),
        ("route.selected", ["scorer"]),
        ("route.dependency_source.pointer", "/does/not/exist"),
    ],
)
def test_saved_route_is_recomputed_from_current_sources(tmp_path, operation, field, value):
    _tampered_selected_report(tmp_path, operation, field=field, value=value)


def _tampered_selected_report(tmp_path, operation, *, mutation=None, field=None, value=None):
    audit, path, selection, args = selected_measurement(tmp_path)
    if field == "route.scorer" and value == "other-scorer":
        other = json.loads(json.dumps(selection["artifacts"][2]))
        other["id"] = "other-scorer"
        selection["artifacts"].append(other)
        graph = {"scorer": ["trace", "other-scorer"], "other-scorer": [], "trace": []}
        selection["route"]["dependencies"] = graph
        dispatch_path = tmp_path / "dispatch.json"
        dispatch = json.loads(dispatch_path.read_text())
        dispatch["dependencies"] = graph
        dispatch_path.write_text(json.dumps(dispatch))
        digest = hashlib.sha256(dispatch_path.read_bytes()).hexdigest()
        selection["artifacts"][1].update(digest=digest, evidence=[{"path": dispatch_path.name, "digest": digest}])
        path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    coverage_path = Path(result["measurements"][0]["coverage"])
    coverage = json.loads(coverage_path.read_text())
    aggregate = tmp_path / "aggregate.json"
    aggregate_args = ["--audit", str(audit), "--coverage", str(coverage_path), "--out", str(aggregate)]
    code, result, stderr = _run_json_script(_AUDIT_REPORT, *aggregate_args)
    assert code == 0, stderr or result
    if field is not None:
        target = coverage["identity_preflight"]
        keys = field.split(".")
        for key in keys[:-1]:
            target = target[int(key) if isinstance(target, list) else key]
        target[keys[-1]] = value
    else:
        assert isinstance(mutation, str)
        record = coverage["identity_preflight"]["identity"]["records"][0]
        target = record["evidence"][0] if mutation.startswith("evidence-") else record
        target["digest" if mutation.endswith("digest") else "path"] = (
            "0" * 64 if mutation.endswith("digest") else "unrelated.json"
        )
    coverage_path.write_text(json.dumps(coverage))
    if operation == "verify-existing":
        report = json.loads(aggregate.read_text())
        report["input_reports"][0]["identity_preflight"] = coverage["identity_preflight"]
        report["binding"]["inputs"][0]["sha256"] = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
        aggregate.write_text(json.dumps(report))
        report_args = ["--verify-existing", str(aggregate)]
    else:
        aggregate.unlink()
        report_args = aggregate_args
    before = aggregate.read_bytes() if aggregate.exists() else None
    code, result, stderr = _run_json_script(_AUDIT_REPORT, *report_args)
    assert code == 1 and stderr == "", result
    expected_error = (
        "coverage_report" if operation == "verify-existing" and field == "route_request" else "coverage_input"
    )
    assert result["error_type"] == expected_error and not result["written"]
    assert "identity" in result["error"]
    assert (aggregate.read_bytes() if aggregate.exists() else None) == before


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_actual_measurement_retains_access_unknown_without_writing_zero_coverage(tmp_path, access):
    _, path, selection, args = selected_measurement(tmp_path)
    record = selection["artifacts"][0]
    record.update(access=access, digest=None, evidence=[])
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and not result["written"] and stderr == ""
    preflight = result["identity_preflight"]
    assert preflight["identity"]["status"] == "unknown"
    assert preflight["identity"]["records"] == selection["artifacts"]
    assert preflight["identity"]["diagnostics"] == [
        {"code": "identity.access_" + access, "subject": "trace", "status": "unknown"}
    ]
    assert preflight["measurement_allowed"] is False
    assert preflight["preflight_success"] is False
    assert preflight["subject"] == {"task_id": "task:/stable", "run_id": "run:/stable"}
    assert not (tmp_path / "measurements").exists()


@pytest.mark.parametrize("route_problem", ["dynamic", "wrong-scorer", "missing-dependency"])
def test_route_failure_cannot_become_successful_measurement(tmp_path, route_problem):
    _, path, selection, args = selected_measurement(tmp_path)
    if route_problem == "dynamic":
        dispatch = tmp_path / "dispatch.json"
        dispatch.write_text(json.dumps({"selector": None, "routes": {"selected": "scorer"}}))
        digest = hashlib.sha256(dispatch.read_bytes()).hexdigest()
        selection["artifacts"][1].update(digest=digest, evidence=[{"path": dispatch.name, "digest": digest}])
        expected = "route.dynamic_unresolved"
    elif route_problem == "wrong-scorer":
        selection["route"]["expected_scorer"] = "different-scorer"
        expected = "route.scorer_mismatch"
    else:
        selection["route"]["dependencies"] = {}
        dispatch = tmp_path / "dispatch.json"
        document = json.loads(dispatch.read_text())
        document["dependencies"] = {}
        dispatch.write_text(json.dumps(document))
        digest = hashlib.sha256(dispatch.read_bytes()).hexdigest()
        selection["artifacts"][1].update(digest=digest, evidence=[{"path": dispatch.name, "digest": digest}])
        expected = "route.dependencies_unresolved"
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and not result["written"] and stderr == ""
    preflight = result["identity_preflight"]
    assert preflight["identity"]["status"] == "pass"
    assert preflight["route"]["status"] == ("fail" if route_problem == "wrong-scorer" else "unknown")
    assert [row["code"] for row in preflight["route"]["diagnostics"]] == [expected]
    assert not preflight["measurement_allowed"] and not preflight["preflight_success"]
    assert not (tmp_path / "measurements").exists()


def test_missing_selected_bytes_remain_unmeasured(tmp_path):
    _, path, _, args = selected_measurement(tmp_path)
    (tmp_path / "trace.json").unlink()
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == ""
    assert result["identity_preflight"]["identity"]["diagnostics"] == [
        {"code": "identity.artifact_missing", "subject": "trace", "status": "unknown"}
    ]
    assert not result["written"] and not (tmp_path / "measurements").exists()


def test_valid_unrelated_selection_cannot_authorize_a_different_trace(tmp_path):
    _, path, _, args = selected_measurement(tmp_path)
    other = tmp_path / "other.json"
    _write_atif_trace(other, tool_calls=[])
    args[args.index("--trace") + 1] = str(other)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == ""
    assert result["error"] == "identity.measured_trace_mismatch"
    assert not result["written"] and not (tmp_path / "measurements").exists()


def test_unselected_trace_is_rejected_before_opening_or_parsing(tmp_path, monkeypatch, capsys):
    import runpy

    _, path, _, args = selected_measurement(tmp_path)
    other = tmp_path / "unselected.json"
    _write_atif_trace(other, tool_calls=[])
    args[args.index("--trace") + 1] = str(other)
    main = runpy.run_path(str(_AUDIT_MEASURE))["main"]

    def forbidden(*args, **kwargs):
        pytest.fail("Unselected trace must not reach the native parser")

    monkeypatch.setitem(main.__globals__, "_load_trace", forbidden)
    assert main([*args, "--identity-root", str(tmp_path), "--identity-selection", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "identity.measured_trace_mismatch"
    assert not result["written"]


@pytest.mark.parametrize("size", [1_048_577, "limit", "over-limit"])
def test_selected_trace_uses_shared_explicit_trace_limit(tmp_path, size):
    import runpy

    identity_api = runpy.run_path(str(_AUDIT_MEASURE))["identity_module"]()
    limit = identity_api.artifact_byte_limit({"role": "trace"})
    assert limit > 1_048_576
    size = limit if size == "limit" else limit + 1 if size == "over-limit" else size
    audit, path, selection, args = selected_measurement(tmp_path)
    trace = tmp_path / "trace.json"
    with trace.open("ab") as stream:
        stream.write(b" " * (size - trace.stat().st_size))
    digest = hashlib.sha256(trace.read_bytes()).hexdigest()
    selection["artifacts"][0].update(digest=digest, evidence=[{"path": trace.name, "digest": digest}])
    path.write_text(json.dumps(selection))
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert stderr == ""
    if size > limit:
        assert code == 1 and not result["written"]
        assert any(
            row["code"] == "identity.source_too_large"
            for row in result["identity_preflight"]["identity"]["diagnostics"]
        )
        return
    assert code == 0 and result["written"], result
    aggregate = tmp_path / "aggregate.json"
    code, result, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage",
        result["measurements"][0]["coverage"],
        "--out",
        str(aggregate),
    )
    assert code == 0 and stderr == "", result
    code, result, stderr = _run_json_script(_AUDIT_REPORT, "--verify-existing", str(aggregate))
    assert code == 0 and stderr == "", result


@pytest.mark.parametrize("damage", ["symlink", "oversize", "changed-bytes"])
def test_selected_trace_replacement_after_identity_is_rejected_before_parsing(tmp_path, damage, monkeypatch, capsys):
    import runpy

    _, path, _, args = selected_measurement(tmp_path)
    module = runpy.run_path(str(_AUDIT_MEASURE))
    main = module["main"]
    original_subject = main.__globals__["_subject"]

    def replace_after_identity(arguments):
        subject = original_subject(arguments)
        trace = subject.trace_path
        if damage == "symlink":
            target = tmp_path / "replacement.json"
            trace.rename(target)
            trace.symlink_to(target)
        elif damage == "changed-bytes":
            trace.write_bytes(trace.read_bytes().replace(b"customer.lookup", b"customer.update"))
        else:
            with trace.open("ab") as stream:
                limit = module["identity_module"]().artifact_byte_limit({"role": "trace"})
                stream.write(b" " * (limit + 1 - trace.stat().st_size))
        return subject

    monkeypatch.setitem(main.__globals__, "_subject", replace_after_identity)

    def forbidden(*args, **kwargs):
        pytest.fail("Changed selected bytes must not reach Compass")

    monkeypatch.setitem(main.__globals__, "_load_trace", forbidden)
    assert main([*args, "--identity-root", str(tmp_path), "--identity-selection", str(path)]) == 1
    result = json.loads(capsys.readouterr().out)
    expected = "identity.measured_trace_mismatch" if damage == "changed-bytes" else "identity.measured_trace_invalid"
    assert result["error"] == expected
    assert not result["written"] and not (tmp_path / "measurements").exists()


def test_ordinary_measurement_does_not_activate_rubric_assessment(tmp_path):
    _, _, _, args = selected_measurement(tmp_path)
    code, result, stderr = _run_json_script(_AUDIT_MEASURE, *args)
    assert code == 0, stderr or result
    assert "identity_preflight" not in result
    coverage = json.loads(Path(result["measurements"][0]["coverage"]).read_text())
    assert "identity_preflight" not in coverage
    assert coverage["covered"] == ["customer.lookup"]


@pytest.mark.parametrize("key", ["identity", "route", "unbound", "binding_missing"])
def test_aggregator_cannot_promote_unresolved_producer_status(tmp_path, key):
    audit, path, _, args = selected_measurement(tmp_path)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    coverage_path = Path(result["measurements"][0]["coverage"])
    coverage = json.loads(coverage_path.read_text())
    if key == "unbound":
        coverage["identity_preflight"]["route"]["bound_dependency"] = False
    elif key == "binding_missing":
        coverage["identity_preflight"]["route"].pop("bound_dependency")
    else:
        coverage["identity_preflight"][key]["status"] = "unknown"
    coverage_path.write_text(json.dumps(coverage))
    aggregate = tmp_path / "aggregate.json"
    code, result, stderr = _run_json_script(
        _AUDIT_REPORT, "--audit", str(audit), "--coverage", str(coverage_path), "--out", str(aggregate)
    )
    assert code == 1 and not result["written"] and stderr == ""
    assert "unresolved identity or route" in result["error"]
    assert not aggregate.exists()


@pytest.mark.parametrize("damage", ["symlink", "symlink-parent", "oversize", "fifo"])
def test_identity_selection_unsafe_file_is_not_read(tmp_path, damage):
    import os

    _, path, _, args = selected_measurement(tmp_path)
    if damage == "symlink":
        alias = tmp_path / "alias.json"
        alias.symlink_to(path)
        path = alias
    elif damage == "symlink-parent":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        path = alias / path.name
    elif damage == "oversize":
        with path.open("ab") as stream:
            stream.write(b" " * (1_048_577 - path.stat().st_size))
    else:
        path.unlink()
        os.mkfifo(path)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 1 and stderr == ""
    assert result["error"] == "identity.selection_invalid"
    assert not result["written"]


def test_trial_measurement_preserves_provider_task_run_ids_through_aggregate(tmp_path):
    audit, path, selection, args = selected_measurement(tmp_path)
    trial = tmp_path / "trial"
    (trial / "agent").mkdir(parents=True)
    trace = trial / "agent/trajectory.json"
    trace.write_bytes((tmp_path / "trace.json").read_bytes())
    (trial / "result.json").write_text(json.dumps({"task_name": "native-task", "trial_name": "native-run"}))
    selection["artifacts"][0]["path"] = "trial/agent/trajectory.json"
    selection["artifacts"][0]["evidence"][0]["path"] = "trial/agent/trajectory.json"
    path.write_text(json.dumps(selection))
    for flag in ("--task-id", "--run-id"):
        index = args.index(flag)
        del args[index : index + 2]
    index = args.index("--trace")
    args[index : index + 2] = ["--trial-dir", str(trial)]
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    assert result["identity_preflight"]["subject"] == {"task_id": "native-task", "run_id": "native-run"}
    aggregate = tmp_path / "aggregate.json"
    code, summary, stderr = _run_json_script(
        _AUDIT_REPORT,
        "--audit",
        str(audit),
        "--coverage",
        result["measurements"][0]["coverage"],
        "--out",
        str(aggregate),
    )
    assert code == 0, stderr or summary
    report = json.loads(aggregate.read_text())
    assert report["input_reports"][0]["identity_preflight"] == result["identity_preflight"]


@pytest.mark.parametrize("mutation", ["subject", "access", "empty-records"])
def test_aggregate_rejects_inconsistent_passing_identity_envelope(tmp_path, mutation):
    audit, path, _, args = selected_measurement(tmp_path)
    code, result, stderr = _run_json_script(
        _AUDIT_MEASURE, *args, "--identity-root", str(tmp_path), "--identity-selection", str(path)
    )
    assert code == 0, stderr or result
    coverage_path = Path(result["measurements"][0]["coverage"])
    coverage = json.loads(coverage_path.read_text())
    if mutation == "subject":
        coverage["identity_preflight"]["subject"]["run_id"] = "unrelated"
    elif mutation == "access":
        coverage["identity_preflight"]["identity"]["records"][0]["access"] = "unresolved"
    else:
        coverage["identity_preflight"]["identity"]["records"] = []
    coverage_path.write_text(json.dumps(coverage))
    aggregate = tmp_path / "aggregate.json"
    code, result, stderr = _run_json_script(
        _AUDIT_REPORT, "--audit", str(audit), "--coverage", str(coverage_path), "--out", str(aggregate)
    )
    assert code == 1 and not result["written"] and stderr == ""
    assert not aggregate.exists()
