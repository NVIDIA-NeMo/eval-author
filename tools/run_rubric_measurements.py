# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Credential-free synthetic rubric mutations; never runs task/scorer code."""

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author-rubric/scripts"))
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
from eval_author_shared.task_evidence import digest, loads  # noqa: E402
from first_eval_state import fingerprint  # noqa: E402
from measure import measure  # noqa: E402


def fixture(root, mutation):
    files: dict[str, Any] = {
        "task": {"task_id": "task-1"},
        "entry": {"selector": "strict", "routes": {"strict": "scorer", "weak": "other"}},
        "scorer": {"checks": ["amount", "approval"]},
        "other": {"checks": ["claimed_success"]},
        "prompt": {"role": "judge-input"},
        "ethos": {"intent": "synthetic refund task"},
    }
    if mutation == "wrong-scorer":
        files["entry"]["selector"] = "weak"
    elif mutation == "ambiguous-route":
        files["entry"] = {"selector": None, "routes": files["entry"]["routes"]}
    scope: dict[str, dict[str, Any]] = {
        "task_artifacts": {"required": ["task"], "source": "task-inventory", "unresolved_material": []},
        "grading_dependencies": {
            "required": ["scorer", "other", "prompt"],
            "source": "grading-inventory",
            "unresolved_material": [],
            "review_source": "review",
        },
    }
    if mutation == "partial":
        scope["task_artifacts"]["unresolved_material"] = ["Unresolved generated task input reference"]
        scope["grading_dependencies"]["required"].append("absent")
        scope["grading_dependencies"]["unresolved_material"] = ["Unresolved scorer dependency: absent"]
    for name, inventory in scope.items():
        files[inventory["source"]] = {
            name: {key: copy.deepcopy(inventory[key]) for key in ("required", "unresolved_material")}
        }
    roles = {
        "task": "task",
        "entry": "configuration",
        "scorer": "scorer",
        "other": "scorer",
        "prompt": "judge_prompt",
        "ethos": "ethos",
        "task-inventory": "manifest",
        "grading-inventory": "manifest",
        "review": "review",
    }
    files["review"] = {
        "source": scope["grading_dependencies"]["source"],
        "required": copy.deepcopy(scope["grading_dependencies"]["required"]),
        "unresolved_material": copy.deepcopy(scope["grading_dependencies"]["unresolved_material"]),
        "complete": mutation != "partial",
    }
    dependencies = {"scorer": ["prompt"], "other": [], "prompt": []}
    if mutation == "partial":
        dependencies["scorer"].append("absent")
    if mutation == "missing-dependency":
        dependencies["scorer"] = ["absent"]
    entry: dict[str, Any] = files["entry"]
    entry["dependencies"] = copy.deepcopy(dependencies)
    entry["expected_scorer"] = "scorer"
    records = []
    for name, value in files.items():
        path = root / (name + ".json")
        path.write_text(json.dumps(value, sort_keys=True))
        records.append(
            {
                "id": name,
                "role": roles[name],
                "path": path.name,
                "digest": digest(path),
                "revision": None,
                "access": "available",
                "selection_origin": "explicit",
                "source": "task-inventory.json"
                if name == "task"
                else "grading-inventory.json"
                if name in ("scorer", "other", "prompt", "review")
                else "fixture:selection",
                "evidence": [{"path": path.name, "digest": digest(path)}],
            }
        )
    if mutation in ("missing", "denied", "unresolved"):
        records[0]["access"] = mutation
        records[0]["digest"] = None
        records[0]["evidence"] = []
    if mutation == "confirmed-missing":
        (root / "task.json").unlink()
    if mutation == "changed-task":
        (root / "task.json").write_text('{"task_id":"task-changed"}')
    config: dict = {
        "agent": "scripted",
        "run": "run-1",
        "task": "task-1",
        "tool_permissions": [],
        "provider": "harbor",
        "runtime_pins": {"harbor": "0.24.0"},
        "environment": {"network": "none"},
    }
    observed = copy.deepcopy(config)
    if mutation == "config-difference":
        observed["runtime_pins"]["harbor"] = "different"
    bindings = {row["id"]: row["digest"] for row in records}
    approval: dict = {
        "id": "approval",
        "decision_id": "decision",
        "scope": "task:task/substitution",
        "substitution": {"from": "other", "to": "scorer"},
        "bindings": copy.deepcopy(bindings),
    }
    decision = {key: copy.deepcopy(value) for key, value in approval.items() if key != "decision_id"}
    decision.update(id="decision", outcome="approved", source="fixture:user-turn")
    if mutation == "stale-approval":
        approval["bindings"]["ethos"] = "f" * 64
        decision["bindings"]["ethos"] = "f" * 64
    nodes = [{"id": "task", "role": "task"}, {"id": "run", "role": "run"}]
    links = [{"source": "run", "target": "task", "relation": "task"}]
    if mutation == "broken-reference":
        links = []
    return {
        "artifacts": records,
        "rubric_scope": scope,
        "route": {"entrypoint": "entry", "expected_scorer": "scorer", "dependencies": dependencies},
        "declared_config": config,
        "observed_config": observed,
        "nodes": nodes,
        "links": links,
        "approvals": [approval],
        "decisions": [] if mutation == "missing-decision" else [decision],
    }


def selected_file(root, selection, ident, role, value, name=None):
    """Select a handcrafted JSON value, or raw bytes written to `name`."""
    relative = name or ident + ".json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
    record = next((row for row in selection["artifacts"] if row["id"] == ident), None)
    if record is None:
        record = {
            "id": ident,
            "role": role,
            "path": relative,
            "revision": None,
            "access": "available",
            "selection_origin": "explicit",
            "source": "handcrafted:rubric-demo",
        }
        selection["artifacts"].append(record)
    record.update(digest=digest(path), evidence=[{"path": relative, "digest": digest(path)}])
    return record


ETHOS_VALIDATOR = ROOT / "team-skills/ethos/ethos-write/scripts/validate_ethos.py"
ETHOS_DOCUMENT = (ROOT / "team-skills/ethos/ethos-write/evals/files/router-help/ETHOS.md").read_bytes()


def ethos_selection(root, selection, mutation):
    """Select the ethos-write router-help fixture ETHOS.md for the installed validator."""
    content = ETHOS_DOCUMENT
    if mutation == "ethos-malformed":
        content = content.replace(b"\n## Constraints\n", b"\n## Limits\n")
    selected_file(root, selection, "ethos-document", "ethos", content, "ETHOS.md")
    selection["ethos"] = {"artifact_id": "ethos-document"}


def authority_selection(root, selection, mutation):
    """Select a handcrafted construction plan whose second requirement is an unaffected neighbor."""
    selected_file(root, selection, "ethos-document", "ethos", ETHOS_DOCUMENT, "ETHOS.md")
    first = {
        "authority-resolved": "ETHOS.md#Constraints",
        "authority-broken-reference": "ETHOS.md#Refund limits",
        "authority-prose": "Reviewed refund rule",
    }[mutation]
    plan = {
        "requirements": [
            {
                "id": "limit",
                "statement": "Respect the constraint",
                "authority": {"basis": "specification", "reference": first},
            },
            {
                "id": "task",
                "statement": "Use the task",
                "authority": {"basis": "specification", "reference": "task.json#/task_id"},
            },
        ],
        "checks": [{"id": "total", "requirements": ["limit", "task"]}],
    }
    selected_file(root, selection, "construction", "construction", plan)
    selection["authority"] = {"plan_artifact_id": "construction"}


def evidence_fixture(root, mutation):
    selection = fixture(root, None)
    if mutation.startswith("ethos-"):
        ethos_selection(root, selection, mutation)
    elif mutation.startswith("authority-"):
        authority_selection(root, selection, mutation)
    elif mutation.startswith("sourced-"):
        config = copy.deepcopy(selection["declared_config"])
        config["tool_permissions"] = {"network": False}
        declared = selected_file(root, selection, "declared-settings", "configuration", config)
        selected_file(root, selection, "run-record", "run", {"run_id": "run-1", "synthetic": True})
        observed = copy.deepcopy(config)
        if mutation == "sourced-different":
            observed["runtime_pins"]["harbor"] = "handcrafted-different-version"
        elif mutation == "sourced-provider-hidden":
            del observed["runtime_pins"]["harbor"]
        selected_file(
            root,
            selection,
            "observed-settings",
            "configuration",
            {
                "settings": observed,
                "run": "run-1",
                "task": "task-1",
                "configuration_digest": declared["digest"],
                "provenance": "handcrafted observation; not native metadata",
            },
        )
        pointers = ["/agent", "/run", "/task", "/tool_permissions", "/provider", "/runtime_pins/harbor", "/environment"]
        selection["sourced_config"] = {
            "declared": {path: {"artifact_id": "declared-settings", "pointer": path} for path in pointers},
            "observed": {
                path: {"artifact_id": "observed-settings", "pointer": "/settings" + path} for path in pointers
            },
            "binding": {
                "run": {"artifact_id": "run-record", "pointer": "/run_id"},
                "task": {"artifact_id": "task", "pointer": "/task_id"},
                "configuration_artifact_id": "declared-settings",
                "observations": {
                    "observed-settings": {
                        "run": "/run",
                        "task": "/task",
                        "configuration_digest": "/configuration_digest",
                    }
                },
            },
        }
        if mutation == "sourced-provider-hidden":
            selection["sourced_config"]["observed"]["/runtime_pins/harbor"] = {
                "state": "provider_hidden",
                "reason": "provider_not_disclosed",
            }
    else:
        selected_file(
            root,
            selection,
            "task",
            "task",
            {"task_id": "task-1", "instruction": "Refund only approved amounts", "synthetic": True},
        )
        selected_file(
            root, selection, "neighbor", "dependency", {"instruction": "Count approved invoices", "synthetic": True}
        )
        selection["freshness"] = []
        for ident, dependencies in (
            ("refund-note", ["task", "scorer", "prompt", "ethos"]),
            ("neighbor-note", ["neighbor"]),
        ):
            note = selected_file(
                root, selection, ident, "evidence", {"kind": "handcrafted review note", "executed": False}
            )
            records = {row["id"]: row for row in selection["artifacts"]}
            retained = {
                "evidence": {"id": ident, "digest": note["digest"]},
                "dependencies": [
                    {
                        "id": dep,
                        "digest": records[dep]["digest"],
                        "revision": records[dep]["revision"],
                        "impact": "unknown" if dep == "ethos" else "material",
                    }
                    for dep in dependencies
                ],
            }
            selected_file(root, selection, ident + "-bindings", "manifest", {"retained": retained})
            selection["freshness"].append(
                {"id": ident, "kind": "bindings", "source": ident + "-bindings", "pointer": "/retained"}
            )
        changes = {
            "material-task-change": (
                "task",
                "task",
                {"task_id": "task-1", "instruction": "Refund approved amounts capped at 25", "synthetic": True},
            ),
            "material-scorer-change": ("scorer", "scorer", {"checks": ["amount", "manager_approval"]}),
            "material-dependency-change": (
                "prompt",
                "judge_prompt",
                {"role": "judge-input", "instruction": "Require manager approval"},
            ),
            "unknown-ethos-change": (
                "ethos",
                "ethos",
                {"intent": "handcrafted refund task with revised approval obligations"},
            ),
        }
        if mutation in changes:
            selected_file(root, selection, *changes[mutation])
    # Current selection is refreshed; historical note bindings are never rewritten.
    bindings = {row["id"]: row["digest"] for row in selection["artifacts"]}
    for row in selection["approvals"] + selection["decisions"]:
        row["bindings"] = copy.deepcopy(bindings)
    return selection


def ethos_or_authority_matches(observation, case):
    codes = sorted({row["code"] for row in observation.get("diagnostics", [])})
    if codes != case["codes"]:
        return False
    if case["measurement"] == "ethos":
        return observation.get("valid") == case["valid"]
    return [row["status"] for row in observation.get("requirements", [])] == case["requirement_statuses"]


def canonical_checks(report, definitions, expected=None):
    items = report.get("items", {})
    if set(items) != {item["id"] for item in definitions["items"]}:
        return False
    seen = {}
    for definition in definitions["items"]:
        item = items[definition["id"]]
        checks = item.get("check_results", [])
        if not set(definitions["result_template"]) <= set(item):
            return False
        if len(checks) != len(definition["checks"]) or {check["check_id"] for check in checks} != {
            check["id"] for check in definition["checks"]
        }:
            return False
        by_id = {check["id"]: check for check in definition["checks"]}
        for check in checks:
            source = by_id[check["check_id"]]
            if check["method"] != source["method"] or check["id"] != check["check_id"]:
                return False
            if not check.get("evidence_boundary") or not check.get("next_action"):
                return False
            if check["outcome"] != "PASS" and not check.get("remaining_gap"):
                return False
            if source["method"] != "deterministic" and (check["outcome"] != "NOT_RUN" or check["evidence"]):
                return False
            seen[check["check_id"]] = check["outcome"]
    return (
        (expected is None or seen == expected)
        and report.get("preflight_success") is False
        and report.get("anti_cheat_scores") is None
    )


def evidence_matches(report, case, selection, root):
    observation = report.get("measurements", {}).get(case["measurement"], {})
    if observation.get("status") != case["status"]:
        return False
    if case["measurement"] in ("ethos", "authority"):
        return ethos_or_authority_matches(observation, case)
    if case["measurement"] == "sourced_config":
        if not all(observation.get(key) == case[key] for key in ("differences", "unknowns", "counts")):
            return False
        if (
            not all(key in observation and observation[key] is None for key in ("acceptable", "score"))
            or observation.get("native_attestation") is not False
        ):
            return False
        for path, field in observation["fields"].items():
            if (
                field["declared"]["state"] != "sourced"
                or field["observed"]["basis"] != "selected_source_not_native_attestation"
            ):
                return False
            if path in case["unknowns"]:
                if (
                    field["status"] != "unknown"
                    or field["observed"]["state"] != "provider_hidden"
                    or field["observed"]["reason"] != "provider_not_disclosed"
                ):
                    return False
            elif field["observed"]["binding"]["status"] != "run_bound" or field["status"] != (
                "different" if path in case["differences"] else "equal"
            ):
                return False
        return True
    if (
        sorted({row["code"] for row in observation.get("diagnostics", [])}) != case["codes"]
        or observation.get("criterion_3") != "not_assessed"
    ):
        return False
    records = observation.get("records", [])
    if len(records) != 2 or {row["id"] for row in records} != {"refund-note", "neighbor-note"}:
        return False
    selected = {row["id"]: row for row in selection["artifacts"]}
    for row in records:
        affected = row["id"] == "refund-note"
        state = case["affected_status"] if affected else "current"
        if (
            row["status"] != state
            or row["usable"] != (state == "current")
            or row["changed_dependencies"] != (case["changed_dependencies"] if affected else [])
        ):
            return False
        retained = loads((root / selected[row["id"] + "-bindings"]["path"]).read_text())["retained"]
        expected_lineage = [
            {
                "id": old["id"],
                "old": {key: old[key] for key in ("digest", "revision")},
                "new": {key: selected[old["id"]][key] for key in ("digest", "revision")},
                "impact": old["impact"],
            }
            for old in sorted(retained["dependencies"], key=lambda dep: dep["id"])
        ]
        if row["lineage"] != expected_lineage:
            return False
        changed = [entry["id"] for entry in row["lineage"] if entry["old"] != entry["new"]]
        if changed != row["changed_dependencies"]:
            return False
    return True


def package_cli(root, script, *args):
    process = subprocess.run(
        [sys.executable, "-B", str(ROOT / script), *map(str, args)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={"PYTHONDONTWRITEBYTECODE": "1", "HOME": str(root), "PATH": "/usr/bin:/bin"},
    )
    return process.returncode, loads(process.stdout), process.stderr


def package_inputs(root):
    return {path.relative_to(root).as_posix(): digest(path) for path in root.rglob("*") if path.is_file()}


def shared_package_fixture(root, mutation):
    from eval_author_shared import task_evidence

    selection = {
        "artifacts": [],
        "route": {
            "entrypoint": "dispatch",
            "expected_scorer": "scorer",
            "dependencies": {"scorer": []},
        },
    }
    task = root / "task"
    task.mkdir()
    scorer = selected_file(task, selection, "scorer", "scorer", {"synthetic": True, "executed": False})
    scorer["path"] = "task/scorer.json"
    scorer["evidence"][0]["path"] = scorer["path"]
    (task / "scorer.json").chmod(0o644)
    selected_file(
        root,
        selection,
        "dispatch",
        "dispatch",
        {
            "selector": None if mutation == "unknown-route" else "selected",
            "routes": {"selected": "scorer"},
            **({} if mutation == "unbound-route" else {"dependencies": {"scorer": []}}),
        },
    )
    if mutation == "dependency-mismatch":
        selection["route"]["dependencies"] = {"scorer": ["trace"], "trace": []}
    if mutation == "wrong-scorer":
        selection["route"]["expected_scorer"] = "different-scorer"
    controls: dict[str, dict[str, Any]] = {
        name: {
            "kind": name,
            "requirement": "Synthetic parser control; never an executed trial.",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
        }
        for name, reward in (("reference", 1), ("incorrect", 0), ("empty", 0), ("agent", 1))
    }
    manifest = selected_file(
        root,
        selection,
        "manifest",
        "manifest",
        {
            "schema": task_evidence.MANIFEST_SCHEMA,
            "task_dir": "task",
            "task_sha256": task_evidence.task_digest(task),
            "harbor_task_checksum": "synthetic-not-executed",
            "contract": {
                "task_id": "example",
                "provider": "harbor",
                "native_run_id": "/id",
                "cases": controls,
                "alternative_not_applicable": "One exact synthetic output; no alternative implementation is run.",
                "side_effect_not_applicable": "Synthetic read-only parser fixture.",
            },
            "construction": {"status": "unmeasured"},
            "repair_count": 0,
        },
    )
    for name, control in controls.items():
        native = selected_file(
            root,
            selection,
            name + "-result",
            "native-result",
            {
                "id": name,
                "task_checksum": "synthetic-not-executed",
                "exception_info": None,
                "verifier_result": {"rewards": {"reward": control["checks"][0]["min"]}},
                "synthetic": True,
            },
        )
        selected_file(
            root,
            selection,
            name + "-receipt",
            "receipt",
            {
                "schema": task_evidence.SCHEMA,
                "manifest": "manifest.json",
                "manifest_sha256": manifest["digest"],
                "case": name,
                "purpose": "closure",
                "returncode": 0,
                "result": native["path"],
                "result_sha256": native["digest"],
                "native_run_id": name,
                "status": "passed",
                "synthetic": True,
            },
        )
    trace = selected_file(
        root,
        selection,
        "trace",
        "trace",
        {
            "schema_version": "ATIF-v1.7",
            "session_id": "synthetic-session",
            "trajectory_id": "synthetic-root",
            "agent": {"name": "example-agent", "version": "1.0.0"},
            "steps": [
                {"step_id": 1, "source": "user", "message": "Synthetic lookup."},
                {
                    "step_id": 2,
                    "source": "agent",
                    "message": "Synthetic, not executed.",
                    "tool_calls": [{"tool_call_id": "call-1", "function_name": "customer.lookup", "arguments": {}}],
                },
            ],
        },
    )
    if mutation in {"missing", "denied", "unresolved"}:
        trace.update(access=mutation, digest=None, evidence=[])
    (root / "findings.md").write_text("## Key findings\n\nSynthetic control parser fixture, not task qualification.\n")
    (root / "ETHOS.md").write_text("# Ethos\n\n## Tools\n\n- customer.lookup\n")
    audit = root / ".eval-author/audit.md"
    audit.parent.mkdir()
    template = (ROOT / "skills/eval-author-audit/templates/audit.md").read_text()
    audit.write_text(
        template.replace('sha256: "sha256:<replace-with-64-hex-digest>"', "sha256: sha256:" + digest(root / "ETHOS.md"))
    )
    return selection


def shared_package_case(root, case):
    selection = shared_package_fixture(root, case["mutation"])
    request = root / "selection.json"
    request.write_text(json.dumps(selection, sort_keys=True))
    before = package_inputs(root)
    retained_credit_code, retained_credit, retained_credit_error = package_cli(
        root,
        "skills/eval-author/scripts/audit_verifier_evidence.py",
        "--repo",
        root,
        "--report",
        root / "findings.md",
        "--task",
        root / "task",
        "--selection",
        request,
    )
    coverage_code, coverage, coverage_error = package_cli(
        root,
        "skills/eval-author-audit/scripts/audit_spec/measure.py",
        "--audit",
        root / ".eval-author/audit.md",
        "--trace",
        root / "trace.json",
        "--task-id",
        "task:/stable",
        "--run-id",
        "run:/stable",
        "--evidence-class",
        "agent_under_test",
        "--out-dir",
        root / "output",
        "--identity-root",
        root,
        "--identity-selection",
        request,
    )
    preflight = coverage["identity_preflight"]
    same = (
        retained_credit["identity"] == preflight["identity"]
        and retained_credit["identity"]["records"] == selection["artifacts"]
    )
    trace_binding = None
    if case["allowed"] and coverage.get("measurements"):
        coverage_report = loads(Path(coverage["measurements"][0]["coverage"]).read_text())
        trace_binding = (
            coverage_report["identity_preflight"] == preflight
            and coverage_report["binding"]["trace_sha256"] == digest(root / "trace.json")
            and coverage_report["covered"] == ["customer.lookup"]
        )
    after = package_inputs(root)
    unchanged = before == {key: value for key, value in after.items() if not key.startswith("output/")}
    observed = {
        "same_identity_records": same,
        "retained_credit": {key: retained_credit[key] for key in ("identity", "route", "preflight_success", "valid")},
        "coverage": preflight,
        "cli_exit_codes": [retained_credit_code, coverage_code],
        "coverage_binding_verified": trace_binding,
        "retained_credit_controls": sorted(row["classification"] for row in retained_credit["controls"]),
        "outputs_written": (root / "output").exists(),
    }
    matches = (
        same
        and not retained_credit_error
        and not coverage_error
        and retained_credit_code == coverage_code == case["cli_exit_code"]
        and retained_credit["preflight_success"] is case["allowed"]
        and retained_credit["valid"] is case["allowed"]
        and preflight["measurement_allowed"] is case["allowed"]
        and preflight["preflight_success"] is False
        and observed["outputs_written"] is case["allowed"]
        and (trace_binding is True if case["allowed"] else not retained_credit["controls"])
        and (
            observed["retained_credit_controls"] == ["agent_run", "passed", "passed", "passed"]
            if case["allowed"]
            else True
        )
    )
    for envelope in (retained_credit, preflight):
        for key in ("identity", "route"):
            matches &= (
                envelope[key]["status"] == case[key + "_status"]
                and sorted({row["code"] for row in envelope[key]["diagnostics"]}) == case[key + "_codes"]
            )
        matches &= envelope["route"]["executed_scorer"] is None
        if case["allowed"]:
            dispatch = next(row for row in selection["artifacts"] if row["id"] == "dispatch")
            matches &= envelope["route"].get("bound_dependency") is True
            matches &= envelope["route"].get("dependency_source") == {
                "artifact_id": "dispatch",
                "pointer": "/dependencies",
                "digest": dispatch["digest"],
            }
        elif case["mutation"] in {"unbound-route", "dependency-mismatch"}:
            matches &= envelope["route"].get("bound_dependency") is False
    return observed, selection, unchanged, matches


def rubric_package_case(root, case):
    if case["kind"] == "approval":
        selection = fixture(root, None)
        retained = copy.deepcopy(selection["decisions"][0])
        if case["mutation"] == "rejected":
            retained["outcome"] = "denied"
        selected_file(root, selection, "retained-decision", "decision", {"decisions": [retained]})
        selection["approval_sources"] = {"decision": {"artifact_id": "retained-decision", "pointer": "/decisions/0"}}
    else:
        selection = evidence_fixture(root, "sourced-equal")
        if case["mutation"] != "equal":
            value = loads((root / "observed-settings.json").read_text())
            value[case["mutation"]] = "unrelated-binding"
            selected_file(root, selection, "observed-settings", "configuration", value)
            bindings = {row["id"]: row["digest"] for row in selection["artifacts"]}
            for row in selection["approvals"] + selection["decisions"]:
                row["bindings"] = copy.deepcopy(bindings)
    request = root / "selection.json"
    request.write_text(json.dumps(selection, sort_keys=True))
    before = package_inputs(root)
    code, report, error = package_cli(
        root, "skills/eval-author-rubric/scripts/measure.py", "--root", root, "--selection", request
    )
    matches = not error and code == case["cli_exit_code"] and report["preflight_success"] is False
    if case["kind"] == "approval":
        observed = report["measurements"]["approvals"]
        matches &= (
            observed["status"] == case["status"]
            and sorted({row["code"] for row in observed["diagnostics"]}) == case["codes"]
            and observed["authority_authenticated"] is False
        )
        retained_record = next(row for row in selection["artifacts"] if row["id"] == "retained-decision")
        expected_evidence = (
            [
                {
                    "artifact_id": "retained-decision",
                    "decision_id": "decision",
                    "pointer": "/decisions/0",
                    "digest": retained_record["digest"],
                }
            ]
            if case["mutation"] == "approved"
            else []
        )
        matches &= observed["evidence"] == expected_evidence
    else:
        config = report["measurements"]["sourced_config"]
        bindings = [field["observed"]["binding"] for field in config["fields"].values()]
        observed = {
            "comparison_status": config["status"],
            "binding_accepted": all(row["status"] == "run_bound" for row in bindings),
            "binding_statuses": sorted({row["status"] for row in bindings}),
            "binding_reasons": sorted({row.get("reason") for row in bindings}, key=str),
            "counts": config["counts"],
            "native_attestation": config["native_attestation"],
            "score": config["score"],
            "acceptable": config["acceptable"],
            "cli_exit_code": code,
        }
        matches &= (
            observed["comparison_status"] == case["comparison_status"]
            and observed["binding_accepted"] is case["binding_accepted"]
            and observed["binding_statuses"] == [case["binding_status"]]
            and observed["binding_reasons"] == [case["binding_reason"]]
            and config["counts"]["equal"] == 7
            and config["counts"]["run_bound"] == (7 if case["binding_accepted"] else 0)
            and config["native_attestation"] is False
            and config["score"] is None
            and config["acceptable"] is None
        )
    return observed, selection, before == package_inputs(root), matches


def package_acceptance(cases):
    rows = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="rubric-package-") as directory:
            root = Path(directory).resolve()
            run = shared_package_case if case["kind"] == "shared" else rubric_package_case
            observed, selection, unchanged, matches = run(root, case)
            rows.append(
                {
                    "id": case["id"],
                    "expected": case,
                    "observed": observed,
                    "selection_digest": fingerprint(selection),
                    "inputs_unchanged": unchanged,
                    "matches_expectations": bool(matches and unchanged),
                }
            )
    matched = sum(row["matches_expectations"] is True for row in rows)
    return {
        "status": "passed" if rows and matched == len(rows) else "failed",
        "cases": rows,
        "counts": {"cases": len(rows), "matched": matched},
        "runtime_acceptance": "not_measured",
        "merit_acceptance": "not_assessed",
        "evidence_kind": "handcrafted-local-cli-integration",
        "gaps": [
            "No native execution, model calls, solver permissions, or provider validation.",
            "Synthetic native-shaped receipts exercise parsers, not actual task outcomes.",
            "Source-bound declared JSON dependencies do not prove arbitrary-code closure.",
            "Retained decision bytes do not authenticate user authority.",
        ],
    }


def run_panel():
    cases = loads((ROOT / "evals/rubric-measurements/cases.json").read_text())
    definitions = loads(
        (ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json").read_text()
    )
    canonical_ids = {check["id"] for item in definitions["items"] for check in item["checks"]}
    if set(cases["check_outcomes"]) != canonical_ids:
        raise ValueError("Panel expectations must cover every canonical check ID")
    rows = []
    for case in cases["raw_measurements"]:
        with tempfile.TemporaryDirectory(prefix="rubric-measurements-") as directory:
            root = Path(directory).resolve()
            selection = fixture(root, case["mutation"])
            report = measure(root, selection)
            observation = report["measurements"][case["measurement"]]
            codes = sorted({row["code"] for row in observation.get("diagnostics", [])})
            matches = (
                observation["status"] == case["status"]
                and codes == case["codes"]
                and observation.get("differences", []) == case.get("differences", [])
                and canonical_checks(report, definitions)
            )
            rows.append(
                {
                    "id": case["id"],
                    "measurement": case["measurement"],
                    "expected": case,
                    "observed": observation,
                    "selection_digest": fingerprint(selection),
                    "matches_expectations": matches,
                }
            )
    assessments = []
    evidence_cases = []
    baseline_items = next(case["items"] for case in cases["assessments"] if case["id"] == "valid")
    for case in cases["assessments"] + cases["evidence_cases"]:
        is_evidence = "measurement" in case
        with tempfile.TemporaryDirectory(prefix="rubric-assessment-") as directory:
            root = Path(directory).resolve()
            selection = evidence_fixture(root, case["mutation"]) if is_evidence else fixture(root, case["mutation"])
            before = {path.name: digest(path) for path in root.iterdir()}
            request = root / "selection.json"
            request.write_text(json.dumps(selection, sort_keys=True, allow_nan=False))
            code, report, error = package_cli(
                root,
                "skills/eval-author-rubric/scripts/measure.py",
                "--root",
                root,
                "--selection",
                request,
                *(("--ethos-validator", ETHOS_VALIDATOR) if "ethos" in selection else ()),
            )
            expected_items = {
                **case.get("items", baseline_items),
                **{key: {"score": None, "status": "NOT_ASSESSED"} for key in cases["not_assessed"]},
            }
            observed_items = report.get("items", {})
            expected_checks = {
                **cases["check_outcomes"],
                **cases["check_overrides"].get(case["id"], {}),
                **cases["evidence_check_overrides"].get(case.get("measurement"), {}),
            }
            matches = (
                code == case["cli_exit_code"]
                and not error
                and before == {path.name: digest(path) for path in root.iterdir() if path != request}
                and canonical_checks(report, definitions, expected_checks)
                and set(observed_items) == set(expected_items)
                and all(
                    all(observed_items[key].get(field) == value for field, value in expected.items())
                    for key, expected in expected_items.items()
                )
                and all(
                    sorted({row["code"] for row in report["measurements"][name].get("diagnostics", [])}) == codes
                    for name, codes in case.get("diagnostics", {"identity": [], "route": [], "approvals": []}).items()
                )
                and (not is_evidence or evidence_matches(report, case, selection, root))
            )
            (evidence_cases if is_evidence else assessments).append(
                {
                    "id": case["id"],
                    "expected": case,
                    "expected_items": expected_items,
                    "expected_checks": expected_checks,
                    "observed": report,
                    "cli_exit_code": code,
                    "selection_digest": fingerprint(selection),
                    "matches_expectations": matches,
                }
            )
    package = package_acceptance(cases["package_acceptance"])
    return {
        "package_acceptance": package,
        "schema": "rubric-mutation-panel-v3",
        "measurement": "synthetic-local-mutations",
        "status": "passed"
        if rows
        and assessments
        and evidence_cases
        and package["status"] == "passed"
        and all(row["matches_expectations"] for row in rows + assessments + evidence_cases)
        else "failed",
        "cases_digest": fingerprint(cases),
        "cases": rows,
        "assessments": assessments,
        "evidence_cases": evidence_cases,
        "counts": {"cases": len(rows), "assessments": len(assessments), "evidence_cases": len(evidence_cases)},
        "canonical_checks_per_case": len(canonical_ids),
        "native_execution": False,
        "agent_exploitation": False,
        "workflow_integration": False,
    }


def summary(result):
    panels = {
        "cases": result["cases"],
        "assessments": result["assessments"],
        "evidence_cases": result["evidence_cases"],
        "package_acceptance": result["package_acceptance"]["cases"],
    }
    mismatches = {name: [row["id"] for row in rows if not row["matches_expectations"]] for name, rows in panels.items()}
    lines = [
        f"Rubric handcrafted assessment demo: {result['status']}",
        f"Raw measurement regressions: {sum(row['matches_expectations'] for row in result['cases'])}/{len(result['cases'])} matched",
        "Variant | DISC-01-A | DISC-01-B | DISC-01-C | expected match",
    ]
    for case in result["assessments"]:
        values = [
            json.dumps(case["observed"]["items"][key]["score"]) for key in ("DISC-01-A", "DISC-01-B", "DISC-01-C")
        ]
        lines.append(" | ".join([case["id"], *values, str(case["matches_expectations"])]))
    lines.extend(
        [
            f"Config, freshness, Ethos and authority evidence: {sum(row['matches_expectations'] for row in result['evidence_cases'])}/{len(result['evidence_cases'])} matched; {result['canonical_checks_per_case']} canonical check IDs per case.",
            "null = NOT_ASSESSED.",
            "No native execution or solver attempts; full workflow/runtime acceptance remains unmeasured.",
            f"Package acceptance: {result['package_acceptance']['status']}; {result['package_acceptance']['counts']['matched']}/{result['package_acceptance']['counts']['cases']} CLI cases matched; inputs unchanged: {all(row['inputs_unchanged'] for row in result['package_acceptance']['cases'])}.",
        ]
    )
    if any(mismatches.values()):
        lines.extend(f"Mismatched cases ({name}): {', '.join(ids)}" for name, ids in mismatches.items() if ids)
    else:
        lines.extend(
            [
                "Config equality/difference is not merit; stale evidence is unusable; ETHOS impact stays unresolved; neighbor stays current.",
                "Scores are bounded 0/1/2; the other eight criteria remain null; no 3 without independent corroboration.",
                "Wrong scorer: weak-route defect, not demonstrated exploitation.",
                "Retained-credit and coverage consumers use the same selected records; unavailable identities and unknown/wrong routes cannot succeed.",
                "Retained approvals are source-checked; equal config with mismatched run/task/digest remains unbound, not accepted.",
            ]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()
    result = run_panel()
    print(summary(result) if args.format == "text" else json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)
