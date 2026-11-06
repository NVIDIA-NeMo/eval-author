# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schema import load_audit_spec, validate_audit_spec  # noqa: E402

VERSION = 1
FINDING_REFERENCE = re.compile(r"\{(F[1-9][0-9]*)\}")
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas/report_content.schema.json"
CONTENT_PATH = ".eval-author/.working/audit/report-content.json"
MAX_BYTES = 8_000_000


class ContentError(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("\n".join(f"{row['field']}: {row['rule']}. {row['fix']}" for row in errors))


def issue(path: str, rule: str, fix: str) -> dict:
    return {"field": path, "rule": rule, "fix": fix}


def load_module(name: str, path: Path):
    existing = sys.modules.get(name)
    if existing is not None and existing.__file__ is not None and Path(existing.__file__).resolve() == path.resolve():
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load bundled module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def core_module():
    scripts = Path(__file__).resolve().parents[3] / "eval-author/scripts"
    if not scripts.is_dir():
        raise ValueError("Install the sibling eval-author skill to read task evidence")
    sys.path.insert(0, str(scripts))
    return load_module("audit_verifier_evidence", scripts / "audit_verifier_evidence.py")


def local(repo: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or name == "."
        or relative.as_posix() != name
        or any(part in {"..", "~"} for part in relative.parts)
        or any(char in name for char in ("\\", "\n", "\r", "\x00"))
        or ":" in relative.parts[0]
    ):
        raise ValueError("Use a canonical repository-relative path")
    path = repo / name
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Source paths must not traverse symlinks")
    if not path.resolve().is_relative_to(repo):
        raise ValueError("Source path is outside the repository")
    return path


def read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Select a regular file of at most {MAX_BYTES} bytes: {path}")
    return path.read_bytes()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(data: bytes) -> dict:
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Nonfinite JSON value: {value}")

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def shape_errors(content: dict) -> list[dict]:
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        issue(
            "content" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path),
            "schema",
            error.message,
        )
        for error in sorted(validator.iter_errors(content), key=lambda e: (str(list(e.absolute_path)), e.message))
    ]


def prose_fields(content: Any, schema: dict | None = None, path: str = "content", *, visible_only: bool = False):
    root = json.loads(SCHEMA_PATH.read_text())

    def walk(value, node, location):
        if "$ref" in node:
            node = {**root["$defs"][node["$ref"].split("/")[-1]], **node}
        if visible_only and node.get("x-report-visible") is False:
            return
        if node.get("x-prose") and isinstance(value, str):
            yield location, value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from walk(item, node.get("properties", {}).get(key, {}), location + "." + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from walk(item, node.get("items", {}), f"{location}[{index}]")
        elif value is not None:
            for branch in node.get("anyOf", []):
                if branch.get("type") != "null":
                    yield from walk(value, branch, location)

    yield from walk(content, schema or root, path)


def prose_warnings(content: dict) -> list[dict]:
    pattern = re.compile(r"\b(?:delve|leverage|foster|it's worth noting|bottom line|grader|test case)\b", re.I)
    return [
        issue(path, "prose", f"Replace {match.group()!r} with direct wording or the report's defined term")
        for path, value in prose_fields(content)
        for match in pattern.finditer(value)
    ]


@dataclass
class Content:
    repo: Path
    path: Path
    data: dict
    digest: str
    paths: dict[str, Path] = field(default_factory=dict)
    values: dict[str, dict] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)
    tasks: dict[str, dict] = field(default_factory=dict)
    spec: dict = field(default_factory=dict)
    aggregate: dict | None = None
    controls: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def source(self, identifier: str) -> Path:
        if identifier not in self.paths:
            raise ValueError(f"Unknown saved source {identifier!r}")
        return self.paths[identifier]

    def result(self) -> dict:
        return {
            "valid": True,
            "validator_version": VERSION,
            "content_sha256": self.digest,
            "warnings": self.warnings,
            "bindings": [
                {"path": row["path"], "digest": row["sha256"], "kind": "task" if row["kind"] == "task" else "file"}
                for row in self.data["sources"]
            ],
            "evidence": [
                {"path": str(self.path.relative_to(self.repo)), "digest": self.digest},
                *[{"path": row["path"], "digest": row["sha256"]} for row in self.data["sources"]],
            ],
        }


def relative_to_repo(repo: Path, path: Path) -> str:
    """Resolve only the repository prefix, so a symlinked repo path still works and in-repo symlinks stay visible."""
    if not path.is_absolute():
        return path.as_posix()
    for base in (path, *path.parents):
        if base.exists() and base.resolve() == repo:
            return path.relative_to(base).as_posix()
    return path.relative_to(repo).as_posix()


def load(path: Path, repo: Path, *, lint: bool = False) -> Content:
    repo = repo.resolve(strict=True)
    try:
        path = local(repo, relative_to_repo(repo, path))
        raw = read_bytes(path)
        data = decode(raw)
    except (OSError, ValueError) as exc:
        raise ContentError([issue("content", "read", str(exc))]) from exc
    errors = shape_errors(data)
    if errors:
        raise ContentError(errors)
    model = Content(repo, path, data, sha(raw))
    errors = _sources(model)
    if errors:
        raise ContentError(errors)
    errors = _references(model)
    if errors:
        raise ContentError(errors)
    if lint:
        model.warnings.extend(prose_warnings(data))
    return model


def _sources(model: Content) -> list[dict]:
    errors = []
    seen_paths = set()
    for index, row in enumerate(model.data["sources"]):
        field_path = f"content.sources[{index}]"
        try:
            if row["id"] in model.sources or row["path"] in seen_paths:
                raise ValueError("Source IDs and paths must be unique")
            model.sources[row["id"]] = row
            seen_paths.add(row["path"])
            source = local(model.repo, row["path"])
            if row["kind"] == "task":
                actual = core_module().task_evidence.task_digest(source)
            else:
                raw = read_bytes(source)
                actual = sha(raw)
                if row["kind"] in {"receipt", "manifest", "measurement", "run"}:
                    model.values[row["id"]] = decode(raw)
            if actual != row["sha256"]:
                raise ValueError("Saved source bytes changed. Review the change and update its digest")
            model.paths[row["id"]] = source
        except (OSError, ValueError) as exc:
            errors.append(issue(field_path, "source_binding", str(exc)))
    if errors:
        return errors
    try:
        audit_id = model.data["inputs"]["audit"]
        _kind(model, audit_id, "audit")
        # Bound audit sources must be selected before its loader follows their paths.
        import yaml
        from _markdown import extract_schema_block

        spec_path = model.source(audit_id)
        try:
            unvalidated = yaml.safe_load(extract_schema_block(spec_path))
        except yaml.YAMLError as exc:
            raise ValueError(f"Audit YAML does not parse: {exc}") from exc
        if not isinstance(unvalidated, dict) or not isinstance(unvalidated.get("sources", []), list):
            raise ValueError("Audit specification must be an object with a source list")
        unvalidated = validate_audit_spec(unvalidated)
        for source in unvalidated.get("sources", []):
            if source.get("sha256"):
                selected = (spec_path.parent / source["path"]).resolve()
                if selected not in model.paths.values():
                    raise ValueError(f"Select the audit's bound source before reading it: {source['path']}")
        model.spec = load_audit_spec(spec_path)
        if model.data["inputs"]["ethos"] is not None:
            _kind(model, model.data["inputs"]["ethos"], "ethos")
        measurement = model.data["inputs"]["measurement"]
        if measurement is not None:
            _kind(model, measurement, "measurement")
            _measurement(model, measurement)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(issue("content.inputs", "source_binding", str(exc)))
    return errors


def _kind(model: Content, identifier: str, kind: str):
    model.source(identifier)
    if model.sources[identifier]["kind"] != kind:
        raise ValueError(f"Source {identifier!r} must have kind {kind!r}")


def _measurement(model: Content, identifier: str):
    aggregate = model.values[identifier]
    path = model.source(identifier)
    binding = aggregate.get("binding")
    if not binding:
        raise ValueError("Unbound measurement report. Re-measure and aggregate before rendering current coverage")
    artifact_map = binding.get("artifacts", {})

    def selected(original: str) -> Path:
        candidate = (path.parent / artifact_map[original]) if original in artifact_map else Path(original)
        if not candidate.is_absolute():
            candidate = model.repo / candidate
        candidate = candidate.resolve()
        if candidate not in model.paths.values():
            raise ValueError(f"Select the bound measurement source before reading it: {candidate}")
        return candidate

    if selected(aggregate["audit"]["path"]) != model.source(model.data["inputs"]["audit"]):
        raise ValueError("Coverage measurements reference a different audit specification")
    for entry in binding["inputs"]:
        payload = decode(read_bytes(selected(entry["path"])))
        selected(payload["subject"]["trace"])
        for judgment in payload.get("binding", {}).get("judgments", []):
            selected(judgment["path"])
    module = load_module("audit_content_aggregation", Path(__file__).with_name("report.py"))
    module.verify_existing(path)
    model.aggregate = aggregate
    if aggregate.get("warnings"):
        model.warnings.append(
            issue("content.inputs.measurement", "measurement_warning", "Review the aggregate's warnings")
        )


def trace_path(model: Content, entry: dict) -> Path:
    """Resolve a measured input's trace through the aggregate's artifact map."""
    if model.aggregate is None:
        raise ValueError("Resolve a trace only after the measurement aggregate is loaded")
    aggregate_path = model.source(model.data["inputs"]["measurement"])
    artifacts = model.aggregate["binding"].get("artifacts", {})
    original = entry["subject"]["trace"]
    path = aggregate_path.parent / artifacts[original] if original in artifacts else Path(original)
    if not path.is_absolute():
        path = model.repo / path
    return path.resolve()


def _references(model: Content) -> list[dict]:
    errors: list[dict] = []
    data = model.data
    items = {row["name"]: row for row in model.spec["items"]}
    findings = {row["id"]: row for row in data["findings"]}
    runs = {row["id"]: row for row in data["runs"]}
    ids = set()

    def unique(value: str):
        if value in ids:
            raise ValueError(f"Duplicate stable ID {value!r}")
        ids.add(value)

    def known(values, mapping):
        for value in values:
            if value not in mapping:
                raise ValueError(f"Unknown reference {value!r}")

    def check(path, action):
        try:
            action()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(issue(path, "reference", str(exc)))

    def task(row):
        unique(row["id"])
        _kind(model, row["source"], "task")
        if row["revision"] != model.sources[row["source"]]["sha256"]:
            raise ValueError("Task revision must match the selected task source")
        known(row["run_ids"], runs)
        model.tasks[row["id"]] = row

    for index, row in enumerate(data["tasks"]):
        check(f"content.tasks[{index}]", lambda row=row: task(row))
    for index, row in enumerate(data["runs"]):

        def run(row=row):
            unique(row["id"])
            known([row["task_id"]], model.tasks)
            _kind(model, row["source"], "run")
            native = model.values[row["source"]]
            aliases = []
            if model.aggregate is not None:
                for entry in model.aggregate["input_reports"]:
                    if trace_path(model, entry) == model.source(row["source"]):
                        aliases.append(entry["subject"]["run_id"])
            if row["id"] not in {native.get("id"), native.get("session_id"), *aliases}:
                raise ValueError("Run ID must match the saved native run or ATIF session identity")
            if row["id"] not in model.tasks[row["task_id"]]["run_ids"]:
                raise ValueError("Task and run references must agree")

        check(f"content.runs[{index}]", run)
    mapped = [row["item"] for row in data["task_mappings"]]
    if len(mapped) != len(set(mapped)) or set(mapped) != set(items):
        errors.append(
            issue(
                "content.task_mappings", "item_coverage", "Map every declared audit item exactly once, with no extras"
            )
        )
    for index, row in enumerate(data["task_mappings"]):

        def mapping(row=row):
            unique(row["id"])
            known([row["item"]], items)
            known(row["evidence"], model.paths)
            task_ids = [ref["task_id"] for ref in row["tasks"]]
            known(task_ids, model.tasks)
            if len(set(task_ids)) != len(task_ids):
                raise ValueError("Map a task to an item only once")

        check(f"content.task_mappings[{index}]", mapping)
    for index, row in enumerate(data["findings"]):

        def finding(row=row):
            unique(row["id"])
            known(row["evidence"], model.paths)
            known(row["items"], items)
            for ref in row["tasks"]:
                known([ref["task_id"]], model.tasks)
                task = model.tasks[ref["task_id"]]
                current = ref["revision"] == task["revision"]
                historical = any(
                    value.get("task_sha256") == ref["revision"]
                    and _manifest_task(model, value) == model.source(task["source"])
                    for identifier, value in model.values.items()
                    if model.sources[identifier]["kind"] == "manifest"
                )
                if not current and not historical:
                    raise ValueError("Finding task revision needs the current task or a selected historical manifest")
            if row["basis"] == "executed_control" and not any(
                model.sources[ref]["kind"] == "receipt" for ref in row["evidence"]
            ):
                raise ValueError("An executed-control finding requires a saved receipt")
            if row["category"] == "missing_task" and row["status"] != "confirmed":
                raise ValueError(
                    "A missing-task finding must be confirmed; record an unproven or resolved gap elsewhere"
                )
            if row["category"] == "verifier_cheating" and not row["tasks"]:
                raise ValueError("A verifier cheating finding must identify an affected task revision")
            if row["advisory"] is not None and (row["category"] != "verifier_cheating" or row["status"] == "unproven"):
                raise ValueError("Advisory prose requires a confirmed or resolved verifier cheating finding")

        check(f"content.findings[{index}]", finding)
    orders = [row["order"] for row in data["recommendations"]]
    if orders != sorted(set(orders)):
        errors.append(issue("content.recommendations", "recommendation_order", "Use unique increasing order values"))
    for index, row in enumerate(data["recommendations"]):

        def recommendation(row=row):
            unique(row["id"])
            known([row["finding_id"]], findings)
            known(row["items"], items)
            if row.get("details") is not None and row["proposed_task"] is None:
                raise ValueError("Details describe a proposed task, so a recommendation with details needs one")
            if row["action"] in {"new_task", "revise_task"} and row["proposed_task"] is None:
                raise ValueError("A task proposal needs its concrete scenario and verifier requirements")

        check(f"content.recommendations[{index}]", recommendation)
    for index, row in enumerate(data["scope_notes"]):
        check(f"content.scope_notes[{index}].evidence", lambda row=row: known(row["evidence"], model.paths))
    for index, row in enumerate(data["verifier_review"]["checks"]):

        def review_check(row=row):
            unique(row["id"])
            known([row["task_id"]], model.tasks)
            known([row["definition"]], model.paths)
            known(row["finding_ids"], findings)
            if row["receipt"] is not None:
                _kind(model, row["receipt"], "receipt")
            if row["applicability"] == "not_applicable" and row["receipt"] is not None:
                raise ValueError("A not-applicable check cannot claim an executed receipt")

        check(f"content.verifier_review.checks[{index}]", review_check)
    if not errors:
        check("content.verifier_review", lambda: _controls(model))
    for path, value in prose_fields(data, visible_only=True):
        for match in FINDING_REFERENCE.finditer(value):
            if match[1] not in findings:
                errors.append(
                    issue(
                        path,
                        "unknown_finding_reference",
                        f"{match[0]} does not match a finding ID. Use the ID of an existing finding or remove the reference",
                    )
                )
    return errors


def _manifest_task(model: Content, value: dict) -> Path | None:
    name = value.get("task_dir")
    if not isinstance(name, str):
        return None
    path = Path(name)
    return (path if path.is_absolute() else model.repo / path).resolve()


def _controls(model: Content):
    if not model.tasks:
        return
    core = core_module()
    task_paths = [model.source(task["source"]) for task in model.tasks.values()]
    review = core.Review(model.repo, list(model.paths.values()))
    selected = set(task_paths)
    for identifier, source in model.sources.items():
        if source["kind"] == "receipt":
            model.controls.append(
                core.inspect_receipt(
                    review, model.source(identifier), model.values[identifier], selected, verify_historical=True
                )
            )
        elif source["kind"] == "manifest":
            review.manifests[model.source(identifier)] = model.values[identifier]
    for path, manifest in list(review.manifests.items()):
        model.controls.extend(core.inspect_manifest(review, path, manifest, selected, model.controls))
    for task in task_paths:
        relative = task.relative_to(model.repo).as_posix()
        if not any(row.get("task") == relative and row["classification"] != "agent_run" for row in model.controls):
            model.controls.append(
                {
                    "task": relative,
                    "path": relative,
                    "classification": "unproven",
                    "reason": "No supported control receipts found for this task",
                }
            )
    model.evidence = [{"path": path, "digest": digest} for path, digest in sorted(review.evidence.items())]
    for record in model.controls:
        failed = record["classification"] in {"failed_control", "false_rejection"} or record.get(
            "historical_classification"
        ) in {"failed_control", "false_rejection"}
        if not failed:
            continue
        explained = any(
            any(model.sources[identifier]["path"] == record["path"] for identifier in finding["evidence"])
            and any(
                model.sources[model.tasks[ref["task_id"]]["source"]]["path"] == record.get("task")
                and ref["revision"] == record.get("task_revision")
                for ref in finding["tasks"]
            )
            for finding in model.data["findings"]
        )
        if not explained:
            raise ValueError(f"Add an explained finding tied to the task revision and receipt {record['path']}")
    for check in model.data["verifier_review"]["checks"]:
        if check["receipt"] is not None:
            path = model.sources[check["receipt"]]["path"]
            task_path = model.sources[model.tasks[check["task_id"]]["source"]]["path"]
            if not any(row.get("path") == path and row.get("task") == task_path for row in model.controls):
                raise ValueError(f"Check {check['id']} receipt belongs to a different task")
    for finding in model.data["findings"]:
        if (
            finding["category"] != "verifier_cheating"
            or finding["basis"] != "executed_control"
            or finding["status"] == "unproven"
        ):
            continue
        receipts = {
            model.sources[identifier]["path"]
            for identifier in finding["evidence"]
            if model.sources[identifier]["kind"] == "receipt"
        }
        matching = [
            row
            for row in model.controls
            if row.get("path") in receipts
            and any(
                model.sources[model.tasks[ref["task_id"]]["source"]]["path"] == row.get("task")
                and ref["revision"] == row.get("task_revision")
                for ref in finding["tasks"]
            )
        ]
        if not any(
            row["classification"] == "failed_control"
            or row["classification"] == "stale"
            and row.get("historical_classification") == "failed_control"
            for row in matching
        ):
            raise ValueError(f"Finding {finding['id']} needs failed cheat-control evidence for its task revision")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--content", type=Path, default=Path(CONTENT_PATH))
    parser.add_argument("--lint-prose", action="store_true")
    parser.add_argument("--source-digest", type=str, help="Read the digest of one selected repository-relative source")
    parser.add_argument("--task-tree", action="store_true", help="Use the native task revision digest")
    args = parser.parse_args(argv)
    if args.task_tree and args.source_digest is None:
        parser.error("--task-tree requires --source-digest")
    try:
        if args.source_digest is not None:
            source = local(args.repo.resolve(strict=True), args.source_digest)
            digest = core_module().task_evidence.task_digest(source) if args.task_tree else sha(read_bytes(source))
            result = {"valid": True, "path": args.source_digest, "sha256": digest}
        else:
            result = load(args.content, args.repo, lint=args.lint_prose).result()
    except ContentError as exc:
        print(json.dumps({"valid": False, "errors": exc.errors}, indent=2))
        return 1
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({"valid": False, "errors": [issue("content", "environment", str(exc))]}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
