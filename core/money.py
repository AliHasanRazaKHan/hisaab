"""
Paisa — aur is ka pehla usool: **float kabhi nahi**.

0.1 + 0.2 float mein 0.30000000000000004 hota hai. Ek freelancer ke saal bhar
ke 40-50 remittances pe ye chhoti ghaltiyan jama ho kar us ke tax return mein
ghalat adad daal deti hain, aur wo adad wo FBR ko bhejta hai. Is liye har jagah
`Decimal`, aur har rounding saaf likhi hui.

Do currencies hain aur dono ka kirdar alag hai:
- **USD** — client jo bhejta hai (gross).
- **PKR** — bank mein jo aata hai (net). Tax isi pe lagta hai.

Beech mein rail ki fees aur FX spread hai, aur asal product yehi farq dikhata hai.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# Paisa 2 decimal pe; FX rate 4 pe (PKR/USD aam taur pe 279.4512 jaisa hota hai).
MONEY = Decimal("0.01")
RATE = Decimal("0.0001")
# Percentages 4 decimal pe — 0.25% ko 0.0025 likhte hain, aur us ko round karna
# poore hisaab ko kharab kar deta.
PERCENT = Decimal("0.000001")


def money(value) -> Decimal:
    """Kisi bhi input ko paise mein — str se guzar kar, taake float ka kachra na aaye."""
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def rate(value) -> Decimal:
    return Decimal(str(value)).quantize(RATE, rounding=ROUND_HALF_UP)


def percent(value) -> Decimal:
    return Decimal(str(value)).quantize(PERCENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Remittance:
    """
    Ek payment jo client se bank tak pahunchi.

    `gross_usd` client ne bheja. `net_pkr` bank mein aaya. Beech ka farq do
    hisson mein hai: saaf fees (jo statement pe likhi hoti hai) aur chhupa FX
    spread (jo nahi likhi hoti). Doosra hissa hi bara hota hai, aur usi ko log
    nahi dekhte.
    """

    gross_usd: Decimal
    net_pkr: Decimal
    # Us din ka asli (interbank / mid-market) rate. Is ke bagair spread nikalna
    # mumkin nahi — is liye ye zaroori field hai, optional nahi.
    mid_market_rate: Decimal
    # Rail ki declared fee, agar statement pe alag likhi ho.
    declared_fee_usd: Decimal = Decimal("0.00")

    @property
    def expected_pkr_at_mid_market(self) -> Decimal:
        """Agar koi fee aur koi spread na hota to itne PKR aate."""
        return money(self.gross_usd * self.mid_market_rate)

    @property
    def total_cost_pkr(self) -> Decimal:
        """Poora nuqsan — declared fees aur chhupa spread, dono."""
        return money(self.expected_pkr_at_mid_market - self.net_pkr)

    @property
    def effective_rate(self) -> Decimal:
        """Jo rate us ko waqai mila (net PKR / gross USD)."""
        if self.gross_usd <= 0:
            return Decimal("0.0000")
        return rate(self.net_pkr / self.gross_usd)

    @property
    def cost_fraction(self) -> Decimal:
        """
        Kul laagat, gross ka hissa (0.021 = 2.1%).

        Ye wo ek adad hai jis se do rails ka muqabla hota hai — "Payoneer ne
        2.1% liya, Wise 0.8% leta" — kyunke ye fees aur spread dono ko ek
        jagah jama kar deta hai.
        """
        baseline = self.expected_pkr_at_mid_market
        if baseline <= 0:
            return Decimal("0.000000")
        return percent(self.total_cost_pkr / baseline)

    @property
    def hidden_spread_fraction(self) -> Decimal:
        """
        Wo hissa jo declared fee se bahar hai — yaani FX spread.

        Rails "zero fees" ka ishtihaar de kar yahin se kamate hain. Ye adad us
        dawe ko jaanchne ke liye hai.
        """
        baseline = self.expected_pkr_at_mid_market
        if baseline <= 0:
            return Decimal("0.000000")
        declared_pkr = money(self.declared_fee_usd * self.mid_market_rate)
        return percent((self.total_cost_pkr - declared_pkr) / baseline)
