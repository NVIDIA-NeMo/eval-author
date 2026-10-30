# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted Harbor agent; no model endpoint or inference credentials."""

import json
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent


class ControlAgent(BaseAgent):
    def __init__(self, *args, control, **kwargs):
        super().__init__(*args, **kwargs)
        if control not in ("reference", "early-exit", "fake-success", "hidden-case-read", "hang", "boolean-output"):
            raise ValueError("unknown control")
        self.control = control

    @staticmethod
    def name():
        return "verifier-cheat-control"

    def version(self):
        return "1"

    async def setup(self, environment):
        # Protect the non-mounted parent: Desktop bind-mount ownership can differ.
        result = await environment.exec(
            command="mkdir -p /work /controls /tests /logs/verifier; "
            "chown root:root /logs; chmod 700 /logs; "
            "chown root:root /tests /logs/verifier; chmod 700 /tests /logs/verifier; "
            "chown 65534:65534 /work; chmod 700 /work",
            user="root",
            timeout_sec=10,
        )
        if result.return_code != 0:
            raise RuntimeError("control setup failed")
        await environment.upload_file(
            source_path=Path(__file__).parent / "controls" / f"{self.control}.py",
            target_path="/controls/policy.py",
        )

    async def run(self, instruction, environment, context):
        program = (
            "import json,os,pathlib; "
            "pathlib.Path('/work/normalize.py').write_bytes(pathlib.Path('/controls/policy.py').read_bytes()); "
            "print(json.dumps(dict(uid=os.getuid(), "
            "verifier_writable=os.access('/logs/verifier',os.W_OK), "
            "tests_writable=os.access('/tests',os.W_OK), "
            "tests_readable=os.access('/tests',os.R_OK|os.X_OK))))"
        )
        command = "python -I -c " + shlex.quote(program)
        result = await environment.exec(command=command, timeout_sec=10)
        receipt = json.loads(result.stdout)
        receipt.update(control=self.control, command=command, return_code=result.return_code)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "control.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if result.return_code != 0 or receipt["uid"] != 65534:
            raise RuntimeError("scripted control failed or ran as the wrong user")
