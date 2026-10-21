#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load provider traces into NeMo Compass ``Trace`` objects, optionally exporting ATIF.

This is eval-author's only trace-loading boundary. Provider parsing, normalization, and
ATIF export belong to the pinned ``nemo-compass`` distribution.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trace_ingest import Trace

COMPASS_DISTRIBUTION = "nemo-compass"
COMPASS_VERSION = "0.2.0"
RECEIPT_SCHEMA = "nemo.eval_author.trace_load.v1"
SOURCE_KINDS = ("atif", "mlflow", "gym", "intake", "trace")
MAX_GYM_RECORD_BYTES = 128 * 1024 * 1024
# Distributions that install their own top-level ``trace_ingest`` package.
_SHADOWING = ("trace-ingest", "insight-agent")


class TraceLoadError(ValueError):
    """A source cannot be loaded into Compass traces under eval-author's bounds."""


def check_installation() -> None:
    """Require the pinned PyPI release of NeMo Compass."""

    pinned = f"{COMPASS_DISTRIBUTION}=={COMPASS_VERSION}"
    try:
        distribution = importlib.metadata.distribution(COMPASS_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        raise TraceLoadError(f"install {pinned}") from None
    provenance = json.loads(distribution.read_text("direct_url.json") or "{}")
    if distribution.version != COMPASS_VERSION or "vcs_info" in provenance or "dir_info" in provenance:
        raise TraceLoadError(f"install {pinned}")
    for name in _SHADOWING:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        raise TraceLoadError(f"remove {name} and reinstall {pinned}")


def load_traces(
    source: Path,
    source_kind: str,
    *,
    max_traces: int = 1000,
    spans: Path | None = None,
    workspace: str | None = None,
    row: int | None = None,
) -> list[Trace]:
    """Load every trace in ``source``; never truncate."""

    if source_kind not in SOURCE_KINDS:
        raise TraceLoadError(f"unsupported source kind {source_kind!r}")
    if max_traces < 1:
        raise TraceLoadError("max_traces must be at least 1")
    if source_kind != "intake" and (spans is not None or workspace is not None):
        raise TraceLoadError("--spans and --workspace apply only to intake sources")
    if source_kind != "gym" and row is not None:
        raise TraceLoadError("--row applies only to gym sources")
    check_installation()
    if source_kind == "atif":
        from trace_ingest.loaders.atif import ATIFTraceConfig, ATIFTraceLoader

        traces = list(ATIFTraceLoader(ATIFTraceConfig(path=source)).load())
    elif source_kind == "mlflow":
        traces = _load_mlflow(source, max_traces)
    elif source_kind == "gym":
        traces = _load_gym(source, row)
    elif source_kind == "intake":
        if spans is None or not workspace:
            raise TraceLoadError("intake sources require spans and workspace")
        from trace_ingest.loaders.intake import IntakeFileTraceConfig, IntakeFileTraceLoader

        config = IntakeFileTraceConfig(trace_path=source, spans_path=spans, workspace=workspace)
        traces = list(IntakeFileTraceLoader(config).load())
    else:
        from trace_ingest.loaders.fs import FSDataLoader

        traces = list(FSDataLoader(path=source).load())
    if len(traces) > max_traces:
        raise TraceLoadError("source contains more than max_traces traces")
    if not traces:
        raise TraceLoadError("source contains no traces")
    return traces


def _load_mlflow(source: Path, max_traces: int) -> list[Trace]:
    from trace_ingest.loaders.mlflow import MLflowFileTraceConfig, MLflowFileTraceLoader

    loader = MLflowFileTraceLoader(MLflowFileTraceConfig(path=source, max_traces=max_traces))
    traces = list(loader.load())
    description = loader.describe()
    if description["truncated"]:
        raise TraceLoadError("source contains more than max_traces traces")
    if description["continuation_token_present"] or description["unresolved_parent_count"]:
        raise TraceLoadError("MLflow export is incomplete")
    return traces


def _load_gym(source: Path, row: int | None) -> list[Trace]:
    from trace_ingest.loaders.gym import GymTraceConfig, GymTraceLoader

    if source.suffix.lower() == ".json":
        if row is not None:
            raise TraceLoadError("--row applies only to JSONL gym sources")
        return list(GymTraceLoader(GymTraceConfig(path=source, format="json")).load())
    if row is None or row < 1:
        raise TraceLoadError("JSONL gym sources require a positive one-based row")
    record = gym_record(source, row)
    with tempfile.TemporaryDirectory() as staging:
        selected = Path(staging) / "record.json"
        selected.write_bytes(record)
        return list(GymTraceLoader(GymTraceConfig(path=selected, format="json")).load())


def gym_record(source: Path, row: int) -> bytes:
    """Return the bytes of one physical, one-based JSONL line."""

    with source.open("rb") as stream:
        for _ in range(row):
            data = stream.readline(MAX_GYM_RECORD_BYTES + 1)
            if not data:
                raise TraceLoadError("selected JSONL row does not exist")
            if len(data) > MAX_GYM_RECORD_BYTES:
                raise TraceLoadError("JSONL record exceeds the raw-byte limit")
    if not data.strip():
        raise TraceLoadError("selected JSONL row is empty")
    return data


def export_atif(trace: Trace, *, agent_name: str | None, agent_version: str | None) -> dict[str, Any]:
    """Export one trace as ATIF through the pinned Compass exporter."""

    check_installation()
    from trace_ingest.exporters.atif import to_atif

    return to_atif(trace, agent_name=agent_name, agent_version=agent_version)


def safe_filename(trace_id: str, suffix: str = ".atif.json") -> str:
    """Create a stable restricted filename without trusting an external trace ID as a path."""

    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", trace_id).strip("._-")[:80] or "trace"
    digest = hashlib.sha256(trace_id.encode()).hexdigest()[:12]
    return f"{slug}-{digest}{suffix}"


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        os.chmod(stream.name, 0o600)
        stream.write(data)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def write_load(args: argparse.Namespace) -> dict[str, Any]:
    """Load the source and write private source copies, normalized traces, ATIF, and a receipt."""

    kind = args.source_kind
    # ATIF and Gym loaders choose whole-file JSON (".json") or JSONL by suffix; the copy keeps it.
    suffix = ".json" if args.input.suffix.lower() == ".json" else ".jsonl"
    sources = [(f"source.{kind}{suffix}", args.input)]
    if kind == "intake":
        sources = [("source.intake-trace.json", args.input)]
        if args.spans is not None:
            sources.append(("source.intake-spans.json", args.spans))
    copies = [(name, path.read_bytes()) for name, path in sources]
    output: Path = args.output_dir
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        output.chmod(0o700)
        for name, data in copies:
            _write_private(output / name, data)
        copied = {name: output / name for name, _ in copies}
        traces = load_traces(
            copied[sources[0][0]],
            kind,
            max_traces=args.max_traces,
            spans=copied.get("source.intake-spans.json"),
            workspace=args.workspace,
            row=args.row,
        )
        normalized = "".join(trace.model_dump_json() + "\n" for trace in traces).encode()
        _write_private(output / "traces.normalized.jsonl", normalized)
        exported = []
        if args.emit_atif:
            (output / "atif").mkdir(mode=0o700)
            for trace in traces:
                name = safe_filename(trace.id)
                data = _json_bytes(export_atif(trace, agent_name=args.agent_name, agent_version=args.agent_version))
                _write_private(output / "atif" / name, data)
                exported.append({"trace_id": trace.id, "path": f"atif/{name}", "sha256": _sha256(data)})
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "source_kind": kind,
            "loader_distribution": COMPASS_DISTRIBUTION,
            "loader_version": COMPASS_VERSION,
            "trace_schema_version": traces[0].schema_version,
            "source_sha256": {name: _sha256(data) for name, data in copies},
            "normalized_sha256": _sha256(normalized),
            "trace_count": len(traces),
            "max_traces": args.max_traces,
            "atif_emitted": bool(args.emit_atif),
            "atif": exported,
        }
        _write_private(output / "loading.json", _json_bytes(receipt))
        return receipt
    except BaseException:
        shutil.rmtree(output)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-kind", required=True, choices=SOURCE_KINDS)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--spans", type=Path, help="Intake spans JSON from `nemo intake spans list`")
    parser.add_argument("--workspace", help="Intake workspace that owns the trace")
    parser.add_argument("--row", type=int, help="One-based physical JSONL line, required for Gym JSONL")
    parser.add_argument("--max-traces", type=int, default=1000, help="Reject larger sources instead of truncating")
    parser.add_argument("--output-dir", required=True, type=Path, help="New private directory to create")
    parser.add_argument("--emit-atif", action="store_true", help="Also export each trace as ATIF")
    parser.add_argument("--agent-name", help="Agent name recorded in ATIF for non-ATIF sources")
    parser.add_argument("--agent-version", default="unknown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Third-party diagnostics may contain source values; keep CLI output content-free.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            receipt = write_load(args)
    except Exception:
        message = f"{args.source_kind} loading failed; check input and pinned dependencies"
        print(json.dumps({"status": "error", "message": message}))
        return 2
    print(json.dumps({"status": "loaded", "traces": receipt["trace_count"], "atif_emitted": receipt["atif_emitted"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
