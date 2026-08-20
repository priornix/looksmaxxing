"""SQLite claim graph. The dict in run.py evaporated; this is the system of record.

Dedup by normalised claim key is what makes one node serve many pages - and therefore
what makes corpus-wide regeneration possible when evidence moves.
"""
from __future__ import annotations
import sqlite3, json, hashlib, re, datetime
from pathlib import Path
from core.ledger import Claim, SourceRef

DDL = """
CREATE TABLE IF NOT EXISTS claims(
  id TEXT PRIMARY KEY, key TEXT UNIQUE, canonical_text TEXT, entities TEXT,
  evidence_tier INT, risk_tier INT, verdict TEXT, harm_note TEXT,
  verified_at TEXT, tripwire_hit TEXT);
CREATE TABLE IF NOT EXISTS sources(
  claim_id TEXT, ext_id TEXT, source_db TEXT, title TEXT, year INT,
  pub_type TEXT, resolved INT, entails INT, substrate TEXT, note TEXT,
  PRIMARY KEY(claim_id, ext_id));
CREATE TABLE IF NOT EXISTS pages(
  slug TEXT PRIMARY KEY, title TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS page_claims(
  slug TEXT, claim_id TEXT, PRIMARY KEY(slug, claim_id));
CREATE TABLE IF NOT EXISTS audit(
  ts TEXT, claim_id TEXT, field TEXT, old TEXT, new TEXT, reason TEXT);
"""

_NORM = re.compile(r"[^a-z0-9 ]+")
_STOP = {"the","a","an","of","in","on","and","or","to","for","with","is","are","does","do","can","your","you"}


def _stem(w: str) -> str:
    """Crude suffix stripper. A production graph would use a real stemmer or embedding
    nearest-neighbour; this is enough to collapse changes/change, remodels/remodel."""
    for suf in ("ing", "ies", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    # normalise the silent -e so produce/produced/produces all land on "produc"
    return w[:-1] if w.endswith("e") and len(w) >= 5 else w


def stem_set(text: str) -> set[str]:
    return {_stem(w) for w in _NORM.sub(" ", text.lower()).split()
            if w not in _STOP and len(w) > 2}


def claim_key(text: str) -> str:
    """Two pages phrasing the same assertion must collapse to one node."""
    return hashlib.sha1(" ".join(sorted(stem_set(text))).encode()).hexdigest()[:16]


NEAR_DUP = 0.60


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(len(a | b), 1)


class Store:
    def __init__(self, path: str = "runs/ledger.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)

    def close(self): self.db.commit(); self.db.close()

    # --- writes -----------------------------------------------------------
    def upsert_claim(self, c: Claim, reason: str = "verification run") -> tuple[str, bool]:
        """Returns (claim_id, changed). Dedups on normalised key; audits verdict flips."""
        k = claim_key(c.canonical_text)
        row = self.db.execute("SELECT * FROM claims WHERE key=?", (k,)).fetchone()
        if row is None:
            row = self._near_dup(c.canonical_text)
            if row is not None:
                k = row["key"]   # collapse onto the existing node
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        cid = row["id"] if row else c.id
        changed = False
        if row:
            for field, new in (("verdict", c.verdict.value),
                               ("evidence_tier", int(c.evidence_tier)),
                               ("risk_tier", int(c.risk_tier))):
                if str(row[field]) != str(new):
                    self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)",
                                    (now, cid, field, str(row[field]), str(new), reason))
                    changed = True
        else:
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?)",
                            (now, cid, "created", "", c.verdict.value, reason))
            changed = True
        self.db.execute("""INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET evidence_tier=excluded.evidence_tier,
              risk_tier=excluded.risk_tier, verdict=excluded.verdict,
              harm_note=excluded.harm_note, verified_at=excluded.verified_at,
              tripwire_hit=excluded.tripwire_hit""",
            (cid, k, c.canonical_text, json.dumps(c.entities), int(c.evidence_tier),
             int(c.risk_tier), c.verdict.value, c.harm_note, c.verified_at or now, c.tripwire_hit))
        for s in c.sources:
            self.db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)",
                (cid, s.ext_id, s.source_db, s.title, s.year, s.pub_type,
                 int(s.resolved), None if s.entails is None else int(s.entails),
                 s.substrate.value, s.note))
        self.db.commit()
        return cid, changed

    def _near_dup(self, text: str):
        """Exact stem-key misses paraphrase. Overlap catches the rest of what lexical can.
        Genuine semantic paraphrase ("mewing remodels the maxilla" vs "habitual tongue
        posture changes the jaw") still escapes this - that needs embedding nearest-neighbour
        or an LLM canonicaliser, which is the documented next step for the graph."""
        target = stem_set(text)
        best, best_score = None, 0.0
        for r in self.db.execute("SELECT * FROM claims"):
            sc = jaccard(target, stem_set(r["canonical_text"]))
            if sc > best_score:
                best, best_score = r, sc
        return best if best_score >= NEAR_DUP else None

    def link_page(self, slug: str, title: str, claim_ids: list[str]):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self.db.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?)", (slug, title, now))
        for cid in claim_ids:
            self.db.execute("INSERT OR IGNORE INTO page_claims VALUES(?,?)", (slug, cid))
        self.db.commit()

    # --- reads ------------------------------------------------------------
    def pages_for_claim(self, claim_id: str) -> list[str]:
        return [r["slug"] for r in self.db.execute(
            "SELECT slug FROM page_claims WHERE claim_id=?", (claim_id,))]

    def stale_claims(self, older_than_days: int) -> list[sqlite3.Row]:
        cut = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=older_than_days)).isoformat(timespec="seconds")
        return list(self.db.execute("SELECT * FROM claims WHERE verified_at < ?", (cut,)))

    def all_claims(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM claims ORDER BY risk_tier DESC"))

    def history(self, claim_id: str) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM audit WHERE claim_id=? ORDER BY ts", (claim_id,)))

    def counts(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]
        return {"claims": q("SELECT COUNT(*) FROM claims"),
                "sources": q("SELECT COUNT(*) FROM sources"),
                "pages": q("SELECT COUNT(*) FROM pages"),
                "audit": q("SELECT COUNT(*) FROM audit")}
