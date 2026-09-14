"""
Wo report jo user ke liye asal cheez hai: "mera paisa kahan gaya, aur tax kitna?"

Do hisse jorta hai — rails ki laagat (core/money.py) aur export tax
(core/taxrules.py) — aur beech mein ek shart hai jo nazar-andaz nahi ki ja
sakti: **mid-market rate ke bagair laagat ka hisaab mumkin nahi**.

Is liye yahan ka sab se ahem faisla: jis payment ka rate maloom nahi, us ko
report **chhorti nahi aur andaza bhi nahi lagati** — us ko `needs_rate` mein
alag rakhti hai. Agar aisi payments ko chupke se chhor diya jata to total
export income kam dikhta, aur wo adad seedha tax return mein jata hai.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from core.fx import RateSource, RateUnavailable
from core.importers.wise import ImportedTransfer
from core.money import Remittance, money, percent
from core.taxrules import compare_scenarios, estimate as estimate_tax


@dataclass
class PaymentCost:
    external_id: str
    received_on: str
    payer: str
    gross_usd: Decimal
    net_pkr: Decimal
    cost_pkr: Decimal
    cost_fraction: Decimal
    hidden_spread_fraction: Decimal
    rate_source: str
    rate_is_authoritative: bool
    rate_is_mid_market: bool = True


@dataclass
class IncomeReport:
    tax_year: str
    payments: list[PaymentCost] = field(default_factory=list)
    # Jin ka rate nahi mila — saaf alag, chupke se gire nahi.
    needs_rate: list[str] = field(default_factory=list)
    # Jo is stage se pehle chhoot gayin (mesal: ePRC na mila).
    excluded_earlier: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_gross_usd(self) -> Decimal:
        return money(sum((p.gross_usd for p in self.payments), Decimal("0")))

    @property
    def total_net_pkr(self) -> Decimal:
        return money(sum((p.net_pkr for p in self.payments), Decimal("0")))

    @property
    def total_cost_pkr(self) -> Decimal:
        return money(sum((p.cost_pkr for p in self.payments), Decimal("0")))

    @property
    def average_cost_fraction(self) -> Decimal:
        """
        Weighted — payments ke size bohot farq hote hain, is liye saada average
        ek chhoti mehngi payment ko poore saal jitna wazan de deta.
        """
        baseline = money(self.total_net_pkr + self.total_cost_pkr)
        if baseline <= 0:
            return Decimal("0.000000")
        return percent(self.total_cost_pkr / baseline)

    @property
    def cost_analysis_available(self) -> bool:
        """Laagat ka hisaab sirf asli mid-market rates pe mumkin hai."""
        return bool(self.payments) and all(p.rate_is_mid_market for p in self.payments)

    @property
    def is_complete(self) -> bool:
        """Tax figures sirf tab bharose ke laiq hain jab har payment shamil ho."""
        return not self.needs_rate and not self.excluded_earlier


def build(
    transfers: list[ImportedTransfer],
    rates: RateSource,
    *,
    tax_year: str | None = None,
    pseb_registered: bool = False,
    banking_channel_fraction=Decimal("1.00"),
    excluded_earlier: list[str] | None = None,
) -> dict:
    """
    `excluded_earlier` un payments ke liye hai jo is stage se **pehle** chhoot
    gayin — mesal ke taur pe wo Payoneer credit jis ka ePRC nahi mila.

    Ye parameter is liye mojood hai ke bina us ke report khud ko "mukammal"
    keh deti thi jab ke export income kam dikh raha tha: matching stage pe
    girne wali payment `needs_rate` mein nahi aati, to `is_complete` True reh
    jata tha aur exit code 0. Wahi ghalti thi jo banking-channel check ke saath
    hui — hissa mehfooz tha, jor mehfooz nahi.
    """
    report = IncomeReport(tax_year=tax_year or "")
    report.excluded_earlier = list(excluded_earlier or [])

    for transfer in transfers:
        try:
            mid = rates.rate_on(transfer.received_on)
        except RateUnavailable:
            report.needs_rate.append(
                f"{transfer.received_on.isoformat()} — {transfer.external_id} "
                f"(${transfer.gross_usd})"
            )
            continue

        remittance = Remittance(
            gross_usd=transfer.gross_usd,
            net_pkr=transfer.net_pkr,
            mid_market_rate=mid.value,
            declared_fee_usd=transfer.declared_fee_usd,
        )
        report.payments.append(
            PaymentCost(
                external_id=transfer.external_id,
                received_on=transfer.received_on.isoformat(),
                payer=transfer.payer,
                gross_usd=transfer.gross_usd,
                net_pkr=transfer.net_pkr,
                cost_pkr=remittance.total_cost_pkr,
                cost_fraction=remittance.cost_fraction,
                hidden_spread_fraction=remittance.hidden_spread_fraction,
                rate_source=mid.source,
                rate_is_authoritative=mid.is_authoritative,
                rate_is_mid_market=mid.is_mid_market,
            )
        )

    if report.excluded_earlier:
        report.warnings.append(
            f"{len(report.excluded_earlier)} payment(s) were excluded before this report "
            "(no matching ePRC), so export income below is understated. Get the ePRC from "
            "your bank for each one before filing."
        )

    if report.needs_rate:
        report.warnings.append(
            f"{len(report.needs_rate)} payment(s) have no recorded mid-market rate, so "
            "they are excluded from every figure below — including export income. Enter "
            "their rate from the bank's PRC before using this for a filing."
        )

    # Realised rate (PRC) ko mid-market ki tarah istemal karne se laagat 0
    # nikalti hai — "koi nuqsan nahi hua", jo jhoot hai. Aisi soorat mein
    # laagat ka tajziya dikhana hi nahi chahiye.
    if report.payments and not report.cost_analysis_available:
        report.warnings.append(
            "Rail cost is not shown: the rates available are realised rates (from the "
            "PRC), which already include the spread — measuring the spread against them "
            "would always report zero. Supply mid-market rates for that day with --rates "
            "to see what the rail actually took."
        )

    non_authoritative = [p for p in report.payments if not p.rate_is_authoritative]
    if non_authoritative:
        report.warnings.append(
            f"{len(non_authoritative)} payment(s) use a hand-entered rate rather than the "
            "bank's PRC. For a Pakistani filing the PRC figure is the one that counts."
        )

    # Export income = jo PKR waqai bank mein aaya.
    tax = compare_scenarios(report.total_net_pkr, tax_year=tax_year)

    # **Yahan `estimate_tax` ko khud bulana zaroori hai, `tax[...]` uthana kaafi
    # nahi.** Pehle yahan `tax["pseb_registered"]` uthaya jata tha, jo default
    # 100% banking-channel pe bana hota hai — nateeja ye ke report 0.25% wala
    # rate dikha deti thi chahe PRC se sirf 75% sabit ho. Safety check
    # `estimate()` ke andar mojood tha aur integration us ke ird-gird se guzar
    # raha tha. Ye asli bug tha, CLI chalane pe pakra gaya.
    chosen = estimate_tax(
        report.total_net_pkr,
        pseb_registered=pseb_registered,
        banking_channel_fraction=banking_channel_fraction,
        tax_year=tax_year,
    )
    report.warnings.extend(chosen.warnings)

    return {
        "income": report,
        "rails": {
            "total_gross_usd": report.total_gross_usd,
            "total_net_pkr": report.total_net_pkr,
            "total_cost_pkr": report.total_cost_pkr,
            "average_cost_fraction": report.average_cost_fraction,
        },
        "tax": tax,
        "your_tax_pkr": chosen.tax_pkr,
        "your_scenario": chosen.scenario,
        "pseb_registered": pseb_registered,
        "is_complete": report.is_complete,
        "warnings": report.warnings,
    }


def render(result: dict) -> str:
    """Plain-text report — CLI aur email dono ke liye kaafi."""
    income: IncomeReport = result["income"]
    rails = result["rails"]
    tax = result["tax"]
    lines: list[str] = []

    lines.append("HISAAB — export income and rail cost")
    lines.append("=" * 58)
    lines.append(f"Tax year: {tax['tax_year']}    Payments counted: {len(income.payments)}")
    lines.append("")

    lines.append("WHAT THE RAILS TOOK")
    lines.append(f"  Client sent            $ {rails['total_gross_usd']:>14,}")
    lines.append(f"  Reached your bank      PKR {rails['total_net_pkr']:>12,}")
    if income.cost_analysis_available:
        lines.append(f"  Lost to fees + spread  PKR {rails['total_cost_pkr']:>12,}"
                     f"   ({rails['average_cost_fraction'] * 100:.2f}%)")
    else:
        lines.append("  Lost to fees + spread  not calculable (see READ THIS below)")
    lines.append("")

    if income.payments and income.cost_analysis_available:
        lines.append("  per payment:")
        for p in income.payments:
            marker = "" if p.rate_is_authoritative else "  (rate entered by hand)"
            lines.append(
                f"    {p.received_on}  ${p.gross_usd:>10,}  cost {p.cost_fraction * 100:>5.2f}%"
                f"  (hidden spread {p.hidden_spread_fraction * 100:>5.2f}%){marker}"
            )
        lines.append("")

    lines.append("EXPORT TAX")
    lines.append(f"  Your situation: {result['your_scenario']}")
    lines.append(f"  Estimated tax:  PKR {result['your_tax_pkr']:>12,}")
    lines.append("")
    if not result["pseb_registered"]:
        lines.append(
            f"  Registering with PSEB would make it PKR "
            f"{tax['pseb_registered'].tax_pkr:,} — a saving of PKR "
            f"{tax['saving_from_registration_pkr']:,}."
        )
    lines.append(
        f"  Not claiming export status at all: PKR {tax['not_claimed_as_export_pkr']:,}"
    )
    lines.append("")

    if income.needs_rate or income.excluded_earlier:
        lines.append("PAYMENTS EXCLUDED — export income below is understated")
        for item in income.excluded_earlier:
            lines.append(f"    {item}")
        for item in income.needs_rate:
            lines.append(f"    {item}  (no mid-market rate recorded)")
        lines.append("")

    if result["warnings"]:
        lines.append("READ THIS")
        for warning in result["warnings"]:
            lines.append(f"  ! {warning}")
        lines.append("")

    lines.append(f"Rule source: {tax['rule_source']} (verified {tax['verified_on']})")
    lines.append(tax["disclaimer"])
    return "\n".join(lines)
