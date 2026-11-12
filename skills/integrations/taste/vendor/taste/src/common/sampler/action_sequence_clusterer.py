"""
Action sequence clustering via k-medoids on edit distance.

Generates a pool of unique sequences from an n-gram model, computes pairwise
edit distances, and clusters via k-medoids with k-medoids++ initialization.
Each cluster is represented by its medoid, with members ordered by proximity.
"""

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

from src.common.sampler.adaptive_ngram_model import BayesianSignedNGram
from src.common.sampler.length_distribution import LengthDistribution, LengthDistributionConfig
from src.common.sampler.sequence_metrics import weighted_edit_distance


@dataclass
class SubstitutionCosts:
    """Cost hierarchy for weighted edit distance substitution operations.

    Groups are derived from the first underscore-delimited token of the action
    name (e.g. "search", "get", "book") unless overridden by *group_map*.
    GENERIC actions are treated as READ for type comparison, but keep their own
    prefix group so they only match other GENERIC actions with the same prefix
    at the group level.
    """

    cost_same_group: float = 0.33  # same type + same prefix group
    cost_same_type: float = 0.66  # same type, different group
    cost_diff_type: float = 1.0  # different type (READ/GENERIC vs WRITE)
    group_map: Optional[Dict[str, str]] = None

    def build_cost_fn(self, action_types: Dict[str, str]):
        """Return a callable ``(action_a, action_b) -> float`` suitable for
        :func:`weighted_edit_distance`."""

        gmap = self.group_map

        def _effective_type(action: str) -> str:
            t = action_types.get(action, "GENERIC")
            return "READ" if t == "GENERIC" else t

        def _group(action: str) -> str:
            if gmap and action in gmap:
                return gmap[action]
            return action.split("_")[0]

        def cost_fn(a: str, b: str) -> float:
            if a == b:
                return 0.0
            ta, tb = _effective_type(a), _effective_type(b)
            if ta != tb:
                return self.cost_diff_type
            if _group(a) == _group(b):
                return self.cost_same_group
            return self.cost_same_type

        return cost_fn


@dataclass
class ClusterInfo:
    """Information about a single cluster produced by k-medoids."""

    cluster_idx: int
    medoid_idx: int  # index into clusterer.sequences (or new_pool when returned by recluster_with_frozen)
    medoid_sequence: List[str]
    member_indices: List[int]  # ordered by proximity to medoid
    member_sequences: List[List[str]]  # same order
    intra_cluster_distances: List[float]  # composite distance from medoid to each member


class ActionSequenceClusterer:
    """
    Generate a pool of unique action sequences and cluster via k-medoids.

    Uses the underlying BayesianSignedNGram model for generation and edit
    distance for the dissimilarity metric.
    """

    def __init__(
        self,
        model: BayesianSignedNGram,
        length_distribution: LengthDistribution,
        length_config: Optional[LengthDistributionConfig],
        n_clusters: int,
        pool_size: int = 1000,
        temperature: float = 1.5,
        max_kmedoids_iters: int = 100,
        rng: Optional[random.Random] = None,
        action_types: Optional[Dict[str, str]] = None,
        substitution_costs: Optional[SubstitutionCosts] = None,
        write_medoid_bias: float = 0.0,
        structural_filter=None,
        evidence=None,
    ):
        self.model = model
        self.length_distribution = length_distribution
        self.length_config = length_config
        self.n_clusters = n_clusters
        self.pool_size = pool_size
        self.temperature = temperature
        self.max_kmedoids_iters = max_kmedoids_iters
        self.rng = rng if rng is not None else random.Random()
        self.action_types = action_types or {}
        self.substitution_costs = substitution_costs or SubstitutionCosts()
        self.write_medoid_bias = write_medoid_bias
        self.structural_filter: Optional[Callable[[List[str]], bool]] = structural_filter

        # Populated after generate_and_cluster()
        self.sequences: List[List[str]] = []
        self.clusters: List[ClusterInfo] = []
        self.dist_matrix: Optional[np.ndarray] = None
        self._total_attempts: int = 0
        self._filtered_out: int = 0
        self.evidence = evidence
        self._pool_index = 0

    def _write_fraction(self, seq: List[str]) -> float:
        """Fraction of actions in *seq* that are WRITE type."""
        if not seq:
            return 0.0
        return sum(1 for a in seq if self.action_types.get(a) == "WRITE") / len(seq)

    # ------------------------------------------------------------------
    # Pool generation
    # ------------------------------------------------------------------

    def generate_pool(self) -> List[List[str]]:
        """Generate pool_size *unique* sequences from the n-gram model.

        Keeps sampling until pool_size distinct sequences are collected.
        Gives up after 10x pool_size total attempts to avoid infinite loops.
        When a ``structural_filter`` is set, sequences that fail the filter
        are discarded (counted as attempts but not added to the pool).
        """
        seen: set = set()
        unique: List[List[str]] = []
        max_attempts = self.pool_size * 10
        attempts = 0
        filtered_out = 0
        pool_index = self._pool_index
        self._pool_index += 1
        if self.evidence is not None:
            self.evidence.record(
                "pool_started",
                pool_index=pool_index,
                requested=self.pool_size,
                temperature=self.temperature,
                max_attempts=max_attempts,
            )
        while len(unique) < self.pool_size and attempts < max_attempts:
            length = self.length_distribution.sample_length(self.length_config, rng=self.rng)
            seq = self.model.generate_random_sequence(
                length=length,
                rng=self.rng,
                temperature=self.temperature,
            )
            attempts += 1
            if self.structural_filter is not None and not self.structural_filter(seq):
                filtered_out += 1
                if self.evidence is not None:
                    self.evidence.record(
                        "pool_draw",
                        pool_index=pool_index,
                        attempt=attempts,
                        requested_length=length,
                        sequence=seq,
                        outcome="filtered",
                    )
                continue
            key = tuple(seq)
            if self.evidence is not None:
                self.evidence.record(
                    "pool_draw",
                    pool_index=pool_index,
                    attempt=attempts,
                    requested_length=length,
                    sequence=seq,
                    outcome="duplicate" if key in seen else "accepted",
                )
            if key not in seen:
                seen.add(key)
                unique.append(seq)
        self._total_attempts = attempts
        self._filtered_out = filtered_out
        if self.evidence is not None:
            self.evidence.record(
                "pool_completed", pool_index=pool_index, sequences=unique, attempts=attempts, filtered=filtered_out
            )
        if filtered_out > 0:
            print(f"  Structural filter: {filtered_out}/{attempts} sequences rejected ({filtered_out / attempts:.0%})")
        if len(unique) < self.pool_size:
            print(
                f"Warning: only generated {len(unique)} unique sequences "
                f"after {attempts} attempts (requested {self.pool_size}). "
                f"Consider raising temperature or relaxing structural filter."
            )
        return unique

    # ------------------------------------------------------------------
    # Distance matrix
    # ------------------------------------------------------------------

    def compute_distance_matrix(self, sequences: List[List[str]]) -> np.ndarray:
        """Compute NxN pairwise weighted-edit distance matrix (symmetric, zero diagonal)."""
        n = len(sequences)
        dist = np.zeros((n, n), dtype=np.float64)

        cost_fn = self.substitution_costs.build_cost_fn(self.action_types)

        for i in range(n):
            for j in range(i + 1, n):
                d = weighted_edit_distance(sequences[i], sequences[j], cost_fn)
                dist[i, j] = d
                dist[j, i] = d

        return dist

    # ------------------------------------------------------------------
    # K-medoids++ initialization
    # ------------------------------------------------------------------

    def _kmedoids_pp_init(self, dist_matrix: np.ndarray, k: int) -> List[int]:
        """
        K-medoids++ initialization: first medoid uniform random, subsequent
        medoids chosen with probability proportional to squared distance
        to nearest existing medoid.
        """
        n = dist_matrix.shape[0]
        medoids: List[int] = [self.rng.randrange(n)]

        for _ in range(k - 1):
            # Distance from each point to its nearest medoid
            dists = np.min(dist_matrix[:, medoids], axis=1).astype(np.float64)
            probs = dists**2
            total = probs.sum()
            if total == 0:
                # All remaining points are at distance 0 from some medoid;
                # pick uniformly from non-medoid points
                candidates = [i for i in range(n) if i not in medoids]
                medoids.append(self.rng.choice(candidates))
            else:
                probs /= total
                r = self.rng.random()
                cumsum = 0.0
                chosen = n - 1
                for i in range(n):
                    cumsum += probs[i]
                    if r <= cumsum:
                        chosen = i
                        break
                medoids.append(chosen)

        return medoids

    def _kmedoids_pp_init_constrained(
        self,
        dist_matrix: np.ndarray,
        k_new: int,
        frozen_indices: List[int],
        pool_indices: List[int],
    ) -> List[int]:
        """K-medoids++ init for k_new free medoids drawn only from pool_indices.

        Seeds the probability distribution using distance to *all* centers already
        chosen (frozen + previously selected free medoids).
        """
        chosen: List[int] = list(frozen_indices)

        for _ in range(k_new):
            # Distance from each pool candidate to its nearest already-chosen center
            dists = np.min(dist_matrix[np.ix_(pool_indices, chosen)], axis=1).astype(np.float64)
            probs = dists**2
            total = probs.sum()
            if total == 0:
                remaining = [p for p in pool_indices if p not in chosen]
                if not remaining:
                    break
                chosen.append(self.rng.choice(remaining))
            else:
                chosen_set = set(chosen)
                for i, pool_pt in enumerate(pool_indices):
                    if pool_pt in chosen_set:
                        probs[i] = 0.0
                total = probs.sum()
                if total == 0:
                    remaining = [p for p in pool_indices if p not in chosen_set]
                    if not remaining:
                        break
                    chosen.append(self.rng.choice(remaining))
                    continue
                probs /= total
                r = self.rng.random()
                cumsum = 0.0
                selected = next(p for p in reversed(pool_indices) if p not in chosen_set)
                for i, pool_pt in enumerate(pool_indices):
                    if probs[i] == 0.0:
                        continue
                    cumsum += probs[i]
                    if r <= cumsum:
                        selected = pool_pt
                        break
                chosen.append(selected)

        return chosen[len(frozen_indices) :]  # only the newly added free medoids

    # ------------------------------------------------------------------
    # K-medoids (PAM-style assign-update)
    # ------------------------------------------------------------------

    def _run_kmedoids(
        self,
        dist_matrix: np.ndarray,
        k: int,
        sequences: Optional[List[List[str]]] = None,
    ) -> tuple:
        """
        Run k-medoids clustering.

        Returns:
            (medoid_indices, labels) where labels[i] is the cluster index
            (0..k-1) for point i.
        """
        medoids = self._kmedoids_pp_init(dist_matrix, k)

        for _ in range(self.max_kmedoids_iters):
            # Assign: each point to its nearest medoid
            dists_to_medoids = dist_matrix[:, medoids]  # (n, k)
            labels = np.argmin(dists_to_medoids, axis=1)

            # Update: for each cluster, find the member minimizing total
            # intra-cluster distance
            new_medoids: List[int] = []
            for c in range(k):
                members = np.where(labels == c)[0]
                if len(members) == 0:
                    new_medoids.append(medoids[c])
                    continue
                cluster_dists = dist_matrix[np.ix_(members, members)]
                sum_dists = cluster_dists.sum(axis=1)
                if self.write_medoid_bias > 0 and sequences is not None:
                    n_mem = len(members)
                    write_fracs = np.array([self._write_fraction(sequences[int(m)]) for m in members])
                    scores = sum_dists / n_mem - self.write_medoid_bias * write_fracs
                    best_local = int(np.argmin(scores))
                else:
                    best_local = np.argmin(sum_dists)
                new_medoids.append(int(members[best_local]))

            if set(new_medoids) == set(medoids):
                medoids = new_medoids
                break
            medoids = new_medoids

        # Final assignment
        dists_to_medoids = dist_matrix[:, medoids]
        labels = np.argmin(dists_to_medoids, axis=1)

        return medoids, labels

    def _run_kmedoids_constrained(
        self,
        dist_matrix: np.ndarray,
        frozen_indices: List[int],
        free_medoids: List[int],
        pool_indices: List[int],
        sequences: Optional[List[List[str]]] = None,
    ) -> tuple:
        """Constrained k-medoids: frozen centers are fixed, only free centers update.

        All points in dist_matrix are assigned to the nearest center (frozen or free).
        Only free centers update each iteration; they must remain in pool_indices.

        Returns:
            (all_centers, labels) where
            - all_centers = frozen_indices + final_free_medoids (List[int])
            - labels[i] is index into all_centers (0..n_frozen+n_free-1) for point i
        """
        free_medoids = list(free_medoids)
        pool_set = set(pool_indices)
        n_frozen = len(frozen_indices)

        for _ in range(self.max_kmedoids_iters):
            all_centers = frozen_indices + free_medoids
            dists_to_centers = dist_matrix[:, all_centers]  # (n_combined, n_centers)
            labels = np.argmin(dists_to_centers, axis=1)

            new_free_medoids: List[int] = []
            for j in range(len(free_medoids)):
                cluster_label = n_frozen + j
                members = np.where(labels == cluster_label)[0]
                pool_members = [m for m in members if m in pool_set]
                if not pool_members:
                    new_free_medoids.append(free_medoids[j])
                    continue
                pool_arr = np.array(pool_members)
                all_members_arr = np.array(members.tolist())
                sub_dists = dist_matrix[np.ix_(pool_arr, all_members_arr)]
                sum_dists = sub_dists.sum(axis=1)
                if self.write_medoid_bias > 0 and sequences is not None:
                    n_mem = len(all_members_arr)
                    write_fracs = np.array([self._write_fraction(sequences[int(m)]) for m in pool_arr])
                    scores = sum_dists / n_mem - self.write_medoid_bias * write_fracs
                    best_local = int(np.argmin(scores))
                else:
                    best_local = int(np.argmin(sum_dists))
                new_free_medoids.append(int(pool_arr[best_local]))

            if set(new_free_medoids) == set(free_medoids):
                free_medoids = new_free_medoids
                break
            free_medoids = new_free_medoids

        # Final assignment
        all_centers = frozen_indices + free_medoids
        labels = np.argmin(dist_matrix[:, all_centers], axis=1)
        return all_centers, labels

    def recluster_with_frozen(
        self,
        frozen_sequences: List[List[str]],
        n_new_clusters: int,
        new_pool: Optional[List[List[str]]] = None,
    ) -> List["ClusterInfo"]:
        """Re-cluster: frozen_sequences are fixed centers, find n_new_clusters more.

        Args:
            frozen_sequences: Sequences to keep as immovable centers (e.g. already
                validated medoids).
            n_new_clusters: How many new cluster centers to find.
            new_pool: Candidate sequences to draw new centers from.  If None,
                ``generate_pool()`` is called automatically.

        Returns:
            List of ClusterInfo for the *new* clusters only.
            ``cluster_idx`` values start at ``len(frozen_sequences)``.
            Members are drawn only from new_pool.
        """
        if new_pool is None:
            new_pool = self.generate_pool()

        n_frozen = len(frozen_sequences)
        combined: List[List[str]] = frozen_sequences + new_pool
        n_pool = len(new_pool)

        if n_pool < n_new_clusters:
            raise ValueError(f"new_pool size {n_pool} < n_new_clusters={n_new_clusters}")

        frozen_indices = list(range(n_frozen))
        pool_indices = list(range(n_frozen, n_frozen + n_pool))
        pool_set = set(pool_indices)

        dist = self.compute_distance_matrix(combined)

        free_medoids = self._kmedoids_pp_init_constrained(dist, n_new_clusters, frozen_indices, pool_indices)
        if len(free_medoids) < n_new_clusters:
            raise ValueError(
                f"_kmedoids_pp_init_constrained returned only {len(free_medoids)} "
                f"medoids (requested {n_new_clusters}); pool may be too small or degenerate."
            )
        all_centers, labels = self._run_kmedoids_constrained(dist, frozen_indices, free_medoids, pool_indices, combined)

        new_clusters: List[ClusterInfo] = []
        for local_idx in range(n_new_clusters):
            center_pos = n_frozen + local_idx
            med_idx = all_centers[center_pos]
            all_members = np.where(labels == center_pos)[0].tolist()
            pool_members = [m for m in all_members if m in pool_set]
            member_dists = [(m, float(dist[med_idx, m])) for m in pool_members]
            member_dists.sort(key=lambda x: x[1])
            sorted_idxs = [m for m, _ in member_dists]
            sorted_dists = [d for _, d in member_dists]
            sorted_seqs = [combined[m] for m in sorted_idxs]
            med_idx_relative = med_idx - n_frozen
            sorted_idxs_relative = [m - n_frozen for m in sorted_idxs]
            new_clusters.append(
                ClusterInfo(
                    cluster_idx=n_frozen + local_idx,
                    medoid_idx=med_idx_relative,
                    medoid_sequence=combined[med_idx],
                    member_indices=sorted_idxs_relative,
                    member_sequences=sorted_seqs,
                    intra_cluster_distances=sorted_dists,
                )
            )

        return new_clusters

    def summary_stats(self) -> Dict:
        """Pool size, unique count, cluster size statistics."""
        sizes = [len(c.member_indices) for c in self.clusters]
        stats: Dict = {
            "total_attempts": self._total_attempts,
            "unique_sequences": len(self.sequences),
            "n_clusters": len(self.clusters),
            "cluster_size_min": int(np.min(sizes)) if sizes else 0,
            "cluster_size_max": int(np.max(sizes)) if sizes else 0,
            "cluster_size_mean": float(np.mean(sizes)) if sizes else 0.0,
            "cluster_size_median": float(np.median(sizes)) if sizes else 0.0,
            "has_action_types": bool(self.action_types),
            "substitution_costs": {
                "cost_same_group": self.substitution_costs.cost_same_group,
                "cost_same_type": self.substitution_costs.cost_same_type,
                "cost_diff_type": self.substitution_costs.cost_diff_type,
                "group_map": self.substitution_costs.group_map,
            },
        }
        return stats


# ---------------------------------------------------------------------------
# Standalone artifact helpers (entry-point 2 → 3 handoff)
# ---------------------------------------------------------------------------


def save_validated_clusters(
    path: str,
    domain: str,
    num_clusters: int,
    ngram_checkpoint: Optional[str],
    length_distribution_config: Dict,
    clustering_config: Dict,
    validation_config: Dict,
    clusters: List[ClusterInfo],
    validation_results: List[Dict],
    backend_spec: Dict,
    reference_lengths: Optional[List[int]] = None,
) -> str:
    """Save validated-cluster artifact to *path* and return the path.

    Merges ``clusters`` (geometry) with ``validation_results`` (LLM outcome)
    into a single JSON document suitable for loading by the task-generation
    entry point.

    Each cluster entry in the output contains:
    - ``cluster_idx``
    - ``status``            — from the validation result
    - ``representative_sequence`` — ``final_sequence`` from validation (or None)
    - ``representative_length``   — ``final_length`` from validation (or None)
    - ``validation_attempts``     — number of LLM validation attempts
    - ``n_members``         — number of cluster members
    - ``members``           — list of ``{sequence, distance_to_medoid}``
    """
    import json
    from datetime import datetime

    from src.backends.artifacts import require_backend_spec

    require_backend_spec({"backend_spec": backend_spec})
    if domain != backend_spec["domain"]:
        raise ValueError("Cluster domain does not match its prepared input configuration")

    # Index validation results by cluster_idx for O(1) lookup
    vr_by_idx: Dict[int, Dict] = {vr["cluster_idx"]: vr for vr in validation_results}

    merged_clusters = []
    for c in clusters:
        vr = vr_by_idx.get(c.cluster_idx, {})
        merged_clusters.append(
            {
                "cluster_idx": c.cluster_idx,
                "status": vr.get("status"),
                "representative_sequence": vr.get("final_sequence"),
                "representative_length": vr.get("final_length"),
                "validation_attempts": vr.get("attempts"),
                "n_members": len(c.member_indices),
                "members": [
                    {
                        "sequence": c.member_sequences[i],
                        "distance_to_medoid": c.intra_cluster_distances[i],
                    }
                    for i in range(len(c.member_sequences))
                ],
            }
        )

    num_usable = sum(1 for entry in merged_clusters if entry["status"] in ("valid_medoid", "replaced"))

    artifact = {
        "backend_spec": backend_spec,
        "timestamp": datetime.now().isoformat(),
        "domain": domain,
        "num_clusters": num_clusters,
        "num_usable": num_usable,
        "ngram_checkpoint": ngram_checkpoint,
        "length_distribution": length_distribution_config,
        "reference_lengths": reference_lengths,
        "clustering_config": clustering_config,
        "validation_config": validation_config,
        "clusters": merged_clusters,
    }

    with open(path, "w") as f:
        json.dump(artifact, f, indent=2)

    return path


def load_validated_clusters(path: str) -> Dict:
    """Load a validated-cluster artifact from *path* and return it as a dict."""
    import json

    from src.backends.artifacts import require_backend_spec

    with open(path) as f:
        artifact = json.load(f)
    require_backend_spec(artifact)
    return artifact
