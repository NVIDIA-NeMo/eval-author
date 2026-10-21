<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Contributions are currently limited to the NVIDIA ASE team
(`@NVIDIA-NeMo/ase_team`). This contribution policy does not configure repository
access permissions.

## Development and review

Create a branch in this repository and open a pull request against `main`.
Use a Git worktree when practical. Do not commit or push directly to `main`.
Fork contributions are not supported during this restricted phase.

Use Python 3.12 or 3.13 and uv (CI uses uv 0.12.3). Before handing off or
committing changes, run the same checks as CI, plus Git's whitespace check:

```bash
uv sync --locked
make hooks
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q -rs
make check-copyright-headers
make check-licenses
git diff --check
make commit-check COMMIT_MSG=/path/to/prepared-commit-message
```

License validation requires OSV-Scanner 2.3.3 on `PATH` and network access;
see [DEVELOPMENT.md](DEVELOPMENT.md) for setup and dependency updates.
`make hooks` installs the local sign-off check for `git commit`.
`make commit-check` checks the current `HEAD` commit's message by default,
including after a fresh clone or pull. Pass `COMMIT_MSG` to check a prepared
message before committing. Report any failed or skipped checks and explain
their impact in the pull request. Describe the
problem, resulting behavior, and validation.
Obtain approval from another ASE team member before merging; `.github/CODEOWNERS`
assigns that team to every file, including workflows and CODEOWNERS itself.

The default tests use synthetic evidence and mocked providers. Some checks skip
when a backend or optional plugin is unavailable. Live model execution outside
the configured CI workflows requires explicit authorization. See
[trace fixture checks](docs/trace-derived-fixtures.md#regression-checks).
Keep credentials, customer traces, and private evaluation artifacts out of
commits, CI logs, and artifacts. Review runtime dependency additions carefully:
users copy the standalone skill trees and rely on their documented dependencies.

## Sign-off and contribution license

NVIDIA contributors must follow the applicable [IP review process](https://nv/ip_review_process)
for ongoing changes and contributions.

Contributions are licensed under this project's [Apache License 2.0](LICENSE).
Sign off every new commit to certify the [Developer Certificate of Origin 1.1](DCO),
using your own name and email configured in Git:

```bash
git commit -s -m "docs: clarify contribution requirements"
```

This adds a `Signed-off-by: Your Name <your@email.com>` trailer. Use `-s` for
amends and fixups as well. A DCO sign-off is a contributor certification, not a
cryptographic signature. Only sign off work you have the right to submit.

The sign-off check runs locally through `make commit-check` and the installed
commit-message hook. It requires no GitHub app or repository settings changes
and does not enforce a merge gate on GitHub.

### Developer Certificate of Origin

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

## PR titles and commit subjects

Follow nemo-helix's Conventional Commit-style PR titles:
`type(optional-scope): description`, with a maximum of 100 characters.
Examples: `fix(audit): reject missing evidence` and `docs: explain ASE access`.
Use `!` before the colon for a breaking change. Supported types are `feat`,
`fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, and
`revert`. Use the same style for commit subjects where practical; the CI format
check applies to PR titles, while the commit hook checks sign-off.

The PR-title workflow reads metadata with a read-only token and does not check
out PR code. If squash-merging, retain the semantic PR title and the appropriate
sign-off trailers in the resulting commit.

## CI trust boundary

The checked-in CI workflow tests same-repository pull requests targeting `main`
and pushes to `main`, manual dispatches, and daily runs on GitHub-hosted Ubuntu
runners. It uses a read-only
`GITHUB_TOKEN`, disables persisted checkout credentials and shared dependency
caching, pins actions to commit SHAs and uv to a version, and bounds job runtimes.
New change-triggered runs cancel older runs for the same PR or branch; daily
runs use a separate concurrency group and are not cancelled by pushes. See
[advisory evaluation CI](docs/skill-evaluation-ci.md) for approval requirements
and report details.

Dependency installation and tests execute code from the proposed change.
Repository writers must therefore be trusted to run code in CI. Review workflow,
dependency, build, and test changes with that in mind. Unit-test dependencies,
including the pinned public NeMo Compass package, install without repository
credentials; see [setup](DEVELOPMENT.md#public-nemo-compass-dependency).
Model-evaluation jobs use a dedicated CI inference credential in the protected
`skill-evaluator` environment. That environment permits only `main`, requires
approval from `NVIDIA-NeMo/ase_team`, prevents self-approval, and disables
administrator bypass. PR checks do not receive this credential. These controls
must be configured in GitHub settings, not only in workflow YAML. Fork PRs are
excluded. Keep unrelated secrets, cloud credentials, self-hosted runners, and
internal application access out of these jobs. Do not use
`pull_request_target` or a privileged `workflow_run` to execute PR code.

The same-repository job condition is defense in depth, not an access control:
a PR can modify workflow YAML. CODEOWNERS routes review requests to ASE; it
does not restrict access or enforce approval. These repository files do not
enforce ASE-only access or prevent every unwanted workflow run.
