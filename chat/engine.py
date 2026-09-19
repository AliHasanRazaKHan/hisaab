"""
Project ke bare mein sawal-jawab — **bina kuch farz kiye**.

Pehle ye saaf kar dein ke ye kya *nahi* hai: ye koi train kiya hua model nahi.
Is domain mein wo ghalat aur khatarnak dono hota:

- Tax ke adad jhoot nahi ho sakte. Agar koi poochay "mera rate kya hai?" aur
  bot yaad-dasht se adad bana de, to shakhs us pe file kar dega. Is liye har
  numeric jawab **`core/taxrules.py` se live nikalta hai** — wahi code jo report
  chalata hai. Rate badle to jawab khud badal jata hai; koi alag copy nahi jo
  purani ho jaye.
- Jo maloom nahi wo "maloom nahi" hai. Andaza lagane wala jawab is poore
  project ke usool ke khilaf hai.

To ye retrieval hai: ek curated knowledge base (chat/knowledge.py), jis ki har
entry ka hawala mojood hai, aur scoring deterministic hai — wahi sawal hamesha
wahi jawab deta hai. Koi API key nahi chahiye; offline chalta hai.

Match na ho to bot saaf kehta hai ke us ke paas jawab nahi, aur qareeb tareen
mauzu tajweez karta hai.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from chat.knowledge import ENTRIES, KnowledgeEntry

# Ye alfaz har sawal mein aate hain, is liye in se match karna shor hai.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "do", "does", "did", "can", "could",
    "should", "would", "will", "what", "why", "how", "when", "where", "which", "who",
    "this", "that", "these", "those", "it", "its", "i", "my", "me", "you", "your",
    "to", "of", "in", "on", "for", "with", "and", "or", "but", "if", "so", "about",
    "from", "by", "at", "as", "be", "been", "have", "has", "had", "not", "no",
    "there", "here", "any", "all", "some", "then", "than", "too", "very", "just",
    "tell", "explain", "show", "give", "want", "need", "know", "please",
}

# Is se kam score pe jawab dena andaza lagana hai.
MIN_SCORE = 0.22


def tokenise(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9%.]+", (text or "").lower())
    return [w.strip(".") for w in words if w.strip(".") and w not in STOPWORDS]


@dataclass
class Answer:
    text: str
    entry_id: str | None = None
    topic: str | None = None
    sources: list[str] = field(default_factory=list)
    score: float = 0.0
    # True ka matlab: jawab nahi mila, ye sirf rehnumai hai.
    is_fallback: bool = False
    suggestions: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return not self.is_fallback


def _normalise_phrase(text: str) -> str:
    """Phrase muqable ke liye — stopwords hatae bagair, sirf safai."""
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())


def _dice(a: set[str], b: set[str]) -> float:
    """
    Dice similarity — overlap ko dono taraf ki lambai se naapta hai.

    Sirf "kitne alfaz mile" ginna kaafi nahi tha: us se score saturate ho jata
    tha aur kai entries barabar ho kar faisla alphabetical id pe chhod deti
    thin. Dice un entries ko saza deta hai jin ke keywords bohot se hain magar
    mutabiq kam.
    """
    if not a or not b:
        return 0.0
    return (2 * len(a & b)) / (len(a) + len(b))


def _score(query_tokens: list[str], entry: KnowledgeEntry, raw_query: str = "") -> float:
    """Deterministic score — wahi sawal hamesha wahi jawab de."""
    query = set(query_tokens)
    # Poora sawal stopwords ka bhi ho sakta hai ("Who is this for?") — us soorat
    # mein token overlap 0 hota hai aur sirf phrase match hi bacha sakta hai.
    # Pehle aise sawal seedha "maloom nahi" pe chale jate the, jab ke wo
    # corpus mein lafz-ba-lafz mojood the.
    score = 0.0
    if query:
        # Keywords haath se chune gaye hain, is liye un ka wazan zyada.
        score = 0.62 * _dice(query, entry.keyword_set) + 0.38 * _dice(
            query, entry.question_token_set
        )

    # Poore sawal ka match sab se mazboot signal hai.
    #
    # Muqabla **asli sawal** se hota hai, stopwords hatae hue tokens se nahi.
    # Pehle tokens se hota tha, aur us ka matlab ye tha ke jis sawal mein
    # stopwords hon (yaani tqreeban har sawal) us ka phrase kabhi match hi
    # nahi karta tha.
    haystack = _normalise_phrase(raw_query)
    if haystack.strip():
        for question in entry.questions:
            needle = _normalise_phrase(question).strip()
            if needle and needle in haystack:
                score += 0.55
                break

    return round(min(score, 1.0), 4)


def ask(question: str, *, limit_suggestions: int = 3) -> Answer:
    tokens = tokenise(question)
    if not tokens and not (question or "").strip():
        return Answer(
            text="Ask me something about how Hisaab works, what it costs you, or what it "
                 "refuses to do.",
            is_fallback=True,
            suggestions=[e.questions[0] for e in ENTRIES[:limit_suggestions]],
        )

    ranked = sorted(
        ((_score(tokens, entry, question), entry) for entry in ENTRIES),
        key=lambda pair: (-pair[0], pair[1].id),
    )
    best_score, best = ranked[0]

    if best_score < MIN_SCORE:
        # Andaza lagane se behtar hai mana karna — aur raasta dikhana.
        return Answer(
            text=(
                "I don't have a grounded answer for that. Everything I say is tied to this "
                "project's code or documentation, and I'd rather say nothing than invent "
                "something — particularly about tax."
            ),
            is_fallback=True,
            score=best_score,
            suggestions=[entry.questions[0] for _, entry in ranked[:limit_suggestions]],
        )

    return Answer(
        text=best.render(),
        entry_id=best.id,
        topic=best.topic,
        sources=list(best.sources),
        score=best_score,
        suggestions=[entry.questions[0] for _, entry in ranked[1 : limit_suggestions + 1]],
    )


def topics() -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in ENTRIES:
        counts[entry.topic] = counts.get(entry.topic, 0) + 1
    return dict(sorted(counts.items()))


def coverage() -> dict:
    """Knowledge base ki soorat-e-haal — dawa karne se pehle ginti."""
    return {
        "entries": len(ENTRIES),
        "question_phrasings": sum(len(e.questions) for e in ENTRIES),
        "topics": topics(),
        "entries_with_sources": sum(1 for e in ENTRIES if e.sources),
        "computed_answers": sum(1 for e in ENTRIES if e.compute),
    }
