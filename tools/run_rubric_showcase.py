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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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


SCRIPTS = ROOT / "skills/eval-author-rubric/scripts"


def current_modules():
    sys.path.insert(0, str(SCRIPTS))
    import rubric_leakage_current
    import rubric_table

    return rubric_leakage_current, rubric_table


def collect_current(output: Path) -> None:
    for name, script, accepted in (
        ("source-controls", "rubric_mutation_runner.py", (0,)),
        ("current-leakage", "rubric_leakage_recipes.py", (0, 1)),
    ):
        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / script), "--output", str(output / name)],
            capture_output=True,
            text=True,
            timeout=900,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        (output / f"{name}.stdout").write_text(completed.stdout)
        (output / f"{name}.stderr").write_text(completed.stderr)
        if completed.returncode not in accepted:
            raise ValueError(f"{name} failed; inspect retained stderr")
        if name == "current-leakage":
            result = read(output / name / "leakage-results.json")
            require_equal(result["verification"], "ordinary-replay-verified", "current leakage replay")
            require_equal(
                completed.returncode,
                int(bool(result["pending_requests"]["exact_request_count"])),
                "current leakage exit",
            )


def current_assessments(output: Path) -> list[dict]:
    current_modules()
    import rubric_leakage_recipes as leakage
    import rubric_mutation_runner as runner

    records: dict[str, dict] = {}

    def index(runs: list[dict], expected: set[str], producer: str) -> dict[str, dict]:
        indexed = {run["case"]: run for run in runs}
        if len(indexed) != len(runs) or set(indexed) != expected:
            raise ValueError(f"{producer} case inventory differs from producer catalog")
        return indexed

    def retain(record: dict, relative: Path) -> None:
        if record["state"] != "assessed":
            raise ValueError("current assessment blocked")
        path = Path(record["report"])
        if ".." in path.parts or path.parts[-len(relative.parts) :] != relative.parts:
            raise ValueError("current assessment reference differs from producer case")
        key = relative.as_posix()
        if key in records:
            raise ValueError("duplicate current assessment reference")
        records[key] = record

    source_cases = {path.stem for path in (runner.fixtures.EXAMPLE / "mutations").glob("*.json")}
    source_cases.update(runner.EXTRA_CASES)
    source = index(read(output / "source-controls/runs.json"), source_cases, "source-controls")
    for case, run in source.items():
        for arm, record in (
            ("changed", run["assessment"]),
            ("baseline", run["baseline_assessment"]),
            ("repair", run["repair"]["assessment"]),
        ):
            retain(record, Path("source-controls") / case / arm / "report/assessment.json")

    result = read(output / "current-leakage/leakage-results.json")
    runs = index(result["runs"], {"leakage-" + case for case in leakage.CASES}, "current-leakage")
    repairs = result["source_repairs"]
    require_equal(sorted(repairs), ["baseline", "repair"], "current leakage source repairs")
    require_equal(repairs["baseline"], runs["leakage-inventory"]["assessment"], "current leakage source baseline")
    retain(repairs["repair"], Path("current-leakage/repair/assessment/ordinary/report/assessment.json"))
    for name, run in runs.items():
        case = name.removeprefix("leakage-")
        retain(run["assessment"], Path("current-leakage") / case / "assessment/ordinary/report/assessment.json")
        if case in leakage.CONTROL_CASES:
            ident, _ = leakage.CONTROL_CASES[case]
            baseline = runs["leakage-" + ident + ".base"]["assessment"]
            repair = runs["leakage-" + ident + ".repair"]["assessment"]
        else:
            baseline, repair = repairs["baseline"], repairs["repair"]
        require_equal(run["criterion_baseline"], baseline, "current leakage paired baseline")
        require_equal(run["criterion_repair"], repair, "current leakage paired repair")
    return [
        {"id": str(Path(relative).parent), "evidence": relative, "report": read(output / relative)["report"]}
        for relative in sorted(records)
    ]


def validate_current(output: Path) -> None:
    current, table = current_modules()
    import rubric_leakage_recipes as leakage
    import rubric_mutation_runner as runner

    assessments = current_assessments(output)
    for case in assessments:
        path = output / case["evidence"]
        saved = read(path)
        root = Path(saved["root"])
        expected_root = path.parents[1 if case["evidence"].startswith("source-controls/") else 3]
        if root != expected_root or ".." in root.parts:
            raise ValueError("current assessment root differs from retained bundle")
    source = read(output / "source-controls/runs.json")
    for run in source:
        catalog = runner.expectation(run["case"])
        expected = catalog.get("expected_scores", dict.fromkeys(catalog["target_criteria"], catalog["intended_score"]))
        require_equal(run["expected"], expected, "source expectation catalog")
        for record, case in (
            (run["assessment"], run["case"]),
            (run["baseline_assessment"], "base"),
            (run["repair"]["assessment"], "base"),
        ):
            root = Path(record["report"]).parents[1]
            relative = root.relative_to(output / "source-controls")
            if ".." in relative.parts:
                raise ValueError("source assessment root differs from retained bundle")
            runner.verify_case_input(root, case)
    require_equal(
        read(output / "source-controls/coverage.json"), runner.reconcile(source), "source coverage reconciliation"
    )
    result = read(output / "current-leakage/leakage-results.json")
    for record in [*(run["assessment"] for run in result["runs"]), *result["source_repairs"].values()]:
        path = Path(record["report"])
        relative = path.relative_to(output / "current-leakage")
        if ".." in relative.parts:
            raise ValueError("current leakage assessment path differs from retained bundle")
    require_equal(result["verification"], "ordinary-replay-verified", "current leakage replay")
    require_equal(result["pending_requests"], leakage.pending_requests(result["runs"]), "current owner requests")
    require_equal(
        read(output / "current-leakage/owner-requests.json"), result["pending_requests"], "owner request file"
    )
    for case in assessments:
        path = output / case["evidence"]
        saved = read(path)
        root = Path(saved["root"])
        if not root.is_relative_to(output):
            raise ValueError("current assessment root differs from retained bundle")
        report = table.assess_table(root, saved["selection"], saved["task"])
        require_equal(saved["definitions_sha256"], current.definitions_digest(), "current definitions")
        require_equal(report, saved["report"], "current assessment replay")
        if set(report["items"]) != set(current.canonical_definitions()):
            raise ValueError("current criterion inventory mismatch")
        title = Path(saved["task"]).parent.name if saved["task"] else "Selected package"
        markdown = table.render(report, title)
        require_equal((path.parent / "results.md").read_text(), markdown, "current Markdown")
        require_equal((path.parent / "results.html").read_text(), table.render_html(markdown), "current HTML")
    for run in result["runs"]:
        saved = read(Path(run["assessment"]["report"]))
        leakage.verify_input(Path(saved["root"]), run["case"].removeprefix("leakage-"), saved["selection"])
        leakage.verify_observation(saved["report"], run["case"].removeprefix("leakage-"))
    repair = read(Path(result["source_repairs"]["repair"]["report"]))
    leakage.verify_input(Path(repair["root"]), "inventory", repair["selection"])
    leakage.verify_observation(repair["report"], "inventory")


def collect(output):
    (output / "identity").mkdir(mode=0o700)

    def identity() -> None:
        panel = json.loads(run(output, "identity", "run_rubric_measurements.py", "--format", "json"))
        (output / "identity/panel.json").write_text(json.dumps(panel, indent=2, allow_nan=False) + "\n")

    def scorer() -> None:
        run(output, "scorer", "run_scorer_controls.py", "--rubric", "--output", output / "scorer")
        run(output, "scorer-replay", "run_scorer_controls.py", "--rubric", "--verify-existing", output / "scorer")

    # The demos write disjoint outputs; only the scorer replay depends on the scorer run.
    jobs = (
        identity,
        lambda: collect_current(output),
        lambda: run(output, "provenance", "run_rubric_provenance_demo.py", "--run-dir", output / "provenance"),
        lambda: run(output, "task-contract", "run_task_contract_demo.py", "--output", output / "task-contract"),
        lambda: run(
            output,
            "integrity",
            "run_verification_integrity_demo.py",
            "--format",
            "json",
            "--run-dir",
            output / "integrity",
        ),
        scorer,
        lambda: run(
            output, "criterion-scoring", "run_criterion_scoring_demo.py", "--output", output / "criterion-scoring"
        ),
    )
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for future in [pool.submit(job) for job in jobs]:
            future.result()
    return summarize_reports(output)


def summarize_reports(output: Path) -> dict:
    from run_criterion_scoring_demo import canonical_definitions

    definitions = canonical_definitions()
    current, _ = current_modules()
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
    for item in inventory:
        definition = definitions[item["id"]]
        item["title"] = definition["title"]
        if "source" in definition:
            item["source"] = definition["source"]
    demonstrated = {
        "provenance.task-artifact-access",
        "provenance.grading-path",
        "provenance.grading-dependency-access",
        "AC-02",
        "AC-03",
        "task-contract.discoverable-requirements",
    }
    for item in inventory:
        item["demonstration_status"] = (
            "Source-bound canonical score demonstrated"
            if item["id"] in demonstrated
            else "Integrity groups only; canonical Unassessed"
            if item["id"] == "provenance.evidence-revision-consistency"
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
        criterion = report["items"]["provenance.evidence-revision-consistency"]
        check = next(
            row
            for row in criterion["check_results"]
            if row["id"] == "provenance.evidence-revision-consistency.deterministic-1"
        )
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
    historical_inventory = inventory
    assessments = current_assessments(output)
    inventory = [
        {
            "id": ident,
            "title": definition["title"],
            "source": definition.get("source"),
            "level": current.level(definition),
        }
        for ident, definition in current.canonical_definitions().items()
    ]
    numeric = {
        ident
        for case in assessments
        for ident, row in case["report"]["items"].items()
        if current.observed_score(row) is not None
    }
    if any(
        read(output / "task-contract" / case["report"])["items"]["task-contract.discoverable-requirements"]["score"]
        is not None
        for case in task_contract["cases"]
        if case["kind"] == "task_contract"
    ):
        numeric.add("task-contract.discoverable-requirements")
    return {
        "schema": "rubric-showcase-v2",
        "historical_criterion_inventory": historical_inventory,
        "current_assessments": assessments,
        "current_numeric_criteria": sorted(numeric),
        "pending_owner_requests": read(output / "current-leakage/owner-requests.json"),
        "native_execution": False,
        "workflow_acceptance": False,
        "aggregate_score": None,
        "criterion_inventory": inventory,
        "criterion_demonstration": demonstration,
        "production_gaps": [row["id"] for row in inventory if row["id"] not in numeric],
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
            "evidence_cases": [
                {
                    "id": row["id"],
                    "measurement": row["expected"]["measurement"],
                    "observation": row["observed"]["measurements"][row["expected"]["measurement"]],
                }
                for row in identity["evidence_cases"]
            ],
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
                        for key in (
                            "task-contract.clear-contract",
                            "task-contract.discoverable-requirements",
                            "task-contract.scoring-authority",
                        )
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
                    "structure_usable": row["structure_usable"],
                    "usable_authority": row["usable_authority"],
                    "owner_confirmation": row["owner_confirmation"],
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
    from run_verification_integrity_demo import acceptance_score, receipt_score

    panel = read(output / "integrity/panel.json")
    expected_ids = [f"anchor-{score}" for score in range(4)] + [
        "missing",
        "stale",
        "acceptance-contradiction",
        "acceptance-consistent-claim",
    ]
    require_equal([row["id"] for row in panel["cases"]], expected_ids, "integrity membership")
    require_equal(panel["status"], "passed", "integrity status")
    require_equal(panel["determinism"]["normalized_fresh_run_equal"], True, "integrity replay")
    for row, passed, score in zip(panel["cases"][:4], (0, 1, 3, 4), range(4), strict=True):
        report = read(output / "integrity" / row["evidence"]["report"])
        observed = receipt_score(report)
        require_equal(observed, row["observed"], "integrity emitted groups")
        require_equal(
            {key: observed[key] for key in ("score", "passed", "failed", "unknown", "excluded")},
            {"score": score, "passed": passed, "failed": 4 - passed, "unknown": 0, "excluded": 0},
            "integrity anchor",
        )
        require_equal(
            report["items"]["provenance.evidence-revision-consistency"]["score"], None, "integrity canonical score"
        )
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
        require_equal(
            row["expected"],
            {
                key: observed[key]
                for key in (
                    "score",
                    "passed",
                    "failed",
                    "unknown",
                    "excluded",
                    "required_group",
                    "required_freshness",
                    "group_scopes",
                )
            },
            "integrity anchor expectation",
        )
    for row in panel["cases"]:
        require_equal(row["matches_expectations"], True, "integrity case match")
    for row, status, score in zip(
        panel["cases"][4:], ("unknown", "stale", "violation", "consistent"), (None, None, 0, 3), strict=True
    ):
        report = read(output / "integrity" / row["evidence"]["report"])
        observed = acceptance_score(report)
        expected = {"acceptance_status": status, "score": score}
        require_equal(observed, row["observed"], "integrity partial claim projection")
        require_equal(row["expected"], expected, "integrity partial claim expectation")
        require_equal({key: observed[key] for key in expected}, expected, "integrity partial claim")
        require_equal(row["workflow_acceptance"], False, "integrity workflow boundary")
        require_equal(
            row["default_acceptance"],
            {"returncode": 1, "report": {"valid": False, "error": "invalid saved acceptance: 'before'"}},
            "integrity default rejection",
        )
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

    from run_rubric_measurements import canonical_checks, evidence_matches

    identity = read(output / "identity/panel.json")
    definitions = read(ROOT / "evals/rubric-measurements/cases.json")
    canonical = read(ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json")
    baseline_items = next(row["items"] for row in definitions["assessments"] if row["id"] == "valid")
    require_equal(identity["schema"], "rubric-mutation-panel-v4", "identity panel schema")
    for name, source in (
        ("cases", "raw_measurements"),
        ("assessments", "assessments"),
        ("evidence_cases", "evidence_cases"),
    ):
        rows = identity[name]
        require_equal([row["id"] for row in rows], [row["id"] for row in definitions[source]], "identity membership")
        require_equal(identity["counts"][name], len(rows), "identity count")
        for row, definition in zip(rows, definitions[source], strict=True):
            require_equal(row["matches_expectations"], True, "identity case match")
            require_equal(row["expected"], definition, "identity case definition")
            if name == "cases":
                require_equal(row["measurement"], definition["measurement"], "identity raw measurement")
                observation = row["observed"]
                require_equal(observation["status"], definition["status"], "identity raw status")
                require_equal(
                    sorted({item["code"] for item in observation.get("diagnostics", [])}),
                    definition["codes"],
                    "identity raw diagnostics",
                )
                require_equal(
                    observation.get("differences", []), definition.get("differences", []), "identity raw differences"
                )
            if name == "evidence_cases":
                require_equal(row["expected"], definition, "identity evidence definition")
                measurement = definition["measurement"]
                observation = row["observed"]["measurements"][measurement]
                require_equal(observation["status"], definition["status"], "identity evidence status")
                require_equal(row["cli_exit_code"], definition["cli_exit_code"], "identity evidence exit")
                if measurement in ("ethos", "authority"):
                    require_equal(
                        sorted({item["code"] for item in observation["diagnostics"]}),
                        definition["codes"],
                        "identity evidence diagnostics",
                    )
                    if measurement == "ethos":
                        require_equal(observation["valid"], definition["valid"], "identity ethos validity")
                    else:
                        require_equal(
                            evidence_matches(row["observed"], definition, None, None),
                            True,
                            "identity authority checks and questions",
                        )
                        require_equal(
                            [item["status"] for item in observation["requirements"]],
                            definition["requirement_statuses"],
                            "identity authority requirements",
                        )
            if name != "cases":
                expected_items = {
                    **definition.get("items", baseline_items),
                    **{key: {"score": None, "status": "NOT_ASSESSED"} for key in definitions["not_assessed"]},
                }
                expected_checks = {
                    **definitions["check_outcomes"],
                    **definitions["check_overrides"].get(definition["id"], {}),
                    **definitions["evidence_check_overrides"].get(definition.get("measurement"), {}),
                }
                report = row["observed"]
                require_equal(row["expected_items"], expected_items, "identity expected items")
                require_equal(row["expected_checks"], expected_checks, "identity expected checks")
                require_equal(row["cli_exit_code"], definition["cli_exit_code"], "identity CLI exit")
                require_equal(canonical_checks(report, canonical, expected_checks), True, "identity canonical report")
                for item_index, item_definition in enumerate(canonical["items"]):
                    item = report["items"][item_definition["id"]]
                    require_equal(item["source"], item_definition["source"], "identity criterion source")
                    require_equal(item["item_id"], item_definition["id"], "identity criterion ID")
                    for check_index, (check, check_definition) in enumerate(
                        zip(item["check_results"], item_definition["checks"], strict=True)
                    ):
                        require_equal(
                            {key: check[key] for key in check_definition}, check_definition, "identity check definition"
                        )
                        require_equal(check["source_ids"], item_definition["source_ids"], "identity check sources")
                        require_equal(
                            check["definition_ref"],
                            f"artifact-configuration-provenance.json#/items/{item_index}/checks/{check_index}",
                            "identity check reference",
                        )
                for ident, fields in expected_items.items():
                    for field, expected in fields.items():
                        require_equal(report["items"][ident][field], expected, "identity emitted score")
                for measurement, codes in definition.get(
                    "diagnostics", {"identity": [], "route": [], "approvals": []}
                ).items():
                    require_equal(
                        sorted({item["code"] for item in report["measurements"][measurement].get("diagnostics", [])}),
                        codes,
                        "identity assessment diagnostics",
                    )
                if definition.get("measurement") == "sourced_config":
                    # This owner branch checks retained metadata only. Temporary
                    # identity inputs are absent: do not claim source-byte replay.
                    require_equal(evidence_matches(report, definition, None, None), True, "identity sourced metadata")
                    require_equal(
                        sorted(report["measurements"]["sourced_config"]["fields"]),
                        [
                            "/agent",
                            "/environment",
                            "/provider",
                            "/run",
                            "/runtime_pins/harbor",
                            "/task",
                            "/tool_permissions",
                        ],
                        "identity sourced fields",
                    )
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

    validate_scorer_reports(output / "scorer")

    validate_source_bound_scores(output)
    validate_linked_reports(output)

    # The owner replays only trusted current measure.py against data. Its CLI
    # writes invocation logs, so verify a private copy to keep this bundle read-only.
    verify_private_copy(output / "criterion-scoring", verify_scoring_demo)
    validate_invocation_captures(output)


def verify_private_copy(source: Path, verifier: Callable) -> None:
    with tempfile.TemporaryDirectory(prefix="showcase-verify-") as scratch:
        # Resolve the trusted system temporary root, not retained input paths.
        copied = Path(scratch).resolve() / source.name
        shutil.copytree(source, copied)
        verifier(copied, verify=True)


def validate_scorer_reports(output: Path) -> dict:
    from run_scorer_controls import rubric_demo, run_panel

    selected = read(output / "selection.json")
    panel = run_panel(output, selected)
    require_equal(read(output / "panel.json"), panel, "scorer replay")
    panel["rubric_comparison"] = rubric_demo(output, selected, verify=True)
    return panel


def validate_invocation_captures(output: Path) -> None:
    from run_scorer_controls import encoded

    panels = {
        name: read(output / name / "panel.json")
        for name in ("identity", "provenance", "task-contract", "integrity", "scorer")
    }
    panels["criterion-scoring"] = read(output / "criterion-scoring/summary.json")
    panels["scorer"] = {
        **panels["scorer"],
        "rubric_comparison": {
            "criterion": "AC-02",
            "expected": {"baseline": 0, "reviewed": 1},
            "observed": {
                arm: read(output / f"scorer/rubric-{arm}.json")["items"]["AC-02"]["score"]
                for arm in ("baseline", "reviewed")
            },
            "matched": True,
        },
    }
    panels["scorer-replay"] = panels["scorer"]
    for name, panel in panels.items():
        require_equal((output / f"{name}.stderr").read_text(), "", f"{name} invocation capture stderr")
        try:
            captured = read(output / f"{name}.stdout")
        except ValueError as exc:
            raise ValueError(f"{name} invocation capture JSON mismatch") from exc
        require_equal(captured, panel, f"{name} invocation capture")
        if name.startswith("scorer"):
            require_equal((output / f"{name}.stdout").read_text(), encoded(panel), f"{name} invocation capture bytes")


def validate_linked_reports(output: Path) -> None:
    from run_task_contract_demo import collect as collect_contract
    from run_verification_integrity_demo import render as render_integrity

    require_equal(
        (output / "integrity/report.md").read_text(),
        render_integrity(read(output / "integrity/panel.json")),
        "integrity Markdown",
    )
    # The contract renderer is owned by collect; regenerate from trusted fixtures
    # rather than copy its formatting or execute retained sources.
    with tempfile.TemporaryDirectory(prefix="showcase-contract-verify-") as scratch:
        root = Path(scratch).resolve()
        collect_contract(root)
        require_equal(
            (output / "task-contract/report.md").read_text(), (root / "report.md").read_text(), "task contract Markdown"
        )


def validate_source_bound_scores(output: Path) -> None:
    from run_task_contract_demo import contract_observation

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
    expected_cases = [
        "planted-summary",
        "clean-neighbor",
        "ethos-valid",
        "ethos-invalid",
        "ethos-stale",
        "ethos-missing-validator",
    ]
    require_equal(contract["case_count"], len(expected_cases), "task contract count")
    require_equal(contract["matched_count"], len(expected_cases), "task contract matched count")
    require_equal([row["id"] for row in contract["cases"]], expected_cases, "task contract membership")
    require_equal(contract["human_adjudication"], False, "task contract adjudication")
    for row in contract["cases"]:
        report = read(output / "task-contract" / row["report"])
        require_equal(row["matched"], True, "task contract match")
        criteria = (
            "task-contract.clear-contract",
            "task-contract.discoverable-requirements",
            "task-contract.scoring-authority",
        )
        expected = dict.fromkeys(criteria)
        require_equal(
            row["observed"]["rubric_scores"] if row["kind"] == "task_contract" else row["rubric_scores"],
            row["rubric_scores"],
            "task contract observed scores",
        )
        if row["kind"] == "ethos":
            require_equal(row["structure_usable"], row["observed"]["status"] == "pass", "ethos structure")
            require_equal(row["usable_authority"], False, "ethos authority boundary")
            require_equal(
                row["owner_confirmation"],
                {
                    "status": "NOT_RUN",
                    "owner": "skills/eval-author/scripts/journey_state.py",
                    "confirmed_digest": None,
                    "confirmation_source": None,
                },
                "ethos owner confirmation",
            )
            require_equal(row["rubric_scores"], expected, "task contract ethos unscored")
            require_equal(row["emitted_criterion_ids"], [], "task contract ethos criteria")
            require_equal([key for key in criteria if key in report["items"]], [], "ethos emitted rows")
            require_equal(report["measurements"]["ethos"], row["observed"], "task contract ethos report")
        else:
            # Re-read the selected source closure with its owning static validator.
            # It admits frozen source shapes and never executes retained task code.
            sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
            sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
            from rubric_contract_controls import assess_contract_controls

            directory = output / "task-contract" / row["id"]
            source_rows = assess_contract_controls(directory, read(directory / "selection.json"))
            require_equal(
                {key: report["items"][key] for key in criteria}, source_rows, "task contract source-bound rows"
            )
            if row["id"] == "planted-summary":
                expected["task-contract.discoverable-requirements"] = 0
            emitted = {key: report["items"][key]["score"] for key in criteria}
            require_equal(emitted, expected, "task contract emitted score")
            require_equal(row["rubric_scores"], emitted, "task contract score projection")
            require_equal(row["emitted_criterion_ids"], list(criteria), "task contract emitted criteria")
            require_equal(
                report["items"]["task-contract.discoverable-requirements"]["evidence_strength"],
                "E1" if row["id"] == "planted-summary" else None,
                "task contract evidence",
            )
            observation = contract_observation(report)
            require_equal(row["observed"], observation, "task contract inspection projection")
            require_equal(row["expected"], observation, "task contract inspection expectation")
            read(output / "task-contract" / row["crosswalk"])


def current_semantics(report: dict) -> dict:
    from copy import deepcopy

    result = deepcopy(report)
    request = result["measurements"].get("scoped_reviews", {}).get("contract", {}).get("request")
    if request is None:
        return result
    # Intake hashes bind the absolute task path. Keep all request content and
    # verify original bindings against each root during retained validation.
    binding = request.pop("binding")
    request["crosswalk"].pop("intake_binding")
    for ident, row in result["items"].items():
        if not ident.startswith("task-contract."):
            continue
        for owner in [row, *row["check_results"]]:
            for reference in owner.get("evidence_refs", []):
                if reference.get("binding") == binding:
                    reference.pop("binding")
    return result


def semantic_projection(output: Path) -> dict:
    from run_verification_integrity_demo import normalized

    contract = read(output / "task-contract/panel.json")
    return {
        "current_assessments": [
            {**case, "report": current_semantics(case["report"])} for case in current_assessments(output)
        ],
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
        "current_comparison": "Contract intake/request bindings include absolute task paths. Only those bindings and their criterion references are omitted from semantic comparison; retained replay verifies the original bindings.",
        "identity_input_retention": "Identity panel uses temporary inputs; retained reports are not durable input replay.",
    }


def render(summary, panel):
    def esc(value):
        return html.escape(str(value), quote=True)

    def display_text(value: str) -> str:
        for number in range(1, 5):
            value = value.replace(f"AC-0{number}", f"Anticheat-0{number}")
        return esc(value)

    def score(value):
        return "Unassessed" if value is None else esc(value)

    current, table = current_modules()
    labels = {
        row["id"]: row["title"] for row in [*summary["historical_criterion_inventory"], *summary["criterion_inventory"]]
    }

    def label(ident: str) -> str:
        return display_text(labels[ident])

    def criterion(ident: str) -> str:
        return f"{label(ident)}<br><code>{display_text(ident)}</code>"

    scorer = summary["scorer"]
    compare = "".join(
        f"<tr><td>{arm.capitalize()}</td><td>{score(scorer[arm]['score'])}/{esc(scorer[arm]['evidence_strength'])}</td>"
        f"<td>{esc(scorer[arm]['false_accepts'])}/{esc(scorer[arm]['invalid_denominator'])}</td>"
        f"<td>{esc(scorer[arm]['false_rejects'])}/{esc(scorer[arm]['valid_denominator'])}</td>"
        f'<td>{esc(scorer[arm]["health_errors"])}</td><td><a href="scorer/rubric-{arm}.json">Rubric report</a></td></tr>'
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
        f"<tr><td>{esc(row['id'])}</td><td>{esc(row['expected'])}</td><td>{score(row['observed'])}</td>"
        f"<td>{esc(row['passed'])} / {esc(row['failed'])} / {esc(row['unknown'])} / {esc(row['excluded'])}</td>"
        f'<td>{score(row["canonical_score"])}</td><td><a href="{esc(row["evidence"])}">Evidence</a></td></tr>'
        for row in summary["integrity"]["anchors"]
    )
    ethos_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td><td>{esc(row['status'])}</td><td>{esc(row['valid'])}</td>"
        f'<td>Owner confirmation: {esc(row["owner_confirmation"]["status"])}</td><td><a href="{esc(row["report"])}">Evidence</a></td></tr>'
        for row in summary["task_contract"]["ethos"]
    )
    evidence_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td><td>{esc(row['measurement'])}</td>"
        f"<td>{esc(row['observation']['status'])}</td>"
        f"<td>{esc(', '.join(item['status'] for item in row['observation'].get('requirements', [])) if row['measurement'] == 'authority' else 'Structure valid: ' + str(row['observation']['valid']) if row['measurement'] == 'ethos' else 'See retained observation')}</td>"
        f"<td>{esc(', '.join(item['code'] for item in row['observation'].get('diagnostics', [])) or 'None')}</td></tr>"
        for row in summary["identity"]["evidence_cases"]
    )
    authority_cases = [row for row in summary["identity"]["evidence_cases"] if row["measurement"] == "authority"]
    authority_sections = []
    for row in authority_cases:
        observation = row["observation"]
        requirements = "".join(
            f"<tr><td>{esc(item['id'])}</td><td>{esc(item['basis'])}</td>"
            f"<td><code>{esc(item['reference'])}</code></td><td>{esc(item['status'])}</td>"
            f"<td>{esc(', '.join(item['checks']))}</td></tr>"
            for item in observation["requirements"]
        )
        checks = "".join(
            f"<tr><td>{esc(item['id'])}</td><td><code>{esc(item['pointer'])}</code></td>"
            f"<td>{esc(item['status'])}</td></tr>"
            for item in observation["checks"]
        )
        questions = "".join(
            f"<li>{esc(item['question']['requirement_id'])}: {esc(item['question']['reference'])}. "
            f"{esc(item['question']['decision_needed'])}</li>"
            for item in observation["requirements"]
            if "question" in item
        )
        diagnostics = "".join(
            f"<li>{esc(item['subject'])}: {esc(item['code'])} ({esc(item['status'])})</li>"
            for item in observation["diagnostics"]
        )
        authority_sections.append(
            f"<h4>{esc(row['id'])}: {esc(observation['status'])}</h4>"
            f"<p>Result: {esc(observation['result_artifact_id'] or 'No result artifact selected')}. "
            f"Observation scope: {esc(observation['check_observation_scope'])}.</p>"
            '<div class="scroll"><table><thead><tr><th>Requirement</th><th>Basis</th><th>Reference</th>'
            f"<th>Status</th><th>Mapped checks</th></tr></thead><tbody>{requirements}</tbody></table></div>"
            '<div class="scroll"><table><thead><tr><th>Check</th><th>Result pointer</th><th>Observation</th>'
            f"</tr></thead><tbody>{checks}</tbody></table></div>"
            f"<p>Unresolved authority questions</p><ul>{questions or '<li>None recorded</li>'}</ul>"
            f"<p>Failures and diagnostics</p><ul>{diagnostics or '<li>None recorded</li>'}</ul>"
        )
    authority_html = "".join(authority_sections)
    identity_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td>"
        + "".join(
            f"<td>{score(row['scores'][key])}</td>"
            for key in (
                "provenance.task-artifact-access",
                "provenance.grading-path",
                "provenance.grading-dependency-access",
            )
        )
        + "</tr>"
        for row in summary["identity"]["assessments"]
    )
    authenticity_rows = "".join(
        f"<tr><td>{esc(arm)}</td><td>{score(row['criterion']['score'])}</td>"
        f"<td>{esc(row['criterion']['evidence_strength'] or 'Unassessed')}</td>"
        f'<td><a href="{esc(row["evidence"])}">Emitted Anticheat-03</a></td></tr>'
        for arm, row in summary["receipt_authenticity"].items()
    )
    contract_rows = "".join(
        f"<tr><td>{esc(row['id'])}</td>"
        + "".join(
            f"<td>{score(row['items'][key]['score'])}</td>"
            for key in (
                "task-contract.clear-contract",
                "task-contract.discoverable-requirements",
                "task-contract.scoring-authority",
            )
        )
        + f"<td>{esc(row['items']['task-contract.discoverable-requirements']['evidence_strength'] or 'Unassessed')}</td>"
        + f'<td><a href="{esc(row["evidence"])}">Emitted criteria</a></td></tr>'
        for row in summary["task_contract"]["assessments"]
    )
    demo = summary["criterion_demonstration"]
    variants = ("score-0", "score-1", "score-2", "score-3", "missing")
    matrix = "".join(
        f"<tr><td>{criterion(item['id'])}</td>"
        + "".join(
            f'<td><a href="criterion-scoring/run-a/{variant}/measure-report.json">'
            f"{score(demo['results'][variant][item['id']])}</a></td>"
            for variant in variants
        )
        + "</tr>"
        for item in summary["historical_criterion_inventory"]
    )
    current_rows = []
    for item in summary["criterion_inventory"]:
        ident = item["id"]
        observations = []
        for case in summary["current_assessments"]:
            row = case["report"]["items"][ident]
            observations.append((case, row))
        numeric = [(case, row) for case, row in observations if current.observed_score(row) is not None]
        grouped: dict[tuple, list] = {}
        for case, row in numeric:
            evidence = row.get("suite_evidence") or row
            scope = evidence.get("assessment_scope") or row.get("assessment_scope") or "Not specified"
            limitations = tuple(dict.fromkeys([*row.get("limitations", []), *evidence.get("limitations", [])]))
            key = (current.observed_score(row), evidence.get("evidence_strength"), scope, limitations)
            grouped.setdefault(key, []).append(case)
        links = "".join(
            f'<li><a href="{esc(str(Path(cases[0]["evidence"]).parent / "results.html"))}">'
            f"{score(value)}/{esc(strength or 'Unassessed')}</a> ({len(cases)} assessments). "
            f"Scope: {esc(scope)}. {esc(' '.join(limitations))}</li>"
            for (value, strength, scope, limitations), cases in sorted(grouped.items())
        )
        exemplar_case, exemplar = (numeric or observations)[0]
        if ident == "task-contract.discoverable-requirements":
            for case in summary["task_contract"]["assessments"]:
                row = case["items"][ident]
                if row["score"] is not None:
                    links += f'<li><a href="{esc(case["evidence"])}">{esc(case["id"])}</a>: {score(row["score"])}/{esc(row["evidence_strength"])}</li>'
                    exemplar = row
        gap = table.attention(exemplar, "No numeric assessment supplied.")
        action = table.human_action(exemplar)
        grouped_details: dict[tuple[str, str, str], list[dict]] = {}
        for case, row in observations:
            reasons = [table.attention(row, "Unassessed")]
            if row.get("execution_state"):
                reasons.append(f"Execution: {row['execution_state']}.")
            reasons.extend(row.get("remaining_gaps", []))
            suite_gaps = (row.get("suite_evidence") or {}).get("remaining_gaps", [])
            if suite_gaps:
                reasons.append("Suite evidence: " + " ".join(suite_gaps))
            key = (row["status"], " ".join(dict.fromkeys(reasons)), table.human_action(row))
            grouped_details.setdefault(key, []).append(case)
        details = "".join(
            f"<li>{esc(status)}. {esc(reason)} {esc(next_action)}<ul>"
            + "".join(
                f'<li><a href="{esc(str(Path(case["evidence"]).parent / "results.html"))}">{esc(case["id"])}</a></li>'
                for case in cases
            )
            + "</ul></li>"
            for (status, reason, next_action), cases in grouped_details.items()
        )
        current_rows.append(
            f"<tr><td>{criterion(ident)}</td><td>{esc(item['level'])}</td>"
            f"<td>{'Suite evidence only. Not applicable to a single task. ' if item['level'] == 'suite' else ''}"
            f"{'<ul>' + links + '</ul>' if links else 'Unassessed'}</td>"
            f"<td>{esc(gap)}<br>{esc(action)}"
            f"<details><summary>All case findings and evidence</summary><ul>{details}</ul></details></td></tr>"
        )
    current_html = "".join(current_rows)
    production_gaps = ", ".join(label(ident) for ident in summary["production_gaps"])
    gaps = "".join(f"<li>{display_text(gap)}</li>" for gap in scorer["reviewed"]["remaining_gaps"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rubric demo: scorer quality and evidence integrity</title>
<style>
:root{{color-scheme:light dark}}body{{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:32px;max-width:1100px;color:var(--foreground,#ddd);background:var(--card,#172026)}}
h1{{font-size:30px;line-height:1.2}}h2{{margin-top:36px;font-size:22px}}a{{color:var(--accent,#95cd7a)}}p,li{{max-width:90ch}}.muted{{color:var(--muted-foreground,#b6c0c7)}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:left;border-bottom:1px solid var(--border,#46515a);padding:10px 12px;vertical-align:top}}th{{font-size:14px}}code,pre{{font-size:13px}}pre{{overflow:auto;padding:16px;border:1px solid var(--border,#46515a)}}summary{{cursor:pointer;padding:12px 0}}@media(max-width:600px){{body{{padding:16px}}h1{{font-size:25px}}}}
</style><main>
<p class="muted">Experimental rubric · local paired demonstration</p>
<h1>Current rubric assessments and authority evidence</h1>
<h2>Current canonical criteria</h2>
<p>The current inventory contains {len(summary["criterion_inventory"])} criteria: 19 task rows and 4 suite rows. These are separate fixture assessments, not one task score. Local refund controls are a synthetic simulation, not native/model evidence. Numeric groups retain their assessment scope and limitations. Numeric observations include collateral results. They do not establish qualified mutation coverage.</p>
<p>{len(summary["current_numeric_criteria"])} criteria have numeric observations in the retained evidence. Suite evidence stays separate from task scores. Unassessed is not zero. METHOD-J1 remains Unassessed because its canonical definition, checks and anchors are missing.</p>
<p>The current producers leave {esc(summary["pending_owner_requests"]["exact_request_count"])} exact review requests pending. <a href="current-leakage/owner-requests.json">Review requests and required decisions</a>. <a href="source-controls/coverage.json">Source-control coverage</a>. <a href="current-leakage/leakage-results.json">Current leakage controls and replay</a>.</p>
<div class="scroll"><table><thead><tr><th>Criterion</th><th>Level</th><th>Observed scores and evidence</th><th>Findings, missing evidence and next action</th></tr></thead><tbody>{current_html}</tbody></table></div>
<h2>Supporting local evidence</h2>
<p>The fixture demo runs the same frozen refund controls through two scorers. The integrity demo passes synthetic native results to the real receipt producer and rubric CLI. This run starts no Docker containers or models.</p>
<h2>Historical Anticheat-02 scoring evidence</h2>
<p>The AC criteria below belong to the historical rubric. Their numeric scores do not transfer to current criteria. Current AUDIT-03 observations above use the owning current assessment.</p>
<p>The completion-only baseline awards credit that the task forbids. The reviewed scorer accepts valid solutions and checks the required effects. Task-credit totals use each arm's own scale, separate from the canonical rubric score.</p>
<div class="scroll"><table><thead><tr><th>Scorer</th><th>Anticheat-02</th><th>False accepts / healthy invalid</th><th>False rejects / healthy valid</th><th>Operational errors</th><th>Evidence</th></tr></thead><tbody>{compare}</tbody></table></div>
<p>{esc(scorer["matched_controls"])}/{esc(scorer["attempted_controls"])} control outcomes match the frozen expectations. The reviewed scorer accepts {esc(scorer["reviewed"]["alternatives_accepted"])}/{esc(scorer["reviewed"]["alternatives_total"])} declared valid alternatives. The baseline has {esc(scorer["baseline"]["health_errors"])} operational errors. The reviewed arm has {esc(scorer["reviewed"]["health_errors"])}. These errors do not count as semantic rejections. Each control's frozen acceptance expectation for the reviewed arm determines its valid/invalid denominator. They include valid alternatives, permitted inaction, partial credit and the negative approval boundary. Operational cases remain separate. Same-input scorer replay: {esc(scorer["replay_equal"])}.</p>
<details><summary>Inspect all paired controls and task credit</summary><div class="scroll"><table><thead><tr><th>Control</th><th>Role</th><th>Baseline credit</th><th>Reviewed credit</th><th>Expected comparison</th></tr></thead><tbody>{cases}</tbody></table></div></details>
<h3>Receipt authenticity: Anticheat-03</h3>
<p>The baseline accepts a solver-authored success claim. The actual local control emits Anticheat-03 = 0/E2. The reviewed arm remains Unassessed. Anticheat-02 and Anticheat-03 share the refund-solver-authored-success finding. They describe the same exploit, not two independent exploits. Rejecting this one shortcut does not prove a complete receipt trust boundary.</p>
<table><thead><tr><th>Arm</th><th>Anticheat-03</th><th>Evidence strength</th><th>Evidence</th></tr></thead><tbody>{authenticity_rows}</tbody></table>
<h2>2. Integrity group: receipt_consistency</h2>
<p>The deterministic receipt-consistency check emits these separate 0–3 group scores (<code>provenance.evidence-revision-consistency.deterministic-1</code>). They do not score the whole Evidence revision consistency criterion. Required verification remains Unassessed because native freshness is unknown.</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Expected group</th><th>Observed group</th><th>Pass / fail / unknown / excluded</th><th>Canonical evidence revision consistency</th><th>Evidence</th></tr></thead><tbody>{anchors}</tbody></table></div>
<p><a href="integrity/report.md">Missing/stale evidence, acceptance claims and retry cases</a>: missing or stale evidence stays Unassessed. The acceptance helpers project results from partial claims. These examples do not demonstrate saved-envelope workflow acceptance. Retry candidates remain unscored. Fresh-directory normalized integrity replay: {esc(summary["integrity"]["fresh_replay_equal"])}.</p>
<h2>3. Identity and provenance</h2>
<p>Separate canonical scores assess the selected task, declared scorer route and dependency inventory. A wrong route lowers Grading-path identification to 0. An unresolved route remains Unassessed. A confirmed missing task lowers Task artifact access to 0. These identity results do not prove scorer correctness.</p>
<div class="scroll"><table><thead><tr><th>Mutation</th><th>{label("provenance.task-artifact-access")}</th><th>{label("provenance.grading-path")}</th><th>{label("provenance.grading-dependency-access")}</th></tr></thead><tbody>{identity_rows}</tbody></table></div>
<p><a href="identity/panel.json">Identity assessment and source/configuration diagnostics</a>. The provenance panel matches {esc(summary["provenance"]["matched"])}/{esc(summary["provenance"]["cases"])} cases through the actual CLI. The cases include changed selected bytes, broken links, configuration binding, stale dependencies and retained-text reconstruction. <a href="provenance/panel.json">Provenance evidence and individual report links</a>.</p>
<h2>4. Task contract and scoring authority</h2>
<h3>Selected-source evidence diagnostics</h3>
<p>The identity panel includes {esc(summary["identity"]["counts"]["evidence_cases"])} evidence cases. Authority locators resolve selected content, fail for a broken reference, or stay unknown for prose. An unaffected neighbor remains resolved. Resolving a locator does not establish semantic correctness or authenticated owner approval. These checks add no canonical score.</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Measurement</th><th>Status</th><th>Observed detail</th><th>Diagnostics</th></tr></thead><tbody>{evidence_rows}</tbody></table></div>
<h3 id="authority">W03 authority observations</h3>
<p>{len(authority_cases)} authority cases use the retained <a href="identity/panel.json">identity observations</a>. Check observations establish emission only. The selected results are synthetic inputs, not executed scorer results. Resolving a reference does not establish authenticated owner approval or semantic correctness.</p>
<p>The refund case resolves its contract references but selects no result artifact. Its checks remain unknown. Compare the <a href="scorer/inputs/contract.json">retained refund contract</a> and the separate <a href="scorer/rubric-reviewed.json">reviewed refund scorer evidence</a>. Those local controls do not supply a result to the authority case.</p>
{authority_html}
<p>The order-total task asks for total.txt. Its planted scorer also requires summary.json. The retained static crosswalk cites the instruction, scorer and input data. A clean neighbor removes that hidden condition. For the planted hidden condition, the real CLI emits Discoverable grading requirements = 0/E1. This score is bound to the supported fixture source bytes. The clean neighbor stays Unassessed because removing one defect does not establish a positive anchor. Clear task contract and Correctness and scoring authority remain Unassessed. No human adjudication is supplied.</p>
<table><thead><tr><th>Source case</th><th>{label("task-contract.clear-contract")}</th><th>{label("task-contract.discoverable-requirements")}</th><th>{label("task-contract.scoring-authority")}</th><th>Discoverability evidence strength</th><th>Evidence</th></tr></thead><tbody>{contract_rows}</tbody></table>
<p><a href="task-contract/report.md">Condition crosswalk and expected-versus-observed inspection</a>. {esc(summary["task_contract"]["matched"])}/{esc(summary["task_contract"]["cases"])} task-contract and Ethos scenarios match their expectations. The results come from authored static inspections and real structure checks. No model judgment was measured.</p>
<div class="scroll"><table><thead><tr><th>Ethos case</th><th>Measurement status</th><th>Structure valid</th><th>Owner confirmation</th><th>Evidence</th></tr></thead><tbody>{ethos_rows}</tbody></table></div>
<p>A structurally valid but changed Ethos still fails its digest binding. An unavailable validator leaves the result unknown. Valid structure does not establish confirmed owner intent. The owning journey-state validator handles saved-byte confirmation and invalidation. This demo supplies no reviewed user turn. Confirmation is NOT_RUN, and the demo creates no journey-state approval.</p>
<h2>Historical synthetic scoring-rule coverage</h2>
<p>The historical {esc(demo["criterion_count"])} criteria have tests for 0, 1, 2, 3 and missing through the actual measurement CLI: {esc(demo["vector_count_per_root"])} criterion vectors per root. These authored synthetic numbers do not qualify a task. They appear only in criterion_demonstration, never in real measured items. The source-bound scores above come from inspecting selected sources or executing local controls. This matrix tests how scoring rules respond to invented observations. Human review, native execution and independent corroboration were NOT_RUN.</p>
<div class="scroll"><table><thead><tr><th>Criterion</th><th>0 anchor</th><th>1 anchor</th><th>2 anchor</th><th>3 anchor</th><th>Missing evidence</th></tr></thead><tbody>{matrix}</tbody></table></div>
<p><a href="criterion-scoring/summary.json">Synthetic coverage and rejection controls</a>. Each cell links to the full emitted report, including evidence references and limitations. Synthetic E1 does not establish native or authenticated human evidence.</p>
<p>The historical synthetic matrix does not establish current 23-criterion coverage. The current inventory and ordinary assessments appear first.</p>
<h2>7. Remaining evidence gaps</h2>
<p>This demo produces no numeric observation for these {len(summary["production_gaps"])} criteria: {production_gaps}. Synthetic anchor tests do not fill these production evidence gaps. This list describes what the demo covers. It does not mean every adapter is absent.</p>
<p>The local paired controls match their expected outcomes. A canonical score of 2 requires the remaining applicable attack controls and native evidence. Independent held-out attacks and valid alternatives are required for score 3.</p><ul>{gaps}</ul>
<p>To demonstrate complete verification and recovery, first produce a full saved acceptance envelope and show ordinary acceptance. Then invalidate it with a later failure or source change. Rerun the affected controls and show ordinary acceptance again. The current partial-claim cases do not demonstrate that sequence.</p>
<p>These results establish no overall quality score, coverage/diversity score or task qualification. They establish neither authenticated approval authority nor a causal authoring benefit. A matched authoring study is separate work.</p>
<h2>Reproduce</h2><p>Fresh-root semantic repeat: {esc(summary["determinism"]["fresh_run_equal"])}. The demo regenerates every included area in fresh-replay/ and compares the results, including partial acceptance and retry diagnostics. <a href="semantic-result.json">Explicit semantic projection</a> · <a href="fresh-replay/semantic-result.json">Fresh projection</a>. Raw path-specific identities remain separate and unmodified. This shows repeatability within the same checkout. It does not show that raw bytes stay equal after relocation. Identity inputs are temporary. The retained panel cannot replay those inputs durably.</p><pre>uv run --locked python tools/run_rubric_showcase.py \\
  --output "$PWD/.eval-author/showcase-new"</pre>
<p>Use a new output directory. Data-only retained verification: <code>uv run --locked python tools/run_rubric_showcase.py --verify-existing "$PWD/.eval-author/showcase-new"</code>. This validates emitted reports, semantic comparisons and byte manifests without executing retained code. The local hash manifest is not authenticated attestation. Logs are invocation captures, not authenticated execution evidence. Top-level captures are checked against their emitted JSON and expected empty stderr; nested logs have byte-manifest coverage only. These consistency checks do not authenticate a historical execution. The temporary identity inputs cannot be replayed. <a href="showcase.json">Summary projection</a> · <a href="scorer/panel.json">Frozen control comparison</a> · <a href="integrity/hashes.json">Integrity artifact hashes</a></p>
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
    for root in (output, output / "fresh-replay"):
        validate_current(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path, help="new private output directory")
    mode.add_argument(
        "--verify-existing", type=Path, help="read-only retained bundle validation; never executes retained code"
    )
    args = parser.parse_args(argv)
    created = False
    output = (args.output or args.verify_existing).absolute()
    try:
        if ".." in output.parts:
            raise ValueError(f"unsafe output directory (parent traversal): {output}")
        for path in (output, *output.parents):
            if path.is_symlink():
                raise ValueError(f"unsafe output directory: symlinked component {path}; use its physical path")
        if args.verify_existing:
            verify_existing(output)
            print(output / "index.html")
            return 0
        if output.exists():
            raise ValueError(
                f"output already exists: {output}; choose a new output directory; retained files were not changed"
            )
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
        summary = collect(output)
        fresh = output / "fresh-replay"
        fresh.mkdir(mode=0o700)
        collect(fresh)
        summary["determinism"] = compare_runs(output)
        if not summary["scorer"]["replay_equal"] or not summary["integrity"]["fresh_replay_equal"]:
            raise ValueError("demo replay mismatch")
        (output / "showcase.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        (output / "index.html").write_text(render(summary, read(output / "scorer/panel.json")))
        (output / "showcase-hashes.json").write_text(json.dumps(retained_hashes(output), indent=2) + "\n")
        verify_existing(output)
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, subprocess.TimeoutExpired) as exc:
        detail = str(exc)
        if created:
            detail += f"; partial evidence retained at {output}; choose a new output directory to retry"
        parser.error(detail)
    print(output / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
