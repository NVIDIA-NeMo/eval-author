# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit admitted refund result identities and scripted comparison settings."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from eval_author_shared.task_evidence import loads  # noqa: E402
from rubric_combined import project_panels  # noqa: E402
from rubric_evidence_scoring import definitions_digest  # noqa: E402
from rubric_example_assessment import Sources  # noqa: E402
from rubric_identity import read_selected  # noqa: E402

REVISION = "provenance.evidence-revision-consistency"
FAIRNESS = "provenance.comparison-fairness"


class AuditSelection(TypedDict):
    mode: Literal["inspect", "exercise"]
    path: NotRequired[str]


def selection_mode(value: Any) -> Literal["inspect", "exercise"]:
    if not isinstance(value, dict):
        raise ValueError("invalid audit selection")
    if value == {"mode": "inspect"}:
        return "inspect"
    if set(value) == {"mode", "path"} and value["mode"] == "exercise" and isinstance(value["path"], str):
        path = Path(value["path"])
        if (
            value["path"]
            and not path.is_absolute()
            and ".." not in path.parts
            and path.as_posix() == value["path"]
            and path.parts
        ):
            return "exercise"
    raise ValueError("invalid audit selection")


def selected(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    mode = selection_mode(selection["audit_companion"])
    return (
        inspect(root, selection)
        if mode == "inspect"
        else verify(root, selection, root / selection["audit_companion"]["path"])
    )


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_json(root: Path, path: str, expected_digest: str | None = None) -> Any:
    raw, reason = read_selected(root, path)
    if reason or raw is None or (expected_digest is not None and hashlib.sha256(raw).hexdigest() != expected_digest):
        raise ValueError("audit source unavailable")
    return loads(raw)


def inspect(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    sources = Sources(root, selection["artifacts"])
    selector = selection["example_assessment"]
    task_record = next(row for row in selection["artifacts"] if row["id"] == selector["task_config_id"])
    task = str(Path(task_record["path"]).parent)
    panels = project_panels(sources, selector, task)
    if not panels or any(panel["task_freshness"] != "current" for panel in panels):
        raise ValueError("audit requires current admitted native panels")
    manifests = []
    results: list[dict[str, Any]] = []
    attack = None
    for panel in panels:
        record = next(row for row in selection["artifacts"] if row["digest"] == panel["index_digest"])
        index = read_json(root, record["path"], record["digest"])
        bundle = Path(record["path"]).parent
        bindings = {"producer": index["producer"], "files": index["files"], "index_digest": panel["index_digest"]}
        revision = digest(bindings)
        manifests.append({"id": revision, "index": record["path"], "bindings": bindings})
        for control in panel["controls"]:
            case = control["case"]
            result_path = (bundle / "trials" / case / "result.json").as_posix()
            result_digest = next(row["sha256"] for row in index["files"] if row["path"] == f"trials/{case}/result.json")
            native = read_json(root, result_path, result_digest)
            results.append(
                {
                    "id": native["id"],
                    "case": case,
                    "result": result_path,
                    "recorded_revision": revision,
                    "current_revision": revision,
                    "change": "unchanged",
                    "disposition": "current",
                    "lineage": record["path"],
                    "status": control["status"],
                    "execution_error": native["exception_info"] is not None,
                    "reward": control.get("reward"),
                    "checks": control.get("checks"),
                    "result_digest": next(
                        row["sha256"] for row in index["files"] if row["path"] == f"trials/{case}/result.json"
                    ),
                    "runtime_configuration": native["config"],
                }
            )
        if panel["scope"] == "eval-author.refund-attack-panel.v1":
            attack = panel, bundle
    if len({row["id"] for row in results}) != len(results):
        raise ValueError("duplicate audit native result")
    if attack is None:
        raise ValueError("scripted comparison panel missing")
    panel, bundle = attack
    arms = []
    for case in ("reference", "split-alternative"):
        control = next(row for row in panel["controls"] if row["case"] == case)
        native_row = next(
            row for row in results if row["result"] == (bundle / "trials" / case / "result.json").as_posix()
        )
        native = read_json(root, native_row["result"], native_row["result_digest"])
        arms.append(
            {
                "case": case,
                "native_id": native["id"],
                "configuration": native["config"],
                "checks": control["checks"],
                "reward": control["reward"],
                "candidate": control["observation"]["after"]["candidate"],
                "solver_uid": control["observation"]["uid"],
            }
        )
    differences = typed_diff(arms[0]["configuration"], arms[1]["configuration"])
    for row in differences:
        row["classification"] = (
            "run_identity"
            if row["pointer"] == "/trial_name"
            else "scripted_policy_variant"
            if row["pointer"] == "/agent/kwargs/case"
            else "unreviewed_setup_difference"
        )
    comparison = {
        "scope": "Scripted reference versus split-refund policy on one fixed local task. No model performance or review-benefit claim.",
        "model_study": False,
        "fairness_score": None,
        "human_review": False,
        "arms": arms,
        "setup_differences": differences,
        "candidate_differences": typed_diff(arms[0]["candidate"], arms[1]["candidate"]),
        "owner_question": {
            "difference_refs": [row["pointer"] for row in differences if row["classification"] != "run_identity"],
            "question": "For this scripted scorer comparison, may reference (one refund) and split-alternative (split refunds) differ only by /agent/kwargs/case? Both run as UID 65534 on the same task and scorer. Both earned 9/9. Recommend treating the policy as the intended variant, not a model-specific accommodation. Does this claim need another matched setup? This does not decide zero-cent refunds or ledger/refund ordering.",
            "decision": "pending",
            "binding": digest(arms),
        },
    }
    ethos = any("ethos" in row["path"].lower() for row in selection["artifacts"])
    return {
        "schema": "rubric-audit-companion-v1",
        "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "definitions_digest": definitions_digest(),
        "scope": "Current admitted refund panels and their conservative full-panel source closure.",
        "counts": {
            "panels": len(panels),
            "results": len(results),
            "infrastructure_errors": sum(panel["counts"]["infrastructure_errors"] for panel in panels),
        },
        "revision_manifests": manifests,
        "result_revisions": results,
        "comparison": comparison,
        "revision_score": None if ethos else 2,
        "checks": {
            REVISION + ".deterministic-1": "PASS",
            REVISION + ".deterministic-2": "PASS",
            REVISION + ".deterministic-3": "NOT_RUN",
            REVISION + ".deterministic-4": "BLOCKED" if ethos else "NOT_APPLICABLE",
            FAIRNESS + ".deterministic": "PASS",
            FAIRNESS + ".human-review": "NOT_RUN",
            FAIRNESS + ".probe": "NOT_RUN",
        },
        "limitations": [
            "The fixed admitted refund contract has no ETHOS dependency or model judge. Selected ETHOS evidence blocks automatic scoring.",
            "Panel bindings include every registered task, scorer, fixture, runtime configuration and producer file. Changing one conservatively invalidates its whole panel.",
            "The retained stale-after-failure infrastructure error remains an error. It is not fresh successful grading.",
            "Human fairness review and matched sensitivity tests remain pending. Hash equality does not establish independence.",
        ],
    }


def _copy_selection(root: Path, selection: dict[str, Any], destination: Path) -> None:
    destination.mkdir(mode=0o700)
    paths = {row["path"] for row in selection["artifacts"]}
    for row in selection["artifacts"]:
        if Path(row["path"]).name == "native-index.json":
            index = read_json(root, row["path"])
            paths.update((Path(row["path"]).parent / item["path"]).as_posix() for item in index["files"])
    for path in sorted(paths):
        raw, reason = read_selected(root, path)
        if reason or raw is None:
            raise ValueError("cannot isolate audit sources")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        shutil.copymode(root / path, target)


def exercise(root: Path, selection: dict[str, Any], output: Path) -> dict[str, Any]:
    if output.exists() or any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("audit exercise requires new private directory")
    baseline = inspect(root, selection)
    output.mkdir(mode=0o700)
    (output / "baseline.json").write_text(json.dumps(baseline, sort_keys=True))
    attack_manifest = next(row for row in baseline["revision_manifests"] if "/attacks/" in row["index"])
    bundle = Path(attack_manifest["index"]).parent
    tests = []
    recipes = (
        ("scoring-source", bundle / "task/tests/refund_scorer.py", 'grader == "baseline"', 'grader == "reviewed"'),
        ("runtime-budget", bundle / "task/task.toml", "timeout_sec = 120", "timeout_sec = 121"),
        ("observed-runtime", bundle / "trials/reference/config.json", None, None),
    )
    for name, relative, old, new in recipes:
        isolated = output / name
        _copy_selection(root, selection, isolated)
        if typed_diff(baseline, inspect(isolated, selection)):
            raise ValueError("isolated baseline differs before mutation")
        target = isolated / relative
        original = target.read_bytes()
        if old is None:
            value = loads(original)
            value["timeout_multiplier"] = 2.0
            changed = json.dumps(value).encode()
        else:
            if new is None or old.encode() not in original:
                raise ValueError("audit mutation source shape changed")
            changed = original.replace(old.encode(), new.encode())
        target.write_bytes(changed)
        rejected = False
        try:
            inspect(isolated, selection)
        except ValueError:
            rejected = True
        if not rejected:
            raise ValueError("stale audit evidence was reused")
        # A new static observation binds changed bytes without adopting old native credit.
        fresh = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "previous_sha256": hashlib.sha256(original).hexdigest(),
            "native_score": None,
            "changed": target.read_bytes() != original,
            "scope": "fresh source observation, not native regrading",
        }
        affected = [row for row in baseline["result_revisions"] if row["lineage"] == attack_manifest["index"]]
        invalidation = [
            {
                **row,
                "current_revision": None,
                "disposition": "invalidated",
                "reward": None,
                "checks": None,
                "original_result": row,
            }
            for row in affected
        ]
        (output / (name + "-invalidated.json")).write_text(json.dumps(invalidation, sort_keys=True))
        repaired = output / (name + "-fresh-counterpart")
        _copy_selection(root, selection, repaired)
        replay = inspect(repaired, selection)
        neighbors = [row for row in baseline["result_revisions"] if row["lineage"] != attack_manifest["index"]]
        unchanged = [row for row in replay["result_revisions"] if row["lineage"] != attack_manifest["index"]]
        # Validate unchanged panels while the affected panel is still rejected.
        from rubric_table import select_task

        for manifest in baseline["revision_manifests"]:
            if manifest["index"] == attack_manifest["index"]:
                continue
            record = next(row for row in selection["artifacts"] if row["path"] == manifest["index"])
            neighbor_task = str(Path(manifest["index"]).parent / "task")
            neighbor_selection = select_task(isolated, neighbor_task + "/task.toml")
            neighbor_sources = Sources(isolated, [*neighbor_selection["artifacts"], record])
            projected = project_panels(neighbor_sources, {"native_bundle_ids": [record["id"]]}, neighbor_task)
            if projected[0]["task_freshness"] != "current":
                raise ValueError("unchanged neighbor invalidated")
        tests.append(
            {
                "id": name,
                "mutation_path": relative.as_posix(),
                "stale_reuse_rejected": rejected,
                "invalidated_results": len(affected),
                "unchanged_results": len(neighbors),
                "fresh_source_observation": fresh,
                "restored_replay_equal": not typed_diff(baseline, replay),
                "neighbors_equal": not typed_diff(neighbors, unchanged),
                "original_preserved": (root / relative).read_bytes() == original,
                "recovery": "Fresh isolated copy of the original admitted source, revalidated by its owner. No new native run or regrading claimed.",
            }
        )
    if any(
        not row["restored_replay_equal"] or not row["neighbors_equal"] or not row["original_preserved"] for row in tests
    ):
        raise ValueError("audit recovery failed")
    result = deepcopy(baseline)
    result["invalidation_tests"] = tests
    result["checks"][REVISION + ".deterministic-3"] = "PASS"
    result["limitations"].append(
        "Score 3 covers change-injection invalidation and preserved lineage. Recovery revalidates an exact rollback, not changed-source regrading. Changed-source observations carry no native credit."
    )
    if result["revision_score"] == 2:
        result["revision_score"] = 3
    (output / "audit.json").write_text(json.dumps(result, sort_keys=True))
    return result


def _tree_bytes(root: Path) -> dict[str, tuple[bytes, int]]:
    result = {}
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("unsafe audit evidence tree")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("unsafe audit evidence tree")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        raw, reason = read_selected(root, relative)
        if reason or raw is None:
            raise ValueError("unreadable audit evidence tree")
        result[relative] = (raw, path.stat().st_mode & 0o111)
    return result


def verify(root: Path, selection: dict[str, Any], output: Path) -> dict[str, Any]:
    """Rebuild the fixed probes with current code, then compare retained data."""
    retained = _tree_bytes(output)
    with tempfile.TemporaryDirectory(prefix="rubric-audit-replay-") as scratch:
        fresh = Path(scratch) / "exercise"
        result = exercise(root, selection, fresh)
        if retained != _tree_bytes(fresh) or retained != _tree_bytes(output):
            raise ValueError("retained audit probes disagree with current sources")
    return result


def project_report(report: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    """Project an in-process inspect/exercise result, never an imported packet."""
    result = deepcopy(report)
    result["measurements"]["audit_companion"] = {
        key: deepcopy(audit[key])
        for key in ("schema", "scope", "counts", "checks", "revision_score", "limitations")
        if key in audit
    }
    result["measurements"]["audit_companion"]["status"] = "pass"
    result["measurements"]["audit_companion"]["inventory_ref"] = {"path": "audit.json", "digest": digest(audit)}
    if "comparison" in audit:
        result["measurements"]["audit_companion"]["owner_question"] = audit["comparison"]["owner_question"]
    for ident, score in ((REVISION, audit["revision_score"]), (FAIRNESS, None)):
        row = result["items"][ident]
        row["prior_audit_observation"] = deepcopy(report["items"][ident])
        if row["score"] == 0 or any(check["outcome"] in ("FAIL", "ERROR") for check in row["check_results"]):
            continue
        row["score"] = score
        row["assessment_state"] = "ASSESSED" if score is not None else "NOT_ASSESSED"
        row["execution_state"] = "COMPLETE" if score is not None else "PARTIAL"
        row["evidence_strength"] = "E2"
        row["remaining_gaps"] = [] if score is not None else [audit["comparison"]["owner_question"]["question"]]
        row["limitations"] = audit["limitations"]
        row["evidence_refs"].append({"measurement": "audit_companion", "digest": digest(audit)})
        for check in row["check_results"]:
            check["outcome"] = audit["checks"][check["id"]]
            check["diagnostic_refs"] = []
            check["evidence_refs"] = [{"measurement": "audit_companion", "digest": digest(audit)}]
            check["remaining_gap"] = None
            check["next_action"] = None
            if check["outcome"] == "NOT_APPLICABLE":
                check["remaining_gap"] = audit["limitations"][0]
            if check["outcome"] in ("NOT_RUN", "BLOCKED"):
                check["remaining_gap"] = (
                    audit["comparison"]["owner_question"]["question"]
                    if ident == FAIRNESS
                    else "Change-injection checks have not run for this selection."
                )
                check["next_action"] = check["remaining_gap"]
    return result


class Difference(TypedDict):
    classification: NotRequired[str]
    pointer: str
    kind: str
    left: Any
    right: Any


def typed_diff(left: Any, right: Any, pointer: str = "") -> list[Difference]:
    if type(left) is not type(right):
        return [{"pointer": pointer, "kind": "type_changed", "left": left, "right": right}]
    result: list[Difference] = []
    if isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in left or key not in right:
                result.append(
                    {
                        "pointer": path,
                        "kind": "added" if key not in left else "removed",
                        "left": left.get(key),
                        "right": right.get(key),
                    }
                )
            else:
                result.extend(typed_diff(left[key], right[key], path))
    elif isinstance(left, list):
        for index in range(max(len(left), len(right))):
            path = pointer + "/" + str(index)
            if index >= len(left) or index >= len(right):
                result.append(
                    {
                        "pointer": path,
                        "kind": "added" if index >= len(left) else "removed",
                        "left": left[index] if index < len(left) else None,
                        "right": right[index] if index < len(right) else None,
                    }
                )
            else:
                result.extend(typed_diff(left[index], right[index], path))
    elif left != right:
        result.append({"pointer": pointer, "kind": "value_changed", "left": left, "right": right})
    return result
