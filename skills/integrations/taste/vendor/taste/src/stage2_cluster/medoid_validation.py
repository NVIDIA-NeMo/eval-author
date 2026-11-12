"""Sequential medoid validation helpers for stage 2.

Validates each cluster's medoid with an :class:`ActionSequenceValidator`.
When the medoid is invalid, searches the cluster's other members for a valid
replacement, preferring candidates that differ from the failed sequence at the
positions flagged as problematic by the validator.
"""

from typing import Dict, List

from src.common.sampler.action_sequence_clusterer import ClusterInfo
from src.common.sequence_evidence import validation_payload
from src.stage2_cluster.action_sequence_validator import ActionSequenceValidator


def _differs_at_problematic(
    candidate: List[str],
    failed_seq: List[str],
    problematic_indices: List[int],
) -> bool:
    """Return True if ``candidate`` differs from ``failed_seq`` at any problematic index."""
    for idx in problematic_indices:
        if idx >= len(candidate) or idx >= len(failed_seq):
            return True
        if candidate[idx] != failed_seq[idx]:
            return True
    return False


def validate_cluster(
    cluster_info: ClusterInfo,
    validator: ActionSequenceValidator,
    max_replacements: int = 5,
    *,
    evidence=None,
    round_index: int = 0,
) -> Dict:
    """Validate a cluster's medoid; search for a replacement if invalid.

    Returns a dict with keys ``cluster_idx``, ``status`` (one of
    ``valid_medoid`` / ``replaced`` / ``unusable``), ``final_sequence``,
    ``final_length``, ``attempts``, ``original_valid``, ``reason``.
    """

    def check(sequence, pass_name, member_index):
        context = {
            "round_index": round_index,
            "cluster_idx": cluster_info.cluster_idx,
            "pass_name": pass_name,
            "member_index": member_index,
            "sequence": sequence,
        }
        if evidence is not None:
            evidence.record("cluster_candidate_started", **context)
        result = validator.validate(sequence)
        if evidence is not None:
            evidence.record("cluster_candidate_completed", **context, decision=validation_payload(result))
        return result

    medoid_seq = cluster_info.medoid_sequence
    result = check(medoid_seq, "medoid", None)
    attempts = 1

    if result.valid:
        return {
            "cluster_idx": cluster_info.cluster_idx,
            "status": "valid_medoid",
            "final_sequence": medoid_seq,
            "final_length": len(medoid_seq),
            "attempts": attempts,
            "original_valid": True,
            "reason": result.reason,
        }

    failed_seq = medoid_seq
    problematic = result.problematic_indices
    original_reason = result.reason

    # First pass: prefer candidates that differ from the failed sequence at the
    # problematic positions flagged by the validator. Skip member_sequences[0]
    # since that is the medoid itself.
    candidates_tried = 0
    for member_index, member_seq in enumerate(cluster_info.member_sequences[1:], 1):
        if candidates_tried >= max_replacements:
            break
        if problematic and not _differs_at_problematic(member_seq, failed_seq, problematic):
            continue

        candidates_tried += 1
        attempts += 1
        replacement_result = check(member_seq, "preferred", member_index)

        if replacement_result.valid:
            return {
                "cluster_idx": cluster_info.cluster_idx,
                "status": "replaced",
                "final_sequence": member_seq,
                "final_length": len(member_seq),
                "attempts": attempts,
                "original_valid": False,
                "reason": f"Original: {original_reason}; replaced with member",
            }

    # Second pass: fall back to candidates we previously skipped (those that
    # did NOT differ at problematic positions), if we still have a budget.
    if candidates_tried < max_replacements:
        for member_index, member_seq in enumerate(cluster_info.member_sequences[1:], 1):
            if candidates_tried >= max_replacements:
                break
            if problematic and _differs_at_problematic(member_seq, failed_seq, problematic):
                continue

            candidates_tried += 1
            attempts += 1
            replacement_result = check(member_seq, "fallback", member_index)

            if replacement_result.valid:
                return {
                    "cluster_idx": cluster_info.cluster_idx,
                    "status": "replaced",
                    "final_sequence": member_seq,
                    "final_length": len(member_seq),
                    "attempts": attempts,
                    "original_valid": False,
                    "reason": f"Original: {original_reason}; replaced with member (fallback)",
                }

    return {
        "cluster_idx": cluster_info.cluster_idx,
        "status": "unusable",
        "final_sequence": None,
        "final_length": None,
        "attempts": attempts,
        "original_valid": False,
        "reason": f"Original: {original_reason}; no valid replacement found",
    }


def validate_clusters(
    clusters: List[ClusterInfo],
    validator: ActionSequenceValidator,
    max_replacements: int = 5,
    *,
    evidence=None,
    round_index: int = 0,
) -> List[Dict]:
    """Validate every cluster in order, printing one progress line per cluster.

    Returns the validation results in the same order as the input clusters.
    """
    total = len(clusters)
    results_by_idx: Dict[int, Dict] = {}
    for i, cluster_info in enumerate(clusters):
        result = validate_cluster(cluster_info, validator, max_replacements, evidence=evidence, round_index=round_index)
        if evidence is not None:
            evidence.record("cluster_completed", round_index=round_index, result=result)
        seq_display = result["final_sequence"]
        if seq_display and len(seq_display) > 3:
            seq_display = f"[{seq_display[0]}, ..., {seq_display[-1]}] (len={result['final_length']})"
        print(
            f"  [{i + 1}/{total}] Cluster {result['cluster_idx']:3d}:"
            f" {result['status']:14s}"
            f" ({result['attempts']} attempts) — {seq_display}",
            flush=True,
        )
        results_by_idx[result["cluster_idx"]] = result
    return [results_by_idx[c.cluster_idx] for c in clusters]
