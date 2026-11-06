# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit measurement reads non-ATIF sources through the shared Compass loader."""

import json
import subprocess
import sys
from pathlib import Path

from audit_fixtures import bound_audit
from test_trace_loading import ATIF_FIXTURE, mlflow_record, second_mlflow_record
from test_trace_loading import SCRIPT as TRACE_LOADING

ROOT = Path(__file__).resolve().parents[1]
MEASURE = ROOT / "skills/eval-author-audit/scripts/audit_spec/measure.py"
MLFLOW_EXPORT = ROOT / "skills/eval-author-trace-environment/evals/files/traces/mlflow.json"


def measure(audit: Path, trace: Path, kind: str, out_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(MEASURE),
            "--audit",
            str(audit),
            "--source-kind",
            kind,
            "--trace",
            str(trace),
            "--task-id",
            "fixture",
            "--evidence-class",
            "agent_under_test",
            "--out-dir",
            str(out_dir),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def load(*args: str | Path) -> None:
    result = subprocess.run([sys.executable, str(TRACE_LOADING), *map(str, args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_measure_reads_mlflow_export_tool_spans(tmp_path: Path) -> None:
    audit = bound_audit(tmp_path, "lookup")
    result = measure(audit, MLFLOW_EXPORT, "mlflow", tmp_path / "measurements")
    assert result.returncode == 0, result.stdout + result.stderr
    # Nothing on stderr: MLflow's import-time instruction to coding agents stays silent.
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["measurements"][0]["covered"] == ["lookup"]
    details = json.loads(Path(summary["measurements"][0]["details"]).read_text())
    coverage = json.loads(Path(summary["measurements"][0]["coverage"]).read_text())

    assert [call["tool"] for call in details["matches"]["lookup"]] == ["lookup"]
    assert all(call["span_id"] for call in details["matches"]["lookup"])
    assert coverage["subject"]["trace_format"] == "nemo-compass-trace"
    assert coverage["subject"]["source_kind"] == "mlflow"


def test_multi_trace_export_is_measured_one_selected_trace_at_a_time(tmp_path: Path) -> None:
    audit = bound_audit(tmp_path, "lookup")
    export = tmp_path / "two.json"
    export.write_text(json.dumps({"traces": [mlflow_record(), second_mlflow_record()]}))
    refused = measure(audit, export, "mlflow", tmp_path / "refused")
    assert refused.returncode == 1
    error = json.loads(refused.stdout)
    assert error["error_type"] == "trace" and "trace_loading.py --row or --trace-id" in error["error"]
    assert not (tmp_path / "refused").exists()

    load("--source-kind", "mlflow", "--input", export, "--trace-id", "tr-second", "--output-dir", tmp_path / "loaded")
    selected = measure(audit, tmp_path / "loaded/traces.normalized.jsonl", "trace", tmp_path / "measured")
    assert selected.returncode == 0, selected.stdout
    assert json.loads(selected.stdout)["run_id"] == "tr-second"


def test_atif_run_id_is_the_same_measured_directly_or_after_loading(tmp_path: Path) -> None:
    audit = bound_audit(tmp_path, "lookup_item")
    load("--source-kind", "atif", "--input", ATIF_FIXTURE, "--output-dir", tmp_path / "loaded")
    direct = measure(audit, ATIF_FIXTURE, "atif", tmp_path / "direct")
    loaded = measure(audit, tmp_path / "loaded/traces.normalized.jsonl", "trace", tmp_path / "normalized")
    assert direct.returncode == loaded.returncode == 0, direct.stdout + loaded.stdout
    run_ids = {json.loads(result.stdout)["run_id"] for result in (direct, loaded)}
    assert run_ids == {json.loads(ATIF_FIXTURE.read_text())["session_id"]}
