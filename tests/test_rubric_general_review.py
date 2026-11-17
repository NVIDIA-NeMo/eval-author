# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


def test_contract_record_uses_existing_operator_boundary(tmp_path, monkeypatch):
    import rubric_general_review as module
    from rubric_review_intake import BASE, prepare_task
    from rubric_source_reviews import OPERATOR_TRUST

    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(tmp_path))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", OPERATOR_TRUST)
    request = module.prepare(BASE, kind="contract")
    assert request["crosswalk"]["intake_binding"] == prepare_task(BASE)["binding"]
    assert len(request["crosswalk"]["rows"]) == 15
    event = {
        "request": request,
        "decisions": {
            key: {"decision": "approve", "content": "Synthetic test only", "rationale": "Synthetic test only"}
            for key in request["scopes"]
        },
        "retention_consent": "Synthetic contract consent",
        "retained_scopes": list(request["scopes"]),
        "save_authorized": True,
    }
    module.record(BASE, lambda actual: event if actual == request else None, kind="contract")
    accepted = module.read(BASE, kind="contract")
    assert accepted["event"] == event
    from rubric_table import main, select_task

    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(select_task(BASE.parent, "base/task.toml")))
    output = tmp_path / "contract-report"
    assert main(["--root", str(BASE.parent), "--selection", str(selection), "--output", str(output)]) == 1
    assert main(["--root", str(BASE.parent), "--verify-existing", str(output)]) == 1
    module.revoke(BASE, kind="contract")
    assert module.read(BASE, kind="contract")["revoked"]
    assert main(["--root", str(BASE.parent), "--verify-existing", str(output)]) == 2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))


def test_positive_contract_requires_current_coverage_and_keeps_higher_checks_pending():
    import rubric_contract_positive as positive
    import rubric_general_review as general
    from rubric_refund_contract_evidence import project
    from rubric_review_intake import BASE
    from rubric_table import select_task

    request = general.prepare(BASE, kind="contract")
    selection = select_task(BASE.parent, "base/task.toml")
    measured = {"refund_contract": project(BASE.parent, selection)}
    automatic = positive.automatic(request, measured)
    assert automatic["task-contract.clear-contract"]["ready"]
    assert automatic["task-contract.scoring-authority"]["ready"]
    assert not automatic["task-contract.discoverable-requirements"]["ready"]
    rows = {
        key: {
            "score": None,
            "check_results": [
                {"id": key + "." + method, "method": method, "outcome": "NOT_RUN", "evidence_refs": []}
                for method in ("human_review", "llm_as_judge", "deterministic", "probe")
            ],
            "evidence_refs": [],
            "limitations": [],
            "remaining_gaps": [],
        }
        for key in request["scopes"]
    }
    projection = {
        "request": request,
        "automatic": automatic,
        "receipt": {
            "event": {
                "decisions": {
                    key: {"decision": "approve", "content": "Synthetic", "rationale": "Synthetic"} for key in rows
                }
            },
            "revoked": False,
            "digest": "synthetic",
            "trust_basis": "synthetic",
        },
    }
    failed = deepcopy(rows)
    failed["task-contract.clear-contract"]["check_results"][0]["outcome"] = "FAIL"
    general.apply_scoped(failed, projection)
    assert failed["task-contract.clear-contract"]["score"] is None
    general.apply_scoped(rows, projection)
    assert rows["task-contract.clear-contract"]["score"] == 2
    assert rows["task-contract.scoring-authority"]["score"] == 2
    assert rows["task-contract.discoverable-requirements"]["score"] is None
    assert rows["task-contract.clear-contract"]["check_results"][-1]["outcome"] == "NOT_RUN"
    changed = deepcopy(request)
    changed["crosswalk"]["rows"].pop()
    assert not positive.automatic(changed, measured)["task-contract.clear-contract"]["ready"]


def test_table_complete_numeric_rows_allow_nonstatus_measurements(monkeypatch, tmp_path, capsys):
    import rubric_table

    report = {"items": {"test": {"score": 2}}, "measurements": {"scoped_reviews": {}}}
    monkeypatch.setattr(rubric_table, "assess_table", lambda *args: report)
    monkeypatch.setattr(rubric_table, "render", lambda *args: "test\n")
    monkeypatch.setattr(rubric_table, "render_html", lambda *args: "<p>test</p>")
    path = tmp_path / "selection.json"
    path.write_text("{}")
    assert rubric_table.main(["--root", str(tmp_path), "--selection", str(path)]) == 1


def test_table_reports_exact_scoped_owner_answer():
    from rubric_table import human_action

    row = {
        "scoped_operator_review": {
            "decision": {"decision": "approve", "content": "Synthetic exact answer"},
            "revoked": False,
        },
        "check_results": [],
    }
    assert "Synthetic exact answer" in human_action(row)
    assert "No repeated owner decision" in human_action(row)


def test_scoped_operator_record_requires_consent_and_revokes(tmp_path, monkeypatch):
    assert importlib.util.find_spec("rubric_general_review") is not None, "general review intake missing"
    module = importlib.import_module("rubric_general_review")
    from rubric_review_intake import BASE
    from rubric_source_reviews import OPERATOR_TRUST

    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(tmp_path))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", OPERATOR_TRUST)
    request = module.prepare(BASE)
    event = {
        "request": request,
        "decisions": {
            key: {"decision": "approve", "content": "Synthetic test answer", "rationale": "Synthetic exact rationale"}
            for key in request["scopes"]
        },
        "retention_consent": "Synthetic consent for these two scopes only",
        "retained_scopes": list(request["scopes"]),
        "save_authorized": True,
    }
    denied = deepcopy(event)
    denied["retained_scopes"] = ["provenance.task-artifact-access"]
    with pytest.raises(ValueError):
        module.record(BASE, lambda _: denied)
    assert not list(tmp_path.iterdir())
    path = module.record(BASE, lambda actual: event if actual == request else None)
    accepted = module.read(BASE)
    assert accepted["event"] == event
    assert accepted["revoked"] is False
    assert path.exists()
    from rubric_table import main, select_task

    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(select_task(BASE.parent, "base/task.toml")))
    output = tmp_path / "report"
    assert main(["--root", str(BASE.parent), "--selection", str(selection_path), "--output", str(output)]) == 1
    assert main(["--root", str(BASE.parent), "--verify-existing", str(output)]) == 1
    assert "authenticated" in accepted["trust_basis"]
    module.revoke(BASE)
    assert main(["--root", str(BASE.parent), "--verify-existing", str(output)]) == 2
    revoked = module.read(BASE)
    assert revoked["event"] == event
    assert revoked["revoked"] is True
    with pytest.raises(ValueError):
        module.record(BASE, lambda _: event)
    tampered = json.loads(path.read_text())
    tampered["event"]["request"]["sources"]["instruction.md"] = "0" * 64
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError):
        module.read(BASE)


def test_fairness_rejects_undisclosed_setup_and_retains_scoped_decision(tmp_path, monkeypatch):
    import rubric_fairness as fairness
    import rubric_general_review as general
    from rubric_review_intake import BASE, _digest
    from rubric_source_reviews import OPERATOR_TRUST

    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(tmp_path))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", OPERATOR_TRUST)
    arms = [
        {
            "case": case,
            "native_id": case,
            "configuration": {"agent": {"kwargs": {"case": case}}, "trial_name": case},
            "checks": {"health": "ok", "total_credit": 9, "max_credit": 9, "checks": {str(i): 1 for i in range(9)}},
            "reward": 1,
            "solver_uid": 65534,
            "candidate": {},
        }
        for case in ("reference", "split-alternative")
    ]
    from rubric_audit_companion import typed_diff

    differences = typed_diff(arms[0]["configuration"], arms[1]["configuration"])
    for row in differences:
        row["classification"] = "run_identity" if row["pointer"] == "/trial_name" else "scripted_policy_variant"
    comparison = {
        "model_study": False,
        "arms": arms,
        "setup_differences": differences,
        "owner_question": {"binding": _digest(arms), "difference_refs": ["/agent/kwargs/case"]},
    }
    request = general.prepare(BASE, kind="fairness", comparison=comparison)
    assert fairness.automatic(request)["provenance.comparison-fairness"]["ready"]
    event = {
        "request": request,
        "decisions": {
            "provenance.comparison-fairness": {"decision": "approve", "content": "Synthetic", "rationale": "Synthetic"}
        },
        "retention_consent": "Synthetic",
        "retained_scopes": list(request["scopes"]),
        "save_authorized": True,
    }
    general.record(BASE, lambda _: event, kind="fairness", comparison=comparison)
    assert general.read(BASE, kind="fairness", comparison=comparison)["event"] == event
    assert (
        "request" not in general.projection_receipt(general.read(BASE, kind="fairness", comparison=comparison))["event"]
    )
    general.revoke(BASE, kind="fairness", comparison=comparison)
    assert general.read(BASE, kind="fairness", comparison=comparison)["revoked"]
    changed = deepcopy(request)
    changed["comparison"]["arms"][1]["configuration"]["budget"] = 2
    assert not fairness.automatic(changed)["provenance.comparison-fairness"]["ready"]


def test_projection_never_replaces_missing_native_evidence_or_failed_checks(tmp_path, monkeypatch):
    module = importlib.import_module("rubric_general_review")
    assert hasattr(module, "project"), "ordinary measurement projection missing"
    from rubric_review_intake import BASE
    from rubric_source_reviews import OPERATOR_TRUST
    from rubric_table import select_task

    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(tmp_path))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", OPERATOR_TRUST)
    selection = select_task(BASE.parent, "base/task.toml")
    request = module.prepare(BASE)
    module.record(
        BASE,
        lambda _: {
            "request": request,
            "decisions": {
                key: {"decision": "approve", "content": "Synthetic intent", "rationale": "Synthetic rationale"}
                for key in module.SCOPES
            },
            "retention_consent": "Synthetic scoped consent",
            "retained_scopes": list(module.SCOPES),
            "save_authorized": True,
        },
    )
    rows = {
        key: {
            "score": None,
            "check_results": [
                {
                    "id": key + "." + method.replace("_", "-"),
                    "method": method,
                    "outcome": "NOT_RUN" if method == "human_review" else "PASS",
                    "evidence_refs": [],
                }
                for method in ("human_review", "deterministic", "probe")
            ],
            "evidence_refs": [],
            "limitations": [],
            "remaining_gaps": [],
        }
        for key in module.SCOPES
    }
    projection = module.project(BASE.parent, selection, {"example_assessment": {"native": None}})
    module.apply(rows, projection)
    assert all(row["score"] is None for row in rows.values())
    assert all(row["check_results"][0]["outcome"] == "PASS" for row in rows.values())
    assert all("current" in " ".join(row["remaining_gaps"]) for row in rows.values())
    revoked_rows = deepcopy(rows)
    for row in revoked_rows.values():
        row["check_results"][0]["outcome"] = "NOT_RUN"
    module.revoke(BASE)
    projection = module.project(BASE.parent, selection, {"example_assessment": {"native": None}})
    module.apply(revoked_rows, projection)
    assert all(row["check_results"][0]["outcome"] == "NOT_RUN" for row in revoked_rows.values())


def test_ordinary_measurement_projects_pending_intent(tmp_path, monkeypatch):
    from rubric_review_intake import BASE
    from rubric_table import select_task

    module = importlib.import_module("skills.eval-author-rubric.scripts.measure")
    monkeypatch.delenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", raising=False)
    selection = select_task(BASE.parent, "base/task.toml")
    report = module.measure(BASE.parent, selection)
    assert "scoped_reviews" in report["measurements"], "ordinary path omits contract/fairness reviews"
    assert report["measurements"]["scoped_reviews"]["contract"]["request"]["crosswalk"]["rows"]
    assert "general_review" in report["measurements"], "ordinary path omits operator review"
    assert report["measurements"]["general_review"]["receipt"] is None
    assert report["items"]["provenance.solver-tool-access"]["score"] is None


def test_automatic_coverage_and_numeric_gate_preserve_failures():
    module = importlib.import_module("rubric_general_review")
    from rubric_native_collect import observation_contract
    from rubric_review_intake import BASE

    request = module.prepare(BASE)
    contract = observation_contract()
    components = {
        name: {
            **component,
            "source_sha256": request["sources"][component["source"]],
            "observed_in": ["nop", "actions", "reset"],
            "observed_identities": {case: {"synthetic": True} for case in ("nop", "actions", "reset")},
        }
        for name, component in contract["components"].items()
    }
    panel = {
        "scope": "eval-author.refund-runtime-observation.v1",
        "index_digest": "synthetic-unit-only",
        "native_execution": True,
        "task_freshness": "current",
        "environment": {
            "contract": contract,
            "components": components,
            "state_relation": contract["state"],
            "action_controls": [
                {
                    "case": case,
                    "uid": 65534,
                    "errors_preserve_exact_state": True,
                    "initial_state_matches": True,
                    "event_count": 3 if case == "actions" else 0,
                    "reward": 1 if case == "actions" else 0,
                    "container_id": case,
                    "session_id": case,
                }
                for case in ("nop", "actions", "reset")
            ],
        },
    }
    automatic = module._automatic(panel, request)
    assert automatic["ready"] is True
    rows = {
        key: {
            "score": None,
            "evidence_refs": [],
            "limitations": [],
            "remaining_gaps": [],
            "check_results": [
                {
                    "id": key + "." + method,
                    "method": method,
                    "outcome": "NOT_RUN" if method == "human_review" else "PASS",
                    "evidence_refs": [],
                }
                for method in ("human_review", "deterministic", "probe")
            ],
        }
        for key in module.SCOPES
    }
    projection = {
        "request": request,
        "automatic": automatic,
        "receipt": {
            "event": {
                "decisions": {
                    key: {"decision": "approve", "content": "Synthetic", "rationale": "Synthetic"}
                    for key in module.SCOPES
                }
            },
            "revoked": False,
            "digest": "synthetic-unit-only",
            "trust_basis": "synthetic",
        },
    }
    failed = deepcopy(rows)
    failed["provenance.solver-tool-access"]["check_results"][-1]["outcome"] = "FAIL"
    module.apply(failed, projection)
    assert failed["provenance.solver-tool-access"]["score"] is None
    assert not any("support level 2" in gap for gap in failed["provenance.solver-tool-access"]["remaining_gaps"])
    module.apply(rows, projection)
    assert {row["score"] for row in rows.values()} == {2}
    assert automatic["level_3"] is False
    for change in ("missing-reset", "wrong-uid", "stale", "missing-component"):
        altered = deepcopy(panel)
        if change == "missing-reset":
            altered["environment"]["action_controls"].pop()
        elif change == "wrong-uid":
            altered["environment"]["action_controls"][0]["uid"] = 0
        elif change == "stale":
            altered["task_freshness"] = "stale"
        else:
            altered["environment"]["components"].pop("ledger")
        assert module._automatic(altered, request)["ready"] is False
