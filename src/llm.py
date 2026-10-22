"""Single entry point for every LLM call.

# STUB: owned by Inter agent communication. Minimal version so Intake can run; the owner's
# real version replaces it.
"""
from __future__ import annotations

import os


def is_offline() -> bool:
    return not os.getenv("ANTHROPIC_API_KEY") or os.getenv("SILVERFILE_OFFLINE") == "1"


def get_chat_model():
    """Return a chat model, or None in offline mode."""
    if is_offline():
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=os.getenv("MODEL_NAME", "claude-sonnet-4-5"), temperature=0)
