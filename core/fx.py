"""
Mid-market rate kahan se aaye — aur ye sawal design ko badal deta hai.

**Statement se spread nikalna mumkin nahi.** Wise (aur baqi rails) jo
"Exchange rate" likhte hain wo *un ka diya hua* rate hai, mid-market nahi.
Yaani chhupa spread nikalne ke liye us din ka asli rate bahar se chahiye.

Aur wo aasani se nahi milta — 2026-09-15 pe jaancha gaya:

| Source | Natija |
|---|---|
| Frankfurter (ECB) | **PKR hi nahi hai** — sirf 30 bari currencies |
| exchangerate.host | ab API key maangta hai |
| exchangerate-api free | `latest` deta hai, **historical nahi** (404) |
| stooq | JavaScript proof-of-work challenge |
| SBP (sbp.org.pk) | 403 |

Do options bachte hain: koi keyed API, ya **user khud rate de**. Aur ghaur se
dekhein to doosra option behtar hai, kam nahi:

**Bank ka PRC/ePRC us rate aur PKR raqam ko khud likhta hai** — aur Pakistani
tax filing ke liye wohi dastaawez authoritative hai. To jo adad file hona hai
wo pehle se ek sarkari kaghaz pe mojood hai. Aisi soorat mein tool ka kaam
andaza lagana nahi, us adad ko lena hai. Kisi tisre API ka rate le kar PRC se
mukhtalif adad dikhana user ko us ke apne dastaawez ke khilaf le jana hai.

Is liye default `ManualRate` hai, aur koi bhi automatic provider optional —
`configured()` false ho to tool saaf kehta hai ke rate chahiye, khud nahi
banata. (Wahi rawayya jo Stripe/Elasticsearch ke saath hai: mojood na ho to
degrade karo, jhoot na bolo.)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from core.money import rate as to_rate


class RateUnavailable(LookupError):
    """Us din ka rate maloom nahi — andaza lagane se behtar hai mana karna."""


@dataclass(frozen=True)
class MidMarketRate:
    """Ek rate, aur us ka hawala. Bina source ke rate ek afwah hai."""

    on: date
    pair: str
    value: Decimal
    source: str
    # PRC se aaya ho to ye sab se mazboot soorat hai — sarkari dastaawez.
    is_authoritative: bool = False
    # **Ye rate mid-market hai ya wo rate jo waqai mila?**
    #
    # PRC ka rate *realised* hai — us mein spread pehle se shamil hai. Us ko
    # mid-market ki jagah rakhne se laagat 0 nikalti hai, yaani tool kehta hai
    # "koi nuqsan nahi hua", jo jhoot hai. Is liye ye flag zaroori hai aur
    # report is pe laagat ka tajziya band kar deti hai.
    is_mid_market: bool = True


class RateSource(Protocol):
    def configured(self) -> bool: ...
    def rate_on(self, day: date, pair: str = "USD/PKR") -> MidMarketRate: ...


class ManualRate:
    """
    User (ya us ka PRC) jo rate deta hai.

    Default source yehi hai — aur PRC wale adad ke liye `authoritative=True`
    rakha jata hai, taake report mein farq dikh sake ke ye adad kahan se aaya.
    """

    def __init__(self) -> None:
        self._rates: dict[tuple[date, str], MidMarketRate] = {}

    def configured(self) -> bool:
        return True

    def set(
        self,
        day: date,
        value,
        *,
        pair: str = "USD/PKR",
        source: str = "entered by hand",
        authoritative: bool = False,
        is_mid_market: bool = True,
    ) -> MidMarketRate:
        record = MidMarketRate(
            on=day,
            pair=pair,
            value=to_rate(value),
            source=source,
            is_authoritative=authoritative,
            is_mid_market=is_mid_market,
        )
        self._rates[(day, pair)] = record
        return record

    def from_prc(self, day: date, value, *, pair: str = "USD/PKR") -> MidMarketRate:
        """Bank ke PRC/ePRC se — Pakistani filing ke liye authoritative adad."""
        return self.set(
            day, value, pair=pair, source="bank PRC/ePRC", authoritative=True
        )

    def rate_on(self, day: date, pair: str = "USD/PKR") -> MidMarketRate:
        try:
            return self._rates[(day, pair)]
        except KeyError:
            raise RateUnavailable(
                f"No {pair} rate recorded for {day.isoformat()}. Enter it from the "
                "bank's PRC/ePRC — that is the figure a Pakistani filing rests on."
            ) from None


class NullRateSource:
    """
    Koi automatic provider configure nahi hua.

    Ye jaan-boojh kar mojood hai: "provider nahi hai" ko chupke se "rate 0" ya
    "aaj ka rate" ban jane dena wo ghalti hai jis se poora spread ka hisaab
    jhoot ho jata hai.
    """

    def configured(self) -> bool:
        return False

    def rate_on(self, day: date, pair: str = "USD/PKR") -> MidMarketRate:
        raise RateUnavailable(
            "No automatic rate provider is configured, and historical USD/PKR is not "
            "freely available (see core/fx.py). Enter the rate from your PRC instead."
        )
