"""Orchestrator. Deterministic steps run live; LLM steps via provider (recorded|live)."""
from __future__ import annotations
import json, os, datetime
from core.ledger import Claim, SourceRef, EvidenceTier, RiskTier, Verdict, Substrate
from core.evidence import europepmc_search, to_source, evidence_tier_for, openfda_faers
from core.relevance import relevance
from core.tripwires import apply_tripwires
from core.lint import lint
from core.jsonld import emit

TOPIC = "Does mewing change adult jaw structure?"
TRACE = []
def step(n, trigger, tool, out):
    TRACE.append({"step": n, "trigger": trigger, "tool": tool, "output": out})
    print(f"[{n}] {tool}: {out}")

# --- STEP 1: Claim Miner (LLM, recorded) -------------------------------------
MINED = json.load(open("fixtures/claims_mined.json"))
step(1, "topic queued", "claim_miner (LLM, structured out)",
     f"{len(MINED)} atomic claim candidates")

# --- STEP 2: Triage — LLM tier + deterministic tripwire floor ----------------
triaged = []
for m in MINED:
    llm_tier = RiskTier[m["llm_risk_tier"]]
    final, note, hit = apply_tripwires(m["text"], llm_tier)
    triaged.append({**m, "risk": final, "harm_note": note or m.get("harm_note"), "hit": hit})
overrides = [t for t in triaged if t["hit"] and t["risk"] > RiskTier[t["llm_risk_tier"]]]
step(2, "step 1 output", "triage (LLM + TRIPWIRE floor)",
     f"{len(overrides)} LLM risk calls overridden upward by tripwire")

# --- STEP 3+4: Evidence retrieval (LIVE) + relevance gate -------------------
ledger: dict[str, Claim] = {}
dropped_total = 0
for t in triaged:
    recs = []
    for q in t["queries"]:
        recs += europepmc_search(q, 4)
    seen, cand = set(), []
    for r in recs:
        pid = r.get("pmid") or r.get("id")
        if not pid or pid in seen: continue
        seen.add(pid)
        title, abst = r.get("title") or "", r.get("abstractText") or ""
        sc, ok, why = relevance(t["text"], t["entities"], title, abst)
        if not ok:
            dropped_total += 1
            continue
        s = to_source(r); s.note = why
        cand.append((s, evidence_tier_for(r), r))
    # --- STEP 5: Entailment (recorded verdicts keyed by pmid) ---------------
    ENT = json.load(open("fixtures/entailment.json"))
    kept = []
    for s, tier, r in cand:
        v = ENT.get(s.ext_id)
        if v is None: continue
        s.entails = v["entails"]; s.substrate = Substrate(v["substrate"])
        s.note = v["why"]
        if s.entails: kept.append((s, tier))
    best_tier = max([t2 for _, t2 in kept], default=EvidenceTier.NONE)
    verdict = Verdict(t["verdict"]) if kept or t["verdict"] in ("refuted","unsupported") else Verdict.UNSUPPORTED
    if verdict == Verdict.SUPPORTED and not kept:
        verdict = Verdict.UNSUPPORTED
    ledger[t["id"]] = Claim(
        id=t["id"], canonical_text=t["text"], entities=t["entities"],
        evidence_tier=best_tier, risk_tier=t["risk"], verdict=verdict,
        harm_note=t["harm_note"], sources=[s for s, _ in kept] + [s for s,_,_ in cand if not s.entails][:3],
        verified_at=datetime.date.today().isoformat(), tripwire_hit=t["hit"])
step(3, "step 2 output", "europepmc_search (LIVE HTTP)", f"{len(seen)} unique records retrieved")
step(4, "step 3 output", "relevance_gate (deterministic)", f"{dropped_total} irrelevant sources dropped pre-entailment")
step(5, "step 4 output", "entailment_judge (LLM vs fulltext/abstract)",
     f"{sum(1 for c in ledger.values() for s in c.sources if s.entails)} sources entail")

# --- STEP 6: openFDA harm data (LIVE) ---------------------------------------
faers = openfda_faers("finasteride", 5)
step(6, "risk>=HIGH claim present", "openfda_faers (LIVE HTTP)",
     f"top AEs: {', '.join(x['term'].lower() for x in faers[:3])}" if faers else "none")

# --- STEP 7: Compose + STEP 8: blocking lint (with back-edge) ---------------
for attempt, path in enumerate(["fixtures/draft_v1.md", "fixtures/draft_v2.md"], 1):
    prose = open(path).read()
    ok, findings = lint(prose, ledger)
    step(7 if attempt == 1 else 9, "ledger complete" if attempt == 1 else "lint back-edge",
         f"compose (LLM, ledger-constrained) attempt {attempt}", f"{len(prose.split())} words")
    step(8 if attempt == 1 else 10, "compose output", "coverage_lint (DETERMINISTIC GATE)",
         "PASS" if ok else f"BLOCKED — {len(findings)} findings: " +
         "; ".join(f"{f['rule']}({f['detail']})" for f in findings[:3]))
    if ok:
        break
    print("      back-edge -> compose")
else:
    step(11, "lint never passed", "publish", "HELD - gate never passed, nothing published")
    json.dump({"topic": TOPIC, "trace": TRACE, "blocked": True}, open("runs/latest.json","w"), indent=2)
    raise SystemExit("BLOCKED: refusing to publish content that failed the coverage gate")

# --- STEP 11: Publish -------------------------------------------------------
ld = emit(TOPIC, "https://looksmaxxing.guide/mewing-adult-jaw", ledger)
step(11, "lint PASS", "publish (JSON-LD ClaimReview)", f"{len(json.loads(ld)['hasPart'])} ClaimReview nodes emitted")

os.makedirs("runs", exist_ok=True)
json.dump({"topic": TOPIC, "trace": TRACE,
           "ledger": {k: json.loads(v.model_dump_json()) for k, v in ledger.items()},
           "jsonld": json.loads(ld), "faers": faers},
          open("runs/latest.json", "w"), indent=2)
print("\n--- LEDGER ---")
for c in ledger.values():
    print(f"  {c.id}  {c.verdict.value:12} tier={c.evidence_tier.name:13} risk={c.risk_tier.name:9} "
          f"src={sum(1 for s in c.sources if s.entails)}  {c.canonical_text[:58]}")
