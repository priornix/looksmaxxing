"""Claim ledger: the system of record. Content is a projection of this."""
from __future__ import annotations
from enum import IntEnum, Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class EvidenceTier(IntEnum):
    """Derived from publication type - a mapping, never a model judgment."""
    NONE = 0          # no source found
    ANECDOTE = 1      # forum, testimonial, case report
    MECHANISTIC = 2   # plausible-mechanism reasoning, in-vitro
    OBSERVATIONAL = 3 # cohort, cross-sectional
    TRIAL = 4         # RCT
    SYNTHESIS = 5     # systematic review / meta-analysis


class RiskTier(IntEnum):
    COSMETIC = 0    # reversible, no medical exposure
    MODERATE = 1    # OTC actives, minor procedures
    HIGH = 2        # prescription drugs, invasive procedures
    DANGEROUS = 3   # irreversible harm potential, self-surgery


class Verdict(str, Enum):
    SUPPORTED = "supported"
    CONTESTED = "contested"
    REFUTED = "refuted"
    UNSUPPORTED = "unsupported"   # searched, found nothing


class Substrate(str, Enum):
    """What the entailment check actually read. An abstract-only check is weaker."""
    FULLTEXT = "fulltext"
    ABSTRACT = "abstract"
    LABEL = "label"          # openFDA drug label
    FAERS = "faers"          # adverse event counts
    NONE = "none"


class SourceRef(BaseModel):
    ext_id: str                  # PMID / PMCID / openFDA id
    source_db: str               # europepmc | openfda
    title: str = ""
    year: Optional[int] = None
    pub_type: str = ""
    resolved: bool = False       # stage A: does it exist?
    entails: Optional[bool] = None  # stage B: does it support the claim?
    substrate: Substrate = Substrate.NONE
    note: str = ""


class Claim(BaseModel):
    """Invalid content is unconstructable - the gate lives in the type system."""
    id: str
    canonical_text: str
    entities: list[str] = Field(default_factory=list)
    evidence_tier: EvidenceTier = EvidenceTier.NONE
    risk_tier: RiskTier = RiskTier.COSMETIC
    verdict: Verdict = Verdict.UNSUPPORTED
    harm_note: Optional[str] = None
    sources: list[SourceRef] = Field(default_factory=list)
    verified_at: Optional[str] = None
    tripwire_hit: Optional[str] = None

    @model_validator(mode="after")
    def enforce_invariants(self) -> "Claim":
        if self.risk_tier >= RiskTier.HIGH and not self.harm_note:
            raise ValueError(
                f"{self.id}: risk_tier={self.risk_tier.name} requires a harm_note")
        if self.verdict == Verdict.SUPPORTED:
            entailing = [s for s in self.sources if s.resolved and s.entails]
            if not entailing:
                raise ValueError(
                    f"{self.id}: verdict=SUPPORTED requires >=1 source that both "
                    f"resolves AND entails (found {len(self.sources)} sources, "
                    f"{sum(1 for s in self.sources if s.resolved)} resolved, "
                    f"{sum(1 for s in self.sources if s.entails)} entailing)")
            if self.evidence_tier == EvidenceTier.NONE:
                raise ValueError(f"{self.id}: SUPPORTED cannot have evidence_tier=NONE")
        return self

    @property
    def renderable(self) -> bool:
        """Composer may render any claim that completed verification - including
        UNSUPPORTED. Reporting the absence of evidence is the core editorial act of an
        evidence-led site; excluding it silences the correct answer to most of this
        niche's claims. Positive assertion of a weak claim is caught by overclaim_vs_tier."""
        return True

    @property
    def strength(self) -> str:
        if self.verdict == Verdict.REFUTED:
            return "refuted"
        if self.evidence_tier >= EvidenceTier.TRIAL:
            return "strong"
        if self.evidence_tier >= EvidenceTier.OBSERVATIONAL:
            return "moderate"
        return "weak"


PUBTYPE_TO_TIER = {
    "systematic review": EvidenceTier.SYNTHESIS,
    "meta-analysis": EvidenceTier.SYNTHESIS,
    "randomized controlled trial": EvidenceTier.TRIAL,
    "clinical trial": EvidenceTier.TRIAL,
    "cohort": EvidenceTier.OBSERVATIONAL,
    "observational study": EvidenceTier.OBSERVATIONAL,
    "case reports": EvidenceTier.ANECDOTE,
    "review": EvidenceTier.MECHANISTIC,
    "editorial": EvidenceTier.ANECDOTE,
    "letter": EvidenceTier.ANECDOTE,
}


def tier_from_pubtypes(pub_types: list[str], title: str = "", abstract: str = "") -> EvidenceTier:
    """Deterministic. 'Journal Article' is uninformative and no longer promotes a tier;
    design markers in title/abstract are the secondary signal. Highest wins."""
    from core.relevance import design_tier_from_text
    best = EvidenceTier.NONE
    for pt in pub_types:
        key = pt.strip().lower()
        for needle, tier in PUBTYPE_TO_TIER.items():
            if needle in key and tier > best:
                best = tier
    design = EvidenceTier(design_tier_from_text(title, abstract))
    return max(best, design)
