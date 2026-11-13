# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run paired synthetic refund controls and check the identity of selected sources."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))

from eval_author_shared.task_evidence import loads  # noqa: E402
from rubric_identity import validate_identity_selection  # noqa: E402


def encoded(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def select_inputs(root, paths):
    root = root.resolve(strict=True)
    dispatch = {
        "selector": "reviewed",
        "routes": {"reviewed": "scorer"},
        "dependencies": {**{key: [] for key in paths}, "scorer": [key for key in paths if key != "scorer"]},
    }
    with (root / "dispatch.json").open("x") as stream:
        stream.write(encoded(dispatch))
    records = []
    for ident, relative in {**paths, "dispatch": "dispatch.json"}.items():
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        records.append(
            {
                "id": ident,
                "role": "scorer" if ident == "scorer" else "input",
                "path": relative,
                "digest": digest,
                "revision": digest,
                "access": "available",
                "selection_origin": "explicit",
                "source": "synthetic:refund-control-panel",
                "evidence": [{"path": relative, "digest": digest}],
            }
        )
    return {
        "artifacts": records,
        "route": {
            "entrypoint": "dispatch",
            "expected_scorer": "scorer",
            "dependencies": dispatch["dependencies"],
        },
    }


def measure_identity(root, selection):
    with tempfile.TemporaryDirectory(prefix="scorer-identity-") as temporary:
        private = Path(temporary)
        path = private / "selection.json"
        path.write_text(encoded(selection))
        env = {"PATH": os.defpath, "HOME": str(private), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "skills/eval-author-rubric/scripts/measure.py"),
                "--root",
                str(root),
                "--selection",
                str(path),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise ValueError("identity measurement failed")
        return loads(result.stdout)


FIXTURES = ROOT / "evals/scorer-controls"


def source_bytes(path, *, root=None):
    sys.path.insert(0, str(ROOT / "skills/eval-author/scripts"))
    from rubric_identity import read_selected

    relative = path.name if root is None else path.relative_to(root).as_posix()
    root = path.parent.resolve(strict=True) if root is None else root
    data, reason = read_selected(root, relative)
    if reason or data is None:
        raise ValueError("selected input unavailable")
    return data


def prepare(output):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    inputs = output / "inputs"
    inputs.mkdir(mode=0o700)
    paths = {
        "contract": "contract.json",
        "controls": "cases.json",
        "scorer": "refund_scorer.py",
        "applicability": "applicability.json",
    }
    for relative in paths.values():
        (inputs / relative).write_bytes(source_bytes(FIXTURES / relative))
    (inputs / "runtime.json").write_text(
        encoded(
            {
                "execution": "fixture-only",
                "python": platform.python_version(),
                "runner_sha256": hashlib.sha256(source_bytes(Path(__file__))).hexdigest(),
            }
        )
    )
    paths["runtime"] = "runtime.json"
    selection = select_inputs(inputs, paths)
    (output / "selection.json").write_text(encoded(selection))
    return selection


def run_panel(output, selection):
    validate_identity_selection(selection)
    required = {
        "contract": "contract.json",
        "controls": "cases.json",
        "scorer": "refund_scorer.py",
        "runtime": "runtime.json",
        "dispatch": "dispatch.json",
        "applicability": "applicability.json",
    }
    records = selection["artifacts"]
    if (
        len(records) != len(required)
        or any(
            not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in ("id", "path"))
            for record in records
        )
        or {r["id"]: r["path"] for r in records} != required
    ):
        raise ValueError("incomplete selection")
    inputs = output.resolve(strict=True) / "inputs"
    if inputs.is_symlink():
        raise ValueError("selected input directory is a symlink")
    identity = measure_identity(inputs, selection)
    measured = identity["measurements"]
    if measured["identity"]["status"] != "pass" or measured["route"]["bound_dependency"] is not True:
        raise ValueError("selected input identity failed")
    selected_bytes = {}
    for record in records:
        data = source_bytes(inputs / record["path"], root=inputs)
        if hashlib.sha256(data).hexdigest() != record["digest"]:
            raise ValueError("selected input digest mismatch")
        selected_bytes[record["id"]] = data
    # Treat retained reports as data. Never execute Python from them.
    trusted_scorer = source_bytes(FIXTURES / "refund_scorer.py")
    if selected_bytes["scorer"] != trusted_scorer:
        raise ValueError("retained scorer differs from selected implementation")
    contract = loads(selected_bytes["contract"])
    cases_document = loads(selected_bytes["controls"])
    # Bind schema, case IDs, roles and expectations to the current fixed panel.
    for ident in ("contract", "controls", "applicability"):
        if selected_bytes[ident] != source_bytes(FIXTURES / required[ident]):
            raise ValueError("retained input differs from fixed panel")
    runtime = loads(selected_bytes["runtime"])
    if runtime != {
        "execution": "fixture-only",
        "python": platform.python_version(),
        "runner_sha256": hashlib.sha256(source_bytes(Path(__file__))).hexdigest(),
    }:
        raise ValueError("selected runtime changed")
    scorer = ModuleType("refund_panel_scorer")
    exec(compile(trusted_scorer, str(FIXTURES / "refund_scorer.py"), "exec"), scorer.__dict__)
    cases = cases_document["cases"]
    if not isinstance(cases, list) or not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("invalid control collection")
    rows = []
    for case in cases:
        selected_contract = scorer.contract_for_case(contract, case)
        actual = {
            arm: scorer.evaluate(selected_contract, case["candidate"], grader=arm) for arm in ("baseline", "reviewed")
        }
        if set(case["expected"]) != set(actual):
            raise ValueError("missing arm expectations")
        rows.append(
            {
                "id": case["id"],
                "role": case["role"],
                "expected": case["expected"],
                "observed": actual,
                "matched": encoded(actual) == encoded(case["expected"]),
                "contract_digest": hashlib.sha256(encoded(selected_contract).encode()).hexdigest(),
                "candidate_digest": hashlib.sha256(encoded(case["candidate"]).encode()).hexdigest(),
            }
        )
    final_identity = measure_identity(inputs, selection)
    if encoded(identity) != encoded(final_identity):
        raise ValueError("inputs changed during scoring")
    return {
        "schema": "refund-scorer-panel-v1",
        "status": "passed" if all(row["matched"] for row in rows) else "failed",
        "execution": "fixture-only",
        "native_execution": False,
        "task_qualification": "NOT_ASSESSED",
        "authority_authenticated": False,
        "automatic_rubric_scoring": False,
        "causal_rubric_benefit": False,
        "control_count": len(rows),
        "matched_count": sum(1 for row in rows if row["matched"]),
        "selection": selection,
        "identity": identity,
        "cases": rows,
    }


def rubric_selection(selection, arm, mode="fixture"):
    selected = deepcopy(selection)
    for record in selected["artifacts"]:
        record["path"] = "inputs/" + record["path"]
        for evidence in record["evidence"]:
            evidence["path"] = "inputs/" + evidence["path"]
    selected["scorer_controls"] = {"evidence_root": ".", "mode": mode, "arm": arm}
    return selected


def rubric_demo(output, selection, *, verify=False, mode="fixture"):
    if not verify:
        for arm in ("baseline", "reviewed"):
            for name in (f"rubric-selection-{arm}.json", f"rubric-{arm}.json"):
                if os.path.lexists(output / name):
                    raise ValueError("retained rubric output already exists")
    reports = {}
    for arm in ("baseline", "reviewed"):
        selected = rubric_selection(selection, arm, mode)
        report = measure_identity(output, selected)
        for name, value in ((f"rubric-selection-{arm}.json", selected), (f"rubric-{arm}.json", report)):
            path = output / name
            if verify:
                if encoded(loads(source_bytes(path, root=output))) != encoded(value):
                    raise ValueError("retained rubric report disagrees with replay")
            else:
                with path.open("x") as stream:
                    stream.write(encoded(value))
        reports[arm] = report
    observed = {arm: report["items"]["AC-02"]["score"] for arm, report in reports.items()}
    expected = {"baseline": 0, "reviewed": 1}
    return {"criterion": "AC-02", "expected": expected, "observed": observed, "matched": observed == expected}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify-existing", type=Path)
    mode.add_argument(
        "--native-evidence",
        type=Path,
        help="Measure both rubric arms from retained native evidence; implies --rubric and never launches native work",
    )
    parser.add_argument(
        "--evidence-mode",
        choices=("fixture", "native"),
        default=None,
        help="Evidence mode for --verify-existing (default: fixture); native implies --rubric",
    )
    parser.add_argument("--rubric", action="store_true", help="Measure canonical AC-02 before/after through measure.py")
    args = parser.parse_args(argv)
    if args.evidence_mode is not None and args.verify_existing is None:
        parser.error("--evidence-mode requires --verify-existing")
    try:
        output = (args.output or args.verify_existing or args.native_evidence).absolute()
        native = args.native_evidence is not None or args.evidence_mode == "native"
        if native:
            selection = loads(source_bytes(output / "selection.json", root=output))
            comparison = rubric_demo(output, selection, verify=bool(args.verify_existing), mode="native")
            report = {
                "schema": "refund-scorer-panel-v1",
                "status": "passed" if comparison["matched"] else "failed",
                "execution": "retained-native",
                "rubric_comparison": comparison,
            }
            print(encoded(report), end="")
            return 0 if report["status"] == "passed" else 1
        if args.output:
            selection = prepare(output)
            report = run_panel(output, selection)
            (output / "panel.json").write_text(encoded(report))
        else:
            selection = loads(source_bytes(output / "selection.json"))
            report = run_panel(output, selection)
            if encoded(report) != encoded(loads(source_bytes(output / "panel.json"))):
                raise ValueError("retained report disagrees with replay")
        if args.rubric:
            report["rubric_comparison"] = rubric_demo(output, selection, verify=bool(args.verify_existing))
        print(encoded(report), end="")
        return 0 if report["status"] == "passed" else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            encoded({"schema": "refund-scorer-panel-v1", "status": "invalid", "reason": "input_or_execution_invalid"}),
            end="",
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
