# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-boundary probes through the native Harbor solver and verifier hooks."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import rubric_native_collect as collector
from harbor.agents.base import BaseAgent
from harbor.verifier.verifier import Verifier
from rubric_refund_boundary_runtime import container_identity, execute

PROBE = r"""
import hashlib, json, os, socket
from pathlib import Path

def read(path):
    try:
        raw = Path(path).read_bytes()
        info = Path(path).stat()
        return {'status':'read', 'sha256':hashlib.sha256(raw).hexdigest(), 'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode & 511}
    except PermissionError:
        return {'status':'denied'}
    except FileNotFoundError:
        return {'status':'absent'}

def send(value):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect('/run/refund/ledger.sock')
        s.sendall((json.dumps(value)+'\n').encode())
        return json.loads(s.makefile('rb').readline())

paths = ['/solution/reference.py','/solution/solve.sh','/var/lib/refund/record.json','/var/lib/refund/record.lock','/app/contract.json','/app/ledger.py','/app/server.py']
reads = {}
for method in ['direct','proc-root','symlink']:
    results = {}
    for index, path in enumerate(paths):
        target = path if method == 'direct' else '/proc/self/root' + path
        if method == 'symlink':
            link = Path('/work/access-link-' + str(index))
            link.unlink(missing_ok=True)
            link.symlink_to(path)
            target = str(link)
        results[path] = read(target)
    reads[method] = results
result = {'uid':os.getuid(), 'reads':reads, 'socket':send({'operation':'read'}), 'invalid_tool':send({'operation':'read','path':'/solution/reference.py'}), 'helpers':{p:read(p) for p in ['/tests/test.sh','/tests/verify.py','/tests/refund_scorer.py']}, 'docker_socket':read('/var/run/docker.sock')['status']}
print(json.dumps(result))
"""


def image_scan(image_id: str) -> dict[str, Any]:
    targets = {p: collector.recorder.digest(collector.BASE / p) for p in ("solution/reference.py", "solution/solve.sh")}
    result: dict[str, Any] = {"targets": targets, "layers": 0, "files_hashed": 0, "matches": [], "errors": []}
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "image.tar"
        subprocess.run(
            ["docker", "image", "save", "-o", str(archive), image_id], check=True, capture_output=True, timeout=60
        )
        with tarfile.open(archive) as outer:
            manifest_stream = outer.extractfile("manifest.json")
            if manifest_stream is None:
                raise ValueError("image manifest missing")
            manifest = json.load(manifest_stream)
            collector.require(len(manifest) == 1)
            for name in manifest[0]["Layers"]:
                stream = outer.extractfile(name)
                if stream is None:
                    raise ValueError("image layer missing")
                result["layers"] += 1
                with tarfile.open(fileobj=stream, mode="r|*") as layer:
                    for member in layer:
                        if not member.isfile():
                            continue
                        source = layer.extractfile(member)
                        if source is None:
                            raise ValueError("image member missing")
                        hasher = hashlib.sha256()
                        while block := source.read(262144):
                            hasher.update(block)
                        digest = hasher.hexdigest()
                        result["files_hashed"] += 1
                        if digest in targets.values():
                            result["matches"].append({"layer": name, "path": member.name, "sha256": digest})
    return result


class AccessAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "refund-access-control"

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: Any) -> None:
        pass

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        observed = await execute(environment, PROBE, "65534")
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        collector.recorder.write_new(self.logs_dir / "access.json", observed)
        context.metadata = observed


class AccessVerifier(Verifier):
    async def verify(self):
        await self.environment.upload_dir(source_dir=self.task.paths.tests_dir, target_dir="/tests")
        self._skip_tests_upload = True
        runtime = container_identity(self.environment)
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", runtime["image"]], timeout=30))[0]
        observation = {
            "runtime": runtime,
            "image": image,
            "layers": image_scan(runtime["image"]),
            "after_upload": await execute(self.environment, PROBE, "65534"),
        }
        reward = await super().verify()
        observation["after_verification"] = await execute(self.environment, PROBE, "65534")
        collector.recorder.write_new(self.trial_paths.verifier_dir / "access.json", observation)
        return reward
