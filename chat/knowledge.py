"""
Knowledge base — har jawab ka hawala, aur har adad live.

Do qism ki entries hain:

1. **Sada jawab** — text, jis ke saath `sources` hote hain (file ya doc jahan
   se baat aayi). Koi bhi jawab bina hawale ke nahi.
2. **Computed jawab** — `compute` function jo asli code ko bulata hai. Tax ke
   rates, thresholds, aur laagat ke adad **kabhi likhe hue nahi** — wo
   `core/taxrules.py` aur `core/money.py` se nikalte hain.

Doosri qism is poore module ki wajah hai. Agar rate yahan likha hota to Finance
Act badalne pe do jagah badalna parta, aur ek jagah purani reh jati — aur wo
purani jagah wo hai jahan se user jawab parh raha hai.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from core.money import Remittance, money, rate
from core.taxrules import LATEST_TAX_YEAR, compare_scenarios, rule_for


@dataclass
class KnowledgeEntry:
    id: str
    topic: str
    # Kai phrasings — log ek hi baat kai tarah se poochte hain.
    questions: list[str]
    answer: str = ""
    # Adad wale jawab ke liye — asli code se nikalta hai, likha hua nahi.
    compute: Callable[[], str] | None = None
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.compute is not None:
            computed = self.compute()
            return f"{self.answer}\n\n{computed}".strip() if self.answer else computed
        return self.answer

    @property
    def keyword_set(self) -> set[str]:
        from chat.engine import tokenise

        joined = " ".join(self.keywords)
        return set(tokenise(joined))

    @property
    def question_token_set(self) -> set[str]:
        from chat.engine import tokenise

        return set(tokenise(" ".join(self.questions)))


# --- computed answers: numbers come from the engine, never from memory ------


def _rates_now() -> str:
    rule = rule_for()
    return (
        f"For tax year {rule.tax_year}:\n"
        f"  PSEB-registered IT/ITeS export : {rule.pseb_registered_rate * 100:.2f}% "
        f"({'final tax' if rule.is_final_tax else 'not final'})\n"
        f"  IT export, not registered      : {rule.unregistered_export_rate * 100:.2f}%\n"
        f"  Not claimed as export          : up to {rule.top_slab_rate * 100:.0f}% (slab)\n"
        f"  Banking-channel condition      : at least "
        f"{rule.banking_channel_minimum * 100:.0f}% through formal channels\n\n"
        f"Source: {rule.source_url} (verified {rule.verified_on}).\n"
        "These are read live from core/taxrules.py, so they cannot drift from what the "
        "tool actually applies. Confirm at fbr.gov.pk before filing."
    )


def _saving_example() -> str:
    income = money(36000 * 279)  # ~$36k at ~279 PKR
    c = compare_scenarios(income)
    return (
        f"On PKR {c['export_income_pkr']:,} of export income (~$36,000/yr):\n"
        f"  PSEB-registered  : PKR {c['pseb_registered'].tax_pkr:>12,}\n"
        f"  Unregistered     : PKR {c['unregistered_export'].tax_pkr:>12,}\n"
        f"  Not claimed      : PKR {c['not_claimed_as_export_pkr']:>12,}\n\n"
        f"Registering saves PKR {c['saving_from_registration_pkr']:,} a year; claiming "
        f"export status at all saves PKR {c['saving_vs_not_claiming_pkr']:,}.\n"
        "Computed live by core/taxrules.compare_scenarios()."
    )


def _spread_example() -> str:
    payoneer = Remittance(
        gross_usd=money(3000), net_pkr=money(819000),
        mid_market_rate=rate("279.50"), declared_fee_usd=money(30),
    )
    wise = Remittance(
        gross_usd=money(3000), net_pkr=money(832000),
        mid_market_rate=rate("279.50"), declared_fee_usd=money("13.50"),
    )
    diff = payoneer.total_cost_pkr - wise.total_cost_pkr
    return (
        "A $3,000 payment at a 279.50 mid-market rate:\n"
        f"  Rail A: all-in {payoneer.cost_fraction * 100:.2f}% "
        f"(declared fee {(payoneer.cost_fraction - payoneer.hidden_spread_fraction) * 100:.2f}%, "
        f"hidden spread {payoneer.hidden_spread_fraction * 100:.2f}%)\n"
        f"  Rail B: all-in {wise.cost_fraction * 100:.2f}% "
        f"(declared fee {(wise.cost_fraction - wise.hidden_spread_fraction) * 100:.2f}%, "
        f"hidden spread {wise.hidden_spread_fraction * 100:.2f}%)\n\n"
        f"Difference on one payment: PKR {diff:,} — about PKR {diff * 12:,} a year on a "
        "monthly $3,000.\nComputed live by core/money.Remittance."
    )


def _threshold_now() -> str:
    rule = rule_for()
    return (
        f"At least {rule.banking_channel_minimum * 100:.0f}% of your export income must "
        "arrive through formal banking channels (Payoneer, Wise, SWIFT — not hawala) for "
        f"the {rule.pseb_registered_rate * 100:.2f}% rate to apply.\n\n"
        "Hisaab enforces this rather than assuming it: if too little is proven by ePRCs, "
        f"the reduced rate is withheld and the {rule.unregistered_export_rate * 100:.2f}% "
        "rate is used, with the reason stated. Showing a saving you are not entitled to "
        "would make you underpay on our word."
    )


def _known_years() -> str:
    from core.taxrules import RULES

    years = ", ".join(sorted(RULES))
    return (
        f"Verified rules exist for: {years} (latest: {LATEST_TAX_YEAR}).\n"
        "Asking for any other year raises UnknownTaxYear rather than quietly applying "
        "this year's rate to that year's income. Refusing to answer beats answering "
        "wrongly."
    )


# --- the corpus -------------------------------------------------------------
#
# Teen tarah ke log sawal karte hain, aur teeno ke sawal alag hote hain:
# freelancer (mera paisa kahan gaya?), reviewer (ye kaise bana?), aur
# interviewer (ye faisla kyun kiya?). Corpus teeno ko cover karta hai.

def _e(id, topic, questions, answer="", keywords=(), sources=(), compute=None):
    return KnowledgeEntry(
        id=id, topic=topic, questions=list(questions), answer=answer.strip(),
        keywords=list(keywords), sources=list(sources), compute=compute,
    )


ENTRIES: list[KnowledgeEntry] = [
    # ---------- what it is ----------
    _e("what-is-hisaab", "overview",
       ["What is Hisaab?", "What does this project do?", "What is this tool for?",
        "Explain the project", "What problem does this solve?"],
       "Hisaab works out two things for Pakistani freelancers earning in foreign currency: "
       "what a payment actually cost you once the hidden FX spread is counted, and what "
       "your export tax should be. It reads your payment statement and your bank's ePRC, "
       "and produces a report plus a filing record pack.",
       keywords=["hisaab", "project", "tool", "purpose", "overview", "what"],
       sources=["README.md"]),

    _e("who-is-it-for", "overview",
       ["Who is this for?", "Who should use Hisaab?", "Is this for freelancers?",
        "Target user", "Who is the customer?"],
       "Pakistani freelancers and small agencies invoicing foreign clients in USD, paid "
       "through Payoneer, Wise, Deel or SWIFT, who file export income in Pakistan. The "
       "domain knowledge — PSEB, ePRCs, the banking-channel condition — is specific "
       "to that situation, and that specificity is the point.",
       keywords=["who", "for", "customer", "user", "audience", "freelancer", "agency"],
       sources=["README.md"]),

    _e("why-built", "overview",
       ["Why was this built?", "What is the business case?", "Why does this matter?",
        "Is this worth building?"],
       "Because the two savings are large, recurring, and computable rather than "
       "speculative. A tool that recovers either is worth more to the user in a month "
       "than it could reasonably charge in a year — that is the whole business case, and "
       "it is arithmetic rather than a growth story.",
       compute=_saving_example,
       keywords=["why", "built", "business", "case", "worth", "value", "saving", "money"],
       sources=["README.md", "core/taxrules.py"]),

    # ---------- tax ----------
    _e("tax-rates", "tax",
       ["What are the tax rates?", "What tax do I pay on export income?",
        "What is the PSEB rate?", "How much tax will I owe?", "What is the export tax rate?",
        "Tax rate for freelancers in Pakistan"],
       "",
       compute=_rates_now,
       keywords=["tax", "rate", "rates", "pseb", "export", "percent", "owe", "pay"],
       sources=["core/taxrules.py"]),

    _e("pseb-worth-it", "tax",
       ["Is PSEB registration worth it?", "Should I register with PSEB?",
        "What does PSEB registration save me?", "Why register with PSEB?"],
       "Registration moves you from the unregistered export rate to the PSEB rate — a "
       "four-fold difference on the same income.",
       compute=_saving_example,
       keywords=["pseb", "registration", "register", "worth", "save", "saving"],
       sources=["core/taxrules.py"]),

    _e("banking-channel", "tax",
       ["What is the 80% rule?", "What is the banking channel condition?",
        "Why was my reduced rate withheld?", "Why am I not getting 0.25%?",
        "Does hawala count?"],
       "",
       compute=_threshold_now,
       keywords=["80", "banking", "channel", "condition", "threshold", "withheld",
                 "hawala", "formal", "reduced"],
       sources=["core/taxrules.py", "core/prc.py"]),

    _e("final-tax", "tax",
       ["What does final tax mean?", "Do I pay slab rates as well?",
        "Is export income taxed twice?"],
       "Final tax means that income is settled by the reduced rate — normal slab rates do "
       "not apply to it on top. That is why claiming export status matters so much: the "
       "alternative is slab treatment of the same money.",
       keywords=["final", "tax", "slab", "twice", "additional"],
       sources=["core/taxrules.py"]),

    _e("tax-years", "tax",
       ["Which tax years are supported?", "Can I do last year's return?",
        "What happens for an old tax year?"],
       "",
       compute=_known_years,
       keywords=["year", "years", "old", "previous", "supported", "last"],
       sources=["core/taxrules.py"]),

    _e("rates-change", "tax",
       ["What if the tax rates change?", "Finance Act changed the rates, now what?",
        "How do you keep rates up to date?", "Are these rates current?"],
       "Rates live in one place, core/taxrules.py, each stored with its tax year, source "
       "URL and verification date. A new Finance Act means adding a new entry — never "
       "editing an old one, because old entries are the record of what was true then. "
       "Nothing else in the codebase hardcodes a rate, including the chatbot you are "
       "talking to.",
       keywords=["change", "changed", "finance", "act", "update", "current", "stale"],
       sources=["core/taxrules.py"]),

    _e("is-this-tax-advice", "tax",
       ["Is this tax advice?", "Can I file using this?", "Can I trust these numbers?",
        "Should I show this to my accountant?"],
       "No, and the code is built so nobody can pretend otherwise. Every estimate carries "
       "is_estimate=True as a field, the rule it used, and a link to the source, so "
       "anything rendering it has to carry that through. The rates were verified against a "
       "published guide, not the Finance Act text — the filing pack's FBR-verification "
       "item is permanently unticked for that reason. Show it to a practitioner; that is "
       "what the record pack is for.",
       keywords=["advice", "trust", "file", "filing", "accountant", "reliable", "legal"],
       sources=["core/taxrules.py", "core/pack.py"]),

    # ---------- rails and cost ----------
    _e("hidden-spread", "rails",
       ["What is the hidden spread?", "Why is the fee higher than advertised?",
        "What did my payment really cost?", "How do you calculate the real cost?",
        "Why is my Payoneer fee 2% when they say 1%?"],
       "The advertised fee is only part of it. The rest is the gap between the mid-market "
       "rate and the rate you were actually given, which no statement itemises.",
       compute=_spread_example,
       keywords=["spread", "hidden", "fee", "cost", "really", "advertised", "fx",
                 "exchange", "margin"],
       sources=["core/money.py"]),

    _e("why-mid-market-needed", "rails",
       ["Why do you need the mid-market rate?", "Why can't you get the rate yourself?",
        "Why do I have to enter a rate?", "Can't you look up the exchange rate?"],
       "Because the rate on your statement is the rate the rail gave you — measuring the "
       "spread against it would always return zero. An independent mid-market rate is "
       "required, and free historical USD/PKR is not available: ECB/Frankfurter does not "
       "carry PKR at all, exchangerate.host now needs a key, exchangerate-api's free tier "
       "has no history, stooq serves a JavaScript challenge and SBP returns 403. I will "
       "not scrape past bot protection. Your bank's ePRC states the rate anyway, and for a "
       "Pakistani filing that document is the authoritative one.",
       keywords=["mid-market", "midmarket", "rate", "lookup", "api", "historical",
                 "enter", "manual", "why"],
       sources=["core/fx.py", "README.md"]),

    _e("realised-vs-mid", "rails",
       ["Why does it say cost is not calculable?", "Why no spread shown?",
        "Why is the rail cost missing?"],
       "Because the only rates available were realised rates from the ePRC, which already "
       "include the spread. Measuring the spread against them would report zero, so the "
       "tool refuses rather than printing a comforting lie. Supply mid-market rates for "
       "those dates and the analysis appears. This was a real bug once: the report printed "
       "'Lost to fees and spread: PKR 0.00' and one payment showed a negative spread.",
       keywords=["not", "calculable", "missing", "spread", "zero", "realised", "cost"],
       sources=["core/report.py", "core/fx.py"]),

    _e("which-rail-cheapest", "rails",
       ["Which payment method is cheapest?", "Should I use Wise or Payoneer?",
        "What is the best rail?", "How do I get paid most cheaply?"],
       "Hisaab will not tell you which rail is best in the abstract, because it depends on "
       "corridor, amount and the day's spread. What it does is measure your own history: "
       "each payment's all-in cost, split into declared fee and hidden spread, so you can "
       "compare what actually happened to you rather than what a marketing page claims.",
       keywords=["cheapest", "best", "rail", "wise", "payoneer", "deel", "compare",
                 "which", "method"],
       sources=["core/money.py", "core/report.py"]),

    _e("no-float", "rails",
       ["Why Decimal and not float?", "How is money handled?",
        "Do you use floating point?"],
       "Never float. 0.1 + 0.2 is 0.30000000000000004 in floating point, and across fifty "
       "remittances a year those errors accumulate into a number someone sends to FBR. "
       "Everything uses Decimal with rounding stated explicitly: money to 2 places, FX "
       "rates to 4, percentages to 6.",
       keywords=["decimal", "float", "floating", "point", "money", "rounding", "precision"],
       sources=["core/money.py"]),
]

ENTRIES += [
    # ---------- ePRC and evidence ----------
    _e("what-is-prc", "eprc",
       ["What is a PRC?", "What is an ePRC?", "What is a Proceeds Realization Certificate?",
        "Why do I need a PRC?", "Where do I get an ePRC?"],
       "A Proceeds Realization Certificate is the document your bank issues stating that "
       "foreign currency arrived and how many PKR were credited. In Pakistan an export "
       "income claim rests on it. You request it from the branch or portal that received "
       "the remittance. Hisaab treats it as the authoritative figure — above any "
       "third-party rate API.",
       keywords=["prc", "eprc", "proceeds", "realization", "realisation", "certificate",
                 "bank", "document"],
       sources=["core/prc.py"]),

    _e("payoneer-no-pkr", "eprc",
       ["Why does Payoneer need an ePRC?", "Why can't you read PKR from my Payoneer file?",
        "Payoneer statement has no PKR", "Why do I need two files?"],
       "A Payoneer export gives Transaction Date, Description, Amount, Currency, Source, "
       "Target and Reference ID — no exchange rate and no PKR. That is not an omission: "
       "the statement records their own foreign-currency account. Your client sent USD and "
       "it arrived as USD. PKR appears when the money reaches your bank, and that "
       "transaction is not in the file at all. So the two sides are matched: rail credits "
       "from the statement, PKR from the ePRC.",
       keywords=["payoneer", "pkr", "missing", "two", "files", "statement", "prc"],
       sources=["core/importers/generic.py", "core/prc.py"]),

    _e("matching-logic", "eprc",
       ["How does matching work?", "How do you pair payments with PRCs?",
        "Why did my payment not match?", "How are credits matched to certificates?"],
       "On amount, within a date window — not on exact date, because a withdrawal takes "
       "two to five days and the dates never line up. Each ePRC is consumed once, so one "
       "certificate cannot 'prove' two payments and double your export income. Anything "
       "unmatched is named in the report and makes it incomplete; it is never assumed.",
       keywords=["matching", "match", "pair", "window", "unmatched", "certificate"],
       sources=["core/prc.py"]),

    _e("unmatched-payment", "eprc",
       ["What if a payment has no ePRC?", "A payment is missing from my report",
        "Why is my income lower than expected?", "What does PAYMENTS EXCLUDED mean?"],
       "It is excluded and named, and the report is marked incomplete (the CLI exits 1). "
       "Quietly dropping it would understate export income — and that number goes on a tax "
       "return. Two reasons it happens, and both matter: either you have not obtained the "
       "ePRC yet, or the money did not arrive through a formal channel, which affects the "
       "banking-channel condition.",
       keywords=["unmatched", "excluded", "missing", "payment", "lower", "incomplete"],
       sources=["core/report.py", "core/prc.py"]),

    # ---------- importing ----------
    _e("supported-formats", "import",
       ["What file formats are supported?", "Which banks work?", "Can I use my bank statement?",
        "Does it support Deel?", "What statements can I upload?"],
       "Wise transfer exports and Payoneer transaction exports have presets. Anything else "
       "— a bank statement, Deel, a custom export — works through column mapping: you say "
       "which column is the date, the amount, the currency. That was a deliberate choice "
       "over guessing each provider's format, because those formats change and guessing "
       "breaks silently.",
       keywords=["format", "formats", "supported", "bank", "statement", "deel", "upload",
                 "csv", "which"],
       sources=["core/importers/generic.py"]),

    _e("import-failed", "import",
       ["My import failed", "It says this does not look like a Wise export",
        "Why won't my file import?", "Import error"],
       "Run it with --diagnose (or pick 'Something else' on the web page). Instead of "
       "refusing, the tool prints the file's columns, the mapping it would guess and a "
       "sample row, so you can supply a ColumnMap yourself with no code change. This "
       "exists because both parsers are written against documented formats rather than "
       "real exports, so some real file will not match.",
       keywords=["import", "failed", "error", "diagnose", "wont", "broken", "recognise"],
       sources=["core/diagnose.py"]),

    _e("date-format", "import",
       ["Why are my dates wrong?", "What date format do you expect?",
        "Is it DD-MM-YYYY or MM-DD-YYYY?"],
       "Wise writes DD-MM-YYYY, so 03-04-2026 is 3 April, not 4 March. ISO (YYYY-MM-DD) is "
       "also accepted. Anything else raises rather than guessing, because a misparsed date "
       "puts income in the wrong tax year — and the tax year is the basis of the entire "
       "calculation.",
       keywords=["date", "format", "dd", "mm", "yyyy", "wrong", "parse"],
       sources=["core/importers/wise.py"]),

    _e("fee-rows", "import",
       ["Why is my fee different from the statement?", "How are separate fee rows handled?",
        "Wise charges appear as their own line"],
       "Wise sometimes bills a transfer in its own row ('Wise charges for transfer X') "
       "rather than in the fee column; Payoneer bills withdrawal fees as separate lines. "
       "Ignoring those understates your cost and counting them twice overstates it — both "
       "wrong. Wise fee rows are matched to their transfer by ID; Payoneer fees, which are "
       "not tied to a transfer, are apportioned across credits and the report says so.",
       keywords=["fee", "rows", "separate", "charges", "different", "line"],
       sources=["core/importers/wise.py", "core/importers/generic.py"]),

    _e("gross-vs-net", "import",
       ["Is the amount gross or net?", "Why is my gross higher than the statement?",
        "What does after fees mean?"],
       "Wise's column is literally 'Source amount (after fees)', so the fee is added back "
       "to reconstruct what the client actually sent. Treating that column as gross would "
       "make every cost figure come out too low — your client sent $3,000, not $2,986.50.",
       keywords=["gross", "net", "after", "fees", "amount", "higher"],
       sources=["core/importers/wise.py"]),

    # ---------- privacy ----------
    _e("data-stored", "privacy",
       ["Is my data stored?", "Do you keep my statement?", "Is this private?",
        "Where does my data go?", "Do you have a database?"],
       "Nothing is stored. No database, no saved upload, no account, and no amounts in any "
       "log. Your statement is parsed in memory and discarded when the page renders. A "
       "test asserts that web/app.py contains no storage calls at all, so the claim cannot "
       "quietly stop being true. This is bank and tax data: what is never stored cannot be "
       "leaked.",
       keywords=["data", "stored", "store", "privacy", "private", "database", "keep",
                 "secure", "safe"],
       sources=["web/app.py", "tests/test_web.py"]),

    _e("need-credentials", "privacy",
       ["Do you need my bank login?", "Do I connect my Payoneer account?",
        "Do you need API access to my accounts?"],
       "No. It never asks for a bank or Payoneer password and never scrapes their "
       "dashboards. You export a CSV yourself and supply it. If an official API is ever "
       "used it will be with a token you issue and can revoke.",
       keywords=["login", "password", "credentials", "connect", "account", "access",
                 "scrape"],
       sources=["README.md"]),

    _e("handles-money", "privacy",
       ["Does it move my money?", "Is this a payment processor?",
        "Can it transfer funds?", "Do you touch my funds?"],
       "Never. Moving money is money transmission and requires licensing. Hisaab is a "
       "ledger and a calculator — it reads records and computes. That boundary is "
       "deliberate and structural, not a stage it will grow out of.",
       keywords=["move", "money", "transfer", "payment", "processor", "funds", "licence",
                 "license"],
       sources=["README.md"]),

    # ---------- the filing pack ----------
    _e("filing-pack", "pack",
       ["What is the filing pack?", "What do I give my accountant?",
        "What does --pack do?", "How do I file with this?"],
       "The report is for you; the pack is for whoever files your return. It contains the "
       "remittance register (a CSV, one line per payment with its ePRC backing), the "
       "totals, the basis used, and a checklist of what still needs doing. Use --pack DIR "
       "on the CLI, or download the register from the web page.",
       keywords=["pack", "filing", "accountant", "filer", "register", "give", "hand"],
       sources=["core/pack.py"]),

    _e("never-ready", "pack",
       ["Why does it always say NOT READY TO FILE?", "Why is one item never ticked?",
        "How do I get it to say ready?"],
       "The FBR-verification item is permanently unticked, deliberately. The rates here "
       "came from a published guide rather than the Finance Act text, so the tool has no "
       "business declaring your filing ready. A checklist that always goes green is worse "
       "than no checklist, because someone files on the strength of it. Confirm the rates "
       "at fbr.gov.pk and have a practitioner review the pack.",
       keywords=["ready", "not", "ticked", "checklist", "green", "always"],
       sources=["core/pack.py"]),

    _e("checklist-dash", "pack",
       ["What does the dash mean in the checklist?", "What is [-] in the pack?",
        "Why is an item marked not applicable?"],
       "[-] means the condition does not apply to you. The banking-channel rule is a "
       "condition of the reduced rate, so it is meaningless if you are not PSEB-registered. "
       "Showing [x] there once sent exactly the wrong signal — an unregistered user would "
       "go off to fix their banking channel when the actual issue is registration. The "
       "first version printed [x] directly above the words 'Below the threshold': the "
       "checklist contradicting itself in its own output.",
       keywords=["dash", "checklist", "applicable", "bracket", "mark", "symbol"],
       sources=["core/pack.py"]),
]

ENTRIES += [
    # ---------- running it ----------
    _e("how-to-run", "usage",
       ["How do I run this?", "How do I start it?", "How do I use Hisaab?",
        "Getting started", "Installation"],
       "Web page: `uvicorn web.app:app`, then open http://127.0.0.1:8000 and upload your "
       "statement.\n\nCommand line:\n"
       "  python cli.py statement.csv --rates rates.csv --pseb\n"
       "  python cli.py payoneer.csv --format payoneer --prc prc.csv\n"
       "  python cli.py mystery.csv --diagnose\n"
       "  python cli.py statement.csv --rates rates.csv --pack ./pack\n\n"
       "Tests: `python -m pytest tests -q`.",
       keywords=["run", "start", "use", "install", "getting", "started", "command",
                 "uvicorn", "cli"],
       sources=["README.md", "cli.py"]),

    _e("rates-file", "usage",
       ["What format is the rates file?", "How do I supply rates?",
        "What goes in rates.csv?"],
       "One line per date: `date,rate` — for example `03-04-2026, 279.50`. Take the figure "
       "from your ePRC where you have it; rates entered by hand are flagged in the report "
       "as less authoritative than the bank's.",
       keywords=["rates", "file", "csv", "format", "supply", "column"],
       sources=["cli.py"]),

    _e("prc-file", "usage",
       ["What format is the PRC file?", "How do I enter my ePRCs?",
        "What goes in prc.csv?"],
       "One line per certificate: `date,usd,pkr[,certificate,bank]` — for example "
       "`08-04-2026, 3000.00, 834000.00, PRC-2026-041, HBL`. On the web page, paste the "
       "same lines into the ePRC box.",
       keywords=["prc", "eprc", "file", "format", "enter", "csv", "ledger"],
       sources=["cli.py", "web/app.py"]),

    _e("exit-codes", "usage",
       ["What do the exit codes mean?", "Why did the CLI exit 1?",
        "Can I script this?"],
       "0 means a complete report. 1 means incomplete — a payment lacked a rate or an "
       "ePRC, so export income is understated. 2 means the file could not be read or "
       "parsed. That makes it safe to run in a script: a silent understatement would "
       "otherwise look like success.",
       keywords=["exit", "code", "codes", "script", "automation", "status"],
       sources=["cli.py"]),

    # ---------- architecture ----------
    _e("architecture", "architecture",
       ["How is the code organised?", "What is the architecture?",
        "Explain the structure", "What are the modules?"],
       "core/ holds the domain and has no dependencies beyond the standard library — "
       "money, taxrules, fx, prc, report, pack, diagnose, importers. cli.py and web/app.py "
       "are thin shells over it, so the command line and the web page can never disagree. "
       "chat/ is this knowledge base. The domain was written first, deliberately: it is "
       "both the riskiest part and the only defensible one — anyone can build the CRUD.",
       keywords=["architecture", "structure", "organised", "modules", "code", "layout",
                 "design"],
       sources=["README.md"]),

    _e("no-dependencies", "architecture",
       ["Why does core have no dependencies?", "What libraries does it use?",
        "Why so few packages?"],
       "core/ uses only the standard library — Decimal, csv, dataclasses, re. The part "
       "that matters most should not be able to break because someone else shipped a "
       "release. FastAPI and python-multipart are needed only for the web page.",
       keywords=["dependencies", "libraries", "packages", "stdlib", "requirements"],
       sources=["requirements.txt"]),

    _e("why-no-accounts", "architecture",
       ["Why is there no login?", "Why no user accounts?", "Where is the database?",
        "Why no billing?"],
       "Because the report is what saves the user money; auth and dashboards do not. "
       "Those come after someone pays for a report, not before. Building auth, billing and "
       "a dashboard before knowing whether anyone wants the output is exactly how time "
       "gets wasted. A stateless tool also has nothing to protect yet.",
       keywords=["login", "accounts", "auth", "database", "billing", "signup", "why"],
       sources=["README.md"]),

    _e("tests", "architecture",
       ["How is it tested?", "How many tests are there?", "What do the tests cover?"],
       "Around a hundred tests, weighted towards the places where being wrong costs money: "
       "Decimal arithmetic, the tax rules and their refusals, date parsing, fee rows, ePRC "
       "matching, the report's completeness rules, and the checklist. Several are named "
       "after bugs that actually happened, so they read as a record rather than a "
       "checklist.",
       keywords=["test", "tests", "tested", "coverage", "pytest", "quality"],
       sources=["tests/"]),

    # ---------- the bugs, honestly ----------
    _e("known-bugs", "honesty",
       ["What bugs have you found?", "What went wrong?", "Has this had bugs?",
        "What mistakes were made?"],
       "Four worth knowing, all the same shape — the part was safe and the join was not:\n"
       "1. The banking-channel condition never reached the report, so the reduced rate applied "
       "even though too little was proven by ePRCs. Tax showed PKR 3,816 instead of PKR 15,265 — a real underpayment.\n"
       "2. ePRC realised rates were used as mid-market, so cost printed as PKR 0.00 and "
       "one payment showed a negative spread.\n"
       "3. Payments dropped during matching left the report calling itself complete.\n"
       "4. The filing checklist printed [x] directly above 'Below the threshold'.\n\n"
       "All four were found by running the tool, not by unit tests, and all four now have "
       "regression tests at the level where the bug lived.",
       keywords=["bug", "bugs", "wrong", "mistake", "mistakes", "broken", "failure"],
       sources=["README.md"]),

    _e("limitations", "honesty",
       ["What are the limitations?", "What doesn't work?", "What should I not trust?",
        "What is unfinished?"],
       "Three things, stated plainly:\n"
       "1. Both importers are written against documented formats, not real exports. Some "
       "real file will not match — hence --diagnose.\n"
       "2. The tax rates were verified against a published guide, not the Finance Act or "
       "an FBR circular. Confirm at fbr.gov.pk before filing.\n"
       "3. Rail cost examples are realistic but not measured. Real per-rail benchmarks "
       "require actual user remittances.",
       keywords=["limitation", "limitations", "unfinished", "trust", "work", "caveat",
                 "weakness"],
       sources=["README.md"]),

    _e("why-not-scrape", "honesty",
       ["Why don't you scrape the rates?", "Why not scrape my bank?",
        "Can you get data automatically?"],
       "Two of the historical-rate sources sit behind bot protection — stooq serves a "
       "JavaScript proof-of-work challenge and SBP returns 403. I will not build something "
       "whose core function is getting around that, the same way this project's sibling "
       "declined to scrape LinkedIn. It also would not last: those defences change and the "
       "scraper silently rots.",
       keywords=["scrape", "scraping", "automatic", "bot", "block", "sbp", "stooq"],
       sources=["core/fx.py"]),

    # ---------- the chatbot itself ----------
    _e("about-this-bot", "meta",
       ["Are you an AI?", "How does this chatbot work?", "Were you trained on this project?",
        "Is this a language model?", "How do you know the answers?"],
       "No model was trained. This is retrieval over a curated knowledge base where every "
       "entry carries its source, and the scoring is deterministic — the same question "
       "always gives the same answer. Every number you see is computed live from "
       "core/taxrules.py and core/money.py rather than recalled, so it cannot drift from "
       "what the tool actually applies. If nothing matches well enough, I say I do not "
       "know instead of guessing. For a tax tool, a confident wrong answer is the worst "
       "possible failure.",
       keywords=["ai", "chatbot", "bot", "trained", "model", "llm", "work", "know",
                 "how"],
       sources=["chat/engine.py", "chat/knowledge.py"]),

    _e("bot-limits", "meta",
       ["What can't you answer?", "What don't you know?", "What are your limits?"],
       "Anything outside this project. I have no opinion on your specific tax situation, "
       "no access to your data, and no ability to look anything up. I also will not answer "
       "a tax question by recalling a rate — every number comes from the engine, and if "
       "the engine has no verified rule for a tax year it refuses rather than guessing.",
       keywords=["cant", "answer", "dont", "know", "limits", "outside"],
       sources=["chat/engine.py"]),
]
