# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from generation_benchmark.eval_author.proof import _closure_evidence_paths, _revision_manifests_with_ledgers
from generation_benchmark.eval_author.ports.errors import ExperimentError


def test_revision_manifests_with_ledgers_skips_orphan_manifests(tmp_path: Path) -> None:
    measurements = tmp_path / ".eval-author" / "task-measurements" / "cover-tool-1"
    measurements.mkdir(parents=True)
    (measurements / "revision-1.json").write_text(
        json.dumps({"attempts": str((measurements / "revision-1.json.attempts").resolve())}),
        encoding="utf-8",
    )
    ledger = measurements / "revision-1.json.attempts"
    ledger.mkdir()
    (measurements / "revision-2.json").write_text(
        json.dumps({"attempts": str((measurements / "revision-2.json.attempts").resolve())}),
        encoding="utf-8",
    )
    assert _revision_manifests_with_ledgers(measurements) == [measurements / "revision-1.json"]

    receipt = measurements / "reference-worker.json"
    receipt.write_text("{}", encoding="utf-8")
    entry = ledger / ("a" * 64 + ".json")
    entry.write_text(
        json.dumps({"purpose": "closure", "receipt": str(receipt)}),
        encoding="utf-8",
    )
    paths = _closure_evidence_paths(tmp_path, "cover-tool-1")
    assert paths == [receipt.resolve()]
