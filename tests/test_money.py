"""
Paise ka hisaab — aur wo teen ghaltiyan jo asal mein log karte hain.

Sab se ahem test `test_money_never_uses_float` hai: float ka istemal is domain
mein bug nahi, nuqsan hai. Jo adad yahan se nikalta hai wo kisi ka tax return
mein jata hai.
"""
from decimal import Decimal

import pytest

from core.money import Remittance, money, percent, rate


def test_money_never_uses_float():
    """0.1 + 0.2 float mein 0.30000000000000004 hai. 50 remittances pe ye jama hota hai."""
    total = money(0) + money("0.1") + money("0.2")
    assert total == Decimal("0.30")
    assert str(total) == "0.30"


def test_float_input_is_accepted_but_normalised():
    # UI/JSON se float aa sakta hai; str se guzar kar us ka kachra saaf hota hai.
    assert money(0.1 + 0.2) == Decimal("0.30")


def _payoneer_style() -> Remittance:
    """
    Asli shakal ka payment: $3,000, mid-market 279.50, bank mein 819,000 PKR.
    Declared fee $30 (1%), magar asal nuqsan us se zyada hai.
    """
    return Remittance(
        gross_usd=money(3000),
        net_pkr=money(819000),
        mid_market_rate=rate("279.50"),
        declared_fee_usd=money(30),
    )


def test_expected_pkr_at_mid_market():
    assert _payoneer_style().expected_pkr_at_mid_market == Decimal("838500.00")


def test_total_cost_includes_fees_and_spread():
    r = _payoneer_style()
    # 838,500 aana chahiye tha, 819,000 aaya.
    assert r.total_cost_pkr == Decimal("19500.00")


def test_cost_fraction_is_the_number_that_compares_rails():
    r = _payoneer_style()
    # 19,500 / 838,500 = 2.325%
    assert r.cost_fraction == percent("0.023256")


def test_hidden_spread_is_separated_from_the_declared_fee():
    """
    Rails "low fees" ka ishtihaar dete hain aur FX spread se kamate hain. Is
    liye declared fee aur chhupa spread alag alag dikhna zaroori hai.
    """
    r = _payoneer_style()
    # Declared $30 = 8,385 PKR. Baqi 11,115 PKR chhupa spread hai.
    assert r.hidden_spread_fraction == percent("0.013256")  # 11,115 / 838,500
    assert r.hidden_spread_fraction < r.cost_fraction


def test_effective_rate_is_what_they_actually_got():
    r = _payoneer_style()
    assert r.effective_rate == rate("273.00")  # 819000 / 3000
    assert r.effective_rate < r.mid_market_rate


def test_a_cheaper_rail_shows_a_smaller_cost_fraction():
    """Wise-style: same payment, kam spread."""
    wise = Remittance(
        gross_usd=money(3000),
        net_pkr=money(832000),
        mid_market_rate=rate("279.50"),
        declared_fee_usd=money("13.50"),
    )
    assert wise.cost_fraction < _payoneer_style().cost_fraction
    # Ek payment pe farq — yehi wo adad hai jo user ko dikhana hai.
    saving = _payoneer_style().total_cost_pkr - wise.total_cost_pkr
    assert saving == Decimal("13000.00")


@pytest.mark.parametrize("gross", [0, "0.00"])
def test_zero_gross_does_not_divide_by_zero(gross):
    r = Remittance(gross_usd=money(gross), net_pkr=money(0), mid_market_rate=rate("279.50"))
    assert r.cost_fraction == Decimal("0.000000")
    assert r.effective_rate == Decimal("0.0000")
