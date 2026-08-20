"""The outer loop: re-verify claims as evidence moves, then fan out to affected pages.

This is what a one-shot pipeline cannot do. Because pages reference claim ids, a verdict
change names exactly which pages are now wrong.

DESIGN NOTE (learned the hard way): the first version declared a "flip" on a tier rise
detected by the relevance gate alone. That is the same relevance-is-not-entailment bug the
main pipeline already fixed, reappearing here because the invariant lived in pipeline.py
rather than in shared code. A tier rise is only ever a CANDIDATE. Nothing regenerates until
stage B entailment confirms support - a cron job must never silently rewrite a health page.
"""
from __future__ import annotations
import json
from core.ledger import Verdict, EvidenceTier, Substrate
from core.evidence import europepmc_search, to_source, evidence_tier_for, get_text
from core.relevance import relevance
from core.store import Store
from agents.steps import judge_entailment, LIVE

ENT_FIX = json.load(open("fixtures/entailment.json"))


def rewatch(store: Store, older_than_days: int = 0, verbose=True) -> dict:
    stale = store.stale_claims(older_than_days)
    candidates, confirmed, regen = [], [], set()

    for row in stale:
        ents = json.loads(row["entities"])
        recs, seen = [], set()
        for q in (row["canonical_text"], " ".join(ents[:3])):
            for r in europepmc_search(q, 5):
                pid = r.get("pmid") or r.get("id")
                if pid and pid not in seen:
                    seen.add(pid); recs.append(r)

        relevant = []
        for r in recs:
            _, ok, _ = relevance(row["canonical_text"], ents,
                                 r.get("title") or "", r.get("abstractText") or "")
            if ok:
                relevant.append(r)
        best = max([evidence_tier_for(r) for r in relevant], default=EvidenceTier.NONE)
        rose = int(best) > int(row["evidence_tier"]) and row["verdict"] == Verdict.UNSUPPORTED.value

        if verbose:
            print(f"  {row['id']:14} tier {EvidenceTier(row['evidence_tier']).name:13}"
                  f"~> {best.name:13} relevant={len(relevant):2}"
                  f"  {'CANDIDATE' if rose else ''}")
        if not rose:
            continue

        cand = {"claim": row["id"], "text": row["canonical_text"][:60],
                "was": EvidenceTier(row["evidence_tier"]).name, "tier_seen": best.name,
                "relevant_sources": len(relevant), "pages": store.pages_for_claim(row["id"])}
        candidates.append(cand)

        # STAGE B - the only thing that may promote a candidate to a real change.
        supported = False
        for r in sorted(relevant, key=evidence_tier_for, reverse=True)[:2]:
            s = to_source(r)
            body, sub = ((r.get("abstractText") or ""), Substrate.ABSTRACT)
            if LIVE:
                body, sub = get_text(r)
            v = judge_entailment(row["canonical_text"], s.title, body, sub.value,
                                 ENT_FIX.get(s.ext_id))
            if v.entails:
                supported = True
                cand["entailed_by"] = {"pmid": s.ext_id, "why": v.why}
                break
        if supported:
            confirmed.append(cand)
            regen.update(cand["pages"])

    return {"checked": len(stale), "candidates": candidates,
            "confirmed": confirmed, "regenerate": sorted(regen)}
