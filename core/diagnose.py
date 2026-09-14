"""
"Ye file kaisi hai?" — parser fail hone se pehle jawab dena.

**Ye feature us khaami se nikla hai jo abhi mojood hai:** Wise aur Payoneer ke
parsers un ke *documented* format pe bane hain, asli exported file pe nahi. Aur
pichle project mein har feed bug asli data se mila tha, docs se ek bhi nahi. To
ye maan lena chahiye ke kisi asli file pe format match nahi karega.

Aise waqt do rawayye hain. Bura rawayya: "Import failed" likh kar chup ho jana
— user ke paas na wajah hai na hal. Accha rawayya: file dekh kar batana ke is
mein kaun se columns hain, hum ne kya samjha, aur kya kami hai — taake user
khud mapping de sake (`ColumnMap`) bina hamare code ko badalne ke.

Yaani jab tak asli formats tasdeeq nahi hote, tool khud ko user ke haath se
theek karwa sakta hai.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from core.importers.generic import PRESETS, ColumnMap
from core.importers.wise import COLUMNS as WISE_COLUMNS

# Column ke naam pehchanne ke ishare — sirf andaza, faisla user ka.
HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transactiondate", "valuedate", "finishedon", "createdon", "postingdate"),
    # Tarteeb ahem hai: pehle wo naam jo zyada makhsoos hain. "targetamount"
    # ko "amount" se pehle rakha hai taake Wise mein wo jeete.
    "amount": (
        "targetamountafterfees", "targetamount", "amount", "credit",
        "sourceamountafterfees", "value", "netamount",
    ),
    "currency": ("targetcurrency", "currency", "ccy", "sourcecurrency"),
    "description": ("description", "narrative", "details", "reference", "memo", "particulars"),
    "external_id": ("id", "referenceid", "transactionid", "transferid", "reference"),
    "fee": ("fee", "fees", "charge", "charges", "commission", "sourcefeeamount"),
    "counterparty": ("source", "sourcename", "payer", "from", "client", "createdby"),
}


@dataclass
class Diagnosis:
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    delimiter: str = ","
    looks_like: str | None = None
    guessed: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    sample: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Kam az kam date aur amount pehchan mein aa gaye?"""
        return "date" in self.guessed and "amount" in self.guessed

    def suggested_map(self) -> ColumnMap | None:
        if not self.usable:
            return None
        return ColumnMap(
            date=self.guessed["date"],
            amount=self.guessed["amount"],
            currency=self.guessed.get("currency"),
            description=self.guessed.get("description"),
            external_id=self.guessed.get("external_id"),
            fee=self.guessed.get("fee"),
            counterparty=self.guessed.get("counterparty"),
        )

    def explain(self) -> str:
        lines = [f"Found {len(self.columns)} columns, {self.row_count} data row(s)."]
        if self.looks_like:
            lines.append(f"This looks like a {self.looks_like} export.")
        lines.append("")
        lines.append("Columns: " + ", ".join(self.columns))
        lines.append("")
        if self.guessed:
            lines.append("Best guess at the mapping:")
            for field_name, column in self.guessed.items():
                lines.append(f"  {field_name:<14} <- {column!r}")
        if self.missing:
            lines.append("")
            lines.append("Could not identify: " + ", ".join(self.missing))
            lines.append(
                "  Pass a ColumnMap naming those columns yourself — no code change needed."
            )
        if self.sample:
            lines.append("")
            lines.append("First data row:")
            for column, value in zip(self.columns, self.sample):
                lines.append(f"  {column:<28} {value!r}")
        return "\n".join(lines)


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Sniffer chhoti files pe aksar haar jata hai — sab se aam ginti se faisla.
        counts = {d: sample.count(d) for d in ",;\t|"}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def diagnose(content: str | bytes) -> Diagnosis:
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    head = "\n".join(content.splitlines()[:5])
    delimiter = _sniff_delimiter(head)

    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return Diagnosis(delimiter=delimiter)

    header = [h.strip() for h in rows[0]]
    data = [r for r in rows[1:] if any((c or "").strip() for c in r)]
    result = Diagnosis(
        columns=header,
        row_count=len(data),
        delimiter=delimiter,
        sample=data[0] if data else [],
    )

    normalised = {_normalise(h): h for h in header}

    # Maloom format pehchanna — Wise ke apne column naam sab se khaas hain.
    wise_markers = {_normalise(c) for names in WISE_COLUMNS.values() for c in names}
    if len({_normalise(h) for h in header} & wise_markers) >= 6:
        result.looks_like = "Wise"
    else:
        for name, preset in PRESETS.items():
            wanted = {_normalise(c) for c in preset.fields().values()}
            if len({_normalise(h) for h in header} & wanted) >= 3:
                result.looks_like = name.title()
                break

    # Pehle theek match, phir "shuru mein aata hai" — kyunke asli columns aksar
    # suffix le kar aate hain: Wise ka "Target amount (after fees)"
    # normalise ho kar "targetamountafterfees" banta hai, aur sirf exact match
    # dekhne se wo chhoot jata tha (apne hi test ne pakra).
    for field_name, hints in HINTS.items():
        for hint in hints:
            if hint in normalised:
                result.guessed[field_name] = normalised[hint]
                break
        else:
            for hint in hints:
                match = next(
                    (original for key, original in normalised.items() if key.startswith(hint)),
                    None,
                )
                if match:
                    result.guessed[field_name] = match
                    break

    for required in ("date", "amount"):
        if required not in result.guessed:
            result.missing.append(required)

    return result
