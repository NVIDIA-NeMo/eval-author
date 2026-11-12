"""
Sequence diversity metrics based on edit distance.

This module provides functions for computing edit distance (Levenshtein distance)
between action sequences and measuring diversity of sequences relative to a set.
"""

from typing import Callable, List, Tuple


def edit_distance(seq1: List[str], seq2: List[str]) -> int:
    """
    Compute the Levenshtein edit distance between two sequences.

    The edit distance is the minimum number of single-element edits (insertions,
    deletions, or substitutions) required to transform seq1 into seq2.

    Uses dynamic programming with O(m*n) time and O(m*n) space complexity,
    where m and n are the lengths of the sequences.

    Args:
        seq1: First sequence of action names
        seq2: Second sequence of action names

    Returns:
        The edit distance (non-negative integer)

    Examples:
        >>> edit_distance(["a", "b", "c"], ["a", "b", "c"])
        0
        >>> edit_distance(["a", "b", "c"], ["a", "x", "c"])
        1
        >>> edit_distance(["a", "b"], ["a", "b", "c"])
        1
    """
    m, n = len(seq1), len(seq2)

    # Handle empty sequences
    if m == 0:
        return n
    if n == 0:
        return m

    # Initialize DP table
    # dp[i][j] = edit distance between seq1[:i] and seq2[:j]
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    # Base cases: distance from empty sequence
    for i in range(m + 1):
        dp[i][0] = i  # Delete all i elements
    for j in range(n + 1):
        dp[0][j] = j  # Insert all j elements

    # Fill DP table
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                # Elements match, no edit needed
                dp[i][j] = dp[i - 1][j - 1]
            else:
                # Take minimum of:
                # 1. Substitute: dp[i-1][j-1] + 1
                # 2. Delete from seq1: dp[i-1][j] + 1
                # 3. Insert into seq1: dp[i][j-1] + 1
                dp[i][j] = 1 + min(
                    dp[i - 1][j - 1],  # Substitute
                    dp[i - 1][j],  # Delete
                    dp[i][j - 1],  # Insert
                )

    return dp[m][n]


def normalized_edit_distance(seq1: List[str], seq2: List[str]) -> float:
    """
    Compute normalized edit distance between two sequences.

    Normalizes the edit distance by dividing by the maximum sequence length.
    This ensures the result is in the range [0, 1], making it easier to
    compare distances between sequences of different lengths.

    Args:
        seq1: First sequence of action names
        seq2: Second sequence of action names

    Returns:
        Normalized edit distance in range [0.0, 1.0]
        - 0.0 means sequences are identical
        - 1.0 means maximum distance (completely different or one is empty)

    Examples:
        >>> normalized_edit_distance(["a", "b", "c"], ["a", "b", "c"])
        0.0
        >>> normalized_edit_distance(["a", "b", "c"], ["x", "y", "z"])
        1.0
        >>> normalized_edit_distance(["a", "b"], ["a", "b", "c", "d"])
        0.5
    """
    max_len = max(len(seq1), len(seq2))

    # Handle empty sequences
    if max_len == 0:
        return 0.0

    dist = edit_distance(seq1, seq2)
    return dist / max_len


def weighted_edit_distance(
    seq1: List[str],
    seq2: List[str],
    sub_cost_fn: Callable[[str, str], float],
    indel_cost: float = 1.0,
) -> float:
    """
    Compute weighted edit distance between two sequences.

    Like standard Levenshtein but substitution cost is determined by
    sub_cost_fn(a, b) instead of a fixed 1.  Insertion and deletion
    costs are uniform (indel_cost).

    Returns the raw (unnormalized) distance.
    """
    m, n = len(seq1), len(seq2)
    if m == 0:
        return n * indel_cost
    if n == 0:
        return m * indel_cost

    dp = [[0.0] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        dp[i][0] = i * indel_cost
    for j in range(n + 1):
        dp[0][j] = j * indel_cost

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = sub_cost_fn(seq1[i - 1], seq2[j - 1])
            dp[i][j] = min(
                dp[i - 1][j - 1] + cost,  # substitute
                dp[i - 1][j] + indel_cost,  # delete
                dp[i][j - 1] + indel_cost,  # insert
            )

    return dp[m][n]


def min_distance_to_set(sequence: List[str], sequence_set: List[List[str]]) -> Tuple[float, int]:
    """
    Find the minimum normalized distance from a sequence to any sequence in a set.

    This is useful for determining how different a new sequence is from existing
    sequences in a training set.

    Args:
        sequence: The sequence to compare
        sequence_set: List of sequences to compare against

    Returns:
        Tuple of (min_distance, closest_index) where:
        - min_distance: Minimum normalized distance (float in [0.0, 1.0])
        - closest_index: Index of the closest sequence in sequence_set

    Raises:
        ValueError: If sequence_set is empty

    Examples:
        >>> seq = ["a", "b", "c"]
        >>> seq_set = [["x", "y"], ["a", "b", "c"], ["p", "q"]]
        >>> min_dist, idx = min_distance_to_set(seq, seq_set)
        >>> min_dist
        0.0
        >>> idx
        1
    """
    if not sequence_set:
        raise ValueError("sequence_set cannot be empty")

    min_dist = float("inf")
    closest_idx = -1

    for i, other_seq in enumerate(sequence_set):
        dist = normalized_edit_distance(sequence, other_seq)
        if dist < min_dist:
            min_dist = dist
            closest_idx = i

    return min_dist, closest_idx


def avg_distance_to_set(sequence: List[str], sequence_set: List[List[str]]) -> float:
    """
    Compute the average normalized distance from a sequence to all sequences in a set.

    This provides a measure of how different a sequence is from a set of sequences
    on average, which can be useful for understanding overall diversity.

    Args:
        sequence: The sequence to compare
        sequence_set: List of sequences to compare against

    Returns:
        Average normalized distance (float in [0.0, 1.0])

    Raises:
        ValueError: If sequence_set is empty

    Examples:
        >>> seq = ["a", "b"]
        >>> seq_set = [["a", "b"], ["a", "x"], ["x", "y"]]
        >>> avg_distance_to_set(seq, seq_set)
        0.5
    """
    if not sequence_set:
        raise ValueError("sequence_set cannot be empty")

    total_dist = 0.0
    for other_seq in sequence_set:
        total_dist += normalized_edit_distance(sequence, other_seq)

    return total_dist / len(sequence_set)


def diversity_score(sequence: List[str], sequence_set: List[List[str]]) -> float:
    """
    Compute diversity score of a sequence relative to a set of sequences.

    The diversity score is defined as the minimum normalized distance from the
    sequence to any sequence in the set. A higher score indicates the sequence
    is more diverse (different) from the training set.

    Score interpretation:
    - 0.0: Sequence is identical to some sequence in the set
    - 0.3-0.5: Moderately different from the closest sequence
    - 0.7+: Very different (novel pattern)

    Args:
        sequence: The sequence to evaluate
        sequence_set: List of sequences to compare against (e.g., training set)

    Returns:
        Diversity score (float in [0.0, 1.0])

    Raises:
        ValueError: If sequence_set is empty

    Examples:
        >>> seq = ["a", "b", "c"]
        >>> seq_set = [["x", "y"], ["a", "b", "c"], ["p", "q"]]
        >>> diversity_score(seq, seq_set)
        0.0
        >>> seq = ["a", "b", "c"]
        >>> seq_set = [["x", "y", "z"], ["p", "q", "r"]]
        >>> diversity_score(seq, seq_set)
        1.0
    """
    min_dist, _ = min_distance_to_set(sequence, sequence_set)
    return min_dist
