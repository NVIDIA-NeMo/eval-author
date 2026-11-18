# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Additive native upper-anchor companion, with explicit nonblind provenance.

The table subcommand invokes the ordinary rubric_table CLI and its replay. It
installs only an additive measurement hook; selected legacy evidence is unchanged.
No score is promoted without a separately scoped method decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import rubric_native_collect as collector
from rubric_example_native import PairPanel, _same, _trusted_module, audit

SCRIPTS = Path(__file__).resolve().parent
SCHEMA = "upper-anticheat-contract-v1"
CASES = ("target-alone", "valid-predecessor", "target-after", "unlisted-approver")
CRITERIA = (
    "AC-01",
    "AC-02",
    "AC-03",
    "AC-04",
    "task-contract.clear-contract",
    "task-contract.discoverable-requirements",
    "task-contract.scoring-authority",
)
GAPS = {
    "AC-01": "Additional native direct/proc-root reads corroborate denial, but exposed author is not an unexposed held-out access reviewer; independent/held-out qualification remains pending.",
    "AC-02": "Public-derived attacks and alternative executed; this reviewer saw prior attack-mechanism lessons and overlapping case knowledge. Independently authored unexposed held-out attacks remain absent.",
    "AC-03": "New actual native consumer controls completed. Decide whether this separately implemented, disclosed-exposure consumer corroboration meets E3; no blanket method approval exists.",
    "AC-04": "New ordered session/predecessor/reset controls completed. Decide whether this separately implemented, disclosed-exposure session corroboration meets E3.",
    "task-contract.clear-contract": "Public-first interpretation and valid boundary executed; decide whether disclosed-exposure independent implementation qualifies the additional interpretation. Existing owner crosswalk remains level-2 scope only.",
    "task-contract.discoverable-requirements": "Truly blind public-only solver-scope requirement discovery is not run. This exposed agent and prior exposed held-out session cannot satisfy it.",
    "task-contract.scoring-authority": "Independent sources and adjudicators corroborating the full scoring contract remain absent. New bounded computation is not full authority or independent adjudication.",
}


def identity():
    value = collector.producer_identity()
    value["upper"] = [
        {"path": p.name, "sha256": collector.recorder.digest(p)}
        for p in (
            Path(__file__),
            SCRIPTS / "upper_anticheat_contract_runtime.py",
            SCRIPTS / "rubric_refund_boundary_runtime.py",
        )
    ]
    value["definitions"] = [
        {"path": p.name, "sha256": collector.recorder.digest(p)}
        for p in sorted((SCRIPTS.parent / "references").glob("*"))
        if p.is_file()
    ]
    return value


def command(output: Path, case: str):
    code = "import sys; sys.path.insert(0," + repr(str(SCRIPTS)) + "); from harbor.cli.main import app; app()"
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        code,
        "trial",
        "start",
        "-p",
        str(output / "task"),
        "-a",
        "upper_anticheat_contract_runtime:UpperAgent",
        "--agent-kwarg",
        "case=" + case,
        "--trial-name",
        case,
        "--trials-dir",
        str(output / "trials"),
        "--env",
        "docker",
        "--delete",
        "--verifier-import-path",
        "upper_anticheat_contract_runtime:UpperVerifier",
    ]


def expected(case, contract):
    checks = dict(contract["scoring"]["weights"])
    if case == "valid-predecessor":
        checks["completion_signal"] = 0
    elif case == "unlisted-approver":
        checks["approval_present"] = checks["approval_before_effect"] = 0
    else:
        for name in ("amount", "approval_present", "fresh_effect"):
            checks[name] = 0
    accepted = all(checks[k] == contract["scoring"]["weights"][k] for k in contract["scoring"]["mandatory"])
    return {
        "health": "ok",
        "reason": None,
        "checks": checks,
        "raw_credit": sum(checks.values()),
        "max_credit": sum(contract["scoring"]["weights"].values()),
        "total_credit": sum(checks.values()) if accepted else 0,
        "accepted": accepted,
    }


def native_contract():
    public = json.loads((collector.BASE / "environment/contract.json").read_text())
    value = collector.contract()
    value["task_id"] = SCHEMA
    value["cases"].update(
        {
            case: {
                "kind": "reference" if case == "valid-predecessor" else "incorrect",
                "requirement": "Frozen public-derived checks and current actual native consumer.",
                "health": [{"pointer": "/exception_info", "equals": None}],
                "checks": [
                    {
                        "pointer": "/verifier_result/rewards/reward",
                        "min": expected(case, public)["total_credit"] / sum(public["scoring"]["weights"].values()),
                        "max": expected(case, public)["total_credit"] / sum(public["scoring"]["weights"].values()),
                    }
                ],
            }
            for case in CASES
        }
    )
    return value


class UpperPanel(PairPanel):
    documents = ("manifest.json", "contract.json", "exposure.json", "execution-order.json")


def collect(output: Path, exposure: Path, registry: Path, plugins: Path):
    collector.require(not output.exists())
    source_identity = identity()
    output.mkdir(mode=0o700, parents=True)
    env = collector.private_environment(output, plugins)
    collector.preflight(env)
    shutil.copytree(collector.BASE, output / "task")
    shutil.copyfile(exposure, output / "exposure.json")
    collector.recorder.write_new(output / "contract.json", native_contract())
    collector.recorder.prepare(output / "task", output / "contract.json", output / "manifest.json")
    prior = dict(os.environ)
    order = []
    try:
        os.environ.clear()
        os.environ.update(env)
        for case in CASES:
            receipt = collector.recorder.run(
                output / "manifest.json",
                case,
                case,
                output / "receipts" / f"{case}.json",
                output / "trials" / case / "result.json",
                None,
                command(output, case),
                timeout=420,
            )
            path = output / "trials" / case / "verifier/upper-verifier.json"
            removed = None
            if path.is_file():
                native = json.loads(path.read_text())
                removed = not subprocess.check_output(
                    ["docker", "ps", "-aq", "--filter", "id=" + native["runtime"]["container"]], text=True, timeout=30
                ).strip()
            order.append({"case": case, "container_removed": removed})
            print(case + ": " + receipt["status"], flush=True)
    finally:
        os.environ.clear()
        os.environ.update(prior)
    collector.recorder.write_new(output / "execution-order.json", {"runs": order})
    collector.require(source_identity == identity())
    files = []
    for name in UpperPanel.directories:
        files.extend({**r, "path": name + "/" + r["path"]} for r in collector.inventory(output / name))
    for name in UpperPanel.documents:
        p = output / name
        files.append({"path": name, "sha256": collector.recorder.digest(p), "mode": p.stat().st_mode & 0o777})
    document = {"schema": SCHEMA, "producer": source_identity, "bundle": str(output), "files": files}
    collector.recorder.write_new(output / "native-index.json", document)
    projection = validate(output, document)
    collector.recorder.write_new(output / "summary.json", projection)
    raw = (output / "native-index.json").read_bytes()
    registry.mkdir(mode=0o700, parents=True, exist_ok=True)
    collector.recorder.write_new(registry / (hashlib.sha256(raw).hexdigest() + ".json"), document)
    return projection


def validate(bundle: Path, document: dict[str, Any]):
    from harbor.models.trial.config import TrialConfig
    from harbor.models.trial.result import TrialResult

    require = collector.require
    require(document["schema"] == SCHEMA and document["producer"] == identity())
    panel = UpperPanel(bundle, document)
    require(collector.inventory(bundle / "task") == collector.inventory(collector.BASE))
    require(_same(panel.object(bundle / "contract.json"), native_contract()))
    public = panel.object(bundle / "task/environment/contract.json")
    exposure = panel.object(bundle / "exposure.json")
    require(exposure["blind"] is False)
    require(
        hashlib.sha256(panel.read(bundle / "exposure.json")).hexdigest()
        == "67244d4927ddc0084c7988359f48053dc86edaecc09f89aa15101941010821d5"
    )
    require(set((bundle / "receipts").iterdir()) == {bundle / "receipts" / f"{c}.json" for c in CASES})
    order = panel.object(bundle / "execution-order.json")["runs"]
    require([r["case"] for r in order] == list(CASES))
    scorer = _trusted_module(collector.BASE / "tests/refund_scorer.py", "_upper_scorer")
    rows = []
    sessions = set()
    containers = set()
    ids = set()
    previous = None
    for case, closure in zip(CASES, order, strict=True):
        receipt_path = bundle / "receipts" / f"{case}.json"
        receipt = panel.object(receipt_path)
        require(receipt["command"] == command(panel.origin, case))
        owning = audit.inspect_receipt(panel, receipt_path, receipt, {bundle / "task"}, include_integrity=True)
        require(owning["integrity"]["freshness"] == "current" and owning["integrity"]["status"] == "consistent")
        trial = bundle / "trials" / case
        if trial / "verifier/upper-verifier.json" not in panel.index:
            require(receipt["status"] == "infrastructure_error")
            rows.append({"case": case, "status": "infrastructure_error", "reward": None, "owning": owning})
            continue
        raw = panel.object(trial / "result.json")
        parsed = TrialResult.model_validate(raw)
        require(parsed.started_at is not None and parsed.finished_at is not None)
        assert parsed.started_at is not None  # narrowed by require above
        require(previous is None or parsed.started_at >= previous)
        previous = parsed.finished_at
        require(
            TrialConfig.model_validate(raw["config"]) == TrialConfig.model_validate(panel.object(trial / "config.json"))
        )
        require(raw["config"]["agent"]["kwargs"] == {"case": case} and raw["config"]["agent"]["model_name"] is None)
        require(raw["config"]["task"]["path"] == str(panel.origin / "task"))
        require(raw["config"]["verifier"]["import_path"] == "upper_anticheat_contract_runtime:UpperVerifier")
        require(raw["config"]["environment"]["delete"] is True)
        agent = panel.object(trial / "agent/upper-agent.json")
        verifier = panel.object(trial / "verifier/upper-verifier.json")
        require(_same(agent, raw["agent_result"]["metadata"]))
        runtime = verifier["runtime"]
        require(runtime["session"] not in sessions and runtime["container"] not in containers and raw["id"] not in ids)
        sessions.add(runtime["session"])
        containers.add(runtime["container"])
        ids.add(raw["id"])
        require(closure["container_removed"] is True)
        require("(empty: deny all controlled TCP egress)" in runtime["network_policy"])
        require(
            agent["uid"] == 65534
            and agent["before"]
            == {
                "ok": True,
                "value": {"candidate": {"events": [], "success": False}, "state": public["sources"]["initial_state"]},
            }
        )
        require(all(v is None for v in agent["start_files"].values()))
        if case == "valid-predecessor":
            require(
                all(
                    agent["end_files"][p] is not None
                    for p in (
                        "/work/upper-sentinel.json",
                        "/work/.cache/upper-sentinel.json",
                        "/tmp/upper-sentinel.json",
                    )
                )
            )
        access = verifier["access"]
        require(access["uid"] == 65534)
        for prefix in ("", "/proc/self/root"):
            for path in ("/app/contract.json", "/tests/test.sh", "/tests/verify.py", "/tests/refund_scorer.py"):
                require(access["reads"][prefix + path]["status"] == "read")
            for path in ("/var/lib/refund/record.json", "/var/lib/refund/record.lock", "/solution/reference.py"):
                require(access["reads"][prefix + path]["status"] in ("PermissionError", "FileNotFoundError"))
        require(access["private_directory"] == "PermissionError")
        require(all(x["status"] == "written" for x in access["writes"].values()))
        reconstructed = scorer.evaluate(public, agent["after"]["value"]["candidate"])
        checks = panel.object(trial / "verifier/checks.json")
        require(_same(checks, verifier["checks"]) and _same(checks, reconstructed))
        reward = raw["verifier_result"]["rewards"]["reward"]
        require(reward == verifier["consumer"]["rewards"]["reward"] == checks["total_credit"] / checks["max_credit"])
        match = _same(checks, expected(case, public))
        rows.append(
            {
                "case": case,
                "status": "matched" if match else "disagreement",
                "reward": reward,
                "checks": checks,
                "expected": expected(case, public),
                "owning": owning,
            }
        )
    complete = len(rows) == len(CASES) and all(r["status"] == "matched" for r in rows)
    return {
        "schema": SCHEMA,
        "status": "pass" if complete else "partial",
        "controls": rows,
        "native_sessions": len(sessions),
        "blind": False,
        "evidence_strength": "E2",
        "source_inventory": document["producer"]["task"],
        "exposure": exposure,
        "level_3_awarded": False,
        "remaining_method_decisions": GAPS,
        "boundaries": "Separately authored corroboration candidate; prior mechanism exposure disclosed. Not blinded, not automatically E3. No owner record invented.",
    }


def admitted(bundle: Path, registry: Path):
    raw = (bundle / "native-index.json").read_bytes()
    trusted = (registry / (hashlib.sha256(raw).hexdigest() + ".json")).read_bytes()
    collector.require(raw == trusted and not registry.is_relative_to(bundle))
    return validate(bundle, json.loads(raw))


@contextmanager
def table_hook(bundle: Path, registry: Path):
    """Minimal ordinary integration seam; no selected evidence or owner writes."""
    import rubric_table

    original = rubric_table.measure

    def measured(root, selection):
        projection = admitted(bundle, registry)
        task_id = selection.get("example_assessment", {}).get("task_config_id")
        tasks = [r for r in selection.get("artifacts", []) if r.get("id") == task_id]
        collector.require(len(tasks) == 1)
        selected_task = root / Path(tasks[0]["path"]).parent
        collector.require(collector.inventory(selected_task) == projection["source_inventory"])
        report = original(root, selection)
        # Read and validate again after all owning score projections.
        collector.require(_same(projection, admitted(bundle, registry)))
        # Keep raw native accounting in the linked companion instead of
        # duplicating its owning receipts inside the bounded table document.
        table_projection = {
            k: v for k, v in projection.items() if k not in ("controls", "exposure", "source_inventory")
        }
        table_projection["controls"] = [
            {k: v for k, v in r.items() if k not in ("owning", "expected")} for r in projection["controls"]
        ]
        table_projection["native_index"] = {
            "path": str(bundle / "native-index.json"),
            "sha256": collector.recorder.digest(bundle / "native-index.json"),
        }
        table_projection["exposure"] = {
            "path": str(bundle / "exposure.json"),
            "sha256": collector.recorder.digest(bundle / "exposure.json"),
            "blind": False,
        }
        report["measurements"]["upper_anticheat_contract"] = table_projection
        for key in CRITERIA:
            row = report["items"][key]
            row.setdefault("measurements", [])
            row["upper_anchor_companion"] = {
                "measurement": "upper_anticheat_contract",
                "status": projection["status"],
                "independence": "disclosed-exposure; not qualified E3",
                "remaining": GAPS[key],
            }
            row.setdefault("remaining_gaps", []).append(GAPS[key])
            # Supersede absence wording only for the newly executed boundary
            # probe. Keep failures, blind discovery and independent authority open.
            if key == "task-contract.clear-contract" and projection["status"] == "pass":
                for check in row["check_results"]:
                    if check["id"].endswith(".probe") and check["outcome"] == "NOT_RUN":
                        check["outcome"] = "PARTIAL"
                        check["remaining_gap"] = GAPS[key]
                        check.setdefault("evidence_refs", []).append(
                            {"measurement": "upper_anticheat_contract", "case": "valid-predecessor"}
                        )
                row["next_action"] = GAPS[key]
            if key in ("AC-03", "AC-04"):
                old = "Independent consumer/reset controls remain absent. This evidence covers the declared local Docker runtime only."
                for field in ("remaining_gaps", "limitations", "findings"):
                    if isinstance(row.get(field), list):
                        row[field] = [GAPS[key] if value == old else value for value in row[field]]
        return report

    rubric_table.measure = measured  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        rubric_table.measure = original


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["collect", "verify", "table"])
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--exposure", type=Path)
    p.add_argument("--plugins", type=Path, default=Path("/Applications/Docker.app/Contents/Resources/cli-plugins"))
    args, rest = p.parse_known_args()
    if args.action == "collect":
        if args.exposure is None:
            p.error("--exposure required")
        value = collect(args.bundle.absolute(), args.exposure, args.registry.absolute(), args.plugins)
    elif args.action == "verify":
        value = admitted(args.bundle.absolute(), args.registry.absolute())
    else:
        import rubric_table

        with table_hook(args.bundle.absolute(), args.registry.absolute()):
            return rubric_table.main(rest)
    print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
