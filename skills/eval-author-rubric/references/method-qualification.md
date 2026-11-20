<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Method qualification

METHOD-J1 scores the corroboration behind the rubric, not the task. A score of 3 on
any criterion rests on corroborating controls run by a different actor. METHOD-J1
asks whether those actors and methods are what the evidence says they are.

[Canonical definition](method-qualification.json)

METHOD-J1 is reported beside the 23 criteria and is not part of their total. A low
METHOD-J1 does not lower any criterion score. It tells the reader how much weight
the criteria's 3s can bear.

**Facts:** one row per corroborating actor, with its kind (human, agent, tool or
simulated), its relation to the original author (independent or shared author),
its exposure (blind or exposed), whether that relation is disclosed, and whether
the method is qualified for its role. Record what the evidence claims about the
actor. A claim of independence or blindness that the record contradicts scores 0.

**Score 3:** every row is independent, blind and qualified, and a separate method
audit confirms them.
