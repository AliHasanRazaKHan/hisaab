"""
Web tool — aur us ka sab se ahem dawa: **kuch store nahi hota**.

Baqi tests un soorat ke liye hain jahan user ki file ya input adhoora hai, aur
tool ko us ko samjhana hai — chup ho kar 500 dena sab se bura natija hai.
"""
import io

import pytest
from fastapi.testclient import TestClient

from web.app import app

client = TestClient(app)

WISE_CSV = (
    '"ID","Status","Direction","Created on","Finished on","Source fee amount",'
    '"Source fee currency","Source name","Source amount (after fees)","Source currency",'
    '"Target name","Target amount (after fees)","Target currency","Exchange rate",'
    '"Reference","Batch","Created by"\n'
    '"1","COMPLETED","IN","01-04-2026","03-04-2026","13.50","USD","Acme","2986.50","USD",'
    '"Ali","832000.00","PKR","278.6","Inv 41","","Acme"\n'
)

PAYONEER_CSV = (
    "Transaction Date,Description,Amount,Currency,Source,Target,Reference ID\n"
    "03-04-2026,Payment from Acme,3000.00,USD,Acme,Balance,REF-1\n"
)


def _upload(content: str, **form):
    return client.post(
        "/report",
        files={"statement": ("statement.csv", io.BytesIO(content.encode()), "text/csv")},
        data=form,
    )


def test_the_page_states_that_nothing_is_stored():
    """Ye dawa product ka hissa hai, sirf ehtiyat nahi."""
    body = client.get("/").text
    assert "Nothing is stored" in body
    assert "cannot be leaked" in body


def test_the_page_carries_the_tax_disclaimer():
    assert "not tax advice" in client.get("/").text


def test_a_wise_statement_produces_a_report():
    response = _upload(WISE_CSV, statement_format="wise", rates="03-04-2026, 279.50")
    assert response.status_code == 200
    assert "WHAT THE RAILS TOOK" in response.text
    assert "EXPORT TAX" in response.text


def test_pseb_checkbox_changes_the_rate_applied():
    plain = _upload(WISE_CSV, statement_format="wise", rates="03-04-2026, 279.50").text
    registered = _upload(
        WISE_CSV, statement_format="wise", rates="03-04-2026, 279.50", pseb="1"
    ).text
    assert "no PSEB registration" in plain
    assert "PSEB-registered" in registered


def test_payoneer_without_prc_lines_is_explained_not_rejected_silently():
    """
    Payoneer ka statement PKR bata hi nahi sakta — user ko wajah samajhni
    chahiye, warna wo samjhega tool toota hua hai.
    """
    response = _upload(PAYONEER_CSV, statement_format="payoneer")
    assert response.status_code == 200
    assert "ePRC lines are needed" in response.text
    assert "cannot show what your bank credited" in response.text


def test_payoneer_with_prc_lines_works():
    response = _upload(
        PAYONEER_CSV,
        statement_format="payoneer",
        prc="08-04-2026, 3000.00, 834000.00",
    )
    assert "EXPORT TAX" in response.text
    # PRC ka realised rate mid-market nahi — laagat dikhani hi nahi chahiye.
    assert "not calculable" in response.text


def test_an_unreadable_file_shows_its_columns_instead_of_an_error_page():
    response = _upload("Foo,Bar\n1,2\n", statement_format="wise")
    assert response.status_code == 200
    assert "could not read that file" in response.text
    assert "Foo" in response.text  # diagnosis dikh rahi hai


def test_auto_format_works_out_an_unknown_bank_statement():
    unknown = (
        "Posting Date,Particulars,Credit,Currency\n"
        "03-04-2026,INWARD REMITTANCE ACME,3000.00,USD\n"
    )
    response = _upload(
        unknown, statement_format="auto", prc="08-04-2026, 3000.00, 834000.00"
    )
    assert "EXPORT TAX" in response.text


def test_an_empty_file_is_handled():
    assert "empty" in _upload("   ", statement_format="wise").text


def test_an_oversized_upload_is_refused_before_parsing():
    """Bina had ke koi bhi memory khatam kar sakta hai."""
    from web.app import MAX_UPLOAD_BYTES

    big = "x" * (MAX_UPLOAD_BYTES + 10)
    assert "larger than" in _upload(big, statement_format="wise").text


def test_html_in_a_statement_cannot_inject_markup():
    """User ka data page pe chhapta hai — escape zaroori hai."""
    nasty = PAYONEER_CSV.replace("Payment from Acme", "<script>alert(1)</script>")
    response = _upload(nasty, statement_format="payoneer")
    assert "<script>alert(1)</script>" not in response.text


def test_no_database_or_storage_is_configured():
    """
    Ye test us dawe ko pakka karta hai jo page pe likha hai. Koi DB ya file
    write is module mein hona hi nahi chahiye.
    """
    source = (__import__("pathlib").Path("web/app.py")).read_text()
    for forbidden in ("sqlite", "create_engine", "SessionLocal", "open(", ".write("):
        assert forbidden not in source, forbidden
