"""Blocking coverage gate. Deterministic. The composer is boxed by this, not by prompt."""
from __future__ import annotations
import re
from core.ledger import Claim

ABSOLUTES = re.compile(
    r"\b(always|never|proven|guaranteed|permanently|completely|100%|cures?|"
    r"eliminates?|reverses?|definitely|scientifically proven)\b", re.I)
HEDGE = re.compile(r"\b(may|might|can|suggests?|associated|limited|unclear|"
                   r"insufficient|no evidence|does not appear)\b", re.I)
CITE = re.compile(r"\[\[([a-z0-9_\-]+)\]\]", re.I)
ASSERTIVE = re.compile(r"[.!?]")


def lint(prose: str, ledger: dict[str, Claim]) -> tuple[bool, list[dict]]:
    """Every assertive sentence maps to a renderable claim id. Unmapped absolutes fail."""
    findings = []
    for i, raw in enumerate(s.strip() for s in ASSERTIVE.split(prose) if s.strip()):
        if raw.startswith("#") or len(raw.split()) < 5:
            continue
        ids = CITE.findall(raw)
        if not ids:
            if ABSOLUTES.search(raw):
                findings.append({"sentence": raw[:90], "rule": "unbacked_absolute",
                                 "detail": ABSOLUTES.search(raw).group(0)})
            elif not HEDGE.search(raw):
                findings.append({"sentence": raw[:90], "rule": "uncited_assertion",
                                 "detail": "no claim id and no hedge"})
            continue
        for cid in ids:
            c = ledger.get(cid)
            if c is None:
                findings.append({"sentence": raw[:90], "rule": "unknown_claim_id",
                                 "detail": cid})
            elif not c.renderable:
                findings.append({"sentence": raw[:90], "rule": "unrenderable_claim",
                                 "detail": f"{cid} verdict={c.verdict.value}"})
            elif ABSOLUTES.search(raw) and c.strength == "weak":
                findings.append({"sentence": raw[:90], "rule": "overclaim_vs_tier",
                                 "detail": f"{cid} tier={c.evidence_tier.name} but absolute language"})
    return (len(findings) == 0), findings
