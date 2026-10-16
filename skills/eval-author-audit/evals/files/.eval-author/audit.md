# Audit: inventory-fixture

Generated from `../ETHOS.md`.

This file defines the finite coverage denominator for audit measurement.
Generated and hand-edited content is allowed outside the marked block; scripts
validate only the block between the markers.

<!-- BEGIN:nemo-eval-author-audit:v1 -->
```yaml
schema: nemo.eval_author.audit.v1
agent: inventory-fixture
sources:
  - name: ethos
    path: ../ETHOS.md
    sha256: sha256:d99b69fe02dfe671a4090cff61c18d58614579023e66a13d1f0dd07aca6f8e18
status: draft
items:
  - kind: tool
    name: lookup_item
    source_refs:
      - ethos:Tools
      - ethos:Behavior
    description: Look up one exact SKU.
    expected_use: Look up one exact SKU.
    expected_failure_behavior: Report missing data explicitly; do not invent records.
    evidence_required:
      - kind: tool_call
        tool: lookup_item
        description: The trace records this tool invocation.
  - kind: tool
    name: list_items
    source_refs:
      - ethos:Tools
      - ethos:Behavior
    description: List every item in the selected warehouse before calculating a total.
    expected_use: List every item in the selected warehouse before calculating a total.
    expected_failure_behavior: Report missing data explicitly; do not invent records.
    evidence_required:
      - kind: tool_call
        tool: list_items
        description: The trace records this tool invocation.
```
<!-- END:nemo-eval-author-audit:v1 -->
