import json
import math
import random
import re
from typing import Callable, Dict

_SECTION_RE = re.compile(r"^===([A-Z_]+)===\s*$", re.MULTILINE)


def load_prompt_sections(path) -> Dict[str, str]:
    """Parse a prompt file split into ``===SECTION_NAME===`` blocks."""
    with open(path, "r") as f:
        text = f.read()

    sections: Dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        raise ValueError(f"No sections found in prompt file: {path}")

    for i, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()

    required = {
        "SYSTEM_PROMPT",
        "AGENT_INSTRUCTION",
        "ORDERING_NOTE_SHUFFLED",
        "ORDERING_NOTE_FIXED",
    }
    missing = required - sections.keys()
    if missing:
        raise ValueError(f"Prompt file {path} missing sections: {sorted(missing)}")
    return sections


class PartialCoverageHints:
    actions: list[dict]
    prompt_sections: dict[str, str]
    extra_notes: tuple[str, ...] = ()
    shuffle: bool
    domain_policy: str
    coverage_p: float
    provide_function_args: bool
    make_agent_instructions_from_action: Callable[..., str]

    @property
    def system_prompt(self) -> str:
        ordering_note = (
            self.prompt_sections["ORDERING_NOTE_SHUFFLED"]
            if self.shuffle
            else self.prompt_sections["ORDERING_NOTE_FIXED"]
        )
        instruction = self.prompt_sections["AGENT_INSTRUCTION"].format(ordering_note=ordering_note)
        for note in self.extra_notes:
            instruction += "\n\n" + note
        return self.prompt_sections["SYSTEM_PROMPT"].format(
            agent_instruction=instruction,
            domain_policy=self.domain_policy,
            resolution_steps=self.make_agent_instructions_from_actions(),
        )

    def make_agent_instructions_from_actions(self) -> str:
        """Build action list (optionally shuffled) with argument values redacted for covered actions."""
        actions = list(self.actions)

        # Only assistant actions are eligible for coverage
        assistant_indices = [i for i, a in enumerate(actions) if a.get("requestor", "assistant") == "assistant"]
        n_assistant = len(assistant_indices)
        # coverage_p == 0 means zero redaction.
        # For p > 0 we enforce at least one redaction so "partial coverage" is
        # meaningful.
        if n_assistant > 0 and self.coverage_p > 0.0:
            n_to_cover = max(1, math.floor(self.coverage_p * n_assistant))
            covered_indices = set(random.sample(assistant_indices, min(n_to_cover, n_assistant)))
        else:
            covered_indices = set()

        order = list(range(len(actions)))
        if self.shuffle:
            random.shuffle(order)

        lines = []
        for i in order:
            action = actions[i]
            if i in covered_indices:
                lines.append(f"- {self._make_covered_instruction(action)}")
            else:
                lines.append(
                    f"- {self.make_agent_instructions_from_action(action=action, include_function_args=self.provide_function_args)}"
                )
        return "\n".join(lines)

    @staticmethod
    def _make_covered_instruction(action) -> str:
        """Render an assistant action with all argument values replaced by ???."""
        args_str = ", ".join(f"{k}=???" for k in action["arguments"].keys())
        return f"Perform the following action: {action['name']}({args_str})."


class ActionHints(PartialCoverageHints):
    def __init__(self, actions, policy, *, coverage_p=0.33, shuffle=True):
        if not 0 <= coverage_p <= 1:
            raise ValueError("coverage_p must be in [0, 1]")
        self.actions = actions
        self.domain_policy = policy
        self.coverage_p = coverage_p
        self.shuffle = shuffle
        self.provide_function_args = True
        self.prompt_sections = {
            "SYSTEM_PROMPT": "{domain_policy}\n\n{agent_instruction}\n{resolution_steps}",
            "AGENT_INSTRUCTION": "Solve the user's task using the available tools. The following reference hints are private assistant guidance; ??? marks arguments you must determine. {ordering_note}",
            "ORDERING_NOTE_FIXED": "Hints follow reference order.",
            "ORDERING_NOTE_SHUFFLED": "Hints are shuffled; determine the appropriate order.",
        }

    @staticmethod
    def make_agent_instructions_from_action(action, include_function_args=False):
        arguments = json.dumps(action["arguments"], ensure_ascii=False) if include_function_args else ""
        actor = action.get("requestor", "assistant")
        return f"{actor}: {action['name']}({arguments})"
