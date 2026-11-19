# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def module():
    path = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric/current_rubric_completion.py"
    spec = importlib.util.spec_from_file_location("current_completion_test", path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_observations_do_not_use_targets_or_count_operational_failures(monkeypatch):
    completion = module()
    monkeypatch.setattr(completion, "load", lambda _: {"report": {"items": {"criterion": {"check_results": []}}}})
    definition = {"scoring": {str(n): str(n) for n in range(4)}, "checks": []}
    monkeypatch.setattr(completion.runner, "canonical_definitions", lambda: {"criterion": definition})
    monkeypatch.setattr(completion.runner, "matrix_remaining", lambda *_: "pending")
    rows: dict[str, Any] = {
        "base": {"criterion": {"score": 3, "checks": {}, "remaining_gaps": []}},
        "control": {"criterion": {"score": 1, "checks": {}, "remaining_gaps": []}},
        "error": {"criterion": {"score": 0, "checks": {}, "remaining_gaps": []}},
    }
    monkeypatch.setattr(completion, "read_rows", lambda record: rows[record["report"]])
    base = {"report": "base"}
    matrix = {
        "base": base,
        "repair": base,
        "base_scores": {"criterion": 3},
        "base_qualified": True,
        "current_inventory": {},
        "missing_evidence_cells": [],
        "operational_failure_controls": [],
        "runs": [
            {"case": "wrong-target", "assessment": {"report": "control"}, "current_expected": {"criterion": 0}},
            {
                "case": "error",
                "assessment": {"report": "error"},
                "current_expected": {"criterion": 0},
                "evidence_control_kind": "operational_failure",
            },
        ],
    }
    result = completion.coverage(matrix)
    assert result["numeric_observed"] == 2
    assert result["lower_numeric_observed"] == 1
    assert result["cells"][0]["state"] == "pending"
    assert result["cells"][1]["state"] == "qualified_base_drop"
    assert result["cells"][1]["observations"][0]["score"] == 1
    rows["base"] = {"criterion": {"score": None, "checks": {}, "remaining_gaps": []}}
    result = completion.coverage(matrix)
    assert result["cells"][1]["state"] == "observed_unqualified_control"
    assert not result["cells"][1]["observations"][0]["lower_base_drop"]


def test_separate_qualified_subject_never_inherits_base_or_repair(monkeypatch):
    completion = module()
    monkeypatch.setattr(completion, "load", lambda _: {"report": {"items": {"criterion": {"check_results": []}}}})
    definition = {"scoring": {str(n): str(n) for n in range(4)}, "checks": []}
    monkeypatch.setattr(completion.runner, "canonical_definitions", lambda: {"criterion": definition})
    monkeypatch.setattr(completion.runner, "matrix_remaining", lambda *_: "pending")
    monkeypatch.setattr(
        completion,
        "read_rows",
        lambda record: {
            "criterion": {"score": 1 if record["report"] == "base" else 3, "checks": {}, "remaining_gaps": []}
        },
    )
    base = {"report": "base"}
    matrix = {
        "base": base,
        "repair": base,
        "base_scores": {"criterion": 1},
        "base_qualified": False,
        "current_inventory": {},
        "runs": [],
        "missing_evidence_cells": [],
        "operational_failure_controls": [],
        "supporting_reports": [{"case": "separate-subject", "report": "support"}],
    }
    value = completion.coverage(matrix)
    assert value["base_scores"] == {"criterion": 1}
    assert value["numeric_observed"] == 2
    assert value["separate_supporting_reports"] == 1
    observation = value["cells"][3]["observations"][0]
    assert observation["paired_base"] is None
    assert observation["repair_report"] is None
    assert not observation["qualified_base_drop"]
    assert not observation["lower_base_drop"]


def support_module(monkeypatch):
    import sys

    root = Path(__file__).resolve().parents[1] / "skills/eval-author-rubric"
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.delitem(sys.modules, "current_rubric_completion", raising=False)
    monkeypatch.delitem(sys.modules, "current_rubric_support", raising=False)
    import current_rubric_support  # ty: ignore[unresolved-import]

    return current_rubric_support


def test_unreviewed_companion_rejected_before_code_execution(tmp_path, monkeypatch):
    import pytest

    support = support_module(monkeypatch)
    roots = {key: tmp_path for key in ("qualification", "upper", "native")}
    marker = tmp_path / "executed"
    (tmp_path / "qualification.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError, match="unreviewed companion"):
        with support.reviewed_modules(roots):
            raise AssertionError("must not load")
    assert not marker.exists()


def test_owning_matrix_digest_and_cycle_rejected(tmp_path, monkeypatch):
    import json

    import pytest

    support = support_module(monkeypatch)
    monkeypatch.setattr(support.c.runner, "definitions_digest", lambda: "canonical")
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps({"definitions_sha256": "canonical", "owning_matrix": str(path), "owning_matrix_sha256": "wrong"})
    )
    with pytest.raises(ValueError, match="owning matrix changed"):
        support.matrix_lineage(path)
    monkeypatch.setattr(support, "sha", lambda _: "wrong")
    with pytest.raises(ValueError, match="lineage cycle"):
        support.matrix_lineage(path)


def test_revoked_or_modified_crosswalk_is_not_retained(tmp_path, monkeypatch):
    import json

    import pytest

    support = support_module(monkeypatch)
    path = tmp_path / "event.json"
    event = {"request": {"binding": "exact"}, "decisions": {"x": {"decision": "defer"}}}
    path.write_text(json.dumps({"event": event, "trust_basis": "local"}))
    receipt = {"event": event, "trust_basis": "local", "digest": support.sha(path)}
    crosswalk = {"rows": [{"kind": "owner", "status": "retained", "binding": "exact", "current_receipt": receipt}]}
    support.validate_crosswalk(crosswalk, tmp_path)
    path.with_suffix(".revoked").touch()
    with pytest.raises(ValueError, match="revoked"):
        support.validate_crosswalk(crosswalk, tmp_path)
    path.with_suffix(".revoked").unlink()
    path.write_text("{}")
    with pytest.raises(ValueError, match="absent or ambiguous"):
        support.validate_crosswalk(crosswalk, tmp_path)


def test_trust_is_explicit_and_restored(tmp_path, monkeypatch):
    import os

    support = support_module(monkeypatch)
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE", "unrelated")
    monkeypatch.setenv("EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE", "inherited")
    with support.trust_environment(tmp_path, "localhost-assessment-operator-v1", [tmp_path / "registry"]):
        assert os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE"] == str(tmp_path)
        assert "EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE" not in os.environ
    assert os.environ["EVAL_AUTHOR_SOURCE_REVIEW_OPERATOR_STORE"] == "unrelated"
    assert os.environ["EVAL_AUTHOR_SOURCE_REVIEW_SESSION_STORE"] == "inherited"


def test_retained_selection_bytes_rechecked_without_saved_commands(tmp_path, monkeypatch):
    import json

    import pytest

    support = support_module(monkeypatch)
    monkeypatch.setattr(support.c.runner, "canonical_definitions", lambda: {"criterion": {}})
    source = tmp_path / "source.txt"
    source.write_text("original")
    report = tmp_path / "assessment.json"
    report.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "report": {"items": {"criterion": {"score": None}}},
                "selection": {
                    "artifacts": [{"path": "source.txt", "access": "available", "digest": support.sha(source)}]
                },
            }
        )
    )
    record = {"report": str(report), "command": ["never-execute-this"]}
    matrix = {"base": record, "repair": record, "runs": []}
    assert len(support.records(matrix)) == 1
    assert len(support.retained_inputs(matrix)) == 2
    source.write_text("changed")
    with pytest.raises(ValueError, match="selected evidence changed"):
        support.retained_inputs(matrix)


def test_checkout_and_skill_help_share_the_supported_command():
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    outputs = []
    for relative in ("tools/current_rubric_completion.py", "skills/eval-author-rubric/current_rubric_completion.py"):
        result = subprocess.run(
            [sys.executable, "-B", str(root / relative), "--help"],
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            check=True,
        )
        assert "--reuse-current" in result.stdout
        assert "finite-upper-v1" in result.stdout
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
