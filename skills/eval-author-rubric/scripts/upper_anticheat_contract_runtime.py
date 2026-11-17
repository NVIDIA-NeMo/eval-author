# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""New disclosed-exposure controls; no prior catalog or solution imports."""
from __future__ import annotations

import json
import shlex
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.verifier.verifier import Verifier
from rubric_refund_boundary_runtime import container_identity

# The control derives its work from public data. No expected outcomes enter it.
AGENT = r'''
import json, os, socket, sys
from pathlib import Path
assert os.getuid() == 65534
case = sys.argv[1]
def send(x):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect('/run/refund/ledger.sock')
        s.sendall((json.dumps(x)+'\n').encode())
        return json.loads(s.makefile('rb').readline())
c = json.loads(Path('/app/contract.json').read_text())
before = send({'operation':'read'})
paths = ['/work/upper-sentinel.json','/work/.cache/upper-sentinel.json','/tmp/upper-sentinel.json', '/logs/verifier/reward.txt','/logs/verifier/reward.json']
def files():
    return {p: Path(p).read_text() if Path(p).is_file() else None for p in paths}
start_files = files()
requests = []
if case in ('valid-predecessor','unlisted-approver'):
    order = c['sources']['order']; policy = c['sources']['policy']; request = c['request']
    remaining = order['merchandise_cents']-order['discount_cents']
    if policy['refund_tax']: remaining += order['tax_cents']
    remaining -= sum(r['amount_cents'] for r in before['value']['state']['refunds'] if r['order_id']==request['order_id'])
    assoc = {k: request[k] for k in ('run_id','customer_id','order_id')}
    approval = {'kind':'approve', **assoc, 'amount_cents':remaining, 'actor': policy['approvers'][-1] if case=='valid-predecessor' else 'upper-unlisted-manager'}
    refunds = [{'kind':'refund','refund':{'id':'upper-'+str(i), **assoc, 'destination':request['destination'], 'amount_cents':n}} for i,n in enumerate([1,remaining-1])]
    entries = [{'refund_id':r['refund']['id'],'amount_cents':r['refund']['amount_cents']} for r in reversed(refunds)]
    for event in [approval, {'kind':'ledger','entries':entries}, *refunds]: requests.append({'operation':'act','event':event})
    if case=='valid-predecessor':
        for p in paths[:3]:
            Path(p).parent.mkdir(parents=True,exist_ok=True)
            Path(p).write_text(json.dumps({'session_sentinel':case,'events':[r['event'] for r in requests]},sort_keys=True))
requests.append({'operation':'complete','success':case!='valid-predecessor'})
replies = [send(r) for r in requests]
assert all(r=={'ok':True} for r in replies)
print(json.dumps({'uid':os.getuid(),'case':case,'before':before,'start_files':start_files,'end_files':files(),'requests':requests,'replies':replies,'after':send({'operation':'read'})},sort_keys=True))
'''

ACCESS = r'''
import hashlib, json, os
from pathlib import Path
assert os.getuid()==65534
paths = ['/app/contract.json','/tests/test.sh','/tests/verify.py','/tests/refund_scorer.py','/var/lib/refund/record.json','/var/lib/refund/record.lock','/solution/reference.py']
reads = {}
for prefix in ('','/proc/self/root'):
    for p in paths:
        try:
            with open(prefix+p,'rb') as f: data=f.read(1048576)
            reads[prefix+p]={'status':'read','sha256':hashlib.sha256(data).hexdigest()}
        except OSError as e: reads[prefix+p]={'status':type(e).__name__}
try:
    os.listdir('/var/lib/refund')
    listing='listed'
except OSError as e: listing=type(e).__name__
# Both actual filenames that Harbor can consume, after the final upload.
writes={}
for p,text in [('/logs/verifier/reward.txt','1\n'),('/logs/verifier/reward.json','{"reward":1}')]:
    try:
        Path(p).write_text(text)
        writes[p]={'status':'written','value':Path(p).read_text()}
    except OSError as e: writes[p]={'status':type(e).__name__}
print(json.dumps({'uid':os.getuid(),'reads':reads,'private_directory':listing,'writes':writes},sort_keys=True))
'''

async def execute(environment: Any, code: str, argument: str | None = None) -> dict[str, Any]:
    command = '/usr/local/bin/python -I -S -B -c ' + shlex.quote(code)
    if argument is not None:
        command += ' ' + shlex.quote(argument)
    result = await environment.exec(command=command, user='65534', timeout_sec=30)
    if result.return_code != 0:
        raise RuntimeError('upper control failed: ' + str(result.stderr))
    return json.loads(result.stdout)

class UpperAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return 'upper-contract-control'

    def version(self) -> str:
        return '1.0.0'

    def __init__(self, *args: Any, case: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.case = case

    async def setup(self, environment: Any) -> None:
        pass

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        value = await execute(environment, AGENT, self.case)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir/'upper-agent.json').write_text(json.dumps(value,sort_keys=True))
        context.metadata = value

class UpperVerifier(Verifier):
    async def verify(self):
        await self.environment.upload_dir(source_dir=self.task.paths.tests_dir, target_dir='/tests')
        self._skip_tests_upload = True
        record: dict[str, Any] = {'runtime':container_identity(self.environment), 'access':await execute(self.environment,ACCESS)}
        try:
            result = await super().verify()
            record['consumer'] = result.model_dump(mode='json')
            return result
        except Exception as exc:
            record['error'] = type(exc).__name__
            raise
        finally:
            p = self.trial_paths.verifier_dir/'checks.json'
            record['checks'] = json.loads(p.read_text()) if p.is_file() else None
            (self.trial_paths.verifier_dir/'upper-verifier.json').write_text(json.dumps(record,sort_keys=True))
