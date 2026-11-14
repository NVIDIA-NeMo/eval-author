# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run the existing fixture demos and render their retained results together."""

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def run(output, name, script, *args):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / script), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    (output / f"{name}.stdout").write_text(completed.stdout)
    (output / f"{name}.stderr").write_text(completed.stderr)
    if completed.returncode:
        raise ValueError(f"{name} failed; inspect retained stderr")
    return completed.stdout


def collect(output):
    (output / "identity").mkdir(mode=0o700)
    identity = json.loads(run(output, "identity", "run_rubric_measurements.py", "--format", "json"))
    (output / "identity/panel.json").write_text(json.dumps(identity, indent=2, allow_nan=False) + "\n")
    run(output, "provenance", "run_rubric_provenance_demo.py", "--run-dir", output / "provenance")
    run(output, "task-contract", "run_task_contract_demo.py", "--output", output / "task-contract")
    run(
        output, "integrity", "run_verification_integrity_demo.py", "--format", "json", "--run-dir", output / "integrity"
    )
    run(output, "scorer", "run_scorer_controls.py", "--rubric", "--output", output / "scorer")
    run(output, "scorer-replay", "run_scorer_controls.py", "--rubric", "--verify-existing", output / "scorer")
    run(output, "criterion-scoring", "run_criterion_scoring_demo.py", "--output", output / "criterion-scoring")
    return summarize_reports(output)


def summarize_reports(output: Path) -> dict:
    identity = read(output / "identity/panel.json")
    provenance = read(output / "provenance/panel.json")
    task_contract = read(output / "task-contract/panel.json")
    if task_contract["matched_count"] != task_contract["case_count"]:
        raise ValueError("task contract demo mismatch")
    demonstration = read(output / "criterion-scoring/summary.json")
    inventory = []
    references = ROOT / "skills/eval-author-rubric/references"
    for name in ("artifact-configuration-provenance", "task-contract-authority"):
        for item in read(references / f"{name}.json")["items"]:
            inventory.append({"id": item["id"], "area": name})
    inventory.extend({"id": f"AC-0{number}", "area": "anti-cheat"} for number in range(1, 5))
    demonstrated = {"DISC-01-A", "DISC-01-B", "DISC-01-C", "AC-02", "AC-03", "CREATE-04"}
    for item in inventory:
        item["demonstration_status"] = (
            "Source-bound canonical score demonstrated"
            if item["id"] in demonstrated
            else "Integrity groups only; canonical Unassessed"
            if item["id"] == "CREATE-07"
            else "No source-bound canonical score demonstrated"
        )
        item["scoring_rule_status"] = "Synthetic 0/1/2/3/missing demonstrated"
    if len(inventory) != 18 or {row["id"] for row in inventory} != set(demonstration["results"]["missing"]):
        raise ValueError("criterion inventory mismatch")
    if identity["status"] != "passed" or provenance["status"] != "passed":
        raise ValueError("identity or provenance demo mismatch")
    original = (output / "scorer.stdout").read_text()
    repeated = (output / "scorer-replay.stdout").read_text()
    panel = read(output / "scorer/panel.json")
    integrity = read(output / "integrity/panel.json")
    scorer: dict = {
        "attempted_controls": panel["control_count"],
        "matched_controls": panel["matched_count"],
        "replay_equal": original == repeated,
    }
    for arm in ("baseline", "reviewed"):
        criterion = read(output / f"scorer/rubric-{arm}.json")["items"]["AC-02"]
        scorer[arm] = {key: criterion[key] for key in ("score", "evidence_strength", "remaining_gaps")}
        rows = [row for row in panel["cases"] if row["observed"][arm]["health"] == "ok"]
        scorer[arm]["valid_accepted"] = sum(row["observed"][arm]["accepted"] for row in rows if row["role"] == "valid")
        scorer[arm]["valid_total"] = sum(row["role"] == "valid" for row in rows)
        scorer[arm]["invalid_accepted"] = sum(
            row["observed"][arm]["accepted"] for row in rows if row["role"] == "invalid"
        )
        scorer[arm]["invalid_total"] = sum(row["role"] == "invalid" for row in rows)
        scorer[arm]["alternatives_accepted"] = sum(
            row["observed"][arm]["accepted"] for row in rows if row["role"] == "alternative"
        )
        scorer[arm]["alternatives_total"] = sum(row["role"] == "alternative" for row in rows)
        scorer[arm]["health_errors"] = panel["control_count"] - len(rows)
        invalid = [row for row in rows if row["expected"]["reviewed"]["accepted"] is False]
        valid = [row for row in rows if row["expected"]["reviewed"]["accepted"] is True]
        scorer[arm]["invalid_denominator"] = len(invalid)
        scorer[arm]["valid_denominator"] = len(valid)
        scorer[arm]["false_accepts"] = sum(row["observed"][arm]["accepted"] is True for row in invalid)
        scorer[arm]["false_rejects"] = sum(row["observed"][arm]["accepted"] is False for row in valid)
        scorer[arm]["other_attempted"] = (
            panel["control_count"] - scorer[arm]["valid_total"] - scorer[arm]["invalid_total"]
        )
    anchors = []
    for case in integrity["cases"][:4]:
        report = read(output / "integrity" / case["evidence"]["report"])
        criterion = report["items"]["CREATE-07"]
        check = next(row for row in criterion["check_results"] if row["id"] == "CREATE-07-C01")
        group = next(row for row in check["group_scores"] if row["id"] == "receipt_consistency")
        anchors.append(
            {
                "id": case["id"],
                "expected": case["expected"]["score"],
                "observed": group["score"],
                "canonical_score": criterion["score"],
                **{key: group[key] for key in ("passed", "failed", "unknown", "excluded")},
                "evidence": "integrity/" + case["evidence"]["report"],
            }
        )
    return {
        "schema": "rubric-showcase-v1",
        "native_execution": False,
        "workflow_acceptance": False,
        "aggregate_score": None,
        "criterion_inventory": inventory,
        "criterion_demonstration": demonstration,
        "production_gaps": [row["id"] for row in inventory if row["id"] not in demonstrated],
        "receipt_authenticity": {
            arm: {
                "criterion": read(output / f"scorer/rubric-{arm}.json")["items"]["AC-03"],
                "evidence": f"scorer/rubric-{arm}.json",
            }
            for arm in ("baseline", "reviewed")
        },
        "identity": {
            "status": identity["status"],
            "counts": identity["counts"],
            "assessments": [
                {"id": row["id"], "scores": {key: value["score"] for key, value in row["observed"]["items"].items()}}
                for row in identity["assessments"]
            ],
        },
        "provenance": provenance["counts"],
        "task_contract": {
            "matched": task_contract["matched_count"],
            "cases": task_contract["case_count"],
            "human_adjudication": task_contract["human_adjudication"],
            "assessments": [
                {
                    "id": row["id"],
                    "items": {
                        key: read(output / "task-contract" / row["report"])["items"][key]
                        for key in ("CREATE-03", "CREATE-04", "CREATE-05-A")
                    },
                    "evidence": "task-contract/" + row["report"],
                }
                for row in task_contract["cases"]
                if row["kind"] == "task_contract"
            ],
            "ethos": [
                {
                    "id": row["id"],
                    "status": row["observed"]["status"],
                    "valid": row["observed"]["valid"],
                    "report": "task-contract/" + row["report"],
                }
                for row in task_contract["cases"]
                if row["kind"] == "ethos"
            ],
        },
        "scorer": scorer,
        "integrity": {"anchors": anchors, "fresh_replay_equal": integrity["determinism"]["normalized_fresh_run_equal"]},
    }


def require_equal(actual: object, expected: object, label: str) -> None:
    if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
        raise ValueError(label + " mismatch")


def validate_reports(output: Path) -> None:
    from run_criterion_scoring_demo import run as verify_scoring_demo
    from run_verification_integrity_demo import receipt_score

    panel = read(output / "integrity/panel.json")
    expected_ids = [f"anchor-{score}" for score in range(4)] + [
        "missing",
        "stale",
        "acceptance-contradiction",
        "acceptance-consistent-claim",
    ]
    require_equal([row["id"] for row in panel["cases"]], expected_ids, "integrity membership")
    require_equal(panel["status"], "passed", "integrity status")
    for row, passed, score in zip(panel["cases"][:4], (0, 1, 3, 4), range(4), strict=True):
        report = read(output / "integrity" / row["evidence"]["report"])
        observed = receipt_score(report)
        require_equal(observed, row["observed"], "integrity emitted groups")
        require_equal(
            {key: observed[key] for key in ("score", "passed", "failed", "unknown", "excluded")},
            {"score": score, "passed": passed, "failed": 4 - passed, "unknown": 0, "excluded": 0},
            "integrity anchor",
        )
        require_equal(report["items"]["CREATE-07"]["score"], None, "integrity canonical score")
        require_equal(
            observed["required_group"],
            {
                "score": None,
                "outcome": "unassessed",
                "passed": 0,
                "failed": 0,
                "unknown": 3,
                "excluded": 0,
                "scope": "verification_integrity",
            },
            "integrity required group",
        )
        require_equal(observed["required_freshness"], ["unknown"] * 3, "integrity freshness")
        require_equal(observed["group_scopes"], ["verification_integrity"] * 2, "integrity groups")
    for row in panel["cases"]:
        require_equal(row["matches_expectations"], True, "integrity case match")
    for row, status in zip(panel["cases"][4:], ("unknown", "stale", "violation", "consistent"), strict=True):
        report = read(output / "integrity" / row["evidence"]["report"])
        require_equal(report["acceptance_integrity"]["status"], status, "integrity partial claim")
        require_equal(row["observed"]["acceptance_status"], status, "integrity claim projection")
        require_equal(row["workflow_acceptance"], False, "integrity workflow boundary")
        require_equal(
            read(output / "integrity" / row["evidence"]["default_report"]),
            row["default_acceptance"]["report"],
            "integrity default acceptance",
        )
    retry = read(output / "integrity" / panel["retry"]["evidence"])
    require_equal(retry["counts"]["review_candidates"], 1, "integrity retry candidates")
    require_equal(retry["counts"]["confirmed_violations"], 0, "integrity retry violations")
    require_equal(panel["retry"]["score"], None, "integrity retry score")
    require_equal(panel["retry"]["matches_expectations"], True, "integrity retry match")

    identity = read(output / "identity/panel.json")
    definitions = read(ROOT / "evals/rubric-measurements/cases.json")
    for name, source in (
        ("cases", "raw_measurements"),
        ("assessments", "assessments"),
        ("evidence_cases", "evidence_cases"),
    ):
        rows = identity[name]
        require_equal([row["id"] for row in rows], [row["id"] for row in definitions[source]], "identity membership")
        require_equal(identity["counts"][name], len(rows), "identity count")
        for row in rows:
            require_equal(row["matches_expectations"], True, "identity case match")
            if name != "cases":
                for ident, fields in row["expected_items"].items():
                    for field, expected in fields.items():
                        require_equal(row["observed"]["items"][ident][field], expected, "identity emitted score")
    package = identity["package_acceptance"]
    require_equal(
        [row["id"] for row in package["cases"]],
        [row["id"] for row in definitions["package_acceptance"]],
        "identity package membership",
    )
    require_equal(
        package["counts"], {"cases": len(package["cases"]), "matched": len(package["cases"])}, "identity package count"
    )
    for row in package["cases"]:
        require_equal(row["matches_expectations"], True, "identity package match")

    provenance = read(output / "provenance/panel.json")
    require_equal(provenance["counts"], {"cases": 16, "matched": 16}, "provenance count")
    require_equal(len({row["id"] for row in provenance["cases"]}), 16, "provenance membership")
    for row in provenance["cases"]:
        report = read(output / "provenance" / row["report_path"])
        require_equal(report, row["report"], "provenance emitted report")
        require_equal(row["observed"], row["expected"], "provenance observations")
        require_equal(row["matches_expectations"], True, "provenance match")
        for criterion in report["items"].values():
            require_equal(criterion["score"], None, "provenance unassessed")

    scorer = read(output / "scorer/panel.json")
    require_equal(scorer["control_count"], 21, "scorer count")
    require_equal(scorer["matched_count"], 21, "scorer matched count")
    require_equal(len({row["id"] for row in scorer["cases"]}), 21, "scorer membership")
    for row in scorer["cases"]:
        require_equal(row["observed"], row["expected"], "scorer control")
        require_equal(row["matched"], True, "scorer match")
    for arm, expected in (("baseline", 0), ("reviewed", 1)):
        criterion = read(output / f"scorer/rubric-{arm}.json")["items"]["AC-02"]
        require_equal(criterion["score"], expected, "scorer emitted score")
        require_equal(criterion["evidence_strength"], "E2", "scorer evidence")
        if not criterion["remaining_gaps"]:
            raise ValueError("scorer missing gaps")

    validate_source_bound_scores(output)

    # The owner replays only trusted current measure.py against data. Its CLI
    # writes invocation logs, so verify a private copy to keep this bundle read-only.
    with tempfile.TemporaryDirectory(prefix="showcase-scoring-verify-") as scratch:
        copied = Path(scratch) / "criterion-scoring"
        shutil.copytree(output / "criterion-scoring", copied)
        verify_scoring_demo(copied, verify=True)


def validate_source_bound_scores(output: Path) -> None:
    for arm, score in (("baseline", 0), ("reviewed", None)):
        report = read(output / f"scorer/rubric-{arm}.json")
        criterion = report["items"]["AC-03"]
        require_equal(criterion["score"], score, "AC-03 emitted score")
        require_equal(criterion["evidence_strength"], "E2" if score == 0 else None, "AC-03 evidence")
        if not criterion["remaining_gaps"]:
            raise ValueError("AC-03 missing gaps")
        if arm == "baseline":
            finding = "refund-solver-authored-success"
            require_equal(criterion["finding_ids"], [finding], "AC-03 shared finding")
            if finding not in report["items"]["AC-02"]["finding_ids"]:
                raise ValueError("AC-02 shared finding missing")
    contract = read(output / "task-contract/panel.json")
    require_equal(contract["case_count"], 7, "task contract count")
    require_equal(contract["matched_count"], 7, "task contract matched count")
    require_equal(len({row["id"] for row in contract["cases"]}), 7, "task contract membership")
    require_equal(contract["human_adjudication"], False, "task contract adjudication")
    for row in contract["cases"]:
        report = read(output / "task-contract" / row["report"])
        require_equal(row["matched"], True, "task contract match")
        criteria = ("CREATE-03", "CREATE-04", "CREATE-05-A")
        expected = dict.fromkeys(criteria)
        require_equal(
            row["observed"]["rubric_scores"] if row["kind"] == "task_contract" else row["rubric_scores"],
            row["rubric_scores"],
            "task contract observed scores",
        )
        if row["kind"] == "ethos":
            require_equal(row["rubric_scores"], expected, "task contract ethos unscored")
            require_equal(row["emitted_criterion_ids"], [], "task contract ethos criteria")
            require_equal([key for key in criteria if key in report["items"]], [], "ethos emitted rows")
            require_equal(report["measurements"]["ethos"], row["observed"], "task contract ethos report")
        else:
            if row["id"] == "planted-summary":
                expected["CREATE-04"] = 0
            emitted = {key: report["items"][key]["score"] for key in criteria}
            require_equal(emitted, expected, "task contract emitted score")
            require_equal(row["rubric_scores"], emitted, "task contract score projection")
            require_equal(row["emitted_criterion_ids"], list(criteria), "task contract emitted criteria")
            require_equal(
                report["items"]["CREATE-04"]["evidence_strength"],
                "E1" if row["id"] == "planted-summary" else None,
                "task contract evidence",
            )
            require_equal(row["observed"], row["expected"], "task contract inspection")
            read(output / "task-contract" / row["crosswalk"])


def semantic_projection(output: Path) -> dict:
    from run_verification_integrity_demo import normalized

    contract = read(output / "task-contract/panel.json")
    return {
        # These panels retain all cases, diagnostics, checks, gaps and stable identities.
        "criterion_demonstration": {
            "summary": read(output / "criterion-scoring/summary.json"),
            "reports": {
                f"{name}/{variant}/{kind}": read(output / "criterion-scoring" / name / variant / f"{kind}-report.json")
                for name in ("run-a", "run-b")
                for variant in ("score-0", "score-1", "score-2", "score-3", "missing")
                for kind in ("measure", "baseline")
            },
        },
        "identity": read(output / "identity/panel.json"),
        "provenance": read(output / "provenance/panel.json"),
        "task_contract": {
            **contract,
            "reports": {row["id"]: read(output / "task-contract" / row["report"]) for row in contract["cases"]},
            "crosswalks": {
                row["id"]: read(output / "task-contract" / row["crosswalk"])
                for row in contract["cases"]
                if row["kind"] == "task_contract"
            },
        },
        "scorer": {
            "panel": read(output / "scorer/panel.json"),
            "reports": {arm: read(output / f"scorer/rubric-{arm}.json") for arm in ("baseline", "reviewed")},
        },
        # The owning projection explicitly omits only path-bound provenance and
        # replay hashes; the untouched reports and receipts remain in each root.
        "integrity": normalized(read(output / "integrity/panel.json"), output / "integrity"),
    }


def compare_runs(output: Path) -> dict:
    primary = semantic_projection(output)
    repeated = semantic_projection(output / "fresh-replay")
    areas = {area: primary[area] == repeated[area] for area in primary}
    if not all(areas.values()):
        raise ValueError(
            "fresh-root semantic mismatch: " + ", ".join(area for area, equal in areas.items() if not equal)
        )
    for root, projection in ((output, primary), (output / "fresh-replay", repeated)):
        (root / "semantic-result.json").write_text(json.dumps(projection, indent=2, allow_nan=False) + "\n")
    return {
        "fresh_run_equal": all(areas.values()),
        "areas": areas,
        "scope": "same-checkout semantic projections across newly produced roots; not raw-byte or relocation equality",
        "raw_identity": "Unmodified reports, path-bound receipts and hashes retained separately in both roots.",
        "identity_input_retention": "Identity panel uses temporary inputs; retained reports are not durable input replay.",
    }


def render(summary, panel):
    def esc(value):
        return html.escape(str(value), quote=True)

    def score(value):
        return "Unassessed" if value is None else str(value)

    scorer = summary["scorer"]
    compare = "".join(
        f"<tr><td>{arm.capitalize()}</td><td>{score(scorer[arm]['score'])}/{esc(scorer[arm]['evidence_strength'])}</td>"
        f"<td>{scorer[arm]['false_accepts']}/{scorer[arm]['invalid_denominator']}</td>"
        f"<td>{scorer[arm]['false_rejects']}/{scorer[arm]['valid_denominator']}</td>"
        f'<td>{scorer[arm]["health_errors"]}</td><td><a href="scorer/rubric-{arm}.json">Rubric report</a></td></tr>'
        for arm in ("baseline", "reviewed")
    )
    cases = "".join(
        f"<tr><td>{esc(row['id'])}</td><td>{esc(row['role'])}</td>"
        + "".join(
            f"<td>{esc('Error: ' + outcome['health'] if outcome['health'] != 'ok' else 'Accept' if outcome['accepted'] else 'Reject')} "
            f"({esc(outcome['total_credit'])}/{esc(outcome['max_credit'])})</td>"
            for outcome in (row["observed"]["baseline"], row["observed"]["reviewed"])
        )
        + f"<td>{'Match' if row['matched'] else 'Mismatch'}</td></tr>"
        for row in panel["cases"]
    )
    anchors = "".join(
        f"<tr><td>{esc(row['id'])}</td><td>{row['expected']}</td><td>{score(row['observed'])}</td>"
        f"<td>{row['passed']} / {row['failed']} / {row['unknown']} / {row['excluded']}</td>"
        f'<td>{score(row["canonical_score"])}</td><td><a href="{esc(row["evidence"])}">Evidence</a></td></tr>'
        for row in summary["integrity"]["anchors"]
    )
    ethos_rows = "".join(
        f'<tr><td>{esc(row["id"])}</td><td>{esc(row["status"])}</td><td>{esc(row["valid"])}</td><td><a href="{esc(row["report"])}">Evidence</a></td></tr>'
        for row in summary["task_contract"]["ethos"]
    )
    identity_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td>"
        + "".join(f"<td>{score(row['scores'][key])}</td>" for key in ("DISC-01-A", "DISC-01-B", "DISC-01-C"))
        + "</tr>"
        for row in summary["identity"]["assessments"]
    )
    inventory_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td><td>{esc(row['area'])}</td>"
        f"<td>{esc(row['demonstration_status'])}; {esc(row['scoring_rule_status'])}</td></tr>"
        for row in summary["criterion_inventory"]
    )
    authenticity_rows = "".join(
        f"<tr><td>{esc(arm)}</td><td>{score(row['criterion']['score'])}</td>"
        f"<td>{esc(row['criterion']['evidence_strength'] or 'Unassessed')}</td>"
        f'<td><a href="{esc(row["evidence"])}">Emitted AC-03</a></td></tr>'
        for arm, row in summary["receipt_authenticity"].items()
    )
    contract_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td>"
        + "".join(f"<td>{score(row['items'][key]['score'])}</td>" for key in ("CREATE-03", "CREATE-04", "CREATE-05-A"))
        + f"<td>{esc(row['items']['CREATE-04']['evidence_strength'] or 'Unassessed')}</td>"
        + f'<td><a href="{esc(row["evidence"])}">Emitted criteria</a></td></tr>'
        for row in summary["task_contract"]["assessments"]
    )
    demo = summary["criterion_demonstration"]
    variants = ("score-0", "score-1", "score-2", "score-3", "missing")
    matrix = "".join(
        f"<tr><td>{esc(item['id'])}</td>"
        + "".join(
            f'<td><a href="criterion-scoring/run-a/{variant}/measure-report.json">'
            f"{score(demo['results'][variant][item['id']])}</a></td>"
            for variant in variants
        )
        + "</tr>"
        for item in summary["criterion_inventory"]
    )
    production_gaps = ", ".join(esc(ident) for ident in summary["production_gaps"])
    gaps = "".join(f"<li>{esc(gap)}</li>" for gap in scorer["reviewed"]["remaining_gaps"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rubric demo: scorer quality and evidence integrity</title>
<style>
:root{{color-scheme:light dark}}body{{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:32px;max-width:1100px;color:var(--foreground,#ddd);background:var(--card,#172026)}}
h1{{font-size:30px;line-height:1.2}}h2{{margin-top:36px;font-size:22px}}a{{color:var(--accent,#95cd7a)}}p,li{{max-width:90ch}}.muted{{color:var(--muted-foreground,#b6c0c7)}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:left;border-bottom:1px solid var(--border,#46515a);padding:10px 12px;vertical-align:top}}th{{font-size:14px}}code,pre{{font-size:13px}}pre{{overflow:auto;padding:16px;border:1px solid var(--border,#46515a)}}summary{{cursor:pointer;padding:12px 0}}@media(max-width:600px){{body{{padding:16px}}h1{{font-size:25px}}}}
</style><main>
<p class="muted">Experimental rubric · local paired demonstration</p>
<h1>A repaired scorer rejects shortcuts. Integrity checks expose unsupported claims.</h1>
<p>Fixture execution runs the same frozen refund controls through two scorers. The integrity demo uses synthetic native results with the real receipt producer and rubric CLI. This run starts no Docker containers or models.</p>
<h2>1. Canonical rubric: AC-02</h2>
<p>The completion-only baseline gives forbidden credit. The reviewed scorer preserves valid solutions and checks the required effects. Task-credit totals use each arm's own scale. They are separate from the canonical rubric score.</p>
<div class="scroll"><table><thead><tr><th>Scorer</th><th>AC-02</th><th>False accepts / healthy invalid</th><th>False rejects / healthy valid</th><th>Operational errors</th><th>Evidence</th></tr></thead><tbody>{compare}</tbody></table></div>
<p>{scorer["matched_controls"]}/{scorer["attempted_controls"]} control outcomes match the frozen expectations. The reviewed scorer accepts {scorer["reviewed"]["alternatives_accepted"]}/{scorer["reviewed"]["alternatives_total"]} declared valid alternatives. The baseline has {scorer["baseline"]["health_errors"]} operational errors. The reviewed arm has {scorer["reviewed"]["health_errors"]}. These errors do not count as semantic rejections. The valid/invalid denominators follow each control's frozen reviewed acceptance expectation. They include valid alternatives, permitted inaction, partial credit and the negative approval boundary. Operational cases remain separate. Same-input scorer replay: {scorer["replay_equal"]}.</p>
<details><summary>Inspect all paired controls and task credit</summary><div class="scroll"><table><thead><tr><th>Control</th><th>Role</th><th>Baseline credit</th><th>Reviewed credit</th><th>Expected comparison</th></tr></thead><tbody>{cases}</tbody></table></div></details>
<h3>Receipt authenticity: AC-03</h3>
<p>The baseline accepts a solver-authored success claim. The actual local control emits AC-03 = 0/E2; the reviewed arm remains Unassessed. This shares the refund-solver-authored-success finding with AC-02, so it is not a second independent exploit. Rejecting this one shortcut does not prove a complete receipt trust boundary.</p>
<table><thead><tr><th>Arm</th><th>AC-03</th><th>Evidence strength</th><th>Evidence</th></tr></thead><tbody>{authenticity_rows}</tbody></table>
<h2>2. Integrity group: receipt_consistency</h2>
<p>These are separate 0–3 group scores emitted under CREATE-07-C01. They do not score the whole CREATE-07 criterion. Required verification remains Unassessed because native freshness is unknown.</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Expected group</th><th>Observed group</th><th>Pass / fail / unknown / excluded</th><th>Canonical CREATE-07</th><th>Evidence</th></tr></thead><tbody>{anchors}</tbody></table></div>
<p><a href="integrity/report.md">Missing/stale evidence, acceptance claims and retry cases</a>: missing or stale evidence stays Unassessed. Acceptance examples are helper projections from partial claims, not saved-envelope workflow acceptance. Retry candidates remain unscored. Fresh-directory normalized integrity replay: {summary["integrity"]["fresh_replay_equal"]}.</p>
<h2>3. Identity and provenance</h2>
<p>The selected task, declared scorer route and dependency inventory have their own canonical scores. A wrong route lowers DISC-01-B to 0. An unresolved route remains Unassessed. A confirmed missing task lowers DISC-01-A to 0. These identity results do not prove scorer correctness.</p>
<div class="scroll"><table><thead><tr><th>Mutation</th><th>DISC-01-A</th><th>DISC-01-B</th><th>DISC-01-C</th></tr></thead><tbody>{identity_rows}</tbody></table></div>
<p><a href="identity/panel.json">Identity assessment and source/configuration diagnostics</a>. The provenance panel matches {summary["provenance"]["matched"]}/{summary["provenance"]["cases"]} cases through the actual CLI, including changed selected bytes, broken links, configuration binding, stale dependencies and retained-text reconstruction. <a href="provenance/panel.json">Provenance evidence and individual report links</a>.</p>
<h2>4. Task contract and scoring authority</h2>
<p>The order-total task asks for total.txt. Its planted scorer also requires summary.json. The retained static crosswalk cites the instruction, scorer and input data. A clean neighbor removes that hidden condition. The real CLI emits CREATE-04 = 0/E1 for the planted hidden condition, bound to the supported fixture source bytes. The clean neighbor stays Unassessed: removing one defect does not establish a positive anchor. CREATE-03 and CREATE-05-A remain Unassessed. No human adjudication is supplied.</p>
<table><thead><tr><th>Source case</th><th>CREATE-03</th><th>CREATE-04</th><th>CREATE-05-A</th><th>CREATE-04 evidence strength</th><th>Evidence</th></tr></thead><tbody>{contract_rows}</tbody></table>
<p><a href="task-contract/report.md">Condition crosswalk and expected-versus-observed inspection</a>. {summary["task_contract"]["matched"]}/{summary["task_contract"]["cases"]} task-contract and Ethos scenarios match their expectations. These are authored static inspections and real structure checks, not a measured model-judgment result.</p>
<div class="scroll"><table><thead><tr><th>Ethos case</th><th>Measurement status</th><th>Structure valid</th><th>Evidence</th></tr></thead><tbody>{ethos_rows}</tbody></table></div>
<p>A structurally valid but changed Ethos still fails its digest binding. An unavailable validator leaves the result unknown. Valid structure does not establish confirmed owner intent.</p>
<h2>5. Synthetic scoring-rule coverage</h2>
<p>All {demo["criterion_count"]} criteria exercise 0, 1, 2, 3 and missing through the actual measurement CLI: {demo["vector_count_per_root"]} criterion vectors per root. These authored synthetic numbers do not qualify a task. They appear only in criterion_demonstration, never in real measured items. The source-bound scores above come from selected source inspection or executed local controls; this matrix instead tests how scoring rules respond to invented observations. Human review, native execution and independent corroboration were NOT_RUN.</p>
<div class="scroll"><table><thead><tr><th>Criterion</th><th>0 anchor</th><th>1 anchor</th><th>2 anchor</th><th>3 anchor</th><th>Missing evidence</th></tr></thead><tbody>{matrix}</tbody></table></div>
<p><a href="criterion-scoring/summary.json">Synthetic coverage and rejection controls</a>. Each cell links to the full emitted report, including evidence references and limitations. Synthetic E1 does not establish native or authenticated human evidence.</p>
<h2>6. Full criterion inventory (score-free)</h2>
<p>The populated rubric spans validity areas. Coverage and diversity remain unimplemented. A defined criterion does not imply an executed check or numeric score.</p>
<div class="scroll"><table><thead><tr><th>Criterion</th><th>Area</th><th>This demonstration</th></tr></thead><tbody>{inventory_rows}</tbody></table></div>
<h2>7. What prevents a stronger showcase?</h2>
<p>No source-bound canonical number is demonstrated for these {len(summary["production_gaps"])} criteria: {production_gaps}. Their synthetic anchor coverage does not close production evidence gaps. This is a showcase coverage statement, not a claim that every adapter is absent.</p>
<p>The local paired story works. A canonical score of 2 needs the remaining applicable attack controls and native evidence. Independent held-out attacks and valid alternatives are needed for score 3.</p><ul>{gaps}</ul>
<p>For a complete verification-and-recovery story, produce a full saved acceptance envelope, demonstrate ordinary acceptance, invalidate it with a later failure or source change, then rerun the affected controls and show ordinary acceptance again. The current partial-claim cases do not demonstrate that sequence.</p>
<p>No overall quality score, coverage/diversity score, task qualification, authenticated approval authority or causal authoring benefit follows from these results. A matched authoring study is separate work.</p>
<h2>Reproduce</h2><p>Fresh-root semantic repeat: {summary["determinism"]["fresh_run_equal"]}. Every included area is regenerated in fresh-replay/ and compared, including partial acceptance and retry diagnostics. <a href="semantic-result.json">Explicit semantic projection</a> · <a href="fresh-replay/semantic-result.json">Fresh projection</a>. Raw path-specific identities remain separate, unmodified. This demonstrates same-checkout repeatability, not relocation-independent raw bytes. Identity inputs are temporary; their retained panel does not provide durable input replay.</p><pre>uv run --locked python tools/run_rubric_showcase.py \\
  --output "$PWD/.eval-author/showcase-new"</pre>
<p>Use a new output directory. Data-only retained verification: <code>uv run --locked python tools/run_rubric_showcase.py --verify-existing "$PWD/.eval-author/showcase-new"</code>. This validates emitted reports, semantic comparisons and byte manifests without executing retained code. The local hash manifest is not authenticated attestation; identity temporary inputs cannot be replayed. <a href="showcase.json">Summary projection</a> · <a href="scorer/panel.json">Frozen control comparison</a> · <a href="integrity/hashes.json">Integrity artifact hashes</a></p>
</main></html>"""


def retained_hashes(output: Path) -> dict[str, str]:
    result = {}
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError("unsafe retained symlink")
        if path.is_file() and path != output / "showcase-hashes.json":
            result[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify_existing(output: Path) -> None:
    # This is data-only validation of a retained bundle, not execution of retained code
    # or authenticated attestation. A local hash manifest alone is not a trust root.
    require_equal(retained_hashes(output), read(output / "showcase-hashes.json"), "retained artifact hashes")
    for root in (output, output / "fresh-replay"):
        validate_reports(root)
        require_equal(semantic_projection(root), read(root / "semantic-result.json"), "retained semantic projection")
    primary = read(output / "semantic-result.json")
    require_equal(primary, read(output / "fresh-replay/semantic-result.json"), "fresh-root semantic projection")
    summary = read(output / "showcase.json")
    require_equal(
        {key: value for key, value in summary.items() if key != "determinism"},
        summarize_reports(output),
        "retained summary",
    )
    require_equal(summary["determinism"]["fresh_run_equal"], True, "retained repeat")
    require_equal(summary["determinism"]["areas"], dict.fromkeys(primary, True), "retained repeat areas")
    require_equal(
        (output / "index.html").read_text(), render(summary, read(output / "scorer/panel.json")), "retained HTML"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="new private output directory")
    mode.add_argument(
        "--verify-existing", type=Path, help="read-only retained bundle validation; never executes retained code"
    )
    args = parser.parse_args(argv)
    try:
        output = (args.output or args.verify_existing).absolute()
        if ".." in output.parts or any(path.is_symlink() for path in (output, *output.parents)):
            raise ValueError("unsafe output directory")
        if args.verify_existing:
            verify_existing(output)
            print(output / "index.html")
            return 0
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        summary = collect(output)
        validate_reports(output)
        fresh = output / "fresh-replay"
        fresh.mkdir(mode=0o700)
        collect(fresh)
        validate_reports(fresh)
        summary["determinism"] = compare_runs(output)
        if not summary["scorer"]["replay_equal"] or not summary["integrity"]["fresh_replay_equal"]:
            raise ValueError("demo replay mismatch")
        (output / "showcase.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        (output / "index.html").write_text(render(summary, read(output / "scorer/panel.json")))
        (output / "showcase-hashes.json").write_text(json.dumps(retained_hashes(output), indent=2) + "\n")
        verify_existing(output)
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    print(output / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
