# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Original-population admission beside frozen producers; no collection or consent.

The finite packet allowlist is an identity boundary, not semantic authority.
Only explicit local-operator readback can qualify the new questions. Historical
Audit verification is reused, not rerun or resealed after entrypoint integration.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import stat
import sys
from collections import Counter
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import current_rubric_audit as audit_adapter
import current_rubric_support as support
from rubric_general_review import OPERATOR_BOUNDARY, _event, _operator_root, _write
from rubric_identity import read_selected

completion: Any = importlib.import_module("current_rubric_completion")

HERE = Path(__file__).resolve().parent
# Historical external packets are deliberately path-bound and immutable.
RUNS = HERE.parents[2] / "rubric-guided-runs"
SCOPE = RUNS / "original-base-scope-current"
HISTORY = RUNS / "original-base-history-current"
AUDIT = RUNS / "deterministic-audit-finish-current/closure-final"
ROOT = RUNS / "merged-native-current"
PINS = {
    SCOPE / "packet.json": "d7a080d9c2ed9977d06fcb87c05ca2e8d4714b57ca90471af9149de811d07433",
    SCOPE / "owner-questions.json": "6df6c43da902c39f65e8b8e80555e39642118e690455005193a12944067b455e",
    HISTORY / "packet-manifest.json": "9d9fe5244ac10649da0c3e6afd14de2b8b36dacd0f92f42cc77d78876a5e2a5d",
    AUDIT / "exercise/audit.json": "0f2f719d5beabfac44c9bfb5b7eb808b4f891ff4dbdc4f2bc0399b2eab217506",
    AUDIT / "report/assessment.json": "15164e2e8299a608837107fba322a57558ef3fdd95145d2265a7ac1cbee0e88a",
    AUDIT / "verification.json": "ad3b68a32018760c8d2ef4bd49b3ad6faf6c15c289ceda7a0fb9c03797cad3a9",
    AUDIT / "report/audit.json": "01168440824e751c70e771157bd6387d9350e668ff0f20cc4f210fb8017d8adf",
}
REVISION = audit_adapter.REVISION
SCOPED = ("AUDIT-03", "ENV-03-S01", "AUDIT-04-B", "TRACE-04-A", "AUDIT-05-B", "AUDIT-05-S03")
BOUNDARY = (
    "Exact original Harbor population only. Same-user local operator trust, not authentication. "
    "No inherited Q1-Q3/UQ1/UQ2 authority, held-out independence, publication, training use, "
    "new native/model execution, or score approval. Preserve all dissent and unknown history."
)


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def raw(path: Path) -> bytes:
    """Use the ordinary bounded no-follow reader, including ancestor checks."""
    require(path.is_absolute() and ".." not in path.parts, "nonliteral evidence path")
    data, reason = read_selected(path.parent, path.name)
    require(reason is None and data is not None, "unreadable bound input: " + str(path))
    return data


def load(path: Path) -> Any:
    from eval_author_shared.task_evidence import loads

    return loads(raw(path))


def pin(path: Path, expected: str) -> bytes:
    require(path.is_absolute() and ".." not in path.parts, "nonliteral evidence path")
    if path == SCOPE / "source-closure.json" or path.stat().st_size > 2_000_000:
        # Large linked identity manifest is hashed, never parsed or embedded in
        # the ordinary report. Keep the ordinary report reader's cap unchanged.
        require(not any(p.is_symlink() for p in (path, *path.parents)), "manifest alias")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), "nonregular manifest")
            hasher = hashlib.sha256()
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
            require(os.fstat(stream.fileno()) == before, "manifest changed during read")
        require(hasher.hexdigest() == expected, "bound input changed: " + str(path))
        return b""
    data = raw(path)
    require(hashlib.sha256(data).hexdigest() == expected, "bound input changed: " + str(path))
    return data


def inventory(root: Path) -> dict[str, Any]:
    require(root.is_dir() and not root.is_symlink(), "task directory unavailable")
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "task alias")
        if path.is_dir():
            continue
        require(stat.S_ISREG(path.stat().st_mode), "nonregular task input")
        result[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(raw(path)).hexdigest(),
            "mode": path.stat().st_mode & 0o777,
        }
    return result


def packet_inputs() -> dict[str, Any]:
    for path, expected in PINS.items():
        pin(path, expected)
    scope = load(SCOPE / "packet.json")
    for entry in scope["components"]:
        require(Path(entry["path"]).name == entry["path"], "unsafe component")
        pin(SCOPE / entry["path"], entry["sha256"])
    for entry in load(HISTORY / "packet-manifest.json")["files"]:
        require(Path(entry["path"]).name == entry["path"], "unsafe history component")
        pin(HISTORY / entry["path"], entry["sha256"])
    history = load(HISTORY / "packet.json")
    require(history["root"] == str(ROOT), "wrong original root")
    pin(Path(scope["selection_path"]), scope["selection_sha256"])
    pin(Path(scope["base_report"]), scope["base_report_sha256"])
    pin(Path(scope["canonical_path"]), scope["canonical_sha256"])
    population = history["population"]
    require(scope["selection_sha256"] == population["selection_sha256"], "packet population differs")
    for panel in history["panels"]:
        pin(ROOT / panel["path"] / "native-index.json", panel["index_sha256"])
        require(inventory(ROOT / panel["path"] / "task") == population["files"], "whole task inventory changed")
    # Check the existing history input closure, not a new filesystem discovery.
    for entry in load(HISTORY / "input-manifest.json")["files"]:
        path = Path(entry["path"])
        pin(path, entry["sha256"])
        require(path.stat().st_mode & 0o777 == entry["mode"], "history input mode changed")
    return {"scope": scope, "history": history, "selection": load(Path(scope["selection_path"]))}


def check_population(root: Path, selection: dict[str, Any], packets: dict[str, Any]) -> None:
    require(root == ROOT and encoded(selection) == encoded(packets["selection"]), "unsupported population or selection")
    require(
        len({a["id"] for a in selection["artifacts"]}) == len(selection["artifacts"]), "duplicate artifact identity"
    )
    for artifact in selection["artifacts"]:
        if artifact.get("access") == "available":
            path = Path(artifact["path"])
            require(not path.is_absolute() and ".." not in path.parts, "unsafe selected artifact")
            pin(root / path, artifact["digest"])


def outcome_inventory(packets: dict[str, Any]) -> dict[str, Any]:
    """Join the prebuilt history to exact indexed native results and check vectors."""
    rows = load(SCOPE / "native-outcomes.json")["rows"]
    ledger = load(HISTORY / "arithmetic.json")["control_ledger"]
    by_id = {r["native_id"]: r for r in ledger}
    require(len(by_id) == len(ledger) == len(rows), "duplicate or missing history/native outcomes")
    indexed = set()
    for panel in packets["history"]["panels"]:
        directory = ROOT / panel["path"]
        index = load(directory / "native-index.json")
        indexed.update(str(directory / f["path"]) for f in index["files"] if f["path"].endswith("/result.json"))
    require(indexed == {r["result_path"] for r in rows}, "native result population incomplete")
    for row in rows:
        native = json.loads(pin(Path(row["result_path"]), row["result_sha256"]))
        actual = load(Path(row["checks_path"]))
        prior = by_id[row["trial_uuid"]]
        rewards = (native.get("verifier_result") or {}).get("rewards") or {}
        exception = (native.get("exception_info") or {}).get("exception_type")
        require(native["id"] == row["trial_uuid"], "trial identity differs")
        require(encoded(actual) == encoded(row["actual"]) == encoded(prior["checks"]), "per-check outcome changed")
        require(rewards.get("reward") == row["reward"] == prior["reward"], "native reward differs")
        require(exception == row["exception_type"] == prior["exception_type"], "health error lost")
        require(prior["result"] == row["result_path"], "history source differs")
        require(prior["original_population"] is (row["population"] == "original-Harbor"), "population transfer")
        if row["health"] == "ok":
            require(row["expected"] is not None and row["reward"] is not None, "missing expected credit")
            expected = row["expected"]
            require(encoded(actual["checks"]) == encoded(expected["checks"]), "expected per-check mismatch")
            require(actual["total_credit"] == expected["total_credit"], "expected total mismatch")
            require(abs(row["reward"] - actual["total_credit"] / actual["max_credit"]) < 1e-12, "reward scale mismatch")
        else:
            require(row["reward"] is None and actual["health"] == "error", "operational failure counted as defense")
    originals = [r for r in rows if r["population"] == "original-Harbor"]
    healthy = [r for r in originals if r["health"] == "ok"]
    values = [Fraction(r["actual"]["total_credit"], r["actual"]["max_credit"]) for r in healthy]
    counts = {
        "trial_count": len(originals),
        "healthy_reward_count": len(healthy),
        "operational_error_count": len(originals) - len(healthy),
        "null_reward_count": sum(r["reward"] is None for r in originals),
        "reward_histogram": dict(Counter(str(v) for v in values)),
        "reward_sum": str(sum(values)),
        "descriptive_control_mean": str(sum(values) / len(values)),
    }
    sensitivity = load(HISTORY / "arithmetic.json")["sensitivity"]
    require(
        counts == sensitivity["original_controls"] == sensitivity["known_exposed_controls"],
        "sensitivity arithmetic differs",
    )
    require(
        sensitivity["unexposed_subject_ids"] == [] and sensitivity["exclusion_effect"] is None,
        "empty exclusion acquired effect",
    )
    classes = load(SCOPE / "AUDIT-03-crosswalk.json")["classes"]
    lookup = {r["panel"] + "/" + r["case"]: r for r in originals}
    for group in classes:
        controls = [lookup[c] for c in group["controls"]]
        require(len(controls) == group["declared_controls"], "class denominator differs")
        require(all(r["health"] == "ok" for r in controls), "class includes operational error")
    return {
        "counts": counts,
        "separate_trials": len(rows) - len(originals),
        "classes": classes,
        "sensitivity": sensitivity,
        "outcomes_ref": str(SCOPE / "native-outcomes.json"),
        "installed_verifier_followup": "supporting only; not original full-Trial denominator",
    }


def trace_inventory() -> dict[str, Any]:
    scope = load(SCOPE / "TRACE-04-A-scope.json")
    units = scope["eligible_material"]
    require(len(units) == scope["eligible_count"], "trace denominator changed")
    require(len({(u["source"], u["pointer"]) for u in units}) == len(units), "duplicate trace material")
    for unit in units:
        data = pin(Path(unit["source"]), unit["source_sha256"])
        pointer = unit["pointer"]
        if pointer.startswith("allowlist:"):
            source = json.loads(data)
            data = encoded({k: source[k] for k in pointer.removeprefix("allowlist:").split(",") if k in source})
        elif pointer == "/actions":
            data = encoded(json.loads(data)["actions"])
        elif pointer == "/stdout (parsed synthetic control JSON)":
            data = encoded(json.loads(json.loads(data)["stdout"]))
        elif pointer == "":
            if hashlib.sha256(data).hexdigest() != unit["material_sha256"]:
                data = encoded(json.loads(data))
        else:
            raise ValueError("unknown trace projection")
        require(
            hashlib.sha256(data).hexdigest() == unit["material_sha256"] and len(data) == unit["bytes"],
            "trace material differs",
        )
    require(dict(Counter(u["channel"] for u in units)) == scope["coverage_by_channel"], "trace channel count differs")
    return {
        "eligible_count": len(units),
        "channels": scope["coverage_by_channel"],
        "material_manifest_sha256": digest(units),
        "recipients": scope["recipients_proposed"],
        "use": scope["use_proposed"],
        "independent_labels": False,
        "release_audit": False,
    }


def requests(packets: dict[str, Any], observations: dict[str, Any]) -> list[dict[str, Any]]:
    questions = load(SCOPE / "owner-questions.json")["questions"] + load(HISTORY / "owner-questions.json")["questions"]
    result = []
    for question in questions:
        ident = question.get("criterion") or question["criteria"][0]
        scopes = {ident: question["question"]}
        if question["id"] == "OBH-Q1":
            scopes = {
                "AUDIT-05-B": "Accept only the bounded original exposure disclosure and retrospective sensitivity; "
                + question["question"],
                "AUDIT-05-B.provenance-method": "Separately qualify the disclosed contextual provenance method as an alternative, or defer. No blind/independent provenance is asserted.",
            }
        request = {
            "schema": "current-original-finish-request-v1",
            "question_id": question["id"],
            "population": packets["history"]["population"],
            "packet_sha256": hashlib.sha256(
                raw(SCOPE / "packet.json" if question["id"].startswith("OBS-") else HISTORY / "packet.json")
            ).hexdigest(),
            "components_sha256": digest(packets),
            "observations_sha256": digest(observations),
            "definitions_sha256": completion.runner.definitions_digest(),
            "question": deepcopy(question),
            "scopes": scopes,
            "boundary": BOUNDARY,
        }
        request["binding"] = digest(request)
        result.append(request)
    return result


def current_request(request: dict[str, Any], root: Path, selection: dict[str, Any]) -> None:
    packets = packet_inputs()
    check_population(root, selection, packets)
    previous = load(Path(packets["scope"]["base_report"]))["report"]
    current = validate(root, selection, previous)
    require(request in current["requests"], "request is not current exact original binding")


def record_decision(
    request: dict[str, Any],
    root: Path,
    selection: dict[str, Any],
    observed_decision: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> Path:
    """Retain only a genuine trusted-caller answer; never collect controls."""
    current_request(request, root, selection)
    store = _operator_root()
    require(store is not None, "explicit operator trust required")
    require(read_decision(request, root, selection) is None, "decision already retained")
    target = store / (request["binding"] + ".original-finish.json")
    event = _event(request, observed_decision(deepcopy(request)))
    current_request(request, root, selection)
    document = {"schema": "current-original-finish-review-v1", "trust_basis": OPERATOR_BOUNDARY, "event": event}
    _write(target, encoded(document) + b"\n")
    observed = read_decision(request, root, selection)
    require(observed is not None and observed["event"] == event and not observed["revoked"], "record readback differs")
    return target


def revoke_decision(request: dict[str, Any], root: Path, selection: dict[str, Any]) -> Path:
    # Historical decisions must remain revocable even after source drift.
    receipt = read_decision(request, root, selection)
    if receipt is None or receipt["revoked"]:
        raise ValueError("active exact record required")
    target = Path(receipt["path"]).with_suffix(".revoked")
    _write(target, b"revoked\n")
    observed = read_decision(request, root, selection)
    require(observed is not None and observed["revoked"] is True, "revocation readback differs")
    return target


def read_decision(request: dict[str, Any], root: Path, selection: dict[str, Any]) -> dict[str, Any] | None:
    """Read only. No selected JSON or status string can create owner authority."""
    store = _operator_root()
    if store is None:
        return None
    require(not store.resolve().is_relative_to(root.resolve()), "operator store inside assessed root")
    require(
        all(not (root / a["path"]).resolve().is_relative_to(store.resolve()) for a in selection["artifacts"]),
        "operator store selected as evidence",
    )
    target = store / (request["binding"] + ".original-finish.json")
    if not target.exists():
        require(not target.with_suffix(".revoked").exists(), "orphaned revocation")
        return None
    from eval_author_shared.task_evidence import loads

    content = raw(target)
    value = loads(content)
    require(set(value) == {"schema", "trust_basis", "event"}, "invalid finish event")
    require(
        value["schema"] == "current-original-finish-review-v1" and value["trust_basis"] == OPERATOR_BOUNDARY,
        "untrusted finish event",
    )
    event = _event(request, value["event"])
    return {
        "path": str(target),
        "sha256": hashlib.sha256(content).hexdigest(),
        "event": event,
        "revoked": target.with_suffix(".revoked").exists(),
        "trust_basis": OPERATOR_BOUNDARY,
        "authenticated": False,
    }


def audit_result(report: dict[str, Any], root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    """Reuse the exact externally verified exercise; never transfer another row."""
    retained = load(AUDIT / "exercise/audit.json")
    verification = load(AUDIT / "verification.json")
    # Completion is an orchestration dependency changed by this additive integration.
    # Retain its old identity; require exact current owning Audit and support code.
    for path, expected in verification["source_hashes"].items():
        if Path(path) != Path(completion.__file__):
            pin(Path(path), expected)
    baseline = audit_adapter.audit.inspect(root, selection)
    require(encoded(baseline) == encoded(load(AUDIT / "report/audit.json")), "owning Audit baseline changed")
    expected = deepcopy(retained)
    expected.pop("invalidation_tests")
    for key in ("adapter_identity", "selection_sha256"):
        expected.pop(key)
    expected["checks"][REVISION + ".deterministic-3"] = baseline["checks"][REVISION + ".deterministic-3"]
    expected["revision_score"] = baseline["revision_score"]
    expected["limitations"] = baseline["limitations"]
    require(encoded(expected) == encoded(baseline), "Audit nonexercise evidence changed")
    require(verification["three_controls_verified"] is True, "Audit verification absent")
    projected = audit_adapter.project(report, retained)
    stored = load(AUDIT / "report/assessment.json")
    require(
        encoded(projected["items"][REVISION]) == encoded(stored["report"]["items"][REVISION]),
        "Audit row differs from owning projection",
    )
    projected["measurements"]["audit_revision_closure"]["inventory_ref"]["path"] = str(AUDIT / "exercise/audit.json")
    return projected


def native_followup() -> dict[str, Any]:
    """Read the fixed reviewed companion through its owning offline validator."""
    directory = RUNS / "native-closure-current"
    names = ("candidate_boundary", "closure", "admission", "observation_reconciliation")
    buffers = {}
    for name in names:
        _, relative, expected = support.REVIEWED[name]
        path = directory / relative
        buffers[name] = (path, pin(path, expected))
    pin(RUNS / "upper-execution-current/public/independent_audit.py", support.ORACLE_SHA)
    report_path = directory / "ordinary-report/assessment.json"
    pin(report_path, support.REVIEWED_REPORTS["native"])
    previous_path = sys.path[:]
    previous_modules = {name: sys.modules.get(name) for name in names}
    try:
        modules = {}
        for name, (path, _) in buffers.items():
            module = ModuleType(name)
            module.__file__ = str(path)
            modules[name] = sys.modules[name] = module
        for name, (path, source) in buffers.items():
            exec(compile(source, str(path), "exec"), modules[name].__dict__)
        require(modules["closure"].REPO == HERE.parents[1], "follow-up checkout differs")
        value = modules["observation_reconciliation"].reconcile()
        require(
            encoded(value) == encoded(load(report_path)["report"]["measurements"]["external_native_closure"]),
            "follow-up projection changed",
        )
        for name, (path, source) in buffers.items():
            require(raw(path) == source, "follow-up implementation changed")
        originals = [r for r in value["rows"] if r["subject"] == "retained-repair"]
        require(
            inventory(directory / "tasks/retained-repair") == inventory(ROOT / "runtime/task"),
            "follow-up original task differs",
        )
        require(
            {r["policy"] for r in originals} == {"duplicate", "foreign", "symlink", "empty"},
            "original follow-up coverage differs",
        )
        require(
            len(originals) == 4
            and all(
                r["status"] == "healthy" and r["task_credit"] == 0 and r["checks"]["health"] == "ok" for r in originals
            ),
            "original follow-up outcome differs",
        )
        return {
            "validated": True,
            "report": str(report_path),
            "report_sha256": support.REVIEWED_REPORTS["native"],
            "projection_sha256": digest(value),
            "original_controls": [{"id": r["id"], "task_credit": r["task_credit"], "raw": r["raw"]} for r in originals],
            "scope": value["scope"],
            "limitations": value["limitations"],
        }
    finally:
        sys.path[:] = previous_path
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def validate(root: Path, selection: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    packets = packet_inputs()
    check_population(root, selection, packets)
    support.closure_check(ROOT / "source-freeze.json")
    upper_environment = importlib.import_module("rubric_upper_environment")
    upper_ac = importlib.import_module("upper_anticheat_contract")
    from rubric_combined import registry_for

    for owner, directory in ((upper_environment, ROOT / "upper-environment"), (upper_ac, ROOT / "upper-ac")):
        index_bytes = raw(directory / "native-index.json")
        with registry_for(index_bytes):
            actual = owner.validate(directory, json.loads(index_bytes))
        require(encoded(actual) == encoded(load(directory / "summary.json")), "owning upper projection changed")
    expected = load(Path(packets["scope"]["base_report"]))["report"]
    # Caller supplies the freshly run ordinary assessment, including owning native
    # validators, current exact source review and revocation readback.
    require(encoded(report) == encoded(expected), "ordinary owning assessment changed")
    observations = {"native": outcome_inventory(packets), "trace": trace_inventory()}
    history = load(HISTORY / "history-ledger.json")
    counts = dict(Counter(r["state"] for r in history["refresh_history"]))
    require(counts == history["refresh_counts"], "history refresh count differs")
    observations["history"] = {
        "refresh_counts": counts,
        "refresh_total": sum(counts.values()),
        "completeness": history["completeness"],
        "unknowns": history["unrecoverable_or_outside_scope"],
        "confirmation_contract": history["confirmation_contract_proposal"],
        "discrepancy": "Retained manifest has 18 unchanged entries; earlier prose said 20. No invented entries; full project history remains unknown.",
    }
    queries = requests(packets, observations)
    receipts = {r["question_id"]: read_decision(r, root, selection) for r in queries}
    try:
        followup = native_followup()
    except ValueError as exc:
        followup = {
            "validated": False,
            "reason": str(exc),
            "report": str(RUNS / "native-closure-current/ordinary-report/assessment.json"),
        }
    return {
        "schema": "current-original-finish-validation-v1",
        "root": str(root),
        "selection_sha256": digest(selection),
        "report_sha256": digest(report),
        "packet_pins": {str(p): h for p, h in PINS.items()},
        "observations": observations,
        "requests": queries,
        "receipts": receipts,
        "native_followup": followup,
        "audit_report": audit_result(report, root, selection),
    }


SUITE_NATIVE = RUNS / "suite-native-recovery-current/native-run"
SUITE_PINS = {
    SUITE_NATIVE / "summary.json": "de12c4e82747bc76c7ca1fad6c15dc9b75a0567ae6f87c5e0e37b826db11d27b",
    SUITE_NATIVE / "image-manifest.json": "c3d8f9dbabdc40b0c449fb448f16738469e4e38d3b18deceba22bbfe5d75b519",
    SUITE_NATIVE.parent / "code-hashes.json": "cd6fea62152e2f28c323a2fea101d0b3e76678a420de59e10c731fb3b0df4bfd",
    RUNS
    / "suite-native-viability-current/source-freeze.json": "cd01ee7f98e1f73795dad3fc031a8a38d1908b8de359fc2998a0555fa2499c94",
}


def suite_sources() -> dict[str, Any]:
    for path, expected in SUITE_PINS.items():
        pin(path, expected)
    freeze = load(RUNS / "suite-native-viability-current/source-freeze.json")
    for name, entry in freeze["files"].items():
        path = Path(name)
        pin(path, entry["sha256"])
        require(path.stat().st_mode & 0o777 == entry["mode"], "suite source mode changed")
    package = load(SUITE_NATIVE.parent / "code-hashes.json")
    for name, expected in package.items():
        require(Path(name).name == name, "unsafe suite adapter path")
        pin(SUITE_NATIVE.parent / name, expected)
    prepared = RUNS / "suite-build-finish-current"
    manifest = load(prepared / "manifest.json")
    for family, entry in manifest["tasks"].items():
        require(family in {"interval-reservation", "dependency-release"}, "wrong suite family")
        task = prepared / "tasks" / family
        expected = {
            str(Path(n).relative_to(task)): v for n, v in freeze["files"].items() if Path(n).is_relative_to(task)
        }
        require(inventory(task) == expected, "suite task inventory changed")
        template = HERE / "examples/finish-suite" / family
        observed = inventory(template)
        wanted = {
            n.removeprefix(family + "/"): v
            for n, v in manifest["bindings"]["templates"].items()
            if n.startswith(family + "/")
        }
        require(
            {n: {"sha256": v["sha256"], "executable": bool(v["mode"] & 0o111)} for n, v in observed.items()} == wanted,
            "suite template inventory changed",
        )
    return manifest


def suite_trial(
    root: Path, episode: dict[str, Any], receipt: dict[str, Any], attempt: dict[str, Any], images: dict[str, Any]
) -> dict[str, Any]:
    family = episode["family"]
    require(
        family in {"interval-reservation", "dependency-release"} and episode["id"] == family + "/" + episode["control"],
        "wrong suite family",
    )
    require(receipt["episode"] == attempt["episode"] == episode["id"], "wrong suite family/control binding")
    require(
        receipt["readback_verified"] is True
        and receipt["cleanup"] is True
        and receipt["worker_returncode"] == 0
        and receipt["error"] is None
        and receipt["exception_type"] is None
        and attempt["status"] == "closed",
        "suite Trial incomplete",
    )
    obs = receipt["observation"]
    require(obs["episode"] == episode["id"] and obs["project"] == attempt["project"], "wrong suite observation")
    project = attempt["project"]
    require(Path(project).name == project and project == "finish-" + attempt["token"], "unsafe suite project")
    trial = root / "trials" / project
    observed = inventory(trial)
    require({n: v["sha256"] for n, v in observed.items()} == obs["inventory"], "suite raw inventory changed")
    require(load(root / ("readback-" + attempt["token"] + ".json")) == obs, "suite readback differs")
    packet = load(root / ("packet-" + attempt["token"] + ".json"))
    require(
        encoded(packet)
        == encoded(
            {"episode": episode, "images": images[family], "project": project, "trials_dir": str(root / "trials")}
        ),
        "suite packet family/image differs",
    )
    native = load(trial / "result.json")
    from harbor.models.trial.config import TrialConfig

    config_module = sys.modules[TrialConfig.__module__]
    assert config_module.__file__ is not None
    config_source = Path(config_module.__file__)
    frozen_config = RUNS / "suite-native-viability-current/source-freeze.json"
    config_pin = next(
        v["sha256"] for n, v in load(frozen_config)["files"].items() if n.endswith("/harbor/models/trial/config.py")
    )
    pin(config_source, config_pin)
    config = TrialConfig.model_validate(load(trial / "config.json")).model_dump(mode="json")
    require(encoded(config) == encoded(native["config"]), "suite configuration differs")
    task = RUNS / "suite-build-finish-current" / episode["task"]
    require(
        episode["task"] == "tasks/" + family
        and native["task_name"] == family
        and native["task_id"] == {"path": str(task)}
        and config["task"]["path"] == str(task),
        "wrong suite native family",
    )
    require(
        native["id"] == obs["trial_uuid"] and native["trial_name"] == project and native["trial_uri"] == trial.as_uri(),
        "suite Trial identity differs",
    )
    require(
        native["exception_info"] is None
        and native["finished_at"]
        and native["agent_execution"]["finished_at"]
        and native["verifier"]["finished_at"],
        "suite execution incomplete",
    )
    require(
        config["environment"]["import_path"] == "harbor_adapter:OwnedDocker"
        and config["agent"]["import_path"] == "harbor_adapter:CandidateAgent"
        and config["agent"]["model_name"] is None
        and config["verifier"]["disable"] is False,
        "suite declared adapter differs",
    )
    require(
        encoded(config["agent"]["kwargs"]) == encoded({"candidate": episode["candidate"]}), "suite candidate differs"
    )
    checks = load(trial / "verifier/checks.json")
    case_bytes = raw(task / "environment/case.json")
    scorer_path = HERE / "examples/finish-suite" / family / "tests/verify.py"
    scorer = ModuleType("suite_viability_scorer")
    exec(compile(raw(scorer_path), str(scorer_path), "exec"), scorer.__dict__)
    expected = scorer.evaluate(json.loads(case_bytes), episode["candidate"])
    expected.update(input_sha256=hashlib.sha256(case_bytes).hexdigest(), candidate_sha256=episode["candidate_sha256"])
    require(encoded(checks) == encoded(obs["checks"]) == encoded(expected), "suite per-check reconstruction differs")
    reward = float(raw(trial / "verifier/reward.txt"))
    require(not (trial / "verifier/reward.json").exists(), "suite unexpected priority reward")
    require(
        checks["health"] == "ok"
        and native["verifier_result"]["rewards"] == {"reward": reward}
        and reward == obs["reward"] == checks["total_credit"] / checks["max_credit"],
        "suite native reward differs",
    )
    require(
        obs["resources"] == attempt["resources"]
        and obs["task_ids"] == attempt["task_ids"]
        and len(obs["task_ids"]) == 1,
        "suite resource identity differs",
    )
    task_id = obs["task_ids"][0]
    inspection = attempt["inspections"][task_id]
    require(
        inspection["Id"] == task_id
        and inspection["Image"] == images[family]["task"]
        and inspection["Config"]["Labels"]["finish.readiness.attempt"] == attempt["token"],
        "wrong suite family image",
    )
    return {
        "episode": episode["id"],
        "family": family,
        "control": episode["control"],
        "task": str(task),
        "task_checksum": native["task_checksum"],
        "trial_uuid": native["id"],
        "task_container_id": task_id,
        "images": images[family],
        "checks": checks,
        "task_credit": checks["total_credit"],
        "max_credit": checks["max_credit"],
        "reward": reward,
        "raw_file_count": len(observed),
        "raw_result": str(trial / "result.json"),
        "raw_result_sha256": obs["inventory"]["result.json"],
    }


def validate_suite_native(root: Path) -> dict[str, Any]:
    require(root == SUITE_NATIVE, "unsupported suite native root")
    manifest = suite_sources()
    summary = load(root / "summary.json")
    require(
        summary["status"] == "completed"
        and summary["error"] is None
        and summary["preparation_cleanup"] is True
        and summary["source_unchanged"] is True,
        "suite native batch incomplete",
    )
    batch = summary["native_trials"]
    episodes = manifest["episodes"]
    require(
        len(episodes) == len(batch["receipts"]) == len(batch["attempts"]) == 8
        and batch["exceptions"] == [None] * 8
        and batch["native_execution"] is True,
        "suite native denominator differs",
    )
    images = load(root / "image-manifest.json")["images"]
    require(
        set(images) == {"interval-reservation", "dependency-release"}
        and len({v["task"] for v in images.values()}) == 2,
        "wrong suite image families",
    )
    for family, values in images.items():
        image = summary["images_retained"][family]
        require(
            image["status"] == "verified" and image["id"] == image["inspection"]["Id"] == values["task"],
            "suite image not verified",
        )
        require(
            image["probe"]["case_sha256"]
            == hashlib.sha256(
                raw(RUNS / "suite-build-finish-current/tasks" / family / "environment/case.json")
            ).hexdigest(),
            "suite image input differs",
        )
    rows = [
        suite_trial(root, e, r, a, images)
        for e, r, a in zip(episodes, batch["receipts"], batch["attempts"], strict=True)
    ]
    require(
        len({r["trial_uuid"] for r in rows}) == len({r["task_container_id"] for r in rows}) == 8,
        "suite duplicate Trial/resource",
    )
    require(
        {r["episode"] for r in rows}
        == {f + "/" + c for f in images for c in ("valid", "invalid", "partial", "missing")},
        "suite family/control coverage differs",
    )
    suite_sources()
    return {
        "schema": "current-finish-suite-native-v1",
        "scope": "S/new-family-viability; not original B qualification",
        "scoring_path": "Full Harbor Trial through declared harbor_adapter:OwnedDocker and CandidateAgent; not stock provider path",
        "root": str(root),
        "pins": {str(p): h for p, h in SUITE_PINS.items()},
        "adapter_sha256": load(root.parent / "code-hashes.json")["harbor_adapter.py"],
        "trial_count": len(rows),
        "raw_file_count": sum(r["raw_file_count"] for r in rows),
        "elapsed_sec": batch["elapsed_sec"],
        "rows": rows,
        "rubric_scores": None,
        "independent_review": False,
        "held_out": False,
        "authority_transferred": False,
        "new_execution_authorized": False,
        "limits": [
            "Exposed scripted viability controls only; not model performance, independent review or held-out counterexamples.",
            "Closed viability8 batch only; no renewed execution, image, cleanup or retention budget.",
            "Original B measurements, approvals and lower catalog remain separate; no score transfer.",
        ],
    }


def apply_suite_native(report: dict[str, Any], value: dict[str, Any], reference: Path | None = None) -> dict[str, Any]:
    result = deepcopy(report)
    require("suite_native_viability" not in result["measurements"], "suite already attached")
    if reference is not None:
        require(encoded(load(reference)) == encoded(value), "suite linked artifact differs")
        result["measurements"]["suite_native_viability"] = {
            "path": str(reference),
            "sha256": hashlib.sha256(raw(reference)).hexdigest(),
            "scope": "S/new-family viability only; no score/authority transfer",
            "trial_count": value["trial_count"],
        }
    else:
        result["measurements"]["suite_native_viability"] = deepcopy(value)
    require(encoded(validate_suite_native(Path(value["root"]))) == encoded(value), "suite changed during projection")
    return result


@contextlib.contextmanager
def suite_original_context():
    """Reuse the unchanged follow-up validator beside additive orchestration."""
    import ast

    directory = RUNS / "containment-admission-finish-current"
    binding_path = directory / "binding-v4.json"
    binding = json.loads(pin(binding_path, "69c87904b3300e6ec7d47f9aa1a445c80f66bfa7c081dc11b00e5d9c9cc4e74a"))
    path = directory / "source_reconciliation_v4.py"
    source = pin(path, binding["files"][str(path)]["sha256"])
    module: Any = ModuleType("suite_original_source_reconciliation")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    original_paths = {HERE / n for n in ("current_rubric_finish.py", "current_rubric_completion.py")}
    current = {
        str(p): {"sha256": hashlib.sha256(raw(p)).hexdigest(), "mode": p.stat().st_mode & 0o777} for p in original_paths
    }
    for p in original_paths:
        snapshot = RUNS / "suite-native-integration-current/historical-orchestration" / p.name
        historical = pin(snapshot, binding["files"][str(p)]["sha256"])
        old_tree, new_tree = ast.parse(historical), ast.parse(raw(p))
        old_functions = {
            n.name: ast.dump(n) for n in old_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        new_functions = {
            n.name: ast.dump(n) for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        exceptions = {"run", "table_hook"} if p.name == "current_rubric_finish.py" else {"main"}
        require(
            all(new_functions.get(n) == value for n, value in old_functions.items() if n not in exceptions),
            "historical semantic consumer changed",
        )

    def check_binding() -> dict[str, Any]:
        for name, entry in binding["files"].items():
            expected = current.get(name, entry)
            pin(Path(name), expected["sha256"])
            require(Path(name).stat().st_mode & 0o777 == expected["mode"], "original companion mode drift")
        require(
            str(Path(sys.executable).resolve()) == binding["interpreter"]
            and sys.dont_write_bytecode
            and os.environ.get("PYTHONDONTWRITEBYTECODE") == "1",
            "original interpreter boundary differs",
        )
        return binding

    module.check_binding = check_binding
    previous = globals()["native_followup"]
    try:
        check_binding()
        globals()["native_followup"] = module.reconciled_followup
        with module.source_imports():
            yield
        check_binding()
    finally:
        globals()["native_followup"] = previous


def approved(value: dict[str, Any], question: str, scope: str) -> bool:
    receipt = value["receipts"].get(question)
    return bool(receipt and not receipt["revoked"] and receipt["event"]["decisions"][scope]["decision"] == "approve")


def project(report: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    require(digest(report) == value["report_sha256"], "projection report differs")
    output = deepcopy(value["audit_report"])
    summary = {k: deepcopy(v) for k, v in value.items() if k != "audit_report"}
    summary["requests"] = [
        {
            "question_id": r["question_id"],
            "binding": r["binding"],
            "scopes": r["scopes"],
            "request_file": "../requests.json",
        }
        for r in value["requests"]
    ]
    summary["receipts"] = deepcopy(value["receipts"])
    for receipt in summary["receipts"].values():
        if receipt is not None and "request" in receipt["event"]:
            request = receipt["event"].pop("request")
            receipt["event"]["request_ref"] = {"binding": request["binding"], "path": "../requests.json"}
    output["measurements"]["original_finish"] = summary
    obs = value["observations"]
    # Every positive anchor is criterion-specific. No target vector is consumed.
    updates = {
        "AUDIT-03": (
            "OBS-AUDIT03",
            all(g["per_check_total_match"] for g in obs["native"]["classes"]),
            "Independent held-out original-path attacks remain absent.",
        ),
        "ENV-03-S01": (
            "OBS-ENV03",
            True,
            "Serial fresh-container scope only; independent parallel, delayed-effect and held-out ordering remain absent.",
        ),
        "TRACE-04-A": (
            "OBS-TRACE04A",
            obs["trace"]["eligible_count"] > 0,
            "Independent labels and exact-release audit remain absent.",
        ),
        "AUDIT-05-B": (
            "OBH-Q1",
            obs["native"]["sensitivity"]["unexposed_subject_count"] == 0,
            "Independent provenance is not established; retrospective sensitivity is not prospective preregistration.",
        ),
        "AUDIT-05-S03": (
            "OBH-Q2",
            bool(obs["history"]["confirmation_contract"]),
            "Independent confirmation, feedback-boundary review and a retained gain remain absent. Full project history is not recovered.",
        ),
    }
    for ident in SCOPED:
        row = output["items"][ident]
        request = next(r for r in value["requests"] if ident in r["scopes"])
        receipt = value["receipts"][request["question_id"]]
        state = (
            "unanswered"
            if receipt is None
            else "revoked"
            if receipt["revoked"]
            else receipt["event"]["decisions"][ident]["decision"]
        )
        gap = f"{request['question_id']}: {state}; exact request {request['binding']}. " + request["scopes"][ident]
        row["evidence_refs"].append({"measurement": "original_finish", "question_id": request["question_id"]})
        row["remaining_gaps"].append(gap)
        row.setdefault("approval_refs", []).append(
            {"measurement": "original_finish.receipts", "question_id": request["question_id"], "state": state}
        )
        if ident == "AUDIT-04-B":
            if approved(value, "OBS-AUDIT04B", ident) and value.get("native_followup", {}).get("validated") is True:
                affected = load(SCOPE / "AUDIT-04-B-affected-inventory.json")
                require(affected["original_confirmed_affected"] == [], "confirmed original mechanism needs containment")
                reason = "Owner-confirmed empty affected inventory for the exact original task, with current owning follow-up admission. Conditional only; not universal exploit absence, score 3 or repair benefit."
                row.update(
                    score=None,
                    assessment_state="NOT_APPLICABLE",
                    status="NOT_APPLICABLE",
                    execution_state="NOT_APPLICABLE",
                    applicability_reason=reason,
                    remaining_gaps=[],
                )
                row["evidence_refs"].append({"measurement": "original_finish.native_followup"})
                for check in row["check_results"]:
                    require(check["outcome"] not in {"FAIL", "ERROR"}, "failed containment check cannot become N/A")
                    check.update(
                        outcome="NOT_APPLICABLE", remaining_gap=None, next_action=None, applicability_reason=reason
                    )
                row.setdefault("limitations", []).append(reason)
            else:
                row["remaining_gaps"].append(
                    "Conditional N/A requires both the exact owner-confirmed empty affected inventory and current owning installed-Verifier follow-up admission. "
                    + value.get("native_followup", {}).get("reason", "Follow-up admission absent.")
                )
            continue
        question, automatic, upper_gap = updates[ident]
        accepted = (
            approved(value, question, ident)
            and automatic
            and not any(c["outcome"] in {"FAIL", "ERROR"} for c in row["check_results"])
        )
        # Preserve measured defects and their evidence. Partial source review can
        # support level 1 without inventing semantic owner approval.
        if row.get("score") == 0 or any(c["outcome"] in {"FAIL", "ERROR"} for c in row["check_results"]):
            continue
        if ident in {"TRACE-04-A", "AUDIT-05-S03"} and row.get("score") is None and automatic:
            row.update(
                score=1,
                assessment_state="ASSESSED",
                status="ASSESSED",
                evidence_strength="E1",
                execution_state="PARTIAL",
            )
        if accepted:
            row.update(score=2, assessment_state="ASSESSED", status="ASSESSED", execution_state="COMPLETED")
            # Keep original observations in place; only replace exact absence gaps
            # at the check seam where this packet supplies owning evidence.
            for index, check in enumerate(row["check_results"]):
                if ident in {"AUDIT-05-S03", "AUDIT-05-B"} and index == 2:
                    continue
                if check["outcome"] in {"FAIL", "ERROR"}:
                    continue
                check.update(outcome="PASS", remaining_gap=None, next_action=None)
                check["evidence_refs"].append({"measurement": "original_finish", "question_id": question})
            if ident == "ENV-03-S01":
                row["check_results"][-1].update(
                    outcome="NOT_APPLICABLE",
                    remaining_gap=None,
                    applicability_reason="No residual deterministic outcome question in the exact accepted serial scope.",
                )
            if ident == "AUDIT-05-S03":
                row["check_results"][-1].update(
                    outcome="NOT_RUN",
                    remaining_gap="No untouched confirmation or reuse experiment executed; exploratory-only conclusion.",
                )
            if ident == "AUDIT-05-B":
                row["check_results"][-1].update(
                    outcome="NOT_RUN",
                    remaining_gap="Retrospective sensitivity is measured, but a predeclared exclusion alternative has not been separately qualified.",
                )
            row["remaining_gaps"] = [upper_gap] + [
                c["remaining_gap"] for c in row["check_results"] if c.get("remaining_gap")
            ]
            if any(c["outcome"] == "NOT_RUN" for c in row["check_results"]):
                row["execution_state"] = "PARTIAL"
        row.setdefault("limitations", []).append(upper_gap)
    return output


def apply(report: dict[str, Any], value: dict[str, Any], *, root: Path, selection: dict[str, Any]) -> dict[str, Any]:
    result = project(report, value)
    # Final source and receipt/revocation admission occurs AFTER projection.
    current = validate(root, selection, report)
    require(encoded(current) == encoded(value), "finish admission changed during projection")
    require(
        all(result["items"][k] == row for k, row in report["items"].items() if k not in {*SCOPED, REVISION}),
        "unchanged neighbor modified",
    )
    return result


CONTRACT_REVIEW = RUNS / "finish-contract-review-current"
CONTRACT_QUESTION_SHA = "aa58a7ea0fc0bfd17bf9c6fec8ee989cd957abfad3f094a457e9c23e3b051e69"
CONTRACT_ID = "task-contract.scoring-authority"
CONTRACT_METHOD = CONTRACT_ID + ".corroboration-method"
CONTRACT_OWNER_QUESTION = (
    "For the original refund simulation only, do you accept the exact review packet "
    "32dcb90ec1d4ea26ef384972d7079e29d9430aff9e44389b9442182294ff361c "
    "as an alternative scoring-authority corroboration method, with local retention until revoked? "
    "The reviewer separately derived the full contract and recomputed retained results, but used the same "
    "model/public specification and read the accepted crosswalk before freezing its comparisons. "
    "Acceptance retains those limitations and dissent; it does not approve a score, blind discovery, "
    "held-out evidence, companion-task qualification, or new execution. Proposed rationale: the separate "
    "derivation and per-check comparison provide bounded corroboration of this deterministic contract, "
    "not independent case authorship."
)


def contract_review_sources(directory: Path) -> dict[str, Any]:
    require(directory == CONTRACT_REVIEW, "unsupported contract review packet")
    packet = json.loads(pin(directory / "method-qualification-request.json", CONTRACT_QUESTION_SHA))
    for name, expected in packet["evidence_files_sha256"].items():
        require(Path(name).name == name, "unsafe contract review component")
        pin(directory / name, expected)
    manifest = load(directory / "input-manifest.json")
    for name, entry in manifest.items():
        path = Path(name)
        pin(path, entry["sha256"])
        require(
            path.stat().st_size == entry["bytes"] and path.stat().st_mode & 0o777 == entry["mode"],
            "contract review input identity changed",
        )
    task = ROOT / "runtime/task"
    expected_task = {
        str(Path(name).relative_to(task)): {"sha256": entry["sha256"], "mode": entry["mode"]}
        for name, entry in manifest.items()
        if Path(name).is_relative_to(task)
    }
    require(inventory(task) == expected_task, "contract review task inventory changed")
    previous = load(RUNS / "parallel-source-intake/contract-request.json")
    require(previous["binding"] == packet["original_request_binding"], "original crosswalk binding differs")
    for row in previous["crosswalk"]["rows"]:
        for span in row["exact_spans"]:
            source = pin(task / span["path"], span["sha256"]).decode()
            require(
                "".join(source.splitlines(keepends=True)[span["start_line"] - 1 : span["end_line"]]) == span["quote"],
                "contract review citation changed",
            )
    return packet


def contract_review_request(directory: Path = CONTRACT_REVIEW) -> dict[str, Any]:
    packet = contract_review_sources(directory)
    return {
        "schema": "current-original-contract-method-request-v1",
        "question_id": "B-CONTRACT-CORROBORATION",
        # This is the exact packet identity presented to the owner, not a new scope.
        "binding": packet["packet_binding"],
        "packet": str(directory / "method-qualification-request.json"),
        "packet_sha256": CONTRACT_QUESTION_SHA,
        "scopes": {CONTRACT_METHOD: CONTRACT_OWNER_QUESTION},
        "question": CONTRACT_OWNER_QUESTION,
        "subject": packet["subject"],
        "retention_scope": "Local assessment-operator retention until revoked, exact B method only.",
        "exclusions": deepcopy(packet["exclusions"]),
        "known_dissent": deepcopy(packet["known_dissent"]),
        "boundary": OPERATOR_BOUNDARY,
    }


def validate_contract_request(request: dict[str, Any], root: Path, selection: dict[str, Any]) -> None:
    check_population(root, selection, packet_inputs())
    require(
        encoded(request) == encoded(contract_review_request()), "contract method request is not exact current packet"
    )


def record_contract_decision(
    request: dict[str, Any],
    root: Path,
    selection: dict[str, Any],
    observed_decision: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> Path:
    """Route exact method intake through the existing record/read/revoke boundary."""
    validate_contract_request(request, root, selection)
    previous = globals()["current_request"]
    try:
        globals()["current_request"] = validate_contract_request
        return record_decision(request, root, selection, observed_decision)
    finally:
        globals()["current_request"] = previous


def contract_comparison(directory: Path) -> dict[str, Any]:
    rows = load(directory / "original-native-comparison.json")
    native_rows = load(SCOPE / "native-outcomes.json")["rows"]
    require(len({r["trial_uuid"] for r in rows}) == len(rows) == len(native_rows), "contract review population differs")
    indexed = {r["trial_uuid"]: r for r in native_rows}
    healthy = 0
    original_healthy = 0
    errors = 0
    for row in rows:
        native = indexed[row["trial_uuid"]]
        require(
            row["result"] == native["result_path"]
            and row["checks_path"] == native["checks_path"]
            and row["population"] == native["population"],
            "contract review native identity differs",
        )
        result = json.loads(pin(Path(row["result"]), native["result_sha256"]))
        actual = load(Path(row["checks_path"]))
        require(encoded(actual) == encoded(row["actual"]) == encoded(native["actual"]), "contract review checks differ")
        require(
            encoded(row["reward"]) == encoded((result.get("verifier_result") or {}).get("rewards"))
            and encoded(row["exception"]) == encoded(result.get("exception_info")),
            "contract review health differs",
        )
        derived = row["independently_derived"]
        if actual["health"] != "ok":
            require(
                derived is None and row["reward"] is None and actual["total_credit"] is None,
                "contract review error counted as corroboration",
            )
            errors += 1
            continue
        require(
            derived is not None and row["differences"] == {} and row["reward_match"] is True,
            "contract review comparison incomplete",
        )
        require(
            all(
                encoded(derived[key]) == encoded(actual[key])
                for key in ("checks", "raw_credit", "total_credit", "max_credit", "accepted")
            )
            and derived["replayed_state_matches"] is True,
            "contract review derivation differs",
        )
        require(
            row["reward"]["reward"] == derived["total_credit"] / derived["max_credit"], "contract review reward differs"
        )
        healthy += 1
        original_healthy += row["population"] == "original-Harbor"
    verification = load(directory / "verification.json")
    require(
        healthy == verification["original_derived"]
        and errors == verification["original_errors"]
        and verification["original_mismatches"] == verification["original_missing_snapshot"] == []
        and verification["crosswalk_citation_failures"] == [],
        "contract review verification differs",
    )
    return {
        "original_healthy_comparisons": original_healthy,
        "original_operational_errors": errors,
        "separate_zero_balance_comparisons": healthy - original_healthy,
        "crosswalk_groups": verification["accepted_crosswalk_groups"],
        "cited_spans": verification["crosswalk_citation_count"],
        "comparisons": str(directory / "original-native-comparison.json"),
        "source_derivation": "Retained separately implemented semantic review, not a newly executed independent experiment.",
    }


def validate_contract_review(
    directory: Path, root: Path, selection: dict[str, Any], report: dict[str, Any]
) -> dict[str, Any]:
    request = contract_review_request(directory)
    check_population(root, selection, packet_inputs())
    comparison = contract_comparison(directory)
    owner = report["measurements"]["scoped_reviews"]["contract"]
    prior_receipt = owner["receipt"]
    old_request = load(RUNS / "parallel-source-intake/contract-request.json")
    from rubric_general_review import read as read_contract

    current_owner = read_contract(ROOT / "runtime/task", kind="contract")
    require(
        current_owner is not None
        and prior_receipt is not None
        and current_owner["digest"] == prior_receipt["digest"]
        and current_owner["event"]["request"] == old_request
        and current_owner["revoked"] is prior_receipt["revoked"],
        "contract lower-anchor owner readback differs",
    )
    assert current_owner is not None
    row = report["items"][CONTRACT_ID]
    automatic = owner["automatic"][CONTRACT_ID]
    lower = (
        row["score"] == 2
        and row["assessment_state"] == "ASSESSED"
        and {c["check_id"] for c in row["check_results"]}
        == {CONTRACT_ID + suffix for suffix in (".human-review", ".llm-judge", ".deterministic")}
        and all(c["outcome"] == "PASS" for c in row["check_results"])
        and automatic["ready"] is True
        and automatic["controls_match"] is True
        and automatic["coverage"]["supported"] == automatic["coverage"]["total"] == comparison["crosswalk_groups"]
        and current_owner["revoked"] is False
        and current_owner["event"]["decisions"][CONTRACT_ID]["decision"] == "approve"
    )
    receipt = read_decision(request, root, selection)
    qualified = bool(
        receipt and not receipt["revoked"] and receipt["event"]["decisions"][CONTRACT_METHOD]["decision"] == "approve"
    )
    return {
        "schema": "current-original-contract-corroboration-v1",
        "request": request,
        "receipt": receipt,
        "comparison": comparison,
        "input_report_sha256": digest(report),
        "original_source_inspection": deepcopy(row.get("source_inspection")),
        "lower_anchor_satisfied": lower,
        "method_qualified": qualified,
        "promotion_ready": lower and qualified,
        "reviewer_kind": "agent",
        "independence": "Same-model, nonblind, shared public specification, post-crosswalk comparison freeze; no independent case authorship.",
        "policy_basis": {
            "anchor": "Independent sources and adjudicators corroborate the material reference and scoring contract.",
            "alternative": "A qualified alternative may replace an unavailable method, with rationale retained.",
            "scope": "Owner-qualified bounded corroboration alternative only; literal independence is not asserted.",
        },
        "findings": deepcopy(request["known_dissent"]),
        "source_report": str(directory / "REPORT.md"),
        "packet_evidence": load(directory / "method-qualification-request.json")["evidence_files_sha256"],
    }


def project_contract_review(report: dict[str, Any], value: dict[str, Any], reference: Path) -> dict[str, Any]:
    require(digest(report) == value["input_report_sha256"], "contract projection input differs")
    require(encoded(load(reference)) == encoded(value), "contract linked artifact differs")
    output = deepcopy(report)
    require("contract_corroboration" not in output["measurements"], "contract review already attached")
    output["measurements"]["contract_corroboration"] = {
        "path": str(reference),
        "sha256": hashlib.sha256(raw(reference)).hexdigest(),
        "binding": value["request"]["binding"],
        "method_qualified": value["method_qualified"],
        "scope": "Original B scoring-authority only; same-model/nonblind/post-crosswalk exposure retained.",
        "findings": deepcopy(value["findings"]),
    }
    row = output["items"][CONTRACT_ID]
    require(
        encoded(row.get("source_inspection")) == encoded(value["original_source_inspection"]),
        "contract source inspection differs",
    )
    row["source_inspection"] = {
        "measurement": "contract_corroboration",
        "pointer": "/original_source_inspection",
        "note": "Unchanged source inspection linked once; historical independence labels are not current qualification.",
    }
    row["evidence_refs"].append({"measurement": "contract_corroboration", "binding": value["request"]["binding"]})
    row.setdefault("limitations", []).append(value["independence"])
    row.setdefault("approval_refs", []).append(
        {"measurement": "contract_corroboration", "method_qualified": value["method_qualified"]}
    )
    if value["promotion_ready"]:
        require(
            value["lower_anchor_satisfied"]
            and value["method_qualified"]
            and row["score"] == 2
            and all(c["outcome"] == "PASS" for c in row["check_results"]),
            "contract promotion predicates absent",
        )
        row.update(score=3, evidence_strength="E3")
        # Preserve historical absence prose explicitly rather than deleting evidence.
        row["historical_pre_corroboration_gaps"] = row["remaining_gaps"]
        row["remaining_gaps"] = []
        if "upper_anchor_companion" in row:
            row["historical_pre_corroboration_companion"] = row.pop("upper_anchor_companion")
        row["qualified_corroboration"] = {
            "measurement": "contract_corroboration",
            "basis": value["policy_basis"],
            "qualification": "Exact owner-accepted alternative, not newly independent sources or case authorship.",
        }
    else:
        row["remaining_gaps"].append(
            "Completed nonblind contract corroboration is attached. Exact method decision/retention is pending, "
            "rejected or revoked, or lower-anchor predicates are unmet; no score promotion."
        )
    return output


def apply_contract_review(
    report: dict[str, Any], value: dict[str, Any], reference: Path, *, root: Path, selection: dict[str, Any]
) -> dict[str, Any]:
    result = project_contract_review(report, value, reference)
    require(
        encoded(validate_contract_review(CONTRACT_REVIEW, root, selection, report)) == encoded(value),
        "contract admission changed during projection",
    )
    require(
        all(result["items"][key] == row for key, row in report["items"].items() if key != CONTRACT_ID),
        "contract changed another criterion",
    )
    return result


PROVENANCE_REVIEW = RUNS / "finish-provenance-release-review-current"
PROVENANCE_PACKET_SHA = "30f4c607ef12716ee1ee3068b2e1ec8305c2e9a0ec8bf1293da5b32a481c1958"
PROVENANCE_BINDING = "aa93cb7b326e0274e72eb4ec4e7b9e671913b1936536bae991429c00da913b0f"
PROVENANCE_BASELINE = RUNS / "finish-contract-integration-current/targeted-final/assessment.json"
PROVENANCE_BASELINE_SHA = "3bca876c3d871508dc6f8126667f7ef05da9a107d66dd7130f410c8a952cdc22"
PROVENANCE_IDS = ("AUDIT-05-B", "TRACE-04-A")
TRACE_METHOD = "TRACE-04-A.corroboration-method"
TRACE_OWNER_QUESTION = (
    "For TRACE-04-A on the original 194 private simulation projections only, accept packet "
    "aa93cb7b326e0274e72eb4ec4e7b9e671913b1936536bae991429c00da913b0f as bounded alternative "
    "corroboration, with local retention until revoked? It supplies separately performed material labels, "
    "contextual-negative/embedded-answer review and an exact restricted-release audit. The reviewer used "
    "the same model and saw prior labels/conclusions; it is not blind authorship, general secret clearance "
    "or a host-wide disclosure audit. Proposed rationale: these substantive per-material comparisons "
    "corroborate the existing category restrictions within this finite scope. Acceptance does not approve "
    "a score or change recipients/use, and permits no new release or execution."
)


def provenance_review_sources(directory: Path = PROVENANCE_REVIEW) -> dict[str, Any]:
    require(directory == PROVENANCE_REVIEW, "unsupported provenance/release packet")
    packet = json.loads(pin(directory / "packet.json", PROVENANCE_PACKET_SHA))
    require(
        digest(packet["components"]) == packet["components_sha256"] == PROVENANCE_BINDING,
        "provenance component binding differs",
    )
    require(
        len({entry["path"] for entry in packet["components"]}) == len(packet["components"]) == 15,
        "provenance component inventory differs",
    )
    for entry in packet["components"]:
        require(Path(entry["path"]).name == entry["path"], "unsafe provenance component")
        data = pin(directory / entry["path"], entry["sha256"])
        require(len(data) == entry["bytes"], "provenance component size differs")
    manifest = load(directory / "source-hashes-final.json")
    require(len(manifest["files"]) == manifest["count"], "provenance source inventory differs")
    for entry in manifest["files"]:
        path = Path(entry["path"])
        if entry["roles"] == ["shared reviewer exposure"]:
            # Skills were read as exposure history, never executed or used by the
            # comparison. Their historical hashes are not current source pins.
            require(
                path
                in {
                    Path("/Users/asutermorris/.hermes/skills") / name / "SKILL.md"
                    for name in ("rubric-mutation-reconciliation", "engineering-operating-defaults")
                },
                "unknown historical exposure source",
            )
            continue
        if path == Path(__file__).resolve():
            # This inspected, unexecuted adapter is the only moving input. Keep its
            # exact historical bytes; the new consumer gets a separate identity.
            path = RUNS / "finish-provenance-release-integration-current/historical-orchestration" / path.name
        data = pin(path, entry["sha256"])
        require(
            path.stat().st_size == entry["bytes"] and path.stat().st_mode & 0o777 == entry["mode"],
            "provenance source size/mode differs",
        )
    return packet


def provenance_review_request() -> dict[str, Any]:
    packet = provenance_review_sources()
    return {
        "schema": "current-original-trace-corroboration-request-v1",
        "question_id": "B-TRACE-CORROBORATION",
        "binding": PROVENANCE_BINDING,
        "packet": str(PROVENANCE_REVIEW / "packet.json"),
        "packet_sha256": PROVENANCE_PACKET_SHA,
        "subject": packet["subject"],
        "scopes": {TRACE_METHOD: TRACE_OWNER_QUESTION},
        "question": TRACE_OWNER_QUESTION,
        "original_review_question": packet["qualified_alternative_request"]["question"],
        "retention_scope": "Exact original 194 private simulation projections; existing local workflow until revoked.",
        "boundary": OPERATOR_BOUNDARY,
        "exclusions": packet["prohibited_inferences"],
    }


def validate_provenance_request(request: dict[str, Any], root: Path, selection: dict[str, Any]) -> None:
    check_population(root, selection, packet_inputs())
    require(
        encoded(request) == encoded(provenance_review_request()), "trace method request is not exact current packet"
    )


def record_provenance_decision(
    request: dict[str, Any],
    root: Path,
    selection: dict[str, Any],
    observed_decision: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> Path:
    validate_provenance_request(request, root, selection)
    previous = globals()["current_request"]
    try:
        globals()["current_request"] = validate_provenance_request
        return record_decision(request, root, selection, observed_decision)
    finally:
        globals()["current_request"] = previous


def provenance_comparison() -> dict[str, Any]:
    """Recompute the fixed reviewed documentary comparison, not evidence-selected code."""
    packet = provenance_review_sources()
    path = PROVENANCE_REVIEW / "review.py"
    expected = next(e["sha256"] for e in packet["components"] if e["path"] == path.name)
    source = pin(path, expected)
    module = ModuleType("admitted_provenance_release_comparison")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    previous_argv = sys.argv
    try:
        # The allowlisted main has no writes without --write; never inherit argv.
        sys.argv = [str(path)]
        with contextlib.redirect_stdout(io.StringIO()):
            status = module.main()
    finally:
        sys.argv = previous_argv
    saved = load(PROVENANCE_REVIEW / "verification-final.json")
    require(status == 0 and saved["failed_comparisons"] == [], "provenance substantive comparison failed")
    require(encoded(module.COMPARISONS) == encoded(saved["comparisons"]), "provenance comparison results differ")
    require(len(module.COMPARISONS) == saved["comparison_count"], "provenance comparison count differs")
    labels = load(PROVENANCE_REVIEW / "trace-label-comparison-final.json")
    units = load(SCOPE / "TRACE-04-A-scope.json")["eligible_material"]
    require(len(labels["labels"]) == labels["count"] == len(units), "trace label coverage differs")
    for index, (label, unit) in enumerate(zip(labels["labels"], units, strict=True)):
        require(
            label["unit"] == index
            and all(
                label[k] == unit[k]
                for k in ("source", "pointer", "source_sha256", "material_sha256", "bytes", "channel")
            ),
            "trace label material binding differs",
        )
        require(
            label["unknown_vocabulary"] == [] and bool(label["reviewer_labels"]), "trace contextual review incomplete"
        )
    require(saved["release_audit"]["material_manifest_sha256"] == digest(units), "release material digest differs")
    return {
        "comparison_count": saved["comparison_count"],
        "failed_comparisons": saved["failed_comparisons"],
        "release_audit": saved["release_audit"],
        "sensitivity": {k: v for k, v in saved["sensitivity"].items() if k != "native_records"},
        "exposure": saved["exposure"],
        "labels_ref": str(PROVENANCE_REVIEW / "trace-label-comparison-final.json"),
        "verification_ref": str(PROVENANCE_REVIEW / "verification-final.json"),
        "initial_failure_ref": str(PROVENANCE_REVIEW / "verification.json"),
        "initial_failures": load(PROVENANCE_REVIEW / "verification.json")["failed_comparisons"],
        "current_consumer_disagreements": saved["current_consumer_disagreements"],
    }


def validate_provenance_review(
    directory: Path, root: Path, selection: dict[str, Any], report: dict[str, Any]
) -> dict[str, Any]:
    packet = provenance_review_sources(directory)
    packets = packet_inputs()
    check_population(root, selection, packets)
    require(
        packet["subject"] in load(HISTORY / "arithmetic.json")["sensitivity"]["original_subject_ids"],
        "review subject differs",
    )
    original = report["measurements"]["original_finish"]
    native, trace = outcome_inventory(packets), trace_inventory()
    require(
        encoded(native) == encoded(original["observations"]["native"])
        and encoded(trace) == encoded(original["observations"]["trace"]),
        "owning original observations differ",
    )
    queries = requests(packets, original["observations"])
    receipts = {}
    for question in ("OBH-Q1", "OBS-TRACE04A"):
        request = next(r for r in queries if r["question_id"] == question)
        receipt = read_decision(request, root, selection)
        require(receipt is not None, "current original owner readback unavailable")
        assert receipt is not None
        compact = deepcopy(receipt)
        retained_request = compact["event"].pop("request")
        compact["event"]["request_ref"] = {"binding": retained_request["binding"], "path": "../requests.json"}
        require(encoded(compact) == encoded(original["receipts"][question]), "original owner readback differs")
        receipts[question] = receipt
    comparison = provenance_comparison()
    require(comparison["sensitivity"]["recomputed_original_controls"] == native["counts"], "owning sensitivity differs")
    release = comparison["release_audit"]
    require(
        release["recipients"] == trace["recipients"]
        and release["use"] == trace["use"]
        and release["material_manifest_sha256"] == trace["material_manifest_sha256"],
        "owning release scope differs",
    )
    request = provenance_review_request()
    method_receipt = read_decision(request, root, selection)
    definitions = completion.runner.canonical_definitions()
    predicates = {}
    for ident, question in zip(PROVENANCE_IDS, ("OBH-Q1", "OBS-TRACE04A"), strict=True):
        row = report["items"][ident]
        checks = row["check_results"]
        expected = definitions[ident]["checks"]
        canonical = len(checks) == len(expected) and all(
            c["check_id"] == e["id"] and all(c[k] == e[k] for k in ("action", "method", "expected_evidence"))
            for c, e in zip(checks, expected, strict=False)
        )
        receipt = receipts[question]
        lower = (
            row["score"] == 2
            and row["assessment_state"] == "ASSESSED"
            and canonical
            and all(c["outcome"] == "PASS" for c in checks[:2])
            and not any(c["outcome"] in {"FAIL", "ERROR"} for c in checks)
            and not receipt["revoked"]
            and receipt["event"]["decisions"][ident]["decision"] == "approve"
        )
        if ident == "AUDIT-05-B":
            # C03's exact stale NOT_RUN is a consumption gap, not missing execution.
            lower = (
                lower
                and checks[-1]["outcome"] == "NOT_RUN"
                and checks[-1]["remaining_gap"]
                == (
                    "Retrospective sensitivity is measured, but a predeclared exclusion alternative has not been separately qualified."
                )
            )
            qualified = (
                receipt["event"]["decisions"][ident + ".provenance-method"]["decision"] == "approve"
                and not receipt["revoked"]
            )
        else:
            lower = lower and checks[-1]["outcome"] == "PASS"
            qualified = bool(
                method_receipt
                and not method_receipt["revoked"]
                and method_receipt["event"]["decisions"][TRACE_METHOD]["decision"] == "approve"
            )
        predicates[ident] = {
            "lower_anchor_satisfied": lower,
            "method_qualified": qualified,
            "promotion_ready": lower and qualified,
            "canonical_lower": definitions[ident]["scoring"]["2"],
            "canonical_upper": definitions[ident]["scoring"]["3"],
        }
    return {
        "schema": "current-original-provenance-release-corroboration-v1",
        "input_report_sha256": digest(report),
        "request": request,
        "receipt": method_receipt,
        "original_receipts": receipts,
        "predicates": predicates,
        "comparison": comparison,
        "packet_sha256": PROVENANCE_PACKET_SHA,
        "components_sha256": PROVENANCE_BINDING,
        "historical_rows": {ident: deepcopy(report["items"][ident]) for ident in PROVENANCE_IDS},
        "independence": "Separately performed nonblind shared-model documentary review; prior labels/conclusions visible. Qualified bounded alternatives only, not blind or independent authorship.",
        "limits": packet["prohibited_inferences"],
        "policy_basis": "A qualified alternative may replace an unavailable method, with rationale retained; dependent pipeline stages are not independent corroboration.",
    }


def project_provenance_review(report: dict[str, Any], value: dict[str, Any], reference: Path) -> dict[str, Any]:
    require(digest(report) == value["input_report_sha256"], "provenance projection input differs")
    require(encoded(load(reference)) == encoded(value), "provenance linked artifact differs")
    output = deepcopy(report)
    require("provenance_release_corroboration" not in output["measurements"], "provenance review already attached")
    output["measurements"]["provenance_release_corroboration"] = {
        "path": str(reference),
        "sha256": hashlib.sha256(raw(reference)).hexdigest(),
        "binding": PROVENANCE_BINDING,
        "predicates": deepcopy(value["predicates"]),
        "scope": "Exact original exposure statement and 194 restricted private projections only.",
    }
    for ident in PROVENANCE_IDS:
        row = output["items"][ident]
        row["evidence_refs"].append({"measurement": "provenance_release_corroboration"})
        row.setdefault("limitations", []).append(value["independence"])
        if not value["predicates"][ident]["promotion_ready"]:
            row["remaining_gaps"].append(
                "Actual bounded corroboration attached; exact method qualification or lower-anchor predicates unmet. No score promotion."
            )
            continue
        require(
            row["score"] == 2 and not any(c["outcome"] in {"FAIL", "ERROR"} for c in row["check_results"]),
            "provenance promotion predicates absent",
        )
        row.update(score=3, evidence_strength="E3", execution_state="COMPLETED")
        row["historical_pre_corroboration"] = {
            "measurement": "provenance_release_corroboration",
            "pointer": "/historical_rows/" + ident,
        }
        row["remaining_gaps"] = []
        if ident == "AUDIT-05-B":
            stale = "Inspectable local exposure inventory; known broader provenance gaps are not yet bounded by a reviewed statement."
            row["findings"] = [f for f in row["findings"] if f != stale]
            row["findings"].append(
                "The exact retained OBH-Q1 bounded statement and contextual provenance/arithmetic alternative are qualified; exposure and training unknowns remain explicit."
            )
            row["check_results"][-1].update(outcome="PASS", remaining_gap=None, next_action=None)
            row["check_results"][-1]["evidence_refs"].append(
                {"measurement": "provenance_release_corroboration", "pointer": "/comparison/sensitivity"}
            )
        else:
            row["limitations"] = [
                x for x in row["limitations"] if x != "Independent labels and exact-release audit remain absent."
            ]
        row["qualified_corroboration"] = {
            "measurement": "provenance_release_corroboration",
            "basis": value["policy_basis"],
            "literal_independence": False,
        }
    return output


def apply_provenance_review(
    report: dict[str, Any], value: dict[str, Any], reference: Path, *, root: Path, selection: dict[str, Any]
) -> dict[str, Any]:
    result = project_provenance_review(report, value, reference)
    require(
        encoded(validate_provenance_review(PROVENANCE_REVIEW, root, selection, report)) == encoded(value),
        "provenance admission changed during projection",
    )
    require(
        all(result["items"][k] == row for k, row in report["items"].items() if k not in PROVENANCE_IDS)
        and all(result["measurements"][k] == v for k, v in report["measurements"].items()),
        "provenance changed neighbor or prior measurement",
    )
    return result


def run_provenance_review(args: Any) -> int:
    require(
        args.source_review_operator_store and args.source_review_operator_trust,
        "provenance review requires explicit operator store/trust",
    )
    require(args.source_report == PROVENANCE_BASELINE, "unsupported provenance baseline")
    baseline = json.loads(pin(args.source_report, PROVENANCE_BASELINE_SHA))
    out = args.output.absolute()
    require(not out.exists() and not any(p.is_symlink() for p in (out, *out.parents)), "new nonsymlink output required")
    out.mkdir(mode=0o700, parents=True)
    before = support.store_inventory(args.source_review_operator_store)
    with support.trust_environment(args.source_review_operator_store.absolute(), args.source_review_operator_trust, []):
        value = validate_provenance_review(
            args.provenance_release_review_root, ROOT, baseline["selection"], baseline["report"]
        )
        reference = out / "provenance-release-corroboration.json"
        completion.save(reference, value)
        projected = apply_provenance_review(
            baseline["report"], value, reference, root=ROOT, selection=baseline["selection"]
        )
        packet = deepcopy(baseline)
        packet["report"] = projected
        (out / "assessment.json").write_bytes(encoded(packet) + b"\n")
        markdown = audit_adapter.table.render(projected)
        (out / "results.md").write_text(markdown)
        (out / "results.html").write_text(audit_adapter.table.render_html(markdown))
        require(encoded(load(out / "assessment.json")) == encoded(packet), "provenance emitted readback differs")
        require(
            encoded(validate_provenance_review(PROVENANCE_REVIEW, ROOT, baseline["selection"], baseline["report"]))
            == encoded(value),
            "provenance final readback changed",
        )
    require(
        support.store_inventory(args.source_review_operator_store) == before, "operator store changed during projection"
    )
    verification = {
        "scope": "Targeted owning two-anchor admission/projection and ordinary renderers; no full assessment or replay.",
        "baseline": str(args.source_report),
        "baseline_sha256": PROVENANCE_BASELINE_SHA,
        "score_delta": {
            ident: [baseline["report"]["items"][ident]["score"], projected["items"][ident]["score"]]
            for ident in PROVENANCE_IDS
        },
        "unchanged_neighbors": sum(
            projected["items"][k] == v for k, v in baseline["report"]["items"].items() if k not in PROVENANCE_IDS
        ),
        "prior_measurements_unchanged": len(baseline["report"]["measurements"]),
        "status_counts": dict(
            Counter(
                "NOT_APPLICABLE"
                if row["assessment_state"] == "NOT_APPLICABLE"
                else "Unassessed"
                if row["score"] is None
                else str(row["score"])
                for row in projected["items"].values()
            )
        ),
        "source_hashes": {
            str(p): hashlib.sha256(raw(p)).hexdigest()
            for p in (
                Path(__file__),
                Path(completion.__file__),
                HERE.parents[1] / "tests/test_current_rubric_finish.py",
            )
        },
        "report_sha256": hashlib.sha256(raw(out / "assessment.json")).hexdigest(),
        "operator_store_unchanged": True,
        "native_launches": 0,
        "model_launches": 0,
        "new_owner_events": 0,
        "full_assessment": "not_run",
        "full_replay": "not_run",
        "predicates": value["predicates"],
    }
    completion.save(out / "verification.json", verification)
    completion.save(out / "trace-method-request.json", value["request"])
    print(json.dumps(verification, sort_keys=True))
    return 0


@contextlib.contextmanager
def table_hook(
    suite_root: Path | None = None,
    suite_reference: Path | None = None,
    contract_review_root: Path | None = None,
    contract_reference: Path | None = None,
):
    """Install last, after existing Audit/upper hooks; never call table recursively."""
    table: Any = audit_adapter.table
    previous = table.assess_table

    def assess(root: Path, selected: dict[str, Any], task: str | None) -> dict[str, Any]:
        report = previous(root, selected, task)
        if root != ROOT:
            return report
        packets = packet_inputs()
        if encoded(selected) != encoded(packets["selection"]):
            return report
        value = validate(root, selected, report)
        result = apply(report, value, root=root, selection=selected)
        if suite_root:
            result = apply_suite_native(result, validate_suite_native(suite_root), suite_reference)
        if contract_review_root:
            require(contract_reference is not None, "contract companion reference required")
            assert contract_reference is not None
            contract = validate_contract_review(contract_review_root, root, selected, result)
            completion.save(contract_reference, contract)
            result = apply_contract_review(result, contract, contract_reference, root=root, selection=selected)
        return result

    table.assess_table = assess
    try:
        yield
    finally:
        table.assess_table = previous


def run(args) -> int:
    require(
        args.source_review_operator_store and args.source_review_operator_trust and args.registry,
        "original finish requires explicit operator store/trust and registries",
    )
    # Matrix is an operator-selected orchestration inventory, not a report.
    matrix = support.document(args.matrix.absolute())
    saved = load(Path(matrix["base"]["report"]))
    require(saved["root"] == str(ROOT), "unsupported original matrix")
    options = deepcopy(matrix["base"]["options"])
    options.update(
        {
            "registry": [str(p.absolute()) for p in args.registry],
            "source-review-operator-store": str(args.source_review_operator_store.absolute()),
            "source-review-operator-trust": args.source_review_operator_trust,
        }
    )
    out = args.output.absolute()
    require(not out.exists() and not any(p.is_symlink() for p in (out, *out.parents)), "new nonsymlink output required")
    out.mkdir(mode=0o700, parents=True)
    selection_path = out / "selection.json"
    completion.save(selection_path, saved["selection"])
    before = support.store_inventory(args.source_review_operator_store)
    suite_root = getattr(args, "suite_native_root", None)
    contract_root = getattr(args, "contract_review_root", None)
    require(not contract_root or suite_root, "contract review requires the current original suite context")
    contract_reference = out / "contract-corroboration.json" if contract_root else None
    suite: dict[str, Any] = {}
    suite_reference = out / "suite-native.json" if suite_root else None
    if suite_root:
        suite = validate_suite_native(suite_root)
        completion.save(suite_reference, suite)
    original_context = suite_original_context() if suite_root else contextlib.nullcontext()
    with original_context, audit_adapter.companion_context(options):
        closures = support.closure_check(args.source_freeze)
        with (
            table_hook(suite_root, suite_reference, contract_root, contract_reference),
            contextlib.redirect_stdout(io.StringIO()) as captured,
        ):
            status = audit_adapter.table.main(
                ["--root", str(ROOT), "--selection", str(selection_path), "--output", str(out / "report")]
            )
        require(status in (0, 1), "ordinary finish assessment failed: " + captured.getvalue())
        if suite_root:
            generated = out / "report/assessment.json"
            # Compact only this newly emitted packet; retain identical parsed data
            # and use the unchanged bounded reader for its subsequent admission.
            with generated.open("rb") as handle:
                generated_bytes = handle.read(2_000_001)
            require(len(generated_bytes) <= 2_000_000, "generated suite packet too large")
            parsed = json.loads(generated_bytes)
            generated.write_bytes(encoded(parsed) + b"\n")
            require(encoded(load(generated)) == encoded(parsed), "compact packet differs")
        packet = load(out / "report/assessment.json")
        value = packet["report"]["measurements"]["original_finish"]
        full_requests = requests(packet_inputs(), value["observations"])
        require(
            [r["binding"] for r in full_requests] == [r["binding"] for r in value["requests"]],
            "emitted requests differ",
        )
        completion.save(out / "requests.json", full_requests)
        completion.save(out / "admission.json", value)
        require(support.closure_check(args.source_freeze) == closures, "producer closure changed")
    require(support.store_inventory(args.source_review_operator_store) == before, "owner store changed")
    baseline = load(Path(load(SCOPE / "packet.json")["base_report"]))["report"]
    changed = [k for k, v in baseline["items"].items() if packet["report"]["items"][k] != v]
    verification = {
        "ordinary_exit": status,
        "native_launches": 0,
        "model_launches": 0,
        "new_owner_decisions": 0,
        "selection_unchanged": packet["selection"] == saved["selection"],
        "changed_rows": changed,
        "neighbor_rows_unchanged": len(baseline["items"]) - len(changed),
        "producer_closures": closures,
        "owner_store_unchanged": True,
        "retained_replay": "not_run",
        "full_matrix_replay": "not_run",
        "scores": {k: v["score"] for k, v in packet["report"]["items"].items()},
        "source_hashes": {
            str(p): hashlib.sha256(raw(p)).hexdigest() for p in (Path(__file__), Path(completion.__file__))
        },
        "report_sha256": hashlib.sha256(raw(out / "report/assessment.json")).hexdigest(),
    }
    if suite_root:
        previous_path = RUNS / "containment-admission-finish-current/ordinary-v4/report/assessment.json"
        previous = json.loads(pin(previous_path, "00665769bc1b4d99310fafd3eac97fb33c6d416c5ddb0c2c5158c115ca71f0d7"))
        current_report = deepcopy(packet["report"])
        if contract_root:
            assert contract_reference is not None
            contract_value = load(contract_reference)
            reference = current_report["measurements"].pop("contract_corroboration")
            require(
                reference["sha256"] == hashlib.sha256(raw(contract_reference)).hexdigest(),
                "contract report link differs",
            )
            require(
                all(
                    current_report["items"][k] == row
                    for k, row in previous["report"]["items"].items()
                    if k != CONTRACT_ID
                ),
                "contract neighbor changed",
            )
            current_report["items"][CONTRACT_ID] = deepcopy(previous["report"]["items"][CONTRACT_ID])
            completion.save(out / "contract-method-request.json", contract_value["request"])
            verification["contract_corroboration"] = {
                "binding": contract_value["request"]["binding"],
                "method_qualified": contract_value["method_qualified"],
                "lower_anchor_satisfied": contract_value["lower_anchor_satisfied"],
                "promoted": contract_value["promotion_ready"],
                "neighbor_rows_unchanged": len(current_report["items"]) - 1,
                "baseline": str(RUNS / "suite-native-integration-current/ordinary-final/report/assessment.json"),
            }
        linked = current_report["measurements"].pop("suite_native_viability")
        require(
            linked["path"] == str(suite_reference)
            and linked["sha256"] == hashlib.sha256(raw(out / "suite-native.json")).hexdigest(),
            "suite report link differs",
        )
        require(
            encoded(current_report) == encoded(previous["report"]) and packet["selection"] == previous["selection"],
            "original full report changed by suite supplement",
        )
        verification["suite_native"] = {
            "path": "suite-native.json",
            "sha256": hashlib.sha256(raw(out / "suite-native.json")).hexdigest(),
            "trial_count": suite["trial_count"],
            "raw_file_count": suite["raw_file_count"],
            "all_original_rows_and_measurements_unchanged": True,
            "baseline": str(previous_path),
        }
        verification["status_counts"] = dict(
            Counter(
                "NOT_APPLICABLE"
                if r["assessment_state"] == "NOT_APPLICABLE"
                else "Unassessed"
                if r["score"] is None
                else str(r["score"])
                for r in packet["report"]["items"].values()
            )
        )
    completion.save(out / "verification.json", verification)
    text = [
        "# Original evidence admission",
        "",
        "Ordinary assessment completed; no new owner decisions, native/model runs or retained replay.",
        "",
        "[Criterion table](report/results.md) · [Exact scoped requests](requests.json) · [Retained receipts](admission.json) · [Verification](verification.json)",
        "",
        "## Scoped decisions and readback",
        "",
    ]
    for request in full_requests:
        receipt = value["receipts"][request["question_id"]]
        text.extend(
            [
                "### " + request["question_id"],
                "",
                request["question"]["question"],
                "",
                "Binding: `"
                + request["binding"]
                + "`. Status: "
                + ("unanswered" if receipt is None else "retained; see exact disposition")
                + ".",
                "",
            ]
        )
    text.extend(
        [
            "## Limits",
            "",
            BOUNDARY,
            "",
            value["observations"]["history"]["discrepancy"],
            "",
            "Historical Audit closure is separately admitted by exact verification/output pins and current owning baseline comparison. Its earlier completion-module hash remains historical; this additive entrypoint has its own source binding.",
        ]
    )
    if suite_root:
        text += [
            "",
            "## Separately admitted native suite viability",
            "",
            suite["scoring_path"],
            "",
            "[Native evidence and exact identities](suite-native.json). No original score, approval, independent-review or held-out credit transfers.",
            "",
            "| Family | Control | Task credit | Harbor reward | Raw Trial |",
            "|---|---|---:|---:|---|",
        ]
        for row in suite["rows"]:
            text.append(
                f"| {row['family']} | {row['control']} | {row['task_credit']}/{row['max_credit']} | {row['reward']} | [result]({row['raw_result']}) |"
            )
        text += ["", *suite["limits"], ""]
    if contract_root:
        assert contract_reference is not None
        contract_value = load(contract_reference)
        text += [
            "",
            "## Original B full-contract corroboration",
            "",
            "[Actual review](" + contract_value["source_report"] + ") · "
            "[Source-bound comparison and receipt](contract-corroboration.json) · "
            "[Exact method intake request](contract-method-request.json)",
            "",
            "Method qualified: "
            + str(contract_value["method_qualified"])
            + "; lower anchor satisfied: "
            + str(contract_value["lower_anchor_satisfied"])
            + "; emitted scoring-authority: "
            + str(packet["report"]["items"][CONTRACT_ID]["score"])
            + ".",
            "",
            contract_value["independence"],
            "",
            "Qualification is scoped method acceptance, not score approval. Canonical predicates remain separate.",
            "",
            "### Retained findings (S is outside qualification)",
            "",
            *["- " + finding for finding in contract_value["findings"]],
            "",
        ]
    (out / "REPORT.md").write_text("\n".join(text) + "\n")
    print(
        json.dumps(
            {
                "output": str(out),
                "scores": dict(Counter(str(x) for x in verification["scores"].values())),
                "unanswered": sum(v is None for v in value["receipts"].values()),
                "report_sha256": verification["report_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0
