"""
Ek page ka web tool — statement andar, report bahar.

**Sab se ahem design faisla: kuch bhi store nahi hota.**

Ye bank aur tax ka data hai. Koi database nahi, koi uploaded file disk pe nahi,
koi account nahi, koi log mein raqam nahi. File request ke andar memory mein
parse hoti hai, report banti hai, aur sab kuch response ke saath khatam.

Ye sirf ehtiyat nahi, ye is product ka ek dawa hai — aur aisa dawa jo aksar
muqablay wale nahi kar sakte. Jo cheez store nahi hoti wo chori bhi nahi ho
sakti. Isi liye auth aur billing jaan-boojh kar baad mein hain: pehle ye sabit
ho ke report ki qeemat hai.

CLI wahi core istemal karta hai (core/), is liye dono kabhi alag nahi hote.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse

from core.diagnose import diagnose
from core.fx import ManualRate
from core.importers import generic
from core.importers.wise import ImportProblem, parse_date
from core.importers.wise import parse as parse_wise
from core.money import money
from core.prc import PrcEntry, match, rates_from_prc
from core.report import build, render

app = FastAPI(title="Hisaab", docs_url=None, redoc_url=None)

# Upload ki had — bina is ke koi 500 MB ki file bhej kar memory khatam kar sakta.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hisaab — what your payments really cost</title>
<style>
  :root {{ color-scheme: light dark; --fg:#111; --bg:#fbfaf8; --muted:#666; --line:#ddd; --accent:#0b6b5f; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e8e6e3; --bg:#16181a; --muted:#9a9a9a; --line:#333; --accent:#4fd1c5; }}
  }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  main {{ max-width:720px; margin:0 auto; padding:2.5rem 1.25rem 4rem; }}
  h1 {{ font-size:1.7rem; margin:0 0 .3rem; }}
  .sub {{ color:var(--muted); margin:0 0 2rem; }}
  fieldset {{ border:1px solid var(--line); border-radius:10px; padding:1rem 1.1rem; margin:0 0 1.1rem; }}
  legend {{ padding:0 .4rem; font-weight:600; font-size:.9rem; }}
  label {{ display:block; font-weight:600; margin:.7rem 0 .25rem; font-size:.9rem; }}
  .hint {{ color:var(--muted); font-weight:400; font-size:.82rem; }}
  input[type=file], select, textarea {{ width:100%; box-sizing:border-box; padding:.5rem;
    border:1px solid var(--line); border-radius:7px; background:transparent; color:var(--fg);
    font:inherit; }}
  textarea {{ min-height:80px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85rem; }}
  .row {{ display:flex; gap:.5rem; align-items:center; margin-top:.6rem; }}
  .row input {{ width:auto; }}
  button {{ margin-top:1.2rem; background:var(--accent); color:#fff; border:0; border-radius:8px;
    padding:.7rem 1.3rem; font:600 15px inherit; cursor:pointer; }}
  pre {{ background:rgba(127,127,127,.09); border:1px solid var(--line); border-radius:10px;
    padding:1rem; overflow-x:auto; font-size:.84rem; line-height:1.5; }}
  .privacy {{ border-left:3px solid var(--accent); padding:.6rem .9rem; background:rgba(127,127,127,.06);
    font-size:.88rem; margin:0 0 1.8rem; }}
  footer {{ margin-top:2.5rem; color:var(--muted); font-size:.82rem; border-top:1px solid var(--line);
    padding-top:1rem; }}
</style>
<main>
<h1>What did that payment really cost you?</h1>
<p class="sub">Your statement's fee is not the whole fee. And your export tax may be 4&times; higher than it needs to be.</p>

<p class="privacy"><strong>Nothing is stored.</strong> No account, no database, no saved
files. Your statement is read in memory and discarded when this page renders. What is
never stored cannot be leaked.</p>

{body}

<footer>
Estimates from published rates &mdash; not tax advice. Rates change with every Finance Act;
verify at fbr.gov.pk or with a tax advisor before filing.
</footer>
</main>
"""

FORM = """
<form method="post" action="/report" enctype="multipart/form-data">
  <fieldset>
    <legend>1. Your payment statement</legend>
    <label>Statement CSV <span class="hint">exported from Wise, Payoneer, or your bank</span></label>
    <input type="file" name="statement" accept=".csv,text/csv" required>
    <label>Format</label>
    <select name="statement_format">
      <option value="wise">Wise (transfer export)</option>
      <option value="payoneer">Payoneer (transactions)</option>
      <option value="auto">Something else &mdash; work it out from the columns</option>
    </select>
  </fieldset>

  <fieldset>
    <legend>2. What your bank actually credited</legend>
    <label>ePRC lines <span class="hint">one per line: date, USD, PKR &mdash; from your bank's Proceeds Realization Certificate</span></label>
    <textarea name="prc" placeholder="08-04-2026, 3000.00, 834000.00
07-05-2026, 2500.00, 692500.00"></textarea>
    <p class="hint">Required for Payoneer and bank statements: those files record a
    foreign-currency account, so they cannot show what landed in PKR. For a Pakistani
    filing the ePRC is the figure that counts anyway.</p>
  </fieldset>

  <fieldset>
    <legend>3. Optional &mdash; to see the hidden spread</legend>
    <label>Mid-market rates <span class="hint">one per line: date, rate</span></label>
    <textarea name="rates" placeholder="03-04-2026, 279.50"></textarea>
    <p class="hint">Without these, tax can still be worked out, but the rail's hidden
    spread cannot &mdash; the rate on your statement already includes it.</p>
    <div class="row"><input type="checkbox" name="pseb" id="pseb" value="1">
      <label for="pseb" style="margin:0">I am registered with PSEB</label></div>
  </fieldset>

  <button type="submit">Show me the report</button>
</form>
"""


def _parse_pairs(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO((text or "").strip())):
        cleaned = [c.strip() for c in row if (c or "").strip()]
        if cleaned:
            rows.append(cleaned)
    return rows


def _load_rates(text: str) -> ManualRate:
    source = ManualRate()
    for row in _parse_pairs(text):
        if len(row) < 2:
            continue
        try:
            source.set(parse_date(row[0]), row[1])
        except (ValueError, ArithmeticError):
            continue
    return source


def _load_prc(text: str) -> list[PrcEntry]:
    entries: list[PrcEntry] = []
    for row in _parse_pairs(text):
        if len(row) < 3:
            continue
        try:
            entries.append(
                PrcEntry(
                    issued_on=parse_date(row[0]),
                    usd_amount=money(row[1]),
                    pkr_credited=money(row[2]),
                    certificate_no=row[3] if len(row) > 3 else "",
                )
            )
        except (ValueError, ArithmeticError):
            continue
    return entries


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body))


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


@app.get("/", response_class=HTMLResponse)
def form() -> HTMLResponse:
    return _page(FORM)


@app.post("/report", response_class=HTMLResponse)
async def report(
    statement: UploadFile = File(...),
    statement_format: str = Form("wise"),
    prc: str = Form(""),
    rates: str = Form(""),
    pseb: str | None = Form(None),
) -> HTMLResponse:
    raw = await statement.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return _page(
            f"<pre>That file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB. "
            "A statement export should be far smaller — is it the right file?</pre>" + FORM
        )
    if not raw.strip():
        return _page("<pre>That file is empty.</pre>" + FORM)

    prc_entries = _load_prc(prc)
    excluded_earlier: list[str] = []
    banking_fraction = Decimal("1.00")

    try:
        if statement_format == "wise":
            imported = parse_wise(raw)
            transfers = imported.transfers
            rate_source = _load_rates(rates)
        else:
            if statement_format == "auto":
                mapping = diagnose(raw).suggested_map()
                if mapping is None:
                    return _page(
                        "<h2>I could not read that file</h2><pre>"
                        + _escape(diagnose(raw).explain())
                        + "</pre>" + FORM
                    )
            else:
                mapping = statement_format
            if not prc_entries:
                return _page(
                    "<h2>The ePRC lines are needed for this format</h2>"
                    "<pre>A Payoneer or bank statement records a foreign-currency "
                    "account, so it cannot show what your bank credited in PKR. "
                    "Add one ePRC line per remittance: date, USD, PKR.</pre>" + FORM
                )
            rail = generic.parse(raw, mapping)
            paired = match(rail.credits, prc_entries)
            transfers = paired.matched
            banking_fraction = paired.banking_channel_fraction
            for credit in paired.credits_without_prc:
                excluded_earlier.append(
                    f"{credit.received_on.isoformat()} ${credit.gross_usd} — no matching ePRC"
                )
            rate_source = _load_rates(rates) if rates.strip() else rates_from_prc(prc_entries)
    except ImportProblem as exc:
        return _page(
            f"<h2>I could not read that file</h2><pre>{_escape(str(exc))}\n\n"
            + _escape(diagnose(raw).explain())
            + "</pre>" + FORM
        )

    if not transfers:
        return _page(
            "<h2>No usable payments found</h2><pre>"
            + _escape(diagnose(raw).explain())
            + "</pre>" + FORM
        )

    result = build(
        transfers,
        rate_source,
        pseb_registered=bool(pseb),
        banking_channel_fraction=banking_fraction,
        excluded_earlier=excluded_earlier,
    )
    return _page(
        f"<pre>{_escape(render(result))}</pre>"
        '<p><a href="/">Run another statement</a></p>'
    )
