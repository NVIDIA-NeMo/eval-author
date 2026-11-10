# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline W02 byte/reference demo. No native execution or semantic assessment."""

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "skills/eval-author-rubric/scripts/measure.py"
EXPECTATIONS = ROOT / "evals/rubric-measurements/w02-provenance.json"
CANONICAL = ROOT / "skills/eval-author-rubric/references/artifact-configuration-provenance.json"


def encoded(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded(value))


def prepare_reconstruction(root, prefix):
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    from eval_author_shared import trace_workspace

    workspace = root / ".eval-author/.working/trace-environments"
    initialized = trace_workspace._init(argparse.Namespace(root=workspace, task_id=prefix))
    task_dir = Path(initialized["task_dir"])
    source = ROOT / "evals/rubric-measurements/w02-reconstruction.trace.jsonl"
    content = json.loads(source.read_bytes())["root_spans"][0]["turns"][0]["content"]
    trace_workspace._prepare(
        argparse.Namespace(
            task_dir=task_dir,
            source=source,
            source_kind="trace",
            row=None,
            trace_id=None,
        )
    )
    artifact = task_dir / "private/ground-truth/file.json"
    artifact.parent.mkdir(mode=0o700)
    artifact.write_bytes(content.encode("utf-8"))
    artifact.chmod(0o600)
    write_json(
        task_dir / "candidate.json",
        {
            "schema": trace_workspace.CANDIDATE_SCHEMA,
            "status": "no_candidate",
            "decision_basis": "safe_trace_only",
            "instruction": None,
            "requirements": [],
            "verification_mode": None,
            "evidence_turns": ["trajectory/turn/1"],
            "uncertainties": ["Synthetic comparison only."],
            "reason_codes": ["missing_outcome"],
            "software_requirements": [],
            "ground_truth": {
                "availability": "available",
                "use": "comparison_only",
                "absence_reason": None,
                "artifacts": [
                    {
                        "kind": "fixture",
                        "path": "private/ground-truth/file.json",
                        "sha256": "sha256:" + sha(artifact),
                        "provenance": {
                            "kind": "trace_turn",
                            "turn_ids": ["trajectory/turn/1"],
                            "uri": None,
                            "revision": None,
                            "source_id": None,
                        },
                        "notes": "Synthetic exact retained string.",
                    }
                ],
            },
        },
    )
    trace_workspace._finalize(
        argparse.Namespace(
            task_dir=task_dir,
            status="no_candidate",
            reason=[],
            worked_well=[],
            did_not_work=[],
        )
    )
    return task_dir


def build_selection(root, mutation):
    records = {}

    def select(ident, role, value, revision="v1", relative_path=None):
        path = root / (relative_path or f"{ident}-{revision}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if value is not None:
            write_json(path, value)
        digest = sha(path)
        records[ident] = {
            "id": ident,
            "role": role,
            "path": path.relative_to(root).as_posix(),
            "digest": digest,
            "revision": revision,
            "access": "available",
            "selection_origin": "explicit",
            "source": "handcrafted:w02-provenance-selection",
            "evidence": [{"path": path.relative_to(root).as_posix(), "digest": digest}],
        }
        return records[ident]

    config: dict[str, Any] = {
        "agent": "synthetic-record-only",
        "run": "generation-session-17",
        "task": "ledger-balance",
        "tool_permissions": {"network": False},
        "provider": "handcrafted-no-provider",
        "runtime_pins": {"generator": "generator-r4"},
        "environment": {"execution": "not-run"},
    }
    select("generator-config", "configuration", config, "r4")
    select("generation-session", "run", {"run_id": config["run"], "executed": False})
    select("task-spec", "task", {"task_id": config["task"], "synthetic": True})
    select("seed", "input", {"instruction": "Balance the synthetic ledger", "seed": 17})
    select("candidate-kept", "candidate", {"rows": ["credit", "debit"], "synthetic": True})
    select("candidate-rejected", "candidate", {"rows": [], "synthetic": True})
    select("candidate-failed", "candidate", {"attempt": "supplied-failure", "synthetic": True})
    select("validation-note", "evidence", {"kind": "handcrafted validation note", "executed": False})
    select("selected-set", "manifest", {"members": ["candidate-kept"], "policy": "explicit-handcrafted-selection"})
    select("dataset", "dataset", {"examples": ["ledger-a", "ledger-b"], "synthetic": True})
    select("scorer-spec", "scorer", {"rule": "exact-ledger-balance", "executed": False})
    select("runtime-spec", "configuration", {"runtime": "synthetic-v1", "launched": False})
    select("compatibility-note", "evidence", {"compatibility": "unknown", "reason": "not-evaluated"})
    select("neighbor-input", "dependency", {"instruction": "List ledger labels", "synthetic": True})
    select("neighbor-record", "evidence", {"kind": "separate retained note", "executed": False})
    generation = {
        "synthetic": True,
        "executed": False,
        "supplied_candidates": [
            {"id": "candidate-kept", "decision": "selected", "reason": "handcrafted positive control"},
            {"id": "candidate-rejected", "decision": "rejected", "reason": "handcrafted empty-row defect"},
            {"id": "candidate-failed", "decision": "failed", "reason": "handcrafted incomplete attempt"},
        ],
        "population_completeness": "not-established",
        "nodes": [
            {"id": ident, "role": "dependency"}
            for ident in (
                "generation-record",
                "seed",
                "generator-config",
                "candidate-kept",
                "candidate-rejected",
                "candidate-failed",
                "validation-note",
            )
        ],
        "links": [
            {"source": "generation-record", "target": ident, "relation": "dependency"}
            for ident in (
                "seed",
                "generator-config",
                "candidate-kept",
                "candidate-rejected",
                "candidate-failed",
                "validation-note",
            )
        ],
    }
    if mutation == "validation-dangling":
        generation["links"][-1]["target"] = "validation-absent"
    generation["nodes"].extend({"id": ident, "role": "dependency"} for ident in ("neighbor-record", "neighbor-input"))
    generation["links"].append({"source": "neighbor-record", "target": "neighbor-input", "relation": "dependency"})
    select("generation-record", "evidence", generation)
    select("subset-record", "evidence", {"method": "handcrafted subset record", "optimizer_run": False})
    select(
        "calibration-record",
        "evidence",
        {"method": "handcrafted calibration record", "model_fitted": False, "transfer": "unknown"},
    )
    freshness = []
    for ident, dependencies in (
        (
            "generation-record",
            ["seed", "generator-config", "candidate-kept", "candidate-rejected", "candidate-failed", "validation-note"],
        ),
        ("subset-record", ["selected-set"]),
        ("calibration-record", ["selected-set", "dataset", "scorer-spec", "runtime-spec", "compatibility-note"]),
        ("neighbor-record", ["neighbor-input"]),
    ):
        retained = {
            "evidence": {"id": ident, "digest": records[ident]["digest"]},
            "dependencies": [
                {
                    "id": dependency,
                    "digest": records[dependency]["digest"],
                    "revision": records[dependency]["revision"],
                    "impact": "unknown" if dependency == "compatibility-note" else "material",
                }
                for dependency in dependencies
            ],
        }
        select(ident + "-bindings", "manifest", {"retained": retained})
        freshness.append({"id": ident, "kind": "bindings", "source": ident + "-bindings", "pointer": "/retained"})
    observation = {
        "settings": copy.deepcopy(config),
        "run": config["run"],
        "task": config["task"],
        "configuration_digest": records["generator-config"]["digest"],
        "synthetic": True,
    }
    if mutation == "generator-configuration-revision-mismatch":
        revised = copy.deepcopy(config)
        revised["runtime_pins"]["generator"] = "generator-r5"
        select("generator-config", "configuration", revised, "r5")
    replacements = {
        "candidate-replaced": ("candidate-kept", "candidate", {"rows": ["credit", "credit"], "synthetic": True}),
        "selected-set-membership-changed": (
            "selected-set",
            "manifest",
            {"members": ["candidate-rejected"], "policy": "explicit-handcrafted-selection"},
        ),
        "calibration-dataset-changed": ("dataset", "dataset", {"examples": ["ledger-c"], "synthetic": True}),
        "calibration-scorer-changed": ("scorer-spec", "scorer", {"rule": "exact-ledger-balance-v2", "executed": False}),
        "calibration-runtime-changed": (
            "runtime-spec",
            "configuration",
            {"runtime": "synthetic-v2", "launched": False},
        ),
        "compatibility-unknown": (
            "compatibility-note",
            "evidence",
            {"compatibility": "unknown", "reason": "revised-but-not-evaluated"},
        ),
    }
    if mutation in replacements:
        select(*replacements[mutation], revision="v2")
    select("generator-observation", "configuration", observation)
    reconstruction: list[dict[str, Any]] = []
    if mutation.startswith("reconstruction-"):
        for prefix in ("initial", "neighbor"):
            task_dir = prepare_reconstruction(root, prefix)
            artifact, trace, candidate, source = [
                select(prefix + suffix, role, None, relative_path=(task_dir / name).relative_to(root).as_posix())
                for suffix, role, name in (
                    ("-file", "input", "private/ground-truth/file.json"),
                    ("-trace", "trace", "safe/trace.json"),
                    ("-candidate", "candidate", "candidate.json"),
                    ("-summary", "evidence", "summary.json"),
                )
            ]
            reconstruction.append(
                {
                    "artifact_id": artifact["id"],
                    "candidate": {"artifact_id": candidate["id"], "pointer": "/ground_truth/artifacts/0"},
                    "source": {"artifact_id": source["id"], "pointer": "/source"},
                    "trace_id": trace["id"],
                    "step": {"pointer": "/root_spans/0/turns/0", "step_id": "trajectory/turn/1"},
                    "content": {"pointer": "/content", "encoding": "utf-8"},
                    "declaration": "recovered",
                }
            )
        entry = reconstruction[0]
        if mutation == "reconstruction-wrong-step":
            entry["step"]["pointer"] = "/root_spans/0/turns/1"
        elif mutation == "reconstruction-source-bytes-changed":
            with (root / records["initial-trace"]["path"]).open("ab") as stream:
                stream.write(b"\n")
        elif mutation in ("reconstruction-partial", "reconstruction-generated"):
            entry["declaration"] = mutation.removeprefix("reconstruction-")
        elif mutation == "reconstruction-source-unavailable":
            records["initial-trace"]["access"] = "missing"
            (root / records["initial-trace"]["path"]).unlink()
    pointers = ["/agent", "/run", "/task", "/tool_permissions", "/provider", "/runtime_pins/generator", "/environment"]
    return {
        **({"reconstruction": reconstruction} if reconstruction else {}),
        "artifacts": list(records.values()),
        "nodes": generation["nodes"],
        "links": generation["links"],
        "freshness": freshness,
        "sourced_config": {
            "declared": {pointer: {"artifact_id": "generator-config", "pointer": pointer} for pointer in pointers},
            "observed": {
                pointer: {"artifact_id": "generator-observation", "pointer": "/settings" + pointer}
                for pointer in pointers
            },
            "binding": {
                "run": {"artifact_id": "generation-session", "pointer": "/run_id"},
                "task": {"artifact_id": "task-spec", "pointer": "/task_id"},
                "configuration_artifact_id": "generator-config",
                "observations": {
                    "generator-observation": {
                        "run": "/run",
                        "task": "/task",
                        "configuration_digest": "/configuration_digest",
                    }
                },
            },
        },
    }


def merge(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        result[key] = merge(result.get(key, {}), value) if isinstance(value, dict) else copy.deepcopy(value)
    return result


def observed_summary(report, exit_code):
    measurements = report["measurements"]
    result = {"cli_exit_code": exit_code}
    for name in ("identity", "links", "freshness"):
        result[name] = {key: measurements[name][key] for key in ("status", "diagnostics")}
    result["sourced_config"] = {
        key: measurements["sourced_config"][key] for key in ("status", "differences", "unknowns")
    }
    result["freshness"]["records"] = {
        row["id"]: {key: row[key] for key in ("status", "usable", "changed_dependencies")}
        for row in measurements["freshness"]["records"]
    }
    if "reconstruction" in measurements:
        measurement = measurements["reconstruction"]
        result["reconstruction"] = {
            "status": measurement["status"],
            "diagnostics": measurement["diagnostics"],
            "records": [
                {key: row[key] for key in ("status", "declaration", "source_proof", "semantic_proof")}
                for row in measurement["records"]
            ],
        }
    return result


def canonical_rows(report, definitions):
    items = report["items"]
    if set(items) != {item["id"] for item in definitions["items"]}:
        return False
    for definition in definitions["items"]:
        item = items[definition["id"]]
        checks = item["check_results"]
        if not set(definitions["result_template"]) <= set(item) or item["score"] is not None:
            return False
        if len(checks) != len(definition["checks"]) or {row["check_id"] for row in checks} != {
            row["id"] for row in definition["checks"]
        }:
            return False
    return report["anti_cheat_scores"] is None and report["preflight_success"] is False


def invoke_measure(root, selection_path):
    return subprocess.run(
        [sys.executable, str(CLI), "--root", str(root), "--selection", str(selection_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def run_case(root, case, defaults, definitions):
    root.mkdir(mode=0o700)
    selection = build_selection(root, case["id"])
    write_json(root / "selection.json", selection)
    baseline_path = root / "baseline-selection.json"
    write_json(baseline_path, {key: value for key, value in selection.items() if key != "reconstruction"})
    input_paths = sorted(path for path in root.rglob("*") if path.is_file())
    before = {path.relative_to(root).as_posix(): sha(path) for path in input_paths}
    process = invoke_measure(root, root / "selection.json")
    report = json.loads(process.stdout)
    baseline = invoke_measure(root, baseline_path)
    baseline_report = json.loads(baseline.stdout)
    rows_unchanged = report["items"] == baseline_report["items"]
    unchanged = before == {
        path.relative_to(root).as_posix(): sha(path) for path in sorted(root.rglob("*")) if path.is_file()
    }
    expected = merge(defaults, case["overrides"])
    observed = observed_summary(report, process.returncode)
    write_json(root / "report.json", report)
    return {
        "id": case["id"],
        "expected": expected,
        "observed": observed,
        "report": report,
        "cli_exit_code": process.returncode,
        "input_digests": before,
        "inputs_unchanged": unchanged,
        "canonical_rows_unchanged": rows_unchanged,
        "selection": f"{case['id']}/selection.json",
        "report_path": f"{case['id']}/report.json",
        "matches_expectations": observed == expected
        and unchanged
        and not process.stderr
        and not baseline.stderr
        and baseline.returncode == process.returncode
        and rows_unchanged
        and canonical_rows(report, definitions),
    }


def run_panel(root):
    expectations = json.loads(EXPECTATIONS.read_text())
    definitions = json.loads(CANONICAL.read_text())
    rows = [run_case(root / case["id"], case, expectations["defaults"], definitions) for case in expectations["cases"]]
    matched = sum(row["matches_expectations"] is True for row in rows)
    return {
        "schema": "w02-provenance-demo-v1",
        "status": "passed" if matched == len(rows) else "failed",
        "counts": {"cases": len(rows), "matched": matched},
        "expectations_digest": sha(EXPECTATIONS),
        "canonical_checks_per_case": sum(len(item["checks"]) for item in definitions["items"]),
        "boundary": expectations["boundary"],
        "native_execution": False,
        "semantic_assessment": False,
        "new_scores": False,
        "population_completeness": "not-established",
        "cases": rows,
    }


def summary(result):
    lines = [f"W02 provenance demo: {result['status']}", "Case | expected | observed | match"]
    for case in result["cases"]:

        def compact(value):
            states = [value[name]["status"] for name in ("identity", "links", "sourced_config", "freshness")]
            states.append(value.get("reconstruction", {}).get("status", "not-selected"))
            return "/".join(states)

        lines.append(
            f"{case['id']} | {compact(case['expected'])} | {compact(case['observed'])} | {case['matches_expectations']}"
        )
    lines.extend(
        [
            "Order: identity/links/configuration/freshness/reconstruction. CLI exit 1 retains unassessed workflow requirements.",
            f"Matched {result['counts']['matched']}/{result['counts']['cases']}; {result['canonical_checks_per_case']} canonical checks per report; inputs unchanged.",
            result["boundary"],
        ]
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--run-dir", type=Path, help="retain synthetic inputs and reports in a new directory")
    args = parser.parse_args(argv)
    try:
        if args.run_dir is None:
            with tempfile.TemporaryDirectory(prefix="w02-provenance-") as directory:
                result = run_panel(Path(directory).resolve())
        else:
            root = args.run_dir.absolute()
            if ".." in root.parts or any(path.is_symlink() for path in (root, *root.parents)):
                raise ValueError("unsafe run directory")
            root.mkdir(mode=0o700)
            result = run_panel(root)
            write_json(root / "panel.json", result)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        parser.error(
            "run directory must be new, with existing nonsymlink parents; demo inputs and CLI must be readable"
        )
    print(summary(result) if args.format == "text" else encoded(result), end="\n" if args.format == "text" else "")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
