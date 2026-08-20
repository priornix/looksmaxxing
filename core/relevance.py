"""Stage A2: relevance gate. Sits between 'the source resolves' and 'the source entails'.

Found by running the pipeline: EuropePMC returns real, resolvable papers that are
topically unrelated to the query. Existence checks pass them. Entailment is expensive
and the LLM is agreeable, so unrelated sources leak into the ledger as citations.
"""
from __future__ import annotations
import re

STOP = {"the","a","an","of","in","on","and","or","to","for","with","is","are","does",
        "do","can","your","you","adult","adults","change","changes","effect","effects"}


def _terms(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def relevance(claim_text: str, entities: list[str], title: str, abstract: str) -> tuple[float, bool, str]:
    """Deterministic. Requires the claim's own vocabulary to appear in the source."""
    hay = f"{title} {abstract}".lower()
    ents = [e.lower() for e in entities if e]
    # Anchor entities are non-negotiable: at least one must appear verbatim.
    anchor_hits = [e for e in ents if e in hay]
    ct = _terms(claim_text)
    overlap = ct & _terms(f"{title} {abstract}")
    score = len(overlap) / max(len(ct), 1)
    # Anchors are SUFFICIENT, not merely necessary. Domain vocabulary diverges
    # ("maxilla" vs "midpalatal"), so a lexical floor applied conjunctively drops
    # the most probative papers. Anchor hit -> hand to entailment, let stage B judge.
    if anchor_hits:
        return score, True, f"anchor {anchor_hits} (overlap {score:.2f})"
    if score >= 0.12:
        return score, True, f"no anchor but overlap {score:.2f} >= 0.12"
    return score, False, f"no anchor entity {ents}; overlap {score:.2f} < 0.12"


DESIGN_MARKERS = [
    (r"\b(systematic review|meta-?analys)", 5),
    (r"\b(randomi[sz]ed|randomised controlled|double-?blind|placebo-?controlled)", 4),
    (r"\b(cohort|longitudinal|cross-?sectional|case-?control|retrospective)", 3),
    (r"\b(in vitro|animal model|cadaver|finite element|biomechanical)", 2),
    (r"\b(case report|case series|survey|thematic analysis|qualitative)", 1),
]


def design_tier_from_text(title: str, abstract: str) -> int:
    """Secondary deterministic signal. 'Journal Article' alone tells you nothing."""
    hay = f"{title} {abstract}".lower()
    best = 0
    for rx, t in DESIGN_MARKERS:
        if re.search(rx, hay) and t > best:
            best = t
    return best
