# Current-base numeric mutation matrix

The consolidated runner assesses the supplied combined base once, executes shared physical cases through `rubric_base_assessment.py`, and reassesses the exact original selection once as the common repair. It does not require an all-3 base. It retains accepted operator decisions and explicit upper companions without changing their packets or native receipts.

Canonical definitions generate all 18 × 4 numeric cells. `expected` is external recipe input; `actual` is read from the ordinary report. Missing evidence is never converted to zero. The main `summary.md` joins base, case, criterion, expected, actual, reason and repair, with per-case generation/replay commands. `matrix.json` retains every criterion's collateral score/check/strength changes and unchanged neighbors.

## Run

From the repository root, choose a **new** scratch output directory:

```sh
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/Users/asutermorris/.hermes/cache/scratch \
.venv/bin/python -B skills/eval-author-rubric/scripts/rubric_mutation_runner.py \
  --base-report /Users/asutermorris/.hermes/cache/scratch/upper-combined-ordinary-04/report/assessment.json \
  --output /Users/asutermorris/.hermes/cache/scratch/consolidated-numeric-matrix-fresh \
  --native-numeric-bundle /Users/asutermorris/.hermes/cache/scratch/native-numeric-mutations-04 \
  --native-numeric-registry /Users/asutermorris/.local/state/eval-author/native \
  --registry /Users/asutermorris/.local/state/eval-author/native \
  --source-review-operator-store /Users/asutermorris/.hermes/cache/scratch/operator-source-qualification-01/operator-store \
  --source-review-operator-trust localhost-assessment-operator-v1 \
  --upper-environment-bundle /Users/asutermorris/.hermes/cache/scratch/upper-environment-native-03 \
  --upper-anticheat-bundle /Users/asutermorris/.hermes/cache/scratch/upper-anticheat-contract/native-05 \
  --upper-anticheat-registry /Users/asutermorris/.hermes/cache/scratch/upper-anticheat-contract/registry
```

For read-only current verification, replace `--base-report ... --output ...` with `--verify-matrix /absolute/output/matrix.json` and omit the native-numeric generation flags; preserve every base trust/registry/companion flag. Replay occurs once per distinct report, not per numeric cell. Individual reports can also be replayed with their owning recorded command.

The runner automatically materializes semantic task/subject copies, executes the ordinary assessment, validates native mutations through their owning registry/validator, and generates their ordinary reports. It never assembles score packets or collects new native trials. The native bundle is a required existing input, not a claim that this command reproduces native execution. A separate `-base` output retains the original runner's complete evidence.

For the faster validated-base path, replace `--base-report ...` with `--extend-matrix /Users/asutermorris/.hermes/cache/scratch/consolidated-numeric-matrix-01/matrix.json`. This reuses its case reports and exact repairs but performs current ordinary verification. Optionally pass `--native-numeric-reports /Users/asutermorris/.hermes/cache/scratch/native-numeric-reports-04` to reuse unchanged native reports **after** owning admission and before ordinary replay. Without that flag, the owning helper emits new reports from admitted raw evidence. Neither mode trusts an extension summary's selected score.

## Current verified deliverable

`/Users/asutermorris/.hermes/cache/scratch/consolidated-numeric-matrix-03/summary.md` and `matrix.json` are the consolidated result: all 72 numeric cells enumerated; 50 actual transitions, 18 actual-base cells and four deferred upper cells. All 54 lower numeric cells have emitted report and repair links. The base remains 14 scores of 3 and four scores of 2. The final batch replays 43 distinct reports, covering 39 physical mutation cases, the full-base baseline/repair and semantic source-only baseline/repair. All original 14 exact task repairs and collateral metadata remain linked. These are demonstrated numeric cells, not an all-3 qualified-base claim.

Fairness 0 consumes the actual parent-observed answer **“Yes—unjustified; retain this scoped judgment”** for `semantic-numeric-batch-02/owner-question.json`. The trusted callback records it outside selection via `record_decision`, with explicit retention scope. `semantic-fairness-actual-intake.json` retains the original question hash and proves current source/rule/comparison results equal the asked packet; the semantic implementation changed only for intake. No actor ID, session authentication or cryptographic provenance is invented. The store remains same-user mutable. `read_decision` and `revoke_decision` share the existing operator boundary and ordinary assessment rechecks them. Approve in this request means affirm **unjustified**, not approve fairness; no base or upper decision transfers.

## Evidence limits

- Scope controls retain entire admitted native panels. Explicit partial observations describe only a proper subset; they do not erase failed controls or alter receipt bytes.
- Inventory controls compare sourced claims with selected active entrypoints and configuration identities. Wrong scorer and material contradictory agent identity are not missing-evidence controls.
- Provider-incompatibility zero comes from the installed Harbor `TaskConfig` validator, not an invented native execution.
- Reachable shortcut zeros reconstruct a finite, full-closure admitted source variant and exercise the current guarded Ledger plus scorer on bounded data. Native probes remain unrun for this static-evidence route.
- Source mutants are fresh copies. Changed task bytes cannot inherit frozen native/review authority. Each exact byte/mode repair is retained separately; the common full-base repair restores the original task and selection. This is **not** native regrading of a mutant.
- `AC-01`, `AC-02`, discoverable requirements and scoring authority remain at 2 in the base pending independent upper gates. No tokens, models, native recollection or changed approval packets are needed for this runner.
- `hidden-completion` does not prove a hidden requirement when verifier source is allowed authority. Its expected zero remains an explicit mismatch rather than relabeling the ordinary Unassessed result.

The authoritative current results and exact unsupported criterion/state list are in the generated main summary, not this command guide. No broad test or lint sweep is required to execute the matrix.
