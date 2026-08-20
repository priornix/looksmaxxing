# Claim Ledger Pipeline

Agentic content pipeline for **looksmaxxing.guide**. Content is a projection of a verified
claim graph — the composer cannot assert a fact, only render rows that already passed
evidence verification, and a deterministic gate refuses to publish anything else.

See [ARCHITECTURE1.md](ARCHITECTURE1.md) for the design and [architecture.svg](architecture.svg)
for the diagram.

## Run it

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python "pydantic-ai-slim[anthropic]" httpx

.venv/bin/python tests/test_gates.py                 # 20/20, no key, no network
.venv/bin/python cli.py run "Does mewing change adult jaw structure?" --slug mewing-adult-jaw
.venv/bin/python cli.py run "Is jaw surgery worth it for looks?"      --slug jaw-surgery-worth-it
.venv/bin/python cli.py ledger                        # the claim graph
.venv/bin/python cli.py watch --older-than-days 0     # re-verify + fan out
.venv/bin/python cli.py history c_marpe               # audit trail
.venv/bin/python cli.py geo                          # GEO surface + score + citation probe
```

**No API key required.** Deterministic steps (Europe PMC, openFDA, tripwires, relevance,
lint, store) run live. LLM steps run through PydanticAI `TestModel` with recorded outputs,
which still exercises the real `Agent` machinery and real schema validation. Export
`ANTHROPIC_API_KEY` and the same code paths go live against Claude — including full-text
entailment, which is skipped in recorded mode.

## Layout

```
core/          framework-free. every invariant lives here.
  ledger.py       Claim model + validators (invalid content is unconstructable)
  tripwires.py    deterministic risk floor — raises only, never lowers
  evidence.py     Europe PMC (full text) + openFDA (labels/FAERS), keyless
  relevance.py    stage A2 anchor gate + design-marker tiering
  lint.py         coverage gate — blocking, fail-closed
  store.py        SQLite claim graph, dedup, audit trail
  jsonld.py       schema.org ClaimReview emitter
agents/steps.py   PydanticAI seam over the 3 LLM steps. Delete it; core still runs.
pipeline.py       topic -> ledger -> gated prose -> publish
watcher.py        outer loop: re-verify, confirm by entailment, fan out
core/geo.py       extractability scoring, Q&A pairs, claim feed, llms.txt
geo/probe.py      pluggable citation probe (recorded | live)
export_geo.py     emits site/ GEO surface
cli.py            run | ledger | watch | history | geo
```

## What a run does

```
[01] claim_miner (LLM)              4 atomic claims
[02] triage + tripwire_floor (det.) 2 LLM risk calls overridden upward
[03] europepmc_search (LIVE)        12 candidate sources
[04] relevance_gate (det.)          6 dropped before entailment
[05] entailment_judge (LLM)         6 sources entail
[06] openfda_faers (LIVE)           fatigue, adverse drug reaction, erectile dysfunction
[07] claim_graph.upsert (SQLite)    3 rows, 2 new/changed, 1 merged (c_marpe_alt->c_marpe)
[08] compose attempt 1 (LLM)        59 words
[09] coverage_lint (DET. GATE)      BLOCKED - unbacked_absolute(permanently); ...
[10] compose attempt 2 (LLM)        96 words
[11] coverage_lint (DET. GATE)      PASS
[12] publish (JSON-LD)              4 ClaimReview nodes
```

Two articles, six claim nodes, and `c_marpe` carries `pages=2` — one node serving both
articles. That is the mechanic a one-shot pipeline cannot reproduce: when evidence moves,
the graph names exactly which pages are now wrong.

## GEO layer

SEO rewards depth and crawlability. GEO rewards atomic, self-contained, attributed
assertions that survive chunking — which is what a claim ledger already is. `cli.py geo`
stops that going to waste:

```
site/claims.json    schema.org Dataset — the site as a citable dataset, not just pages
site/qa.jsonld      FAQPage, one entry per claim, each with its verification date
site/llms.txt       crawler guidance stating the citation terms explicitly
site/geo_report.json  score + citation probe results
```

`core/geo.py` scores prose on five weighted parts — extractability (does a lifted chunk
still stand alone?), answer-first, structured data, freshness, attribution. The
extractability check is deterministic: it flags orphan pronouns, deictic references
("as mentioned above"), and sentences naming no entity. It found a real one in the
published draft — *"That is a supervised orthodontic procedure…"* dies on extraction.

`geo/probe.py` measures the outcome that actually matters: ask assistants the target
questions, record whether we are cited and who beat us. Pluggable — `RecordedProbe` runs in
CI with no key, `LiveProbe` behind `GEO_PROBE_LIVE`. GEO tactics are unstable and
vendor-opaque, so measure the outcome rather than trusting current folklore.

**It refuses to report a citation rate below 5 answered samples.** The first run showed
`rate=1.0` off a single question; a dashboard reporting 100% from n=1 is worse than
reporting nothing.

## Defects found by running it

Every one of these surfaced from execution, not review.

1. **Real citations, wrong topic.** Europe PMC returned a 1984 chromosome-5 deletion paper
   for a mewing query. Genuine PMID, genuine title — so an existence check passes it and it
   ships as a citation. No hallucination involved. *Fixed:* deterministic relevance gate
   between "resolves" and "entails".
2. **Tier inflation.** `"Journal Article"` appears on nearly every record and my mapping
   promoted it to OBSERVATIONAL, so 1984 conference proceedings scored as evidence.
   *Fixed:* removed it from the mapping; added design-marker detection over title/abstract.
3. **The fix over-corrected.** The single most probative paper (midpalatal suture maturation
   by CBCT) was dropped — anchors matched but a conjunctive lexical floor rejected it, because
   the claim says *maxilla* and the literature says *midpalatal*. *Fixed:* anchor matches are
   sufficient, not merely necessary.
4. **The blocking gate failed open.** Lint printed `BLOCKED`, then the run published anyway —
   the retry loop `break`s on pass but fell through on exhaustion. *Fixed:* `for…else` hard
   exit writing `blocked: true`.
5. **A paraphrase evaded the safety floor.** `"habitual tongue posture changes the adult
   skeletal jaw"` scored COSMETIC while the same claim saying *mewing* scored MODERATE — the
   tripwire matched a brand word, not a concept. *Fixed:* patterns match tongue posture and
   orthotropics too.
6. **Page links pointed at pre-merge ids.** After near-dup collapse, `link_page` still linked
   the mined id, so the fan-out silently under-reported. *Fixed:* link the canonical id the
   store returns.
7. **The watcher re-introduced bug 1.** It declared verdict flips on a tier rise detected by
   the relevance gate alone — relevance is not entailment. The invariant lived in
   `pipeline.py` instead of shared code, so a new component didn't inherit it. *Fixed:* a
   tier rise is only a candidate; stage B entailment must confirm before anything regenerates.

## Known limits

- **Semantic paraphrase still escapes dedup.** `jaccard` over stemmed tokens collapses
  reworded claims but not true paraphrases — `c_mewing` and `c_mewing_alt` remain separate
  nodes. Needs embedding nearest-neighbour or an LLM canonicaliser. Asserted as a failing-by-
  design test (`test_known_limit_semantic_paraphrase_still_escapes`) so it can't be forgotten.
- **Entailment verdicts are recorded fixtures without a key.** Full-text entailment only runs
  live.
- **`REFUTED` is currently assigned from the fixture, not derived** from contradicting
  evidence. A real refutation path needs `refutes` edges.
- **GEO citation probing is recorded by default.** Real measurement needs `GEO_PROBE_LIVE=1`
  plus a key, and costs money per run. The recorded fixture covers 1 of 12 target questions,
  which is why the harness reports INSUFFICIENT DATA rather than a number.
- **`has_author` is hardcoded false** in the GEO score. Author credentials and `dateModified`
  are the E-E-A-T signals that matter most for YMYL, and the schema doesn't carry them yet.
- **The stemmer is crude** (suffix stripping + silent-e). Adequate for keying, not for search.

## Architectural backlog

Ranked. Each is a known gap with a diagnosis, not a wishlist item.

### 1. The gate does not check claim faithfulness

The lint verifies a sentence *cites* a claim id. It does not verify the sentence agrees with
that claim's verdict. Reproducible today:

```python
>>> lint("Mewing works for adults [[c_mewing]].", ledger)   # c_mewing is UNSUPPORTED
(True, [])   # PASS
```

The composer can assert the exact opposite of the ledger and the gate waves it through.
This is the defect that most undermines the core thesis — "prose can only say what the
ledger says" is currently enforced syntactically, not semantically.

*Fix:* polarity checking between sentence and cited claim. Deterministic floor first — a
claim with verdict `UNSUPPORTED` or `REFUTED` cited in a sentence carrying no negation or
hedge fails. Then an NLI or LLM entailment check for the cases polarity heuristics miss.
Accuracy is also a GEO input: being wrong is how you stop getting cited.

### 2. Claims have no scope, so they cannot be conditionally true

`Claim` is a sentence plus a verdict. But truth here is almost always conditional:
*"mewing changes jaw structure"* is unsupported **for adults** and arguable **for growing
children**, where orthotropic intervention has a real literature. The current model collapses
that to one verdict and therefore cannot state the honest answer.

*Fix:* give claims PICO structure — population, intervention, comparator, outcome. This pays
three ways at once:

- **Expressiveness** — verdict per scope, which is what a harm-reduction site actually needs
- **Retrieval** — PICO fields generate far better literature queries, fixing relevance at
  source instead of filtering it after the fact
- **Dedup** — canonicalise on the PICO tuple and paraphrases collapse structurally, which
  retires the lexical-overlap limitation below

Largest refactor of the four, and the one with the highest ceiling.

### 3. The ledger is a record, not yet a cache

The amortisation argument above is currently rhetoric. Re-running a topic re-verifies every
claim from scratch:

```
[03] europepmc_search (LIVE): 12 candidate sources
[05] entailment_judge (LLM):  6 sources entail
[07] claim_graph.upsert:      4 rows, 0 new/changed     <- all that work changed nothing
```

*Fix:* consult the graph before verifying. Skip claims already verified inside the freshness
window, cache source records by PMID (the same paper is fetched repeatedly across claims),
and batch entailment calls. Makes the cost story real, and every run faster.

### 4. Authority signals are missing from the schema

`has_author` is hardcoded `false` in the GEO score. There is no author with credentials, no
`dateModified`, no page-level `citation` array. For YMYL these are the heaviest E-E-A-T
signals there are, and the ledger already holds the underlying data.

*Fix:* extend the emitters with `author` (`Person` with `hasCredential`), `reviewedBy`, and
`dateModified` sourced from `verified_at`. Cheapest real GEO win available.

### 5. Source quality is unchecked beyond publication type

Nothing checks retraction status, and Europe PMC indexes preprints (`source: PPR`) that
currently tier identically to peer-reviewed work. Citing a retracted paper on a health site
is a credibility-ending event, and the lookup is cheap and deterministic.

*Fix:* retraction and `source` checks in `core/evidence.py`; preprints capped below
`OBSERVATIONAL` regardless of design markers.

### 6. Verdicts are assigned, not derived

`REFUTED` is currently set from a fixture rather than computed from contradicting evidence,
and the `refutes` edge in the schema is unused. Verdict is effectively "any entailing source
wins", with no policy for conflict — three supporting studies and one refuting meta-analysis
produce the same answer as three supporting studies alone.

*Fix:* make verdict a pure function of the source set under an explicit, versioned
aggregation policy (highest-tier evidence wins; conflict at equal tier yields `CONTESTED`).
Reproducibility is the whole YMYL argument, and a verdict that depends on fixture order
cannot be defended.

### 7. No human sign-off on the highest risk tier

`DANGEROUS` claims publish automatically. For the tier that includes bone smashing and
self-surgery, a review queue is the appropriate control.

*Fix:* hold `risk_tier == DANGEROUS` pages in a `pending_review` state; the gate passes but
publish requires an explicit approval recorded in the audit trail.
