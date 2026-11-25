<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Use the `eval-author-task-create` skill in `/workspace` to author one Harbor task
that closes the audit gap below. Journey state is already seeded for the
`expand` journey. Do not ask questions; record a pending question in journey
state instead.

- Tool: `{{tool_name}}`
- Task slug: `{{task_slug}}`
- Proposal: `{{proposal_path}}`
- Draft: `{{draft_path}}`
- Measurements: `{{measurements_path}}`
- Gap report: `{{report_path}}`
- Harbor Python: `{{harbor_python}}`
- Agent under test: `{{agent_id}}`
- Wrong-answer agent: `{{wrong_answer_agent}}`
- Wrong-answer script: `{{wrong_answer_script}}`
- Authorizations: `{{authorizations}}`

Run `task_evidence.py` with the Harbor Python. Write the wrong answer as a
nonempty script at the wrong-answer script path under the draft, and pass the
wrong-answer agent as `-a` for the incorrect control. Keep manifests, receipts,
trials, measurements, and the verification JSON under the measurements path.
Record the task with `journey_state.py record-task`: `accepted` only when
`task_pipeline.py verify` accepts two closure runs, otherwise `draft`.
