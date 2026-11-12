# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/run_rubric_provenance_demo.py"
EXPECTATIONS = ROOT / "evals/rubric-measurements/provenance.json"


def invoke(*args):
    return subprocess.run(
        [sys.executable, str(RUNNER), *map(str, args)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=90,
        check=False,
    )


def test_reconstruction_recovered_binds_exact_source_step():
    process = invoke()
    assert process.returncode == 0, process.stdout + process.stderr
    cases = {case["id"]: case for case in json.loads(process.stdout)["cases"]}
    assert "reconstruction-recovered" in cases
    case = cases["reconstruction-recovered"]
    measurement = case["report"]["measurements"]["reconstruction"]
    assert measurement["status"] == "pass"
    assert measurement["diagnostics"] == []
    assert len(measurement["records"]) == 2
    for row in measurement["records"]:
        assert row["status"] == "pass"
        assert row["artifact_digest"] == row["source_content_digest"]
        assert row["source_proof"] == "exact_selected_bytes"
        assert row["semantic_proof"] is False
        assert row["source_step_id"] == "trajectory/turn/1"
        assert row["source_step_index"] == 0
    assert case["canonical_rows_unchanged"] is True


@pytest.mark.parametrize(
    "suffix,status,code,declaration",
    [
        ("wrong-step", "fail", "step_mismatch", "recovered"),
        ("source-bytes-changed", "fail", "trace_digest_mismatch", "recovered"),
        ("partial", "unknown", "partial_source_proof_unknown", "partial"),
        ("generated", "unknown", "generated_source_proof_unknown", "generated"),
        ("source-unavailable", "unknown", "trace_access_missing", "recovered"),
    ],
)
def test_reconstruction_defects_preserve_known_identity_and_neighbor(suffix, status, code, declaration):
    process = invoke()
    assert process.returncode == 0, process.stdout + process.stderr
    cases = {case["id"]: case for case in json.loads(process.stdout)["cases"]}
    assert "reconstruction-" + suffix in cases
    case = cases["reconstruction-" + suffix]
    measurement = case["report"]["measurements"]["reconstruction"]
    assert measurement["status"] == status
    assert measurement["diagnostics"] == [
        {"code": "reconstruction." + code, "subject": "reconstruction:0", "status": status}
    ]
    row, neighbor = measurement["records"]
    assert row["status"] == status
    assert row["declaration"] == declaration
    assert (
        row["artifact_digest"]
        == case["input_digests"][".eval-author/.working/trace-environments/initial/private/ground-truth/file.json"]
    )
    assert row["source_proof"] == "unknown"
    assert row["semantic_proof"] is False
    recovered = cases["reconstruction-recovered"]["report"]["measurements"]["reconstruction"]
    assert neighbor == recovered["records"][1]
    assert case["canonical_rows_unchanged"] is True


def test_generation_demo_uses_cli_and_preserves_canonical_rows():
    assert RUNNER.is_file(), "The independent rubric provenance demo is missing"
    process = invoke()
    assert process.returncode == 0, process.stdout + process.stderr
    assert process.stderr == ""
    panel = json.loads(process.stdout)
    expected = json.loads(EXPECTATIONS.read_text())
    assert panel["status"] == "passed"
    assert panel["counts"] == {"cases": 16, "matched": 16}
    assert [case["id"] for case in panel["cases"][:10]] == [
        "generation-valid",
        "candidate-replaced",
        "validation-dangling",
        "generator-configuration-revision-mismatch",
        "selected-set-membership-changed",
        "calibration-current",
        "calibration-dataset-changed",
        "calibration-scorer-changed",
        "calibration-runtime-changed",
        "compatibility-unknown",
    ]
    assert [case["id"] for case in panel["cases"]] == [case["id"] for case in expected["cases"]]
    definitions = json.loads(
        (ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json").read_text()
    )
    canonical = {item["id"]: {check["id"] for check in item["checks"]} for item in definitions["items"]}
    for case in panel["cases"]:
        assert case["matches_expectations"] is True
        assert case["inputs_unchanged"] is True
        assert case["canonical_rows_unchanged"] is True
        assert case["cli_exit_code"] == 1
        report = case["report"]
        assert set(report["items"]) == set(canonical)
        assert report["anti_cheat_scores"] is None
        assert report["preflight_success"] is False
        for ident, item in report["items"].items():
            assert item["score"] is None
            checks = item["check_results"]
            assert len(checks) == len(canonical[ident])
            assert {check["check_id"] for check in checks} == canonical[ident]
        assert case["observed"] == case["expected"]
        assert case["observed"]["freshness"]["records"]["neighbor-record"] == {
            "status": "current",
            "usable": True,
            "changed_dependencies": [],
        }


def test_retained_demo_repeats_bytes_and_replays_cli(tmp_path):
    first = invoke("--run-dir", tmp_path / "first")
    second = invoke("--run-dir", tmp_path / "second")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout == second.stdout
    panel = json.loads(first.stdout)
    assert json.loads((tmp_path / "first/panel.json").read_text()) == panel
    for case in panel["cases"]:
        directory = tmp_path / "first" / case["id"]
        for name, digest in case["input_digests"].items():
            assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
        selection = json.loads((directory / "selection.json").read_text())
        records = {record["id"]: record for record in selection["artifacts"]}
        generation = json.loads((directory / records["generation-record"]["path"]).read_text())
        assert {row["decision"] for row in generation["supplied_candidates"]} == {"selected", "rejected", "failed"}
        assert all(row["reason"] for row in generation["supplied_candidates"])
        assert generation["population_completeness"] == "not-established"
        assert selection["links"] == generation["links"]
        assert selection["nodes"] == generation["nodes"]
        assert {"source": "neighbor-record", "target": "neighbor-input", "relation": "dependency"} in selection["links"]
        observation = case["report"]["measurements"]["sourced_config"]
        for field in observation["fields"].values():
            binding = field["observed"]["binding"]
            if case["id"] == "generator-configuration-revision-mismatch":
                assert binding["status"] == "unbound"
                assert binding["reason"] == "configuration_digest_mismatch"
            else:
                assert binding["status"] == "run_bound"
        for row in case["report"]["measurements"]["freshness"]["records"]:
            retained = json.loads((directory / records[row["id"] + "-bindings"]["path"]).read_text())["retained"]
            old = {dependency["id"]: dependency for dependency in retained["dependencies"]}
            for dependency in row["lineage"]:
                ident = dependency["id"]
                assert dependency["old"] == {key: old[ident][key] for key in ("digest", "revision")}
                assert dependency["new"] == {key: records[ident][key] for key in ("digest", "revision")}
                assert dependency["impact"] == old[ident]["impact"]
        replay = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
                "--root",
                str(directory),
                "--selection",
                str(directory / "selection.json"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert replay.returncode == case["cli_exit_code"]
        assert not replay.stderr
        assert json.loads(replay.stdout) == case["report"]
        assert json.loads((directory / "report.json").read_text()) == case["report"]
        baseline_selection = json.loads((directory / "baseline-selection.json").read_text())
        assert baseline_selection == {key: value for key, value in selection.items() if key != "reconstruction"}
        baseline_argv = replay.args.copy()
        baseline_argv[-1] = str(directory / "baseline-selection.json")
        baseline = subprocess.run(
            baseline_argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert baseline.returncode == case["cli_exit_code"]
        assert not baseline.stderr
        assert json.loads(baseline.stdout)["items"] == case["report"]["items"]
        if "reconstruction" in selection:
            for entry in selection["reconstruction"]:
                candidate = directory / records[entry["candidate"]["artifact_id"]]["path"]
                summary = directory / records[entry["source"]["artifact_id"]]["path"]
                assert candidate.name == "candidate.json"
                assert summary == candidate.parent / "summary.json"
                document = json.loads(summary.read_text())
                assert document["schema"] == "nemo.eval_author.trace_environment_summary.v4"
                assert document["candidate"]["schema"] == "nemo.eval_author.trace_environment_candidate.v3"
                assert document["source"]["kind"] == "trace"
                assert document["source"]["safe_path"] == "safe/trace.json"
                assert document["source"]["trace_schema_version"] == "3"
                assert document["candidate"] == {"path": "candidate.json", **json.loads(candidate.read_text())}
            source = records["initial-trace"]
            source_path = directory / source["path"]
            if case["id"] == "reconstruction-source-unavailable":
                assert source["access"] == "missing"
                assert not source_path.exists()
            elif case["id"] == "reconstruction-source-bytes-changed":
                assert hashlib.sha256(source_path.read_bytes()).hexdigest() != source["digest"]
            else:
                assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source["digest"]
            recovered = tmp_path / "first/reconstruction-recovered"
            for name, digest in case["input_digests"].items():
                if name.startswith(("neighbor-", ".eval-author/.working/trace-environments/neighbor/")):
                    assert hashlib.sha256((recovered / name).read_bytes()).hexdigest() == digest
    baseline = panel["cases"][0]["input_digests"]
    for case in panel["cases"]:
        assert case["input_digests"]["neighbor-input-v1.json"] == baseline["neighbor-input-v1.json"]
        assert case["input_digests"]["neighbor-record-v1.json"] == baseline["neighbor-record-v1.json"]
        assert case["input_digests"]["neighbor-record-bindings-v1.json"] == baseline["neighbor-record-bindings-v1.json"]
    changed = tmp_path / "first/candidate-replaced"
    assert (changed / "candidate-kept-v1.json").is_file()
    assert (changed / "candidate-kept-v2.json").is_file()


@pytest.mark.parametrize("kind", ["empty", "nonempty", "file", "symlink", "ancestor-symlink", "parent-traversal"])
def test_run_dir_refuses_unsafe_or_existing_root(tmp_path, kind):
    target = tmp_path / "retained"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"preserve")
    if kind in ("empty", "nonempty"):
        target.mkdir()
        if kind == "nonempty":
            (target / "keep").write_bytes(b"keep")
    elif kind == "file":
        target.write_bytes(b"keep")
    elif kind in ("symlink", "ancestor-symlink"):
        target.symlink_to(outside, target_is_directory=True)
        if kind == "ancestor-symlink":
            target = target / "new"
    else:
        target = outside / ".." / "new"
    result = invoke("--run-dir", target)
    assert result.returncode == 2
    assert "run directory" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert sentinel.read_bytes() == b"preserve"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]


def test_text_summary_shows_expected_observed_and_limits():
    result = invoke("--format", "text")
    assert result.returncode == 0
    assert "expected | observed" in result.stdout
    assert "Matched 16/16" in result.stdout
    assert "identity/links/configuration/freshness/reconstruction" in result.stdout
    assert "reconstruction-recovered | pass/pass/equal/pass/pass" in result.stdout
    assert "No population completeness, native execution, semantic judgments, or new scores" in result.stdout
