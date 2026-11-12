import json
import math
import random
from collections import defaultdict
from typing import Callable, Set

from src.common.sampler.length_distribution import LengthDistribution


class BayesianSignedNGram:
    """
    Bayesian n-gram model with:
      - BOS used only for left padding (never generated)
      - EOS forced only at the final step (never generated in the middle)
      - Two Dirichlet-multinomial models:
          P_pos(token | context): updated by "positive" sequences
          P_neg(token | context): updated by "negative" sequences
        Combined generation score:
        # TODO: why not just P_pos(token | context) / P_neg(token | context)?
          score(token | context) = P_pos(token | context) * (P_neg(token | context) + eps)^(-lambda_neg)

    Sampling:
      - Approximate forward sampling of a fixed-length sequence of K content tokens (no BOS/EOS in output),
        with temperature and uniform fallback if a context has no positive-scored next tokens.
    """

    def __init__(
        self,
        n: int,
        vocab: Set[str],
        alpha0: float = 0.1,
        lambda_neg: float = 1.0,
        eps: float = 1e-12,
        bos_token: str = "|BOS|",
        eos_token: str = "|EOS|",
    ):
        assert n >= 2, "n must be >= 2."
        assert alpha0 > 0, "alpha0 must be > 0."
        assert lambda_neg >= 0, "lambda_neg must be >= 0."
        assert eps > 0, "eps must be > 0."

        self.n = n
        self.bos_token = bos_token
        self.eos_token = eos_token

        # Generation vocabulary excludes special tokens.
        self.vocab = vocab - {self.bos_token, self.eos_token}
        assert self.vocab, "vocab (excluding BOS/EOS) must be non-empty."

        self.allowed_tokens = sorted(self.vocab)  # stable ordering helps reproducibility
        self.V = len(self.allowed_tokens)

        # alpha0 = 0.1 is saying: "I've seen 0.1 pseudocounts of each token everywhere." It prevents zeros without overriding the data.
        self.alpha0 = alpha0
        # Lambda_neg = 0 -> score = P_pos, negatives completely ignored
        # Lambda_neg = 1 (default) -> score ≈ P_pos / P_neg, balanced contrastive scoring
        # Lambda_neg > 1 -> Aggressive avoidance of negative patterns
        self.lambda_neg = lambda_neg
        # eps is a small positive number to avoid division by zero
        self.eps = eps

        # Counts for Dirichlet-multinomial updates
        self.pos_ngram = defaultdict(float)  # key: (context_tuple, token)
        self.pos_ctx = defaultdict(float)  # key: context_tuple -> total

        self.neg_ngram = defaultdict(float)
        self.neg_ctx = defaultdict(float)

    # -------------------------
    # Construction from data
    # -------------------------
    @classmethod
    def from_sequences(
        cls,
        sequences: list[list[str]],
        n: int,
        *,
        alpha0: float = 0.1,
        lambda_neg: float = 1.0,
        eps: float = 1e-12,
        bos_token: str = "|BOS|",
        eos_token: str = "|EOS|",
        negative_sequences: list[list[str]] | None = None,
        positive_weight: float = 1.0,
        negative_weight: float = 1.0,
    ):
        """
        Build a model and infer vocab from sequences.
        BOS/EOS (if present in raw sequences) are stripped before vocab inference and ingestion.

        All `sequences` are ingested as positive examples.
        If `negative_sequences` is provided, they are ingested as negative examples.
        """

        def clean(seq):
            return [t for t in seq if t not in (bos_token, eos_token)]

        vocab = set()
        for seq in sequences:
            vocab.update(clean(seq))
        if negative_sequences is not None:
            for seq in negative_sequences:
                vocab.update(clean(seq))

        if not vocab:
            raise ValueError("Inferred vocabulary is empty.")

        model = cls(
            n=n,
            vocab=vocab,
            alpha0=alpha0,
            lambda_neg=lambda_neg,
            eps=eps,
            bos_token=bos_token,
            eos_token=eos_token,
        )

        for seq in sequences:
            model.ingest_sequence(clean(seq), weight=positive_weight, positive=True)

        if negative_sequences is not None:
            for seq in negative_sequences:
                model.ingest_sequence(clean(seq), weight=negative_weight, positive=False)

        return model

    # -------------------------
    # Ingestion
    # -------------------------
    def ingest_sequence(self, sequence: list[str], weight: float = 1.0, positive: bool = True):
        """
        Update counts from a sequence (content tokens only).

        positive=True  -> update positive model
        positive=False -> update negative model

        BOS/EOS in input are ignored (we add BOS padding and terminal EOS internally).
        """
        if weight <= 0:
            return

        padded = self._pad_for_training(sequence)
        for i in range(len(padded) - self.n + 1):
            window = padded[i : i + self.n]
            ctx = tuple(window[:-1])
            nxt = window[-1]

            # nxt can be EOS at the end; BOS should never be predicted
            if nxt == self.bos_token:
                continue
            if nxt != self.eos_token and nxt not in self.vocab:
                continue

            if positive:
                self.pos_ngram[(ctx, nxt)] += weight
                self.pos_ctx[ctx] += weight
            else:
                self.neg_ngram[(ctx, nxt)] += weight
                self.neg_ctx[ctx] += weight

    def ingest_negative_windows_at_indices(
        self,
        sequence: list[str],
        indices: list[int],
        weight: float = 1.0,
    ):
        """
        Update the *negative* model only for the n-gram windows where the
        predicted token (``nxt``) is one of the given 0-based *indices*.

        This allows targeted negative feedback: instead of penalising every
        n-gram in a rejected sequence, only the windows anchored at the
        problematic actions are updated.

        For original index ``j``, the relevant window in the padded sequence
        starts at position ``j`` (there are ``n-1`` BOS tokens prepended, so
        padded[j : j+n] has ``nxt = sequence[j]``).
        """
        if weight <= 0 or not indices:
            return

        padded = self._pad_for_training(sequence)
        for j in set(indices):
            if j < 0 or j >= len(sequence):
                continue
            # Window whose predicted token is sequence[j]
            window = padded[j : j + self.n]
            if len(window) < self.n:
                continue
            ctx = tuple(window[:-1])
            nxt = window[-1]

            if nxt == self.bos_token:
                continue
            if nxt != self.eos_token and nxt not in self.vocab:
                continue

            self.neg_ngram[(ctx, nxt)] += weight
            self.neg_ctx[ctx] += weight

    def _pad_for_training(self, sequence):
        clean = [t for t in sequence if t not in (self.bos_token, self.eos_token)]
        return [self.bos_token] * (self.n - 1) + clean + [self.eos_token]

    # -------------------------
    # Dirichlet posterior predictive
    # -------------------------
    def _dirichlet_predictive(self, ngram_counts, ctx_counts, ctx, token, token_space_size):
        # (alpha0 + count(ctx,token)) / (V*alpha0 + count(ctx,*))
        c = ngram_counts.get((ctx, token), 0.0)
        tot = ctx_counts.get(ctx, 0.0)
        return (self.alpha0 + c) / (token_space_size * self.alpha0 + tot)

    def p_pos(self, ctx, token):
        # intermediate-step predictive prob for content tokens only
        if token in (self.bos_token, self.eos_token):
            return 0.0
        if token not in self.vocab:
            return 0.0
        return self._dirichlet_predictive(self.pos_ngram, self.pos_ctx, ctx, token, self.V)

    def p_neg(self, ctx, token):
        if token in (self.bos_token, self.eos_token):
            return 0.0
        if token not in self.vocab:
            return 0.0
        return self._dirichlet_predictive(self.neg_ngram, self.neg_ctx, ctx, token, self.V)

    def score(self, ctx, token):
        """
        Unnormalized combined score for intermediate steps (content tokens only).
        EOS/BOS are never allowed here.
        """
        pplus = self.p_pos(ctx, token)  # "how much do the good examples like this token here?"
        if pplus <= 0.0:
            return 0.0
        if self.lambda_neg == 0.0:
            return pplus
        pminus = self.p_neg(ctx, token)  # a penalty for negative examples
        return pplus * (pminus + self.eps) ** (-self.lambda_neg)

    # -------------------------
    # Save / Load
    # -------------------------
    def _serialize_state(self) -> dict:
        """Return model state as a JSON-serializable dict."""

        def serialize_counts(d):
            out = {}
            for (ctx, tok), val in d.items():
                key = json.dumps([list(ctx), tok])
                out[key] = val
            return out

        def serialize_ctx(d):
            out = {}
            for ctx, val in d.items():
                key = json.dumps(list(ctx))
                out[key] = val
            return out

        return {
            "config": {
                "n": self.n,
                "vocab": sorted(self.vocab),
                "alpha0": self.alpha0,
                "lambda_neg": self.lambda_neg,
                "eps": self.eps,
                "bos_token": self.bos_token,
                "eos_token": self.eos_token,
            },
            "counts": {
                "pos_ngram": serialize_counts(self.pos_ngram),
                "pos_ctx": serialize_ctx(self.pos_ctx),
                "neg_ngram": serialize_counts(self.neg_ngram),
                "neg_ctx": serialize_ctx(self.neg_ctx),
            },
        }

    def save_state(self, path: str) -> None:
        """Serialize all model state to a JSON file."""
        with open(path, "w") as f:
            json.dump(self._serialize_state(), f, indent=2)

    @classmethod
    def load_state(cls, path: str) -> "BayesianSignedNGram":
        """Deserialize from a JSON file and reconstruct the model."""
        with open(path, "r") as f:
            state = json.load(f)

        cfg = state["config"]
        model = cls(
            n=cfg["n"],
            vocab=set(cfg["vocab"]),
            alpha0=cfg["alpha0"],
            lambda_neg=cfg["lambda_neg"],
            eps=cfg["eps"],
            bos_token=cfg["bos_token"],
            eos_token=cfg["eos_token"],
        )

        def deserialize_counts(d):
            out = defaultdict(float)
            for key, val in d.items():
                parsed = json.loads(key)
                ctx = tuple(parsed[0])
                tok = parsed[1]
                out[(ctx, tok)] = val
            return out

        def deserialize_ctx(d):
            out = defaultdict(float)
            for key, val in d.items():
                ctx = tuple(json.loads(key))
                out[ctx] = val
            return out

        counts = state["counts"]
        model.pos_ngram = deserialize_counts(counts["pos_ngram"])
        model.pos_ctx = deserialize_ctx(counts["pos_ctx"])
        model.neg_ngram = deserialize_counts(counts["neg_ngram"])
        model.neg_ctx = deserialize_ctx(counts["neg_ctx"])

        return model

    # -------------------------
    # Sampling helpers
    # -------------------------
    def _sample_from_weights(self, items, weights, rng):
        total = float(sum(weights))
        if total <= 0.0:
            raise ValueError("All weights are zero; cannot sample.")
        r = rng.random() * total
        acc = 0.0
        last = items[-1]
        for it, w in zip(items, weights):
            acc += w
            if r <= acc:
                return it
        return last

    def _sample_uniform(self, items, rng):
        if not items:
            raise ValueError("Cannot sample uniformly from empty list.")
        return items[rng.randrange(len(items))]

    # -------------------------
    # Fixed-length sampling (approximate forward)
    # -------------------------
    def generate_random_sequence(
        self,
        length: int,
        rng: random.Random | None = None,
        *,
        temperature: float = 1.0,
    ):
        """
        Sample `length` content tokens and return them (no BOS/EOS).

        Approximate forward sampling using only local scores score(ctx, token),
        with temperature and uniform fallback on dead ends.
        """
        if rng is None:
            rng = random.Random()
        if length < 0:
            raise ValueError("length must be >= 0.")
        if temperature <= 0:
            raise ValueError("temperature must be > 0.")

        start_ctx = tuple([self.bos_token] * (self.n - 1))

        def shift(ctx, nxt):
            return ctx[1:] + (nxt,)

        out = []
        ctx = start_ctx
        inv_tau = 1.0 / float(temperature)

        for _ in range(length):
            items = []
            weights = []
            for w in self.allowed_tokens:
                sc = self.score(ctx, w)
                if sc > 0.0:
                    items.append(w)
                    weights.append(sc**inv_tau)

            if items:
                nxt = self._sample_from_weights(items, weights, rng)
            else:
                # Uniform fallback (Option C)
                nxt = self._sample_uniform(self.allowed_tokens, rng)

            out.append(nxt)
            ctx = shift(ctx, nxt)

        return out

    @staticmethod
    def temp_schedule_exp(t: int, tau0: float, decay_steps: float) -> float:
        """
        tau(t) = 1 + (tau0 - 1) * exp(-t / decay_steps)
        """
        return 1.0 + (float(tau0) - 1.0) * math.exp(-float(t) / float(decay_steps))


# TODO: Make this function work (Maybe as a BayesianSignedNGram method)
def build_ngram_model_with_validator(
    *,
    n: int,
    vocab: set[str],
    validator: Callable[[list[str]], bool],
    N_valid: int,
    length_distribution: LengthDistribution,
    rng: random.Random | None = None,
    alpha0: float = 0.1,
    lambda_neg: float = 1.0,
    eps: float = 1e-12,
    bos_token: str = "|BOS|",
    eos_token: str = "|EOS|",
    tau0: float = 2.0,
    tau_decay_steps: float = 500.0,
    positive_weight: float = 1.0,
    negative_weight: float = 1.0,
    ingest_negatives: bool = True,
) -> tuple[BayesianSignedNGram, set[tuple[str, ...]]]:
    """
    Iteratively:
      - sample a length L ~ length_distribution
      - generate a sequence of length L
      - validate it (True=positive, False=negative)
      - ingest as positive/negative
      - keep UNIQUE valid sequences (exact duplicate filtering)
    Stop when we have N_valid unique valid sequences.

    Returns:
      (trained_model, accepted_set) where accepted_set is a set of tuples.
    """
    if rng is None:
        rng = random.Random()

    assert N_valid >= 0, "N_valid must be >= 0."
    assert tau0 >= 1.0, "tau0 must be >= 1.0 (anneals down to 1.0)."
    assert positive_weight > 0, "positive_weight must be > 0."
    assert negative_weight > 0, "negative_weight must be > 0."

    model = BayesianSignedNGram(
        n=n,
        vocab=vocab,
        alpha0=alpha0,
        lambda_neg=lambda_neg,
        eps=eps,
        bos_token=bos_token,
        eos_token=eos_token,
    )

    accepted_set: set[tuple[str, ...]] = set()
    t = 0  # generation counter for temperature schedule

    while len(accepted_set) < N_valid:
        L = length_distribution.sample_length(config=None)
        tau = BayesianSignedNGram.temp_schedule_exp(t, tau0=tau0, decay_steps=tau_decay_steps)

        seq = model.generate_random_sequence(
            length=L,
            rng=rng,
            temperature=tau,
        )

        is_valid = bool(validator(seq))

        if is_valid:
            key = tuple(seq)
            if key in accepted_set:
                # Exact duplicate filtering: ignore completely (no ingest).
                t += 1
                continue

            accepted_set.add(key)
            model.ingest_sequence(seq, weight=positive_weight, positive=True)
        else:
            if ingest_negatives:
                model.ingest_sequence(seq, weight=negative_weight, positive=False)

        t += 1

    return model, accepted_set
