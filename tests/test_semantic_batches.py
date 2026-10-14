# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_semantic_batches as batches
from discovery_determinism import sha
from test_semantic_regression import suite


def test_batches_preserve_regressions_and_reject_changed_baselines(tmp_path):
    spec = tmp_path / "suite.json"
    configuration = suite()
    configuration["cases"][0]["baseline"] = "lock.json"
    spec.write_text(json.dumps(configuration))
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"N": 5}')
    lock = {
        "schema": "nemo.semantic_lock.v1",
        "baseline_digest": sha(baseline.read_bytes()),
        "reviewed_mapping_digest": "a" * 64,
    }
    (tmp_path / "lock.json").write_text(json.dumps(lock))
    paths = []
    for index in range(2):
        work = tmp_path / f"batch-{index}"
        case = work / "audit"
        case.mkdir(parents=True)
        accounting = {"batch_id": f"{index:032x}", "runs": []}
        for offset in range(5):
            run_id = f"{index:032x}-r{offset + 1}"
            raw = case / f"{run_id}.txt"
            claims = case / f"{run_id}.json"
            raw.write_text("synthetic report")
            claims.write_text(json.dumps({"run_id": run_id, "claims": []}))
            accounting["runs"].append(
                {
                    "run_id": run_id,
                    "status": "completed",
                    "report_sha256": sha(raw.read_bytes()),
                    "claims_sha256": sha(claims.read_bytes()),
                }
            )
        (case / "batch.json").write_text(json.dumps(accounting))
        (case / "gate-result.json").write_text(json.dumps({"status": "passed", "metrics": {"retention": 1}}))
        report = {
            "mode": "check",
            "suite_digest": sha(spec.read_bytes()),
            "runs": 5,
            "models": suite()["models"],
            "source_revision": "frozen",
            "inputs_clean": True,
            "observations": [
                {
                    "case_id": "audit",
                    "status": "passed",
                    "baseline_digest": lock["baseline_digest"],
                    "comparison_digest": batches.digest(lock),
                    "batch_digest": sha((case / "batch.json").read_bytes()),
                    "gate_digest": sha((case / "gate-result.json").read_bytes()),
                    "metrics": {"retention": 1},
                }
            ],
        }
        path = work / "summary.json"
        path.write_text(json.dumps(report))
        paths.append(path)
    assert batches.check(spec, paths)["status"] == "passed"
    report["observations"][0]["status"] = "regression"
    gate = paths[-1].parent / "audit/gate-result.json"
    gate.write_text(json.dumps({"status": "regression", "metrics": {"retention": 1}}))
    report["observations"][0]["gate_digest"] = sha(gate.read_bytes())
    paths[-1].write_text(json.dumps(report))
    assert batches.check(spec, paths)["status"] == "regression"
    for key, value in [
        ("baseline_digest", "changed"),
        ("batch_digest", "0"),
        ("status", "passed"),
        ("metrics", {"retention": 2}),
    ]:
        changed = json.loads(json.dumps(report))
        changed["observations"][0][key] = value
        paths[-1].write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            batches.check(spec, paths)
    report["observations"] = []
    paths[-1].write_text(json.dumps(report))
    with pytest.raises(ValueError, match="omitted"):
        batches.check(spec, paths)
    with pytest.raises(ValueError, match="two or more"):
        batches.check(spec, paths[:1])
