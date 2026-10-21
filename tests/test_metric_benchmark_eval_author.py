# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from metric_benchmark.coverage import invoke_coverage
from metric_benchmark.eval_author import source_digest
from metric_benchmark.models import Subset
from test_metric_benchmark_config import LAB
from test_metric_benchmark_coverage import coverage_example


def eval_author_example(tmp_path, *, non_tool=False):
    args = list(coverage_example(tmp_path))
    root = LAB.parents[1]
    for row in args[0].results:
        native = args[-2] / row.native_results_path / "agent"
        native.mkdir()
        name = row.evaluation.task.task_id.replace("-", "_")
        (native / "trajectory.json").write_text(
            json.dumps(
                {
                    "schema_version": "ATIF-v1.7",
                    "session_id": "test",
                    "agent": {"name": "example", "version": "1"},
                    "steps": [
                        {"step_id": 1, "source": "user", "message": "Test request."},
                        {
                            "step_id": 2,
                            "source": "agent",
                            "message": "Test action.",
                            "tool_calls": [{"tool_call_id": "call-1", "function_name": name, "arguments": {}}],
                        },
                    ],
                }
            )
        )
    items = [
        {
            "kind": "tool",
            "name": name,
            "description": "Test tool.",
            "expected_use": "Test action.",
            "expected_failure_behavior": "Report failure.",
            "evidence_required": [{"kind": "tool_call", "description": "Call tool.", "tool": name}],
        }
        for name in ("task_0", "task_1")
    ]
    if non_tool:
        items.append(
            {
                "kind": "capability",
                "name": "intent",
                "description": "Recognize user intent.",
                "required_tools": [],
                "expected_behavior": "Understand the request.",
                "evidence_required": [{"kind": "user_intent", "description": "User requests help."}],
            }
        )
    payload = {"schema": "nemo.eval_author.audit.v1", "agent": "test", "status": "draft", "items": items}
    audit = (
        "<!-- BEGIN:nemo-eval-author-audit:v1 -->\n```yaml\n"
        + yaml.safe_dump(payload)
        + "```\n<!-- END:nemo-eval-author-audit:v1 -->\n"
    )
    author = tmp_path / "author.py"
    author.write_text(
        "from pathlib import Path\nimport argparse\np=argparse.ArgumentParser()\n"
        "p.add_argument('--request');p.add_argument('--output-dir');a=p.parse_args()\n"
        "out=Path(a.output_dir);out.mkdir()\n"
        f"(out/'audit.md').write_text({audit!r})\n(out/'judgments.json').write_text('{{}}')\n"
    )
    args[3].system_id = "eval-author"
    args[3].system_revision = "test-pinned-source"
    args[3].command = [sys.executable, str(LAB / "eval_author_coverage.py")]
    args[3].settings = {
        "eval_author_root": str(root),
        "source_sha256": source_digest(root),
        "author_command": [sys.executable, str(author)],
    }
    return args


def test_real_measurement_adapter_replay_and_cold_repeat(tmp_path):
    args = eval_author_example(tmp_path)
    first = invoke_coverage(*args)
    assert [s.value for s in first.scores.values()] == [0.5, 0.5, 0.0]
    assert (args[-1] / "subset-000001/report.json").is_file()
    second_args = [*args[:-1], tmp_path / "second"]
    second = invoke_coverage(*second_args)
    # Native provenance includes absolute workspace paths; receipts bind those
    # exact bytes, while scores and their experiment identities stay fixed.
    assert second.model_dump(exclude={"receipts"}) == first.model_dump(exclude={"receipts"})
    for work_root in (args[-1], second_args[-1]):
        for report in sorted(work_root.glob("subset-*/report.json")):
            verify = subprocess.run(
                [
                    sys.executable,
                    str(LAB.parents[1] / "skills/eval-author-audit/scripts/audit_spec/report.py"),
                    "--verify-existing",
                    str(report),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert json.loads(verify.stdout)["valid"]
    Path(args[3].settings["author_command"][1]).write_text("raise AssertionError('must not reauthor')")
    assert invoke_coverage(*args, cached_only=True) == first


def test_missing_semantic_judgments_are_unavailable(tmp_path):
    args = eval_author_example(tmp_path, non_tool=True)
    scores = invoke_coverage(*args)
    assert all(s.value is None and s.reason is not None and "judgments" in s.reason for s in scores.scores.values())


def test_bad_runtime_pin_fails_without_measurement(tmp_path):
    args = eval_author_example(tmp_path)
    args[3].settings["source_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="exited"):
        invoke_coverage(*args)


def test_multiple_tasks_have_distinct_measurement_identities(tmp_path):
    args = eval_author_example(tmp_path)
    args[1].eval_subsets = [Subset(eval_subset_id="all", tasks=args[0].task_inventory.tasks)]
    scores = invoke_coverage(*args)
    assert scores.scores["all"].value == 1.0
    report = json.loads((args[-1] / "subset-000001/report.json").read_text())
    subjects = [item["subject"] for item in report["input_reports"]]
    assert len({subject["run_id"] for subject in subjects}) == 3
