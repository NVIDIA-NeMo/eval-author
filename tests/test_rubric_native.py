# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic retained evidence, exercising existing control/accounting seams only."""

import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from eval_author_shared import task_evidence
    from rubric_identity import identify

    task = tmp_path / "tasks/example"
    task.mkdir(parents=True)
    (task / "verify.py").write_text("raise AssertionError('TASK MUST NEVER EXECUTE')\n")
    paths = {
        "manifest": tmp_path / "evidence/manifest.json",
        "receipt": tmp_path / "evidence/receipt.json",
        "result": tmp_path / "run/result.json",
        "trace": tmp_path / "run/agent/trajectory.json",
    }
    cases = {
        kind: {
            "kind": kind,
            "requirement": "Synthetic retained control",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "equals": 1}],
        }
        for kind in ("reference", "incorrect", "empty", "agent")
    }
    manifest = {
        "schema": task_evidence.MANIFEST_SCHEMA,
        "task_dir": str(task),
        "task_sha256": task_evidence.task_digest(task),
        "harbor_task_checksum": "synthetic-checksum",
        "contract": {
            "task_id": "example",
            "provider": "harbor",
            "native_run_id": "/id",
            "cases": cases,
            "alternative_not_applicable": "Synthetic fixture",
            "side_effect_not_applicable": "Synthetic fixture",
        },
    }
    result = {
        "id": "native-1",
        "task_name": "example",
        "trial_name": "trial-1",
        "task_checksum": "synthetic-checksum",
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 0}},
    }
    trace = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session-1",
        "trajectory_id": "trace-1",
        "agent": {"name": "synthetic", "version": "1"},
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "PRIVATE-MESSAGE",
                "tool_calls": [
                    {"tool_call_id": "call-1", "function_name": "read", "arguments": {"secret": "PRIVATE-ARGS"}}
                ],
                "observation": {"results": [{"source_call_id": "call-1", "content": "PRIVATE-RESULT"}]},
            }
        ],
    }
    receipt = {
        "schema": task_evidence.SCHEMA,
        "manifest": str(paths["manifest"]),
        "case": "reference",
        "run_id": "recording-1",
        "native_run_id": "native-1",
        "result": str(paths["result"]),
        "trace": str(paths["trace"]),
        "status": "behavior_failed",
        "returncode": 0,
        "error": "PRIVATE-EXCEPTION",
    }

    def seal():
        for key, value in (("manifest", manifest), ("result", result), ("trace", trace)):
            paths[key].parent.mkdir(parents=True, exist_ok=True)
            paths[key].write_text(json.dumps(value))
            receipt[key + "_sha256"] = task_evidence.digest(paths[key])
        paths["receipt"].write_text(json.dumps(receipt))
        records = [
            {
                "id": key,
                "role": key,
                "path": path.relative_to(tmp_path).as_posix(),
                "digest": task_evidence.digest(path),
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "synthetic",
                "evidence": [{"path": path.relative_to(tmp_path).as_posix(), "digest": task_evidence.digest(path)}],
            }
            for key, path in paths.items()
        ]
        checked = identify(tmp_path, records)
        assert checked["status"] == "pass"
        return checked

    identity = seal()
    selection = {
        "receipt_ids": ["receipt"],
        "manifest_ids": ["manifest"],
        "result_ids": ["result"],
        "trace_ids": ["trace"],
    }
    return SimpleNamespace(**locals())


def run_rubric_cli(panel, selection):
    selected = panel.tmp_path / "selection.json"
    selected.write_text(json.dumps(selection))
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
            "--root",
            str(panel.tmp_path),
            "--selection",
            str(selected),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_null_trace_retains_native_control_without_trace_graph(panel):
    panel.receipt["trace"] = None
    records = panel.seal()["records"]
    # seal refreshes trace hashes, so bind the producer-shaped null receipt last.
    panel.receipt["trace_sha256"] = None
    panel.paths["receipt"].write_text(json.dumps(panel.receipt))
    for record in records:
        if record["id"] == "receipt":
            record["digest"] = hashlib.sha256(panel.paths["receipt"].read_bytes()).hexdigest()
            record["evidence"][0]["digest"] = record["digest"]
    from rubric_identity import identify

    selected = identify(panel.tmp_path, [row for row in records if row["id"] != "trace"])
    output = adapter().project_native(panel.tmp_path, selected, **{**panel.selection, "trace_ids": []})
    assert output["status"] == "unknown"
    assert output["controls"][0]["classification"] == "false_rejection"
    assert output["subjects"][0]["trace"] is None
    assert output["subjects"][0]["trace_sha256"] is None
    assert {node["role"] for node in output["nodes"]} == {"task", "run"}
    assert output["link_check"]["status"] == "pass"


def test_native_trace_between_default_and_explicit_limit_is_supported(panel):
    panel.trace["private_padding"] = "x" * 1_048_576
    selected = panel.seal()
    output = adapter().project_native(panel.tmp_path, selected, **panel.selection)
    assert output["status"] == "unknown"
    assert output["link_check"]["status"] == "pass"
    assert output["subjects"][0]["trace_sha256"] == panel.receipt["trace_sha256"]
    assert "private_padding" not in json.dumps(output)


@pytest.mark.parametrize("access", ["missing", "denied", "unresolved"])
def test_native_respects_unavailable_full_selection_alias(panel, access):
    unavailable = dict(
        next(row for row in panel.identity["records"] if row["id"] == "trace"), id="unavailable", access=access
    )
    panel.identity["records"].append(unavailable)
    output = adapter().project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert not any(row["id"] == "trace" for row in output["bindings"])
    assert {"code": "native.access_" + access, "subject": "trace", "status": "unknown"} in output["diagnostics"]


def test_manifest_missing_controls_uses_historical_shared_validation(panel, monkeypatch):
    native = adapter()
    inspected = []
    original = native.audit.inspect_manifest

    def observed(*args, **kwargs):
        rows = original(*args, **kwargs)
        inspected.extend(rows)
        return rows

    monkeypatch.setattr(native.audit, "inspect_manifest", observed)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert inspected == [
        {
            "path": "evidence/manifest.json",
            "task": "tasks/example",
            "classification": "unproven",
            "reason": "Declared controls have no retained receipts",
            "cases": ["incorrect", "empty"],
        }
    ]
    codes = {row["code"] for row in output["diagnostics"]}
    assert "native.controls_missing" in codes
    assert "native.manifest_unproven" not in codes
    assert output["status"] == "unknown"
    assert output["task_freshness"] == "unknown"
    assert output["native_execution"] is False


def test_complete_historical_manifest_does_not_promote_native_pass(panel, monkeypatch):
    from rubric_identity import identify

    records = {"manifest": panel.identity["records"][0]}
    selection = {"manifest_ids": ["manifest"], "receipt_ids": [], "result_ids": [], "trace_ids": []}
    panel.result["verifier_result"]["rewards"]["reward"] = 1
    panel.receipt["status"] = "passed"
    for case in ("reference", "incorrect", "empty"):
        panel.receipt["case"] = case
        panel.receipt["native_run_id"] = panel.result["id"] = case
        panel.result["trial_name"] = case
        panel.trace["session_id"] = panel.trace["trajectory_id"] = case
        for role in ("receipt", "result", "trace"):
            panel.paths[role] = panel.paths[role].with_name(case + ".json")
        panel.receipt.update(result=str(panel.paths["result"]), trace=str(panel.paths["trace"]))
        for record in panel.seal()["records"]:
            if record["id"] != "manifest":
                role = record["id"]
                record["id"] = case + "-" + role
                records[record["id"]] = record
                selection[role + "_ids"].append(record["id"])
    selected = identify(panel.tmp_path, list(records.values()))
    (panel.task / "verify.py").write_text("raise AssertionError('CHANGED TASK MUST NEVER EXECUTE')\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Historical validation must not scan or execute the current task")

    monkeypatch.setattr(panel.task_evidence, "task_digest", forbidden)
    monkeypatch.setattr(panel.task_evidence, "run", forbidden)
    output = adapter().project_native(panel.tmp_path, selected, **selection)
    assert {row["classification"] for row in output["controls"]} == {"passed"}
    assert len(output["controls"]) == 3
    assert {row["code"] for row in output["diagnostics"]} == {"native.task_freshness_unmeasured"}
    assert output["status"] == "unknown"
    assert output["task_freshness"] == "unknown"
    assert output["native_execution"] is False


def test_cli_retains_failed_control_and_native_references(panel):
    result = run_rubric_cli(panel, {"artifacts": panel.identity["records"], "native": panel.selection})
    report = json.loads(result.stdout)
    native = report["measurements"]["native"]
    assert [
        {key: row[key] for key in ("receipt", "classification", "task_freshness")} for row in native["controls"]
    ] == [{"receipt": "receipt", "classification": "false_rejection", "task_freshness": "unknown"}]
    assert native["controls"][0]["integrity"]["status_claim"] == "unknown"
    assert {node["role"] for node in native["nodes"]} == {"task", "run", "trace", "tool_call", "result"}
    assert native["link_check"]["status"] == "pass"
    assert native["subjects"][0]["native_identity"] == "verified"
    assert native["status"] == "unknown"
    assert native["native_execution"] is False
    assert report["preflight_success"] is False
    assert report["anti_cheat_scores"] is None
    assert result.returncode == 1
    assert result.stderr == ""
    assert "PRIVATE-" not in result.stdout


@pytest.mark.parametrize("key", ["receipt_ids", "manifest_ids", "result_ids", "trace_ids"])
@pytest.mark.parametrize("value", [None, "PRIVATE-SELECTOR", {}, 0, [None], [0], [""], ["  "]])
def test_cli_rejects_malformed_native_id_lists(panel, key, value):
    selection = {**panel.selection, key: value}
    result = run_rubric_cli(panel, {"artifacts": panel.identity["records"], "native": selection})
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "schema": "rubric-measurements-v2",
        "status": "invalid",
        "code": "selection.invalid",
        "preflight_success": False,
    }
    assert result.stderr == ""
    assert "PRIVATE-" not in result.stdout


@pytest.mark.parametrize("value", [None, [], "PRIVATE-SELECTOR", 0, False])
def test_cli_rejects_nonobject_native_selection(panel, value):
    result = run_rubric_cli(panel, {"native": value})
    assert result.stderr == ""
    assert result.returncode == 2
    assert json.loads(result.stdout) == {
        "schema": "rubric-measurements-v2",
        "status": "invalid",
        "code": "selection.invalid",
        "preflight_success": False,
    }


@pytest.mark.parametrize("key", ["receipt_ids", "manifest_ids", "result_ids", "trace_ids", "PRIVATE-EXTRA"])
def test_cli_requires_exact_native_selector_keys(panel, key):
    selected = dict(panel.selection)
    if key == "PRIVATE-EXTRA":
        selected[key] = ["PRIVATE-VALUE"]
    else:
        del selected[key]
    result = run_rubric_cli(panel, {"native": selected})
    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "schema": "rubric-measurements-v2",
        "status": "invalid",
        "code": "selection.invalid",
        "preflight_success": False,
    }


@pytest.mark.parametrize("damage", ["missing-receipt", "unselected-result", "broken-call", "missing-trace"])
def test_cli_broken_native_references_cannot_pass(panel, damage):
    identity = panel.identity
    if damage == "missing-receipt":
        panel.selection["receipt_ids"] = ["missing-receipt"]
    elif damage == "unselected-result":
        panel.selection["result_ids"] = []
    elif damage == "broken-call":
        panel.trace["steps"][0]["observation"]["results"][0]["source_call_id"] = "PRIVATE-MISSING-CALL"
        identity = panel.seal()
    else:
        panel.paths["trace"].unlink()
    result = run_rubric_cli(panel, {"artifacts": identity["records"], "native": panel.selection})
    report = json.loads(result.stdout)
    native = report["measurements"]["native"]
    assert native["status"] == "fail"
    if damage == "missing-receipt":
        assert "native.reference_unconsumed" in {row["code"] for row in native["diagnostics"]}
    if damage == "broken-call":
        assert native["controls"][0]["classification"] == "false_rejection"
        assert native["subjects"]
        assert "native.tool_call_unresolved" in {row["code"] for row in native["diagnostics"]}
    assert report["preflight_success"] is False
    assert native["native_execution"] is False
    assert result.returncode == 1
    assert result.stderr == ""
    assert "PRIVATE-" not in result.stdout


def test_cli_native_projection_is_optional(panel):
    result = run_rubric_cli(panel, {"artifacts": panel.identity["records"]})
    report = json.loads(result.stdout)
    assert "native" not in report["measurements"]
    assert report["preflight_success"] is False
    assert result.stderr == ""


def adapter():
    assert (SCRIPTS / "rubric_native.py").is_file(), "bounded native adapter is missing"
    return importlib.import_module("rubric_native")


def test_failed_control_retained_through_actual_consumer(panel, monkeypatch):
    native = adapter()
    audit = importlib.import_module("audit_verifier_evidence")
    calls = []
    original = audit.inspect_receipt

    def observed(*args, **kwargs):
        row = original(*args, **kwargs)
        calls.append(row)
        return row

    monkeypatch.setattr(audit, "inspect_receipt", observed)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert calls[0]["historical_classification"] == "false_rejection"
    assert output["controls"][0]["classification"] == "false_rejection"
    assert output["controls"][0]["task_freshness"] == "unknown"
    assert output["status"] == "unknown"  # No selected current task-tree proof.
    assert {node["role"] for node in output["nodes"]} == {"task", "run", "trace", "tool_call", "result"}
    assert output["link_check"]["status"] == "pass"
    assert output["native_execution"] is False
    assert "PRIVATE-" not in json.dumps(output)
    assert output == native.project_native(panel.tmp_path, panel.identity, **panel.selection)


def test_same_projection_matches_actual_accounting_subject_resolver(panel):
    native = adapter()
    path = ROOT / "skills/eval-author-audit/scripts/audit_spec/measure.py"
    spec = importlib.util.spec_from_file_location("native_accounting_measure_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    pending = module._subject_from_trial_dir(panel.paths["result"].parent, task_id=None, run_id=None)
    loaded = module._load_trace(pending.trace_path, "atif")
    subject = module._finalize_subject(pending, loaded, source_kind="atif", evidence_class=None)
    projected = output["subjects"][0]
    assert subject.task_id == "example"
    assert subject.run_id == "trial-1"
    assert projected["accounting_task"] == native._ref("accounting_task", subject.task_id)
    assert projected["accounting_run"] == native._ref("accounting_run", subject.run_id)
    assert projected["trace_sha256"] == subject.trace_sha256
    assert subject.trace_sha256 == panel.identity["records"][3]["digest"]


@pytest.mark.parametrize(
    "damage,code",
    [
        ("missing-result", "tool_result_missing"),
        ("duplicate-result", "tool_result_duplicate"),
        ("missing-call", "tool_call_unresolved"),
        ("duplicate-step", "step_duplicate"),
    ],
)
def test_retains_incomplete_or_ambiguous_tool_evidence(panel, damage, code):
    step = panel.trace["steps"][0]
    if damage == "missing-result":
        step["observation"]["results"] = []
    elif damage == "duplicate-result":
        step["observation"]["results"] *= 2
    elif damage == "missing-call":
        step["observation"]["results"][0]["source_call_id"] = "other-run-call"
    else:
        panel.trace["steps"].append(dict(step))
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert "native." + code in {row["code"] for row in output["diagnostics"]}
    assert output["controls"][0]["classification"] == "false_rejection"
    assert output["nodes"]


def test_infrastructure_failure_keeps_native_references_unverified(panel):
    panel.receipt.update(status="infrastructure_error", returncode=1)
    panel.result["exception_info"] = {"message": "PRIVATE-NATIVE-ERROR"}
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["controls"][0]["classification"] == "unproven"
    assert output["subjects"][0]["native_identity"] == "unverified"
    assert {node["role"] for node in output["nodes"]} >= {"task", "run", "trace"}
    assert output["status"] == "unknown"
    assert "PRIVATE-" not in json.dumps(output)


@pytest.mark.parametrize(
    "damage",
    ["unselected", "missing", "changed", "symlink", "oversized", "malformed", "nonfinite", "duplicate-identity"],
)
def test_selected_read_boundary_fails_closed(panel, monkeypatch, damage):
    native = adapter()
    if damage == "unselected":
        panel.selection["result_ids"] = []
    elif damage == "missing":
        panel.paths["result"].unlink()
    elif damage == "changed":
        panel.paths["result"].write_text("PRIVATE-CHANGED")
    elif damage == "symlink":
        target = panel.tmp_path / "PRIVATE-TARGET"
        target.write_text("PRIVATE-CONTENT")
        panel.paths["result"].unlink()
        panel.paths["result"].symlink_to(target)
    elif damage == "oversized":
        monkeypatch.setattr(native, "MAX_BYTES", 1)
    elif damage in ("malformed", "nonfinite"):
        panel.paths["result"].write_text(
            '{"PRIVATE-KEY":1,"PRIVATE-KEY":2}' if damage == "malformed" else '{"PRIVATE-KEY":1e999}'
        )
        for record in panel.identity["records"]:
            if record["id"] == "result":
                record["digest"] = hashlib.sha256(panel.paths["result"].read_bytes()).hexdigest()
    else:
        panel.identity["records"].append(dict(panel.identity["records"][2]))

    def forbidden(*args, **kwargs):
        pytest.fail("implicit filesystem traversal or task execution")

    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(native.task_evidence, "task_digest", forbidden)
    monkeypatch.setattr(native.task_evidence, "run", forbidden)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert output["status"] == "fail"
    assert "PRIVATE-" not in json.dumps(output)
    assert not output["subjects"]


def test_same_trace_cannot_be_bound_to_two_native_runs(panel):
    native = adapter()
    first_identity = panel.identity
    first_selection = dict(panel.selection)
    other_receipt = panel.paths["receipt"].with_name("other.json")
    other_result = panel.paths["result"].with_name("other.json")
    result = {**panel.result, "id": "native-2", "trial_name": "trial-2"}
    other_result.write_text(json.dumps(result))
    receipt = {
        **panel.receipt,
        "native_run_id": "native-2",
        "run_id": "recording-2",
        "result": str(other_result),
        "result_sha256": hashlib.sha256(other_result.read_bytes()).hexdigest(),
    }
    other_receipt.write_text(json.dumps(receipt))
    for ident, path, template in (
        ("other-receipt", other_receipt, first_identity["records"][1]),
        ("other-result", other_result, first_identity["records"][2]),
    ):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        first_identity["records"].append(
            {
                **template,
                "id": ident,
                "path": path.relative_to(panel.tmp_path).as_posix(),
                "digest": checksum,
                "evidence": [{"path": path.relative_to(panel.tmp_path).as_posix(), "digest": checksum}],
            }
        )
    first_selection["receipt_ids"] = ["receipt", "other-receipt"]
    first_selection["result_ids"] = ["result", "other-result"]
    output = native.project_native(panel.tmp_path, first_identity, **first_selection)
    assert output["status"] == "fail"
    assert "native.trace_reused" in {row["code"] for row in output["diagnostics"]}
    assert len(output["controls"]) == 2


def test_missing_selected_receipt_retains_unknown_control(panel):
    panel.selection["receipt_ids"] = ["missing-receipt"]
    output = adapter().project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert [
        {key: row[key] for key in ("receipt", "classification", "task_freshness")} for row in output["controls"]
    ] == [{"receipt": "missing-receipt", "classification": "unproven", "task_freshness": "unknown"}]
    assert output["controls"][0]["integrity"]["status_claim"] == "unknown"
    assert "native.controls_missing" in {row["code"] for row in output["diagnostics"]}


def test_oversized_reference_array_not_partially_projected(panel, monkeypatch):
    native = adapter()
    monkeypatch.setattr(native, "MAX_REFERENCES", 8)
    panel.trace["steps"][0]["tool_calls"] *= 20
    output = native.project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert not any(node["role"] == "tool_call" for node in output["nodes"])


def test_falsy_malformed_native_containers_not_treated_as_absent(panel):
    panel.trace["steps"][0]["tool_calls"] = 0
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert "native.reference_invalid" in {row["code"] for row in output["diagnostics"]}


def test_trusted_root_alias_matches_resolved_root(panel):
    alias = panel.tmp_path / "alias"
    alias.symlink_to(panel.tmp_path, target_is_directory=True)
    native = adapter()
    assert native.project_native(alias, panel.identity, **panel.selection) == native.project_native(
        panel.tmp_path, panel.identity, **panel.selection
    )


def test_total_budget_stops_before_reading_next_artifact(panel, monkeypatch):
    native = adapter()
    monkeypatch.setattr(native, "MAX_TOTAL_BYTES", 1)
    opened = []
    import rubric_identity

    original = rubric_identity.os.open

    def observe(path, *args, **kwargs):
        opened.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(rubric_identity.os, "open", observe)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert output["status"] == "fail"
    assert len([path for path in opened if path.endswith(".json")]) <= 1


@pytest.mark.parametrize(
    "damage", ["run", "task", "checksum", "trace-hash", "manifest-hash", "call-duplicate", "parent-symlink"]
)
def test_conflicting_native_bindings_fail_without_payloads(panel, damage):
    if damage == "run":
        panel.receipt["native_run_id"] = "other-run"
    elif damage == "task":
        panel.result["task_name"] = "other-task"
    elif damage == "checksum":
        panel.result["task_checksum"] = "other-checksum"
    elif damage == "call-duplicate":
        panel.trace["steps"][0]["tool_calls"] *= 2
    identity = panel.seal()
    if damage in ("trace-hash", "manifest-hash"):
        panel.receipt[damage.replace("-hash", "_sha256")] = "0" * 64
        panel.paths["receipt"].write_text(json.dumps(panel.receipt))
        identity["records"][1]["digest"] = hashlib.sha256(panel.paths["receipt"].read_bytes()).hexdigest()
    elif damage == "parent-symlink":
        original = panel.paths["trace"].parent
        moved = original.with_name("relocated")
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    output = adapter().project_native(panel.tmp_path, identity, **panel.selection)
    assert output["status"] == "fail"
    assert "PRIVATE-" not in json.dumps(output)


def test_read_only_atime_change_does_not_invalidate_selected_bytes(panel, monkeypatch):
    native = adapter()
    import rubric_identity

    original = rubric_identity.os.fstat
    count = 0

    def observed(fd):
        nonlocal count
        value = original(fd)
        count += 1
        fields = list(value)
        fields[7] += count
        return rubric_identity.os.stat_result(fields)

    monkeypatch.setattr(rubric_identity.os, "fstat", observed)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert output["link_check"]["status"] == "pass"


@pytest.mark.parametrize(
    "kind,classification", [("reference", "passed"), ("incorrect", "failed_control"), ("agent", "agent_run")]
)
def test_legacy_manifest_preserves_actual_control_classifications(panel, kind, classification):
    panel.manifest["schema"] = panel.task_evidence.SCHEMA
    panel.receipt["case"] = kind
    if kind == "reference":
        panel.result["verifier_result"]["rewards"]["reward"] = 1
        panel.receipt["status"] = "passed"
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["controls"][0]["classification"] == classification
    assert output["link_check"]["status"] == "pass"
