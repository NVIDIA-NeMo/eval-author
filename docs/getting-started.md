<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Get started with Eval Author

Install Eval Author for your coding agent, then describe the evaluation help you
need. Eval Author uses a coordinated set of skills: instructions and supporting
scripts that your agent reads to carry out the work.

## Load the skills

Use a coding agent that can read local instructions, read and write project
files, and run shell commands. Choose either the Skills CLI or a manual copy;
both install the same ten active skill directories. Keep the complete set
together: skills load sibling skills and their bundled scripts, schemas,
templates, and references. The set includes the **experimental**
trace-to-environment workflow.

All three journeys also need the Ethos skills (`ethos-orchestrate`,
`ethos-explore`, and `ethos-write`), which install separately from the NVIDIA
skills catalog. Use the [installation command](../README.md#prerequisites) for
your assistant and scope; see the
[ETHOS.md guide](https://github.com/NVIDIA/skills/blob/main/skills/ethos-orchestrate/README.md#how-to-use).

Eval Author uses an explicit document path first. For a Helix agent, it otherwise
checks `ETHOS.md` in the agent's `<workspace>/<agent-name>-ethos` fileset before
looking at repository-root `ETHOS.md`. A missing or empty stored document allows
the local fallback; connection and access errors need to be resolved. Local-only
agents do not need Helix.

A stored document is downloaded to a new local snapshot for validation and your
review. Eval Author reports its fileset source and local path, preserves existing
repository files, and keeps the selected snapshot when you resume. Ask to refresh
it if you want to use a newer stored version. An explicitly selected agent-local
file stays in place. New documents default to the repository root unless you
specify another destination; selected files retain the uppercase `ETHOS.md` name.

The linked catalog guide describes lowercase `ethos.md` and renaming uppercase
files. Use a catalog version that preserves an explicitly supplied uppercase
path, and verify the installed skills before starting Ethos. Incompatible
instructions block the handoff; reinstalling the same version does not resolve
the mismatch. See the
[required handoff capabilities](../skills/eval-author/references/ethos-confirmation.md#required-handoff-capabilities).

Neither installation path installs Harbor, Python dependencies, a coding
assistant, or NeMo services. Those have [workflow-specific requirements](#requirements).

### Install Eval Author with npx

Install [Node.js 22.20+](https://nodejs.org/en/download) (including npm and `npx`)
and [Git](https://git-scm.com/downloads), and set up your coding assistant.
Confirm the tools are available in your terminal:

```bash
node --version
npx --version
git --version
```

Run the installer in the root of **your agent's repository**, where you want
evaluation help. You do not need an Eval Author checkout for this path. Internet
access to npm and GitHub is required; if GitHub requests authentication, use a
Git credential setup with access to the repository.

Choose your assistant's identifier and installation scope before running the
command. These are common choices; see the
[Skills CLI's supported assistants](https://github.com/vercel-labs/skills#supported-agents)
for the full list and current paths.

| Coding assistant | `--agent` value | Project skills folder | Global skills folder (default) |
| --- | --- | --- | --- |
| Claude Code | `claude-code` | `.claude/skills/` | `~/.claude/skills/` |
| Codex | `codex` | `.agents/skills/` | `~/.codex/skills/` |
| Cursor | `cursor` | `.agents/skills/` | `~/.cursor/skills/` |
| Pi | `pi` | `.pi/skills/` | `~/.pi/agent/skills/` |

For a **project installation**, replace the path and `claude-code` as needed:

```bash
cd /path/to/your-agent-repository
npx skills add NVIDIA-NeMo/eval-author --skill '*' --agent claude-code --yes
```

This makes the skills available in this repository. For a **global installation**
available across your projects, use this command instead:

```bash
npx skills add NVIDIA-NeMo/eval-author --skill '*' --agent claude-code --global --yes
```

`--agent` selects your coding assistant (`claude-code`, not `claude`). `--yes`
skips Skills CLI prompts; `npx` may first ask to download the CLI itself.
`--skill '*'` installs all active Eval Author skills; the quotes prevent your
shell from expanding the wildcard into local filenames.

The [Skills CLI](https://github.com/vercel-labs/skills) copies the files into the
selected assistant's skills folder or links them from a shared copy. The summary
may name other assistants that read the same shared folder; it does not install
those assistants. Add `--copy` if your environment does not support symlinks.
Review the printed destinations to confirm your intended scope. A project
installation also creates `skills-lock.json` to record the installed sources.

If you prefer the interactive installer, run:

```bash
npx skills add NVIDIA-NeMo/eval-author --skill '*'
```

Use the arrow keys to move, **Space** to select or deselect assistants, then
**Enter** to continue. Confirm your assistant is selected before continuing;
highlighting its name alone does not select it. Choose **Project** or **Global**
scope and a symlink or copy installation when prompted. These screens and their
completion message belong to the Skills CLI.

Verify a project installation from the same repository:

```bash
npx skills list --agent claude-code
```

For a global installation, use `npx skills list --agent claude-code --global`.
Use the same assistant identifier you installed for, and confirm the listed
paths match your chosen project or global destination. Expect these ten skills:

```text
eval-author
eval-author-audit
eval-author-discover
eval-author-environment
eval-author-first-eval
eval-author-inspect-trace
eval-author-task-create
eval-author-trace-environment
gym-to-atif
mlflow-to-atif
```

If none appear, check the directory, assistant identifier, and scope. Then
[verify assistant availability and start a request](#verify-your-assistant-can-use-eval-author).

### Manual installation without Node.js

Use this path when you want to install local skills without Node.js, npm, or the
Skills CLI. You need Git, access to this GitHub repository, a POSIX shell with
`cp` and `diff` (macOS, Linux, or WSL), and a configured coding assistant.

1. Clone Eval Author into a separate directory where you keep source checkouts:

   ```bash
   cd /path/to/your/checkouts
   git clone https://github.com/NVIDIA-NeMo/eval-author.git
   cd eval-author
   pwd
   ```

2. In your agent's repository, set the source to the absolute path printed above
   and choose the project skills folder from the assistant table. This example
   uses Claude Code; for Codex or Cursor, set `skills_dir=.agents/skills`.

   ```bash
   cd /path/to/your-agent-repository
   eval_author_source="/absolute/path/to/eval-author"
   skills_dir=.claude/skills
   ```

3. Copy every active skill directory, including its supporting files. Run this
   block in the same terminal. It stops before copying if a destination already
   exists; resolve those existing copies before retrying.

   ```bash
   (
     set -eu
     test -f "$eval_author_source/skills/eval-author/SKILL.md"
     for skill_file in "$eval_author_source"/skills/*/SKILL.md; do
       skill_dir=${skill_file%/SKILL.md}
       skill_name=${skill_dir##*/}
       if [ -e "$skills_dir/$skill_name" ] || [ -L "$skills_dir/$skill_name" ]; then
         printf 'Already exists: %s\n' "$skills_dir/$skill_name" >&2
         exit 1
       fi
     done
     mkdir -p "$skills_dir"
     for skill_file in "$eval_author_source"/skills/*/SKILL.md; do
       cp -R "${skill_file%/SKILL.md}" "$skills_dir/"
     done
   )
   ```

   Copying just the entry skill or individual `SKILL.md` files is insufficient.
   The loop selects active skills by `SKILL.md` and leaves archived, disabled
   workflows out. Manual copies are independent of the checkout; updating the
   checkout does not update installed copies.

4. Verify all ten installed skill directories match the checkout:

   ```bash
   (
     set -eu
     for skill_file in "$eval_author_source"/skills/*/SKILL.md; do
       skill_dir=${skill_file%/SKILL.md}
       diff -r "$skill_dir" "$skills_dir/${skill_dir##*/}"
     done
     printf 'Eval Author skill files verified.\n'
   )
   ```

   Expect the verification message with no differences. Then
   [open your assistant and start a request](#verify-your-assistant-can-use-eval-author).

### Manual checkout

If you prefer to keep the skills outside your project, you can load a checkout
by file path instead of installing copies into an assistant's skills folder.
This requires Git and a coding assistant allowed to read both directories;
Node.js is not required. This option does not register skills for automatic
discovery in the assistant.

1. Clone this repository and print the absolute path to the entry skill:

   ```bash
   git clone https://github.com/NVIDIA-NeMo/eval-author.git
   cd eval-author
   printf '%s/skills/eval-author/SKILL.md\n' "$PWD"
   ```

2. Open the repository you want to evaluate in your coding agent. Give the agent
   access to both that repository and this skill checkout. Keep this checkout
   intact: the entry skill loads sibling skills, scripts, schemas, templates,
   and references.

3. Start your request with this text, replacing `<entry-skill-path>` with the
   path printed above. Add your goal from one of the [workflow guides](../README.md#choose-your-journey):

   ```text
   Use Eval Author at <entry-skill-path> in this repository.
   Load supporting skills and references from the same skill tree as needed.
   ```

Loading the skills requires no Python package installation. You need your coding
agent's usual setup; later steps have [workflow-specific requirements](#requirements).

### Verify your assistant can use Eval Author

After either installation path, open your agent's repository in a new session of
the selected coding assistant. If it has a skill picker, look for `eval-author`.
You can check access without starting an evaluation workflow by asking:

```text
Find the installed eval-author skill, read its SKILL.md, and tell me its path.
Confirm you can also read the sibling eval-author-discover/SKILL.md.
```

Confirm those paths belong to the installation you just verified. If the skill
is missing, check the assistant and scope, reopen the session, or give the
assistant the absolute path to the installed `eval-author/SKILL.md` as in the
[checkout instructions](#manual-checkout). Listing installed files confirms
the copy; this check confirms your assistant can read it and its supporting skills.

Then start your first interaction:

```text
Use eval-author to help me with the evals for my agent.
```

Eval Author helps you build your first evaluations, audit existing coverage, or
improve a suite. It first confirms your setup and your agent's intended behavior
in `ETHOS.md`, then continues with your selected goal. You can also use a
[more specific starter prompt](../README.md#choose-your-journey).

After you agree to begin, Eval Author checks your setup before reviewing
`ETHOS.md`. Setup blockers must be resolved before that review or evaluation
work can continue. If progress cannot be loaded or saved, work also pauses:
findings remain in the current chat, and a fresh chat cannot resume them until
they are saved.

Full creation and improvement onboarding pauses before Ethos if the selected
runtime is missing, incompatible, or cannot yet be verified. Progress is saved
while you resolve setup; prerequisite checks do not install software. A full
audit checks audit tooling without probing an evaluation runner.

## Find existing evaluations

With the skills loaded, ask your coding assistant:

```text
Find the existing evaluations, explain what they test, and show their
documented run commands. Save the findings to .eval-author/discovery.md.
```

This request inventories the repository's documentation and source. It does not
require Harbor, Docker, or model credentials for evaluation runs. If you already
know your starting point, go directly to the relevant workflow guide.

### What to expect

The agent produces `.eval-author/discovery.md` in the repository being evaluated.
It describes the evaluations it found, their purpose, and available run
instructions. For example, an inventory might identify a smoke-test suite,
point to its configuration, and quote its documented command. Those instructions
remain unverified until a readiness check. If no evaluations are found, the
report records that result and the scope inspected.

To continue, [check an existing suite](existing-evals.md#check-readiness),
[audit its coverage](existing-evals.md#audit-coverage), or
[build your first evaluations](first-evals.md).

## Requirements

Requirements depend on the operation. Use the
[Harbor setup guide](../skills/eval-author/references/harbor-setup.md) when
you need to create, validate, or run Harbor tasks.

| Operation | Requirements |
| --- | --- |
| Read repository docs or explicitly request case planning only | Your coding agent and repository access. No evaluation runtime is required for this scoped work. |
| Start full creation or improvement onboarding | The selected supported NeMo Gym or Harbor runtime, verified before Ethos and case planning. |
| Run the discovery helper | Python 3.11+. Harbor configs/tasks need Harbor importable by that interpreter; native NeMo Gym manifests need a NeMo Gym v0.6.0+ CLI. Execution checks need the selected backend and task dependencies; Docker is needed for Docker-backed tasks. |
| Build first evals with native NeMo Gym | NeMo Gym v0.6.0+ in its separate Python 3.13.14+ environment; no audit report required. See [first evals](first-evals.md). Execution needs the actual agent integration and configured model access. |
| Create a NeMo Gym task from an audit gap | An existing actionable coverage report and an installed NeMo Gym v0.6.0+ runtime in Python 3.13.14+. See the [NeMo Gym task-authoring guide](../skills/eval-author-task-create/references/gym-tasks.md). Execution needs the task components and actual agent/model configuration. |
| Create, validate, and run Harbor tasks | Harbor and its supported Python environment. Execution also needs the selected backend and any application access, agent integration, and provider credentials required by the task. |
| Build and prove a task environment | Repository access for the fit check and inventory. Harbor environments need Harbor and Docker with Compose for sidecar services; NeMo Gym environments need the NeMo Gym runtime above. Vendor sandboxes need your authorization and credentials, supplied by variable name. |
| Generate or validate an audit specification; aggregate measured coverage | Python 3.11+, PyYAML, and jsonschema. These operations read local evidence and do not call NeMo services. |
| Measure coverage from ATIF traces | Python 3.12+ and the [audit dependencies](../skills/eval-author-audit/requirements.txt), including Harbor. Measurement does not start evaluation jobs. |
| Convert exported MLflow traces | Python 3.11+ and the standard library. Live MLflow queries additionally need an existing MLflow environment and access to the store. |
| Convert NeMo Gym traces with `gym-to-atif` | Python 3.11+ and the standard library for conversion. |
| Prepare and validate a trace-derived environment (**experimental**) | Python 3.11+, jsonschema 4.23+, and referencing 0.28.4+ for preparation; Harbor and Docker for Harbor checks, or the sibling task-create skill and NeMo Gym v0.6.0+ in Python 3.13.14+ for [native NeMo Gym output](../skills/eval-author-trace-environment/references/gym-output.md). |
| Inspect NeMo Intake traces | A working `nemo` CLI, an explicit workspace, and read access to a configured local or remote NeMo Helix instance. |
| Generate the Harbor HTML case/run report | Python 3.11+, jsonschema 4.23+, and markdown-it-py 4.x. Reads local task/run artifacts; no Harbor runtime, Docker, model access, or network is needed to render. See the [Report generation](../skills/eval-author/references/suite-readme.md#report-generation). |

Use the Python environment supported by your installed Harbor version for
Harbor-backed operations. This repository's development checks use Python 3.12
and 3.13 with Harbor 0.20.0; see [development setup](../DEVELOPMENT.md).

The experimental trace-to-environment workflow uses Harbor's `nop` agent (a no-op
baseline) and `oracle` agent (the task's reference solution), or NeMo Gym's native
positive and negative controls. These checks do not require a model provider.
Running your actual agent uses its configured provider and dependencies.

If you need help, [open an issue](https://github.com/NVIDIA-NeMo/eval-author/issues)
with the workflow, relevant versions, and a minimal reproduction using synthetic
data. Keep credentials and private traces out of public reports.
