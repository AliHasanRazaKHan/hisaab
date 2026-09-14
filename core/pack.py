"""
Filing record pack — wo cheez jo user apne tax filer ko deta hai.

Report user ko batati hai ke kya ho raha hai. Pack us se alag maqsad rakhta
hai: **filing ke waqt kaam aane wala record**. Filer ko tajziya nahi chahiye,
use teen cheezein chahiye — har remittance ki line, kul raqam, aur ye pata ke
kis cheez ka saboot mojood hai aur kis ka nahi.

Do faisle jo is ki shakal tay karte hain:

1. **Checklist "sab theek hai" kehne se inkar karti hai jab tak waqai theek na
   ho.** Har item ke saath ya to saboot hai ya us ka na hona saaf likha hai.
   Aisi checklist jo hamesha hari dikhe kisi kaam ki nahi — user us pe bharosa
   kar ke ghalat filing kar dega.

2. **Register mein wahi lines jati hain jo PRC se sabit hain**, aur jo sabit
   nahi wo alag hisse mein — kyunke filer ka kaam un dono ko alag alag
   dekhna hai. Ek hi list mein mila dena us se wo farq chhupa deta jo us ke
   liye sab se ahem hai.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from core.money import money
from core.taxrules import rule_for


@dataclass
class ChecklistItem:
    label: str
    ok: bool
    detail: str
    # True ka matlab: ye filing rok deta hai, sirf mashwara nahi.
    blocking: bool = False
    # Kuch sharten har kisi pe lagti hi nahi (mesal: 80% wali shart un pe jo
    # PSEB pe registered hi nahi). Aisi item ko "pass" dikhana jhoot hai aur
    # "fail" dikhana bhi — ye teesri haalat us ke liye hai.
    not_applicable: bool = False

    @property
    def mark(self) -> str:
        if self.not_applicable:
            return "[-]"
        if self.ok:
            return "[x]"
        return "[!]" if self.blocking else "[ ]"


@dataclass
class FilingPack:
    tax_year: str
    generated_on: str
    export_income_pkr: Decimal
    estimated_tax_pkr: Decimal
    scenario: str
    rule_source: str
    register: list[dict] = field(default_factory=list)
    unproven: list[str] = field(default_factory=list)
    checklist: list[ChecklistItem] = field(default_factory=list)

    @property
    def ready_to_file(self) -> bool:
        return not any(
            item.blocking and not item.ok and not item.not_applicable
            for item in self.checklist
        )


def build_pack(result: dict) -> FilingPack:
    """
    Report ke natije se filing pack banata hai.

    `result` wahi dict hai jo `core.report.build()` deta hai — is liye pack
    kabhi report se alag adad nahi dikha sakta.
    """
    income = result["income"]
    tax = result["tax"]
    rule = rule_for(tax["tax_year"])

    pack = FilingPack(
        tax_year=tax["tax_year"],
        generated_on=datetime.utcnow().date().isoformat(),
        export_income_pkr=result["income"].total_net_pkr,
        estimated_tax_pkr=result["your_tax_pkr"],
        scenario=result["your_scenario"],
        rule_source=tax["rule_source"],
    )

    for payment in income.payments:
        pack.register.append(
            {
                "date_credited": payment.received_on,
                "payer": payment.payer,
                "gross_usd": str(payment.gross_usd),
                "pkr_credited": str(payment.net_pkr),
                "reference": payment.external_id,
                "rate_source": payment.rate_source,
                "prc_backed": "yes" if payment.rate_is_authoritative else "no",
            }
        )

    pack.unproven = list(income.excluded_earlier) + list(income.needs_rate)

    proven = [p for p in income.payments if p.rate_is_authoritative]
    pack.checklist = [
        ChecklistItem(
            label="Every remittance has an ePRC from the bank",
            ok=not pack.unproven and bool(income.payments),
            detail=(
                "All remittances accounted for"
                if not pack.unproven and income.payments
                else f"{len(pack.unproven)} remittance(s) without an ePRC — export income "
                "below is understated until you obtain them"
            ),
            # Ye filing rok deta hai: ePRC hi export income ka saboot hai.
            blocking=True,
        ),
        ChecklistItem(
            label="PKR figures come from the ePRC, not a third-party rate",
            ok=bool(income.payments) and len(proven) == len(income.payments),
            detail=(
                "All figures PRC-backed"
                if income.payments and len(proven) == len(income.payments)
                else f"{len(income.payments) - len(proven)} figure(s) are hand-entered; the "
                "ePRC amount is the one a filing rests on"
            ),
            blocking=True,
        ),
        # Ye shart sirf PSEB-registered logon pe lagti hai — reduced rate ki
        # shart hai. Un pe jo registered hi nahi, ise "pass" dikhana ghalat
        # ta'assur deta tha: pehle item `[x]` dikhti thi aur us ke neeche likha
        # hota "Below the threshold", yaani checklist khud apni baat ka ulta
        # keh rahi thi (apne hi output mein pakra gaya).
        ChecklistItem(
            label=(
                f"At least {rule.banking_channel_minimum * 100:.0f}% of income arrived "
                "through formal banking channels"
            ),
            ok=bool(result["pseb_registered"]) and "no PSEB registration" not in pack.scenario,
            not_applicable=not result["pseb_registered"],
            detail=(
                "Only relevant once you are PSEB-registered — this is the condition for "
                "the reduced rate"
                if not result["pseb_registered"]
                else "Condition met"
                if "no PSEB registration" not in pack.scenario
                else "Below the threshold, so the reduced rate does not apply — the 1% "
                "rate is used instead"
            ),
        ),
        ChecklistItem(
            label="PSEB registration",
            ok=bool(result["pseb_registered"]),
            detail=(
                "Registered — reduced rate claimed"
                if result["pseb_registered"]
                else f"Not registered. Registering would change the estimate to PKR "
                f"{tax['pseb_registered'].tax_pkr:,} "
                f"(a difference of PKR {tax['saving_from_registration_pkr']:,})"
            ),
        ),
        ChecklistItem(
            label="Rates verified against FBR for this tax year",
            ok=False,
            detail=(
                f"Rates in this tool were taken from {rule.source_url} on "
                f"{rule.verified_on} — a published guide, not the Finance Act. Confirm at "
                "fbr.gov.pk before filing."
            ),
            # Jaan-boojh kar hamesha unchecked: hum ne primary source nahi dekha.
            blocking=True,
        ),
    ]
    return pack


def register_csv(pack: FilingPack) -> str:
    """Remittance register — filer ke liye machine-readable."""
    columns = [
        "date_credited",
        "payer",
        "gross_usd",
        "pkr_credited",
        "reference",
        "rate_source",
        "prc_backed",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in pack.register:
        writer.writerow(row)
    return buffer.getvalue()


def render_pack(pack: FilingPack) -> str:
    total_usd = money(sum((Decimal(r["gross_usd"]) for r in pack.register), Decimal("0")))
    lines = [
        "EXPORT INCOME — FILING RECORD PACK",
        "=" * 58,
        f"Tax year        {pack.tax_year}",
        f"Prepared        {pack.generated_on}",
        f"Remittances     {len(pack.register)}",
        "",
        f"Gross received  $ {total_usd:,}",
        f"PKR credited    PKR {pack.export_income_pkr:,}",
        f"Basis           {pack.scenario}",
        f"Estimated tax   PKR {pack.estimated_tax_pkr:,}",
        "",
        "BEFORE FILING",
    ]
    for item in pack.checklist:
        lines.append(f"  {item.mark} {item.label}")
        lines.append(f"      {item.detail}")

    if pack.unproven:
        lines.append("")
        lines.append("NOT INCLUDED ABOVE — no ePRC on file")
        for item in pack.unproven:
            lines.append(f"    {item}")

    lines.append("")
    lines.append(
        "READY TO FILE" if pack.ready_to_file
        else "NOT READY TO FILE — see the [!] items above    ([-] = does not apply to you)"
    )
    lines.append("")
    lines.append(f"Rate rules: {pack.rule_source}")
    lines.append(
        "Estimates from published rates, not tax advice. Have a tax practitioner review "
        "this before submission."
    )
    return "\n".join(lines)
