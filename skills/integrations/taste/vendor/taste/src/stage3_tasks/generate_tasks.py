#!/usr/bin/env python3
"""Stage 3 (generate): build validated tasks from a clusters artifact.

Loads validated cluster medoids produced by stage 2, generates a task per
usable cluster (one LLM call per task with patch-based retries), then
re-clusters and retries any clusters that failed end-to-end. Successful
tasks are checkpointed to ``task_sets/<name>/tasks.json`` after every
success so a crash or interrupt does not lose work; ``--resume`` reconciles
progress against these durable task records before skipping successful clusters.

Example:
    python -m src.stage3_tasks.generate_tasks \\
        --clusters artifacts/validated_clusters.json
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.backends.contracts import IncompatibleBackend
from src.common.domain_config import DomainConfig
from src.common.sampler.action_sequence_clusterer import load_validated_clusters
from src.common.sampler.length_distribution import LengthDistributionConfig
from src.stage3_tasks.task_builder import GeneratedTask
from src.stage3_tasks.task_generation import TaskGenerator
from src.stage3_tasks.task_set import TaskSetConfig, TaskSetManager, atomic_write_text, save_task_rows
from src.stage3_tasks.task_validator import TaskValidator
from src.stage3_tasks.validation_with_retry import validate_task_with_retries


def _load_progress_records(progress_path: str) -> list[dict]:
    if not os.path.exists(progress_path):
        return []
    with open(progress_path) as f:
        lines = f.readlines()
    records = []
    for line_idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            # An interrupt can leave only the final append incomplete.
            if line_idx == len(lines) - 1 and not line.endswith("\n"):
                continue
            raise ValueError(f"Cannot resume: malformed progress at {progress_path}:{line_idx + 1}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Cannot resume: progress record {line_idx + 1} must be an object")
        records.append(record)
    return records


def _successful_cluster_records(records: list[dict]) -> dict[int, dict]:
    completed = {}
    for record in records:
        if not record.get("success"):
            continue
        cluster_idx = record.get("cluster_idx")
        if type(cluster_idx) is not int:
            raise ValueError("Cannot resume: successful progress record has no integer cluster_idx")
        if cluster_idx in completed and completed[cluster_idx] != record:
            raise ValueError(f"Cannot resume: conflicting successful progress for cluster {cluster_idx}")
        completed[cluster_idx] = record
    return completed


def _load_successful_cluster_records(progress_path: str) -> dict[int, dict]:
    return _successful_cluster_records(_load_progress_records(progress_path))


def _load_completed_cluster_idxs(progress_path: str) -> set:
    return set(_load_successful_cluster_records(progress_path))


def _load_existing_tasks(task_set_dir: str) -> List[Dict]:
    """Load existing tasks.json if it exists, return empty list otherwise."""
    tasks_path = os.path.join(task_set_dir, "tasks.json")
    if not os.path.exists(tasks_path):
        return []
    with open(tasks_path) as f:
        try:
            rows = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Cannot resume: malformed {tasks_path}; restore the checkpoint or use a fresh run"
            ) from exc
    if not isinstance(rows, list):
        raise ValueError(f"Cannot resume: {tasks_path} must contain a task-record list")
    return rows


def _reconcile_resume(task_set_dir: str, entries: List[Dict]) -> tuple[List[Dict], dict[int, dict]]:
    rows = _load_existing_tasks(task_set_dir)
    progress_path = os.path.join(task_set_dir, "progress.jsonl")
    progress = _load_progress_records(progress_path)
    logged = _successful_cluster_records(progress)
    completed = {}
    task_ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Cannot resume: tasks.json contains a record without a string id")
        task_id = row["id"]
        if task_id in task_ids:
            raise ValueError(f"Cannot resume: duplicate task identity {task_id!r} in tasks.json")
        task_ids.add(task_id)
        generation = row.get("generation")
        if not isinstance(generation, dict) or generation.get("success") is not True:
            raise ValueError(
                f"Cannot resume: task {task_id!r} lacks successful generation provenance; "
                "restore a complete checkpoint or use a fresh run"
            )
        index, cluster_idx = generation.get("index"), generation.get("cluster_idx")
        if (
            type(index) is not int
            or index < 0
            or index >= len(entries)
            or type(cluster_idx) is not int
            or entries[index].get("cluster_idx") != cluster_idx
            or task_id != f"generated_{index}"
        ):
            raise ValueError(f"Cannot resume: task {task_id!r} conflicts with its generation or cluster identity")
        if cluster_idx in completed:
            raise ValueError(f"Cannot resume: duplicate successful cluster {cluster_idx} in tasks.json")
        candidate = row.get("candidate")
        sequence = generation.get("action_sequence")
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("task"), dict)
            or not isinstance(candidate.get("reference_actions"), list)
            or not isinstance(row.get("fixture"), dict)
            or not isinstance(row.get("native_task"), dict)
            or not isinstance(sequence, list)
            or not all(isinstance(name, str) for name in sequence)
            or not all(isinstance(action, dict) for action in candidate["reference_actions"])
            or [action.get("name") for action in candidate["reference_actions"]] != sequence
        ):
            raise ValueError(
                f"Cannot resume: task {task_id!r} has missing or conflicting payload/provenance; "
                "restore a complete checkpoint or use a fresh run"
            )
        completed[cluster_idx] = generation
    for cluster_idx, record in logged.items():
        if cluster_idx not in completed:
            raise ValueError(
                f"Cannot resume: progress reports successful cluster {cluster_idx}, but tasks.json has no payload; "
                "restore the matching tasks.json or use a fresh run"
            )
        if completed[cluster_idx] != record:
            raise ValueError(f"Cannot resume: tasks.json and progress.jsonl conflict for cluster {cluster_idx}")
    progress.extend(record for cluster_idx, record in completed.items() if cluster_idx not in logged)
    # Rebuild missing success lines and remove an interrupted trailing append.
    atomic_write_text(progress_path, "".join(json.dumps(record) + "\n" for record in progress))
    return rows, completed


def _checkpoint_save_tasks(task_set_dir: str, task_dicts: List[Dict], *, domain_config: DomainConfig) -> None:
    """Write tasks to tasks.json immediately so resume can recover them after a crash."""
    save_task_rows(task_set_dir, task_dicts, domain_config=domain_config)


def _short_model_name(model_name: str) -> str:
    """Extract a short name from a full model identifier.

    Example: ``vertex_ai/gemini-3-flash-preview`` -> ``gemini_3_flash``.
    """
    base = model_name.rsplit("/", 1)[-1]
    base = re.sub(r"-(preview|latest)$", "", base)
    return base.replace("-", "_")


def _build_entries_from_clusters(
    clusters_path: str,
    indices: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Load validated clusters and return usable entries with action sequences."""
    data = load_validated_clusters(clusters_path)
    clusters = data["clusters"]

    entries = []
    for cluster in clusters:
        if cluster.get("status") == "unusable":
            continue
        idx = cluster["cluster_idx"]
        if indices is not None and idx not in indices:
            continue
        entries.append(
            {
                "action_sequence": cluster["representative_sequence"],
                "source": "medoid",
                "cluster_idx": idx,
            }
        )

    return entries


def generate_single_task(
    entry: Dict[str, Any],
    index: int,
    n_total: int,
    domain: str,
    model_name: str,
    gt_llm: str,
    max_task_retries: int,
    *,
    gt_coverage_p: float = 0.33,
    gt_coverage_shuffle: bool = True,
    domain_config: DomainConfig,
    max_patch_attempts: int = 3,
    write_only: bool = False,
) -> Tuple[Optional[GeneratedTask], Dict[str, Any]]:
    """Generate and validate a single task from an action sequence."""
    action_sequence = entry["action_sequence"]
    label = f"[{index + 1}/{n_total}]"
    print(f"{label} Generating task for sequence of length {len(action_sequence)}: {action_sequence}")

    if domain != domain_config.domain:
        raise ValueError("Domain does not match the selected backend configuration")

    generator = TaskGenerator(domain=domain, model_name=model_name, domain_config=domain_config)
    validator = TaskValidator(
        domain=domain,
        gt_llm=gt_llm,
        partial_coverage_p=gt_coverage_p,
        gt_coverage_shuffle=gt_coverage_shuffle,
        domain_config=domain_config,
        write_only=write_only,
    )

    t0 = time.time()

    def on_retry(retry_count, max_retries, db_only):
        kind = "DB-only" if db_only else "full"
        print(f"  {label} Retry {retry_count} ({kind})")

    success, task_dict, db_entities, result, retries_used = validate_task_with_retries(
        action_sequence=action_sequence,
        generator=generator,
        validator=validator,
        max_retries=max_task_retries,
        max_patch_attempts=max_patch_attempts,
        on_retry=on_retry,
    )
    if result.unsupported:
        raise IncompatibleBackend(
            f"EA task service lacks required capabilities: {', '.join(result.unsupported)}. "
            f"{result.error or 'Prepare compatible task support before continuing.'}"
        )

    elapsed = time.time() - t0

    meta = {
        "backend": domain_config.to_spec(),
        "index": index,
        "source": entry.get("source"),
        "action_sequence": action_sequence,
        "success": success,
        "validation_evidence": result.evidence_path,
        "retries_used": retries_used,
        "elapsed_seconds": round(elapsed, 1),
    }
    if "cluster_idx" in entry:
        meta["cluster_idx"] = entry["cluster_idx"]
    if not success:
        meta["error"] = result.error

    if success:
        generated = GeneratedTask.create(
            task_dict, db_entities, domain=domain, domain_config=domain_config, validation_result=result
        )
        generated.task.id = f"generated_{index}"
        generated.task.row["generation"] = copy.deepcopy(meta)
        generated.validation.success = True
        generated.validation.solver_retries = retries_used
        print(f"  {label} Success (retries={retries_used}, {elapsed:.1f}s)")
        return generated, meta
    else:
        print(f"  {label} Failed after {retries_used} retries ({elapsed:.1f}s): {result.error}")
        return None, meta


def select_recovery_representatives(
    failed_orig_indices: List[int],
    entries: List[Dict[str, Any]],
    all_meta: List[Dict],
    clusters_path: str,
    domain: str,
    model_name: str,
    *,
    recluster_pool_size: int = 500,
    recluster_temperature: float = 1.5,
    domain_config: DomainConfig,
) -> dict:
    """Select one recovery batch without generating tasks or resolving a task service."""
    if not failed_orig_indices:
        return {"replacements": [], "skipped": "no_failed_sequences"}
    import random

    from src.common.sampler.action_sequence_clusterer import ActionSequenceClusterer, SubstitutionCosts
    from src.common.sampler.action_sequence_ngram_sampler import ActionSequenceNGramModelSampler
    from src.common.tool_spec_retriever import ToolsSpecRetriever
    from src.stage2_cluster.action_sequence_validator import ActionSequenceValidator as SeqValidator

    if domain != domain_config.domain:
        raise ValueError("Domain does not match the selected backend configuration")

    data = load_validated_clusters(clusters_path)
    checkpoint_path = data.get("ngram_checkpoint")
    if not checkpoint_path:
        print("  WARNING: cluster artifact has no ngram_checkpoint, skipping re-cluster.")
        return {"replacements": [], "skipped": "missing_checkpoint"}

    print(f"  Loading n-gram model from {checkpoint_path}...")
    sampler = ActionSequenceNGramModelSampler.load_state(checkpoint_path)
    from src.backends.artifacts import require_same_backend

    require_same_backend(domain_config.to_spec(), data["backend_spec"])
    require_same_backend(domain_config.to_spec(), sampler.backend_spec)
    if data.get("length_distribution"):
        sampler._length_config = LengthDistributionConfig.from_dict(data["length_distribution"])
        sampler._reference_lengths = data.get("reference_lengths", sampler._reference_lengths)
        sampler._length_distribution = None
    action_types = ToolsSpecRetriever(path=domain_config.tool_spec_path).get_action_types()

    failed_set = set(failed_orig_indices)
    frozen_seqs = [
        all_meta[i].get("action_sequence", entries[i]["action_sequence"])
        for i in range(len(entries))
        if i not in failed_set and all_meta[i].get("success")
    ]
    n_new = len(failed_orig_indices)
    print(f"  Frozen: {len(frozen_seqs)} successful sequences, requesting {n_new} new clusters")

    if not frozen_seqs:
        print("  WARNING: no successful sequences to use as frozen centers; skipping re-cluster.")
        return {"replacements": [], "skipped": "no_successful_sequences"}

    clustering = data.get("clustering_config", {})
    structural_filter = None
    if clustering.get("only_write", False):
        backend_filter = structural_filter

        def structural_filter(sequence):
            return (backend_filter is None or backend_filter(sequence)) and all(
                action_types.get(action) in ("WRITE", "GENERIC") for action in sequence
            )

    substitution_costs = SubstitutionCosts(
        **clustering.get("substitution_costs", {"group_map": domain_config.action_group_map})
    )
    clusterer = ActionSequenceClusterer(
        model=sampler.model,
        length_distribution=sampler.length_distribution,
        length_config=sampler._length_config,
        n_clusters=len(entries),
        pool_size=recluster_pool_size,
        temperature=recluster_temperature,
        action_types=action_types,
        rng=random.Random(42),
        structural_filter=structural_filter,
        substitution_costs=substitution_costs,
        max_kmedoids_iters=clustering.get("max_kmedoids_iters", 100),
    )

    print(f"  Generating pool of {recluster_pool_size} sequences for re-clustering...")
    new_pool = clusterer.generate_pool()
    print("  Running recluster_with_frozen...")
    new_cluster_infos = clusterer.recluster_with_frozen(frozen_seqs, n_new, new_pool)

    # Validate new sequences: pick the first LLM-valid sequence per new cluster.
    validator_factory = getattr(domain_config, "sequence_validator_factory", SeqValidator)
    seq_validator = validator_factory(
        domain=domain,
        model_name=model_name,
        domain_config=domain_config,
    )
    replacements = []
    for orig_idx, ci in zip(failed_orig_indices, new_cluster_infos):
        chosen = None
        sequence_checks = []
        for seq in ci.member_sequences:
            r = seq_validator.validate(seq)
            sequence_checks.append(
                {
                    "action_sequence": seq,
                    "valid": r.valid,
                    "reason": getattr(r, "reason", None),
                    "problematic_indices": getattr(r, "problematic_indices", []),
                }
            )
            if r.valid:
                chosen = seq
                break
        sequence_validated = chosen is not None
        if chosen is None:
            chosen = ci.medoid_sequence  # fallback: use the medoid even if not LLM-validated
        replacements.append(
            {
                "index": orig_idx,
                "action_sequence": chosen,
                "source": "reclustered",
                "cluster_idx": entries[orig_idx].get("cluster_idx"),
                "parent_sequence": entries[orig_idx]["action_sequence"],
                "sequence_validated": sequence_validated,
                "sequence_checks": sequence_checks,
            }
        )
        print(f"    New cluster {ci.cluster_idx}: {chosen}")

    return {"replacements": replacements, "skipped": None}


def _recluster_and_retry(
    failed_orig_indices: List[int],
    entries: List[Dict[str, Any]],
    all_generated: List,
    all_meta: List[Dict],
    clusters_path: str,
    domain: str,
    model_name: str,
    gt_llm: str,
    max_task_retries: int,
    progress_path: str,
    *,
    gt_coverage_p: float = 0.33,
    gt_coverage_shuffle: bool = True,
    recluster_pool_size: int = 500,
    recluster_temperature: float = 1.5,
    domain_config: DomainConfig,
    on_task_success=None,
    max_patch_attempts: int = 3,
    write_only: bool = False,
) -> None:
    """Re-cluster failed entries, then run the automated generation path."""
    recovery = select_recovery_representatives(
        failed_orig_indices,
        entries,
        all_meta,
        clusters_path,
        domain,
        model_name,
        recluster_pool_size=recluster_pool_size,
        recluster_temperature=recluster_temperature,
        domain_config=domain_config,
    )
    # Retry generation with the new sequences.
    n_total_retry = len(failed_orig_indices)
    for retry_i, replacement in enumerate(recovery["replacements"]):
        orig_idx = replacement["index"]
        new_seq = replacement["action_sequence"]
        new_entry = replacement
        print(f"  [recluster {retry_i + 1}/{n_total_retry}] Retrying orig_idx={orig_idx} with new sequence: {new_seq}")
        generated, meta = generate_single_task(
            new_entry,
            orig_idx,
            n_total_retry,
            domain,
            model_name,
            gt_llm,
            max_task_retries,
            gt_coverage_p=gt_coverage_p,
            gt_coverage_shuffle=gt_coverage_shuffle,
            domain_config=domain_config,
            max_patch_attempts=max_patch_attempts,
            write_only=write_only,
        )
        meta["reclustered"] = True
        meta["parent_sequence"] = entries[orig_idx]["action_sequence"]
        all_generated[orig_idx] = generated
        all_meta[orig_idx] = meta
        if generated is not None:
            generated.task.row["generation"] = copy.deepcopy(meta)
        if generated is not None and on_task_success is not None:
            on_task_success(generated)
        with open(progress_path, "a") as f:
            f.write(json.dumps(meta) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate validated tasks from a stage-2 clusters artifact.",
    )
    parser.add_argument(
        "--clusters",
        type=str,
        required=True,
        help="Path to validated_clusters.json (output of stage 2).",
    )
    parser.add_argument(
        "--indices", type=str, default=None, help="Comma-separated cluster indices to use (default: all usable)."
    )
    parser.add_argument("--domain", type=str, default=None, help="Assert that the artifact uses this domain.")
    parser.add_argument(
        "--model-name", type=str, default="gemini/gemini-3-flash-preview", help="LLM model for task generation."
    )
    parser.add_argument(
        "--gt-llm", type=str, default="gemini/gemini-3-flash-preview", help="LLM model for GT agent validation."
    )
    parser.add_argument(
        "--gt-coverage-p",
        type=float,
        default=0.33,
        help="Fraction of assistant actions to cover in partial_coverage mode (default: 0.33).",
    )
    parser.add_argument(
        "--gt-coverage-shuffle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Shuffle action order in partial_coverage mode (default: True). "
        "Use --no-gt-coverage-shuffle to keep original order.",
    )
    parser.add_argument("--max-task-retries", type=int, default=3, help="Max retries per task (default: 3).")
    parser.add_argument("--name", type=str, default=None, help="Task set name (default: auto-generated).")
    parser.add_argument(
        "--resume",
        type=str,
        nargs="?",
        const="auto",
        default=None,
        metavar="TASK_SET_NAME",
        help="Resume a previous run. Pass a task set name "
        "or omit the value to derive it from the backend/domain and --model-name. "
        "Skips successful task payloads in tasks.json and reconciles progress.jsonl.",
    )
    parser.add_argument(
        "--write-only",
        action="store_true",
        default=False,
        help="Request write-only action hints when supported by the selected backend.",
    )
    parser.add_argument(
        "--max-patch-attempts",
        type=int,
        default=3,
        help="Max patch-LLM calls per failed validation step before full regeneration; 0 disables patching.",
    )

    args = parser.parse_args()

    artifact = load_validated_clusters(args.clusters)
    domain_config = DomainConfig.from_spec(artifact["backend_spec"])
    if args.domain is not None and args.domain != domain_config.domain:
        raise ValueError("Domain does not match cluster backend configuration")
    args.domain = domain_config.domain

    indices = None
    if args.indices is not None:
        indices = [int(x.strip()) for x in args.indices.split(",")]

    print(f"Loading validated clusters from '{args.clusters}'...")
    entries = _build_entries_from_clusters(args.clusters, indices)

    if not entries:
        print("No action sequences to process. Exiting.")
        sys.exit(1)

    domain_config.service()  # Resolve EA support before starting generation.

    print(f"Found {len(entries)} action sequences to generate tasks for.")

    short_model = _short_model_name(args.model_name)
    if args.resume and args.resume != "auto":
        name = args.resume
    else:
        name = args.name or f"{args.domain}_{short_model}"
    print(f"Task set name: {name}")

    task_set_dir = os.path.join("task_sets", name)
    os.makedirs(task_set_dir, exist_ok=True)
    from src.backends.artifacts import bind_run

    bind_run(
        task_set_dir,
        domain_config.to_spec(),
        artifact.get("length_distribution"),
        resume=args.resume is not None,
        settings={
            "clusters_sha256": hashlib.sha256(Path(args.clusters).read_bytes()).hexdigest(),
            "indices": indices,
            "model_name": args.model_name,
            "gt_llm": args.gt_llm,
            "gt_coverage_p": args.gt_coverage_p,
            "gt_coverage_shuffle": args.gt_coverage_shuffle,
            "max_task_retries": args.max_task_retries,
            "max_patch_attempts": args.max_patch_attempts,
            "write_only": args.write_only,
        },
    )
    progress_path = os.path.join(task_set_dir, "progress.jsonl")

    # Durable task payloads determine completion; progress is a recoverable journal.
    existing_tasks: List[Dict] = []
    completed_cluster_idxs: set = set()
    completed_records: dict[int, dict] = {}
    if args.resume is not None:
        existing_tasks, completed_records = _reconcile_resume(task_set_dir, entries)
        completed_cluster_idxs = set(completed_records)
        if existing_tasks:
            domain_config.service().export(existing_tasks, os.path.join(task_set_dir, "native-tasks.jsonl"))

        if completed_cluster_idxs:
            print(
                f"Resuming: {len(completed_cluster_idxs)} cluster_idxs already succeeded, "
                f"{len(existing_tasks)} existing tasks in tasks.json"
            )
            pending = [(i, e) for i, e in enumerate(entries) if e.get("cluster_idx") not in completed_cluster_idxs]
            print(f"  {len(pending)} entries to retry")
        else:
            pending = list(enumerate(entries))
    else:
        pending = list(enumerate(entries))

    if not pending:
        print("All entries already completed. Rebuilding task-set outputs from the checkpoint.")

    all_generated: List[Optional[GeneratedTask]] = [None] * len(entries)
    all_meta: List[Dict[str, Any]] = [dict(completed_records.get(entry.get("cluster_idx"), {})) for entry in entries]

    # Running list of completed task dicts (existing + newly succeeded).
    # Checkpointed to tasks.json after each success so a crash doesn't lose work.
    checkpoint_tasks: List[Dict] = list(existing_tasks)

    def _on_task_success(generated: GeneratedTask) -> None:
        row = generated.to_dict()
        if any(
            saved["id"] == row["id"] or saved["generation"]["cluster_idx"] == row["generation"]["cluster_idx"]
            for saved in checkpoint_tasks
        ):
            raise ValueError(f"Refusing to checkpoint duplicate task or cluster identity: {row['id']}")
        updated = [*checkpoint_tasks, row]
        _checkpoint_save_tasks(task_set_dir, updated, domain_config=domain_config)
        checkpoint_tasks.append(row)

    n_total = len(pending)

    for seq_idx, (orig_idx, entry) in enumerate(pending):
        generated, meta = generate_single_task(
            entry,
            orig_idx,
            n_total,
            args.domain,
            args.model_name,
            args.gt_llm,
            args.max_task_retries,
            gt_coverage_p=args.gt_coverage_p,
            gt_coverage_shuffle=args.gt_coverage_shuffle,
            domain_config=domain_config,
            max_patch_attempts=args.max_patch_attempts,
            write_only=args.write_only,
        )
        all_generated[orig_idx] = generated
        all_meta[orig_idx] = meta
        if generated is not None:
            _on_task_success(generated)
        with open(progress_path, "a") as f:
            f.write(json.dumps(meta) + "\n")

    # Re-cluster and retry any clusters that failed end-to-end.
    failed_orig_indices = [
        orig_idx for orig_idx, meta in enumerate(all_meta) if meta.get("index") is not None and not meta.get("success")
    ]
    if failed_orig_indices:
        print(f"\n--- Re-clustering on failure: {len(failed_orig_indices)} failed entries ---")
        _recluster_and_retry(
            failed_orig_indices=failed_orig_indices,
            entries=entries,
            all_generated=all_generated,
            all_meta=all_meta,
            clusters_path=args.clusters,
            domain=args.domain,
            model_name=args.model_name,
            gt_llm=args.gt_llm,
            max_task_retries=args.max_task_retries,
            progress_path=progress_path,
            gt_coverage_p=args.gt_coverage_p,
            gt_coverage_shuffle=args.gt_coverage_shuffle,
            domain_config=domain_config,
            on_task_success=_on_task_success,
            max_patch_attempts=args.max_patch_attempts,
            write_only=args.write_only,
        )
    else:
        print("  No failed entries; re-clustering skipped.")

    new_successful = [g for g in all_generated if g is not None]
    n_new_success = len(new_successful)
    n_new_failed = n_total - n_new_success
    print(f"\nThis run: {n_new_success}/{n_total} tasks generated successfully, {n_new_failed} failed.")

    # checkpoint_tasks already contains existing_tasks + all newly succeeded tasks.
    all_task_dicts = checkpoint_tasks
    if args.resume is not None and existing_tasks:
        print(f"Merged: {len(existing_tasks)} existing + {n_new_success} new = {len(all_task_dicts)} total")

    if not all_task_dicts:
        print("No tasks generated. Skipping save.")
        sys.exit(1)

    config = TaskSetConfig(
        name=name,
        domain=args.domain,
        seed_source="medoids",
        num_tasks_to_generate=len(entries),
        length_distribution_config=LengthDistributionConfig.from_dict(
            load_validated_clusters(args.clusters).get("length_distribution") or {"mode": "from_seed"}
        ),
        model_name=args.model_name,
        backend="prepared",
    )

    manager = TaskSetManager(domain_config=domain_config)
    saved_path = manager.save(
        name=name,
        all_tasks=all_task_dicts,
        config=config,
        num_seed_tasks=0,
        num_generated_tasks=len(all_task_dicts),
    )
    print(f"Task set saved to: {saved_path}")

    generation_meta = {
        "name": name,
        "backend_spec": domain_config.to_spec(),
        "domain": args.domain,
        "model_name": args.model_name,
        "gt_llm": args.gt_llm,
        "gt_coverage_p": args.gt_coverage_p,
        "gt_coverage_shuffle": args.gt_coverage_shuffle,
        "seed_source": "medoids",
        "max_task_retries": args.max_task_retries,
        "total_sequences": len(entries),
        "successful_this_run": n_new_success,
        "failed_this_run": n_new_failed,
        "total_tasks_saved": len(all_task_dicts),
        "resumed": args.resume is not None,
        "created_at": datetime.now().isoformat(),
        "tasks": all_meta,
    }
    meta_path = os.path.join(task_set_dir, "generation_meta.json")
    with open(meta_path, "w") as f:
        json.dump(generation_meta, f, indent=2)
    print(f"Generation metadata saved to: {meta_path}")


if __name__ == "__main__":
    main()
