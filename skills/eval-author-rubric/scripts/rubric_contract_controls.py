# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inspect only the frozen order-total planted/clean source pair; never execute it.

Selection: ``contract_controls = {"artifact_id": "manifest"}``. The selected
manifest is strict JSON with exactly this schema (values are selected IDs):

    {"schema": "rubric-contract-controls-v1",
     "instruction_id": "instruction", "dockerfile_id": "dockerfile",
     "scorer_id": "scorer", "test_data_id": "orders",
     "task_config_id": "task-config"}

All six artifacts require ordinary explicit artifact records with available
access and exact SHA-256 digests. IDs and paths must be distinct. task_config_id
binds the no-extra-environment/authority configuration as well as the four
required source types. Paths may be relocated: IDs, not basenames, select bytes.
The selected data must remain orders.csv beside the selected Dockerfile; its
parent is the only supported build context (original task or flat demo layout).
No caller-supplied semantic findings, inventory, score, or source hashes in the
manifest are accepted. Whole-source frozen hashes, not substring/regex matches
or self-resealed caller hashes, admit the two reviewed scorer shapes. The
Dockerfile's ENTIRE COPY inventory and public instruction are frozen too.

Return selected CREATE-03/04/05-A rows. Only the planted hidden summary yields
CREATE-04=0/E1; the clean neighbor and other criteria remain NOT_ASSESSED. This
is finite static source inspection, not a general entailment analyzer, native
access test, authenticated human review, or measured model judgment. The image
is not executed/inspected; this proves the selected recipe's declared task
surface, not the contents/behavior of a deployed container. Unsupported,
missing, stale, unsafe or changing sources raise ValueError before any score.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

import rubric_identity
from eval_author_shared import task_evidence
from eval_author_shared.task_evidence import loads
from rubric_identity import identify, read_selected

FIELDS = ("instruction_id", "dockerfile_id", "scorer_id", "test_data_id", "task_config_id")
FROZEN = {
    "instruction_id": "955f0801cbda8210e1b026729d5b0c50c9915a21fbf18c3f88ec4f5c2a8de6c6",
    "dockerfile_id": "5a331bdd8a43ae46fe2b22fea0261373ddebcc61d09164719273b03b74996635",
    "test_data_id": "a1371d0a97cec243e3b0f6bbfe73e3e1e7958dc76979dc5226be8c20df7630d8",
    "task_config_id": "5650748960792ba873c04c79332ffee021ecaf221124d5635ce01cb4420efec7",
}
SCORERS = {
    "1a3e89aebead3531b2d5f03772cebd84a599f36eab3ca5cf02b1796b15d58970": "planted-summary",
    "9c1bc98c7afff989524c0a6268ac99a7d7a5a76bfd95632d9ecd44426efa3cf7": "clean-neighbor",
}
REFERENCE = Path(__file__).resolve().parents[1] / "references/task-contract-authority.json"


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read(root, record):
    raw, reason = read_selected(root, record["path"])
    if reason or raw is None or _digest(raw) != record["digest"]:
        raise ValueError("contract source unavailable or stale")
    return raw


def _citation(record, raw, start=1, end=None):
    lines = raw.decode("utf-8").splitlines()
    end = len(lines) if end is None else end
    return {
        "artifact_id": record["id"],
        "path": record["path"],
        "digest": _digest(raw),
        "start_line": start,
        "end_line": end,
        "quote": "\n".join(lines[start - 1 : end]),
    }


def _code_binding():
    # Trusted current implementation closure, never import/execute retained code.
    paths = {
        "adapter": Path(__file__),
        "reader": Path(rubric_identity.__file__),
        "json_loader": Path(task_evidence.__file__),
        "definitions": REFERENCE,
    }
    result = {}
    for name, path in paths.items():
        raw, reason = read_selected(path.parent, path.name)
        if reason or raw is None:
            raise ValueError("contract implementation unavailable")
        result[name] = _digest(raw)
    return result


def assess_contract_controls(root, selection):
    """Return source-cited criterion rows or fail closed; see module schema."""
    if not isinstance(selection, dict):
        raise ValueError("invalid contract selection")
    if "contract_controls" not in selection:
        return {}
    selector = selection["contract_controls"]
    if (
        not isinstance(selector, dict)
        or set(selector) != {"artifact_id"}
        or not isinstance(selector["artifact_id"], str)
    ):
        raise ValueError("invalid contract selector")
    root = Path(root).absolute()  # Do not resolve away a symlink before no-follow reads.
    artifacts = selection.get("artifacts")
    if not isinstance(artifacts, list) or identify(root, artifacts)["status"] != "pass":
        raise ValueError("contract identity is incomplete")
    indexed = {record["id"]: record for record in artifacts}
    if selector["artifact_id"] not in indexed:
        raise ValueError("contract manifest is not selected")
    manifest_record = indexed[selector["artifact_id"]]
    manifest_raw = _read(root, manifest_record)
    manifest = loads(manifest_raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", *FIELDS}
        or manifest["schema"] != "rubric-contract-controls-v1"
    ):
        raise ValueError("invalid contract manifest")
    ids = [selector["artifact_id"], *(manifest[field] for field in FIELDS)]
    if any(not isinstance(ident, str) or ident not in indexed for ident in ids) or len(set(ids)) != len(ids):
        raise ValueError("contract sources must be explicitly selected and distinct")
    if len({indexed[ident]["path"] for ident in ids}) != len(ids):
        raise ValueError("contract source paths must be distinct")
    records = {field: indexed[manifest[field]] for field in FIELDS}
    raw = {field: _read(root, record) for field, record in records.items()}
    for field, digest in FROZEN.items():
        if _digest(raw[field]) != digest:
            raise ValueError("unsupported contract source shape")
    variant = SCORERS.get(_digest(raw["scorer_id"]))
    if variant is None:
        raise ValueError("unsupported contract scorer shape")
    implementation = _code_binding()
    reference_raw, reason = read_selected(REFERENCE.parent, REFERENCE.name)
    if reason or reference_raw is None or _digest(reference_raw) != implementation["definitions"]:
        raise ValueError("contract definitions changed")
    definitions = loads(reference_raw)["items"]

    # These AST operations are valid ONLY after whole shell+Python source admission.
    # No retained code execution, partial syntax recognizer, or arbitrary entailment.
    scorer = raw["scorer_id"].decode()
    program = scorer.split("python - <<'CHECK'\n", 1)[1].rsplit("\nCHECK\n", 1)[0]
    tree = ast.parse(program)
    body = tree.body[3].body  # frozen try block
    comparison = body[-1].value
    planted = isinstance(comparison, ast.BoolOp)
    total_compare = comparison.values[0] if planted else comparison
    expected_total = ast.literal_eval(total_compare.comparators[0])
    summary_expected = ast.literal_eval(comparison.values[1].comparators[0]) if planted else None
    output_paths = [
        ast.literal_eval(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path"
    ]
    expected_paths = {"/workspace/total.txt", "/logs/verifier/reward.txt"}
    if planted:
        expected_paths.add("/workspace/summary.json")
    if set(output_paths) != expected_paths or planted != (variant == "planted-summary"):
        raise ValueError("contract scoring inventory disagrees")
    data_rows = list(csv.DictReader(io.StringIO(raw["test_data_id"].decode())))
    fixture_total = format(sum((Decimal(row["amount"]) for row in data_rows), Decimal(0)), ".2f")
    if expected_total != fixture_total:
        raise ValueError("contract total authority disagrees")
    docker_lines = raw["dockerfile_id"].decode().splitlines()
    copies = [line.split() for line in docker_lines if line.startswith("COPY ")]
    if copies != [["COPY", "orders.csv", "/workspace/orders.csv"]]:
        raise ValueError("contract COPY inventory disagrees")
    # Supported build context is the selected Dockerfile's parent, both for the
    # original task layout and the demo's flat retained source layout.
    copied_path = (Path(records["dockerfile_id"]["path"]).parent / copies[0][1]).as_posix()
    if records["test_data_id"]["path"] != copied_path:
        raise ValueError("contract COPY source is not the selected data")
    refs = {field: _citation(records[field], data) for field, data in raw.items()}
    conditions = [
        {
            "id": "total-output",
            "authority": "public instruction",
            "status": "supported",
            "evidence_refs": [refs["instruction_id"], refs["scorer_id"]],
        },
        {
            "id": "total-value",
            "authority": "public instruction and exact Decimal sum",
            "status": "supported",
            "evidence_refs": [refs["instruction_id"], refs["test_data_id"], refs["scorer_id"]],
        },
    ]
    if planted:
        conditions.extend(
            [
                {
                    "id": "summary-output",
                    "authority": None,
                    "status": "unsupported",
                    "evidence_refs": [refs["instruction_id"], refs["dockerfile_id"], refs["scorer_id"]],
                },
                {
                    "id": "summary-value",
                    "authority": None,
                    "status": "unsupported",
                    "evidence_refs": [refs["instruction_id"], refs["test_data_id"], refs["scorer_id"]],
                },
            ]
        )
    conditions.append(
        {"id": "binary-reward", "authority": None, "status": "unadjudicated", "evidence_refs": [refs["scorer_id"]]}
    )
    binding = {
        "implementation": implementation,
        "manifest": {
            "artifact_id": manifest_record["id"],
            "path": manifest_record["path"],
            "digest": _digest(manifest_raw),
        },
        "sources": {
            field: {"artifact_id": record["id"], "path": record["path"], "digest": _digest(raw[field])}
            for field, record in records.items()
        },
    }
    revision = "sha256:" + _digest(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode())
    inspection = {
        "variant": variant,
        "binding": binding,
        "condition_count": len(conditions),
        "conditions": conditions,
        "hidden_requirement": "summary-output-and-value" if planted else None,
        "solver_visible_inventory": [
            {"artifact_id": manifest["instruction_id"], "destination": "public instruction"},
            {"artifact_id": manifest["test_data_id"], "destination": copies[0][2]},
        ],
        "inventory_scope": "Complete declared task COPY inventory plus public instruction; not a native image inspection.",
        "expected_total": expected_total,
        "fixture_total": fixture_total,
        "summary_expected": summary_expected,
        "reward_rule": "binary-all-or-nothing",
        "native_execution": False,
        "model_execution": False,
        "human_adjudication": False,
        "decision_basis": (
            "Frozen full instruction specifies total.txt only; frozen data has only order_id/amount; "
            "the only COPY exposes orders.csv. The scorer makes undeclared summary.json with USD "
            "and order_count mandatory. This finite reviewed source mapping does not generalize."
            if planted
            else "The frozen clean scorer has no summary requirement; absence of this one defect does not "
            "establish a complete authority mapping or independently justified scoring semantics."
        ),
    }
    rows = {}
    for definition in definitions:
        ident = definition["id"]
        scored = ident == "CREATE-04" and planted
        gaps = [
            "No native solver-scope access or blind discovery probe was run.",
            "No human adjudication or model judgment was performed.",
        ]
        if ident == "CREATE-03":
            gaps.append(
                "Independent contract interpretations and boundary outcomes are unassessed; hidden-summary evidence is not an automatic CREATE-03 score."
            )
        elif ident == "CREATE-05-A":
            gaps.append(
                "Correctness and scoring semantics require separate authority review; no automatic CREATE-05-A score."
            )
        elif not planted:
            gaps.append(
                "Removing the hidden summary is not proof of complete legitimate authority; scoring semantics and independent discovery remain unassessed."
            )
        checks = []
        for check in definition["checks"]:
            deterministic = ident == "CREATE-04" and check["method"] == "deterministic"
            checks.append(
                {
                    "check_id": check["id"],
                    "method": check["method"],
                    "outcome": "PASS" if deterministic else "NOT_RUN",
                    "evidence_refs": list(refs.values()) if deterministic else [],
                    "remaining_gap": None
                    if deterministic
                    else "Required review/probe not run; static source inspection is not that method.",
                }
            )
        rows[ident] = {
            "item_id": ident,
            "pillar": "validity",
            "status": "ASSESSED" if scored else "NOT_ASSESSED",
            "assessment_state": "ASSESSED" if scored else "NOT_ASSESSED",
            "score": 0 if scored else None,
            "evidence_strength": "E1" if scored else None,
            "assessment_scope": "bounded-source-inspection",
            "definition_ref": "task-contract-authority.json#" + ident,
            "canonical_scoring": definition["scoring"],
            "subject_id": manifest_record["id"],
            "subject_revision": revision,
            "evidence_refs": list(refs.values()),
            "check_results": checks,
            "source_inspection": inspection,
            "remaining_gaps": gaps,
        }
    # Re-read the exact selected closure and code after projection, never cached summaries.
    if _read(root, manifest_record) != manifest_raw or any(
        _read(root, records[field]) != raw[field] for field in FIELDS
    ):
        raise ValueError("contract source changed during inspection")
    if _code_binding() != implementation or identify(root, artifacts)["status"] != "pass":
        raise ValueError("contract binding changed during inspection")
    return rows
