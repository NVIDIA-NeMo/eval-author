#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Report how much code authored in trace tool calls also appears in declared sources."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TypeAlias

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _schema import AuditEnvironmentError  # noqa: E402
from measurements.trace_tools import collect_tool_call_inputs  # noqa: E402


def _load_measure() -> ModuleType:
    """Load the sibling measure.py by path; eval-author-rubric ships another top-level `measure` module."""
    name = "eval_author_audit_measure"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("measure.py"))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


measure = _load_measure()

JsonObject: TypeAlias = dict[str, Any]

PROVENANCE_SCHEMA = "nemo.eval_author.audit_code_provenance.v1"
PROVENANCE_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "audit_code_provenance.schema.json"
OUTPUT_NAME = "provenance.json"
MIN_LINE_CHARS = 10
SOURCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
COMMENT_LINE = re.compile(r"^(#|//|/\*|\*/|\*(\s|$)|<!--|-->)")
IMPORT_LINE = re.compile(r"^(import\s|from\s+\S+\s+import\s|package\s|using\s|use\s)")


class ProvenanceSourceError(ValueError):
    """Raised when a declared source cannot be read as text."""


@dataclass(frozen=True)
class Source:
    """One declared file or directory and its normalized, non-trivial lines."""

    name: str
    path: Path
    kind: str
    sha256: str
    file_count: int
    lines: frozenset[str]

    def to_binding(self) -> JsonObject:
        return {
            "name": self.name,
            "path": str(self.path),
            "kind": self.kind,
            "sha256": self.sha256,
            "file_count": self.file_count,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trace", type=Path, action="append", default=[], help="trace file holding exactly one trace; repeatable"
    )
    parser.add_argument(
        "--trial-dir",
        type=Path,
        action="append",
        default=[],
        help="Harbor trial directory containing agent/trajectory.json; repeatable",
    )
    parser.add_argument(
        "--source-kind",
        choices=measure.SOURCE_KINDS,
        help="format of every --trace (default: atif); --trial-dir always reads agent/trajectory.json as atif",
    )
    parser.add_argument("--task-id", help="task id to stamp; only with a single trace")
    parser.add_argument("--run-id", help="run id to stamp; only with a single trace")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="file or directory whose text the agent could copy, such as a skills directory or prompt file; repeatable",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help=f"directory where task=<id>/run=<id>/{OUTPUT_NAME} will be written",
    )
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)

    try:
        sources = [load_source(value) for value in _source_args(args.source)]
        subjects = _subjects(args)
        reports = []
        for pending, source_kind in subjects:
            loaded = measure._load_trace(pending.trace_path, source_kind)
            subject = measure._finalize_subject(pending, loaded, source_kind=source_kind, evidence_class=None)
            reports.append((subject, provenance_report(loaded, subject, sources)))
        for _, report in reports:
            measure._validate_report(report, schema_path=PROVENANCE_SCHEMA_PATH, label="code provenance")
        written = _write_reports(reports, out_dir=args.out_dir, compact=args.compact)
    except AuditEnvironmentError as exc:
        _print({"valid": None, "written": False, "error_type": "environment", "error": str(exc)}, args.compact)
        return 2
    except ProvenanceSourceError as exc:
        _print({"valid": False, "written": False, "error_type": "source", "error": str(exc)}, args.compact)
        return 1
    except measure.AuditTraceError as exc:
        _print({"valid": True, "written": False, "error_type": "trace", "error": str(exc)}, args.compact)
        return 1
    except measure.AuditMeasurementError as exc:
        _print({"valid": True, "written": False, "error_type": "measurement", "error": str(exc)}, args.compact)
        return 1

    _print({"valid": True, "written": True, "provenance": written}, args.compact)
    return 0


def _source_args(values: list[str]) -> list[tuple[str, Path]]:
    if not values:
        raise ProvenanceSourceError("pass at least one --source NAME=PATH")
    parsed: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not path or not SOURCE_NAME.fullmatch(name):
            raise ProvenanceSourceError(f"--source must be NAME=PATH with NAME matching {SOURCE_NAME.pattern}")
        if name in parsed:
            raise ProvenanceSourceError(f"duplicate --source name {name!r}")
        parsed[name] = Path(path)
    return list(parsed.items())


def _subjects(args: argparse.Namespace) -> list[tuple[Any, str]]:
    """Resolve subjects exactly as measure.py does, so provenance and coverage name runs alike."""
    count = len(args.trace) + len(args.trial_dir)
    if count == 0:
        raise measure.AuditTraceError("provide --trace or --trial-dir")
    if count > 1 and (args.task_id or args.run_id):
        raise measure.AuditMeasurementError("--task-id and --run-id apply only to a single trace")
    if args.trial_dir and args.source_kind not in (None, "atif"):
        raise measure.AuditMeasurementError("--trial-dir reads agent/trajectory.json as atif; omit --source-kind")
    ids = {"task_id": args.task_id, "run_id": args.run_id}
    return [(measure._subject_from_trace(path, **ids), args.source_kind or "atif") for path in args.trace] + [
        (measure._subject_from_trial_dir(path, **ids), "atif") for path in args.trial_dir
    ]


def load_source(value: tuple[str, Path]) -> Source:
    """Read one declared source; a directory includes its non-hidden UTF-8 files."""
    name, path = value
    path = path.resolve()
    try:
        if path.is_file():
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ProvenanceSourceError(f"source {name!r} is not UTF-8 text: {path}") from None
            return Source(name, path, "file", hashlib.sha256(raw).hexdigest(), 1, _line_set([text]))
        if not path.is_dir():
            raise ProvenanceSourceError(f"source {name!r} does not exist: {path}")
        texts = _directory_texts(path)
    except OSError as exc:
        raise ProvenanceSourceError(f"could not read source {name!r} at {path}: {exc}") from exc
    if not texts:
        raise ProvenanceSourceError(f"source {name!r} contains no UTF-8 text files: {path}")
    return Source(
        name,
        path,
        "directory",
        _directory_digest(texts),
        len(texts),
        _line_set([raw.decode("utf-8") for raw in texts.values()]),
    )


def source_digest(path: Path) -> str:
    """Return the digest `load_source` binds for *path* (a file or a directory)."""
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise ProvenanceSourceError(f"source does not exist: {path}")
    return _directory_digest(_directory_texts(path))


def _directory_texts(root: Path) -> dict[str, bytes]:
    """Map each non-hidden UTF-8 file's POSIX path relative to *root* to its bytes; symlinks are not followed."""
    texts: dict[str, bytes] = {}
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [entry for entry in subdirs if not entry.startswith(".")]
        for filename in names:
            file = Path(directory, filename)
            if filename.startswith(".") or file.is_symlink() or not file.is_file():
                continue
            raw = file.read_bytes()
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            texts[file.relative_to(root).as_posix()] = raw
    return texts


def _directory_digest(texts: dict[str, bytes]) -> str:
    """SHA-256 of sha256sum-style `<hex>  <relative path>` lines for the included files, sorted by path."""
    manifest = "".join(f"{hashlib.sha256(texts[name]).hexdigest()}  {name}\n" for name in sorted(texts))
    return hashlib.sha256(manifest.encode()).hexdigest()


def _line_set(texts: list[str]) -> frozenset[str]:
    return frozenset(line for text in texts for line in code_lines(text))


def code_lines(text: str) -> Iterator[str]:
    """Yield normalized, non-trivial lines of *text*."""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not is_trivial(line):
            yield line


def is_trivial(line: str) -> bool:
    """Short, comment-only, import, and symbol-only lines carry no evidence of copying."""
    return (
        len(line) < MIN_LINE_CHARS
        or not any(character.isalnum() for character in line)
        or COMMENT_LINE.match(line) is not None
        or IMPORT_LINE.match(line) is not None
    )


def argument_strings(value: Any) -> Iterator[str]:
    """Yield string values from tool-call arguments, decoding JSON-encoded objects and arrays."""
    if isinstance(value, str):
        decoded = _json_container(value)
        if decoded is None:
            yield value
        else:
            yield from argument_strings(decoded)
    elif isinstance(value, dict):
        for item in value.values():
            yield from argument_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from argument_strings(item)


def _json_container(value: str) -> dict | list | None:
    stripped = value.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict | list) else None


def provenance_report(loaded: Any, subject: Any, sources: list[Source]) -> JsonObject:
    """Count authored lines per tool call in measure.py's loaded trace and the lines each source also contains."""
    names = [source.name for source in sources]
    totals = {"authored_lines": 0, "copied_lines": 0, "by_source": dict.fromkeys(names, 0)}
    calls = collect_tool_call_inputs(loaded.trace)
    entries = []
    for call, arguments in calls:
        authored = copied = 0
        by_source = dict.fromkeys(names, 0)
        for text in argument_strings(arguments):
            for line in code_lines(text):
                authored += 1
                matched = [source.name for source in sources if line in source.lines]
                copied += bool(matched)
                for name in matched:
                    by_source[name] += 1
        if authored == 0:
            continue
        totals["authored_lines"] += authored
        totals["copied_lines"] += copied
        for name, count in by_source.items():
            totals["by_source"][name] += count
        entries.append(
            {
                **call,
                "authored_lines": authored,
                "copied_lines": copied,
                "matched_lines_by_source": {name: count for name, count in by_source.items() if count},
            }
        )
    return {
        "schema": PROVENANCE_SCHEMA,
        "subject": subject.to_json(),
        "totals": {
            "tool_call_count": len(calls),
            "authored_lines": totals["authored_lines"],
            "copied_lines": totals["copied_lines"],
            "copied_share": _share(totals["copied_lines"], totals["authored_lines"]),
            "matched_lines_by_source": totals["by_source"],
        },
        "tool_calls": entries,
        "binding": {
            "trace_sha256": subject.trace_sha256,
            "sources": [source.to_binding() for source in sources],
        },
    }


def _share(copied: int, authored: int) -> float | None:
    return round(copied / authored, 4) if authored else None


def _write_reports(reports: list[tuple[Any, JsonObject]], *, out_dir: Path, compact: bool) -> list[JsonObject]:
    """Write one provenance.json per trace after every report validated."""
    paths = [
        out_dir
        / measure._path_component("task", subject.task_id)
        / measure._path_component("run", subject.run_id)
        / OUTPUT_NAME
        for subject, _ in reports
    ]
    if len(set(paths)) != len(paths):
        raise measure.AuditMeasurementError("two traces resolve to the same task and run; pass distinct traces")
    written = []
    for path, (subject, report) in zip(paths, reports, strict=True):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(measure._json(report, compact=compact) + "\n", encoding="utf-8")
        except OSError as exc:
            raise AuditEnvironmentError(f"could not write code provenance under {path.parent}: {exc}") from exc
        totals = report["totals"]
        written.append(
            {
                "path": str(path),
                "task_id": subject.task_id,
                "run_id": subject.run_id,
                "authored_lines": totals["authored_lines"],
                "copied_lines": totals["copied_lines"],
                "copied_share": totals["copied_share"],
                "matched_lines_by_source": totals["matched_lines_by_source"],
            }
        )
    return written


def _print(payload: JsonObject, compact: bool) -> None:
    print(measure._json(payload, compact=compact))


if __name__ == "__main__":
    raise SystemExit(main())
