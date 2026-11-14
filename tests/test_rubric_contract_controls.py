# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/rubric_contract_controls.py"
TASK = ROOT / "skills/eval-author-audit/evals/files/tasks/order-total"
SOURCES = {
    "instruction_id": "instruction.md",
    "dockerfile_id": "environment/Dockerfile",
    "scorer_id": "tests/test.sh",
    "test_data_id": "environment/orders.csv",
    "task_config_id": "task.toml",
}


def module():
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    assert SCRIPT.is_file(), "bounded source-inspection adapter is missing"
    spec = importlib.util.spec_from_file_location("tested_rubric_contract_controls", SCRIPT)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def artifact(root, selection, ident, path, raw):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    record = {
        "id": ident,
        "path": path,
        "digest": digest,
        "role": "task",
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "local-selected-source",
        "evidence": [{"path": path, "digest": digest}],
    }
    selection["artifacts"].append(record)
    return record


def packet(root, clean=False):
    selection = {"artifacts": [], "contract_controls": {"artifact_id": "manifest"}}
    manifest = {"schema": "rubric-contract-controls-v1"}
    for field, path in SOURCES.items():
        raw = (TASK / path).read_bytes()
        if clean and field == "scorer_id":
            raw = raw.replace(b"    summary = json.loads(Path('/workspace/summary.json').read_text())\n", b"")
            raw = raw.replace(b" and summary == {'currency': 'USD', 'order_count': 3}", b"")
        artifact(root, selection, field, path, raw)
        manifest[field] = field
    artifact(root, selection, "manifest", "contract.json", json.dumps(manifest).encode())
    return selection


def test_measure_cli_emits_bounded_contract_scores(tmp_path):
    import subprocess

    selection = packet(tmp_path)
    request = tmp_path / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(SCRIPT.with_name("measure.py")), "--root", str(tmp_path), "--selection", str(request)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["items"]["CREATE-04"]["score"] == 0
    assert report["items"]["CREATE-04"]["evidence_strength"] == "E1"
    assert report["items"]["CREATE-03"]["score"] is None
    assert "criterion_demonstration" not in report


def test_planted_hidden_summary_is_real_source_bound_zero(tmp_path):
    selection = packet(tmp_path)
    rows = module().assess_contract_controls(tmp_path, selection)
    assert set(rows) == {"CREATE-03", "CREATE-04", "CREATE-05-A"}
    row = rows["CREATE-04"]
    assert (row["status"], row["score"], row["evidence_strength"]) == ("ASSESSED", 0, "E1")
    assert row["assessment_scope"] == "bounded-source-inspection"
    assert row["source_inspection"]["hidden_requirement"] == "summary-output-and-value"
    assert row["source_inspection"]["solver_visible_inventory"] == [
        {"artifact_id": "instruction_id", "destination": "public instruction"},
        {"artifact_id": "test_data_id", "destination": "/workspace/orders.csv"},
    ]
    assert row["source_inspection"]["native_execution"] is False
    assert row["source_inspection"]["human_adjudication"] is False
    assert row["source_inspection"]["model_execution"] is False
    assert row["source_inspection"]["condition_count"] == 5
    assert row["source_inspection"]["expected_total"] == "250.50"
    assert row["source_inspection"]["binding"]["sources"]["scorer_id"]["digest"] == selection["artifacts"][2]["digest"]
    for ref in row["evidence_refs"]:
        raw = (tmp_path / ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["digest"]
        assert "\n".join(raw.decode().splitlines()[ref["start_line"] - 1 : ref["end_line"]]) == ref["quote"]
    for ident in ("CREATE-03", "CREATE-05-A"):
        assert rows[ident]["score"] is None
        assert rows[ident]["status"] == "NOT_ASSESSED"
        assert rows[ident]["remaining_gaps"]
    assert all(row["check_results"] for row in rows.values())


def test_clean_neighbor_is_unknown_not_a_clean_bill_of_health(tmp_path):
    rows = module().assess_contract_controls(tmp_path, packet(tmp_path, clean=True))
    assert all(row["score"] is None and row["status"] == "NOT_ASSESSED" for row in rows.values())
    inspection = rows["CREATE-04"]["source_inspection"]
    assert inspection["condition_count"] == 3
    assert inspection["hidden_requirement"] is None
    assert inspection["summary_expected"] is None
    assert "no summary requirement" in inspection["decision_basis"]


def test_copy_source_must_be_the_selected_data_not_a_decoy(tmp_path):
    selection = packet(tmp_path)
    record = next(row for row in selection["artifacts"] if row["id"] == "test_data_id")
    (tmp_path / "decoy.csv").write_bytes((tmp_path / record["path"]).read_bytes())
    record["path"] = "decoy.csv"
    record["evidence"][0]["path"] = "decoy.csv"
    (tmp_path / SOURCES["test_data_id"]).write_text("different Docker COPY bytes\n")
    with pytest.raises(ValueError, match="COPY source"):
        module().assess_contract_controls(tmp_path, selection)


def reseal(root, record, raw):
    (root / record["path"]).write_bytes(raw)
    record["digest"] = hashlib.sha256(raw).hexdigest()
    record["evidence"] = [{"path": record["path"], "digest": record["digest"]}]


@pytest.mark.parametrize("field", list(SOURCES) + ["manifest"])
@pytest.mark.parametrize("fault", ["missing", "stale", "symlink", "denied"])
def test_sources_fail_closed(tmp_path, field, fault):
    selection = packet(tmp_path)
    record = next(row for row in selection["artifacts"] if row["id"] == field)
    path = tmp_path / record["path"]
    if fault == "missing":
        path.unlink()
    elif fault == "stale":
        path.write_bytes(path.read_bytes() + b"\n")
    elif fault == "symlink":
        target = tmp_path / "target"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        record["access"] = "denied"
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


@pytest.mark.parametrize("field", list(SOURCES))
def test_resealed_unknown_whole_source_fails_closed(tmp_path, field):
    selection = packet(tmp_path)
    record = next(row for row in selection["artifacts"] if row["id"] == field)
    reseal(tmp_path, record, (tmp_path / record["path"]).read_bytes() + b"\n# unknown additional source\n")
    with pytest.raises(ValueError, match="unsupported contract"):
        module().assess_contract_controls(tmp_path, selection)


@pytest.mark.parametrize("mutation", ["extra_copy", "visible_summary", "dead_branch", "extra_reward", "changed_total"])
def test_semantic_lookalikes_do_not_generalize(tmp_path, mutation):
    selection = packet(tmp_path)
    field = (
        "dockerfile_id"
        if mutation == "extra_copy"
        else "instruction_id"
        if mutation == "visible_summary"
        else "scorer_id"
    )
    record = next(row for row in selection["artifacts"] if row["id"] == field)
    raw = (tmp_path / record["path"]).read_bytes()
    if mutation == "extra_copy":
        raw += b"COPY requirement.md /workspace/requirement.md\n"
    elif mutation == "visible_summary":
        raw += b"Write summary.json with currency USD and order_count 3.\n"
    elif mutation == "dead_branch":
        raw = raw.replace(b"    summary =", b"    if False:\n        summary =")
    elif mutation == "extra_reward":
        raw += b"printf 1.0 > /logs/verifier/reward.txt\n"
    else:
        raw = raw.replace(b"250.50", b"0.00")
    reseal(tmp_path, record, raw)
    with pytest.raises(ValueError, match="unsupported contract"):
        module().assess_contract_controls(tmp_path, selection)


@pytest.mark.parametrize(
    "fault", ["extra_assertion", "missing_id", "unselected_id", "duplicate_id", "duplicate_key", "nonfinite"]
)
def test_manifest_cannot_supply_semantic_truth(tmp_path, fault):
    selection = packet(tmp_path)
    record = selection["artifacts"][-1]
    document = json.loads((tmp_path / record["path"]).read_bytes())
    if fault == "extra_assertion":
        document["hidden_requirement"] = True
    elif fault == "missing_id":
        del document["task_config_id"]
    elif fault == "unselected_id":
        document["scorer_id"] = "other"
    elif fault == "duplicate_id":
        document["scorer_id"] = document["instruction_id"]
    raw = json.dumps(document).encode()
    if fault == "duplicate_key":
        raw = raw[:-1] + b', "schema": "rubric-contract-controls-v1"}'
    elif fault == "nonfinite":
        raw = raw[:-1] + b', "assertion": NaN}'
    reseal(tmp_path, record, raw)
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


def test_deeply_nested_manifest_fails_with_bounded_validation(tmp_path):
    selection = packet(tmp_path)
    reseal(tmp_path, selection["artifacts"][-1], b"[" * 2000 + b"0" + b"]" * 2000)
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


def test_identity_selection_requires_exact_unique_ids_and_digests(tmp_path):
    selection = packet(tmp_path)
    selection["artifacts"].append(dict(selection["artifacts"][0]))
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


def test_symlink_root_is_not_resolved_into_authority(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    selection = packet(real)
    link = tmp_path / "alias"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        module().assess_contract_controls(link, selection)


def test_unknown_selector_keys_are_rejected(tmp_path):
    selection = packet(tmp_path)
    selection["contract_controls"]["synthetic"] = False
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


def test_unselected_adapter_emits_no_rows(tmp_path):
    assert module().assess_contract_controls(tmp_path, {}) == {}


def test_no_claim_of_scoring_authority_check_completion(tmp_path):
    rows = module().assess_contract_controls(tmp_path, packet(tmp_path))
    assert all(check["outcome"] == "NOT_RUN" for check in rows["CREATE-05-A"]["check_results"])


def test_late_source_change_prevents_returning_score(tmp_path, monkeypatch):
    adapter = module()
    selection = packet(tmp_path)
    original = adapter._code_binding
    calls = []

    def change_after_projection():
        binding = original()
        calls.append(1)
        if len(calls) == 2:
            (tmp_path / SOURCES["instruction_id"]).write_text("changed late")
        return binding

    monkeypatch.setattr(adapter, "_code_binding", change_after_projection)
    with pytest.raises(ValueError):
        adapter.assess_contract_controls(tmp_path, selection)


@pytest.mark.parametrize("fault", ["oversize", "directory", "escape"])
def test_bounded_regular_sources_only(tmp_path, fault):
    selection = packet(tmp_path)
    record = selection["artifacts"][-1]
    path = tmp_path / record["path"]
    if fault == "oversize":
        reseal(tmp_path, record, b" " * 1_048_577)
    elif fault == "directory":
        path.unlink()
        path.mkdir()
    else:
        record["path"] = "../contract.json"
    with pytest.raises(ValueError):
        module().assess_contract_controls(tmp_path, selection)


def test_flat_retained_demo_layout_is_supported(tmp_path):
    selection = packet(tmp_path)
    for record in selection["artifacts"][:-1]:
        old = tmp_path / record["path"]
        new = tmp_path / old.name
        if new != old:
            old.rename(new)
            record["path"] = new.name
            record["evidence"][0]["path"] = new.name
    row = module().assess_contract_controls(tmp_path, selection)["CREATE-04"]
    assert row["score"] == 0
    assert row["source_inspection"]["binding"]["sources"]["dockerfile_id"]["path"] == "Dockerfile"


def test_implementation_and_source_identity_are_both_bound(tmp_path):
    selection = packet(tmp_path)
    adapter = module()
    row = adapter.assess_contract_controls(tmp_path, selection)["CREATE-04"]
    binding = row["source_inspection"]["binding"]
    assert binding["implementation"]["adapter"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert set(binding["implementation"]) == {"adapter", "reader", "json_loader", "definitions"}
    assert set(binding["sources"]) == set(SOURCES)
    assert binding["manifest"]["digest"] == selection["artifacts"][-1]["digest"]
    assert adapter.assess_contract_controls(tmp_path, selection) == adapter.assess_contract_controls(
        tmp_path, selection
    )
