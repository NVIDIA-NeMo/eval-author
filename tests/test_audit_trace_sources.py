# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit measurement reads non-ATIF sources through the shared Compass loader."""

import json
import subprocess
import sys
from pathlib import Path

from audit_fixtures import bound_audit

ROOT = Path(__file__).resolve().parents[1]
MEASURE = ROOT / "skills/eval-author-audit/scripts/audit_spec/measure.py"
MLFLOW_EXPORT = ROOT / "skills/eval-author-trace-environment/evals/files/traces/mlflow.json"


def test_measure_reads_mlflow_export_tool_spans(tmp_path: Path) -> None:
    audit = bound_audit(tmp_path, "lookup")
    out_dir = tmp_path / "measurements"
    result = subprocess.run(
        [
            sys.executable,
            str(MEASURE),
            "--audit",
            str(audit),
            "--source-kind",
            "mlflow",
            "--trace",
            str(MLFLOW_EXPORT),
            "--task-id",
            "mlflow",
            "--evidence-class",
            "agent_under_test",
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["measurements"][0]["covered"] == ["lookup"]
    details = json.loads(Path(summary["measurements"][0]["details"]).read_text())
    coverage = json.loads(Path(summary["measurements"][0]["coverage"]).read_text())

    assert [call["tool"] for call in details["matches"]["lookup"]] == ["lookup"]
    assert all(call["span_id"] for call in details["matches"]["lookup"])
    assert coverage["subject"]["trace_format"] == "nemo-compass-trace"
    assert coverage["subject"]["source_kind"] == "mlflow"
