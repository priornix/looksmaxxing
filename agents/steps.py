"""PydanticAI seam over the LLM steps.

With ANTHROPIC_API_KEY set, these run live against Claude. Without one they run through
TestModel with recorded outputs - which still exercises the real Agent machinery and real
schema validation, so the code path under test is the one that ships.

core/ deliberately does not import this module. Delete agents/ and every invariant still runs.
"""
from __future__ import annotations
import os, json
from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

LIVE = bool(os.getenv("ANTHROPIC_API_KEY"))
MODEL_ID = os.getenv("LEDGER_MODEL", "anthropic:claude-opus-5")
FIX = Path(__file__).resolve().parent.parent / "fixtures"


# ---------- schemas ---------------------------------------------------------
class MinedClaim(BaseModel):
    """One atomic, independently checkable assertion."""
    id: str = Field(description="stable snake_case id, e.g. c_mewing")
    text: str = Field(description="the assertion as a single declarative sentence")
    entities: list[str] = Field(description="anchor terms that MUST appear in a relevant source")
    llm_risk_tier: str = Field(description="COSMETIC | MODERATE | HIGH | DANGEROUS")
    queries: list[str] = Field(description="1-3 literature search queries")
    harm_note: str | None = Field(default=None, description="required if risk is HIGH or above")


class Draft(BaseModel):
    """Structured so the recorded path exercises the same output tool as the live one."""
    markdown: str = Field(description="the article body; cite every assertion as [[claim_id]]")


class Entailment(BaseModel):
    entails: bool = Field(description="does this source actually support the claim?")
    substrate: str = Field(description="fulltext | abstract")
    why: str = Field(description="one sentence citing what in the source decides it")


# ---------- prompts ---------------------------------------------------------
MINER_SP = """You extract atomic factual claims for an evidence-led men's grooming site.
Each claim must be independently checkable against biomedical literature.
Split compound assertions. Never soften a claim to make it easier to verify - extract the
strong version the audience actually believes, so it can be tested and, if false, refuted.
Anchor entities must be terms that would literally appear in a relevant paper (use clinical
vocabulary: 'midpalatal suture', not 'jaw bone')."""

ENTAIL_SP = """You judge whether a source supports a specific claim. You are the last line
before a health claim ships. A source that is merely topically adjacent does NOT entail.
A source about a different population (children vs adults) does NOT entail.
Say entails=false unless the source's own findings bear directly on the claim as stated."""


# ---------- agents ----------------------------------------------------------
def _model(recorded):
    return MODEL_ID if LIVE else TestModel(custom_output_args=recorded)


def _slugify(t: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in t.lower()).strip("-")[:48]


def mine_claims(topic: str) -> list[MinedClaim]:
    """Recorded fixtures are keyed per topic so multi-article runs are reproducible."""
    per_topic = FIX / f"claims_mined.{_slugify(topic)}.json"
    src = per_topic if per_topic.exists() else FIX / "claims_mined.json"
    rec = json.loads(src.read_text())
    agent = Agent(_model(rec), output_type=list[MinedClaim], system_prompt=MINER_SP)
    return agent.run_sync(f"Topic: {topic}\nExtract the claims a reader is really asking about.").output


def judge_entailment(claim_text: str, title: str, body: str, substrate: str,
                     recorded: dict | None = None) -> Entailment:
    rec = recorded or {"entails": False, "substrate": substrate, "why": "no recorded verdict"}
    agent = Agent(_model(rec), output_type=Entailment, system_prompt=ENTAIL_SP)
    return agent.run_sync(
        f"CLAIM: {claim_text}\n\nSOURCE ({substrate}): {title}\n\n{body[:6000]}").output


def compose(topic: str, ledger_rows: list[dict], findings: list[dict] | None = None) -> str:
    """Renders prose from ledger rows only. Findings from the lint gate drive a redraft."""
    draft_n = 2 if findings else 1
    rec = {"markdown": (FIX / f"draft_v{draft_n}.md").read_text()}
    agent = Agent(_model(rec), output_type=Draft, system_prompt=(
        "You write from a claim ledger. You may not assert anything not present as a row. "
        "Cite every assertive sentence as [[claim_id]]. Match language strength to "
        "evidence_tier: never use absolutes for a weak tier. State refuted and unsupported "
        "claims plainly - saying 'there is no evidence for X' is the point of the page."))
    prompt = f"TOPIC: {topic}\nLEDGER:\n{json.dumps(ledger_rows, indent=1)}"
    if findings:
        prompt += f"\n\nYour previous draft was BLOCKED by the coverage gate. Fix:\n{json.dumps(findings, indent=1)}"
    return agent.run_sync(prompt).output.markdown
