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

from core.fx import ManualRate
from core.importers.wise import ImportProblem, parse, parse_date
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export income and rail-cost report.")
    parser.add_argument("statement", type=Path, help="Wise transfer CSV export")
    parser.add_argument(
        "--rates", type=Path, default=None,
        help="CSV of 'date,rate' mid-market rates (take them from your PRC/ePRC)",
    )
    parser.add_argument(
        "--hand-entered-rates", action="store_true",
        help="Mark the rates as hand-entered rather than from a PRC",
    )
    parser.add_argument("--pseb", action="store_true", help="You are PSEB-registered")
    parser.add_argument("--tax-year", default=None, help="e.g. 2025-26")
    parser.add_argument(
        "--banking-fraction", default="1.00",
        help="Fraction of income received through formal banking channels",
    )
    args = parser.parse_args(argv)

    try:
        content = args.statement.read_bytes()
    except OSError as exc:
        print(f"Could not read {args.statement}: {exc}", file=sys.stderr)
        return 2

    try:
        imported = parse(content)
    except ImportProblem as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2

    if imported.skipped:
        print(f"Skipped {len(imported.skipped)} row(s):", file=sys.stderr)
        for reason in imported.skipped[:10]:
            print(f"  - {reason}", file=sys.stderr)
        print("", file=sys.stderr)

    rates = load_rates(args.rates, from_prc=not args.hand_entered_rates)
    result = build(
        imported.transfers,
        rates,
        tax_year=args.tax_year,
        pseb_registered=args.pseb,
        banking_channel_fraction=args.banking_fraction,
    )
    print(render(result))
    # Adhoori report pe non-zero exit — script mein chalane wale ko pata chale.
    return 0 if result["is_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
