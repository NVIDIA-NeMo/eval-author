# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"
sys.path.insert(0, str(SCRIPTS))
import rubric_create02_integration as integration  # noqa: E402
import rubric_create02_subject as subject  # noqa: E402
import rubric_create02_subject_runtime as runtime  # noqa: E402


def test_fresh_repair_actual_consumers(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    root = tmp_path / "repair"
    selected = integration.build_repair(root)
    before = subject.implementation()
    projection = integration.collect_repair(root, selected)
    assert len(projection["pairs"]) == len(projection["defective_counterparts"]) == 28
    assert projection["approval_inherited"] is False
    assert projection["fresh_policy_execution"]
    assert subject.implementation() == before
    assert {p["partition"] for p in projection["pairs"]} == {"retained-affected", "fresh-same-family"}
    for bad, good in zip(projection["defective_counterparts"], projection["pairs"], strict=True):
        for arm in ("baseline", "intervention"):
            assert good[arm]["published"] == good[arm]["evidence_oracle"]
            assert bad[arm]["answer"] == good[arm]["answer"]
        if good["class"] == "misleading-cue" and good["policy"] == "cue-only":
            assert bad["published_transition"] == [0, 1]
            assert good["published_transition"] == [1, 0]
    assert {runtime.instance(i)["evidence"]["order"]["id"] for i in range(2)}.isdisjoint(
        {runtime.instance(i)["evidence"]["order"]["id"] for i in (2, 3)}
    )


def test_repair_ordinary_table_no_transferred_score(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    root = tmp_path / "repair"
    integration.build_repair(root)
    output = tmp_path / "report"
    assert (
        integration.repair_cli(
            ["--root", str(root), "--selection", str(root / "selection.json"), "--output", str(output)]
        )
        == 1
    )
    saved = json.loads((output / "assessment.json").read_text())
    assert saved["report"]["items"]["CREATE-02"]["score"] is None
    assert saved["report"]["items"]["CREATE-02"]["execution_state"] == "COMPLETED"
    assert integration.repair_cli(["--root", str(root), "--verify-existing", str(output)]) == 1


def test_repaired_runtime_reseal_rejected(tmp_path):
    root = tmp_path / "repair"
    selected = integration.build_repair(root)
    path = root / "subject-runtime.py"
    path.write_bytes(Path(runtime.__file__).read_bytes())
    selected["artifacts"][1]["digest"] = integration.sha(path)
    selected["artifacts"][1]["evidence"][0]["digest"] = integration.sha(path)
    with pytest.raises(ValueError, match="unsupported repaired source"):
        integration.admit(root, selected)
