"""The HTML draft of Form 1040-SR, where every number explains itself.

Owner: Security.

One self-contained file (inline CSS and a few lines of script, no CDN, works offline). Lines are
laid out in 1040-SR order and sections. Every dollar value is a button: hovering, focusing it with
the keyboard or clicking it opens a popover with the value's provenance (formula, sources, tool,
method note, who entered it and when). Escape closes it. The page is watermarked
"CLASSROOM DRAFT - DO NOT FILE" and shows SSNs only as ***-**-1234.
"""
from __future__ import annotations

from html import escape

from src.form.labels import (FILING_STATUS_LABELS, LINE_LABELS, SECTIONS, describe_provenance, friendly_time,
                             money)
from src.security import mask_ssn, redact_pii

WATERMARK = "CLASSROOM DRAFT - DO NOT FILE"
BOX_LABELS = (("tp_65", "You were born before January 2, 1961"), ("tp_blind", "You are blind"),
              ("sp_65", "Spouse was born before January 2, 1961"), ("sp_blind", "Spouse is blind"))

STYLE = """
:root { --ink: #1b1f24; --muted: #5a6370; --line: #c9cfd6; --paper: #ffffff; --tint: #f3f5f8;
        --accent: #1f5fae; --pop: #fffdf4; --warn: #9a3412; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--tint); color: var(--ink);
       font: 18px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; position: relative; }
.banner { background: var(--warn); color: #fff; font-weight: 700; text-align: center; padding: 10px 16px;
          letter-spacing: .04em; }
.watermark { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
             pointer-events: none; z-index: 0; }
.watermark span { transform: rotate(-30deg); font-size: clamp(36px, 7vw, 88px); font-weight: 800;
                  color: rgba(154, 52, 18, .08); white-space: nowrap; }
.sheet { position: relative; z-index: 1; background: var(--paper); border: 1px solid var(--line);
         border-radius: 8px; padding: 24px; margin-bottom: 20px; }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 22px; margin: 0 0 12px; padding-bottom: 6px; border-bottom: 2px solid var(--ink); }
.sub { color: var(--muted); margin: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px 24px; }
.field { display: flex; flex-direction: column; }
.field span { color: var(--muted); font-size: 15px; }
.field strong { font-size: 20px; }
ul.checks { list-style: none; padding: 0; margin: 0; }
ul.checks li { padding: 2px 0; }
.box { display: inline-block; width: 1.1em; text-align: center; border: 1.5px solid var(--ink); border-radius: 3px;
       margin-right: 8px; font-weight: 700; line-height: 1.05; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 15px; color: var(--muted); font-weight: 600; }
td.no { width: 56px; font-weight: 700; }
td.amount { width: 180px; text-align: right; position: relative; }
.amt { font: inherit; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--accent); background: none;
       border: 1px dashed transparent; border-radius: 4px; padding: 2px 6px; cursor: pointer; }
.amt:hover, .amt[aria-expanded="true"] { border-color: var(--accent); background: #eef4fb; }
.amt:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.prov { display: none; position: absolute; right: 0; top: calc(100% - 4px); z-index: 10; width: min(440px, 88vw);
        text-align: left; background: var(--pop); border: 1px solid #d9c98f; border-radius: 8px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .15); padding: 12px 14px; font-size: 16px; }
.amt:hover + .prov, .amt:focus + .prov, .amt[aria-expanded="true"] + .prov, .prov:hover { display: block; }
.prov dl { margin: 0; }
.prov dt { font-weight: 700; margin-top: 6px; }
.prov dt:first-child { margin-top: 0; }
.prov dd { margin: 0; overflow-wrap: anywhere; }
.prov ul { margin: 0; padding-left: 18px; }
.hint { color: var(--muted); font-size: 15px; margin: 0 0 12px; }
.note { font-size: 16px; }
.pass { color: #166534; font-weight: 700; } .fail { color: var(--warn); font-weight: 700; }
h3 { font-size: 18px; margin: 16px 0 6px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (max-width: 600px) { body { font-size: 17px; } .sheet { padding: 16px 12px; } td.no { width: 40px; }
  td.amount { width: auto; white-space: nowrap; } .amt { padding: 2px 2px; } }
@media print { body { background: #fff; } .prov { display: none !important; } .watermark { position: absolute; } }
"""

SCRIPT = """
document.querySelectorAll('.amt').forEach(function (button) {
  button.addEventListener('click', function () {
    var open = button.getAttribute('aria-expanded') === 'true';
    document.querySelectorAll('.amt[aria-expanded="true"]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
    button.setAttribute('aria-expanded', open ? 'false' : 'true');
  });
});
document.addEventListener('keydown', function (event) {
  if (event.key !== 'Escape') return;
  document.querySelectorAll('.amt[aria-expanded="true"]').forEach(function (b) { b.setAttribute('aria-expanded', 'false'); });
  if (document.activeElement && document.activeElement.classList.contains('amt')) document.activeElement.blur();
});
"""


def _answer(state: dict, qid: str):
    return ((state.get("intake") or {}).get(qid) or {}).get("value")


def _text(value) -> str:
    """Escaped display text; any identity number that slipped into free text is masked first."""
    return escape(redact_pii(str(value if value is not None else "")))


def _field(label: str, value: str) -> str:
    return f'<div class="field"><span>{escape(label)}</span><strong>{value}</strong></div>'


def _check(checked: bool, label: str) -> str:
    return f'<li><span class="box" aria-hidden="true">{"X" if checked else "&nbsp;"}</span>' \
           f'<span class="sr">{"Checked: " if checked else ""}</span>{escape(label)}</li>'


def _people(state: dict) -> str:
    status = (state.get("rules") or {}).get("filing_status") or _answer(state, "filing_status")
    name = f"{_answer(state, 'taxpayer_first_name') or ''} {_answer(state, 'taxpayer_last_name') or ''}".strip()
    fields = [_field("Your name", _text(name or "Not given")),
              _field("Your social security number", escape(mask_ssn(_answer(state, "taxpayer_ssn"))))]
    if status in ("mfj", "mfs"):
        spouse = f"{_answer(state, 'spouse_first_name') or ''} {_answer(state, 'spouse_last_name') or ''}".strip()
        fields += [_field("Spouse's name", _text(spouse or "Not given")),
                   _field("Spouse's social security number", escape(mask_ssn(_answer(state, "spouse_ssn"))))]
    statuses = "".join(_check(code == status, label) for code, label in FILING_STATUS_LABELS.items())
    boxes = (state.get("rules") or {}).get("boxes") or {}
    twelve_d = "".join(_check(bool(boxes.get(key)), label) for key, label in BOX_LABELS
                       if key.startswith("tp") or status in ("mfj", "mfs"))
    return (f'<section class="sheet" aria-labelledby="who"><h2 id="who">Filing status and taxpayer</h2>'
            f'<div class="grid">{"".join(fields)}</div>'
            f'<div class="grid" style="margin-top:16px"><div><h3>Filing status</h3><ul class="checks">{statuses}</ul></div>'
            f'<div><h3>Line 12d: age and blindness</h3><ul class="checks">{twelve_d}</ul></div></div></section>')


def _dependents(state: dict) -> str:
    people = _answer(state, "dependents") or []
    if not people:
        body = '<p class="note">No dependents were listed.</p>'
    else:
        rows = "".join(f"<tr><td>{_text(p.get('first_name'))} {_text(p.get('last_name'))}</td>"
                       f"<td>{escape(mask_ssn(p.get('ssn')))}</td><td>{_text(p.get('relationship'))}</td></tr>"
                       for p in people)
        body = f"<table><thead><tr><th>Name</th><th>SSN</th><th>Relationship</th></tr></thead><tbody>{rows}</tbody></table>"
    return f'<section class="sheet" aria-labelledby="deps"><h2 id="deps">Dependents</h2>{body}</section>'


def _popover(line_key: str, line: dict, extracted: dict) -> str:
    info = describe_provenance(line, extracted)
    sources = "".join(f"<li>{_text(s)}</li>" for s in info["sources"]) or "<li>None recorded</li>"
    return (f'<div class="prov" id="prov-{line_key}" role="tooltip"><dl>'
            f"<dt>Formula</dt><dd>{_text(info['formula'])}</dd>"
            f"<dt>Sources</dt><dd><ul>{sources}</ul></dd>"
            f"<dt>Tool</dt><dd>{_text(info['tool'])}</dd>"
            f"<dt>Method</dt><dd>{_text(info['method'])}</dd>"
            f"<dt>Entered</dt><dd>{_text(info['entered'])}</dd></dl></div>")


def _amount_cell(line_key: str, line: dict | None, extracted: dict) -> str:
    if line is None:
        return '<td class="amount"><span class="fail">Not computed</span></td>'
    label = f"Line {line_key}: {money(line['value'])}. Show where this number comes from"
    return (f'<td class="amount"><button type="button" class="amt" aria-describedby="prov-{line_key}" '
            f'aria-expanded="false" aria-label="{escape(label)}">{money(line["value"])}</button>'
            f"{_popover(line_key, line, extracted)}</td>")


def _sections(state: dict) -> str:
    lines, extracted = state.get("lines") or {}, state.get("extracted") or {}
    parts = []
    for title, rows in SECTIONS:
        body = "".join(f'<tr><td class="no">{escape(key)}</td><td>{escape(label)}</td>'
                       f"{_amount_cell(key, lines.get(key), extracted)}</tr>" for key, label in rows)
        slug = title.lower().replace(" ", "-").replace("/", "")
        parts.append(f'<section class="sheet" aria-labelledby="{slug}"><h2 id="{slug}">{escape(title)}</h2>'
                     f'<table><thead><tr><th>Line</th><th>Description</th><th style="text-align:right">Amount ($)</th>'
                     f"</tr></thead><tbody>{body}</tbody></table></section>")
    return "".join(parts)


def _issue_text(issue) -> str:
    """One review issue for display. Review writes dicts; anything else is shown as text, never a crash."""
    if not isinstance(issue, dict):
        return _text(issue)
    return (f"{_text(issue.get('check'))} on line {_text(issue.get('line') or '-')}: expected "
            f"{_text(issue.get('expected'))}, found {_text(issue.get('found'))}")


def _checks(state: dict) -> str:
    review = state.get("review") or {}
    verdict = ('<span class="pass">Passed</span>' if review.get("passed") else
               '<span class="fail">Not passed</span>' if review else "Not run")
    issues = "".join(f"<li>{_issue_text(issue)}</li>" for issue in review.get("issues", []))
    events = "".join(
        f"<li><strong>{_text(e.get('condition'))}</strong>: {_text(e.get('reason'))}<br>"
        f"Decision: {_text((e.get('resolution') or {}).get('decision', 'still open'))} by "
        f"{_text((e.get('resolution') or {}).get('resolved_by', '-'))}, "
        f"{_text(friendly_time((e.get('resolution') or {}).get('resolved_at', '')))}. "
        f"Note: {_text((e.get('resolution') or {}).get('note', ''))}</li>" for e in state.get("hitl", []))
    findings = "".join(f"<li>{_text(f.get('rule_id'))} ({_text(f.get('citation'))}): {_text(f.get('detail'))}</li>"
                       for f in (state.get("rules") or {}).get("findings", []))
    return (f'<section class="sheet" aria-labelledby="checks"><h2 id="checks">How this draft was checked</h2>'
            f"<p>Independent review: {verdict} after {state.get('review_iterations', 0)} round(s).</p>"
            f"{f'<ul>{issues}</ul>' if issues else ''}"
            f"<h3>Volunteer decisions</h3><ul>{events or '<li>No human review was needed.</li>'}</ul>"
            f"<h3>Rules applied</h3><ul>{findings or '<li>None recorded.</li>'}</ul>"
            f'<p class="note">Full record: <code>audit.json</code> in the same folder.</p></section>')


def render_html(state: dict) -> str:
    """The complete HTML document for one session."""
    session = escape(str(state.get("session_id", "")))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Form 1040-SR draft</title>
<style>{STYLE}</style>
</head>
<body>
<div class="banner" role="note">{WATERMARK}: a practice draft for a volunteer to review. It is not a tax return.</div>
<div class="watermark" aria-hidden="true"><span>{WATERMARK}</span></div>
<main>
<section class="sheet"><h1>Form 1040-SR, tax year 2025</h1>
<p class="sub">U.S. Income Tax Return for Seniors. Session {session}.</p></section>
<p class="hint">Hover over, tab to or click any amount to see where it comes from. Press Escape to close.</p>
{_people(state)}
{_dependents(state)}
{_sections(state)}
{_checks(state)}
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""
