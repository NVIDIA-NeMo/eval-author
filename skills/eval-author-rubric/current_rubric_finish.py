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


@contextlib.contextmanager
def table_hook(suite_root: Path | None = None, suite_reference: Path | None = None):
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
        return apply_suite_native(result, validate_suite_native(suite_root), suite_reference) if suite_root else result

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
    suite: dict[str, Any] = {}
    suite_reference = out / "suite-native.json" if suite_root else None
    if suite_root:
        suite = validate_suite_native(suite_root)
        completion.save(suite_reference, suite)
    original_context = suite_original_context() if suite_root else contextlib.nullcontext()
    with original_context, audit_adapter.companion_context(options):
        closures = support.closure_check(args.source_freeze)
        with table_hook(suite_root, suite_reference), contextlib.redirect_stdout(io.StringIO()) as captured:
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
                for r in current_report["items"].values()
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
