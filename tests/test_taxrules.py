"""
Tax rules — aur wo cheezein jo is module ko **mehfooz** banati hain.

Is product ka sab se khatarnak hissa yehi hai: ghalat rate ka matlab kisi ka
ghalat tax return. Is liye ye tests sirf hisaab nahi jaanchte, wo usool bhi
jaanchte hain: har rate ka source ho, na-maloom saal pe tool mana kar de, aur
reduced rate us ko na mile jo us ka haqdaar nahi.
"""
from decimal import Decimal

import pytest

from core.money import money, percent
from core.taxrules import (
    LATEST_TAX_YEAR,
    RULES,
    UnknownTaxYear,
    compare_scenarios,
    estimate,
    rule_for,
)


# --- the safety rules -------------------------------------------------------


def test_every_rule_carries_a_source_and_a_verification_date():
    """Bina source ke rate ek afwah hai."""
    for year, rule in RULES.items():
        assert rule.source_url.startswith("http"), year
        assert rule.verified_on, year
        assert rule.notes, year


def test_an_unknown_tax_year_is_refused_not_guessed():
    """
    Purane saal ka hisaab naye rate pe karna chupke se ghalat jawab dena hai.
    Koi jawab na dena us se behtar hai.
    """
    with pytest.raises(UnknownTaxYear) as exc:
        estimate(1_000_000, pseb_registered=True, tax_year="2019-20")
    assert "2019-20" in str(exc.value)


def test_estimates_are_always_labelled_as_estimates():
    result = estimate(1_000_000, pseb_registered=True)
    assert result.is_estimate is True
    assert result.rule_source.startswith("http")


def test_the_comparison_carries_a_disclaimer():
    assert "not tax advice" in compare_scenarios(1_000_000)["disclaimer"]


# --- the arithmetic ---------------------------------------------------------


def test_pseb_registered_rate():
    # 3,600,000 PKR (~$13k) pe 0.25% = 9,000
    result = estimate(3_600_000, pseb_registered=True)
    assert result.rate_applied == percent("0.0025")
    assert result.tax_pkr == Decimal("9000.00")
    assert "final tax" in result.basis


def test_unregistered_export_rate_is_four_times_higher():
    registered = estimate(3_600_000, pseb_registered=True)
    unregistered = estimate(3_600_000, pseb_registered=False)
    assert unregistered.tax_pkr == Decimal("36000.00")
    assert unregistered.tax_pkr == registered.tax_pkr * 4


def test_the_registration_saving_is_the_selling_point():
    """
    Ye adad hi product ki dalil hai — aur ye asli rates se nikalta hai, kisi
    marketing claim se nahi.
    """
    comparison = compare_scenarios(3_600_000)
    assert comparison["saving_from_registration_pkr"] == Decimal("27000.00")
    # Aur export income claim hi na karne ke muqable farq bohot bara hai.
    assert comparison["saving_vs_not_claiming_pkr"] > Decimal("1200000.00")


# --- the trap this module exists to avoid -----------------------------------


def test_the_reduced_rate_is_withheld_when_banking_channel_is_too_low():
    """
    Reduced rate ki shart hai ke 80% paisa formal banking channel se aaye.

    Agar tool ye shart nazar-andaz kar de to wo user ko ek bachat dikha raha
    hoga jis ka wo haqdaar nahi — aur wo usi bharose pe kam tax bhar dega.
    Yehi is poore module ki sab se ahem soorat hai.
    """
    result = estimate(
        3_600_000, pseb_registered=True, banking_channel_fraction=Decimal("0.55")
    )
    assert result.rate_applied == percent("0.01"), "0.25% nahi milna chahiye"
    assert result.warnings, "wajah likhi honi chahiye"
    assert "80%" in result.warnings[0]


def test_exactly_at_the_threshold_qualifies():
    rule = rule_for()
    result = estimate(
        3_600_000, pseb_registered=True, banking_channel_fraction=rule.banking_channel_minimum
    )
    assert result.rate_applied == rule.pseb_registered_rate
    assert not result.warnings


def test_latest_tax_year_points_at_a_real_rule():
    assert LATEST_TAX_YEAR in RULES
    assert rule_for().tax_year == LATEST_TAX_YEAR
