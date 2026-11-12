import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import gaussian_kde, skewnorm


@dataclass
class LengthDistributionConfig:
    """
    Configures how action sequence lengths are sampled.

    Modes:
        - "from_seed": Fit a KDE from a reference task set.
          ``reference_source`` can be "original" (prepared seeds),
          a path to a tasks.json, or None (use the seed tasks).
        - "empirical": Draw with replacement from explicit positive integer
          ``empirical_lengths``; repeated entries retain their frequency.
        - "skew_normal": Sample from a skew-normal distribution with
          parameters (skew_alpha, gaussian_mean, gaussian_std) clipped to
          [min_length, max_length].  When skew_alpha=0 this is a standard
          Gaussian.
    """

    mode: str  # "from_seed" | "skew_normal" | "empirical"
    reference_source: Optional[str] = None  # path, "original", or None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    gaussian_mean: Optional[float] = None
    gaussian_std: Optional[float] = None
    skew_alpha: float = 0.0
    empirical_lengths: Optional[List[int]] = None

    def __post_init__(self):
        valid_modes = ("from_seed", "skew_normal", "empirical")
        if self.mode not in valid_modes:
            raise ValueError(f"mode must be one of {valid_modes}, got '{self.mode}'")
        if self.mode == "empirical":
            if not isinstance(self.empirical_lengths, list) or not self.empirical_lengths:
                raise ValueError("empirical mode requires a non-empty empirical_lengths list")
            if any(type(length) is not int or length < 1 for length in self.empirical_lengths):
                raise ValueError(
                    "empirical_lengths must contain positive integers; explicitly exclude "
                    "and record zero-call references before mutation-only sampling"
                )
            if (
                self.min_length is not None
                and min(self.empirical_lengths) < self.min_length
                or self.max_length is not None
                and max(self.empirical_lengths) > self.max_length
            ):
                raise ValueError("empirical bounds must include all supplied lengths; draws are not clipped")
            if self.gaussian_mean is not None or self.gaussian_std is not None or self.skew_alpha != 0:
                raise ValueError("empirical mode does not accept Gaussian or skew parameters")
            self.empirical_lengths = self.empirical_lengths.copy()
        elif self.empirical_lengths is not None:
            raise ValueError("empirical_lengths requires mode='empirical'")
        if self.mode == "skew_normal":
            if self.gaussian_mean is None or self.gaussian_std is None:
                raise ValueError("skew_normal mode requires both gaussian_mean and gaussian_std")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LengthDistributionConfig":
        known_fields = {
            "mode",
            "reference_source",
            "min_length",
            "max_length",
            "gaussian_mean",
            "gaussian_std",
            "skew_alpha",
            "empirical_lengths",
        }
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, path: str) -> "LengthDistributionConfig":
        with open(path) as stream:
            return cls.from_dict(json.load(stream))


def load_reference_tasks(config: LengthDistributionConfig, domain_config):
    if config.mode != "from_seed" or config.reference_source is None:
        return None
    if config.reference_source == "original":
        return domain_config.seeds()
    path = Path(config.reference_source).resolve()
    if not path.is_file():
        raise ValueError(
            f"Length reference source must be 'original' or an existing normalized seed JSON/JSONL file: {path}"
        )
    with path.open() as stream:
        records = (
            [json.loads(line) for line in stream if line.strip()] if path.suffix == ".jsonl" else json.load(stream)
        )
    if not isinstance(records, list):
        raise ValueError("Length reference file must contain an array of normalized seed records or JSONL rows")
    config.reference_source = str(path)
    return domain_config.seeds(records, split="reference")


class LengthDistribution:
    def __init__(
        self,
        median: float,
        std: float,
        min_length: int,
        max_length: int,
        p10: float,
        p90: float,
        lengths: List[int],
        _kde: Optional[gaussian_kde] = None,
    ):
        self.median = median
        self.std = std
        self.min_length = min_length
        self.max_length = max_length
        self.p10 = p10
        self.p90 = p90
        self.lengths = lengths
        self._kde = _kde

    @classmethod
    def from_lengths(
        cls,
        lengths: List[int],
    ) -> "LengthDistribution":
        """
        Construct a LengthDistribution instance from a list of sequence lengths.
        """
        assert lengths and isinstance(lengths, list), "lengths must be a non-empty list"

        median = float(np.median(lengths))
        std = float(np.std(lengths))
        min_length = max(1, min(lengths))
        max_length = max(lengths)
        p10 = float(np.percentile(lengths, 10))
        p90 = float(np.percentile(lengths, 90))
        p10 = max(p10, min_length)
        p90 = min(p90, max_length)

        return cls(
            median=median,
            std=std,
            min_length=min_length,
            max_length=max_length,
            p10=p10,
            p90=p90,
            lengths=lengths.copy(),
        )

    @classmethod
    def fit(
        cls,
        lengths: List[int],
        fallback_lengths: Optional[List[int]] = None,
    ) -> "LengthDistribution":
        """
        Fit a LengthDistribution from lengths. Uses primary lengths if non-empty,
        otherwise fallback_lengths. Raises if both are empty (matches original
        _fit_length_distribution behavior).
        """
        to_use = lengths or fallback_lengths
        if not to_use:
            raise ValueError(
                "Cannot fit LengthDistribution: no lengths provided "
                "(action_sequence_set and reference_lengths are both empty)"
            )
        return cls.from_lengths(to_use)

    def _get_kde(self):
        if self._kde is None:
            self._kde = gaussian_kde(self.lengths)
        return self._kde

    def sample_length(
        self,
        config: Optional["LengthDistributionConfig"] = None,
        *,
        rng: Optional[random.Random] = None,
    ) -> int:
        """
        Sample an action sequence length based on the configured mode.

        Modes:
            - "skew_normal": Sample from a skew-normal(skew_alpha, gaussian_mean,
              gaussian_std) clipped to [min_length, max_length].  When
              skew_alpha=0 this is a standard Gaussian.
            - "from_seed" or None: KDE-based sample from fitted lengths.
            - "empirical": Exact row-frequency draws using the supplied RNG
              (or NumPy global RNG); independent of fitted/adaptively accepted lengths.
        """
        if config is not None and config.mode == "empirical":
            assert config.empirical_lengths is not None
            if rng is not None:
                return rng.choice(config.empirical_lengths)
            return int(np.random.choice(config.empirical_lengths))
        if config is not None and config.mode == "skew_normal":
            lo = config.min_length if config.min_length is not None else self.min_length
            hi = config.max_length if config.max_length is not None else self.max_length
            sample = skewnorm.rvs(
                config.skew_alpha,
                loc=config.gaussian_mean,
                scale=config.gaussian_std,
            )
            sample = max(lo, min(hi, sample))
            return int(round(sample))

        # KDE-based (from_seed or default)
        kde = self._get_kde()
        sample = kde.resample(1)[0, 0]
        sample = max(self.min_length, min(self.max_length, sample))
        return int(round(sample))


def build_length_distribution(
    sampler: Any,
    *,
    gaussian_mean: float,
    gaussian_std: float,
    skew_alpha: float,
    min_length: int,
    max_length: int,
) -> Tuple[LengthDistribution, LengthDistributionConfig]:
    """Build a (LengthDistribution, LengthDistributionConfig) pair for skew-normal sampling.

    Returns the sampler's pre-fitted ``LengthDistribution`` together with a
    skew-normal ``LengthDistributionConfig`` that drives sample-length
    selection at the given parameters. ``sampler`` is typed loosely to avoid a
    circular import with ``ActionSequenceNGramModelSampler``; any object
    exposing a ``length_distribution`` attribute works.
    """
    config = LengthDistributionConfig(
        mode="skew_normal",
        gaussian_mean=gaussian_mean,
        gaussian_std=gaussian_std,
        skew_alpha=skew_alpha,
        min_length=min_length,
        max_length=max_length,
    )
    return sampler.length_distribution, config
