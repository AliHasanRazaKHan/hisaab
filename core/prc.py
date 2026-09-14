"""
PRC ledger — bank ne waqai kitne PKR diye, aur us ko rail ki aamad se jorna.

**Ye is tool ka sab se authoritative data hai.** Proceeds Realization
Certificate (ePRC) bank ka jaari kiya hua dastaawez hai jo batata hai ke kis
tareekh ko kitni foreign currency aayi aur us ke kitne PKR credit hue. Pakistan
mein export income ka dawa isi kaghaz pe khara hota hai.

Is liye do usool:

1. **PKR ka adad PRC se aata hai, andaze se nahi.** Payoneer ka statement PKR
   bata hi nahi sakta (wo us ke USD account ka record hai). Bank statement PKR
   batata hai magar rate nahi. PRC dono batata hai.
2. **Jo aamad PRC se match na ho wo report mein alag likhi jati hai.** Us ko
   "shayad yehi hai" keh kar jor dena export income ka adad ghalat kar deta hai
   — aur wo adad tax return pe jata hai. Match na hone ki do wajah hoti hain,
   aur dono ahem hain: ya PRC abhi bank se liya nahi (yaani filing adhoori
   hai), ya paisa formal channel se nahi aaya (yaani 80% ki shart pe asar
   parta hai).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from core.importers.generic import RailCredit
from core.importers.wise import ImportedTransfer
from core.money import money, rate as to_rate

# Rail se paisa nikalne aur bank mein aane ke darmiyan ka waqfa. Payoneer ka
# withdrawal aam taur pe 2-5 din leta hai, is liye tareekh bilkul match nahi
# karti aur exact-date matching sab kuch unmatched chhor deti.
DEFAULT_WINDOW_DAYS = 10
# USD raqam mein itna farq qubool hai — bank aur rail ke rounding mein chhota
# antar aata hai.
AMOUNT_TOLERANCE = Decimal("1.00")


@dataclass(frozen=True)
class PrcEntry:
    """Bank ke ePRC ki ek line."""

    issued_on: date
    usd_amount: Decimal
    pkr_credited: Decimal
    certificate_no: str = ""
    bank: str = ""

    @property
    def realised_rate(self) -> Decimal:
        """Jo rate bank ne waqai diya."""
        if self.usd_amount <= 0:
            return Decimal("0.0000")
        return to_rate(self.pkr_credited / self.usd_amount)


@dataclass
class MatchResult:
    matched: list[ImportedTransfer] = field(default_factory=list)
    # Rail pe aamad dikhi magar PRC nahi mila.
    credits_without_prc: list[RailCredit] = field(default_factory=list)
    # PRC mila magar rail pe us ki aamad nahi — doosre rail ya seedhi wire se.
    prc_without_credit: list[PrcEntry] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.credits_without_prc and not self.prc_without_credit

    @property
    def banking_channel_fraction(self) -> Decimal:
        """
        Kitna hissa PRC se sabit hai — yaani formal banking channel se.

        Yehi adad 0.25% wali reduced rate ki shart hai (kam az kam 80%), is liye
        ise andaza lagana nahi chahiye: jo PRC se sabit nahi, wo is kasr mein
        shumar nahi hota.
        """
        proven = sum((t.gross_usd for t in self.matched), Decimal("0"))
        unproven = sum((c.gross_usd for c in self.credits_without_prc), Decimal("0"))
        total = proven + unproven
        if total <= 0:
            return Decimal("1.00")
        return (proven / total).quantize(Decimal("0.0001"))


def match(
    credits: list[RailCredit],
    prc_entries: list[PrcEntry],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    tolerance: Decimal = AMOUNT_TOLERANCE,
) -> MatchResult:
    """
    Rail ki aamad ko PRC se jorta hai — raqam pe, tareekh ke waqfe ke andar.

    Raqam pe match karte hain, tareekh pe nahi, kyunke tareekh kabhi barabar
    nahi hoti (withdrawal 2-5 din leta hai). Aur har PRC sirf ek dafa istemal
    hota hai — warna ek hi PRC do aamad ko "sabit" kar deta aur export income
    dugna dikhta.
    """
    result = MatchResult()
    available = sorted(prc_entries, key=lambda p: p.issued_on)
    used: set[int] = set()

    for credit in sorted(credits, key=lambda c: c.received_on):
        found = None
        for position, entry in enumerate(available):
            if position in used:
                continue
            if abs(entry.usd_amount - credit.gross_usd) > tolerance:
                continue
            gap = entry.issued_on - credit.received_on
            # PRC aamad ke baad aata hai; thora pehle ki tareekh bhi qubool
            # (bank kabhi value date likhta hai).
            if -timedelta(days=2) <= gap <= timedelta(days=window_days):
                found = (position, entry)
                break

        if found is None:
            result.credits_without_prc.append(credit)
            continue

        position, entry = found
        used.add(position)
        result.matched.append(
            ImportedTransfer(
                external_id=credit.external_id,
                # Tax year ka faisla us tareekh se hota hai jab PKR bank mein
                # aaye — yahi wo waqt hai jab income "realised" hoti hai.
                received_on=entry.issued_on,
                gross_usd=credit.gross_usd,
                net_pkr=entry.pkr_credited,
                declared_fee_usd=credit.declared_fee_usd,
                rail_rate=None,
                reference=credit.description or entry.certificate_no,
                payer=credit.counterparty,
            )
        )

    result.prc_without_credit = [
        entry for position, entry in enumerate(available) if position not in used
    ]
    return result


def rates_from_prc(prc_entries: list[PrcEntry]):
    """
    PRC se rate source banata hai.

    Ghaur karein: ye **realised** rate hai (jo bank ne diya), mid-market nahi.
    Dono ka farq hi spread hai — is liye is ko mid-market ki jagah istemal
    karne se spread 0 aa jata hai. Ye function sirf us soorat ke liye hai jab
    user ko sirf tax ka adad chahiye, laagat ka tajziya nahi.
    """
    from core.fx import ManualRate

    source = ManualRate()
    for entry in prc_entries:
        source.set(
            entry.issued_on,
            entry.realised_rate,
            source="bank PRC/ePRC (realised rate)",
            authoritative=True,
            # Ahem: ye mid-market nahi. Is flag ke bagair report laagat 0
            # dikhati hai — yaani "koi nuqsan nahi hua", jo jhoot hai.
            is_mid_market=False,
        )
    return source
