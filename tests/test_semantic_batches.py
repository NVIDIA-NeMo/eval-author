# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_semantic_batches as batches
from discovery_determinism import sha
from semantic_regression import MEASUREMENT_VERSION, SCOPE
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
        "binding": {
            "scope": SCOPE,
            "query_digest": sha(configuration["cases"][0]["query"].encode()),
            "runs": 5,
            "skill_files": configuration["cases"][0]["skill_files"],
            "extractor": "extractor-v1",
            "judge": "judge-v1",
            "measurement_version": MEASUREMENT_VERSION,
            "runtime_digest": "a" * 64,
            "endpoint_digest": "b" * 64,
        },
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
        for name in ("config.json", "retention.json", "clusters.json", "ret-input.json", "match.json"):
            (case / name).write_text("{}")
        input_binding = {
            "baseline_digest": lock["baseline_digest"],
            "comparison_digest": batches.digest(lock),
            "measurement": lock["binding"],
            "files": {
                name: sha((case / name).read_bytes())
                for name in (
                    "batch.json",
                    "config.json",
                    "retention.json",
                    "clusters.json",
                    "ret-input.json",
                    "match.json",
                )
            },
        }
        (case / "gate-result.json").write_text(
            json.dumps({"status": "passed", "metrics": {"retention": 1}, "input_binding": input_binding})
        )
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
                    "input_binding": input_binding,
                }
            ],
        }
        path = work / "summary.json"
        path.write_text(json.dumps(report))
        paths.append(path)
    assert batches.check(spec, paths)["status"] == "passed"
    # Updating summary digests cannot legitimize an incompatible baseline lock.
    lock_path = tmp_path / "lock.json"
    saved = {
        path: path.read_bytes()
        for path in [lock_path, *paths, *(path.parent / "audit/gate-result.json" for path in paths)]
    }
    for replacement in (None, {**lock["binding"], "runs": 6}, {**lock["binding"], "query_digest": "0" * 64}):
        changed_lock = {**lock, "binding": replacement}
        lock_path.write_text(json.dumps(changed_lock))
        for path in paths:
            changed = json.loads(saved[path])
            gate_path = path.parent / "audit/gate-result.json"
            changed_gate = json.loads(saved[gate_path])
            changed_gate["input_binding"]["comparison_digest"] = batches.digest(changed_lock)
            gate_path.write_text(json.dumps(changed_gate))
            changed["observations"][0].update(
                changed_gate, comparison_digest=batches.digest(changed_lock), gate_digest=sha(gate_path.read_bytes())
            )
            path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="binding"):
            batches.check(spec, paths)
    for path, raw in saved.items():
        path.write_bytes(raw)
    # A receipt from another valid batch cannot be substituted, even with updated summary hashes.
    original = paths[-1].read_bytes()
    gate_path = paths[-1].parent / "audit/gate-result.json"
    original_gate = gate_path.read_bytes()
    other_gate = (paths[0].parent / "audit/gate-result.json").read_bytes()
    gate_path.write_bytes(other_gate)
    substituted = json.loads(original)
    substituted["observations"][0].update(json.loads(other_gate), gate_digest=sha(other_gate))
    paths[-1].write_text(json.dumps(substituted))
    with pytest.raises(ValueError, match="input binding"):
        batches.check(spec, paths)
    gate_path.write_bytes(original_gate)
    paths[-1].write_bytes(original)
    report["observations"][0]["status"] = "regression"
    gate = paths[-1].parent / "audit/gate-result.json"
    gate.write_text(json.dumps({"status": "regression", "metrics": {"retention": 1}, "input_binding": input_binding}))
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
    for incomplete_gate in ({}, {"status": "regression"}, {"metrics": {"retention": 1}}, []):
        gate.write_text(json.dumps(incomplete_gate))
        report["observations"][0]["gate_digest"] = sha(gate.read_bytes())
        paths[-1].write_text(json.dumps(report))
        with pytest.raises(ValueError, match="lacks status or metrics"):
            batches.check(spec, paths)
    report["observations"] = []
    paths[-1].write_text(json.dumps(report))
    with pytest.raises(ValueError, match="omitted"):
        batches.check(spec, paths)
    with pytest.raises(ValueError, match="two or more"):
        batches.check(spec, paths[:1])
