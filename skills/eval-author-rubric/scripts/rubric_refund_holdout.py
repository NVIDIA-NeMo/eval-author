# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import warnings
from pathlib import Path
from typing import Any

import rubric_native_collect as collector
from rubric_example_native import PairPanel, _same, _trusted_module, audit
from rubric_refund_holdout_runtime import CASES, payload

SCHEMA = "eval-author.refund-heldout-panel.v1"
FREEZE_SHA256 = "dbf1e39b04c0279cd022683639a589df21961afaa2d8e10d0d58e5c8588c727a"
SCRIPTS = Path(__file__).resolve().parent
require = collector.require


def frozen(path: Path) -> dict[str, Any]:
    raw, reason = collector.read_selected(path.parent, path.name)
    require(reason is None and raw is not None and hashlib.sha256(raw).hexdigest() == FREEZE_SHA256)
    result = collector.recorder.loads(raw)
    require([c["id"] for c in result["cases"]] == list(CASES))
    return result


def validate_public_sources() -> None:
    for name, digest in {
        "instruction.md": "ad5c684e2575351513c8e0781a640ff9d84e26032554fd823f01de5ca30c0585",
        "environment/contract.json": "1b1c9ee7c58bfa2dec18c8a39cd87389901ab3f77f08c5f269e8d4b38c05d3cb",
    }.items():
        raw, reason = collector.read_selected(collector.BASE, name)
        require(reason is None and raw is not None and hashlib.sha256(raw).hexdigest() == digest)


def producer_identity() -> dict[str, Any]:
    validate_public_sources()
    result = collector.producer_identity()
    result["holdout"] = [
        {"path": name, "sha256": collector.recorder.digest(SCRIPTS / name)}
        for name in (
            "rubric_refund_holdout.py",
            "rubric_refund_holdout_runtime.py",
        )
    ]
    result["rules"] = collector.inventory(SCRIPTS.parent / "references")
    result["freeze_sha256"] = FREEZE_SHA256
    return result


def task_contract(companion: bool) -> dict[str, Any]:
    result = json.loads((collector.BASE / "environment/contract.json").read_text())
    if companion:
        result["sources"]["initial_state"]["refunds"][0]["amount_cents"] = 9900
        result["sources"]["initial_state"]["ledger"][0]["amount_cents"] = 9900
        result["sources"]["policy"]["allow_zero_balance_no_action"] = True
    return result


def native_contract(companion: bool) -> dict[str, Any]:
    cases = {
        case: {
            "kind": "alternative" if companion else "incorrect",
            "requirement": "Observe native task credit without using frozen expected labels as assessment inputs.",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 1}],
        }
        for case in CASES
        if (case == "declared-zero-inaction") == companion
    }
    for kind in ("agent", "reference", "empty", "incorrect", "alternative"):
        if not any(row["kind"] == kind for row in cases.values()):
            cases[kind + "-unselected"] = {
                "kind": kind,
                "requirement": "Not selected.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 1}],
            }
    return {
        "task_id": "refund-heldout-zero" if companion else "refund-heldout-base",
        "provider": "harbor",
        "native_run_id": "/id",
        "cases": cases,
        "side_effect_not_applicable": "Bounded public action controls only.",
    }


def native_command(bundle: Path, case: str) -> list[str]:
    task = "inaction-task" if case == "declared-zero-inaction" else "task"
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
        "rubric_refund_holdout_runtime:RefundHoldoutAgent",
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


class HoldoutPanel(PairPanel):
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
        "public-freeze.json",
    )


def indexed_files(output: Path) -> list[dict[str, Any]]:
    rows = []
    for name in HoldoutPanel.directories:
        rows.extend({**row, "path": name + "/" + row["path"]} for row in collector.inventory(output / name))
    for name in HoldoutPanel.documents:
        path = output / name
        rows.append({"path": name, "sha256": collector.recorder.digest(path), "mode": path.stat().st_mode & 0o777})
    return rows


def admission(bundle: Path) -> dict[str, Any]:
    raw, reason = collector.read_selected(bundle, "native-index.json")
    require(reason is None and raw is not None)
    registry = collector.registry_root()
    require(not registry.is_relative_to(bundle) and not any(p.is_symlink() for p in (registry, *registry.parents)))
    require(registry.stat().st_uid == os.getuid() and registry.stat().st_mode & 0o077 == 0)
    trusted, reason = collector.read_selected(registry, hashlib.sha256(raw).hexdigest() + ".json")
    require(reason is None and trusted == raw)
    document = collector.recorder.loads(raw)
    require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    return document


def validate(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = HoldoutPanel(bundle, document, sources)
    freeze = frozen(bundle / "public-freeze.json")
    base = collector.inventory(collector.BASE)
    require(collector.inventory(bundle / "task") == base)
    companion = collector.inventory(bundle / "inaction-task")
    require(
        [r for r in companion if r["path"] != "environment/contract.json"]
        == [r for r in base if r["path"] != "environment/contract.json"]
    )
    require(_same(panel.object(bundle / "inaction-task/environment/contract.json"), task_contract(True)))
    if sources is not None:
        if task_path is None:
            raise ValueError("held-out selected task is missing")
        require(collector.inventory(sources.root / task_path) == base)
        for row in base:
            raw = sources.source((Path(task_path) / row["path"]).as_posix())
            require(raw is not None and hashlib.sha256(raw).hexdigest() == row["sha256"])
    require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (c + ".json") for c in CASES})
    manifests = {}
    for zero, stem, tree in ((False, "base", "task"), (True, "inaction", "inaction-task")):
        manifest = panel.object(bundle / (stem + "-manifest.json"))
        require(_same(manifest["contract"], native_contract(zero)))
        require(_same(panel.object(bundle / (stem + "-contract.json")), native_contract(zero)))
        collector.recorder.validate_contract(manifest["contract"])
        require(manifest["task_dir"] == str(panel.origin / tree))
        require(manifest["attempts"] == str(panel.origin / (stem + "-manifest.json.attempts")))
        require(manifest["construction"] == {"status": "unmeasured"} and manifest["repair_count"] == 0)
        require(panel.task_digest(bundle / tree) == manifest["task_sha256"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            require(Task(bundle / tree).checksum == manifest["harbor_task_checksum"])
        selected = [c for c in CASES if (c == "declared-zero-inaction") == zero]
        require(
            set((bundle / (stem + "-manifest.json.attempts")).iterdir())
            == {
                bundle / (stem + "-manifest.json.attempts") / (hashlib.sha256(c.encode()).hexdigest() + ".json")
                for c in selected
            }
        )
        manifests[stem] = manifest
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_holdout_current_scorer")
    controls, ids = [], set()
    for spec in freeze["cases"]:
        case = spec["id"]
        zero = case == "declared-zero-inaction"
        stem = "inaction" if zero else "base"
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
        integrity = audit.inspect_receipt(
            panel, path, receipt, {bundle / "task", bundle / "inaction-task"}, include_integrity=True
        )
        require(integrity["integrity"]["freshness"] == "current" and integrity["integrity"]["status"] == "consistent")
        if receipt["status"] == "infrastructure_error":
            controls.append({"case": case, "status": "infrastructure_error", "actual": None})
            continue
        native = panel.object(bundle / "trials" / case / "result.json")
        TrialResult.model_validate(native)
        config = native["config"]
        require(
            TrialConfig.model_validate(config)
            == TrialConfig.model_validate(panel.object(bundle / "trials" / case / "config.json"))
        )
        require(native["id"] not in ids and native["trial_name"] == case and native["exception_info"] is None)
        ids.add(native["id"])
        require(config["task"]["path"] == manifest["task_dir"])
        require(
            config["agent"]["name"] == "rubric_refund_holdout_runtime:RefundHoldoutAgent"
            and config["agent"]["import_path"] is None
        )
        require(config["agent"]["kwargs"] == {"case": case} and config["agent"]["model_name"] is None)
        require(native["agent_info"]["model_info"] is None)
        require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
        execution = panel.object(bundle / "trials" / case / "agent/holdout-exec.json")
        observed = native["agent_result"]["metadata"]
        require(execution["return_code"] == 0 and _same(collector.recorder.loads(execution["stdout"]), observed))
        require(observed["case"] == case and type(observed["uid"]) is int and observed["uid"] == 65534)
        script = payload(case)
        contract = task_contract(zero)
        initial = {"candidate": {"events": [], "success": False}, "state": contract["sources"]["initial_state"]}
        require(_same(observed["before"], initial))
        require(_same(observed["after"]["candidate"], script["candidate"]))
        require(_same(observed["replies"], [{"ok": True}] * (len(script["candidate"]["events"]) + 1)))
        rejections = [
            {
                "request": req,
                "reply": {"ok": False, "error": "invalid_request"},
                "state": {"ok": True, "value": initial},
            }
            for req in script["invalid_requests"]
        ]
        require(_same(observed["rejections"], rejections))
        state, _ = scorer._apply_events(contract["sources"]["initial_state"], script["candidate"]["events"])
        require(_same(observed["after"]["state"], state))
        checks = panel.object(bundle / "trials" / case / "verifier/checks.json")
        require(_same(checks, scorer.evaluate(contract, observed["after"]["candidate"], grader="reviewed")))
        reward = native["verifier_result"]["rewards"]["reward"]
        require(type(reward) in (int, float) and reward == checks["total_credit"] / checks["max_credit"])
        require(float(panel.read(bundle / "trials" / case / "verifier/reward.txt")) == reward)
        require(receipt["status"] == "passed" and integrity["integrity"]["execution"] == "healthy")
        controls.append(
            compare(spec, freeze["check_order"], checks)
            | {"reward": reward, "native_id": native["id"], "companion": zero}
        )
    return {
        "scope": SCHEMA,
        "status": "matched" if all(c["status"] == "matched" for c in controls) else "disagreement-or-incomplete",
        "counts": {
            "declared": len(CASES),
            "completed": len(ids),
            "matched": sum(c["status"] == "matched" for c in controls),
            "disagreements": sum(c["status"] == "disagreement" for c in controls),
            "infrastructure_errors": sum(c["status"] == "infrastructure_error" for c in controls),
        },
        "controls": controls,
        "freeze_sha256": FREEZE_SHA256,
        "native_execution": True,
        "independence_qualified": False,
        "rubric_score": None,
        "human_decisions": [],
        "qualification_requirements": [
            "Accept actual procedural separation and disclosed prior exposure for the exact criterion scope.",
            "Retain and resolve any expected/actual disagreement without rewriting the frozen expectation.",
            "Establish all lower-anchor checks with their own admitted sources.",
            "Obtain canonical human adjudication for contract criteria.",
            "Integrate this owning validator through the ordinary combined selection and replay path.",
        ],
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }


def collect(output: Path, freeze_path: Path, plugins: Path | None) -> dict[str, Any]:
    output = output.absolute()
    require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)))
    frozen(freeze_path)
    identity = producer_identity()
    output.mkdir(mode=0o700)
    shutil.copyfile(freeze_path, output / "public-freeze.json")
    env = collector.private_environment(output, plugins)
    collector.preflight(env)
    for zero, stem, tree in ((False, "base", "task"), (True, "inaction", "inaction-task")):
        shutil.copytree(collector.BASE, output / tree)
        if zero:
            (output / tree / "environment/contract.json").write_text(
                json.dumps(task_contract(True), sort_keys=True) + "\n"
            )
        collector.recorder.write_new(output / (stem + "-contract.json"), native_contract(zero))
        collector.recorder.prepare(
            output / tree, output / (stem + "-contract.json"), output / (stem + "-manifest.json")
        )
    prior = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in CASES:
            stem = "inaction" if case == "declared-zero-inaction" else "base"
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
    require(identity == producer_identity())
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": indexed_files(output)}
    result = validate(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    collector.register(raw, output)
    require(admission(output) == document)
    collector.recorder.write_new(output / "summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--docker-plugins", type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    if args.verify_existing:
        result = validate(args.output.absolute(), admission(args.output.absolute()))
    else:
        require(args.freeze is not None)
        result = collect(args.output, args.freeze, args.docker_plugins)
    print(json.dumps({"status": result["status"], "counts": result["counts"]}, sort_keys=True))
    return 0 if result["counts"]["infrastructure_errors"] == 0 else 2


def compare(spec: dict[str, Any], order: list[str], actual: dict[str, Any]) -> dict[str, Any]:
    expected = dict(zip(order, map(int, spec["expected_checks"]), strict=True))
    differences = [
        {"check": name, "expected": value, "actual": actual["checks"].get(name)}
        for name, value in expected.items()
        if actual["checks"].get(name) != value
    ]
    if actual["total_credit"] != spec["expected_total"]:
        differences.append(
            {"check": "total_credit", "expected": spec["expected_total"], "actual": actual["total_credit"]}
        )
    return {
        "case": spec["id"],
        "status": "disagreement" if differences else "matched",
        "differences": differences,
        "expected": {"checks": expected, "total_credit": spec["expected_total"]},
        "actual": actual,
    }


if __name__ == "__main__":
    raise SystemExit(main())
