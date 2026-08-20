"""AI-search citability: the ledger IS the citable unit. Emit it as structured data."""
from __future__ import annotations
import json
from core.ledger import Claim


def emit(topic: str, url: str, claims: dict[str, Claim]) -> str:
    revs = []
    for c in claims.values():
        if not c.renderable:
            continue
        srcs = [s for s in c.sources if s.resolved and s.entails]
        revs.append({
            "@type": "ClaimReview",
            "claimReviewed": c.canonical_text,
            "reviewRating": {"@type": "Rating",
                             "alternateName": c.verdict.value,
                             "ratingValue": int(c.evidence_tier), "bestRating": 5},
            "itemReviewed": {"@type": "Claim", "appearance": {"@type": "CreativeWork"}},
            "citation": [{"@type": "ScholarlyArticle", "identifier": f"PMID:{s.ext_id}",
                          "name": s.title} for s in srcs],
        })
    return json.dumps({"@context": "https://schema.org", "@type": "MedicalWebPage",
                       "about": topic, "url": url,
                       "reviewedBy": {"@type": "Organization", "name": "looksmaxxing.guide"},
                       "hasPart": revs}, indent=2)
