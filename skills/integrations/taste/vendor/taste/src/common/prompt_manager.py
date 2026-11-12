"""Load shared generation prompts and prepared overrides."""

from pathlib import Path


class PromptManager:
    def __init__(self, prompts_dir: str, domain: str | None = None):
        self.prompts: dict[str, str] = {}
        root = Path(prompts_dir)
        for stage in sorted(root.glob("stage*")):
            if not stage.is_dir():
                continue
            for path in sorted(stage.glob("*.txt")):
                self.prompts[path.stem] = path.read_text()
            if domain is not None:
                for path in sorted((stage / domain).glob("*.txt")):
                    self.prompts[path.stem] = path.read_text()

    def get_prompt(self, prompt_name: str) -> str:
        if prompt_name not in self.prompts:
            raise KeyError(f"Prepared prompt {prompt_name!r} missing; available: {sorted(self.prompts)}")
        return self.prompts[prompt_name]
