# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys

from generation_benchmark.config import GenerationConfig
from generation_benchmark.generation import invoke_generation
from metric_benchmark.models import Score


def test_invoke_generation_fake_adapter(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "task-a").mkdir()
    (suite / "task-a" / "task.toml").write_text('[task]\nname = "a"\n')
    coverage_work = tmp_path / "coverage"
    coverage_work.mkdir()
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse, json, shutil
from pathlib import Path
from metric_benchmark.config import file_manifest
from metric_benchmark.io import digest_bytes, json_bytes
p = argparse.ArgumentParser()
p.add_argument('--request')
p.add_argument('--output')
a = p.parse_args()
req = json.loads(Path(a.request).read_text())
out = Path(req['inputs']['output_suite'])
out.mkdir(parents=True)
shutil.copytree(Path(req['inputs']['suite']) / 'task-a', out / 'task-b')
expanded = digest_bytes(json_bytes(file_manifest(out)))
Path(a.output).write_text(json.dumps({
  'eval_subset_id': req['eval_subset_id'],
  'new_task_ids': ['task-b'],
  'expanded_suite_digest': expanded,
  'summary': {},
}))
"""
    )
    config = GenerationConfig(
        system_id="fake", system_revision="1", command=[sys.executable, str(adapter)], settings={}
    )
    work = tmp_path / "work"
    suite_plus, receipt = invoke_generation(
        config,
        {"goal": "test"},
        "subset-1",
        [{"task_id": "task-a", "revision": 1}],
        suite,
        coverage_work,
        work,
        coverage=Score(value=0.0),
    )
    assert (suite_plus / "task-b").is_dir()
    assert receipt.response.new_task_ids == ["task-b"]
    request = json.loads((work / "request.json").read_text())
    assert request["inputs"]["suite"] == "input/tasks"
    assert request["inputs"]["coverage_work"] == "input/coverage"
    assert request["inputs"]["output_suite"] == "output/tasks-generated"
    assert request["coverage"] == {"value": 0.0, "missing_score_reason": None}
    assert (work / "input" / "coverage").is_dir()
    assert not (work / "input" / "measurement").exists()


def test_invoke_generation_strips_transient_work_dir(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "task-a").mkdir()
    (suite / "task-a" / "task.toml").write_text('[task]\nname = "a"\n')
    coverage_work = tmp_path / "coverage"
    coverage_work.mkdir()
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse, json, shutil
from pathlib import Path
from metric_benchmark.config import file_manifest
from metric_benchmark.io import digest_bytes, json_bytes
p = argparse.ArgumentParser()
p.add_argument('--request')
p.add_argument('--output')
a = p.parse_args()
req = json.loads(Path(a.request).read_text())
out = Path(req['inputs']['output_suite'])
out.mkdir(parents=True)
shutil.copytree(Path(req['inputs']['suite']) / 'task-a', out / 'task-b')
work = Path('output/.tasks-generated-generation-work-deadbeef')
work.mkdir()
(work / 'link').symlink_to(out / 'task-b')
expanded = digest_bytes(json_bytes(file_manifest(out)))
Path(a.output).write_text(json.dumps({
  'eval_subset_id': req['eval_subset_id'],
  'new_task_ids': ['task-b'],
  'expanded_suite_digest': expanded,
  'summary': {},
}))
"""
    )
    config = GenerationConfig(
        system_id="fake", system_revision="1", command=[sys.executable, str(adapter)], settings={}
    )
    work_root = tmp_path / "work"
    suite_plus, receipt = invoke_generation(
        config,
        {"goal": "test"},
        "subset-1",
        [{"task_id": "task-a", "revision": 1}],
        suite,
        coverage_work,
        work_root,
        coverage=Score(value=0.0),
    )
    assert receipt.response.new_task_ids == ["task-b"]
    assert not (work_root / "output" / ".tasks-generated-generation-work-deadbeef").exists()


def test_invoke_generation_resumes_from_receipt_without_rerunning_adapter(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "task-a").mkdir()
    (suite / "task-a" / "task.toml").write_text('[task]\nname = "a"\n')
    coverage_work = tmp_path / "coverage"
    coverage_work.mkdir()
    good_adapter = tmp_path / "good_adapter.py"
    good_adapter.write_text(
        """
import argparse, json, shutil
from pathlib import Path
from metric_benchmark.config import file_manifest
from metric_benchmark.io import digest_bytes, json_bytes
p = argparse.ArgumentParser()
p.add_argument('--request')
p.add_argument('--output')
a = p.parse_args()
req = json.loads(Path(a.request).read_text())
out = Path(req['inputs']['output_suite'])
out.mkdir(parents=True)
shutil.copytree(Path(req['inputs']['suite']) / 'task-a', out / 'task-b')
expanded = digest_bytes(json_bytes(file_manifest(out)))
Path(a.output).write_text(json.dumps({
  'eval_subset_id': req['eval_subset_id'],
  'new_task_ids': ['task-b'],
  'expanded_suite_digest': expanded,
  'summary': {},
}))
"""
    )
    config = GenerationConfig(
        system_id="fake", system_revision="1", command=[sys.executable, str(good_adapter)], settings={}
    )
    work_root = tmp_path / "work"
    invoke_generation(
        config,
        {},
        "subset-1",
        [{"task_id": "task-a", "revision": 1}],
        suite,
        coverage_work,
        work_root,
        coverage=Score(value=0.0),
    )
    good_adapter.write_text("import sys\nsys.exit('adapter must not run on cache hit')\n")
    suite_plus, cached = invoke_generation(
        config,
        {},
        "subset-1",
        [{"task_id": "task-a", "revision": 1}],
        suite,
        coverage_work,
        work_root,
        coverage=Score(value=0.0),
        cached_only=True,
    )
    assert (suite_plus / "task-b").is_dir()
    assert cached.response.new_task_ids == ["task-b"]


def test_reference_adapter_reads_audit_from_coverage_work(tmp_path, monkeypatch):
    from generation_benchmark.config import load_generation_benchmark
    from generation_benchmark.eval_author import adapter as adapter_mod
    from test_generation_benchmark_config import prepared_generation_benchmark
    from test_generation_benchmark_measurement_export import _minimal_coverage_cell

    bench, _ = prepared_generation_benchmark(tmp_path / "bench")
    settings = load_generation_benchmark(bench).resolved_generation_settings()
    settings["subset_local_measurement_export"] = True
    workspace = tmp_path / "cell"
    suite = workspace / "input" / "tasks" / "gen-task-a"
    suite.mkdir(parents=True)
    (suite / "task.toml").write_text('[task]\nname = "gen-task-a"\n', encoding="utf-8")
    _minimal_coverage_cell(workspace / "input" / "coverage")
    request = {
        "schema_version": 1,
        "case_description": {},
        "eval_subset_id": "subset-1",
        "tasks": [{"task_id": "gen-task-a", "revision": 1}],
        "coverage": {"value": 0.5, "missing_score_reason": None},
        "inputs": {
            "suite": "input/tasks",
            "coverage_work": "input/coverage",
            "output_suite": "output/tasks-generated",
        },
        "settings": settings,
    }
    (workspace / "request.json").write_text(json.dumps(request), encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_generate(*_args, measurement_path, output, **_kwargs):
        seen["measurement"] = measurement_path
        output.mkdir(parents=True, exist_ok=True)
        destination = output / "gen-task-a"
        destination.mkdir()
        (destination / "task.toml").write_text('[task]\nname = "gen-task-a"\n', encoding="utf-8")

    monkeypatch.setattr(adapter_mod, "generate_suite", fake_generate)
    monkeypatch.chdir(workspace)
    adapter_mod.run_from_request(workspace / "request.json", workspace / "response.json")
    measurement_path = seen["measurement"]
    assert measurement_path == workspace / ".measurement"
    payload = json.loads((measurement_path / "measurement.json").read_text(encoding="utf-8"))
    assert payload["metric"] == 0.5
    assert (measurement_path / "audit.md").is_file()
    assert not (workspace / "input" / "measurement").exists()


def test_export_copies_draft_without_skill_acceptance(tmp_path):
    from generation_benchmark.eval_author.generation import _export_attempt

    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
    output = tmp_path / "suite"
    output.mkdir()
    attempt = {
        "generated": True,
        "finalized": False,
        "draft": str(draft),
        "task_slug": "cover-tool-1",
    }
    _export_attempt(attempt, output)
    assert (output / "_generated" / "cover-tool-1" / "task.toml").is_file()
    _export_attempt({"generated": False, "draft": str(draft), "task_slug": "cover-tool-1"}, output)
