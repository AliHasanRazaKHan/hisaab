"""
Pakistan ke IT-export tax rules — **cited data ki shakal mein, code mein dafan nahi**.

Ye is product ka sab se khatarnak hissa hai. Ghalat rate ka matlab ye hai ke
koi shakhs FBR ko ghalat adad bhejta hai, aur nateeja us ka bhugta hai, hamara
nahi. Is liye teen usool:

1. **Har rate ke saath tax year, us ka source aur fetch ki tareekh.** Rates har
   Finance Act ke saath badalte hain. Bina source ke rate ek afwah hai.
2. **Kabhi "tax advice" nahi — hisaab aur hawala.** Tool adad nikal kar ye
   batata hai ke kis rule se nikla aur wo rule kahan likha hai. Faisla
   user aur us ke tax advisor ka.
3. **Purana tax year chupke se naye rate pe hisaab nahi karega.** Agar us saal
   ka rule mojood nahi, tool saaf mana kar deta hai (`UnknownTaxYear`) — ghalat
   jawab dene se behtar hai koi jawab na dena.

Rates verify kiye gaye 2026-09-15. Naya Finance Act aaye to `RULES` mein naya
entry daalo; purane entries **kabhi na badlo** (wo mazi ka record hain).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from core.money import money, percent


class UnknownTaxYear(LookupError):
    """Us saal ka rule hamare paas nahi — andaza lagane se behtar hai mana karna."""


@dataclass(frozen=True)
class TaxRule:
    tax_year: str
    # PSEB pe registered IT/ITeS exporter, paisa banking channel se aaya.
    pseb_registered_rate: Decimal
    # IT export income magar PSEB registration ke bagair.
    unregistered_export_rate: Decimal
    # Reduced rate ke liye kitna hissa formal banking channel se aana chahiye.
    banking_channel_minimum: Decimal
    # Final tax = is income pe slab rate ka koi mutalba nahi.
    is_final_tax: bool
    # Muqable ke liye — "agar export income claim hi na ki" wali soorat.
    top_slab_rate: Decimal
    source_url: str
    verified_on: str
    notes: str = ""


RULES: dict[str, TaxRule] = {
    "2025-26": TaxRule(
        tax_year="2025-26",
        pseb_registered_rate=percent("0.0025"),      # 0.25%
        unregistered_export_rate=percent("0.01"),    # 1%
        banking_channel_minimum=percent("0.80"),     # 80%
        is_final_tax=True,
        top_slab_rate=percent("0.35"),
        source_url="https://pakistantaxes.com/freelancer-tax-pakistan/",
        verified_on="2026-09-15",
        notes=(
            "0.25% concession Tax Year 2029 tak barhaya gaya hai. Formal banking "
            "channel zaroori hai (Payoneer/Wise/SWIFT theek, hawala nahi). Rates "
            "har Finance Act pe badal sakte hain — fbr.gov.pk pe tasdeeq karo."
        ),
    ),
}

# Naya saal add karte waqt sirf yahan badlo.
LATEST_TAX_YEAR = "2025-26"


def rule_for(tax_year: str | None = None) -> TaxRule:
    year = tax_year or LATEST_TAX_YEAR
    if year not in RULES:
        raise UnknownTaxYear(
            f"No verified tax rule for {year}. Known years: {', '.join(sorted(RULES))}. "
            "Add it to core/taxrules.py with a source before relying on a number."
        )
    return RULES[year]


@dataclass(frozen=True)
class TaxEstimate:
    """
    Ek hisaab — aur us ke saath wajah, rule aur source.

    `is_estimate` hamesha True hai aur jaan-boojh kar field ki shakal mein hai:
    jo bhi is ko UI ya PDF mein dikhaye, us ko ye saaf likhna parega.
    """

    tax_year: str
    export_income_pkr: Decimal
    scenario: str
    rate_applied: Decimal
    tax_pkr: Decimal
    rule_source: str
    basis: str
    warnings: list[str] = field(default_factory=list)
    is_estimate: bool = True


def estimate(
    export_income_pkr,
    *,
    pseb_registered: bool,
    banking_channel_fraction=Decimal("1.00"),
    tax_year: str | None = None,
) -> TaxEstimate:
    """
    Export income pe tax ka andaza.

    `banking_channel_fraction` ahem hai: reduced rate ki shart hi yehi hai.
    Agar us had se neeche ho to tool reduced rate **nahi** lagata aur wajah
    warning mein likhta hai — warna tool us ko ek aisi bachat dikha raha hota
    jis ka wo haqdaar nahi, aur wo usi bharose pe kam tax bhar deta.
    """
    rule = rule_for(tax_year)
    income = money(export_income_pkr)
    fraction = percent(banking_channel_fraction)
    warnings: list[str] = []

    qualifies = pseb_registered and fraction >= rule.banking_channel_minimum

    if pseb_registered and not qualifies:
        warnings.append(
            f"Only {fraction * 100:.1f}% of income came through formal banking channels; "
            f"the reduced rate needs at least {rule.banking_channel_minimum * 100:.0f}%. "
            "The 1% export rate is applied instead."
        )

    if qualifies:
        scenario = "PSEB-registered IT/ITeS export"
        applied = rule.pseb_registered_rate
        basis = f"{applied * 100:.2f}% final tax on gross export proceeds"
    else:
        scenario = "IT export income, no PSEB registration"
        applied = rule.unregistered_export_rate
        basis = f"{applied * 100:.2f}% final tax on gross export proceeds"

    if rule.is_final_tax:
        basis += " (final tax — slab rates do not apply to this income)"

    return TaxEstimate(
        tax_year=rule.tax_year,
        export_income_pkr=income,
        scenario=scenario,
        rate_applied=applied,
        tax_pkr=money(income * applied),
        rule_source=rule.source_url,
        basis=basis,
        warnings=warnings,
    )


def compare_scenarios(export_income_pkr, *, tax_year: str | None = None) -> dict:
    """
    Teen soorat ka muqabla — yehi is product ki asal dalil hai.

    Adad chhota nahi hai: 0.25% aur 1% mein chaar guna ka farq hai, aur export
    income claim hi na karne pe farq slab rate tak chala jata hai.
    """
    rule = rule_for(tax_year)
    income = money(export_income_pkr)

    registered = estimate(income, pseb_registered=True, tax_year=rule.tax_year)
    unregistered = estimate(income, pseb_registered=False, tax_year=rule.tax_year)
    not_claimed_tax = money(income * rule.top_slab_rate)

    return {
        "tax_year": rule.tax_year,
        "export_income_pkr": income,
        "pseb_registered": registered,
        "unregistered_export": unregistered,
        # Ye scenario un logon ke liye hai jo export income declare hi nahi
        # karte — aur ye sab se mehnga hai.
        "not_claimed_as_export_pkr": not_claimed_tax,
        "saving_from_registration_pkr": money(unregistered.tax_pkr - registered.tax_pkr),
        "saving_vs_not_claiming_pkr": money(not_claimed_tax - registered.tax_pkr),
        "rule_source": rule.source_url,
        "verified_on": rule.verified_on,
        "disclaimer": (
            "Estimates from published rates, not tax advice. Rates change with each "
            "Finance Act — verify at fbr.gov.pk or with a tax advisor before filing."
        ),
    }
