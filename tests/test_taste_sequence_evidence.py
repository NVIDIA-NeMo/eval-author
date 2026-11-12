# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sequence validation evidence with scripted provider responses."""

import pytest
from test_taste_backends import run_source

COMMON_PRELUDE = r"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.common.sequence_evidence import SequenceEvidence

def records(log):
    return [json.loads(line) for line in log.path.read_text().splitlines()]
"""

LLM_PRELUDE = (
    COMMON_PRELUDE
    + r"""
from src.common.call_to_llm import LLMCaller

SECRET = 'provider-private-token-that-must-not-be-retained'

class ProviderError(RuntimeError):
    def __init__(self, status):
        super().__init__(f'{status}: Authorization: Bearer {SECRET}')
        self.status_code = status
        self.request = {'headers': {'Authorization': SECRET}}
        self.response = {'headers': {'Set-Cookie': SECRET}}

class Response(SimpleNamespace):
    def model_dump(self, *, include):
        return {key: copy.deepcopy(self.payload[key]) for key in include if key in self.payload}

def response(identity, text, finish_reason, prompt_tokens, completion_tokens):
    usage = {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
             'total_tokens': prompt_tokens + completion_tokens,
             'completion_tokens_details': {'reasoning_tokens': 2}}
    choice = {'index': 0, 'finish_reason': finish_reason,
              'message': {'role': 'assistant', 'content': text}}
    payload = {'id': identity, 'object': 'chat.completion', 'created': 123,
               'model': 'provider/scripted', 'choices': [choice], 'usage': usage,
               'headers': {'Authorization': SECRET},
               '_hidden_params': {'api_key': SECRET},
               'request': {'headers': {'Authorization': SECRET}},
               'response_headers': {'Set-Cookie': SECRET}}
    return Response(payload=payload, usage=SimpleNamespace(**usage),
                    choices=[SimpleNamespace(finish_reason=finish_reason,
                                             message=SimpleNamespace(content=text))])
"""
)


def test_llm_retains_truncated_and_retried_responses_with_all_observed_usage(tmp_path):
    output = run_source(
        LLM_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + r"""
log = SequenceEvidence(root / 'evidence')
truncated = response('truncated', 'partial output', 'length', 11, 7)
accepted = response('accepted', 'complete output', 'stop', 13, 5)
provider = SimpleNamespace(
    completion=Mock(side_effect=[truncated, ProviderError(503), accepted]),
    completion_cost=Mock(side_effect=lambda *, completion_response:
                         0.125 if completion_response.payload['id'] == 'truncated' else 0.25),
)
with patch.dict(sys.modules, {'litellm': provider}), \
     patch('src.common.call_to_llm.time.sleep') as sleep:
    caller = LLMCaller(model_name='provider/scripted', temperature=0.2,
                       max_output_tokens=64, evidence=log)
    result = caller.call('validate sequence', context='retained policy',
                         stage_name='sequence', evidence_context={'sample': 7})
assert result == 'complete output'
assert provider.completion.call_count == 3 and sleep.call_count == 2
rows = records(log)
assert [row['index'] for row in rows] == list(range(len(rows)))
started = [row for row in rows if row['event'] == 'llm_call_started']
assert len(started) == 1
assert started[0]['prompt'] == 'retained policy\n\nvalidate sequence'
assert started[0]['settings'] == {'temperature': 0.2, 'max_tokens': 64}
assert started[0]['context'] == {'sample': 7} and started[0]['stage'] == 'sequence'
assert started[0]['model'] == started[0]['routed_model'] == 'provider/scripted'
call_id = started[0]['index']
assert all(row['call_id'] == call_id for row in rows[1:])
attempts = [row for row in rows if row['event'] == 'llm_attempt_started']
assert [row['attempt'] for row in attempts] == [1, 2, 3]
responses = [row for row in rows if row['event'] == 'llm_attempt_response']
assert [row['attempt'] for row in responses] == [1, 3]
assert [row['response']['id'] for row in responses] == ['truncated', 'accepted']
assert [row['response']['choices'][0]['message']['content'] for row in responses] == [
    'partial output', 'complete output']
assert [row['response']['choices'][0]['finish_reason'] for row in responses] == ['length', 'stop']
assert [row['response']['usage'] for row in responses] == [truncated.payload['usage'], accepted.payload['usage']]
assert sum(row['response']['usage']['total_tokens'] for row in responses) == 36
assert [row['cost_usd'] for row in responses] == [0.125, 0.25]
assert all(set(row['response']) == {'id', 'object', 'created', 'model', 'choices', 'usage'} for row in responses)
failures = [row for row in rows if row['event'] == 'llm_attempt_failed']
assert len(failures) == 1 and failures[0]['attempt'] == 2
assert failures[0]['error'] == {'classes': ['ProviderError'], 'http_status': 503}
errors = [row for row in rows if row['event'] == 'llm_call_error']
assert len(errors) == 1 and errors[0]['error']['classes'] == ['ValueError']
assert caller.last_usage.total_tokens == 18
assert SECRET not in log.path.read_text()
"""
    )
    assert "provider-private-token-that-must-not-be-retained" not in output


def test_llm_unknown_cost_and_terminal_failure_preserve_prior_evidence_without_stale_usage(tmp_path):
    output = run_source(
        LLM_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + r"""
log = SequenceEvidence(root / 'evidence')
failure = ProviderError(401)
failure.__cause__ = ValueError(SECRET)
provider = SimpleNamespace(
    completion=Mock(side_effect=[response('accepted', 'complete output', 'stop', 2, 3), failure]),
    completion_cost=Mock(side_effect=LookupError(SECRET)),
)
with patch.dict(sys.modules, {'litellm': provider}), \
     patch('src.common.call_to_llm.time.sleep') as sleep:
    caller = LLMCaller(model_name='provider/scripted', evidence=log)
    assert caller.call('first sequence') == 'complete output'
    before = log.path.read_bytes()
    assert caller.last_usage.total_tokens == 5
    try:
        caller.call('second sequence')
    except ProviderError as error:
        assert error is failure
    else:
        raise AssertionError('terminal provider failure was hidden')
assert provider.completion.call_count == 2 and sleep.call_count == 0
assert caller.last_usage is None
assert log.path.read_bytes().startswith(before)
rows = records(log)
started = [row for row in rows if row['event'] == 'llm_call_started']
assert [row['prompt'] for row in started] == ['first sequence', 'second sequence']
responses = [row for row in rows if row['event'] == 'llm_attempt_response']
assert len(responses) == 1 and responses[0]['cost_usd'] is None
assert responses[0]['response']['usage']['total_tokens'] == 5
assert responses[0]['call_id'] == started[0]['index']
failures = [row for row in rows if row['event'] == 'llm_attempt_failed']
errors = [row for row in rows if row['event'] == 'llm_call_error']
assert len(failures) == len(errors) == 1
assert failures[0]['attempt'] == 1
for row in failures + errors:
    assert row['call_id'] == started[1]['index']
    assert row['error'] == {'classes': ['ProviderError', 'ValueError'], 'http_status': 401}
assert SECRET not in log.path.read_text()
"""
    )
    assert "provider-private-token-that-must-not-be-retained" not in output


def test_llm_preserves_outer_retry_after_exhausted_transport_retries(tmp_path):
    output = run_source(
        LLM_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + r"""
log = SequenceEvidence(root / 'evidence')
provider = SimpleNamespace(
    completion=Mock(side_effect=[ProviderError(503) for _ in range(4)] +
                               [response('recovered', 'complete output', 'stop', 2, 3)]),
    completion_cost=Mock(return_value=0.125),
)
with patch.dict(sys.modules, {'litellm': provider}), \
     patch('src.common.call_to_llm.time.sleep') as sleep:
    caller = LLMCaller(model_name='provider/scripted', evidence=log)
    assert caller.call('sequence') == 'complete output'
assert provider.completion.call_count == 5 and sleep.call_count == 4
rows = records(log)
attempts = [row for row in rows if row['event'] == 'llm_attempt_started']
assert [row['attempt'] for row in attempts] == [1, 2, 3, 4, 5]
failed = [row for row in rows if row['event'] == 'llm_attempt_failed']
assert [row['attempt'] for row in failed] == [1, 2, 3, 4]
errors = [row for row in rows if row['event'] == 'llm_call_error']
assert len(errors) == 1 and errors[0]['error']['classes'][0] == 'LLMConnectivityError'
responses = [row for row in rows if row['event'] == 'llm_attempt_response']
assert len(responses) == 1 and responses[0]['attempt'] == 5
assert responses[0]['response']['id'] == 'recovered'
assert SECRET not in log.path.read_text()
"""
    )
    assert "provider-private-token-that-must-not-be-retained" not in output


def test_pool_evidence_retains_filtered_duplicate_and_accepted_draws_without_changing_sampling(tmp_path):
    run_source(
        COMMON_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + r"""
import random
from src.common.sampler.action_sequence_clusterer import ActionSequenceClusterer

draws = [['read'], ['write'], ['write'], ['other_write']]
def clusterer_for(evidence):
    remaining = iter(copy.deepcopy(draws))
    def generate(**kwargs):
        kwargs['rng'].random()
        return next(remaining)
    return ActionSequenceClusterer(
        model=SimpleNamespace(generate_random_sequence=Mock(side_effect=generate)),
        length_distribution=SimpleNamespace(sample_length=Mock(return_value=1)),
        length_config=None, n_clusters=1, pool_size=2, temperature=1.2, rng=random.Random(7),
        structural_filter=lambda sequence: sequence != ['read'], evidence=evidence,
    )
plain = clusterer_for(None)
expected = plain.generate_pool()
log = SequenceEvidence(root / 'evidence')
observed = clusterer_for(log)
assert observed.generate_pool() == expected == [['write'], ['other_write']]
assert observed.rng.getstate() == plain.rng.getstate()
assert observed._total_attempts == plain._total_attempts == 4
assert observed._filtered_out == plain._filtered_out == 1
assert observed.model.generate_random_sequence.call_count == plain.model.generate_random_sequence.call_count == 4
rows = records(log)
assert rows[0]['event'] == 'pool_started' and rows[0]['requested'] == 2
retained = [row for row in rows if row['event'] == 'pool_draw']
assert [row['sequence'] for row in retained] == draws
assert [row['attempt'] for row in retained] == [1, 2, 3, 4]
assert [row['outcome'] for row in retained] == ['filtered', 'accepted', 'duplicate', 'accepted']
assert all(row['requested_length'] == 1 for row in retained)
assert all(row['pool_index'] == 0 for row in rows)
assert rows[-1]['event'] == 'pool_completed'
assert rows[-1]['sequences'] == expected and rows[-1]['attempts'] == 4 and rows[-1]['filtered'] == 1
"""
    )


MEDOID_PRELUDE = (
    COMMON_PRELUDE
    + r"""
from src.common.sampler.action_sequence_clusterer import ClusterInfo
from src.stage2_cluster.action_sequence_validator import ActionSequenceValidator
from src.stage2_cluster.medoid_validation import validate_clusters

medoid = ['start', 'bad']
preferred = ['start', 'different']
fallback = ['other', 'bad']
cluster = ClusterInfo(cluster_idx=7, medoid_idx=11, medoid_sequence=medoid,
                      member_indices=[11, 13, 17], member_sequences=[medoid, fallback, preferred],
                      intra_cluster_distances=[0.0, 1.0, 2.0])
original = {'valid': False, 'reason': 'original failure',
            'problematic_indices': [1, -1, 20, '1'], 'extra_explanation': 'retained extension'}

def validator_for(log, responses):
    spec = root / 'tool_spec.json'
    spec.write_text(json.dumps({name: {'parameters': {}}
                               for name in ('start', 'bad', 'different', 'other')}))
    caller = SimpleNamespace(call=Mock(side_effect=responses))
    config = SimpleNamespace(
        domain='control', load_policy=lambda: 'retained policy', tool_spec_path=str(spec),
        prompt_manager=lambda: SimpleNamespace(get_prompt=lambda name:
            'Sequence: {action_sequence}\nPolicy: {policy}\nTools: {tool_spec}'),
        make_llm=lambda **kwargs: caller,
    )
    return ActionSequenceValidator(domain_config=config, evidence=log), caller
"""
)


def test_medoid_evidence_preserves_preferred_then_fallback_checks_and_decision_references(tmp_path):
    run_source(
        MEDOID_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\n"
        + r"""
responses = [json.dumps(original),
             json.dumps({'valid': False, 'reason': 'preferred failure', 'problematic_indices': [0]}),
             json.dumps({'valid': True, 'reason': 'fallback accepted', 'problematic_indices': []})]
plain_validator, plain_caller = validator_for(None, copy.deepcopy(responses))
expected = validate_clusters([cluster], plain_validator, max_replacements=2, round_index=3)
log = SequenceEvidence(root / 'evidence')
validator, caller = validator_for(log, copy.deepcopy(responses))
actual = validate_clusters([cluster], validator, max_replacements=2, evidence=log, round_index=3)
assert actual == expected
assert actual[0]['final_sequence'] == fallback and actual[0]['attempts'] == 3
assert actual[0]['status'] == 'replaced' and not actual[0]['original_valid']
assert caller.call.call_count == plain_caller.call.call_count == 3
assert [call.kwargs['prompt'] for call in caller.call.call_args_list] == [
    call.kwargs['prompt'] for call in plain_caller.call.call_args_list]
rows = records(log)
started = [row for row in rows if row['event'] == 'cluster_candidate_started']
completed = [row for row in rows if row['event'] == 'cluster_candidate_completed']
assert [row['sequence'] for row in started] == [medoid, preferred, fallback]
assert [row['pass_name'] for row in started] == ['medoid', 'preferred', 'fallback']
assert [row['member_index'] for row in started] == [None, 2, 1]
assert len(completed) == 3
for pending, finished in zip(started, completed):
    for key in ('sequence', 'pass_name', 'member_index', 'cluster_idx', 'round_index'):
        assert finished[key] == pending[key]
    assert finished['round_index'] == 3 and finished['cluster_idx'] == 7
decisions = [row for row in rows if row['event'] == 'validation_decision']
assert [row['decision']['valid'] for row in decisions] == [False, False, True]
assert [row['decision']['reason'] for row in decisions] == [
    'original failure', 'preferred failure', 'fallback accepted']
assert decisions[0]['parsed'] == original and decisions[0]['decision']['problematic_indices'] == [1]
assert [row['response_text'] for row in decisions] == responses
assert [row['decision']['evidence_id'] for row in completed] == [row['index'] for row in decisions]
validation_starts = {row['index']: row for row in rows if row['event'] == 'validation_started'}
for sequence, decision in zip([medoid, preferred, fallback], decisions):
    assert validation_starts[decision['validation_id']]['sequence'] == sequence
finished = [row for row in rows if row['event'] == 'cluster_completed']
assert len(finished) == 1 and finished[0]['result'] == actual[0] and finished[0]['round_index'] == 3
"""
    )


@pytest.mark.parametrize(
    "response_text",
    ["not a validation response", "[]", '{"valid":false,"problematic_indices":null}'],
)
def test_failed_candidate_retains_prior_decision_and_raw_response_without_completing_cluster(tmp_path, response_text):
    run_source(
        MEDOID_PRELUDE
        + f"\nroot = Path({str(tmp_path)!r})\nbroken_response = {response_text!r}\n"
        + r"""
log = SequenceEvidence(root / 'evidence')
validator, caller = validator_for(log, [json.dumps(original), broken_response])
try:
    validate_clusters([cluster], validator, max_replacements=2, evidence=log, round_index=4)
except (ValueError, AttributeError, TypeError):
    pass
else:
    raise AssertionError('invalid validator response was accepted')
assert caller.call.call_count == 2
rows = records(log)
assert [row['index'] for row in rows] == list(range(len(rows)))
starts = [row for row in rows if row['event'] == 'cluster_candidate_started']
assert [row['sequence'] for row in starts] == [medoid, preferred]
assert all(row['round_index'] == 4 and row['cluster_idx'] == 7 for row in starts)
completed = [row for row in rows if row['event'] == 'cluster_candidate_completed']
decisions = [row for row in rows if row['event'] == 'validation_decision']
assert len(completed) == len(decisions) == 1
assert completed[0]['decision']['evidence_id'] == decisions[0]['index']
assert completed[0]['decision']['reason'] == 'original failure'
assert decisions[0]['response_text'] == json.dumps(original)
failures = [row for row in rows if row['event'] == 'validation_failed']
assert len(failures) == 1 and failures[0]['response_text'] == broken_response
validation_starts = {row['index']: row for row in rows if row['event'] == 'validation_started'}
assert validation_starts[failures[0]['validation_id']]['sequence'] == preferred
assert failures[0]['error']['classes'][0] in ('ValueError', 'AttributeError', 'TypeError')
assert not any(row['event'] == 'cluster_completed' for row in rows)
"""
    )
