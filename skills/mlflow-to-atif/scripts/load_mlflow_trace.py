#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load a bounded local MLflow export through Trace Intel; this does not emit ATIF."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

REVISION = "ac6170678755f4dd134635e699f0f970b35fe4bb"
VERSION = "0.1.0rc1"


def load(input_path: Path, output: Path, max_traces: int) -> dict:
    """Normalize a complete export, rejecting truncation or unresolved parents."""
    distribution = importlib.metadata.distribution("insight-agent")
    provenance = json.loads(distribution.read_text("direct_url.json") or "{}")
    revision = provenance.get("vcs_info", {}).get("commit_id")
    if distribution.version != VERSION or ("vcs_info" in provenance and revision != REVISION):
        raise ValueError("install the documented insight-agent version or revision")
    if "dir_info" in provenance:
        raise ValueError("install the documented insight-agent wheel or Git revision")
    try:
        importlib.metadata.distribution("trace-ingest")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise ValueError("remove the standalone trace-ingest distribution and reinstall insight-agent")
    from trace_ingest.loaders.mlflow import MLflowFileTraceConfig, MLflowFileTraceLoader

    raw = input_path.read_bytes()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        source = output / "source.mlflow.json"
        with source.open("xb") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(raw)
        loader = MLflowFileTraceLoader(MLflowFileTraceConfig(path=source, max_traces=max_traces))
        snapshot = loader.load()
        description = loader.describe()
        if any(description[key] for key in ("continuation_token_present", "truncated", "unresolved_parent_count")):
            raise ValueError("export is incomplete or exceeds the requested bound")
        traces = [trace.model_dump(mode="json") for trace in snapshot]
        if not traces:
            raise ValueError("export has no traces")
        normalized = (json.dumps(traces, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        receipt = {
            "schema": "eval-author.trace-intel-load.v1",
            "provider": "mlflow",
            "loader_distribution": "insight-agent",
            "loader_revision": revision,
            "loader_version": distribution.version,
            "trace_count": len(traces),
            "max_traces": max_traces,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
            "format": "list[trace_ingest.Trace]",
            "atif_emitted": False,
        }
        for name, data in (
            ("traces.normalized.json", normalized),
            ("loading.json", (json.dumps(receipt, indent=2) + "\n").encode()),
        ):
            with (output / name).open("xb") as stream:
                os.chmod(stream.name, 0o600)
                stream.write(data)
        return receipt
    except Exception:
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--max-traces", required=True, type=int, help="Reject larger exports instead of truncating")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            receipt = load(args.input, args.output_dir, args.max_traces)
    except Exception:
        print(json.dumps({"status": "error", "message": "MLflow loading failed; check input and pinned dependencies"}))
        return 2
    print(json.dumps({"status": "loaded", "traces": receipt["trace_count"], "atif_emitted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
