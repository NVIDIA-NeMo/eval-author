# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Focused regressions for the portable, explicitly fictional assessment."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools/rubric_task_scenario"


def load(path: Path) -> types.ModuleType:
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    output = tmp_path_factory.mktemp("scenario").resolve() / "run"
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(PACKAGE / "run.py"), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=180,
        env={"PATH": os.defpath},
    )
    assert proc.returncode == 0, proc.stderr + (output / "execution.log").read_text()
    assessor = load(output / "source/assess.py")
    evidence = assessor.gather(output / "source/execution")
    premises = assessor.read(output / "source/scenario-premises.json")
    return assessor, evidence, premises, output


def assess(scenario, evidence=None, premises=None, binding_valid=True):
    a, original_e, original_p, _ = scenario
    e = copy.deepcopy(original_e if evidence is None else evidence)
    p = a._dispositions.bind_templates(original_p if premises is None else premises, e, a.source_manifest())
    return a.assess(e, p, binding_valid=binding_valid)["items"]


def test_actual_style_representations_consumed(scenario):
    _, evidence, _, output = scenario
    pairs = [p for p in evidence["cues"] if p["class"] == "style"]
    assert {p["family"] for p in pairs} == {p["family"] for p in evidence["cues"]}
    observations = {o["id"]: o for o in evidence["observations"]}
    for pair in pairs:
        left, right = (pair["arms"][arm] for arm in ("baseline", "intervention"))
        assert left["view"] == right["view"]
        for field in ("policy_input_wire", "candidate_wire"):
            assert left[field] != right[field]
            assert json.loads(left[field]) == json.loads(right[field])
        assert left["case_sha256"] == right["case_sha256"]
        assert left["candidate_sha256"] == right["candidate_sha256"]
        # The file scorer binds actual candidate bytes, which intentionally differ.
        assert {k: v for k, v in left["result"].items() if k != "candidate_sha256"} == {
            k: v for k, v in right["result"].items() if k != "candidate_sha256"
        }
        for arm in (left, right):
            row = observations[arm["observation"]]
            assert row["candidate_wire"] == arm["candidate_wire"]
            assert json.loads(row["candidate_wire"]) == row["candidate"]
            if pair["family"] != "refund":
                assert (output / "source/execution/choice-controls" / row["id"] / "candidate.json").read_text() == row[
                    "candidate_wire"
                ]
    assert assess(scenario)["CREATE-02"]["check_results"][2]["outcome"] == "PASS"


@pytest.mark.parametrize("fault", ["metadata-confound", "unconsumed-style", "changed-semantics"])
def test_style_counterexamples(scenario, fault):
    evidence = copy.deepcopy(scenario[1])
    pair = next(p for p in evidence["cues"] if p["class"] == "style")
    left, right = pair["arms"]["baseline"], pair["arms"]["intervention"]
    if fault == "metadata-confound":
        right["view"]["filename"] = "answer-A.json"
        right["policy_input_wire"] = json.dumps(right["view"], indent=2)
    elif fault == "unconsumed-style":
        right["candidate_wire"] = left["candidate_wire"]
        right["policy_input_wire"] = left["policy_input_wire"]
    else:
        right["candidate_wire"] = '{"success":false,"events":[]}'
    row = assess(scenario, evidence)["CREATE-02"]
    assert row["score"] != 3
    assert row["check_results"][2]["outcome"] == "FAIL"


@pytest.mark.parametrize("family", ["refund", "interval-reservation", "dependency-release"])
@pytest.mark.parametrize("cue_class", ["cue-only", "nuisance", "misleading", "style"])
@pytest.mark.parametrize("arm_name", ["baseline", "intervention"])
@pytest.mark.parametrize("location", ["observation", "pair"])
def test_cue_full_result_matches_owning_observation(scenario, family, cue_class, arm_name, location):
    evidence = copy.deepcopy(scenario[1])
    pair = next(p for p in evidence["cues"] if p["family"] == family and p["class"] == cue_class)
    arm = pair["arms"][arm_name]
    observation = next(o for o in evidence["observations"] if o["id"] == arm["observation"])
    original = copy.deepcopy(arm["result"])
    assert original == observation["result"]
    check = "amount" if family == "refund" else "current_evidence_correct"
    changed = observation["result"] if location == "observation" else arm["result"]
    changed["checks"][check] = 1 - changed["checks"][check]
    # Only a check contradicts its other copy: acceptance, credit and wires stay intact.
    assert {k: v for k, v in changed.items() if k != "checks"} == {k: v for k, v in original.items() if k != "checks"}
    assert (arm["result"] if location == "observation" else observation["result"]) == original
    rows = assess(scenario, evidence)
    # Task rows read only the refund family; a companion-family contradiction moves only suite rows.
    task_rows = (("CREATE-02", (1, 2, 3)), ("provenance.comparison-fairness", (2,)))
    if family != "refund":
        assert all(rows[ident]["score"] == 3 for ident, _ in task_rows)
        task_rows = (("CREATE-02-SUITE", (2,)),)
    for ident, indexes in (*task_rows, ("AUDIT-05-S03", (2,))):
        row = rows[ident]
        assert row["score"] is None, (family, cue_class, arm_name, location, ident)
        assert row["assessment_state"] == "NOT_ASSESSED"
        for index in indexes:
            result = row["check_results"][index]
            assert result["actual_measurement_predicate"] is False
            assert result["premise_present"] is True


@pytest.mark.parametrize("fault", ["result", "state"])
def test_parallel_target_matches_standalone(scenario, fault):
    evidence = copy.deepcopy(scenario[1])
    target = next(r for r in evidence["isolation"]["overlap"] if r["id"] == "T")
    if fault == "result":
        target["result"]["accepted"] = False
        target["result"]["total_credit"] = 0
        target["result"]["checks"]["amount"] = 0
    else:
        target["state_sha256"] = "0" * 64
    row = assess(scenario, evidence)["ENV-03-S01"]
    assert row["score"] != 3
    assert row["check_results"][2]["actual_measurement_predicate"] is False


def test_release_use_is_bound(scenario):
    evidence = copy.deepcopy(scenario[1])
    evidence["trace"]["use"] = "unapproved-public-training"
    row = assess(scenario, evidence)["TRACE-04-A"]
    assert row["score"] != 3
    assert row["check_results"][2]["actual_measurement_predicate"] is False


def test_stale_packet_invalidates_all_rows(scenario):
    rows = assess(scenario, binding_valid=False)
    assert all(r["score"] is None and r["assessment_state"] == "NOT_ASSESSED" for r in rows.values())
    assert all(r["remaining_gaps"] and not r["top_anchor_predicate"]["satisfied"] for r in rows.values())


@pytest.mark.parametrize("fault", ["missing", "string", "rejected", "contradiction", "condition", "scope"])
@pytest.mark.parametrize("target", ["review", "alternative"])
def test_disposition_contents_are_required(scenario, target, fault):
    premises = copy.deepcopy(scenario[2])
    parent, key = (
        (premises["reviews"], "discovery") if target == "review" else (premises["method_qualification"], "alternative")
    )
    if fault == "missing":
        del parent[key]
    elif fault == "string":
        parent[key] = "REJECTED: material contradiction; required review not performed."
    elif fault == "rejected":
        parent[key]["disposition"] = "REJECTED"
    elif fault == "contradiction":
        parent[key]["contradictions"] = ["Unresolved material contradiction"]
    elif fault == "condition":
        parent[key]["conditions"]["no_material_contradiction"] = False
    else:
        parent[key]["scope"]["provider"] = "Harbor"
    row = assess(scenario, premises=premises)["task-contract.discoverable-requirements"]
    assert row["score"] is None
    assert row["assessment_state"] == "NOT_ASSESSED"
    assert row["remaining_gaps"]


def test_disposition_binding_not_presence(scenario):
    a, e, p, _ = scenario
    bound = a._dispositions.bind_templates(p, e, a.source_manifest())
    bound["reviews"]["discovery"]["binding"]["evidence_sha256"] = "0" * 64
    assert a.assess(e, bound)["items"]["task-contract.discoverable-requirements"]["score"] != 3


def test_complete_output_has_canonical_inventory_and_no_empirical_claim(scenario):
    a, _, _, output = scenario
    report = a.read(output / "source/execution/assessment.json")["report"]
    definitions, _ = a.definitions()
    assert set(report["items"]) == set(definitions)
    assert report["empirical_qualification"] is False
    task = {k: r for k, r in report["items"].items() if r["level"] == "task"}
    suite = {k: r for k, r in report["items"].items() if r["level"] == "suite"}
    # The refund task is scored on its own; suite rows use the companion families and selection cohort.
    assert len(task) == 19 and sum(r["score"] == 3 for r in task.values()) == 18
    assert len(suite) == 4 and sum(r["score"] == 3 for r in suite.values()) == 3
    assert [k for k, r in report["items"].items() if r["assessment_state"] == "NOT_APPLICABLE"] == [
        "AUDIT-04-B",
        "AUDIT-04-B-SUITE",
    ]
    assert {r["subject_id"] for r in task.values()} == {"perfect-task/refund"}
    for ident, row in report["items"].items():
        assert row["canonical_anchors"] == definitions[ident]["scoring"]
        assert row["approval_refs"] == [] and row["target_score"] is None
    verification = a.read(output / "source/execution/FINAL-VERIFICATION.json")
    assert verification["bundled_source_identity_verified"] is True
    assert verification["historical_integrity_claimed"] is False


@pytest.fixture(scope="module")
def harbor_scenario(tmp_path_factory):
    location = os.environ.get("EVAL_AUTHOR_TEST_NATIVE_PAIR")
    if not location:
        pytest.skip("set EVAL_AUTHOR_TEST_NATIVE_PAIR to the approved retained pair")
    output = tmp_path_factory.mktemp("harbor-scenario").resolve() / "run"
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(PACKAGE / "run.py"),
            "--output",
            str(output),
            "--harbor-bundle",
            location,
            "--native-registry",
            os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"],
            "--harbor-python",
            os.environ["EVAL_AUTHOR_TEST_HARBOR_PYTHON"],
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr + (
        (output / "execution.log").read_text() if (output / "execution.log").exists() else ""
    )
    assessor = load(output / "source/assess.py")
    return (
        assessor,
        assessor.gather(output / "source/execution"),
        assessor.read(output / "source/scenario-premises.json"),
        output,
    )


def test_harbor_base_all_frozen_anchors_and_actual_credit(harbor_scenario):
    a, evidence, _, output = harbor_scenario
    report = a.read(output / "source/execution/assessment.json")["report"]
    assert len(report["items"]) == 23
    assert all(r["score"] == 3 and r["status"] == "ASSESSED" for r in report["items"].values())
    assert report["native_harbor_run"] is True and report["empirical_qualification"] is False
    assert evidence["harbor"]["controls"]["oracle"]["checks"]["total_credit"] == 9
    assert evidence["harbor"]["controls"]["nop"]["checks"]["total_credit"] == 0
    assert len(evidence["harbor"]["task"]) == 11
    assert evidence["containment"]["attribution"] == "SUPPLIED_FICTIONAL_HISTORY_WITH_LOCAL_REGRESSIONS"
    definitions, _ = a.definitions()
    for surface in ("report.md", "report.html"):
        text = (output / "source/execution" / surface).read_text()
        assert "Harbor Oracle 1.0 (9/9); NOP 0.0 (0/9)" in text
        assert "Local controls are supplemental" in text
    assert all(r["canonical_anchors"] == definitions[k]["scoring"] for k, r in report["items"].items())


@pytest.mark.parametrize("family", ["refund", "interval-reservation"])
def test_harbor_history_does_not_displace_current_exploits(harbor_scenario, family):
    evidence = copy.deepcopy(harbor_scenario[1])
    name = "early-exit-inert-copy" if family == "refund" else "development-1-fixed-template"
    observation = next(o for o in evidence["observations"] if o["id"] == name)
    observation.setdefault("oracle", copy.deepcopy(observation["result"]))
    result = observation["result"]
    result.update(accepted=True, total_credit=result["max_credit"], raw_credit=result["max_credit"])
    result["checks"] = dict.fromkeys(result["checks"], 1)
    observation["oracle_match"] = False
    if family == "refund":
        observation["reward"] = 1.0
    assert result["health"] == "ok" and observation["oracle"]["total_credit"] == 0
    rows = assess(harbor_scenario, evidence)
    affected = ["AUDIT-04-B-SUITE"]
    if family == "refund":
        assert rows["AUDIT-03"]["score"] is None
        affected.append("AUDIT-04-B")
    else:
        assert rows["AUDIT-04-B"]["score"] == 3
    for ident in affected:
        row = rows[ident]
        assert row["score"] is None
        assert row["top_anchor_predicate"]["satisfied"] is False
        assert name in " ".join(row["findings"] + row["remaining_gaps"])
        for check in row["check_results"]:
            assert check["outcome"] == "FAIL"
            assert check["actual_measurement_predicate"] is False
            assert name in check["interpretation"]
            assert any(ref["path"] == "observations.json" for ref in check["evidence_refs"])


@pytest.mark.parametrize(
    "fault",
    [
        "native-check",
        "native-config",
        "native-task-mode",
        "native-receipt",
        "history-subject",
        "history-candidate",
        "history-case",
        "history-revision",
        "history-rule",
        "history-credit",
        "cross-family-replay",
        "cross-family-observation",
        "missing-known-role",
        "duplicate-replay",
        "quarantine",
        "regression-check",
        "missing-retained",
        "rejected-review",
        "wrong-subject",
    ],
)
def test_harbor_base_rejects_bound_counterevidence(harbor_scenario, fault):
    a, original, template, _ = harbor_scenario
    evidence, premises = copy.deepcopy(original), copy.deepcopy(template)
    if fault == "native-check":
        evidence["harbor"]["controls"]["oracle"]["checks"]["checks"]["amount"] = 0
    elif fault == "native-config":
        evidence["harbor"]["controls"]["oracle"]["config"]["trial_name"] = "nop"
    elif fault == "native-task-mode":
        evidence["harbor"]["task"]["tests/test.sh"]["mode"] = 493
    elif fault == "native-receipt":
        evidence["harbor"]["controls"]["oracle"]["receipt"]["command"] = []
    elif fault == "history-subject":
        evidence["containment"]["affected"][0]["subject"] = "another-task"
    elif fault == "history-candidate":
        evidence["containment"]["affected"][0]["historical_observation"]["candidate"]["success"] = False
    elif fault == "history-case":
        evidence["containment"]["affected"][0]["historical_observation"]["case_sha256"] = "0" * 64
    elif fault == "history-revision":
        evidence["containment"]["affected"][0]["affected_revision"]["id"] = "another-revision"
    elif fault == "history-rule":
        evidence["containment"]["affected"][0]["affected_revision"]["rule"] = "all mandatory checks required"
    elif fault == "history-credit":
        evidence["containment"]["affected"][0]["historical_observation"]["published_credit"] = 8
    elif fault == "cross-family-replay":
        evidence["containment"]["regressions"][0]["affected_id"] = evidence["containment"]["affected"][1]["id"]
    elif fault == "cross-family-observation":
        evidence["containment"]["regressions"][0]["family"] = "interval-reservation"
    elif fault == "missing-known-role":
        evidence["containment"]["regressions"][0].update(
            attack_role="independent-attack", independence="SUPPLIED_FICTIONAL_A_AUTHORSHIP"
        )
    elif fault == "duplicate-replay":
        evidence["containment"]["regressions"].append(copy.deepcopy(evidence["containment"]["regressions"][0]))
    elif fault == "quarantine":
        evidence["containment"]["affected"][0]["accepted_for_use"] = True
    elif fault == "regression-check":
        evidence["containment"]["regressions"][0]["result"]["checks"]["amount"] = 1
    elif fault == "missing-retained":
        evidence["containment"]["regressions"] = [
            r for r in evidence["containment"]["regressions"] if r["cohort"] != "retained-affected"
        ]
    elif fault == "rejected-review":
        premises["reviews"]["containment"]["disposition"] = "REJECTED"
    else:
        premises["reviews"]["harbor_base"]["scope"]["subject"] = "another-task"
    bound = a._dispositions.bind_templates(premises, evidence, a.source_manifest())
    rows = a.assess(evidence, bound)["items"]
    assert rows["AUDIT-04-B"]["score"] is None
    assert rows["AUDIT-04-B-SUITE"]["score"] is None
    if fault.startswith("native-") or fault == "wrong-subject":
        assert all(r["score"] is None for r in rows.values())
    else:
        assert all(r["score"] == 3 for k, r in rows.items() if k not in ("AUDIT-04-B", "AUDIT-04-B-SUITE"))


def test_harbor_base_adapter_consumes_receipts_and_containment(scenario):
    adapter_path = PACKAGE / "sources/base_adapter.py"
    assert adapter_path.is_file(), "base-only Harbor adapter is missing"
    adapter = load(adapter_path)
    baseline = scenario[1]
    sources = scenario[0].source_manifest()
    packet = adapter.containment_packet(baseline, sources)
    assert packet["attribution"] == "SUPPLIED_FICTIONAL_HISTORY_WITH_LOCAL_REGRESSIONS"
    assert adapter.containment_checks(packet, baseline, sources, suite=False) == (True, True, True)
    assert adapter.containment_checks(packet, baseline, sources, suite=True) == (True, True, True)
    changed = copy.deepcopy(packet)
    changed["affected"][0]["disposition"] = "accepted-uncontained"
    assert not all(adapter.containment_checks(changed, baseline, sources, suite=False))
    changed = copy.deepcopy(packet)
    changed["regressions"][0]["result"]["checks"]["amount"] = (
        1 - changed["regressions"][0]["result"]["checks"]["amount"]
    )
    assert not all(adapter.containment_checks(changed, baseline, sources, suite=False))
    changed = copy.deepcopy(packet)
    changed["regressions"] = [r for r in changed["regressions"] if r["cohort"] != "retained-affected"]
    assert not all(adapter.containment_checks(changed, baseline, sources, suite=True))


def test_harbor_input_requires_owning_validation(tmp_path):
    runner = load(PACKAGE / "run.py")
    assert callable(getattr(runner, "capture_harbor", None)), "base adapter must validate actual Harbor receipts"
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        runner.capture_harbor(tmp_path, tmp_path / "registry", Path(sys.executable))


def test_materialized_refund_preserves_all_source_bytes_and_modes(tmp_path):
    runner = load(PACKAGE / "run.py")
    source = runner.materialize(tmp_path / "materialized")
    original = ROOT / "skills/eval-author-rubric/examples/refund/base"
    copied = source / "prior-package/perfect-task/refund"
    expected = {
        str(p.relative_to(original)): (p.read_bytes(), p.stat().st_mode & 0o777)
        for p in original.rglob("*")
        if p.is_file()
    }
    actual = {
        str(p.relative_to(copied)): (p.read_bytes(), p.stat().st_mode & 0o777) for p in copied.rglob("*") if p.is_file()
    }
    assert len(expected) == 11
    assert actual == expected


def test_source_change_rejected_before_output(tmp_path, monkeypatch):
    runner = load(PACKAGE / "run.py")
    fake = tmp_path / "package"
    fake.mkdir()
    mapping = json.loads((PACKAGE / "source-map.json").read_bytes())
    next(iter(mapping["files"].values()))["sha256"] = "0" * 64
    (fake / "source-map.json").write_text(json.dumps(mapping))
    monkeypatch.setattr(runner, "PACKAGE", fake)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="bundled source identity mismatch"):
        runner.materialize(output)
    assert not output.exists()
