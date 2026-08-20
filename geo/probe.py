"""GEO citation measurement. You cannot optimise what you do not measure.

GEO tactics are unstable and vendor-opaque, so this measures the outcome that actually
matters - does an assistant cite us when asked the target question - rather than trusting
current best-practice folklore.

Same seam as agents/steps.py: a recorded backend so it runs in CI with no key or network,
a live backend behind an env var.
"""
from __future__ import annotations
import os, json, datetime, re
from pathlib import Path
from typing import Protocol

FIX = Path(__file__).resolve().parent.parent / "fixtures"
SITE = "looksmaxxing.guide"
MIN_SAMPLES = 5   # below this a citation rate is noise, not a metric
LIVE = bool(os.getenv("GEO_PROBE_LIVE") and os.getenv("ANTHROPIC_API_KEY"))


class ProbeResult(dict):
    """question -> cited? -> who else was cited."""


class Probe(Protocol):
    def ask(self, question: str) -> tuple[str, list[str]]:
        """Returns (answer_text, cited_domains)."""


class RecordedProbe:
    """Replays captured assistant answers so the metric is reproducible in CI."""
    def __init__(self, path: Path | None = None):
        p = path or FIX / "geo_probes.json"
        self.data = json.loads(p.read_text()) if p.exists() else {}

    def ask(self, question: str) -> tuple[str, list[str]]:
        rec = self.data.get(question)
        if rec is None:
            return "", []
        return rec.get("answer", ""), rec.get("cited_domains", [])


class LiveProbe:
    """Queries a real assistant with web search enabled. Costs money; needs a key."""
    def __init__(self, model: str = "anthropic:claude-opus-5"):
        from pydantic_ai import Agent
        self.agent = Agent(model, output_type=str, system_prompt=(
            "Answer the user's question as you normally would for someone researching it. "
            "Cite the sources you drew on, listing their domains explicitly."))

    def ask(self, question: str) -> tuple[str, list[str]]:
        out = self.agent.run_sync(question).output
        domains = sorted(set(re.findall(r"\b([a-z0-9-]+\.(?:com|org|net|guide|io|co\.uk))\b",
                                        out.lower())))
        return out, domains


def get_probe() -> Probe:
    return LiveProbe() if LIVE else RecordedProbe()


def run_probes(questions: list[str], probe: Probe | None = None, site: str = SITE) -> dict:
    """The GEO metric: share of target questions where we are cited, and by whom we're beaten."""
    probe = probe or get_probe()
    rows, competitors = [], {}
    for q in questions:
        answer, domains = probe.ask(q)
        cited = any(site in d for d in domains)
        for d in domains:
            if site not in d:
                competitors[d] = competitors.get(d, 0) + 1
        rows.append({"question": q, "cited": cited, "domains": domains,
                     "answered": bool(answer)})
    asked = [r for r in rows if r["answered"]]
    coverage = len(asked) / max(len(questions), 1)
    # A rate over n=1 reads as 100% and means nothing. Refuse to publish a headline
    # number without enough samples - a confident wrong metric is worse than a gap.
    enough = len(asked) >= MIN_SAMPLES
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if LIVE else "recorded",
        "questions": len(questions), "answered": len(asked),
        "coverage": round(coverage, 3),
        "sufficient_data": enough,
        "citation_rate": (round(sum(r["cited"] for r in asked) / len(asked), 3)
                          if enough else None),
        "top_competitors": sorted(competitors.items(), key=lambda kv: -kv[1])[:5],
        "rows": rows,
    }


def questions_for(claims: list[dict]) -> list[str]:
    """Target questions come from the claims themselves - these are what people ask
    assistants, phrased the way they actually phrase them."""
    # A mechanical "Does <declarative>?" transform produces "Does mewing remodels..." -
    # ungrammatical questions measure nothing. Stick to forms that stay grammatical, plus
    # the colloquial entity forms people actually type into an assistant.
    qs, seen = [], set()
    for c in claims:
        t = c["canonical_text"].rstrip(".")
        for q in (f"Is it true that {t[0].lower() + t[1:]}?" if not t.split(" ")[0].isupper()
                  else f"Is it true that {t}?",):
            if q not in seen:
                seen.add(q); qs.append(q)
        for e in c.get("entities", [])[:1]:
            for q in (f"does {e} actually work?", f"is {e} real or a myth?"):
                if q not in seen:
                    seen.add(q); qs.append(q)
    return qs[:12]
