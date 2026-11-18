# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author-rubric/scripts/rubric_evidence_scoring.py"


def module():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(SCRIPT.parent))
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    return importlib.import_module("skills.eval-author-rubric.scripts.rubric_evidence_scoring")


@pytest.mark.parametrize(
    "reported_value,claim_limit,expected",
    [("model-v1", "none", 2), ("model-v2", "none", 1), ("model-v2", "uninterpretable", 0)],
)
def test_declared_configuration_misstatement_cannot_claim_acceptance(tmp_path, reported_value, claim_limit, expected):
    scorer = module()
    ident = "provenance.declared-configuration"
    facts = facts_for(ident, 2)
    facts["configuration_fields"][0].update(reported_value=reported_value, claim_limit=claim_limit)
    selection = packet(tmp_path, scorer, ident, facts)
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == expected


@pytest.mark.parametrize("synthetic", [True, False])
@pytest.mark.parametrize("facts", [{}, {"grading_routes": None}])
def test_unscored_evidence_has_no_strength(tmp_path, synthetic, facts):
    scorer = module()
    ident = "provenance.grading-path"
    selection = packet(tmp_path, scorer, ident, facts, synthetic=synthetic)
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["score"] is None
    assert row["status"] == "NOT_ASSESSED"
    assert row["evidence_strength"] is None


@pytest.mark.parametrize("target", ["facts", "packet"])
@pytest.mark.parametrize("alias_kind", ["path", "digest"])
def test_same_source_as_support_rejects_aliases(tmp_path, target, alias_kind):
    scorer = module()
    ident = "provenance.grading-path"
    selection = packet(tmp_path, scorer, ident, facts_for(ident, 2))
    document = json.loads((tmp_path / f"{target}.json").read_text())
    support = {
        "artifact_id": "support-alias",
        "pointer": "/subject",
        "digest": scorer.value_digest(document["subject"]),
    }
    rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"]["grading_routes"].update(support=[support]))
    original = next(r for r in selection["artifacts"] if r["id"] == target)
    if alias_kind == "path":
        selection["artifacts"].append({**original, "id": "support-alias"})
    else:
        document = json.loads((tmp_path / original["path"]).read_text())
        alias = add_artifact(tmp_path, selection, "support-alias", document)
        assert alias["digest"] == original["digest"]
        assert alias["path"] != original["path"]
    with pytest.raises(ValueError, match="fact assertion cannot support itself"):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize("spacing", ["", "\n", " \n\n"])
def test_anti_cheat_scoring_parser_tolerates_marker_whitespace(tmp_path, monkeypatch, spacing):
    import shutil

    scorer = module()
    baseline = scorer.canonical_definitions()
    refs = tmp_path / "references"
    shutil.copytree(scorer.REFERENCES, refs)
    path = refs / "anti-cheat.md"
    path.write_text(path.read_text().replace("**Scoring:** ", "**Scoring:**" + spacing))
    monkeypatch.setattr(scorer, "REFERENCES", refs)
    assert scorer.canonical_definitions() == baseline


def test_anti_cheat_checks_use_lowercase_unique_method_ids(tmp_path, monkeypatch):
    import shutil

    scorer = module()
    expected = {
        "AC-01": ["deterministic", "review", "probe"],
        "AC-02": ["review", "probe", "deterministic"],
        "AC-03": ["review", "deterministic", "probe"],
        "AC-04": ["review", "deterministic", "probe"],
    }
    definitions = scorer.canonical_definitions()
    for ident, methods in expected.items():
        assert [check["method"] for check in definitions[ident]["checks"]] == methods
        assert [check["id"] for check in definitions[ident]["checks"]] == [f"{ident}.{m}" for m in methods]
    refs = tmp_path / "references"
    shutil.copytree(scorer.REFERENCES, refs)
    path = refs / "anti-cheat.md"
    path.write_text(
        path.read_text().replace("- Deterministic:", "- Deterministic: first comparison.\n- Deterministic:", 1)
    )
    monkeypatch.setattr(scorer, "REFERENCES", refs)
    checks = scorer.canonical_definitions()["AC-01"]["checks"]
    assert [check["id"] for check in checks] == [
        "AC-01.deterministic-1",
        "AC-01.deterministic-2",
        "AC-01.review",
        "AC-01.probe",
    ]
    assert checks[0]["action"] == "first comparison."


def test_all_eighteen_definitions_preserve_canonical_anchors(tmp_path):
    scorer = module()
    definitions = scorer.canonical_definitions()
    assert len(definitions) == 18
    for name in ("artifact-configuration-provenance", "task-contract-authority"):
        source = json.loads((SCRIPT.parents[1] / f"references/{name}.json").read_text())
        for item in source["items"]:
            assert definitions[item["id"]] == item
    assert set(scorer.FACT_POLICIES) == set(definitions)
    assert set(scorer.ROBUSTNESS_POLICIES) == set(definitions)
    assert set(scorer.ROBUSTNESS_OUTCOMES) == set(definitions)
    rows = scorer.assess_evidence(tmp_path, {})
    assert set(rows) == set(definitions)
    assert all(row["status"] == "NOT_ASSESSED" and row["score"] is None for row in rows.values())
    assert all(row["check_results"] and row["remaining_gaps"] for row in rows.values())


# Frozen source metadata and ordered method suffixes from the reviewed ID map.
MIGRATED_DEFINITIONS = [
    (
        {"id": "DISC-01-A", "skill_id": "discover"},
        "provenance.task-artifact-access",
        "deterministic-1,deterministic-2,human-review",
    ),
    (
        {"id": "DISC-01-B", "skill_id": "discover"},
        "provenance.grading-path",
        "deterministic-1,deterministic-2,human-review-1,llm-judge,human-review-2",
    ),
    (
        {"id": "DISC-01-C", "skill_id": "discover"},
        "provenance.grading-dependency-access",
        "deterministic-1,deterministic-2,human-review-1,human-review-2",
    ),
    (
        {"id": "DISC-02-A", "skill_id": "discover"},
        "provenance.declared-configuration",
        "deterministic-1,deterministic-2,human-review-1,human-review-2",
    ),
    (
        {"id": "DISC-02-B", "skill_id": "discover"},
        "provenance.observed-configuration",
        "deterministic-1,deterministic-2,probe",
    ),
    ({"id": "DISC-03-A", "skill_id": "discover"}, "provenance.runtime-route", "deterministic-1,deterministic-2,probe"),
    ({"id": "AUD-02", "skill_id": "audit"}, "provenance.comparison-fairness", "deterministic,human-review,probe"),
    ({"id": "ENV-01", "skill_id": "environment"}, "provenance.solver-tool-access", "human-review,deterministic,probe"),
    (
        {"id": "ENV-02-A", "skill_id": "environment"},
        "provenance.dependency-substitution",
        "deterministic-1,human-review,deterministic-2",
    ),
    ({"id": "ENV-03", "skill_id": "environment"}, "provenance.external-state", "deterministic,human-review,probe"),
    (
        {"id": "CREATE-07", "skill_id": "first_eval_create"},
        "provenance.evidence-revision-consistency",
        "deterministic-1,deterministic-2,deterministic-3,deterministic-4",
    ),
    (
        {"id": "CREATE-03", "skill_id": "first_eval_create"},
        "task-contract.clear-contract",
        "llm-judge,human-review,probe",
    ),
    (
        {"id": "CREATE-04", "skill_id": "first_eval_create"},
        "task-contract.discoverable-requirements",
        "llm-judge,deterministic,probe",
    ),
    (
        {"id": "CREATE-05-A", "skill_id": "first_eval_create"},
        "task-contract.scoring-authority",
        "human-review,llm-judge,deterministic",
    ),
]


def test_exact_identifier_mapping_preserves_source_metadata(tmp_path):
    scorer = module()
    definitions = scorer.canonical_definitions()
    rows = scorer.assess_evidence(tmp_path, {})
    mapped_count = 0
    for source, ident, suffixes in MIGRATED_DEFINITIONS:
        definition = definitions[ident]
        assert definition["source"] == source
        assert rows[ident]["source"] == source
        assert [check["source_id"] for check in rows[ident]["check_results"]] == [
            check["source_id"] for check in definition["checks"]
        ]
        assert "skill_id" not in definition
        assert source["id"] not in definitions
        expected_checks = [ident + "." + suffix for suffix in suffixes.split(",")]
        assert [check["id"] for check in definition["checks"]] == expected_checks
        assert [check["source_id"] for check in definition["checks"]] == [
            source["id"] + f"-C{index:02}" for index in range(1, len(expected_checks) + 1)
        ]
        assert [check["check_id"] for check in rows[ident]["check_results"]] == expected_checks
        mapped_count += 1 + len(expected_checks)
    assert mapped_count == 61


def add_artifact(root, selection, ident, document, role="evidence"):
    import hashlib

    raw = json.dumps(document).encode()
    (root / f"{ident}.json").write_bytes(raw)
    record = {
        "id": ident,
        "path": f"{ident}.json",
        "digest": hashlib.sha256(raw).hexdigest(),
        "access": "available",
        "role": role,
        "revision": "fixture-v1",
        "source": "explicit-fixture",
        "selection_origin": "explicit fixture",
        "evidence": [{"path": f"{ident}.json", "digest": hashlib.sha256(raw).hexdigest()}],
    }
    selection["artifacts"].append(record)
    return record


def packet(root, scorer, item_id, facts, *, synthetic=True, kind="agent"):
    selection = {"artifacts": []}
    task = add_artifact(root, selection, "task", {"requirements": ["output exact JSON"]}, "task")
    subject = {"artifact_id": "task", "digest": task["digest"]}
    source = {
        "schema": "rubric-fact-source-v1",
        "subject": subject,
        "definitions_digest": scorer.definitions_digest(),
        "provenance": {
            "kind": "synthetic" if synthetic else kind,
            "actor_id": "fixture-author",
            "activity": "review",
            "limitations": ["Synthetic unit fixture; not native execution."]
            if synthetic
            else ["Recorded review; identity not authenticated."],
        },
        "facts": facts,
    }
    add_artifact(root, selection, "facts", source)
    selected_facts = {
        name: {
            "basis": "recorded_judgment",
            "source": {"artifact_id": "facts", "pointer": f"/facts/{name}", "digest": scorer.value_digest(value)},
            "support": [
                {
                    "artifact_id": "task",
                    "pointer": "/requirements",
                    "digest": scorer.value_digest(["output exact JSON"]),
                }
            ],
        }
        for name, value in facts.items()
    }
    envelope = {
        "schema": "rubric-criterion-evidence-v1",
        "subject": subject,
        "definitions_digest": scorer.definitions_digest(),
        "assessment_scope": "synthetic-scoring-demonstration" if synthetic else "recorded-evidence-assessment",
        "items": [{"item_id": item_id, "facts": selected_facts}],
    }
    add_artifact(root, selection, "packet", envelope)
    selection["criterion_evidence"] = {"artifact_id": "packet"}
    return selection


def measure_cli(root: Path, selection: dict) -> dict:
    import subprocess

    request = root / "selection.json"
    request.write_text(json.dumps(selection))
    result = subprocess.run(
        [sys.executable, str(SCRIPT.with_name("measure.py")), "--root", str(root), "--selection", str(request)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr
    return json.loads(result.stdout)


def assert_cli_score_boundary(report: dict, ident: str, synthetic: bool, expected: int | None) -> None:
    group = "criterion_demonstration" if synthetic else "recorded_criterion_evidence"
    row = report[group][ident]
    assert row["score"] == (expected if synthetic else None)
    assert row["evidence_strength"] == ("E1" if synthetic and expected is not None else None)
    assert row["native_execution_verified"] is False
    assert row["reviewer_authenticated"] is False
    assert row["score_provenance"]["kind"] == ("synthetic-anchor-application" if synthetic else "recorded-review-only")
    assert ident not in report["items"] or report["items"][ident]["score"] is None
    if not synthetic:
        assert "criterion_demonstration" not in report
        assert row["status"] == "NOT_ASSESSED"
    assert report["preflight_success"] is False


@pytest.mark.parametrize("synthetic", [True, False])
@pytest.mark.parametrize("ident", [row[1] for row in MIGRATED_DEFINITIONS] + ["AC-01", "AC-02", "AC-03", "AC-04"])
def test_measure_cli_separates_demonstration_from_task_scores(tmp_path, synthetic, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, facts_for(ident, 0), synthetic=synthetic)
    report = measure_cli(tmp_path, selection)
    if synthetic:
        row = report["criterion_demonstration"][ident]
        assert row["score"] == 0
        assert ident not in report["items"] or report["items"][ident]["score"] is None
    else:
        assert "criterion_demonstration" not in report
        row = report["recorded_criterion_evidence"][ident]
        assert report["items"][ident]["score"] is None
        assert row["evidence_strength"] is None
    definition = scorer.canonical_definitions()[ident]
    if "source" in definition:
        assert row["source"] == definition["source"]
        assert "skill_id" not in row
        assert [check["source_id"] for check in row["check_results"]] == [
            check["source_id"] for check in definition["checks"]
        ]
    assert [check["check_id"] for check in row["check_results"]] == [check["id"] for check in definition["checks"]]
    assert report["preflight_success"] is False


@pytest.mark.parametrize("target", ["packet", "task", "facts"])
@pytest.mark.parametrize("access", ["denied", "missing", "unresolved"])
def test_selection_wide_alias_restrictions_precede_reads(tmp_path, monkeypatch, target, access):
    scorer = module()
    selection = packet(
        tmp_path, scorer, "provenance.task-artifact-access", facts_for("provenance.task-artifact-access", 2)
    )
    record = next(r for r in selection["artifacts"] if r["id"] == target)
    selection["artifacts"].append({**record, "id": "restricted-alias", "access": access})
    original = scorer.read_selected
    reads = []

    def tracked_read(root, path):
        reads.append(path)
        return original(root, path)

    monkeypatch.setattr(scorer, "read_selected", tracked_read)
    with pytest.raises(ValueError, match="unavailable"):
        scorer.assess_evidence(tmp_path, selection)
    assert record["path"] not in reads


def test_source_bound_access_rows_have_explicit_demo_scope(tmp_path):
    scorer = module()
    selection = packet(
        tmp_path,
        scorer,
        "provenance.task-artifact-access",
        {
            "task_artifacts": [
                {"id": "instructions", "access": "accessible", "revision": "v1", "inspection_effect": "none"},
                {"id": "fixture", "access": "missing", "revision": None, "inspection_effect": "blocks"},
            ]
        },
    )
    row = scorer.assess_evidence(tmp_path, selection)["provenance.task-artifact-access"]
    assert row["score"] == 0
    assert row["assessment_scope"] == "synthetic-scoring-demonstration"
    assert row["evidence_strength"] == "E1"
    assert row["score_provenance"]["kind"] == "synthetic-anchor-application"
    assert row["measurements"]["task_artifacts"]["denominator"] == 2
    assert row["fact_results"][0]["basis"] == "recorded_judgment"
    assert row["reviewer_authenticated"] is False
    assert row["native_execution_verified"] is False
    assert all(
        r["score"] is None
        for k, r in scorer.assess_evidence(tmp_path, selection).items()
        if k != "provenance.task-artifact-access"
    )


# Literal source-row vectors share the demo fixture, not production policy.
sys.path.insert(0, str(ROOT))
from tools.run_criterion_scoring_demo import VECTORS, facts_for, robustness_for  # noqa: E402


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("anchor", [0, 1, 2])
def test_all_criterion_specific_low_partial_acceptance_relations(tmp_path, ident, anchor):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, facts_for(ident, anchor))
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["score"] == anchor
    assert row["assessment_scope"] == "synthetic-scoring-demonstration"
    assert row["evidence_strength"] == "E1"


@pytest.mark.parametrize("ident", VECTORS)
def test_missing_inventory_is_unknown_not_a_defect(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {VECTORS[ident][0]: None})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] is None


@pytest.mark.parametrize("ident", VECTORS)
def test_additional_scoped_controls_derive_three_without_inventing_e3(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robustness_for(ident)})
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["score"] == 3
    assert row["evidence_strength"] == "E1"
    assert row["native_execution_verified"] is False
    assert row["reviewer_authenticated"] is False


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("outcome", ["arbitrary-equal-text", "forged receipt credited"])
def test_robustness_rejects_untyped_equal_assertions(tmp_path, ident, outcome):
    scorer = module()
    robust = robustness_for(ident)
    for control in next(iter(robust.values())):
        control.update(expected=outcome, observed=outcome)
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    with pytest.raises(ValueError, match="robustness outcome"):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize(
    "ident,role,outcome",
    [
        ("AC-01", "independent_path", "protected_read_allowed"),
        ("AC-02", "heldout_attack", "forbidden_credit_awarded"),
        ("AC-03", "invalid_rejection", "invalid_receipt_credited"),
        ("AC-04", "order", "order_carryover_observed"),
    ],
)
def test_completed_scoped_corroboration_defect_overrides_acceptance(tmp_path, ident, role, outcome):
    scorer = module()
    robust = robustness_for(ident)
    control = next(r for r in next(iter(robust.values())) if r["role"] == role)
    control["observed"] = outcome
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 0


@pytest.mark.parametrize("ident", VECTORS)
def test_failed_corroboration_cannot_preserve_full_acceptance(tmp_path, ident):
    scorer = module()
    for role, (_, failure) in scorer.ROBUSTNESS_OUTCOMES[ident].items():
        robust = robustness_for(ident)
        control = next(r for r in next(iter(robust.values())) if r["role"] == role)
        control["observed"] = failure
        selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
        assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] in (0, 1)


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("state", ["unrun", "error", "other-revision", "missing", "failed-expectation"])
def test_incomplete_or_misdeclared_corroboration_never_qualifies(tmp_path, ident, state):
    scorer = module()
    robust = robustness_for(ident)
    control = next(iter(robust.values()))[0]
    _, failure = scorer.ROBUSTNESS_OUTCOMES[ident][control["role"]]
    control["observed"] = failure
    if state in ("unrun", "error"):
        control["execution"] = state
    elif state == "other-revision":
        control["scope"] = state
    elif state == "missing":
        control["observed"] = None
    else:
        control["expected"] = failure
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    if state == "failed-expectation":
        with pytest.raises(ValueError, match="robustness outcome"):
            scorer.assess_evidence(tmp_path, selection)
    else:
        assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 2


@pytest.mark.parametrize("ident", VECTORS)
def test_robust_controls_never_erase_a_demonstrated_defect(tmp_path, ident):
    scorer = module()
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 0), **robustness_for(ident)})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == 0


@pytest.mark.parametrize("ident", VECTORS)
def test_same_actor_is_not_independent_even_in_demo(tmp_path, ident):
    scorer = module()
    robust = robustness_for(ident)
    for row in next(iter(robust.values())):
        row["corroborating_actor"] = row["original_actor"]
    selection = packet(tmp_path, scorer, ident, {**facts_for(ident, 2), **robust})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] == (
        3 if ident in ("provenance.comparison-fairness", "provenance.evidence-revision-consistency") else 2
    )


def rewrite(root, selection, ident, mutate):
    import hashlib

    target = next(r for r in selection["artifacts"] if r["id"] == ident)
    doc = json.loads((root / target["path"]).read_text())
    mutate(doc)
    raw = json.dumps(doc).encode()
    (root / target["path"]).write_bytes(raw)
    target["digest"] = hashlib.sha256(raw).hexdigest()
    target["evidence"][0]["digest"] = target["digest"]


@pytest.mark.parametrize(
    "mutation",
    [
        "packet_score",
        "unknown_fact",
        "duplicate_item",
        "unknown_item",
        "stale_definitions",
        "wrong_subject",
        "missing_source",
        "bad_pointer",
        "bad_content_digest",
        "empty_support",
        "missing_support",
        "human_true",
        "unknown_author",
        "missing_limits",
        "promote_demo",
        "review_as_observation",
        "same_source_as_support",
        "wrong_fact_pointer",
        "contradiction",
    ],
)
def test_invalid_or_unbound_evidence_is_rejected(tmp_path, mutation):
    scorer = module()
    selection = packet(tmp_path, scorer, "provenance.grading-path", facts_for("provenance.grading-path", 2))
    field = "grading_routes"
    if mutation == "packet_score":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0].update(score=3))
    elif mutation == "unknown_fact":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"].update(satisfies_anchor=True))
    elif mutation == "duplicate_item":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"].append(deepcopy(d["items"][0])))
    elif mutation == "unknown_item":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0].update(item_id="AC-05"))
    elif mutation == "stale_definitions":
        rewrite(tmp_path, selection, "packet", lambda d: d.update(definitions_digest="0" * 64))
    elif mutation == "wrong_subject":
        rewrite(tmp_path, selection, "facts", lambda d: d["subject"].update(digest="0" * 64))
    elif mutation == "missing_source":
        (tmp_path / "facts.json").unlink()
    elif mutation == "bad_pointer":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field]["source"].update(pointer="/not_there"),
        )
    elif mutation == "bad_content_digest":
        rewrite(
            tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field]["source"].update(digest="0" * 64)
        )
    elif mutation == "empty_support":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field].update(support=[]))
    elif mutation == "missing_support":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field]["support"][0].update(artifact_id="absent"),
        )
    elif mutation == "human_true":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(human=True))
    elif mutation == "unknown_author":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(kind="authenticated-human"))
    elif mutation == "missing_limits":
        rewrite(tmp_path, selection, "facts", lambda d: d["provenance"].update(limitations=[]))
    elif mutation == "promote_demo":
        rewrite(tmp_path, selection, "packet", lambda d: d.update(assessment_scope="recorded-evidence-assessment"))
    elif mutation == "review_as_observation":
        rewrite(tmp_path, selection, "packet", lambda d: d["items"][0]["facts"][field].update(basis="json_observation"))
    elif mutation == "same_source_as_support":
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field].update(support=[deepcopy(d["items"][0]["facts"][field]["source"])]),
        )
    elif mutation == "wrong_fact_pointer":
        selection = packet(tmp_path, scorer, "provenance.grading-path", {field: None, "route_reconstruction": None})
        rewrite(
            tmp_path,
            selection,
            "packet",
            lambda d: d["items"][0]["facts"][field]["source"].update(pointer="/facts/route_reconstruction"),
        )
    elif mutation == "contradiction":
        bad = facts_for("provenance.grading-path", 2)
        bad[field].append({**bad[field][0], "selected_scorer": "different"})
        selection = packet(tmp_path, scorer, "provenance.grading-path", bad)
    match = "does not name the selected fact" if mutation == "wrong_fact_pointer" else None
    with pytest.raises(ValueError, match=match):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize("ident", VECTORS)
@pytest.mark.parametrize("kind", ["agent", "human", "tool"])
def test_recorded_authorship_does_not_authenticate_or_grant_real_scores(tmp_path, ident, kind):
    scorer = module()
    selection = packet(
        tmp_path, scorer, ident, {**facts_for(ident, 2), **robustness_for(ident)}, synthetic=False, kind=kind
    )
    row = scorer.assess_evidence(tmp_path, selection)[ident]
    assert row["status"] == "NOT_ASSESSED"
    assert row["score"] is None
    assert row["evidence_strength"] is None
    assert row["reviewer_authenticated"] is False
    assert row["native_execution_verified"] is False


@pytest.mark.parametrize("mutation", ["symlink", "parent_symlink", "oversize", "digest", "duplicate_json", "nan"])
def test_source_reader_fails_closed(tmp_path, mutation):
    scorer = module()
    selection = packet(
        tmp_path, scorer, "provenance.task-artifact-access", facts_for("provenance.task-artifact-access", 2)
    )
    source = tmp_path / "facts.json"
    record = next(r for r in selection["artifacts"] if r["id"] == "facts")
    if mutation == "symlink":
        source.rename(tmp_path / "real.json")
        source.symlink_to(tmp_path / "real.json")
    elif mutation == "parent_symlink":
        (tmp_path / "real").mkdir()
        source.rename(tmp_path / "real/facts.json")
        (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
        record["path"] = "linked/facts.json"
    else:
        raw = {"oversize": b" " * 1_048_577, "digest": b"{}", "duplicate_json": b'{"a":1,"a":2}', "nan": b'{"a":NaN}'}[
            mutation
        ]
        source.write_bytes(raw)
        if mutation != "digest":
            import hashlib

            record["digest"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        scorer.assess_evidence(tmp_path, selection)


@pytest.mark.parametrize("synthetic", [True, False])
@pytest.mark.parametrize("with_neighbor", [False, True])
@pytest.mark.parametrize("run_identity", [None, "mismatching-run"])
def test_measure_cli_observed_mismatch_requires_its_own_run_identity(
    tmp_path: Path, synthetic: bool, with_neighbor: bool, run_identity: str | None
) -> None:
    scorer = module()
    ident = "provenance.observed-configuration"
    facts = facts_for(ident, 2)
    rows = facts["run_settings"]
    if with_neighbor:
        rows.append({**rows[0], "id": "identified-agreeing-neighbor"})
    rows[0].update(observed="m2", disposition="invalidates", run_identity=run_identity)
    selection = packet(tmp_path, scorer, ident, facts, synthetic=synthetic)
    report = measure_cli(tmp_path, selection)
    expected = 0 if run_identity else (1 if with_neighbor else None)
    assert_cli_score_boundary(report, ident, synthetic, expected)


@pytest.mark.parametrize("synthetic", [True, False])
@pytest.mark.parametrize(
    "native_validation,health,reach,consumer,expected",
    [
        ("invalid", "healthy", "reached", "credited", 0),
        ("unrun", "healthy", "reached", "credited", 1),
        ("valid", "healthy", "reached", "credited", 2),
        ("invalid", "error", "reached", "credited", 1),
        ("invalid", "unrun", "reached", "credited", 1),
        ("invalid", "healthy", "unreachable", "credited", 1),
        ("invalid", "healthy", "reached", "rejected", 1),
    ],
)
def test_measure_cli_current_receipt_invalid_credit_requires_healthy_reached_validation(
    tmp_path: Path,
    synthetic: bool,
    native_validation: str,
    health: str,
    reach: str,
    consumer: str,
    expected: int,
) -> None:
    scorer = module()
    facts = facts_for("AC-03", 2)
    current = next(row for row in facts["receipt_controls"] if row["receipt_kind"] == "current")
    current.update(native_validation=native_validation, health=health, reach=reach, consumer=consumer)
    selection = packet(tmp_path, scorer, "AC-03", facts, synthetic=synthetic)
    report = measure_cli(tmp_path, selection)
    assert_cli_score_boundary(report, "AC-03", synthetic, expected)


@pytest.mark.parametrize("with_neighbor", [False, True])
def test_justified_setting_without_observation_stays_unknown(tmp_path, with_neighbor):
    scorer = module()
    facts = facts_for("provenance.observed-configuration", 2)
    rows = facts["run_settings"]
    if with_neighbor:
        rows.append({**rows[0], "id": "observed-neighbor"})
    rows[0].update(observed=None, disposition="justified")
    selection = packet(tmp_path, scorer, "provenance.observed-configuration", facts)
    assert scorer.assess_evidence(tmp_path, selection)["provenance.observed-configuration"]["score"] == (
        1 if with_neighbor else None
    )


@pytest.mark.parametrize("with_neighbor", [False, True])
def test_completed_probe_without_actual_outcome_stays_unknown(tmp_path, with_neighbor):
    scorer = module()
    facts = facts_for("provenance.solver-tool-access", 2)
    rows = facts["tool_probes"]
    if with_neighbor:
        rows.append({**rows[0], "id": "observed-neighbor"})
    rows[0]["actual"] = None
    selection = packet(tmp_path, scorer, "provenance.solver-tool-access", facts)
    assert scorer.assess_evidence(tmp_path, selection)["provenance.solver-tool-access"]["score"] == (
        1 if with_neighbor else None
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"reset_state": None, "inspection": "unrun", "consequence": "answer_exposure"},
        {"reset_state": None, "inspection": "complete", "consequence": "answer_exposure"},
        {"reset_state": "answer", "inspection": "unrun", "consequence": "answer_exposure"},
        {"target_alone": None, "target_after": "1", "inspection": "unrun", "consequence": "credit_change"},
        {"target_alone": "0", "target_after": None, "inspection": "complete", "consequence": "credit_change"},
    ],
)
def test_reset_defect_requires_observed_state_or_paired_credit(tmp_path, patch):
    scorer = module()
    facts = facts_for("AC-04", 2)
    facts["reset_surfaces"][0].update(patch)
    selection = packet(tmp_path, scorer, "AC-04", facts)
    assert scorer.assess_evidence(tmp_path, selection)["AC-04"]["score"] is None


@pytest.mark.parametrize("semantics", ["resolved", "disputed", "contradicted"])
def test_authority_mismatch_is_derived_not_overridden_by_semantics(tmp_path, semantics):
    scorer = module()
    facts = facts_for("task-contract.scoring-authority", 2)
    facts["scoring_authorities"][0].update(authoritative="43", semantics=semantics)
    selection = packet(tmp_path, scorer, "task-contract.scoring-authority", facts)
    assert scorer.assess_evidence(tmp_path, selection)["task-contract.scoring-authority"]["score"] == 0


def test_private_hidden_authority_is_a_defect_even_when_identified(tmp_path):
    scorer = module()
    facts = facts_for("task-contract.discoverable-requirements", 2)
    facts["scored_conditions"][0].update(authority="private-grader-rule", solver_access="denied", mapping="hidden")
    selection = packet(tmp_path, scorer, "task-contract.discoverable-requirements", facts)
    assert scorer.assess_evidence(tmp_path, selection)["task-contract.discoverable-requirements"]["score"] == 0


@pytest.mark.parametrize(
    "ident", ["provenance.observed-configuration", "provenance.external-state", "task-contract.scoring-authority"]
)
def test_partial_requires_some_established_scope_not_just_an_asserted_gap(tmp_path, ident):
    scorer = module()
    name, base, _, partial = VECTORS[ident]
    selection = packet(tmp_path, scorer, ident, {name: [{**base, **partial}]})
    assert scorer.assess_evidence(tmp_path, selection)[ident]["score"] is None


def test_fact_read_is_revalidated_after_projection(tmp_path, monkeypatch):
    scorer = module()
    selection = packet(
        tmp_path, scorer, "provenance.task-artifact-access", facts_for("provenance.task-artifact-access", 2)
    )
    original = scorer._derive

    def change_after_read(ident, facts):
        result = original(ident, facts)
        (tmp_path / "facts.json").write_text("{}")
        return result

    monkeypatch.setattr(scorer, "_derive", change_after_read)
    with pytest.raises(ValueError, match="changed during assessment"):
        scorer.assess_evidence(tmp_path, selection)


def test_definition_binding_includes_supplemental_evidence_floors(tmp_path, monkeypatch):
    scorer = module()
    import shutil

    refs = tmp_path / "references"
    shutil.copytree(scorer.REFERENCES, refs)
    before = scorer.definitions_digest()
    monkeypatch.setattr(scorer, "REFERENCES", refs)
    assert scorer.definitions_digest() == before
    path = refs / "task-contract-authority.md"
    path.write_text(path.read_text() + "\nChanged adjudication policy.\n")
    assert scorer.definitions_digest() != before


@pytest.mark.parametrize("bad", [True, 1, "human=true", ["not-a-row"], {"score": 3}])
def test_fact_inventory_rejects_booleans_scalars_and_verdict_objects(tmp_path, bad):
    scorer = module()
    selection = packet(tmp_path, scorer, "task-contract.clear-contract", {"contract_requirements": bad})
    with pytest.raises(ValueError):
        scorer.assess_evidence(tmp_path, selection)


def test_zero_and_partial_merit_do_not_change_evidence_strength(tmp_path):
    scorer = module()
    strengths = []
    for score in (0, 1, 2, 3):
        facts = facts_for("task-contract.clear-contract", min(score, 2))
        if score == 3:
            facts.update(robustness_for("task-contract.clear-contract"))
        selection = packet(tmp_path, scorer, "task-contract.clear-contract", facts)
        result = scorer.assess_evidence(tmp_path, selection)["task-contract.clear-contract"]
        strengths.append(result["evidence_strength"])
    assert strengths == ["E1"] * 4


@pytest.mark.parametrize(
    "ident,patch",
    [
        ("provenance.observed-configuration", {"observed": "different", "disposition": "agrees"}),
        ("provenance.evidence-revision-consistency", {"current_revision": "other", "change": "unchanged"}),
        ("AC-01", {"solver_read": "denied", "exposure": "unintended"}),
        (
            "provenance.dependency-substitution",
            {"form": "real", "authorization": "approved", "approved_revision": "v1"},
        ),
    ],
)
def test_internally_contradictory_observations_are_rejected(tmp_path, ident, patch):
    scorer = module()
    facts = facts_for(ident, 2)
    next(iter(facts.values()))[0].update(patch)
    selection = packet(tmp_path, scorer, ident, facts)
    with pytest.raises(ValueError, match="contradictory"):
        scorer.assess_evidence(tmp_path, selection)
