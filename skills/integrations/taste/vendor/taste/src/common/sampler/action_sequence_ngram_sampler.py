"""
Action sequence sampler backed by a Bayesian signed n-gram model.

Wraps BayesianSignedNGram to provide the same high-level interface that the
Orchestrator expects (get_new_action_sequence / ingest), while internally
using learned n-gram statistics with temperature scheduling to produce
increasingly plausible action sequences over time.
"""

import json
import random
from collections import defaultdict
from typing import List, Optional, Tuple

import numpy as np

from src.backends.artifacts import require_backend_spec
from src.backends.contracts import IncompatibleBackend, Seed, sequence_of
from src.common.sampler.adaptive_ngram_model import BayesianSignedNGram
from src.common.sampler.checkpointable import CheckpointableModel
from src.common.sampler.length_distribution import LengthDistribution, LengthDistributionConfig
from src.common.tool_spec_retriever import ToolsSpecRetriever


class ActionSequenceNGramModelSampler(CheckpointableModel):
    """
    Generates action sequences using a Bayesian signed n-gram model with
    temperature scheduling and positive/negative feedback.

    Lifecycle:
        1. Initialised with seed tasks (ingested as positive examples) and the
           full action vocabulary from the tool spec.
        2. ``get_new_action_sequence()`` generates a candidate:
               - sample a length from the length distribution
               - compute a temperature via exponential decay schedule
               - generate from the n-gram model at that temperature
        3. The caller validates the candidate (e.g. via ActionSequenceValidator).
        4. ``ingest()`` feeds the result back into the model as positive or
           negative evidence and updates internal bookkeeping (accepted set,
           generation counter).
    """

    def __init__(
        self,
        action_sequence_set: List[Seed],
        domain: str,
        length_distribution_config: Optional[LengthDistributionConfig] = None,
        reference_tasks: Optional[List[Seed]] = None,
        # NGram model parameters
        n: int = 3,
        alpha0: float = 0.1,
        lambda_neg: float = 1.0,
        eps: float = 1e-12,
        tau0: float = 2.0,
        tau_decay_steps: float = 500.0,
        positive_weight: float = 1.0,
        negative_weight: float = 1.0,
        ingest_negatives: bool = True,
        rng: Optional[random.Random] = None,
        *,
        tool_spec_path: str,
        backend_spec: dict,
        length_policy_explicit: bool = True,
    ):
        # ── Store domain ───────────────────────────────────────────────────
        self.backend_spec = require_backend_spec({"backend_spec": backend_spec})
        if domain != self.backend_spec["domain"]:
            raise IncompatibleBackend("Sampler label does not match its prepared input configuration")
        self.domain = domain
        if any(not isinstance(seed, Seed) for seed in action_sequence_set):
            raise TypeError("Sampler seeds must be normalized Seed records from the prepared bundle")
        self.seed_provenance = [{"id": t.id, "split": t.split, "length": len(t.actions)} for t in action_sequence_set]

        # ── Extract action sequences from seed tasks ──────────────────────
        extracted_sequences = [sequence_of(task) for task in action_sequence_set]

        # ── Build vocabulary from tool spec ───────────────────────────────
        tool_spec_retriever = ToolsSpecRetriever(path=tool_spec_path)
        self.available_actions: List[str] = [action[0] for action in tool_spec_retriever.get_tool_spec()]
        vocab: set[str] = set(self.available_actions)

        # ── Store config ──────────────────────────────────────────────────
        self._length_config = length_distribution_config
        self.length_policy_explicit = length_policy_explicit and length_distribution_config is not None
        self._tau0 = tau0
        self._tau_decay_steps = tau_decay_steps
        self._positive_weight = positive_weight
        self._negative_weight = negative_weight
        self._ingest_negatives = ingest_negatives
        self._rng = rng if rng is not None else random.Random()
        self.checkpoint_rng_restored = False

        # ── Reference lengths (for "from_reference" length mode) ──────────
        self._reference_lengths: Optional[List[int]] = None
        if reference_tasks is not None:
            self._reference_lengths = [len(sequence_of(t)) for t in reference_tasks]

        self._length_distribution: Optional[LengthDistribution] = None

        # ── Build the n-gram model and seed it ────────────────────────────
        self.model = BayesianSignedNGram(
            n=n,
            vocab=vocab,
            alpha0=alpha0,
            lambda_neg=lambda_neg,
            eps=eps,
        )

        # ── Bookkeeping ───────────────────────────────────────────────────
        self.accepted_set: set[tuple[str, ...]] = set()
        self.rejected_set: set[tuple[str, ...]] = set()
        self.t: int = 0  # generation counter for temperature schedule
        self._pending_seeds: List[List[str]] = []

        # Defer seeding — seeds must be validated via validate_seed_tasks() before ingestion
        self.action_sequence_set: List[List[str]] = []
        self._pending_seeds = extracted_sequences

    # ── Length distribution (lazy, same pattern as ActionSequenceSampler) ──

    @property
    def length_distribution(self) -> LengthDistribution:
        if self._length_distribution is None:
            if self._length_config is not None and self._length_config.mode == "empirical":
                lengths = self._length_config.empirical_lengths
                assert lengths is not None
            elif (
                self._length_config is not None
                and self._length_config.mode == "from_seed"
                and self._length_config.reference_source is not None
            ):
                if self._reference_lengths is None:
                    raise ValueError("An explicit length reference requires normalized reference_tasks")
                lengths = self._reference_lengths
            else:
                lengths = [len(seq) for seq in self.action_sequence_set]
            self._length_distribution = LengthDistribution.fit(lengths, fallback_lengths=self._reference_lengths)
        return self._length_distribution

    def sample_length(self) -> int:
        """Sample an action sequence length based on the configured mode."""
        return self.length_distribution.sample_length(self._length_config, rng=self._rng)

    # ── Core interface ────────────────────────────────────────────────────

    def get_new_action_sequence(self, length: Optional[int] = None) -> Tuple[List[str], str]:
        """
        Generate a single new action sequence using the n-gram model.

        Steps:
            1. Use the provided *length*, or sample one from the length
               distribution if not given.
            2. Compute temperature via the exponential decay schedule.
            3. Generate a sequence from the n-gram model at that temperature.
            4. Check for exact duplicates against the accepted and rejected sets.

        Args:
            length: Desired sequence length.  When called from the Orchestrator
                    the length is sampled there; when used standalone the
                    sampler falls back to its own length distribution.

        Returns:
            (sequence, status) where *status* is one of:
              - ``"new"``: sequence has not been seen before
              - ``"accepted_dup"``: sequence was already accepted
              - ``"rejected_dup"``: sequence was already rejected
            The caller should typically skip duplicates without validating.
        """
        if length is None:
            length = self.sample_length()
        tau = BayesianSignedNGram.temp_schedule_exp(self.t, tau0=self._tau0, decay_steps=self._tau_decay_steps)

        seq = self.model.generate_random_sequence(
            length=length,
            rng=self._rng,
            temperature=tau,
        )

        key = tuple(seq)
        if key in self.accepted_set:
            return seq, "accepted_dup"
        if key in self.rejected_set:
            return seq, "rejected_dup"
        return seq, "new"

    @property
    def current_temperature(self) -> float:
        """Return the temperature that *would* be used for the next generation."""
        return BayesianSignedNGram.temp_schedule_exp(self.t, tau0=self._tau0, decay_steps=self._tau_decay_steps)

    def ingest(
        self,
        sequence: List[str],
        positive: bool,
        problematic_indices: Optional[List[int]] = None,
    ) -> None:
        """
        Feed a validated (positive) or rejected (negative) sequence back into
        the n-gram model.

        - Positive sequences are added to the accepted set, ingested into the
          positive model, and appended to the action sequence set (so the
          length distribution can update).
        - Negative sequences are ingested into the negative model only when
          ``ingest_negatives`` is True (set at construction).
          When *problematic_indices* is provided and non-empty, only the
          n-gram windows anchored at those indices are updated (targeted
          negative feedback). If *problematic_indices* is None or empty the
          whole sequence is ingested as before.

        The generation counter ``t`` is always incremented.
        """
        if positive:
            key = tuple(sequence)
            self.accepted_set.add(key)
            self.model.ingest_sequence(sequence, weight=self._positive_weight, positive=True)
            # Keep the action sequence set in sync for length distribution
            self.action_sequence_set.append(list(sequence))
            self._length_distribution = None  # invalidate cached distribution
        else:
            # Ingest negatives only if problematic_indices is provided
            if self._ingest_negatives and problematic_indices:
                self.rejected_set.add(tuple(sequence))
                # Targeted update: only penalise the bad-action windows
                self.model.ingest_negative_windows_at_indices(
                    sequence,
                    problematic_indices,
                    weight=self._negative_weight,
                )

        self.t += 1

    def is_duplicate(self, sequence: List[str]) -> bool:
        """Check whether *sequence* has already been accepted."""
        return tuple(sequence) in self.accepted_set

    # ── Convenience (backward-compat with Orchestrator) ───────────────────

    def update_action_sequence_set(self, action_sequence: List[str]) -> None:
        """
        Compatibility shim: the Orchestrator calls this after a task is fully
        generated.  For the n-gram sampler this is a no-op because ingestion
        already happens via ``ingest()``.
        """
        pass

    # ── CheckpointableModel ──────────────────────────────────────────────

    def get_rng_state(self) -> dict:
        # KDE and skew-normal lengths use NumPy's global RNG, not self._rng.
        python_state = self._rng.getstate()
        numpy_state = np.random.get_state()
        return {
            "version": 1,
            "python": [python_state[0], list(python_state[1]), python_state[2]],
            "numpy": {
                "bit_generator": numpy_state[0],
                "keys": numpy_state[1].tolist(),
                "position": numpy_state[2],
                "has_gauss": numpy_state[3],
                "cached_gaussian": numpy_state[4],
            },
        }

    def restore_rng_state(self, rng_state: dict) -> None:
        if not isinstance(rng_state, dict) or type(rng_state.get("version")) is not int or rng_state["version"] != 1:
            raise ValueError("Unsupported sampler checkpoint RNG state")
        try:
            python_state = rng_state["python"]
            if not isinstance(python_state, (list, tuple)) or len(python_state) != 3:
                raise ValueError("Python RNG state must contain version, state, and Gaussian cache")
            restored_python = random.Random()
            restored_python.setstate((python_state[0], tuple(python_state[1]), python_state[2]))
            numpy_state = rng_state["numpy"]
            if (
                type(numpy_state["position"]) is not int
                or not 0 <= numpy_state["position"] <= 624
                or type(numpy_state["has_gauss"]) is not int
                or numpy_state["has_gauss"] not in (0, 1)
            ):
                raise ValueError("Invalid NumPy RNG position or Gaussian cache flag")
            restored_numpy = np.random.RandomState()
            restored_numpy.set_state(
                (
                    numpy_state["bit_generator"],
                    numpy_state["keys"],
                    numpy_state["position"],
                    numpy_state["has_gauss"],
                    numpy_state["cached_gaussian"],
                )
            )
        except (KeyError, IndexError, OverflowError, TypeError, ValueError) as error:
            raise ValueError("Malformed sampler checkpoint RNG state") from error
        self._rng.setstate(restored_python.getstate())
        np.random.set_state(restored_numpy.get_state())
        self.checkpoint_rng_restored = True

    def _serialize_state(self) -> dict:
        """Return full sampler state (including model) as a JSON-serializable dict."""
        return {
            "model": self.model._serialize_state(),
            "sampler": {
                "t": self.t,
                "accepted_set": [list(seq) for seq in self.accepted_set],
                "rejected_set": [list(seq) for seq in self.rejected_set],
                "action_sequence_set": self.action_sequence_set,
                "pending_seeds": self._pending_seeds,
                "rng_state": self.get_rng_state(),
            },
            "config": {
                "domain": self.domain,
                "backend_spec": self.backend_spec,
                "seed_provenance": self.seed_provenance,
                "reference_lengths": self._reference_lengths,
                "tau0": self._tau0,
                "tau_decay_steps": self._tau_decay_steps,
                "positive_weight": self._positive_weight,
                "negative_weight": self._negative_weight,
                "ingest_negatives": self._ingest_negatives,
                "length_config": self._length_config.to_dict() if self._length_config else None,
                "length_policy_explicit": self.length_policy_explicit,
                "available_actions": self.available_actions,
            },
        }

    @classmethod
    def _restore_from_state(cls, instance, state: dict) -> None:
        """Restore sampler instance from a loaded state dict."""

        def _deserialize_counts(d):
            out = defaultdict(float)
            for key, val in d.items():
                parsed = json.loads(key)
                ctx = tuple(parsed[0])
                tok = parsed[1]
                out[(ctx, tok)] = val
            return out

        def _deserialize_ctx(d):
            out = defaultdict(float)
            for key, val in d.items():
                ctx = tuple(json.loads(key))
                out[ctx] = val
            return out

        model_data = state["model"]
        cfg = state["config"]
        sampler_data = state["sampler"]
        model_cfg = model_data["config"]

        # Restore config
        instance.backend_spec = require_backend_spec(cfg)
        instance.domain = instance.backend_spec["domain"]
        if cfg.get("domain", instance.domain) != instance.domain:
            raise IncompatibleBackend("Checkpoint label conflicts with its prepared input configuration")
        instance.seed_provenance = cfg.get("seed_provenance", [])
        instance._tau0 = cfg["tau0"]
        instance._tau_decay_steps = cfg["tau_decay_steps"]
        instance._positive_weight = cfg["positive_weight"]
        instance._negative_weight = cfg["negative_weight"]
        instance._ingest_negatives = cfg["ingest_negatives"]
        instance._rng = random.Random()
        instance.checkpoint_rng_restored = False
        raw_length_config = cfg.get("length_config")
        instance.length_policy_explicit = cfg.get(
            "length_policy_explicit", bool(raw_length_config and raw_length_config.get("mode") == "empirical")
        )
        instance._length_config = LengthDistributionConfig.from_dict(raw_length_config) if raw_length_config else None
        instance._reference_lengths = cfg.get("reference_lengths")
        instance._length_distribution = None

        # Restore model
        instance.model = BayesianSignedNGram(
            n=model_cfg["n"],
            vocab=set(model_cfg["vocab"]),
            alpha0=model_cfg["alpha0"],
            lambda_neg=model_cfg["lambda_neg"],
            eps=model_cfg["eps"],
            bos_token=model_cfg["bos_token"],
            eos_token=model_cfg["eos_token"],
        )
        counts = model_data["counts"]
        instance.model.pos_ngram = _deserialize_counts(counts["pos_ngram"])
        instance.model.pos_ctx = _deserialize_ctx(counts["pos_ctx"])
        instance.model.neg_ngram = _deserialize_counts(counts["neg_ngram"])
        instance.model.neg_ctx = _deserialize_ctx(counts["neg_ctx"])

        # Restore sampler state
        instance.t = sampler_data["t"]
        instance.accepted_set = {tuple(seq) for seq in sampler_data["accepted_set"]}
        instance.rejected_set = {tuple(seq) for seq in sampler_data.get("rejected_set", [])}
        instance.action_sequence_set = sampler_data["action_sequence_set"]
        instance.available_actions = cfg.get("available_actions", model_cfg["vocab"])
        instance._pending_seeds = sampler_data.get("pending_seeds", [])

        if "rng_state" in sampler_data:
            instance.restore_rng_state(sampler_data["rng_state"])

    def checkpoint_filename(self) -> str:
        """Generate a descriptive checkpoint filename."""
        n = self.model.n
        return f"ngram_{self.domain}_n{n}_t{self.t}.json"
