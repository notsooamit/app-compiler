"""
LLM Client wrapper around DeepSeek API (OpenAI-compatible).
Handles structured output via function calling, temperature control,
per-request API keys, and thread-safe token tracking.
"""
import os
import json
import re
import threading
import time
import random
import contextvars
from typing import Optional
from openai import OpenAI
from app.config import DEFAULT_MODEL, PRICING, DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, MAX_RETRY_ATTEMPTS

# Per-request token tracking using ContextVar for concurrent request isolation
_token_usage_ctx: contextvars.ContextVar = contextvars.ContextVar("token_usage", default=None)

# Client cache — one OpenAI client per API key (avoids creating new HTTP sessions per call)
_clients: dict = {}
_clients_lock = threading.Lock()
_client_last_used: dict = {}  # Track last access time for eviction






def _get_client(api_key: str) -> OpenAI:
    """Get or create a cached OpenAI client for DeepSeek. One client per API key.
    Periodically evicts stale clients to prevent memory leaks."""
    if not api_key:
        raise ValueError(
            "No DeepSeek API key provided. Please enter your API key in the UI. "
            "Get a key at https://platform.deepseek.com/"
        )
    # Periodically evict stale clients (5% chance per call)
    if random.random() < 0.05:
        _evict_stale_clients()
    # All reads/writes to _clients and _client_last_use under lock
    with _clients_lock:
        if api_key in _clients:
            _client_last_used[api_key] = time.time()
            return _clients[api_key]
        _clients[api_key] = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        _client_last_used[api_key] = time.time()
        return _clients[api_key]


def _evict_stale_clients():
    """Remove clients not used in the last 30 minutes."""
    cutoff = time.time() - 1800  # 30 minutes
    with _clients_lock:
        stale = [k for k, t in _client_last_used.items() if t < cutoff]
        for k in stale:
            _clients.pop(k, None)
            _client_last_used.pop(k, None)


def _get_token_bucket() -> dict:
    """Get or create the current request's token bucket."""
    bucket = _token_usage_ctx.get()
    if bucket is None:
        bucket = {"input": 0, "output": 0}
        _token_usage_ctx.set(bucket)
    return bucket


def get_token_usage() -> dict:
    """Get accumulated token usage for the current request (isolated per context)."""
    return dict(_get_token_bucket())


def reset_token_usage():
    """Reset token usage counters for the current request."""
    _token_usage_ctx.set({"input": 0, "output": 0})


def _track_usage(prompt_tokens: int, completion_tokens: int):
    """Record token usage for the current request."""
    bucket = _get_token_bucket()
    bucket["input"] += prompt_tokens
    bucket["output"] += completion_tokens


def _parse_json_robust(raw: str, context_name: str = "unknown") -> dict:
    """
    Parse potentially malformed JSON from LLM output.
    Handles: extra data, single quotes, trailing commas, unquoted keys.
    """
    attempts = []

    # Attempt 1: Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        attempts.append(f"direct: {e.msg[:80]}")

    # Attempt 2: raw_decode (extract first valid JSON object)
    decoder = json.JSONDecoder()
    try:
        obj, end = decoder.raw_decode(raw)
        # Check if most of the string was consumed (>80%)
        if end > len(raw) * 0.5:
            return obj
        attempts.append(f"raw_decode partial ({end}/{len(raw)} chars)")
    except json.JSONDecodeError as e:
        attempts.append(f"raw_decode: {e.msg[:80]}")

    # Attempt 3: Fix common LLM JSON issues with regex
    fixed = raw
    # Remove trailing commas before } or ]
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)
    # Fix unquoted keys: capture ({ or ,) + key + : and preserve the delimiter
    fixed = re.sub(r'([\{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', fixed)
    # Fix single-quoted keys
    fixed = re.sub(r"'([a-zA-Z_][a-zA-Z0-9_]*)'\s*:", r'"\1":', fixed)
    # Fix single-quoted string values
    fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        attempts.append(f"regex_fix: {e.msg[:80]}")

    # Attempt 4: More aggressive — fix all bare words before colons
    fixed2 = raw
    fixed2 = re.sub(r',\s*}', '}', fixed2)
    fixed2 = re.sub(r',\s*]', ']', fixed2)
    fixed2 = re.sub(r'([\{,])\s*(\w+)\s*:', r'\1"\2":', fixed2)
    try:
        return json.loads(fixed2)
    except json.JSONDecodeError as e:
        attempts.append(f"regex2: {e.msg[:80]}")

    # Attempt 5: Try to find the longest valid JSON substring
    best_obj = None
    best_end = 0
    for start in range(min(10, len(raw))):
        ch = raw[start]
        if ch not in ('{', '['):
            continue
        try:
            obj, end = decoder.raw_decode(raw[start:])
            if end > best_end:
                best_obj = obj
                best_end = end
        except json.JSONDecodeError:
            continue

    if best_obj and best_end > len(raw) * 0.3:
        return best_obj

    # Attempt 6: Truncation recovery — strip back to last complete element, then close brackets
    truncated = raw.rstrip()

    # Try multiple truncation points: strip trailing incomplete values
    for trim_pattern in [
        # Strip incomplete string value: ..."key": "incomple
        r',?\s*"[^"]*":\s*"[^"]*$',
        # Strip incomplete key: ..."incomple
        r',?\s*"[^"]*$',
        # Strip trailing comma or partial value
        r',\s*$',
    ]:
        candidate = re.sub(trim_pattern, '', truncated)
        if candidate != truncated:
            # Count unclosed brackets
            open_braces = candidate.count('{') - candidate.count('}')
            open_brackets = candidate.count('[') - candidate.count(']')
            if open_braces >= 0 and open_brackets >= 0:
                candidate += ']' * open_brackets + '}' * open_braces
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

    # Attempt 7: Progressive truncation — find longest parseable prefix
    # Work backwards from the end, looking for a valid JSON close point
    for i in range(len(truncated) - 1, max(len(truncated) // 2, 100), -1):
        ch = truncated[i]
        if ch not in ('}', ']', '"', '0', '1', '2', '3', '4', '5',
                       '6', '7', '8', '9', 'e', 'l', 's'):
            continue
        candidate = truncated[:i + 1]
        open_braces = candidate.count('{') - candidate.count('}')
        open_brackets = candidate.count('[') - candidate.count(']')
        if open_braces >= 0 and open_brackets >= 0:
            candidate += ']' * open_brackets + '}' * open_braces
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue

    # Attempt 8: Original simple bracket closure (last resort)
    truncated = raw.rstrip()
    # Count unclosed brackets
    open_braces = truncated.count('{') - truncated.count('}')
    open_brackets = truncated.count('[') - truncated.count(']')
    # Check for unterminated string (odd number of unescaped quotes)
    in_string = False
    for i, ch in enumerate(truncated):
        if ch == '"':
            backslash_count = 0
            j = i - 1
            while j >= 0 and truncated[j] == '\\':
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                in_string = not in_string
    if in_string:
        truncated += '"'  # Close the string
    # Close any open containers
    truncated += ']' * open_brackets
    truncated += '}' * open_braces
    try:
        obj, end = decoder.raw_decode(truncated)
        if end > 0:
            return obj
    except json.JSONDecodeError:
        pass

    raise RuntimeError(
        f"Failed to parse JSON from {context_name}. "
        f"Attempts: {'; '.join(attempts)}. "
        f"Raw preview ({len(raw)} chars): {raw[:300]}..."
    )


def _normalize_llm_nulls(data):
    """Recursively replace null arrays with empty lists in LLM output.
    The LLM occasionally emits null where an empty collection is expected."""
    _list_keys = {"tables", "columns", "endpoints", "pages", "rules", "workflows",
                  "roles", "steps", "components", "sections", "items", "relations",
                  "indexes", "middleware", "feature_gates", "fields", "permissions",
                  "navigation", "data_bindings", "access_roles", "auth_methods",
                  "repair_log", "assumptions_log", "clarification_questions",
                  "suggested_fields", "suggested_permissions", "features",
                  "entities_involved", "ambiguities", "entities", "methods",
                  "api_endpoints", "business_rules", "constraints"}

    if isinstance(data, dict):
        for key in list(data.keys()):
            if data[key] is None and key in _list_keys:
                data[key] = []
            elif isinstance(data[key], (dict, list)):
                _normalize_llm_nulls(data[key])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                _normalize_llm_nulls(item)


def structured_call(
    system_prompt: str,
    user_message: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_key: Optional[str] = None,
) -> dict:
    """
    Make a structured LLM call using DeepSeek's function calling to enforce JSON output.

    Args:
        system_prompt: System-level instructions
        user_message: The user message / task
        tool_name: Name of the tool (used for structured output)
        tool_description: Description of what the tool produces
        input_schema: JSON Schema for the tool's output
        model: DeepSeek model to use
        temperature: Sampling temperature (0.1 for near-deterministic)
        max_tokens: Max output tokens
        api_key: Optional per-request API key (user's own key)

    Returns:
        Parsed JSON dict from the structured output
    """
    client = _get_client(api_key)

    last_error = None
    max_attempts = MAX_RETRY_ATTEMPTS
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature + (attempt * 0.05),  # Slightly increase temp on retry
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": input_schema,
                    },
                }],
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                import time as _time
                _time.sleep(0.5 * (attempt + 1))  # Backoff before retry
                continue
            raise

        # Track token usage
        usage = response.usage
        if usage:
            _track_usage(usage.prompt_tokens, usage.completion_tokens)

        # Extract function call output
        choice = response.choices[0]
        if choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            raw_args = tool_call.function.arguments
            try:
                parsed = _parse_json_robust(raw_args, tool_name)
                _normalize_llm_nulls(parsed)
                return parsed
            except RuntimeError as e:
                last_error = e
                if attempt < max_attempts - 1:
                    continue  # Retry
                raise

        # Fallback: try content as JSON
        content = choice.message.content or ""
        if content.strip():
            try:
                parsed = _parse_json_robust(content, tool_name)
                _normalize_llm_nulls(parsed)
                return parsed
            except RuntimeError:
                pass

        last_error = RuntimeError(f"No valid JSON in response. Content: {content[:200]}")
        if attempt < max_attempts - 1:
            continue
        raise last_error

    raise last_error or RuntimeError(f"Failed after {max_attempts} attempts")


def unstructured_call(
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_key: Optional[str] = None,
) -> str:
    """
    Make an unstructured LLM call for text responses.
    Used for clarification questions, repair instructions, etc.
    Includes retry for transient failures.
    """
    client = _get_client(api_key)
    last_error = None

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature + (attempt * 0.05),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            usage = response.usage
            if usage:
                _track_usage(usage.prompt_tokens, usage.completion_tokens)
            content = response.choices[0].message.content or ""
            if content.strip():
                return content
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                import time as _time
                _time.sleep(0.5 * (attempt + 1))

    if last_error:
        raise last_error
    return ""


def estimate_cost(model: str = DEFAULT_MODEL) -> dict:
    """
    Estimate cost based on token usage.
    DeepSeek pricing: $0.14/M input, $0.28/M output tokens (deepseek-chat).
    """
    usage = get_token_usage()
    pricing = PRICING.get(model, PRICING[DEFAULT_MODEL])
    input_cost = (usage["input"] / 1_000_000) * pricing["input"]
    output_cost = (usage["output"] / 1_000_000) * pricing["output"]
    return {
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "total_tokens": usage["input"] + usage["output"],
        "estimated_cost_usd": round(input_cost + output_cost, 6),
        "model": model,
        "provider": "deepseek",
    }
