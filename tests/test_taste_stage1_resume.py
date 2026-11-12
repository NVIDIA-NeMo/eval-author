# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Interrupted Stage 1 replays learning and retains pending model work."""

import pytest
from test_taste_backends import run_source


@pytest.mark.parametrize("mode", ["before", "after", "twice"])
def test_interrupted_stage1_resumes_exact_pending_draw(tmp_path, mode):
    after_decision = mode == "after"
    run_source(f"""
import json, os, runpy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from src.common.domain_config import DomainConfig
from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler as Sampler
root=Path({str(tmp_path)!r})
(root/'policy.md').write_text('Use tools.')
(root/'tool_spec.json').write_text(json.dumps({{'write':{{'type':'WRITE'}},'read':{{'type':'READ'}}}}))
(root/'seeds.jsonl').write_text(json.dumps({{'id':'one','actions':[{{'name':'write','arguments':{{}}}}]}}))
(root/'prepared.json').write_text(json.dumps(DomainConfig('control',resources_dir=str(root)).to_spec()))
(root/'lengths.json').write_text(json.dumps({{'mode':'empirical','empirical_lengths':[1,2,3]}}))
original_init=Sampler.__init__
original_ingest=Sampler.ingest
calls=[]
class Interrupted(Exception): pass
class Scripted:
    def __init__(self,**kwargs): pass
    def call(self,**kwargs):
        calls.append(kwargs['prompt'])
        if interrupt_call and len(calls)==4: raise Interrupted('stopped before response')
        sequence=json.loads(kwargs['prompt'].split('Sequence: ',1)[1].split('\\n',1)[0])
        valid=sequence[-1]=='write'
        return json.dumps({{'valid':valid,'reason':'control','problematic_indices':[] if valid else [0]}})
def initialize(self,*args,**kwargs):
    original_init(self,*args,**kwargs)
    self._rng.seed(42)
    np.random.seed(43)
def ingest(self,*args,**kwargs):
    if interrupt_ingest and self.t==stop_t: raise Interrupted('stopped after decision')
    return original_ingest(self,*args,**kwargs)
def train(name, extra=()):
    os.environ['TASTE_WORKSPACE']=str(root/name)
    sys.argv=['train','--backend-config',str(root/'prepared.json'),'--num-samples','6',
              '--length-config',str(root/'lengths.json'),'--save-every','2',
              '--save-dir',str(root/name/'training'),*extra]
    runpy.run_module('src.stage1_ngram.initialize_ngram_model',run_name='__main__')
def events(name):
    return [json.loads(line) for line in (root/name/'training/evidence/events.jsonl').read_text().splitlines()]
with patch.object(DomainConfig,'llm_factory',Scripted), patch.object(Sampler,'__init__',initialize), patch.object(Sampler,'ingest',ingest):
    interrupt_call=interrupt_ingest=False
    stop_t=2
    train('uninterrupted')
    expected_calls=list(calls)
    calls.clear()
    interrupt_call={not after_decision!r}
    interrupt_ingest={after_decision!r}
    try: train('interrupted')
    except Interrupted: pass
    else: raise AssertionError('expected interruption')
    original_bytes=(root/'interrupted/training/evidence/events.jsonl').read_bytes()
    calls.clear()
    interrupt_call=interrupt_ingest=False
    extra=['--resume-checkpoint',str(root/'interrupted/training/ngram_control_n3_t2.json'),
           '--resume-journal',str(root/'interrupted/training/evidence/events.jsonl')]
    for flag,value in [('--num-samples','7'),('--validation-model','different'),('--tau0','2.5')]:
        try: train('invalid-'+flag[2:],extra+[flag,value])
        except ValueError: pass
        else: raise AssertionError('configuration drift accepted')
        assert not (root/('invalid-'+flag[2:])/'training').exists()
    assert not calls
    stop_t=4
    interrupt_ingest={mode == "twice"!r}
    try: train('resumed',extra)
    except Interrupted:
        assert {mode == "twice"!r}
        interrupt_ingest=False
        ancestor=root/'interrupted/training/evidence/events.jsonl'
        ancestor.write_bytes(original_bytes+b' ')
        try:
            train('altered-ancestor',['--resume-checkpoint',str(root/'resumed/training/ngram_control_n3_t4.json'),
                                      '--resume-journal',str(root/'resumed/training/evidence/events.jsonl')])
        except ValueError as error: assert 'source changed' in str(error)
        else: raise AssertionError('changed parent journal accepted')
        ancestor.write_bytes(original_bytes)
        train('resumed-twice',['--resume-checkpoint',str(root/'resumed/training/ngram_control_n3_t4.json'),
                               '--resume-journal',str(root/'resumed/training/evidence/events.jsonl')])
    assert calls==expected_calls[{4 if after_decision else 3}:], (len(calls),len(expected_calls))
assert (root/'interrupted/training/evidence/events.jsonl').read_bytes()==original_bytes
expected=json.loads(next((root/'uninterrupted/artifacts/ngram/checkpoints').glob('*.json')).read_text())
actual=json.loads(next((root/{"resumed-twice" if mode == "twice" else "resumed"!r}/'artifacts/ngram/checkpoints').glob('*.json')).read_text())
assert actual==expected
assert events('resumed')[0]['completed']==2
assert events('resumed')[0]['rng_mode']=='restored_recorded_boundary'
old_draw=[r for r in events('interrupted') if r['event']=='draw_started'][-1]
new_draw=[r for r in events('resumed') if r['event']=='draw_started'][0]
assert old_draw['sequence']==new_draw['sequence'] and old_draw['rng_state']==new_draw['rng_state']
assert events({"resumed-twice" if mode == "twice" else "resumed"!r})[-1]['accepted_draws']==events('uninterrupted')[-1]['accepted_draws']
assert actual['sampler']['rejected_set']
""")


def test_legacy_resume_requires_disclosed_rng_boundary_and_rejects_bad_checkpoint(tmp_path):
    run_source(f"""
import json, os, runpy
from pathlib import Path
from unittest.mock import patch
from src.common.domain_config import DomainConfig
from src.common.sequence_evidence import SequenceEvidence
from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler as Sampler
root=Path({str(tmp_path)!r})
(root/'policy.md').write_text('Use tools.')
(root/'tool_spec.json').write_text(json.dumps({{'write':{{'type':'WRITE'}},'read':{{'type':'READ'}}}}))
(root/'seeds.jsonl').write_text(json.dumps({{'id':'one','actions':[{{'name':'write','arguments':{{}}}}]}}))
(root/'prepared.json').write_text(json.dumps(DomainConfig('control',resources_dir=str(root)).to_spec()))
(root/'lengths.json').write_text(json.dumps({{'mode':'empirical','empirical_lengths':[1,2,3]}}))
calls=[]
original_record=SequenceEvidence.record
original_serialize=Sampler._serialize_state
class Interrupted(Exception): pass
class Scripted:
    def __init__(self,**kwargs): pass
    def call(self,**kwargs):
        calls.append(kwargs['prompt'])
        if interrupted and len(calls)==4: raise Interrupted()
        return json.dumps({{'valid':True,'reason':'control','problematic_indices':[]}})
def legacy_record(self,event,**payload):
    payload.pop('rng_state',None)
    return original_record(self,event,**payload)
def legacy_state(self):
    state=original_serialize(self)
    state['sampler'].pop('rng_state')
    return state
def train(name,extra=()):
    os.environ['TASTE_WORKSPACE']=str(root/name)
    sys.argv=['train','--backend-config',str(root/'prepared.json'),'--num-samples','6',
              '--length-config',str(root/'lengths.json'),'--save-every','2',
              '--save-dir',str(root/name/'training'),*extra]
    runpy.run_module('src.stage1_ngram.initialize_ngram_model',run_name='__main__')
with patch.object(DomainConfig,'llm_factory',Scripted):
    interrupted=True
    with patch.object(SequenceEvidence,'record',legacy_record), patch.object(Sampler,'_serialize_state',legacy_state):
        try: train('legacy')
        except Interrupted: pass
    checkpoint=root/'legacy/training/ngram_control_n3_t2.json'
    journal=root/'legacy/training/evidence/events.jsonl'
    source_bytes=journal.read_bytes()
    extra=['--resume-checkpoint',str(checkpoint),'--resume-journal',str(journal)]
    calls.clear()
    interrupted=False
    try: train('missing-seed',extra)
    except ValueError as error: assert '--resume-rng-seed' in str(error)
    else: raise AssertionError('legacy RNG silently reset')
    assert not calls and not (root/'missing-seed/training').exists()
    damaged=json.loads(checkpoint.read_text())
    damaged['model']['counts']['pos_ctx']['[]']=99
    bad=root/'bad-checkpoint.json'; bad.write_text(json.dumps(damaged))
    try: train('bad-state',['--resume-checkpoint',str(bad),'--resume-journal',str(journal),'--resume-rng-seed','99'])
    except ValueError as error: assert 'recorded ingestion' in str(error)
    else: raise AssertionError('unbound learning accepted')
    assert not calls and not (root/'bad-state/training').exists()
    train('recovered',extra+['--resume-rng-seed','99'])
    assert len(calls)==4
assert journal.read_bytes()==source_bytes
rows=[json.loads(line) for line in (root/'recovered/training/evidence/events.jsonl').read_text().splitlines()]
assert rows[0]['rng_mode']=='new_recorded_boundary_after_legacy_interruption' and rows[0]['rng_seed']==99
assert rows[0]['pending']['sample_index']==3
assert next(row for row in rows if row['event']=='draw_started')['sequence']==rows[0]['pending']['sequence']
assert rows[-1]['t']==6
state=json.loads(next((root/'recovered/artifacts/ngram/checkpoints').glob('*.json')).read_text())
assert state['sampler']['rng_state']['version']==1
""")
