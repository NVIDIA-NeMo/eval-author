# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Separately declared CREATE-02 subject; no authority inherited from old tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))

import rubric_create02_subject_runtime as runtime
from rubric_identity import identify, read_selected

SUBJECT_ID = "create02-heldout-refund-subject"
SCOPE = "finite-heldout-refund-template-cache-v1"
CLAIM = (
    "Full benchmark credit certifies correct refund amount and original-payment destination on each "
    "previously unseen order, using that order's supplied merchandise, discount, tax policy and prior-refund "
    "evidence. Template labels, option identities, option order and formatting are non-authoritative; "
    "the same template can describe different orders. No reasoning transcript or particular algorithm is required."
)
OWNER_QUESTION = (
    "For this NEW finite refund-decision benchmark only, accept the declared held-out-order correctness claim "
    "and the actual agent's exact-packet control review: template labels are incidental, both options are "
    "authorized/feasible, and full stale-cache credit for the wrong current-order answer contradicts that claim? "
    "Retain this disposition for this exact packet only; no change to original CREATE-02.0, prior approvals, "
    "or claim of model/general-population robustness."
)
AGENT_QUESTION = (
    "Review this exact NEW subject and executed packet against CREATE-02-C01 through C04 and its zero anchor. "
    "Inspect the claim, current/runtime source bindings, selected inputs, actual read traces and per-check "
    "published/evidence outcomes. Are the label flip, evidence-absent cue-only flip, option-order swap and "
    "style-only pairs admissible with fixed policy/scorer/authority/feasibility? Is the stale template cache "
    "rewarding a genuinely wrong current-order decision rather than penalizing legitimate correct shortcuts? "
    "Record actual findings/dissent and disclosed exposure, not a canned score. This author already knows "
    "the intended defect; your review is not blind/independent corroboration. Owner acceptance remains separate."
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def declaration() -> dict[str, Any]:
    return {
        "schema": SCOPE,
        "claim": CLAIM,
        "instances": [runtime.instance(i) for i in range(2)],
        "authority": "Original payment and amount formula in evidence are authoritative; labels are advisory only.",
        "output": "A refund decision JSON, not executed money movement and not a reasoning transcript.",
        "benchmark": "Published exact-match scorer caches development keys by template, erroneously reusing them for held-out orders.",
        "evidence_obligation": "Independent per-instance amount/destination check; no access-path requirement in task credit.",
        "limits": "Synthetic finite same-family orders reserved from key-cache construction, not blinded reviewer exposure or model generalization.",
        "cue_register": {
            "template_label": "Candidate inherited development default; directly consumed by cue policy.",
            "option_order": "Counterbalanced nuisance; stable option IDs.",
            "formatting": "Whitespace-only nuisance.",
            "filenames": "Fixed neutral presentation.json/evidence.json, no answer-bearing filename.",
            "stale_output": "No previous solver output; stale scorer cache is the subject defect.",
            "private_access": "Excluded: no reference program, native filesystem or prohibited access claim.",
        },
    }


def implementation() -> dict[str, str]:
    names = (
        Path(__file__).name,
        Path(runtime.__file__).name,
        "rubric_table.py",
        "rubric_leakage_presentation.py",
        "rubric_general_review.py",
        "rubric_source_reviews.py",
        "rubric_leakage_current.py",
    )
    paths = [Path(__file__).with_name(name) for name in names]
    shared = Path(__file__).resolve().parents[2] / "eval-author/scripts"
    paths.extend([shared / "rubric_identity.py", shared / "eval_author_shared/task_evidence.py"])
    return {
        str(path.relative_to(Path(__file__).resolve().parents[2])): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def selected_input(root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = selection.get("artifacts", [])
    if not any(a.get("id") == SUBJECT_ID for a in artifacts):
        return None
    if set(selection) != {"artifacts"} or len(artifacts) != 2 or identify(root, artifacts)["status"] != "pass":
        raise ValueError("new subject must be selected alone without old approvals")
    expected = {SUBJECT_ID: ("subject.json", "task"), SUBJECT_ID + "-runtime": ("subject-runtime.py", "dependency")}
    if {a["id"] for a in artifacts} != expected.keys():
        raise ValueError("subject inventory mismatch")
    content = {}
    for artifact in artifacts:
        name, role = expected[artifact["id"]]
        if artifact["path"] != name or artifact["role"] != role or artifact["access"] != "available":
            raise ValueError("subject source alias")
        raw, error = read_selected(root, name)
        if error or raw is None:
            raise ValueError("unreadable subject")
        content[name] = raw
    if content["subject-runtime.py"] != Path(runtime.__file__).read_bytes():
        raise ValueError("subject runtime is not current")
    subject = json.loads(content["subject.json"])
    if digest(subject) != digest(declaration()):
        raise ValueError("unsupported subject declaration")
    return subject


def build(output: Path) -> dict[str, Any]:
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("new nonsymlink subject directory required")
    output.mkdir(mode=0o700, parents=True)
    (output / "subject.json").write_text(json.dumps(declaration(), indent=2) + "\n")
    (output / "subject-runtime.py").write_bytes(Path(runtime.__file__).read_bytes())
    records = []
    for ident, name, role in (
        (SUBJECT_ID, "subject.json", "task"),
        (SUBJECT_ID + "-runtime", "subject-runtime.py", "dependency"),
    ):
        value = hashlib.sha256((output / name).read_bytes()).hexdigest()
        records.append(
            {
                "id": ident,
                "path": name,
                "role": role,
                "digest": value,
                "access": "available",
                "revision": None,
                "selection_origin": "separately declared mutable benchmark subject",
                "source": name,
                "evidence": [{"path": name, "digest": value}],
            }
        )
    selection = {"artifacts": records}
    (output / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    return selection


def run_policy(
    directory: Path,
    task: dict[str, Any],
    presentation: dict[str, Any],
    policy: str,
    *,
    evidence: bool = True,
    styled: bool = False,
) -> dict[str, Any]:
    directory.mkdir()
    (directory / "presentation.json").write_text(json.dumps(presentation, indent=4 if styled else None))
    if evidence:
        (directory / "evidence.json").write_text(json.dumps(task["evidence"]))
    decision = runtime.consume(directory, policy)
    return {
        **decision,
        "candidate_sha256": digest(decision["answer"]),
        "input_inventory": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())},
        "feasible": runtime.feasible(task, decision["answer"]),
        "published": runtime.published_score(task, decision["answer"]),
        "evidence_oracle": runtime.evidence_score(task, decision["answer"]),
        "scorer_repair_rescore": runtime.evidence_score(task, decision["answer"]),
    }


def collect(root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    from rubric_leakage_current import definitions_digest

    subject = selected_input(root, selection)
    if subject is None:
        return None
    binding = {
        "subject": digest(subject),
        "selection": digest(selection),
        "implementation": implementation(),
        "definitions": definitions_digest(),
    }
    pairs = []
    with tempfile.TemporaryDirectory(prefix="create02-subject-") as temporary:
        work = Path(temporary)
        for index, task in enumerate(subject["instances"]):
            aligned = deepcopy(task["presentation"])
            aligned["template_label"] = "current-order"
            misleading = deepcopy(aligned)
            misleading["template_label"] = "draft-default"
            swapped = deepcopy(aligned)
            swapped["options"].reverse()
            for kind, left, right, evidence, styled in (
                ("misleading-cue", aligned, misleading, True, False),
                ("cue-only", aligned, misleading, False, False),
                ("nuisance-swap", aligned, swapped, True, False),
                ("style-only", aligned, aligned, True, True),
            ):
                for policy in ("cue-only", "evidence-first") if evidence else ("cue-only",):
                    label = f"{index}-{kind}-{policy}"
                    baseline = run_policy(work / (label + "-a"), task, left, policy, evidence=evidence)
                    intervention = run_policy(
                        work / (label + "-b"), task, right, policy, evidence=evidence, styled=styled
                    )
                    pairs.append(
                        {
                            "instance": index,
                            "class": kind,
                            "policy": policy,
                            "task_sha256": digest(task),
                            "scorer_sha256": binding["implementation"][
                                "eval-author-rubric/scripts/" + Path(runtime.__file__).name
                            ],
                            "authority_unchanged": True,
                            "baseline": baseline,
                            "intervention": intervention,
                            "published_transition": [
                                baseline["published"]["credit"],
                                intervention["published"]["credit"],
                            ],
                            "evidence_transition": [
                                baseline["evidence_oracle"]["credit"],
                                intervention["evidence_oracle"]["credit"],
                            ],
                        }
                    )
    if (
        selected_input(root, selection) != subject
        or implementation() != binding["implementation"]
        or definitions_digest() != binding["definitions"]
    ):
        raise ValueError("subject changed during observation")
    return {
        "schema": "create02-subject-observations-v1",
        "scope": SCOPE,
        "binding": binding,
        "subject": subject,
        "pairs": pairs,
        "execution_state": "COMPLETED",
        "native_execution": False,
        "model_execution": False,
        "independent": False,
        "uncertainty": "Exact paired outcomes from two synthetic order clusters in one family; no population interval.",
        "limitations": [
            "Evidence withholding changes difficulty: causal cue-only comparison holds evidence absent in BOTH arms.",
            "Independent new-family controls and held-out agent review are absent.",
            "Access traces are actual instrumented policy reads, not an OS sandbox.",
        ],
    }


def request(projection: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind not in {"owner", "agent"}:
        raise ValueError("invalid review kind")
    value = {
        "schema": "create02-subject-review-request-v1",
        "kind": kind,
        "binding_sources": projection["binding"],
        "observation_sha256": digest(projection),
        "scopes": {"CREATE-02": OWNER_QUESTION if kind == "owner" else AGENT_QUESTION},
    }
    return {**value, "binding": digest(value)}


def review_path(root: Path, projection: dict[str, Any], kind: str) -> tuple[Path | None, dict[str, Any]]:
    from rubric_general_review import _operator_root

    value = request(projection, kind)
    store = _operator_root()
    if store is not None and store.resolve().is_relative_to(root.resolve()):
        raise ValueError("operator store overlaps selected subject")
    return (store / (value["binding"] + ".create02-subject-" + kind + ".json") if store else None), value


def read_review(root: Path, projection: dict[str, Any], kind: str) -> dict[str, Any] | None:
    from rubric_general_review import OPERATOR_BOUNDARY, _event

    path, value = review_path(root, projection, kind)
    if path is None or not path.exists():
        return None
    raw, error = read_selected(path.parent, path.name)
    if error or raw is None:
        raise ValueError("unreadable subject review")
    saved = json.loads(raw)
    if (
        set(saved) != {"schema", "trust_basis", "event"}
        or saved["schema"] != "create02-subject-review-v1"
        or saved["trust_basis"] != OPERATOR_BOUNDARY
    ):
        raise ValueError("unsupported subject review")
    return {
        **saved,
        "event": _event(value, saved["event"]),
        "revoked": path.with_suffix(".revoked").exists(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "authenticated": False,
        "independent": False,
    }


def record_review(
    root: Path, selection: dict[str, Any], kind: str, observed_decision: Callable[[dict[str, Any]], dict[str, Any]]
) -> Path:
    from rubric_general_review import OPERATOR_BOUNDARY, _event, _write

    projection = collect(root, selection)
    if projection is None:
        raise ValueError("subject required")
    path, value = review_path(root, projection, kind)
    if path is None or path.exists() or path.with_suffix(".revoked").exists():
        raise ValueError("new exact review and explicit operator store required")
    event = _event(value, observed_decision(deepcopy(value)))
    _write(
        path,
        json.dumps(
            {"schema": "create02-subject-review-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}, sort_keys=True
        ).encode(),
    )
    saved = read_review(root, projection, kind)
    if saved is None or saved["event"] != event:
        raise ValueError("review readback failed")
    return path


def assess(root: Path, selection: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    from rubric_leakage_current import reconcile

    projection = collect(root, selection)
    if projection is None:
        return report
    result = reconcile(report, {})
    reviews = {kind: read_review(root, projection, kind) for kind in ("owner", "agent")}
    qualified = all(
        value and not value["revoked"] and value["event"]["decisions"]["CREATE-02"]["decision"] == "approve"
        for value in reviews.values()
    )
    pairs = projection["pairs"]
    healthy = all(
        arm["feasible"] and arm["published"]["health"] == arm["evidence_oracle"]["health"] == "ok"
        for pair in pairs
        for arm in (pair["baseline"], pair["intervention"])
    )
    witnesses = [
        pair
        for pair in pairs
        if pair["class"] == "misleading-cue"
        and pair["policy"] == "cue-only"
        and pair["published_transition"] == [0, 1]
        and pair["evidence_transition"] == [1, 0]
        and pair["baseline"]["candidate_sha256"] != pair["intervention"]["candidate_sha256"]
        and all(read["path"] != "evidence.json" for read in pair["intervention"]["access_trace"])
    ]
    robust = [
        pair
        for pair in pairs
        if pair["class"] == "misleading-cue"
        and pair["policy"] == "evidence-first"
        and pair["evidence_transition"] == [1, 1]
    ]
    isolated = all(
        pair["baseline"]["input_inventory"].get("evidence.json")
        == pair["intervention"]["input_inventory"].get("evidence.json")
        and pair["baseline"]["policy"] == pair["intervention"]["policy"]
        and (
            pair["baseline"]["answer"] == pair["intervention"]["answer"]
            if pair["class"] in {"nuisance-swap", "style-only"}
            else True
        )
        for pair in pairs
    )
    cue_only = [
        pair
        for pair in pairs
        if pair["class"] == "cue-only"
        and pair["published_transition"] == [0, 1]
        and pair["evidence_transition"] == [1, 0]
        and all("evidence.json" not in arm["input_inventory"] for arm in (pair["baseline"], pair["intervention"]))
    ]
    defect = (
        healthy
        and isolated
        and len(witnesses) == len(projection["subject"]["instances"]) == len(robust) == len(cue_only)
    )
    pending = {
        kind: request(projection, kind)
        for kind, value in reviews.items()
        if not value or value["revoked"] or value["event"]["decisions"]["CREATE-02"]["decision"] != "approve"
    }
    result["measurements"]["create02_subject"] = {
        **projection,
        "reviews": reviews,
        "pending_requests": pending,
        "material_bypass_observed": defect,
    }
    row = result["items"]["CREATE-02"]
    row.update(
        score=0 if defect and qualified else None,
        status="ASSESSED" if defect and qualified else "NOT_ASSESSED",
        assessment_state="ASSESSED" if defect and qualified else "NOT_ASSESSED",
        execution_state="COMPLETED",
        evidence_strength="E2",
        assessment_scope=SCOPE,
        findings=[
            "Published stale-template scorer rewards a wrong current-order amount/destination via advisory label; "
            "the same fixed evidence policy stays correct under that label flip. Correct cue-selected answers are not penalized."
            if defect
            else "The complete healthy isolated bypass predicate was not established; inspect retained controls."
        ],
        remaining_gaps=[value["scopes"]["CREATE-02"] for value in pending.values()] + projection["limitations"],
        limitations=projection["limitations"],
        evidence_refs=[{"measurement": "create02_subject", "digest": digest(projection["binding"])}],
    )
    for check in row["check_results"]:
        suffix = check["id"].rsplit("-", 1)[-1]
        check.update(
            outcome=("PASS" if qualified else "NOT_RUN")
            if suffix == "C02"
            else ("FAIL" if defect and qualified else "PARTIAL")
            if suffix == "C03"
            else "PASS",
            evidence_refs=row["evidence_refs"],
            remaining_gap="Exact agent and owner qualification pending."
            if suffix == "C02" and not qualified
            else projection["uncertainty"]
            if suffix == "C04"
            else "Finite actual controls retained; no independent corroboration.",
        )
    from rubric_leakage_current import definitions_digest

    if (
        {kind: read_review(root, projection, kind) for kind in reviews} != reviews
        or selected_input(root, selection) != projection["subject"]
        or implementation() != projection["binding"]["implementation"]
        or definitions_digest() != projection["binding"]["definitions"]
    ):
        raise ValueError("subject or decisions changed at projection")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify-existing", type=Path)
    parser.add_argument("--defer-replay", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_existing:
        if args.defer_replay:
            parser.error("cannot defer an explicit replay")
        root = args.verify_existing.resolve()
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("rubric_table.py")),
                "--root",
                str(root),
                "--verify-existing",
                str(root / "assessment"),
            ],
            check=False,
        ).returncode
    root = args.output.absolute()
    build(root)
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).with_name("rubric_table.py")),
        "--root",
        str(root),
        "--selection",
        str(root / "selection.json"),
        "--output",
        str(root / "assessment"),
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    (root / "assessment-command.json").write_text(
        json.dumps({"argv": command, "returncode": run.returncode, "stderr": run.stderr}) + "\n"
    )
    if run.returncode not in (0, 1) or not (root / "assessment/assessment.json").exists():
        raise ValueError("ordinary subject assessment failed: " + run.stdout[-1000:])
    saved = json.loads((root / "assessment/assessment.json").read_bytes())
    projection = saved["report"]["measurements"]["create02_subject"]
    for kind, value in projection["pending_requests"].items():
        (root / (kind + "-request.json")).write_text(json.dumps(value, indent=2) + "\n")
    summary = {
        "scope": SCOPE,
        "claim": CLAIM,
        "actual_score": saved["report"]["items"]["CREATE-02"]["score"],
        "qualification": "pending" if projection["pending_requests"] else "qualified",
        "material_bypass_observed": projection["material_bypass_observed"],
        "pair_count": len(projection["pairs"]),
        "replay": "deferred" if args.defer_replay else "requested",
        "report": str(root / "assessment/assessment.json"),
        "original_CREATE02_0": {"actual": 2, "external_target": 0, "unchanged_history": True},
    }
    (root / "subject-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    if not args.defer_replay:
        replay = subprocess.run(
            [sys.executable, "-B", str(Path(__file__)), "--verify-existing", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        if replay.returncode not in (0, 1):
            raise ValueError("ordinary retained subject replay failed")
    print(json.dumps(summary))
    return 1 if projection["pending_requests"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
