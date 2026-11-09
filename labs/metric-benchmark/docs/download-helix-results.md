<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Native Helix collection boundary

This execution PR supplies the native evidence collector used by the downstream
`helix_collect.py` command. The command and standard-results import are implemented
together in the collection PR; users do not run a separate raw-download stage.
See the [architecture](helix-architecture.md) for the complete workflow.

The library checks all selected task jobs before transferring evidence, preserves
native artifacts and job specifications, records failed jobs as unavailable, and
publishes a raw collection atomically. Pending runs raise `NotReady`. Interrupted
transfers reuse verified cached archives. Native evidence remains unmodified;
only the downstream identity verifier can produce analysis-ready output.
