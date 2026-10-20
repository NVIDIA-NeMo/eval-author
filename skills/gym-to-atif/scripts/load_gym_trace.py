#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load one Gym attachment through Trace Intel; this does not emit ATIF."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import shutil
import tempfile
from pathlib import Path

from gym_to_atif import read_source

VERSION = "0.1.0"


def load(input_path: Path, output: Path, row: int | None = None) -> dict:
    """Preserve the selected raw record, normalized evidence, and loader provenance."""
    distribution = importlib.metadata.distribution("nemo-compass")
    provenance = json.loads(distribution.read_text("direct_url.json") or "{}")
    if distribution.version != VERSION or "vcs_info" in provenance or "dir_info" in provenance:
        raise ValueError("install nemo-compass==0.1.0 from PyPI or its release wheel")
    for legacy in ("trace-ingest", "insight-agent"):
        try:
            importlib.metadata.distribution(legacy)
        except importlib.metadata.PackageNotFoundError:
            pass
        else:
            raise ValueError(f"remove {legacy} and reinstall nemo-compass==0.1.0")
    from trace_ingest.loaders.gym import GymTraceConfig, GymTraceLoader

    raw = read_source(input_path, row)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(dir=output) as staging:
            source = Path(staging) / "source.gym.json"
            source.write_bytes(raw)
            snapshot = GymTraceLoader(GymTraceConfig(path=source, format="json")).load()
            traces = [trace.model_dump(mode="json") for trace in snapshot]
        if len(traces) != 1:
            raise ValueError("expected one normalized rollout")
        normalized = (json.dumps(traces[0], ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        receipt = {
            "schema": "eval-author.trace-intel-load.v1",
            "provider": "gym",
            "loader_distribution": "nemo-compass",
            "loader_revision": None,
            "loader_version": distribution.version,
            "selected_line": row,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "normalized_sha256": hashlib.sha256(normalized).hexdigest(),
            "format": "trace_ingest.Trace",
            "atif_emitted": False,
        }
        for name, data in (
            ("source.gym.json", raw),
            ("trace.normalized.json", normalized),
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
    parser.add_argument("--row", type=int, help="One-based physical JSONL line, required for JSONL")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        # Third-party diagnostics may contain source values; keep CLI output content-free.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            load(args.input, args.output_dir, args.row)
    except Exception:
        print(json.dumps({"status": "error", "message": "Gym loading failed; check input and pinned dependencies"}))
        return 2
    print(json.dumps({"status": "loaded", "traces": 1, "atif_emitted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
