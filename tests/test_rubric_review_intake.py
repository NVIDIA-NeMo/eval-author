# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))


def intake():
    return importlib.import_module("rubric_review_intake")


def test_changed_base_cannot_relabel_stale_crosswalk(tmp_path, monkeypatch):
    import shutil

    module = intake()
    task = tmp_path / "base"
    shutil.copytree(module.BASE, task)
    (task / "tests/refund_scorer.py").write_text("# different scoring contract\n")
    monkeypatch.setattr(module, "BASE", task)
    with pytest.raises(ValueError):
        module.prepare_task(task)


def test_public_simulation_policy_is_explicit():
    task = ROOT / "skills/eval-author-rubric/examples/refund/base"
    text = (task / "instruction.md").read_text()
    requirements = json.loads((task / "environment/contract.json").read_text())["requirements"]
    assert "Each new refund must be positive" in text
    assert "Each new refund must be positive" in requirements["amount"]
    assert "before or after refund actions" in text
    assert "before or after refund actions" in requirements["ledger"]
    assert "Verifier source is public and readable" in text
    assert "approval precedes every refund" in requirements["approval_before_effect"]


def test_prepare_refund_questions_binds_quotes_and_keeps_intent_undecided():
    module = intake()
    packet = module.prepare_task(ROOT / "skills/eval-author-rubric/examples/refund/base")
    assert set(packet["questions"]) == {
        "contract_crosswalk",
    }
    assert all(row["status"] == "supported" for row in packet["crosswalk"])
    assert packet["declared_policy"] == {
        "positive_refunds": "positive_only",
        "ledger_order": "final_multiset",
        "verifier_source": "public_readable_integrity_protected",
        "solver_actions": "offline_socket_no_solver_reset",
        "external_state": "pinned_local_simulation",
    }
    assert packet["computed"]["remaining_balance_cents"] == 8000
    assert packet["computed"]["approval_required"] is True
    assert packet["computed"]["max_credit"] == 9
    assert packet["computed"]["mandatory_credit"] == 8
    assert {row["check"] for row in packet["crosswalk"]} >= set(packet["checks"]) | {
        "verifier_access",
        "solver_actions",
        "external_state",
    }
    assert packet["expected_examples"] == {
        "reference": {"total_credit": 9, "failed_checks": []},
        "correct-total-plus-zero": {"total_credit": 0, "failed_checks": ["amount"]},
        "ledger-before-refund": {"total_credit": 9, "failed_checks": []},
        "reversed-ledger-rows": {"total_credit": 9, "failed_checks": []},
        "late-approval": {"total_credit": 0, "failed_checks": ["approval_before_effect"]},
        "optional-completion": {"total_credit": 8, "failed_checks": ["completion_signal"]},
    }
    assert packet["decisions"] == {}
    for citation in packet["citations"].values():
        raw = (Path(packet["task"]) / citation["path"]).read_bytes()
        lines = raw.decode().splitlines(keepends=True)
        assert citation["quote"] == "".join(lines[citation["start_line"] - 1 : citation["end_line"]])
    rendered = module.render(packet)
    assert "ledger-before-refund" in rendered
    assert "positive_only" in rendered
    assert "8" in rendered
    assert "No owner decision" in rendered


@pytest.mark.parametrize(
    ("case", "credit", "failed"),
    [
        ("reference", 9, set()),
        ("zero-extra", 0, {"amount"}),
        ("ledger-before-refund", 9, set()),
        ("reversed-ledger-rows", 9, set()),
        ("late-approval", 0, {"approval_before_effect"}),
        ("optional-completion", 8, {"completion_signal"}),
    ],
)
def test_clarified_policy_preserves_actual_scorer(tmp_path, case, credit, failed):
    import importlib.util
    from copy import deepcopy

    task = ROOT / "skills/eval-author-rubric/examples/refund/base"
    loaded = {}
    for name, relative in {"scorer": "tests/refund_scorer.py", "ledger": "environment/ledger.py"}.items():
        spec = importlib.util.spec_from_file_location(f"policy_{name}", task / relative)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[name] = module
    controls = json.loads((task.parent / "controls.json").read_text())["cases"]
    control = "split-alternative" if case == "reversed-ledger-rows" else "reference"
    candidate = deepcopy(next(row["candidate"] for row in controls if row["id"] == control))
    events = candidate["events"]
    if case == "zero-extra":
        refund = deepcopy(events[1])
        refund["refund"].update(id="zero-extra", amount_cents=0)
        events.extend([refund, {"kind": "ledger", "entries": [{"refund_id": "zero-extra", "amount_cents": 0}]}])
    elif case == "ledger-before-refund":
        events[1], events[2] = events[2], events[1]
    elif case == "reversed-ledger-rows":
        for event in events:
            if event["kind"] == "ledger":
                event["entries"].reverse()
    elif case == "late-approval":
        events.append(events.pop(0))
    elif case == "optional-completion":
        candidate["success"] = False
    contract = json.loads((task / "environment/contract.json").read_text())
    engine = loaded["ledger"].Ledger(contract, tmp_path / "record.json")
    for event in events:
        engine.act(event)
    engine.complete(candidate["success"])
    assert engine.read()["candidate"] == candidate
    result = loaded["scorer"].evaluate(contract, engine.read()["candidate"])
    assert result["health"] == "ok"
    assert result["total_credit"] == credit
    assert result["max_credit"] == 9
    assert {key for key, value in result["checks"].items() if value == 0} == failed


class SyntheticHost:
    # Models an external application store, never a transcript authenticator.
    def __init__(self):
        self.saved = None

    def decide(self, request):
        self.saved = {
            "request": request,
            "purpose": "rubric_qualification",
            "owner_id": "synthetic-owner",
            "session_id": "synthetic-owner-session",
            "message_id": "test-event",
            "role": "user",
            "save_authorized": True,
            "content": "Synthetic scoped answers, not real approval.",
            "answers": {
                key: {"choice": "defer", "rationale": "Synthetic pending choice."} for key in request["questions"]
            },
        }
        return self.saved

    def read(self, binding):
        return self.saved


def test_host_roundtrip_retains_exact_choices_without_scores():
    module = intake()
    packet = module.prepare_task(ROOT / "skills/eval-author-rubric/examples/refund/base")
    host = SyntheticHost()
    result = module.resolve(packet, host)
    assert result["status"] == "recorded"
    assert result["event"] == host.saved
    assert result["scores_changed"] is False
    assert host.saved is not None
    assert result["decisions"] == host.saved["answers"]
    assert module.replay(packet, host, result) == result
    host.saved = None
    with pytest.raises(ValueError):
        module.replay(packet, host, result)


@pytest.mark.parametrize(
    "change", ["purpose", "request", "role", "save", "choice", "rationale", "partial", "changed_read"]
)
def test_host_rejects_wrong_scope_consent_and_changed_readback(change, monkeypatch):
    module = intake()
    packet = module.prepare_task(ROOT / "skills/eval-author-rubric/examples/refund/base")
    host = SyntheticHost()
    original = host.decide

    def decide(request):
        event = original(request)
        if change == "purpose":
            event["purpose"] = "execution_approval"
        elif change == "request":
            event["request"] = {}
        elif change == "role":
            event["role"] = "assistant"
        elif change == "save":
            event["save_authorized"] = False
        elif change == "choice":
            event["answers"]["contract_crosswalk"]["choice"] = "yes"
        elif change == "rationale":
            event["answers"]["contract_crosswalk"]["rationale"] = ""
        elif change == "partial":
            event["answers"].pop("contract_crosswalk")
        return event

    monkeypatch.setattr(host, "decide", decide)
    if change == "changed_read":
        monkeypatch.setattr(host, "read", lambda binding: None)
    with pytest.raises(ValueError):
        module.resolve(packet, host)


def test_source_changes_require_new_question_packet(tmp_path):
    import shutil

    module = intake()
    task = tmp_path / "task"
    shutil.copytree(ROOT / "skills/eval-author-rubric/examples/refund/base", task)
    packet = module.prepare_task(task)
    (task / "instruction.md").write_text("Changed public rules.")
    with pytest.raises(ValueError):
        module.resolve(packet, SyntheticHost())


def test_read_only_cli_emits_same_packet(tmp_path):
    import subprocess

    module = intake()
    task = ROOT / "skills/eval-author-rubric/examples/refund/base"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "rubric_review_intake.py"), "--task", str(task), "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == module.prepare_task(task)
    denied = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "rubric_review_intake.py"), "--task", str(task), "--decision", "approve"],
        capture_output=True,
        text=True,
    )
    assert denied.returncode == 2


def test_no_host_cannot_be_replaced_by_packet_approval():
    module = intake()
    packet = module.prepare_task(ROOT / "skills/eval-author-rubric/examples/refund/base")
    packet["decisions"] = {"positive_refunds": "positive_only"}
    with pytest.raises(ValueError):
        module.resolve(packet, None)
