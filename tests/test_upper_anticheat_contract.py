# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused upper-anchor admission and ordinary-table seam checks."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/eval-author-rubric/scripts'
sys.path.insert(0,str(SCRIPTS))
import upper_anticheat_contract as upper


def test_frozen_public_expectations_preserve_optional_and_vacuous_ordering():
    import json
    public=json.loads((upper.collector.BASE/'environment/contract.json').read_text())
    valid=upper.expected('valid-predecessor',public)
    assert valid['total_credit']==8 and valid['checks']['completion_signal']==0
    empty=upper.expected('target-alone',public)
    assert empty['total_credit']==0 and empty['checks']['approval_before_effect']==1
    assert empty['checks']['approval_present']==empty['checks']['fresh_effect']==0
    invalid=upper.expected('unlisted-approver',public)
    assert invalid['checks']['approval_before_effect']==invalid['checks']['approval_present']==0


def test_missing_registry_is_not_admitted(tmp_path):
    bundle=tmp_path/'bundle';bundle.mkdir()
    (bundle/'native-index.json').write_text('{}')
    with pytest.raises(FileNotFoundError): upper.admitted(bundle,tmp_path/'registry')


def test_native_runtime_has_no_scorer_or_catalog_import():
    spec=importlib.util.find_spec('upper_anticheat_contract_runtime')
    assert spec is not None and spec.origin is not None
    source=Path(spec.origin).read_text()
    assert 'control_specs' not in source and 'refund_scorer import' not in source
    assert "json.loads(Path('/app/contract.json').read_text())" in source


def test_upper_method_gaps_do_not_claim_blindness():
    assert 'Truly blind' in upper.GAPS['task-contract.discoverable-requirements']
    assert 'not an unexposed' in upper.GAPS['AC-01']
    assert 'adjudicators' in upper.GAPS['task-contract.scoring-authority']
