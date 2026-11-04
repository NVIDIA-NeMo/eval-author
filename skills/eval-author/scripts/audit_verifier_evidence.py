# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check that an audit accounts for selected, retained task-control evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from eval_author_shared import task_evidence


class EvidenceScopeError(ValueError):
    pass


class Review:
    def __init__(self, repo: Path, roots: list[Path]):
        self.repo = repo.resolve()
        self.roots = [self.path(path) for path in roots]
        self.evidence: dict[str, str] = {}
        self.task_digests: dict[Path, str] = {}
        self.manifests: dict[Path, dict] = {}

    def path(self, value: str | Path) -> Path:
        path = Path(value)
        path = path if path.is_absolute() else self.repo / path
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise EvidenceScopeError(f"Evidence must not traverse symlinks: {path}")
        path = path.resolve()
        if not path.is_relative_to(self.repo):
            raise EvidenceScopeError(f"Evidence is outside the repository: {path}")
        return path

    def read(self, path: Path) -> bytes:
        path = self.path(path)
        if not any(path.is_relative_to(root) for root in self.roots):
            raise EvidenceScopeError(f"Select the evidence source before reading: {path}")
        if path.stat().st_size > 8_000_000:
            raise ValueError(f"Evidence exceeds 8 MB: {path}")
        raw = path.read_bytes()
        self.evidence[path.relative_to(self.repo).as_posix()] = hashlib.sha256(raw).hexdigest()
        return raw

    def object(self, path: Path) -> dict:
        value = task_evidence.loads(self.read(path))
        if not isinstance(value, dict):
            raise ValueError(f"Expected an evidence object: {path}")
        return value

    def task_digest(self, task: Path) -> str:
        if task not in self.task_digests:
            self.task_digests[task] = task_evidence.task_digest(task)
        return self.task_digests[task]


def findings_links(report: Path, text: str) -> set[Path]:
    """Only links accompanied by prose in Findings and limitations count."""
    section = re.search(r"^## Findings and limitations\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if section is None:
        return set()
    content = re.sub(r"<!--.*?-->|```.*?```|~~~.*?~~~", "", section[1], flags=re.S)
    links = set()
    pattern = r"\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)"
    for paragraph in re.split(r"\n\s*\n", content):
        prose = re.sub(pattern, "", paragraph)
        if not re.search(r"[a-zA-Z]", prose) or prose.lstrip().startswith("#"):
            continue
        for match in re.finditer(pattern, paragraph):
            target = urlsplit(match[1].strip("<>"))
            if not target.scheme and not target.netloc and target.path:
                links.add((report.parent / unquote(target.path)).resolve())
    return links


def inspect_receipt(review: Review, path: Path, receipt: dict, tasks: set[Path]) -> dict:
    row = {"path": path.relative_to(review.repo).as_posix(), "classification": "unproven"}
    try:
        manifest_path = review.path(receipt["manifest"])
        row["manifest"] = manifest_path.relative_to(review.repo).as_posix()
        manifest = review.object(manifest_path)
        if manifest.get("schema") not in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA):
            raise ValueError("Unsupported task manifest")
        review.manifests[manifest_path] = manifest
        task = review.path(manifest["task_dir"])
        if task not in tasks:
            row.update(classification="out_of_scope", reason="Manifest belongs to an unselected task")
            return row
        row["task"] = task.relative_to(review.repo).as_posix()
        row["case"] = receipt.get("case")
        if hashlib.sha256(review.read(manifest_path)).hexdigest() != receipt.get("manifest_sha256"):
            raise ValueError("Manifest bytes differ from the receipt")
        contract = manifest["contract"]
        task_evidence.validate_contract(contract)
        case = contract["cases"][receipt["case"]]
        if case["kind"] == "agent":
            row.update(classification="agent_run", reason="Agent runs are reviewed through the selected trace evidence")
            return row
        row["kind"] = case["kind"]
        if review.task_digest(task) != manifest.get("task_sha256"):
            row.update(classification="stale", reason="Task changed since this control ran")
            return row
        if receipt.get("status") == "infrastructure_error" or receipt.get("returncode") != 0:
            raise ValueError("Control has no healthy completed execution")
        result_path = review.path(receipt["result"])
        native = review.object(result_path)
        if hashlib.sha256(review.read(result_path)).hexdigest() != receipt.get("result_sha256"):
            raise ValueError("Native result bytes differ from the receipt")
        if task_evidence.native_identity(native, manifest) != receipt.get("native_run_id"):
            raise ValueError("Native run identity differs from the receipt")
        task_evidence._validate_control(native, contract, case)
        if not task_evidence.assertions(native, case["health"]):
            raise ValueError("Native control health checks failed")
        passed = task_evidence.assertions(native, case["checks"])
        expected_status = "passed" if passed else "behavior_failed"
        if receipt.get("status") != expected_status:
            raise ValueError("Receipt status disagrees with native assertions")
        if case["kind"] == "diagnostic":
            classification = "diagnostic"
        elif passed:
            classification = "passed"
        elif case["kind"] in {"reference", "alternative"}:
            classification = "false_rejection"
        else:
            classification = "failed_control"
        row.update(classification=classification, reason="Native assertions " + ("passed" if passed else "failed"))
        row["result"] = result_path.relative_to(review.repo).as_posix()
        row["checks"] = case["checks"]
    except EvidenceScopeError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row["reason"] = str(exc)
    return row


def inspect_manifest(review: Review, path: Path, manifest: dict, tasks: set[Path], rows: list[dict]) -> list[dict]:
    row = {"path": path.relative_to(review.repo).as_posix(), "classification": "unproven"}
    try:
        task = review.path(manifest["task_dir"])
        if task not in tasks:
            return []
        row["task"] = task.relative_to(review.repo).as_posix()
        task_evidence.validate_contract(manifest["contract"])
        if review.task_digest(task) != manifest.get("task_sha256"):
            row.update(classification="stale", reason="This manifest describes an earlier task revision")
            return [row]
        missing = [
            case
            for case, definition in manifest["contract"]["cases"].items()
            if definition["kind"] not in {"agent", "diagnostic"}
            and not any(item.get("manifest") == row["path"] and item.get("case") == case for item in rows)
        ]
        if missing:
            row.update(reason="Declared controls have no retained receipts", cases=missing)
            return [row]
    except EvidenceScopeError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row["reason"] = str(exc)
        return [row]
    return []


def check(repo: Path, report: Path, tasks: list[Path], evidence_dirs: list[Path], source_roots: list[Path]) -> dict:
    review = Review(repo, [*tasks, *evidence_dirs, *source_roots])
    selected = {review.path(path) for path in tasks}
    if not selected or any(not path.is_dir() for path in selected):
        raise ValueError("Select existing task directories")
    report = review.path(report)
    links = findings_links(report, report.read_text())
    rows, scanned = [], set()
    for directory in evidence_dirs:
        directory = review.path(directory)
        if not directory.is_dir():
            raise ValueError(f"Evidence directory is missing: {directory}")
        for path in sorted(directory.rglob("*.json")):
            if path in scanned:
                continue
            scanned.add(path)
            if len(scanned) > 1000:
                raise ValueError("Select narrower evidence directories (maximum 1000 JSON files)")
            raw = review.read(path)
            try:
                payload = task_evidence.loads(raw)
            except ValueError:
                rows.append(
                    {
                        "path": path.relative_to(review.repo).as_posix(),
                        "classification": "unproven",
                        "reason": "Malformed evidence JSON",
                    }
                )
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("schema") == task_evidence.SCHEMA and "manifest" in payload:
                rows.append(inspect_receipt(review, path, payload, selected))
            elif (
                payload.get("schema") in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA) and "task_dir" in payload
            ):
                review.manifests[path] = payload
            elif str(payload.get("schema", "")).startswith("nemo.eval_author.task_evidence."):
                rows.append(
                    {
                        "path": path.relative_to(review.repo).as_posix(),
                        "classification": "unproven",
                        "reason": "Malformed or unsupported task-evidence record",
                    }
                )
    for path, manifest in review.manifests.items():
        rows.extend(inspect_manifest(review, path, manifest, selected, rows))
    required = []
    accounted = {"passed", "agent_run", "out_of_scope"}
    for row in rows:
        if row["classification"] not in accounted:
            row["reported"] = review.path(row["path"]) in links
            if row["classification"] in {"stale", "unproven"} and row.get("manifest"):
                row["reported"] |= review.path(row["manifest"]) in links
            if not row["reported"]:
                required.append(row["path"])
    for task in sorted(selected):
        if not any(
            row.get("task") == task.relative_to(review.repo).as_posix() and row["classification"] != "agent_run"
            for row in rows
        ):
            relative = task.relative_to(review.repo).as_posix()
            reported = task in links
            rows.append(
                {
                    "task": relative,
                    "classification": "unproven",
                    "reason": "No supported control receipts found for this task",
                    "reported": reported,
                }
            )
            if not reported:
                required.append(relative)
    return {
        "valid": not required,
        "missing_findings": required,
        "controls": rows,
        "evidence": [{"path": path, "digest": digest} for path, digest in sorted(review.evidence.items())],
        "limits": "Checks retained native assertions and finding links. An agent must review applicability, grading logic, and the accuracy of each explanation. This is not task qualification.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--task", type=Path, action="append", required=True)
    parser.add_argument("--evidence-dir", type=Path, action="append", default=[])
    parser.add_argument("--source-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        result = check(args.repo, args.report, args.task, args.evidence_dir, args.source_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"valid": False, "error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
