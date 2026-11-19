# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scripted solver-role controls for the refund action service."""

from __future__ import annotations

import json
import shlex
from typing import Any

from harbor.agents.base import BaseAgent

PROBE = r"""
import json, os, socket, subprocess, sys
from pathlib import Path
p = json.loads(sys.argv[1])
assert os.getuid() == 65534

def send(request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect('/run/refund/ledger.sock')
        client.sendall((json.dumps(request) + '\n').encode())
        return json.loads(client.makefile('rb').readline())

before = send({'operation':'read'})
assert before['ok'] is True
observed = {'case':p['case'], 'uid':os.getuid(), 'before':before['value'], 'writes':{}, 'replies':[], 'process_exit':None}
case = p['case']
if case in ('fake-output', 'stale-output', 'inert-reference'):
    for name, content in {'/tmp/submission.json':json.dumps(p.get('copied_candidate', p['candidate'])), '/tmp/solution.py':'# refund answer\nraise SystemExit(0)\n', '/logs/verifier/reward.txt':'1\n', '/logs/verifier/checks.json':'{"accepted":true,"total_credit":9}\n'}.items():
        try:
            Path(name).write_text(content)
            observed['writes'][name] = 'written'
        except OSError as exc:
            observed['writes'][name] = type(exc).__name__
if case == 'private-tamper':
    for name in ('/tests/test.sh','/tests/verify.py','/tests/refund_scorer.py','/app/ledger.py','/app/server.py','/app/contract.json','/var/lib/refund/record.json'):
        try:
            with open(name, 'w') as stream:
                stream.write('raise SystemExit(0)\n')
            observed['writes'][name] = 'written'
        except OSError as exc:
            observed['writes'][name] = type(exc).__name__
    for kind in ('helper_error','ordering_evidence_unavailable','change_protected','replace_history'):
        observed['replies'].append(send({'operation':'act','event':{'kind':kind}}))
if case in ('early-exit', 'stale-output', 'all-failure'):
    code = 'raise SystemExit(0)' if case == 'early-exit' else 'raise SystemExit(1)'
    observed['process_exit'] = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code]).returncode
if case == 'all-failure':
    observed['replies'].append(send({'operation':'act','event':{'kind':'helper_error'}}))
for event in p['candidate']['events']:
    reply = send({'operation':'act','event':event})
    observed['replies'].append(reply)
    assert reply == {'ok':True}
if case not in ('early-exit', 'fake-output', 'stale-output', 'private-tamper', 'inert-reference'):
    observed['replies'].append(send({'operation':'complete','success':p['candidate']['success']}))
observed['after'] = send({'operation':'read'})['value']
print(json.dumps(observed, sort_keys=True))
"""


def payload(spec: dict[str, Any]) -> dict[str, Any]:
    return {"case": spec["id"], "candidate": spec["candidate"]}


class RefundControlAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "refund-scripted-control"

    def version(self) -> str:
        return "2.0.0"

    def __init__(self, *args: Any, case: str = "reference", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from rubric_refund_panel import control_specs

        specs = control_specs()
        self.case = case
        self.payload = payload(specs[case])
        if case in ("fake-output", "stale-output", "inert-reference"):
            self.payload["copied_candidate"] = specs["reference"]["candidate"]

    async def setup(self, environment: Any) -> None:
        pass

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        result = await environment.exec(
            command="/usr/local/bin/python -I -S -B -c "
            + shlex.quote(PROBE)
            + " "
            + shlex.quote(json.dumps(self.payload)),
            timeout_sec=20,
            user="65534",
        )
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "control-exec.json").write_text(
            json.dumps(
                {
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                sort_keys=True,
            )
        )
        if result.return_code != 0:
            raise RuntimeError("refund control execution failed")
        context.metadata = json.loads(result.stdout)
