# Hisaab

**Income and tax operations for Pakistani freelancers earning in foreign currency.**

Two questions, both of which quietly cost Pakistani freelancers real money every month:

1. **What did that payment actually cost me?** Not the advertised fee — the fee *plus*
   the FX spread nobody itemises.
2. **Am I paying the right tax on export income?** The gap between doing this correctly
   and doing it wrong is not small.

## The numbers this is built on

Computed by [`core/taxrules.py`](core/taxrules.py) and [`core/money.py`](core/money.py)
from published rates, for a freelancer earning ~$36,000/year:

| Lever | Annual difference |
|---|---|
| PSEB-registered (0.25%) vs unregistered IT export (1%) | **PKR 75,330** |
| Choosing the cheaper rail (0.78% vs 2.33% all-in) | **PKR 156,000** |
| vs. never claiming export status at all (slab rates) | PKR 3,490,290 |

A tool that recovers even the first two is worth more to the user in a month than it
could reasonably charge in a year. That is the whole business case, and it is arithmetic
rather than a growth story.

## What it does

- **Log each remittance** — client, gross USD, rail, declared fee, the day's mid-market
  rate, PKR actually received.
- **Separate the declared fee from the hidden FX spread.** Rails advertise "low fees" and
  earn on the spread; `hidden_spread_fraction` exists to check that claim. In the example
  above, Payoneer's declared 1% is really 2.33% all-in.
- **Compare rails on your own history**, not on marketing pages.
- **Estimate export tax** per tax year, against the cited rule, with the PSEB-registered
  and unregistered cases side by side.
- **Track what filing needs**: PSEB status, the 80%-through-banking-channels condition,
  PRC/ePRC collected per remittance.

## What it deliberately does not do

These are boundaries, not a backlog.

- **It never touches money.** No transfers, no custody, no payment processing — that is
  money transmission and it needs licensing. This is a ledger and a calculator.
- **It never asks for your bank or Payoneer password**, and it does not scrape their
  dashboards. CSV import and manual entry; official APIs only, with a token you issue
  yourself and can revoke.
- **It does not file anything on your behalf.** No automated FBR submission.
- **It is not tax advice.** Every figure comes back labelled as an estimate, with the rule
  it used and a link to the source. `TaxEstimate.is_estimate` is a field precisely so that
  anything rendering it has to carry that through.

## The rule that makes the tax engine safe

Rates change with every Finance Act, and a wrong rate means someone files a wrong return
and bears the consequence. So:

- Every rate is stored **with its tax year, source URL and verification date**. A rate
  without a source is a rumour.
- **An unknown tax year raises `UnknownTaxYear`** rather than silently applying this
  year's rate to last year's income. Refusing to answer beats answering wrongly.
- **The 80% banking-channel condition is enforced.** If too little income arrived through
  formal channels, the reduced rate is *withheld* and the reason is returned as a warning.
  A tool that skipped this check would show a saving the user isn't entitled to, and they
  would underpay on the strength of it. `test_the_reduced_rate_is_withheld_when_banking_channel_is_too_low`
  exists for exactly that.

## Money is never a float

`0.1 + 0.2` is `0.30000000000000004`. Across 50 remittances a year those errors accumulate
into a number someone sends to FBR. Everything uses `Decimal`, with rounding stated
explicitly — money to 2 places, FX rates to 4, percentages to 6.

## Status

**Working end to end from the command line** — 43 tests.

```bash
python cli.py statement.csv --rates rates.csv --pseb
```

Built: the money engine, the tax engine, the Wise CSV importer, the FX rate source, the
report, and a CLI. Exit codes are meaningful — `1` means the report is incomplete
(a payment had no rate), `2` means the file could not be read.

Deliberately **not** built yet: accounts, billing, web UI, PDF export. The report is what
saves the user money; auth and dashboards do not. Those come after someone pays for a
report, not before.

### Why the rate has to come from your PRC

The spread cannot be derived from the statement alone — the "Exchange rate" Wise reports
is *the rate they gave you*, so measuring the spread against it always yields zero. An
independent mid-market rate is required, and on 2026-09-15 none was freely available:

| Source | Result |
|---|---|
| Frankfurter (ECB) | **PKR not covered** — 30 currencies only |
| exchangerate.host | now requires an API key |
| exchangerate-api (free) | `latest` only, no history |
| stooq | JavaScript proof-of-work challenge |
| State Bank of Pakistan | HTTP 403 |

Which turns out not to be a limitation. **The bank's PRC/ePRC states the rate and the PKR
credited**, and for a Pakistani filing that document is the authoritative one. A
third-party API figure that disagrees with your own PRC is worse than useless. So manual
entry from the PRC is the primary path, rates carry `is_authoritative`, and hand-entered
rates are flagged in the report.

> **Honesty note:** the tax rates were verified on 2026-09-15 against a published guide,
> not against the Finance Act text or an FBR circular. Before anyone relies on this for a
> real filing, the rates need checking against fbr.gov.pk, and ideally a tax practitioner
> should review the logic. The engine is built so that this is a data change in one file,
> not a code change.
>
> Rail fee figures in the examples are realistic, not measured. Real per-rail benchmarks
> have to come from actual user remittances — which is also the dataset that would make
> this defensible over time.
>
> **The Wise importer is written against Wise's *documented* CSV format, not a real
> export.** The column names, the DD-MM-YYYY dates and the separate
> `"Wise charges for transfer X"` fee row all come from their help documentation. In the
> previous project every single feed bug was found by hitting real data and none by
> reading docs, so this parser should be considered unverified until it has run against an
> actual statement. Payoneer is not supported at all yet, for the same reason.

## The three parsing traps, and why each one matters

All three are handled, and each has a test named after it:

1. **Dates are DD-MM-YYYY.** `03-04-2026` is 3 April, not 4 March. A misparsed date lands
   income in the wrong tax year, and the tax year is the basis of the whole calculation.
   An unrecognised format raises rather than guesses.
2. **The rail's rate is not the mid-market rate** (above).
3. **Fees can arrive as their own row.** Ignoring them understates cost; counting them
   twice overstates it. Both are wrong, so fee rows are matched to their transfer by ID
   and a fee row for an unknown transfer is reported rather than dropped.

And the rule the report holds to: **a payment with no recorded rate is never silently
skipped.** It is named in a `PAYMENTS EXCLUDED` section and the process exits `1`, because
quietly dropping it would understate export income — and that number goes on a tax return.

## Running it

```bash
python -m pytest tests -q
```
