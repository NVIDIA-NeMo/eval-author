# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Assess selected sources and print a private Markdown rubric table."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval-author/scripts"))
from eval_author_shared.task_evidence import loads  # noqa: E402
from measure import measure  # noqa: E402
from rubric_evidence_scoring import canonical_definitions as legacy_definitions  # noqa: E402
from rubric_identity import MAX_FILES, MAX_TOTAL_BYTES, read_selected  # noqa: E402
from rubric_leakage_current import assess_current, canonical_definitions, definitions_digest  # noqa: E402


def cell(value: object) -> str:
    text = " ".join(str(value).split())
    for old, new in (("|", "&#124;"), ("`", "&#96;"), ("[", "&#91;"), ("]", "&#93;")):
        text = html.escape(text) if old == "|" else text
        text = text.replace(old, new)
    return text


def select_task(root: Path, task: str) -> dict[str, Any]:
    path = Path(task)
    if path.is_absolute() or ".." in path.parts or path.name != "task.toml" or path.as_posix() != task:
        raise ValueError("invalid task path")
    records = []
    total = 0
    for target in sorted((root / path.parent).rglob("*")):
        if target.is_symlink():
            raise ValueError("unsafe task source")
        if target.is_dir():
            continue
        relative = target.relative_to(root).as_posix()
        raw, reason = read_selected(root, relative)
        if reason or raw is None:
            raise ValueError("unreadable task source")
        total += len(raw)
        if len(records) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("task source limit")
        digest = hashlib.sha256(raw).hexdigest()
        records.append(
            {
                "id": "source:" + hashlib.sha256(relative.encode()).hexdigest(),
                "path": relative,
                "role": "task" if relative == task else "dependency",
                "digest": digest,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit task directory",
                "source": task,
                "evidence": [{"path": relative, "digest": digest}],
            }
        )
    return {
        "artifacts": records,
        "example_assessment": {
            "task_config_id": "source:" + hashlib.sha256(task.encode()).hexdigest(),
            "reference_scope": "task_directory",
        },
    }


def structured(value: Any) -> str:
    if isinstance(value, dict):
        return (
            "<dl>\n"
            + "\n".join(f"<dt>{cell(key)}</dt><dd>{structured(child)}</dd>" for key, child in sorted(value.items()))
            + "\n</dl>"
        )
    if isinstance(value, list):
        return "<ol>\n" + "\n".join(f"<li>{structured(child)}</li>" for child in value) + "\n</ol>"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    escaped = html.escape(text).replace("[", "&#91;").replace("]", "&#93;").replace("|", "&#124;")
    return "<span>" + escaped + "</span>"


def human_action(row: dict[str, Any]) -> str:
    if row.get("display_review_summary"):
        return row["display_review_summary"]
    if row.get("assessment_scope") == "finite-heldout-refund-template-cache-v1":
        return (
            "Exact new-subject owner and agent qualification required; no prior approval transfers."
            if row["score"] is None
            else "Exact new-subject decisions retained; independent corroboration remains absent."
        )
    if row.get("upper_method_acceptance"):
        return "Exact-packet disclosed-exposure method and retention accepted by local operator. Original lower-anchor decisions remain scoped; no repeated approval needed."
    scoped = row.get("scoped_operator_review")
    if scoped:
        decision = scoped["decision"]
        current = not scoped["revoked"] and decision["decision"] == "approve"
        return (
            "Local operator retained owner answer: “"
            + decision["content"]
            + "”. "
            + (
                "No repeated owner decision is required for this exact scope. Higher evidence prerequisites remain separate."
                if current
                else "This decision is not current approval; resolve its revocation or disposition."
            )
        )
    acceptance = row.get("source_review_acceptance", {})
    if acceptance.get("status") == "accepted":
        basis = "Local operator" if acceptance.get("local_operator_decision_recorded") else "Owner"
        pending = [
            str(check.get("next_action") or check.get("action"))
            for check in row.get("check_results", [])
            if check.get("method") in ("human_review", "review")
            and check.get("outcome") not in ("PASS", "NOT_APPLICABLE")
            and (check.get("next_action") or check.get("action"))
        ]
        message = basis + " qualification accepted for this source review. No repeated owner decision is required."
        return message + (" Separate required review: " + " ".join(dict.fromkeys(pending)) if pending else "")
    reviews = [check for check in row.get("check_results", []) if check.get("method") in ("human_review", "review")]
    completed = [
        f"{check.get('check_id', check.get('id', check['method']))} ({check['outcome']})"
        for check in reviews
        if check.get("outcome") in ("PASS", "FAIL")
    ]
    actions = [
        str(check.get("next_action") or check.get("action"))
        for check in reviews
        if check.get("outcome") not in ("PASS", "FAIL", "NOT_APPLICABLE")
        and (check.get("next_action") or check.get("action"))
    ]
    messages = []
    if completed:
        messages.append("Completed review checks: " + ", ".join(completed) + ".")
    if actions:
        messages.append("Required review: " + " ".join(dict.fromkeys(actions)))
    if completed:
        gaps = list(row.get("remaining_gaps", []))
        gaps.extend(check["remaining_gap"] for check in reviews if check.get("remaining_gap"))
        gaps.extend(check["next_action"] for check in reviews if check.get("next_action"))
        if gaps:
            messages.append("Remaining requirements: " + " ".join(dict.fromkeys(gaps)))
    return " ".join(messages) or "No human action specified by these check records."


def attention(row: dict[str, Any], fallback: str) -> str:
    messages = [row["upper_evidence_summary"]] if row.get("upper_evidence_summary") else []
    for review in row.get("recorded_source_reviews", []):
        decision = review.get("decision", {})
        messages.append(
            f"Unauthenticated recorded review: {decision.get('status', 'unknown')}. {decision.get('rationale', '')}"
        )
    for finding in row.get("findings", []):
        if isinstance(finding, str):
            messages.append("Finding: " + finding)
        elif isinstance(finding, dict):
            messages.append(
                "Finding: " + str(finding.get("message") or finding.get("reason") or finding.get("code") or finding)
            )
    if not messages:
        checks = row.get("check_results", [])
        check = next(
            (
                item
                for state in ("FAIL", "ERROR", "BLOCKED", "PARTIAL", "NOT_RUN")
                for item in checks
                if item.get("outcome") == state and item.get("remaining_gap")
            ),
            None,
        )
        if check:
            messages.append(f"{check['outcome']}: {check.get('action', '')} {check['remaining_gap']}")
    return " ".join(messages) or fallback


def task_credit(report: dict[str, Any], row: dict[str, Any], number: int | str) -> tuple[str, list[str]]:
    subject = report.get("measurements", {}).get("create02_subject")
    if subject and row.get("assessment_scope") == subject["scope"]:
        pairs = subject["pairs"]
        published = [arm["published"]["credit"] for pair in pairs for arm in (pair["baseline"], pair["intervention"])]
        oracle = [
            arm["evidence_oracle"]["credit"] for pair in pairs for arm in (pair["baseline"], pair["intervention"])
        ]
        return (
            f"Published {min(published)}/1 to {max(published)}/1; evidence oracle {min(oracle)}/1 to {max(oracle)}/1 across {len(pairs)} pairs.",
            [
                f'<details id="task-credit-{number}"><summary>Finite refund decision credit</summary>',
                structured(
                    [
                        {
                            key: pair[key]
                            for key in ("instance", "class", "policy", "published_transition", "evidence_transition")
                        }
                        for pair in pairs
                    ]
                ),
                "</details>",
            ],
        )
    native = report.get("measurements", {}).get("example_assessment", {}).get("native", {})
    if "panels" in native:
        labels, details = [], []
        for index, panel in enumerate(native["panels"], 1):
            label, content = task_credit(
                {"measurements": {"example_assessment": {"native": panel}}}, row, f"{number}-{index}"
            )
            if content:
                labels.append(cell(panel["scope"]) + ": " + label)
                details.extend(["<p>" + cell(panel["scope"]) + ": " + cell(panel["counts"]) + "</p>", *content])
        if details:
            details = [
                f'<details id="task-credit-{number}"><summary>Panel task credit</summary>',
                *details,
                "</details>",
            ]
        return "; ".join(labels) or "Not measured here", details
    if native.get("native_execution") is not True or not any(
        ref.get("measurement") in {"example_assessment.native", "example_assessment.native.environment"}
        and ref.get("digest") == native.get("index_digest")
        for ref in row.get("evidence_refs", [])
    ):
        return "Not measured here", []
    controls = native.get("controls", [])
    if not controls:
        return "No per-case task credit recorded", []
    anchor = f"task-credit-{number}"
    details = [
        f'<details id="{anchor}"><summary>Per-case task credit</summary>',
        "<p>Task credit uses each control's own scale. Control status records whether the control met its expectation.</p>",
        "<ul>",
    ]
    observed = []
    rewards = []
    reward_only = native.get("scope") == "eval-author.refund-runtime-observation.v1"
    if reward_only:
        details.insert(
            2, "<p>Task-check totals are not recorded for this panel. Native reward is reported as retained.</p>"
        )
    for index, control in enumerate(controls, 1):
        checks = control.get("checks") or {}
        total, maximum = checks.get("total_credit"), checks.get("max_credit")
        measured = (
            isinstance(total, (int, float))
            and not isinstance(total, bool)
            and isinstance(maximum, (int, float))
            and not isinstance(maximum, bool)
            and math.isfinite(total)
            and math.isfinite(maximum)
            and 0 <= total <= maximum
            and maximum > 0
        )
        credit = f"{total}/{maximum}" if measured else "Unmeasured"
        if measured:
            observed.append((total, maximum))
        elif reward_only and "checks" not in control:
            reward = control.get("reward")
            if type(reward) in (int, float) and math.isfinite(reward) and 0 <= reward <= 1:
                rewards.append(reward)
                credit = f"Native reward {reward}"
        details.append(f'<li><a href="#{anchor}-case-{index}">{cell(control["case"])}: {cell(credit)}</a></li>')
    details.extend(["</ul>", "</details>"])
    for index, control in enumerate(controls, 1):
        details.extend(
            [
                f'<details id="{anchor}-case-{index}"><summary>{cell(control["case"])}: task-credit evidence</summary>',
                structured(
                    {key: control[key] for key in ("case", "status", "checks", "reward", "control") if key in control}
                ),
                "</details>",
            ]
        )
    if len(rewards) == len(controls):
        summary = (
            f"Native reward {min(rewards)} to {max(rewards)} across {len(controls)} native controls. No aggregate."
        )
    elif rewards:
        summary = f"{len(rewards)}/{len(controls)} native controls have a recorded reward. See per-case evidence. No aggregate."
    elif len(observed) == len(controls) and len({maximum for _, maximum in observed}) == 1:
        maximum = observed[0][1]
        summary = f"{min(total for total, _ in observed)}/{maximum} to {max(total for total, _ in observed)}/{maximum}"
        summary += f" across {len(controls)} native controls. No aggregate."
    else:
        summary = (
            f"{len(observed)}/{len(controls)} native controls have task credit. See per-case scales. No aggregate."
        )
    return summary, details


def render(report: dict[str, Any], case_name: str = "Selected task") -> str:
    from rubric_leakage_presentation import presentation

    report = presentation(report)
    definitions = canonical_definitions() if "current_inventory" in report else legacy_definitions()
    if not report["items"] or report["items"].keys() - definitions.keys():
        raise ValueError("invalid criterion inventory")
    for ident, row in report["items"].items():
        if row.get("item_id", ident) != ident:
            raise ValueError("invalid criterion identity")
        ids = [check.get("check_id", check.get("id", check.get("method"))) for check in row.get("check_results", [])]
        named = [key for key in ids if key is not None]
        if len(named) != len(set(named)):
            raise ValueError("duplicate check identity")
    lines = [
        "# Experimental rubric assessment",
        "",
        "Scores apply only to the selected evidence. This table does not establish full task acceptance.",
        "",
        "| Workflow | Criterion | Case | Expected | Observed rubric score | Task credit | Controls run | Evidence | Human action | Reason | Next action |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    details = ["", "## Evidence details"]
    for number, (ident, row) in enumerate(report["items"].items(), 1):
        source = row.get("source") or {}
        workflow = {
            "discover": "Discover",
            "audit": "Audit",
            "environment": "Environment",
            "task-create": "task creation",
            "first-eval": "task creation",
            "first_eval_create": "task creation",
        }.get(source.get("skill_id"), "Rubric")
        if ident.startswith("AC-") or ident in {
            "AUDIT-03",
            "AUDIT-04-B",
            "AUDIT-05-B",
            "AUDIT-05-S03",
            "TRACE-04-A",
            "ENV-03-S01",
            "CREATE-02",
        }:
            workflow = "Anticheat"
        if ident.startswith("task-contract."):
            workflow = "task creation"
        title = definitions.get(ident, {}).get("title", ident)
        if row["score"] is not None and (type(row["score"]) is not int or row["score"] not in range(4)):
            raise ValueError("invalid rubric score")
        score = "Unassessed" if row["score"] is None else str(row["score"])
        if row.get("status") == "NOT_APPLICABLE":
            if row["score"] is not None or not row.get("applicability_reason"):
                raise ValueError("missing applicability reason")
            score = "Not applicable"
        outcomes = [check["outcome"] for check in row.get("check_results", [])]
        if any(
            outcome
            not in {
                "PASS",
                "FAIL",
                "PARTIAL",
                "NOT_RUN",
                "NOT_APPLICABLE",
                "NOT_ASSESSED",
                "BLOCKED",
                "UNKNOWN",
                "ERROR",
            }
            for outcome in outcomes
        ):
            raise ValueError("invalid check outcome")
        counts = Counter(outcomes)
        completed = counts["PASS"] + counts["FAIL"]
        checks = f"{completed}/{len(outcomes)} completed check records. " + (
            ", ".join(f"{name}: {count}" for name, count in sorted(counts.items())) or "No check records"
        )
        if row["score"] is None:
            reason = (
                "A prerequisite blocked a check. See details."
                if counts["BLOCKED"]
                else "A check did not run. See details."
                if counts["NOT_RUN"]
                else "Checks ran with insufficient evidence. See details."
                if completed
                else "No completed check evidence. See details."
            )
        else:
            reason = (
                "Confirmed defect. See details." if row["score"] == 0 else "Recorded criterion outcome. See details."
            )
        reason = row.get("applicability_reason") if score == "Not applicable" else reason
        evidence = row.get("evidence_strength") or "Unassessed"
        details.extend(
            [
                "",
                f'<details id="evidence-{number}">',
                f"<summary>Evidence {number}: {cell(title)}</summary>",
                "<p>Recorded reviews and approval references are unauthenticated. "
                "They do not establish human identity, owner authority, independence or semantic correctness.</p>",
                f"<p>{cell(ident)}</p>",
                structured({key: value for key, value in row.items() if key != "source_review_pre_acceptance"}),
                *(
                    [
                        "<p>Historical pre-acceptance record (not a current owner request)</p>",
                        structured(row["source_review_pre_acceptance"]),
                    ]
                    if "source_review_pre_acceptance" in row
                    else []
                ),
                "</details>",
                "",
            ]
        )
        reason = attention(row, reason)
        pending = next(
            (
                check
                for check in row.get("check_results", [])
                if check.get("outcome") in ("FAIL", "ERROR", "BLOCKED", "PARTIAL", "NOT_RUN")
                and (check.get("next_action") or check.get("action"))
            ),
            {},
        )
        credit, credit_details = task_credit(report, row, number)
        details.extend(credit_details)
        next_actions = [pending.get("next_action") or pending.get("action")]
        if row.get("remaining_gaps") and completed == len(outcomes) and completed:
            next_actions.append("Address remaining requirements: " + " ".join(row["remaining_gaps"]))
        values = (
            workflow,
            title,
            case_name[:48],
            "Not supplied",
            score,
            credit,
            checks,
            evidence,
            human_action(row),
            reason,
            (
                "Retain the accepted source review; recheck its bindings after source changes."
                if row.get("source_review_acceptance", {}).get("status") == "accepted" and not pending
                else " ".join(action for action in next_actions if action)
                or row.get("next_action")
                or "Supply the missing criterion evidence."
            ),
        )
        cells = [cell(value) for value in values]
        if credit_details:
            cells[5] += f" [Per-case task credit](#task-credit-{number})"
        cells[7] += f". [Evidence {number}](#evidence-{number})"
        lines.append("| " + " | ".join(cells) + " |")
    for ident, undefined in report.get("undefined_criteria", {}).items():
        lines.append(f"\n**{cell(ident)} — Unassessed:** {cell(undefined['reason'])}")
    if "legacy_evidence_items" in report:
        lines.append(
            "\nLegacy Anticheat rows are retained in assessment.json as evidence history, not additional current scores."
        )
    lines.extend(
        [
            "",
            "Coverage and diversity are outside the populated rubric.",
            "Check records are not native trial counts. Partial and failed execution remain separate.",
            "Expected scores are withheld from assessment. Compare a frozen catalog only in a separate review.",
        ]
    )
    from copy import deepcopy

    metadata = deepcopy({key: value for key, value in report.items() if key != "items"})
    if "legacy_evidence_items" in metadata:
        metadata["legacy_evidence_items"] = {"full_evidence": "assessment.json#/report/legacy_evidence_items"}
    example = metadata.get("measurements", {}).get("example_assessment", {})
    for key in ("configuration", "source_closure", "source_reviews"):
        if key in example:
            link = "assessment.json#/report/measurements/example_assessment/" + key
            example[key] = {"full_evidence": link}
            label = "Historical review packet and current acceptance" if key == "source_reviews" else key
            details.append(f'<p><a href="{link}">{cell(label)} (full JSON)</a>.</p>')
    native = metadata.get("measurements", {}).get("example_assessment", {}).get("native", {})
    if "panels" in native:
        from copy import deepcopy

        metadata = deepcopy(metadata)
        metadata["measurements"]["example_assessment"]["native"] = {
            **{key: value for key, value in native.items() if key != "panels"},
            "panels": [
                {
                    key: panel[key]
                    for key in (
                        "scope",
                        "index_digest",
                        "counts",
                        "task_freshness",
                        "human_review",
                        "independence",
                        "full_task_acceptance",
                    )
                    if key in panel
                }
                for panel in native["panels"]
            ],
            "full_evidence": "assessment.json#/report/measurements/example_assessment/native/panels",
        }
        details.append(
            '<p><a href="assessment.json">Full native panel inventories, observations and source identities (JSON)</a>. Per-case credit and criterion findings remain above.</p>'
        )
    for companion in ("upper_environment", "upper_anticheat_contract", "upper_method_review"):
        if companion in metadata.get("measurements", {}):
            value = metadata["measurements"][companion]
            link = "assessment.json#/report/measurements/" + companion
            metadata["measurements"][companion] = {
                key: value[key] for key in ("scope", "status", "boundary", "source_review_scope") if key in value
            }
            metadata["measurements"][companion]["full_evidence"] = link
            details.append(
                f'<p><a href="{link}">{cell(companion)} controls, exposure and retained evidence (JSON)</a>.</p>'
            )
    if "audit_companion" in report.get("measurements", {}):
        details.append(
            '<p><a href="audit.json">Full Audit result manifest, change probes and owner question (JSON)</a>.</p>'
        )
    if metadata:
        details.extend(
            [
                '<details id="measurement-details"><summary>Measurements and report-level unknowns</summary>',
                structured(metadata),
                "</details>",
            ]
        )
    lines.extend(details)
    return "\n".join(lines) + "\n"


def render_html(markdown: str) -> str:
    """Render only this module's escaped table format, without external assets."""
    main, details = markdown.split("## Evidence details", 1)
    rows = [line.split(" | ") for line in main.splitlines() if line.startswith("| ")][1:]
    body = [
        "<h1>Experimental rubric assessment</h1>",
        "<p>Scores apply only to the selected evidence. This table does not establish full task acceptance.</p>",
        "<table><caption>Criterion results</caption><thead><tr>",
        '<th scope="col">Criterion / case</th><th scope="col">Result / controls</th>',
        '<th scope="col">Findings / blockers</th><th scope="col">Human action</th><th scope="col">Next action</th>',
        "</tr></thead><tbody>",
    ]
    for parts in rows:
        cells = [part.removeprefix("| ").removesuffix(" |") for part in parts]
        workflow, title, case, expected, score, credit, checks, evidence, human, reason, action = cells
        evidence = re.sub(r"\[Evidence (\d+)\]\(#evidence-(\d+)\)", r'<a href="#evidence-\2">Evidence \1</a>', evidence)
        credit = re.sub(
            r"\[Per-case task credit\]\(#task-credit-(\d+)\)",
            r'<a href="#task-credit-\1">Per-case task credit</a>',
            credit,
        )
        values = (
            ("Criterion / case", f"<small>{workflow}</small><h2>{title}</h2><p>Case: {case}</p>"),
            (
                "Result / controls",
                f'<p class="score">Observed rubric score: <strong>{score}</strong></p>'
                f"<p>Expected: {expected}</p><p>Task credit: {credit}</p><p>{checks}</p><p>{evidence}</p>",
            ),
            ("Findings / blockers", reason),
            ("Human action", human),
            ("Next action", action),
        )
        body.append("<tr>" + "".join(f'<td data-label="{label}">{value}</td>' for label, value in values) + "</tr>")
    body.extend(
        [
            "</tbody></table>",
            "<p>Coverage and diversity are outside the populated rubric. Check records are not native trial counts.</p>",
            "<p>Expected scores are withheld from assessment. Compare a frozen catalog only in a separate review.</p>",
            *[
                "<p>" + html.escape(line.strip()) + "</p>"
                for line in main.splitlines()
                if "METHOD-J1" in line or line.startswith("Legacy Anticheat")
            ],
            "<h2>Evidence details</h2>",
            details,
        ]
    )
    style = """
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 24px; max-width: 1600px; font: 16px/1.5 system-ui, sans-serif; color: #17212b; background: #fff; }
h1 { font-size: 1.65rem; } h2 { font-size: 1.05rem; margin: .3em 0; }
table { width: 100%; table-layout: fixed; border-collapse: collapse; }
caption { text-align: left; font-weight: 700; padding: 8px 0; }
th, td { padding: 12px; border: 1px solid #cbd2d9; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #edf2f7; } td p { margin: 0 0 .7em; } .score { font-size: 1.1rem; }
a { color: #064da1; } a:focus, summary:focus { outline: 3px solid #ad6400; }
details { margin: 16px 0; border: 1px solid #cbd2d9; padding: 12px; overflow-wrap: anywhere; }
summary { cursor: pointer; font-weight: 600; } dl, ol { margin: 8px 0; padding-left: 16px; }
dt { font-weight: 600; } dd { margin: 0 0 12px 8px; } dd span { white-space: pre-wrap; }
@media (max-width: 900px) {
 body { padding: 12px; } table, tbody, tr, td { display: block; width: 100%; }
 thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
 tr { border: 1px solid #cbd2d9; margin: 0 0 20px; } td { border: 0; padding: 10px 12px; }
 td::before { content: attr(data-label); display: block; font-weight: 700; color: #425466; }
 dl, ol { padding-left: 8px; } dd { margin-left: 4px; }
}
"""
    script = """
function revealEvidence() {
 const target = document.getElementById(location.hash.slice(1));
 if (target && target.tagName === 'DETAILS') { target.open = true; target.scrollIntoView(); }
}
addEventListener('hashchange', revealEvidence); revealEvidence();
"""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Experimental rubric assessment</title><style>"
        + style
        + "</style></head><body><main>"
        + "\n".join(body)
        + "</main><script>"
        + script
        + "</script></body></html>\n"
    )


def assess_table(root: Path, selected: dict[str, Any], task: str | None) -> dict[str, Any]:
    report = measure(root, selected)
    report = assess_current(root, selected, report)
    from rubric_create02_subject import assess as assess_create02_subject

    report = assess_create02_subject(root, selected, report)
    if task:
        definitions = canonical_definitions()
        if report["items"].keys() != definitions.keys():
            raise ValueError("incomplete emitted criterion inventory")
        for ident, definition in definitions.items():
            row = report["items"][ident]
            ids = [check.get("check_id", check.get("id")) for check in row["check_results"]]
            if ids != [check["id"] for check in definition["checks"]] or row.get("source") != definition.get("source"):
                raise ValueError("invalid emitted check or source inventory")
    return report


def read_private(path: Path) -> bytes:
    raw, reason = read_selected(path.parent, path.name)
    if reason or raw is None:
        raise ValueError("unreadable retained assessment")
    return raw


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--selection", type=Path)
    selection.add_argument("--task", help="Root-relative task.toml. Inspect this task directory without running it.")
    selection.add_argument(
        "--verify-existing", type=Path, help="Recheck saved JSON and Markdown against current sources."
    )
    parser.add_argument("--output", type=Path, help="New private report directory outside the task directory.")
    args = parser.parse_args(argv)
    try:
        root = args.root.absolute()
        if args.verify_existing:
            if args.output:
                raise ValueError("verification is read-only")
            bundle = args.verify_existing.absolute()
            saved = loads(read_private(bundle / "assessment.json"))
            task, selected = saved["task"], saved["selection"]
            if task and encoded(select_task(root, task)) != encoded(selected):
                raise ValueError("task inventory changed")
        else:
            task = args.task
            selected = select_task(root, task) if task else loads(read_private(args.selection.absolute()))
        report = assess_table(root, selected, task)
        audit = None
        if "audit_companion" in selected:
            from rubric_audit_companion import selected as selected_audit

            audit = selected_audit(root, selected)
            if args.verify_existing and encoded(loads(read_private(bundle / "audit.json"))) != encoded(audit):
                raise ValueError("retained audit manifest disagrees")
        current = {
            "schema": "rubric-table-v1",
            "root": str(root),
            "task": task,
            "definitions_sha256": definitions_digest(),
            "selection": selected,
            "report": report,
        }
        case_name = Path(task).parent.name if task else "Selected package"
        output = render(report, case_name or "Selected task")
        page = render_html(output)
        if args.verify_existing:
            if encoded(saved) != encoded(current) or read_private(bundle / "results.md").decode() != output:
                raise ValueError("retained assessment disagrees with current sources")
            if read_private(bundle / "results.html").decode() != page:
                raise ValueError("retained HTML disagrees with current sources")
        elif args.output:
            destination = args.output.absolute()
            if task and destination.is_relative_to(root / Path(task).parent):
                raise ValueError("output overlaps task sources")
            if any(path.is_symlink() for path in (destination, *destination.parents)):
                raise ValueError("unsafe output path")
            destination.mkdir(mode=0o700)
            if audit is not None:
                (destination / "audit.json").write_text(encoded(audit) + "\n")
            (destination / "assessment.json").write_text(encoded(current) + "\n")
            (destination / "results.md").write_text(output)
            (destination / "results.html").write_text(page)
    except (OSError, ValueError, TypeError, KeyError):
        print("Assessment could not complete. Check the selection and access to its evidence.")
        return 2
    print(output, end="")
    return (
        0
        if all(row["score"] is not None for row in report["items"].values())
        and all(row.get("status") in ("pass", "equal") for row in report["measurements"].values())
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
