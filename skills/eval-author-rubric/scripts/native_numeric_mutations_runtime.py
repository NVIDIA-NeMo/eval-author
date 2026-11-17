# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from harbor.agents.base import BaseAgent
from harbor.verifier.verifier import Verifier
import rubric_native_collect as collector
from rubric_refund_boundary_runtime import execute, container_identity

PROBE = r'''
import os,json,socket,sys,hashlib
from pathlib import Path
p=json.loads(sys.argv[1])
def read(path):
    try: return {'status':'readable','sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    except OSError as e: return {'status':type(e).__name__}
def call(q):
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(3); s.connect('/run/refund/ledger.sock')
            s.sendall((json.dumps(q)+'\n').encode())
            return json.loads(s.makefile('rb').readline())
    except OSError as e: return {'transport_error':type(e).__name__}
before=call({'operation':'read'})
replies=[call(q) for q in p['requests']]
print(json.dumps({'uid':os.getuid(),'public':read('/app/contract.json'),'reference':read('/app/exposed-reference.py'),'private_record':read('/var/lib/refund/record.json'),'endpoint_exists':Path('/run/refund/ledger.sock').exists(),'before':before,'replies':replies,'after':call({'operation':'read'})}))
'''
OBSERVE = r'''
import json,hashlib
from pathlib import Path
print(json.dumps({'record':json.loads(Path('/var/lib/refund/record.json').read_text()),'components':{n:hashlib.sha256(Path('/app/'+n+'.py').read_bytes()).hexdigest() for n in ('server','ledger')},'declared_endpoint_exists':Path('/run/refund/ledger.sock').exists(),'other_endpoint_exists':Path('/run/refund/unadvertised.sock').exists()}))
'''

class NumericAgent(BaseAgent):
    @staticmethod
    def name(): return 'native-numeric-mutant'
    def version(self): return '1.0.0'
    def __init__(self, *args: Any, policy: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.policy = policy
    async def setup(self, environment): pass
    async def run(self, instruction, environment, context):
        from rubric_example_native import _trusted_module
        contract = json.loads((collector.BASE / 'environment/contract.json').read_text())
        reference = _trusted_module(collector.BASE / 'solution/reference.py', '_numeric_reference')
        candidate = reference.solve(contract) if self.policy == 'reference' else {'events':[], 'success':False}
        requests = [{'operation':'act','event':e} for e in candidate['events']]
        if candidate['success']: requests.append({'operation':'complete','success':True})
        result = await execute(environment, PROBE, '65534', {'requests':requests})
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        collector.recorder.write_new(self.logs_dir / 'numeric.json', result)
        context.metadata = {'policy':self.policy,'model_execution':False}

class NumericVerifier(Verifier):
    async def verify(self):
        runtime = container_identity(self.environment)
        observed = await execute(self.environment, OBSERVE, 'root')
        container = json.loads(subprocess.check_output(['docker','inspect',runtime['container']],timeout=30))[0]
        reward = await super().verify()
        collector.recorder.write_new(self.trial_paths.verifier_dir / 'numeric.json', {**observed,'runtime':runtime,'container':container,'reward':reward.model_dump(mode='json'),'probe_sha256':hashlib.sha256(PROBE.encode()).hexdigest()})
        return reward
