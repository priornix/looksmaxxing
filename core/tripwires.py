"""Deterministic danger floor. An LLM may escalate risk; it may never de-escalate."""
from __future__ import annotations
import re
from core.ledger import RiskTier

# (pattern, floor, harm_note) - curated from the actual looksmaxxing threat surface
TRIPWIRES: list[tuple[str, RiskTier, str]] = [
    (r"\bbone[\s-]?smash", RiskTier.DANGEROUS,
     "Bone smashing risks fracture, nerve damage and permanent asymmetry. "
     "Wolff's law does not apply to blunt facial trauma."),
    # Concept, not brand word. "habitual tongue posture" is mewing under another name -
    # a paraphrase must not be able to walk around the safety floor.
    (r"\b(mew(ing)?|tongue\s+post\w+|orthotropic\w*)\b.*\b(bone|maxilla|jaw|skeletal|facial)\b"
     r"|\b(bone|maxilla|jaw|skeletal)\b.*\b(mew(ing)?|tongue\s+post\w+|orthotropic\w*)\b",
     RiskTier.MODERATE,
     "Craniofacial sutures fuse in adulthood; soft-tissue and postural change is not "
     "skeletal remodelling. Claims of adult bone change are unsupported."),
    (r"\b(finasteride|dutasteride|isotretinoin|accutane|tretinoin|minoxidil oral|"
     r"semaglutide|tirzepatide|melanotan|anavar|testosterone)\b", RiskTier.HIGH,
     "Prescription-only in US/UK/AU/CA. Requires clinician supervision; "
     "self-sourcing carries counterfeit, dosing and monitoring risks."),
    (r"\b(derma[\s-]?roll|derma[\s-]?pen|micro[\s-]?needl)\w*\b.*\b(diy|at[\s-]?home|1\.5|2\.0|2\.5)\b",
     RiskTier.HIGH,
     "Needle depths >1.0mm at home risk scarring, infection and pigment change. "
     "Depth and sterility control are clinical requirements."),
    (r"\b(starv|water fast|omad|500 ?cal|dry fast)\w*\b", RiskTier.HIGH,
     "Aggressive caloric restriction risks muscle loss, disordered eating and "
     "cardiac events. Screen for eating disorder history first."),
    (r"\b(jaw|orthognathic|rhinoplast|blephar|zygoma)\w*\s*(surgery|implant)\b",
     RiskTier.HIGH,
     "Irreversible surgical intervention. Outcomes vary; requires board-certified "
     "surgeon consultation and realistic expectation setting."),
]

_COMPILED = [(re.compile(p, re.I), t, n) for p, t, n in TRIPWIRES]


def apply_tripwires(text: str, llm_tier: RiskTier) -> tuple[RiskTier, str | None, str | None]:
    """Returns (final_tier, harm_note, tripwire_id). Floor always wins over the LLM."""
    floor, note, hit = RiskTier.COSMETIC, None, None
    for rx, tier, harm in _COMPILED:
        if rx.search(text) and tier > floor:
            floor, note, hit = tier, harm, rx.pattern[:40]
    final = max(llm_tier, floor)
    if final > llm_tier:
        return final, note, hit          # tripwire overrode a too-low LLM call
    return final, note if hit else None, hit
