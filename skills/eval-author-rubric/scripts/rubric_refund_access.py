# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect and replay the refund task's declared read boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import rubric_native_collect as collector
from rubric_example_native import PairPanel, _same, _trusted_module, audit

SCHEMA = "eval-author.refund-access-panel.v1"
SCRIPTS = Path(__file__).resolve().parent


def control_inventory() -> dict[str, Any]:
    return {
        "independence": False,
        "protected": {
            "/solution/reference.py": "absent",
            "/solution/solve.sh": "absent",
            "/var/lib/refund/record.json": "denied",
            "/var/lib/refund/record.lock": "denied",
        },
        "permitted": ["/app/contract.json", "/app/ledger.py", "/app/server.py"],
        "paths": ["direct", "proc-root", "symlink"],
        "cases": ["access-a", "access-b"],
        "review": {
            "kind": "agent source review, not independent or human",
            "authority": "instruction.md explicitly permits contract scoring rules, initial state and socket read responses. Protected state means write integrity, not confidentiality. The private record is an integrity channel whose content is intentionally returned by read.",
            "solution": "The reference solution remains host-only for these non-Oracle solver runs. The environment-only Docker context excludes solution, tests and expectation files. No task-specific content is present in the pinned upstream base layers.",
            "helpers": "Verifier sources are uploaded only after agent completion. The public task declares them public and readable, with write integrity protected. Native probes observe their actual readability. Reference solutions and private record files remain outside permitted reads.",
            "scope": "Selected task and native scripted solver UID 65534: task context, image layers, application files, mounted logs, /proc/self/root aliases, symlinks, socket read/error responses. Host Docker control is trusted and unavailable to the solver. No model-training or semantic leak claim.",
        },
        "owner_question": "The public task now declares verifier source readable and integrity-protected; no verifier-confidentiality decision remains. Final source-bound rubric adjudication and independent access corroboration remain separate requirements.",
        "remaining": "Independent, separately authored held-out access controls with documented prior exposure and separation are required for AC-01=3/E3. No such review is claimed.",
    }


def assess_reads(observed: dict[str, bool]) -> str | None:
    """'denied' when protected reads fail and public reads work, 'exposed' when a protected read succeeds."""
    if observed["public"] is not True:
        return None
    return "denied" if observed["protected"] is True else "exposed"


def producer_identity() -> dict[str, Any]:
    value = collector.producer_identity()
    value["access"] = [
        {"path": p.name, "sha256": collector.recorder.digest(p)}
        for p in (
            Path(__file__),
            SCRIPTS / "rubric_refund_access_runtime.py",
            SCRIPTS / "rubric_refund_boundary_runtime.py",
        )
    ]
    return value


def admission(raw: bytes, bundle: Path) -> dict[str, Any]:
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
        "rubric_refund_access_runtime:AccessAgent",
        "--trial-name",
        case,
        "--trials-dir",
        str(bundle / "trials"),
        "--env",
        "docker",
        "--delete",
        "--verifier-import-path",
        "rubric_refund_access_runtime:AccessVerifier",
    ]


def contract() -> dict[str, Any]:
    value = collector.contract()
    value["task_id"] = "refund-read-boundary"
    value["cases"] |= {
        case: {
            "kind": "empty",
            "requirement": "Read-only solver must retain NOP credit and intact state.",
            "health": [{"pointer": "/exception_info", "equals": None}],
            "checks": [{"pointer": "/verifier_result/rewards/reward", "min": 0, "max": 0}],
        }
        for case in control_inventory()["cases"]
    }
    return value


class AccessPanel(PairPanel):
    documents = ("manifest.json", "contract.json", "control-inventory.json", "teardown.json")


def collect(output: Path, plugins: Path | None) -> dict[str, Any]:
    import subprocess

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
    teardown = []
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in control_inventory()["cases"]:
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
            observation = output / "trials" / case / "verifier/access.json"
            removed = None
            if observation.is_file():
                ident = json.loads(observation.read_text())["runtime"]["container"]
                removed = not subprocess.check_output(
                    ["docker", "ps", "-aq", "--filter", "id=" + ident], text=True, timeout=30
                ).strip()
                collector.require(removed)
            teardown.append({"case": case, "removed": removed})
            print(case + ": " + receipt["status"], flush=True)
    finally:
        os.environ.clear()
        os.environ.update(prior)
    collector.recorder.write_new(output / "teardown.json", {"runs": teardown})
    collector.require(identity == producer_identity())
    files = []
    for name in AccessPanel.directories:
        files.extend({**r, "path": name + "/" + r["path"]} for r in collector.inventory(output / name))
    for name in AccessPanel.documents:
        p = output / name
        files.append({"path": name, "sha256": collector.recorder.digest(p), "mode": p.stat().st_mode & 0o777})
    document = {"schema": SCHEMA, "producer": identity, "bundle": str(output), "files": files}
    result = validate(output, document)
    raw = json.dumps(document, sort_keys=True).encode()
    (output / "native-index.json").write_bytes(raw)
    collector.register(raw, output)
    collector.recorder.write_new(output / "summary.json", result)
    return result


def validate_reads(record: dict[str, Any], after_upload: bool) -> dict[str, bool]:
    expected = control_inventory()
    collector.require(type(record["uid"]) is int and record["uid"] == 65534)
    collector.require(set(record["reads"]) == set(expected["paths"]))
    public = True
    protected = True
    for results in record["reads"].values():
        collector.require(set(results) == set(expected["protected"]) | set(expected["permitted"]))
        public = public and all(
            results[p]["status"] == "read"
            and results[p]["sha256"] == collector.recorder.digest(collector.BASE / "environment" / Path(p).name)
            for p in expected["permitted"]
        )
        protected = protected and all(results[p]["status"] == status for p, status in expected["protected"].items())
    task = json.loads((collector.BASE / "environment/contract.json").read_text())
    collector.require(
        _same(
            record["socket"],
            {
                "ok": True,
                "value": {"candidate": {"events": [], "success": False}, "state": task["sources"]["initial_state"]},
            },
        )
    )
    collector.require(record["invalid_tool"] == {"ok": False, "error": "invalid_request"})
    helpers = record["helpers"]
    collector.require(set(helpers) == {"/tests/test.sh", "/tests/verify.py", "/tests/refund_scorer.py"})
    for name, row in helpers.items():
        collector.require(row["status"] == ("read" if after_upload else "absent"))
        if after_upload:
            collector.require(row["sha256"] == collector.recorder.digest(collector.BASE / name.lstrip("/")))
    collector.require(record["docker_socket"] == "absent")
    return {"public": public, "protected": protected}


def validate(
    bundle: Path, document: dict[str, Any], sources: Any = None, task_path: str | None = None
) -> dict[str, Any]:
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    require = collector.require
    require(document["schema"] == SCHEMA and document["producer"] == producer_identity())
    panel = AccessPanel(bundle, document, sources)
    expected = collector.inventory(collector.BASE)
    require(collector.inventory(bundle / "task") == expected)
    refs = []
    if sources is not None:
        if task_path is None:
            raise ValueError("selected task required")
        require(collector.inventory(sources.root / task_path) == expected)
        for row in expected:
            path = (Path(task_path) / row["path"]).as_posix()
            raw = sources.source(path)
            require(raw is not None and hashlib.sha256(raw).hexdigest() == row["sha256"])
            refs.append({"path": path, "digest": row["sha256"]})
    require(_same(panel.object(bundle / "control-inventory.json"), control_inventory()))
    require(_same(panel.object(bundle / "contract.json"), contract()))
    manifest = panel.object(bundle / "manifest.json")
    require(_same(manifest["contract"], contract()))
    require(
        manifest["task_dir"] == str(panel.origin / "task")
        and panel.task_digest(bundle / "task") == manifest["task_sha256"]
    )
    cases = control_inventory()["cases"]
    require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / (c + ".json") for c in cases})
    require(
        set((bundle / "manifest.json.attempts").iterdir())
        == {bundle / "manifest.json.attempts" / (hashlib.sha256(c.encode()).hexdigest() + ".json") for c in cases}
    )
    require(set((bundle / "trials").iterdir()) == {bundle / "trials" / c for c in cases})
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_access_scorer")
    task_contract = panel.object(bundle / "task/environment/contract.json")
    controls = []
    ids = set()
    outcomes = []
    for case in cases:
        trial = bundle / "trials" / case
        receipt_path = bundle / "receipts" / (case + ".json")
        receipt = panel.object(receipt_path)
        require(
            receipt["case"] == receipt["run_id"] == case and receipt["command"] == native_command(panel.origin, case)
        )
        owning = audit.inspect_receipt(panel, receipt_path, receipt, {bundle / "task"}, include_integrity=True)
        require(owning["integrity"]["freshness"] == "current" and owning["integrity"]["status"] == "consistent")
        native = panel.object(trial / "result.json")
        TrialResult.model_validate(native)
        config = native["config"]
        require(TrialConfig.model_validate(config) == TrialConfig.model_validate(panel.object(trial / "config.json")))
        require(config["task"]["path"] == str(panel.origin / "task"))
        require(
            config["agent"]["name"] == "rubric_refund_access_runtime:AccessAgent"
            and config["agent"]["model_name"] is None
        )
        require(config["verifier"]["import_path"] == "rubric_refund_access_runtime:AccessVerifier")
        require(config["environment"]["type"] == "docker" and config["environment"]["delete"] is True)
        require(native["exception_info"] is None and native["id"] not in ids)
        ids.add(native["id"])
        observation = panel.object(trial / "verifier/access.json")
        agent = panel.object(trial / "agent/access.json")
        require(_same(agent, native["agent_result"]["metadata"]))
        outcomes.extend(
            assess_reads(validate_reads(r, uploaded))
            for r, uploaded in (
                (agent, False),
                (observation["after_upload"], True),
                (observation["after_verification"], True),
            )
        )
        runtime = observation["runtime"]
        require(
            runtime["network_policy"]
            == "mode: controlled egress\nallowlist: /opt/egress-sidecar/allowlist.txt\n(empty: deny all controlled TCP egress)\n"
        )
        require(
            sorted(runtime["mounts"], key=lambda r: r["Destination"])
            == sorted(
                [
                    {
                        "Type": "bind",
                        "Source": str(panel.origin / "trials" / case / source),
                        "Destination": dest,
                        "RW": True,
                    }
                    for source, dest in (
                        ("verifier", "/logs/verifier"),
                        ("agent", "/logs/agent"),
                        ("artifacts/logs/artifacts", "/logs/artifacts"),
                    )
                ],
                key=lambda r: r["Destination"],
            )
        )
        require(observation["image"]["Id"] == runtime["image"])
        require(
            observation["layers"]["matches"] == []
            and observation["layers"]["files_hashed"] > 0
            and observation["layers"]["layers"] > 0
        )
        require(
            observation["layers"]["targets"]
            == {
                p: collector.recorder.digest(collector.BASE / p) for p in ("solution/reference.py", "solution/solve.sh")
            }
        )
        require(observation["layers"]["errors"] == [])
        checks = panel.object(trial / "verifier/checks.json")
        require(_same(checks, scorer.evaluate(task_contract, {"events": [], "success": False}, grader="reviewed")))
        require(
            native["verifier_result"] == {"rewards": {"reward": 0.0}}
            and float(panel.read(trial / "verifier/reward.txt")) == 0
        )
        controls.append({"case": case, "status": "passed", "reward": 0, "checks": checks, "owning": owning})
    require(panel.object(bundle / "teardown.json")["runs"] == [{"case": c, "removed": True} for c in cases])
    return {
        "scope": SCHEMA,
        "task_freshness": "current",
        "status": "exposed"
        if "exposed" in outcomes
        else "passed"
        if all(o == "denied" for o in outcomes)
        else "partial",
        "native_execution": True,
        "human_review": False,
        "independence": False,
        "full_task_acceptance": False,
        "configuration_comparison": [],
        "task_sources": refs,
        "counts": {"attempts": len(cases), "completed": len(ids), "infrastructure_errors": 0},
        "controls": controls,
        "observations": {
            "inventory": control_inventory(),
            "read_phases": len(outcomes),
            "paths_per_phase": len(control_inventory()["paths"]),
        },
        "evidence_refs": [{"path": p, "digest": d} for p, d in sorted(panel.evidence.items())],
    }
