"""
Project chatbot — retrieval, na ke koi train kiya hua model.

Is domain mein sab se bura natija ek **pur-yaqeen ghalat jawab** hai: koi tax ka
adad poochta hai, bot yaad-dasht se bana deta hai, aur wo shakhs us pe file kar
deta hai. Is liye ye tests zyadatar do baaton ke hain — adad hamesha live
engine se aaye, aur jo maloom na ho us pe bot mana kar de.
"""
import re

import pytest

from chat.engine import ENTRIES, ask, coverage, tokenise
from chat.knowledge import KnowledgeEntry


# --- the safety properties --------------------------------------------------


def test_every_entry_cites_a_source():
    """Bina hawale ke jawab is poore project ke usool ke khilaf hai."""
    for entry in ENTRIES:
        assert entry.sources, entry.id


def test_no_current_tax_rate_is_written_into_a_static_answer():
    """
    Sab se ahem test — magar theek jagah pe.

    Pehli koshish poori file mein "0.25%" dhoondti thi, aur wo ghalat thi: log
    literally isi tarah poochte hain ("Why am I not getting 0.25%?"), to sawal
    ke andar adad hona bilkul theek hai. Jo cheez ghalat hai wo ye ke **jawab**
    mein rate likha ho — kyunke Finance Act badalne pe wohi jagah purani reh
    jayegi jahan se user parh raha hai.
    """
    from core.taxrules import rule_for

    rule = rule_for()
    live_rates = {
        f"{rule.pseb_registered_rate * 100:.2f}%",
        f"{rule.unregistered_export_rate * 100:.2f}%",
        f"{rule.banking_channel_minimum * 100:.0f}%",
    }
    for entry in ENTRIES:
        if entry.compute is not None:
            continue  # computed answers pull from the engine by definition
        for literal in live_rates:
            assert literal not in entry.answer, (
                f"{entry.id} states {literal} in its answer — it must come from "
                "core/taxrules.py instead"
            )


def test_tax_answers_come_from_the_live_engine():
    from core.taxrules import rule_for

    rule = rule_for()
    answer = ask("what tax rate do I pay?").text
    # Wahi adad jo engine deta hai, usi shakal mein.
    assert f"{rule.pseb_registered_rate * 100:.2f}%" in answer
    assert f"{rule.unregistered_export_rate * 100:.2f}%" in answer
    assert rule.source_url in answer


def test_changing_the_rule_changes_the_answer(monkeypatch):
    """
    Live hone ka asal imtihan: rule badlo, jawab khud badal jana chahiye. Agar
    adad likha hua hota to ye test fail hota.
    """
    from decimal import Decimal

    import core.taxrules as taxrules

    original = taxrules.RULES[taxrules.LATEST_TAX_YEAR]
    changed = taxrules.TaxRule(
        tax_year=original.tax_year,
        pseb_registered_rate=Decimal("0.005000"),  # 0.50%
        unregistered_export_rate=original.unregistered_export_rate,
        banking_channel_minimum=original.banking_channel_minimum,
        is_final_tax=original.is_final_tax,
        top_slab_rate=original.top_slab_rate,
        source_url=original.source_url,
        verified_on=original.verified_on,
        notes=original.notes,
    )
    monkeypatch.setitem(taxrules.RULES, taxrules.LATEST_TAX_YEAR, changed)

    assert "0.50%" in ask("what tax rate do I pay?").text


def test_an_off_topic_question_is_refused_not_answered():
    for question in ["what is the capital of France", "how do I cook biryani",
                     "who won the cricket match"]:
        answer = ask(question)
        assert answer.is_fallback is True, question
        assert "don't have a grounded answer" in answer.text


def test_a_refusal_still_offers_a_way_forward():
    answer = ask("what is the capital of France")
    assert answer.suggestions
    assert all(isinstance(s, str) and s for s in answer.suggestions)


def test_the_bot_is_honest_about_what_it_is():
    answer = ask("are you an AI?")
    assert answer.confident
    assert "No model was trained" in answer.text


# --- retrieval quality ------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("what tax rate do I pay?", "tax-rates"),
        ("is pseb registration worth it", "pseb-worth-it"),
        ("why is my payoneer fee higher than advertised", "hidden-spread"),
        ("do you store my data", "data-stored"),
        ("do you need my bank login", "need-credentials"),
        ("why does it say not ready to file", "never-ready"),
        ("my import failed", "import-failed"),
        ("what is a PRC", "what-is-prc"),
        ("why do I need a PRC", "what-is-prc"),
        ("what goes in prc.csv", "prc-file"),
        ("why Decimal and not float", "no-float"),
        ("what are the limitations", "limitations"),
        ("how do I run this", "how-to-run"),
        ("why is there no login", "why-no-accounts"),
        ("what is the 80% rule", "banking-channel"),
        ("why are my dates wrong", "date-format"),
        ("does it move my money", "handles-money"),
        ("what do I give my accountant", "filing-pack"),
    ],
)
def test_known_questions_route_to_the_right_entry(question, expected):
    answer = ask(question)
    assert answer.entry_id == expected, f"{question!r} went to {answer.entry_id}"


def test_every_listed_phrasing_finds_its_own_entry():
    """
    Har entry apni hi phrasings pe jeetni chahiye. Ye pehle nahi hota tha:
    "what is a PRC" CSV-format wali entry pe chala jata tha, kyunke chhote
    sawal base score ko saturate kar dete the aur faisla alphabetical id pe
    ho jata tha.
    """
    wrong = []
    for entry in ENTRIES:
        for phrasing in entry.questions:
            got = ask(phrasing)
            if got.entry_id != entry.id:
                wrong.append((phrasing, entry.id, got.entry_id))
    assert not wrong, f"{len(wrong)} phrasing(s) routed elsewhere: {wrong[:5]}"


def test_the_same_question_always_gives_the_same_answer():
    """Deterministic — koi randomness nahi."""
    first = ask("what tax rate do I pay?")
    for _ in range(5):
        again = ask("what tax rate do I pay?")
        assert again.entry_id == first.entry_id
        assert again.text == first.text


def test_an_empty_question_asks_for_one():
    answer = ask("")
    assert answer.is_fallback
    assert answer.suggestions


# --- the corpus itself ------------------------------------------------------


def test_the_corpus_is_substantial_and_broad():
    stats = coverage()
    assert stats["entries"] >= 40
    assert stats["question_phrasings"] >= 150
    assert len(stats["topics"]) >= 8
    assert stats["entries_with_sources"] == stats["entries"]


def test_entry_ids_are_unique():
    ids = [e.id for e in ENTRIES]
    assert len(ids) == len(set(ids))


def test_no_entry_is_empty():
    for entry in ENTRIES:
        assert entry.questions, entry.id
        assert entry.render().strip(), entry.id


def test_tokenise_drops_noise_words():
    assert tokenise("what is the tax rate?") == ["tax", "rate"]
