"""
Report — aur us ki sab se ahem shart: adhoora data chupke se "poora" na bane.
"""
from datetime import date
from decimal import Decimal

from core.fx import ManualRate
from core.importers.wise import ImportedTransfer
from core.money import money, rate
from core.report import build, render


def _transfer(day, gross="3000.00", net="819000.00", fee="30.00", tid="t1") -> ImportedTransfer:
    return ImportedTransfer(
        external_id=tid,
        received_on=day,
        gross_usd=money(gross),
        net_pkr=money(net),
        declared_fee_usd=money(fee),
        rail_rate=rate("273.00"),
        payer="Acme Inc",
    )


def _rates(*days) -> ManualRate:
    source = ManualRate()
    for day in days:
        source.from_prc(day, "279.50")
    return source


def test_a_payment_without_a_rate_is_excluded_and_named():
    """
    Sab se ahem test. Aisi payment ko chupke se chhor dena total export income
    kam dikha deta — aur wo adad seedha tax return mein jata hai.
    """
    d1, d2 = date(2026, 4, 3), date(2026, 5, 3)
    result = build([_transfer(d1, tid="a"), _transfer(d2, tid="b")], _rates(d1))

    assert len(result["income"].payments) == 1
    assert len(result["income"].needs_rate) == 1
    assert "b" in result["income"].needs_rate[0]
    assert result["is_complete"] is False
    assert any("excluded from every figure" in w for w in result["warnings"])


def test_a_complete_report_says_so():
    d1 = date(2026, 4, 3)
    result = build([_transfer(d1)], _rates(d1))
    assert result["is_complete"] is True
    assert not result["income"].needs_rate


def test_hand_entered_rates_are_flagged_against_the_prc():
    d1 = date(2026, 4, 3)
    source = ManualRate()
    source.set(d1, "279.50")  # PRC se nahi
    result = build([_transfer(d1)], source)
    assert any("PRC figure is the one that counts" in w for w in result["warnings"])


def test_rail_cost_totals():
    d1, d2 = date(2026, 4, 3), date(2026, 5, 3)
    result = build([_transfer(d1, tid="a"), _transfer(d2, tid="b")], _rates(d1, d2))
    rails = result["rails"]
    assert rails["total_gross_usd"] == Decimal("6000.00")
    assert rails["total_net_pkr"] == Decimal("1638000.00")
    # Har payment: 838,500 aana chahiye tha, 819,000 aaya -> 19,500 x 2
    assert rails["total_cost_pkr"] == Decimal("39000.00")


def test_average_cost_is_weighted_not_a_plain_mean():
    """
    Ek chhoti mehngi payment ko poore saal jitna wazan dena ghalat tasveer deta.
    """
    d1, d2 = date(2026, 4, 3), date(2026, 5, 3)
    big = _transfer(d1, gross="10000.00", net="2760000.00", tid="big")
    small = _transfer(d2, gross="100.00", net="20000.00", tid="small")  # bohot mehngi
    result = build([big, small], _rates(d1, d2))

    weighted = result["rails"]["average_cost_fraction"]
    plain = (result["income"].payments[0].cost_fraction
             + result["income"].payments[1].cost_fraction) / 2
    assert weighted < plain


def test_tax_uses_pkr_actually_received():
    d1 = date(2026, 4, 3)
    result = build([_transfer(d1)], _rates(d1), pseb_registered=True)
    # 819,000 x 0.25%
    assert result["your_tax_pkr"] == Decimal("2047.50")
    assert "PSEB-registered" in result["your_scenario"]


def test_unregistered_shows_the_registration_saving():
    d1 = date(2026, 4, 3)
    result = build([_transfer(d1)], _rates(d1), pseb_registered=False)
    assert result["tax"]["saving_from_registration_pkr"] > 0
    assert "Registering with PSEB" in render(result)


def test_render_always_carries_the_source_and_disclaimer():
    d1 = date(2026, 4, 3)
    text = render(build([_transfer(d1)], _rates(d1)))
    assert "Rule source:" in text
    assert "not tax advice" in text


def test_render_lists_excluded_payments_prominently():
    d1, d2 = date(2026, 4, 3), date(2026, 5, 3)
    text = render(build([_transfer(d1, tid="a"), _transfer(d2, tid="b")], _rates(d1)))
    assert "PAYMENTS EXCLUDED" in text
    assert "READ THIS" in text


def test_an_empty_statement_does_not_crash():
    result = build([], ManualRate())
    assert result["rails"]["total_cost_pkr"] == Decimal("0.00")
    assert result["your_tax_pkr"] == Decimal("0.00")
    assert "HISAAB" in render(result)
