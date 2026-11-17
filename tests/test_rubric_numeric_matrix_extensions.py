# SPDX-License-Identifier: Apache-2.0
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/scripts"))
import rubric_table
import rubric_semantic_numeric_mutations as semantic


def event(request):
    return {"request": request, "decisions": {semantic.FAIRNESS: {"decision": "approve", "content": "Synthetic test answer only", "rationale": "Synthetic test exact-comparison disposition"}},
            "retention_consent": "Synthetic test scope", "retained_scopes": list(request["scopes"]), "save_authorized": True}


def test_operator_scoped_answer_replay_revocation_and_reseal(tmp_path, monkeypatch):
    store = tmp_path / "operator"
    store.mkdir()
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", str(store))
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_TRUST", "localhost-assessment-operator-v1")
    root = tmp_path / "case"
    selection = semantic.materialize(root, "advantaged-comparison")
    before = rubric_table.measure(root, selection)
    assert before["items"][semantic.FAIRNESS]["score"] == 1
    path = semantic.record_decision(root, selection, event)
    report = tmp_path / "report"
    selected = tmp_path / "selection.json"
    selected.write_text(json.dumps(selection))
    assert rubric_table.main(["--root", str(root), "--selection", str(selected), "--output", str(report)]) == 1
    assert rubric_table.main(["--root", str(root), "--verify-existing", str(report)]) == 1
    after = json.loads((report / "assessment.json").read_text())["report"]
    assert after["items"][semantic.FAIRNESS]["score"] == 0
    proof = after["measurements"]["semantic_numeric"]
    assert proof["observations"]["comparison"]["owner_decision"]["owner_signature_verified"] is False
    other = tmp_path / "other"
    other_selection = semantic.materialize(other, "untested-comparison")
    assert rubric_table.measure(other, other_selection)["items"][semantic.FAIRNESS]["score"] == 1
    semantic.revoke_decision(proof)
    assert rubric_table.main(["--root", str(root), "--verify-existing", str(report)]) == 2
    assert rubric_table.measure(root, selection)["items"][semantic.FAIRNESS]["score"] == 1
    retained = json.loads(path.read_text())
    retained["event"]["request"]["comparison"]["paired"]["no-work"]["system-A"]["total_credit"] = 999
    path.write_text(json.dumps(retained))
    with pytest.raises(ValueError):
        rubric_table.measure(root, selection)


def test_extension_expectation_not_selected_score():
    import rubric_mutation_runner as runner
    from rubric_numeric_matrix_extensions import extension_specs
    assert sum(len(v) for v in extension_specs("native").values()) == 7
    assert sum(len(v) for v in extension_specs("semantic").values()) == 9
    with pytest.raises(ValueError):
        runner.validate_matrix_expectation({"extension_kind": "semantic", "case": "advantaged-comparison", "expected": {semantic.FAIRNESS: 3}})
