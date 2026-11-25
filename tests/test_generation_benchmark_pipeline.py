# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from generation_benchmark.models import (
    GenerationInputs,
    MetricCommandReceipt,
    Stage0SetupReceipt,
    Stage1ExecutionReceipt,
    SubsetsStageReceipt,
    load_experiment,
)
from generation_benchmark.pipeline import run_experiment
from generation_benchmark.signing import sign_experiment
from generation_benchmark.validation import task_checksum
from harbor.models.job.config import JobConfig
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import TrialResult
from metric_benchmark.io import read_json
from test_generation_benchmark_reference_metric import _write_items_yaml

GEN_LAB_ROOT = Path(__file__).resolve().parents[1] / "labs" / "generation-benchmark"
SMOKE_EXPERIMENT = GEN_LAB_ROOT / "examples" / "smoke" / "generation-experiment.yml"
SIGN_TOOL = GEN_LAB_ROOT / "tools" / "sign-generation"
RUN_CLI = GEN_LAB_ROOT / "run_generation_experiment.py"
REF_METRIC_CLI = GEN_LAB_ROOT / "reference_metric.py"


def _make_fake_runner(
    *,
    counter: dict[str, int],
    loaded_exp_path: Path,
) -> Callable[[JobConfig], None]:
    """Create a Harbor-free fake Stage 1 runner that writes valid TrialResult + ATIF trajectories."""

    def fake_runner(job_config: JobConfig) -> None:
        counter["calls"] += 1
        loaded = load_experiment(loaded_exp_path)
        job_dir = job_config.jobs_dir / job_config.job_name
        job_dir.mkdir(parents=True, exist_ok=True)
        agent_cfg = job_config.agents[0]

        # Match tasks by resolved path
        matching_case = next(
            lc
            for lc in loaded.cases.values()
            if {str(p.resolve()) for p in lc.task_paths.values()}
            == {str(t.path.resolve()) for t in job_config.tasks if t.path is not None}
        )
        path_to_id = {str(p.resolve()): tid for tid, p in matching_case.task_paths.items()}

        for task_spec in job_config.tasks:
            assert task_spec.path is not None
            task_path = task_spec.path.resolve()
            task_id = path_to_id[str(task_path)]
            htask = HarborTask(task_path)
            trial_name = f"{task_id}__fake01"
            trial_dir = job_dir / trial_name
            agent_dir = trial_dir / "agent"
            verifier_dir = trial_dir / "verifier"
            agent_dir.mkdir(parents=True, exist_ok=True)
            verifier_dir.mkdir(parents=True, exist_ok=True)

            (verifier_dir / "reward.txt").write_text("1\n", encoding="utf-8")
            (agent_dir / "trajectory.json").write_text(
                json.dumps(
                    {
                        "schema_version": "ATIF-v1.8",
                        "session_id": f"fake-session-{matching_case.definition.case_id}-{task_id}",
                        "agent": {"name": "smoke", "version": "1.0"},
                        "steps": [
                            {"step_id": 1, "source": "user", "message": f"TOOL: {task_id}"},
                            {
                                "step_id": 2,
                                "source": "agent",
                                "message": f"Called {task_id}",
                                "tool_calls": [
                                    {
                                        "tool_call_id": "call-1",
                                        "function_name": task_id,
                                        "arguments": {},
                                    }
                                ],
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            trial_result = TrialResult.model_validate(
                {
                    "task_name": htask.name,
                    "trial_name": trial_name,
                    "trial_uri": trial_dir.resolve().as_uri(),
                    "task_id": {"path": str(task_path)},
                    "task_checksum": task_checksum(htask),
                    "config": {
                        "task": {"path": str(task_path)},
                        "trial_name": trial_name,
                        "trials_dir": str(job_dir),
                        "agent": agent_cfg.model_dump(mode="json"),
                        "environment": {"type": "docker"},
                    },
                    "agent_info": {"name": "smoke", "version": "1.0"},
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "started_at": "2026-11-23T00:00:00Z",
                    "finished_at": "2026-11-23T00:00:01Z",
                }
            )
            (trial_dir / "result.json").write_text(trial_result.model_dump_json(indent=2), encoding="utf-8")

        (job_dir / "result.json").write_text(
            json.dumps({"started_at": "2026-11-23T00:00:00Z", "finished_at": "2026-11-23T00:00:02Z"}),
            encoding="utf-8",
        )

    return fake_runner


def _write_pipeline_experiment(
    root: Path,
    *,
    case_ids: list[str] | None = None,
    subsets_by_case: dict[str, list[dict[str, object]]] | None = None,
    with_setup: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cids = case_ids or ["smoke"]
    cases_entries = []
    (root / "gen_prompt.txt").write_text("prompt v1\n", encoding="utf-8")

    for cid in cids:
        agent_dir = root / "agents" / cid
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "smoke_agent.py").write_text("# dummy agent\n", encoding="utf-8")
        (agent_dir / "ethos.md").write_text(f"---\nname: {cid}-agent\n---\n# {cid} Ethos\n", encoding="utf-8")
        _write_items_yaml(agent_dir / "items.yaml", ["alpha", "beta", "gamma", "delta"])
        (agent_dir / "agent.yml").write_text(
            yaml.safe_dump(
                {
                    "agent_bundle_id": f"{cid}-agent",
                    "revision": "1",
                    "harbor": {"import_path": "examples.smoke.agents.smoke.smoke_agent:SmokeAgent"},
                    "inputs": {"source": {"path": f"agents/{cid}/smoke_agent.py"}},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        task_root = root / "tasks" / cid
        for slug in ("alpha", "beta", "gamma", "delta"):
            tdir = task_root / slug
            (tdir / "environment").mkdir(parents=True, exist_ok=True)
            (tdir / "solution").mkdir(parents=True, exist_ok=True)
            (tdir / "tests").mkdir(parents=True, exist_ok=True)
            (tdir / "task.toml").write_text(
                f'version = "1.0"\n[task]\nname = "smoke/{slug}"\n[agent]\ntimeout_sec = 30\n[verifier]\ntimeout_sec = 30\n',
                encoding="utf-8",
            )
            (tdir / "instruction.md").write_text(f"TOOL: {slug}\n", encoding="utf-8")
            (tdir / "environment/Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
            (tdir / "solution/solve.sh").write_text("#!/bin/bash\nprintf ready > /app/answer\n", encoding="utf-8")
            (tdir / "tests/test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n", encoding="utf-8")
            (tdir / "README.md").write_text(f"# {slug}\n", encoding="utf-8")

        (task_root / "tasks.yml").write_text(
            yaml.safe_dump(
                {
                    "tasks": [
                        {"task_id": slug, "revision": 1, "path": f"tasks/{cid}/{slug}"}
                        for slug in ("alpha", "beta", "gamma", "delta")
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        sub_defs = (subsets_by_case or {}).get(
            cid,
            [
                {"eval_subset_id": "s1", "task_ids": ["alpha", "beta"]},
                {"eval_subset_id": "s2", "task_ids": ["gamma"]},
            ],
        )
        (task_root / "subsets.json").write_text(
            json.dumps(
                {
                    "artifact_type": "eval_subset_definitions",
                    "sampling_parameters": {"method": "fixed", "seed": 1},
                    "eval_subsets": sub_defs,
                }
            ),
            encoding="utf-8",
        )
        cases_entries.append(
            {
                "case_id": cid,
                "agent": {"path": f"agents/{cid}/agent.yml"},
                "tasks": {"path": f"tasks/{cid}/tasks.yml"},
                "subsets": {"path": f"tasks/{cid}/subsets.json"},
                "ethos": {"path": f"agents/{cid}/ethos.md"},
                "agent_source_excludes": [],
                "settings": {"items": {"path": f"agents/{cid}/items.yaml"}},
            }
        )

    (root / "cases.yml").write_text(yaml.safe_dump(cases_entries, sort_keys=False), encoding="utf-8")

    metric: dict[str, object] = {
        "score": {
            "command": [sys.executable, str(REF_METRIC_CLI)],
            "timeout_seconds": 120,
            "output": {
                "python": sys.executable,
                "type": "generation_benchmark.reference_metric:ToolCallsScoreOutput",
            },
        }
    }
    if with_setup:
        metric["setup"] = {
            "command": [sys.executable, str(REF_METRIC_CLI), "--setup"],
            "timeout_seconds": 120,
            "output": {
                "python": sys.executable,
                "type": "generation_benchmark.reference_metric:ToolCallsSetupOutput",
            },
        }

    exp = {
        "benchmark_id": "test-gen",
        "revision": 1,
        "cases": {"path": "cases.yml"},
        "execution": {
            "backend": "local_harbor",
            "harbor_version": "0.24.0",
            "n_concurrent_trials": 2,
            "environment": {"type": "docker"},
        },
        "metric": metric,
        "generation": {
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 60,
            "settings": {"prompt": {"path": "gen_prompt.txt"}},
        },
    }
    exp_path = root / "generation-experiment.yml"
    exp_path.write_text(yaml.safe_dump(exp, sort_keys=False), encoding="utf-8")
    sign_experiment(exp_path)
    return exp_path


def test_stages_0_to_2_receipts_reuse_generation_settings_and_setup_change(tmp_path: Path) -> None:
    exp = _write_pipeline_experiment(tmp_path / "exp")
    work = tmp_path / "work"
    counter = {"calls": 0}
    runner = _make_fake_runner(counter=counter, loaded_exp_path=exp)

    res1 = run_experiment(exp, "run-1", work, runner=runner)
    assert counter["calls"] == 1
    assert res1["cases"]["smoke"]["m_full"]["value"] == pytest.approx(1.0)
    assert res1["cases"]["smoke"]["subsets"]["s1"]["score"]["value"] == pytest.approx(0.5)
    assert res1["cases"]["smoke"]["subsets"]["s2"]["score"]["value"] == pytest.approx(0.25)

    # generation-inputs.json records run_id, implementation, and every lock entry the run read (stage 3 adds generation)
    gen_inputs = GenerationInputs.model_validate(read_json(work / "generation-inputs.json"))
    assert gen_inputs.run_id == "run-1"
    assert "execution" in gen_inputs.entries
    assert "metric" in gen_inputs.entries
    assert "generation" in gen_inputs.entries
    assert "generation.settings.prompt" in gen_inputs.entries

    case_dir = work / "cases" / "case-0000"
    assert not (case_dir / "execution" / "agents" / "nop").exists()
    assert not (case_dir / "execution" / "agents" / "oracle").exists()

    # Stage 1 and Stage 2 receipts name the same metric command and same metric-setup digest
    setup_receipt_sha = Stage0SetupReceipt.model_validate(read_json(case_dir / "metric-setup" / "receipt.json"))
    assert setup_receipt_sha.case_id == "smoke"
    full_rec = MetricCommandReceipt.model_validate(read_json(case_dir / "metric-full" / "receipt.json"))
    sub1_rec = MetricCommandReceipt.model_validate(
        read_json(case_dir / "metric-subsets" / "subset-000000" / "receipt.json")
    )
    sub2_rec = MetricCommandReceipt.model_validate(
        read_json(case_dir / "metric-subsets" / "subset-000001" / "receipt.json")
    )
    assert full_rec.command == sub1_rec.command == sub2_rec.command
    assert full_rec.metric_setup_sha256 == sub1_rec.metric_setup_sha256 == sub2_rec.metric_setup_sha256
    assert full_rec.metric_setup_sha256 is not None

    # A second run into the same work root skips every stage and runs zero Harbor jobs
    setup_mtime = (case_dir / "metric-setup" / ".eval-author" / "audit.md").stat().st_mtime_ns
    full_mtime = (case_dir / "metric-full" / "output" / "score.json").stat().st_mtime_ns
    sub1_mtime = (case_dir / "metric-subsets" / "subset-000000" / "output" / "score.json").stat().st_mtime_ns
    run_experiment(exp, "run-1", work, runner=runner)
    assert counter["calls"] == 1
    assert (case_dir / "metric-setup" / ".eval-author" / "audit.md").stat().st_mtime_ns == setup_mtime
    assert (case_dir / "metric-full" / "output" / "score.json").stat().st_mtime_ns == full_mtime
    assert (case_dir / "metric-subsets" / "subset-000000" / "output" / "score.json").stat().st_mtime_ns == sub1_mtime

    # Changing a file under generation.settings and resigning still matches stage 0-2 receipts
    (exp.parent / "gen_prompt.txt").write_text("prompt v2\n", encoding="utf-8")
    sign_experiment(exp, resign=True)
    run_experiment(exp, "run-1", work, runner=runner)
    assert counter["calls"] == 1
    assert (case_dir / "metric-full" / "output" / "score.json").stat().st_mtime_ns == full_mtime

    # Different run-id into same work root is rejected
    with pytest.raises(ValueError, match="run-1"):
        run_experiment(exp, "run-2", work, runner=runner)

    # Changing ETHOS after render and resigning fails with "metric-setup changed; use a new work root"
    (exp.parent / "agents/smoke/ethos.md").write_text(
        "---\nname: smoke-agent\n---\n# Changed ETHOS\n", encoding="utf-8"
    )
    sign_experiment(exp, resign=True)
    with pytest.raises(ValueError, match="metric-setup changed; use a new work root"):
        run_experiment(exp, "run-1", work, runner=runner)


def test_invalidation_and_case_selection_and_tamper_checks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exp = _write_pipeline_experiment(tmp_path / "exp", case_ids=["c1", "c2"])
    work = tmp_path / "work"
    counter = {"calls": 0}
    runner = _make_fake_runner(counter=counter, loaded_exp_path=exp)

    # Unknown --case fails before any stage runs
    with pytest.raises(ValueError, match="unknown --case"):
        run_experiment(exp, "run-1", work, case_filter="missing", runner=runner)
    assert not work.exists()

    # Run only c1; c2 directory is not created
    run_experiment(exp, "run-1", work, case_filter="c1", runner=runner)
    assert counter["calls"] == 1
    assert (work / "cases" / "case-0000" / "metric-subsets").is_dir()
    assert not (work / "cases" / "case-0001").exists()

    # Run c2; both cases exist
    run_experiment(exp, "run-1", work, case_filter="c2", runner=runner)
    assert counter["calls"] == 2
    assert (work / "cases" / "case-0001" / "metric-subsets").is_dir()

    # --invalidate-stage subsets with --case c1 removes only case-0000/metric-subsets and warns
    c2_sub_mtime = (work / "cases" / "case-0001" / "metric-subsets" / "receipt.json").stat().st_mtime_ns
    run_experiment(exp, "run-1", work, invalidate_stages=["subsets"], case_filter="c1", runner=runner)
    captured = capsys.readouterr().err
    assert "subsets and generate will rerun" in captured
    assert counter["calls"] == 2
    assert (work / "cases" / "case-0001" / "metric-subsets" / "receipt.json").stat().st_mtime_ns == c2_sub_mtime

    # --invalidate-stage baseline with --case c1 removes execution & metric-full and warns
    run_experiment(exp, "run-1", work, invalidate_stages=["baseline"], case_filter="c1", runner=runner)
    captured = capsys.readouterr().err
    assert "baseline, subsets, and generate will rerun" in captured
    assert counter["calls"] == 3

    # --invalidate-stage setup with --case c1 removes metric-setup and warns
    run_experiment(exp, "run-1", work, invalidate_stages=["setup"], case_filter="c1", runner=runner)
    captured = capsys.readouterr().err
    assert "setup, baseline, subsets, and generate will rerun" in captured
    assert (
        counter["calls"] == 3
    )  # execution still valid if baseline wasn't invalidated, while metric-full/subsets rerun!

    # Verify CLI --help mentions deleting directories by hand
    help_proc = subprocess.run([sys.executable, str(RUN_CLI), "--help"], capture_output=True, text=True, check=True)
    assert "deleting those directories by hand" in help_proc.stdout

    # Tampering with published metric-setup/ fails without rerunning setup command
    audit_file = work / "cases" / "case-0000" / "metric-setup" / ".eval-author" / "audit.md"
    audit_file.write_text(audit_file.read_text(encoding="utf-8") + "\n<!-- tampered -->\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metric-setup"):
        run_experiment(exp, "run-1", work, case_filter="c1", runner=runner)


def test_stage0_and_stage1_failure_and_stage2_unavailable_and_no_setup(tmp_path: Path) -> None:
    # 1. Invalid metric-setup/ stops stage 0 naming case & path, and starts NO Harbor job
    exp1 = _write_pipeline_experiment(tmp_path / "exp_bad_setup")
    bad_setup = exp1.parent / "bad_setup.py"
    bad_setup.write_text("from pathlib import Path\nPath('ETHOS.md').write_text('incomplete')\n", encoding="utf-8")
    raw1 = yaml.safe_load(exp1.read_text(encoding="utf-8"))
    raw1["metric"]["setup"]["command"] = [sys.executable, str(bad_setup)]
    exp1.write_text(yaml.safe_dump(raw1, sort_keys=False), encoding="utf-8")
    sign_experiment(exp1, resign=True)

    c1 = {"calls": 0}
    with pytest.raises(ValueError, match=r"case smoke.*metric-setup"):
        run_experiment(exp1, "run-1", tmp_path / "work1", runner=_make_fake_runner(counter=c1, loaded_exp_path=exp1))
    assert c1["calls"] == 0

    # 2. Invalid metric.score.output in stage 1 stops stage 1
    exp2 = _write_pipeline_experiment(tmp_path / "exp_bad_score", with_setup=False)
    bad_score = exp2.parent / "bad_score.py"
    bad_score.write_text("from pathlib import Path\nPath('output').mkdir(exist_ok=True)\n", encoding="utf-8")
    raw2 = yaml.safe_load(exp2.read_text(encoding="utf-8"))
    raw2["metric"]["score"]["command"] = [sys.executable, str(bad_score)]
    raw2["metric"]["score"]["output"] = {
        "python": sys.executable,
        "type": "generation_benchmark.models:ScoreOutput",
    }
    exp2.write_text(yaml.safe_dump(raw2, sort_keys=False), encoding="utf-8")
    sign_experiment(exp2, resign=True)

    c2 = {"calls": 0}
    with pytest.raises(ValueError, match=r"case smoke.*metric-full"):
        run_experiment(exp2, "run-1", tmp_path / "work2", runner=_make_fake_runner(counter=c2, loaded_exp_path=exp2))
    assert not (tmp_path / "work2" / "cases" / "case-0000" / "metric-setup").exists()

    # 3. Stage 2 subset failure marks subset unavailable (and reuses unavailable on rerun)
    exp3 = _write_pipeline_experiment(tmp_path / "exp_sub_unavail", with_setup=False)
    partial_score = exp3.parent / "partial_score.py"
    partial_score.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--request', required=True)\n"
        "p.add_argument('--workspace', required=True)\n"
        "p.add_argument('--output-dir', required=True)\n"
        "a = p.parse_args()\n"
        "req = json.loads(Path(a.request).read_text())\n"
        "out = Path(a.output_dir)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "if req['scope'] == 'full':\n"
        "    (out / 'score.json').write_text(json.dumps({'value': 1.0, 'missing_score_reason': None}))\n"
        "else:\n"
        "    (out / 'score.json').write_text(json.dumps({'value': None, 'missing_score_reason': 'subset_failed'}))\n",
        encoding="utf-8",
    )
    raw3 = yaml.safe_load(exp3.read_text(encoding="utf-8"))
    raw3["metric"]["score"]["command"] = [sys.executable, str(partial_score)]
    raw3["metric"]["score"]["output"] = {
        "python": sys.executable,
        "type": "generation_benchmark.models:ScoreOutput",
    }
    exp3.write_text(yaml.safe_dump(raw3, sort_keys=False), encoding="utf-8")
    sign_experiment(exp3, resign=True)

    c3 = {"calls": 0}
    work3 = tmp_path / "work3"
    res3 = run_experiment(exp3, "run-1", work3, runner=_make_fake_runner(counter=c3, loaded_exp_path=exp3))
    assert res3["cases"]["smoke"]["metric_setup_sha256"] is None
    assert res3["cases"]["smoke"]["subsets"]["s1"]["status"] == "unavailable"
    assert res3["cases"]["smoke"]["subsets"]["s1"]["unavailable_reason"] == "subset_failed"
    res3_again = run_experiment(exp3, "run-1", work3, runner=_make_fake_runner(counter=c3, loaded_exp_path=exp3))
    assert res3_again["cases"]["smoke"]["subsets"]["s1"]["status"] == "unavailable"
    assert c3["calls"] == 1

    # Stage1ExecutionReceipt & SubsetsStageReceipt validate cleanly
    Stage1ExecutionReceipt.model_validate(read_json(work3 / "cases/case-0000/execution/receipt.json"))
    SubsetsStageReceipt.model_validate(read_json(work3 / "cases/case-0000/metric-subsets/receipt.json"))


def test_committed_smoke_example_passes_sign_check(tmp_path: Path) -> None:
    proc = subprocess.run(
        [str(SIGN_TOOL), str(SMOKE_EXPERIMENT), "--check"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr

    # Running sign-generation then sign-generation --check on a fresh copy exits 0
    smoke_copy = tmp_path / "smoke_copy"
    shutil.copytree(SMOKE_EXPERIMENT.parent, smoke_copy)
    (smoke_copy / "generation-experiment.lock.json").unlink()
    copied_exp = smoke_copy / "generation-experiment.yml"
    sign_proc = subprocess.run([str(SIGN_TOOL), str(copied_exp)], capture_output=True, text=True, check=False)
    assert sign_proc.returncode == 0, sign_proc.stderr
    check_proc = subprocess.run(
        [str(SIGN_TOOL), str(copied_exp), "--check"], capture_output=True, text=True, check=False
    )
    assert check_proc.returncode == 0, check_proc.stderr


@pytest.mark.skipif(
    os.environ.get("GENERATION_BENCHMARK_DOCKER_TEST") != "1",
    reason="Docker Harbor 0.24.0 smoke test runs only when GENERATION_BENCHMARK_DOCKER_TEST=1",
)
def test_smoke_docker_end_to_end_and_rerun_no_new_harbor_jobs(tmp_path: Path) -> None:
    work_root = tmp_path / "smoke-work"
    res1 = subprocess.run(
        [
            sys.executable,
            str(RUN_CLI),
            "--benchmark",
            str(SMOKE_EXPERIMENT),
            "--run-id",
            "smoke-docker",
            "--work-root",
            str(work_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res1.returncode == 0, res1.stderr
    summary1 = json.loads(res1.stdout[res1.stdout.index("{") :])
    assert summary1["cases"]["smoke"]["m_full"]["value"] == pytest.approx(1.0)
    assert summary1["cases"]["smoke"]["subsets"]["s1"]["score"]["value"] == pytest.approx(0.5)

    case_dir = work_root / "cases" / "case-0000"
    m_full = read_json(case_dir / "metric-full" / "output" / "score.json")
    m_s1 = read_json(case_dir / "metric-subsets" / "subset-000000" / "output" / "score.json")
    assert isinstance(m_full, dict) and m_full["value"] == pytest.approx(1.0)
    assert isinstance(m_s1, dict) and m_s1["value"] == pytest.approx(0.5)
    report = json.loads(
        (case_dir / "metric-subsets" / "subset-000000" / "output" / "audit-coverage-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["covered"] == ["alpha", "beta"]

    job_dir = case_dir / "execution" / "agents" / "agent" / "run-0001"
    job_result_mtime = (job_dir / "result.json").stat().st_mtime_ns
    trial_dirs = sorted(p.name for p in job_dir.iterdir() if p.is_dir())
    assert len(trial_dirs) == 4

    # Second run into the same work root starts no new Harbor jobs
    res2 = subprocess.run(
        [
            sys.executable,
            str(RUN_CLI),
            "--benchmark",
            str(SMOKE_EXPERIMENT),
            "--run-id",
            "smoke-docker",
            "--work-root",
            str(work_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 0, res2.stderr
    assert (job_dir / "result.json").stat().st_mtime_ns == job_result_mtime
    assert sorted(p.name for p in job_dir.iterdir() if p.is_dir()) == trial_dirs
