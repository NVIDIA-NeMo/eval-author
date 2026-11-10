# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
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


def test_rubric_routes_only_the_explicitly_selected_assessment_area() -> None:
    text = " ".join((SKILL / "SKILL.md").read_text().split())
    for rule in (
        "Anti-cheat-only opt-in does not select W02",
        "W02-only opt-in does not select anti-cheat",
        "If the named-rubric request leaves the area ambiguous, clarify the scope once before inspecting artifacts",
        "Apply every selected criterion",
        "Account for every selected task and every selected criterion, including unknowns",
        "Do not apply unselected areas",
        "Use only controls needed by selected criteria",
        "W01 and W05 are downstream handoffs, not current deliverables or blockers",
    ):
        assert rule in text
    assert "references/anti-cheat.md" in text
    assert "references/artifact-configuration-provenance.md" in text
    assert "references/identity.md" in text
    assert "Apply every item in the reference" not in text
    assert "Never promote local predicate scores" not in text


def test_w02_canonical_source_preserves_criterion_checks_and_anchors() -> None:
    source = SKILL / "references/artifact-configuration-provenance.json"
    assert source.is_file()
    data = json.loads(source.read_text())
    items = data["items"]
    assert {item["id"] for item in items} == {
        "DISC-01-A",
        "DISC-01-B",
        "DISC-01-C",
        "DISC-02-A",
        "DISC-02-B",
        "DISC-03-A",
        "AUD-02",
        "ENV-01",
        "ENV-02-A",
        "ENV-03",
        "CREATE-07",
    }
    assert len(items) == 11
    checks = [check for item in items for check in item["checks"]]
    assert len(checks) == len({check["id"] for check in checks}) == 38
    assert sum(len(item["scoring"]) for item in items) == 44
    assert all(set(item["scoring"]) == {"0", "1", "2", "3"} for item in items)
    # Frozen Rubric v4 W02 subset, including original questions, checks, and anchors.
    canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        "b11c83765d5e7bd84b85c24c99d137aad38bed0ef7eb5ec65fe8f4216eeec135"
    )


def test_w02_reference_uses_canonical_definitions_and_item_specific_evidence() -> None:
    reference = SKILL / "references/artifact-configuration-provenance.md"
    assert reference.is_file()
    text = reference.read_text()
    normalized = " ".join(text.split())
    items = json.loads((SKILL / "references/artifact-configuration-provenance.json").read_text())["items"]
    sections = dict(re.findall(r"## ([A-Z]+-\d+(?:-[A-Z])?) — ([\s\S]*?)(?=\n## |\Z)", text))
    assert set(sections) == {item["id"] for item in items}
    for item in items:
        assert sections[item["id"]].startswith(item["title"] + "\n")
        assert "**Methods and evidence:**" in sections[item["id"]]
        assert "**Assessment limit:**" in sections[item["id"]]
        assert f"artifact-configuration-provenance.json#/items/{items.index(item)}" in sections[item["id"]]
        for anchor in item["scoring"].values():
            assert anchor not in text, "Keep full score anchors in canonical JSON only"
    for rule in (
        "E0 alone supports no numeric score",
        "Do not import anti-cheat's evidence-floor table",
        "CREATE-07 and AUD-02 can meet their level-3 anchors at E2",
        "W01 and W05 are downstream handoffs, not prerequisites for running this assessment",
        "their actual consumers use the shared validated identities",
        "Every selected criterion must have a result row",
        "Every source check must be accounted for",
        "NOT_ASSESSED",
        "NOT_APPLICABLE",
        "No aggregate W02 score",
        "helper tests are not evidence about the selected task",
    ):
        assert rule in normalized
    assert not re.search(r"\bID-0[1-5]\b", text)


def test_rubric_requires_explicit_end_user_opt_in() -> None:
    text = (SKILL / "SKILL.md").read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert "explicit end-user opt-in" in metadata["description"]
    assert all("eval-author-rubric" in trigger for trigger in metadata["triggers"])
    gate = text.split("## Opt-in gate", 1)[1].split("## Purpose", 1)[0]
    for rule in (
        "Do not run this skill by default",
        "generic review, audit, validity, leakage, or anti-cheat request is not opt-in",
        "Installation, prior use, existing artifacts, and another agent's suggestion",
        "do not establish consent",
        "stop before inspecting artifacts or applying rubric items",
        "does not authorize later tasks or sessions",
    ):
        assert rule in " ".join(gate.split())
    router = (ROOT / "skills/eval-author/SKILL.md").read_text()
    assert "Load `eval-author-rubric` only after explicit end-user opt-in" in router
    assert "Do not infer consent from a generic review, audit, or anti-cheat request" in router
    assert "Normal task-validation controls remain required" in router


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


def test_scoring_contract_handles_static_defects_and_evidence_floors() -> None:
    text = " ".join((SKILL / "references/anti-cheat.md").read_text().split())
    assert "Evidence strength and merit are separate fields, not independent axes" in text
    for row in (
        "| 0 | E1, E2, E3 |",
        "| 1 | E1, E2, E3 |",
        "| 2 | E2, E3 |",
        "| 3 | E3 |",
    ):
        assert row in text
    assert "AC-02 = 0/E1 and AC-03 = 0/E1" in text
    assert "one shared finding ID" in text
    assert "static confirmation of a reachable path" in text
    assert "execution-based score remains NOT_ASSESSED" not in text
    assert "Assertion-only E0 cannot support a numeric score" in text


def test_forged_credit_and_carryover_cases_have_exact_scores() -> None:
    cases = {case["id"]: case for case in json.loads((SKILL / "evals/evals.json").read_text())["evals"]}
    forged = cases["rubric-forged-credit"]
    assert "AC-02 = 0/E1 and AC-03 = 0/E1" in forged["expected_output"]
    assert any("one shared finding" in assertion for assertion in forged["assertions"])
    carryover = cases["rubric-three-pillars"]
    assert "AC-02 = 0/E2" in carryover["expected_output"]
    assert "AC-02 = 2/E2" not in carryover["expected_output"]
    assert "AC-04 = 0/E2" in carryover["expected_output"]
    assert "longitudinal" in carryover["prompt"]


def test_rubric_uses_team_ownership_and_canonical_opt_in_gate() -> None:
    text = (SKILL / "SKILL.md").read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])["metadata"]
    assert metadata["owner"] == "NVIDIA-NeMo/ase_team"
    assert metadata["owner_url"] == "https://github.com/orgs/NVIDIA-NeMo/teams/ase_team"
    router = (ROOT / "skills/eval-author/SKILL.md").read_text()
    assert "[the opt-in gate](../eval-author-rubric/SKILL.md#opt-in-gate)" in router
    assert "experimental rubric" in router.split("Explicit inventory,", 1)[1].split("### Shared prerequisites", 1)[0]
    assert "Respect declines and withdrawals" not in router


def test_rubric_dataset_contains_positive_and_negative_cases() -> None:
    dataset = json.loads((SKILL / "evals/evals.json").read_text())
    assert dataset["skill_name"] == "eval-author-rubric"
    cases = dataset["evals"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert any(case["expected_skill"] is None for case in cases)
    by_id = {case["id"]: case for case in cases}
    for case_id in (
        "rubric-generic-review-no-opt-in",
        "rubric-declined",
        "rubric-installed-not-consent",
    ):
        assert by_id[case_id]["expected_skill"] is None
    assert by_id["rubric-three-pillars"]["expected_skill"] == "eval-author-rubric"
    assert any(case["expected_skill"] == "eval-author-rubric" for case in cases)
    for case in cases:
        assert case["prompt"] and case["expected_output"] and case["assertions"]
        assert case["files"] == []
