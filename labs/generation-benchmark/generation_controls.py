# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Harbor control agents the generator sidecar allows besides A, Oracle, and NOP."""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath
from typing import Any

from generation_benchmark.models import resolve_evidence
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

SCRIPT_CONTROL_IMPORT_PATH = "generation_controls:ScriptControl"
DEFAULT_SCRIPT_RELPATH = "private/incorrect.sh"
CONTAINER_SCRIPT_PATH = "/tmp/incorrect.sh"
LOG_NAME = "script-control.txt"


class ScriptControl(BaseAgent):
    """Run the task's wrong-answer script inside the disposable trial container.

    The script is a nonempty regular file under the task's `private/` directory.
    It is uploaded and executed with bash in the trial environment; no file from
    the task bundle is imported into the host process.
    """

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        task_dir: str | Path | None = None,
        script_relpath: str = DEFAULT_SCRIPT_RELPATH,
        **kwargs: Any,
    ) -> None:
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        if task_dir is None:
            raise ValueError("ScriptControl requires the task_dir agent kwarg")
        if PurePosixPath(script_relpath).parts[:1] != ("private",):
            raise ValueError(f"ScriptControl script must be under private/: {script_relpath}")
        root = Path(task_dir)
        try:
            script = resolve_evidence(root, script_relpath)
        except FileNotFoundError:
            raise ValueError(f"ScriptControl script is missing: {script_relpath}") from None
        if not script.is_file() or script.stat().st_size == 0:
            raise ValueError(f"ScriptControl script must be a nonempty regular file: {script_relpath}")
        self.script_path = script

    @staticmethod
    def name() -> str:
        return "script-control"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        target = shlex.quote(CONTAINER_SCRIPT_PATH)
        await environment.upload_file(source_path=self.script_path, target_path=CONTAINER_SCRIPT_PATH)
        await environment.exec(command=f"chmod +x {target}")
        result = await environment.exec(
            command=f"bash {target} 2>&1",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        output = (result.stdout or "") + (result.stderr or "")
        (self.logs_dir / LOG_NAME).write_text(f"{output}\nexit_code={result.return_code}\n", encoding="utf-8")
        if result.return_code != 0:
            raise RuntimeError(f"wrong-answer script exited {result.return_code}; see agent/{LOG_NAME}")
