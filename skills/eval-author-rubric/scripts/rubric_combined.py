# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compose independently validated native panels without combining scores."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator


def select_bundle(root: Path, bundle: str) -> dict[str, Any]:
    from rubric_example_native import select_bundle as select_original
    from rubric_identity import read_selected

    path = (Path(bundle) / "native-index.json").as_posix()
    raw, reason = read_selected(root, path)
    if reason is not None or raw is None:
        return select_original(root, bundle)
    schema = json.loads(raw).get("schema")
    if schema == "eval-author.refund-heldout-panel.v1":
        from rubric_refund_holdout import admission as admit_holdout

        with registry_for(raw):
            if admit_holdout(root / bundle) != json.loads(raw):
                raise ValueError("changed held-out index")
    elif schema == "eval-author.refund-access-panel.v1":
        from rubric_refund_access import admission

        admission(raw, root / bundle)
    else:
        return select_original(root, bundle)
    digest = hashlib.sha256(raw).hexdigest()
    ident = "native-panel:" + digest
    return {
        "native_bundle_id": ident,
        "artifacts": [
            {
                "id": ident,
                "path": path,
                "role": "dependency",
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit native bundle",
                "source": path,
                "evidence": [{"path": path, "digest": digest}],
            }
        ],
    }


def _union(left: list[Any], right: list[Any]) -> list[Any]:
    result = []
    for value in [*left, *right]:
        if value not in result:
            result.append(deepcopy(value))
    return result


def _replaced_absences(before: dict[str, Any], candidate: dict[str, Any], panel: dict[str, Any]) -> set[str]:
    from rubric_example_assessment import _ensure_rows
    from rubric_report import PARTIAL_HELPERS
    from rubric_scoring import ITEM_LIMITATIONS

    defaults: dict[str, Any] = {}
    _ensure_rows(defaults)
    absent = {
        defaults["AC-02"]["limitations"][0],
        *(
            ITEM_LIMITATIONS["provenance." + name]
            for name in ("observed-configuration", "runtime-route", "solver-tool-access")
        ),
    }
    replaced = set()
    for old, changed in zip(before["check_results"], candidate["check_results"], strict=True):
        gap = old.get("remaining_gap")
        if (
            old.get("outcome") in {"FAIL", "ERROR"}
            or changed.get("outcome") not in {"PASS", "PARTIAL", "NOT_APPLICABLE"}
            or changed.get("remaining_gap") == gap
            or not any(
                isinstance(ref, dict)
                and ref.get("digest") == panel["index_digest"]
                and ref.get("measurement") in {"example_assessment.native", "example_assessment.native.environment"}
                and ref not in old.get("evidence_refs", [])
                for ref in changed.get("evidence_refs", [])
            )
        ):
            continue
        if gap in absent or gap == "No qualified evidence consumed for: " + old.get("expected_evidence", ""):
            replaced.add(gap)
        if (
            old["id"].startswith("provenance.observed-configuration.")
            and old["id"] in PARTIAL_HELPERS
            and changed.get("outcome") == "PASS"
            and gap == PARTIAL_HELPERS[old["id"]][1] + " Still required: " + old.get("expected_evidence", "")
        ):
            replaced.add(gap)
        replaced.update(
            value
            for value in before.get("limitations", [])
            if value in absent and value not in candidate.get("remaining_gaps", [])
        )
    # An unresolved or failed check can still need the same baseline evidence.
    return replaced - {check.get("remaining_gap") for check in candidate["check_results"]}


def apply_panels(rows: dict[str, Any], panels: list[dict[str, Any]], apply: Callable[..., None]) -> None:
    baseline = deepcopy(rows)
    superseded: dict[str, set[str]] = {}
    rank = {"FAIL": 7, "ERROR": 6, "BLOCKED": 5, "PARTIAL": 4, "PASS": 3, "NOT_APPLICABLE": 2, "NOT_RUN": 1}
    for panel in sorted(panels, key=lambda item: item["index_digest"]):
        if panel.get("scope") == "eval-author.refund-heldout-panel.v1":
            # This owner supplies measurements, not a qualified score judgment.
            continue
        projected = deepcopy(baseline)
        if panel.get("scope") == "eval-author.refund-access-panel.v1":
            from rubric_refund_access import apply as apply_access

            apply_access(projected, panel)
        else:
            apply(projected, panel)
        for ident, candidate in projected.items():
            before = baseline[ident]
            if candidate == before:
                continue
            target = rows[ident]
            replaced = _replaced_absences(before, candidate, panel)
            superseded.setdefault(ident, set()).update(replaced)
            target.setdefault("native_panel_assessments", []).append(
                {
                    "scope": panel["scope"],
                    "index_digest": panel["index_digest"],
                    "score": candidate["score"],
                    "checks": [
                        {"id": check["id"], "outcome": check["outcome"]} for check in candidate["check_results"]
                    ],
                }
            )
            # Each owner scores its own scope. A lower current result is a cap,
            # never a contribution to a total or an implicit supersession.
            for key, value in candidate.items():
                if key == "check_results" or (value == before.get(key) and key != "score"):
                    continue
                if isinstance(value, list):
                    target[key] = _union(target.get(key, []), value)
                elif key == "score":
                    scores = [
                        entry["score"] for entry in target["native_panel_assessments"] if entry["score"] is not None
                    ]
                    if before.get("score") == 0:
                        scores.append(0)
                    target[key] = min(scores) if scores else None
                elif key == "execution_state" and target.get(key) == "PARTIAL":
                    continue
                else:
                    target[key] = deepcopy(value)
            for old, changed, merged in zip(
                before["check_results"], candidate["check_results"], target["check_results"], strict=True
            ):
                if old == changed:
                    continue
                for key, value in changed.items():
                    if value == old.get(key):
                        continue
                    if isinstance(value, list):
                        merged[key] = _union(merged.get(key, []), value)
                    elif key == "outcome":
                        if (old.get(key) == merged.get(key) and merged.get(key) not in {"FAIL", "ERROR"}) or rank.get(
                            value, 0
                        ) > rank.get(merged.get(key), 0):
                            merged[key] = value
                    elif key == "remaining_gap" and merged.get(key) in replaced:
                        merged[key] = deepcopy(value)
                    elif key == "remaining_gap" and merged.get(key) and value:
                        merged[key] = " ".join(dict.fromkeys((merged[key], value)))
                    elif merged.get("outcome") != "FAIL" or value is not None:
                        merged[key] = deepcopy(value)
    for ident, replaced in superseded.items():
        for key in ("limitations", "remaining_gaps"):
            if key in rows[ident]:
                rows[ident][key] = _union([], [value for value in rows[ident][key] if value not in replaced])


@contextmanager
def registry_for(raw: bytes) -> Iterator[None]:
    """Use only registry roots explicitly configured by the invoking process."""
    from rubric_identity import read_selected

    roots = os.environ.get("EVAL_AUTHOR_NATIVE_REGISTRIES")
    previous = os.environ.get("EVAL_AUTHOR_NATIVE_REGISTRY")
    if roots:
        matched = []
        name = hashlib.sha256(raw).hexdigest() + ".json"
        for value in roots.split(os.pathsep):
            root = Path(value).absolute()
            if not value or any(p.is_symlink() for p in (root, *root.parents)):
                raise ValueError("unsafe native registry")
            content, reason = read_selected(root, name)
            if reason is None and content == raw:
                matched.append(root)
        if not matched:
            raise ValueError("native registry admission missing")
        os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] = str(matched[0])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("EVAL_AUTHOR_NATIVE_REGISTRY", None)
        else:
            os.environ["EVAL_AUTHOR_NATIVE_REGISTRY"] = previous


def project_panels(sources: Any, selector: dict[str, Any], task_path: str) -> list[dict[str, Any]]:
    from rubric_example_native import project

    ids = selector.get("native_bundle_ids")
    if ids is None:
        ident = selector.get("native_bundle_id")
        if ident is None:
            return []
        return project_panels(sources, {"native_bundle_ids": [ident]}, task_path)
    if (
        "native_bundle_id" in selector
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(ident, str) or not ident for ident in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("invalid native panel selection")
    panels = []
    for ident in sorted(ids):
        records = [row for row in sources.identity["records"] if row["id"] == ident]
        if len(records) != 1:
            raise ValueError("missing native panel")
        raw = sources.source(records[0]["path"])
        if raw is None:
            raise ValueError("unavailable native panel")
        with registry_for(raw):
            index = json.loads(raw)
            if not isinstance(index, dict):
                raise ValueError("invalid native panel index")
            schema = index.get("schema")
            if schema == "eval-author.refund-heldout-panel.v1":
                from rubric_refund_holdout import admission as admit_holdout
                from rubric_refund_holdout import validate as validate_holdout

                bundle = sources.root / Path(records[0]["path"]).parent
                document = admit_holdout(bundle)
                if document != json.loads(raw):
                    raise ValueError("changed held-out index")
                panel = validate_holdout(bundle, document, sources, task_path)
                panel["task_freshness"] = "current"
                panel["index_digest"] = hashlib.sha256(raw).hexdigest()
                panel["observation_status"] = (
                    "complete"
                    if panel["counts"]["completed"] == panel["counts"]["declared"]
                    and panel["counts"]["infrastructure_errors"] == 0
                    else "incomplete"
                )
                panel["expected_agreement"] = (
                    panel["observation_status"] == "complete" and panel["counts"]["disagreements"] == 0
                )
                for control in panel["controls"]:
                    complete = control["status"] in {"matched", "disagreement"}
                    control["observation_status"] = "complete" if complete else "incomplete"
                    control["expected_agreement"] = control["status"] == "matched" if complete else None
                panel["qualification_requirements"] = [
                    requirement
                    for requirement in panel["qualification_requirements"]
                    if requirement
                    != "Integrate this owning validator through the ordinary combined selection and replay path."
                ]
            elif schema == "eval-author.refund-access-panel.v1":
                from rubric_refund_access import admission, validate

                bundle = sources.root / Path(records[0]["path"]).parent
                panel = validate(bundle, admission(raw, bundle), sources, task_path)
                panel["index_digest"] = hashlib.sha256(raw).hexdigest()
            else:
                panel = project(sources, {"native_bundle_id": ident}, task_path)
        if panel is None:
            raise ValueError("missing native panel projection")
        panels.append(panel)
    return panels


def native_measurement(panels: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not panels:
        return None
    if len(panels) == 1 and panels[0].get("scope") != "eval-author.refund-heldout-panel.v1":
        return panels[0]
    return {
        "scope": "combined-native-panels-v1",
        "native_execution": True,
        "index_digest": hashlib.sha256(json.dumps([p["index_digest"] for p in panels]).encode()).hexdigest(),
        "panels": panels,
        "counts": {
            key: sum(panel["counts"].get(key, 0) for panel in panels)
            for key in sorted({key for panel in panels for key in panel["counts"]})
        },
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
    }


def runtime_observation(native: dict[str, Any] | None) -> dict[str, Any] | None:
    panels = native.get("panels", [native]) if native else []
    observations = [panel for panel in panels if panel["scope"] == "eval-author.refund-runtime-observation.v1"]
    if len(observations) > 1:
        raise ValueError("multiple runtime observations need explicit reconciliation")
    return observations[0] if observations else None
