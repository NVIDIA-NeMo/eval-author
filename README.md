<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NeMo Eval Author

![Status: Research Preview](https://img.shields.io/badge/Status-Research%20Preview-orange)

Turning your agent's intended behavior into representative custom evaluation cases is
hard. Each case needs checks that distinguish success from failure, and passing
cases can still leave important behavior untested.

NeMo Eval Author is a set of tools that guides your coding assistant to help you create evaluations
and investigate gaps in behavior coverage for your custom evals. It provides skills that work with
your agent's repository, its intended behavior, and any available execution traces
(records of the steps your agent took). You can start without traces: build a
first evaluation suite, audit existing evaluations, or plan new eval cases with your
coding assistant.

For example, the results of an audit might report:

> **Recovery from rejected tool arguments lacks trace evidence.** The agent is
> expected to correct an invalid argument, but the measured traces do not cover
> that behavior. This does not establish that a recovery test is missing. Review
> the existing cases to decide whether to add a test or collect more evidence.


> [!IMPORTANT]
> **Research preview**
>
> NeMo Eval Author is an early-stage applied research project.
> Workflows and interfaces may change. Review generated cases, graders (the
> checks that score an agent's work), and coverage findings before relying on
> their results.

See [known issues](KNOWN_ISSUES.md) for current limitations and practical guidance
on task difficulty and authoring prompts.

# Set up
## Prerequisites

NeMo Eval Author has tested support for [NeMo Gym](https://docs.nvidia.com/nemo/gym/latest/) and
[Harbor](https://docs.harborframework.com/) eval formats. Workflows default to Harbor when neither is specified. 
Gym is compatible with Harbor, and the user has the final say. 

Gym authoring needs Gym v0.6.0+ in its separate Python 3.13.14+ environment; Harbor authoring needs its
CLI and supported Python environment. See the [first-eval guide](docs/first-evals.md)
and [Harbor setup guide](skills/eval-author/references/harbor-setup.md).
Repository inventory and case planning can start without either runtime.

First-evaluation design and coverage audits require a local **ETHOS.md file** describing
your agent's purpose, boundaries, and success criteria. Install the
[ethos skills](team-skills/ethos/ethos-orchestrate/README.md) to create one:
`npx skills add https://github.com/NVIDIA-NeMo/eval-author/tree/main/team-skills/ethos --agent claude-code`.

The discovery helper requires Python 3.11+. Audit generation, validation, and
aggregation require Python 3.11+, PyYAML, and jsonschema. Measuring coverage from
ATIF traces requires Python 3.12+ and the
[audit dependencies](skills/eval-author-audit/requirements.txt), including Harbor.

Task execution may need Docker, application access, and model credentials,
depending on the task and agent. See the
[full requirements](docs/getting-started.md#requirements)
for each workflow, including local trace conversion and experimental
environment preparation.

## Start here

To use NeMo Eval Author, you need a coding assistant that can read and write project files and run shell
commands, [Node.js 22.20+](https://nodejs.org/en/download) (including npm and
`npx`), and [Git](https://git-scm.com/downloads). In a terminal, change to the
root of the repository containing **your agent**, then install for your assistant:

```bash
cd /path/to/your-agent-repository
npx skills add NVIDIA-NeMo/eval-author --skill '*' --agent claude-code --yes
```

Replace `claude-code` with your assistant's identifier:

| Coding assistant | `--agent` value |
| --- | --- |
| Claude Code | `claude-code` |
| Codex | `codex` |
| Cursor | `cursor` |
| Pi | `pi` |

See the [Skills CLI documentation](https://github.com/vercel-labs/skills#supported-agents)
for other assistants.

Installation adds ten skill directories with their instructions, scripts,
schemas, templates, and references. Harbor and Python dependencies are separate
[workflow requirements](docs/getting-started.md#requirements).

Verify the installation from the same directory, using the same assistant value:

```bash
npx skills list --agent claude-code
```

For a global installation, add `--global` to this check too. Look for `eval-author`
and its supporting skills. The CLI may show other assistants that share a skills
directory; it has not installed those assistants. See the
[installation guide](docs/getting-started.md#install-eval-author-with-npx)
for scope, file locations, and troubleshooting.

To install without Node.js, follow the
[manual installation steps](docs/getting-started.md#manual-installation-without-nodejs).

Open your agent's repository in a new session of the selected coding assistant
and ask:

```text
Use eval-author to help me with the evals for my agent.
```

With no goal supplied, Eval Author welcomes you and asks whether you want to
build first evals, improve an existing suite, or find out what's already here.
Your answer starts guided authoring, a guided coverage audit, or discovery.
It reuses a known goal or prior progress without repeating that question.
If the assistant cannot find
`eval-author`, follow the [availability check](docs/getting-started.md#verify-your-assistant-can-use-eval-author).
If you know more about your scenario, try one of these:

| Say to your coding assistant | Guide |
| --- | --- |
| "Find the evals in this repository and explain how to run them." | [Discover existing evaluations](docs/getting-started.md#find-existing-evaluations) |
| "I don't have evals yet. Help me build my first ones." | [Build your first evaluations](docs/first-evals.md) |
| "Help me onboard to expand my evals." | [Start a guided coverage audit](docs/existing-evals.md#audit-coverage), then propose additions from its findings |
| "Check whether my Gym or Harbor evals are ready to run." | [Check readiness](docs/existing-evals.md#check-readiness) |
| "Audit my evals against my agent's intended behavior using these traces." | [Audit coverage](docs/existing-evals.md#audit-coverage) |
| "Create a Gym evaluation from this audit gap." | [Author and prove a Gym task](skills/eval-author-task-create/references/gym-tasks.md) |
| "Use this audit report to propose the next evals to add." | [Plan new or improved evaluations](docs/existing-evals.md#propose-new-evaluations) |
| "Explain Intake trace TRACE_ID in workspace WORKSPACE." | [Inspect a trace](docs/traces.md#inspect-an-intake-trace) |
| "Turn this local trace into a Harbor eval task." | **Experimental** · [Build a task from a trace](docs/traces.md#build-an-evaluation-task) |
| "Explain this eval report and what I should check next." | [Read your results](docs/results.md) |
| "Review this task derived from a trace before I share it." | **Experimental** · [Review the task for publication](docs/traces.md#sharing-generated-tasks) |

Include the relevant file paths, trace IDs, or workspace names in your request.

### Experimental trace-environment loop

The [trace-environment workflow](docs/traces.md#build-an-evaluation-task) turns
trace evidence into a private candidate task, validates it with Harbor's `nop`
and `oracle` or Gym's native validation and repeated controls, and uses the findings
to refine the task. Gym output uses a private native report; automated publication
and readiness reporting remain Harbor-specific. It can start directly
from a trace or be paired with [dataset proposals](docs/existing-evals.md#propose-new-evaluations):
propose a useful scenario, construct it from supporting trace evidence, then
validate and refine it.

```text
Use this audit report to propose an eval, then use eval-author-trace-environment
to build a private candidate from the supporting trace. Validate it when the
required runtime is available, and report any remaining blockers.
```

A proposal does not establish that a task can be built or that its environment
works. The loop may produce no candidate or an unproven candidate; successful
validation still does not measure your agent's performance. Proposal-only
requests stop at recommendations. See the [trace guide](docs/traces.md#build-an-evaluation-task)
for evidence requirements and validation, and the
[publication review](docs/traces.md#sharing-generated-tasks) before sharing a task.

[Development](DEVELOPMENT.md) · [Skill reference](DEVELOPMENT.md#skill-reference) · [Support](https://github.com/NVIDIA-NeMo/eval-author/issues)

# Research Topics
## Questions we are exploring

- How can intended behavior become representative cases with meaningful success
  checks and execution environments?
- How can generated tasks challenge an agent in realistic ways and distinguish
  stronger behavior from weaker behavior?
- How can evaluations and traces reveal gaps without overstating coverage?
- How can developers decide which evaluations to add or improve next?

## Help shape the research

Try Eval Author with your agent and tell us where the approach needs work:

- Where do proposed cases miss behavior that matters to you?
- Where do graders judge outcomes incorrectly?
- Which coverage findings leave you unsure what to test or investigate next?

Generated tasks can be too easy. Where do your agents pass without exercising
the behavior you wanted to test? An anonymized example of the task, how the agent
passed, and what would make it meaningfully challenging would help us improve
task generation.

If an NVIDIA contact shared this preview with you, send your feedback through
that contact and ask them to route it to the Eval Author research team.
Otherwise, use the [NVIDIA Developer contact form](https://developer.nvidia.com/contact)
with **Eval Author research preview feedback** in the subject. Include your
workflow, what you expected, what happened, and a small anonymized example.
Please avoid sending raw traces, prompts, credentials, or user data in an initial
message. For code contributions, see the [current contribution policy](CONTRIBUTING.md).

## License

Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.

Eval Author is licensed under the [Apache License, Version 2.0](LICENSE).
See [NOTICE](NOTICE) for attributions and the external-materials disclaimer,
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for dependency license
disclosures, and [third_party/licenses.jsonl](third_party/licenses.jsonl) for
the machine-readable inventory.

The bundled JSON examples and synthetic test fixtures are NVIDIA-authored and
covered by the project license.

This project is currently not accepting contributions. Internal maintenance is
limited to the NVIDIA ASE team. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
current policy, sign-off, and review requirements.
