<!-- SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Rubrics, Reward Hacking and Traces

How we keep eval generation tools from cheating.

**November 23, 2026 · Research**

## The problem

When a coding agent generates an evaluation, it is easy for it to cheat. It is asked for a task that runs and a verifier that passes good answers, and a task that tests nothing meets both requests. That is the main reason we are building Eval Author.

In our [first dev note](https://github.com/NVIDIA-NeMo/eval-author/blob/main/docs/dev-notes/posts/2026-10-08-introducing-eval-author.md), we introduced NeMo Eval Author. The hard part of evaluation authoring is rarely getting a task to run. It is knowing what the task should test, and then knowing whether it actually tests that. A pass rate tells you how solutions scored. It does not tell you whether to trust the task that produced it.

## An example: the refund task

Our demo task asks an agent to handle a customer refund for an order that may already be partly refunded. The agent reads the order and the refund policy. It calculates discounted merchandise plus eligible tax, excludes shipping, and subtracts earlier refunds. If the remaining amount reaches the approval threshold, it must record approval before refunding anything. The money must go back to the original payment method, and every new refund needs a matching ledger entry.

Suppose a coding agent builds this task for you. Here are four ways it can go wrong while the task still runs and still reports a pass rate:

1. The instructions state the refund amount, so the agent under test copies it instead of calculating it.
2. The verifier checks for a message that says the refund is done, not for the refund itself.
3. The task is unclear or wrong. The policy never says if shipping is refundable, or the reference answer gets the tax wrong.
4. The order history is made up. The coding agent never saw a real one, so it writes one that is simpler than what real customers produce.

The first one happens in real benchmarks. SWE-Bench+ found that in 32.67% of the successful SWE-bench patches it studied, the solution was already written in the issue report or its comments.[^swe-bench-plus] Our internal instruction-leakage study asks how much it helps a solver when part of the solution is in the prompt. We compared 300 task pairs that differ only in whether the instructions include part of the answer.[^datasets]

![Solution exposure: BIRD baseline 68.8%, gain 7.1 percentage points; customer service baseline 67.1%, gain 10.0 points; USACO baseline 96.3%, gain 1.2 points.](../assets/rubrics-cheats-and-traces/solution-exposure-results.svg)

The gain averaged about 6 percentage points, partly because most tasks already passed without the leak. The average hides tasks where the leak decided the outcome. On 18 tasks, the leaked version passed every trial and the clean version passed none.

## What we are doing about it

Here are some of the approaches we are taking. Reward-hacking checks cover the verifier. The rubric covers leaks and unclear tasks. Traces cover made-up state.

### Reward hacking

In the refund task, the shortcut is an agent that says the refund is done without issuing it. If the verifier gives credit for that, the task rewards the cheat.

A verifier has two jobs. It must reject invalid routes to reward, and it must accept valid solutions. A verifier that rejects everything never gives false credit, and it is also useless. So we test both sides.[^reward-hacking]

Our scripted verifier-cheat panel runs in [Harbor](https://github.com/harbor-framework/harbor), the evaluation framework our tasks run in. It tests a small coding task with three verifiers and six fixed submissions. No model is involved. Each submission is a known attack or a known good answer.

In our November 19 run, all 18 outcomes matched expectations. The sound verifier accepted the correct answer and rejected the five others. A deliberately vulnerable verifier rewarded all four attacks it was built to allow. An always-fail verifier rejected the correct answer. Those last two are the point. The panel has to catch broken verifiers as well as pass good ones.

![The sound verifier accepts valid work and rejects non-reference controls. Deliberately defective verifiers expose both false acceptance and false rejection.](../assets/rubrics-cheats-and-traces/deterministic-controls.svg)

We also tried a model-backed version, where a model attempts the task instead of a script. In its latest batch, the model never produced a cheat, so every submission passed both verifiers. Scripted controls are the more reliable way to exercise known failure paths, because they do not depend on a model choosing to attack.

### The rubric

In the refund task, a stated refund amount, a vague shipping rule or a wrong tax calculation does not stop the task from running. It changes what the score means, and the pass rate cannot show that.

Our hypothesis is that a rubric with explicit questions can tell us how good an evaluation is, and show the evidence behind that judgment.[^rubric-sources] Each question scores 0 to 3. If the evidence is missing, the question is marked Unassessed rather than scored 0.

One question asks:

> Can independent readers resolve the goal and grading boundaries from the public or discoverable contract?

For the refund task: can the agent tell whether shipping is refundable, when approval is required, and whether splitting the refund is allowed? A reference solution that happens to pass does not answer that. The agent has to be able to work it out from what it can see.

Other questions check for leaks and shortcuts. A scan compares what the agent can see with the private solution and test files. A separate control checks whether a refund amount stated in the instructions earns credit on its own.

For this demo, we built a version of the refund task that scores 3 on every question, and supplied the supporting evidence ourselves. That gives us a known, perfect starting point. When we break one thing, we can see which scores drop and which stay put. Each change, which we call a mutation, alters the task or its evidence. The rubric's scoring rules stay fixed.

![Six cases scored on four rubric rows. Base scores 3, 3, 3 and METHOD-J1 1. A completion-only verifier drops forbidden-credit resistance and scoring authority to 0. Tax drift drops them to 1 and 0. Unrun held-out attacks drop forbidden-credit resistance to 2. Missing native evidence leaves observed configuration Unassessed. Undisclosed self-corroboration drops METHOD-J1 to 0.](../assets/rubrics-cheats-and-traces/rubric-mutations.svg)

Different breaks hit different claims. A verifier that accepts a completion claim breaks both scoring and cheat resistance. A tax error breaks scoring and weakens cheat resistance. Skipping the attacks that someone else wrote only lowers confidence in cheat resistance. Missing evidence produces Unassessed, not a low score. Each result points to a different next step.

The completion-only mutation is the second failure from our example. We broke the refund verifier so that it accepted a completion claim without the required refund events. The task requirements and the rubric did not change.

![A completion-only submission earns no credit under the original verifier but receives undeserved credit under the deliberately broken variant. Forbidden-credit resistance falls from the base score of 3 to 0.](../assets/rubrics-cheats-and-traces/refund-completion-mutation.svg)

We ran the broken verifier and confirmed that the shortcut earned credit. The rubric then scored cheat resistance at 0. Scoring also fell to 0, because the verifier no longer checked the refund itself. The same held across all 26 mutations in the demo. Each one lowered at least one score, and none raised a score.

The report prints a total, but the score for each question is the result to read. A total would hide which claim failed, and that is the part you need to fix.

### Traces

In the refund task, the order history is what makes the problem hard: earlier partial refunds, approvals and ledger entries. A coding agent that only has the request has to make that history up.

Production traces record how customers actually use an agent. A trace keeps inputs, outputs, and actions. Building from one means the task starts from state someone observed.[^trace2env]

We tested this in October on a different task. Eval Author was asked to build the same task twice: once from the user's request alone, and once with the recorded trace of the earlier session. The task was a selection operation in a design tool, where the starting state depends on what happened earlier in the session.

| Measure | Request only | With the trace |
| --- | ---: | ---: |
| Runnable tasks built | 0 / 3 | 3 / 3 |
| Hidden checks passed (15 per task) | n/a | 45 / 45 |

From the request alone, Eval Author could not know the starting state, so it correctly declined to build the task every time. With the trace, it built a working tool environment every time. We wrote 15 checks before the run and kept them hidden from Eval Author, so it could not tune a task to pass them. All three tasks passed all 15.

The trace is what makes the task possible. It supplies the objects and state that the request never mentioned.

A second study used recorded sessions with an inventory service. One of the three traces was missing the only page that showed one product's stock. Eval Author named the missing product and declined to build the task instead of inventing a number.

## What's next

This moves us in the right direction, but there is more work to be done. We plan to fold parts of the trace-environment and rubric workflows into the main Eval Author skills. The studies below decide which parts.

1. **Validate the rubric on fresh defects.** We will compare the rubric with plain pass/fail scoring on a small set of reviewed tasks. Acceptance uses new defects and real outputs that played no part in building the rubric. We will measure missed defects, rejected valid solutions, and cost.
2. **Measure difficulty.** We want to know whether these checks produce tasks that separate stronger agents from weaker ones.[^difficulty]
3. **Make tasks harder on purpose.** We want to try perturbations of tool sequences, along with varied inputs and boundary cases for the verifier, and check that the harder task is still valid.[^taste]
4. **Simulate realistic users.** Real users are vague, change their minds, and write in their own style. We will use [NeMo Data Designer](https://github.com/NVIDIA-NeMo/DataDesigner) and [NeMo User Sim](https://github.com/NVIDIA-NeMo/UserSim) to generate those interactions. Synthetic interactions stay clearly separate from observed traces.
5. **Build stateful tools from many traces.** One trace shows one path through a tool. Many traces show how it behaves across paths. We want to build substitute tools from many traces and check that they behave like the real service beyond the calls we recorded.
6. **Add observers.** Add a separate observer that watches the agent's actions and environment during a trial and collects evidence of cheating or evaluation-aware behavior for the rubric.[^monitoring] We would measure missed detections, false alarms, and the effect of any intervention.

In a future dev note, we will cover where Eval Author gets the source material it generates evaluations from. The checks in this note keep the coding agent on track while it builds from that material.

## Try it

- `eval-author-rubric` scores tasks against the rubric. It is experimental and runs only when you ask for it by name, for example "apply eval-author-rubric to these tasks."
- `eval-author-trace-environment` builds tasks from traces. See [Traces](../../traces.md).
- To rerun the refund demo and the cheat panel, follow [Running the determinism checks](../../determinism-checks.md).

## Further reading

The studies in this note are trimmed to their main result. The full write-ups are in [Rubrics, reward hacking and traces: study details](../rubrics-cheats-and-traces-details.md):

- [Trace environments](../rubrics-cheats-and-traces-details.md#trace-environments-turning-recorded-interactions-into-tasks), including the stateful inventory study.
- [The rubric](../rubrics-cheats-and-traces-details.md#the-rubric-making-quality-claims-inspectable), with the full refund task, two criteria and their scoring anchors, and the base-case scores.
- [Deterministic anti-cheat controls](../rubrics-cheats-and-traces-details.md#deterministic-anti-cheat-controls).
- [How leaked solutions affect pass rates](../rubrics-cheats-and-traces-details.md#how-leaked-solutions-affect-pass-rates), including why some leaks did not help.
- [What we learned](../rubrics-cheats-and-traces-details.md#what-we-learned), a one-table summary of every study.

## References

[^reward-hacking]: Skalse et al., [Defining and Characterizing Reward Hacking](https://arxiv.org/abs/2209.13085). Related benchmarks that tempt models to cheat: [ImpossibleBench](https://arxiv.org/abs/2510.20270) and [EvilGenie](https://arxiv.org/abs/2511.21654). Our panel is described in [Running the determinism checks](https://github.com/NVIDIA-NeMo/eval-author/blob/00d0b8a7a9eb192807d3a3d09c6a5740d6b1e87a/docs/determinism-checks.md#verifier-cheat-panel).

[^swe-bench-plus]: [SWE-Bench+](https://arxiv.org/abs/2410.06992) reports 32.67% of the successful patches it studied. See also [Introducing SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/) and [Are "Solved Issues" in SWE-bench Really Solved Correctly?](https://arxiv.org/abs/2503.15223).

[^datasets]: [BIRD](https://arxiv.org/abs/2305.03111) text-to-SQL, vetted with corrections from [Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards](https://arxiv.org/abs/2601.08778). [USACO](https://arxiv.org/abs/2404.10952) programming problems. Customer-service tasks built from [τ²-bench](https://github.com/sierra-research/tau2-bench) data. Three trials per variant, 1,800 trials in total, with web search turned off. This is an internal NVIDIA study. One finding: some of BIRD's own hint sentences are wrong, and without the ten tasks where the hint misled the agent, BIRD's gain is about 14 points instead of 7.

[^rubric-sources]: The rubric borrows from [Epoch AI's benchmark reviews](https://epoch.ai/data/benchmark-reviews-documentation/methodology), which check benchmarks against a written rubric, and [Mubric](https://arxiv.org/abs/2609.37322), which tests a rubric by planting defects. The [experimental rubric](https://github.com/NVIDIA-NeMo/eval-author/blob/00d0b8a7a9eb192807d3a3d09c6a5740d6b1e87a/skills/eval-author-rubric/SKILL.md) is in this repository. Benchmark quality more broadly: [Establishing Best Practices for Building Rigorous Agentic Benchmarks](https://arxiv.org/abs/2507.02825) and [BetterBench](https://arxiv.org/abs/2411.12990).

[^trace2env]: Our trace work grew from an internal NVIDIA project, trace2env, developed by Dave and Jeff Farris. It predates and is not affiliated with the public [trace2env](https://github.com/ruyue0001/trace2env) and [From Traces to Agentic Worlds](https://arxiv.org/abs/2610.06100). We also drew on [Terminal-Universe](https://arxiv.org/abs/2609.04148), [Repo2RLEnv](https://github.com/huggingface/Repo2RLEnv) and [bandits](https://github.com/bandr-ai/bandits).

[^difficulty]: [Fluid Language Model Benchmarking](https://arxiv.org/abs/2509.11106), [Reliable and Efficient Amortized Model-based Evaluation](https://arxiv.org/abs/2503.13335) and [BenchBench](https://arxiv.org/abs/2603.20807).

[^taste]: [A Matter of TASTE](https://arxiv.org/abs/2605.28556). See also [A Framework for Generating Valid Context-Specific Benchmarks through Expert Guidance](https://arxiv.org/abs/2609.16592).

[^monitoring]: [Monitoring and Discovering Reward Hacking with Internal Representations during LLM Evaluations](https://arxiv.org/abs/2609.19101), [Monitoring Reasoning Models for Misbehavior](https://arxiv.org/abs/2503.11926) and [Large Language Models Often Know When They Are Being Evaluated](https://arxiv.org/abs/2505.23836).
