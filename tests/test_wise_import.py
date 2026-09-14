"""
Wise CSV import — aur teen soorat jo naive parser ko todti hain.

**Honesty note:** ye tests documented column format pe bane hain (Wise ke help
docs, 2026-09-15), asli exported file pe nahi. Pichle project mein har feed bug
asli data se mila tha, docs se nahi — is liye jab asli statement mile to inhe
dobara jaanchna hai.
"""
from datetime import date
from decimal import Decimal

import pytest

from core.importers.wise import ImportProblem, parse, parse_date

HEADER = (
    '"ID","Status","Direction","Created on","Finished on","Source fee amount",'
    '"Source fee currency","Source name","Source amount (after fees)","Source currency",'
    '"Target name","Target amount (after fees)","Target currency","Exchange rate",'
    '"Reference","Batch","Created by"'
)


def _row(**over) -> str:
    f = {
        "id": "1234567",
        "status": "COMPLETED",
        "direction": "IN",
        "created": "01-04-2026",
        "finished": "03-04-2026",
        "fee": "13.50",
        "fee_ccy": "USD",
        "source_name": "Acme Inc",
        "source_amt": "2986.50",
        "source_ccy": "USD",
        "target_name": "Ali",
        "target_amt": "832000.00",
        "target_ccy": "PKR",
        "rate": "278.6",
        "reference": "Invoice 41",
        "batch": "",
        "created_by": "Acme Inc",
    }
    f.update(over)
    return (
        f'"{f["id"]}","{f["status"]}","{f["direction"]}","{f["created"]}","{f["finished"]}",'
        f'"{f["fee"]}","{f["fee_ccy"]}","{f["source_name"]}","{f["source_amt"]}","{f["source_ccy"]}",'
        f'"{f["target_name"]}","{f["target_amt"]}","{f["target_ccy"]}","{f["rate"]}",'
        f'"{f["reference"]}","{f["batch"]}","{f["created_by"]}"'
    )


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


# --- gotcha 1: DD-MM-YYYY, not ISO -----------------------------------------


def test_dates_are_day_first_like_wise_writes_them():
    """
    `03-04-2026` 3 April hai, 4 March nahi. Ghalat parse ka matlab ghalat tax
    year, aur tax year pe poora hisaab khara hai.
    """
    assert parse_date("03-04-2026") == date(2026, 4, 3)
    assert parse_date("03-04-2026 14:22:01") == date(2026, 4, 3)
    # ISO bhi qubool (kuch exports wo dete hain).
    assert parse_date("2026-04-03") == date(2026, 4, 3)


def test_an_unrecognised_date_is_refused_not_guessed():
    with pytest.raises(ValueError):
        parse_date("April 3, 2026")


# --- gotcha 2: the rail's rate is not the mid-market rate -------------------


def test_the_rails_own_rate_is_kept_separate_and_never_used_as_mid_market():
    """
    Wise ka "Exchange rate" us ka diya hua rate hai. Us se spread naapna apne
    aap ko apne hi rate se naapna hai — hamesha 0 aayega.
    """
    result = parse(_csv(_row()))
    transfer = result.transfers[0]
    assert transfer.rail_rate == Decimal("278.6000")
    # Aur mid-market ka koi field yahan mojood hi nahi — wo bahar se aata hai.
    assert not hasattr(transfer, "mid_market_rate")


# --- gotcha 3: fees can arrive as their own row -----------------------------


def test_a_separate_fee_row_is_added_not_ignored():
    """
    "Wise charges for transfer X" alag row mein aati hai. Use chhor dena laagat
    kam dikhata hai; dobara ginna zyada. Dono ghalat.
    """
    fee_row = _row(
        id="9999", fee="0.00", source_amt="-8.25", reference="Wise charges for transfer 1234567"
    )
    result = parse(_csv(_row(), fee_row))

    assert result.fee_rows_applied == 1
    transfer = result.transfers[0]
    # 13.50 (column) + 8.25 (alag row)
    assert transfer.declared_fee_usd == Decimal("21.75")


def test_a_fee_row_for_an_unknown_transfer_is_reported_not_silently_dropped():
    fee_row = _row(id="9999", reference="Wise charges for transfer 000000")
    result = parse(_csv(fee_row))
    assert any("unknown transfer" in s for s in result.skipped)


# --- gross must include the fee --------------------------------------------


def test_gross_adds_the_fee_back_because_wise_reports_net_of_fees():
    """
    Column ka naam hi "Source amount (after fees)" hai. Usi ko gross maan lena
    laagat ko kam dikha deta hai — client ne 3000 bheje the, 2986.50 nahi.
    """
    transfer = parse(_csv(_row())).transfers[0]
    assert transfer.gross_usd == Decimal("3000.00")
    assert transfer.net_pkr == Decimal("832000.00")


# --- what gets skipped, and loudly -----------------------------------------


def test_outgoing_and_unfinished_and_wrong_currency_rows_are_skipped_with_reasons():
    rows = [
        _row(),
        _row(id="2", direction="OUT"),
        _row(id="3", status="CANCELLED"),
        _row(id="4", target_ccy="EUR"),
    ]
    result = parse(_csv(*rows))

    assert len(result.transfers) == 1
    assert len(result.skipped) == 3
    # Chupke se girana ghalat adad se bhi bura hai — user ko pata nahi chalta.
    assert all(":" in reason for reason in result.skipped)


def test_thousands_separators_are_handled():
    transfer = parse(_csv(_row(target_amt="1,832,000.00"))).transfers[0]
    assert transfer.net_pkr == Decimal("1832000.00")


def test_a_bad_row_does_not_sink_the_file():
    result = parse(_csv(_row(), _row(id="2", target_amt="not-a-number"), _row(id="3")))
    assert len(result.transfers) == 2
    assert any("not a number" in s for s in result.skipped)


def test_totals_are_decimal_sums():
    result = parse(_csv(_row(), _row(id="2")))
    assert result.total_gross_usd == Decimal("6000.00")
    assert result.total_net_pkr == Decimal("1664000.00")


# --- refusing files that aren't Wise ---------------------------------------


def test_a_non_wise_csv_is_refused_with_a_useful_message():
    with pytest.raises(ImportProblem) as exc:
        parse("Date,Amount,Balance\n01-04-2026,100,200")
    assert "does not look like a Wise" in str(exc.value)


def test_an_empty_file_is_refused():
    with pytest.raises(ImportProblem):
        parse("")


def test_a_utf8_bom_does_not_break_the_header():
    result = parse(("﻿" + _csv(_row())).encode("utf-8"))
    assert len(result.transfers) == 1
