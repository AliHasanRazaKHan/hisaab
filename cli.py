"""
Hisaab CLI — statement in, report out.

Jaan-boojh kar pehle CLI, web app nahi: jo cheez user ko paisa bachati hai wo
report hai, auth/billing/dashboard nahi. Pehle ye sabit ho ke report ki qeemat
hai, phir us ke oopar app banti hai.

    python cli.py statement.csv --rates rates.csv --pseb

`rates.csv` shakal: `date,rate` — PRC/ePRC se. Bina rate wali payments report
mein alag likhi jati hain, chupke se giraayi nahi jati.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from chat.engine import ask as ask_bot
from core.diagnose import diagnose
from core.fx import ManualRate
from core.importers import generic
from core.importers.wise import ImportProblem, parse_date
from core.importers.wise import parse as parse_wise
from core.prc import PrcEntry, match, rates_from_prc
from core.money import money
from core.pack import build_pack, register_csv, render_pack
from core.report import build, render


def load_rates(path: Path | None, *, from_prc: bool) -> ManualRate:
    source = ManualRate()
    if path is None:
        return source
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        # Header optional — pehli row tareekh jaisi lage to usi ko data maano.
        if header and len(header) >= 2:
            try:
                parse_date(header[0])
            except ValueError:
                header = None
            else:
                reader = [header, *reader]  # type: ignore[assignment]
        for line, row in enumerate(reader, start=1):
            if not row or len(row) < 2:
                continue
            try:
                day = parse_date(row[0])
            except ValueError as exc:
                print(f"  rates.csv line {line}: {exc}", file=sys.stderr)
                continue
            if from_prc:
                source.from_prc(day, row[1].strip())
            else:
                source.set(day, row[1].strip())
    return source


def load_prc(path: Path) -> list[PrcEntry]:
    """
    ePRC ledger: `date,usd,pkr[,certificate,bank]`.

    Ye sab se authoritative data hai — bank ka jaari kiya hua kaghaz, jis pe
    Pakistan mein export income ka dawa khara hota hai.
    """
    entries: list[PrcEntry] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for line, row in enumerate(csv.reader(handle), start=1):
            if not row or len(row) < 3:
                continue
            try:
                day = parse_date(row[0])
            except ValueError:
                continue  # header row
            try:
                entries.append(
                    PrcEntry(
                        issued_on=day,
                        usd_amount=money(row[1].strip()),
                        pkr_credited=money(row[2].strip()),
                        certificate_no=row[3].strip() if len(row) > 3 else "",
                        bank=row[4].strip() if len(row) > 4 else "",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  prc.csv line {line}: {exc}", file=sys.stderr)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export income and rail-cost report.")
    parser.add_argument("statement", type=Path, help="Rail statement CSV export")
    parser.add_argument(
        "--ask", metavar="QUESTION", default=None,
        help="Ask about this tool instead of running a report",
    )
    parser.add_argument(
        "--format", default="wise", choices=["wise", *sorted(generic.PRESETS)],
        help="Statement format (default: wise)",
    )
    parser.add_argument(
        "--prc", type=Path, default=None,
        help="ePRC ledger CSV 'date,usd,pkr[,certificate,bank]' — required for formats "
             "whose statement cannot show PKR (e.g. payoneer)",
    )
    parser.add_argument(
        "--rates", type=Path, default=None,
        help="CSV of 'date,rate' mid-market rates (take them from your PRC/ePRC)",
    )
    parser.add_argument(
        "--hand-entered-rates", action="store_true",
        help="Mark the rates as hand-entered rather than from a PRC",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="Describe the file's columns and the mapping it would use, then stop",
    )
    parser.add_argument(
        "--pack", type=Path, default=None, metavar="DIR",
        help="Also write the filing record pack (summary + register CSV) to this directory",
    )
    parser.add_argument("--pseb", action="store_true", help="You are PSEB-registered")
    parser.add_argument("--tax-year", default=None, help="e.g. 2025-26")
    parser.add_argument(
        "--banking-fraction", default="1.00",
        help="Fraction of income received through formal banking channels",
    )
    args = parser.parse_args(argv)

    if args.ask:
        answer = ask_bot(args.ask)
        print(answer.text)
        if answer.sources:
            print(f"\nFrom: {', '.join(answer.sources)}")
        if answer.suggestions:
            print(f"\n{'Try' if answer.is_fallback else 'Related'}: "
                  + " | ".join(answer.suggestions))
        return 0 if answer.confident else 1

    try:
        content = args.statement.read_bytes()
    except OSError as exc:
        print(f"Could not read {args.statement}: {exc}", file=sys.stderr)
        return 2

    if args.diagnose:
        print(diagnose(content).explain())
        return 0

    banking_fraction = args.banking_fraction
    excluded_earlier: list[str] = []

    try:
        if args.format == "wise":
            imported = parse_wise(content)
            transfers, skipped = imported.transfers, imported.skipped
            rates = load_rates(args.rates, from_prc=not args.hand_entered_rates)
        else:
            # Ye formats PKR bata hi nahi sakte (core/importers/generic.py dekho),
            # is liye PRC ledger lazmi hai.
            if args.prc is None:
                print(
                    f"--prc is required with --format {args.format}: a {args.format} "
                    "statement records its own currency account, so it cannot show what "
                    "your bank credited in PKR. That figure comes from the ePRC.",
                    file=sys.stderr,
                )
                return 2
            rail = generic.parse(content, args.format)
            prc_entries = load_prc(args.prc)
            paired = match(rail.credits, prc_entries)
            transfers = paired.matched
            skipped = list(rail.skipped)
            for credit in paired.credits_without_prc:
                # Ye report ko **adhoora** banati hain, sirf "skipped" nahi —
                # in ke bagair export income kam dikhta hai.
                excluded_earlier.append(
                    f"{credit.received_on.isoformat()} ${credit.gross_usd} — no matching ePRC"
                )
            for entry in paired.prc_without_credit:
                skipped.append(
                    f"{entry.issued_on.isoformat()} ${entry.usd_amount} — ePRC with no "
                    "matching credit in this statement"
                )
            # Reduced rate ki shart isi se tay hoti hai: jo PRC se sabit nahi,
            # wo formal banking channel mein shumar nahi hota.
            banking_fraction = paired.banking_channel_fraction
            rates = load_rates(args.rates, from_prc=not args.hand_entered_rates) \
                if args.rates else rates_from_prc(prc_entries)
    except ImportProblem as exc:
        # Sirf "Import failed" likh kar chup ho jana bekar hai — user ke paas na
        # wajah hoti hai na hal. File ki shakal bata dena us ko khud mapping
        # dene ke qabil banata hai, aur hamare parsers abhi documented formats
        # pe bane hain (asli files pe tasdeeq nahi), to ye soorat aani hi hai.
        print(f"Import failed: {exc}\n", file=sys.stderr)
        print(diagnose(content).explain(), file=sys.stderr)
        return 2

    if excluded_earlier:
        print(f"Excluded, income understated ({len(excluded_earlier)}):", file=sys.stderr)
        for reason in excluded_earlier:
            print(f"  - {reason}", file=sys.stderr)
        print("", file=sys.stderr)

    if skipped:
        print(f"Skipped ({len(skipped)}):", file=sys.stderr)
        for reason in skipped[:10]:
            print(f"  - {reason}", file=sys.stderr)
        print("", file=sys.stderr)

    result = build(
        transfers,
        rates,
        tax_year=args.tax_year,
        pseb_registered=args.pseb,
        banking_channel_fraction=banking_fraction,
        excluded_earlier=excluded_earlier,
    )
    print(render(result))

    if args.pack is not None:
        pack = build_pack(result)
        args.pack.mkdir(parents=True, exist_ok=True)
        summary = args.pack / f"filing-{pack.tax_year.replace('/', '-')}.txt"
        register = args.pack / f"remittance-register-{pack.tax_year.replace('/', '-')}.csv"
        summary.write_text(render_pack(pack), encoding="utf-8")
        register.write_text(register_csv(pack), encoding="utf-8")
        print("")
        print(f"Filing pack written:\n  {summary}\n  {register}")
        if not pack.ready_to_file:
            print("  (pack is marked NOT READY TO FILE — see its checklist)")

    # Adhoori report pe non-zero exit — script mein chalane wale ko pata chale.
    return 0 if result["is_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
