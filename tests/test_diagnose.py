"""
File diagnosis — wo feature jo is waqt ki sab se bari khaami ka jawab hai.

Parsers documented formats pe bane hain, asli files pe nahi. To jab koi asli
file match na kare, tool ko "Import failed" keh kar chup nahi hona chahiye —
use batana chahiye ke file mein kya hai, aur user khud mapping de sake.
"""
from core.diagnose import diagnose

WISE = (
    '"ID","Status","Direction","Created on","Finished on","Source fee amount",'
    '"Source fee currency","Source name","Source amount (after fees)","Source currency",'
    '"Target name","Target amount (after fees)","Target currency","Exchange rate",'
    '"Reference","Batch","Created by"\n'
    '"1","COMPLETED","IN","01-04-2026","03-04-2026","13.50","USD","Acme","2986.50","USD",'
    '"Ali","832000.00","PKR","278.6","Inv 41","","Acme"\n'
)

PAYONEER = (
    "Transaction Date,Description,Amount,Currency,Source,Target,Reference ID\n"
    "03-04-2026,Payment from Acme,3000.00,USD,Acme,Balance,REF-1\n"
)

# Kisi Pakistani bank ka statement — na Wise na Payoneer.
UNKNOWN_BANK = (
    "Posting Date;Particulars;Debit;Credit;Balance\n"
    "03-04-2026;INWARD REMITTANCE ACME INC;;834000.00;1250000.00\n"
)


def test_a_wise_file_is_recognised():
    d = diagnose(WISE)
    assert d.looks_like == "Wise"
    assert d.usable is True
    assert d.row_count == 1


def test_a_payoneer_file_is_recognised():
    d = diagnose(PAYONEER)
    assert d.looks_like == "Payoneer"
    assert d.guessed["date"] == "Transaction Date"
    assert d.guessed["amount"] == "Amount"


def test_an_unknown_semicolon_statement_is_still_understood():
    """
    Ye asal maqsad hai: format maloom na ho to bhi tool kaam ka jawab de.
    Semicolon delimiter bhi pehchana jaye.
    """
    d = diagnose(UNKNOWN_BANK)
    assert d.delimiter == ";"
    assert d.looks_like is None
    assert d.guessed["date"] == "Posting Date"
    assert d.guessed["amount"] == "Credit"
    assert d.guessed["description"] == "Particulars"
    assert d.usable is True


def test_a_usable_diagnosis_produces_a_working_column_map():
    """Diagnosis ka natija seedha importer mein daala ja sakta hai."""
    from core.importers.generic import parse

    mapping = diagnose(UNKNOWN_BANK).suggested_map()
    assert mapping is not None
    result = parse(UNKNOWN_BANK.replace(";", ","), mapping, currency="PKR")
    assert result.credits[0].gross_usd.quantize(__import__("decimal").Decimal("0.01")) \
        == __import__("decimal").Decimal("834000.00")


def test_an_unrecognisable_file_says_what_is_missing_instead_of_failing_silently():
    d = diagnose("Foo,Bar,Baz\n1,2,3\n")
    assert d.usable is False
    assert "date" in d.missing and "amount" in d.missing
    text = d.explain()
    assert "Could not identify" in text
    assert "no code change needed" in text


def test_explain_shows_the_columns_and_a_sample_row():
    text = diagnose(PAYONEER).explain()
    assert "Transaction Date" in text
    assert "First data row:" in text
    assert "REF-1" in text


def test_an_empty_file_does_not_crash():
    d = diagnose("")
    assert d.columns == []
    assert d.usable is False
