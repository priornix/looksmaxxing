"""GEO layer: make the ledger extractable and citable by retrieval systems.

SEO rewards depth and crawlability. GEO rewards atomic, self-contained, attributed
assertions that survive chunking. The claim ledger is already that shape - this module
stops it going to waste, and scores the prose on whether a lifted chunk still stands alone.
"""
from __future__ import annotations
import re, json, datetime
from core.ledger import Claim, Verdict, EvidenceTier

SENT = re.compile(r"(?<=[.!?])\s+")
CITE = re.compile(r"\[\[([a-z0-9_\-]+)\]\]", re.I)
# A chunk starting with a bare pronoun loses its subject the moment it is lifted.
ORPHAN = re.compile(r"^\s*(it|this|that|they|these|those|he|she|such|both|either)\b", re.I)
DEICTIC = re.compile(r"\b(above|below|as mentioned|the former|the latter|earlier|previously)\b", re.I)


def _sentences(prose: str) -> list[str]:
    out = []
    for block in prose.split("\n"):
        b = block.strip()
        if not b or b.startswith("#"):
            continue
        out += [s.strip() for s in SENT.split(b) if len(s.split()) >= 4]
    return out


def extractability(prose: str, ledger: dict[str, Claim]) -> tuple[float, list[dict]]:
    """Would each sentence still mean something on its own, pulled out of the page?"""
    sents = _sentences(prose)
    findings, good = [], 0
    entities = {e.lower() for c in ledger.values() for e in c.entities}
    for i, s in enumerate(sents):
        problems = []
        if ORPHAN.match(s):
            problems.append("orphan_pronoun")
        if DEICTIC.search(s):
            problems.append("deictic_reference")
        named = any(e in s.lower() for e in entities) if entities else True
        if not named:
            problems.append("no_entity_named")
        if problems:
            findings.append({"i": i, "sentence": s[:80], "problems": problems})
        else:
            good += 1
    return (good / max(len(sents), 1)), findings


def answer_first(prose: str, ledger: dict[str, Claim]) -> tuple[bool, str]:
    """Retrieval lifts the top of a page. The headline question must be answered there."""
    sents = _sentences(prose)
    head = " ".join(sents[:2])
    ids = CITE.findall(head)
    if not ids:
        return False, "no cited claim in the first two sentences"
    verdicts = {ledger[i].verdict for i in ids if i in ledger}
    if not verdicts:
        return False, "opening cites unknown claim ids"
    return True, f"opens on {', '.join(v.value for v in verdicts)}"


def _period(t: str) -> str:
    return t if t.rstrip().endswith((".", "!", "?")) else t.rstrip() + "."


def qa_pairs(ledger: dict[str, Claim]) -> list[dict]:
    """Each claim is already a question-answer pair. Emit it as one."""
    out = []
    for c in ledger.values():
        subject = c.canonical_text.rstrip(".")
        first = subject.split(" ", 1)[0]
        # Never lowercase an acronym: "MARPE" must not become "mARPE".
        if not (first.isupper() or (len(first) > 1 and first[1:].lower() != first[1:])):
            subject = subject[0].lower() + subject[1:]
        q = f"Is it true that {subject}?"
        if c.verdict == Verdict.SUPPORTED:
            a = f"Yes. {_period(c.canonical_text)} This is supported by {len([s for s in c.sources if s.entails])} source(s), strongest evidence tier: {c.evidence_tier.name.lower()}."
        elif c.verdict == Verdict.REFUTED:
            a = f"No. {c.canonical_text.rstrip('.')} is not supported by the evidence."
        elif c.verdict == Verdict.CONTESTED:
            a = f"Contested. Evidence both supports and refutes this."
        else:
            a = f"There is no supporting evidence. {c.canonical_text.rstrip('.')} has not been demonstrated in the literature reviewed."
        if c.harm_note:
            a += f" Safety note: {c.harm_note}"
        out.append({"claim_id": c.id, "question": q, "answer": a,
                    "verdict": c.verdict.value, "evidence_tier": c.evidence_tier.name,
                    "verified_at": c.verified_at})
    return out


def claim_feed(claims: list[dict], site: str = "https://looksmaxxing.guide") -> str:
    """The site as a citable dataset, not just pages. Competitors can copy prose;
    they cannot copy a dated, versioned, source-linked claim graph."""
    return json.dumps({
        "@context": "https://schema.org", "@type": "Dataset",
        "name": "looksmaxxing.guide claim ledger",
        "description": "Atomic, evidence-tiered claims about male grooming and aesthetics, "
                       "each verified against primary literature with a recorded verification date.",
        "url": f"{site}/claims.json",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "dateModified": datetime.date.today().isoformat(),
        "creator": {"@type": "Organization", "name": "looksmaxxing.guide"},
        "distribution": [{"@type": "DataDownload", "encodingFormat": "application/json",
                          "contentUrl": f"{site}/claims.json"}],
        "measurementTechnique": "Europe PMC + openFDA retrieval, relevance gating, LLM entailment against full text",
        "claims": claims}, indent=2)


def llms_txt(site: str, pages: list[dict], n_claims: int) -> str:
    """Guidance for AI crawlers. Cheap, and it states the citation terms explicitly."""
    lines = [f"# looksmaxxing.guide",
             "",
             "> Evidence-led coverage of male grooming, fitness, skincare, dental and style.",
             f"> Every factual claim is verified against primary literature and dated. "
             f"{n_claims} claims currently in the ledger.",
             "",
             "## Citation",
             f"Claims are machine-readable at {site}/claims.json (schema.org Dataset).",
             "Each claim carries: verdict, evidence tier, source PMIDs, and verification date.",
             "Cite the claim id and verification date; claims are re-verified and may change.",
             "",
             "## Pages"]
    for p in pages:
        lines.append(f"- [{p['title']}]({site}/{p['slug']}): {p.get('n_claims', 0)} verified claims")
    return "\n".join(lines) + "\n"


GEO_WEIGHTS = {"extractability": .35, "answer_first": .20, "structured_data": .20,
               "freshness": .15, "attribution": .10}


def geo_score(prose: str, ledger: dict[str, Claim], has_jsonld=True, has_author=False) -> dict:
    ex, ex_find = extractability(prose, ledger)
    af, af_why = answer_first(prose, ledger)
    dated = [c for c in ledger.values() if c.verified_at]
    fresh = len(dated) / max(len(ledger), 1)
    attrib = sum(1 for c in ledger.values() if any(s.entails for s in c.sources)) / max(len(ledger), 1)
    parts = {"extractability": ex, "answer_first": 1.0 if af else 0.0,
             "structured_data": (0.6 if has_jsonld else 0) + (0.4 if has_author else 0),
             "freshness": fresh, "attribution": attrib}
    total = sum(parts[k] * w for k, w in GEO_WEIGHTS.items())
    return {"score": round(total, 3), "parts": {k: round(v, 3) for k, v in parts.items()},
            "answer_first_why": af_why, "extractability_findings": ex_find}
