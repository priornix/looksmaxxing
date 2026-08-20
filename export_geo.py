"""Emit the GEO surface: llms.txt, claims.json (citable dataset), Q&A structured data."""
from __future__ import annotations
import json, sys
from pathlib import Path
from core.store import Store
from core.ledger import Claim, SourceRef, Substrate, EvidenceTier, RiskTier, Verdict
from core.geo import qa_pairs, claim_feed, llms_txt, geo_score
from geo.probe import run_probes, questions_for

SITE = "https://looksmaxxing.guide"
OUT = Path("site")


def load_ledger(store: Store) -> dict[str, Claim]:
    led = {}
    for r in store.all_claims():
        srcs = [SourceRef(ext_id=x["ext_id"], source_db=x["source_db"], title=x["title"] or "",
                          year=x["year"], pub_type=x["pub_type"] or "", resolved=bool(x["resolved"]),
                          entails=None if x["entails"] is None else bool(x["entails"]),
                          substrate=Substrate(x["substrate"]), note=x["note"] or "")
                for x in store.db.execute("SELECT * FROM sources WHERE claim_id=?", (r["id"],))]
        led[r["id"]] = Claim(id=r["id"], canonical_text=r["canonical_text"],
            entities=json.loads(r["entities"]), evidence_tier=EvidenceTier(r["evidence_tier"]),
            risk_tier=RiskTier(r["risk_tier"]), verdict=Verdict(r["verdict"]),
            harm_note=r["harm_note"], sources=srcs, verified_at=r["verified_at"],
            tripwire_hit=r["tripwire_hit"])
    return led


def main():
    OUT.mkdir(exist_ok=True)
    store = Store()
    led = load_ledger(store)
    rows = [json.loads(c.model_dump_json()) for c in led.values()]
    pages = [{"slug": p["slug"], "title": p["title"],
              "n_claims": len(store.db.execute(
                  "SELECT 1 FROM page_claims WHERE slug=?", (p["slug"],)).fetchall())}
             for p in store.db.execute("SELECT * FROM pages")]

    qa = qa_pairs(led)
    (OUT / "claims.json").write_text(claim_feed(rows, SITE))
    (OUT / "llms.txt").write_text(llms_txt(SITE, pages, len(led)))
    (OUT / "qa.jsonld").write_text(json.dumps({
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": x["question"],
                        "acceptedAnswer": {"@type": "Answer", "text": x["answer"]},
                        "dateModified": x["verified_at"]} for x in qa]}, indent=2))

    prose = ""
    latest = Path("runs/latest.json")
    if latest.exists():
        prose = json.loads(latest.read_text()).get("prose", "")
    gs = geo_score(prose, led, has_jsonld=True, has_author=False) if prose else None

    probes = run_probes(questions_for(rows))
    (OUT / "geo_report.json").write_text(json.dumps(
        {"geo_score": gs, "citation_probe": probes}, indent=2))

    print(f"wrote {OUT}/claims.json  ({len(rows)} claims, schema.org Dataset)")
    print(f"wrote {OUT}/llms.txt     ({len(pages)} pages listed)")
    print(f"wrote {OUT}/qa.jsonld    ({len(qa)} FAQPage entries)")
    if gs:
        print(f"\nGEO score {gs['score']}  {gs['parts']}")
        for f in gs["extractability_findings"][:3]:
            print(f"   FLAG {','.join(f['problems']):32} {f['sentence'][:52]}")
    rate = (f"rate={probes['citation_rate']}" if probes["sufficient_data"]
            else f"INSUFFICIENT DATA (need >=5 answered, got {probes['answered']})")
    print(f"\ncitation probe [{probes['mode']}]: {rate} "
          f"| coverage {probes['coverage']} ({probes['answered']}/{probes['questions']})")
    for d, n in probes["top_competitors"][:3]:
        print(f"   beaten by {d} ({n})")
    store.close()


if __name__ == "__main__":
    main()
