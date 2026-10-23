"""End to end over MCP stdio: start the real tax_math server and call tools through the client."""
import json

import pytest

from src.tools import tax_math_client
from src.tools.tax_math_client import TaxMathError, call_tool
from src.tools.tracing import session


@pytest.fixture
def stdio(monkeypatch, tmp_path):
    monkeypatch.delenv("TAX_MATH_INPROCESS", raising=False)
    monkeypatch.setenv("SILVERFILE_TRACE_DIR", str(tmp_path))
    tax_math_client.close()
    yield tmp_path
    tax_math_client.close()


def test_tool_call_over_stdio(stdio):
    with session("stdio-test"):
        result = call_tool("standard_deduction", filing_status="single", box_count=1)
    assert result["value"] == 17_750
    assert result["formula"] == "15,750 base + 1 x 2,000 = 17,750"
    trace = [json.loads(line) for line in (stdio / "stdio-test.jsonl").read_text().splitlines()]
    assert trace[0]["transport"] == "stdio" and trace[0]["tool"] == "standard_deduction"


def test_stdio_and_inprocess_agree(stdio, monkeypatch):
    kwargs = dict(filing_status="single", benefits=12_000, other_income=30_400, tax_exempt_interest=0, adjustments=0)
    over_stdio = call_tool("taxable_social_security", **kwargs)
    monkeypatch.setenv("TAX_MATH_INPROCESS", "1")
    assert call_tool("taxable_social_security", **kwargs) == over_stdio
    assert over_stdio["value"] == 6_540


def test_stdio_error_is_reported_not_guessed(stdio):
    with pytest.raises(TaxMathError, match="filing_status must be one of"):
        call_tool("standard_deduction", filing_status="married", box_count=1)
