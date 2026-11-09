# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reviewed audit snapshots and the comparison between consecutive audits."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = ".eval-author/.working/audit/history"
VERSION = 1
KINDS = ("capability", "tool", "failure_case")


def summarize(items: list[dict], covered: set[str], measured: set[str], mapped: set[str]) -> dict:
    """Per-kind declared items and covered count, from run evidence once measured, else from task mappings."""
    kinds = {}
    for kind in KINDS:
        names = sorted(row["name"] for row in items if row["kind"] == kind)
        demonstrated = kind in measured
        hits = covered if demonstrated else mapped
        kinds[kind] = {
            "items": names,
            "covered": sum(name in hits for name in names),
            "basis": "demonstrated" if demonstrated else "mapped",
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
        "commit": identity["commit"],
        "dirty": identity["dirty"],
        "content_sha256": content_sha256,
        "report_sha256": report_sha256,
        "runs": sorted(runs),
        "kinds": kinds,
    }


def path_for(repo: Path, audit_id: str) -> Path:
    return repo / HISTORY_DIR / f"{audit_id}.json"


def save(repo: Path, value: dict) -> bool:
    """Write an immutable snapshot. An audit already recorded is left untouched."""
    target = path_for(repo, value["audit_id"])
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, target)
    return True


def _valid(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == VERSION
        and all(isinstance(value.get(key), str) for key in ("audit_id", "agent", "recorded_at"))
        and isinstance(value.get("kinds"), dict)
        and all(
            isinstance(value["kinds"].get(kind), dict) and {"items", "covered", "basis"} <= set(value["kinds"][kind])
            for kind in KINDS
        )
    )


def load(repo: Path) -> list[dict]:
    """Retained snapshots, skipping files that are not readable snapshots."""
    found = []
    for path in sorted((repo / HISTORY_DIR).glob("*.json")):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if _valid(value):
            found.append(value)
    return found


def previous(snapshots: list[dict], agent: str, audit_id: str) -> dict | None:
    """The latest other audit of the same agent, ordered by recorded time."""
    earlier = [row for row in snapshots if row["agent"] == agent and row["audit_id"] != audit_id]
    if not earlier:
        return None
    return max(earlier, key=lambda row: (row["recorded_at"], row["audit_id"]))


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
    elif added or removed:
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
    sys.path.insert(0, str(Path(__file__).parent))
    import report_content as content

    renderer = content.load_module("audit_markdown_renderer", Path(__file__).with_name("render_report.py"))

    try:
        model = content.load(args.content, args.repo)
        report = content.local(args.repo, renderer.OUTPUT)
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
        recorded = save(args.repo, value)
    except (content.ContentError, OSError, ValueError) as exc:
        message = exc.errors if isinstance(exc, content.ContentError) else str(exc)
        print(json.dumps({"recorded": False, "errors": message}, indent=2))
        return 1
    print(
        json.dumps(
            {"recorded": recorded, "audit_id": value["audit_id"], "path": str(path_for(args.repo, value["audit_id"]))},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
