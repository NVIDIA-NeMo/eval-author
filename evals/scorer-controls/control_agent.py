# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted synthetic event submission, with no model or payment endpoint."""

import json
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent


class ControlAgent(BaseAgent):
    @staticmethod
    def name():
        return "refund-scorer-control"

    def version(self):
        return "1"

    async def setup(self, environment):
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
            source_path=Path(__file__).parent / "submissions.json",
            target_path="/controls/submissions.json",
        )

    async def run(self, instruction, environment, context):
        program = """import hashlib,json,os,pathlib
p = pathlib.Path('/work/submissions.json')
p.write_bytes(pathlib.Path('/controls/submissions.json').read_bytes())
blocked = False
try:
    pathlib.Path('/tests/manifest.json').read_bytes()
except PermissionError:
    blocked = True
print(json.dumps(dict(uid=os.getuid(), hidden_read_blocked=blocked,
    tests_readable=os.access('/tests',os.R_OK|os.X_OK),
    tests_writable=os.access('/tests',os.W_OK),
    verifier_writable=os.access('/logs/verifier',os.W_OK),
    submission_sha256=hashlib.sha256(p.read_bytes()).hexdigest())))
"""
        result = await environment.exec(
            command="python -I -c " + shlex.quote(program),
            user="65534",
            timeout_sec=10,
        )
        receipt = json.loads(result.stdout)
        receipt.update(control="refund-panel", return_code=result.return_code)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "control.json").write_text(json.dumps(receipt) + "\n")
        if result.return_code != 0 or receipt["uid"] != 65534 or receipt["hidden_read_blocked"] is not True:
            raise RuntimeError("scripted control privilege failure")
