# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/eval-author-rubric"
PLANNING_TICKET = re.compile(r"(?<![a-z0-9])" + "w" + r"(?:0[1-9]|1[01])(?![a-z0-9])", re.IGNORECASE)


def test_authored_sources_use_descriptive_names_not_planning_tickets() -> None:
    extensions = {".py", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".txt", ".html", ".sh"}
    paths = [*ROOT.glob("*.md")]
    for directory in ("skills", "tools", "evals", "docs", "tests"):
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file() and path.suffix in extensions)
    violations = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix()
        if PLANNING_TICKET.search(relative):
            violations.append(relative)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PLANNING_TICKET.search(line):
                violations.append(f"{relative}:{number}")
    assert not violations, "Planning-ticket identifiers remain:\n" + "\n".join(violations)


def test_rubric_scope_and_initial_slice() -> None:
    text = (SKILL / "SKILL.md").read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["maturity"] == "alpha"
    assert metadata["user-invocable"] is True
    assert "validity, coverage, and diversity" in text
    assert "anti-cheat within validity" in text
    assert "Do not write an Audit" in text
    assert "A review request alone does not authorize executing repository code" in text
    assert "Default to the response only" in text


def test_rubric_routes_only_the_explicitly_selected_assessment_area() -> None:
    text = " ".join((SKILL / "SKILL.md").read_text().split())
    for rule in (
        "If the named-rubric request leaves the area ambiguous, clarify the scope once before inspecting artifacts",
        "Apply every selected criterion",
        "Account for every selected task and every selected criterion, including unknowns",
        "Do not apply unselected areas",
        "Use only controls needed by selected criteria",
    ):
        assert rule in text
    purpose = text.split("## Purpose", 1)[1].split("## Instructions", 1)[0]
    assert re.search(r"Anti-cheat-only opt-in does not select artifact, configuration, and provenance", purpose)
    assert re.search(r"Artifact, configuration, and provenance opt-in does not select anti-cheat", purpose)
    assert not PLANNING_TICKET.search(purpose)
    assert "references/anti-cheat.md" in text
    assert "references/artifact-configuration-provenance.md" in text
    assert "references/identity.md" in text


CANONICAL_AREAS = {
    "artifact-configuration-provenance": (
        [
            "provenance.task-artifact-access",
            "provenance.grading-path",
            "provenance.grading-dependency-access",
            "provenance.declared-configuration",
            "provenance.observed-configuration",
            "provenance.runtime-route",
            "provenance.comparison-fairness",
            "provenance.solver-tool-access",
            "provenance.dependency-substitution",
            "provenance.external-state",
            "provenance.evidence-revision-consistency",
        ],
        38,
        {"rubric_version", "policies", "result_template", "items"},
    ),
    "task-contract-authority": (
        ["task-contract.clear-contract", "task-contract.discoverable-requirements", "task-contract.scoring-authority"],
        9,
        {"rubric_version", "items"},
    ),
}
METHOD_SLUGS = {
    "deterministic": "deterministic",
    "human_review": "human-review",
    "llm_as_judge": "llm-judge",
    "probe": "probe",
}


@pytest.mark.parametrize("area", CANONICAL_AREAS)
def test_rubric_canonical_source_preserves_criterion_checks_and_anchors(area: str) -> None:
    ids, check_count, keys = CANONICAL_AREAS[area]
    data = json.loads((SKILL / f"references/{area}.json").read_text())
    assert set(data) == keys, "Rubric-wide policies and result_template have one source"
    items = data["items"]
    assert [item["id"] for item in items] == ids
    checks = [check for item in items for check in item["checks"]]
    assert len(checks) == len({check["id"] for check in checks}) == check_count
    assert len({check["source_id"] for check in checks}) == check_count
    assert len({item["source"]["id"] for item in items}) == len(items)
    for item in items:
        assert set(item["scoring"]) == {"0", "1", "2", "3"}
        for check in item["checks"]:
            prefix = re.escape(f"{item['id']}.{METHOD_SLUGS[check['method']]}")
            assert re.fullmatch(prefix + r"(-\d+)?", check["id"]), "Check IDs name their item and method"
        assert {check["method"] for check in item["checks"]} == set(item["methods"])


@pytest.mark.parametrize("area", CANONICAL_AREAS)
def test_rubric_reference_links_each_criterion_without_copying_anchors(area: str) -> None:
    text = (SKILL / f"references/{area}.md").read_text()
    items = json.loads((SKILL / f"references/{area}.json").read_text())["items"]
    sections = dict(re.findall(r"## (\S+) — ([\s\S]*?)(?=\n## |\Z)", text))
    assert set(sections) == {item["id"] for item in items}
    assert "#/items/" not in text, "Link entries by ID, not position"
    for item in items:
        section = sections[item["id"]]
        assert section.startswith(item["title"] + "\n")
        assert f"the `{item['id']}` entry in" in " ".join(section.split())
        assert "**Methods and evidence:**" in section and "**Assessment limit:**" in section
        for anchor in item["scoring"].values():
            assert anchor not in text, "Keep full score anchors in canonical JSON only"


def test_provenance_reference_states_rubric_wide_assessment_rules() -> None:
    text = (SKILL / "references/artifact-configuration-provenance.md").read_text()
    normalized = " ".join(text.split())
    for rule in (
        "E0 alone supports no numeric score",
        "Do not import anti-cheat's evidence-floor table",
        "`provenance.evidence-revision-consistency` and `provenance.comparison-fairness` can meet their level-3 anchors at E2",
        "Every selected criterion must have a result row",
        "Every source check must be accounted for",
        "NOT_ASSESSED",
        "NOT_APPLICABLE",
        "No aggregate score",
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
