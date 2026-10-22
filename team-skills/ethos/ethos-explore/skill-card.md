## Description: <br>
Captures what an AI agent is supposed to do — its purpose, boundaries, and success criteria — as a durable snapshot for anyone building, evaluating, reviewing, or changing it. <br>

This skill is for research and development only. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers building, evaluating, reviewing, or onboarding an AI agent use this skill to capture the agent's intended behavior — purpose, boundaries, and success criteria — by scanning the codebase and docs and then interviewing the user for intent, handing the result to ethos-write to produce an ETHOS.md. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [No] <br>
**Credential Type(s):** [None] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>


## Skill Output: <br>
**Output Type(s):** [Analysis] <br>
**Output Format:** [Interactive Q&A dialogue followed by an inline Markdown ETHOS.md draft with YAML front matter] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Produces a structured conversation handed to ethos-write, which writes the ETHOS.md file; this skill itself writes no files] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-sonnet-5-5`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
4 evaluation tasks (3 positive, 1 negative) drawn from the skill-evaluator-dataset-snapshot/1 dataset; one attempt per task, each run in an isolated k8s-sandbox pod. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Is the answer correct? Final-answer correctness against the reference answer. <br>
- Discoverability: Was the right skill loaded when needed, decoys avoided, and the workflow executed. <br>
- Effectiveness: Did the skill help complete the task? Equal-weight mean of goal completion and expected workflow adherence. <br>
- Efficiency: Did it avoid wasted tool calls and token usage? Equal-weight mean of tool-call productivity and token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage (50% of Efficiency). <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 88.3% — baseline ran, no comparable score; uplift unavailable | 85.8% — baseline ran, no comparable score; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 55.0% → 80.0% (+25.0 points) | 65.0% → 75.0% (+10.0 points) |
| Discoverability | 100.0% — baseline ran, no comparable score; uplift unavailable | 95.0% — baseline ran, no comparable score; uplift unavailable |
| Effectiveness | 55.4% → 73.1% (+17.7 points) | 43.3% → 66.0% (+22.7 points) |
| Efficiency | 88.5% — baseline ran, no comparable score; uplift unavailable | 93.0% — baseline ran, no comparable score; uplift unavailable |

## Skill Version(s): <br>
0.1.0 (source: pyproject.toml) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
