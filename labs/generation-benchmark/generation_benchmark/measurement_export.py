# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Export subset measurement artifacts for the generation adapter."""

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from metric_benchmark.io import write_json

from generation_benchmark.eval_author.harbor import load_agent_config
from generation_benchmark.eval_author.identity import file_digest, tree_digest


def _bind_audit_sources(audit_md: Path, case_root: Path) -> None:
    text = audit_md.read_text(encoding="utf-8")
    for match in re.finditer(r"^(\s*path:\s*)(.+)$", text, flags=re.MULTILINE):
        raw = match.group(2).strip()
        if not raw:
            continue
        if not raw.startswith("/") and ".." not in raw and (audit_md.parent / raw).is_file():
            continue
        source = case_root / Path(raw).name
        if source.is_file():
            text = text.replace(match.group(0), f"{match.group(1)}{source.resolve()}")
    audit_md.write_text(text, encoding="utf-8")


def _load_audit_spec(audit_md: Path, eval_author_root: Path) -> dict[str, Any]:
    audit_spec_dir = eval_author_root / "skills" / "eval-author-audit" / "scripts" / "audit_spec"
    module_path = audit_spec_dir / "_schema.py"
    spec = importlib.util.spec_from_file_location("generation_audit_schema", module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import audit schema loader from {module_path}")
    module = importlib.util.module_from_spec(spec)
    audit_spec_str = str(audit_spec_dir)
    if audit_spec_str not in sys.path:
        sys.path.insert(0, audit_spec_str)
    spec.loader.exec_module(module)
    return module.load_audit_spec(audit_md)


def _sync_binding_audit_digest(
    report: dict[str, Any], audit_md: Path, eval_author_root: Path
) -> None:
    binding = report.get("binding")
    if not isinstance(binding, dict):
        return
    audit = _load_audit_spec(audit_md, eval_author_root)
    binding["audit_sha256"] = hashlib.sha256(
        json.dumps(audit, sort_keys=True).encode()
    ).hexdigest()


def _resolve_binding_source(coverage_work_cell: Path, absolute_path: str, relative_path: str) -> Path:
    candidate = Path(absolute_path).expanduser()
    if candidate.is_file():
        return candidate
    relative = coverage_work_cell / relative_path
    if relative.is_file():
        return relative
    raise ValueError(f"coverage binding artifact missing: {absolute_path} ({relative_path})")


def _bundle_coverage_report(
    coverage_work_cell: Path,
    report_src: Path,
    output_dir: Path,
    *,
    case_root: Path | None,
    eval_author_root: Path,
) -> Path:
    """Copy binding artifacts into ``output_dir`` and rewrite report paths to match."""
    report = json.loads(report_src.read_text(encoding="utf-8"))
    binding = report.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("coverage report binding must be an object")
    artifacts = binding.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("coverage report binding.artifacts must be an object")
    remapped: dict[str, str] = {}
    path_updates: dict[str, str] = {}
    for absolute_path, relative_path in artifacts.items():
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("coverage binding artifact relative path must be a non-empty string")
        source = _resolve_binding_source(coverage_work_cell, str(absolute_path), relative_path)
        destination = output_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        resolved = str(destination.resolve())
        remapped[resolved] = relative_path
        path_updates[str(absolute_path)] = resolved
    binding["artifacts"] = remapped
    inputs = binding.get("inputs")
    if isinstance(inputs, list):
        for entry in inputs:
            if not isinstance(entry, dict):
                continue
            previous = str(entry.get("path", ""))
            if previous in path_updates:
                entry["path"] = path_updates[previous]
    audit = report.get("audit")
    if isinstance(audit, dict) and isinstance(audit.get("path"), str):
        previous = audit["path"]
        if previous in path_updates:
            audit["path"] = path_updates[previous]
    if case_root is not None:
        for relative in ("audit.md", "authored/audit.md"):
            audit_md = output_dir / relative
            if audit_md.is_file():
                _bind_audit_sources(audit_md, case_root.resolve())
        authored_audit = output_dir / "authored" / "audit.md"
        if authored_audit.is_file():
            _sync_binding_audit_digest(report, authored_audit, eval_author_root)
    report_dst = output_dir / "audit-coverage-report.json"
    write_json(report_dst, report)
    return report_dst


def export_subset_measurement(
    *,
    coverage_work_cell: Path,
    suite_root: Path,
    agent_config_path: Path,
    output_dir: Path,
    score_value: float | None,
    eval_author_root: Path,
    case_root: Path | None = None,
) -> Path:
    """Build the eval-author measurement bundle from a staged coverage work cell."""
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_src = coverage_work_cell / "authored" / "audit.md"
    if not audit_src.is_file():
        raise ValueError("coverage work cell missing authored/audit.md")
    report_src = coverage_work_cell / "report.json"
    if not report_src.is_file():
        raise ValueError("coverage work cell missing report.json")
    audit_dst = output_dir / "audit.md"
    shutil.copy2(audit_src, audit_dst)
    report_dst = _bundle_coverage_report(
        coverage_work_cell,
        report_src,
        output_dir,
        case_root=case_root,
        eval_author_root=eval_author_root,
    )
    agent = load_agent_config(agent_config_path)
    assert agent is not None
    skill_dir = eval_author_root / "skills" / "eval-author-audit"
    static_identity = {
        "suite": tree_digest(suite_root),
        "audit": file_digest(audit_dst),
        "skill": tree_digest(skill_dir),
        "agent": agent.digest,
        "method": "tool_calls",
        "convert_legacy_harbor": False,
        "trace_inputs": [],
    }
    measurement: dict[str, Any] = {
        "schema": "nemo.eval_author.experiment_2.measurement.v1",
        "status": "complete",
        "cached": False,
        "suite": str(suite_root.resolve()),
        "audit": str(audit_dst.resolve()),
        "skill": str(skill_dir.resolve()),
        "agent_config": str(agent_config_path.resolve()),
        "identity": {},
        "static_identity": static_identity,
        "method": "tool_calls",
        "metric": score_value,
        "coverage_report": str(report_dst.resolve()),
        "coverage_report_digest": file_digest(report_dst),
    }
    path = output_dir / "measurement.json"
    write_json(path, measurement)
    return path
