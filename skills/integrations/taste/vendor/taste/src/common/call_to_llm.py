from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, cast

if TYPE_CHECKING:
    from vertexai.generative_models import GenerationConfig, GenerationResponse
from dataclasses import dataclass

from src.backends.contracts import InfrastructureError
from src.common.sequence_evidence import EvidenceWriteError, error_metadata

_DEFAULT_PROMPTS_DIR = os.path.join(os.environ.get("TASTE_WORKSPACE", os.getcwd()), "logs", "current_prompts")


@dataclass
class LLMUsageInfo:
    """Information about LLM usage and cost for a single call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    model_name: str

    def __str__(self) -> str:
        return (
            f"Tokens: {self.prompt_tokens} in / {self.completion_tokens} out / {self.total_tokens} total | "
            f"Cost: ${self.cost_usd:.6f}"
        )


# Pricing per 1M tokens
# https://cloud.google.com/vertex-ai/generative-ai/pricing
GEMINI_PRICING = {
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-3-flash-preview": {"input": 0.30, "output": 2.50},
    "gemini-3-pro": {"input": 2.00, "output": 12.00},
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
}


def _is_vertex_model(model_name: str) -> bool:
    """Check if the model should be routed through Vertex AI (Gemini models).

    Only ``vertex_ai/...`` or a bare ``gemini-...`` name counts as Vertex AI.
    A ``gemini/...`` prefix is litellm's Google AI Studio provider and must
    not be treated as Vertex AI.
    """
    m = model_name.lower().strip()
    if m.startswith("vertex_ai/"):
        return True
    if m.startswith("gemini/"):
        return False
    return m.startswith("gemini")


def _get_litellm_model_name(model_name: str) -> str:
    """Return the model name to use with litellm.completion().

    GPT-5.2 and GPT-5.4+ models must be accessed via OpenAI's Responses API
    (/v1/responses) rather than Chat Completions (/v1/chat/completions).
    Sending a Chat Completions request to these models causes OpenAI to return
    "We could not parse the JSON body of your request."

    Prefixing with "responses/" tells litellm to route through its Responses API
    bridge, which converts the request/response format transparently.
    """
    try:
        from litellm.llms.openai.chat.gpt_5_transformation import OpenAIGPT5Config

        bare = model_name.split("/")[-1] if "/" in model_name else model_name
        if OpenAIGPT5Config.is_model_gpt_5_4_plus_model(bare) or OpenAIGPT5Config.is_model_gpt_5_2_model(bare):
            # "openai/responses/<model>" is the litellm format that routes to
            # the Responses API while keeping the provider explicit.
            return f"openai/responses/{bare}"
    except Exception:
        pass
    return model_name


class LLMConnectivityError(InfrastructureError):
    """Raised when LLM calls fail due to transient connectivity issues after all retries."""

    pass


class LLMCaller:
    """
    A class to handle LLM calls using Vertex AI with Gemini model.
    """

    def __init__(
        self,
        model_name: str = "gemini-3-flash-preview",
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        location: str = "global",
        temperature: float = 0.9,
        top_p: float = 1.0,
        top_k: int = 32,
        max_output_tokens: int = 8192,
        # reasoning_effort: str = "high",
        save_prompts: bool = False,
        prompts_dir: Optional[str] = None,
        evidence=None,
    ):
        """
        Initialize the LLM caller.

        Args:
            model_name: The name of the Gemini model to use
            credentials_path: Path to the Google Cloud credentials JSON file.
                If ``None``, falls back to the ``GOOGLE_APPLICATION_CREDENTIALS``
                environment variable (standard Google Cloud SDK behavior).
            project_id: Google Cloud project ID. If ``None``, falls back to the
                ``GOOGLE_CLOUD_PROJECT`` environment variable.
            location: Google Cloud location
            temperature: Controls randomness in generation (0.0 to 1.0)
            top_p: Nucleus sampling parameter
            top_k: Top-k sampling parameter
            max_output_tokens: Maximum number of tokens in the response
            save_prompts: Whether to save full prompts to a directory
            prompts_dir: Directory to save prompts (defaults to logs/current_prompts/)
        """
        # Auto-prefix bare gemini model names (e.g. "gemini-3-flash-preview")
        # with vertex_ai/. Do NOT touch names that already carry an explicit
        # provider prefix — in particular "gemini/..." is litellm's Google AI
        # Studio provider and must be passed through unchanged.
        lower = model_name.lower()
        if lower.startswith("gemini") and not lower.startswith("vertex_ai/") and not lower.startswith("gemini/"):
            model_name = f"vertex_ai/{model_name}"
        self.model_name = model_name
        self._use_vertex = _is_vertex_model(model_name) and False

        if self._use_vertex:
            import vertexai
            from vertexai.generative_models import GenerationConfig, GenerativeModel

            # Set credentials and initialize Vertex AI. We never overwrite an
            # existing GOOGLE_APPLICATION_CREDENTIALS; only fall back to the
            # explicit ``credentials_path`` argument if the env var is unset.
            if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") is None and credentials_path is not None:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
            resolved_project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
            vertexai.init(project=resolved_project, location=location)
            bare_model = model_name.split("/", 1)[-1] if "/" in model_name else model_name
            self.model = GenerativeModel(bare_model)
            self.generation_config = GenerationConfig(
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_output_tokens=max_output_tokens,
            )
            self._litellm_model_name = model_name  # unused for Vertex path
        else:
            # litellm path — store params for litellm.completion()
            self.model = None
            self.generation_config = None
            # For GPT-5.4+ models, use the Responses API endpoint via litellm's
            # responses/ prefix.  These models require /v1/responses rather than
            # /v1/chat/completions; sending Chat Completions format to them causes
            # OpenAI to return "We could not parse the JSON body of your request."
            self._litellm_model_name = _get_litellm_model_name(model_name)
            # top_p is omitted: it equals the default (1.0) and is unsupported by
            # OpenAI reasoning models that don't have reasoning_effort="none".
            self._litellm_params = {
                "temperature": temperature,
                "max_tokens": max_output_tokens,
                #  "reasoning_effort": reasoning_effort,
            }

        # Usage tracking
        self.last_usage: Optional[LLMUsageInfo] = None
        self.total_cost_usd: float = 0.0
        self.total_calls: int = 0
        self.evidence = evidence
        self._evidence_call_id = None
        self._evidence_attempt = 0

        # Prompt saving
        self.save_prompts = save_prompts
        self.prompts_dir = prompts_dir or _DEFAULT_PROMPTS_DIR
        self._prompt_counter = 0

        if self.save_prompts:
            self._initialize_prompts_dir()

    def _initialize_prompts_dir(self):
        """
        Initialize the prompts directory.
        Clears existing contents if directory exists, then creates fresh.
        """
        if os.path.exists(self.prompts_dir):
            shutil.rmtree(self.prompts_dir)
        os.makedirs(self.prompts_dir, exist_ok=True)
        print(f"📁 Prompts will be saved to: {self.prompts_dir}")

    def _save_prompt(self, prompt: str, stage_name: Optional[str] = None):
        """
        Save a prompt to the prompts directory.

        Args:
            prompt: The full prompt text passed to the LLM
            stage_name: Optional name of the stage/call (e.g., 'create_user_task')
        """
        if not self.save_prompts:
            return

        self._prompt_counter += 1
        timestamp = datetime.now().strftime("%H%M%S")

        # Build filename with counter and optional stage name
        if stage_name:
            filename = f"{self._prompt_counter:02d}_{stage_name}_{timestamp}.txt"
        else:
            filename = f"{self._prompt_counter:02d}_prompt_{timestamp}.txt"

        filepath = os.path.join(self.prompts_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"{'=' * 80}\n")
            if stage_name:
                f.write(f"Stage: {stage_name}\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Model: {self.model_name}\n")
            f.write(f"{'=' * 80}\n\n")
            f.write(prompt)

        print(f"  💾 Saved prompt to: {filename}")

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if an error is a transient/connectivity error worth retrying."""
        if isinstance(error, EvidenceWriteError):
            return False
        if isinstance(error, LLMConnectivityError):
            return True
        error_str = str(error).lower()
        transient_patterns = [
            "503",
            "service unavailable",
            "visibility check",
            "500",
            "internal server error",
            "internal error",
            "429",
            "too many requests",
            "resource exhausted",
            "temporarily unavailable",
            "response was truncated",
        ]
        return any(pattern in error_str for pattern in transient_patterns)

    def _generate_with_retry(
        self,
        full_prompt: str,
        config,
        stream: bool,
        max_retries: int = 3,
        retry_sleep_seconds: int = 15,
    ):
        """
        Call LLM with retry logic for transient errors.

        Routes to Vertex AI (Gemini) or litellm (OpenAI/Anthropic/etc.)
        based on model name. On transient errors, sleeps and retries up to
        max_retries times.
        """
        last_error: Optional[Exception] = None
        total_attempts = max_retries + 1  # 1 original + max_retries

        for attempt in range(1, total_attempts + 1):
            self._evidence_attempt += 1
            attempt_id = self._evidence_attempt
            attempt_started = time.monotonic()
            if self.evidence is not None:
                self.evidence.record("llm_attempt_started", call_id=self._evidence_call_id, attempt=attempt_id)
            try:
                if self._use_vertex:
                    assert self.model is not None
                    response = self.model.generate_content(
                        full_prompt,
                        generation_config=config,
                        stream=stream,
                    )
                else:
                    import litellm

                    response = litellm.completion(
                        model=self._litellm_model_name,
                        messages=[{"role": "user", "content": full_prompt}],
                        stream=stream,
                        **self._litellm_params,
                    )
            except Exception as e:
                if self.evidence is not None:
                    self.evidence.record(
                        "llm_attempt_failed",
                        call_id=self._evidence_call_id,
                        attempt=attempt_id,
                        error=error_metadata(e),
                        elapsed_seconds=time.monotonic() - attempt_started,
                    )
                if not self._is_transient_error(e):
                    raise  # Non-transient error, don't retry

                last_error = e
                if attempt < total_attempts:
                    print(f"  ⚠️  Transient error (attempt {attempt}/{total_attempts}): {type(e).__name__}")
                    print(f"  ⏳ Sleeping {retry_sleep_seconds}s before retry...")
                    time.sleep(retry_sleep_seconds)
                else:
                    print(f"  ❌ Transient error persisted after {total_attempts} attempts: {type(e).__name__}")
            else:
                if self.evidence is not None and not stream:
                    if self._use_vertex:
                        raw = cast(Any, response).to_dict()
                        cost = None
                    else:
                        # Only completion payload fields; never HTTP/client metadata.
                        raw = cast(Any, response).model_dump(
                            include={"id", "object", "created", "model", "choices", "usage"}
                        )
                        try:
                            cost = litellm.completion_cost(completion_response=response)
                        except Exception:
                            cost = None
                    self.evidence.record(
                        "llm_attempt_response",
                        call_id=self._evidence_call_id,
                        attempt=attempt_id,
                        response=raw,
                        cost_usd=cost,
                        elapsed_seconds=time.monotonic() - attempt_started,
                    )
                return response

        raise LLMConnectivityError(
            f"LLM call failed after {max_retries} retries due to transient connectivity issues. "
            f"Last error class: {type(last_error).__name__}"
        )

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate cost in USD based on token usage and model pricing."""
        # Get pricing for current model, default to gemini-3-flash-preview pricing if unknown
        pricing = GEMINI_PRICING.get(self.model_name, GEMINI_PRICING["gemini-3-flash-preview"])

        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def call(
        self,
        prompt: str,
        context: Optional[str] = None,
        stream: bool = False,
        custom_config: Optional[GenerationConfig] = None,
        stage_name: Optional[str] = None,
        evidence_context=None,
    ) -> Any:
        """
        Call the LLM with a prompt and optional context.

        Args:
            prompt: The prompt to send to the LLM
            context: Optional context to prepend to the prompt
            stream: Whether to stream the response
            custom_config: Optional custom generation configuration
            stage_name: Optional name for this call stage (used for prompt saving)

        Returns:
            The LLM's response as a string

        Note:
            After calling, access `self.last_usage` for token counts and cost info.
        """
        # Combine context and prompt if context is provided
        if context:
            full_prompt = f"{context}\n\n{prompt}"
        else:
            full_prompt = prompt

        self.last_usage = None
        self._evidence_attempt = 0
        if self.evidence is not None:
            self._evidence_call_id = self.evidence.record(
                "llm_call_started",
                stage=stage_name,
                context=evidence_context,
                model=self.model_name,
                routed_model=self._litellm_model_name,
                settings=getattr(self, "_litellm_params", {}),
                prompt=full_prompt,
            )

        # Save prompt if enabled
        self._save_prompt(full_prompt, stage_name=stage_name)

        # Use custom config if provided, otherwise use default
        config = custom_config if custom_config else self.generation_config

        # Generate + parse with retry for transient errors (including
        # spurious MAX_TOKENS truncation from the Vertex AI API).
        max_retries = 3
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self._generate_with_retry(full_prompt, config, stream)

                if stream:
                    self.last_usage = None
                    return response

                if not self._use_vertex:
                    return self._handle_litellm_response(response)

                return self._parse_vertex_response(response, config)
            except (ValueError, Exception) as e:
                if self.evidence is not None:
                    self.evidence.record(
                        "llm_call_error", call_id=self._evidence_call_id, outer_attempt=attempt, error=error_metadata(e)
                    )
                if self._is_transient_error(e) and attempt < max_retries:
                    last_error = e
                    print(
                        f"  ⚠️  Transient error during response parsing (attempt {attempt}/{max_retries}): {type(e).__name__}"
                    )
                    print("  ⏳ Sleeping 15s before retry...")
                    time.sleep(15)
                    continue
                raise

        assert last_error is not None
        raise last_error

    def _parse_vertex_response(self, response, config) -> str:
        """Parse a Vertex AI GenerationResponse, raising ValueError on truncation."""
        from vertexai.generative_models import GenerationResponse

        non_stream_response = cast(GenerationResponse, response)
        self._update_usage_info(non_stream_response)

        # Vertex AI GenerationConfig is a proto wrapper; read max_output_tokens
        # from the underlying proto since getattr on the wrapper returns nothing.
        max_tokens = getattr(
            getattr(config, "_raw_generation_config", config),
            "max_output_tokens",
            "unknown",
        )

        try:
            text = non_stream_response.text
        except ValueError as e:
            if hasattr(non_stream_response, "candidates") and len(non_stream_response.candidates) > 0:
                candidate = non_stream_response.candidates[0]
                if hasattr(candidate, "finish_reason") and candidate.finish_reason.name == "MAX_TOKENS":
                    prompt_tokens = (
                        getattr(non_stream_response.usage_metadata, "prompt_token_count", "unknown")
                        if hasattr(non_stream_response, "usage_metadata")
                        else "unknown"
                    )
                    raise ValueError(
                        f"Response exceeded max_output_tokens ({max_tokens}). "
                        f"Prompt was {prompt_tokens} tokens. "
                        f"Consider increasing max_output_tokens or reducing prompt size."
                    ) from e
            raise

        # Check if response was truncated
        if hasattr(non_stream_response, "candidates") and len(non_stream_response.candidates) > 0:
            candidate = non_stream_response.candidates[0]
            if hasattr(candidate, "finish_reason") and candidate.finish_reason.name == "MAX_TOKENS":
                prompt_tokens = (
                    getattr(non_stream_response.usage_metadata, "prompt_token_count", "unknown")
                    if hasattr(non_stream_response, "usage_metadata")
                    else "unknown"
                )
                completion_tokens = (
                    getattr(non_stream_response.usage_metadata, "candidates_token_count", "unknown")
                    if hasattr(non_stream_response, "usage_metadata")
                    else "unknown"
                )
                raise ValueError(
                    f"Response was truncated due to hitting max_output_tokens ({max_tokens}). "
                    f"Prompt: {prompt_tokens} tokens, Output: {completion_tokens} tokens. "
                    f"Consider increasing max_output_tokens or simplifying the request."
                )

        return text

    def _handle_litellm_response(self, response) -> str:
        """Extract text and usage from a litellm ModelResponse."""
        choice = response.choices[0]
        text = choice.message.content or ""

        # Check for truncation
        if choice.finish_reason == "length":
            usage = response.usage
            raise ValueError(
                f"Response was truncated due to hitting max_tokens. "
                f"Prompt: {usage.prompt_tokens} tokens, Output: {usage.completion_tokens} tokens. "
                f"Consider increasing max_output_tokens or simplifying the request."
            )

        # Usage tracking
        usage = response.usage
        prompt_tokens = usage.prompt_tokens or 0
        completion_tokens = usage.completion_tokens or 0
        total_tokens = usage.total_tokens or 0

        try:
            import litellm

            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        self.last_usage = LLMUsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            model_name=self.model_name,
        )
        self.total_cost_usd += cost
        self.total_calls += 1

        return text

    def _update_usage_info(self, response: GenerationResponse) -> None:
        """Extract and store usage info from the response."""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            completion_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            total_tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0

        cost = self._calculate_cost(prompt_tokens, completion_tokens)

        self.last_usage = LLMUsageInfo(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            model_name=self.model_name,
        )

        # Update totals
        self.total_cost_usd += cost
        self.total_calls += 1
