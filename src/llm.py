"""Single entry point for every LLM call.

# STUB: owned by Inter agent communication. Minimal version with the signatures from
# prompts/02 (get_llm, is_offline, invoke_with_usage); the owner's real version replaces it.
"""
from __future__ import annotations

import os


def is_offline() -> bool:
    return not os.getenv("ANTHROPIC_API_KEY") or os.getenv("SILVERFILE_OFFLINE") == "1"


def get_llm():
    """Return a ChatAnthropic model, or None in offline mode."""
    if is_offline():
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=os.getenv("MODEL_NAME", "claude-sonnet-4-5"), temperature=0)


def invoke_with_usage(messages: list) -> tuple[str, int]:
    """Call the model and return (text, input_tokens) so callers can log token use."""
    response = get_llm().invoke(messages)
    return str(response.content), response.usage_metadata["input_tokens"]
