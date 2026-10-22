"""Single entry point for every LLM call.

Owner: Inter agent communication.

Offline mode is mandatory (contract section 1): with no ANTHROPIC_API_KEY, or with
SILVERFILE_OFFLINE=1, `get_llm()` returns None and every agent falls back to deterministic
rules. Agents check `get_llm() is not None` (or `is_offline()`) before calling the model, and
treat any exception from `invoke_with_usage` as "use the fallback".
"""
from __future__ import annotations

import base64
import mimetypes
import os
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage

DEFAULT_MODEL = "claude-sonnet-5"


def is_offline() -> bool:
    """True when no model may be called: no API key, or SILVERFILE_OFFLINE=1."""
    return not os.getenv("ANTHROPIC_API_KEY", "").strip() or os.getenv("SILVERFILE_OFFLINE") == "1"


def get_llm():
    """Return a ChatAnthropic model (MODEL_NAME env, temperature 0), or None in offline mode."""
    if is_offline():
        return None
    return _client(os.getenv("MODEL_NAME") or DEFAULT_MODEL)


@lru_cache(maxsize=4)
def _client(model: str):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, temperature=0, max_tokens=1024, timeout=60, max_retries=2)


def invoke_with_usage(messages: list[BaseMessage]) -> tuple[str, int]:
    """Call the model and return (text, input_tokens) so callers can log token use.

    Raises RuntimeError in offline mode; callers are expected to have checked first.
    """
    model = get_llm()
    if model is None:
        raise RuntimeError("invoke_with_usage called in offline mode")
    response = model.invoke(messages)
    return _text(response.content), _input_tokens(response)


def image_message(path: str | Path, prompt: str) -> HumanMessage:
    """A user message carrying one image (base64) followed by an instruction."""
    path = Path(path)
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return HumanMessage(content=[
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
        {"type": "text", "text": prompt},
    ])


def _text(content) -> str:
    """Flatten a response's content (a string, or a list of content blocks) to plain text."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)


def _input_tokens(response) -> int:
    usage = getattr(response, "usage_metadata", None) or {}
    if "input_tokens" in usage:
        return int(usage["input_tokens"])
    return int((response.response_metadata or {}).get("usage", {}).get("input_tokens", 0))
