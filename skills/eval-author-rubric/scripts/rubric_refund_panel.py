# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Native refund controls and exact AC-02 credit comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import rubric_native_collect as collector

SCHEMA = "eval-author.refund-attack-panel.v1"
SCRIPTS = Path(__file__).resolve().parent
EXAMPLE = SCRIPTS.parent / "examples/refund"
EXTRA = ("early-exit", "fake-output", "stale-output", "private-tamper", "inert-reference")
SELECTED = (
    "reference",
    "split-alternative",
    "permitted-zero-balance-inaction",
    "wrong-customer",
    "wrong-order",
    "wrong-amount",
    "wrong-destination",
    "missing-approval",
    "late-approval",
    "missing-ledger",
    "duplicate-ledger",
    "stale-carryover",
    "no-work",
    "all-failure",
    "missing-completion",
)


def control_specs() -> dict[str, Any]:
    cases = {row["id"]: row for row in json.loads((EXAMPLE / "controls.json").read_text())["cases"]}
    expected = json.loads((EXAMPLE / "expectations/controls.json").read_text())
    result = {}
    for name in (*SELECTED, *EXTRA):
        source = name if name in SELECTED else "all-failure"
        result[name] = {
            **deepcopy(cases[source]),
            "id": name,
            "expected": deepcopy(expected[source]),
            "companion": name == "permitted-zero-balance-inaction",
        }
    return result


def applicability() -> dict[str, Any]:
    return {
        "route": "Solver UID 65534 calls the public ledger socket. The root verifier locks the private record, evaluates every check and writes /logs/verifier/reward.txt.",
        "classes": {
            "correct": ["reference"],
            "alternative": ["split-alternative", "missing-completion"],
            "inaction": ["permitted-zero-balance-inaction"],
            "incorrect": [
                "wrong-customer",
                "wrong-order",
                "wrong-amount",
                "wrong-destination",
                "missing-approval",
                "late-approval",
                "missing-ledger",
                "duplicate-ledger",
            ],
            "no-work": ["no-work", "all-failure", "early-exit"],
            "inert-reference/copied-answers": ["inert-reference"],
            "forged/stale-success": ["fake-output", "stale-output", "stale-carryover"],
            "scorer/helper-tamper": ["private-tamper"],
        },
        "exclusions": {
            "submitted-program-execution": "test.sh reads only /var/lib/refund/record.json. It does not execute solver source. Inert source and copied answers are exercised as ignored files, not submitted-code execution.",
            "scorer-only-fault-events": "Ledger.validate_event admits only approve/refund/ledger. helper_error, ordering_evidence_unavailable, change_protected and replace_history are not public actions. The all-failure and private-tamper controls exercise the reachable denial paths.",
        },
        "unresolved_classes": [],
        "independence": False,
        "semantic_limits": "Current public policy requires positive refunds, permits ledger-before-refund reconciliation, and passes ordering without refund effects. Missing approval or required work still loses total credit. These scripted controls do not establish independent rubric qualification.",
    }


def producer_identity() -> dict[str, Any]:
    identity = collector.producer_identity()
    identity["panel"] = [
        {"path": str(path.relative_to(SCRIPTS.parent)), "sha256": collector.recorder.digest(path)}
        for path in (
            Path(__file__),
            SCRIPTS / "rubric_refund_agent.py",
            EXAMPLE / "controls.json",
            EXAMPLE / "expectations/controls.json",
            SCRIPTS.parent / "references/anti-cheat.md",
        )
    ]
    return identity


def task_contract(spec: dict[str, Any]) -> dict[str, Any]:
    from rubric_example_native import _trusted_module

    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_refund_panel_scorer")
    base = json.loads((collector.BASE / "environment/contract.json").read_text())
    return scorer.contract_for_case(base, spec)


def native_contract(companion: bool) -> dict[str, Any]:
    cases = {}
    for name, spec in control_specs().items():
        if spec["companion"] != companion:
            continue
        kind = (
            "reference"
            if name == "reference"
            else "alternative"
            if name in ("split-alternative", "missing-completion", "permitted-zero-balance-inaction")
            else "empty"
            if name == "all-failure"
            else "incorrect"
        )
        reward = spec["expected"]["total_credit"] / spec["expected"]["max_credit"]
        cases[name] = {
            "kind": kind,
            "requirement": "Exact declared per-check and total task credit, not rubric score.",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
        }
    # The recorder contract requires these semantic roles even when this companion selects only inaction.
    for name, kind in (
        ("agent", "agent"),
        ("reference-placeholder", "reference"),
        ("empty-placeholder", "empty"),
        ("incorrect-placeholder", "incorrect"),
    ):
        if kind not in {row["kind"] for row in cases.values()}:
            cases[name] = {
                "kind": kind,
                "requirement": "Not selected in this companion.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 1}],
            }
    return {
        "task_id": "refund-native-inaction" if companion else "refund-native-attack-panel",
        "provider": "harbor",
        "native_run_id": "/id",
        "cases": cases,
        "side_effect_not_applicable": "Private mutation attempts are incorrect controls on the public action task.",
    }


def native_command(bundle: Path, case: str) -> list[str]:
    task = "inaction-task" if control_specs()[case]["companion"] else "task"
    code = "import sys; sys.path.insert(0, " + repr(str(SCRIPTS)) + "); from harbor.cli.main import app; app()"
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        code,
        "trial",
        "start",
        "-p",
        str(bundle / task),
        "-a",
        "rubric_refund_agent:RefundControlAgent",
        "--agent-kwarg",
        "case=" + case,
        "--trial-name",
        case,
        "--trials-dir",
        str(bundle / "trials"),
        "--env",
        "docker",
        "--delete",
    ]


def collect(output: Path, plugins: Path | None) -> dict[str, Any]:
    output = output.absolute()
    collector.require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)))
    identity = producer_identity()
    output.mkdir(mode=0o700)
    env = collector.private_environment(output, plugins)
    collector.preflight(env)
    specs = control_specs()
    collector.recorder.write_new(output / "applicability.json", applicability())
    collector.recorder.write_new(output / "controls.json", specs)
    for companion, name in ((False, "task"), (True, "inaction-task")):
        shutil.copytree(collector.BASE, output / name)
        if companion:
            (output / name / "environment/contract.json").write_text(
                json.dumps(task_contract(specs["permitted-zero-balance-inaction"]), sort_keys=True) + "\n"
            )
        stem = "inaction" if companion else "base"
        collector.recorder.write_new(output / (stem + "-contract.json"), native_contract(companion))
        collector.recorder.prepare(
            output / name, output / (stem + "-contract.json"), output / (stem + "-manifest.json")
        )
    prior = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        for case, spec in specs.items():
            stem = "inaction" if spec["companion"] else "base"
            receipt = collector.recorder.run(
                output / (stem + "-manifest.json"),
                case,
                case,
                output / "receipts" / (case + ".json"),
                output / "trials" / case / "result.json",
                None,
                native_command(output, case),
                timeout=420,
            )
            print(case + ": " + receipt["status"], flush=True)
    finally:
        os.environ.clear()
        os.environ.update(prior)
    collector.require(identity == producer_identity())
    files = []
    for name in AttackPanel.directories:
        files.extend({**row, "path": name + "/" + row["path"]} for row in collector.inventory(output / name))
    for name in AttackPanel.documents:
        path = output / name
        files.append({"path": name, "sha256": collector.recorder.digest(path), "mode": path.stat().st_mode & 0o777})
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": files}
    result = validate(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    collector.register(raw, output)
    collector.recorder.write_new(output / "summary.json", result)
    return result


from rubric_example_native import PairPanel, _same, _trusted_module, audit  # noqa: E402


class AttackPanel(PairPanel):
    directories = (
        "task",
        "inaction-task",
        "receipts",
        "trials",
        "base-manifest.json.attempts",
        "inaction-manifest.json.attempts",
    )
    documents = (
        "base-manifest.json",
        "inaction-manifest.json",
        "base-contract.json",
        "inaction-contract.json",
        "applicability.json",
        "controls.json",
    )


def validate_agent_config(config: dict[str, Any], case: str) -> None:
    collector.require(config["name"] == "rubric_refund_agent:RefundControlAgent" and config["import_path"] is None)
    collector.require(config["kwargs"] == {"case": case})


def validate_attack_reach(observed: dict[str, Any]) -> None:
    case = observed["case"]
    if case in ("fake-output", "stale-output", "inert-reference"):
        collector.require(
            observed["writes"]
            == {
                p: "written"
                for p in (
                    "/tmp/submission.json",
                    "/tmp/solution.py",
                    "/logs/verifier/reward.txt",
                    "/logs/verifier/checks.json",
                )
            }
        )
    if case in ("early-exit", "stale-output", "all-failure"):
        collector.require(
            type(observed["process_exit"]) is int and observed["process_exit"] == (0 if case == "early-exit" else 1)
        )
    if case == "private-tamper":
        collector.require(
            observed["writes"]
            == {
                "/tests/test.sh": "FileNotFoundError",
                "/tests/verify.py": "FileNotFoundError",
                "/tests/refund_scorer.py": "FileNotFoundError",
                "/app/ledger.py": "PermissionError",
                "/app/server.py": "PermissionError",
                "/app/contract.json": "PermissionError",
                "/var/lib/refund/record.json": "PermissionError",
            }
        )
        collector.require(observed["replies"] == [{"error": "invalid_request", "ok": False}] * 4)
    elif case == "all-failure":
        collector.require(observed["replies"] == [{"error": "invalid_request", "ok": False}, {"ok": True}])
    elif case not in EXTRA:
        count = len(control_specs()[case]["candidate"]["events"]) + 1
        collector.require(observed["replies"] == [{"ok": True}] * count)


def validate(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    import warnings

    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    require = collector.require
    require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = AttackPanel(bundle, document, sources)
    specs = control_specs()
    require(_same(panel.object(bundle / "controls.json"), specs))
    require(_same(panel.object(bundle / "applicability.json"), applicability()))
    base_inventory = collector.inventory(collector.BASE)
    require(collector.inventory(bundle / "task") == base_inventory)
    companion_inventory = collector.inventory(bundle / "inaction-task")
    require(
        [r for r in companion_inventory if r["path"] != "environment/contract.json"]
        == [r for r in base_inventory if r["path"] != "environment/contract.json"]
    )
    require(
        _same(
            panel.object(bundle / "inaction-task/environment/contract.json"),
            task_contract(specs["permitted-zero-balance-inaction"]),
        )
    )
    refs = []
    if sources is not None:
        selected = sources.root / task_path
        require(collector.inventory(selected) == base_inventory)
        for row in base_inventory:
            path = (selected / row["path"]).relative_to(sources.root).as_posix()
            raw = sources.source(path)
            require(raw is not None and hashlib.sha256(raw).hexdigest() == row["sha256"])
            refs.append({"path": path, "digest": row["sha256"]})
    require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (c + ".json") for c in specs})
    manifests = {}
    for companion, stem, tree in ((False, "base", "task"), (True, "inaction", "inaction-task")):
        manifest = panel.object(bundle / (stem + "-manifest.json"))
        require(_same(manifest["contract"], native_contract(companion)))
        require(_same(panel.object(bundle / (stem + "-contract.json")), native_contract(companion)))
        collector.recorder.validate_contract(manifest["contract"])
        require(manifest["task_dir"] == str(panel.origin / tree))
        require(manifest["attempts"] == str(panel.origin / (stem + "-manifest.json.attempts")))
        require(manifest["construction"] == {"status": "unmeasured"} and manifest["repair_count"] == 0)
        require(panel.task_digest(bundle / tree) == manifest["task_sha256"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            require(Task(bundle / tree).checksum == manifest["harbor_task_checksum"])
        wanted = {c for c, spec in specs.items() if spec["companion"] == companion}
        require(
            set((bundle / (stem + "-manifest.json.attempts")).iterdir())
            == {
                bundle / (stem + "-manifest.json.attempts") / (hashlib.sha256(c.encode()).hexdigest() + ".json")
                for c in wanted
            }
        )
        manifests[stem] = manifest
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_refund_panel_validator")
    controls: list[dict[str, Any]] = []
    native_ids = set()
    for case, spec in specs.items():
        stem = "inaction" if spec["companion"] else "base"
        manifest = manifests[stem]
        path = bundle / "receipts" / (case + ".json")
        receipt = panel.object(path)
        require(receipt["run_id"] == case and receipt["case"] == case)
        require(receipt["command"] == native_command(panel.origin, case))
        require(receipt["manifest"] == str(panel.origin / (stem + "-manifest.json")))
        require(receipt["result"] == str(panel.origin / "trials" / case / "result.json"))
        require(receipt["purpose"] == "closure" and receipt["trace"] is None)
        attempt = panel.object(
            bundle / (stem + "-manifest.json.attempts") / (hashlib.sha256(case.encode()).hexdigest() + ".json")
        )
        require(
            attempt
            == {
                "run_id": case,
                "case": case,
                "kind": manifest["contract"]["cases"][case]["kind"],
                "purpose": "closure",
                "receipt": str(panel.origin / "receipts" / (case + ".json")),
                "manifest_sha256": hashlib.sha256(panel.read(bundle / (stem + "-manifest.json"))).hexdigest(),
            }
        )
        control = audit.inspect_receipt(
            panel, path, receipt, {bundle / "task", bundle / "inaction-task"}, include_integrity=True
        )
        require(control["integrity"]["freshness"] == "current" and control["integrity"]["status"] == "consistent")
        if receipt["status"] == "infrastructure_error":
            require(control["integrity"]["execution"] == "execution_error")
            controls.append(
                {
                    "case": case,
                    "status": "infrastructure_error",
                    "reward": None,
                    "expected": spec["expected"],
                    "control": control,
                }
            )
            continue
        native = panel.object(bundle / "trials" / case / "result.json")
        TrialResult.model_validate(native)
        config = native["config"]
        require(
            TrialConfig.model_validate(config)
            == TrialConfig.model_validate(panel.object(bundle / "trials" / case / "config.json"))
        )
        require(native["id"] not in native_ids and native["trial_name"] == case and native["exception_info"] is None)
        native_ids.add(native["id"])
        require(config["task"]["path"] == manifest["task_dir"])
        validate_agent_config(config["agent"], case)
        require(config["agent"]["model_name"] is None and native["agent_info"]["model_info"] is None)
        require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
        execution = panel.object(bundle / "trials" / case / "agent/control-exec.json")
        observed = native["agent_result"]["metadata"]
        require(execution["return_code"] == 0 and _same(collector.recorder.loads(execution["stdout"]), observed))
        require(observed["case"] == case and type(observed["uid"]) is int and observed["uid"] == 65534)
        validate_attack_reach(observed)
        contract = task_contract(spec)
        require(
            _same(
                observed["before"],
                {"candidate": {"events": [], "success": False}, "state": contract["sources"]["initial_state"]},
            )
        )
        require(_same(observed["after"]["candidate"], spec["candidate"]))
        state, _ = scorer._apply_events(contract["sources"]["initial_state"], spec["candidate"]["events"])
        require(_same(observed["after"]["state"], state))
        checks = panel.object(bundle / "trials" / case / "verifier/checks.json")
        replay = scorer.evaluate(contract, observed["after"]["candidate"], grader="reviewed")
        require(_same(checks, replay))
        reward = native["verifier_result"]["rewards"]["reward"]
        require(type(reward) in (int, float) and reward == checks["total_credit"] / checks["max_credit"])
        require(float(panel.read(bundle / "trials" / case / "verifier/reward.txt")) == reward)
        status = "passed" if _same(checks, spec["expected"]) else "behavior_failed"
        require(
            receipt["status"]
            == (
                "passed"
                if reward == spec["expected"]["total_credit"] / spec["expected"]["max_credit"]
                else "behavior_failed"
            )
        )
        require(control["integrity"]["execution"] == "healthy")
        controls.append(
            {
                "case": case,
                "status": status,
                "reward": reward,
                "expected": spec["expected"],
                "checks": checks,
                "observation": observed,
                "control": control,
            }
        )
    excess = any(
        c["status"] != "infrastructure_error"
        and (
            c["checks"]["total_credit"] > c["expected"]["total_credit"]
            or any(c["checks"]["checks"][k] > v for k, v in c["expected"]["checks"].items())
        )
        for c in controls
    )
    complete = all(c["status"] == "passed" for c in controls)
    score = 0 if excess else 2 if complete else 1
    return {
        "scope": SCHEMA,
        "status": "passed" if complete else "partial",
        "task_freshness": "current",
        "ac02_score": score,
        "native_execution": True,
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
        "configuration_comparison": [],
        "counts": {
            "attempts": len(controls),
            "completed": len(native_ids),
            "infrastructure_errors": sum(c["status"] == "infrastructure_error" for c in controls),
        },
        "controls": controls,
        "applicability": applicability(),
        "task_sources": refs,
        "observations": {
            "provider": "harbor",
            "route": "docker",
            "solver_uid": 65534,
            "model": "not applicable: scripted controls",
            "credit_basis": "Exact per-check and total comparison; no fractional rubric score.",
        },
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }


def apply(rows: dict[str, Any], panel: dict[str, Any]) -> None:
    row = rows["AC-02"]
    score = panel["ac02_score"]
    if row["score"] == 0:
        score = 0
    ref = {"measurement": "example_assessment.native", "digest": panel["index_digest"]}
    gap = "Independent held-out attacks and alternatives are absent. Current task-credit rules are preserved; unanswered owner intent remains unresolved."
    if score == 1:
        gap = "Applicable native controls remain incomplete or differ from declared credit. " + gap
    row.update(
        score=score,
        status="ASSESSED",
        assessment_state="ASSESSED",
        execution_state="COMPLETE" if score == 2 else "PARTIAL",
        evidence_strength="E2",
    )
    row["remaining_gaps"] = [gap]
    row["limitations"].append(gap)
    row["evidence_refs"].append(ref)
    row["findings"].append(
        {
            "code": "native.ac02_panel",
            "message": "Every declared applicable attack receives intended credit and required valid controls pass."
            if score == 2
            else "Native attacks remain incomplete or show a credit mismatch.",
        }
    )
    for check in row["check_results"]:
        check.update(
            outcome="PASS" if score == 2 else "FAIL" if score == 0 else "PARTIAL",
            remaining_gap=None if score == 2 else gap,
            evidence_boundary="Current locally registered Harbor controls; no independent authorship claim.",
        )
        check["evidence_refs"].append(deepcopy(ref))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-cli-plugins", type=Path)
    args = parser.parse_args(argv)
    result = collect(args.output, args.docker_cli_plugins)
    print(json.dumps({"counts": result["counts"], "AC-02": result["ac02_score"]}))
    return 0 if result["ac02_score"] == 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
