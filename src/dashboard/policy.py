"""The policy corpus (data/policy.md) as the dashboard shows it.

Owner: Task division and management.

Agents and volunteers cite rules as `policy.md#r-fs-02`. That anchor means nothing to a
reader, so the dashboard turns it into the rule's name with a link to the rule on the policy
page. This module only reads policy.md; Eligibility owns its content.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup, escape

POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "policy.md"
_CITATION = re.compile(r"(\(?)\bpolicy\.md#(r-[a-z0-9]+-\d+)(\)?)", re.IGNORECASE)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


@lru_cache(maxsize=1)
def rules() -> dict[str, dict]:
    """Rules by anchor ('r-fs-02'), in file order: id, section, title, text, source and action."""
    found: dict[str, dict] = {}
    section, current = "", None
    for line in POLICY_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("### "):
            rule_id = line[4:].strip()
            current = found.setdefault(rule_id.lower(), {"id": rule_id, "section": section, "title": "",
                                                         "text": "", "source": "", "action": ""})
        elif current is not None and line.strip():
            if line.startswith("Source:"):
                current["source"] = line[len("Source:"):].strip()
            elif line.startswith("Action:"):
                current["action"] = line[len("Action:"):].strip()
            else:
                title = _BOLD.match(line)
                current["title"] = title.group(1).rstrip(".") if title else current["title"]
                current["text"] = _BOLD.sub("", line, count=1).strip() if title else line.strip()
    return found


def sections() -> list[tuple[str, list[dict]]]:
    """Rules grouped under their section heading, for the policy page."""
    grouped: dict[str, list[dict]] = {}
    for rule in rules().values():
        grouped.setdefault(rule["section"], []).append(rule)
    return list(grouped.items())


def link_citations(text: str) -> Markup:
    """Escape `text` and turn each 'policy.md#r-fs-02' in it into a named link to that rule."""
    def replace(match: re.Match) -> str:
        opening, anchor, closing = match.group(1), match.group(2).lower(), match.group(3)
        rule = rules().get(anchor)
        if rule is None:
            return match.group(0)
        return (f'{opening}<a class="policy-ref" href="/policy#{anchor}" title="{escape(rule["text"].replace("`", ""))}">'
                f'policy rule {escape(rule["id"])}: {escape(rule["title"])}</a>{closing}')
    escaped = str(escape(text or ""))
    return Markup(_CITATION.sub(replace, escaped))


def inline(text: str) -> Markup:
    """Policy text with its `code` spans kept, everything else escaped."""
    return Markup(_CODE.sub(r"<code>\1</code>", str(escape(text or ""))))
