# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Find repeated-operation candidates with the real Compass loader and synthetic ATIF traces."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from eval_author_shared.retry_audit import audit_retries  # noqa: E402
from eval_author_shared.safe_trace import load_trace  # noqa: E402

READ = {"name": "read", "inputSchema": {"type": "object"}}
WRITE = {"name": "write", "inputSchema": {"type": "object"}}


def call(call_id, name="read", path="file"):
    return {"tool_call_id": call_id, "function_name": name, "arguments": {"path": path}}


def result(call_id, error):
    status = {} if error is None else {"is_error": error}
    return {"source_call_id": call_id, "content": "private observation text", **status}


def step(number, *pairs):
    """One agent step. Each pair is (call, error flag or None for an unrecorded status)."""
    payload = {"step_id": number, "source": "agent", "message": "private agent text"}
    if pairs:
        payload["tool_calls"] = [item for item, _ in pairs]
        payload["observation"] = {"results": [result(item["tool_call_id"], error) for item, error in pairs]}
    return payload


def audit(tmp_path, *steps):
    source = {
        "schema_version": "ATIF-v1.7",
        "session_id": "session",
        "trajectory_id": "trajectory",
        "agent": {"name": "synthetic", "version": "1", "tool_definitions": [READ, WRITE]},
        "steps": [{"step_id": 1, "source": "user", "message": "Read the file."}, *steps],
    }
    path = tmp_path / "source.atif.json"
    path.write_text(json.dumps(source))
    return audit_retries(load_trace(path, "atif"), safe_trace_sha256="synthetic")


def test_unchanged_repeat_after_an_explicit_error_is_a_review_candidate_with_recovery(tmp_path):
    report = audit(
        tmp_path,
        step(2, (call("a"), True)),
        step(3, (call("b"), True)),
        step(4, (call("c"), False)),
    )
    assert report["counts"]["eligible_opportunities"] == 2
    assert report["counts"]["review_candidates"] == 1
    assert report["counts"]["confirmed_violations"] == 0
    assert report["excluded"] == {"repeat_succeeded": 1}
    assert report["counts"]["assessed_opportunities"] == 1
    assert report["counts"]["excluded"] == 1
    assert report["counts"]["no_repeat"] == 0
    assert report["candidate_rate"] == 1.0
    assert type(report["candidate_rate"]) is float
    assert report["trials"] == {"observable": 1, "affected": 1}
    [finding] = report["findings"]
    assert finding["code"] == "repeat_after_error"
    assert finding["outcome"] == "review_candidate"
    assert finding["span_ids"] == ["trajectory/step/2/tool/0", "trajectory/step/3/tool/0"]
    assert finding["turn_ids"] == ["trajectory/turn/2", "trajectory/turn/3"]
    assert finding["later_success_span_id"] == "trajectory/step/4/tool/0"
    assert report["state_identity"] == "unavailable"
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "steps,outcome",
    [
        ((step(2, (call("a"), True)), step(3, (call("b"), False))), {"excluded": {"repeat_succeeded": 1}}),
        (
            (step(2, (call("a"), True)), step(3, (call("w", "write"), False)), step(4, (call("b"), True))),
            {"excluded": {"intervening_action": 1}},
        ),
        ((step(2, (call("a"), True)),), {"no_repeat": 1}),
        ((step(2, (call("a"), True)), step(3, (call("b", path="other"), True))), {"no_repeat": 2}),
        ((step(2, (call("a"), True), (call("b"), True)),), {"no_repeat": 2}),
    ],
    ids=["transient", "intervening", "no-repeat", "different-input", "same-turn"],
)
def test_legitimate_retries_and_recovery_are_not_candidates(tmp_path, steps, outcome):
    report = audit(tmp_path, *steps)
    assert report["counts"]["review_candidates"] == 0
    if "excluded" in outcome:
        assert report["excluded"] == outcome["excluded"]
    else:
        assert report["counts"]["no_repeat"] == outcome["no_repeat"]


def test_unrecorded_feedback_is_not_an_opportunity_and_unrecorded_repeat_is_unknown(tmp_path):
    report = audit(tmp_path, step(2, (call("a"), None)), step(3, (call("b"), None)))
    assert report["feedback"] == {"unrecorded": 2}
    assert report["counts"]["eligible_opportunities"] == 0
    assert report["candidate_rate"] is None
    assert report["trials"] == {"observable": 0, "affected": 0}
    report = audit(tmp_path, step(2, (call("a"), True)), step(3, (call("b"), None)))
    assert report["counts"]["eligible_opportunities"] == 1
    assert report["counts"]["assessed_opportunities"] == 0
    assert report["unknown"] == {"repeat_status_unrecorded": 1}
    assert report["candidate_rate"] is None


@pytest.mark.parametrize(
    "tail,eligible,assessed,candidates,no_repeat,rate,trials",
    [
        ((), 3, 0, 0, 0, None, {"observable": 0, "affected": 0}),
        ((step(9, (call("n", path="no-repeat"), True)),), 4, 1, 0, 1, 0.0, {"observable": 1, "affected": 0}),
        (
            (step(9, (call("c1", path="candidate"), True)), step(10, (call("c2", path="candidate"), True))),
            5,
            2,
            1,
            1,
            0.5,
            {"observable": 1, "affected": 1},
        ),
    ],
    ids=["zero-denominator", "no-repeat-denominator", "candidate-and-no-repeat-denominator"],
)
def test_retry_command_is_private_repeatable_and_preserves_the_evidence_audit(
    tmp_path, tail, eligible, assessed, candidates, no_repeat, rate, trials
):
    script = ROOT / "skills/eval-author-inspect-trace/scripts/trace_privacy.py"

    def cli(*args):
        run = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)
        return run.returncode, json.loads(run.stdout)

    code, created = cli("init", "--root", tmp_path / ".eval-author/.working/work", "--task-id", "retry-case")
    assert code == 0, created
    root = Path(created["task_dir"])
    source = tmp_path / "source.json"
    audit(
        tmp_path,
        step(2, (call("u1", path="unknown"), True)),
        step(3, (call("u2", path="unknown"), None)),
        step(4, (call("e1", path="excluded"), True)),
        step(5, (call("e2", path="excluded"), False)),
        step(6, (call("i1", path="intervening"), True)),
        step(7, (call("w", "write"), False)),
        step(8, (call("i2", path="intervening"), False)),
        *tail,
    )
    source.write_bytes((tmp_path / "source.atif.json").read_bytes())
    code, prepared = cli("prepare", "--task-dir", root, "--source", source, "--source-kind", "atif")
    assert code == 0, prepared
    first = cli("audit-retries", "--task-dir", root)
    assert first[0] == 0, first
    assert first == cli("audit-retries", "--task-dir", root)
    report = root / "private/retry-audit.json"
    saved = json.loads(report.read_text())
    assert saved["schema"] == "nemo.eval_author.retry_audit.v1"
    expected_counts = {
        "tool_calls": 7 + len(tail),
        "eligible_opportunities": eligible,
        "assessed_opportunities": assessed,
        "review_candidates": candidates,
        "confirmed_violations": 0,
        "no_repeat": no_repeat,
        "excluded": 2,
        "unknown": 1,
        "ineligible": 0,
    }
    assert saved["counts"] == first[1]["counts"] == expected_counts
    assert all(type(value) is int for value in saved["counts"].values())
    assert saved["excluded"] == {"intervening_action": 1, "repeat_succeeded": 1}
    assert saved["unknown"] == {"repeat_status_unrecorded": 1}
    assert saved["candidate_rate"] == rate
    assert type(saved["candidate_rate"]) is type(rate)
    assert saved["trials"] == trials
    assert all(type(value) is int for value in saved["trials"].values())
    assert "private observation text" not in report.read_text()
    assert not (root / "private/evidence-audit.json").exists()
    report.write_text("{}")
    assert cli("audit-retries", "--task-dir", root)[0] == 1
    assert report.read_text() == "{}"
