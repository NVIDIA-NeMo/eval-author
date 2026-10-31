# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from generation_benchmark.measurement_export import _bundle_coverage_report, export_subset_measurement
from test_generation_benchmark_config import LAB, prepared_generation_benchmark


def _minimal_coverage_cell(cell: Path, *, artifact_name: str = "binding.txt") -> None:
    authored = cell / "authored"
    authored.mkdir(parents=True)
    (authored / "audit.md").write_text(
        "---\nschema: nemo.eval_author.audit.v1\nagent: gen-oracle\nstatus: draft\n---\n",
        encoding="utf-8",
    )
    artifact = cell / artifact_name
    artifact.write_text("binding payload\n", encoding="utf-8")
    report = {
        "schema": "nemo.eval_author.audit_coverage_report.v1",
        "binding": {
            "artifacts": {str(artifact.resolve()): artifact_name},
            "inputs": [{"path": str(artifact.resolve()), "method": "tool_calls"}],
        },
        "audit": {"path": str((authored / "audit.md").resolve())},
    }
    (cell / "report.json").write_text(json.dumps(report), encoding="utf-8")


def test_bundle_coverage_report_remaps_artifact_paths(tmp_path):
    cell = tmp_path / "coverage-cell"
    output = tmp_path / "measurement-out"
    _minimal_coverage_cell(cell)
    report_src = cell / "report.json"
    destination = _bundle_coverage_report(
        cell,
        report_src,
        output,
        case_root=None,
        eval_author_root=LAB,
    )
    bundled = json.loads(destination.read_text(encoding="utf-8"))
    artifacts = bundled["binding"]["artifacts"]
    assert len(artifacts) == 1
    bundled_path = next(iter(artifacts))
    assert bundled_path.startswith(str(output.resolve()))
    assert (output / "binding.txt").is_file()
    assert bundled["binding"]["inputs"][0]["path"] == bundled_path


def test_export_subset_measurement_writes_measurement_json(tmp_path):
    path, _ = prepared_generation_benchmark(tmp_path / "bench")
    from generation_benchmark.config import load_generation_benchmark

    loaded = load_generation_benchmark(path)
    settings = loaded.resolved_generation_settings()
    cell = tmp_path / "coverage-cell"
    _minimal_coverage_cell(cell)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "gen-task-a").mkdir()
    (suite / "gen-task-a" / "task.toml").write_text("name = a\n")
    out = tmp_path / "exported"
    measurement_path = export_subset_measurement(
        coverage_work_cell=cell,
        suite_root=suite,
        agent_config_path=Path(settings["agent_config"]),
        output_dir=out,
        score_value=0.5,
        eval_author_root=LAB,
        case_root=None,
    )
    payload = json.loads(measurement_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "nemo.eval_author.experiment_2.measurement.v1"
    assert payload["metric"] == 0.5
    assert Path(payload["coverage_report"]).is_file()
    assert payload["coverage_report_digest"].startswith("sha256:")
