# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    sys.path.insert(0, str(ROOT / "tools"))
    import rubric_demo_common

    return rubric_demo_common


def test_shared_artifact_preserves_selected_digest_and_provenance(tmp_path: Path) -> None:
    common = helper()
    (tmp_path / "input.json").write_text("{}\n")
    actual = hashlib.sha256(b"{}\n").hexdigest()
    record = common.artifact_record(
        tmp_path,
        "input",
        "input.json",
        "task",
        selected_digest="0" * 64,
        revision=None,
        source="local-demo-fixture",
    )
    assert record == {
        "id": "input",
        "path": "input.json",
        "role": "task",
        "digest": "0" * 64,
        "revision": None,
        "access": "available",
        "source": "local-demo-fixture",
        "selection_origin": "explicit",
        "evidence": [{"path": "input.json", "digest": actual}],
    }


@pytest.mark.parametrize("record_invocation", [False, True])
def test_shared_measure_preserves_log_and_replay_modes(tmp_path: Path, record_invocation: bool) -> None:
    common = helper()
    (tmp_path / "selection.json").write_text('{"artifacts": []}\n')
    report, code = common.run_measure(tmp_path, prefix="baseline", record_invocation=record_invocation)
    assert code == 1
    assert report["schema"] == "rubric-measurements-v3"
    retained = tmp_path / "baseline-report.json"
    assert json.loads(retained.read_text()) == report
    before = retained.read_bytes()
    replay, replay_code = common.run_measure(
        tmp_path,
        prefix="baseline",
        verify=True,
        record_invocation=record_invocation,
    )
    assert (replay, replay_code) == (report, code)
    assert retained.read_bytes() == before
    for log in ("baseline", "baseline-verify"):
        assert (tmp_path / f"{log}.stdout.json").is_file()
        assert (tmp_path / f"{log}.stderr.txt").is_file()
        assert (tmp_path / f"{log}-invocation.json").exists() is record_invocation


def test_shared_measure_rejects_invalid_cli_output_with_retained_logs(tmp_path: Path) -> None:
    common = helper()
    (tmp_path / "selection.json").write_text('{"unsupported": true}\n')
    with pytest.raises(ValueError, match="caller-specific error"):
        common.run_measure(tmp_path, record_invocation=True, error_message="caller-specific error")
    assert not (tmp_path / "measure-report.json").exists()
    assert json.loads((tmp_path / "measure-invocation.json").read_text())["returncode"] == 2
