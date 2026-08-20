# Claim Ledger Pipeline — Architecture

**looksmaxxing.guide** · evidence-led content generation for a niche dominated by misinformation

---

## Thesis

**Content is a projection of a verified claim graph, not an artifact in itself.**

Most content pipelines write prose, then fact-check it. That ordering is why they leak: the
prose exists before the evidence does, so verification is a filter applied to something that
already wants to be published. This inverts it. Nothing gets written that isn't already a
verified row in a claim ledger. The composer cannot assert a fact — it can only render rows
that passed verification, and a deterministic gate refuses to publish prose containing
anything else.

The LLM is boxed by code, not instructed by prompt.

---

## Layers

```
┌─ L3  CONTROL ────────────────────────────────────────────────────────────┐
│  model router · evals on gold set · immutable audit log · DSPy optimizer  │
└──────────────────────────────────────────────────────────────────────────┘
        ▲                                                          ▲
┌─ L2  AGENTS ─────────────────────────────────────────────────────────────┐
│                                                                           │
│  Scout ─▶ Claim Miner ─▶ Triage ─▶ Evidence ─▶ Entailment Judge           │
│  (cron)     (LLM)     (LLM+TRIPWIRE)  (det.)     (frontier LLM)           │
│                                                        │                  │
│                                                        ▼ only path        │
│                                              ┌──────────────────────┐     │
│  Composer ◀──── back-edge: unmapped ───────  │  write verified row  │     │
│     │           assertion                    └──────────────────────┘     │
│     ▼                                                                     │
│  Coverage Lint  (DETERMINISTIC · BLOCKING · FAIL-CLOSED) ─▶ Publish ─▶ LD │
│                                                                           │
│  Watcher (cron) ─▶ re-entail affected nodes ─▶ regeneration fan-out       │
└──────────────────────────────────────────────────────────────────────────┘
        ▲
┌─ L1  CLAIM GRAPH  (system of record) ────────────────────────────────────┐
│  Claim{ id, canonical_text, entities, evidence_tier, risk_tier,          │
│         verdict, harm_note, sources[], verified_at, verified_against }    │
│  Edges: supports · refutes · supersedes · duplicate-of                    │
│  Pages reference claim ids — one node serves many pages                   │
└──────────────────────────────────────────────────────────────────────────┘
        ▲
┌─ L0  EVIDENCE SUBSTRATE  (deterministic, keyless, verified live) ────────┐
│  Europe PMC (full text) · openFDA (labels + FAERS) · PubMed              │
│  pub_type + design markers ──▶ evidence_tier   [mapping, not judgment]    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## L0 — Evidence substrate

Deterministic, keyless, live. **Better data beats a better model**, and this layer is where
that leverage sits.

| Source | Role | Why |
|---|---|---|
| **Europe PMC** | primary literature | Returns *full text* for open-access papers, not just abstracts. Entailment against a 200-word abstract is guesswork; against full text it is a real check. Keyless. |
| **openFDA** | drug labels + FAERS adverse events | This niche is saturated with pharma — finasteride, minoxidil, tretinoin, isotretinoin, GLP-1s. Primary regulatory harm data is harm-reduction no competitor is doing. Keyless. |
| **PubMed** | fallback / id resolution | Broadest index when Europe PMC misses. |

`evidence_tier` is derived from publication type **and** design markers detected in
title/abstract. It is a lookup table, never a model judgment:

```
SYNTHESIS (5)     systematic review · meta-analysis
TRIAL (4)         randomized controlled trial · clinical trial
OBSERVATIONAL (3) cohort · cross-sectional · case-control · retrospective
MECHANISTIC (2)   in vitro · animal model · cadaver · finite element
ANECDOTE (1)      case report · case series · survey · qualitative
NONE (0)          no design signal present
```

`"Journal Article"` deliberately maps to nothing — it appears on nearly every record and
carries no design information. Treating it as a tier is how tier inflation happens.

---

## L1 — Claim graph (system of record)

The durable asset. A claim is a node; pages reference claim ids. Because the same claim
recurs across many articles ("X changes adult bone structure", "Y is safe without a
prescription"), deduplication means **one node serves many pages** — which is what makes
corpus-wide correction possible.

```python
class Claim(BaseModel):
    id: str
    canonical_text: str
    entities: list[str]
    evidence_tier: EvidenceTier      # derived, not judged
    risk_tier: RiskTier              # LLM, floored by tripwire
    verdict: Verdict                 # supported | contested | refuted | unsupported
    harm_note: str | None
    sources: list[SourceRef]
    verified_at: str
    tripwire_hit: str | None
```

**Invariants are enforced in the type system — invalid content is unconstructable:**

- `risk_tier >= HIGH` requires a `harm_note`
- `verdict == SUPPORTED` requires ≥1 source that both **resolves** and **entails**
- `verdict == SUPPORTED` cannot coexist with `evidence_tier == NONE`

`SourceRef.verified_against` records which substrate the entailment check actually read
(`fulltext` / `abstract` / `label` / `faers`). A claim verified against an abstract is
weaker evidence than one verified against full text, and the ledger must know the difference.

**Edges:** `supports` · `refutes` · `supersedes` · `duplicate-of`. `supersedes` is what lets
a 2026 meta-analysis retire a 2019 conclusion without deleting the audit history.

---

## L2 — Agents

| Agent | Trigger | Kind | Contract |
|---|---|---|---|
| **Scout** | cron | LLM | Topic and question discovery from search + AI-search gaps |
| **Claim Miner** | topic queued | LLM, structured | Question → atomic claim candidates + entities + queries |
| **Triage** | miner output | LLM **+ deterministic floor** | Assign `risk_tier`; tripwire may override upward |
| **Evidence** | triage output | deterministic | Query L0, return candidate sources |
| **Relevance Gate** | evidence output | **deterministic** | Anchor entities must appear in source; drop before entailment |
| **Entailment Judge** | relevance output | frontier LLM | Does this source *support* this claim? Records substrate |
| **Composer** | ledger complete | LLM, constrained | Render prose from ledger rows; cannot introduce facts |
| **Coverage Lint** | compose output | **deterministic, blocking** | Every assertive sentence maps to a claim id |
| **Publisher** | lint PASS only | deterministic | HTML + JSON-LD ClaimReview |
| **Watcher** | cron | LLM + orchestration | New evidence → re-entail → fan out regeneration |

### Handoffs that matter

- **Triage → tripwire floor is asymmetric.** An LLM may escalate a risk tier; it may never
  de-escalate below the regex floor. Safety-critical decisions never depend on a model
  being in a good mood. On the live mewing run this caught 2 of 4 claims the model had
  rated "cosmetic" — including bone smashing.

- **Entailment is two-stage, and the stages differ in kind.**
  Stage A: does the identifier resolve? — deterministic HTTP.
  Stage A2: does the claim's vocabulary appear in the source? — deterministic relevance gate.
  Stage B: does the source *support* the claim? — frontier LLM against full text.
  Conflating A and B is the failure that kills sites like this: **a citation existing is not
  a citation supporting.**

- **Lint → Composer back-edge.** The only real loop in the inner pipeline. Lint failures
  return findings with rule names (`unbacked_absolute`, `uncited_assertion`,
  `unrenderable_claim`, `overclaim_vs_tier`) and the composer redrafts against them.

- **Entailment → Ledger is the only write path.** Nothing else may create a verified row.

- **Publish is fail-closed.** Reachable only through a passing lint. If drafts are exhausted
  without a pass, the run hard-exits and records `blocked: true`. A fail-open gate is worse
  than no gate, because the trace log claims a safety property that isn't there.

---

## L3 — Control plane

**Model routing.** Cost per article matters at SEO volume, so route by stakes:

- *deterministic* wherever possible — L0 lookups, tripwires, relevance, lint. No model at all.
- *cheap tier* (Haiku, or open weights) for high-volume schema-shaped work — claim extraction,
  entity tagging, citability reformatting.
- *frontier tier* for entailment and risk triage. Never compromised. These are the
  safety-critical judgments.

**Open-weights note.** Hermes-class models are worth considering on the cheap tier for a
reason that isn't cost: safety-tuned frontier models sometimes deflect or over-hedge on
accurate harm-reduction copy about finasteride sourcing, isotretinoin, or aggressive cutting.
Over-hedging is a *worse* safety outcome here — the reader goes to a forum instead. Rent it
through OpenRouter rather than self-hosting.

**Evals.** Entailment accuracy against a labeled gold set; coverage-lint pass rate;
blocked-claim recall. These are real metrics, which is what makes DSPy applicable — it can
optimize the extraction and entailment prompts against them rather than against taste.

**Audit log.** Immutable verdict history per claim node. Required for YMYL defensibility:
you must be able to answer *why was this claim blocked in March* and get the same answer in
September.

---

## Where Hermes fits, and where it must not

Hermes Agent (Nous Research) is a strong fit for **Scout and Watcher**: cron jobs, persistent
cross-session memory, and skills it accretes from recurring patterns map directly onto
"re-audit the claim graph on a schedule" and "the same claim archetypes recur across hundreds
of pages." It replaces Temporal/Prefect in this design and covers more ground.

It must never touch **the gate**. Self-improving components and YMYL auditability are
incompatible: a component that mutates its own verification behavior cannot give you the same
answer in September that it gave in March. The gate stays deterministic, versioned, and
diffable.

---

## Why not one-shot

| | Typical one-shot | This |
|---|---|---|
| **Unit of work** | article | claim |
| **State between runs** | none — every run from scratch | claim graph accrues |
| **Fact provenance** | model memory | resolved source + entailment record + substrate |
| **Fabricated citation** | ships | blocked at stage A / A2 / B |
| **New study lands** | 400 pages silently stale | query graph → regenerate only affected |
| **Safety mechanism** | prompt says "be careful" | type validators + tripwire floor + blocking lint |
| **Cost of article #400** | same as #1 | lower — claims already verified, reused |
| **Audit trail** | "the model wrote it" | verdict history per claim node |
| **AI-search citability** | hope | ledger rows are natively atomic → JSON-LD ClaimReview |

### The structural argument

One-shot pipelines have **zero loops**. This has three:

1. **Inner** — Composer ↔ Lint. Correctness of a single page.
2. **Outer** — Watcher → regeneration fan-out. Freshness of the whole corpus.
3. **Meta** — evals → DSPy over extraction and entailment prompts. Quality of the pipeline itself.

### The economic argument

One-shot content cost is **linear**: article 400 costs what article 1 did, and its facts rot
silently. This is **sublinear and self-healing** — article 400 shares verified claims with the
previous 399, so verification amortizes, and when evidence moves the graph names exactly which
pages to fix.

For a site whose entire market position is *"the one looksmaxxing source that isn't
misinformation,"* that last property is the moat. A competitor mass-producing GPT articles
cannot retroactively correct 400 pages when a meta-analysis drops. This can, because every
page declares which claim ids it asserts.

### The citability argument

AI-search discovery is a different problem from ranking. LLM retrievers extract and cite
statements that are atomic, self-contained, entity-named and dated. A claim ledger row is
already exactly that shape. Emitting it as `schema.org/ClaimReview` means citability is a
byproduct of the architecture rather than a tactic bolted onto it.

---

## Implementation shape

```
core/          pure functions + Pydantic models — no framework import
  ledger.py       Claim, EvidenceTier, RiskTier, Verdict + invariant validators
  tripwires.py    deterministic danger floor
  evidence.py     Europe PMC + openFDA, live
  relevance.py    anchor-entity gate + design-marker tiering
  lint.py         coverage gate, blocking
  jsonld.py       ClaimReview emitter
agents/        thin PydanticAI adapters over core
run.py         orchestrator, trace capture, fail-closed publish
tests/         8/8 green with no API key and no network
```

The domain logic deliberately does not import the agent framework. PydanticAI wraps only the
three LLM steps; if it were removed, `core/` and every invariant would still run. That
separation is the point — the framework is a seam, not an owner.
