"""
Stage 1: Intent Extraction
Parses raw natural language into a structured Intent IR.
Detects ambiguity, vagueness, conflicts, and missing information.
"""
from typing import Optional
from .llm import structured_call
from app.validation.contracts import INTENT_IR_SCHEMA

SYSTEM_PROMPT = """You are the Intent Extraction stage of a software generation compiler.
Your job: parse a user's natural language app idea into a structured Intent IR.

RULES:
1. Extract ALL mentioned features, entities, roles, and constraints.
2. Be thorough -- list every entity and feature the user mentions or implies.
3. Detect ambiguity: if requirements are vague, conflicting, or incomplete, flag them.
4. For vague prompts (fewer than 3 specific features), set is_vague=true.
5. For conflicting requirements (e.g., "free app but users must pay"), set has_conflicts=true.
6. For incomplete inputs (e.g., "login system" with no other features), set is_incomplete=true.
7. Generate clarification_questions only for BLOCKING ambiguities -- be judicious.
8. For minor ambiguities, make a reasonable assumption and document it in assumed_answer.
9. NEVER invent features the user didn't mention. Only extract what's there.
10. Classify complexity as simple (1-2 entities), medium (3-5), or complex (6+)."""


def run(user_prompt: str, api_key: Optional[str] = None) -> dict:
    """
    Run Stage 1: Intent Extraction.

    Args:
        user_prompt: Raw natural language description of the desired app.
        api_key: Optional user-provided DeepSeek API key.

    Returns:
        Intent IR dict following INTENT_IR_SCHEMA.
    """
    return structured_call(
        system_prompt=SYSTEM_PROMPT,
        user_message=f"Parse this app idea into structured intent:\n\n{user_prompt}",
        tool_name="output_intent_ir",
        tool_description="Output the structured Intent IR for the given app description",
        input_schema=INTENT_IR_SCHEMA,
        api_key=api_key,
    )
