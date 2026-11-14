# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline integrity demo: synthetic native results, real receipt producer and rubric CLI.

Run with the repository development environment (Harbor and Compass installed).
No model, Docker, network, task verifier, or agent is executed. Python subprocesses
write explicitly synthetic native results; the production recorder validates them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/eval-author/scripts"
RUBRIC = ROOT / "skills/eval-author-rubric/scripts/measure.py"
sys.path.insert(0, str(SCRIPTS))
from eval_author_shared import task_evidence  # noqa: E402

BOUNDARY = (
    "Synthetic native results. Real receipt recording, integrity checks, and production scoring. "
    "No model or Docker execution. Scores describe selected integrity checks, not agent quality, "
    "intent, context rot, or workflow completion. No aggregate score."
)


def encoded(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded(value))


def prepare(root):
    task = root / "task"
    task.mkdir()
    (task / "environment").mkdir()
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Synthetic fixture only: return the number one.\n")
    (task / "task.toml").write_text('version = "1.0"\n')
    (task / "tests/test.sh").write_text("#!/bin/sh\n# Fixture only. Never executed by this demo.\nexit 99\n")
    contract = {
        "task_id": "task",
        "provider": "harbor",
        "native_run_id": "/id",
        "alternative_not_applicable": "One exact synthetic output.",
        "side_effect_not_applicable": "No side effects are modeled.",
        "cases": {
            kind: {
                "kind": kind,
                "requirement": "Accept one; reject empty and incorrect synthetic outputs.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [{"pointer": "/verifier_result/rewards/reward", "equals": reward}],
            }
            for kind, reward in (("reference", 1), ("incorrect", 0), ("empty", 0), ("agent", 1))
        },
    }
    write_json(root / "contract.json", contract)
    manifest = root / "manifest.json"
    task_evidence.prepare(task, root / "contract.json", manifest)
    return manifest


def record(root, manifest, name, reward, *, corrupt=False, case="reference"):
    """Execute a synthetic writer through the unmodified production recorder."""
    result, receipt = root / f"{name}-native.json", root / f"{name}-receipt.json"
    native = {
        "synthetic": True,
        "id": name,
        "task_name": "task",
        "trial_name": name,
        "task_checksum": task_evidence.read(manifest)["harbor_task_checksum"],
        "exception_info": None,
        "config": {"agent": {"name": "wrong-answer" if case == "incorrect" else "oracle"}},
        "verifier_result": {"rewards": {"reward": reward}},
    }
    fixture = root / f"{name}-fixture.json"
    write_json(fixture, native)
    command = [
        sys.executable,
        "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())",
        str(fixture),
        str(result),
    ]
    produced = task_evidence.run(manifest, case, name, receipt, result, None, command, timeout=30)
    write_json(root / f"{name}-receipt.original.json", produced)
    if corrupt:
        # Deliberately falsify the claim after production recording; retain the original.
        produced["status"] = "passed"
        receipt.write_text(encoded(produced))
    return receipt, result


def selection(root, manifest, pairs):
    records = []
    native = {"manifest_ids": [], "receipt_ids": [], "result_ids": [], "trace_ids": []}
    for role, paths in (
        ("manifest", [manifest]),
        ("receipt", [pair[0] for pair in pairs]),
        ("result", [pair[1] for pair in pairs]),
    ):
        for path in paths:
            ident = path.stem
            relative = path.relative_to(root).as_posix()
            digest = sha(path)
            records.append(
                {
                    "id": ident,
                    "role": role,
                    "path": relative,
                    "digest": digest,
                    "revision": None,
                    "access": "available",
                    "selection_origin": "explicit",
                    "source": "synthetic native fixture; real task_evidence producer",
                    "evidence": [{"path": relative, "digest": digest}],
                }
            )
            native[role + "_ids"].append(ident)
    path = root / "selection.json"
    write_json(path, {"artifacts": records, "native": native})
    return path


def command(root, name, argv):
    completed = subprocess.run(
        [str(arg) for arg in argv],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    (root / f"{name}.stdout.json").write_text(completed.stdout)
    (root / f"{name}.stderr.txt").write_text(completed.stderr)
    write_json(root / f"{name}.command.json", {"argv": [str(arg) for arg in argv], "returncode": completed.returncode})
    return completed, json.loads(completed.stdout)


def receipt_score(report):
    # Read the actual rubric output; never substitute a locally recomputed score.
    check = next(row for row in report["items"]["CREATE-07"]["check_results"] if row["id"] == "CREATE-07-C01")
    groups = check.get("group_scores", [])
    scored = next((row for row in groups if row["id"] == "receipt_consistency"), {})
    required = next((row for row in groups if row["id"] == "required_verification"), {})
    verification = report["measurements"]["native"]["required_verification"]
    return {
        "required_group": {
            key: required.get(key) for key in ("score", "outcome", "passed", "failed", "unknown", "excluded", "scope")
        },
        "required_verification": verification,
        "required_freshness": sorted(row["freshness"] for row in verification["cases"]),
        "group_scopes": [row.get("scope") for row in groups],
        **{key: scored.get(key) for key in ("passed", "failed", "unknown", "excluded", "score", "outcome")},
        "score_source": "CREATE-07-C01.group_scores.receipt_consistency",
        "group_scores": groups,
    }


def replay(root, name, argv, project):
    """Read the same immutable sources twice; retain both byte-exact outputs."""
    inputs = {path: sha(path) for path in root.rglob("*") if path.is_file()}
    completed, report = command(root, name, argv)
    repeated, again = command(root, name + "-replay", argv)
    observed = project(report)
    return (
        completed,
        report,
        observed,
        {
            "same_source_replay": completed.stdout == repeated.stdout
            and completed.stderr == repeated.stderr
            and completed.returncode == repeated.returncode
            and observed == project(again) == project(report),
            "inputs_unchanged": all(sha(path) == digest for path, digest in inputs.items()),
            "input_sha256": {path.relative_to(root).as_posix(): digest for path, digest in inputs.items()},
            "output_sha256": sha(root / f"{name}.stdout.json"),
            "replay_output_sha256": sha(root / f"{name}-replay.stdout.json"),
        },
    )


def anchor(root, passed, expected_score):
    root.mkdir(mode=0o700)
    manifest = prepare(root)
    pairs = [
        record(root, manifest, f"check-{index}", int(index < passed), corrupt=index >= passed) for index in range(4)
    ]
    selected = selection(root, manifest, pairs)
    completed, report, observed, determinism = replay(
        root,
        "rubric",
        [sys.executable, RUBRIC, "--root", root, "--selection", selected],
        receipt_score,
    )
    expected = {
        "passed": passed,
        "failed": 4 - passed,
        "unknown": 0,
        "excluded": 0,
        "score": expected_score,
        "required_group": {
            "score": None,
            "outcome": "unassessed",
            "passed": 0,
            "failed": 0,
            "unknown": 3,
            "excluded": 0,
            "scope": "verification_integrity",
        },
        "required_freshness": ["unknown"] * 3,
        "group_scopes": ["verification_integrity"] * 2,
    }
    return {
        "id": root.name,
        "expected": expected,
        "observed": observed,
        "determinism": determinism,
        "matches_expectations": all(observed[key] == value for key, value in expected.items())
        and completed.returncode == 1
        and not completed.stderr
        and determinism["same_source_replay"]
        and determinism["inputs_unchanged"],
        "evidence": {"report": f"{root.name}/rubric.stdout.json", "selection": f"{root.name}/selection.json"},
        "next_action": "Correct the false passed claims and rerun affected controls."
        if passed < 4
        else "Keep these consistent receipts. Run the remaining required controls before claiming completion.",
    }


def acceptance_score(report):
    from verification_scoring import score_check_group

    integrity = report["acceptance_integrity"]
    status = integrity["status"]
    passed, failed, unknown = (
        int(status == "consistent"),
        int(status == "violation"),
        int(status in {"unknown", "stale"}),
    )
    scored = score_check_group(passed=passed, failed=failed, unknown=unknown)
    return {
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "excluded": 0,
        "score": scored["score"],
        "outcome": scored["outcome"],
        "production_scoring": scored,
        "score_source": "production helper projection; not a rubric group score",
        "acceptance_status": status,
        "finding_codes": sorted(row["code"] for row in integrity["findings"]),
        "required_states": integrity["required_verification"]["states"],
        "required_verification": integrity["required_verification"],
    }


def acceptance_case(root, mode):
    root.mkdir(mode=0o700)
    manifest = prepare(root)
    pairs = (
        []
        if mode == "missing"
        else [
            record(root, manifest, case, reward, case=case)
            for case, reward in (
                ("reference", 0 if mode == "acceptance-contradiction" else 1),
                ("incorrect", 0),
                ("empty", 0),
            )
        ]
    )
    selected = selection(root, manifest, pairs)
    if mode == "stale":
        (root / "task/instruction.md").write_text("Synthetic task revision two: return two.\n")
    claim = root / "acceptance.json"
    write_json(
        claim,
        {
            "schema": "nemo.eval_author.task_gap_verification.v2",
            "task_validation": "passed",
            "evidence": [str(pair[0]) for pair in pairs],
        },
    )
    script = ROOT / "skills/eval-author-task-create/scripts/task_pipeline.py"
    completed, report, observed, determinism = replay(
        root,
        "acceptance",
        [sys.executable, script, "check-acceptance", "--report", claim, "--integrity"],
        acceptance_score,
    )
    default, default_report, _, default_determinism = replay(
        root,
        "default-acceptance",
        [sys.executable, script, "check-acceptance", "--report", claim],
        lambda report: report,
    )
    expected_default = {"valid": False, "error": "invalid saved acceptance: 'before'"}
    expected_status, expected_score = {
        "missing": ("unknown", None),
        "stale": ("stale", None),
        "acceptance-contradiction": ("violation", 0),
        "acceptance-consistent-claim": ("consistent", 3),
    }[mode]
    expected = {"acceptance_status": expected_status, "score": expected_score}
    return {
        "id": mode,
        "boundary": "Synthetic partial claim fixture; not a saved acceptance envelope or workflow recovery.",
        "workflow_acceptance": False,
        "default_acceptance": {"returncode": default.returncode, "report": default_report},
        "default_determinism": default_determinism,
        "expected": expected,
        "observed": observed,
        "determinism": determinism,
        "matches_expectations": all(observed[key] == value for key, value in expected.items())
        and default.returncode == 1
        and default_report == expected_default
        and default_determinism["same_source_replay"]
        and default_determinism["inputs_unchanged"]
        and not default.stderr
        and completed.returncode == int(expected_status != "consistent")
        and not completed.stderr
        and determinism["same_source_replay"]
        and determinism["inputs_unchanged"],
        "evidence": {
            "report": f"{mode}/acceptance.stdout.json",
            "selection": f"{mode}/{selected.name}",
            "claim": f"{mode}/acceptance.json",
            "default_report": f"{mode}/default-acceptance.stdout.json",
        },
        "next_action": {
            "missing": "Capture the required controls. Missing evidence does not prove verification was skipped.",
            "stale": "Prepare the edited task revision and rerun controls. Do not reuse stale evidence.",
            "acceptance-contradiction": "Withdraw the passed claim and fix the failing reference control.",
            "acceptance-consistent-claim": "Retain this consistent partial claim. Complete the real saved-envelope workflow before claiming acceptance; earlier failures remain.",
        }[mode],
    }


def retry_case(root):
    root.mkdir(mode=0o700)
    source = root / "source.atif.json"
    steps = [{"step_id": 1, "source": "user", "message": "Synthetic repeated read."}]
    for number, error in ((2, True), (3, True), (4, False)):
        call_id = f"call-{number}"
        steps.append(
            {
                "step_id": number,
                "source": "agent",
                "message": "Synthetic action.",
                "tool_calls": [{"tool_call_id": call_id, "function_name": "read", "arguments": {"path": "file"}}],
                "observation": {"results": [{"source_call_id": call_id, "content": "synthetic", "is_error": error}]},
            }
        )
    write_json(
        source,
        {
            "schema_version": "ATIF-v1.7",
            "session_id": "synthetic",
            "trajectory_id": "trajectory",
            "agent": {
                "name": "synthetic",
                "version": "1",
                "tool_definitions": [{"name": "read", "inputSchema": {"type": "object"}}],
            },
            "steps": steps,
        },
    )
    from eval_author_shared.safe_trace import canonical_bytes, load_trace, scrub

    safe, _ = scrub(load_trace(source, "atif"))
    safe_path = root / "safe-trace.json"
    safe_path.write_bytes(canonical_bytes(safe))
    code = (
        "import sys,json,hashlib; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from eval_author_shared.safe_trace import validate_payload; "
        "from eval_author_shared.retry_audit import audit_retries; "
        "p=Path(sys.argv[2]); "
        "print(json.dumps(audit_retries(validate_payload(json.loads(p.read_bytes()),label='retained safe trace'), "
        "safe_trace_sha256=hashlib.sha256(p.read_bytes()).hexdigest()),sort_keys=True))"
    )

    def project(report):
        return {
            "review_candidates": report["counts"]["review_candidates"],
            "confirmed_violations": report["counts"]["confirmed_violations"],
            "candidate_rate": report["candidate_rate"],
            "score": None,
            "later_recovery": any(row.get("later_success_span_id") for row in report["findings"]),
        }

    completed, report, observed, determinism = replay(
        root, "retry", [sys.executable, "-c", code, SCRIPTS, safe_path], project
    )
    expected = {
        "review_candidates": 1,
        "confirmed_violations": 0,
        "candidate_rate": 1.0,
        "score": None,
        "later_recovery": True,
    }
    return {
        **observed,
        "expected": expected,
        "matches_expectations": observed == expected
        and completed.returncode == 0
        and not completed.stderr
        and determinism["same_source_replay"]
        and determinism["inputs_unchanged"],
        "determinism": determinism,
        "evidence": "retry/retry.stdout.json",
        "next_action": "Review the operation state before labeling a violation. Later recovery remains visible.",
    }


def render(panel):
    lines = [
        f"# Verification integrity demo: {panel['status']}",
        "",
        BOUNDARY,
        "",
        "| Case | Expected score | Observed score | Passed / failed / unknown | Match |",
        "|---|---|---|---|---|",
    ]
    for row in panel["cases"]:
        value = row["observed"]
        lines.append(
            f"| {row['id']} | {row['expected']['score'] if row['expected']['score'] is not None else 'Unassessed'} | "
            f"{value['score'] if value['score'] is not None else 'Unassessed'} | "
            f"{value['passed']} / {value['failed']} / {value['unknown']} | {row['matches_expectations']} |"
        )
    for row in panel["cases"]:
        lines.extend(
            [
                "",
                f"## {row['id']}",
                f"Score source: {row['observed']['score_source']}",
                *(
                    [
                        "Required verification: "
                        + (
                            "Unassessed"
                            if row["observed"]["required_group"]["score"] is None
                            else str(row["observed"]["required_group"]["score"])
                        )
                        + f" ({row['observed']['required_group']['unknown']} unknown; "
                        + f"freshness {', '.join(row['observed']['required_freshness'])})."
                    ]
                    if "required_group" in row["observed"]
                    else [
                        row["boundary"],
                        "Ordinary check-acceptance: rejected (missing before); no workflow acceptance.",
                        f"Evidence: [default rejection]({row['evidence']['default_report']})",
                    ]
                ),
                f"Next action: {row['next_action']}",
                f"Evidence: [report]({row['evidence']['report']}), [selection]({row['evidence']['selection']})",
            ]
        )
    retry = panel["retry"]
    lines.extend(
        [
            "",
            "## Retry review (not scored)",
            f"Expected / observed review candidates: 1 / {retry['review_candidates']}; "
            f"confirmed violations: 0 / {retry['confirmed_violations']}; later recovery: {retry['later_recovery']}.",
            f"Candidate rate: {retry['candidate_rate']} (descriptive only; never used for scoring).",
            f"Next action: {retry['next_action']}",
            f"Evidence: [retry report]({retry['evidence']})",
            "",
            "## Determinism",
            "Each calculation and command was replayed on unchanged sources.",
            f"Normalized fresh-directory comparison: {panel.get('determinism', {}).get('normalized_fresh_run_equal', 'not checked')}.",
            "Normalization compares the named measurement projection (counts, findings, outcomes, scores), "
            "including actual rubric groups and required-verification freshness, "
            "not path-bound receipt or whole-report bytes. Raw hashes deliberately remain path-specific.",
            "Evidence: [output hashes](hashes.json), [normalized results](normalized-result.json), "
            "[fresh-directory results](fresh-replay/normalized-result.json).",
            "",
            "The rubric CLI exits 1 because full workflow requirements remain unassessed. "
            "This demo does not convert that status into workflow success.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_panel(root):
    rows = [anchor(root / f"anchor-{score}", passed, score) for passed, score in ((0, 0), (1, 1), (3, 2), (4, 3))]
    rows.extend(
        acceptance_case(root / mode, mode)
        for mode in (
            "missing",
            "stale",
            "acceptance-contradiction",
            "acceptance-consistent-claim",
        )
    )
    retry = retry_case(root / "retry")
    return {
        "schema": "verification-integrity-demo-v1",
        "status": "passed"
        if all(row["matches_expectations"] for row in rows) and retry["matches_expectations"]
        else "failed",
        "native_results": "synthetic",
        "model_execution": False,
        "docker_execution": False,
        "aggregate_score": None,
        "boundary": BOUNDARY,
        "cases": rows,
        "retry": retry,
    }


def normalized(panel: dict, root: Path | None = None) -> dict:
    """Explicit semantic comparison surface; raw provenance hashes are NOT erased."""
    projection: dict = {
        "schema": panel["schema"],
        "cases": [
            {
                key: row[key]
                for key in (
                    "id",
                    "expected",
                    "observed",
                    "matches_expectations",
                    "next_action",
                    "boundary",
                    "workflow_acceptance",
                    "default_acceptance",
                )
                if key in row
            }
            for row in panel["cases"]
        ],
        "retry": {
            key: panel["retry"][key]
            for key in (
                "expected",
                "review_candidates",
                "confirmed_violations",
                "candidate_rate",
                "score",
                "later_recovery",
                "matches_expectations",
                "next_action",
            )
        },
    }
    if root is None:
        return projection
    root = root.absolute()
    prefixes = [root.as_posix() + "/"]
    if root.is_relative_to(ROOT):
        prefixes.append(root.relative_to(ROOT).as_posix() + "/")
    projection = deepcopy(projection)
    for case in projection["cases"]:
        for row in case["observed"].get("required_verification", {}).get("cases", []):
            manifest = row.get("manifest")
            if not isinstance(manifest, str):
                continue
            for prefix in prefixes:
                if manifest.startswith(prefix):
                    row["manifest"] = manifest[len(prefix) :]
                    break
    return projection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--run-dir", type=Path, required=True, help="new private output directory; parent must exist")
    args = parser.parse_args(argv)
    try:
        root = args.run_dir.absolute()
        if ".." in root.parts or any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("unsafe run directory")
        root.mkdir(mode=0o700)
        panel = run_panel(root)
        fresh = root / "fresh-replay"
        fresh.mkdir(mode=0o700)
        repeated = run_panel(fresh)
        write_json(fresh / "panel.json", repeated)
        write_json(root / "normalized-result.json", normalized(panel, root))
        write_json(fresh / "normalized-result.json", normalized(repeated, fresh))
        equal = (root / "normalized-result.json").read_bytes() == (fresh / "normalized-result.json").read_bytes()
        panel["determinism"] = {
            "normalized_fresh_run_equal": equal,
            "scope": "measurement projection; not path-bound raw report bytes",
        }
        if not equal or repeated["status"] != "passed":
            panel["status"] = "failed"
        write_json(root / "panel.json", panel)
        (root / "report.md").write_text(render(panel))
        write_json(
            root / "hashes.json",
            {path.relative_to(root).as_posix(): sha(path) for path in sorted(root.rglob("*")) if path.is_file()},
        )
    except (OSError, ValueError, ImportError, subprocess.TimeoutExpired) as exc:
        parser.error(f"{exc}; use a new run directory and the repository development environment")
    print(render(panel) if args.format == "text" else encoded(panel), end="")
    return 0 if panel["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
