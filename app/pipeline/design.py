"""
Stage 2: System Design
Converts Intent IR into a detailed Architecture IR.
Defines entities with fields, pages, API endpoints, auth, and business rules.
"""
import json
from typing import Optional
from .llm import structured_call
from app.validation.contracts import ARCHITECTURE_IR_SCHEMA

SYSTEM_PROMPT = """You are the System Design stage of a software generation compiler.
Your job: convert a structured Intent IR into a detailed Architecture IR.

RULES:
1. Design complete entity schemas -- every entity needs fields with types, and relationships.
2. Create pages for every feature -- each page needs components and data bindings.
3. Design RESTful API endpoints covering all CRUD operations for every entity.
4. Define granular permissions for each role (e.g., "contacts:create", "reports:view").
5. Write specific business rules (e.g., "IF user.plan != 'premium' THEN block premium features").
6. Every API endpoint must declare what entity it operates on.
7. Every page data_binding must reference a real API endpoint path.
8. Document ALL assumptions you make (default auth method, pagination behavior, etc.).
9. Be internally consistent -- entity names must match across pages, APIs, and rules.
10. If the Intent IR has ambiguities with assumed answers, USE those assumptions."""


def run(intent_ir: dict, api_key: Optional[str] = None) -> dict:
    """
    Run Stage 2: System Design.

    Args:
        intent_ir: The Intent IR from Stage 1.
        api_key: Optional user-provided DeepSeek API key.

    Returns:
        Architecture IR dict following ARCHITECTURE_IR_SCHEMA.
    """
    return structured_call(
        system_prompt=SYSTEM_PROMPT,
        user_message=f"Design the complete architecture from this intent:\n\n{json.dumps(intent_ir, indent=2)}",
        tool_name="output_architecture_ir",
        tool_description="Output the complete Architecture IR with entities, pages, APIs, auth, and business rules",
        input_schema=ARCHITECTURE_IR_SCHEMA,
        api_key=api_key,
    )
