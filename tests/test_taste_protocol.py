# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 3 helper controls without native execution or a task-service adapter."""

import json

import pytest
from test_taste_backends import SKILL, run_source

SCRIPT = SKILL / "scripts/taste_protocol.py"


def test_hints_cli_preserves_selected_masking_and_never_resolves_a_service(tmp_path):
    run_source(f"""
import json, runpy
from pathlib import Path
from unittest.mock import patch
from src.common.domain_config import DomainConfig
root = Path({str(tmp_path)!r})
candidate = {{'task': {{'request': 'operate'}}, 'reference_actions': [
    {{'name': 'write', 'arguments': {{'id': index, 'value': index + 10}}}} for index in range(4)]}}
candidate['reference_actions'].append({{'name': 'reply', 'arguments': {{'text': 'yes'}}, 'requestor': 'user'}})
(root / 'candidate.json').write_text(json.dumps(candidate))
(root / 'policy.md').write_text('Follow the policy.')
with patch.object(DomainConfig, 'service', side_effect=AssertionError('native service invoked')):
    for name in ('first', 'second'):
        sys.argv = ['protocol', 'hints', '--candidate', str(root / 'candidate.json'),
            '--policy', str(root / 'policy.md'), '--coverage-p', '0.33', '--seed', '7',
            '--no-shuffle', '--output', str(root / (name + '.json'))]
        runpy.run_path({str(SCRIPT)!r}, run_name='__main__')
first = json.loads((root / 'first.json').read_text())
assert first == json.loads((root / 'second.json').read_text())
assert first['reference_actions'] == candidate['reference_actions']
assert sum(line.count('???') for line in first['hints'].splitlines() if line.startswith('- ')) == 2
assert 'user: reply' in first['hints'] and 'yes' in first['hints']
assert first['hint_policy'] == {{'coverage_p': 0.33, 'shuffle': False, 'seed': 7}}
assert not any(name.startswith(('benchmark_runtime', 'tau2', 'litellm')) for name in sys.modules)
""")


def test_recovery_cli_preserves_frozen_centers_filters_lengths_and_fallback_without_service(tmp_path):
    run_source(f"""
import json, runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.common.domain_config import DomainConfig
from src.stage3_tasks import generate_tasks as stage
root = Path({str(tmp_path)!r})
resources = root / 'inputs'
resources.mkdir()
(resources / 'tool_spec.json').write_text(json.dumps({{
    'write': {{'type': 'WRITE'}}, 'read': {{'type': 'READ'}}, 'generic': {{}}
}}))
config = DomainConfig('mb1', resources_dir=str(resources), task_service='unavailable_native:Service')
clusters = {{'backend_spec': config.to_spec(), 'ngram_checkpoint': 'relative-checkpoint.json',
    'length_distribution': {{'mode': 'empirical', 'empirical_lengths': [1, 1, 3]}},
    'reference_lengths': [0, 1, 1, 3],
    'clustering_config': {{'only_write': True, 'max_kmedoids_iters': 17,
        'substitution_costs': {{'cost_same_group': 0.2, 'group_map': {{'write': 'g', 'generic': 'g'}}}}}},
    'clusters': [{{'cluster_idx': 7, 'representative_sequence': ['original']}},
        {{'cluster_idx': 8, 'representative_sequence': ['failed_a']}},
        {{'cluster_idx': 9, 'representative_sequence': ['failed_b']}},
        {{'cluster_idx': 10, 'status': 'unusable', 'representative_sequence': ['excluded']}}]}}
(root / 'clusters.json').write_text(json.dumps(clusters))
outcomes = [{{'cluster_idx': 9, 'success': False, 'action_sequence': ['failed_b']}},
    {{'cluster_idx': 7, 'success': True, 'action_sequence': ['accepted_replacement']}},
    {{'cluster_idx': 8, 'success': False, 'action_sequence': ['failed_a']}}]
(root / 'outcomes.json').write_text(json.dumps(outcomes))
sampler = SimpleNamespace(backend_spec=config.to_spec(), model='retained model',
    length_distribution='distribution', _length_config=None, _reference_lengths=[99])
class Clusterer:
    def __init__(self, **kwargs):
        assert kwargs['model'] == 'retained model'
        assert kwargs['length_config'].mode == 'empirical'
        assert kwargs['length_config'].empirical_lengths == [1, 1, 3]
        assert sampler._reference_lengths == [0, 1, 1, 3]
        assert sampler._length_distribution is None
        assert kwargs['n_clusters'] == 3 and kwargs['pool_size'] == 12
        assert kwargs['temperature'] == 1.2 and kwargs['max_kmedoids_iters'] == 17
        assert kwargs['structural_filter'](['write', 'generic'])
        assert not kwargs['structural_filter'](['read'])
        assert kwargs['substitution_costs'].cost_same_group == 0.2
        assert kwargs['substitution_costs'].group_map == {{'write': 'g', 'generic': 'g'}}
    def generate_pool(self): return [['pool']]
    def recluster_with_frozen(self, frozen, count, pool):
        assert frozen == [['accepted_replacement']] and count == 2 and pool == [['pool']]
        return [SimpleNamespace(cluster_idx=30, member_sequences=[['bad'], ['valid']], medoid_sequence=['bad']),
            SimpleNamespace(cluster_idx=31, member_sequences=[['bad']], medoid_sequence=['fallback'])]
class Validator:
    def __init__(self, **kwargs): assert kwargs['model_name'] == 'sequence-model'
    def validate(self, sequence): return SimpleNamespace(valid=sequence == ['valid'])
def load(path):
    assert path == 'relative-checkpoint.json' and Path.cwd() == root
    return sampler
with patch.object(DomainConfig, 'service', side_effect=AssertionError('native service invoked')), \
     patch.object(stage, 'generate_single_task', side_effect=AssertionError('task generation invoked')), \
     patch('src.common.sampler.action_sequence_ngram_sampler.ActionSequenceNGramModelSampler.load_state', side_effect=load), \
     patch('src.common.sampler.action_sequence_clusterer.ActionSequenceClusterer', Clusterer), \
     patch('src.stage2_cluster.action_sequence_validator.ActionSequenceValidator', Validator):
    sys.argv = ['protocol', 'recover', '--clusters', str(root / 'clusters.json'),
        '--outcomes', str(root / 'outcomes.json'), '--workspace', str(root),
        '--model-name', 'sequence-model', '--pool-size', '12', '--temperature', '1.2',
        '--output', str(root / 'recovery.json')]
    runpy.run_path({str(SCRIPT)!r}, run_name='__main__')
result = json.loads((root / 'recovery.json').read_text())
assert result['schema'] == 'taste.recovery.v1' and result['skipped'] is None
replacements = result['replacements']
assert [item['cluster_idx'] for item in replacements] == [8, 9]
assert [item['index'] for item in replacements] == [1, 2]
assert [item['action_sequence'] for item in replacements] == [['valid'], ['fallback']]
assert [item['sequence_validated'] for item in replacements] == [True, False]
assert [item['parent_sequence'] for item in replacements] == [['failed_a'], ['failed_b']]
assert [check['valid'] for check in replacements[0]['sequence_checks']] == [False, True]
assert [check['action_sequence'] for check in replacements[1]['sequence_checks']] == [['bad']]
assert not any(name.startswith(('benchmark_runtime', 'tau2', 'unavailable_native', 'litellm')) for name in sys.modules)
""")


@pytest.mark.parametrize("reason", ["no_failed_sequences", "missing_checkpoint", "no_successful_sequences"])
def test_recovery_skip_conditions_do_not_generate_tasks(tmp_path, reason):
    run_source(f"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.common.domain_config import DomainConfig
from src.stage3_tasks import generate_tasks as stage
root = Path({str(tmp_path)!r})
(root / 'tool_spec.json').write_text('{{}}')
config = DomainConfig('mb1', resources_dir=str(root))
artifact = {{'backend_spec': config.to_spec(), 'ngram_checkpoint': 'checkpoint'}}
reason = {reason!r}
if reason == 'missing_checkpoint': del artifact['ngram_checkpoint']
(root / 'clusters.json').write_text(json.dumps(artifact))
sampler = SimpleNamespace(backend_spec=config.to_spec())
with patch.object(DomainConfig, 'service', side_effect=AssertionError('native service invoked')), \
     patch('src.common.sampler.action_sequence_ngram_sampler.ActionSequenceNGramModelSampler.load_state', return_value=sampler), \
     patch('src.common.sampler.action_sequence_clusterer.ActionSequenceClusterer', side_effect=AssertionError('unexpected clustering')):
    result = stage.select_recovery_representatives([] if reason == 'no_failed_sequences' else [0],
        [{{'cluster_idx': 7, 'action_sequence': ['failed']}}], [{{'success': False}}],
        str(root / 'clusters.json'), 'mb1', 'sequence-model', domain_config=config)
assert result == {{'replacements': [], 'skipped': reason}}
""")


def test_automated_runner_uses_recovery_selection_and_retains_generation_checkpoint(tmp_path):
    run_source(f"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.stage3_tasks import generate_tasks as stage
root = Path({str(tmp_path)!r})
replacement = {{'index': 1, 'cluster_idx': 9, 'action_sequence': ['replacement'],
    'source': 'reclustered', 'parent_sequence': ['failed'], 'sequence_validated': False}}
entries = [{{'cluster_idx': 7, 'action_sequence': ['accepted']}},
    {{'cluster_idx': 9, 'action_sequence': ['failed']}}]
generated = SimpleNamespace(task=SimpleNamespace(row={{}}))
all_generated, metadata, saved = [None, None], [{{'success': True}}, {{'success': False}}], []
def generate(entry, index, *args, **kwargs):
    assert entry == replacement and index == 1
    assert kwargs['max_patch_attempts'] == 2 and kwargs['gt_coverage_p'] == 0.25
    return generated, {{'index': 1, 'cluster_idx': 9, 'success': True}}
with patch.object(stage, 'select_recovery_representatives', return_value={{'replacements': [replacement], 'skipped': None}}) as select, \
     patch.object(stage, 'generate_single_task', side_effect=generate):
    stage._recluster_and_retry([1], entries, all_generated, metadata, 'clusters.json',
        'mb1', 'generation-model', 'feasibility-model', 1, str(root / 'progress.jsonl'),
        domain_config=object(), max_patch_attempts=2, gt_coverage_p=0.25, on_task_success=saved.append)
    assert select.call_count == 1
assert all_generated[1] is generated and saved == [generated]
assert generated.task.row['generation'] == metadata[1]
assert metadata[1]['parent_sequence'] == ['failed'] and metadata[1]['reclustered'] is True
assert json.loads((root / 'progress.jsonl').read_text()) == metadata[1]
""")


@pytest.mark.parametrize(
    "outcomes",
    [
        [],
        [{"cluster_idx": 7, "success": True, "action_sequence": ["a"]}] * 2,
        [{"cluster_idx": 7, "success": True}],
        [{"cluster_idx": 7, "success": "false", "action_sequence": ["a"]}],
    ],
)
def test_recovery_rejects_ambiguous_outcomes(outcomes):
    run_source(f"""
import json, runpy
module = runpy.run_path({str(SCRIPT)!r})
try:
    module['_outcomes'](json.loads({json.dumps(outcomes)!r}), [{{'cluster_idx': 7}}])
except ValueError:
    pass
else:
    raise AssertionError('Ambiguous recovery outcomes were accepted')
""")
