# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from harbor.bridges.acp import ACPX_NPM_VERSION, build_acpx_config


def test_native_acpx_preserves_messages_session_and_reset(tmp_path: Path) -> None:
    executable = os.environ.get("EVAL_AUTHOR_ACPX") or shutil.which("acpx")
    if executable is None:
        pytest.skip(f"Native ACP session check requires acpx {ACPX_NPM_VERSION} on PATH or EVAL_AUTHOR_ACPX")
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    Path(env["HOME"]).mkdir()
    state = tmp_path / "state"
    fixture = Path(__file__).parent / "fixtures/acp/counter.py"
    config = build_acpx_config([sys.executable, str(fixture), str(state)], {"ttl": 1, "timeout": 10})
    (tmp_path / ".acpxrc.json").write_text(json.dumps(config))

    def run(*args: str) -> str:
        result = subprocess.run(
            [executable, *args], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    assert run("--version") == ACPX_NPM_VERSION
    try:
        run("sessions", "ensure")
        sessions = list(state.iterdir())
        assert len(sessions) == 1
        original = sessions[0]
        opening = 'Count this exactly: "one"\nsecond line'
        assert run("prompt", opening) == f"turn=1 received={opening}"
        assert run("prompt", "Continue the same conversation.") == "turn=2 received=Continue the same conversation."
        assert list(state.iterdir()) == [original]
        assert original.read_text() == "2"
        run("sessions", "close")
        archive = tmp_path / "conversation.json"
        run("sessions", "export", "--output", str(archive))
        exported = json.dumps(json.loads(archive.read_text()))
        assert original.name in exported
        assert json.dumps(opening)[1:-1] in exported
        assert "turn=2 received=Continue the same conversation." in exported
        run("sessions", "new")
        assert run("prompt", "Fresh attempt.") == "turn=1 received=Fresh attempt."
        assert len(list(state.iterdir())) == 2
        assert original.read_text() == "2"
    finally:
        run("sessions", "close")
