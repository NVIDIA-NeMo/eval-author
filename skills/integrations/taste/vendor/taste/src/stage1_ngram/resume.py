# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify and replay immutable Stage 1 journals before resuming inference."""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

RESUME_ARGUMENTS = {"save_dir", "resume_checkpoint", "resume_journal", "resume_rng_seed"}


def file_binding(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest()}


def check_binding(binding):
    if file_binding(binding["path"]) != binding:
        raise ValueError(f"Resume source changed: {binding['path']}")


def _rows(path):
    with Path(path).open() as stream:
        for index, line in enumerate(stream):
            try:
                row = json.loads(line)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Incomplete or malformed journal row {index}: {path}") from error
            if row.get("schema") != "taste.sequence-evidence.v1" or row.get("index") != index:
                raise ValueError(f"Journal schema/index mismatch at {index}: {path}")
            yield row


def _first(path):
    try:
        return next(_rows(path))
    except StopIteration as error:
        raise ValueError("Cannot resume an empty journal") from error


def _core(state):
    state = json.loads(json.dumps(state))
    state["sampler"].pop("rng_state", None)
    for key in ("accepted_set", "rejected_set"):
        state["sampler"][key] = sorted(state["sampler"].get(key, []))
    return state


def _decision(value, sequence):
    if not isinstance(value, dict) or type(value.get("valid")) is not bool:
        raise ValueError("Journal decision must contain a Boolean verdict")
    indices = value.get("problematic_indices")
    if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(sequence) for i in indices):
        raise ValueError("Journal decision has invalid problematic indices")
    if not isinstance(value.get("reason"), str):
        raise ValueError("Journal decision must retain its reason")
    return {key: value[key] for key in ("valid", "reason", "problematic_indices")}


def _draw_identity(row):
    return {key: row[key] for key in ("sample_index", "t", "sequence", "duplicate_status", "temperature")}


@dataclass
class ResumeState:
    parents: list
    checkpoint: dict
    completed: int
    accepted: int
    rejected: int
    valid_seeds: int
    invalid_seeds: int
    skipped_seeds: int
    pending: dict | None
    pending_decision: dict | None
    rng_mode: str
    rng_seed: int | None

    def payload(self):
        return asdict(self)


def prepare_resume(args, sampler, seed_tasks, backend_spec):
    """Replay judgments, never requests; reject drift before creating output."""
    checkpoint_binding = file_binding(args.resume_checkpoint)
    checkpoint = json.loads(Path(checkpoint_binding["path"]).read_text())
    target = checkpoint["sampler"]["t"]
    if type(target) is not int or not 0 <= target <= args.num_samples:
        raise ValueError("Resume checkpoint has an invalid training counter")
    direct = file_binding(args.resume_journal)
    first = _first(direct["path"])
    parents = first.get("parents", []) if first["event"] == "stage1_resumed" else []
    if not isinstance(parents, list) or len(parents) > 100:
        raise ValueError("Invalid resume journal ancestry")
    parents = [*parents, direct]
    if len({item["path"] for item in parents}) != len(parents):
        raise ValueError("Cyclic or duplicate resume ancestry")
    for binding in parents:
        check_binding(binding)

    completed = accepted = rejected = valid = invalid = skipped = seed_index = 0
    active = validation = decision = pending = pending_decision = None
    latest_rng = None
    checkpoint_verified = False
    original_arguments = None
    seeds_seen = False
    at_segment_start = False

    def verify_checkpoint():
        nonlocal checkpoint_verified
        if sampler.t == target and seed_index == len(seed_tasks):
            if _core(sampler._serialize_state()) != _core(checkpoint):
                raise ValueError("Checkpoint state does not equal its journal's recorded ingestion")
            checkpoint_verified = True

    for segment_index, binding in enumerate(parents):
        at_segment_start = True
        for row in _rows(binding["path"]):
            event = row["event"]
            if row["index"] == 0:
                if segment_index == 0:
                    if event != "stage1_started":
                        raise ValueError("Resume ancestry must begin with the original Stage 1 journal")
                    original_arguments = row["arguments"]
                else:
                    if event != "stage1_resumed" or row.get("parents") != parents[:segment_index]:
                        raise ValueError("Resume journal does not bind its exact ancestor chain")
                    if row.get("completed") != completed or row.get("pending") != pending:
                        raise ValueError("Resume boundary disagrees with its parent journal")
                    if row.get("pending_decision") != pending_decision:
                        raise ValueError("Resume boundary changed a retained pending verdict")
                    latest_rng = row.get("rng_state")
                actual = {k: v for k, v in vars(args).items() if k not in RESUME_ARGUMENTS}
                if not isinstance(original_arguments, dict):
                    raise ValueError("Resume ancestry has no original training arguments")
                expected = {k: v for k, v in original_arguments.items() if k not in RESUME_ARGUMENTS}
                if actual != expected or row.get("backend_spec") != backend_spec:
                    raise ValueError("Resume training arguments or prepared resource digests changed")
                continue
            if event == "stage1_seeds":
                if seeds_seen or row["seeds"] != [asdict(seed) for seed in seed_tasks]:
                    raise ValueError("Resume seed records changed or were repeated")
                seeds_seen = True
                verify_checkpoint()
            elif event == "checkpoint_saved":
                if row["checkpoint"]["path"] == checkpoint_binding["path"]:
                    if row["checkpoint"] != checkpoint_binding or row["t"] != target:
                        raise ValueError("Selected checkpoint bytes differ from their journal binding")
                if row["t"] != completed:
                    raise ValueError("Checkpoint journal counter differs from completed draws")
            elif event == "seed_started":
                if not seeds_seen or active is not None or row["seed_index"] != seed_index:
                    raise ValueError("Seed journal order is invalid")
                if (
                    row["sequence"] != sampler._pending_seeds[seed_index]
                    or row["provenance"] != sampler.seed_provenance[seed_index]
                ):
                    raise ValueError("Seed journal differs from the prepared seed")
                active, validation, decision = row, None, None
            elif event == "seed_skipped":
                if (
                    active is None
                    or active["sequence"]
                    or row["seed_index"] != seed_index
                    or row.get("model_called") is not False
                ):
                    raise ValueError("Only the current empty seed may be skipped")
                skipped += 1
                seed_index += 1
                active = None
                verify_checkpoint()
            elif event == "draw_started":
                if seed_index != len(seed_tasks) or row["sample_index"] != completed + 1 or row["t"] != completed:
                    raise ValueError("Draw journal counter/order is invalid")
                if active is not None:
                    if not at_segment_start or pending != _draw_identity(row):
                        raise ValueError("A pending draw was replaced instead of resumed")
                seq = row["sequence"]
                key = tuple(seq)
                duplicate = (
                    "accepted_dup"
                    if key in sampler.accepted_set
                    else "rejected_dup"
                    if key in sampler.rejected_set
                    else "new"
                )
                if row["duplicate_status"] != duplicate or row["temperature"] != sampler.current_temperature:
                    raise ValueError("Draw duplicate status or temperature differs from restored learning")
                if any(action not in sampler.available_actions for action in seq):
                    raise ValueError("Draw contains actions outside the prepared vocabulary")
                active, validation, decision = row, None, None
                pending = _draw_identity(row)
                pending_decision = None
                latest_rng = row.get("rng_state")
                at_segment_start = False
            elif event == "validation_started":
                if active is None or row["sequence"] != active["sequence"] or validation is not None:
                    raise ValueError("Validation does not belong to the active seed/draw")
                validation = row["index"]
            elif event == "validation_decision":
                if (
                    active is None
                    or row.get("validation_id") != validation
                    or validation is None
                    or decision is not None
                ):
                    raise ValueError("Validation decision has no unique matching request")
                decision = _decision(row["decision"], active["sequence"])
                decision["evidence_id"] = row["index"]
                if active["event"] == "draw_started":
                    pending_decision = {"decision": decision, "journal": binding, "index": row["index"]}
            elif event in ("seed_completed", "draw_completed"):
                if (
                    active is None
                    or decision is None
                    or row["sequence"] != active["sequence"]
                    or row["decision"] != decision
                ):
                    raise ValueError("Completed ingestion lacks its exact validation decision")
                seq = row["sequence"]
                positive = decision["valid"]
                indices = decision["problematic_indices"]
                if row["ingested"] != bool(positive or (sampler._ingest_negatives and indices)) or row["weight"] != (
                    sampler._positive_weight if positive else sampler._negative_weight
                ):
                    raise ValueError("Recorded feedback differs from the training policy")
                if event == "seed_completed":
                    if active["event"] != "seed_started" or row["seed_index"] != seed_index:
                        raise ValueError("Seed completion order is invalid")
                    if positive:
                        sampler.model.ingest_sequence(seq, weight=sampler._positive_weight, positive=True)
                        sampler.accepted_set.add(tuple(seq))
                        sampler.action_sequence_set.append(seq)
                        valid += 1
                    else:
                        invalid += 1
                        if sampler._ingest_negatives and indices:
                            sampler.model.ingest_negative_windows_at_indices(
                                seq, indices, weight=sampler._negative_weight
                            )
                    seed_index += 1
                else:
                    if (
                        active["event"] != "draw_started"
                        or row["sample_index"] != completed + 1
                        or row["t"] != completed + 1
                    ):
                        raise ValueError("Draw completion order is invalid")
                    sampler.ingest(seq, positive=positive, problematic_indices=indices if not positive else None)
                    completed += 1
                    accepted += int(positive)
                    rejected += int(not positive)
                    latest_rng = row.get("rng_state", latest_rng)
                    pending = pending_decision = None
                active = validation = decision = None
                verify_checkpoint()
            elif event == "stage1_completed":
                raise ValueError("Stage 1 already completed; use its final checkpoint")
        check_binding(binding)
    if not checkpoint_verified or seed_index != len(seed_tasks) or completed > args.num_samples:
        raise ValueError("No verified post-seed checkpoint boundary exists in the journals")
    if active is not None and active["event"] != "draw_started":
        raise ValueError("Resume during seed validation is unsupported; retain evidence and restart separately")
    if latest_rng is None and completed == target and pending is None:
        latest_rng = checkpoint["sampler"].get("rng_state")
    if latest_rng is None:
        if args.resume_rng_seed is None:
            raise ValueError("Legacy journal lacks RNG state; explicitly supply --resume-rng-seed")
        sampler._rng.seed(args.resume_rng_seed)
        np.random.seed(args.resume_rng_seed)
        rng_mode = "new_recorded_boundary_after_legacy_interruption"
    else:
        if args.resume_rng_seed is not None:
            raise ValueError("An exact RNG boundary exists; --resume-rng-seed would replace it")
        sampler.restore_rng_state(latest_rng)
        rng_mode = "restored_recorded_boundary"
    check_binding(checkpoint_binding)
    return ResumeState(
        parents,
        checkpoint_binding,
        completed,
        accepted,
        rejected,
        valid,
        invalid,
        skipped,
        pending,
        pending_decision,
        rng_mode,
        args.resume_rng_seed,
    )
