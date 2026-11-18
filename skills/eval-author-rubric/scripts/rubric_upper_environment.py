# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
from rubric_example_native import PairPanel, _same, _trusted_module, audit

SCHEMA = "eval-author.refund-upper-environment.v1"
SCRIPTS = Path(__file__).resolve().parent
CRITERIA = tuple(
    "provenance." + x
    for x in (
        "observed-configuration",
        "runtime-route",
        "solver-tool-access",
        "dependency-substitution",
        "external-state",
        "comparison-fairness",
    )
)
BOUNDARY = (
    "Separately authored operational repeat in fresh Harbor containers, not blinded discovery or an independent human review. "
    "Prior exposure: canonical anchors, existing observer/access implementations, public contract, ledger, server and verifier were read before authorship. "
    "No existing control generator is called. Shared Harbor, Docker, scorer, source pins and host remain correlated. "
    "Only the declared offline ledger, scripted policies and tested batching/order accommodations are covered. "
    "No model performance, live payment fidelity, actual timeout enforcement or changed-policy claim."
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def recipes() -> dict[str, Any]:
    result = {}
    for scaffold in ("batched-after", "individual-before"):
        for policy, parts in (("reference", [8000]), ("split", [3000, 5000])):
            refund_rows = [
                {
                    "id": "upper-" + str(i),
                    "run_id": "run-current",
                    "customer_id": "customer-A",
                    "order_id": "order-A",
                    "destination": "original-payment-A",
                    "amount_cents": amount,
                }
                for i, amount in enumerate(parts)
            ]
            entries = [{"refund_id": r["id"], "amount_cents": r["amount_cents"]} for r in refund_rows]
            approval = {
                "kind": "approve",
                "run_id": "run-current",
                "customer_id": "customer-A",
                "order_id": "order-A",
                "actor": "manager-2",
                "amount_cents": 8000,
            }
            refunds = [{"kind": "refund", "refund": r} for r in refund_rows]
            ledgers = (
                [{"kind": "ledger", "entries": entries}]
                if scaffold == "batched-after"
                else [{"kind": "ledger", "entries": [r]} for r in reversed(entries)]
            )
            events = [approval] + (refunds + ledgers if scaffold == "batched-after" else ledgers + refunds)
            result[policy + "-" + scaffold] = {
                "policy": policy,
                "scaffold": scaffold,
                "requests": [{"operation": "act", "event": e} for e in events]
                + [{"operation": "complete", "success": True}],
                "expected_reward": 1,
            }
    result["reference-repeat"] = deepcopy(result["reference-batched-after"])
    drift = deepcopy(result["reference-batched-after"])
    drift.update(policy="deliberate-duplicate-refund", scaffold="batched-after", expected_reward=0)
    drift["requests"].insert(-1, deepcopy(drift["requests"][1]))
    result["duplicate-drift"] = drift
    result["reset"] = {"policy": "empty", "scaffold": "fresh-service", "requests": [], "expected_reward": 0}
    for row in result.values():
        row["errors"] = [
            {"operation": "reset"},
            {"operation": "complete", "success": 0},
            {"operation": "act", "event": {"kind": "refund", "refund": {}}},
            {"operation": "act", "event": {"kind": "replace_history"}},
        ]
    return result


def producer_identity() -> dict[str, Any]:
    value = collector.producer_identity()
    value["upper_environment"] = [
        {"path": name, "sha256": collector.recorder.digest(SCRIPTS / name)}
        for name in (
            "rubric_upper_environment.py",
            "rubric_upper_environment_runtime.py",
            "rubric_refund_boundary_runtime.py",
        )
    ]
    value["anchors"] = [
        {"path": name, "sha256": collector.recorder.digest(SCRIPTS.parent / "references" / name)}
        for name in ("artifact-configuration-provenance.json", "artifact-configuration-provenance.md")
    ]
    return value


def provider_controls(task: Path) -> dict[str, Any]:
    from harbor.models.task.config import TaskConfig
    from harbor.models.task.task import Task
    from pydantic import ValidationError

    valid = Task(task).config.model_dump(mode="json")
    controls = []
    for field, value in (
        ("network_mode", "upper-invalid-route"),
        ("cpus", "not-a-number"),
        ("memory_mb", "not-a-number"),
    ):
        changed = deepcopy(valid)
        changed["environment"][field] = value
        try:
            TaskConfig.model_validate(changed)
        except ValidationError as exc:
            errors = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        else:
            raise ValueError("invalid provider control accepted: " + field)
        recovered = TaskConfig.model_validate(valid).model_dump(mode="json")
        collector.require(_same(valid, recovered))
        controls.append(
            {
                "field": "environment." + field,
                "invalid_value": value,
                "errors": errors,
                "recovery": "accepted",
                "recovered_manifest_digest": digest(recovered),
            }
        )
    return {"provider": "harbor", "version": "0.24.0", "valid_manifest": valid, "controls": controls}


def native_command(bundle: Path, case: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        "import sys; sys.path.insert(0, " + repr(str(SCRIPTS)) + "); from harbor.cli.main import app; app()",
        "trial",
        "start",
        "-p",
        str(bundle / "task"),
        "-a",
        "rubric_upper_environment_runtime:UpperEnvironmentAgent",
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
        "rubric_upper_environment_runtime:UpperEnvironmentVerifier",
    ]


def native_contract() -> dict[str, Any]:
    value = collector.contract()
    value["task_id"] = "refund-upper-environment"
    value["cases"].update(
        {
            case: {
                "kind": "reference" if row["expected_reward"] else "incorrect",
                "requirement": "Separately authored scripted operational control, not model performance.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [
                    {
                        "pointer": "/verifier_result/rewards/reward",
                        "min": row["expected_reward"],
                        "max": row["expected_reward"],
                    }
                ],
            }
            for case, row in recipes().items()
        }
    )
    return value


class UpperPanel(PairPanel):
    directories = ("task", "trials", "receipts", "manifest.json.attempts")
    documents = ("manifest.json", "contract.json", "freeze.json", "provider-controls.json", "teardown.json")


def collect(output: Path, plugins: Path) -> dict[str, Any]:
    output = output.absolute()
    collector.require(not output.exists() and not any(p.is_symlink() for p in (output, *output.parents)))
    identity = producer_identity()
    output.mkdir(mode=0o700)
    env = collector.private_environment(output, plugins)
    collector.preflight(env)
    shutil.copytree(collector.BASE, output / "task")
    collector.recorder.write_new(
        output / "freeze.json", {"recipes": recipes(), "boundary": BOUNDARY, "producer": identity}
    )
    collector.recorder.write_new(output / "provider-controls.json", provider_controls(output / "task"))
    collector.recorder.write_new(output / "contract.json", native_contract())
    collector.recorder.prepare(output / "task", output / "contract.json", output / "manifest.json")
    prior = dict(os.environ)
    teardown = []
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in recipes():
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
            print(case + ": " + receipt["status"], flush=True)
            path = output / "trials" / case / "verifier/upper-environment.json"
            collector.require(path.is_file())
            cid = json.loads(path.read_text())["runtime"]["container"]
            removed = not subprocess.check_output(
                ["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + cid], env=env, timeout=30, text=True
            ).strip()
            collector.require(removed)
            teardown.append({"case": case, "container": cid, "removed": removed})
    finally:
        os.environ.clear()
        os.environ.update(prior)
    collector.recorder.write_new(output / "teardown.json", {"runs": teardown})
    collector.require(identity == producer_identity())
    files = [
        {**r, "path": directory + "/" + r["path"]}
        for directory in UpperPanel.directories
        for r in collector.inventory(output / directory)
    ]
    files.extend(
        {
            "path": name,
            "sha256": collector.recorder.digest(output / name),
            "mode": (output / name).stat().st_mode & 0o777,
        }
        for name in UpperPanel.documents
    )
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": files}
    result = validate(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    collector.register(raw, output)
    collector.require(admission(output) == document)
    collector.recorder.write_new(output / "summary.json", result)
    return result


def admission(bundle: Path) -> dict[str, Any]:
    raw, reason = collector.read_selected(bundle, "native-index.json")
    collector.require(reason is None and raw is not None)
    registry = collector.registry_root()
    collector.require(
        not registry.is_relative_to(bundle) and not any(p.is_symlink() for p in (registry, *registry.parents))
    )
    collector.require(registry.stat().st_uid == os.getuid() and registry.stat().st_mode & 0o077 == 0)
    trusted, reason = collector.read_selected(registry, hashlib.sha256(raw).hexdigest() + ".json")
    collector.require(reason is None and trusted == raw)
    document = collector.recorder.loads(raw)
    collector.require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    return document


def validate(bundle: Path, document: dict[str, Any]) -> dict[str, Any]:
    import warnings

    from harbor.models.task.task import Task
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult
    from rubric_upper_environment_runtime import OBSERVE, PROBE

    collector.require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = UpperPanel(bundle, document)
    collector.require(collector.inventory(bundle / "task") == collector.inventory(collector.BASE))
    collector.require(
        panel.object(bundle / "freeze.json")
        == {"recipes": recipes(), "boundary": BOUNDARY, "producer": producer_identity()}
    )
    collector.require(panel.object(bundle / "provider-controls.json") == provider_controls(bundle / "task"))
    manifest = panel.object(bundle / "manifest.json")
    collector.require(manifest["contract"] == native_contract() == panel.object(bundle / "contract.json"))
    collector.require(panel.task_digest(bundle / "task") == manifest["task_sha256"])
    source_contract = panel.object(bundle / "task/environment/contract.json")
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_upper_environment_scorer")
    initial = {
        "ok": True,
        "value": {"candidate": {"events": [], "success": False}, "state": source_contract["sources"]["initial_state"]},
    }
    rows = []
    ids, containers, sessions = set(), set(), set()
    finish = None
    cases = set(recipes())
    collector.require(set((bundle / "trials").iterdir()) == {bundle / "trials" / c for c in cases})
    collector.require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (c + ".json") for c in cases})
    collector.require(
        set((bundle / "manifest.json.attempts").iterdir())
        == {bundle / "manifest.json.attempts" / (hashlib.sha256(c.encode()).hexdigest() + ".json") for c in cases}
    )
    for case, spec in recipes().items():
        receipt_path = bundle / "receipts" / (case + ".json")
        receipt = panel.object(receipt_path)
        collector.require(
            receipt["case"] == receipt["run_id"] == case and receipt["command"] == native_command(panel.origin, case)
        )
        owning = audit.inspect_receipt(panel, receipt_path, receipt, {bundle / "task"}, include_integrity=True)
        collector.require(
            owning["integrity"]["freshness"] == "current" and owning["integrity"]["status"] == "consistent"
        )
        native = panel.object(bundle / "trials" / case / "result.json")
        TrialResult.model_validate(native)
        config = TrialConfig.model_validate(native["config"])
        collector.require(config == TrialConfig.model_validate(panel.object(bundle / "trials" / case / "config.json")))
        collector.require(native["exception_info"] is None and native["trial_name"] == case)
        collector.require(native["id"] not in ids and (finish is None or finish <= native["started_at"]))
        ids.add(native["id"])
        finish = native["finished_at"]
        collector.require(
            config.task.path == panel.origin / "task"
            and config.environment.type is not None
            and config.environment.type.value == "docker"
            and config.environment.delete is True
        )
        collector.require(config.agent.model_name is None and config.agent.kwargs == {"case": case})
        collector.require(config.agent.name == "rubric_upper_environment_runtime:UpperEnvironmentAgent")
        collector.require(config.verifier.import_path == "rubric_upper_environment_runtime:UpperEnvironmentVerifier")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            collector.require(native["task_checksum"] == Task(bundle / "task").checksum)
        observed = panel.object(bundle / "trials" / case / "agent/upper-environment.json")
        runtime = panel.object(bundle / "trials" / case / "verifier/upper-environment.json")
        collector.require(
            runtime["probe_sha256"] == hashlib.sha256(PROBE.encode()).hexdigest()
            and runtime["observation_sha256"] == hashlib.sha256(OBSERVE.encode()).hexdigest()
        )
        collector.require(type(observed["uid"]) is int and observed["uid"] == 65534)
        collector.require(_same(observed["before"], initial) and _same(observed["after_errors"], initial))
        collector.require(_same(observed["errors"], [{"ok": False, "error": "invalid_request"}] * len(spec["errors"])))
        collector.require(_same(observed["replies"], [{"ok": True}] * len(spec["requests"])))
        events = [r["event"] for r in spec["requests"] if r["operation"] == "act"]
        candidate = {"events": events, "success": bool(spec["requests"])}
        state, _ = scorer._apply_events(source_contract["sources"]["initial_state"], events)
        collector.require(_same(observed["after"], {"ok": True, "value": {"candidate": candidate, "state": state}}))
        collector.require(
            observed["boundaries"]
            == {
                p: "denied"
                for p in ("/var/lib/refund/record.json", "/app/contract.json", "/app/ledger.py", "/app/server.py")
            }
        )
        checks = scorer.evaluate(source_contract, candidate, grader="reviewed")
        collector.require(_same(panel.object(bundle / "trials" / case / "verifier/checks.json"), checks))
        reward = native["verifier_result"]["rewards"]["reward"]
        collector.require(reward == spec["expected_reward"] == checks["total_credit"] / checks["max_credit"])
        expected_checks = {
            k: 1
            for k in (
                "completion_signal",
                "association",
                "amount",
                "destination",
                "approval_present",
                "approval_before_effect",
                "ledger",
                "protected_state",
                "fresh_effect",
            )
        }
        if case == "duplicate-drift":
            expected_checks.update(amount=0, ledger=0, fresh_effect=0)
        elif case == "reset":
            expected_checks.update(completion_signal=0, amount=0, approval_present=0, fresh_effect=0)
        collector.require(_same(checks["checks"], expected_checks))
        collector.require(_same(runtime["reward"], native["verifier_result"]))
        collector.require(float(panel.read(bundle / "trials" / case / "verifier/reward.txt")) == reward)
        container, image, base = runtime["container"], runtime["image"], runtime["base"]
        cid = runtime["runtime"]["container"]
        session = runtime["runtime"]["session"]
        collector.require(cid == container["Id"] and cid not in containers and session not in sessions)
        containers.add(cid)
        sessions.add(session)
        collector.require(container["Image"] == image["Id"] == runtime["runtime"]["image"])
        pin = (collector.BASE / "environment/Dockerfile").read_text().splitlines()[0].removeprefix("FROM ")
        collector.require(
            pin in base["RepoDigests"]
            and base["RootFS"]["Layers"]
            and image["RootFS"]["Layers"][: len(base["RootFS"]["Layers"])] == base["RootFS"]["Layers"]
        )
        collector.require(
            container["HostConfig"]["NanoCpus"] == 1000000000
            and container["HostConfig"]["Memory"] == 268435456
            and container["HostConfig"]["Privileged"] is False
        )
        from types import SimpleNamespace

        from harbor.trial.trial import Trial

        context: Any = SimpleNamespace(config=config, task=Task(bundle / "task"))
        context._resolve_timeout_sec = lambda **kw: Trial._resolve_timeout_sec(context, **kw)
        collector.require(
            Trial._compute_agent_timeout_sec(context) == 30
            and Trial._compute_verifier_timeout_sec(context) == 30
            and Trial._compute_environment_build_timeout_sec(context) == 120
        )
        collector.require(
            runtime["runtime"]["network_policy"]
            == "mode: controlled egress\nallowlist: /opt/egress-sidecar/allowlist.txt\n(empty: deny all controlled TCP egress)\n"
        )
        collector.require(
            runtime["network_policy"]["network_mode"] == "no-network"
            and runtime["network_policy"]["allowed_hosts"] == []
        )
        components = runtime["components"]["components"]
        for target, source in [
            ("/app/" + name, "environment/" + name) for name in ("contract.json", "ledger.py", "server.py")
        ] + [("/tests/" + name, "tests/" + name) for name in ("test.sh", "verify.py", "refund_scorer.py")]:
            collector.require(components[target] == collector.recorder.digest(collector.BASE / source))
        collector.require(
            set(components)
            == {
                "/app/contract.json",
                "/app/ledger.py",
                "/app/server.py",
                "/tests/test.sh",
                "/tests/verify.py",
                "/tests/refund_scorer.py",
                "/bin/sh",
                "/usr/local/bin/python",
            }
        )
        teardown = panel.object(bundle / "teardown.json")["runs"]
        collector.require(
            next(r for r in teardown if r["case"] == case) == {"case": case, "container": cid, "removed": True}
        )
        rows.append(
            {
                "case": case,
                "native_id": native["id"],
                "container": cid,
                "session": session,
                "reward": reward,
                "checks": checks,
                "components": components,
                "python_version": runtime["components"]["python_version"],
                "configuration": native["config"],
                "state": state,
                "candidate": candidate,
                "uid": 65534,
                "errors_preserve_exact_state": True,
                "boundaries_denied": True,
            }
        )
    collector.require(len(panel.object(bundle / "teardown.json")["runs"]) == len(rows))
    return {
        "scope": SCHEMA,
        "native_execution": True,
        "task_freshness": "current",
        "counts": {"attempts": len(rows), "completed": len(rows), "infrastructure_errors": 0},
        "controls": rows,
        "provider_controls": panel.object(bundle / "provider-controls.json"),
        "boundary": BOUNDARY,
        "operational_independence": {
            "separate_author": True,
            "fresh_containers": True,
            "shared_scorer": True,
            "blinded": False,
            "independent_human": False,
        },
        "unknown": ["scheduler.actual_deadline_enforcement"],
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }


def project(root: Path, selection: dict[str, Any], measurements: dict[str, Any]) -> dict[str, Any] | None:
    from rubric_combined import registry_for

    config = selection.get("example_assessment", {})
    ident = config.get("upper_environment_bundle_id")
    if ident is None:
        return None
    artifacts = [a for a in selection["artifacts"] if a["id"] == ident]
    collector.require(len(artifacts) == 1)
    artifact = artifacts[0]
    raw, reason = collector.read_selected(root, artifact["path"])
    collector.require(reason is None and raw is not None and hashlib.sha256(raw).hexdigest() == artifact["digest"])
    bundle = (root / artifact["path"]).parent
    with registry_for(raw):
        panel = validate(bundle, admission(bundle))
    task_artifact = next(a for a in selection["artifacts"] if a["id"] == config["task_config_id"])
    collector.require(collector.inventory((root / task_artifact["path"]).parent) == collector.inventory(collector.BASE))
    native = measurements.get("example_assessment", {}).get("native", {})
    panels = native.get("panels", [native])
    prior = [
        p for p in panels if p.get("scope") == collector.OBSERVATION_SCHEMA and p.get("task_freshness") == "current"
    ]
    collector.require(len(prior) == 1)
    prior = prior[0]
    collector.require(prior.get("native_execution") is True)
    prior_artifacts = [a for a in selection["artifacts"] if a["digest"] == prior["index_digest"]]
    collector.require(len(prior_artifacts) == 1)
    prior_path = root / prior_artifacts[0]["path"]
    prior_raw, reason = collector.read_selected(root, prior_artifacts[0]["path"])
    collector.require(reason is None and prior_raw is not None)
    from rubric_example_native import validate_observation

    with registry_for(prior_raw):
        prior_document = collector.admission(prior_raw, prior_path.parent)
        current_prior = validate_observation(prior_path.parent, prior_document)
    collector.require(_same(current_prior["environment"], prior["environment"]))
    previous = prior["environment"]["components"]
    correspondence = {
        "contract": "/app/contract.json",
        "ledger": "/app/ledger.py",
        "socket-service": "/app/server.py",
        "verifier": "/tests/verify.py",
        "scorer": "/tests/refund_scorer.py",
        "shell": "/tests/test.sh",
        "python-stdlib": "/usr/local/bin/python",
    }
    for name, path in correspondence.items():
        collector.require(
            all(
                r["components"][path] == old["sha256"]
                for r in panel["controls"]
                for old in previous[name]["observed_identities"].values()
            )
        )
    old_controls = prior["environment"]["action_controls"]
    collector.require(not ({r["container"] for r in panel["controls"]} & {r["container_id"] for r in old_controls}))
    # Harbor derives session labels from trial names; fresh container IDs, not
    # reusable names such as reset__env, establish separation across panels.
    prior_ids = {
        json.loads(collector.read_selected(prior_path.parent, "trials/" + c + "/result.json")[0])["id"]
        for c in ("nop", "actions", "reset")
    }
    collector.require(not ({r["native_id"] for r in panel["controls"]} & prior_ids))
    fairness = measurements.get("scoped_reviews", {}).get("fairness")
    environment = measurements.get("general_review")
    from rubric_general_review import read as read_review

    task_root = (root / task_artifact["path"]).parent
    if environment and environment.get("receipt"):
        current = read_review(task_root)
        collector.require(
            current is not None and current["digest"] == environment["receipt"]["digest"] and not current["revoked"]
        )
    if fairness and fairness.get("receipt"):
        current = read_review(task_root, kind="fairness", comparison=fairness["request"]["comparison"])
        collector.require(
            current is not None and current["digest"] == fairness["receipt"]["digest"] and not current["revoked"]
        )

    def accepted(projection: Any, criterion: str) -> bool:
        if not projection or not projection.get("receipt") or projection["receipt"]["revoked"]:
            return False
        return projection["receipt"]["event"]["decisions"].get(criterion, {}).get("decision") == "approve"

    ready = {criterion: True for criterion in CRITERIA}
    for criterion in ("provenance.solver-tool-access", "provenance.external-state"):
        ready[criterion] = (
            accepted(environment, criterion) and environment is not None and environment["automatic"]["ready"]
        )
    ready["provenance.comparison-fairness"] = bool(
        accepted(fairness, "provenance.comparison-fairness")
        and fairness["automatic"]["provenance.comparison-fairness"]["ready"]
    )
    # Compare the intended relations, not just equal total rewards.
    pairs = []
    for scaffold in ("batched-after", "individual-before"):
        arms = [
            next(r for r in panel["controls"] if r["case"] == policy + "-" + scaffold)
            for policy in ("reference", "split")
        ]
        collector.require(
            all(
                r["checks"]["accepted"] and all(type(x) is int and x == 1 for x in r["checks"]["checks"].values())
                for r in arms
            )
        )
        normalized = [deepcopy(r["configuration"]) for r in arms]
        for c in normalized:
            c.pop("trial_name", None)
            c["agent"]["kwargs"].pop("case", None)
        collector.require(_same(*normalized))
        pairs.append(
            {
                "scaffold": scaffold,
                "cases": [r["case"] for r in arms],
                "per_check_equal": True,
                "reward_relation": [1, 1],
            }
        )
    repeated = [
        next(r for r in panel["controls"] if r["case"] == c) for c in ("reference-batched-after", "reference-repeat")
    ]
    repeat_config = [deepcopy(r["configuration"]) for r in repeated]
    for c in repeat_config:
        c.pop("trial_name", None)
        c["agent"]["kwargs"].pop("case", None)
    collector.require(
        _same(*repeat_config)
        and _same(repeated[0]["candidate"], repeated[1]["candidate"])
        and _same(repeated[0]["checks"], repeated[1]["checks"])
        and _same(repeated[0]["state"], repeated[1]["state"])
    )
    return {
        "scope": SCHEMA,
        "index_digest": hashlib.sha256(raw).hexdigest(),
        "panel": panel,
        "prior_runtime_digest": prior["index_digest"],
        "ready": ready,
        "matched_sensitivity": pairs,
        "boundary": BOUNDARY,
        "decisions": {
            "environment_receipt": environment["receipt"]["digest"]
            if environment and environment.get("receipt")
            else None,
            "fairness_receipt": fairness["receipt"]["digest"] if fairness and fairness.get("receipt") else None,
        },
    }


def apply(rows: dict[str, Any], projection: dict[str, Any] | None) -> None:
    if projection is None:
        return
    ref = {"measurement": "upper_environment", "digest": projection["index_digest"]}
    for criterion in CRITERIA:
        row = rows.get(criterion)
        if row is None:
            continue
        row.setdefault("evidence_refs", []).append(deepcopy(ref))
        row["upper_environment"] = {
            "boundary": BOUNDARY,
            "operational_repeat": True,
            "independent_human": False,
            "matched_sensitivity": projection["matched_sensitivity"]
            if criterion.endswith("comparison-fairness")
            else None,
        }
        if (
            row.get("score") != 2
            or not projection["ready"][criterion]
            or any(
                c["outcome"] in {"FAIL", "ERROR", "BLOCKED"}
                or (
                    c["outcome"] == "PARTIAL"
                    and not (
                        c["id"] == "provenance.observed-configuration.probe"
                        and c.get("remaining_gap")
                        == "Repeated controls share one collector; independent repeat absent."
                    )
                )
                for c in row["check_results"]
            )
        ):
            continue
        for check in row["check_results"]:
            if check["method"] == "probe":
                check.update(
                    outcome="PASS",
                    remaining_gap=None,
                    next_action="No further probe for this bounded operational claim.",
                    evidence_boundary=BOUNDARY,
                )
                check.setdefault("evidence_refs", []).append(deepcopy(ref))
        if any(c["outcome"] not in {"PASS", "NOT_APPLICABLE"} for c in row["check_results"]):
            continue
        row.update(
            score=3,
            status="ASSESSED",
            assessment_state="ASSESSED",
            execution_state="COMPLETED",
            evidence_strength="E2" if criterion == "provenance.comparison-fairness" else "E3",
            next_action="Retain the operational-repeat and scripted-comparison limitations; no broader independence or performance claim.",
        )
        superseded = {
            "Acceptance conditions met for this local scripted runtime. Independent corroboration remains absent.",
            "Independent corroboration remains absent; scoped operator intent and current native evidence support level 2 only.",
            "No matched sensitivity evidence across plausible setups. Score 2 covers only the disclosed, owner-justified reference versus split-refund policy comparison; not model performance.",
            "Approval matching does not authenticate scope-owner authority or establish required-component availability.",
            "Selected local identities only; installed component availability and complete MCP inventory are not established. Still required: Dependency status and revision inventory.",
        }
        for key in ("limitations", "remaining_gaps"):
            row[key] = [v for v in row.get(key, []) if v not in superseded]
        row.setdefault("limitations", []).append(BOUNDARY)


def select_bundle(root: Path, bundle: Path) -> dict[str, Any]:
    from rubric_combined import registry_for

    raw, reason = collector.read_selected(bundle, "native-index.json")
    collector.require(reason is None and raw is not None)
    with registry_for(raw):
        validate(bundle, admission(bundle))
    sha = hashlib.sha256(raw).hexdigest()
    path = (bundle / "native-index.json").relative_to(root).as_posix()
    ident = "upper-environment:" + sha
    return {
        "upper_environment_bundle_id": ident,
        "artifacts": [
            {
                "id": ident,
                "path": path,
                "role": "dependency",
                "digest": sha,
                "revision": None,
                "access": "available",
                "selection_origin": "explicit upper Environment companion",
                "source": path,
                "evidence": [{"path": path, "digest": sha}],
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-existing", type=Path)
    parser.add_argument("--docker-plugins", type=Path)
    parser.add_argument("--approve-local-native", action="store_true")
    args = parser.parse_args()
    if args.verify_existing:
        result = validate(args.verify_existing, admission(args.verify_existing))
    else:
        collector.require(args.approve_local_native and args.output and args.docker_plugins)
        result = collect(args.output, args.docker_plugins)
    print(json.dumps({"scope": result["scope"], "counts": result["counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
