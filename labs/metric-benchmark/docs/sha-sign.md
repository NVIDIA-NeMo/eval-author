<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# sha-sign

Sign or verify the prepared inputs referenced by a benchmark YAML file.

Iterate with SHA fields omitted. When the prepared inputs are ready, sign the
bundle from the repository root:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml
```

`sha-sign` reads the experiment and walks every case's referenced manifests and
inputs. It checks paths, file types, schemas and subset task IDs before writing
anything. It adds `sha256` to task sources, agent inputs, descriptions, subsets,
and agent/tasks manifest references, working from input files up to the
experiment. Shared manifests are updated once. It does not run agents or the
metric tool, and it does not modify task files, agent files or JSON inputs.

If any reference already has a SHA field, signing stops without writing files
and lists those fields' locations. Delete those fields, or explicitly re-sign
the current contents with `--resign`:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml --resign
```

`--resign` replaces old pins, including ones that no longer match edited inputs.
Signing edits only SHA fields, preserving comments, key order, quoting and
formatting elsewhere. New SHA fields are inserted into their reference mapping;
existing pins keep their plain or quoted scalar style. Block-scalar, anchored or
tagged SHA values must be changed to plain or quoted scalars before re-signing.
The input files should not be edited while signing. Inputs cannot include
manifests that the signer itself rewrites, since doing so would invalidate their
hashes.

Once signed, readers verify each supplied digest as they load its reference.
`load_benchmark()` verifies all inputs; `read_benchmark()` verifies the manifests
it reads, and individual artifact loaders verify their own inputs. Omitting a
digest requires explicit `allow_unsigned=True` when loading during iteration.
SHA signing fixes expected contents; it does not authenticate a publisher.

To check that the entire benchmark is signed correctly without changing files:

```bash
uv run labs/metric-benchmark/tools/sha-sign benchmark/experiment.yml --check
```

`--check` requires a SHA on every input reference and verifies it against the
current contents. It stops at the first validation error, including a missing,
null or incorrect digest, and reports the input context. It uses the same strict
`load_benchmark()` path as normal full loading. To fill missing pins throughout
an unsigned bundle, run signing without `--check`; use `--resign` if some pins
already exist. Success exits with status zero; validation failures exit nonzero.
`--check` and `--resign` are mutually exclusive.
The top-level experiment has no self-hash, and inline cases have no separate
hash: their agent, tasks and subsets references are checked instead.

To sign or check from Python:

```python
from pathlib import Path
from metric_benchmark.signing import check_benchmark, sign_benchmark

experiment = Path("benchmark/experiment.yml")
changed_files = sign_benchmark(experiment, resign=False)
check_benchmark(experiment)
```

`sign_benchmark()` returns the paths of YAML files it changed.
`check_benchmark()` returns normally on success and raises on missing or invalid
pins or inputs. Re-signing does not require existing pins to match.
