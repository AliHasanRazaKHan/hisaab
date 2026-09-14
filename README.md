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

**Domain core: built and tested** (20 tests). This is deliberately the first thing
written, because it is both the riskiest part and the part that is actually defensible —
anyone can build the CRUD around it.

Not built yet: API, web UI, CSV importers, PDF record pack.

> **Honesty note:** the tax rates were verified on 2026-09-15 against a published guide,
> not against the Finance Act text or an FBR circular. Before anyone relies on this for a
> real filing, the rates need checking against fbr.gov.pk, and ideally a tax practitioner
> should review the logic. The engine is built so that this is a data change in one file,
> not a code change.
>
> Rail fee figures in the examples are realistic, not measured. Real per-rail benchmarks
> have to come from actual user remittances — which is also the dataset that would make
> this defensible over time.

## Running it

```bash
python -m pytest tests -q
```
