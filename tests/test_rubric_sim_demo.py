# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tools/rubric_sim_demo"
sys.path.insert(0, str(DEMO))

import checks  # noqa: E402  # ty: ignore[unresolved-import]


def _canonical_ids() -> list[str]:
    refs = ROOT / "skills/eval-author-rubric/references"
    ids = []
    for name in ("leakage-and-credit", "task-contract-authority", "artifact-configuration-provenance"):
        ids += [item["id"] for item in json.loads((refs / f"{name}.json").read_text())["items"]]
    return ids


def test_each_canonical_row_has_exactly_one_check():
    assert sorted(c.criterion for c in checks.CHECKS) == sorted(_canonical_ids())


def test_controls_are_partitioned_without_overlap():
    owned = [cid for ids in checks.PARTITION.values() for cid in ids]
    assert len(owned) == len(set(owned))
    controls = json.loads((ROOT / "skills/eval-author-rubric/examples/refund/controls.json").read_text())["cases"]
    assert set(owned) == {row["id"] for row in controls}


def test_base_hits_target_and_each_mutation_drops_its_row(tmp_path):
    cases = [
        "base",
        "approval-after-effect",
        "trace-secret",
        "answer-exposure",
        "unpinned-image",
        "invalid-manifest",
        "stale-evidence",
        "no-independent-review",
    ]
    done = subprocess.run(
        [
            sys.executable,
            "-B",
            str(DEMO / "run_demo.py"),
            "--output",
            str(tmp_path),
            "--runs",
            "1",
            "--offline",
            "--only",
            *cases,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    summary = json.loads((tmp_path / "summary.json").read_text())
    base = dict(zip(summary["criteria"], summary["scores"]["base"]))
    assert base.pop("AUDIT-04-B") == "N/A"
    assert set(base.values()) == {3}
    dropped = {case: set(p["dropped"]) for case, p in summary["preference"].items()}
    assert dropped["approval-after-effect"] == {"AUDIT-03", "AUDIT-04-B"}
    assert dropped["trace-secret"] == {"TRACE-04-A"}
    assert dropped["answer-exposure"] == {"AUDIT-05-B"}
    assert dropped["unpinned-image"] == {"provenance.declared-configuration"}
    assert dropped["invalid-manifest"] == {"provenance.runtime-route"}
    assert dropped["stale-evidence"] == {"provenance.evidence-revision-consistency"}
    assert all(
        v <= 2
        for k, v in zip(summary["criteria"], summary["scores"]["no-independent-review"])
        if k in {"AUDIT-03", "CREATE-02", "task-contract.discoverable-requirements"}
    )
