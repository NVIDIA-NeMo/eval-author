# Upper companions beside accepted source reviews

Use `rubric_base_assessment.py` for generation and retained replay. Both upper
companions are explicit orchestration inputs, not selected artifacts. The
Environment adapter creates a private overlay only for its owning validator;
the ordinary source-review, configuration, native and Audit consumers receive
the original selection unchanged. No accepted packet is rewritten or expanded.

```sh
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/Users/asutermorris/.hermes/cache/scratch
.venv/bin/python -B skills/eval-author-rubric/scripts/rubric_base_assessment.py \
  --root "$TMPDIR" \
  --selection "$TMPDIR/upper-combined-original-selection.json" \
  --registry "$HOME/.local/state/eval-author/native" \
  --source-review-operator-store "$TMPDIR/operator-source-qualification-01/operator-store" \
  --source-review-operator-trust localhost-assessment-operator-v1 \
  --upper-environment-bundle "$TMPDIR/upper-environment-native-03" \
  --upper-anticheat-bundle "$TMPDIR/upper-anticheat-contract/native-05" \
  --upper-anticheat-registry "$TMPDIR/upper-anticheat-contract/registry" \
  --output "$TMPDIR/upper-combined-ordinary-04"
```

Generation includes retained JSON/Markdown/HTML replay. For a later read-only
replay, replace `--selection` and `--output` with
`--verify-existing "$TMPDIR/upper-combined-ordinary-04/report"`; retain all explicit
companion and trust arguments. Exit 1 retains ordinary auxiliary unknown/status
limitations; it is not a replay failure (2). No command here launches native or
model execution. Missing companions cannot silently reproduce a saved combined
report: exact retained comparison fails.

Six Environment upper predicates consume current registered native evidence,
original lower-anchor decisions, and post-projection receipt/revocation readback.
Separately authored operational repeats are not blinded review or model diversity.
Fairness is limited to matched scripted reference/split policies and batching/order
accommodations; it carries E2, not a model-performance claim. Actual deadline
enforcement remains unknown. The declared offline ledger is a real local target,
not an approved replacement for a live payment provider.

The Anticheat/contract native companion remains frozen and observational. The
separate `rubric_upper_method_review.py` uses the existing explicit local-operator
store and event validation for exact-packet method qualification of AC-03, AC-04
and clear contract. Its trusted callback records only an actually observed scoped
answer and retention consent; no selected JSON or transcript is approval input.
The request binds task/rubric sources, native index, exposure and owning validated
observations. Every projection rereads the decision and revocation before and
after scoring. Owner qualification alone is insufficient: current lower anchors,
healthy consumer rejection/valid acceptance, exact reset surfaces and the valid
public-first boundary are checked separately. Reject, defer, missing, revoked or
failed evidence cannot promote. `revoke(request)` leaves an append-only tombstone.

The approved method discloses prior mechanism exposure, the same model and shared
Harbor/Docker/scorer. It excludes AC-01/AC-02 held-out requirements, blind
requirement discovery and scoring-authority adjudication. The separate fresh
review authorization is not evidence in this report. Source review approval is
unchanged and does not extend to either companion. Local operator trust is not
cryptographic authentication or protection against same-user arbitrary code.

Full inventories are retained once in measurement JSON. Exact repeated identity
values link to that inventory; the readable report links companion controls and
exposure. Reader caps are unchanged. Historical errors, dissent, receipts and
native rewards remain available without rewriting original accepted artifacts.
