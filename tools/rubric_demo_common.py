# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared local measurement transport; callers retain their evidence policy."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, TypedDict

ROOT = Path(__file__).resolve().parents[1]
MEASURE = ROOT / "skills/eval-author-rubric/scripts/measure.py"


class ArtifactEvidence(TypedDict):
    path: str
    digest: str


class ArtifactRecord(TypedDict):
    id: str
    path: str
    digest: str
    role: str
    revision: str | None
    access: str
    source: str
    selection_origin: str
    evidence: list[ArtifactEvidence]


def write_json(path: Path, value: Any, *, sort_keys: bool = False) -> None:
    path.write_text(json.dumps(value, sort_keys=sort_keys, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def artifact_record(
    directory: Path,
    ident: str,
    path: str,
    role: str,
    *,
    revision: str | None,
    source: str,
    selected_digest: str | None = None,
) -> ArtifactRecord:
    actual = hashlib.sha256((directory / path).read_bytes()).hexdigest()
    return {
        "id": ident,
        "path": path,
        "digest": actual if selected_digest is None else selected_digest,
        "role": role,
        "revision": revision,
        "access": "available",
        "source": source,
        "selection_origin": "explicit",
        "evidence": [{"path": path, "digest": actual}],
    }


def run_measure(
    directory: Path,
    *,
    selection_name: str = "selection.json",
    prefix: str = "measure",
    verify: bool = False,
    validator: Path | None = None,
    record_invocation: bool = False,
    sort_keys: bool = False,
    error_message: str = "measurement failed; inspect retained measurement output",
) -> tuple[dict[str, Any], int]:
    command = [
        sys.executable,
        "-B",
        str(MEASURE),
        "--root",
        str(directory),
        "--selection",
        str(directory / selection_name),
    ]
    if validator is not None:
        command += ["--ethos-validator", str(validator)]
    if verify:
        command += ["--verify-existing", str(directory / f"{prefix}-report.json")]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    log = prefix + ("-verify" if verify else "")
    (directory / f"{log}.stdout.json").write_text(result.stdout, encoding="utf-8")
    (directory / f"{log}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if record_invocation:
        write_json(
            directory / f"{log}-invocation.json",
            {
                "command": command,
                "returncode": result.returncode,
                "engine": "actual-measure.py-cli",
            },
            sort_keys=sort_keys,
        )
    report = json.loads(result.stdout)
    if (
        result.returncode not in (0, 1)
        or not isinstance(report, dict)
        or report.get("schema") != "rubric-measurements-v3"
    ):
        raise ValueError(error_message)
    if not verify:
        write_json(directory / f"{prefix}-report.json", report, sort_keys=sort_keys)
    return report, result.returncode
