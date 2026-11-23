<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

We welcome contributions from the NVIDIA ASE team (`@NVIDIA-NeMo/ase_team`)
and anyone outside ASE, including people outside NVIDIA.
The ASE team maintains the project and decides which contributions to accept.
This policy does not change repository access permissions.

## Human authorship and AI use

These requirements apply to all contributors, including ASE team members:

- Write issue reports, PR titles and descriptions, and discussion replies in
  your own words. Do not use AI-generated text to explain your contribution.
  You may quote code, logs, and command output as supporting evidence.
- Understand the problem you report and every change you submit.
  Be ready to explain your approach, tradeoffs, tests, and limitations in your
  own words. Answer follow-up questions yourself.
- You may use AI tools to write code and tests. Review every change for
  correctness and licensing. Run the relevant checks and report their results
  accurately. AI output alone does not prove that a change works.

The ASE team reviews contributions and may use AI tools to help.
AI feedback is advisory. An ASE team member other than the author must approve
each change before it can merge.

## Additional requirements for contributors outside ASE

If you are outside the ASE team, including elsewhere at NVIDIA, you must also
meet these requirements:

- For small documentation or typo fixes, you may open a PR directly.
  For behavior changes, new features, or refactors, first open or join an issue.
  Get an ASE maintainer's agreement on the problem and proposed scope.
  This agreement does not guarantee that the team will accept your PR.
- For bug reports, give reproduction steps, expected and actual behavior,
  and relevant environment details. For proposals, explain the use case and
  why the change belongs in Eval Author.
- Keep each PR focused on one concern and within any agreed scope.
  If an issue is required, link to it and the maintainer's agreement.
  Include the relevant commands you ran and their results.
  Report any failed or skipped checks. For behavior changes, include a
  reproducible example and tests that show the change.
- Answer review questions and make requested revisions yourself.
  Maintainers may close a submission if it lacks the required evidence or
  exceeds the agreed scope. They may also close it if you cannot explain it.

## Development and review

If you have repository write access, create a branch in this repository.
Otherwise, fork the repository and create a branch in your fork.
Open a pull request against this repository's `main` branch.
Use a Git worktree when practical. Do not commit or push directly to `main`.

The current CI test jobs exclude fork PRs. If you submit a fork PR, give your
local check results. Before merging, ASE maintainers must review the changes
and arrange validation in an isolated environment without privileged credentials.
Automated AI review currently excludes fork PRs too.

Use Python 3.12 or 3.13 and uv (CI uses uv 0.12.3). Before handing off or
committing changes, run the same checks as CI, plus Git's whitespace check:

```bash
uv sync --locked
make hooks
uv run --locked ruff check .
uv run --locked ruff format --check .
PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest -q -rs -n auto
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
their impact in the pull request. Describe the problem, the change in behavior,
and how you checked it.
`.github/CODEOWNERS` assigns the ASE team to every file, including workflows
and CODEOWNERS itself.

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
