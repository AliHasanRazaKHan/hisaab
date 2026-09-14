"""
Kisi bhi statement ko parhne ka raasta — column mapping se.

**Ye har rail ka format andaza lagane se behtar hai.** Har provider apne column
alag likhta hai aur waqt ke saath badalta rehta hai; un sab ko guess karna wo
kaam hai jo hamesha tootta rehta. Is liye user (ya preset) batata hai ke kaun
sa column kya hai, aur parser us mapping pe chalta hai.

Aur Payoneer ne ek buniyadi baat saaf kar di. Us ka CSV ye deta hai:
`Transaction Date, Description, Amount, Currency, Source, Target, Reference ID,
Store Name, Additional Description` — yaani **na exchange rate, na PKR**.

Wajah simple hai: Payoneer ka statement us ke apne account ka hai. Client ne
USD bheje, wo USD account mein aaye. PKR tab bante hain jab paisa **bank** mein
aata hai — aur wo lenden Payoneer ke statement mein hi nahi hoti.

Is liye do alag cheezein hain, aur inhe milana zaroori hai:

- **RailCredit** — client ne kya bheja (rail ke statement se)
- **PRC entry** — bank mein kitne PKR aaye (core/prc.py, authoritative)

Wise ka CSV itna tafseeli hai ke dono ek hi row mein aa jate hain (is liye
`importers/wise.py` seedha mukammal record deta hai). Payoneer aur aam bank
statements ke liye do taraf ka data chahiye — aur ye is domain ki haqeeqat hai,
hamare design ki kami nahi.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from core.importers.wise import ImportProblem, parse_date
from core.money import money


@dataclass(frozen=True)
class ColumnMap:
    """
    Kaun sa column kya hai. Naam case/space se azad match hote hain.

    `credit_sign` un statements ke liye hai jahan aamad manfi likhi jati hai
    (kuch bank exports aisa karte hain) — andaza lagane ke bajaye user batata hai.
    """

    date: str
    amount: str
    currency: str | None = None
    description: str | None = None
    external_id: str | None = None
    fee: str | None = None
    counterparty: str | None = None
    credit_sign: int = 1

    def fields(self) -> dict[str, str]:
        return {
            name: value
            for name, value in {
                "date": self.date,
                "amount": self.amount,
                "currency": self.currency,
                "description": self.description,
                "external_id": self.external_id,
                "fee": self.fee,
                "counterparty": self.counterparty,
            }.items()
            if value
        }


# Documented formats (2026-09-15). Asli file pe tasdeeq nahi — jaise hi asli
# statement mile, inhe dobara jaanchna hai.
PRESETS: dict[str, ColumnMap] = {
    "payoneer": ColumnMap(
        date="Transaction Date",
        amount="Amount",
        currency="Currency",
        description="Description",
        external_id="Reference ID",
        counterparty="Source",
    ),
}

_FEE_WORDS = re.compile(
    r"\b(fee|fees|charge|charges|commission|withdrawal fee|service charge)\b", re.IGNORECASE
)


@dataclass
class RailCredit:
    """
    Ek aamad jaise rail ne bataya — **PKR is mein nahi hai**.

    PKR bank se aata hai (PRC), is liye wo yahan jaan-boojh kar ghayab hai. Us
    ko 0 rakh dena ya andaza lagana poore hisaab ko jhoot bana deta.
    """

    external_id: str
    received_on: date
    gross_usd: Decimal
    declared_fee_usd: Decimal = Decimal("0.00")
    description: str = ""
    counterparty: str = ""


@dataclass
class GenericImportResult:
    credits: list[RailCredit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    fees_folded: int = 0

    @property
    def total_gross_usd(self) -> Decimal:
        return money(sum((c.gross_usd for c in self.credits), Decimal("0")))


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _decimal(raw: str) -> Decimal:
    text = (raw or "").strip().replace(",", "").replace(" ", "")
    # Kuch exports "(123.45)" se manfi likhte hain.
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    for symbol in ("$", "USD", "PKR", " "):
        text = text.replace(symbol, "")
    if not text:
        return Decimal("0.00")
    try:
        value = money(text)
    except (InvalidOperation, ArithmeticError):
        raise ValueError(f"not a number: {raw!r}") from None
    return -value if negative else value


def parse(
    content: str | bytes,
    mapping: ColumnMap | str,
    *,
    currency: str = "USD",
    fold_fee_rows: bool = True,
) -> GenericImportResult:
    """
    Mapped statement se aamad nikalta hai.

    `fold_fee_rows` un statements ke liye hai jahan fee apni alag row mein aati
    hai (Payoneer aisa karta hai). Aisi rows ko chhor dena laagat kam dikhata
    hai, is liye unhe us mahine ki aamad ke saath jama kar diya jata hai — aur
    ye jaan-boojh kar approximate hai, kyunke Payoneer ka statement fee ko kisi
    khaas transfer se nahi jorta. `fees_folded` is ka hisaab rakhta hai taake
    report mein saaf likha ja sake.
    """
    if isinstance(mapping, str):
        try:
            mapping = PRESETS[mapping]
        except KeyError:
            raise ImportProblem(
                f"No preset named {mapping!r}. Known: {', '.join(sorted(PRESETS))}. "
                "Pass a ColumnMap to describe the columns instead."
            ) from None

    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportProblem("The file is empty.") from None

    normalised = [_normalise(h) for h in header]
    index: dict[str, int] = {}
    for field_name, column_name in mapping.fields().items():
        key = _normalise(column_name)
        if key in normalised:
            index[field_name] = normalised.index(key)

    for required in ("date", "amount"):
        if required not in index:
            wanted = mapping.fields()[required]
            raise ImportProblem(
                f"Column {wanted!r} not found. The file has: {', '.join(header[:8])}…"
            )

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        if position is None or position >= len(row):
            return ""
        return row[position]

    result = GenericImportResult()
    pending_fees = Decimal("0.00")

    for line_number, row in enumerate(reader, start=2):
        if not any((c or "").strip() for c in row):
            continue

        row_currency = (cell(row, "currency") or currency).strip().upper()
        if row_currency and row_currency != currency.upper():
            result.skipped.append(f"line {line_number}: {row_currency}, not {currency}")
            continue

        try:
            day = parse_date(cell(row, "date"))
            amount = _decimal(cell(row, "amount")) * mapping.credit_sign
        except ValueError as exc:
            result.skipped.append(f"line {line_number}: {exc}")
            continue

        description = cell(row, "description").strip()

        # Fee row: raqam manfi hai aur tafseel mein fee ka zikr hai.
        if amount < 0 and _FEE_WORDS.search(description):
            if fold_fee_rows:
                pending_fees += abs(amount)
                result.fees_folded += 1
            else:
                result.skipped.append(f"line {line_number}: fee row ({description[:40]})")
            continue

        if amount <= 0:
            # Withdrawal/transfer out — aamad nahi.
            result.skipped.append(f"line {line_number}: not a credit ({amount})")
            continue

        result.credits.append(
            RailCredit(
                external_id=cell(row, "external_id").strip() or f"line-{line_number}",
                received_on=day,
                gross_usd=amount,
                declared_fee_usd=_decimal(cell(row, "fee")) if "fee" in index else Decimal("0.00"),
                description=description,
                counterparty=cell(row, "counterparty").strip(),
            )
        )

    if pending_fees > 0 and result.credits:
        # Fee kisi khaas transfer se juri nahi hoti, is liye sab pe us ke hisse
        # ke mutabiq baant dete hain — aur report mein ye saaf likha jata hai.
        total = result.total_gross_usd
        for credit in result.credits:
            share = (credit.gross_usd / total) if total > 0 else Decimal("0")
            credit.declared_fee_usd = money(credit.declared_fee_usd + pending_fees * share)
    elif pending_fees > 0:
        result.skipped.append(
            f"{pending_fees} in fees could not be attributed — no credits in this file"
        )

    return result
