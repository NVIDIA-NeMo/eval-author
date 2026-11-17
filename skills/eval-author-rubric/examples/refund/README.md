<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Refund example

The base uses the refund contract and scorer from `evals/scorer-controls`.
The scorer retains its original bytes. The public contract now states that new
refunds must be positive and ledger entries may precede or follow refunds.
Required approval still precedes every refund. Refund defines amount,
entity, destination, approval order, preservation, and ledger obligations.
It supports split refunds and permitted inaction. Reuse avoids adding stock
and reservation rules before the evidence consumers are ready.

The target system is an offline ledger with a Unix-socket action interface.
It persists actions and state in a private record. The Harbor Oracle calculates
the balance and calls that interface. The verifier ignores solver-authored
submission files and compares the exported state with event replay.
The old event-file control panel remains available as a scorer regression.

This is a financial simulation. It neither moves money nor authenticates managers.
Business approval events are task data, not human rubric adjudication.
The base is not qualified at every rubric criterion. The completion gate remains
3 for every populated criterion. Local rewards do not establish canonical scores,
native permissions, human decisions, or independent corroboration.

## Prepare a selected package

Use Python 3.12 or 3.13. From the checkout root, run `uv sync --locked`.
Use new output directories under an existing private parent. Paths must have no
symlink components or `..`. Outputs cannot overlap inputs or the bundled example.

```bash
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$HOME/.hermes/cache/scratch"
OUT="$TMPDIR/refund-example"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  materialize --output "$OUT"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  verify --input "$OUT"
```

Give the assessment agent only `$OUT/assessment`. Use its unchanged `request.md`
for every case. Evaluation review includes the task, verifier, and Oracle source.
Keep this guide, mutation recipes, expectations, and generated `private/` files
outside that agent's context. Harbor must keep tests and solutions out of the
solver image. The image copies only the public contract and ledger implementation.

Host-directory separation is organizational, not a sandbox. Use one trusted
writer when creating outputs. Native solver-role tests must establish the actual
image, mount, file, process, and socket boundaries before access claims are scored.

## Run local controls

```bash
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  local-controls --input "$OUT" --output "$OUT-local"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  ledger-controls --input "$OUT" --output "$OUT-ledger"
```

`local-controls` runs 22 event-file cases through the actual scorer in Python
subprocesses. `ledger-controls` sends those cases through fresh service processes
and grades their persisted records. Each command admits only current bundled
source and checked-in recipes. Neither executes retained task code.
Neither command builds an image, invokes Docker, or calls a model.

The reference, split alternative, permitted zero-balance inaction, and optional
completion cases preserve their public credit rules. Negative cases cover wrong
amounts, entities, destinations, missing or late approval, duplicate or missing
ledger rows, and stale refund IDs. The two threshold cases distinguish the
inclusive threshold from a value immediately below it.

The action interface rejects protected-state edits, history replacement, unknown
actions, and diagnostic fault actions without applying them. The ledger panel
labels those five cases `input_rejected`. Its remaining-state reward does not
turn a rejected action into a successful behavioral control. The event-file
panel separately exercises protected-state and helper-fault scorer behavior.

Local tests also exercise the Oracle over the socket, trusted reset, rejection
without state changes, forged submission isolation, and state/export disagreement.
Missing or inconsistent records produce error health and no reward. The verifier
removes stale reward files before each evaluation. An error is not a rejection.

`local-results.json` and `ledger-results.json` record exit codes, checks, credit,
and source identities. Ledger results also retain action responses. They are
local observations, not authenticated native receipts. Ledger subprocesses use
the host user. These commands do not prove the Dockerfile's permission settings,
provider compatibility, or behavior under concurrent solver calls.

## Create and repair mutations

```bash
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  materialize --mutation completion-only --output "$OUT-bad"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  local-controls --input "$OUT-bad" --output "$OUT-bad-local"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  restore --input "$OUT-bad" --output "$OUT-repaired"
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  local-controls --input "$OUT-repaired" --output "$OUT-repaired-local"
```

| Mutation | Actual change | Required interpretation |
|---|---|---|
| `completion-only` | Selects the completion-only scorer | No-work completion earns forbidden credit. |
| `approval-after-effect` | Removes the approval-order condition | Late approval passes the mutated scorer. |
| `ledger-omission` | Removes ledger reconciliation | An unreconciled refund passes. |
| `hidden-completion` | Makes the optional signal mandatory | Valid work without that point fails. |
| `missing-contract` | Deletes the Dockerfile's contract input | Required task inspection and image construction are blocked. |
| `disabled-ledger` | Replaces service startup with sleep | The required socket has no startup producer. Native validation remains unrun. |
| `tax-drift` | Changes the public tax policy | Old solutions fail amount checks. A fresh policy-aware solution can remain valid. |

The local runners do not execute the Dockerfile. `ledger-controls` refuses a
changed Dockerfile rather than bypassing its startup defect. Do not use scorer-only
results to claim that `disabled-ledger` works or that a provider rejected it. The missing-contract case
cannot run controls. Neither an absent input nor changed tax policy is a universal
rubric zero. External expectations name exact target checks and these limits.

To remove actual reference evidence from the selected scope:

```bash
uv run --locked python skills/eval-author-rubric/scripts/example_fixtures.py \
  mutate-evidence --mutation withhold-reference --input "$OUT" \
  --evidence "$OUT-local" --output "$OUT-reduced"
```

This command replays trusted current controls before copying a reduced panel to
`$OUT-reduced/assessment`. It preserves the original panel and records lineage
outside the assessment inputs. Supply the reduced evidence instead of the full
panel. Missing reference evidence cannot establish a zero. Repair it by running
fresh controls into a new directory. Do not copy the removed result back as a new run.

Every task materialization marks source-bound evidence as unrun. Repair creates
fresh files and retains the prior task digest. Verification reconstructs the
expected bytes from bundled sources. Resealing a changed manifest cannot make
altered task bytes current. The prompt is fixed across all task mutations.

## Native AC-02 controls

Run the credential-free panel on local Docker after approval. Use the repository
`.venv/bin/python` for collection, selection, and replay. The exact interpreter
path forms part of each native command receipt.

```bash
export PYTHONDONTWRITEBYTECODE=1
export EVAL_AUTHOR_NATIVE_REGISTRY="$HOME/.local/state/eval-author/native"
# Choose a new private directory outside the task.
NATIVE="$HOME/refund-native-panel"
.venv/bin/python skills/eval-author-rubric/scripts/rubric_refund_panel.py \
  --output "$NATIVE" \
  --docker-cli-plugins /Applications/Docker.app/Contents/Resources/cli-plugins
```

The plugin option is for Docker Desktop on macOS. Omit it when Docker already
finds its Compose and Buildx plugins. The collector uses a private HOME and an
allowlisted environment. It does not forward model credentials or enable network
access. Keep selected sources unchanged until collection and replay finish.

The panel runs reference, split-payment, permitted inaction, wrong-answer,
no-work, all-failure, early-exit, inert-answer, forged/stale-output, and private
tamper controls as UID 65534. A declared zero-balance companion supplies legitimate
inaction. Other controls retain the base task bytes. Each control compares every
check and total with the existing credit declaration. Optional completion retains
its legitimate partial credit. Infrastructure errors remain unresolved controls.

Use `rubric_example_native.py` to add the admitted bundle to a normal task
selection. Pass a `--root` that contains both the task and bundle, then supply
root-relative `--task` and `--bundle` paths. Run `rubric_table.py --selection` and
its `--verify-existing` command with the same registry and interpreter. The registry
is private local-host admission, not independent authentication.

AC-02 can reach 2 only when every declared applicable attack and required valid
control matches its exact credit. This panel does not establish independent
held-out authorship, resolve unanswered business rules, or qualify other criteria.

## Qualification gaps

Harbor 0.24.0 parses the task and runs the AC-02 panel through Docker. Its Compose
keepalive arguments are handled by the service entrypoint. The declared image uses
a digest-pinned Python base and no-network agent/verifier phases. The panel verifies
solver UID 65534 and denied writes to private service state. It does not establish
complete image-layer confidentiality, predecessor isolation, or independent reset
coverage. The current report keeps those criteria separate from AC-02.

All-criterion 0/1/2 examples, human reviews, independent held-out controls,
configuration/comparison companions, and native receipt consumers remain incomplete.
This slice covers meaningful task, configuration, state, and evidence mutations.
It does not claim all-area qualification or the live review benefit comparison.

Authored prose received an STE80 review. The approved simulation-policy clarification
changes public task bytes and requires fresh evidence. Copied scorer comments,
commands, identifiers, and original control expectations retain their bytes.
