"""
Generic importer + PRC matching.

Buniyadi baat jo ye tests sabit karte hain: **Payoneer ka statement PKR bata
hi nahi sakta**, aur us haqeeqat ko design mein maan lena zaroori hai. PKR
bank ke PRC se aata hai, aur jo aamad PRC se match na ho wo "shayad" jor di
jaye to export income ka adad ghalat ho jata hai.
"""
from datetime import date
from decimal import Decimal

import pytest

from core.importers.generic import ColumnMap, GenericImportResult, parse
from core.importers.wise import ImportProblem
from core.prc import PrcEntry, match, rates_from_prc

PAYONEER = (
    "Transaction Date,Description,Amount,Currency,Source,Target,Reference ID\n"
    '03-04-2026,"Payment from Acme Inc",3000.00,USD,Acme Inc,Balance,REF-1\n'
    '05-05-2026,"Payment from Globex",2500.00,USD,Globex,Balance,REF-2\n'
    '06-05-2026,"Withdrawal fee",-9.95,USD,Payoneer,Bank,REF-3\n'
    '06-05-2026,"Withdrawal to bank",-5490.05,USD,Balance,Bank,REF-4\n'
    '07-05-2026,"Payment from Initech",1200.00,EUR,Initech,Balance,REF-5\n'
)


# --- the generic importer ---------------------------------------------------


def test_payoneer_preset_reads_credits_only():
    result = parse(PAYONEER, "payoneer")
    assert [c.gross_usd for c in result.credits] == [Decimal("3000.00"), Decimal("2500.00")]
    # Withdrawal aamad nahi; EUR row hamari currency nahi.
    assert any("not a credit" in s for s in result.skipped)
    assert any("EUR" in s for s in result.skipped)


def test_a_payoneer_credit_carries_no_pkr_because_it_cannot():
    """
    Payoneer ka statement us ke USD account ka record hai. PKR tab bante hain
    jab paisa bank mein aata hai — wo lenden is file mein hi nahi.
    """
    credit = parse(PAYONEER, "payoneer").credits[0]
    assert not hasattr(credit, "net_pkr")


def test_fee_rows_are_folded_across_credits_not_dropped():
    """
    Payoneer fee ko kisi khaas transfer se nahi jorta, is liye us ko hisse ke
    mutabiq baanta jata hai — aur chhorna galat hoga kyunke laagat kam dikhti.
    """
    result = parse(PAYONEER, "payoneer")
    assert result.fees_folded == 1
    total_fee = sum(c.declared_fee_usd for c in result.credits)
    assert total_fee == Decimal("9.95")
    # Bari aamad pe zyada hissa.
    assert result.credits[0].declared_fee_usd > result.credits[1].declared_fee_usd


def test_fee_rows_can_be_kept_out_when_asked():
    result = parse(PAYONEER, "payoneer", fold_fee_rows=False)
    assert result.fees_folded == 0
    assert any("fee row" in s for s in result.skipped)


def test_a_custom_column_map_handles_any_statement():
    """Har rail ka format guess karne se behtar hai user bataye."""
    csv_text = (
        "Value Date;Narrative;Credit;Ccy\n"
        "03-04-2026;Salary from client;1500.00;USD\n"
    ).replace(";", ",")
    result = parse(
        csv_text,
        ColumnMap(date="Value Date", amount="Credit", currency="Ccy", description="Narrative"),
    )
    assert result.credits[0].gross_usd == Decimal("1500.00")


def test_bracketed_and_symbol_amounts_are_understood():
    csv_text = (
        "Transaction Date,Description,Amount,Currency,Source,Target,Reference ID\n"
        '03-04-2026,"Payment","$2,000.00",USD,Acme,Balance,R1\n'
        '04-04-2026,"Bank fee","(15.00)",USD,Bank,Bank,R2\n'
    )
    result = parse(csv_text, "payoneer")
    assert result.credits[0].gross_usd == Decimal("2000.00")
    assert result.credits[0].declared_fee_usd == Decimal("15.00")


def test_an_unknown_preset_is_refused_with_the_known_ones():
    with pytest.raises(ImportProblem) as exc:
        parse(PAYONEER, "deel")
    assert "payoneer" in str(exc.value)


def test_a_missing_mapped_column_is_refused():
    with pytest.raises(ImportProblem) as exc:
        parse("A,B\n1,2", ColumnMap(date="Nope", amount="Also nope"))
    assert "not found" in str(exc.value)


# --- PRC matching -----------------------------------------------------------


def _credits():
    return parse(PAYONEER, "payoneer").credits


def test_matching_pairs_a_credit_with_its_prc():
    credits = _credits()
    prc = [PrcEntry(date(2026, 4, 8), Decimal("3000.00"), Decimal("834000.00"), "PRC-1", "HBL")]
    result = match(credits, prc)

    assert len(result.matched) == 1
    transfer = result.matched[0]
    assert transfer.net_pkr == Decimal("834000.00")
    # Tax year us tareekh se tay hota hai jab PKR bank mein aaye.
    assert transfer.received_on == date(2026, 4, 8)


def test_a_credit_with_no_prc_is_reported_never_assumed():
    """
    "Shayad yehi hai" keh kar jor dena export income ka adad ghalat kar deta —
    aur wo adad tax return pe jata hai.
    """
    result = match(_credits(), [])
    assert len(result.matched) == 0
    assert len(result.credits_without_prc) == 2
    assert result.is_complete is False


def test_a_prc_with_no_matching_credit_is_also_reported():
    """Doosre rail ya seedhi wire se aayi raqam — chupke se gire nahi."""
    prc = [PrcEntry(date(2026, 6, 1), Decimal("900.00"), Decimal("250000.00"))]
    result = match(_credits(), prc)
    assert len(result.prc_without_credit) == 1


def test_one_prc_cannot_prove_two_credits():
    """Warna ek hi PRC do aamad ko sabit kar deta aur income dugna dikhta."""
    credits = [
        c for c in parse(
            "Transaction Date,Description,Amount,Currency,Source,Target,Reference ID\n"
            '03-04-2026,"Payment A",3000.00,USD,Acme,Balance,A\n'
            '04-04-2026,"Payment B",3000.00,USD,Acme,Balance,B\n',
            "payoneer",
        ).credits
    ]
    prc = [PrcEntry(date(2026, 4, 8), Decimal("3000.00"), Decimal("834000.00"))]
    result = match(credits, prc)
    assert len(result.matched) == 1
    assert len(result.credits_without_prc) == 1


def test_matching_tolerates_the_settlement_delay():
    """Withdrawal 2-5 din leta hai; exact-date matching sab unmatched chhor deti."""
    credits = _credits()[:1]
    prc = [PrcEntry(date(2026, 4, 12), Decimal("3000.00"), Decimal("834000.00"))]
    assert len(match(credits, prc).matched) == 1
    # ...magar be-hadd waqfa nahi.
    far = [PrcEntry(date(2026, 8, 1), Decimal("3000.00"), Decimal("834000.00"))]
    assert len(match(credits, far).matched) == 0


def test_banking_channel_fraction_counts_only_what_a_prc_proves():
    """
    Yehi adad 0.25% wali reduced rate ki shart hai (>=80%). Jo PRC se sabit
    nahi, wo is mein shumar nahi hota.
    """
    credits = _credits()  # 3000 + 2500
    prc = [PrcEntry(date(2026, 4, 8), Decimal("3000.00"), Decimal("834000.00"))]
    result = match(credits, prc)
    # 3000 of 5500 proven
    assert result.banking_channel_fraction == Decimal("0.5455")
    assert result.banking_channel_fraction < Decimal("0.80")


def test_prc_gives_the_realised_rate_not_the_mid_market_rate():
    """
    Realised rate ko mid-market samajh lena spread ko 0 bana deta hai — is liye
    `rates_from_prc` ka maqsad sirf tax ka adad hai, laagat ka tajziya nahi.
    """
    entry = PrcEntry(date(2026, 4, 8), Decimal("3000.00"), Decimal("834000.00"))
    assert entry.realised_rate == Decimal("278.0000")

    source = rates_from_prc([entry])
    assert source.rate_on(date(2026, 4, 8)).value == Decimal("278.0000")
    assert source.rate_on(date(2026, 4, 8)).is_authoritative is True
