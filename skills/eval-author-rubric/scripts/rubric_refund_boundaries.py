# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect current-consumer and cross-trial reset controls without a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import rubric_native_collect as collector

SCHEMA = "eval-author.refund-boundary-panel.v1"
SCRIPTS = Path(__file__).resolve().parent


def control_inventory() -> dict[str, Any]:
    return {
        "independence": False,
        "consumer_cases": ["forged-text", "forged-json", "wrong-task", "stale-after-failure", "valid-neighbor"],
        "surfaces": {
            "workspace": "/work/refund-sentinel.json",
            "temporary": "/tmp/refund-sentinel.json",
            "cache": "/work/.cache/refund-sentinel.json",
            "reward": ["/logs/verifier/reward.txt", "/logs/verifier/reward.json"],
            "checks": "/logs/verifier/checks.json",
            "backend": "/var/lib/refund/record.json via /run/refund/ledger.sock",
            "session": "Distinct native trial, Docker container and public socket state per trial",
            "helpers": ["/tests/test.sh", "/tests/verify.py", "/tests/refund_scorer.py"],
            "image": "Immutable Docker image; no writes to image layers or host build cache",
        },
        "sharing": "Only immutable task image and collector runtime are shared. No external service, model, network, or longitudinal state is configured. Host HOME and registry are private to this collection. Each trial uses Harbor delete=true and a fresh logs mount.",
        "reset": "The supported reset is deletion of the preceding Docker trial and creation of a fresh one, not a handwritten ledger reset.",
        "relations": {
            "target-alone": 0,
            "target-after-a": 0,
            "target-after-b": 0,
            "target-reset": 0,
            "valid-neighbor": 1,
        },
        "order": [
            "target-alone",
            "sentinel-a",
            "target-after-a",
            "sentinel-b",
            "target-after-b",
            "target-reset",
            "forged-text",
            "forged-json",
            "wrong-task",
            "stale-after-failure",
            "valid-neighbor",
        ],
        "expected_consumer": {
            "forged-text": {"reward": 0, "checks": "all-failure"},
            "forged-json": {"reward": 0, "checks": "all-failure"},
            "wrong-task": {"reward": 0, "checks": "all-failure"},
            "stale-after-failure": {"reward": None, "checks": "invalid_submission"},
            "valid-neighbor": {"reward": 1, "checks": "reference"},
        },
        "scope": "Current task shell, uploaded helpers, Harbor Verifier.verify output transfer/parser and owning recorder. Host registry is a local trust root, not independent authentication.",
    }


def native_command(bundle: Path, case: str) -> list[str]:
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
        str(bundle / "task"),
        "-a",
        "rubric_refund_boundary_runtime:BoundaryAgent",
        "--agent-kwarg",
        "case=" + case,
        "--trial-name",
        case,
        "--trials-dir",
        str(bundle / "trials"),
        "--env",
        "docker",
        "--delete",
        "--verifier-import-path",
        "rubric_refund_boundary_runtime:BoundaryVerifier",
    ]


from rubric_example_native import PairPanel, _same, _trusted_module, audit  # noqa: E402


class BoundaryPanel(PairPanel):
    documents = ("manifest.json", "contract.json", "control-inventory.json", "execution-order.json")


def producer_identity() -> dict[str, Any]:
    identity = collector.producer_identity()
    identity["boundary"] = [
        {"path": p.name, "sha256": collector.recorder.digest(p)}
        for p in (
            Path(__file__),
            SCRIPTS / "rubric_refund_boundary_runtime.py",
            SCRIPTS / "rubric_refund_panel.py",
            SCRIPTS.parent / "examples/refund/controls.json",
            SCRIPTS.parent / "examples/refund/expectations/controls.json",
            SCRIPTS.parent / "references/anti-cheat.md",
        )
    ]
    return identity


def contract() -> dict[str, Any]:
    value = collector.contract()
    value["task_id"] = "refund-boundary-controls"
    for case in control_inventory()["order"]:
        reward = 1 if case.startswith("sentinel-") or case == "valid-neighbor" else 0
        value["cases"][case] = {
            "kind": "reference" if reward else "incorrect",
            "requirement": "Current native reward and every task check must match the predeclared relation.",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": reward, "max": reward}],
        }
    return value


def collect(output: Path, plugins: Path | None) -> dict[str, Any]:
    output = output.absolute()
    collector.require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)))
    identity = producer_identity()
    output.mkdir(mode=0o700)
    env = collector.private_environment(output, plugins)
    collector.preflight(env)
    shutil.copytree(collector.BASE, output / "task")
    collector.recorder.write_new(output / "control-inventory.json", control_inventory())
    collector.recorder.write_new(output / "contract.json", contract())
    collector.recorder.prepare(output / "task", output / "contract.json", output / "manifest.json")
    prior = dict(os.environ)
    order = []
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in control_inventory()["order"]:
            receipt = collector.recorder.run(
                output / "manifest.json",
                case,
                case,
                output / "receipts" / (case + ".json"),
                output / "trials" / case / "result.json",
                None,
                native_command(output, case),
                timeout=420,
            )
            boundary_path = output / "trials" / case / "verifier/boundary.json"
            removed = None
            if boundary_path.is_file():
                boundary = collector.recorder.loads(boundary_path.read_bytes())
                remaining = subprocess.check_output(
                    ["docker", "ps", "-aq", "--filter", "id=" + boundary["runtime"]["container"]], text=True, timeout=30
                )
                removed = not remaining.strip()
                collector.require(removed)
            order.append(
                {
                    "case": case,
                    "container_removed": removed,
                    "receipt_sha256": collector.recorder.digest(output / "receipts" / (case + ".json")),
                }
            )
            print(case + ": " + receipt["status"], flush=True)
    finally:
        os.environ.clear()
        os.environ.update(prior)
    collector.recorder.write_new(output / "execution-order.json", {"runs": order})
    collector.require(identity == producer_identity())
    files = []
    for name in BoundaryPanel.directories:
        files.extend({**r, "path": name + "/" + r["path"]} for r in collector.inventory(output / name))
    for name in BoundaryPanel.documents:
        p = output / name
        files.append({"path": name, "sha256": collector.recorder.digest(p), "mode": p.stat().st_mode & 0o777})
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": files}
    result = validate(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    collector.register(raw, output)
    collector.recorder.write_new(output / "summary.json", result)
    return result


def validate_reset_files(case: str, before: dict[str, Any], after: dict[str, Any], candidate: dict[str, Any]) -> None:
    surfaces = control_inventory()["surfaces"]
    paths = [surfaces[k] for k in ("workspace", "temporary", "cache", "checks")] + surfaces["reward"]
    collector.require(before == {p: {"exists": False, "content": None} for p in paths})
    collector.require(set(after) == set(paths))
    if case.startswith("sentinel-"):
        for key in ("workspace", "temporary", "cache"):
            row = after[surfaces[key]]
            collector.require(
                row["exists"] is True and json.loads(row["content"]) == {"sentinel": case, "answer": candidate}
            )
        collector.require(json.loads(after[surfaces["checks"]]["content"]) == {"sentinel": case, "success": True})
        collector.require(float(after[surfaces["reward"][0]]["content"]) == 1)
        collector.require(json.loads(after[surfaces["reward"][1]]["content"]) == {"reward": 1})
    else:
        collector.require(after == before)


def validate(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult
    from rubric_refund_panel import control_specs

    require = collector.require
    require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = BoundaryPanel(bundle, document, sources)
    inventory = control_inventory()
    require(_same(panel.object(bundle / "control-inventory.json"), inventory))
    expected_task = collector.inventory(collector.BASE)
    require(collector.inventory(bundle / "task") == expected_task)
    refs = []
    if sources is not None:
        selected = sources.root / task_path
        require(collector.inventory(selected) == expected_task)
        for item in expected_task:
            p = (selected / item["path"]).relative_to(sources.root).as_posix()
            raw = sources.source(p)
            require(raw is not None and hashlib.sha256(raw).hexdigest() == item["sha256"])
            refs.append({"path": p, "digest": item["sha256"]})
    manifest = panel.object(bundle / "manifest.json")
    require(_same(manifest["contract"], contract()) and _same(panel.object(bundle / "contract.json"), contract()))
    collector.recorder.validate_contract(manifest["contract"])
    require(manifest["task_dir"] == str(panel.origin / "task"))
    require(manifest["attempts"] == str(panel.origin / "manifest.json.attempts"))
    require(panel.task_digest(bundle / "task") == manifest["task_sha256"])
    cases = inventory["order"]
    require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (c + ".json") for c in cases})
    require(
        set((bundle / "manifest.json.attempts").iterdir())
        == {bundle / "manifest.json.attempts" / (hashlib.sha256(c.encode()).hexdigest() + ".json") for c in cases}
    )
    require(
        panel.object(bundle / "execution-order.json")["runs"]
        == [
            {
                "case": c,
                "container_removed": True if bundle / "trials" / c / "verifier/boundary.json" in panel.index else None,
                "receipt_sha256": hashlib.sha256(panel.read(bundle / "receipts" / (c + ".json"))).hexdigest(),
            }
            for c in cases
        ]
    )
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_boundary_scorer")
    task_contract = panel.object(bundle / "task/environment/contract.json")
    controls: list[dict[str, Any]] = []
    receipt_errors = 0
    ids, containers, sessions = set(), set(), set()
    previous_finish = None
    for case in cases:
        receipt_path = bundle / "receipts" / (case + ".json")
        receipt = panel.object(receipt_path)
        receipt_errors += int(receipt["status"] == "infrastructure_error")
        require(
            receipt["case"] == case
            and receipt["run_id"] == case
            and receipt["command"] == native_command(panel.origin, case)
        )
        require(
            receipt["manifest"] == str(panel.origin / "manifest.json")
            and receipt["result"] == str(panel.origin / "trials" / case / "result.json")
        )
        require(receipt["purpose"] == "closure" and receipt["trace"] is None)
        attempt = panel.object(
            bundle / "manifest.json.attempts" / (hashlib.sha256(case.encode()).hexdigest() + ".json")
        )
        require(
            attempt
            == {
                "run_id": case,
                "case": case,
                "kind": manifest["contract"]["cases"][case]["kind"],
                "purpose": "closure",
                "receipt": str(panel.origin / "receipts" / (case + ".json")),
                "manifest_sha256": hashlib.sha256(panel.read(bundle / "manifest.json")).hexdigest(),
            }
        )
        owning = audit.inspect_receipt(panel, receipt_path, receipt, {bundle / "task"}, include_integrity=True)
        require(owning["integrity"]["freshness"] == "current" and owning["integrity"]["status"] == "consistent")
        trial = bundle / "trials" / case
        if trial / "verifier/boundary.json" not in panel.index:
            require(receipt["status"] == "infrastructure_error")
            controls.append({"case": case, "status": "infrastructure_error", "reward": None, "owning": owning})
            continue
        native = panel.object(trial / "result.json")
        parsed = TrialResult.model_validate(native)
        if parsed.started_at is None or parsed.finished_at is None:
            raise ValueError("native trial timestamps are missing")
        require(previous_finish is None or parsed.started_at >= previous_finish)
        previous_finish = parsed.finished_at
        config = native["config"]
        require(TrialConfig.model_validate(config) == TrialConfig.model_validate(panel.object(trial / "config.json")))
        require(native["id"] not in ids and native["trial_name"] == case)
        ids.add(native["id"])
        require(config["task"]["path"] == str(panel.origin / "task"))
        require(
            config["agent"]["name"] == "rubric_refund_boundary_runtime:BoundaryAgent"
            and config["agent"]["kwargs"] == {"case": case}
            and config["agent"]["model_name"] is None
        )
        require(config["verifier"]["import_path"] == "rubric_refund_boundary_runtime:BoundaryVerifier")
        require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
        observation = panel.object(trial / "verifier/boundary.json")
        agent = panel.object(trial / "agent/boundary-agent.json")
        require(
            _same(agent, native["agent_result"]["metadata"]) and agent["case"] == case and observation["case"] == case
        )
        runtime = observation["runtime"]
        require(
            runtime["network_policy"]
            == "mode: controlled egress\nallowlist: /opt/egress-sidecar/allowlist.txt\n(empty: deny all controlled TCP egress)\n"
        )
        require(runtime["image"].startswith("sha256:") and len(runtime["image"]) == 71)
        require(
            sorted(runtime["mounts"], key=lambda r: r["Destination"])
            == sorted(
                [
                    {
                        "Type": "bind",
                        "Source": str(panel.origin / "trials" / case / source),
                        "Destination": destination,
                        "RW": True,
                    }
                    for source, destination in (
                        ("verifier", "/logs/verifier"),
                        ("agent", "/logs/agent"),
                        ("artifacts/logs/artifacts", "/logs/artifacts"),
                    )
                ],
                key=lambda r: r["Destination"],
            )
        )
        require(runtime["container"] not in containers and runtime["session"] not in sessions)
        containers.add(runtime["container"])
        sessions.add(runtime["session"])
        initial = {
            "ok": True,
            "value": {
                "candidate": {"events": [], "success": False},
                "state": task_contract["sources"]["initial_state"],
            },
        }
        require(agent["before"]["uid"] == 65534 and agent["before"]["backend"] == initial)
        require(all(r == {"exists": False, "content": None} for r in agent["before"]["files"].values()))
        valid = case.startswith("sentinel-") or case in ("valid-neighbor", "stale-after-failure")
        candidate = control_specs()[
            "split-alternative" if case == "sentinel-b" else "reference" if valid else "all-failure"
        ]["candidate"]
        require(_same(agent["after"]["backend"]["value"]["candidate"], candidate))
        require(agent["replies"] == [{"ok": True}] * (len(candidate["events"]) + int(candidate["success"])))
        after = observation["after_upload"]
        require(after["uid"] == 65534 and after["backend"] == agent["after"]["backend"])
        validate_reset_files(case, agent["before"]["files"], agent["after"]["files"], candidate)
        require(after["files"] == agent["after"]["files"])
        require(
            set(after["helpers"])
            == {
                "/tests/test.sh",
                "/tests/verify.py",
                "/tests/refund_scorer.py",
                "/app/contract.json",
                "/app/ledger.py",
                "/app/server.py",
            }
        )
        for path, helper in after["helpers"].items():
            source = collector.BASE / (
                path.removeprefix("/") if path.startswith("/tests/") else "environment/" + Path(path).name
            )
            require(
                helper["sha256"] == collector.recorder.digest(source) and helper["write_attempt"] == "PermissionError"
            )
        if case.startswith("sentinel-"):
            for path in (
                "/work/refund-sentinel.json",
                "/tmp/refund-sentinel.json",
                "/work/.cache/refund-sentinel.json",
            ):
                require(json.loads(agent["after"]["files"][path]["content"]) == {"sentinel": case, "answer": candidate})
        if case in ("forged-text", "forged-json", "wrong-task"):
            injection = observation["injection"]
            require(injection["uid"] == 65534)
            key = "/logs/verifier/reward.json" if case == "forged-json" else "/logs/verifier/reward.txt"
            require(key in injection["writes"] and "/logs/verifier/checks.json" in injection["writes"])
            require(json.loads(injection["writes"][key]["content"]) == ({"reward": 1} if case == "forged-json" else 1))
            if case == "wrong-task":
                require(
                    json.loads(injection["writes"]["/logs/verifier/checks.json"]["content"])["task_id"]
                    == "foreign-refund-task"
                )
        checks = panel.object(trial / "verifier/checks.json")
        require(_same(checks, observation["checks"]))
        if case == "stale-after-failure":
            require(observation["prior_consumer"] == {"rewards": {"reward": 1.0}})
            require(_same(observation["prior_checks"], scorer.evaluate(task_contract, candidate, grader="reviewed")))
            require(observation["failure"] == {"record_content": "{", "fault_role": "trusted-controller"})
            require(checks["health"] == "error" and checks["reason"] == "invalid_submission")
            reward = None if native["verifier_result"] is None else native["verifier_result"]["rewards"].get("reward")
            passed = reward is None and observation["error"] == "RewardFileNotFoundError"
        else:
            require(native["exception_info"] is None and observation["error"] is None)
            require(_same(checks, scorer.evaluate(task_contract, candidate, grader="reviewed")))
            reward = native["verifier_result"]["rewards"]["reward"]
            require(observation["consumer"] == native["verifier_result"])
            require(float(panel.read(trial / "verifier/reward.txt")) == checks["total_credit"] / checks["max_credit"])
            passed = reward == checks["total_credit"] / checks["max_credit"]
        controls.append(
            {
                "case": case,
                "status": "passed" if passed else "behavior_failed",
                "reward": reward,
                "checks": checks,
                "observation": observation,
                "owning": owning,
            }
        )
    by_case = {c["case"]: c for c in controls}
    consumer = [by_case[c] for c in inventory["consumer_cases"]]
    reset = [by_case[c] for c in cases if c.startswith(("target-", "sentinel-"))]
    score3 = (
        0
        if any(c["status"] == "behavior_failed" for c in consumer)
        else 2
        if all(c["status"] == "passed" for c in consumer)
        else None
    )
    score4 = 2 if all(c["status"] == "passed" for c in reset) else None
    return {
        "scope": SCHEMA,
        "status": "passed" if score3 == score4 == 2 else "partial",
        "task_freshness": "current",
        "ac03_score": score3,
        "ac04_score": score4,
        "native_execution": True,
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
        "configuration_comparison": [],
        "observations": {"inventory": inventory},
        "task_sources": refs,
        "counts": {
            "attempts": len(controls),
            "completed": len(ids),
            "infrastructure_errors": receipt_errors,
        },
        "controls": controls,
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }


def apply(rows: dict[str, Any], panel: dict[str, Any]) -> None:
    selected = [("AC-03", "ac03_score"), ("AC-04", "ac04_score")]
    if panel["ac03_score"] == 0:
        selected.append(("AC-02", "ac03_score"))
    for ident, field in selected:
        score = panel[field]
        if score is None:
            continue
        row = rows[ident]
        score = 0 if row["score"] == 0 else score
        ref = {"measurement": "example_assessment.native", "digest": panel["index_digest"]}
        gap = "Independent consumer/reset controls remain absent. This evidence covers the declared local Docker runtime only."
        if score == 0:
            gap = "The actual Harbor consumer accepted solver-authored reward.json over current reward.txt. " + gap
        row.update(
            score=score,
            status="ASSESSED",
            assessment_state="ASSESSED",
            execution_state="COMPLETE",
            evidence_strength="E2",
        )
        row["remaining_gaps"] = [gap]
        row["limitations"].append(gap)
        row["evidence_refs"].append(ref)
        row["findings"].append(
            {"code": "native.solver_reward_json" if score == 0 else "native.ac04_boundary", "message": gap}
        )
        for check in row["check_results"]:
            check.update(
                outcome="FAIL" if score == 0 else "PASS",
                remaining_gap=gap,
                evidence_boundary="Current native consumer and sentinel trial controls; local host registry, no independence claim.",
            )
            check["evidence_refs"].append(deepcopy(ref))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-cli-plugins", type=Path)
    args = parser.parse_args()
    result = collect(args.output, args.docker_cli_plugins)
    print(json.dumps({k: result[k] for k in ("counts", "ac03_score", "ac04_score")}))
    return 0 if result["ac03_score"] == result["ac04_score"] == 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
