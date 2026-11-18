# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# SPDX-License-Identifier: Apache-2.0
"""Finite source/evidence subjects, not selected verdicts or synthetic score vectors.

Only byte-for-byte reconstruction from trusted current source is admitted. Reuse
refund_contract's current scorer, bounded candidates, definitions and citations.
No retained Python is executed and no base/native/owner packet is rewritten.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import rubric_refund_contract_evidence as source
from rubric_identity import identify, read_selected

TASK = "task/task.toml"
SUBJECT = "semantic-subject.json"


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


CASES = (
    "contradictory-contract",
    "ambiguous-contract",
    "hidden-authority",
    "disputed-authority",
    "disputed-scoring",
    "combined-revisions",
    "partial-revisions",
    "untested-comparison",
    "advantaged-comparison",
)


def _family(case):
    current = source._current()
    sources = dict(current)
    if case == "contradictory-contract":
        sources["instruction.md"] += (
            b"\nBinding amendment: Do not create any refund effect. Full credit requires zero refund effects. This amendment and all prior mandatory requirements apply simultaneously.\n"
        )
    elif case in ("ambiguous-contract", "disputed-authority", "disputed-scoring"):
        sources["instruction.md"] += (
            b"\nUnresolved policy amendment: the completion signal may be a mandatory acceptance gate rather than optional credit. The two interpretations remain open; neither takes precedence until the owner resolves this amendment. Other refund requirements remain binding.\n"
        )
        sources["instruction.md"] += ("Review scope: " + case + ".\n").encode()
    elif case == "hidden-authority":
        old, new = (s.encode() for s in source.EDITS["hidden-completion"])
        sources["tests/refund_scorer.py"] = sources["tests/refund_scorer.py"].replace(old, new)
        sources["instruction.md"] = sources["instruction.md"].replace(
            b"Verifier source is public and readable when uploaded by the harness. It is\nintegrity-protected: you may inspect it but must not change it.",
            b"Allowed authority is exclusively this instruction, /app/contract.json and the documented socket. Reading verifier source, /tests, image layers or private grading files is forbidden; these are not allowed authority.",
        )
    elif case in ("combined-revisions", "partial-revisions", "untested-comparison", "advantaged-comparison"):
        sources[SUBJECT] = _json(_subject(case, current))
    elif case != "repair":
        raise ValueError("unsupported semantic subject")
    return sources


def _subject(case, current):
    program, _ = source._program(current, current, "base")
    contract = json.loads(current["environment/contract.json"])
    candidate = source._controls(contract)["no-work"]
    if case in ("combined-revisions", "partial-revisions"):
        before = {name: source._digest(raw) for name, raw in current.items()}
        changed = deepcopy(contract)
        changed["sources"]["policy"]["refund_tax"] = False
        after = dict(before, **{"environment/contract.json": source._digest(_json(changed))})
        sample = source._controls(contract)["reference"]
        old = program["evaluate"](contract, sample, grader="reviewed")
        new = program["evaluate"](changed, sample, grader="reviewed")
        records = [
            {"id": "prior", "bindings": before, "candidate": sample, "result": old},
            {"id": "current", "bindings": after, "candidate": sample, "result": new},
        ]
        if case == "partial-revisions":
            del records[0]["bindings"]["tests/refund_scorer.py"]
        return {
            "schema": "refund-assessment-subject-v1",
            "kind": "revision-records",
            "contracts": {"prior": contract, "current": changed},
            "records": records,
            "published": {
                "basis": "current",
                "result_ids": ["prior", "current"] if case == "combined-revisions" else ["current"],
                "invalidations": [],
                "retained_reviews": ["prior"],
            },
        }
    return {
        "schema": "refund-assessment-subject-v1",
        "kind": "comparison",
        "claim": "Compare system A and system B refund correctness on the same task.",
        "systems": [
            {"id": "system-A", "model_slot": "A", "grader": "baseline"},
            {"id": "system-B", "model_slot": "B", "grader": "reviewed"},
        ],
        "candidate": candidate,
        "justifications": [],
        "paired_controls_requested": case == "advantaged-comparison",
    }


def materialize(root, case):
    import rubric_table

    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, raw in _family(case).items():
        path = root / "task" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(source.BASE.joinpath(name).stat().st_mode & 0o777 if name in source.FROZEN else 0o600)
    return rubric_table.select_task(root, TASK)


def _admit(root, selection):
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("unsafe semantic root")
    if identify(root, selection.get("artifacts", []))["status"] != "pass":
        return None
    indexed = {r["id"]: r for r in selection["artifacts"]}
    task = indexed.get(selection.get("example_assessment", {}).get("task_config_id"))
    if task is None:
        return None
    parent = Path(task["path"]).parent
    records = {
        Path(r["path"]).relative_to(parent).as_posix(): r
        for r in indexed.values()
        if Path(r["path"]).is_relative_to(parent)
    }
    observed = {}
    for name, record in records.items():
        raw, reason = read_selected(root, record["path"])
        if reason or raw is None or source._digest(raw) != record["digest"]:
            raise ValueError("semantic source changed")
        observed[name] = raw
    if len(records) != len([r for r in indexed.values() if Path(r["path"]).is_relative_to(parent)]):
        raise ValueError("duplicate semantic source")
    inventory = list((root / parent).rglob("*"))
    if any(p.is_symlink() for p in inventory):
        # No semantic family contains a symlink; the artifact-access checks own this finding.
        return None
    if {p.relative_to(root / parent).as_posix() for p in inventory if not p.is_dir()} != set(observed):
        return None
    for case in CASES:
        if observed == _family(case):
            return case, records, observed
    return None


def project(root, selection):
    if "example_assessment" not in selection:
        return None
    admitted = _admit(root, selection)
    if admitted is None:
        return None
    case, records, raw = admitted
    current = source._current()
    counterpart, grader = source._program(current, current, "base")
    executable = dict(current)
    executable["tests/refund_scorer.py"] = raw["tests/refund_scorer.py"]
    program, _ = source._program(current, executable, "hidden-completion" if case == "hidden-authority" else "base")
    contract = json.loads(raw["environment/contract.json"])
    controls = {
        name: {
            "selected": program["evaluate"](deepcopy(contract), deepcopy(candidate), grader=grader),
            "counterpart": counterpart["evaluate"](deepcopy(contract), deepcopy(candidate), grader=grader),
            "candidate": candidate,
        }
        for name, candidate in source._controls(contract).items()
    }
    if any(c["selected"]["health"] != "ok" for c in controls.values()):
        raise ValueError("semantic owning scorer execution failed")
    if not controls["reference"]["selected"]["accepted"] or controls["no-work"]["selected"]["accepted"]:
        raise ValueError("contradictory requirement control did not reproduce")
    findings, extra = _review(case, raw, controls, program, contract)
    conditions = source._conditions(contract, "base", [])
    disputed = extra.get("condition_coverage", {}).get("disputed", [])
    for condition in conditions:
        condition["status"] = "disputed" if condition["id"] in disputed else "source_mapped_not_human_adjudicated"
    extra["conditions"] = conditions
    citations = [
        source._citation(records[name], raw[name])
        for name in ("instruction.md", "environment/contract.json", "tests/refund_scorer.py")
    ]
    if SUBJECT in raw:
        citations.append(source._citation(records[SUBJECT], raw[SUBJECT]))
    result: dict[str, Any] = {
        "observations": {
            "case": case,
            "controls": controls,
            **extra,
            "native_execution": False,
            "model_execution": False,
            "human_adjudication": False,
        },
        "evidence_refs": citations,
        "findings": findings,
        "binding": {
            "sources": {
                name: {"path": records[name]["path"], "digest": source._digest(value)} for name, value in raw.items()
            },
            "implementation": {
                **source._implementation(),
                "semantic_adapter": source._digest(Path(__file__).read_bytes()),
            },
            "rules": source.RULES,
        },
    }
    if case == "advantaged-comparison":
        decision = read_decision(result, root=root, selection=selection)
        if decision is not None:
            comparison = cast(dict[str, Any], result["observations"]["comparison"])
            comparison["owner_decision"] = decision
            accepted = not decision["revoked"] and decision["event"]["decisions"][FAIRNESS]["decision"] == "approve"
            if accepted:
                result["observations"]["human_adjudication"] = True
                result["findings"][FAIRNESS] = {
                    "score": 0,
                    "reason": "Matched identical no-work input earns A 1/1 versus B 0/9 under the same-task correctness claim. The owner explicitly judged this accommodation unjustified and consented to retain this exact mutant-only judgment. Local operator-recorded provenance, not authenticated identity; no base or upper authority transfers.",
                }
    if _admit(root, selection) != admitted or source._current() != current:
        raise ValueError("semantic source changed during assessment")
    return result


def _review(case, raw, controls, program, contract):
    findings, extra = {}, {}

    def finding(ident, score, reason):
        findings[ident] = {"score": score, "reason": reason}

    if case == "contradictory-contract":
        finding(
            "task-contract.clear-contract",
            0,
            "The binding amendment requires zero refund effects while the still-binding fresh-effect and amount obligations require positive refunds. Reference work passes the scorer and no work fails; simultaneous compliance is impossible. See instruction.md final amendment and contract.json requirements.",
        )
    elif case in ("ambiguous-contract", "disputed-authority", "disputed-scoring"):
        reason = "The public amendment explicitly leaves optional-versus-mandatory completion unresolved without precedence. Eight core refund obligations remain justified. The existing scorer awards valid incomplete-signalled work 8/9; the proposed mandatory interpretation awards it zero. This is an unresolved material dispute, not a settled contradiction or missing assessment evidence."
        for ident in (
            "task-contract.clear-contract",
            "task-contract.discoverable-requirements",
            "task-contract.scoring-authority",
        ):
            finding(ident, 1, reason)
        extra["condition_coverage"] = {
            "total": 12,
            "supported": 10,
            "disputed": ["completion_signal", "mandatory-gates"],
            "unresolved_owner_decision": "Does the public amendment keep completion optional or make it mandatory? No resolution is required to observe that the dispute exists.",
        }
    elif case == "hidden-authority":
        probe = controls["missing-completion"]
        if probe["selected"]["accepted"] or not probe["counterpart"]["accepted"]:
            raise ValueError("hidden requirement not reproduced")
        reason = "The grader newly requires completion for all credit. The complete allowed authority inventory states completion is optional and expressly excludes verifier/private grading files from allowed authority. Verifier readability does not authorize a forbidden source. Valid work without completion changes from 8/9 to 0/9 in the current trusted scorer pair."
        finding("task-contract.discoverable-requirements", 0, reason)
        finding("task-contract.scoring-authority", 0, reason)
        finding("task-contract.clear-contract", 0, reason)
        extra["condition_coverage"] = {
            "total": 12,
            "supported": 11,
            "unsupported": ["mandatory-completion"],
            "allowed_authority_inventory": [
                "instruction.md",
                "environment/contract.json",
                "documented socket read state",
            ],
            "forbidden_authority": ["tests/", "private grading files", "image layers"],
            "native_access_observed": False,
        }
    else:
        subject = json.loads(raw[SUBJECT])
        if subject["kind"] == "revision-records":
            records = subject["records"]
            missing = {r["id"]: sorted(set(source.FROZEN) - r["bindings"].keys()) for r in records}
            missing = {k: v for k, v in missing.items() if v}
            recomputed = {
                r["id"]: program["evaluate"](subject["contracts"][r["id"]], r["candidate"], grader="reviewed")
                for r in records
            }
            if any(recomputed[r["id"]] != r["result"] for r in records):
                raise ValueError("revision subject result differs from owning execution")
            incompatible = (
                len(
                    {
                        r["bindings"]["environment/contract.json"]
                        for r in records
                        if r["id"] in subject["published"]["result_ids"]
                    }
                )
                > 1
            )
            review = {
                "missing_bindings": missing,
                "incompatible_combined": incompatible,
                "recomputed": recomputed,
                "publication": subject["published"],
                "subject_records_are_not_native_receipts": True,
            }
            extra["revision_review"] = review
            if incompatible and not subject["published"]["invalidations"]:
                finding(
                    "provenance.evidence-revision-consistency",
                    0,
                    "The subject publication labels results current while combining prior tax-inclusive and current tax-exclusive contract revisions without invalidation. Real scorer recomputation changes accepted reference work from 9/9 to 0/9. These are faithfully bound defective publication records, not forged native evidence admitted as trustworthy.",
                )
            elif missing:
                finding(
                    "provenance.evidence-revision-consistency",
                    1,
                    "The subject retains a prior review with its scorer binding absent. The current-only result is not silently combined, but material version metadata is incomplete and the prior review remains stale after the tax-policy change.",
                )
        else:
            paired = {}
            if subject["paired_controls_requested"]:
                for name in ("no-work", "reference", "missing-completion"):
                    candidate = controls[name]["candidate"]
                    paired[name] = {
                        arm["id"]: program["evaluate"](contract, candidate, grader=arm["grader"])
                        for arm in subject["systems"]
                    }
            advantage = (
                bool(paired)
                and paired["no-work"]["system-A"]["accepted"]
                and not paired["no-work"]["system-B"]["accepted"]
            )
            extra["comparison"] = {
                "subject": subject,
                "paired": paired,
                "advantage_demonstrated": advantage,
                "owner_decision": None,
                "model_execution": False,
                "owner_question": "For this simulated same-task refund-correctness comparison, is assigning completion-only grading to system A while system B must satisfy all refund obligations an unjustified model-specific accommodation? Recommended: yes. Identical no-work input receives A 1/1 and B 0/9; reference work receives A 1/1 and B 9/9. This answer concerns only this bound changed comparison, not base or upper review.",
            }
            finding(
                "provenance.comparison-fairness",
                1,
                "Known system-specific scorer configurations differ (completion-only versus all mandatory refund obligations) with an empty justification register. "
                + (
                    "Matched local controls reproduce the advantage, but its unjustified disposition requires a new scoped owner answer; base approval is inapplicable."
                    if advantage
                    else "No matched controls have been supplied for this comparison; the effect and justification remain unresolved."
                ),
            )
    return findings, extra


FAIRNESS = "provenance.comparison-fairness"


def decision_request(projection):
    """Exact comparison, not an arbitrary status/score from selected JSON."""
    from rubric_general_review import _digest, _require

    observation = deepcopy(projection["observations"]["comparison"])
    observation["owner_decision"] = None
    _require(observation["advantage_demonstrated"] is True)
    request = {
        "schema": "rubric-semantic-fairness-request-v1",
        "comparison_binding": projection["binding"],
        "comparison": observation,
        "scopes": {FAIRNESS: "Is this exact simulated same-task accommodation unjustified?"},
        "boundary": "Changed comparison only. Approve means affirm unjustified, not approve fairness. No base, upper, model-performance or independent-review authority.",
    }
    request["binding"] = _digest(request)
    return request


def _decision_target(projection):
    from rubric_general_review import _operator_root

    store = _operator_root()
    request = decision_request(projection)
    return (store / (request["binding"] + ".semantic-fairness.json") if store else None), request


def read_decision(projection, *, root=None, selection=None):
    from eval_author_shared.task_evidence import loads
    from rubric_general_review import OPERATOR_BOUNDARY, _event, _require

    target, request = _decision_target(projection)
    if target is None:
        return None
    if selection is not None:
        if root is None:
            raise ValueError("selected root required")
        _require(
            all(not (Path(root) / r["path"]).absolute().is_relative_to(target.parent) for r in selection["artifacts"])
        )
    if not target.exists():
        return None
    raw, reason = read_selected(target.parent, target.name)
    if reason is not None or raw is None:
        raise ValueError("unreadable operator event")
    document = loads(raw)
    _require(set(document) == {"schema", "trust_basis", "event"})
    _require(
        document["schema"] == "rubric-semantic-fairness-review-v1" and document["trust_basis"] == OPERATOR_BOUNDARY
    )
    event = _event(request, document["event"])
    return {
        **document,
        "event": event,
        "revoked": target.with_suffix(".revoked").exists(),
        "digest": source._digest(raw),
        "local_operator_decision_recorded": True,
        "owner_signature_verified": False,
        "owner_session_decision_verified": False,
    }


def record_decision(root, selection, observed_decision):
    """Trusted caller must supply an actually observed answer and retention consent."""
    from rubric_general_review import OPERATOR_BOUNDARY, _event, _require, _write

    projection = project(root, selection)
    _require(projection is not None and projection["observations"]["case"] == "advantaged-comparison")
    target, request = _decision_target(projection)
    if target is None:
        raise ValueError("explicit operator store required")
    _require(not target.exists() and not target.with_suffix(".revoked").exists())
    event = _event(request, observed_decision(deepcopy(request)))
    document = {"schema": "rubric-semantic-fairness-review-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}
    _write(target, _json(document))
    observed = read_decision(projection, root=root, selection=selection)
    _require(observed is not None and observed["event"] == event)
    return target


def revoke_decision(projection):
    from rubric_general_review import _require, _write

    target, _ = _decision_target(projection)
    if target is None:
        raise ValueError("explicit operator store required")
    _require(target.exists())
    _write(target.with_suffix(".revoked"), b"revoked\n")
    observed = read_decision(projection)
    _require(observed is not None and observed["revoked"])
    return target.with_suffix(".revoked")


def apply(report, projection):
    if projection is None:
        return
    report["measurements"]["semantic_numeric"] = projection
    for ident, finding in projection["findings"].items():
        row = report["items"][ident]
        if row.get("score") is None or row["score"] > finding["score"]:
            row.update(
                score=finding["score"],
                status="ASSESSED",
                assessment_state="ASSESSED",
                evidence_strength="E1",
                assessment_scope="bounded-source-inspection",
            )
        row.setdefault("findings", []).append(finding["reason"])
        row.setdefault("evidence_refs", []).append({"measurement": "/measurements/semantic_numeric"})
        for check in row["check_results"]:
            method = check["method"]
            check_id = check["id"]
            measured = method == "deterministic" and not check_id.endswith(("deterministic-3", "deterministic-4"))
            measured = measured or (method == "llm_as_judge" and ident.startswith("task-contract."))
            if measured and check["outcome"] not in ("FAIL", "ERROR"):
                check.update(outcome="FAIL" if finding["score"] == 0 else "PARTIAL", remaining_gap=finding["reason"])
                if method == "llm_as_judge":
                    check["reviewer_kind"] = "agent"
                    check["qualified_judge"] = False
                check.setdefault("evidence", []).append(
                    {"helper": "semantic_numeric", "value": {"measurement": "/measurements/semantic_numeric"}}
                )
        # No human-method success or qualification is inferred from source facts.


def run_batch(output, expectations):
    """Expectations are caller-only; selections contain subject bytes, never targets."""
    import os
    import subprocess
    import sys

    import rubric_table
    from rubric_leakage_current import canonical_definitions

    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not expectations or not set(expectations) <= set(CASES):
        raise ValueError("unknown or empty case inventory")
    baseline = source._current()
    records, rows = {}, {}
    script = str(Path(rubric_table.__file__).resolve())
    definitions = canonical_definitions()
    cases = ["baseline", *expectations, "repair"]
    for case in cases:
        root = output / case
        selection = materialize(root, "repair" if case == "baseline" else case)
        (root / "selection.json").write_bytes(_json(selection))
        argv = [
            sys.executable,
            "-B",
            script,
            "--root",
            str(root),
            "--selection",
            str(root / "selection.json"),
            "--output",
            str(root / "report"),
        ]
        run = subprocess.run(argv, capture_output=True, timeout=180, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        (root / "assessment.stdout").write_bytes(run.stdout)
        (root / "assessment.stderr").write_bytes(run.stderr)
        record = {"argv": argv, "exit": run.returncode, "report": str(root / "report/assessment.json")}
        (root / "assessment.command.json").write_bytes(_json(record))
        if run.returncode not in (0, 1) or not (root / "report/assessment.json").is_file():
            raise ValueError("ordinary semantic assessment failed: " + case)
        report = json.loads((root / "report/assessment.json").read_bytes())["report"]
        if report["items"].keys() != definitions.keys():
            raise ValueError("incomplete criterion inventory")
        for ident, row in report["items"].items():
            if [c["id"] for c in row["check_results"]] != [c["id"] for c in definitions[ident]["checks"]]:
                raise ValueError("incomplete check inventory")
        rows[case] = {
            ident: {
                "score": row["score"],
                "evidence_strength": row.get("evidence_strength"),
                "checks": {c["id"]: c["outcome"] for c in row["check_results"]},
            }
            for ident, row in report["items"].items()
        }
        records[case] = record
        # One replay per physical report, in the final batch below.
    for case, record in records.items():
        root = output / case
        argv = [sys.executable, "-B", script, "--root", str(root), "--verify-existing", str(root / "report")]
        run = subprocess.run(argv, capture_output=True, timeout=180, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        (root / "replay.stdout").write_bytes(run.stdout)
        (root / "replay.stderr").write_bytes(run.stderr)
        record["replay"] = {
            "argv": argv,
            "exit": run.returncode,
            "verified": run.returncode == record["exit"] and run.stdout == (root / "assessment.stdout").read_bytes(),
        }
        (root / "replay.command.json").write_bytes(_json(record["replay"]))
        if not record["replay"]["verified"]:
            raise ValueError("ordinary semantic replay failed: " + case)
    if source._current() != baseline:
        raise ValueError("frozen base changed")
    if rows["baseline"] != rows["repair"]:
        raise ValueError("fresh repair does not restore all neighboring projections")
    result: dict[str, Any] = {
        "schema": "semantic-numeric-batch-v1",
        "verified": True,
        "source_only_mutants": True,
        "base_owner_authority_transferred": False,
        "frozen_base_unchanged": True,
        "cases": {},
        "reports": records,
        "matched_cells": 0,
        "requested_cells": sum(len(v) for v in expectations.values()),
    }
    lines = [
        "# Semantic numeric controls",
        "",
        "Source-only fresh copies; no native/model execution or inherited owner decisions. Repair is a fresh exact-base assessment, not mutant native regrading.",
        "",
        "| Case | Criterion | Expected | Actual | Repair | Result |",
        "|---|---|---:|---:|---:|---|",
    ]
    for case, expected in expectations.items():
        matches = {
            ident: type(rows[case][ident]["score"]) is type(target) and rows[case][ident]["score"] == target
            for ident, target in expected.items()
        }
        result["matched_cells"] += sum(matches.values())
        changes = {
            ident: {"before": rows["baseline"][ident], "after": row}
            for ident, row in rows[case].items()
            if row != rows["baseline"][ident]
        }
        result["cases"][case] = {
            "expected": expected,
            "actual": {ident: rows[case][ident]["score"] for ident in expected},
            "matches": matches,
            "collateral": changes,
            "unchanged_neighbors": [ident for ident in rows[case] if ident not in changes],
            "repair_equal": True,
            "repair_rows": rows["repair"],
            "report": records[case]["report"],
        }
        for ident, target in expected.items():
            lines.append(
                f"| [{case}]({case}/report/results.md) | {ident} | {target} | {rows[case][ident]['score']} | {rows['repair'][ident]['score']} | {'matched' if matches[ident] else 'owner decision pending'} |"
            )
    advantage = output / "advantaged-comparison/report/assessment.json"
    if advantage.is_file():
        proof = json.loads(advantage.read_bytes())["report"]["measurements"]["semantic_numeric"]
        question = proof["observations"]["comparison"]["owner_question"]
        (output / "owner-question.json").write_bytes(
            _json(
                {
                    "question": question,
                    "comparison_binding": proof["binding"],
                    "observations": proof["observations"]["comparison"],
                    "decision": None,
                }
            )
        )
        lines += [
            "",
            "## Scoped owner decision",
            "",
            question,
            "",
            "Fairness remains 1 until a genuine scoped answer is recorded. No base approval is reused.",
        ]
    (output / "batch.json").write_bytes(_json(result))
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--expectations",
        type=Path,
        required=True,
        help="External case-to-criterion target mapping; never selected as evidence",
    )
    args = parser.parse_args()
    result = run_batch(args.output, json.loads(args.expectations.read_bytes()))
    print(
        json.dumps(
            {
                "verified": result["verified"],
                "matched_cells": result["matched_cells"],
                "requested_cells": result["requested_cells"],
                "summary": str(args.output / "summary.md"),
            }
        )
    )


if __name__ == "__main__":
    main()
