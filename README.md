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

**Working end to end, as a web page and a CLI** — 81 tests.

```bash
# The web tool — what a freelancer would actually use
uvicorn web.app:app
# then open http://127.0.0.1:8000

# Wise: one statement has everything
python cli.py statement.csv --rates rates.csv --pseb

# Payoneer (or any bank statement): needs the ePRC ledger too — see below
python cli.py payoneer.csv --format payoneer --prc prc.csv --pseb

# Format not recognised? Ask the file what it is.
python cli.py mystery.csv --diagnose
```

### The parsers will break, so the tool explains itself

Both parsers are written to *documented* formats. In the previous project every feed bug
came from hitting real data and **none** from reading documentation, so some real export
will not match. The useful question is what happens then.

`--diagnose` (and any failed import) prints the file's columns, the mapping it would
guess, and a sample row:

```
Found 5 columns, 1 data row(s).
Columns: Posting Date, Particulars, Debit, Credit, Balance

Best guess at the mapping:
  date           <- 'Posting Date'
  amount         <- 'Credit'
  description    <- 'Particulars'
```

That guess can be handed straight to the mapped importer, so an unrecognised bank
statement is a five-second configuration rather than a code change. Header matching falls
back from exact to prefix, which is how Wise's `Target amount (after fees)` is found —
a gap one of these tests caught.

Built: the money engine, the tax engine, the Wise importer, a **mapped importer for any
statement** (Payoneer preset included), the **ePRC ledger and matcher**, the FX rate
source, the report, and a CLI. Exit codes are meaningful — `1` means the report is
incomplete (a payment lacked a rate or an ePRC, so export income is understated), `2`
means the file could not be read.

### Why a Payoneer statement alone is not enough

Payoneer's export gives `Transaction Date, Description, Amount, Currency, Source, Target,
Reference ID` — **no exchange rate and no PKR.** That is not an omission on their part: the
statement records their own currency account. The client sent USD and it arrived as USD.
PKR appears when the money reaches your **bank**, and that transaction is not in the file
at all.

So the two sides are modelled separately and matched:

- **RailCredit** — what the client sent (from the rail statement)
- **PrcEntry** — what the bank credited in PKR (from the ePRC, the document a Pakistani
  export claim actually rests on)

Matching is on amount within a date window, because a withdrawal takes 2–5 days and exact
dates never line up. Each ePRC is consumed once — otherwise one certificate would "prove"
two payments and export income would double. Anything unmatched is **named**, never
assumed, and it makes the report incomplete.

That matching also produces the number the tax rule depends on: **the fraction of income
proven to have arrived through formal banking channels.** Income with no ePRC does not
count toward the 80% condition, so the evidence and the eligibility are the same
calculation rather than two guesses.

Deliberately **not** built yet: accounts, billing, PDF export. The report is what saves
the user money; auth and dashboards do not. Those come after someone pays for a report,
not before.

### Nothing is stored, and that is a feature

There is **no database, no saved upload, no account, and no amount in any log**. The
statement is parsed in memory and everything is discarded when the response renders.
`test_no_database_or_storage_is_configured` asserts that `web/app.py` contains no
storage calls at all, so this cannot quietly stop being true.

This is bank and tax data. What is never stored cannot be leaked — and most competitors
cannot make that claim, because their business model needs the data. It is also why auth
and billing are deferred rather than merely unfinished: a stateless tool has nothing to
protect yet.

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

## Three bugs the unit tests passed and the CLI caught

All three were the same shape: **the part was safe and the join was not.** The safety check
lived inside `estimate()` or `ManualRate`, and the report walked around it. Every one is now
a regression test at the report level.

1. **The 80% banking-channel condition never reached the report.** It read
   `tax["pseb_registered"]`, which is computed at a default 100%, so the 0.25% rate was
   applied even when only 75% was proven by ePRCs. On the sample data the tax came out at
   **PKR 3,816 instead of PKR 15,265** — four times too low, which is a real underpayment.
2. **PRC rates were used as if they were mid-market.** A realised rate already contains the
   spread, so measuring the spread against it returns zero — the report printed
   "Lost to fees + spread: PKR 0.00" and one payment showed a *negative* spread. It now
   refuses to show a cost analysis and says why. `MidMarketRate.is_mid_market` exists for
   exactly this.
3. **Payments dropped during ePRC matching left the report calling itself complete.** They
   never entered `needs_rate`, so `is_complete` stayed true and the process exited `0`
   while understating income. Exclusions from earlier stages now count.

This is the same lesson as the previous project, arriving a different way: unit tests prove
the pieces, and running the thing proves the assembly.

## Running it

```bash
python -m pytest tests -q
```
