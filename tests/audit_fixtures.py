# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real bound coverage fixtures for task scaffolding integration tests."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def bound_gap(root: Path, output: Path, tool: str, *, source: Path | None = None) -> Path:
    source = source or Path(__file__).resolve().parents[1]
    skill = source / "skills/eval-author-audit"
    ethos = root / "ETHOS.md"
    ethos.write_text(f"# Ethos\n\n## Tools\n\n- {tool}\n")
    audit = root / ".eval-author/audit.md"
    audit.parent.mkdir(exist_ok=True)
    audit.write_text(
        (skill / "templates/audit.md")
        .read_text()
        .replace("customer.lookup", tool)
        .replace(
            'sha256: "sha256:<replace-with-64-hex-digest>"',
            f"sha256: sha256:{hashlib.sha256(ethos.read_bytes()).hexdigest()}",
        )
    )
    trace = root / "baseline.atif.json"
    trace.write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "session_id": "baseline",
                "agent": {"name": "fixture", "version": "1"},
                "steps": [{"step_id": 1, "source": "user", "message": "Read the record."}],
            }
        )
    )
    measurements = root / "baseline-measurements"
    for script, args in [
        (
            "measure.py",
            [
                "--trace",
                trace,
                "--task-id",
                "baseline",
                "--evidence-class",
                "agent_under_test",
                "--out-dir",
                measurements,
            ],
        ),
        ("report.py", ["--coverage-dir", measurements, "--out", output]),
    ]:
        result = subprocess.run(
            [sys.executable, str(skill / "scripts/audit_spec" / script), "--audit", str(audit), *map(str, args)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return output
