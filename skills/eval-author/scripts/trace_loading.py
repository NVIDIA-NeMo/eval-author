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
import math
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trace_ingest import Trace

# The reviewed Compass release; trace contract v3 and its loaders first shipped in 0.2.1.
LOADER = {"distribution": "nemo-compass", "version": "0.2.1"}
TRACE_SCHEMA_VERSION = "3"
RECEIPT_SCHEMA = "nemo.eval_author.trace_load.v2"
SOURCE_KINDS = ("atif", "mlflow", "gym", "intake", "trace")
DEFAULT_MAX_TRACES = 1000
MAX_RECORD_BYTES = 128 * 1024 * 1024
# Compass losses that leave every recorded step of the run loaded. The trace keeps them, so
# consumers still see a subagent Compass could not resolve (it never reads external ATIF
# ``trajectory_path`` files, which Harbor's Terminus-2 writes for summarization subagents)
# or steps missing from a nested agent's turns. Every other loss, including a code this
# pin does not know, means part of the run is missing.
ACCEPTED_LOSS_CODES = frozenset({"llm_calls_aggregated", "unresolved_subagent_ref", "steps_outside_agent_not_in_turns"})
# Content-free reasons for the partial-run losses the pinned Compass emits.
INCOMPLETE_LOSSES = {
    "merge_incomplete": "a session could not be loaded completely",
    "continued_trajectory_not_followed": "an ATIF trajectory continues in a file that was not loaded",
}
# Distributions that install their own top-level ``trace_ingest`` package.
_SHADOWING = ("trace-ingest", "insight-agent")


class TraceLoadError(ValueError):
    """A source cannot be loaded into Compass traces under eval-author's bounds."""


class TraceLimitError(TraceLoadError):
    """A source holds more traces than ``max_traces``."""


def pinned_requirement() -> str:
    return f"{LOADER['distribution']}[mlflow]=={LOADER['version']}"


def check_installation() -> None:
    """Require the pinned Compass release, installed from a package index."""

    pinned = pinned_requirement()
    try:
        distribution = importlib.metadata.distribution(LOADER["distribution"])
    except importlib.metadata.PackageNotFoundError:
        raise TraceLoadError(f"install {pinned}") from None
    # direct_url.json marks a Git, local-directory, editable or URL install, whose code the
    # version number does not identify.
    if distribution.version != LOADER["version"] or distribution.read_text("direct_url.json") is not None:
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
    max_traces: int = DEFAULT_MAX_TRACES,
    row: int | None = None,
    intake: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> list[Trace]:
    """Load every trace in ``source`` (or the Intake selection), or only ``trace_id``; never truncate."""

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
            traces = _load_trace_file(source)
    if len(traces) > max_traces:
        raise TraceLimitError("source contains more than max_traces traces")
    if not traces:
        raise TraceLoadError("source contains no traces")
    if trace_id is not None:
        traces = [trace for trace in traces if trace.id == trace_id]
        if len(traces) != 1:
            raise TraceLoadError("--trace-id must match exactly one trace in the source")
    _refuse_incomplete(traces)
    return traces


def _refuse_incomplete(traces: list[Trace]) -> None:
    """Refuse a run Compass loaded only in part, so nothing downstream reads it as whole."""
    codes = {loss.code for trace in traces for loss in trace.losses} - ACCEPTED_LOSS_CODES
    if not codes:
        return
    reasons = sorted({INCOMPLETE_LOSSES.get(code, "the trace records an unrecognized loss") for code in codes})
    raise TraceLoadError(f"source is incomplete: {'; '.join(reasons)}")


def _load_atif(source: Path) -> list[Trace]:
    from trace_ingest.loaders.atif import ATIFTraceConfig, ATIFTraceLoader
    from trace_ingest.source_links import file_source_url

    if source.suffix.lower() != ".json":
        _check_jsonl_keys(source)
        return list(ATIFTraceLoader(ATIFTraceConfig(path=source)).load())
    # Compass reads ATIF as JSONL. A ``.json`` file, such as Harbor's pretty-printed
    # agent/trajectory.json, holds one trajectory: load it as a single compact line.
    raw = source.read_bytes()
    if len(raw) > MAX_RECORD_BYTES:
        raise TraceLoadError("ATIF trajectory exceeds the raw-byte limit")
    trajectory = _strict_json(raw)
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


def _strict_json(raw: bytes | str) -> Any:
    """Parse JSON, refusing non-finite numbers and repeated object keys."""
    return json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float, object_pairs_hook=_unique_keys)


def _check_jsonl_keys(source: Path) -> None:
    """Refuse repeated keys on any non-blank line, split as the Compass JSONL loaders split it.

    Compass keeps the last of repeated JSON keys, which would drop the earlier values silently.
    """
    with source.open("rb") as lines:
        for raw in lines:
            if raw.strip():
                _strict_json(raw)


def _reject_constant(value: str) -> Any:
    raise TraceLoadError(f"non-finite JSON number {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise TraceLoadError("non-finite JSON number")
    return number


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise TraceLoadError("JSON object repeats a key")
    return value


def _load_mlflow(source: Path, max_traces: int) -> list[Trace]:
    # MLflow logs an instruction to coding agents on import; keep it out of callers' output.
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    from trace_ingest.loaders.mlflow import MLflowFileTraceConfig, MLflowFileTraceLoader

    # Mirror Compass's parse: one JSON document, else one record per line.
    text = source.read_bytes().decode("utf-8")
    try:
        _strict_json(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            if line.strip():
                _strict_json(line)
    loader = MLflowFileTraceLoader(MLflowFileTraceConfig(path=source, max_traces=max_traces))
    traces = list(loader.load())
    description = loader.describe()
    if description["truncated"]:
        raise TraceLimitError("source contains more than max_traces traces")
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


def _load_trace_file(source: Path) -> list[Trace]:
    from trace_ingest.loaders.fs import FSDataLoader

    _check_jsonl_keys(source)
    return list(FSDataLoader(path=source).load())


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


def _snapshot(source: Path, directory: Path, kind: str) -> tuple[Path, str]:
    """Copy ``source`` into ``directory`` as a new private file; return it and the digest of the bytes copied.

    ATIF and Gym loaders choose whole-file JSON (".json") or JSONL by suffix; the copy keeps it.
    """
    suffix = ".json" if source.suffix.lower() == ".json" else ".jsonl"
    path = directory / f"source.{kind}{suffix}"
    digest = hashlib.sha256()
    with source.open("rb") as reader, path.open("xb") as writer:
        os.chmod(writer.name, 0o600)
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    return path, "sha256:" + digest.hexdigest()


def load_snapshot(source: Path, source_kind: str, **load_kwargs: Any) -> tuple[list[Trace], str]:
    """Load a private snapshot of ``source``; return its traces and the ``sha256:`` digest of the bytes loaded.

    Traces and spans cite ``source``, not the deleted snapshot.
    """
    from trace_ingest.source_links import file_source_url

    with tempfile.TemporaryDirectory() as staging:
        directory = Path(staging)
        directory.chmod(0o700)
        snapshot, digest = _snapshot(source, directory, source_kind)
        traces = load_traces(snapshot, source_kind, **load_kwargs)
        staged, original = file_source_url(snapshot), file_source_url(source)
    pending: list[Any] = list(traces)
    while pending:
        item = pending.pop()
        url = item.source_url
        if url is not None and (url == staged or url.startswith(staged + "#")):
            item.source_url = original + url[len(staged) :]
        pending.extend(item.root_spans if hasattr(item, "root_spans") else item.children)
    return traces, digest


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
    output: Path = args.output_dir
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        output.chmod(0o700)
        # Load the private copy, so the recorded digest covers exactly the bytes loaded.
        copy, source_sha256 = _snapshot(args.input, output, kind) if args.input is not None else (None, None)
        traces = load_traces(
            copy, kind, max_traces=args.max_traces, row=args.row, intake=intake, trace_id=args.trace_id
        )
        normalized = "".join(trace.model_dump_json() + "\n" for trace in traces).encode()
        _write_private(output / "traces.normalized.jsonl", normalized)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "source_kind": kind,
            "loader": LOADER,
            "trace_schema_version": traces[0].schema_version,
            "source_sha256": {copy.name: source_sha256} if copy is not None else {},
            "normalized_sha256": _sha256(normalized),
            "trace_count": len(traces),
            "max_traces": args.max_traces,
            "row": args.row,
            "trace_id": args.trace_id,
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
    parser.add_argument("--trace-id", help="Keep only the trace with this id; it must match exactly one trace")
    parser.add_argument("--intake-base-url", help="Intake service URL, without credentials")
    parser.add_argument("--workspace", help="Intake workspace")
    parser.add_argument("--started-after", type=_aware, help="Intake window start, ISO 8601 with timezone")
    parser.add_argument("--started-before", type=_aware, help="Intake window end, ISO 8601 with timezone")
    parser.add_argument("--session-id", help="Load only this Intake session")
    parser.add_argument(
        "--max-traces", type=int, default=DEFAULT_MAX_TRACES, help="Reject larger sources instead of truncating"
    )
    parser.add_argument("--output-dir", required=True, type=Path, help="New private directory to create")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Third-party diagnostics may contain source values; keep CLI output content-free.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            receipt = write_load(args)
    except TraceLoadError as error:
        # These messages name the failed check, never source content.
        print(json.dumps({"status": "error", "message": f"{args.source_kind} loading failed: {error}"}))
        return 2
    except Exception:
        message = f"{args.source_kind} loading failed; check input and pinned dependencies"
        print(json.dumps({"status": "error", "message": message}))
        return 2
    print(json.dumps({"status": "loaded", "traces": receipt["trace_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
