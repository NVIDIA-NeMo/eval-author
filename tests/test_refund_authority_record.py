# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/scorer-controls"


def test_refund_authority_preserves_ids_citations_and_reviewed_bytes():
    contract = json.loads((FIXTURES / "contract.json").read_text())
    authority = json.loads((FIXTURES / "authority.json").read_text())
    cases = json.loads((FIXTURES / "cases.json").read_text())
    review = (FIXTURES / "authority-review.md").read_text()
    spec = importlib.util.spec_from_file_location("refund_authority_scorer", FIXTURES / "refund_scorer.py")
    assert spec and spec.loader
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    ids = list(scorer.CHECKS)
    assert len(ids) == 9
    assert [row["id"] for row in authority["requirements"]] == ids
    assert [row["id"] for row in authority["checks"]] == ids
    policy_refs = set()
    for requirement, check in zip(authority["requirements"], authority["checks"], strict=True):
        ident = requirement["id"]
        assert requirement["authority"] == {
            "basis": "specification",
            "reference": f"evals/scorer-controls/contract.json#/requirements/{ident}",
        }
        assert requirement["statement"].startswith(contract["requirements"][ident])
        assert check == {
            "id": ident,
            "requirements": [ident],
            "success": {"pointer": f"/checks/{ident}", "equals": contract["scoring"]["weights"][ident]},
        }
        refs = re.findall(r"evals/scorer-controls/contract\.json#(/[^\s,.]+)", requirement["statement"])
        assert refs
        for pointer in refs:
            value = contract
            for key in pointer.lstrip("/").split("/"):
                value = value[key]
            if pointer.startswith("/sources/policy/"):
                policy_refs.add(pointer)
    assert policy_refs == {f"/sources/policy/{key}" for key in contract["sources"]["policy"]}
    assert cases["contract_authority"] == "unassessed-synthetic-fixture-not-authenticated"
    assert cases["contract_authority_record"] == "evals/scorer-controls/authority.json"
    assert cases["contract_authority_review"] == "evals/scorer-controls/authority-review.md"
    assert "**Human review: recorded.**" in review
    for name in ("authority.json", "contract.json", "refund_scorer.py"):
        digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert f"`{name}`: `{digest}`" in review


def test_reviewed_contract_clarifications_preserve_all_control_outcomes():
    import copy

    spec = importlib.util.spec_from_file_location("refund_authority_scorer", FIXTURES / "refund_scorer.py")
    assert spec and spec.loader
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    amended = json.loads((FIXTURES / "contract.json").read_text())
    original = copy.deepcopy(amended)
    original["requirements"]["amount"] = (
        "Refund discounted merchandise plus eligible tax minus prior refunds; exclude shipping."
    )
    original["requirements"]["completion_signal"] = (
        "Report success after completing the refund; optional credit only in the reviewed arm."
    )
    cases = json.loads((FIXTURES / "cases.json").read_text())["cases"]
    for case in cases:
        for arm in ("baseline", "reviewed"):
            assert scorer.evaluate(amended, case["candidate"], arm) == scorer.evaluate(
                original, case["candidate"], arm
            ), (case["id"], arm)
