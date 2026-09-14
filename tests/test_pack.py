"""
Filing record pack — aur us ki sab se ahem shart: checklist jhoot na bole.

Aisi checklist jo hamesha hari dikhe kisi kaam ki nahi — user us pe bharosa kar
ke ghalat filing kar dega. Is liye ye tests zyadatar un soorat ke hain jahan
checklist ko **mana** karna chahiye.
"""
from datetime import date
from decimal import Decimal

from core.fx import ManualRate
from core.importers.wise import ImportedTransfer
from core.money import money, rate
from core.pack import build_pack, register_csv, render_pack
from core.report import build


def _transfer(day, tid="t1", gross="3000.00", net="819000.00") -> ImportedTransfer:
    return ImportedTransfer(
        external_id=tid,
        received_on=day,
        gross_usd=money(gross),
        net_pkr=money(net),
        declared_fee_usd=money("30.00"),
        rail_rate=rate("273.00"),
        payer="Acme Inc",
    )


def _prc_rates(*days) -> ManualRate:
    source = ManualRate()
    for day in days:
        source.from_prc(day, "279.50")
    return source


def _pack(**kwargs):
    d1 = date(2026, 4, 3)
    transfers = kwargs.pop("transfers", [_transfer(d1)])
    rates = kwargs.pop("rates", _prc_rates(d1))
    return build_pack(build(transfers, rates, **kwargs))


# --- the checklist must be able to say no -----------------------------------


def test_the_rate_verification_item_is_never_ticked():
    """
    Hum ne rates ek published guide se liye hain, Finance Act se nahi. Is item
    ko hara dikhana us jhoot ki buniyad banega jis pe koi file kar dega.
    """
    pack = _pack(pseb_registered=True)
    item = next(i for i in pack.checklist if "verified against FBR" in i.label)
    assert item.ok is False
    assert item.blocking is True
    assert "not the Finance Act" in item.detail
    # Aur isi wajah se pack kabhi khud ko "ready" nahi kehta.
    assert pack.ready_to_file is False


def test_a_missing_eprc_blocks_filing_and_says_why():
    pack = _pack(excluded_earlier=["2026-06-01 $1800.00 — no matching ePRC"])
    item = next(i for i in pack.checklist if "ePRC from the bank" in i.label)
    assert item.ok is False
    assert item.blocking is True
    assert "understated" in item.detail
    assert pack.unproven


def test_hand_entered_rates_block_filing():
    """ePRC ka adad hi filing ki buniyad hai — haath se likha rate us ka badal nahi."""
    d1 = date(2026, 4, 3)
    hand = ManualRate()
    hand.set(d1, "279.50")
    pack = _pack(rates=hand)
    item = next(i for i in pack.checklist if "come from the ePRC" in i.label)
    assert item.ok is False
    assert item.blocking is True


def test_prc_backed_figures_pass_that_item():
    pack = _pack()
    item = next(i for i in pack.checklist if "come from the ePRC" in i.label)
    assert item.ok is True
    assert pack.register[0]["prc_backed"] == "yes"


def test_the_banking_channel_item_reflects_a_withheld_rate():
    pack = _pack(pseb_registered=True, banking_channel_fraction=Decimal("0.60"))
    item = next(i for i in pack.checklist if "formal banking channels" in i.label)
    assert item.ok is False
    assert "1% rate is used instead" in item.detail


def test_unregistered_users_are_told_what_registering_would_save():
    pack = _pack(pseb_registered=False)
    item = next(i for i in pack.checklist if i.label == "PSEB registration")
    assert item.ok is False
    assert "difference of PKR" in item.detail


# --- the register -----------------------------------------------------------


def test_the_register_lists_only_proven_remittances():
    """
    Sabit aur ghair-sabit ko ek list mein milana filer se wo farq chhupata hai
    jo us ke liye sab se ahem hai.
    """
    d1 = date(2026, 4, 3)
    pack = _pack(
        transfers=[_transfer(d1)],
        excluded_earlier=["2026-06-01 $1800.00 — no matching ePRC"],
    )
    assert len(pack.register) == 1
    assert len(pack.unproven) == 1


def test_register_csv_has_a_stable_header_and_one_row_per_remittance():
    d1, d2 = date(2026, 4, 3), date(2026, 5, 3)
    pack = _pack(
        transfers=[_transfer(d1, "a"), _transfer(d2, "b")], rates=_prc_rates(d1, d2)
    )
    text = register_csv(pack)
    header, *rows = text.strip().split("\n")
    assert header == (
        "date_credited,payer,gross_usd,pkr_credited,reference,rate_source,prc_backed"
    )
    assert len(rows) == 2
    assert "Acme Inc" in rows[0]


def test_amounts_in_the_csv_are_exact_strings_not_floats():
    """CSV filer ke paas jata hai — float ka kachra wahan nahi jana chahiye."""
    pack = _pack()
    assert ",819000.00," in register_csv(pack)
    assert "819000.0000" not in register_csv(pack)


# --- the rendered pack ------------------------------------------------------


def test_render_states_not_ready_when_something_blocks():
    text = render_pack(_pack())
    assert "NOT READY TO FILE" in text
    assert "[!]" in text


def test_render_shows_totals_and_the_basis():
    text = render_pack(_pack(pseb_registered=True))
    assert "PKR credited" in text
    assert "PSEB-registered" in text
    assert "Estimated tax" in text


def test_render_always_carries_the_practitioner_warning():
    text = render_pack(_pack())
    assert "not tax advice" in text
    assert "tax practitioner review" in text


def test_an_empty_pack_does_not_claim_readiness():
    pack = build_pack(build([], ManualRate()))
    assert pack.ready_to_file is False
    assert "NOT READY TO FILE" in render_pack(pack)


def test_the_banking_channel_item_does_not_contradict_itself():
    """
    Apne hi output mein pakra gaya: item `[x]` dikhti thi aur us ke neeche
    likha hota "Below the threshold" — checklist khud apni baat ka ulta keh
    rahi thi.

    Jo shakhs PSEB pe registered hi nahi, us pe 80% wali shart lagti hi nahi.
    Use "pass" dikhana ghalat samt mein bhejta hai (wo banking channel theek
    karne lagta, jab ke asal masla registration hai).
    """
    pack = _pack(pseb_registered=False)
    item = next(i for i in pack.checklist if "formal banking channels" in i.label)

    assert item.not_applicable is True
    assert item.mark == "[-]"
    assert "Only relevant once you are PSEB-registered" in item.detail
    # Aur na "pass" na "fail" dikhna chahiye.
    assert "[x]" not in f"{item.mark} {item.label}"


def test_a_registered_user_below_the_threshold_sees_a_real_failure():
    pack = _pack(pseb_registered=True, banking_channel_fraction=Decimal("0.60"))
    item = next(i for i in pack.checklist if "formal banking channels" in i.label)
    assert item.not_applicable is False
    assert item.ok is False
    assert "Below the threshold" in item.detail


def test_a_registered_user_above_the_threshold_passes_it():
    pack = _pack(pseb_registered=True)
    item = next(i for i in pack.checklist if "formal banking channels" in i.label)
    assert item.ok is True
    assert item.mark == "[x]"


def test_not_applicable_items_never_block_filing():
    """Jo shart lagti hi nahi wo filing nahi rok sakti."""
    pack = _pack(pseb_registered=False)
    blockers = [i for i in pack.checklist if i.blocking and not i.ok and not i.not_applicable]
    assert all("formal banking channels" not in i.label for i in blockers)
