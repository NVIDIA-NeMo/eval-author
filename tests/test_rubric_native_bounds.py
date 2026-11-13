# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Boundary regressions for selected, retained native evidence."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rubric_native import adapter, run_rubric_cli  # noqa: E402
from test_rubric_native import panel as native_panel  # noqa: E402

panel = native_panel


def test_read_rejects_root_ancestor_symlink(panel):
    native = adapter()
    alias = panel.tmp_path / "alias"
    alias.symlink_to(panel.tmp_path, target_is_directory=True)
    record = next(row for row in panel.identity["records"] if row["id"] == "manifest")
    record = {**record, "path": "manifest.json"}
    with pytest.raises((OSError, ValueError)):
        native._read(alias / "evidence", record, native.MAX_BYTES)


def test_root_replaced_after_preflight_is_not_followed(panel, monkeypatch):
    native = adapter()
    original = native._read
    moved = panel.tmp_path.with_name(panel.tmp_path.name + "-moved")
    swapped = False

    def replace_ancestor(root, record, limit):
        nonlocal swapped
        if not swapped:
            panel.tmp_path.rename(moved)
            panel.tmp_path.symlink_to(moved, target_is_directory=True)
            swapped = True
        return original(root, record, limit)

    monkeypatch.setattr(native, "_read", replace_ancestor)
    output = native.project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert output["status"] == "fail"
    assert output["bindings"] == []


@pytest.mark.parametrize("limit", [0, 4])
def test_empty_trace_cannot_exceed_reference_budget(panel, monkeypatch, limit):
    native = adapter()
    monkeypatch.setattr(native, "MAX_REFERENCES", limit)
    panel.trace["steps"] = []
    output = native.project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert len(output["nodes"]) + len(output["links"]) <= limit


@pytest.mark.parametrize("diagnostics", [[None], ["PRIVATE-DIAGNOSTIC"], {}, None])
def test_malformed_identity_diagnostics_fail_without_exception_text(panel, diagnostics):
    panel.identity["diagnostics"] = diagnostics
    output = adapter().project_native(panel.tmp_path, panel.identity, **panel.selection)
    assert output["status"] == "fail"
    assert output["bindings"] == []
    assert "PRIVATE-" not in json.dumps(output)


@pytest.mark.parametrize("value", [[], {"PRIVATE-KEY": "PRIVATE-VALUE"}])
@pytest.mark.parametrize("field", ["session_id", "tool_call_id", "source_call_id", "native_run_id", "case"])
def test_unhashable_native_text_is_rejected_without_payloads(panel, value, field):
    if field == "session_id":
        panel.trace[field] = value
    elif field == "tool_call_id":
        panel.trace["steps"][0]["tool_calls"][0][field] = value
    elif field == "source_call_id":
        panel.trace["steps"][0]["observation"]["results"][0][field] = value
    else:
        panel.receipt[field] = value
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert "PRIVATE-" not in json.dumps(output)


@pytest.mark.parametrize(
    "field,value",
    [(field, value) for field in ("config", "agent") for value in ([], None, "", 0, False, ["PRIVATE-PAYLOAD"])]
    + [("name", value) for value in ([], {}, 0, False, ["PRIVATE-PAYLOAD"])],
)
@pytest.mark.parametrize("case", ["incorrect", "reference", "empty", "agent"])
def test_cli_rejects_malformed_control_config_before_classification(panel, field, value, case):
    panel.receipt.update(case=case, status="passed")
    panel.result["verifier_result"]["rewards"]["reward"] = 1
    panel.result["config"] = {"agent": {"name": "oracle"}}
    if field == "config":
        panel.result[field] = value
    elif field == "agent":
        panel.result["config"][field] = value
    else:
        panel.result["config"]["agent"][field] = value
    identity = panel.seal()
    result = run_rubric_cli(panel, {"artifacts": identity["records"], "native": panel.selection})
    assert result.stderr == ""
    assert result.returncode == 1
    native = json.loads(result.stdout)["measurements"]["native"]
    assert native["status"] == "fail"
    assert native["controls"] == [{"receipt": "receipt", "classification": "unproven", "task_freshness": "unknown"}]
    assert "native.reference_invalid" in {row["code"] for row in native["diagnostics"]}
    assert not native["subjects"]
    assert "PRIVATE-" not in result.stdout


@pytest.mark.parametrize(
    "config,classification",
    [
        ({}, "passed"),
        ({"agent": {}}, "passed"),
        ({"agent": {"name": None, "import_path": "synthetic.Agent"}}, "passed"),
        ({"agent": {"name": "oracle"}}, "passed"),
        ({"agent": {"name": "nop"}}, "unproven"),
    ],
)
def test_cli_valid_optional_control_config_uses_shared_classification(panel, config, classification):
    panel.receipt.update(case="incorrect", status="passed")
    panel.result["verifier_result"]["rewards"]["reward"] = 1
    panel.result["config"] = config
    identity = panel.seal()
    result = run_rubric_cli(panel, {"artifacts": identity["records"], "native": panel.selection})
    assert result.stderr == ""
    assert result.returncode == 1
    native = json.loads(result.stdout)["measurements"]["native"]
    assert native["status"] == "unknown"
    assert native["controls"][0]["classification"] == classification
    assert native["subjects"]
    assert "PRIVATE-" not in result.stdout


def test_control_consumer_programming_errors_are_not_swallowed(panel, monkeypatch):
    native = adapter()

    def broken_consumer(*args, **kwargs):
        raise AttributeError("synthetic consumer defect")

    monkeypatch.setattr(native.audit, "inspect_receipt", broken_consumer)
    with pytest.raises(AttributeError, match="synthetic consumer defect"):
        native.project_native(panel.tmp_path, panel.identity, **panel.selection)


@pytest.mark.parametrize("role", ["result", "trace"])
@pytest.mark.parametrize("with_receipt", [False, True])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_selected_orphan_bytes_do_not_count_as_native_references(panel, role, with_receipt, version):
    from rubric_identity import identify

    native = adapter()
    panel.manifest["schema"] = "nemo.eval_author.task_evidence." + version
    panel.identity = panel.seal()
    orphan = panel.tmp_path / "PRIVATE-ORPHAN.json"
    orphan.write_text("{}")
    digest = panel.task_evidence.digest(orphan)
    template = next(row for row in panel.identity["records"] if row["id"] == role)
    record = {
        **template,
        "id": "orphan",
        "path": orphan.name,
        "digest": digest,
        "evidence": [{"path": orphan.name, "digest": digest}],
    }
    identity = identify(panel.tmp_path, [*panel.identity["records"], record])
    selection = {**panel.selection, role + "_ids": [role, "orphan"]}
    if not with_receipt:
        selection["receipt_ids"] = []
    output = native.project_native(panel.tmp_path, identity, **selection)
    assert {"id": "orphan", "digest": digest} in output["bindings"]
    assert output["status"] == "fail"
    assert {"code": "native.reference_unconsumed", "subject": "orphan", "status": "fail"} in output["diagnostics"]
    assert bool(output["subjects"]) is with_receipt
    assert output["native_execution"] is False
    assert "PRIVATE-" not in json.dumps(output)
    result = run_rubric_cli(panel, {"artifacts": identity["records"], "native": selection})
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    predicate = next(row for row in check["predicate_results"] if row["id"] == "selected_native_references_bound")
    assert predicate["outcome"] == "FAIL"


@pytest.mark.parametrize("schema", ["nemo.eval_author.task_evidence.v1", "nemo.eval_author.task_evidence.v2"])
@pytest.mark.parametrize("case", ["reference", "incorrect", "empty", "agent", "diagnostic"])
@pytest.mark.parametrize(
    "digest",
    [
        "missing",
        None,
        "",
        "A" * 64,
        "a" * 63,
        "a" * 65,
        " " + "a" * 64,
        "a" * 64 + "\n",
        "sha256:" + "a" * 64,
        0,
        [],
        {},
    ],
)
def test_manifest_task_digest_required_before_any_receipt_classification(panel, schema, case, digest):
    panel.manifest["schema"] = schema
    if case == "diagnostic":
        panel.manifest["contract"]["cases"][case] = {
            **panel.manifest["contract"]["cases"]["reference"],
            "kind": case,
        }
    panel.receipt["case"] = case
    if digest == "missing":
        panel.manifest.pop("task_sha256")
    else:
        panel.manifest["task_sha256"] = digest
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert output["controls"][0]["classification"] == "unproven"
    assert {"code": "native.manifest_invalid", "subject": "manifest", "status": "fail"} in output["diagnostics"]
    assert {"code": "native.reference_invalid", "subject": "receipt", "status": "fail"} in output["diagnostics"]
    assert not output["subjects"]
    assert not output["nodes"]
    assert output["native_execution"] is False
    assert "PRIVATE-" not in json.dumps(output)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("case,classification", [("reference", "false_rejection"), ("agent", "agent_run")])
def test_valid_native_digest_keeps_source_only_reference_consumption(panel, monkeypatch, version, case, classification):
    native = adapter()
    panel.manifest["schema"] = "nemo.eval_author.task_evidence." + version
    panel.receipt["case"] = case
    identity = panel.seal()

    def forbidden(*args, **kwargs):
        pytest.fail("task traversal or execution is outside selected source review")

    monkeypatch.setattr(native.task_evidence, "task_digest", forbidden)
    monkeypatch.setattr(native.task_evidence, "run", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    output = native.project_native(panel.tmp_path, identity, **panel.selection)
    assert output["status"] == "unknown"
    assert output["controls"][0]["classification"] == classification
    assert len(output["subjects"]) == 1
    assert output["subjects"][0]["native_identity"] == "verified"
    assert not any(row["code"] == "native.reference_unconsumed" for row in output["diagnostics"])
    assert output["native_execution"] is False
    assert output["task_freshness"] == "unknown"


def test_oversized_contract_integer_fails_without_overflow(panel):
    panel.manifest["contract"]["cases"]["reference"]["checks"] = [
        {"pointer": "/verifier_result/rewards/reward", "min": 10**400, "max": 10**401}
    ]
    output = adapter().project_native(panel.tmp_path, panel.seal(), **panel.selection)
    assert output["status"] == "fail"
    assert not output["subjects"]
    assert "PRIVATE-" not in json.dumps(output)
