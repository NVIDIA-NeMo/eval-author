# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/run_criterion_scoring_demo.py"
IDS = {
    "provenance.task-artifact-access",
    "provenance.grading-path",
    "provenance.grading-dependency-access",
    "provenance.declared-configuration",
    "provenance.observed-configuration",
    "provenance.runtime-route",
    "provenance.comparison-fairness",
    "provenance.solver-tool-access",
    "provenance.dependency-substitution",
    "provenance.external-state",
    "provenance.evidence-revision-consistency",
    "task-contract.clear-contract",
    "task-contract.discoverable-requirements",
    "task-contract.scoring-authority",
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
    assert summary["schema"] == "criterion-scoring-demo-v2"
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
            assert report["schema"] == "rubric-measurements-v3"
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
    verified = invoke("--verify-existing", str(output))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["rejection_controls"] == {name: "rejected" for name in expected}


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


@pytest.mark.parametrize("mutation", ["missing-report", "missing-selection", "wrong-mutation"])
def test_verify_existing_requires_the_named_rejection_control(
    tmp_path: Path, retained_demo: Path, mutation: str
) -> None:
    output = tmp_path / "modified-control"
    shutil.copytree(retained_demo, output)
    directory = output / "rejection-controls/digest-tamper"
    if mutation == "missing-report":
        (directory / "measure-report.json").unlink()
    elif mutation == "missing-selection":
        (directory / "selection.json").unlink()
    else:
        # A different, genuine selection.invalid must not stand in for digest tampering.
        replacement = output / "rejection-controls/stale-subject"
        for name in ("facts.json", "packet.json", "selection.json"):
            shutil.copyfile(replacement / name, directory / name)
    result = invoke("--verify-existing", str(output))
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "invalid"


@pytest.mark.parametrize(
    "mutation",
    ["report-symlink", "selection-symlink", "directory-symlink", "oversized", "task", "baseline", "extra-mutation"],
)
def test_rejection_fixture_validation_precedes_measurement(
    tmp_path: Path, retained_demo: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as context:
        context.setattr(sys, "path", sys.path.copy())
        demo = runpy.run_path(str(SCRIPT))
    output = tmp_path / "modified-control"
    shutil.copytree(retained_demo, output)
    directory = output / "rejection-controls/digest-tamper"
    if mutation in {"report-symlink", "selection-symlink"}:
        filename = "measure-report.json" if mutation == "report-symlink" else "selection.json"
        path = directory / filename
        target = tmp_path / filename
        path.rename(target)
        path.symlink_to(target)
    elif mutation == "directory-symlink":
        target = tmp_path / "linked-control"
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    elif mutation == "oversized":
        with (directory / "measure-report.json").open("ab") as stream:
            stream.truncate(100_000_000)
    elif mutation == "task":
        (directory / "task.json").write_text('{"requirements": ["different task"]}\n')
    elif mutation == "baseline":
        (directory / "baseline-selection.json").unlink()
    else:
        source = json.loads((directory / "facts.json").read_text())
        source["provenance"]["activity"] = "unrelated-alteration"
        (directory / "facts.json").write_text(json.dumps(source))
    rejection_controls = demo["rejection_controls"]
    real_measure = demo["measure"]
    measured = []

    def guarded_measure(path: Path, **kwargs: bool) -> dict:
        measured.append(path)
        assert not path.is_relative_to(output), "invalid retained control reached measurement"
        return real_measure(path, **kwargs)

    monkeypatch.setitem(rejection_controls.__globals__, "measure", guarded_measure)
    with pytest.raises(ValueError, match="rejection control fixture mismatch"):
        rejection_controls(output, verify=True)
    assert measured, "trusted fixture must be generated independently of retained evidence"


@pytest.mark.parametrize("valid", [True, False])
def test_verify_existing_is_read_only(tmp_path: Path, retained_demo: Path, valid: bool) -> None:
    output = tmp_path / "read-only"
    shutil.copytree(retained_demo, output)
    if not valid:
        (output / "summary.json").write_text("{}\n")
    paths = [output, *output.rglob("*")]
    for path in paths:
        path.chmod(path.stat().st_mode & ~0o222)

    def snapshot() -> dict[str, tuple[str | None, int, int]]:
        return {
            path.relative_to(output).as_posix(): (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
                path.stat().st_mtime_ns,
                path.stat().st_mode,
            )
            for path in [output, *output.rglob("*")]
        }

    try:
        before = snapshot()
        result = invoke("--verify-existing", str(output))
        assert result.returncode == (0 if valid else 1), result.stdout + result.stderr
        assert snapshot() == before
    finally:
        for path in paths:
            path.chmod(path.stat().st_mode | 0o200)


@pytest.mark.parametrize("entrypoint", ["nested-verifier", "rejection-controls"])
def test_trusted_temporary_roots_resolve_tmpdir_alias(
    tmp_path: Path, retained_demo: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    import tempfile

    real = tmp_path / "real-tmp"
    real.mkdir()
    alias = tmp_path / "tmp-alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", str(alias))
    monkeypatch.setattr(tempfile, "tempdir", None)
    with monkeypatch.context() as context:
        context.setattr(sys, "path", sys.path.copy())
        demo = runpy.run_path(str(SCRIPT))
        showcase = runpy.run_path(str(ROOT / "tools/run_rubric_showcase.py"))
    before = showcase["retained_hashes"](retained_demo)
    if entrypoint == "nested-verifier":
        showcase["verify_private_copy"](retained_demo, demo["run"])
    else:
        real_measure = demo["measure"]

        def measure_resolved(directory: Path, **kwargs: bool) -> dict:
            assert directory == directory.resolve()
            return real_measure(directory, **kwargs)

        monkeypatch.setitem(demo["rejection_controls"].__globals__, "measure", measure_resolved)
        output = tmp_path / "controls-copy"
        shutil.copytree(retained_demo, output)
        assert demo["rejection_controls"](output, verify=True) == {
            name: "rejected" for name in demo["REJECTION_CONTROLS"]
        }
    assert showcase["retained_hashes"](retained_demo) == before


def test_existing_output_is_never_overwritten(retained_demo: Path) -> None:
    before = (retained_demo / "summary.json").read_bytes()
    result = invoke("--output", str(retained_demo))
    assert result.returncode == 1
    assert (retained_demo / "summary.json").read_bytes() == before
