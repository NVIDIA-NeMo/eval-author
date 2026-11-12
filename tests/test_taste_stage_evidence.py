# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sequence-stage journals retain decisions without changing the pipeline."""

from test_taste_backends import run_source


def test_stage1_retains_empty_duplicate_and_uningested_negative_decisions(tmp_path):
    run_source(f"""
import json, os, runpy
from pathlib import Path
from unittest.mock import patch
from src.common.domain_config import DomainConfig
from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler
root=Path({str(tmp_path)!r})
(root/'policy.md').write_text('Use tools.')
(root/'tool_spec.json').write_text(json.dumps({{'write': {{'type':'WRITE'}}, 'read': {{'type':'READ'}}}}))
rows=[{{'id':str(i),'split':'base','actions':[{{'name':name,'arguments':{{}}}} for name in seq]}}
      for i,seq in enumerate([[],['write'],['write'],['read']])]
(root/'seeds.jsonl').write_text('\\n'.join(json.dumps(r) for r in rows))
config=DomainConfig('control',resources_dir=str(root))
(root/'prepared.json').write_text(json.dumps(config.to_spec()))
(root/'lengths.json').write_text(json.dumps({{'mode':'empirical','empirical_lengths':[1]}}))
replies=iter([(True,[]),(True,[]),(False,[0]),(True,[]),(False,[])])
class Scripted:
    def __init__(self,**kwargs): pass
    def call(self,**kwargs):
        valid,indices=next(replies)
        return json.dumps({{'valid':valid,'reason':'observed control','problematic_indices':indices}})
os.chdir(root)
with patch.object(DomainConfig,'llm_factory',Scripted), \\
     patch.object(ActionSequenceNGramModelSampler,'get_new_action_sequence',side_effect=[(['write'],'accepted_dup'),(['read'],'new')]):
    sys.argv=['train','--backend-config',str(root/'prepared.json'),'--num-samples','2',
              '--length-config',str(root/'lengths.json'),'--save-dir',str(root/'training')]
    runpy.run_module('src.stage1_ngram.initialize_ngram_model',run_name='__main__')
events=[json.loads(line) for line in (root/'training/evidence/events.jsonl').read_text().splitlines()]
assert [e['index'] for e in events]==list(range(len(events)))
assert sum(e['event']=='seed_started' for e in events)==4
assert [e['seed_index'] for e in events if e['event']=='seed_skipped']==[0]
draws=[e for e in events if e['event']=='draw_completed']
assert [e['ingested'] for e in draws]==[True,False]
assert [e['t'] for e in draws]==[1,2]
assert [e['duplicate_status'] for e in events if e['event']=='draw_started']==['accepted_dup','new']
assert len([e for e in events if e['event']=='validation_decision'])==5
sampler=ActionSequenceNGramModelSampler.load_state(str(next((root/'artifacts/ngram/checkpoints').glob('*.json'))))
assert sampler.t==2 and sampler.action_sequence_set==[['write'],['write'],['write']]
assert sampler.accepted_set=={{('write',)}} and sampler.rejected_set==set()
assert events[-1]['event']=='stage1_completed'
""")


def test_stage2_retains_reused_slots_and_all_recovery_rounds(tmp_path):
    run_source(f"""
import json, os, runpy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from src.common.domain_config import DomainConfig
from src.common.sampler import LengthDistributionConfig
from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler
from src.common.sampler.action_sequence_clusterer import ActionSequenceClusterer,ClusterInfo
root=Path({str(tmp_path)!r})
(root/'policy.md').write_text('Use tools.')
(root/'tool_spec.json').write_text(json.dumps({{'write': {{'type':'WRITE'}},'read': {{'type':'READ'}}}}))
(root/'seeds.jsonl').write_text(json.dumps({{'id':'0','split':'base','actions':[{{'name':'write','arguments':{{}}}}]}}))
config=DomainConfig('control',resources_dir=str(root))
sampler=ActionSequenceNGramModelSampler(config.seeds(),domain='control',backend_spec=config.to_spec(),
    tool_spec_path=config.tool_spec_path,length_distribution_config=LengthDistributionConfig(mode='empirical',empirical_lengths=[1]))
sampler.ingest(['write'],positive=True)
sampler.save_state(str(root/'checkpoint.json'))
replies=iter([False,False,True])
class Scripted:
    def __init__(self,**kwargs): pass
    def call(self,**kwargs): return json.dumps({{'valid':next(replies),'reason':'control','problematic_indices':[0]}})
geometries=[ClusterInfo(0,0,['read'],[0],[['read']],[0.0]),
            ClusterInfo(0,0,['write','read'],[0],[['write','read']],[0.0])]
os.chdir(root)
with patch.object(DomainConfig,'llm_factory',Scripted), \\
     patch.object(ActionSequenceClusterer,'generate_pool',side_effect=[[['write']],[['read']],[['write','read']]]), \\
     patch.object(ActionSequenceClusterer,'_run_kmedoids',return_value=([0],np.array([0]))), \\
     patch.object(ActionSequenceClusterer,'recluster_with_frozen',side_effect=[[geometries[0]],[geometries[1]]]):
    sys.argv=['cluster','--checkpoint',str(root/'checkpoint.json'),'-k','1','--pool-size','1',
              '--max-replacements','0','--max-recluster-rounds','2','--output',str(root/'clusters.json')]
    runpy.run_module('src.stage2_cluster.cluster_and_validate',run_name='__main__')
events=[json.loads(line) for line in (root/'clusters.json.evidence/events.jsonl').read_text().splitlines()]
geometry=[e for e in events if e['event']=='cluster_geometry']
assert [e['round_index'] for e in geometry]==[0,1,2]
assert [e['clusters'][0]['medoid_sequence'] for e in geometry]==[['write'],['read'],['write','read']]
recoveries=[e for e in events if e['event']=='recovery_started']
assert [e['prior_clusters'][0]['medoid_sequence'] for e in recoveries]==[['write'],['read']]
assert all(e['replaced_cluster_indices']==[0] and e['frozen_sequences']==[] for e in recoveries)
assert [e['result']['status'] for e in events if e['event']=='cluster_completed']==['unusable','unusable','valid_medoid']
artifact=json.loads((root/'clusters.json').read_text())
assert artifact['clusters'][0]['representative_sequence']==['write','read'] and artifact['num_usable']==1
assert events[-1]['event']=='stage2_completed'
""")
