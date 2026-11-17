# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))


def test_inventory_declares_each_native_shortcut_and_credit():
    module = importlib.import_module("rubric_refund_panel")
    cases = module.control_specs()
    required = {
        "reference",
        "split-alternative",
        "permitted-zero-balance-inaction",
        "wrong-amount",
        "no-work",
        "all-failure",
        "early-exit",
        "fake-output",
        "stale-output",
        "private-tamper",
        "inert-reference",
    }
    assert required <= set(cases)
    assert all(set(c["expected"]["checks"]) == set(cases["reference"]["expected"]["checks"]) for c in cases.values())
    assert cases["missing-completion"]["expected"]["total_credit"] == 8
    assert cases["permitted-zero-balance-inaction"]["companion"] is True
    assert "socket" in module.applicability()["route"]
    assert module.applicability()["unresolved_classes"] == []


def test_current_no_action_expectations_keep_frozen_history():
    import json

    panel = importlib.import_module("rubric_refund_panel")
    path = panel.EXAMPLE / "expectations/controls.json"
    before = path.read_bytes()
    frozen = json.loads(before)
    current = panel.control_specs()
    for name, spec in current.items():
        source = name if name in panel.SELECTED else "all-failure"
        expected = frozen[source].copy()
        expected["checks"] = dict(expected["checks"])
        if source in ("no-work", "all-failure"):
            expected["checks"]["approval_before_effect"] = 1
            expected["raw_credit"] += 1
        assert spec["expected"] == expected
    assert path.read_bytes() == before


def test_agent_payload_has_no_scores_and_uses_solver_role():
    import asyncio
    from types import SimpleNamespace

    agent_module = importlib.import_module("rubric_refund_agent")
    panel = importlib.import_module("rubric_refund_panel")
    calls = []

    class Environment:
        async def exec(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(return_code=0, stdout="{}", stderr="")

    agent = object.__new__(agent_module.RefundControlAgent)
    agent.case = "wrong-amount"
    agent.payload = agent_module.payload(panel.control_specs()["wrong-amount"])
    assert set(agent.payload) == {"case", "candidate"}
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        agent.logs_dir = Path(directory)
        asyncio.run(agent.run("", Environment(), SimpleNamespace()))
    assert calls[0]["user"] == "65534"
    assert "expected" not in calls[0]["command"]
    assert "/usr/local/bin/python -I -S -B" in calls[0]["command"]


def test_failed_native_attempts_remain_operational_not_defenses(tmp_path, monkeypatch):
    import subprocess

    panel = importlib.import_module("rubric_refund_panel")
    monkeypatch.setenv("EVAL_AUTHOR_NATIVE_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setattr(panel.collector, "preflight", lambda env: None)
    monkeypatch.setattr(
        panel.collector.recorder.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1)
    )
    result = panel.collect(tmp_path / "panel", None)
    assert result["counts"]["attempts"] == len(panel.control_specs())
    assert result["counts"]["completed"] == 0
    assert result["counts"]["infrastructure_errors"] == len(panel.control_specs())
    assert result["ac02_score"] == 1
    assert all(c["reward"] is None for c in result["controls"])
    raw = (tmp_path / "panel/native-index.json").read_bytes()
    assert panel.collector.admission(raw, tmp_path / "panel")["schema"] == panel.SCHEMA


def test_ordinary_native_projection_uses_exact_anchor():
    module = importlib.import_module("rubric_example_native")
    assessment = importlib.import_module("rubric_example_assessment")
    rows = {}
    assessment._ensure_rows(rows)
    module.apply(
        rows,
        {
            "scope": "eval-author.refund-attack-panel.v1",
            "ac02_score": 2,
            "index_digest": "a" * 64,
            "counts": {},
            "observations": {},
        },
    )
    assert rows["AC-02"]["score"] == 2
    assert rows["AC-02"]["evidence_strength"] == "E2"
    assert all(c["outcome"] == "PASS" for c in rows["AC-02"]["check_results"])
    assert rows["AC-03"]["score"] is None


def test_probe_receipt_must_prove_actual_attack_reach():
    import pytest

    panel = importlib.import_module("rubric_refund_panel")
    fake = {"case": "fake-output", "process_exit": None, "replies": [], "writes": {}}
    with pytest.raises(ValueError):
        panel.validate_attack_reach(fake)
    fake["writes"] = {
        p: "written"
        for p in ("/tmp/submission.json", "/tmp/solution.py", "/logs/verifier/reward.txt", "/logs/verifier/checks.json")
    }
    panel.validate_attack_reach(fake)
    fake["case"] = "stale-output"
    with pytest.raises(ValueError):
        panel.validate_attack_reach(fake)
    fake["process_exit"] = 1
    panel.validate_attack_reach(fake)


def test_native_agent_cli_name_is_validated_not_import_alias():
    panel = importlib.import_module("rubric_refund_panel")
    config = {"name": "rubric_refund_agent:RefundControlAgent", "import_path": None, "kwargs": {"case": "reference"}}
    panel.validate_agent_config(config, "reference")


def test_registered_panel_relocation_and_tamper(tmp_path):
    import json
    import os
    import shutil

    import pytest

    location = os.environ.get("EVAL_AUTHOR_TEST_REFUND_PANEL")
    if not location:
        pytest.skip("requires current native refund panel")
    panel = importlib.import_module("rubric_refund_panel")
    bundle = tmp_path / "moved"
    shutil.copytree(location, bundle, symlinks=True)
    raw = (bundle / "native-index.json").read_bytes()
    document = panel.collector.admission(raw, bundle)
    assert panel.validate(bundle, document)["ac02_score"] == 2
    reward = bundle / "trials/fake-output/verifier/reward.txt"
    reward.write_text("1\n")
    with pytest.raises(ValueError):
        panel.validate(bundle, document)
    # Even a coherent outer inventory cannot make native credit agree with current scorer output.
    for row in document["files"]:
        if row["path"] == "trials/fake-output/verifier/reward.txt":
            row["sha256"] = panel.collector.recorder.digest(reward)
    with pytest.raises(ValueError):
        panel.validate(bundle, document)
    with pytest.raises(ValueError):
        panel.collector.admission(json.dumps(document).encode(), bundle)
