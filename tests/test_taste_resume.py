# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Durable TASTE stage-3 checkpoints and interruption recovery."""

import pytest
from test_taste_backends import run_source

PRELUDE = """
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.backends.contracts import NativeTask, ValidationResult, IncompatibleBackend
from src.common.domain_config import DomainConfig
from src.stage3_tasks import generate_tasks as stage
from src.stage3_tasks import task_set
from src.stage3_tasks.task_builder import GeneratedTask

def row(index=0, cluster_idx=7, sequence=None):
    sequence = sequence or ['replacement']
    return {
        'id': f'generated_{index}',
        'candidate': {'task': {'intent': 'perform the operation'},
                      'reference_actions': [{'name': name, 'arguments': {}} for name in sequence]},
        'fixture': {}, 'native_task': {'request': 'perform the operation'},
        'generation': {'index': index, 'cluster_idx': cluster_idx, 'action_sequence': sequence,
                       'success': True, 'source': 'reclustered', 'parent_sequence': ['original'],
                       'validation_evidence': 'private/validation.json'},
    }

class Service:
    def __init__(self):
        self.fail_export = False
        self.exports = 0
    def export(self, rows, path):
        self.exports += 1
        path = Path(path)
        if self.fail_export:
            path.write_text('interrupted native export')
            raise RuntimeError('simulated interruption after checkpoint commit')
        path.write_text(''.join(json.dumps(dict(record['native_task'], id=record['id'])) + '\\n' for record in rows))
        return path
"""


def test_saved_payload_without_progress_resumes_once_and_rebuilds_exports(tmp_path):
    run_source(
        PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + """
os.chdir(root)
resources = root / 'inputs'
resources.mkdir()
config = DomainConfig('counter', resources_dir=str(resources))
clusters = root / 'clusters.json'
clusters.write_text(json.dumps({
    'backend_spec': config.to_spec(), 'length_distribution': {'mode': 'from_seed'},
    'clusters': [{'cluster_idx': 7, 'representative_sequence': ['original']},
                 {'cluster_idx': 9, 'representative_sequence': ['other']}],
}))
service = Service()
service.fail_export = True
generated_indices = []
def generate(entry, index, *args, **kwargs):
    generated_indices.append(index)
    record = row(index, entry['cluster_idx'], ['replacement'] if index == 0 else ['other'])
    return GeneratedTask(NativeTask(record)), copy.deepcopy(record['generation'])

# Keep the normal manager but place its output beside this run's checkpoints.
manager = task_set.TaskSetManager(str(root / 'task_sets'), domain_config=config)
config._service = service
with patch.object(DomainConfig, 'service', return_value=service), \
     patch.object(stage, 'generate_single_task', side_effect=generate), \
     patch.object(stage, 'TaskSetManager', return_value=manager):
    sys.argv = ['generate', '--clusters', str(clusters), '--name', 'run']
    try:
        stage.main()
    except RuntimeError as error:
        assert 'simulated interruption' in str(error)
    else:
        raise AssertionError('interruption was not simulated')
    directory = root / 'task_sets/run'
    saved = json.loads((directory / 'tasks.json').read_text())
    assert len(saved) == 1 and saved[0]['generation']['action_sequence'] == ['replacement']
    assert not (directory / 'progress.jsonl').exists()

    service.fail_export = False
    sys.argv = ['generate', '--clusters', str(clusters), '--resume', 'run']
    stage.main()
    assert generated_indices == [0, 1], 'the committed cluster must not regenerate'
    saved = json.loads((directory / 'tasks.json').read_text())
    assert [record['id'] for record in saved] == ['generated_0', 'generated_1']
    progress = [json.loads(line) for line in (directory / 'progress.jsonl').read_text().splitlines()]
    assert [record['cluster_idx'] for record in progress] == [7, 9]
    assert progress[0]['action_sequence'] == ['replacement']
    assert progress[0]['validation_evidence'] == 'private/validation.json'

    for name in ('native-tasks.jsonl', 'config.json', 'metadata.json', 'generation_meta.json'):
        (directory / name).unlink()
    stage.main()
    assert generated_indices == [0, 1], 'a complete resume only rebuilds derived outputs'
    native = [json.loads(line) for line in (directory / 'native-tasks.jsonl').read_text().splitlines()]
    assert [record['id'] for record in native] == ['generated_0', 'generated_1']
    assert all('generation' not in record for record in native)
    assert json.loads((directory / 'metadata.json').read_text())['num_tasks'] == 2
    assert len(json.loads((directory / 'generation_meta.json').read_text())['tasks']) == 2
"""
    )


def test_atomic_checkpoint_failure_preserves_previous_payload(tmp_path):
    run_source(
        PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + """
service = Service()
config = SimpleNamespace(service=lambda: service)
task_set.save_task_rows(str(root), [row()], domain_config=config)
before = (root / 'tasks.json').read_bytes()
with patch.object(task_set.os, 'replace', side_effect=OSError('simulated commit interruption')):
    try:
        task_set.save_task_rows(str(root), [row(), row(1, 9)], domain_config=config)
    except OSError:
        pass
    else:
        raise AssertionError('commit failure was not simulated')
assert (root / 'tasks.json').read_bytes() == before
assert service.exports == 1
assert not list(root.glob('.tasks.json.*.tmp'))
"""
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("rows = []", "no payload"),
        ("rows.append(copy.deepcopy(rows[0]))", "duplicate task identity"),
        ("rows[0]['id'] = 'generated_99'", "identity"),
        ("del rows[0]['native_task']", "payload"),
        ("del rows[0]['generation']", "provenance"),
        ("rows[0]['generation']['action_sequence'] = ['changed']", "payload/provenance"),
        ("progress[0]['validation_evidence'] = 'another/validation.json'", "conflict"),
        ("progress.append(dict(progress[0], index=1))", "conflicting successful progress"),
    ],
)
def test_resume_rejects_missing_or_conflicting_successful_records(tmp_path, mutation, expected):
    run_source(
        PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + "rows = [row()]\nprogress = [copy.deepcopy(rows[0]['generation'])]\n"
        + mutation
        + f"\nexpected = {expected!r}\n"
        + """
(root / 'tasks.json').write_text(json.dumps(rows))
(root / 'progress.jsonl').write_text(''.join(json.dumps(record) + '\\n' for record in progress))
try:
    stage._reconcile_resume(str(root), [{'cluster_idx': 7, 'action_sequence': ['original']}])
except ValueError as error:
    assert expected in str(error), str(error)
else:
    raise AssertionError('inconsistent checkpoint was accepted')
"""
    )


def test_resume_repairs_interrupted_progress_and_freezes_replacement_sequence(tmp_path):
    run_source(
        PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + """
resources = root / 'inputs'
resources.mkdir()
(resources / 'tool_spec.json').write_text('{}')
config = DomainConfig('counter', resources_dir=str(resources))
config.sequence_validator_factory = lambda **kwargs: object()
(root / 'tasks.json').write_text(json.dumps([row()]))
(root / 'progress.jsonl').write_text('{"success":')
entries = [{'cluster_idx': 7, 'action_sequence': ['original']},
           {'cluster_idx': 9, 'action_sequence': ['failed']}]
rows, completed = stage._reconcile_resume(str(root), entries)
assert json.loads((root / 'progress.jsonl').read_text()) == rows[0]['generation']
assert stage._reconcile_resume(str(root), entries) == (rows, completed)

clusters = root / 'clusters.json'
clusters.write_text(json.dumps({'backend_spec': config.to_spec(), 'ngram_checkpoint': 'scripted'}))
sampler = SimpleNamespace(backend_spec=config.to_spec(), model=None, length_distribution=None, _length_config=None)
class Clusterer:
    def __init__(self, **kwargs): pass
    def generate_pool(self): return []
    def recluster_with_frozen(self, frozen, count, pool):
        assert frozen == [['replacement']], 'resume must retain the successful replacement representative'
        assert count == 1
        return []
with patch('src.common.sampler.action_sequence_ngram_sampler.ActionSequenceNGramModelSampler.load_state', return_value=sampler), \
     patch('src.common.sampler.action_sequence_clusterer.ActionSequenceClusterer', Clusterer):
    stage._recluster_and_retry([1], entries, [None, None], [completed[7], {'index': 1, 'success': False}],
        str(clusters), 'counter', 'scripted', 'scripted', 0, str(root / 'progress.jsonl'), domain_config=config)
"""
    )


def test_generation_retains_provenance_and_unsupported_service_stops(tmp_path):
    run_source(
        PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + """
config = DomainConfig('counter', resources_dir=str(root))
candidate = row()['candidate']
result = ValidationResult(True, True, True, native_task={'request': 'operation'}, evidence_path='receipt.json')
with patch.object(stage, 'TaskGenerator'), patch.object(stage, 'TaskValidator'), \
     patch.object(stage, 'validate_task_with_retries', return_value=(True, candidate, {}, result, 1)):
    generated, metadata = stage.generate_single_task({'cluster_idx': 7, 'action_sequence': ['replacement']},
        0, 1, 'counter', 'scripted', 'scripted', 1, domain_config=config)
    assert generated.to_dict()['generation'] == metadata
    assert metadata['validation_evidence'] == 'receipt.json'
result = ValidationResult(False, False, False, error='native grader unavailable', unsupported=('native_grading',))
with patch.object(stage, 'TaskGenerator'), patch.object(stage, 'TaskValidator'), \
     patch.object(stage, 'validate_task_with_retries', return_value=(False, candidate, {}, result, 0)):
    try:
        stage.generate_single_task({'cluster_idx': 7, 'action_sequence': ['replacement']},
            0, 1, 'counter', 'scripted', 'scripted', 1, domain_config=config)
    except IncompatibleBackend as error:
        assert 'native_grading' in str(error)
    else:
        raise AssertionError('unsupported capability remained eligible for cluster recovery')
"""
    )
