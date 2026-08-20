"""Runs green with no API key and no network - the invariants are all deterministic."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.ledger import Claim, SourceRef, RiskTier, Verdict, EvidenceTier, tier_from_pubtypes
from core.tripwires import apply_tripwires
from core.relevance import relevance
from core.lint import lint

def test_dangerous_requires_harm_note():
    try:
        Claim(id="x", canonical_text="t", risk_tier=RiskTier.DANGEROUS); assert False
    except ValueError as e: assert "harm_note" in str(e)

def test_supported_requires_entailing_source():
    s = SourceRef(ext_id="1", source_db="europepmc", resolved=True, entails=False)
    try:
        Claim(id="x", canonical_text="t", verdict=Verdict.SUPPORTED,
              evidence_tier=EvidenceTier.TRIAL, sources=[s]); assert False
    except ValueError as e: assert "entails" in str(e).lower() or "entail" in str(e)

def test_tripwire_floor_beats_llm():
    t,_,hit = apply_tripwires("just bonesmashing a little", RiskTier.COSMETIC)
    assert t == RiskTier.DANGEROUS and hit

def test_tripwire_cannot_be_lowered():
    t,_,_ = apply_tripwires("oral finasteride", RiskTier.DANGEROUS)
    assert t == RiskTier.DANGEROUS

def test_journal_article_no_longer_inflates_tier():
    assert tier_from_pubtypes(["Journal Article"], "a study", "") == EvidenceTier.NONE
    assert tier_from_pubtypes(["Journal Article"], "A systematic review of x", "") == EvidenceTier.SYNTHESIS

def test_relevance_drops_unrelated_but_keeps_vocab_mismatch():
    _, ok, _ = relevance("mewing changes adult maxilla", ["mewing","maxilla"],
                         "Interstitial deletion of chromosome 5", "")
    assert not ok
    _, ok2, _ = relevance("mewing changes adult maxilla", ["mewing","maxilla","suture"],
                          "Midpalatal suture maturation via CBCT", "")
    assert ok2

def test_lint_blocks_unbacked_absolute():
    ok, f = lint("Mewing permanently reshapes the adult maxilla.", {})
    assert not ok and f[0]["rule"] == "unbacked_absolute"

def test_lint_passes_hedged_and_cited():
    c = Claim(id="c1", canonical_text="t", verdict=Verdict.REFUTED)
    ok, f = lint("Wolff's law does not apply to blunt facial trauma [[c1]].", {"c1": c})
    assert ok, f

# ---- claim graph invariants (added after the multi-article run exposed these) ----
def test_tripwire_catches_paraphrase_not_just_brand_word():
    """A rephrase must not walk around the safety floor."""
    brand,_,_ = apply_tripwires("mewing remodels the adult jaw", RiskTier.COSMETIC)
    para,_,_  = apply_tripwires("habitual tongue posture changes the adult skeletal jaw", RiskTier.COSMETIC)
    assert brand == para == RiskTier.MODERATE, (brand, para)

def test_near_dup_collapses_reworded_claim():
    from core.store import Store, jaccard, stem_set
    a = stem_set("Micro-implant-assisted rapid palatal expansion (MARPE) produces measurable skeletal maxillary expansion in adults")
    b = stem_set("Micro-implant-assisted rapid palatal expansion produced skeletal maxillary expansion in adults")
    assert jaccard(a, b) >= 0.60

def test_distinct_claims_do_not_collapse():
    from core.store import jaccard, stem_set
    a = stem_set("Bone smashing increases facial bone density")
    b = stem_set("The midpalatal suture ossifies with age")
    assert jaccard(a, b) < 0.60

def test_store_dedups_and_links_canonical_id(tmp="runs/_test.db"):
    import os
    os.path.exists(tmp) and os.remove(tmp)
    from core.store import Store
    st = Store(tmp)
    c1 = Claim(id="c_a", canonical_text="MARPE produces skeletal maxillary expansion in adults",
               verdict=Verdict.UNSUPPORTED)
    c2 = Claim(id="c_b", canonical_text="MARPE produced skeletal maxillary expansion in adults",
               verdict=Verdict.UNSUPPORTED)
    id1,_ = st.upsert_claim(c1); id2,_ = st.upsert_claim(c2)
    assert id1 == id2, (id1, id2)
    st.link_page("page-one", "One", [id1]); st.link_page("page-two", "Two", [id2])
    assert sorted(st.pages_for_claim(id1)) == ["page-one", "page-two"]
    st.close(); os.remove(tmp)

def test_known_limit_semantic_paraphrase_still_escapes():
    """Documented, not fixed: lexical overlap cannot collapse a true paraphrase.
    Needs embedding nearest-neighbour or an LLM canonicaliser."""
    from core.store import jaccard, stem_set
    a = stem_set("Mewing (habitual tongue posture) remodels adult skeletal jaw and maxilla structure")
    b = stem_set("Habitual tongue posture changes the adult skeletal jaw")
    assert jaccard(a, b) < 0.60   # this SHOULD collapse but does not - see README

# ---- GEO layer -------------------------------------------------------------
def _geo_ledger():
    from core.ledger import SourceRef, Substrate
    src = SourceRef(ext_id="1", source_db="e", resolved=True, entails=True, substrate=Substrate.FULLTEXT)
    return {"c_m": Claim(id="c_m", canonical_text="Mewing remodels the adult jaw",
                         entities=["mewing", "jaw"], verdict=Verdict.UNSUPPORTED, verified_at="2026-08-20"),
            "c_p": Claim(id="c_p", canonical_text="MARPE produces skeletal maxillary expansion in adults",
                         entities=["marpe", "maxilla"], verdict=Verdict.SUPPORTED,
                         evidence_tier=EvidenceTier.SYNTHESIS, sources=[src], verified_at="2026-08-20")}

def test_extractability_flags_orphan_chunk():
    from core.geo import extractability
    led = _geo_ledger()
    _, f = extractability("It does not work for adults.", led)
    assert f and "orphan_pronoun" in f[0]["problems"]

def test_extractability_accepts_self_contained_sentence():
    from core.geo import extractability
    score, _ = extractability("Mewing does not remodel the adult jaw.", _geo_ledger())
    assert score == 1.0

def test_qa_pairs_preserve_acronyms():
    from core.geo import qa_pairs
    qs = [x["question"] for x in qa_pairs(_geo_ledger())]
    assert any("MARPE" in q for q in qs) and not any("mARPE" in q for q in qs)

def test_answer_first_requires_cited_opening():
    from core.geo import answer_first
    led = _geo_ledger()
    ok, _ = answer_first("Mewing does not remodel the adult jaw [[c_m]].", led)
    bad, _ = answer_first("Lots of people wonder about jaw aesthetics these days.", led)
    assert ok and not bad

def test_probe_refuses_rate_below_minimum_samples():
    """A citation rate over n=1 reads as 100% and means nothing."""
    from geo.probe import run_probes
    class OneAnswer:
        def ask(self, q): return ("ans", ["looksmaxxing.guide"]) if q == "a" else ("", [])
    r = run_probes(["a", "b", "c"], OneAnswer())
    assert r["sufficient_data"] is False and r["citation_rate"] is None

def test_probe_reports_rate_with_enough_samples():
    from geo.probe import run_probes
    class AllAnswer:
        def ask(self, q): return ("ans", ["looksmaxxing.guide"] if q < "c" else ["reddit.com"])
    r = run_probes(list("abcde"), AllAnswer())
    assert r["sufficient_data"] and r["citation_rate"] == 0.4

def test_questions_stay_grammatical():
    from geo.probe import questions_for
    qs = questions_for([{"canonical_text": "Mewing remodels the adult jaw", "entities": ["mewing"]}])
    assert not any(q.startswith("Does mewing remodels") for q in qs)

if __name__ == "__main__":
    ts = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for t in ts: t(); print("PASS", t.__name__)
    print(f"\n{len(ts)}/{len(ts)} passed - no key, no network")
