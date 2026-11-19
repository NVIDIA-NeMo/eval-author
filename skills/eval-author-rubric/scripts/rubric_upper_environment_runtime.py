# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any

import rubric_native_collect as collector
from harbor.agents.base import BaseAgent
from harbor.verifier.verifier import Verifier
from rubric_refund_boundary_runtime import container_identity, execute

PROBE = r"""
import json, os, socket, sys
from pathlib import Path
p=json.loads(sys.argv[1])
def call(q):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(3); s.connect('/run/refund/ledger.sock')
        s.sendall((json.dumps(q)+'\n').encode())
        return json.loads(s.makefile('rb').readline())
before=call({'operation':'read'})
errors=[call(q) for q in p['errors']]
after_errors=call({'operation':'read'})
replies=[call(q) for q in p['requests']]
after=call({'operation':'read'})
boundaries={}
for path in ['/var/lib/refund/record.json','/app/contract.json','/app/ledger.py','/app/server.py']:
    try:
        with open(path,'ab'): pass
        boundaries[path]='opened'
    except PermissionError:
        boundaries[path]='denied'
print(json.dumps({'uid':os.getuid(),'before':before,'errors':errors,'after_errors':after_errors,'replies':replies,'after':after,'boundaries':boundaries}))
"""

OBSERVE = r"""
import hashlib,json,os,sys
from pathlib import Path
paths=['/app/contract.json','/app/ledger.py','/app/server.py','/tests/test.sh','/tests/verify.py','/tests/refund_scorer.py','/bin/sh',sys.executable]
print(json.dumps({'python_version':sys.version,'executable':sys.executable,'components':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}}))
"""


class UpperEnvironmentAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "refund-upper-environment"

    def version(self) -> str:
        return "1.0.0"

    def __init__(self, *args: Any, case: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.case = case

    async def setup(self, environment: Any) -> None:
        pass

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        from rubric_upper_environment import recipes

        observation = await execute(environment, PROBE, "65534", recipes()[self.case])
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        collector.recorder.write_new(self.logs_dir / "upper-environment.json", observation)
        context.metadata = {"scripted_control": self.case, "model_execution": False}


class UpperEnvironmentVerifier(Verifier):
    async def verify(self) -> Any:
        runtime = container_identity(self.environment)
        reward = await super().verify()
        components = await execute(self.environment, OBSERVE, "root")
        container = json.loads(subprocess.check_output(["docker", "inspect", runtime["container"]], timeout=30))[0]
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", runtime["image"]], timeout=30))[0]
        pin = (self.task.paths.task_dir / "environment/Dockerfile").read_text().splitlines()[0].removeprefix("FROM ")
        base = json.loads(subprocess.check_output(["docker", "image", "inspect", pin], timeout=30))[0]
        collector.recorder.write_new(
            self.trial_paths.verifier_dir / "upper-environment.json",
            {
                "runtime": runtime,
                "components": components,
                "container": container,
                "image": image,
                "base": base,
                "network_policy": self.environment.network_policy.model_dump(mode="json"),
                "reward": reward.model_dump(mode="json"),
                "probe_sha256": hashlib.sha256(PROBE.encode()).hexdigest(),
                "observation_sha256": hashlib.sha256(OBSERVE.encode()).hexdigest(),
            },
        )
        return reward
