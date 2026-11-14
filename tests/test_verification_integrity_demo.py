# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the offline demo through its real command, not pytest fixture imports."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tools/run_verification_integrity_demo.py"


def load_demo():
    spec = importlib.util.spec_from_file_location("verification_integrity_demo", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "mutation", ["missing-group", "tampered-score", "missing-required", "bad-required-score", "bad-required-freshness"]
)
def test_demo_rejects_mutated_actual_rubric_groups(tmp_path, monkeypatch, mutation):
    demo = load_demo()
    real_command = demo.command

    def mutate(root, name, argv):
        completed, report = real_command(root, name, argv)
        check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
        if mutation == "missing-group":
            check.pop("group_scores")
        elif mutation == "tampered-score":
            next(row for row in check["group_scores"] if row["id"] == "receipt_consistency")["score"] = 0
        elif mutation == "missing-required":
            check["group_scores"] = [row for row in check["group_scores"] if row["id"] != "required_verification"]
        elif mutation == "bad-required-score":
            next(row for row in check["group_scores"] if row["id"] == "required_verification")["score"] = 3
        else:
            report["measurements"]["native"]["required_verification"]["cases"][-1]["freshness"] = "current"
        return completed, report

    monkeypatch.setattr(demo, "command", mutate)
    assert demo.anchor(tmp_path / "anchor-3", 4, 3)["matches_expectations"] is False


def invoke(root):
    return subprocess.run(
        [sys.executable, str(DEMO), "--format", "text", "--run-dir", str(root)],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_real_cli_normalizes_fresh_run_inside_repository():
    with tempfile.TemporaryDirectory(prefix=".integrity-determinism-", dir=ROOT) as directory:
        root = Path(directory) / "demo"
        completed = invoke(root)
        panel = json.loads((root / "panel.json").read_text())
        assert all(case["matches_expectations"] for case in panel["cases"])
        assert panel["determinism"]["normalized_fresh_run_equal"] is True
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert (root / "normalized-result.json").read_bytes() == (
            root / "fresh-replay/normalized-result.json"
        ).read_bytes()
        for run in (root, root / "fresh-replay"):
            raw = json.loads((run / "panel.json").read_text())
            projected = json.loads((run / "normalized-result.json").read_text())
            for name in ("stale", "acceptance-contradiction", "acceptance-consistent-claim"):
                original = next(case for case in raw["cases"] if case["id"] == name)
                normalized_case = next(case for case in projected["cases"] if case["id"] == name)
                original_rows = original["observed"]["required_verification"]["cases"]
                normalized_rows = normalized_case["observed"]["required_verification"]["cases"]
                expected_path = (run / name / "manifest.json").relative_to(ROOT).as_posix()
                assert {row["manifest"] for row in original_rows} == {expected_path}
                assert normalized_rows == [{**row, "manifest": f"{name}/manifest.json"} for row in original_rows]

        demo = load_demo()
        baseline = demo.normalized(panel, root)
        relative_root = root.relative_to(ROOT).as_posix()
        for manifest, expected in (
            (f"{relative_root}/stale/other-manifest.json", "stale/other-manifest.json"),
            (f"{relative_root}/different/manifest.json", "different/manifest.json"),
            (f"{relative_root}-other/stale/manifest.json", f"{relative_root}-other/stale/manifest.json"),
            (f"foreign/{relative_root}/stale/manifest.json", f"foreign/{relative_root}/stale/manifest.json"),
            (f"{root}/stale/other-manifest.json", "stale/other-manifest.json"),
            (f"{root}-other/stale/manifest.json", f"{root}-other/stale/manifest.json"),
        ):
            mutated = json.loads(json.dumps(panel))
            stale = next(case for case in mutated["cases"] if case["id"] == "stale")
            stale["observed"]["required_verification"]["cases"][0]["manifest"] = manifest
            projected = demo.normalized(mutated, root)
            assert projected != baseline
            projected_stale = next(case for case in projected["cases"] if case["id"] == "stale")
            assert projected_stale["observed"]["required_verification"]["cases"][0]["manifest"] == expected
            assert stale["observed"]["required_verification"]["cases"][0]["manifest"] == manifest
        for field, value in (("score", 3), ("failed", 1)):
            mutated = json.loads(json.dumps(panel))
            next(case for case in mutated["cases"] if case["id"] == "stale")["observed"][field] = value
            assert demo.normalized(mutated, root) != baseline


def test_real_receipts_and_cli_produce_all_four_score_anchors(tmp_path):
    root = tmp_path / "demo"
    completed = invoke(root)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    panel = json.loads((root / "panel.json").read_text())
    anchors = [case for case in panel["cases"] if case["id"].startswith("anchor-")]
    assert [case["observed"]["score"] for case in anchors] == [0, 1, 2, 3]
    assert [case["observed"]["passed"] for case in anchors] == [0, 1, 3, 4]
    assert all(case["matches_expectations"] for case in anchors)
    assert panel["status"] == "passed"
    assert panel["native_results"] == "synthetic"
    assert panel["model_execution"] is False
    assert panel["docker_execution"] is False
    assert panel["aggregate_score"] is None
    for case in anchors:
        report = json.loads((root / case["evidence"]["report"]).read_text())
        measured = report["measurements"]["native"]["receipt_integrity"]
        assert measured["eligible_claims"] == 4
        assert measured["contradictions"] == case["observed"]["failed"]
        check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
        assert case["observed"]["group_scores"] == check["group_scores"]
        assert case["observed"]["score"] == next(
            row["score"] for row in check["group_scores"] if row["id"] == "receipt_consistency"
        )
        assert case["observed"]["required_verification"] == report["measurements"]["native"]["required_verification"]
        assert case["next_action"]
        assert (root / case["evidence"]["selection"]).is_file()
        receipts = list((root / case["id"]).glob("*-receipt.original.json"))
        assert len(receipts) == 4
    assert "synthetic" in completed.stdout.lower()
    assert "Next action" in (root / "report.md").read_text()


def test_unknowns_retries_acceptance_and_replays_remain_honest(tmp_path):
    import hashlib

    root = tmp_path / "demo"
    completed = invoke(root)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    panel = json.loads((root / "panel.json").read_text())
    cases = {case["id"]: case for case in panel["cases"]}
    for name in ("missing", "stale"):
        observed = cases[name]["observed"]
        assert observed["score"] is None
        assert observed["unknown"] > 0
        assert observed["failed"] == 0
        report = json.loads((root / cases[name]["evidence"]["report"]).read_text())
        assert observed["required_verification"] == report["acceptance_integrity"]["required_verification"]
    assert cases["acceptance-contradiction"]["observed"]["acceptance_status"] == "violation"
    assert "acceptance-recovery" not in cases
    consistent = cases["acceptance-consistent-claim"]
    assert consistent["observed"]["acceptance_status"] == "consistent"
    assert "partial claim fixture" in consistent["boundary"]
    assert consistent["observed"]["score_source"] == "production helper projection; not a rubric group score"
    for name in ("missing", "stale", "acceptance-contradiction", "acceptance-consistent-claim"):
        case = cases[name]
        default = case["default_acceptance"]
        assert default["returncode"] == 1
        assert default["report"] == {"valid": False, "error": "invalid saved acceptance: 'before'"}
        raw = json.loads((root / case["evidence"]["default_report"]).read_text())
        assert raw == default["report"]
        assert case["workflow_acceptance"] is False
    assert "not a rubric group score" in completed.stdout
    assert "partial claim fixture" in completed.stdout
    assert "Required verification: Unassessed" in completed.stdout
    retry = panel["retry"]
    assert retry["review_candidates"] == 1
    assert retry["confirmed_violations"] == 0
    assert retry["score"] is None
    assert retry["later_recovery"] is True
    assert retry["candidate_rate"] == 1.0
    raw_retry = json.loads((root / retry["evidence"]).read_text())
    assert raw_retry["safe_trace_sha256"] == hashlib.sha256((root / "retry/safe-trace.json").read_bytes()).hexdigest()
    assert all(case["matches_expectations"] for case in cases.values())
    assert all(case["determinism"]["same_source_replay"] for case in cases.values())
    assert all(case["determinism"]["inputs_unchanged"] for case in cases.values())
    assert panel["determinism"]["normalized_fresh_run_equal"] is True
    first = root / "normalized-result.json"
    second = root / "fresh-replay/normalized-result.json"
    assert first.read_bytes() == second.read_bytes()
    hashes = json.loads((root / "hashes.json").read_text())
    for path, expected in hashes.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected
    assert hashes["normalized-result.json"] == hashes["fresh-replay/normalized-result.json"]
    before = (root / "panel.json").read_bytes()
    refused = invoke(root)
    assert refused.returncode != 0
    assert (root / "panel.json").read_bytes() == before
    assert root.stat().st_mode & 0o777 == 0o700
