# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from rubric_ethos import check_ethos  # noqa: E402

VALIDATOR = ROOT / "team-skills/ethos/ethos-write/scripts/validate_ethos.py"
VALID = (ROOT / "team-skills/ethos/ethos-write/evals/files/router-help/ETHOS.md").read_bytes()
CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"


def selected(root, content, *, digest=None, role="ethos"):
    (root / "ETHOS.md").write_bytes(content)
    record = {
        "id": "ethos",
        "role": role,
        "path": "ETHOS.md",
        "digest": digest or hashlib.sha256(content).hexdigest(),
        "revision": None,
        "access": "available",
        "selection_origin": "explicit",
        "source": "fixture",
        "evidence": [{"path": "ETHOS.md", "digest": hashlib.sha256(content).hexdigest()}],
    }
    return [record]


def codes(result):
    return [(row["code"], row["status"]) for row in result["diagnostics"]]


def test_valid_ethos_binds_document_and_validator_bytes(tmp_path):
    result = check_ethos(tmp_path, selected(tmp_path, VALID), {"artifact_id": "ethos"}, VALIDATOR)
    assert (result["status"], result["valid"], result["error"]) == ("pass", True, None)
    assert result["digest"] == hashlib.sha256(VALID).hexdigest()
    assert result["validator_digest"] == hashlib.sha256(VALIDATOR.read_bytes()).hexdigest()


def test_validator_rejection_is_a_failed_measurement_with_its_reason(tmp_path):
    broken = VALID.replace(b"\n## Constraints\n", b"\n## Limits\n")
    result = check_ethos(tmp_path, selected(tmp_path, broken), {"artifact_id": "ethos"}, VALIDATOR)
    assert (result["status"], result["valid"]) == ("fail", False)
    assert result["error"] == "missing section: ## Constraints"
    assert codes(result) == [("ethos.invalid", "fail")]


def test_changed_bytes_fail_even_when_the_new_content_validates(tmp_path):
    records = selected(tmp_path, VALID, digest="0" * 64)
    result = check_ethos(tmp_path, records, {"artifact_id": "ethos"}, VALIDATOR)
    assert result["valid"] is True
    assert codes(result) == [("ethos.digest_mismatch", "fail")]


@pytest.mark.parametrize(
    ("validator", "code"),
    [
        (None, "ethos.validator_unavailable"),
        ("absent.py", "ethos.validator_unavailable"),
        ("crash.py", "ethos.validator_failed"),
    ],
)
def test_unusable_validator_leaves_validity_unknown(tmp_path, validator, code):
    (tmp_path / "crash.py").write_text("class EthosError(ValueError): pass\ndef validate(text): raise RuntimeError\n")
    path = tmp_path / validator if validator else None
    result = check_ethos(tmp_path, selected(tmp_path, VALID), {"artifact_id": "ethos"}, path)
    assert (result["status"], result["valid"]) == ("unknown", None)
    assert codes(result) == [(code, "unknown")]


@pytest.mark.parametrize(
    ("selection", "role"),
    [({"artifact_id": "missing"}, "ethos"), ({"artifact_id": "ethos", "extra": 1}, "ethos"), ("ethos", "ethos")]
    + [({"artifact_id": "ethos"}, "task")],
)
def test_selection_must_name_one_ethos_artifact(tmp_path, selection, role):
    with pytest.raises(ValueError):
        check_ethos(tmp_path, selected(tmp_path, VALID, role=role), selection, VALIDATOR)


def test_cli_reports_ethos_measurement(tmp_path):
    request = tmp_path / "selection.json"
    request.write_text(json.dumps({"artifacts": selected(tmp_path, VALID), "ethos": {"artifact_id": "ethos"}}))
    result = subprocess.run(
        [sys.executable, str(CLI), "--root", str(tmp_path), "--selection", str(request)]
        + ["--ethos-validator", str(VALIDATOR)],
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["measurements"]["ethos"]["valid"] is True
    assert report["measurements"]["ethos"]["status"] == "pass"
