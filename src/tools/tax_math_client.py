"""Client for the tax_math MCP server (contract section 9).

Owner: MLOps/DevOps.

    from src.tools.tax_math_client import call_tool
    result = call_tool("standard_deduction", filing_status="single", box_count=1)
    # {"value": 17750, "formula": "15,750 base + 1 x 2,000 = 17,750", "inputs": {...},
    #  "tool": "standard_deduction", "method_note": "..."}

Two transports, same functions, same results:

- stdio (default): the first call starts `python -m src.tools.tax_math_server` as a child
  process and keeps one MCP session open for the life of this Python process, so a return
  with ~40 tool calls pays the start-up cost once. The session runs on a background event
  loop thread, which lets synchronous LangGraph nodes call it.
- in-process (TAX_MATH_INPROCESS=1): imports the server module and calls the function
  directly. Used by tests and quick local runs.

Every call is written to the session trace (`traces/<session_id>.jsonl`) with its inputs,
output, transport and duration. A tool error (bad filing status, negative amount, ...) is
raised as TaxMathError; callers never get a guessed number back.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from src.tools.tracing import write_event

ToolResult = dict[str, Any]
REPO_ROOT = Path(__file__).resolve().parents[2]
CALL_TIMEOUT_SECONDS = 30.0
START_TIMEOUT_SECONDS = 30.0


class TaxMathError(ValueError):
    """A tax_math tool rejected its inputs or could not be reached."""


def inprocess() -> bool:
    return os.getenv("TAX_MATH_INPROCESS") == "1"


def call_tool(name: str, **kwargs: Any) -> ToolResult:
    """Call one tax_math tool and return its result record. Raises TaxMathError on failure."""
    transport = "inprocess" if inprocess() else "stdio"
    started = time.perf_counter()
    try:
        result = _call_inprocess(name, kwargs) if transport == "inprocess" else _connection().call(name, kwargs)
    except TaxMathError as error:
        _trace(name, kwargs, None, transport, started, error=str(error))
        raise
    _trace(name, kwargs, result, transport, started)
    return result


def _trace(name: str, kwargs: dict, result: ToolResult | None, transport: str, started: float,
           error: str | None = None) -> None:
    write_event({"kind": "tool_call", "tool": name, "inputs": kwargs, "output": result, "error": error,
                 "transport": transport, "duration_ms": round((time.perf_counter() - started) * 1000, 2)})


# ---------------------------------------------------------------------------
# In-process transport
# ---------------------------------------------------------------------------

def _call_inprocess(name: str, kwargs: dict) -> ToolResult:
    from src.tools.tax_math_server import TOOLS

    if name not in TOOLS:
        raise TaxMathError(f"unknown tax_math tool {name!r}; expected one of {', '.join(TOOLS)}")
    try:
        return TOOLS[name](**kwargs)
    except (TypeError, ValueError) as error:
        raise TaxMathError(f"{name}: {error}") from error


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------

class _StdioConnection:
    """One long-lived MCP client session to the tax_math server, driven from a background loop."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="tax_math_mcp", daemon=True)
        self._stop: asyncio.Event | None = None
        self._session: Any = None
        self._runner: Future | None = None

    def start(self) -> None:
        self._thread.start()
        ready: Future = Future()
        self._runner = asyncio.run_coroutine_threadsafe(self._hold_session(ready), self._loop)
        try:
            ready.result(timeout=START_TIMEOUT_SECONDS)
        except Exception as error:  # the server failed to start; surface why
            raise TaxMathError(f"could not start the tax_math MCP server: {error}") from error

    async def _hold_session(self, ready: Future) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=["-m", "src.tools.tax_math_server"],
                                       cwd=str(REPO_ROOT), env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        self._stop = asyncio.Event()
        try:
            async with stdio_client(params) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    self._session = session
                    ready.set_result(True)
                    await self._stop.wait()
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            raise

    def call(self, name: str, arguments: dict) -> ToolResult:
        future = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, arguments), self._loop)
        try:
            response = future.result(timeout=CALL_TIMEOUT_SECONDS)
        except Exception as error:
            raise TaxMathError(f"{name}: MCP call failed: {error}") from error
        text = "".join(getattr(block, "text", "") for block in response.content)
        if getattr(response, "is_error", None) or getattr(response, "isError", None):
            raise TaxMathError(f"{name}: {text}")
        return json.loads(text)

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._runner is not None:
            try:
                self._runner.result(timeout=5)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)


_lock = threading.Lock()
_active: _StdioConnection | None = None


def _connection() -> _StdioConnection:
    global _active
    with _lock:
        if _active is None:
            connection = _StdioConnection()
            connection.start()
            atexit.register(connection.close)
            _active = connection
        return _active


def close() -> None:
    """Stop the stdio server, if one was started. The next call starts a fresh one."""
    global _active
    with _lock:
        if _active is not None:
            _active.close()
            _active = None
