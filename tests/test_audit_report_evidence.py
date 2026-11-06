# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from audit_fixtures import bound_gap
from test_audit_report_content import EXAMPLE, SCRIPTS, assert_markdown_links, example_data, save
from test_audit_verifier_evidence import panel as panel


@pytest.fixture
def native_report(panel, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    content = importlib.import_module("report_content")
    renderer = content.load_module("audit_markdown_renderer", SCRIPTS / "render_report.py")
    root = panel.tmp_path
    shutil.copytree(EXAMPLE / "report-sources", root / "report-sources")
    shutil.copy2(EXAMPLE / "report-content.json", root / "report-content.json")
    panel.record("early-exit", 1, suffix="-failed")
    data = example_data(root)
    revision = panel.evidence.task_digest(panel.task)
    sources = data["sources"][:2]
    sources.extend(
        [
            {"id": "S3", "kind": "task", "path": panel.task.relative_to(root).as_posix(), "sha256": revision},
            {
                "id": "S4",
                "kind": "document",
                "path": (panel.task / "verify.py").relative_to(root).as_posix(),
                "sha256": panel.evidence.digest(panel.task / "verify.py"),
            },
        ]
    )
    by_path = {row["path"]: row["id"] for row in sources}
    for index, path in enumerate(sorted([*panel.records.glob("*.json"), *panel.runs.glob("*.json")]), 10):
        payload = json.loads(path.read_text())
        kind = "manifest" if path == panel.manifest else "receipt" if "manifest" in payload else "document"
        source = {
            "id": f"S{index}",
            "kind": kind,
            "path": path.relative_to(root).as_posix(),
            "sha256": panel.evidence.digest(path),
        }
        sources.append(source)
        by_path[source["path"]] = source["id"]
    data["sources"] = sources
    data["scope_notes"][0]["evidence"] = ["S2", "S3"]
    data["runs"] = []
    data["tasks"][0].update(revision=revision, run_ids=[])
    for finding in data["findings"]:
        for task in finding["tasks"]:
            task["revision"] = revision
    failed = by_path["measurements/example/early-exit-failed.json"]
    data["findings"][0].update(basis="executed_control", evidence=[failed])
    cases = {
        "early_exit": "early-exit-failed",
        "all_failure": "all-failure",
        "reference_comments": "comments",
        "forged_success": "forged-success",
        "valid_alternative": "alternative",
        "realistic_wrong_answer": "nop",
    }
    for check in data["verifier_review"]["checks"]:
        check.update(
            origin="existing",
            definition=by_path["measurements/example/revision.json"],
            receipt=by_path["measurements/example/" + cases[check["family"]] + ".json"],
        )
    save(root, data)
    return content, renderer, root, data, panel, by_path


def test_failed_control_remains_visible_alongside_a_pass_without_agent_trajectory(native_report):
    content, renderer, root, _, _, _ = native_report
    model = content.load(Path("report-content.json"), root)
    checker = importlib.import_module("audit_verifier_evidence")
    result = checker.check(root, Path("unused"), [], [], [], content=Path("report-content.json"))
    assert result["valid"]
    assert any(row["classification"] == "failed_control" for row in result["controls"])
    assert any(row["classification"] == "passed" and row.get("case") == "early-exit" for row in result["controls"])
    report = renderer.render(model)[0].decode()
    assert "### ADVISORY" in report
    assert "No selected agent runs" in report
    assert "Executed controls confirmed verifier cheating, as documented in " in report
    assert "The audit uncovered evidence of verifier cheating." in report
    assert "no evidence of verifier tampering" not in report
    assert "[T1-example](../tasks/example" in report
    assert "T1-source-answer" not in report
    assert_markdown_links(report, renderer)


def test_failed_assertion_with_zero_aggregate_reward_is_a_finding(native_report):
    content, renderer, root, data, panel, paths = native_report
    native = panel.runs / "early-exit-failed.json"
    payload = json.loads(native.read_text())
    payload["verifier_result"]["rewards"].update(reward=0, private_check=1)
    native.write_text(json.dumps(payload))
    manifest = json.loads(panel.manifest.read_text())
    manifest["contract"]["cases"]["early-exit"]["checks"].append(
        {"pointer": "/verifier_result/rewards/private_check", "equals": 0}
    )
    panel.manifest.write_text(json.dumps(manifest))
    for path in panel.records.glob("*.json"):
        if path == panel.manifest:
            continue
        payload = json.loads(path.read_text())
        payload["manifest_sha256"] = panel.evidence.digest(panel.manifest)
        if payload["case"] == "early-exit" and not path.name.endswith("-failed.json"):
            # Both records share the contract. Retain the earlier result as unproven.
            payload["result_sha256"] = panel.evidence.digest(Path(payload["result"]))
        if path.name == "early-exit-failed.json":
            payload["result_sha256"] = panel.evidence.digest(native)
        path.write_text(json.dumps(payload))
    for source in data["sources"]:
        if source["kind"] != "task":
            source["sha256"] = panel.evidence.digest(root / source["path"])
    save(root, data)
    model = content.load(Path("report-content.json"), root)
    assert any(row["classification"] == "failed_control" for row in model.controls)
    assert "measurements/example/early-exit-failed.json" in renderer.render(model)[0].decode()


def test_new_task_revision_keeps_earlier_failure_historical(native_report):
    content, renderer, root, data, panel, _ = native_report
    (panel.task / "verify.py").write_text("A revised synthetic verifier.\n")
    for source in data["sources"]:
        if source["kind"] == "task":
            source["sha256"] = panel.evidence.task_digest(panel.task)
        elif source["path"].endswith("verify.py"):
            source["sha256"] = panel.evidence.digest(panel.task / "verify.py")
    data["tasks"][0]["revision"] = panel.evidence.task_digest(panel.task)
    save(root, data)
    model = content.load(Path("report-content.json"), root)
    report = renderer.render(model)[0].decode()
    assert "historical" in report
    assert "### ADVISORY" in report
    assert "historical, unresolved" in report
    old_revision = data["findings"][0]["tasks"][0]["revision"]
    scope = report.split("## Scope and Execution Evidence\n\n", 1)[1]
    assert old_revision not in report
    assert scope.count("**Earlier task revision:**") == 1


def test_native_mismatch_cannot_be_called_clean(native_report):
    content, renderer, root, data, panel, _ = native_report
    path = panel.runs / "alternative.json"
    native = json.loads(path.read_text())
    native["task_checksum"] = "wrong"
    path.write_text(json.dumps(native))
    for source in data["sources"]:
        if source["path"] == path.relative_to(root).as_posix():
            source["sha256"] = panel.evidence.digest(path)
    save(root, data)
    model = content.load(Path("report-content.json"), root)
    report = renderer.render(model)[0].decode()
    assert any(row["classification"] == "unproven" for row in model.controls)
    assert "no evidence of verifier tampering" not in report


@pytest.mark.parametrize(
    "run_alias,methods",
    [(None, None), ("retained-attempt", None), (None, ["tool_calls", "capabilities", "failure_cases"])],
)
def test_bound_measurements_compute_coverage_and_stale_evidence_blocks_render(
    tmp_path, monkeypatch, run_alias, methods
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    content = importlib.import_module("report_content")
    renderer = content.load_module("audit_markdown_renderer", SCRIPTS / "render_report.py")
    shutil.copytree(EXAMPLE / "report-sources", tmp_path / "report-sources")
    shutil.copy2(EXAMPLE / "report-content.json", tmp_path / "report-content.json")
    aggregate = bound_gap(tmp_path, tmp_path / "coverage.json", "customer.lookup")
    if run_alias or methods:
        shutil.rmtree(tmp_path / "baseline-measurements")
        for script, arguments in [
            (
                "measure.py",
                [
                    "--trace",
                    tmp_path / "baseline.atif.json",
                    "--task-id",
                    "baseline",
                    "--evidence-class",
                    "agent_under_test",
                    "--out-dir",
                    tmp_path / "baseline-measurements",
                    *(["--run-id", run_alias] if run_alias else []),
                    *([part for method in methods for part in ("--measure", method)] if methods else []),
                ],
            ),
            ("report.py", ["--coverage-dir", tmp_path / "baseline-measurements", "--out", aggregate]),
        ]:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / script),
                    "--audit",
                    str(tmp_path / ".eval-author/audit.md"),
                    *map(str, arguments),
                ],
                text=True,
                capture_output=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    data = example_data(tmp_path)
    files = [
        tmp_path / "ETHOS.md",
        tmp_path / ".eval-author/audit.md",
        tmp_path / "baseline.atif.json",
        aggregate,
        *sorted((tmp_path / "baseline-measurements").rglob("coverage.json")),
    ]
    identifiers = {}
    for index, path in enumerate(files, 10):
        kind = (
            "measurement"
            if path == aggregate
            else "audit"
            if path.suffix == ".md" and path.name == "audit.md"
            else "run"
            if path.name == "baseline.atif.json"
            else "document"
        )
        source = {
            "id": f"S{index}",
            "kind": kind,
            "path": path.relative_to(tmp_path).as_posix(),
            "sha256": content.sha(path.read_bytes()),
        }
        data["sources"].append(source)
        identifiers[path] = source["id"]
    data["inputs"].update(audit=identifiers[tmp_path / ".eval-author/audit.md"], measurement=identifiers[aggregate])
    data["runs"] = [
        {"id": run_alias or "baseline", "task_id": "T1", "source": identifiers[tmp_path / "baseline.atif.json"]}
    ]
    data["tasks"][0]["run_ids"] = [run_alias or "baseline"]
    names = [row["name"] for row in content.load_audit_spec(tmp_path / ".eval-author/audit.md")["items"]]
    for mapping, name in zip(data["task_mappings"], names, strict=True):
        mapping["item"] = name
    for finding in data["findings"]:
        finding["items"] = []
    for recommendation in data["recommendations"]:
        recommendation["items"] = []
    save(tmp_path, data)
    model = content.load(Path("report-content.json"), tmp_path)
    report = renderer.render(model)[0].decode()
    assert "Not demonstrated" in report
    assert "Coverage of tools is unmeasured." not in report
    assert "Coverage of capabilities and failure cases is unmeasured." not in report
    summary = report.split("## Summary\n\n", 1)[1].split("### ADVISORY", 1)[0]
    if methods is None:
        assert "Coverage was partially measured: tools were measured" in summary
        assert "capabilities and failure cases remain unmeasured." in summary
        assert "To measure coverage" in report
    else:
        assert "Coverage was measured for capabilities, tools and failure cases" in summary
        assert "To measure coverage" not in summary
    trace = tmp_path / "baseline.atif.json"
    trace.write_text(trace.read_text() + "\n")
    with pytest.raises(content.ContentError, match="source_binding"):
        renderer.refresh(content.load(Path("report-content.json"), tmp_path))


def test_a_retained_verifier_failure_cannot_be_omitted_from_task_findings(native_report):
    content, renderer, root, data, _, _ = native_report
    data["findings"][0].update(basis="inspection", evidence=["S4"])
    save(root, data)
    with pytest.raises(content.ContentError, match="explained finding tied to the task revision and receipt"):
        renderer.refresh(content.load(Path("report-content.json"), root))
    assert not (root / renderer.OUTPUT).exists()


def test_private_control_requirements_are_linked_instead_of_copied(native_report):
    content, renderer, root, data, panel, _ = native_report
    private_text = "PRIVATE_FIXTURE_EXPECTED_ANSWER_DO_NOT_RENDER"
    manifest = json.loads(panel.manifest.read_text())
    manifest["contract"]["cases"]["early-exit"]["requirement"] = private_text
    panel.manifest.write_text(json.dumps(manifest))
    for path in panel.records.glob("*.json"):
        if path != panel.manifest:
            receipt = json.loads(path.read_text())
            receipt["manifest_sha256"] = panel.evidence.digest(panel.manifest)
            path.write_text(json.dumps(receipt))
    for source in data["sources"]:
        if source["kind"] != "task":
            source["sha256"] = panel.evidence.digest(root / source["path"])
    save(root, data)
    report = renderer.render(content.load(Path("report-content.json"), root))[0].decode()
    assert private_text not in report
    assert "reward files written by the agent must not replace evidence" in report
