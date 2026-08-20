"""L0 evidence substrate. Keyless, live. Better data beats a better model."""
from __future__ import annotations
import httpx
from core.ledger import SourceRef, Substrate, tier_from_pubtypes, EvidenceTier

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
FDA = "https://api.fda.gov"
UA = {"User-Agent": "looksmaxxing.guide claim-verifier/0.1"}


def europepmc_search(query: str, limit: int = 5) -> list[dict]:
    r = httpx.get(f"{EPMC}/search", params={
        "query": query, "format": "json", "pageSize": limit,
        "resultType": "core"}, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json().get("resultList", {}).get("result", [])


def to_source(rec: dict) -> SourceRef:
    """Stage A: the record resolved. Tier is derived deterministically from pub type."""
    pts = [t for t in (rec.get("pubTypeList", {}) or {}).get("pubType", []) if t]
    has_ft = bool((rec.get("fullTextIdList") or {}).get("fullTextId"))
    return SourceRef(
        ext_id=rec.get("pmid") or rec.get("id", ""),
        source_db="europepmc",
        title=(rec.get("title") or "").strip(),
        year=int(rec["pubYear"]) if rec.get("pubYear", "").isdigit() else None,
        pub_type="; ".join(pts),
        resolved=True,
        substrate=Substrate.FULLTEXT if has_ft else Substrate.ABSTRACT,
    )


def get_text(rec: dict) -> tuple[str, Substrate]:
    """Full text when the paper is OA, else abstract. Records which - it changes weight."""
    pmcid = (rec.get("fullTextIdList") or {}).get("fullTextId", [])
    if pmcid:
        try:
            r = httpx.get(f"{EPMC}/{pmcid[0]}/fullTextXML", headers=UA, timeout=25)
            if r.status_code == 200 and len(r.text) > 500:
                import re as _re
                txt = _re.sub(r"<[^>]+>", " ", r.text)
                return _re.sub(r"\s+", " ", txt)[:12000], Substrate.FULLTEXT
        except Exception:
            pass
    return (rec.get("abstractText") or "")[:6000], Substrate.ABSTRACT


def openfda_faers(drug: str, limit: int = 5) -> list[dict]:
    """Primary regulatory harm data - the harm-reduction differentiator."""
    try:
        r = httpx.get(f"{FDA}/drug/event.json", params={
            "search": f"patient.drug.medicinalproduct:{drug}",
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": limit}, headers=UA, timeout=20)
        return r.json().get("results", []) if r.status_code == 200 else []
    except Exception:
        return []


def evidence_tier_for(rec: dict) -> EvidenceTier:
    pts = [t for t in (rec.get("pubTypeList", {}) or {}).get("pubType", []) if t]
    return tier_from_pubtypes(pts, rec.get("title") or "", rec.get("abstractText") or "")
