# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synthetic summary fixtures are unit-only, not native execution evidence."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/compare_anti_cheat_live.py"
spec = importlib.util.spec_from_file_location("history_runner", ROOT / "tools/run_anti_cheat_live.py")
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def synthetic(batch, mode="live", repetitions=1, run_id=0):
    rows = runner.cells(mode, repetitions)
    for row in rows:
        trusted = int(row["arm"] in ("reference", "normal"))
        row.update(
            status="completed",
            cases=6,
            semantic_passes=6 * trusted,
            timeouts=0,
            trusted=trusted,
            weak=1,
            turns=1,
            tool_calls=0,
            tokens=1,
            submission_sha256=runner.sha(b"unit-only submission"),
            native_result_sha256=runner.sha(f"unit-only {batch} {row['cell_id']}".encode()),
        )
    components = {
        f"{name}_digest": runner.sha(name.encode()) for name in ("runtime", "model", "prompt", "task", "measurement")
    }
    digest = runner.sha(runner.canonical(components))
    return {
        "schema": "anti-cheat-live-v1",
        "protocol": "paired-repair-v1",
        "mode": mode,
        "status": "completed",
        "reason": "none",
        "expected": runner.expected(mode, repetitions),
        "completed": len(rows),
        "incomplete": 0,
        "cells": rows,
        "metrics": runner.metrics(rows),
        "controls_healthy": True,
        "runtime": runner.runtime_identity(),
        "ci": {"run_id": run_id, "run_attempt": int(run_id > 0)},
        "bindings": {**components, "comparison_digest": digest},
        "comparison_digest": digest,
    }


def cli(tmp_path, *reports):
    paths = []
    for index, report in enumerate(reports):
        path = tmp_path / f"private-unit-only-{index}.json"
        path.write_text(json.dumps(report))
        paths.append(str(path))
    result = subprocess.run([sys.executable, str(SCRIPT), *paths], capture_output=True, text=True, timeout=15)
    assert result.stderr == ""
    assert str(tmp_path) not in result.stdout
    return result.returncode, json.loads(result.stdout)


def test_two_unit_only_batches_report_ordered_per_arm_outcomes(tmp_path):
    code, report = cli(tmp_path, synthetic(1, run_id=20), synthetic(2, run_id=10))
    assert code == 0
    assert report["schema"] == "anti-cheat-history-v1"
    assert report["evidence"] == "summary-only"
    assert report["batch_count"] == 2
    assert report["series_count"] == 1
    series = report["series"][0]
    assert series["ordering"] == "ci-run-attempt"
    assert [row["input_index"] for row in series["measurements"]] == [2, 1]
    measurement = series["measurements"][0]
    assert measurement["completed"] == 10 and measurement["incomplete"] == 0
    assert measurement["metrics"] == runner.metrics(synthetic(2)["cells"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("completed", 9),
        ("completed", True),
        ("incomplete", -1),
        ("expected.total", 11),
        ("expected.live", True),
        ("metrics.0.completed", True),
        ("metrics.0.false_acceptances", 1),
        ("cells.0.task", "PRIVATE"),
        ("cells.0.task_id", True),
        ("cells.0.repetition", 1),
        ("cells.0.cell_id", 2),
        ("cells.0.trusted", True),
        ("cells.0.weak", 0),
        ("cells.0.semantic_passes", -1),
        ("cells.0.timeouts", 7),
        ("cells.0.cases", 0),
        ("cells.0.tokens", -1),
        ("cells.0.turns", False),
        ("cells.0.native_result_sha256", "PRIVATE"),
        ("cells.0.submission_sha256", "a" * 63),
        ("controls_healthy", 1),
        ("controls_healthy", False),
        ("comparison_digest", "a" * 64),
        ("bindings.runtime_digest", "b" * 64),
        ("runtime.python.0", True),
        ("runtime.system", "PRIVATE"),
        ("ci.run_id", -1),
        ("ci.run_attempt", True),
        ("status", "planned"),
        ("mode", "PRIVATE"),
        ("schema", "PRIVATE"),
    ],
)
def test_malformed_summaries_fail_closed(tmp_path, field, value):
    def descend(obj, key):
        return int(key) if isinstance(obj, list) else key

    report = synthetic(1)
    target = report
    keys = field.split(".")
    for key in keys[:-1]:
        target = target[descend(target, key)]
    target[descend(target, keys[-1])] = value
    code, output = cli(tmp_path, report, synthetic(2))
    assert code == 2
    assert output == {"schema": "anti-cheat-history-v1", "status": "rejected", "reason": "invalid-summary"}


@pytest.mark.parametrize("kind", ["copied", "ci", "partial-reuse", "within-batch", "single"])
def test_duplicate_evidence_and_single_batch_rejected(tmp_path, kind):
    first, second = synthetic(1), synthetic(2)
    if kind == "copied":
        second = first
    if kind == "ci":
        first["ci"] = second["ci"] = {"run_id": 8, "run_attempt": 1}
    if kind == "partial-reuse":
        second["cells"][0]["native_result_sha256"] = first["cells"][0]["native_result_sha256"]
    if kind == "within-batch":
        first["cells"][1]["native_result_sha256"] = first["cells"][0]["native_result_sha256"]
    code, report = cli(tmp_path, *([first] if kind == "single" else [first, second]))
    assert code == 2 and report["status"] == "rejected"


def pending(report):
    report["cells"] = [{**cell, "status": "pending"} for cell in runner.cells(report["mode"], 1)]
    report.update(
        completed=0,
        incomplete=len(report["cells"]),
        controls_healthy=False,
        status="planned" if report["mode"] == "plan" else "incomplete",
        reason="none" if report["mode"] == "plan" else "infrastructure",
    )
    report["metrics"] = runner.metrics(report["cells"])
    return report


@pytest.mark.parametrize("difference", ["mode", "repetitions", "runtime", "binding", "denominators", "plan"])
def test_nonmatching_inputs_form_separate_series(tmp_path, difference):
    first, second = synthetic(1), synthetic(2)
    if difference == "mode":
        second = synthetic(2, mode="controls")
    if difference == "repetitions":
        second = synthetic(2, repetitions=2)
    if difference == "runtime":
        second["runtime"]["python"][2] += 1
    if difference == "binding":
        second["bindings"]["additional_digest"] = "a" * 64
        components = {k: v for k, v in second["bindings"].items() if k != "comparison_digest"}
        second["bindings"]["comparison_digest"] = second["comparison_digest"] = runner.sha(runner.canonical(components))
    if difference == "denominators":
        for row in second["cells"]:
            row["cases"] += 1
            row["semantic_passes"] = row["cases"] * row["trusted"]
    if difference == "plan":
        second = pending(synthetic(2, mode="plan"))
    code, report = cli(tmp_path, first, second)
    assert code == 0 and report["series_count"] == 2
    assert [row["series_id"] for row in report["series"]] == [1, 2]


def test_incomplete_and_local_order_visible_without_scores(tmp_path):
    first, second = synthetic(1), synthetic(2)
    second["cells"][-1] = {**runner.cells("live", 1)[-1], "status": "incomplete"}
    second.update(
        status="incomplete",
        reason="invalid-native-evidence",
        completed=9,
        incomplete=1,
        metrics=runner.metrics(second["cells"]),
    )
    code, report = cli(tmp_path, second, first)
    assert code == 0 and report["series_count"] == 1
    series = report["series"][0]
    assert series["ordering"] == "input-order"
    assert [row["input_index"] for row in series["measurements"]] == [1, 2]
    assert series["measurements"][0]["incomplete"] == 1
    arm = next(row for row in series["measurements"][0]["arms"] if row["arm"] == "adversarial")
    assert arm == {"arm": "adversarial", "expected": 2, "completed": 1, "incomplete": 1}
    assert "score" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation", ["duplicate-cell", "duplicate-metric", "extra-pending-score", "missing-control", "nan"]
)
def test_structural_corruption_rejected(tmp_path, mutation):
    report = synthetic(1)
    if mutation == "duplicate-cell":
        report["cells"][1] = report["cells"][0]
    if mutation == "duplicate-metric":
        report["metrics"][1] = report["metrics"][0]
    if mutation == "extra-pending-score":
        report = pending(report)
        report["cells"][0]["trusted"] = 0
    if mutation == "missing-control":
        report["cells"].pop(0)
    if mutation == "nan":
        report["cells"][0]["tokens"] = float("nan")
    assert cli(tmp_path, report, synthetic(2))[0] == 2


@pytest.mark.parametrize("content", ['{"schema": 1, "schema": 2}', "[]", "null", "{PRIVATE", '"PRIVATE"'])
def test_bad_files_never_leak_raw_content_or_paths(tmp_path, content):
    path = tmp_path / "PRIVATE.json"
    path.write_text(content)
    result = subprocess.run([sys.executable, str(SCRIPT), str(path), str(path)], capture_output=True, text=True)
    assert result.returncode == 2 and result.stderr == ""
    assert "PRIVATE" not in result.stdout
    assert json.loads(result.stdout)["reason"] == "invalid-summary"


def test_untrusted_extras_not_published(tmp_path):
    first, second = synthetic(1), synthetic(2)
    first["source_revision"] = first["ci"]["repository"] = first["private_error"] = "PRIVATE"
    first["cells"][0]["raw_trace"] = "PRIVATE"
    code, report = cli(tmp_path, first, second)
    assert code == 0 and "PRIVATE" not in json.dumps(report)


def test_pending_extras_cannot_disguise_copied_local_batch(tmp_path):
    first, second = pending(synthetic(1)), pending(synthetic(2))
    second["ci"]["private_error"] = "PRIVATE"
    assert cli(tmp_path, first, second)[0] == 2


def test_incomplete_extensions_are_not_outcomes(tmp_path):
    first, second = pending(synthetic(1, run_id=1)), pending(synthetic(2, run_id=2))
    first["cells"][0]["private_error"] = "PRIVATE"
    code, report = cli(tmp_path, first, second)
    assert code == 0 and "PRIVATE" not in json.dumps(report)
    assert report["series"][0]["measurements"][0]["incomplete"] == 10


@pytest.mark.parametrize("revision", [None, "b" * 64])
def test_observed_provider_identity_separates_series(tmp_path, revision):
    first, second = synthetic(1), synthetic(2)
    for row in second["cells"][6:]:
        row["observed_models"] = [{"model_sha256": "a" * 64, "revision_sha256": revision}]
    code, report = cli(tmp_path, first, second)
    assert code == 0 and report["series_count"] == 2
    assert report["series"][0]["provider_identity"] == "unknown"
    assert report["series"][1]["provider_identity"] == ("observed-revision" if revision else "unknown-revision")


@pytest.mark.parametrize(
    "observed",
    [
        [{"model_sha256": "PRIVATE", "revision_sha256": None}],
        [{"model_sha256": "a" * 64, "revision_sha256": True}],
        [{"model_sha256": "a" * 64, "revision_sha256": None}] * 2,
    ],
)
def test_malformed_observed_models_rejected(tmp_path, observed):
    first = synthetic(1)
    first["cells"][-1]["observed_models"] = observed
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize("field,value", [("image_id", "PRIVATE"), ("server_version", True), ("server_os", "PRIVATE")])
def test_malformed_docker_observations_rejected(tmp_path, field, value):
    first = synthetic(1)
    first["runtime"]["docker"] = {
        "server_version": "1.2.3",
        "api_version": "1.2",
        "server_os": "linux",
        "server_arch": "amd64",
        "image_id": "a" * 64,
        "image_os": "linux",
        "image_arch": "amd64",
    }
    first["runtime"]["docker"][field] = value
    assert cli(tmp_path, first, synthetic(2))[0] == 2


def test_missing_runtime_remains_visible(tmp_path):
    first, second = pending(synthetic(1, run_id=1)), pending(synthetic(2, run_id=2))
    first["runtime"]["harbor"] = None
    code, report = cli(tmp_path, first, second)
    assert code == 0 and report["series_count"] == 2


@pytest.mark.parametrize("value", [-1, True, 1000000001])
def test_incomplete_usage_is_bounded(tmp_path, value):
    first = pending(synthetic(1, run_id=1))
    first["cells"][0]["observed_tokens"] = value
    assert cli(tmp_path, first, synthetic(2))[0] == 2


def test_partial_usage_remains_separate_from_outcomes(tmp_path):
    first = pending(synthetic(1, run_id=1))
    first["cells"][0].update(
        usage_status="partial_observed_not_total_billed",
        observed_tokens=30,
        observed_turns=1,
        observed_tool_calls=1,
        observed_models=[],
    )
    code, report = cli(tmp_path, first, synthetic(2))
    assert code == 0
    measurement = report["series"][0]["measurements"][0]
    assert measurement["completed"] == 0 and measurement["incomplete"] == 10


def test_deadline_incomplete_summary_visible(tmp_path):
    first = pending(synthetic(1, run_id=1))
    first["reason"] = "deadline"
    assert cli(tmp_path, first, synthetic(2))[0] == 0


@pytest.mark.parametrize(
    "reason", ["missing-key", "unhealthy-controls", "invalid-native-evidence", "infrastructure", "deadline"]
)
@pytest.mark.parametrize("mode", ["live", "plan"])
def test_completed_or_planned_summary_requires_no_failure_reason(tmp_path, reason, mode):
    first = synthetic(1, mode=mode)
    if mode == "plan":
        first = pending(first)
    first["reason"] = reason
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize(
    "contradiction", ["missing-key-controls", "missing-key-started", "healthy-controls", "no-invalid-cell"]
)
def test_incomplete_reason_must_match_evidence(tmp_path, contradiction):
    first = pending(synthetic(1, run_id=1))
    if contradiction == "missing-key-controls":
        first = pending(synthetic(1, mode="controls", run_id=1))
        first["reason"] = "missing-key"
    elif contradiction == "missing-key-started":
        first["cells"][0]["status"] = "incomplete"
        first["reason"] = "missing-key"
    elif contradiction == "healthy-controls":
        first["cells"][:6] = synthetic(1)["cells"][:6]
        first.update(completed=6, incomplete=4, controls_healthy=True, reason="unhealthy-controls")
        first["metrics"] = runner.metrics(first["cells"])
    else:
        first["reason"] = "invalid-native-evidence"
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize(
    "reason",
    [
        "none",
        "in-progress",
        "interrupted",
        "missing-key",
        "unhealthy-controls",
        "invalid-native-evidence",
        "infrastructure",
        "deadline",
    ],
)
def test_native_incomplete_reasons_remain_supported(tmp_path, reason):
    first = pending(synthetic(1, run_id=1))
    first["reason"] = reason
    if reason == "invalid-native-evidence":
        first["cells"][0]["status"] = "incomplete"
    assert cli(tmp_path, first, synthetic(2))[0] == (2 if reason == "none" else 0)


@pytest.mark.parametrize("reason", ["in-progress", "interrupted"])
@pytest.mark.parametrize("mode", ["plan", "controls", "live"])
def test_active_reasons_rejected_for_planned_or_completed_summaries(tmp_path, reason, mode):
    first = synthetic(1, mode=mode)
    if mode == "plan":
        first = pending(first)
    first["reason"] = reason
    assert cli(tmp_path, first, synthetic(2))[0] == 2


def observed_usage(report):
    for row in report["cells"]:
        live = row["arm"] in runner.ARMS
        if not live:
            row.update(tokens=0, turns=0, tool_calls=0)
        row.update(
            usage_status="observed_not_total_billed" if live else "not_applicable",
            observed_tokens=row["tokens"],
            observed_turns=row["turns"],
            observed_tool_calls=row["tool_calls"],
            observed_models=[{"model_sha256": "a" * 64, "revision_sha256": None}] if live else [],
        )
    return report


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_tokens", 999),
        ("observed_turns", 999),
        ("observed_tool_calls", 999),
        ("observed_tokens", None),
        ("usage_status", "not_observed"),
        ("usage_status", "not_applicable"),
        ("usage_status", "partial_observed_not_total_billed"),
        ("observed_models", []),
    ],
)
def test_completed_live_usage_must_match_native_counters(tmp_path, field, value):
    first = observed_usage(synthetic(1))
    first["cells"][6][field] = value
    assert cli(tmp_path, first, observed_usage(synthetic(2)))[0] == 2


@pytest.mark.parametrize("field", ["usage_status", "observed_tokens", "observed_turns", "observed_tool_calls"])
def test_partially_missing_usage_fields_rejected(tmp_path, field):
    first = observed_usage(synthetic(1))
    del first["cells"][6][field]
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_complete_usage_or_explicit_legacy_absence_supported(tmp_path, legacy):
    first, second = synthetic(1), synthetic(2)
    if not legacy:
        first, second = observed_usage(first), observed_usage(second)
    code, report = cli(tmp_path, first, second)
    assert code == 0 and report["series_count"] == 1


@pytest.mark.parametrize("field", ["observed_tokens", "observed_turns", "observed_tool_calls", "observed_models"])
def test_not_observed_rejects_positive_observations(tmp_path, field):
    first = pending(synthetic(1, run_id=1))
    row = first["cells"][6]
    row.update(
        status="incomplete",
        usage_status="not_observed",
        observed_tokens=None,
        observed_turns=None,
        observed_tool_calls=None,
        observed_models=[],
    )
    row[field] = [{"model_sha256": "a" * 64, "revision_sha256": None}] if field == "observed_models" else 1
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize("field", ["observed_tokens", "observed_turns", "observed_tool_calls", "observed_models"])
def test_not_applicable_rejects_provider_observations(tmp_path, field):
    first = observed_usage(synthetic(1))
    row = first["cells"][0]
    if field == "observed_models":
        row[field] = [{"model_sha256": "a" * 64, "revision_sha256": None}]
    else:
        row[field] = row[field.removeprefix("observed_")] = 1
    assert cli(tmp_path, first, synthetic(2))[0] == 2


@pytest.mark.parametrize("counter", [None, 0])
def test_not_observed_accepts_native_unknown_or_initial_receipt(tmp_path, counter):
    first = pending(synthetic(1, run_id=1))
    first["cells"][6].update(
        status="incomplete",
        usage_status="not_observed",
        observed_tokens=counter,
        observed_turns=counter,
        observed_tool_calls=counter,
        observed_models=[],
    )
    assert cli(tmp_path, first, synthetic(2))[0] == 0


@pytest.mark.parametrize("association", ["arm", "task_id", "repetition"])
@pytest.mark.parametrize("field", ["model_sha256", "revision_sha256"])
def test_swapped_cell_provider_identities_split_series(tmp_path, association, field):
    first, second = synthetic(1, repetitions=2), synthetic(2, repetitions=2)
    for report, reverse in ((first, False), (second, True)):
        for row in report["cells"]:
            if row["arm"] not in runner.ARMS:
                continue
            selected = row[association] in ("normal", 1)
            identity = {"model_sha256": "a" * 64, "revision_sha256": "b" * 64}
            identity[field] = ("c" if selected != reverse else "d") * 64
            row["observed_models"] = [identity]
    code, report = cli(tmp_path, first, second)
    assert code == 0 and report["series_count"] == 2


def test_series_fingerprint_is_stable_across_input_order_and_measurements(tmp_path):
    first, second, third = synthetic(1, run_id=1), synthetic(2, mode="controls", run_id=2), synthetic(3, run_id=3)
    second["runtime"]["private_extension"] = "PRIVATE"
    fingerprints = []
    for reports in ((first, second), (second, third), (third, second, first)):
        code, result = cli(tmp_path, *reports)
        assert code == 0 and result["series_count"] == 2
        assert "PRIVATE" not in json.dumps(result)
        identities = {series["mode"]: series["series_fingerprint"] for series in result["series"]}
        assert len(set(identities.values())) == 2
        for identity in identities.values():
            assert runner.hash_value(identity) == identity
        fingerprints.append(identities)
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]


def test_report_provider_inventory_must_match_cells(tmp_path):
    first = synthetic(1)
    first["observed_models"] = [{"model_sha256": "a" * 64, "revision_sha256": None}]
    assert cli(tmp_path, first, synthetic(2))[0] == 2
