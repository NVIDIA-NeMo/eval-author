# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Score a selection with measure.py and write the 23-criterion rubric table.

Usage:
  rubric_table.py --root ROOT --selection SELECTION.json --output NEW_DIRECTORY
  rubric_table.py --root ROOT --verify-existing DIRECTORY

Writes assessment.json, results.md and results.html. Exit 0 when all 23 criteria
have a score, 1 when any is Unassessed, 2 for invalid input or changed evidence.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from eval_author_shared.task_evidence import loads  # noqa: E402
from measure import measure  # noqa: E402
from rubric_evidence_scoring import canonical_definitions, definitions_digest  # noqa: E402

SCOPES = {
    "synthetic-scoring-demonstration": "simulated",
    "recorded-evidence-assessment": "recorded",
    None: "none",
}


def reason(row: dict[str, Any], definition: dict[str, Any]) -> str:
    if row["score"] is not None:
        return definition["scoring"][str(row["score"])]
    return (row.get("remaining_gaps") or ["No evidence selected."])[0]


def rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = canonical_definitions()
    if set(report["items"]) != set(definitions):
        raise ValueError("report does not cover the 23 rubric criteria")
    return [
        {
            "id": ident,
            "title": definition["title"],
            "level": report["items"][ident]["level"],
            "score": report["items"][ident]["score"],
            "strength": report["items"][ident]["evidence_strength"],
            "evidence": SCOPES[report["items"][ident]["assessment_scope"]],
            "reason": reason(report["items"][ident], definition),
        }
        for ident, definition in definitions.items()
    ]


def render(report: dict[str, Any], title: str) -> str:
    table = rows(report)
    scored = [row["score"] for row in table if row["score"] is not None]
    lines = [
        f"# Rubric assessment: {title}",
        "",
        f"Total {sum(scored)}/{3 * len(table)}. Scored {len(scored)} of {len(table)} criteria; "
        f"{len(table) - len(scored)} Unassessed.",
        "",
        "| Criterion | Title | Level | Score | Strength | Evidence | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        score = "U" if row["score"] is None else str(row["score"])
        cells = [row["id"], row["title"], row["level"], score, row["strength"] or "", row["evidence"], row["reason"]]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any], title: str) -> str:
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(value))}</td>"
            for value in (
                row["id"],
                row["title"],
                row["level"],
                "U" if row["score"] is None else row["score"],
                row["strength"] or "",
                row["evidence"],
                row["reason"],
            )
        )
        + "</tr>"
        for row in rows(report)
    )
    style = (
        ":root{color-scheme:light dark}body{font:14px system-ui,sans-serif;margin:24px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #8884;padding:6px;text-align:left;"
        "vertical-align:top}"
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{style}</style></head><body><h1>Rubric assessment: {html.escape(title)}</h1><table><thead><tr>"
        "<th>Criterion</th><th>Title</th><th>Level</th><th>Score</th><th>Strength</th><th>Evidence</th>"
        f"<th>Reason</th></tr></thead><tbody>{body}</tbody></table></body></html>\n"
    )


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def assess(root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    report = measure(root, selection)
    return {
        "schema": "rubric-table-v2",
        "definitions_sha256": definitions_digest(),
        "selection": selection,
        "report": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selection", type=Path)
    mode.add_argument("--verify-existing", type=Path)
    parser.add_argument("--output", type=Path, help="New directory for the table.")
    parser.add_argument("--title", default="Selected task")
    args = parser.parse_args(argv)
    root = args.root.absolute()
    try:
        if args.verify_existing:
            saved = loads((args.verify_existing / "assessment.json").read_bytes())
            current = assess(root, saved["selection"])
            markdown = render(current["report"], saved["title"])
            if encoded({**current, "title": saved["title"]}) != encoded(saved) or (
                (args.verify_existing / "results.md").read_text() != markdown
            ):
                raise ValueError("retained table disagrees with current evidence")
            report = current["report"]
        else:
            current = assess(root, loads(args.selection.read_bytes()))
            report = current["report"]
            markdown = render(report, args.title)
            if args.output:
                args.output.mkdir()
                (args.output / "assessment.json").write_text(encoded({**current, "title": args.title}) + "\n")
                (args.output / "results.md").write_text(markdown)
                (args.output / "results.html").write_text(render_html(report, args.title))
    except (OSError, ValueError, TypeError, KeyError):
        print("Assessment could not complete. Check the selection and its evidence.", file=sys.stderr)
        return 2
    print(markdown, end="")
    return 0 if all(row["score"] is not None for row in report["items"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
