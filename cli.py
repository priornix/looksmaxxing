"""looksmaxxing.guide claim-ledger pipeline."""
from __future__ import annotations
import sys, json, argparse
from core.store import Store
from core.ledger import EvidenceTier, RiskTier

def main():
    p = argparse.ArgumentParser(prog="ledger", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="verify a topic and publish if the gate passes")
    r.add_argument("topic"); r.add_argument("--slug", default=None)
    sub.add_parser("ledger", help="show the claim graph")
    w = sub.add_parser("watch", help="re-verify claims and fan out to affected pages")
    w.add_argument("--older-than-days", type=int, default=0)
    h = sub.add_parser("history", help="audit trail for one claim"); h.add_argument("claim_id")
    sub.add_parser("geo", help="emit llms.txt + claims.json + Q&A schema, score GEO, probe citations")
    a = p.parse_args()
    store = Store()

    if a.cmd == "run":
        from pipeline import run_topic
        slug = a.slug or a.topic.lower().replace("?", "").replace(" ", "-")[:48]
        out = run_topic(a.topic, slug, store)
        json.dump(out, open("runs/latest.json", "w"), indent=2)
        print("\n" + ("BLOCKED - nothing published" if out["blocked"] else out["prose"]))
    elif a.cmd == "ledger":
        c = store.counts()
        print(f"claims={c['claims']} sources={c['sources']} pages={c['pages']} audit={c['audit']}\n")
        for row in store.all_claims():
            print(f"  {row['id']:14} {row['verdict']:12} tier={EvidenceTier(row['evidence_tier']).name:13}"
                  f" risk={RiskTier(row['risk_tier']).name:9} pages={len(store.pages_for_claim(row['id']))}"
                  f"  {row['canonical_text'][:52]}")
    elif a.cmd == "watch":
        from watcher import rewatch
        print(f"re-verifying claims older than {a.older_than_days}d:")
        out = rewatch(store, a.older_than_days)
        print(f"\nchecked={out['checked']}  candidates={len(out['candidates'])}"
              f"  confirmed by entailment={len(out['confirmed'])}")
        for c in out["candidates"]:
            mark = "CONFIRMED" if c in out["confirmed"] else "unconfirmed"
            print(f"  [{mark:11}] {c['claim']}: {c['was']} ~> {c['tier_seen']}"
                  f"  ({c['relevant_sources']} relevant)  pages={c['pages']}")
        print(f"\n  {len(out['regenerate'])} page(s) queued for regeneration"
              + ("" if out["regenerate"] else "  - a tier rise alone never triggers a rewrite"))
    elif a.cmd == "geo":
        store.close()
        import export_geo; export_geo.main(); return
    elif a.cmd == "history":
        for h in store.history(a.claim_id):
            print(f"  {h['ts']}  {h['field']:14} {h['old'] or '-'} -> {h['new']}   ({h['reason']})")
    store.close()

if __name__ == "__main__":
    main()
