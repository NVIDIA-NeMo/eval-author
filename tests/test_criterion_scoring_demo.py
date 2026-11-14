# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run_criterion_scoring_demo.py"
IDS = {
    "DISC-01-A",
    "DISC-01-B",
    "DISC-01-C",
    "DISC-02-A",
    "DISC-02-B",
    "DISC-03-A",
    "AUD-02",
    "ENV-01",
    "ENV-02-A",
    "ENV-03",
    "CREATE-07",
    "CREATE-03",
    "CREATE-04",
    "CREATE-05-A",
    "AC-01",
    "AC-02",
    "AC-03",
    "AC-04",
}


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=ROOT,
    )


def test_all_eighteen_anchor_vectors_are_actual_cli_reports(tmp_path: Path) -> None:
    output = tmp_path / "demo"
    result = invoke("--output", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["schema"] == "criterion-scoring-demo-v1"
    assert summary["semantic_results_equal"] is True
    assert summary["task_qualification"] is False
    assert summary["native_execution"] == summary["human_review"] == "NOT_RUN"
    assert summary["criterion_count"] == 18
    assert summary["vector_count_per_root"] == 90
    for run in ("run-a", "run-b"):
        for vector, expected in (("score-0", 0), ("score-1", 1), ("score-2", 2), ("score-3", 3), ("missing", None)):
            directory = output / run / vector
            report = json.loads((directory / "measure-report.json").read_text())
            baseline = json.loads((directory / "baseline-report.json").read_text())
            assert report["schema"] == "rubric-measurements-v2"
            rows = report["criterion_demonstration"]
            assert set(rows) == IDS
            assert report["items"] == baseline["items"]
            for row in rows.values():
                assert row["score"] == expected
                assert row["assessment_scope"] == "synthetic-scoring-demonstration"
                assert row["native_execution_verified"] is False
                assert row["reviewer_authenticated"] is False
                assert row["execution_state"] == "NOT_RUN"
            assert (directory / "packet.json").is_file()
            assert (directory / "facts.json").is_file()
            assert (directory / "selection.json").is_file()
    assert invoke("--verify-existing", str(output)).returncode == 0


def test_retained_stale_and_tamper_controls_are_rejected_by_measure_cli(tmp_path: Path) -> None:
    output = tmp_path / "controls"
    result = invoke("--output", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text())
    expected = {
        "digest-tamper",
        "stale-definitions",
        "stale-subject",
        "promote-synthetic",
        "missing-report-rows",
        "alter-report-score",
    }
    assert set(summary["rejection_controls"]) == expected
    for name in expected:
        assert summary["rejection_controls"][name] == "rejected"
        directory = output / "rejection-controls" / name
        invocation = json.loads((directory / "measure-verify-invocation.json").read_text())
        report = json.loads((directory / "measure-verify.stdout.json").read_text())
        assert invocation["returncode"] == 2
        assert report["code"] == "selection.invalid"
        assert report["preflight_success"] is False
        if name not in {"missing-report-rows", "alter-report-score"}:
            fresh = json.loads((directory / "measure-invocation.json").read_text())
            assert fresh["returncode"] == 2


@pytest.fixture(scope="module")
def retained_demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("criterion-demo") / "evidence"
    result = invoke("--output", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.mark.parametrize("mutation", ["score", "rows", "source", "stale", "summary"])
def test_verify_existing_rejects_retained_mutations(tmp_path: Path, retained_demo: Path, mutation: str) -> None:
    output = tmp_path / "modified"
    shutil.copytree(retained_demo, output)
    directory = output / "run-a/score-0"
    if mutation in {"score", "rows"}:
        path = directory / "measure-report.json"
        document = json.loads(path.read_text())
        if mutation == "score":
            document["criterion_demonstration"]["AC-01"]["score"] = 3
        else:
            del document["criterion_demonstration"]
    elif mutation == "source":
        path = directory / "facts.json"
        document = json.loads(path.read_text())
        document["provenance"]["actor_id"] = "altered"
    elif mutation == "stale":
        path = directory / "packet.json"
        document = json.loads(path.read_text())
        document["definitions_digest"] = "0" * 64
    else:
        path = output / "summary.json"
        document = json.loads(path.read_text())
        document["task_qualification"] = True
    path.write_text(json.dumps(document))
    if mutation == "stale":
        selection_path = directory / "selection.json"
        selection = json.loads(selection_path.read_text())
        packet = next(row for row in selection["artifacts"] if row["id"] == "packet")
        packet["digest"] = hashlib.sha256(path.read_bytes()).hexdigest()
        packet["evidence"][0]["digest"] = packet["digest"]
        selection_path.write_text(json.dumps(selection))
    result = invoke("--verify-existing", str(output))
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "invalid"
    assert json.loads(result.stdout)["task_qualification"] is False


def test_existing_output_is_never_overwritten(retained_demo: Path) -> None:
    before = (retained_demo / "summary.json").read_bytes()
    result = invoke("--output", str(retained_demo))
    assert result.returncode == 1
    assert (retained_demo / "summary.json").read_bytes() == before
