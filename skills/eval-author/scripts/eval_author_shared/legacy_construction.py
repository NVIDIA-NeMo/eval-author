# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private construction contract: provenance, controls, and dependency evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .runtime_assets import ASSETS
from .runtime_assets import load as load_runtime

_runtime = load_runtime("qualification_runtime")
MAX_OUTPUT_BYTES, digest, observation, pointer = (
    _runtime.MAX_OUTPUT_BYTES,
    _runtime.digest,
    _runtime.observation,
    _runtime.pointer,
)
EXECUTION_SCHEMA = _runtime.SCHEMA

SCHEMA = "nemo.eval_author.construction.v1"
PATH = "private/construction.json"
MAX_BYTES = 2 * 1024 * 1024
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA = re.compile(r"[0-9a-f]{64}")
CUSTOM_ARMS = {"negative", "copy", "alternative", "bypass"}


class ConstructionError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ConstructionError(message)


def fields(value: object, keys: str, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ConstructionError(f"{label}: invalid fields")
    return value


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def identity(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 80 and ID.fullmatch(value) is not None


def secure_path(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and relative != "" and "\\" not in relative, "invalid evidence path")
    path = Path(relative)
    require(not path.is_absolute() and all(p not in (".", "..") for p in path.parts), "evidence path escapes workspace")
    result = root / path
    require(not any(p.is_symlink() for p in (result, *result.parents)), "evidence symlinks are not allowed")
    return result


def read_bytes(root: Path, relative: str) -> bytes:
    path = secure_path(root, relative)
    limit = 25 * 1024 * 1024 if relative == "safe/trace.atif.json" else MAX_BYTES
    require(path.is_file() and path.stat().st_size <= limit, "missing or oversized construction evidence")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "construction evidence exceeds read limit")
    return data


def parse(data: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field in construction evidence")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ConstructionError("nonfinite JSON number in construction evidence")

    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, OSError) as error:
        raise ConstructionError("construction evidence is not readable JSON") from error


def load(root: Path, relative: str) -> object:
    return parse(read_bytes(root, relative))


def file_digest(root: Path, relative: str) -> str:
    return hashlib.sha256(read_bytes(root, relative)).hexdigest()


def ids(value: object, known: set, label: str, *, empty: bool = False) -> list:
    if not isinstance(value, list) or len(value) > 256:
        raise ConstructionError(f"{label}: expected bounded ID list")
    require(all(isinstance(item, str) for item in value), f"{label}: IDs must be strings")
    require(
        (empty or bool(value)) and len(value) == len(set(value)) and set(value) <= known,
        f"{label}: unknown or duplicate IDs",
    )
    return value


def review(value: object) -> None:
    value = fields(value, "reviewer_kind note", "review")
    require(
        value["reviewer_kind"] in ("agent", "human") and text(value["note"]), "review needs reviewer and concrete note"
    )


def source_value(trace: dict, reference: object, order: dict) -> tuple[str, int]:
    reference = fields(reference, "step_id pointer", "source reference")
    require(type(reference["step_id"]) is int and reference["step_id"] in order, "source step is not in trace")
    index = order[reference["step_id"]]
    try:
        value = pointer(trace["steps"][index], reference["pointer"])
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise ConstructionError("source pointer does not resolve") from error
    if not isinstance(value, str):
        raise ConstructionError("source reference must resolve to exact text")
    return value, index


def provenance(root: Path, record: dict, trace: dict, candidate: dict) -> dict:
    fields(record, "cutoff_step_id roots artifacts external_inputs no_files_reason review", "starting state")
    review(record["review"])
    order = {step["step_id"]: index for index, step in enumerate(trace["steps"])}
    require(
        type(record["cutoff_step_id"]) is int and record["cutoff_step_id"] in order, "cutoff must identify a trace step"
    )
    cutoff = order[record["cutoff_step_id"]]
    roots = record["roots"]
    require(
        isinstance(roots, list) and len(roots) <= 64 and all(isinstance(p, str) for p in roots), "invalid input roots"
    )
    require(len(roots) == len(set(roots)), "duplicate input roots")
    discovered = set()
    visited = 0
    for relative in roots:
        require(relative.startswith("task/environment/"), "input roots must be under task/environment/")
        directory = secure_path(root, relative)
        require(directory.is_dir(), "input root is missing")
        pending = [directory]
        while pending:
            current = pending.pop()
            for path in current.iterdir():
                visited += 1
                require(visited <= 4096, "input inventory exceeds traversal limit")
                require(not path.is_symlink(), "input inventory contains a symlink")
                if path.is_dir():
                    pending.append(path)
                else:
                    require(path.is_file(), "input inventory contains non-regular file")
                    discovered.add(path.relative_to(root).as_posix())
    artifacts = record["artifacts"]
    require(isinstance(artifacts, list) and len(artifacts) <= 1024, "invalid input artifact list")
    declared = set()
    counts = {"observed": 0, "reversed_edit": 0, "authored": 0}
    total_bytes = 0
    for artifact in artifacts:
        fields(artifact, "path sha256 basis source edits review", "input artifact")
        path = artifact["path"]
        require(
            isinstance(path, str) and path in discovered and path not in declared,
            "input artifact is missing, outside roots or duplicated",
        )
        declared.add(path)
        data = read_bytes(root, path)
        total_bytes += len(data)
        require(total_bytes <= 16 * MAX_BYTES, "input inventory exceeds byte limit")
        require(hashlib.sha256(data).hexdigest() == artifact["sha256"], "starting artifact digest changed")
        basis = artifact["basis"]
        require(isinstance(basis, str) and basis in counts, "unknown artifact provenance basis")
        counts[basis] += 1
        review(artifact["review"])
        if basis == "authored":
            require(
                candidate.get("state_basis", "recorded") == "reconstructed",
                "authored input requires reconstructed state basis",
            )
            require(
                artifact["source"] is None and artifact["edits"] == [], "authored input must not claim observed content"
            )
            continue
        value, index = source_value(trace, artifact["source"], order)
        require(len(value.encode()) <= MAX_BYTES, "source snapshot exceeds reconstruction limit")
        if basis == "observed":
            require(index <= cutoff and artifact["edits"] == [], "observed input uses future evidence or transforms")
        else:
            edits = artifact["edits"]
            require(isinstance(edits, list) and 1 <= len(edits) <= 64, "reversal requires bounded recorded edits")
            previous = index + 1
            for edit in edits:
                fields(edit, "before after", "reverse edit")
                before, before_index = source_value(trace, edit["before"], order)
                after, after_index = source_value(trace, edit["after"], order)
                require(
                    before_index == after_index and cutoff < after_index < previous,
                    "reverse edits must be later mutations in descending trace order",
                )
                require(
                    bool(after) and before != after and value.count(after) == 1, "reverse edit must match exactly once"
                )
                value = value.replace(after, before, 1)
                require(len(value.encode()) <= MAX_BYTES, "reversal exceeds reconstruction limit")
                previous = after_index
        require(value.encode() == data, "starting artifact differs from evidenced content or exact reversal")
    require(declared == discovered, "starting artifact inventory is incomplete")
    if not discovered:
        require(text(record["no_files_reason"]), "empty input inventory needs an explicit scope reason")
    else:
        require(record["no_files_reason"] is None, "nonempty input inventory cannot claim no files")
    external = record["external_inputs"]
    require(isinstance(external, list) and len(external) <= 64, "invalid external input inventory")
    for item in external:
        fields(item, "description identity review", "external input")
        require(
            text(item["description"]) and text(item["identity"]), "external inputs need immutable identity and scope"
        )
        review(item["review"])
    return {
        "scope": "declared_input_files",
        "artifacts": len(declared),
        "basis_counts": counts,
        "external_inputs": len(external),
    }


def validate(root: Path, *, manifest: dict | None = None) -> dict:
    from_source = manifest is None
    document = load(root, PATH) if from_source else manifest
    manifest = fields(
        document,
        "schema candidate_sha256 trace_sha256 requirements probes controls dependencies starting_state review",
        "construction",
    )
    require(manifest["schema"] == SCHEMA, "unknown construction schema")
    require(
        manifest["candidate_sha256"] == file_digest(root, "candidate.json"), "construction candidate digest changed"
    )
    require(manifest["trace_sha256"] == file_digest(root, "safe/trace.atif.json"), "construction trace digest changed")
    candidate = load(root, "candidate.json")
    trace = load(root, "safe/trace.atif.json")
    if not isinstance(candidate, dict) or not isinstance(trace, dict):
        raise ConstructionError("candidate and trace must be JSON objects")
    review(manifest["review"])
    requirements = manifest["requirements"]
    require(isinstance(requirements, list) and 1 <= len(requirements) <= 256, "construction needs requirements")
    by_requirement, indices, checks = {}, set(), set()
    for requirement in requirements:
        fields(requirement, "id candidate_index checks", "requirement")
        key, index = requirement["id"], requirement["candidate_index"]
        require(identity(key) and key not in by_requirement, "invalid or duplicate requirement ID")
        require(
            type(index) is int and 0 <= index < len(candidate["requirements"]) and index not in indices,
            "invalid or duplicate candidate requirement index",
        )
        rows = requirement["checks"]
        require(
            isinstance(rows, list) and 1 <= len(rows) <= 256 and all(identity(row) for row in rows),
            "requirement needs scored check IDs",
        )
        require(len(rows) == len(set(rows)), "duplicate requirement check IDs")
        checks.update(rows)
        indices.add(index)
        by_requirement[key] = requirement
    require(indices == set(range(len(candidate["requirements"]))), "not every candidate requirement is mapped")
    probes = manifest["probes"]
    require(isinstance(probes, list) and 1 <= len(probes) <= 32, "expected 1..32 setup/reach probes")
    by_probe = {}
    saw_reach = False
    for probe in probes:
        fields(probe, "id kind argv cwd timeout_seconds json_pointer equals assertion_path", "probe")
        key = probe["id"]
        require(
            identity(key) and key not in by_probe and probe["kind"] in ("setup", "reach"), "invalid probe identity/kind"
        )
        require(not (saw_reach and probe["kind"] == "setup"), "setup probes must precede reach probes")
        saw_reach = saw_reach or probe["kind"] == "reach"
        require(
            isinstance(probe["argv"], list) and 1 <= len(probe["argv"]) <= 64 and all(text(a) for a in probe["argv"]),
            "probe needs argv",
        )
        require(text(probe["cwd"]) and probe["cwd"].startswith("/"), "probe cwd must be absolute in verifier")
        require(
            type(probe["timeout_seconds"]) in (int, float) and 0 < probe["timeout_seconds"] <= 30,
            "invalid probe timeout",
        )
        require(
            isinstance(probe["json_pointer"], str)
            and (probe["json_pointer"] == "" or probe["json_pointer"].startswith("/")),
            "invalid observation pointer",
        )
        require(
            isinstance(probe["assertion_path"], str) and probe["assertion_path"].startswith("task/tests/"),
            "probe assertion must be verifier-owned",
        )
        read_bytes(root, probe["assertion_path"])
        by_probe[key] = probe
    require(any(p["kind"] == "setup" for p in probes), "at least one setup probe is required")
    require(any(p["kind"] == "reach" for p in probes), "at least one reach probe is required")
    controls = manifest["controls"]
    require(isinstance(controls, list) and 2 <= len(controls) <= 128, "negative and alternative controls are required")
    by_control = {}
    for control in controls:
        fields(control, "id arm requirements fails reaches dependency rationale review", "control")
        key, arm = control["id"], control["arm"]
        require(
            identity(key) and key not in by_control and isinstance(arm, str) and arm in CUSTOM_ARMS,
            "invalid control ID/arm",
        )
        refs = ids(control["requirements"], set(by_requirement), "control requirements")
        mapped = {c for r in refs for c in by_requirement[r]["checks"]}
        ids(control["fails"], mapped, "control failing checks", empty=arm == "alternative")
        if arm == "alternative":
            require(
                control["fails"] == [] and set(refs) == set(by_requirement), "alternative must accept every requirement"
            )
        ids(control["reaches"], {k for k, p in by_probe.items() if p["kind"] == "reach"}, "control reach probes")
        require(text(control["rationale"]), "control needs a task-specific rationale")
        review(control["review"])
        if arm != "bypass":
            require(control["dependency"] is None, "only bypass controls may name dependencies")
        by_control[key] = control
    require(
        {"negative", "alternative"} <= {c["arm"] for c in controls}, "negative and alternative controls are required"
    )
    dependencies = manifest["dependencies"]
    require(isinstance(dependencies, list) and len(dependencies) <= 256, "invalid dependency list")
    edges, dependency_ids = {}, set()
    for dependency in dependencies:
        fields(dependency, "id prerequisite dependent bypass_control", "dependency")
        key, first, second, control_id = (dependency[k] for k in ("id", "prerequisite", "dependent", "bypass_control"))
        require(identity(key) and key not in dependency_ids, "invalid dependency ID")
        require(
            isinstance(first, str)
            and isinstance(second, str)
            and first in by_requirement
            and second in by_requirement
            and first != second,
            "invalid dependency endpoints",
        )
        require(isinstance(control_id, str) and control_id in by_control, "dependency needs a bypass control")
        control = by_control[control_id]
        require(control["arm"] == "bypass" and control["dependency"] == key, "dependency/control mismatch")
        require(
            second in control["requirements"] and bool(set(control["fails"]) & set(by_requirement[second]["checks"])),
            "bypass must fail a dependent outcome",
        )
        dependency_ids.add(key)
        edges.setdefault(first, set()).add(second)
    require({c["dependency"] for c in controls if c["arm"] == "bypass"} == dependency_ids, "unmatched bypass control")

    def visit(node: str, active: set, done: set) -> None:
        require(node not in active, "dependency graph contains a cycle")
        if node in done:
            return
        active.add(node)
        for child in edges.get(node, set()):
            visit(child, active, done)
        active.remove(node)
        done.add(node)

    done: set = set()
    for node in by_requirement:
        visit(node, set(), done)
    provenance_summary = provenance(root, manifest["starting_state"], trace, candidate)
    require(
        digest(load(root, "task/tests/qualification-probes.json")) == digest(probes),
        "verifier probe definitions differ from private construction contract",
    )
    require(
        read_bytes(root, "task/tests/qualification_runtime.py") == (ASSETS / "qualification_runtime.py").read_bytes(),
        "verifier execution helper differs from bundled producer",
    )
    return {
        "manifest": manifest,
        "sha256": file_digest(root, PATH) if from_source else digest(manifest),
        "checks": checks,
        "controls": by_control,
        "provenance": provenance_summary,
    }


def execution(root: Path, result_path: Path, context: dict, control_id: str | None) -> dict:
    relative = (result_path.parent / "verifier" / "qualification.json").relative_to(root).as_posix()
    evidence = load(root, relative)
    evidence = fields(evidence, "schema probes", "execution receipt")
    require(
        evidence["schema"] == EXECUTION_SCHEMA,
        "unknown execution receipt schema",
    )
    rows = evidence["probes"]
    probes = context["manifest"]["probes"]
    require(isinstance(rows, list) and len(rows) == len(probes), "missing execution probes")
    passed = set()
    seen = set()
    by_id = {p["id"]: p for p in probes}
    for row in rows:
        fields(
            row,
            "id definition_sha256 exit_code failure output_sha256 observation output_file output_truncated",
            "execution probe",
        )
        key = row["id"]
        require(isinstance(key, str) and key in by_id and key not in seen, "unknown or duplicate execution probe")
        seen.add(key)
        probe = by_id[key]
        require(row["definition_sha256"] == digest(probe), "execution probe definition changed")
        require(row["exit_code"] is None or type(row["exit_code"]) is int, "invalid probe exit code")
        require(
            row["failure"]
            in (
                None,
                "start_failed",
                "timeout",
                "output_limit",
                "observation_limit",
                "invalid_observation",
                "nonzero_exit",
            ),
            "unknown probe failure",
        )
        require(
            isinstance(row["output_sha256"], str) and SHA.fullmatch(row["output_sha256"]) is not None,
            "missing probe output digest",
        )
        require(
            row["output_file"] == f"qualification-output/{key}.bin"
            and type(row["output_truncated"]) is bool
            and row["output_truncated"] == (row["failure"] == "output_limit"),
            "invalid retained probe output",
        )
        output = read_bytes(root, str(Path(relative).parent / row["output_file"]))
        require(
            len(output) <= MAX_OUTPUT_BYTES and hashlib.sha256(output).hexdigest() == row["output_sha256"],
            "probe output changed or oversized",
        )
        failure, code = row["failure"], row["exit_code"]
        require(
            (failure == "start_failed" and code is None and not output)
            or (failure in ("timeout", "output_limit") and type(code) is int)
            or (failure == "nonzero_exit" and type(code) is int and code != 0)
            or (failure in (None, "invalid_observation", "observation_limit") and code == 0),
            "inconsistent probe execution status",
        )
        if failure in (None, "invalid_observation", "observation_limit"):
            observed, observed_failure = observation(output, probe["json_pointer"])
            require(observed_failure == failure, "probe observation status differs from output")
            require(digest(observed) == digest(row["observation"]), "probe observation differs from output")
        else:
            require(row["observation"] is None, "failed probe cannot claim an observation")
        if row["failure"] is None and row["exit_code"] == 0 and digest(row["observation"]) == digest(probe["equals"]):
            passed.add(key)
    setup = {p["id"] for p in probes if p["kind"] == "setup"}
    reaches = set(context["controls"][control_id]["reaches"]) if control_id else set()
    return {
        "path": relative,
        "sha256": file_digest(root, relative),
        "setup_passed": setup <= passed,
        "behavior_reached": reaches <= passed,
        "passed_probes": sorted(passed),
        "control_id": control_id,
    }


def qualify(context: dict, runs: dict) -> dict:
    controls = context["controls"]
    seen = set()
    outcomes = []
    nop_observations = [set(run["construction_execution"]["passed_probes"]) for run in runs["nop"]]
    for arm, values in runs.items():
        for run in values:
            evidence = run["construction_execution"]
            check_map = {r["check_id"]: r["status"] for r in run["checks"]}
            require(set(check_map) == context["checks"], "scored checks differ from requirement mapping")
            control_id = evidence["control_id"]
            if arm in CUSTOM_ARMS:
                require(
                    control_id in controls and controls[control_id]["arm"] == arm, "run does not match declared control"
                )
                control = controls[control_id]
                seen.add(control_id)
                if arm == "alternative":
                    expected = all(status == "PASS" for status in check_map.values()) and run["reward"] == 1
                else:
                    expected = all(check_map[c] == "FAIL" for c in control["fails"]) and run["reward"] == 0
                    # Interface availability alone says nothing about control
                    # progress: require a declared observation absent in every
                    # untouched run, but observed after this control's attempt.
                    progress = any(
                        all(probe not in observed for observed in nop_observations) for probe in control["reaches"]
                    )
                    expected = expected and progress
                outcomes.append(
                    expected
                    and evidence["setup_passed"]
                    and evidence["behavior_reached"]
                    and not run["exception_present"]
                )
            elif arm == "oracle":
                outcomes.append(
                    evidence["setup_passed"]
                    and all(s == "PASS" for s in check_map.values())
                    and run["reward"] == 1
                    and not run["exception_present"]
                )
            elif arm == "nop":
                outcomes.append(evidence["setup_passed"] and run["reward"] == 0 and not run["exception_present"])
    require(seen == set(controls), "every declared control must have retained execution evidence")
    return {
        "status": "passed" if all(outcomes) else "failed",
        "manifest_sha256": context["sha256"],
        "requirements": len(context["manifest"]["requirements"]),
        "controls": len(controls),
        "tested_dependencies": len(context["manifest"]["dependencies"]),
        "starting_state": context["provenance"],
        "difficulty": "unmeasured",
        "scope": "declared_controls_and_inputs",
    }
