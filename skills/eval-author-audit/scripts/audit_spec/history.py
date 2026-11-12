# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reviewed audit snapshots and the comparison between consecutive audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, NonNegativeInt

# History lives beside the per-audit folder, not in it: a fresh audit sets that folder aside.
HISTORY_DIR = ".eval-author/.working/audit-history"
LEGACY_HISTORY_DIR = ".eval-author/.working/audit/history"
VERSION = 1
KINDS = ("capability", "tool", "failure_case")


class KindSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[str]
    covered: NonNegativeInt
    basis: Literal["demonstrated", "mapped"]
    criteria: str


class Snapshot(BaseModel):
    """Every field the report renderer reads, so a malformed record is skipped instead of crashing a later report."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    audit_id: str
    agent: str
    recorded_at: str
    commit: str | None
    dirty: bool
    content_sha256: str
    report_sha256: str
    runs: list[str]
    kinds: dict[Literal["capability", "tool", "failure_case"], KindSnapshot]


def criteria_fingerprint(rows: list[dict]) -> str:
    """Digest of what each item requires, so a changed expectation is not mistaken for unchanged scope."""
    # Provenance notes say where an item came from, not what it requires.
    defining = sorted(
        ({key: value for key, value in row.items() if key != "source_refs"} for row in rows),
        key=lambda row: row["name"],
    )
    return hashlib.sha256(json.dumps(defining, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def summarize(items: list[dict], covered: set[str], measured: set[str], mapped: set[str]) -> dict:
    """Per-kind declared items and covered count, from run evidence once measured, else from task mappings."""
    kinds = {}
    for kind in KINDS:
        rows = [row for row in items if row["kind"] == kind]
        names = sorted(row["name"] for row in rows)
        demonstrated = kind in measured
        hits = covered if demonstrated else mapped
        kinds[kind] = {
            "items": names,
            "covered": sum(name in hits for name in names),
            "basis": "demonstrated" if demonstrated else "mapped",
            "criteria": criteria_fingerprint(rows),
        }
    return kinds


def snapshot(
    *, agent: str, kinds: dict, runs: list[str], identity: dict, content_sha256: str, report_sha256: str, now: datetime
) -> dict:
    return {
        "version": VERSION,
        # The reviewed content already binds every input digest, so it identifies the audit.
        "audit_id": content_sha256,
        "agent": agent,
        "recorded_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        # A repository without git history has no commit to record.
        "commit": identity.get("commit"),
        "dirty": identity.get("dirty", False),
        "content_sha256": content_sha256,
        "report_sha256": report_sha256,
        # Measurement inputs are per kind, so one run can appear several times.
        "runs": sorted(set(runs)),
        "kinds": kinds,
    }


def path_for(repo: Path, audit_id: str) -> Path:
    return repo / HISTORY_DIR / f"{audit_id}.json"


def save(repo: Path, value: dict) -> bool:
    """Write an immutable snapshot. An audit already recorded in either location is left untouched."""
    target = path_for(repo, value["audit_id"])
    if target.exists() or (repo / LEGACY_HISTORY_DIR / target.name).exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, target)
    return True


def load(repo: Path) -> list[dict]:
    """Retained snapshots, skipping files that are not readable snapshots."""
    found: dict[str, dict] = {}
    # The earlier location is read too, so audits recorded before the move still serve as baselines.
    for folder in (LEGACY_HISTORY_DIR, HISTORY_DIR):
        for path in sorted((repo / folder).glob("*.json")):
            try:
                value = Snapshot.model_validate(json.loads(path.read_text())).model_dump()
            except (OSError, ValueError):
                continue
            found[value["audit_id"]] = value
    return list(found.values())


def previous(snapshots: list[dict], agent: str, audit_id: str) -> dict | None:
    """The audit before this one for the same agent, ordered by recorded time.

    An audit that is already retained keeps its original baseline, so refreshing it after a later
    audit was saved does not reverse the comparison.
    """
    same = [row for row in snapshots if row["agent"] == agent]
    order = lambda row: (row["recorded_at"], row["audit_id"])  # noqa: E731
    own = next((row for row in same if row["audit_id"] == audit_id), None)
    earlier = [row for row in same if row["audit_id"] != audit_id and (own is None or order(row) < order(own))]
    return max(earlier, key=order) if earlier else None


def ratio(covered: int, total: int) -> float | None:
    return covered / total if total else None


def relative_change(new: int, old: int) -> float | None:
    return (new - old) / old if old else None


def compare(current: dict, earlier: dict) -> dict:
    """Compare one kind across two audits. Method or scope differences suppress the coverage change."""
    added = sorted(set(current["items"]) - set(earlier["items"]))
    removed = sorted(set(earlier["items"]) - set(current["items"]))
    if current["basis"] != earlier["basis"]:
        status = "method"
    elif added or removed or current["criteria"] != earlier["criteria"]:
        status = "scope"
    else:
        status = "comparable"
    now_total, then_total = len(current["items"]), len(earlier["items"])
    now_ratio, then_ratio = ratio(current["covered"], now_total), ratio(earlier["covered"], then_total)
    return {
        "status": status,
        "added": added,
        "removed": removed,
        "total_change": now_total - then_total,
        "total_relative": relative_change(now_total, then_total),
        "covered_change": current["covered"] - earlier["covered"],
        "covered_relative": relative_change(current["covered"], earlier["covered"]),
        "points": None if now_ratio is None or then_ratio is None else (now_ratio - then_ratio) * 100,
    }


def main(argv: list[str] | None = None) -> int:
    """Record the reviewed audit as a snapshot. Run once after findings acceptance."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--content", type=Path, default=Path(".eval-author/.working/audit/report-content.json"))
    args = parser.parse_args(argv)
    # Content paths are checked for containment against an absolute root, so a relative --repo must be resolved first.
    repo = args.repo.resolve()
    sys.path.insert(0, str(Path(__file__).parent))
    import report_content as content

    renderer = content.load_module("audit_markdown_renderer", Path(__file__).with_name("render_report.py"))

    try:
        model = content.load(args.content, repo)
        report = content.local(repo, renderer.OUTPUT)
        covered, measured, inputs = renderer.measurements(model)
        value = snapshot(
            agent=model.spec["agent"],
            kinds=summarize(
                model.spec["items"],
                covered,
                measured,
                {row["item"] for row in model.data["task_mappings"] if row["tasks"]},
            ),
            runs=[row["subject"]["run_id"] for row in inputs],
            identity=model.data["identity"],
            content_sha256=model.digest,
            report_sha256=content.sha(content.read_bytes(report)),
            now=datetime.now(timezone.utc),
        )
        recorded = save(repo, value)
    except (content.ContentError, OSError, ValueError) as exc:
        message = exc.errors if isinstance(exc, content.ContentError) else str(exc)
        print(json.dumps({"recorded": False, "errors": message}, indent=2))
        return 1
    print(
        json.dumps(
            {"recorded": recorded, "audit_id": value["audit_id"], "path": str(path_for(repo, value["audit_id"]))},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
