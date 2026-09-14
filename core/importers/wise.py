"""
Wise ka transfer CSV parse karna.

Documented format (2026-09-15 pe dekha gaya, asli file pe nahi):
`ID, Status, Direction, Created on, Finished on, Source fee amount,
Source fee currency, Source name, Source amount (after fees), Source currency,
Target name, Target amount (after fees), Target currency, Exchange rate,
Reference, Batch, Created by` — UTF-8, comma-delimited, har field quoted,
tareekh **DD-MM-YYYY**.

Teen baatein jo naive parser ko todti hain, aur teeno yahan sambhali gayi hain:

1. **Tareekh DD-MM-YYYY hai, ISO nahi.** `03-04-2026` 3 April hai, 4 March
   nahi. Ghalat parse tax year ko ghalat kar deta hai — aur tax year ka faisla
   hi poore hisaab ki buniyad hai. Is liye ambiguous soorat mein guess nahi
   karte.
2. **"Exchange rate" mid-market nahi hai** — wo Wise ka diya hua rate hai. Is
   se spread nikalna apne aap ko apne hi rate se naapna hai, jo hamesha 0
   dega. Mid-market bahar se aata hai (core/fx.py).
3. **Fee alag row mein bhi aa sakti hai** ("Wise charges for transfer X"), sirf
   `Source fee amount` column mein nahi. Us soorat mein fee ko dobara ginna ya
   bilkul chhor dena, dono ghalat hain.

Rawayya shaki hai: har row alag parse hoti hai aur ek kharab row poori file
nahi giraati — wahi usool jo job feeds mein seekha tha.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from core.money import money, rate

# Wise ke column naam waqt ke saath badalte rehte hain, is liye har field ke
# kai mumkin naam — aur match case/space se azad hai.
COLUMNS = {
    "id": ("id", "transferid", "transfer id"),
    "status": ("status",),
    "direction": ("direction",),
    "finished_on": ("finished on", "finishedon", "date"),
    "created_on": ("created on", "createdon"),
    "fee_amount": ("source fee amount", "sourcefeeamount", "fee"),
    "fee_currency": ("source fee currency", "sourcefeecurrency"),
    "source_amount": ("source amount (after fees)", "source amount", "sourceamount"),
    "source_currency": ("source currency", "sourcecurrency"),
    "target_amount": ("target amount (after fees)", "target amount", "targetamount"),
    "target_currency": ("target currency", "targetcurrency"),
    "exchange_rate": ("exchange rate", "exchangerate"),
    "reference": ("reference", "description"),
    "payer": ("source name", "sourcename", "created by"),
}

# "Wise charges for transfer 1234567" — fee wali alag row.
_FEE_ROW_RE = re.compile(r"wise charges for transfer\s+(\S+)", re.IGNORECASE)


class ImportProblem(Exception):
    """File hi parse nahi ho saki (row ki ghalti nahi)."""


@dataclass
class ImportedTransfer:
    """Ek incoming payment, jaise Wise ne bataya. Mid-market rate abhi nahi."""

    external_id: str
    received_on: date
    gross_usd: Decimal
    net_pkr: Decimal
    declared_fee_usd: Decimal
    # Wise ka diya hua rate — mid-market **nahi**. Muqable ke liye rakha hai.
    rail_rate: Decimal | None
    reference: str = ""
    payer: str = ""


@dataclass
class ImportResult:
    transfers: list[ImportedTransfer] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # Alag row se mili fees, transfer id ke against.
    fee_rows_applied: int = 0

    @property
    def total_gross_usd(self) -> Decimal:
        return money(sum((t.gross_usd for t in self.transfers), Decimal("0")))

    @property
    def total_net_pkr(self) -> Decimal:
        return money(sum((t.net_pkr for t in self.transfers), Decimal("0")))


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9() ]", "", (name or "").strip().lower())


def _build_index(header: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    normalised = [_normalise(h) for h in header]
    for field_name, candidates in COLUMNS.items():
        for candidate in candidates:
            if candidate in normalised:
                index[field_name] = normalised.index(candidate)
                break
    return index


def _decimal(raw: str) -> Decimal:
    """Wise thousands separator aur khali string dono bhejta hai."""
    text = (raw or "").strip().replace(",", "").replace(" ", "")
    if not text:
        return Decimal("0.00")
    try:
        return money(text)
    except (InvalidOperation, ArithmeticError):
        raise ValueError(f"not a number: {raw!r}") from None


def parse_date(raw: str) -> date:
    """
    Wise DD-MM-YYYY deta hai. ISO bhi qubool hai (kuch exports mein wo aata hai).

    **Ambiguous soorat mein guess nahi.** `03-04-2026` ko DD-MM maan kar 3
    April parha jata hai (Wise ka documented format), magar agar koi aur shakal
    ho to error — kyunke ghalat tareekh ka matlab ghalat tax year, aur tax year
    pe poora hisaab khara hai.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty date")
    # Waqt saath aata hai: "03-04-2026 14:22:01"
    text = text.split(" ")[0].split("T")[0]

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    if re.fullmatch(r"\d{2}[-/]\d{2}[-/]\d{4}", text):
        return datetime.strptime(text.replace("/", "-"), "%d-%m-%Y").date()
    raise ValueError(
        f"unrecognised date {raw!r} — expected DD-MM-YYYY (Wise) or YYYY-MM-DD"
    )


def parse(content: str | bytes, *, target_currency: str = "PKR") -> ImportResult:
    """
    Wise CSV se incoming transfers nikalta hai.

    Sirf wo rows leta hai jo (a) incoming hain, (b) mukammal hui hain, aur
    (c) `target_currency` mein aayi hain. Baqi `skipped` mein wajah ke saath
    jati hain — chupke se girana ghalat adad se bhi bura hai, kyunke user ko
    pata bhi nahi chalta ke kuch chhoot gaya.
    """
    if isinstance(content, bytes):
        # Wise UTF-8 deta hai; kuch exports mein BOM hota hai.
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportProblem("The file is empty.") from None

    index = _build_index(header)
    required = ("finished_on", "target_amount")
    missing = [f for f in required if f not in index]
    if missing:
        raise ImportProblem(
            "This does not look like a Wise transfer export — missing "
            f"{', '.join(missing)}. Found columns: {', '.join(header[:8])}…"
        )

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        if position is None or position >= len(row):
            return ""
        return row[position]

    result = ImportResult()
    # Fee rows transfer se pehle ya baad mein aa sakti hain, is liye pehle jama
    # karte hain aur aakhir mein lagate hain.
    extra_fees: dict[str, Decimal] = {}
    pending: list[tuple[str, ImportedTransfer]] = []

    for line_number, row in enumerate(reader, start=2):
        if not any((c or "").strip() for c in row):
            continue

        reference = cell(row, "reference")
        fee_match = _FEE_ROW_RE.search(reference)
        if fee_match:
            # Alag fee row — raqam aksar manfi hoti hai.
            try:
                amount = abs(_decimal(cell(row, "source_amount") or cell(row, "target_amount")))
            except ValueError:
                result.skipped.append(f"line {line_number}: fee row with no usable amount")
                continue
            extra_fees[fee_match.group(1)] = extra_fees.get(
                fee_match.group(1), Decimal("0.00")
            ) + amount
            continue

        direction = _normalise(cell(row, "direction"))
        if direction and direction not in ("in", "incoming", "credit"):
            result.skipped.append(f"line {line_number}: not incoming ({direction})")
            continue

        status = _normalise(cell(row, "status"))
        if status and status not in ("completed", "outgoing payment sent", "finished"):
            result.skipped.append(f"line {line_number}: status {status!r}")
            continue

        currency = _normalise(cell(row, "target_currency")).upper()
        if currency and currency != target_currency.upper():
            result.skipped.append(f"line {line_number}: target {currency}, not {target_currency}")
            continue

        try:
            received_on = parse_date(cell(row, "finished_on") or cell(row, "created_on"))
            net_pkr = _decimal(cell(row, "target_amount"))
            gross_usd = _decimal(cell(row, "source_amount"))
            fee = _decimal(cell(row, "fee_amount"))
            rail_rate_raw = (cell(row, "exchange_rate") or "").strip()
        except ValueError as exc:
            result.skipped.append(f"line {line_number}: {exc}")
            continue

        # `Source amount (after fees)` fee nikalne ke baad ka hai, is liye asli
        # gross us mein fee wapas jorne se banta hai — warna laagat kam dikhti.
        transfer = ImportedTransfer(
            external_id=cell(row, "id").strip() or f"line-{line_number}",
            received_on=received_on,
            gross_usd=money(gross_usd + fee),
            net_pkr=net_pkr,
            declared_fee_usd=fee,
            rail_rate=rate(rail_rate_raw) if rail_rate_raw else None,
            reference=reference.strip(),
            payer=cell(row, "payer").strip(),
        )
        pending.append((transfer.external_id, transfer))

    for external_id, transfer in pending:
        extra = extra_fees.pop(external_id, None)
        if extra:
            # Fee alag row mein thi — gross mein bhi jorna parega, warna laagat
            # ka hisaab us fee ko bilkul nazar-andaz kar deta.
            transfer.declared_fee_usd = money(transfer.declared_fee_usd + extra)
            transfer.gross_usd = money(transfer.gross_usd + extra)
            result.fee_rows_applied += 1
        result.transfers.append(transfer)

    for orphan in extra_fees:
        result.skipped.append(f"fee row for unknown transfer {orphan}")

    return result
