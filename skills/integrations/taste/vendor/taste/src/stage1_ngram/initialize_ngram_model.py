#!/usr/bin/env python3
"""Stage 1: train an n-gram action sequence model.

Loads seed tasks for the chosen domain, validates them with an LLM, then runs
the sampler to generate, validate and ingest new sequences. Writes JSON
checkpoints into the run directory at four points (``pre_seed``, ``post_seed``,
periodic every ``--save-every`` samples, and ``final``), plus a descriptive
copy of the final state into ``artifacts/ngram/checkpoints/`` for downstream
stages to consume.

Example:
    python -m src.stage1_ngram.initialize_ngram_model \\
        --backend-config backend.json --num-samples 500
"""

import argparse
import os
from dataclasses import asdict
from pathlib import Path

from src.common.sampler import LengthDistributionConfig
from src.common.sequence_evidence import SequenceEvidence, validation_payload
from src.stage1_ngram.resume import file_binding, prepare_resume


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Train an n-gram action sequence model with periodic checkpoints",
    )
    parser.add_argument("--backend-config", required=True, help="JSON prepared input configuration")
    parser.add_argument(
        "--seed-tasks",
        type=str,
        default=None,
        help="Path to normalized seed records to use instead of the configured seeds",
    )
    parser.add_argument(
        "--length-config",
        default=None,
        help="JSON LengthDistributionConfig; overrides the default from_seed policy",
    )
    parser.add_argument("--num-samples", type=int, default=3000, help="Total samples to run (default: 3000)")
    parser.add_argument("--save-every", type=int, default=400, help="Checkpoint interval (default: 400)")
    parser.add_argument(
        "--save-dir",
        type=str,
        default=None,
        help="Directory for checkpoints (default: artifacts/ngram/training/<domain>[_no_neg]/)",
    )
    parser.add_argument(
        "--validation-model",
        default="gemini/gemini-3-flash-preview",
        help="LLM model for validation",
    )
    parser.add_argument("--max-output-tokens", type=int, default=8192, help="Max tokens for validator LLM response")

    ngram = parser.add_argument_group("NGram model parameters")
    ngram.add_argument("--n", type=int, default=3, help="N-gram order (default: 3)")
    ngram.add_argument("--alpha0", type=float, default=0.1, help="Dirichlet prior (default: 0.1)")
    ngram.add_argument("--lambda-neg", type=float, default=1.0, help="Negative penalty strength (default: 1.0)")
    ngram.add_argument("--tau0", type=float, default=3.0, help="Initial temperature (default: 2.0)")
    ngram.add_argument("--tau-decay-steps", type=float, default=1500.0, help="Temperature decay steps (default: 1500)")
    ngram.add_argument(
        "--negative-weight", type=float, default=1.0, help="Weight for negative ingestion (default: 1.0)"
    )
    ngram.add_argument(
        "--no-ingest-negatives", action="store_true", help="Do not ingest invalid sequences as negatives"
    )

    parser.add_argument("--resume-checkpoint", help="Checkpoint from an interrupted post-seed training run")
    parser.add_argument("--resume-journal", help="Immutable events.jsonl belonging to the interrupted run")
    parser.add_argument("--resume-rng-seed", type=int, help="Explicit new RNG boundary for legacy journals only")
    args = parser.parse_args(argv)
    if bool(args.resume_checkpoint) != bool(args.resume_journal):
        parser.error("--resume-checkpoint and --resume-journal must be supplied together")
    if args.resume_rng_seed is not None and not args.resume_checkpoint:
        parser.error("--resume-rng-seed requires a resume source")
    if args.resume_rng_seed is not None and not 0 <= args.resume_rng_seed < 2**32:
        parser.error("--resume-rng-seed must be in [0, 2**32)")
    if args.num_samples < 0 or args.save_every < 1:
        parser.error("num-samples must be nonnegative and save-every positive")
    if args.resume_checkpoint and not args.save_dir:
        parser.error("Resume requires an explicit fresh --save-dir")
    return args


def main():
    args = parse_args()

    from src.common.domain_config import DomainConfig
    from src.common.sampler import ActionSequenceNGramModelSampler
    from src.common.sampler.length_distribution import load_reference_tasks
    from src.stage2_cluster.action_sequence_validator import ActionSequenceValidator

    domain_config = DomainConfig.from_file(args.backend_config)
    domain = args.domain = domain_config.domain

    if args.save_dir is None:
        sub = args.domain
        if args.no_ingest_negatives:
            sub += "_no_neg"
        args.save_dir = os.path.join("artifacts", "ngram", "training", sub)
    run_dir = args.save_dir
    if args.resume_checkpoint and Path(run_dir).exists():
        raise ValueError("Resume output directory must be new")
    no_neg_suffix = "_no_neg" if args.no_ingest_negatives else ""
    checkpoints_dir = os.path.join(os.environ.get("TASTE_WORKSPACE", os.getcwd()), "artifacts", "ngram", "checkpoints")
    artifact_name = f"ngram_checkpoint_{domain}{no_neg_suffix}_{args.num_samples}samples.json"
    artifact_path = os.path.join(checkpoints_dir, artifact_name)
    if Path(artifact_path).exists():
        raise ValueError("Final checkpoint destination already exists; use a fresh TASTE_WORKSPACE")

    if args.seed_tasks:
        print(f"\nLoading seed tasks from file: {args.seed_tasks}")
        domain_config.options["seed_path"] = os.path.abspath(args.seed_tasks)
        seed_tasks = domain_config.seeds()
    else:
        print(f"\nLoading seed tasks for domain '{args.domain}'...")
        seed_tasks = domain_config.seeds()
    print(f"Loaded {len(seed_tasks)} seed tasks")

    length_config = (
        LengthDistributionConfig.from_json(args.length_config)
        if args.length_config
        else LengthDistributionConfig(mode="from_seed", reference_source="original")
    )
    reference_tasks = load_reference_tasks(length_config, domain_config)

    sampler = ActionSequenceNGramModelSampler(
        action_sequence_set=seed_tasks,
        backend_spec=domain_config.to_spec(),
        length_policy_explicit=args.length_config is not None,
        length_distribution_config=length_config,
        reference_tasks=reference_tasks,
        domain=args.domain,
        n=args.n,
        alpha0=args.alpha0,
        lambda_neg=args.lambda_neg,
        tau0=args.tau0,
        tau_decay_steps=args.tau_decay_steps,
        negative_weight=args.negative_weight,
        ingest_negatives=not args.no_ingest_negatives,
        tool_spec_path=domain_config.tool_spec_path,
    )

    backend_spec = domain_config.to_spec()
    resume = prepare_resume(args, sampler, seed_tasks, backend_spec) if args.resume_checkpoint else None
    DomainConfig.from_spec(backend_spec)
    os.makedirs(run_dir, exist_ok=not bool(resume))
    evidence = SequenceEvidence(os.path.join(run_dir, "evidence"))
    if resume:
        evidence.record(
            "stage1_resumed",
            arguments=vars(args),
            backend_spec=backend_spec,
            rng_state=sampler.get_rng_state(),
            **resume.payload(),
        )
    else:
        evidence.record("stage1_started", arguments=vars(args), backend_spec=domain_config.to_spec())
        evidence.record("stage1_seeds", seeds=[asdict(seed) for seed in seed_tasks])

    validator = ActionSequenceValidator(
        domain=domain,
        model_name=args.validation_model,
        max_output_tokens=args.max_output_tokens,
        domain_config=domain_config,
        evidence=evidence,
    )

    def save_checkpoint(path):
        destination = Path(path)
        if destination.exists():
            raise ValueError(f"Checkpoint destination already exists: {destination}")
        temporary = destination.with_name(destination.name + ".pending")
        sampler.save_state(str(temporary))
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        evidence.record("checkpoint_saved", checkpoint=file_binding(destination), t=sampler.t)

    if resume:
        valid, invalid, skipped = resume.valid_seeds, resume.invalid_seeds, resume.skipped_seeds
        save_checkpoint(os.path.join(run_dir, "resume_boundary.json"))
    else:
        pre_seed_path = os.path.join(run_dir, "pre_seed.json")
        save_checkpoint(pre_seed_path)
        print(f"  >>> Checkpoint saved: {pre_seed_path}  [pre_seed]")

        pending = sampler._pending_seeds
        print(f"\nValidating {len(pending)} seed action sequences...")
        valid = invalid = skipped = 0
        for i, seq in enumerate(pending):
            evidence.record("seed_started", seed_index=i, sequence=seq, provenance=sampler.seed_provenance[i])
            if not seq:
                skipped += 1
                evidence.record("seed_skipped", seed_index=i, reason="empty_reference", model_called=False)
                continue
            result = validator.validate(seq)
            if result.valid:
                sampler.model.ingest_sequence(seq, weight=sampler._positive_weight, positive=True)
                sampler.accepted_set.add(tuple(seq))
                sampler.action_sequence_set.append(seq)
                valid += 1
            else:
                invalid += 1
                if sampler._ingest_negatives and result.problematic_indices:
                    sampler.model.ingest_negative_windows_at_indices(
                        seq,
                        result.problematic_indices,
                        weight=sampler._negative_weight,
                    )
            evidence.record(
                "seed_completed",
                seed_index=i,
                sequence=seq,
                decision=validation_payload(result),
                ingested=bool(result.valid or (sampler._ingest_negatives and result.problematic_indices)),
                weight=sampler._positive_weight if result.valid else sampler._negative_weight,
            )
            if (i + 1) % max(1, len(pending) // 5) == 0:
                print(f"  Validated [{i + 1}/{len(pending)}] Valid: {valid}  Invalid: {invalid}  Skipped: {skipped}")
        print(f"\n  Valid: {valid}  Invalid: {invalid}  Skipped: {skipped}")

        post_seed_path = os.path.join(run_dir, "post_seed.json")
        save_checkpoint(post_seed_path)
        print(f"  >>> Checkpoint saved: {post_seed_path}  [post_seed]")

    print(f"\nTraining for {args.num_samples} samples, saving every {args.save_every}")
    print(f"  Validation model: {args.validation_model}")
    print(f"  n={args.n}  alpha0={args.alpha0}  lambda_neg={args.lambda_neg}  tau0={args.tau0}")
    print()

    accepted_total = resume.accepted if resume else 0
    rejected_total = resume.rejected if resume else 0

    # Per-window counters (reset every save_every).
    w_accepted = 0
    w_rejected = 0
    window_start = sampler.t + 1
    window_stats: list = []

    for i in range(sampler.t + 1, args.num_samples + 1):
        pending = resume.pending if resume and i == resume.completed + 1 else None
        if pending:
            action_sequence, duplicate_status = pending["sequence"], pending["duplicate_status"]
        else:
            action_sequence, duplicate_status = sampler.get_new_action_sequence()
        tau = sampler.current_temperature
        evidence.record(
            "draw_started",
            sample_index=i,
            t=sampler.t,
            sequence=action_sequence,
            duplicate_status=duplicate_status,
            temperature=tau,
            rng_state=sampler.get_rng_state(),
        )

        if pending and resume is not None and resume.pending_decision:
            from src.stage2_cluster.action_sequence_validator import ValidationResult

            retained = resume.pending_decision
            validation_id = evidence.record("validation_started", sequence=action_sequence)
            payload = dict(retained["decision"])
            payload["evidence_id"] = None
            decision_id = evidence.record(
                "validation_decision",
                validation_id=validation_id,
                decision=payload,
                model_called=False,
                recovered_from=retained,
            )
            payload["evidence_id"] = decision_id
            result = ValidationResult(**payload)
        else:
            result = validator.validate(action_sequence)
        sampler.ingest(
            action_sequence,
            positive=result.valid,
            problematic_indices=result.problematic_indices if not result.valid else None,
        )
        evidence.record(
            "draw_completed",
            sample_index=i,
            t=sampler.t,
            sequence=action_sequence,
            decision=validation_payload(result),
            ingested=bool(result.valid or (sampler._ingest_negatives and result.problematic_indices)),
            weight=sampler._positive_weight if result.valid else sampler._negative_weight,
            rng_state=sampler.get_rng_state(),
        )

        if result.valid:
            accepted_total += 1
            w_accepted += 1
        else:
            rejected_total += 1
            w_rejected += 1

        label = "VALID" if result.valid else "INVLD"
        print(
            f"  [{sampler.t:>5}/{args.num_samples}]  {label}  tau={tau:.3f}  accepted={accepted_total}  len={len(action_sequence)}"
        )

        if i % args.save_every == 0:
            w_attempted = w_accepted + w_rejected
            window_stats.append(
                {
                    "window_index": len(window_stats),
                    "sample_start": window_start,
                    "sample_end": i,
                    "attempted": w_attempted,
                    "accepted": w_accepted,
                    "rejected": w_rejected,
                    "acceptance_rate": w_accepted / w_attempted if w_attempted > 0 else None,
                }
            )

            filename = sampler.checkpoint_filename()
            path = os.path.join(run_dir, filename)
            save_checkpoint(path)
            print(f"  >>> Checkpoint saved: {path}  [periodic]")

            w_accepted = 0
            w_rejected = 0
            window_start = i + 1

    # Capture any partial window that didn't hit save_every.
    if w_accepted + w_rejected > 0:
        w_attempted = w_accepted + w_rejected
        window_stats.append(
            {
                "window_index": len(window_stats),
                "sample_start": window_start,
                "sample_end": args.num_samples,
                "attempted": w_attempted,
                "accepted": w_accepted,
                "rejected": w_rejected,
                "acceptance_rate": w_accepted / w_attempted if w_attempted > 0 else None,
            }
        )

    filename = sampler.checkpoint_filename()
    name, ext = os.path.splitext(filename)
    final_filename = f"{name}_final{ext}"
    path = os.path.join(run_dir, final_filename)
    save_checkpoint(path)
    print(f"  >>> Checkpoint saved: {path}  [final]")

    # Also save into artifacts/ngram/checkpoints/ with a descriptive name as
    # the convenience handle for src.stage2_cluster.cluster_and_validate.
    os.makedirs(checkpoints_dir, exist_ok=True)
    save_checkpoint(artifact_path)
    evidence.record(
        "stage1_completed",
        final_checkpoint=os.path.abspath(artifact_path),
        accepted_draws=accepted_total,
        rejected_draws=rejected_total,
        valid_seeds=valid,
        invalid_seeds=invalid,
        skipped_seeds=skipped,
        t=sampler.t,
    )
    print(f"  >>> Checkpoint saved: {artifact_path}  [artifact]")

    print(f"\nFinal state saved:  {path}")
    print(f"  Artifact:         {artifact_path}")
    print(f"  Target accepted:  {args.num_samples}")
    print(f"  Accepted (total): {len(sampler.accepted_set)}")
    print(f"  Accepted (run):   {accepted_total}")
    print(f"  Rejected:         {rejected_total}")
    overall_attempted = accepted_total + rejected_total
    if overall_attempted > 0:
        print(f"  Overall acc rate: {accepted_total / overall_attempted:.1%}")
    print(f"  Final t:          {sampler.t}")
    print()
    if window_stats:
        print("  Per-window acceptance rates:")
        for w in window_stats:
            rate = f"{w['acceptance_rate']:.1%}" if w["acceptance_rate"] is not None else "n/a"
            print(
                f"    [{w['sample_start']:>5}–{w['sample_end']:<5}]  accepted={w['accepted']}  rejected={w['rejected']}  rate={rate}"
            )
    print(
        f"\n  Next step: python -m src.stage2_cluster.cluster_and_validate --checkpoint {artifact_path} -k <num_clusters>"
    )
    print()


if __name__ == "__main__":
    main()
