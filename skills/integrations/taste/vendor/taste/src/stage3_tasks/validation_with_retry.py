"""
Patch-based retry logic for task validation.

On validation failure:
1. Patch the specific error (up to MAX_PATCH_ATTEMPTS per validation step)
2. If all patches fail, fully regenerate task+DB from scratch
3. Full regeneration can happen up to MAX_FULL_RETRIES times
4. After all retries exhausted, give up

Patch types by validation step:
- template_check  -> patch task + DB
- db_preflight    -> patch DB only (but task+DB when error is about action args)
- db_schema       -> patch DB only
- solver          -> patch DB only
- coherence       -> patch task + DB
- gt_agent        -> patch task + DB
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from src.backends.contracts import CandidateGenerationError, IncompatibleBackend, InfrastructureError
from src.stage3_tasks.task_generation import TaskGenerator
from src.stage3_tasks.task_validator import TaskValidator, ValidationResult

MAX_PATCH_ATTEMPTS = 3
MAX_FULL_RETRIES = 3


def _apply_patch(
    generator: TaskGenerator,
    task_dict: Dict[str, Any],
    db_entities: Dict[str, Any],
    patch_type: str,
    error: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply a patch to fix the specific validation error."""
    if patch_type == "db_only":
        patched_db = generator.patch_db(task_dict, db_entities, error)
        return task_dict, patched_db
    else:
        patched_task = generator.patch_task(task_dict, db_entities, error)
        patched_db = generator.patch_db(patched_task, db_entities, error)
        return patched_task, patched_db


def validate_task_with_retries(
    action_sequence: List[str],
    generator: TaskGenerator,
    validator: TaskValidator,
    task_dict: Optional[Dict[str, Any]] = None,
    db_entities: Optional[Dict[str, Any]] = None,
    max_retries: int = MAX_FULL_RETRIES,
    max_patch_attempts: int = MAX_PATCH_ATTEMPTS,
    on_retry: Optional[Callable[[int, int, bool], None]] = None,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], ValidationResult, int]:
    """
    Generate, validate, patch on failure, and fall back to full regeneration.

    Retry strategy:
    1. Run the full validation pipeline
    2. On failure: patch the specific error (up to max_patch_attempts times)
    3. If patches don't fix it: fully regenerate task + DB
    4. Repeat up to max_retries full regenerations

    Set max_patch_attempts=0 to skip patching entirely and go straight to
    full regeneration (useful for tight cost budgets — each LLM call is ~30s
    with GPT-5.2). Set max_retries=0 AND max_patch_attempts=0 for one-shot.

    Returns:
        (success, final_task_dict, final_db_entities, final_result, total_retries)
    """
    total_retries = 0
    last_task_dict = task_dict
    last_db_entities = db_entities

    for full_attempt in range(1 + max_retries):
        # Generate initial task if needed (or on full regeneration)
        if task_dict is None:
            try:
                task_dict, db_entities = generator.generate(action_sequence)
            except IncompatibleBackend as error:
                result = ValidationResult(False, False, False, str(error), "incompatible", unsupported=(str(error),))
                return False, {}, {}, result, total_retries
            except CandidateGenerationError as error:
                result = ValidationResult(False, False, False, str(error), "generation")
                if full_attempt == max_retries:
                    return False, {}, {}, result, total_retries
                total_retries += 1
                continue
        elif db_entities is None:
            db_entities = {}

        last_task_dict = task_dict
        last_db_entities = db_entities

        # Run validation
        result = validator.validate_task(task_dict, db_entities)
        task_dict = result.candidate if result.candidate is not None else task_dict
        db_entities = result.fixture if result.fixture is not None else db_entities
        last_task_dict, last_db_entities = task_dict, db_entities

        if result.unsupported:
            return (False, task_dict, db_entities, result, total_retries)

        if result.success:
            return (True, task_dict, db_entities, result, total_retries)

        # Patch loop: try to fix the specific error
        for patch_attempt in range(max_patch_attempts):
            total_retries += 1
            patch_type = "db_only" if result.repair_scope == "fixture" else "task_and_db"

            if on_retry:
                on_retry(total_retries, -1, patch_type == "db_only")

            print(
                f"    Patch attempt {patch_attempt + 1}/{max_patch_attempts} "
                f"(step={result.failed_step}, type={patch_type})"
            )

            try:
                task_dict, db_entities = _apply_patch(
                    generator,
                    task_dict,
                    db_entities,
                    patch_type,
                    result.error or "Unknown error",
                )
                last_task_dict = task_dict
                last_db_entities = db_entities
            except InfrastructureError:
                raise
            except IncompatibleBackend as error:
                result = ValidationResult(False, False, False, str(error), "incompatible", unsupported=(str(error),))
                return False, task_dict, db_entities, result, total_retries
            except CandidateGenerationError as e:
                print(f"    Patch failed with error: {e}")
                break

            result = validator.validate_task(task_dict, db_entities)
            task_dict = result.candidate if result.candidate is not None else task_dict
            db_entities = result.fixture if result.fixture is not None else db_entities
            last_task_dict, last_db_entities = task_dict, db_entities

            if result.unsupported:
                return (False, task_dict, db_entities, result, total_retries)

            if result.success:
                return (True, task_dict, db_entities, result, total_retries)

        # All patches failed -- full regeneration
        if full_attempt < max_retries:
            total_retries += 1
            print(
                f"    Full regeneration {full_attempt + 1}/{max_retries} "
                f"(patches exhausted for step={result.failed_step})"
            )
            task_dict = None
            db_entities = None

    return (False, last_task_dict or {}, last_db_entities or {}, result, total_retries)
