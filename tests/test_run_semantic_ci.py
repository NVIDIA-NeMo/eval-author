# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import configure_semantic_ci as setup
import run_semantic_ci as ci
import semantic_regression as semantic


@pytest.fixture
def packet(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "scripts").mkdir()
    (runtime / "scripts/cli.py").write_text("# runtime\n")
    suite = {
        "schema": "nemo.semantic_suite.v1",
        "runs": 5,
        "models": {"author": "author-v1", "extractor": "extractor-v1", "judge": "judge-v1"},
        "cases": [
            {"id": "audit", "query": "Private evidence", "skill_files": ["skills/a.md"], "baseline": "lock.json"}
        ],
    }
    semantic.dump(tmp_path / "suite.json", suite)
    semantic.dump(tmp_path / "baseline.json", {"N": 5})
    lock = {
        "schema": "nemo.semantic_lock.v1",
        "binding": semantic.binding(suite, suite["cases"][0], runtime),
        "baseline_digest": semantic.sha((tmp_path / "baseline.json").read_bytes()),
        "reviewed_mapping_digest": "a" * 64,
    }
    semantic.dump(tmp_path / "lock.json", lock)
    return suite, runtime, lock


def test_binding_ignores_source_and_skill_text(packet, tmp_path, monkeypatch):
    suite, runtime, _ = packet
    before = semantic.binding(suite, suite["cases"][0], runtime)
    # Binding no longer reads pipeline source bytes or the evaluated skill text.
    monkeypatch.setattr(semantic, "ROOT", tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/semantic_regression.py").write_text("changed reporting text")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills/a.md").write_text("changed skill under evaluation")
    assert semantic.binding(suite, suite["cases"][0], runtime) == before


@pytest.mark.parametrize("field", ["measurement_version", "runtime_digest", "query_digest", "judge"])
def test_mismatch_is_incomplete_without_inference(packet, tmp_path, monkeypatch, capsys, field):
    suite, runtime, lock = packet
    assert ci.preflight(suite, runtime, tmp_path) == {}
    lock["binding"][field] = "PRIVATE-MISMATCH-VALUE"
    semantic.dump(tmp_path / "lock.json", lock)

    def forbidden():
        pytest.fail("preflight must not construct an inference client")

    monkeypatch.setattr(semantic, "Model", forbidden)
    output = tmp_path / "out"
    assert ci.run(tmp_path / "suite.json", runtime, output, "check") == 2
    report = semantic.load(output / "semantic-regression-summary.json")
    assert report["repository"] == "NVIDIA-NeMo/eval-author"
    assert report["mode"] == "check"
    row = report["observations"][0]
    assert row["status"] == "incomplete" and row["metrics"] is None
    assert row["reason"] == "baseline_binding_mismatch"
    assert row["binding_mismatch_fields"] == [field]
    public = capsys.readouterr().out + json.dumps(report) + (output / "semantic-regression-summary.md").read_text()
    assert "PRIVATE-MISMATCH-VALUE" not in public and "Private evidence" not in public
    assert "baseline_binding_mismatch" in public


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("malformed", "baseline_input_error"),
        ("digest", "baseline_digest_mismatch"),
        ("review", "baseline_review_missing"),
        ("count", "baseline_run_count_mismatch"),
    ],
)
def test_invalid_baselines_fail_closed(packet, tmp_path, mutation, reason):
    suite, runtime, lock = packet
    if mutation == "malformed":
        lock["binding"] = None
    elif mutation == "digest":
        lock["baseline_digest"] = "b" * 64
    elif mutation == "review":
        lock["reviewed_mapping_digest"] = "unreviewed"
    else:
        semantic.dump(tmp_path / "baseline.json", {"N": 99})
        lock["baseline_digest"] = semantic.sha((tmp_path / "baseline.json").read_bytes())
    semantic.dump(tmp_path / "lock.json", lock)
    assert ci.preflight(suite, runtime, tmp_path) == {"audit": (reason, [])}


@pytest.mark.parametrize("status,exit_code", [("passed", 0), ("regression", 1), ("incomplete", 2)])
def test_valid_binding_preserves_measurement_result(packet, tmp_path, monkeypatch, status, exit_code):
    _, runtime, _ = packet
    monkeypatch.setattr(semantic, "Model", lambda: object())
    monkeypatch.setattr(semantic, "check_case", lambda *args: {"status": status, "reason": "measured"})
    assert ci.run(tmp_path / "suite.json", runtime, tmp_path / "out", "check") == exit_code


@pytest.mark.parametrize(
    "statuses,expected",
    [(["passed"], 0), (["regression"], 0), (["incomplete"], 2), (["regression", "incomplete"], 2)],
)
def test_warning_mode_preserves_verdicts_and_incomplete_failures(packet, tmp_path, monkeypatch, capsys, statuses, expected):
    suite, runtime, _ = packet
    if len(statuses) > 1:
        suite["cases"].append({**suite["cases"][0], "id": "second"})
        semantic.dump(tmp_path / "suite.json", suite)
    metrics = {
        "retention": 1.0, "retention_threshold": 1.0, "consistency": 0.8,
        "baseline_consistency": 1.0, "allowed_drop": 0.0, "presence_passed": True,
    }
    measurements = iter(
        {"status": status, "reason": "execution_or_input_error" if status == "incomplete" else "measured",
         "exit_code": {"passed": 0, "regression": 1, "incomplete": 2}[status],
         "metrics": None if status == "incomplete" else metrics}
        for status in statuses
    )
    monkeypatch.setattr(semantic, "Model", lambda: object())
    monkeypatch.setattr(semantic, "check_case", lambda *args: next(measurements))
    output = tmp_path / "out"
    assert ci.run(tmp_path / "suite.json", runtime, output, "check", warn_on_regression=True) == expected
    rows = semantic.load(output / "semantic-regression-summary.json")["observations"]
    assert [row["status"] for row in rows] == statuses
    assert [row["exit_code"] for row in rows] == [{"passed": 0, "regression": 1, "incomplete": 2}[s] for s in statuses]
    log = capsys.readouterr().out
    assert ("::warning::" in log) == ("regression" in statuses)
    assert ("::error::" in log) == ("incomplete" in statuses)
    summary = (output / "semantic-regression-summary.md").read_text()
    assert "warnings, not passing measurements" in summary
    if "regression" in statuses:
        assert "consistency 80.0% (baseline 100.0%, allowed drop 0.0 percentage points)" in summary
    assert "Private evidence" not in summary + log


def test_warning_cli_plan_does_not_call_models(packet, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "run_semantic_ci.py", "--warn-on-regression", "--suite", str(tmp_path / "suite.json"),
        "--output", str(tmp_path / "out"),
    ])
    monkeypatch.setattr(semantic, "Model", lambda: pytest.fail("plan must not call models"))
    assert ci.main() == 0
    assert "::warning::" not in capsys.readouterr().out


def test_warning_cli_check_passes_flag(packet, tmp_path, monkeypatch):
    _, runtime, _ = packet
    monkeypatch.setattr(sys, "argv", [
        "run_semantic_ci.py", "--mode", "check", "--warn-on-regression",
        "--suite", str(tmp_path / "suite.json"), "--runtime", str(runtime), "--output", str(tmp_path / "out"),
    ])
    monkeypatch.setattr(semantic, "Model", lambda: object())
    monkeypatch.setattr(semantic, "check_case", lambda *args: {"status": "regression", "reason": "measured"})
    assert ci.main() == 0


def test_plan_metadata_and_workflow_entrypoints(packet, tmp_path):
    assert ci.run(tmp_path / "suite.json", None, tmp_path / "out", "plan") == 0
    assert semantic.load(tmp_path / "out/semantic-regression-summary.json")["repository"] == "NVIDIA-NeMo/eval-author"
    workflow = yaml.safe_load((semantic.ROOT / ".github/workflows/semantic-regression.yml").read_text())
    commands = [step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]
    assert sum("python tools/run_semantic_ci.py" in command for command in commands) == 3
    assert all("python tools/semantic_regression.py" not in command for command in commands)
    assert sum("--warn-on-regression" in command for command in commands) == 1
    assert any("--mode check --warn-on-regression" in command for command in commands)


def test_preflight_failure_leaves_other_cases_unmeasured(packet, tmp_path, monkeypatch):
    suite, runtime, _ = packet
    suite["cases"].append({**suite["cases"][0], "id": "missing", "baseline": "missing.json"})
    semantic.dump(tmp_path / "suite.json", suite)

    def forbidden():
        pytest.fail("no inference when any baseline is invalid")

    monkeypatch.setattr(semantic, "Model", forbidden)
    assert ci.run(tmp_path / "suite.json", runtime, tmp_path / "out", "check") == 2
    rows = semantic.load(tmp_path / "out/semantic-regression-summary.json")["observations"]
    assert [row["reason"] for row in rows] == ["not_run", "baseline_input_error"]
    assert all(row["status"] == "incomplete" and row["metrics"] is None for row in rows)


LEGACY_DIGEST = "d1947b2ce85b0ffab580067f8475109c7e2ec6c3634755c3e46e7aa2755be7f0"


def legacy_lock(lock):
    result = {**lock, "binding": dict(lock["binding"])}
    del result["binding"]["measurement_version"]
    result["binding"]["pipeline_digest"] = LEGACY_DIGEST
    return result


def test_legacy_lock_works_in_preflight_check_and_packaging(packet, tmp_path, monkeypatch):
    suite, runtime, lock = packet
    old = legacy_lock(lock)
    semantic.dump(tmp_path / "lock.json", old)
    original = (tmp_path / "lock.json").read_bytes()
    assert ci.preflight(suite, runtime, tmp_path) == {}
    monkeypatch.setattr(semantic, "command", lambda *args: None)
    assert setup.bundle(tmp_path / "suite.json", runtime)

    class ReachedMeasurement(Exception):
        pass

    def batch(*args):
        raise ReachedMeasurement

    monkeypatch.setattr(semantic, "batch", batch)
    with pytest.raises(ReachedMeasurement):
        semantic.check_case(suite, suite["cases"][0], runtime, tmp_path, tmp_path / "work", object())
    assert (tmp_path / "lock.json").read_bytes() == original
    assert semantic.normalize_binding(old["binding"]) == lock["binding"]
    assert "pipeline_digest" in old["binding"]


@pytest.mark.parametrize("kind", ["unknown", "mixed", "extra", "new_version"])
def test_legacy_compatibility_cannot_accept_unreviewed_measurements(packet, tmp_path, monkeypatch, kind):
    suite, runtime, lock = packet
    old = legacy_lock(lock)
    if kind == "unknown":
        old["binding"]["pipeline_digest"] = "0" * 64
    elif kind == "mixed":
        old["binding"]["measurement_version"] = semantic.MEASUREMENT_VERSION
    elif kind == "extra":
        old["binding"]["unexpected"] = "private"
    else:
        monkeypatch.setattr(semantic, "MEASUREMENT_VERSION", "narrative-measurement-v2")
    semantic.dump(tmp_path / "lock.json", old)
    assert ci.preflight(suite, runtime, tmp_path)
    with pytest.raises(ValueError):
        setup.bundle(tmp_path / "suite.json", runtime)
    with pytest.raises(ValueError):
        semantic.check_case(suite, suite["cases"][0], runtime, tmp_path, tmp_path / "work", object())


@pytest.mark.parametrize("revision", ["a" * 40, "b" * 64, None, 123, "private body\n::error::bad"])
def test_revision_reporting_preserves_incomplete_verdict(packet, tmp_path, revision):
    _, runtime, lock = packet
    lock["source_revision"] = revision
    lock["binding"]["measurement_version"] = "incompatible"
    semantic.dump(tmp_path / "lock.json", lock)
    output = tmp_path / "out"
    assert ci.run(tmp_path / "suite.json", runtime, output, "check") == 2
    report = semantic.load(output / "semantic-regression-summary.json")
    row = report["observations"][0]
    expected = revision if revision in ("a" * 40, "b" * 64) else None
    assert row["baseline_source_revision"] == expected
    assert row["reason"] == "baseline_binding_mismatch"
    markdown = (output / "semantic-regression-summary.md").read_text()
    assert report["source_revision"] in markdown
    assert (expected or "unavailable") in markdown
    assert "private body" not in markdown + json.dumps(report)


def test_missing_or_malformed_provenance_is_unavailable(packet, tmp_path):
    suite, _, _ = packet
    case = suite["cases"][0]
    assert ci.recorded_revision(tmp_path, case) is None
    (tmp_path / "lock.json").write_text("invalid JSON")
    assert ci.recorded_revision(tmp_path, case) is None
    assert ci.recorded_revision(tmp_path, {**case, "baseline": "missing.json"}) is None
