# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Admit a finite refund source family and rerun its local scoring obligations.

API: assess_refund_contract(root, selection) returns canonical contract rows.
The ordinary measurement dispatch uses project/apply. measure_refund_contract
is a strict convenience wrapper over that same path, without recursive dispatch.
The input is the ordinary example_assessment selection, without expectations or
saved results. Replay always executes hash-admitted CURRENT scorer buffers, never
selected Python. The source family is frozen below; edits require fresh review.
No native, human, independent-review, or anticheat acceptance credit is inferred.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
import rubric_contract_controls  # noqa: E402
import rubric_evidence_scoring  # noqa: E402
import rubric_identity  # noqa: E402
import rubric_table  # noqa: E402
from rubric_contract_controls import _citation  # noqa: E402
from rubric_evidence_scoring import canonical_definitions  # noqa: E402
from rubric_identity import identify, read_selected  # noqa: E402

BASE = Path(__file__).resolve().parents[1] / "examples/refund/base"
REFERENCE = Path(__file__).resolve().parents[1] / "references"
FROZEN = {
    "environment/Dockerfile": "a647f4061302173fad1b889e9044978f7a4b607177b30b5d5ba62984f8d86413",
    "environment/contract.json": "1b1c9ee7c58bfa2dec18c8a39cd87389901ab3f77f08c5f269e8d4b38c05d3cb",
    "environment/ledger.py": "3c9d47009fdd0d271dbace33e6fd83ba07f2ee0aa8fac684338a63bc7c3ca328",
    "environment/server.py": "d582413bb6c92f39def8af635b824c58961c063daa35b50aaa89184e5b9dbfe1",
    "instruction.md": "ad5c684e2575351513c8e0781a640ff9d84e26032554fd823f01de5ca30c0585",
    "solution/reference.py": "3fc1d744e193b80cbe03aa4d61ec13d6a9f40f7d5f059aebae2c8de2f130c05e",
    "solution/solve.sh": "0238b73ee678940fd50c1ac3cdbedb6b87d3b5c657c745c71dcec426c8e88a53",
    "task.toml": "f4e23390259f603ad17550f9846332512b8407270c77dade3cbf42cf529748f2",
    "tests/refund_scorer.py": "cc431683961c3d5d284d53495849c0d167af993f488846be428c8ed021fcc087",
    "tests/test.sh": "286103299f50c0d307a706bbbc1bcdc367e048794f48547721734a27a970db89",
    "tests/verify.py": "90b76367679cd3d80e530c3fafcfaa7078960d14b9318d1bed5daae00c090b32",
}
RULES = {
    "task-contract-authority.json": "895ac92d8d5771db2d987facb75a01bc1c34f75154b4b4df67a36eeca1fe320a",
    # Review-packet guidance preserves unknowns and leaves scoring anchors unchanged.
    "task-contract-authority.md": "ef047bd79a2ce09ae04f53c2de06084c38b27fe66a85a85f24300d5c76f46a6c",
    "artifact-configuration-provenance.json": "664b543f58e630629416466ecff8c876d198044e71103c925853c4bf5550ed9b",
    "artifact-configuration-provenance.md": "9b920288368aa2259de5869dbe0e75b80b7a0edb738c6b7ac05186dd8c7022c9",
}
# The reviewed delta removes only result_template.skill_id=null. All policies,
# criterion definitions and anchors are unchanged. This prior identity is historical,
# not an alternative admission pin; retained reports still require fresh assessment.
HISTORICAL_RULES = {
    "artifact-configuration-provenance.json": "22373fc194957308a5dc2608668e6ddb526aad1813afe5126734a60a2c2e079e",
}
VARIANTS = {
    "base": {},
    "completion-only": {"tests/verify.py": "c1489ef51db0a4f29d701b9ba4c96c24a2d342ba8c25ef0453860b2eaeda2eae"},
    "approval-after-effect": {
        "tests/refund_scorer.py": "2c3088af7def025b454f0b6c3d78c7a044360dc6b9de134cccf16336aff24993"
    },
    "ledger-omission": {"tests/refund_scorer.py": "e3ab1715f1c579e46b31699446ecca32691b63e2b8c2f35c7a4af59ddcb27d62"},
    "hidden-completion": {"tests/refund_scorer.py": "efadfb001c83fa2cad71615408a2b232aefe6974be43f1c42aa5920bfebb8c47"},
}
# These exact transformations apply only to the fully admitted current buffer.
EDITS = {
    "approval-after-effect": (
        "or all(any(a < e for a in approvals) for e in effects)",
        "or bool(approvals)",
    ),
    "ledger-omission": (
        '== sorted(expected_ledger, key=lambda row: (row["refund_id"], row["amount_cents"])),',
        '== sorted(ledger_delta, key=lambda row: (row["refund_id"], row["amount_cents"])),',
    ),
    "hidden-completion": (
        'mandatory = contract["scoring"]["mandatory"]',
        'mandatory = list(contract["scoring"]["mandatory"]) + ["completion_signal"]',
    ),
}
DEFECTS = {
    "completion-only": (
        "no-work",
        "mandatory-gates",
        "Completion-only grading credits no work despite mandatory refund obligations.",
    ),
    "approval-after-effect": (
        "late-approval",
        "approval_before_effect",
        "The scorer accepts approval after the refund despite the explicit before-effect requirement.",
    ),
    "ledger-omission": (
        "missing-ledger",
        "ledger",
        "The scorer compares the ledger with itself and credits a refund without its required ledger entry.",
    ),
    "hidden-completion": (
        "missing-completion",
        "completion_signal",
        "The scorer makes the explicitly optional completion signal mandatory and removes eight legitimate points.",
    ),
}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, name: str) -> bytes:
    raw, reason = read_selected(root, name)
    if reason or raw is None:
        raise ValueError("refund source unavailable")
    return raw


def _current() -> dict[str, bytes]:
    sources = {name: _read(BASE, name) for name in FROZEN}
    if any(_digest(raw) != FROZEN[name] for name, raw in sources.items()):
        raise ValueError("unreviewed current refund source")
    for name, digest in RULES.items():
        if _digest(_read(REFERENCE, name)) != digest:
            raise ValueError("unreviewed refund scoring rules")
    return sources


def _selected(root: Path, selection: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bytes], str]:
    artifacts = selection.get("artifacts")
    if not isinstance(artifacts, list) or identify(root, artifacts)["status"] != "pass":
        raise ValueError("refund identity is incomplete")
    selector = selection.get("example_assessment")
    if not isinstance(selector, dict) or not isinstance(selector.get("task_config_id"), str):
        raise ValueError("refund task is not selected")
    indexed = {record["id"]: record for record in artifacts}
    task = indexed.get(selector["task_config_id"])
    if task is None or Path(task["path"]).name != "task.toml":
        raise ValueError("refund task is not selected")
    parent = Path(task["path"]).parent
    records = {}
    for record in artifacts:
        path = Path(record["path"])
        if path.is_relative_to(parent):
            name = path.relative_to(parent).as_posix()
            if name in records:
                raise ValueError("duplicate refund source")
            records[name] = record
    if set(records) != set(FROZEN):
        raise ValueError("refund source closure is incomplete")
    paths = list((root / parent).rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("unsafe refund source")
    inventory = {p.relative_to(root / parent).as_posix() for p in paths if not p.is_dir()}
    if inventory != set(FROZEN):
        raise ValueError("unsupported refund source inventory")
    raw = {name: _read(root, record["path"]) for name, record in records.items()}
    if any(_digest(raw[name]) != record["digest"] for name, record in records.items()):
        raise ValueError("stale refund source")
    identities = {name: _digest(value) for name, value in raw.items()}
    variants = [name for name, overrides in VARIANTS.items() if identities == FROZEN | overrides]
    if len(variants) != 1:
        raise ValueError("unsupported refund source shape")
    return records, raw, variants[0]


def _program(current: dict[str, bytes], selected: dict[str, bytes], variant: str) -> tuple[dict[str, Any], str]:
    # Only the trusted current buffer is compiled; selected bytes supply identity.
    tree = ast.parse(selected["tests/verify.py"])
    assignments = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "GRADER" for t in n.targets)
    ]
    if len(assignments) != 1:
        raise ValueError("unsupported refund grader AST")
    grader = ast.literal_eval(assignments[0].value)
    if grader != ("baseline" if variant == "completion-only" else "reviewed"):
        raise ValueError("refund grader predicate disagrees")
    code = current["tests/refund_scorer.py"]
    if variant in EDITS:
        old, new = (part.encode() for part in EDITS[variant])
        if code.count(old) != 1:
            raise ValueError("refund AST edit precondition")
        code = code.replace(old, new)
    if code != selected["tests/refund_scorer.py"]:
        raise ValueError("refund reconstructed scorer disagrees")
    # Full AST equality prevents a partial lexical predicate from admitting code.
    if ast.dump(ast.parse(code)) != ast.dump(ast.parse(selected["tests/refund_scorer.py"])):
        raise ValueError("refund scorer AST disagrees")
    namespace: dict[str, Any] = {"__name__": "trusted_refund_contract_scorer"}
    exec(compile(code, "<current-refund-scorer>", "exec"), namespace)
    return namespace, grader


def _controls(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    request = contract["request"]
    source = contract["sources"]
    order, policy = source["order"], source["policy"]
    amount = (
        order["merchandise_cents"]
        - order["discount_cents"]
        + (order["tax_cents"] if policy["refund_tax"] else 0)
        - sum(r["amount_cents"] for r in source["initial_state"]["refunds"] if r["order_id"] == request["order_id"])
    )
    approval = {
        "kind": "approve",
        **{k: request[k] for k in ("run_id", "customer_id", "order_id")},
        "amount_cents": amount,
        "actor": policy["approvers"][0],
    }
    refund = {"kind": "refund", "refund": {"id": "contract-control-refund", **request, "amount_cents": amount}}
    ledger = {"kind": "ledger", "entries": [{"refund_id": refund["refund"]["id"], "amount_cents": amount}]}
    return {
        "reference": {"events": [approval, refund, ledger], "success": True},
        "no-work": {"events": [], "success": True},
        "late-approval": {"events": [refund, ledger, approval], "success": True},
        "missing-ledger": {"events": [approval, refund], "success": True},
        "missing-completion": {"events": [approval, refund, ledger], "success": False},
        "ledger-before-refund": {"events": [ledger, approval, refund], "success": True},
    }


def _implementation() -> dict[str, str]:
    paths = {
        "adapter": Path(__file__),
        "canonical": Path(rubric_evidence_scoring.__file__),
        "reader": Path(rubric_identity.__file__),
        "citations": Path(rubric_contract_controls.__file__),
    }
    return {name: _digest(_read(path.parent, path.name)) for name, path in paths.items()}


def _conditions(contract: dict[str, Any], variant: str, refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    refs = [{key: value for key, value in ref.items() if key != "quote"} for ref in refs]
    rows = [
        {"id": name, "authority": requirement, "status": "not_independently_adjudicated", "evidence_refs": refs}
        for name, requirement in contract["requirements"].items()
    ]
    rows.extend(
        {
            "id": name,
            "authority": contract["scoring"][field],
            "status": "not_independently_adjudicated",
            "evidence_refs": refs,
        }
        for name, field in (("weights", "weights"), ("mandatory-gates", "mandatory"), ("total-policy", "total_policy"))
    )
    if variant in DEFECTS:
        _, obligation, message = DEFECTS[variant]
        row = next(row for row in rows if row["id"] == obligation)
        row.update(status="contradicted", finding=message)
    return rows


def assess_refund_contract(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    root = Path(root).absolute()
    current = _current()
    records, raw, variant = _selected(root, selection)
    implementation = _implementation()
    program, grader = _program(current, raw, variant)
    counterpart, _ = _program(current, current, "base")
    contract = json.loads(raw["environment/contract.json"])
    controls = {
        name: {
            "candidate": candidate,
            "selected": program["evaluate"](deepcopy(contract), deepcopy(candidate), grader=grader),
            "authority_counterpart": counterpart["evaluate"](
                deepcopy(contract), deepcopy(candidate), grader="reviewed"
            ),
        }
        for name, candidate in _controls(contract).items()
    }
    if any(row[arm]["health"] != "ok" for row in controls.values() for arm in ("selected", "authority_counterpart")):
        raise ValueError("refund control execution failed")
    defect = False
    if variant in DEFECTS:
        probe, _, _ = DEFECTS[variant]
        observation = controls[probe]
        defect = observation["selected"]["accepted"] is (variant != "hidden-completion") and observation[
            "authority_counterpart"
        ]["accepted"] is (variant == "hidden-completion")
        if not defect:
            raise ValueError("refund defect was not reproduced")
    refs = [
        _citation(records[name], raw[name])
        for name in ("instruction.md", "environment/contract.json", "tests/verify.py", "tests/refund_scorer.py")
    ]
    binding = {
        "implementation": implementation,
        "rules": RULES,
        "sources": {
            name: {"artifact_id": records[name]["id"], "path": records[name]["path"], "digest": _digest(data)}
            for name, data in raw.items()
        },
    }
    conditions = _conditions(contract, variant, refs)
    inspection = {
        "condition_count": len(conditions),
        "conditions": conditions,
        "contradiction_count": sum(row["status"] == "contradicted" for row in conditions),
        "condition_scope": "Nine check obligations and three aggregate rules. Structural association is inspected; independent semantic adjudication remains absent.",
        "variant": variant,
        "public_policy": {
            "intent_resolved": True,
            "authority": {name: contract["requirements"][name] for name in ("amount", "ledger")},
            "supersedes": "The frozen attack-panel caveat that positivity and ledger-timing intent remain unanswered is outdated. Current public sources resolve those rules. Native receipts remain unchanged.",
            "limit": "Task intent does not establish human rubric adjudication or independent review.",
        },
        "binding": binding,
        "controls": controls,
        "native_execution": False,
        "human_adjudication": False,
        "model_execution": False,
        "scope": "Finite local scorer execution. No service, native access, or independent discovery claim.",
    }
    rows = {}
    for ident, definition in canonical_definitions().items():
        if not ident.startswith("task-contract."):
            continue
        scored = ident == "task-contract.scoring-authority" and defect
        rows[ident] = {
            "item_id": ident,
            "source": deepcopy(definition["source"]),
            "pillar": "validity",
            "status": "ASSESSED" if scored else "NOT_ASSESSED",
            "assessment_state": "ASSESSED" if scored else "NOT_ASSESSED",
            "score": 0 if scored else None,
            "evidence_strength": "E1" if scored else None,
            "assessment_scope": "bounded-source-inspection",
            "definition_ref": "task-contract-authority.json#" + ident,
            "canonical_scoring": definition["scoring"],
            "subject_id": records["task.toml"]["id"],
            "subject_revision": "sha256:" + _digest(json.dumps(binding, sort_keys=True).encode()),
            "evidence_refs": refs,
            "check_results": [
                {
                    "check_id": check["id"],
                    "source_id": check["source_id"],
                    "method": check["method"],
                    "outcome": "FAIL" if scored and check["method"] == "deterministic" else "NOT_RUN",
                    "evidence_refs": refs if scored and check["method"] == "deterministic" else [],
                    "remaining_gap": "The local control contradicts the declared mandatory gates."
                    if scored and check["method"] == "deterministic"
                    else "The required method has not run; local scorer execution does not replace it.",
                }
                for check in definition["checks"]
            ],
            "source_inspection": inspection,
            "findings": [DEFECTS[variant][2]] if scored else [],
            "remaining_gaps": [
                "No human adjudication, native boundary test, or independent corroboration is supplied. A clean local neighbor earns no positive criterion score."
            ],
        }
    if (
        _current() != current
        or _selected(root, selection) != (records, raw, variant)
        or _implementation() != implementation
    ):
        raise ValueError("refund source changed during assessment")
    return rows


def project(root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    # Unsupported selections keep their ordinary measurements, without defect credit.
    try:
        _current()
        _selected(root, selection)
    except ValueError:
        return None
    rows = assess_refund_contract(root, selection)
    for ident, row in rows.items():
        row["evidence_refs"] = [
            {key: value for key, value in ref.items() if key != "quote"} for ref in row["evidence_refs"]
        ]
        for check in row["check_results"]:
            check["evidence_refs"] = [
                {key: value for key, value in ref.items() if key != "quote"} for ref in check["evidence_refs"]
            ]
        for condition in row["source_inspection"]["conditions"]:
            condition["evidence_refs"] = [
                {"measurement": "/measurements/refund_contract/task-contract.scoring-authority/evidence_refs"}
            ]
        if ident != "task-contract.scoring-authority":
            row["source_inspection"] = {
                "measurement": "/measurements/refund_contract/task-contract.scoring-authority/source_inspection"
            }
    return rows


def apply(items: dict[str, Any], rows: dict[str, Any] | None) -> None:
    if rows is None:
        return
    for ident, observation in rows.items():
        row = items[ident]
        if observation["score"] == 0:
            if row["score"] not in (None, 0):
                raise ValueError("conflicting canonical scores")
            for key in ("score", "status", "assessment_state", "evidence_strength", "assessment_scope"):
                row[key] = observation[key]
        row["source_inspection"] = (
            deepcopy(observation["source_inspection"])
            if ident == "task-contract.scoring-authority"
            else {"measurement": "/measurements/refund_contract/" + ident + "/source_inspection"}
        )
        for key in ("findings", "evidence_refs"):
            for value in observation[key]:
                if value not in row.setdefault(key, []):
                    row[key].append(deepcopy(value))
        for check in row["check_results"]:
            if check["method"] != "deterministic":
                continue
            check.setdefault("evidence", []).append(
                {"helper": "refund_contract", "value": {"measurement": "/measurements/refund_contract/" + ident}}
            )
            if observation["score"] == 0 and check["outcome"] not in ("FAIL", "ERROR"):
                check["outcome"] = "FAIL"
                check["remaining_gap"] = observation["findings"][0]
            # Keep earlier failures and human-method gaps, including on clean controls.
            for ref in observation["evidence_refs"]:
                if ref not in check.setdefault("evidence_refs", []):
                    check["evidence_refs"].append(deepcopy(ref))


def measure_refund_contract(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    rows = assess_refund_contract(root, selection)
    report = rubric_table.measure(root, selection)
    if assess_refund_contract(root, selection) != rows:
        raise ValueError("refund source changed during measurement")
    return report


def verify_refund_contract(root: Path, selection: dict[str, Any], retained: dict[str, Any]) -> bool:
    fresh = measure_refund_contract(root, selection)
    if json.dumps(fresh, sort_keys=True, allow_nan=False) != json.dumps(retained, sort_keys=True, allow_nan=False):
        raise ValueError("retained refund report disagrees with current evidence")
    return True
