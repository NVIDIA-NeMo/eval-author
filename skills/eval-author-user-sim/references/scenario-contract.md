<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# User-simulation scenario contract

`user-sim.yaml` describes one task scenario for a neutral simulated user. It is
an Eval Author artifact, not a native Harbor or NeMo UserSim configuration.
Validate it with the shared script before passing it to an execution adapter:

```bash
python <eval-author>/scripts/user_sim.py validate --path <task>/user-sim.yaml
```

Use the selected runtime's Python with PyYAML installed. The command returns a
JSON validity summary and exits with 0 on success or 2 for invalid input. It
doesn't print user facts or execute either agent.

## Version 1 fields

| Field | Requirement | Meaning |
|---|---|---|
| `schema_version` | Required integer `1` | Format version. |
| `task_id` | Required nonempty string | The agreed task identifier. |
| `goal` | Required nonempty string | What the user wants to accomplish. Reuse the task goal. |
| `start_prompt` | Required nonempty string | The exact opening message the target agent receives. |
| `user_type` | Optional, defaults to `neutral` | Only the shared neutral user is supported. |
| `user_facts` | Optional mapping, defaults to `{}` | Facts this user knows, with string keys and JSON-compatible values. |
| `disclosure_rules` | Optional list of strings, defaults to `[]` | Task-specific rules for when to disclose those facts. |
| `conversation_prompt` | Optional nonempty string | Task-specific instructions for reacting to questions, offers, or actions. |
| `stopping_conditions` | Optional list of strings, defaults to `[]` | Task-specific conditions for ending the conversation. |

The loader rejects unknown fields, duplicate keys, non-string mapping keys,
unsafe YAML tags, recursive aliases, and non-JSON fact values. Quote dates in
facts so YAML reads them as strings. Input is limited to 256 KiB, nesting to
64 levels, and parsed or expanded values to 10,000. YAML merge keys aren't
supported. Nonrecursive aliases are allowed within these limits.

## Shared neutral behavior

The execution adapter must apply the shared [neutral user persona](neutral-persona.md)
together with the task scenario. The Harbor translator reads that same source.

These are reusable behavior defaults. Personality traits, demographic profiles,
persona generation, and persona variants aren't part of this version.

## Information boundaries and validation

The simulator receives the goal, user facts, behavioral instructions, and public
conversation. The target agent receives the opening request and subsequent user
messages. Keep reference solutions, hidden verifier data, and evaluator judgments
out of simulator inputs. Store models, credentials, execution limits, and provider
settings in execution configuration instead of this artifact.

Structural validation doesn't prove that facts match the environment, that the
goal is reachable, or that private grading data is absent from prose. The author
must check those properties against the agreed scenario and prepared fixtures.
Conversation termination doesn't establish task success. The independent verifier
must check the target agent's observed behavior and resulting state.

The execution adapter must enforce the starting message, apply the shared behavior
and any task-specific rules, preserve the conversation session, and enforce runtime
limits. A valid file alone doesn't establish that an adapter supports those duties.
