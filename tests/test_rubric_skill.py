# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/eval-author-rubric"


def test_rubric_scope_and_initial_slice() -> None:
    text = (SKILL / "SKILL.md").read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["maturity"] == "alpha"
    assert metadata["user-invocable"] is True
    assert "validity, coverage, and diversity" in text
    assert "anti-cheat within validity" in text
    assert "not implemented by this initial slice" in text
    assert "not a new scanner or acceptance gate" in text
    assert "Do not write an Audit" in text
    assert "A review request alone does not authorize executing repository code" in text
    assert "Default to the response only" in text


@pytest.mark.parametrize("item_id", ["AC-01", "AC-02", "AC-03", "AC-04"])
def test_each_anti_cheat_item_has_an_actionable_contract(item_id: str) -> None:
    text = (SKILL / "references/anti-cheat.md").read_text()
    sections = dict(re.findall(r"## (AC-\d+): ([\s\S]*?)(?=\n## |\Z)", text))
    assert set(sections) == {"AC-01", "AC-02", "AC-03", "AC-04"}
    section = sections[item_id]
    for field in (
        "Pillar",
        "Question",
        "Requirement",
        "Scope",
        "Measurement",
        "Checks",
        "Scoring",
        "Evidence",
        "On gaps or failure",
        "Boundary",
    ):
        assert f"**{field}:**" in section
    for method in ("Deterministic:", "Review:", "Probe:"):
        assert method in section
    for score in ("0:", "1:", "2:", "3:"):
        assert score in section


def test_rubric_references_survive_copied_skill_install(tmp_path: Path) -> None:
    import shutil

    for name in ("eval-author", "eval-author-rubric"):
        shutil.copytree(ROOT / "skills" / name, tmp_path / name)
    for document in (tmp_path / "eval-author-rubric").rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            assert not target.startswith(("http:", "https:"))
            path = target.split("#", 1)[0]
            assert (document.parent / path).is_file(), target


def test_statuses_do_not_turn_missing_evidence_into_passes() -> None:
    text = (SKILL / "references/anti-cheat.md").read_text()
    for expected in (
        "NOT_ASSESSED",
        "NOT_APPLICABLE",
        "E0",
        "E1",
        "E2",
        "E3",
        "Do not average items",
        "model-training",
        "valid-alternative",
        "infrastructure-failed",
        "not the rubric's coverage pillar",
    ):
        assert expected in text


def test_rubric_dataset_contains_positive_and_negative_cases() -> None:
    dataset = json.loads((SKILL / "evals/evals.json").read_text())
    assert dataset["skill_name"] == "eval-author-rubric"
    cases = dataset["evals"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert any(case["expected_skill"] is None for case in cases)
    assert any(case["expected_skill"] == "eval-author-rubric" for case in cases)
    for case in cases:
        assert case["prompt"] and case["expected_output"] and case["assertions"]
        assert case["files"] == []
