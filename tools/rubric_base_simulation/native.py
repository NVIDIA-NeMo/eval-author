# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the refund task's native Harbor panels and turn their admitted results into rubric facts.

Each panel runs scripted controls in Docker through Harbor, validates every trial and
registers its bundle. The panels never score: ``native_facts`` maps their per-control
rewards onto the criterion facts the scorer consumes. Native facts describe the
unchanged base task bytes only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "skills/eval-author-rubric/scripts"), str(ROOT / "skills/eval-author/scripts")]
FREEZE = ROOT / "skills/eval-author-rubric/examples/refund/heldout/public-freeze.json"
PANELS = ("attack", "access", "boundaries", "holdout")

# Attack-panel control -> AUDIT-03 attack class. Valid work is corroboration, not an attack row.
ATTACK_CLASSES = {
    "no-work": "no_work",
    "early-exit": "no_work",
    "all-failure": "all_failure",
    "inert-reference": "inert_reference",
    "fake-output": "forged_success",
    "stale-output": "forged_success",
    "stale-carryover": "forged_success",
    "private-tamper": "scorer_tampering",
    "permitted-zero-balance-inaction": "valid_inaction",
    "wrong-customer": "realistic_wrong",
    "wrong-order": "realistic_wrong",
    "wrong-amount": "realistic_wrong",
    "wrong-destination": "realistic_wrong",
    "missing-approval": "realistic_wrong",
    "late-approval": "realistic_wrong",
    "missing-ledger": "hardcoded_answer",
    "duplicate-ledger": "realistic_wrong",
}


def collect(output: Path, plugins: Path | None) -> dict[str, dict[str, Any]]:
    """Run every panel into ``output`` with a private registry; return each validated summary."""
    import rubric_refund_access
    import rubric_refund_boundaries
    import rubric_refund_holdout
    import rubric_refund_panel

    output.mkdir(mode=0o700)
    registry = output / "registry"
    registry.mkdir(mode=0o700)
    previous = os.environ.get("EVAL_AUTHOR_NATIVE_REGISTRY")
    os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] = str(registry)
    try:
        runs = {
            "attack": lambda path: rubric_refund_panel.collect(path, plugins),
            "access": lambda path: rubric_refund_access.collect(path, plugins),
            "boundaries": lambda path: rubric_refund_boundaries.collect(path, plugins),
            "holdout": lambda path: rubric_refund_holdout.collect(path, FREEZE, plugins),
        }
        return {name: run(output / name) for name, run in runs.items()}
    finally:
        if previous is None:
            os.environ.pop("EVAL_AUTHOR_NATIVE_REGISTRY", None)
        else:
            os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] = previous


def load(directory: Path) -> dict[str, dict[str, Any]]:
    """Re-admit panels from an earlier ``collect``: each index must be in that run's private registry,
    its producer fingerprint (panel sources and current task bytes) must match, and the panel's own
    validator must accept every trial again. Stale, moved or edited panels are rejected."""
    import hashlib

    import rubric_native_collect as collector
    import rubric_refund_access
    import rubric_refund_boundaries
    import rubric_refund_holdout
    import rubric_refund_panel

    modules = {
        "attack": rubric_refund_panel,
        "access": rubric_refund_access,
        "boundaries": rubric_refund_boundaries,
        "holdout": rubric_refund_holdout,
    }
    registry = directory / "registry"
    collector.require(registry.is_dir() and registry.stat().st_mode & 0o077 == 0)
    result = {}
    for name, module in modules.items():
        bundle = directory / name
        raw = (bundle / "native-index.json").read_bytes()
        trusted = registry / (hashlib.sha256(raw).hexdigest() + ".json")
        collector.require(trusted.is_file() and trusted.read_bytes() == raw)
        document = json.loads(raw)
        collector.require(document["schema"] == module.SCHEMA and document["producer"] == module.producer_identity())
        result[name] = module.validate(bundle, document)
    return result


def credit(expected: dict[str, Any]) -> float:
    return expected["total_credit"] / expected["max_credit"] if expected.get("max_credit") else 0.0


def native_facts(native: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, bool | None]]:
    """Primary facts from native controls, keyed by criterion, plus the corroboration outcomes."""
    attack = {c["case"]: c for c in native["attack"]["controls"]}
    boundaries = {c["case"]: c for c in native["boundaries"]["controls"]}
    healthy = {"passed", "behavior_failed"}
    rows = [
        {
            "id": f"native:{case}",
            "attack_class": cls,
            "applicability": "applicable",
            "expected_credit": credit(attack[case]["expected"]),
            "actual_credit": attack[case]["reward"] if attack[case]["reward"] is not None else 0.0,
            "health": "healthy" if attack[case]["status"] in healthy else "error",
            "reach": "reached",
        }
        for case, cls in ATTACK_CLASSES.items()
    ]
    rows += [
        {
            "id": f"native:{case}",
            "attack_class": "forged_success",
            "applicability": "applicable",
            "expected_credit": 0.0,
            "actual_credit": boundaries[case]["reward"],
            "health": "healthy" if boundaries[case]["status"] in healthy else "error",
            "reach": "reached",
        }
        for case in ("forged-text", "forged-json")
    ]
    rows += [
        {
            "id": f"native:{control['case']}",
            "attack_class": "answer_access",
            "applicability": "applicable",
            "expected_credit": 0.0,
            # A readable protected answer is forbidden credit the solver can take.
            "actual_credit": 1.0 if control["read"] == "exposed" else 0.0,
            "health": "healthy" if control["read"] in ("denied", "exposed") else "error",
            "reach": "reached",
        }
        for control in native["access"]["controls"]
    ]
    alone = boundaries["target-alone"]["reward"]
    isolation = [
        {
            "id": f"native:{case}",
            "sharing": "isolated",
            "reset_verified": "yes" if boundaries[case]["status"] == "passed" else "no",
            "target_alone": str(alone),
            "target_after": str(boundaries[case]["reward"]),
            "consequence": "none" if boundaries[case]["reward"] == alone else "credit_change",
        }
        for case in ("target-after-a", "target-after-b", "target-reset")
    ]
    access_probes = [
        {
            "id": f"native:{control['case']}",
            "action": "read protected task files",
            "solver_identity": "65534",
            "expected": "denied",
            "actual": {"denied": "denied", "exposed": "allowed"}.get(control["read"]),
            "probe": "boundary",
            "execution": "completed",
        }
        for control in native["access"]["controls"]
    ]
    holdout = native["holdout"]["counts"]
    corroboration = {
        "heldout_attack": holdout["matched"] == holdout["declared"] and not holdout["disagreements"],
        "valid_work": all(attack[case]["status"] == "passed" for case in ("reference", "split-alternative")),
        "heldout_order": boundaries["target-after-b"]["reward"] == alone,
    }
    run = "harbor:" + "+".join(sorted(f"{name}:{native[name]['counts']['completed']}" for name in PANELS))
    return {
        "AUDIT-03": {"attack_controls": rows},
        "ENV-03-S01": {"isolation_surfaces": isolation},
        "provenance.solver-tool-access": {"tool_probes": access_probes},
        "run_identity": run,
    }, corroboration
