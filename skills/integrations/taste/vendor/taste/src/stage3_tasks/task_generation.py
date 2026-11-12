import copy
import json
from typing import Any, Dict, List, Tuple

from src.backends.contracts import CandidateGenerationError, IncompatibleBackend
from src.common.llm_response_parser import LLMResponseParser
from src.common.tool_spec_retriever import ToolsSpecRetriever


class TaskGenerator:
    """Generates tasks via LLM: creates user tasks and DB initialization."""

    def __init__(
        self,
        domain: str | None = None,
        model_name: str = "vertex_ai/gemini-3-flash-preview",
        max_output_tokens: int = 65536,
        save_prompts: bool = False,
        domain_config=None,
    ):
        if domain_config is None:
            raise ValueError("TaskGenerator requires an explicit backend domain_config")
        self.domain = domain_config.domain
        if domain is not None and domain != self.domain:
            raise ValueError("Domain does not match backend configuration")

        tool_spec_retriever = ToolsSpecRetriever(path=domain_config.tool_spec_path)
        self.tool_spec_json = tool_spec_retriever.get_tool_spec_json()

        self.llm_caller = domain_config.make_llm(
            model_name=model_name,
            max_output_tokens=max_output_tokens,
            save_prompts=save_prompts,
        )
        self.policy = domain_config.load_policy()
        self.prompt_manager = domain_config.prompt_manager()
        self.domain_config = domain_config
        self.db_schema_str = domain_config.get_db_schema_str()

    def _parse(self, response: str, *, clean: bool = False) -> dict:
        try:
            value = LLMResponseParser.extract_json(response)
            if not isinstance(value, dict):
                raise ValueError("Generation output must be a JSON object")
            return self._clean(value) if clean else value
        except IncompatibleBackend:
            raise
        except (ValueError, TypeError, KeyError) as error:
            raise CandidateGenerationError(str(error)) from error

    def _clean(self, value):
        import jsonschema

        if set(value) != {"task", "reference_actions"}:
            raise ValueError("A candidate requires exactly task and reference_actions")
        if not isinstance(value["task"], dict) or not isinstance(value["reference_actions"], list):
            raise ValueError("Candidate task must be an object and reference_actions an array")
        for action in value["reference_actions"]:
            if not isinstance(action, dict) or not isinstance(action.get("name"), str):
                raise ValueError("Reference actions require tool names")
            if not isinstance(action.get("arguments"), dict):
                raise ValueError("Reference action arguments must be objects")
        try:
            jsonschema.validate(value["task"], self.domain_config.task_schema)
        except jsonschema.ValidationError as error:
            raise ValueError(error.message) from error
        return copy.deepcopy(value)

    # -- helpers --

    def _get_example_task_str(self) -> str:
        examples = [seed.native for seed in self.domain_config.seeds() if seed.native][:4]
        return json.dumps(examples, indent=2) if examples else "No example available"

    def _get_reference_plan_str(self, action_sequence: List[str]) -> str:
        return json.dumps(
            [{"name": name, "arguments": "generate complete arguments"} for name in action_sequence], indent=2
        )

    def _get_task_context_str(self, task_dict: Dict[str, Any]) -> str:
        return json.dumps(task_dict["task"], indent=2)

    # -- LLM generation steps --

    def create_user_task(self, action_sequence: List[str], error_context: str = "") -> Dict[str, Any]:
        """Use LLM to create a user task with scenario and action arguments."""
        prompt = self.prompt_manager.get_prompt("create_user_task").format(
            domain=self.domain,
            policy=self.policy,
            tool_spec=self.tool_spec_json,
            example_task=self._get_example_task_str(),
            reference_plan=self._get_reference_plan_str(action_sequence),
            action_sequence=action_sequence,
            error_context=error_context,
            task_schema=json.dumps(self.domain_config.task_schema),
        )
        response = self.llm_caller.call(prompt=prompt, stage_name="create_user_task")
        candidate = self._parse(response, clean=True)
        if [action["name"] for action in candidate["reference_actions"]] != action_sequence:
            raise CandidateGenerationError("Generated reference actions changed the selected sequence")
        return candidate

    def generate_db_initialization(self, task_dict: Dict[str, Any], error_context: str = "") -> Dict[str, Any]:
        """Use LLM to generate DB entities with correct relationships."""
        action_arguments = task_dict["reference_actions"]
        prompt = self.prompt_manager.get_prompt("generate_db_initialization").format(
            task_context=self._get_task_context_str(task_dict),
            action_arguments=json.dumps(action_arguments, indent=2, default=str),
            policy=self.policy,
            db_schema=self.db_schema_str,
            error_context=error_context,
        )
        response = self.llm_caller.call(prompt=prompt, stage_name="generate_db_initialization")
        return self._parse(response)

    def patch_task(self, task_dict: Dict[str, Any], db_entities: Dict[str, Any], error: str) -> Dict[str, Any]:
        """Fix a specific error in an existing task via LLM."""
        prompt = self.prompt_manager.get_prompt("patch_task").format(
            error=error,
            policy=self.policy,
            task_json=json.dumps(task_dict, indent=2, default=str),
            db_json=json.dumps(db_entities, indent=2, default=str),
            task_schema=json.dumps(self.domain_config.task_schema),
            tool_spec=self.tool_spec_json,
            db_schema=self.db_schema_str,
        )
        response = self.llm_caller.call(prompt=prompt, stage_name="patch_task")
        candidate = self._parse(response, clean=True)
        if [a["name"] for a in candidate["reference_actions"]] != [a["name"] for a in task_dict["reference_actions"]]:
            raise CandidateGenerationError("Repair changed the selected sequence")
        return candidate

    def patch_db(self, task_dict: Dict[str, Any], db_entities: Dict[str, Any], error: str) -> Dict[str, Any]:
        """Fix a specific error in an existing DB via LLM."""
        prompt = self.prompt_manager.get_prompt("patch_db").format(
            error=error,
            policy=self.policy,
            task_json=json.dumps(task_dict, indent=2, default=str),
            db_json=json.dumps(db_entities, indent=2, default=str),
            task_schema=json.dumps(self.domain_config.task_schema),
            tool_spec=self.tool_spec_json,
            db_schema=self.db_schema_str,
        )
        response = self.llm_caller.call(prompt=prompt, stage_name="patch_db")
        return self._parse(response)

    def generate(self, action_sequence: List[str], error_context: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Generate a complete task: user task + DB initialization (two LLM calls)."""
        task_dict = self.create_user_task(action_sequence, error_context)
        db_entities = self.generate_db_initialization(task_dict)
        return task_dict, db_entities
