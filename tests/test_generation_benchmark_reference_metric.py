# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from generation_benchmark.models import OutputSpec
from generation_benchmark.reference_metric import (
    SETUP_CHANGED_ERROR,
    ToolCallsScoreOutput,
    ToolCallsSetupOutput,
    _load_audit_spec,
)
from generation_benchmark.validation import validate_directory_output
from metric_benchmark.io import json_bytes

GEN_LAB_ROOT = Path(__file__).resolve().parents[1] / "labs" / "generation-benchmark"
REF_METRIC_CLI = GEN_LAB_ROOT / "reference_metric.py"


def _write_items_yaml(path: Path, tools: list[str]) -> None:
    items = [
        {
            "kind": "tool",
            "name": name,
            "description": f"Call tool {name}.",
            "expected_use": f"Use {name} during smoke execution.",
            "expected_failure_behavior": f"Report error if {name} fails.",
            "evidence_required": [
                {
                    "kind": "tool_call",
                    "description": f"ATIF contains tool call {name}.",
                    "tool": name,
                }
            ],
        }
        for name in tools
    ]
    path.write_text(yaml.safe_dump({"items": items}, sort_keys=False), encoding="utf-8")


def _write_trial(trials_dir: Path, task_id: str, tools_called: list[str]) -> Path:
    trial_dir = trials_dir / task_id
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    tool_calls = [
        {"tool_call_id": f"call-{idx}", "function_name": name, "arguments": {}}
        for idx, name in enumerate(tools_called, start=1)
    ]
    trajectory = {
        "schema_version": "ATIF-v1.8",
        "session_id": f"session-{task_id}",
        "agent": {"name": "smoke-agent", "version": "1.0"},
        "steps": [
            {"step_id": 1, "source": "user", "message": f"Exercise {task_id}"},
            {
                "step_id": 2,
                "source": "agent",
                "message": "Done.",
                "tool_calls": tool_calls,
            },
        ],
    }
    (agent_dir / "trajectory.json").write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "task_name": f"smoke/{task_id}",
                "trial_name": f"{task_id}__trial",
                "agent_info": {"name": "smoke-agent", "version": "1.0"},
            }
        ),
        encoding="utf-8",
    )
    return trial_dir


def test_reference_setup_renders_tool_audit_and_detects_changes(tmp_path: Path) -> None:
    src_dir = tmp_path / "case_src"
    src_dir.mkdir()
    ethos = src_dir / "ethos.md"
    ethos.write_text("---\nname: smoke-agent\n---\n# Smoke Agent\n", encoding="utf-8")
    items = src_dir / "items.yaml"
    _write_items_yaml(items, ["alpha", "beta", "gamma", "delta"])

    metric_setup = tmp_path / "metric-setup"
    metric_setup.mkdir()
    req_path = metric_setup / "request.json"
    req_path.write_bytes(
        json_bytes(
            {
                "schema": "nemo.eval_author.metric_setup_request.v1",
                "case_id": "smoke",
                "ethos_path": str(ethos),
                "agent_path": str(src_dir / "agent.yml"),
                "tasks_path": str(src_dir / "tasks.yml"),
                "subsets_path": str(src_dir / "subsets.json"),
                "settings": {"items": str(items)},
            }
        )
    )

    res1 = subprocess.run(
        [sys.executable, str(REF_METRIC_CLI), "--setup", "--request", "request.json", "--output-dir", "."],
        cwd=metric_setup,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res1.returncode == 0, res1.stderr

    # Verify audit loads with load_audit_spec, every item has kind: tool, sources[0].path == "../ETHOS.md"
    audit_path = metric_setup / ".eval-author" / "audit.md"
    spec = _load_audit_spec(audit_path)
    assert spec["agent"] == "smoke-agent"
    assert spec["sources"][0]["path"] == "../ETHOS.md"
    assert [item["kind"] for item in spec["items"]] == ["tool", "tool", "tool", "tool"]

    setup_out = ToolCallsSetupOutput.load(metric_setup)
    assert setup_out.tool_names == ["alpha", "beta", "gamma", "delta"]

    # Undeclared files (request.json, extra.log) are ignored by ToolCallsSetupOutput.load
    (metric_setup / "extra.log").write_text("ok\n", encoding="utf-8")
    ToolCallsSetupOutput.load(metric_setup)

    # Identical rerun reuses cleanly
    audit_before = audit_path.read_bytes()
    res_reuse = subprocess.run(
        [sys.executable, str(REF_METRIC_CLI), "--setup", "--request", "request.json", "--output-dir", "."],
        cwd=metric_setup,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_reuse.returncode == 0, res_reuse.stderr
    assert audit_path.read_bytes() == audit_before

    # Changing ETHOS after render and rerunning fails with "metric-setup changed; use a new work root"
    ethos.write_text("---\nname: smoke-agent\n---\n# Updated ETHOS\n", encoding="utf-8")
    res_changed = subprocess.run(
        [sys.executable, str(REF_METRIC_CLI), "--setup", "--request", "request.json", "--output-dir", "."],
        cwd=metric_setup,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_changed.returncode != 0
    assert SETUP_CHANGED_ERROR in res_changed.stderr


def test_reference_score_reads_setup_in_place_and_computes_fractions(tmp_path: Path) -> None:
    src_dir = tmp_path / "case_src"
    src_dir.mkdir()
    ethos = src_dir / "ethos.md"
    ethos.write_text("---\nname: smoke-agent\n---\n# Smoke Agent\n", encoding="utf-8")
    items = src_dir / "items.yaml"
    _write_items_yaml(items, ["alpha", "beta", "gamma", "delta"])

    metric_setup = tmp_path / "metric-setup"
    metric_setup.mkdir()
    (metric_setup / "request.json").write_bytes(
        json_bytes(
            {
                "schema": "nemo.eval_author.metric_setup_request.v1",
                "case_id": "smoke",
                "ethos_path": str(ethos),
                "agent_path": str(src_dir / "agent.yml"),
                "tasks_path": str(src_dir / "tasks.yml"),
                "subsets_path": str(src_dir / "subsets.json"),
                "settings": {"items": str(items)},
            }
        )
    )
    setup_res = subprocess.run(
        [sys.executable, str(REF_METRIC_CLI), "--setup", "--request", "request.json", "--output-dir", "."],
        cwd=metric_setup,
        capture_output=True,
        text=True,
        check=False,
    )
    assert setup_res.returncode == 0, setup_res.stderr
    setup_files_before = {p.relative_to(metric_setup): p.read_bytes() for p in metric_setup.rglob("*") if p.is_file()}

    # Score full set (4 tools -> 1.0) and subset (alpha, beta -> 0.5)
    for scope, tasks, expected_score, expected_covered in (
        ("full", ["alpha", "beta", "gamma", "delta"], 1.0, ["alpha", "beta", "gamma", "delta"]),
        ("subset", ["alpha", "beta"], 0.5, ["alpha", "beta"]),
    ):
        inv_dir = tmp_path / f"metric-{scope}"
        workspace = inv_dir / "workspace"
        trials_dir = workspace / "trials"
        for task in tasks:
            _write_trial(trials_dir, task, [task])
        output_dir = inv_dir / "output"
        output_dir.mkdir(parents=True)
        (inv_dir / "request.json").write_bytes(
            json_bytes(
                {
                    "schema": "nemo.eval_author.metric_score_request.v1",
                    "case_id": "smoke",
                    "scope": scope,
                    "subset_id": None if scope == "full" else "s1",
                    "metric_setup_dir": str(metric_setup),
                    "tasks": [{"task_id": t, "revision": 1} for t in tasks],
                    "trials": [
                        {
                            "task_id": t,
                            "revision": 1,
                            "status": "succeeded",
                            "trial_path": f"workspace/trials/{t}",
                        }
                        for t in tasks
                    ],
                    "settings": {"method": "tool_calls"},
                }
            )
        )
        score_res = subprocess.run(
            [
                sys.executable,
                str(REF_METRIC_CLI),
                "--request",
                "request.json",
                "--workspace",
                "workspace",
                "--output-dir",
                "output",
            ],
            cwd=inv_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert score_res.returncode == 0, score_res.stderr

        # Verify output validates via subprocess validator as ToolCallsScoreOutput
        validate_directory_output(
            OutputSpec(
                python=sys.executable,
                type="generation_benchmark.reference_metric:ToolCallsScoreOutput",
            ),
            output_dir,
            require_score_output=True,
            case_id="smoke",
            subset_id=None if scope == "full" else "s1",
        )
        loaded = ToolCallsScoreOutput.load(output_dir)
        assert loaded.score.value == pytest.approx(expected_score)
        assert loaded.coverage_report["covered"] == expected_covered

    # Confirm metric-setup/ was untouched (no new audit written)
    setup_files_after = {p.relative_to(metric_setup): p.read_bytes() for p in metric_setup.rglob("*") if p.is_file()}
    assert setup_files_after == setup_files_before
