# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sampler restart parity across processes and both random-number sources."""

import os
import subprocess
import sys

import pytest
from test_taste_backends import VENDOR

PRELUDE = """
import json
import random
import sys
from pathlib import Path
import numpy as np
from src.common.domain_config import DomainConfig
from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler
from src.common.sampler.length_distribution import LengthDistributionConfig

def make_sampler(root, mode):
    resources = root / 'inputs'
    resources.mkdir(exist_ok=True)
    (resources / 'tool_spec.json').write_text(json.dumps({name: {} for name in ['read', 'write', 'lookup', 'finish']}))
    config = DomainConfig('checkpoint-test', resources_dir=str(resources))
    rows = [
        {'id': str(index), 'split': 'train', 'actions': [{'name': name, 'arguments': {}} for name in sequence]}
        for index, sequence in enumerate([[], ['read'], ['lookup', 'read', 'finish'], ['read', 'write', 'read', 'finish']])
    ]
    seeds = config.seeds(rows)
    if mode == 'from_seed':
        lengths = LengthDistributionConfig(mode=mode, reference_source='original')
    elif mode == 'skew_normal':
        lengths = LengthDistributionConfig(mode=mode, gaussian_mean=4, gaussian_std=2, skew_alpha=1.5,
                                          min_length=1, max_length=7)
    else:
        lengths = LengthDistributionConfig(mode=mode, empirical_lengths=[1, 1, 2, 3, 6])
    sampler = ActionSequenceNGramModelSampler(seeds, domain='checkpoint-test', backend_spec=config.to_spec(),
        tool_spec_path=config.tool_spec_path, length_distribution_config=lengths, reference_tasks=seeds,
        rng=random.Random(281), n=3, alpha0=0.25, lambda_neg=0.75, tau0=1.8, tau_decay_steps=7,
        positive_weight=1.5, negative_weight=0.75)
    sampler.ingest(['read'], positive=True)
    sampler.ingest(['read', 'lookup', 'finish'], positive=True)
    sampler.ingest(['write', 'write'], positive=False, problematic_indices=[0, 1])
    return sampler

def advance(sampler, count):
    draws = []
    for index in range(count):
        sequence, status = sampler.get_new_action_sequence()
        draws.append([sequence, status, sampler.current_temperature])
        if status == 'new':
            sampler.ingest(sequence, positive=(index % 3 != 1), problematic_indices=[len(sequence) - 1])
    return draws

def normalized_state(sampler):
    state = json.loads(json.dumps(sampler._serialize_state()))
    for name in ('accepted_set', 'rejected_set'):
        state['sampler'][name].sort()
    return state
"""


def run_sampler(source, hash_seed):
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(VENDOR)!r})\n" + PRELUDE + source],
        env={**os.environ, "PYTHONHASHSEED": str(hash_seed)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["from_seed", "skew_normal", "empirical"])
def test_checkpoint_continues_exact_draws_and_feedback_across_processes(tmp_path, mode):
    setup = f"\nroot = Path({str(tmp_path)!r})\n"
    run_sampler(
        setup
        + f"sampler = make_sampler(root, {mode!r})\n"
        + """
np.random.seed(739)
advance(sampler, 11)
while sampler._rng.getstate()[2] is not None:
    sampler._rng.gauss(0, 1)
sampler._rng.gauss(0, 1)
while np.random.get_state()[3]:
    np.random.normal()
np.random.normal()
before = normalized_state(sampler)
assert before['sampler']['rng_state']['python'][2] is not None
assert before['sampler']['rng_state']['numpy']['has_gauss'] == 1
assert before['model']['counts']['pos_ngram'] and before['model']['counts']['neg_ngram']
assert before['sampler']['pending_seeds']
sampler.save_state(str(root / 'checkpoint.json'))
assert normalized_state(sampler) == before, 'saving must not consume randomness'
cache_draws = [sampler._rng.gauss(0, 1), float(np.random.normal())]
draws = advance(sampler, 100)
(root / 'expected.json').write_text(json.dumps({'cache_draws': cache_draws, 'draws': draws,
                                             'state': normalized_state(sampler)}))
""",
        31,
    )
    run_sampler(
        setup
        + """
np.random.seed(999)
sampler = ActionSequenceNGramModelSampler.load_state(str(root / 'checkpoint.json'))
assert sampler.checkpoint_rng_restored is True
expected = json.loads((root / 'expected.json').read_text())
assert [sampler._rng.gauss(0, 1), float(np.random.normal())] == expected['cache_draws']
assert advance(sampler, 100) == expected['draws']
assert normalized_state(sampler) == expected['state']
""",
        79,
    )


def test_legacy_checkpoint_is_explicitly_inexact_and_preserves_learned_state(tmp_path):
    run_sampler(
        f"\nroot = Path({str(tmp_path)!r})\n"
        + """
sampler = make_sampler(root, 'from_seed')
state = normalized_state(sampler)
del state['sampler']['rng_state']
(root / 'legacy.json').write_text(json.dumps(state))
np.random.seed(41)
before_numpy = np.random.get_state()
loaded = ActionSequenceNGramModelSampler.load_state(str(root / 'legacy.json'))
assert loaded.checkpoint_rng_restored is False
after_numpy = np.random.get_state()
assert before_numpy[0] == after_numpy[0]
assert np.array_equal(before_numpy[1], after_numpy[1])
assert before_numpy[2:] == after_numpy[2:]
restored = normalized_state(loaded)
del restored['sampler']['rng_state']
assert restored == state
""",
        31,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "state['sampler']['rng_state'] = None",
        "state['sampler']['rng_state']['version'] = 2",
        "state['sampler']['rng_state']['version'] = True",
        "del state['sampler']['rng_state']['python']",
        "del state['sampler']['rng_state']['numpy']",
        "state['sampler']['rng_state']['python'] = []",
        "state['sampler']['rng_state']['python'].append('extra')",
        "del state['sampler']['rng_state']['numpy']['cached_gaussian']",
        "state['sampler']['rng_state']['numpy']['bit_generator'] = 'unsupported'",
        "state['sampler']['rng_state']['numpy']['position'] = -1",
        "state['sampler']['rng_state']['numpy']['has_gauss'] = 2",
    ],
)
def test_partial_or_invalid_rng_checkpoint_fails_without_changing_global_rng(tmp_path, mutation):
    run_sampler(
        f"\nroot = Path({str(tmp_path)!r})\n"
        + "sampler = make_sampler(root, 'from_seed')\nstate = normalized_state(sampler)\n"
        + mutation
        + """
(root / 'bad.json').write_text(json.dumps(state))
np.random.seed(129)
before_numpy = np.random.get_state()
before_python = sampler._rng.getstate()
try:
    ActionSequenceNGramModelSampler.load_state(str(root / 'bad.json'))
except ValueError as error:
    assert 'RNG state' in str(error)
else:
    raise AssertionError('malformed RNG state was accepted')
after_numpy = np.random.get_state()
assert before_numpy[0] == after_numpy[0]
assert np.array_equal(before_numpy[1], after_numpy[1])
assert before_numpy[2:] == after_numpy[2:]
try:
    sampler.restore_rng_state(state['sampler']['rng_state'])
except ValueError:
    pass
else:
    raise AssertionError('malformed journal RNG state was accepted')
assert sampler._rng.getstate() == before_python
assert np.array_equal(before_numpy[1], np.random.get_state()[1])
assert before_numpy[2:] == np.random.get_state()[2:]
""",
        31,
    )
