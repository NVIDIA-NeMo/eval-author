# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


def test_combined_access_panel_dispatch_preserves_other_rows():
    from rubric_combined import apply_panels
    from rubric_example_assessment import _ensure_rows
    from rubric_example_native import apply

    rows = {}
    _ensure_rows(rows)
    before = deepcopy(rows)
    apply_panels(
        rows, [{"scope": "eval-author.refund-access-panel.v1", "index_digest": "a" * 64, "ac01_score": 2}], apply
    )
    assert rows["AC-01"]["score"] == 2
    assert all(rows[k] == value for k, value in before.items() if k != "AC-01")


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))


def test_access_contract_uses_owning_recorder():
    from rubric_refund_access import collector, contract

    collector.recorder.validate_contract(contract())


def test_access_controls_pair_denial_with_public_read():
    access = importlib.import_module("rubric_refund_access")
    inventory = access.control_inventory()
    assert inventory["independence"] is False
    assert "public and readable" in inventory["review"]["helpers"]
    assert "confidentiality is not explicitly specified" not in inventory["review"]["helpers"]
    assert "no verifier-confidentiality decision remains" in inventory["owner_question"]
    assert inventory["protected"]["/solution/reference.py"] == "absent"
    assert "/app/contract.json" in inventory["permitted"]
    assert set(inventory["paths"]) == {"direct", "proc-root", "symlink"}
    assert access.assess_reads({"public": False, "protected": True}) is None
    assert access.assess_reads({"public": True, "protected": False}) == 0
    assert access.assess_reads({"public": True, "protected": True}) == 2


def test_read_projection_requires_exact_source_and_role():
    import hashlib
    import json

    import pytest
    import rubric_native_collect as collector
    from rubric_refund_access import control_inventory, validate_reads

    inventory = control_inventory()
    reads = {p: {"status": status} for p, status in inventory["protected"].items()}
    reads.update(
        {
            p: {
                "status": "read",
                "sha256": hashlib.sha256((collector.BASE / "environment" / Path(p).name).read_bytes()).hexdigest(),
            }
            for p in inventory["permitted"]
        }
    )
    record: dict[str, Any] = {
        "uid": 65534,
        "reads": {method: deepcopy(reads) for method in inventory["paths"]},
        "socket": {
            "ok": True,
            "value": {
                "candidate": {"events": [], "success": False},
                "state": json.loads((collector.BASE / "environment/contract.json").read_text())["sources"][
                    "initial_state"
                ],
            },
        },
        "invalid_tool": {"ok": False, "error": "invalid_request"},
        "helpers": {p: {"status": "absent"} for p in ["/tests/test.sh", "/tests/verify.py", "/tests/refund_scorer.py"]},
        "docker_socket": "absent",
    }
    assert validate_reads(record, False) == {"public": True, "protected": True}
    broken = deepcopy(record)
    broken["reads"]["direct"]["/app/contract.json"]["sha256"] = "0" * 64
    assert validate_reads(broken, False)["public"] is False
    exposed = deepcopy(record)
    exposed["reads"]["symlink"]["/solution/reference.py"]["status"] = "read"
    assert validate_reads(exposed, False)["protected"] is False
    with pytest.raises(ValueError):
        validate_reads({**record, "uid": 0}, False)


def test_retained_access_relocation_and_coherent_tampering(tmp_path, monkeypatch):
    import json
    import os
    import shutil

    import pytest
    from rubric_refund_access import AccessPanel, collector, validate

    configured = os.environ.get("EVAL_AUTHOR_ACCESS_TEST_BUNDLE")
    if not configured:
        pytest.skip("set EVAL_AUTHOR_ACCESS_TEST_BUNDLE to a current native panel")
    original = Path(configured)
    document = json.loads((original / "native-index.json").read_text())
    baseline = validate(original, document)
    relocated = tmp_path / "access"
    relocated.mkdir()
    for name in AccessPanel.directories:
        shutil.copytree(original / name, relocated / name)
    for name in AccessPanel.documents:
        shutil.copy2(original / name, relocated / name)
    assert validate(relocated, document)["ac01_score"] == baseline["ac01_score"] == 2
    source = relocated / "trials/access-a/verifier/access.json"
    value = json.loads(source.read_text())
    value["after_upload"]["reads"]["direct"]["/app/contract.json"]["sha256"] = "0" * 64
    source.write_text(json.dumps(value))
    for row in document["files"]:
        if row["path"] == source.relative_to(relocated).as_posix():
            row["sha256"] = collector.recorder.digest(source)
    assert validate(relocated, document)["ac01_score"] is None
    value["after_upload"]["uid"] = 0
    source.write_text(json.dumps(value))
    for row in document["files"]:
        if row["path"] == source.relative_to(relocated).as_posix():
            row["sha256"] = collector.recorder.digest(source)
    with pytest.raises(ValueError):
        validate(relocated, document)
