#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load provider traces into NeMo Compass ``Trace`` objects.

This is eval-author's only trace-loading boundary. Provider parsing and normalization
belong to the pinned ``nemo-compass`` build.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trace_ingest import Trace

# Trace contract v3 and its loaders are not in a PyPI release yet, so this pins the
# reviewed Compass commit. Return to a PyPI version pin with the next release.
LOADER = {
    "distribution": "nemo-compass",
    "version": "0.2.0",
    "revision": "a78dc416da247a9f4bcff17881c5fd20e90a7256",
}
COMPASS_REPOSITORY = "https://github.com/NVIDIA-NeMo/nemo-compass.git"
TRACE_SCHEMA_VERSION = "3"
RECEIPT_SCHEMA = "nemo.eval_author.trace_load.v2"
SOURCE_KINDS = ("atif", "mlflow", "gym", "intake", "trace")
MAX_RECORD_BYTES = 128 * 1024 * 1024
# Distributions that install their own top-level ``trace_ingest`` package.
_SHADOWING = ("trace-ingest", "insight-agent")


class TraceLoadError(ValueError):
    """A source cannot be loaded into Compass traces under eval-author's bounds."""


def pinned_requirement() -> str:
    return f"{LOADER['distribution']}[mlflow] @ git+{COMPASS_REPOSITORY}@{LOADER['revision']}"


def check_installation() -> None:
    """Require the pinned Compass build, installed from its Git revision."""

    pinned = pinned_requirement()
    try:
        distribution = importlib.metadata.distribution(LOADER["distribution"])
    except importlib.metadata.PackageNotFoundError:
        raise TraceLoadError(f"install {pinned}") from None
    provenance = json.loads(distribution.read_text("direct_url.json") or "{}")
    vcs = provenance.get("vcs_info") or {}
    if (
        distribution.version != LOADER["version"]
        or vcs.get("commit_id") != LOADER["revision"]
        or provenance.get("url") != COMPASS_REPOSITORY
    ):
        raise TraceLoadError(f"install {pinned}")
    for name in _SHADOWING:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        raise TraceLoadError(f"remove {name} and reinstall {pinned}")
    from trace_ingest import TRACE_SCHEMA_VERSION as installed

    if installed != TRACE_SCHEMA_VERSION:
        raise TraceLoadError(f"install {pinned}")


def load_traces(
    source: Path | None,
    source_kind: str,
    *,
    max_traces: int = 1000,
    row: int | None = None,
    intake: dict[str, Any] | None = None,
) -> list[Trace]:
    """Load every trace in ``source`` (or the Intake selection); never truncate."""

    if source_kind not in SOURCE_KINDS:
        raise TraceLoadError(f"unsupported source kind {source_kind!r}")
    if max_traces < 1:
        raise TraceLoadError("max_traces must be at least 1")
    if (source_kind == "intake") != (intake is not None) or (source_kind == "intake") == (source is not None):
        raise TraceLoadError("intake loads a live selection; every other kind loads an input file")
    if source_kind != "gym" and row is not None:
        raise TraceLoadError("--row applies only to gym sources")
    check_installation()
    if source_kind == "intake":
        assert intake is not None
        traces = _load_intake(intake, max_traces)
    else:
        assert source is not None
        if source_kind == "atif":
            traces = _load_atif(source)
        elif source_kind == "mlflow":
            traces = _load_mlflow(source, max_traces)
        elif source_kind == "gym":
            traces = _load_gym(source, row)
        else:
            from trace_ingest.loaders.fs import FSDataLoader

            traces = list(FSDataLoader(path=source).load())
    if len(traces) > max_traces:
        raise TraceLoadError("source contains more than max_traces traces")
    if not traces:
        raise TraceLoadError("source contains no traces")
    return traces


def _load_atif(source: Path) -> list[Trace]:
    from trace_ingest.loaders.atif import ATIFTraceConfig, ATIFTraceLoader
    from trace_ingest.source_links import file_source_url

    if source.suffix.lower() != ".json":
        return list(ATIFTraceLoader(ATIFTraceConfig(path=source)).load())
    # Compass reads ATIF as JSONL. A ``.json`` file, such as Harbor's pretty-printed
    # agent/trajectory.json, holds one trajectory: load it as a single compact line.
    raw = source.read_bytes()
    if len(raw) > MAX_RECORD_BYTES:
        raise TraceLoadError("ATIF trajectory exceeds the raw-byte limit")
    trajectory = json.loads(raw, parse_constant=_reject_constant)
    if not isinstance(trajectory, dict):
        raise TraceLoadError("an ATIF .json file must hold one trajectory object")
    line = json.dumps(trajectory, ensure_ascii=False, separators=(",", ":"))
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / "trajectory.jsonl"
        staged.write_text(line + "\n", encoding="utf-8")
        traces = list(ATIFTraceLoader(ATIFTraceConfig(path=staged)).load())
    # Cite the input file, not the staging file deleted above.
    for trace in traces:
        trace.source_url = file_source_url(source)
    return traces


def _reject_constant(value: str) -> Any:
    raise TraceLoadError(f"non-finite JSON number {value}")


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
    from trace_ingest.source_links import file_source_url

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
        traces = list(GymTraceLoader(GymTraceConfig(path=selected, format="json")).load())
    # Cite the selected line of the source, not the staging file deleted above.
    for trace in traces:
        trace.source_url = file_source_url(source, row)
    return traces


def _load_intake(selection: dict[str, Any], max_traces: int) -> list[Trace]:
    from trace_ingest.loaders.intake import (
        IntakeLoadError,
        IntakeTraceLoader,
        IntakeTraceLoaderConfig,
        IntakeTraceQuery,
    )

    query = IntakeTraceQuery(
        started_at_gte=selection["started_after"],
        started_at_lte=selection["started_before"],
        session_id=selection.get("session_id"),
        # One more than the limit, so an oversized selection is rejected, not truncated.
        max_traces=max_traces + 1,
    )
    config = IntakeTraceLoaderConfig(base_url=selection["base_url"], workspace=selection["workspace"], query=query)
    try:
        return list(IntakeTraceLoader(config).load())
    except IntakeLoadError as error:
        raise TraceLoadError("Intake selection could not be loaded") from error


def gym_record(source: Path, row: int) -> bytes:
    """Return the bytes of one physical, one-based JSONL line."""

    with source.open("rb") as stream:
        for _ in range(row):
            data = stream.readline(MAX_RECORD_BYTES + 1)
            if not data:
                raise TraceLoadError("selected JSONL row does not exist")
            if len(data) > MAX_RECORD_BYTES:
                raise TraceLoadError("JSONL record exceeds the raw-byte limit")
    if not data.strip():
        raise TraceLoadError("selected JSONL row is empty")
    return data


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        os.chmod(stream.name, 0o600)
        stream.write(data)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def _intake_selection(args: argparse.Namespace) -> dict[str, Any] | None:
    fields = ("intake_base_url", "workspace", "started_after", "started_before", "session_id")
    given = {name for name in fields if getattr(args, name) is not None}
    if args.source_kind != "intake":
        if given:
            raise TraceLoadError("Intake selection arguments apply only to intake sources")
        return None
    if args.input is not None:
        raise TraceLoadError("intake loads a live selection; omit --input")
    if not {"intake_base_url", "workspace", "started_after", "started_before"} <= given:
        raise TraceLoadError("intake requires --intake-base-url, --workspace, --started-after and --started-before")
    return {
        "base_url": args.intake_base_url,
        "workspace": args.workspace,
        "started_after": args.started_after,
        "started_before": args.started_before,
        "session_id": args.session_id,
    }


def write_load(args: argparse.Namespace) -> dict[str, Any]:
    """Load the source and write private source copies, normalized traces, and a receipt."""

    kind = args.source_kind
    intake = _intake_selection(args)
    if intake is None and args.input is None:
        raise TraceLoadError("--input is required")
    sources: list[tuple[str, Path]] = []
    if args.input is not None:
        # ATIF and Gym loaders choose whole-file JSON (".json") or JSONL by suffix; the copy keeps it.
        suffix = ".json" if args.input.suffix.lower() == ".json" else ".jsonl"
        sources = [(f"source.{kind}{suffix}", args.input)]
    copies = [(name, path.read_bytes()) for name, path in sources]
    output: Path = args.output_dir
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        output.chmod(0o700)
        for name, data in copies:
            _write_private(output / name, data)
        traces = load_traces(
            output / copies[0][0] if copies else None,
            kind,
            max_traces=args.max_traces,
            row=args.row,
            intake=intake,
        )
        normalized = "".join(trace.model_dump_json() + "\n" for trace in traces).encode()
        _write_private(output / "traces.normalized.jsonl", normalized)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "source_kind": kind,
            "loader": LOADER,
            "trace_schema_version": traces[0].schema_version,
            "source_sha256": {name: _sha256(data) for name, data in copies},
            "normalized_sha256": _sha256(normalized),
            "trace_count": len(traces),
            "max_traces": args.max_traces,
            "row": args.row,
            "intake": None
            if intake is None
            else {
                **intake,
                "started_after": intake["started_after"].isoformat(),
                "started_before": intake["started_before"].isoformat(),
            },
        }
        _write_private(output / "loading.json", _json_bytes(receipt))
        return receipt
    except BaseException:
        shutil.rmtree(output)
        raise


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-kind", required=True, choices=SOURCE_KINDS)
    parser.add_argument("--input", type=Path, help="Source file; every kind except intake")
    parser.add_argument("--row", type=int, help="One-based physical JSONL line, required for Gym JSONL")
    parser.add_argument("--intake-base-url", help="Intake service URL, without credentials")
    parser.add_argument("--workspace", help="Intake workspace")
    parser.add_argument("--started-after", type=_aware, help="Intake window start, ISO 8601 with timezone")
    parser.add_argument("--started-before", type=_aware, help="Intake window end, ISO 8601 with timezone")
    parser.add_argument("--session-id", help="Load only this Intake session")
    parser.add_argument("--max-traces", type=int, default=1000, help="Reject larger sources instead of truncating")
    parser.add_argument("--output-dir", required=True, type=Path, help="New private directory to create")
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
    print(json.dumps({"status": "loaded", "traces": receipt["trace_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
