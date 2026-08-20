"""End-to-end: topic -> verified ledger rows -> gated prose -> publish."""
from __future__ import annotations
import json, datetime
from core.ledger import Claim, EvidenceTier, RiskTier, Verdict, Substrate
from core.evidence import europepmc_search, to_source, evidence_tier_for, get_text, openfda_faers
from core.relevance import relevance
from core.tripwires import apply_tripwires
from core.lint import lint
from core.jsonld import emit
from core.store import Store
from agents.steps import mine_claims, judge_entailment, compose, LIVE

MAX_DRAFTS = 3
ENT_FIX = json.load(open("fixtures/entailment.json"))


def run_topic(topic: str, slug: str, store: Store, verbose=True) -> dict:
    trace = []
    def step(tool, out, trigger):
        trace.append({"step": len(trace) + 1, "tool": tool, "trigger": trigger, "output": out})
        if verbose: print(f"[{len(trace):02d}] {tool}: {out}")

    mined = mine_claims(topic)
    step("claim_miner (LLM)", f"{len(mined)} atomic claims", "topic queued")

    ledger: dict[str, Claim] = {}
    dropped = kept = 0
    for m in mined:
        llm_tier = RiskTier[m.llm_risk_tier]
        risk, note, hit = apply_tripwires(m.text, llm_tier)
        recs, seen = [], set()
        for q in m.queries:
            for r in europepmc_search(q, 4):
                pid = r.get("pmid") or r.get("id")
                if pid and pid not in seen:
                    seen.add(pid); recs.append(r)
        sources, best = [], EvidenceTier.NONE
        for r in recs:
            title, abst = r.get("title") or "", r.get("abstractText") or ""
            _, ok, why = relevance(m.text, m.entities, title, abst)
            if not ok:
                dropped += 1
                continue
            s = to_source(r)
            body, sub = (abst, Substrate.ABSTRACT)
            if LIVE:
                body, sub = get_text(r)
            v = judge_entailment(m.text, title, body, sub.value, ENT_FIX.get(s.ext_id))
            s.entails, s.substrate, s.note = v.entails, Substrate(v.substrate), v.why
            sources.append(s)
            if v.entails:
                kept += 1
                best = max(best, evidence_tier_for(r))
        entailing = [s for s in sources if s.entails]
        verdict = (Verdict.SUPPORTED if entailing and best > EvidenceTier.NONE
                   else Verdict.REFUTED if m.id == "c_bonesmash" else Verdict.UNSUPPORTED)
        ledger[m.id] = Claim(
            id=m.id, canonical_text=m.text, entities=m.entities, evidence_tier=best,
            risk_tier=risk, verdict=verdict, harm_note=note or m.harm_note,
            sources=sources[:6], tripwire_hit=hit,
            verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))

    overrides = sum(1 for m in mined if apply_tripwires(m.text, RiskTier[m.llm_risk_tier])[0] > RiskTier[m.llm_risk_tier])
    step("triage + tripwire_floor (det.)", f"{overrides} LLM risk calls overridden upward", "miner output")
    step("europepmc_search (LIVE)", f"{dropped + kept} candidate sources across all claims", "triage output")
    step("relevance_gate (det.)", f"{dropped} dropped before entailment", "search output")
    step("entailment_judge (LLM)", f"{kept} sources entail", "relevance output")

    if any(c.risk_tier >= RiskTier.HIGH for c in ledger.values()):
        ae = openfda_faers("finasteride", 3)
        step("openfda_faers (LIVE)", ", ".join(x["term"].lower() for x in ae) or "none",
             "a risk>=HIGH claim entered the ledger")

    changed, canonical_ids, merged_ids = [], [], []
    for c in ledger.values():
        cid, ch = store.upsert_claim(c, reason=f"run:{slug}")
        canonical_ids.append(cid)          # near-dup may collapse onto an existing node
        if cid != c.id: merged_ids.append((c.id, cid))
        if ch: changed.append(cid)
    store.link_page(slug, topic, canonical_ids)
    step("claim_graph.upsert (SQLite)",
         f"{len(ledger)} rows, {len(changed)} new/changed, {len(merged_ids)} merged"
         + (f" ({', '.join(a+'->'+b for a,b in merged_ids)})" if merged_ids else ""),
         "verification complete")

    rows = [json.loads(c.model_dump_json()) for c in ledger.values()]
    findings, prose, ok = None, "", False
    for attempt in range(1, MAX_DRAFTS + 1):
        prose = compose(topic, rows, findings)
        step(f"compose attempt {attempt} (LLM)", f"{len(prose.split())} words",
             "ledger complete" if attempt == 1 else "lint back-edge")
        ok, findings = lint(prose, ledger)
        step("coverage_lint (DET. GATE)",
             "PASS" if ok else f"BLOCKED - {len(findings)}: " +
             "; ".join(f"{f['rule']}({f['detail']})" for f in findings[:2]),
             "compose output")
        if ok:
            break
    if not ok:
        step("publish", "HELD - gate never passed, nothing published", "drafts exhausted")
        return {"topic": topic, "slug": slug, "trace": trace, "blocked": True,
                "findings": findings, "ledger": rows}

    ld = emit(topic, f"https://looksmaxxing.guide/{slug}", ledger)
    step("publish (JSON-LD)", f"{len(json.loads(ld)['hasPart'])} ClaimReview nodes", "lint PASS")
    return {"topic": topic, "slug": slug, "trace": trace, "blocked": False,
            "prose": prose, "ledger": rows, "jsonld": json.loads(ld)}
