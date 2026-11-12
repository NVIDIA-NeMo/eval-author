# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/eval-author/scripts/taste_feature.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("taste_feature", SCRIPT)
assert spec is not None and spec.loader is not None
feature = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feature)


def test_flag_defaults_off_and_requires_explicit_approval(tmp_path, capsys):
    run = tmp_path / "run"
    assert feature.status(run)["enabled"] is False
    assert not run.exists()
    with pytest.raises(SystemExit) as error:
        feature.main(["enable", "--run-dir", str(run)])
    assert error.value.code == 2
    assert not run.exists()
    assert feature.main(["enable", "--run-dir", str(run), "--approval", "Use TASTE for this cohort"]) == 0
    assert feature.status(run)["enabled"] is True
    artifact = run / "retained.json"
    artifact.write_text("retained")
    assert feature.main(["disable", "--run-dir", str(run)]) == 0
    assert feature.status(run)["enabled"] is False
    assert feature.status(run)["approval"] == "Use TASTE for this cohort"
    assert artifact.read_text() == "retained"
    assert feature.status(tmp_path / "other")["enabled"] is False


def test_flag_cannot_be_reused_in_another_run_or_with_malformed_state(tmp_path):
    run = tmp_path / "run"
    feature.main(["enable", "--run-dir", str(run), "--approval", "Use TASTE"])
    other = tmp_path / "other"
    shutil.copytree(run, other)
    with pytest.raises(ValueError, match="different run"):
        feature.status(other)
    value = feature.status(run)
    value["enabled"] = "true"
    (run / "integration.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="malformed"):
        feature.status(run)
