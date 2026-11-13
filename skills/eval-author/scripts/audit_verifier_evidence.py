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
from verification_integrity import (
    ACCEPTANCE_DETECTOR,
    CLAIMS,
    new_integrity,
    project_integrity,
    receipt_state,
    required_verification,
)


class EvidenceScopeError(ValueError):
    pass


class Review:
    # Subclasses that cannot read the current task tree report freshness as unknown.
    measures_task = True

    def __init__(self, repo: Path, roots: list[Path], *, identity: dict | None = None):
        self.repo = repo.resolve()
        self.roots = [self.path(path) for path in roots]
        self.evidence: dict[str, str] = {}
        self.task_digests: dict[Path, str] = {}
        self.manifests: dict[Path, dict] = {}
        self.identity = identity

    def relocated(self, path: Path) -> Path:
        """Map an absolute path saved by the repository's earlier location onto this repository.

        Receipts and manifests record absolute paths, and their bytes are bound by digest, so they
        cannot be rewritten after the repository is copied or moved. The longest suffix that
        exists here is used. The target is still checked against the repository, the selected
        evidence roots, and the digests the receipts record.
        """
        if path.is_absolute() and not path.resolve().is_relative_to(self.repo):
            for start in range(1, len(path.parts)):
                candidate = self.repo.joinpath(*path.parts[start:])
                if candidate.exists():
                    return candidate
        return path

    def path(self, value: str | Path) -> Path:
        path = Path(value)
        path = self.relocated(path) if path.is_absolute() else self.repo / path
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise EvidenceScopeError(f"Evidence must not traverse symlinks: {path}")
        path = path.resolve()
        if not path.is_relative_to(self.repo):
            raise EvidenceScopeError(f"Evidence is outside the repository: {path}")
        return path

    def read(self, path: Path) -> bytes:
        path = self.path(path)
        if self.identity is None and not any(path.is_relative_to(root) for root in self.roots):
            raise EvidenceScopeError(f"Select the evidence source before reading: {path}")
        if self.identity is not None:
            from rubric_identity import artifact_byte_limit, read_selected

            relative = path.relative_to(self.repo).as_posix()
            matches = [record for record in self.identity["records"] if record["path"] == relative]
            if not matches:
                raise EvidenceScopeError("identity.reference_unselected")
            raw, reason = read_selected(self.repo, relative, min(artifact_byte_limit(record) for record in matches))
            if reason:
                raise ValueError("identity." + reason)
            if any(hashlib.sha256(raw).hexdigest() != record["digest"] for record in matches):
                raise ValueError("identity.digest_mismatch")
            self.evidence[relative] = hashlib.sha256(raw).hexdigest()
            return raw
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
    """Only links accompanied by prose in Key findings count. Unconverted legacy reports use Findings and limitations."""
    section = re.search(r"^## (?:Key findings|Findings and limitations)\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
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


def inspect_receipt(
    review: Review,
    path: Path,
    receipt: dict,
    tasks: set[Path],
    *,
    verify_historical: bool = False,
    include_integrity: bool = False,
) -> dict:
    claim = receipt.get("status") if receipt.get("status") in CLAIMS else None
    assessed = (
        receipt.get("schema") == task_evidence.SCHEMA
        and receipt.get("purpose") in ("closure", "baseline")
        and claim is not None
        and (
            type(receipt.get("returncode")) is int or (claim == "infrastructure_error" and "returncode" not in receipt)
        )
    )
    integrity = new_integrity("unassessed" if assessed else "unsupported")
    bound = receipt.get("manifest_sha256")
    if assessed:
        integrity.update(
            claim=claim,
            purpose=receipt["purpose"],
            manifest_sha256=bound if isinstance(bound, str) else None,
            status_claim="unknown" if claim == "passed" else "not_applicable",
        )

    def judge(basis, execution, behavior, consistent):
        """Only a passed claim can contradict. Other claims record agreement only."""
        integrity.update(basis=basis, execution=execution, behavior=behavior)
        if consistent:
            integrity.update(status="consistent", code="validated" if execution == "healthy" else "execution_error")
            if claim == "passed":
                integrity["status_claim"] = "consistent"
        elif claim == "passed":
            integrity.update(status="mismatched", status_claim="contradiction", code="status_contradiction")
        else:
            integrity.update(status="mismatched", code="status_mismatch")

    row: dict = {"path": path.relative_to(review.repo).as_posix(), "classification": "unproven"}
    if include_integrity:
        row["integrity"] = integrity
    try:
        manifest_path = review.path(receipt["manifest"])
        row["manifest"] = manifest_path.relative_to(review.repo).as_posix()
        manifest = review.object(manifest_path)
        if manifest.get("schema") not in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA):
            raise ValueError("Unsupported task manifest")
        review.manifests[manifest_path] = manifest
        task = review.path(manifest["task_dir"])
        if task not in tasks:
            if assessed:
                integrity.update(status_claim="excluded", code="out_of_scope")
            row.update(classification="out_of_scope", reason="Manifest belongs to an unselected task")
            return row
        row["task"] = task.relative_to(review.repo).as_posix()
        row["case"] = receipt.get("case")
        if hashlib.sha256(review.read(manifest_path)).hexdigest() != receipt.get("manifest_sha256"):
            supersedes = manifest.get("supersedes")
            if assessed and isinstance(supersedes, dict) and supersedes.get("manifest_sha256") == bound:
                # An in-place repair replaced these bytes. The receipt belongs to the older revision.
                integrity.update(status_claim="excluded", code="superseded", freshness="stale")
            raise ValueError("Manifest bytes differ from the receipt")
        contract = manifest["contract"]
        task_evidence.validate_contract(contract)
        case = contract["cases"][receipt["case"]]
        if case["kind"] in {"agent", "diagnostic"} or receipt.get("purpose") == "baseline":
            integrity.update(status_claim="excluded", code="excluded")
            assessed = False
        if case["kind"] == "agent":
            row.update(classification="agent_run", reason="Agent runs are reviewed through the selected trace evidence")
            return row
        row["kind"] = case["kind"]
        row["task_revision"] = manifest.get("task_sha256")
        historical = review.task_digest(task) != manifest.get("task_sha256")
        if review.measures_task:
            integrity["freshness"] = "stale" if historical else "current"
        if historical and not verify_historical:
            if assessed:
                integrity["code"] = "stale"
            row.update(classification="stale", reason="Task changed since this control ran")
            return row
        if receipt.get("status") == "infrastructure_error" or receipt.get("returncode") != 0:
            if assessed:
                judge("receipt", "execution_error", "unknown", claim == "infrastructure_error")
            raise ValueError("Control has no healthy completed execution")
        result_path = review.path(receipt["result"])
        native = review.object(result_path)
        if hashlib.sha256(review.read(result_path)).hexdigest() != receipt.get("result_sha256"):
            raise ValueError("Native result bytes differ from the receipt")
        if task_evidence.native_identity(native, manifest) != receipt.get("native_run_id"):
            raise ValueError("Native run identity differs from the receipt")
        task_evidence._validate_control(native, contract, case)
        if not task_evidence.assertions(native, case["health"]):
            if assessed:
                judge("native", "execution_error", "unknown", False)
            raise ValueError("Native control health checks failed")
        passed = task_evidence.assertions(native, case["checks"])
        expected_status = "passed" if passed else "behavior_failed"
        if assessed:
            judge("native", "healthy", "passed" if passed else "failed", claim == expected_status)
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
        if historical:
            row.update(
                classification="stale",
                historical_classification=classification,
                reason="Native assertions belong to an earlier task revision",
            )
        row["result"] = result_path.relative_to(review.repo).as_posix()
        row["checks"] = case["checks"]
    except EvidenceScopeError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        row["reason"] = str(exc)
        if integrity["code"] == "unassessed":
            integrity.update(
                status="unknown",
                code="source_missing" if isinstance(exc, OSError) else "invalid_evidence",
            )
    return row


def inspect_manifest(
    review: Review,
    path: Path,
    manifest: dict,
    tasks: set[Path],
    rows: list[dict],
    *,
    verify_historical: bool = False,
) -> list[dict]:
    row = {"path": path.relative_to(review.repo).as_posix(), "classification": "unproven"}
    try:
        task = review.path(manifest["task_dir"])
        if task not in tasks:
            return []
        row["task"] = task.relative_to(review.repo).as_posix()
        task_evidence.validate_contract(manifest["contract"])
        if not verify_historical and review.task_digest(task) != manifest.get("task_sha256"):
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


def _cited_outcome(row: dict) -> tuple[str, str] | None:
    """Classify one cited control receipt against a passed acceptance claim."""
    if row.get("classification") == "agent_run":
        return None
    integrity = row["integrity"]
    state = receipt_state(integrity)
    if integrity["code"] == "superseded":
        return "superseded_reliance", "violation"
    if integrity["behavior"] == "failed":
        return "cited_failure", "violation"
    if integrity["execution"] == "execution_error":
        return "cited_execution_error", "violation"
    if state in ("pass", "stale"):
        return ("cited_pass", "consistent") if integrity["freshness"] == "current" else ("cited_stale", "stale")
    return "cited_unverified", "unknown"


def _registered_closure(review: Review, manifest: dict, digest: str) -> list[Path] | None:
    """Return closure receipts registered for this manifest revision, or None if the ledger is unavailable."""
    ledger = review.path(manifest["attempts"])
    if not ledger.is_dir():
        return None
    registered = []
    for entry in sorted(ledger.iterdir()):
        if not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name):
            continue
        attempt = review.object(entry)
        # Match the producer's registration shape, including historical records without kind.
        required = {"run_id", "receipt", "purpose", "case", "manifest_sha256"}
        if (
            set(attempt) not in (required, required | {"kind"})
            or not all(isinstance(attempt[field], str) for field in required)
            or not attempt["run_id"].strip()
            or entry.stem != hashlib.sha256(attempt["run_id"].encode()).hexdigest()
            or not re.fullmatch(r"[0-9a-f]{64}", attempt["manifest_sha256"])
            or attempt["purpose"] not in ("baseline", "closure")
            or not attempt["receipt"]
            or (
                "kind" in attempt
                and attempt["kind"]
                not in ("reference", "incorrect", "empty", "alternative", "side_effect", "agent", "diagnostic")
            )
        ):
            raise ValueError("Malformed attempt registration")
        receipt = review.path(attempt["receipt"])
        if attempt["manifest_sha256"] != digest:
            continue
        case = manifest["contract"]["cases"].get(attempt["case"])
        if case is None or ("kind" in attempt and attempt["kind"] != case["kind"]):
            raise ValueError("Attempt registration differs from the declared case")
        if attempt["purpose"] == "closure" and case["kind"] not in ("agent", "diagnostic"):
            registered.append(receipt)
    return registered


def inspect_acceptance(review: Review, saved: dict) -> dict:
    """Compare a saved acceptance's passed validation claim with its authoritative records.

    A violation needs a definitive record: a cited receipt with failed behavior or
    execution, a registered closure failure the claim omits, or a cited receipt
    from a revision that a repair explicitly superseded. Unreadable or incomplete
    evidence stays unknown. Agent runs are reviewed through their traces instead.
    """
    result: dict = {"detector_version": ACCEPTANCE_DETECTOR, "claim": "unsupported", "ordering": "unknown"}
    evidence = saved.get("evidence")
    if (
        saved.get("schema") != "nemo.eval_author.task_gap_verification.v2"
        or saved.get("task_validation") != "passed"
        or not isinstance(evidence, list)
        or not all(isinstance(value, str) for value in evidence)
    ):
        return {
            **result,
            "status": "not_applicable",
            "eligible_claims": 0,
            "violations": 0,
            "violation_findings": 0,
            "findings": [],
        }
    findings, rows, manifests, cited = [], [], {}, set()

    def relative(path: Path) -> str:
        return path.relative_to(review.repo).as_posix()

    def inspect(path: Path) -> dict:
        try:
            receipt = review.object(path)
            manifest = review.object(review.path(receipt["manifest"]))
            return inspect_receipt(review, path, receipt, {review.path(manifest["task_dir"])}, include_integrity=True)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            code = "source_missing" if isinstance(exc, OSError) else "invalid_evidence"
            return {"path": relative(path), "classification": "unproven", "integrity": new_integrity(code)}

    for value in evidence:
        path = review.path(value)
        if path in cited:
            continue
        cited.add(path)
        row = inspect(path)
        rows.append(row)
        outcome = _cited_outcome(row)
        if outcome is not None:
            findings.append(
                {"code": outcome[0], "outcome": outcome[1], "receipt": row["path"], "case": row.get("case")}
            )
    for path, manifest in list(review.manifests.items()):
        if not any(row.get("manifest") == relative(path) for row in rows):
            continue
        try:
            digest = hashlib.sha256(review.read(path)).hexdigest()
            task_evidence.validate_contract(manifest["contract"])
            manifests[relative(path)] = manifest
            registered = _registered_closure(review, manifest, digest)
        except (OSError, ValueError, KeyError, TypeError):
            registered = None
        if registered is None:
            findings.append({"code": "ledger_unavailable", "outcome": "unknown", "receipt": None, "case": None})
            continue
        for receipt in registered:
            if receipt in cited:
                continue
            row = inspect(receipt)
            failed = row["integrity"]["behavior"] == "failed" or row["integrity"]["execution"] == "execution_error"
            findings.append(
                {
                    "code": "omitted_failure" if failed else "omitted_attempt",
                    "outcome": "violation" if failed else "unknown",
                    "receipt": row["path"],
                    "case": row.get("case"),
                }
            )
    verification = required_verification(manifests, rows)
    if not verification["required_cases"]:
        findings.append({"code": "missing_required", "outcome": "unknown", "receipt": None, "case": None})
    for case in verification["cases"]:
        if case["state"] == "missing":
            findings.append({"code": "missing_required", "outcome": "unknown", "receipt": None, "case": case["case"]})
    outcomes = {row["outcome"] for row in findings}
    return {
        **result,
        "claim": "passed",
        "status": next((value for value in ("violation", "unknown", "stale") if value in outcomes), "consistent"),
        "eligible_claims": 1,
        "violations": int("violation" in outcomes),
        "violation_findings": sum(row["outcome"] == "violation" for row in findings),
        "required_verification": verification,
        "agent_runs": "excluded",
        "findings": findings,
    }


def content_model(repo: Path, path: Path):
    import importlib.util
    import sys

    scripts = Path(__file__).resolve().parents[2] / "eval-author-audit/scripts/audit_spec"
    sys.path.insert(0, str(scripts))
    name = "report_content"
    module = sys.modules.get(name)
    if module is None or module.__file__ is None or Path(module.__file__).resolve() != scripts / "report_content.py":
        if not (scripts / "report_content.py").is_file():
            raise ValueError("Install the sibling eval-author-audit skill for structured report review")
        spec = importlib.util.spec_from_file_location(name, scripts / "report_content.py")
        if spec is None or spec.loader is None:
            raise ValueError("Install the sibling eval-author-audit skill for structured report review")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.load(path, repo)


def _account(repo: Path, selected: set[Path], rows: list[dict], links: set[Path], evidence: list[dict]) -> dict:
    required = []
    accounted = {"passed", "agent_run", "out_of_scope"}
    for row in rows:
        if row["classification"] not in accounted:
            row["reported"] = repo / row["path"] in links
            if row["classification"] in {"stale", "unproven"} and row.get("manifest"):
                row["reported"] |= repo / row["manifest"] in links
            if not row["reported"]:
                required.append(row["path"])
    for task in sorted(selected):
        if not any(
            row.get("task") == task.relative_to(repo).as_posix() and row["classification"] != "agent_run"
            for row in rows
        ):
            relative = task.relative_to(repo).as_posix()
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
        "evidence": evidence,
        "limits": "Checks retained native assertions and explained findings. An agent must review applicability, grading logic, and the accuracy of each explanation. This is not task qualification.",
    }


def _selected_manifests(review: Review, selected: set[Path]) -> dict[str, tuple[dict, str]]:
    """Return valid manifests for selected tasks. The control rows already report invalid ones."""
    manifests = {}
    for path, manifest in review.manifests.items():
        try:
            task_evidence.validate_contract(manifest["contract"])
            if review.path(manifest["task_dir"]) in selected:
                digest = hashlib.sha256(review.read(path)).hexdigest()
                manifests[path.relative_to(review.repo).as_posix()] = (manifest, digest)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return manifests


def check(
    repo: Path,
    report: Path,
    tasks: list[Path],
    evidence_dirs: list[Path],
    source_roots: list[Path],
    *,
    content: Path | None = None,
    selection: dict | None = None,
    include_integrity: bool = False,
) -> dict:
    preflight = {}
    identity = None
    repo = repo.resolve()
    if selection is not None:
        from rubric_identity import identify, resolve_route, validate_identity_selection

        if content is not None or evidence_dirs or source_roots:
            raise ValueError("An explicit selection cannot be combined with content or evidence directories")
        validate_identity_selection(selection)
        identity = identify(repo, selection["artifacts"])
        route = resolve_route(repo, selection["artifacts"], selection["route"])
        preflight = {
            "identity": identity,
            "route": route,
            "preflight_success": identity["status"] == route["status"] == "pass"
            and route.get("bound_dependency") is True,
        }
        if not preflight["preflight_success"]:
            return {"valid": False, "controls": [], "evidence": [], "mode": "selected", **preflight}
    if content is not None:
        model = content_model(repo.resolve(), content)
        selected = {model.source(task["source"]) for task in model.tasks.values()}
        requested = {(repo / task).resolve() for task in tasks}
        if tasks and requested != selected:
            raise ValueError("Selected tasks differ from the validated report content")
        links = {model.source(identifier) for finding in model.data["findings"] for identifier in finding["evidence"]}
        evidence = model.evidence
        return {**_account(model.repo, selected, model.controls, links, evidence), "mode": "content"}
    review = Review(repo, [*tasks, *evidence_dirs, *source_roots], identity=identity)
    if identity is not None:
        review.roots = [review.path(record["path"]) for record in identity["records"]]
    selected = {review.path(path) for path in tasks}
    if not selected or any(not path.is_dir() for path in selected):
        raise ValueError("Select existing task directories")
    report = review.path(report)
    links = findings_links(report, report.read_text())
    rows, scanned = [], set()
    evidence_paths = []
    for directory in evidence_dirs:
        directory = review.path(directory)
        if not directory.is_dir():
            raise ValueError(f"Evidence directory is missing: {directory}")
        evidence_paths.extend(sorted(directory.rglob("*.json")))
    if identity is not None:
        evidence_paths = [
            review.path(record["path"]) for record in identity["records"] if record["role"] in {"receipt", "manifest"}
        ]
    for path in evidence_paths:
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
            rows.append(inspect_receipt(review, path, payload, selected, include_integrity=include_integrity))
        elif payload.get("schema") in (task_evidence.SCHEMA, task_evidence.MANIFEST_SCHEMA) and "task_dir" in payload:
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
    accounting = _account(
        review.repo,
        selected,
        rows,
        links,
        [{"path": path, "digest": digest} for path, digest in sorted(review.evidence.items())],
    )
    if identity is not None:
        preflight["preflight_success"] = bool(rows) and all(
            row["classification"] in {"passed", "agent_run"} for row in rows
        )
    if include_integrity:
        manifests = _selected_manifests(review, selected)
        accounting.update(project_integrity(manifests, rows))
    return {
        **accounting,
        "mode": "selected" if identity is not None else "legacy",
        **preflight,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--content", type=Path)
    mode.add_argument("--report", type=Path, help="Explicit legacy Markdown report")
    parser.add_argument("--task", type=Path, action="append", default=[])
    parser.add_argument("--evidence-dir", type=Path, action="append", default=[])
    parser.add_argument("--source-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--selection", type=Path, help="Explicit artifact/route selection instead of evidence discovery"
    )
    parser.add_argument(
        "--integrity",
        action="store_true",
        help="Add typed receipt-consistency, required-control and supersession measurements",
    )
    args = parser.parse_args()
    try:
        selection = None
        if args.selection:
            from rubric_identity import _read_selected

            path = args.selection.absolute()
            raw, reason = _read_selected(path.parent, path.name)
            if reason:
                raise ValueError("identity.selection_invalid")
            selection = task_evidence.loads(raw)
            if not isinstance(selection, dict):
                raise ValueError("Select artifacts and a declared route")
        result = check(
            args.repo,
            args.report,
            args.task,
            args.evidence_dir,
            args.source_root,
            content=args.content,
            selection=selection,
            include_integrity=args.integrity,
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as exc:
        result = {"valid": False, "error": str(exc)}
        if args.selection:
            result["preflight_success"] = False
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] and result.get("preflight_success", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
