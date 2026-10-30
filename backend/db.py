"""SQLite persistence.

Design rules (from the code audit):
  * every agent pass is a `discovery_runs` row; candidates and evidence point at it
  * a candidate is stable per (brief, Qloo entity): reruns update it, never delete it,
    so vetoes and slot picks survive
  * slots are enforced by the database: one primary/backup/alternative per brief,
    and a vetoed candidate can never hold a slot
  * finalized booking briefs are immutable, versioned snapshots behind a random token
  * multi-step writes run in one transaction
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from . import config, rules

BRIEF_STATUSES = ("pending", "resolving", "needs_confirmation", "discovering", "investigating",
                  "ready", "ready_low_evidence", "failed", "finalized")
ACTIVE_STATUSES = ("pending", "resolving", "discovering", "investigating")
TERMINAL_STATUSES = ("needs_confirmation", "ready", "ready_low_evidence", "failed", "finalized")
SLOTS = ("primary", "backup", "alternative")

SCHEMA_VERSION = 6
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS briefs (
  id                      TEXT PRIMARY KEY,
  headliner_name          TEXT NOT NULL,
  headliner_qloo_id       TEXT,
  headliner_resolved_name TEXT,
  resolution_options      TEXT,
  city                    TEXT NOT NULL,
  venue_capacity          INTEGER NOT NULL CHECK (venue_capacity BETWEEN 50 AND 20000),
  show_date               TEXT,
  budget                  REAL CHECK (budget IS NULL OR budget >= 0),
  status                  TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ({",".join(repr(s) for s in BRIEF_STATUSES)})),
  status_reason           TEXT,
  widen_level             INTEGER NOT NULL DEFAULT 0 CHECK (widen_level BETWEEN 0 AND 2),
  use_popularity_cap      INTEGER NOT NULL DEFAULT 1 CHECK (use_popularity_cap IN (0,1)),
  weights                 TEXT,
  headliner_tags          TEXT,
  demo_mode               INTEGER NOT NULL DEFAULT 0 CHECK (demo_mode IN (0,1)),
  compare_parent_id       TEXT REFERENCES briefs(id),
  created_at              TEXT NOT NULL,
  updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_runs (
  id              TEXT PRIMARY KEY,
  brief_id        TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  run_no          INTEGER NOT NULL,
  trigger         TEXT NOT NULL,
  start_level     INTEGER NOT NULL,
  final_level     INTEGER,
  use_popularity_cap INTEGER NOT NULL,
  outcome         TEXT NOT NULL DEFAULT 'running'
                  CHECK (outcome IN ('running','ready','ready_low_evidence','needs_confirmation','failed')),
  outcome_reason  TEXT,
  candidate_count INTEGER,
  proven_count    INTEGER,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  UNIQUE (brief_id, run_no)
);

CREATE TABLE IF NOT EXISTS candidates (
  id                TEXT PRIMARY KEY,
  brief_id          TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  qloo_id           TEXT NOT NULL,
  artist_name       TEXT NOT NULL,
  found_level       INTEGER NOT NULL,
  found_scope       TEXT NOT NULL,
  city_rank         INTEGER,
  city_affinity     REAL,
  popularity        REAL,
  monthly_listeners INTEGER,
  listeners_flag    TEXT,
  place_of_birth    TEXT,
  genres            TEXT,
  image_url         TEXT,
  noloc_rank        INTEGER,
  noloc_affinity    REAL,
  rank_delta        INTEGER,
  new_in_city       INTEGER NOT NULL DEFAULT 0,
  found_via         TEXT NOT NULL DEFAULT 'qloo' CHECK (found_via IN ('qloo','qloo_deeper','scene','llm_local')),
  scene_seed        TEXT,
  tag_overlap       REAL,
  shared_tags       TEXT,
  entity_checked    INTEGER NOT NULL DEFAULT 0,
  local_status      TEXT,
  local_note        TEXT,
  taste_score       REAL,
  local_score       REAL,
  practical_score   REAL,
  practical_note    TEXT,
  total_score       REAL,
  status            TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','vetoed')),
  slot              TEXT CHECK (slot IS NULL OR slot IN ('primary','backup','alternative')),
  excluded_reason   TEXT,
  first_run_id      TEXT NOT NULL REFERENCES discovery_runs(id),
  last_run_id       TEXT NOT NULL REFERENCES discovery_runs(id),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  evidence_run_id TEXT,
  UNIQUE (brief_id, qloo_id),
  CHECK (NOT (status = 'vetoed' AND slot IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_candidates_brief ON candidates(brief_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_slot ON candidates(brief_id, slot) WHERE slot IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence (
  id           TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  run_id       TEXT NOT NULL REFERENCES discovery_runs(id),
  kind         TEXT NOT NULL CHECK (kind IN ('based_here','formed_here','played_here','context','none',
                                                'based_elsewhere','formed_elsewhere','inactive')),
  status       TEXT NOT NULL CHECK (status IN ('verified','unverified','rejected')),
  reason       TEXT NOT NULL,
  method       TEXT NOT NULL CHECK (method IN ('llm','rule','qloo','musicbrainz')),
  claim        TEXT NOT NULL,
  quote        TEXT,
  excerpt      TEXT,
  source_title TEXT,
  source_url   TEXT,
  source_label TEXT NOT NULL CHECK (source_label IN ('qloo','public','musicbrainz')),
  scope        TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  source_year  INTEGER,
  place        TEXT,
  CHECK (source_url IS NOT NULL OR source_label = 'qloo'),
  CHECK (status = 'rejected' OR kind != 'none'),
  CHECK (kind != 'context' OR status = 'unverified')
);
CREATE INDEX IF NOT EXISTS idx_evidence_candidate ON evidence(candidate_id);

CREATE TABLE IF NOT EXISTS agent_events (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  brief_id TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  run_id   TEXT,
  ts       TEXT NOT NULL,
  type     TEXT NOT NULL,
  message  TEXT NOT NULL,
  data     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_brief ON agent_events(brief_id);

CREATE TABLE IF NOT EXISTS qloo_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  brief_id      TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  run_id        TEXT,
  purpose       TEXT NOT NULL,
  endpoint      TEXT NOT NULL,
  params        TEXT NOT NULL,
  response_body TEXT,
  status_code   INTEGER,
  duration_ms   INTEGER,
  error         TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negotiations (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  brief_id   TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  action     TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS booking_briefs (
  id          TEXT PRIMARY KEY,
  brief_id    TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  version     INTEGER NOT NULL,
  share_token TEXT NOT NULL UNIQUE,
  snapshot    TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  UNIQUE (brief_id, version)
);

CREATE TABLE IF NOT EXISTS llm_comparisons (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  brief_id   TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  llm_raw    TEXT,
  result     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class NotFound(LookupError):
    pass


class InvalidTransition(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """One transaction: commits on success, rolls back on any exception."""
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _read(sql: str, args: tuple = ()) -> list[dict]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def init() -> None:
    c = _conn()
    try:
        c.execute("PRAGMA journal_mode = WAL")
        if c.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            # Schema v2/v3 changed the evidence model. Older briefs were built on unverified evidence,
            # so they are dropped rather than migrated.
            c.execute("PRAGMA foreign_keys = OFF")
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                c.execute(f"DROP TABLE IF EXISTS {t}")
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def recover_interrupted() -> int:
    """Briefs left mid-run by a server restart get a clear failure state."""
    with tx() as c:
        cur = c.execute(
            f"UPDATE briefs SET status='failed', status_reason=?, updated_at=? "
            f"WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
            ("Interrupted by a server restart. Use 'Run again' to continue.", now(), *ACTIVE_STATUSES))
        c.execute("UPDATE discovery_runs SET outcome='failed', outcome_reason='interrupted', finished_at=? "
                  "WHERE outcome='running'", (now(),))
        return cur.rowcount


# ── briefs ────────────────────────────────────────────────────────────────
_BRIEF_COLUMNS = {"headliner_qloo_id", "headliner_resolved_name", "resolution_options", "status",
                  "status_reason", "widen_level", "use_popularity_cap", "weights", "headliner_tags"}


def insert_brief(inp: Any, demo_mode: bool, *, headliner_qloo_id: Optional[str] = None,
                 headliner_resolved_name: Optional[str] = None, compare_parent_id: Optional[str] = None,
                 weights: Optional[dict] = None, use_popularity_cap: Optional[bool] = None) -> str:
    bid = str(uuid.uuid4())
    ts = now()
    cap = inp.use_popularity_cap if use_popularity_cap is None else use_popularity_cap
    with tx() as c:
        c.execute(
            """INSERT INTO briefs (id, headliner_name, headliner_qloo_id, headliner_resolved_name, city,
                 venue_capacity, show_date, budget, use_popularity_cap, weights, demo_mode,
                 compare_parent_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bid, inp.headliner, headliner_qloo_id, headliner_resolved_name, inp.city, inp.venue_capacity,
             inp.show_date, inp.budget, int(bool(cap)),
             json.dumps(rules.normalize_weights(weights)), int(demo_mode), compare_parent_id, ts, ts))
    return bid


def get_brief(bid: str) -> Optional[dict]:
    rows = _read("SELECT * FROM briefs WHERE id=?", (bid,))
    if not rows:
        return None
    b = rows[0]
    b["weights"] = json.loads(b["weights"]) if b["weights"] else dict(rules.DEFAULT_WEIGHTS)
    b["resolution_options"] = json.loads(b["resolution_options"]) if b["resolution_options"] else []
    b["headliner_tags"] = json.loads(b["headliner_tags"]) if b.get("headliner_tags") else []
    return b


def require_brief(bid: str) -> dict:
    b = get_brief(bid)
    if not b:
        raise NotFound("Brief not found")
    return b


def update_brief(bid: str, **kw: Any) -> None:
    bad = set(kw) - _BRIEF_COLUMNS
    if bad:
        raise ValueError(f"cannot update brief columns {sorted(bad)}")
    if not kw:
        return
    if "status" in kw and kw["status"] not in BRIEF_STATUSES:
        raise ValueError(f"invalid status {kw['status']}")
    for k in ("resolution_options", "weights", "headliner_tags"):
        if k in kw and not isinstance(kw[k], (str, type(None))):
            kw[k] = json.dumps(kw[k])
    cols = ", ".join(f"{k}=?" for k in kw)
    with tx() as c:
        c.execute(f"UPDATE briefs SET {cols}, updated_at=? WHERE id=?", (*kw.values(), now(), bid))


def set_status(bid: str, status: str, reason: Optional[str] = None) -> None:
    update_brief(bid, status=status, status_reason=reason)


def log_negotiation(bid: str, action: str, payload: dict) -> None:
    with tx() as c:
        c.execute("INSERT INTO negotiations (brief_id, action, payload, created_at) VALUES (?,?,?,?)",
                  (bid, action, json.dumps(payload), now()))


# ── events, runs, Qloo call log ────────────────────────────────────────────
def add_event(bid: str, run_id: Optional[str], type_: str, message: str, data: Optional[dict] = None) -> None:
    with tx() as c:
        c.execute("INSERT INTO agent_events (brief_id, run_id, ts, type, message, data) VALUES (?,?,?,?,?,?)",
                  (bid, run_id, now(), type_, message, json.dumps(data) if data else None))


def list_events(bid: str, limit: int = 200) -> list[dict]:
    rows = _read("SELECT id, run_id, ts, type, message, data FROM agent_events WHERE brief_id=? "
                 "ORDER BY id DESC LIMIT ?", (bid, limit))
    for r in rows:
        r["data"] = json.loads(r["data"]) if r["data"] else None
    return list(reversed(rows))


def start_run(bid: str, trigger: str, start_level: int, use_cap: bool) -> str:
    rid = str(uuid.uuid4())
    with tx() as c:
        n = c.execute("SELECT COALESCE(MAX(run_no),0)+1 FROM discovery_runs WHERE brief_id=?", (bid,)).fetchone()[0]
        c.execute("INSERT INTO discovery_runs (id, brief_id, run_no, trigger, start_level, use_popularity_cap, "
                  "started_at) VALUES (?,?,?,?,?,?,?)", (rid, bid, n, trigger, start_level, int(use_cap), now()))
    return rid


def finish_run(rid: str, outcome: str, reason: Optional[str] = None, final_level: Optional[int] = None,
               candidate_count: Optional[int] = None, proven_count: Optional[int] = None) -> None:
    with tx() as c:
        c.execute("UPDATE discovery_runs SET outcome=?, outcome_reason=?, final_level=?, candidate_count=?, "
                  "proven_count=?, finished_at=? WHERE id=?",
                  (outcome, reason, final_level, candidate_count, proven_count, now(), rid))


def list_runs(bid: str) -> list[dict]:
    return _read("SELECT * FROM discovery_runs WHERE brief_id=? ORDER BY run_no", (bid,))


def make_qloo_recorder(bid: str, run_id: Optional[str]):
    def rec(purpose, url, params, body, status, duration_ms=None, error=None):
        with tx() as c:
            c.execute("INSERT INTO qloo_calls (brief_id, run_id, purpose, endpoint, params, response_body, "
                      "status_code, duration_ms, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (bid, run_id, purpose, url, json.dumps(params), body, status, duration_ms, error, now()))
    return rec


def list_qloo_calls(bid: str) -> list[dict]:
    rows = _read("SELECT id, run_id, purpose, endpoint, params, status_code, duration_ms, error, created_at "
                 "FROM qloo_calls WHERE brief_id=? ORDER BY id", (bid,))
    for r in rows:
        r["params"] = json.loads(r["params"])
    return rows


# ── candidates ────────────────────────────────────────────────────────────
_CAND_FIELDS = ("artist_name", "city_rank", "city_affinity", "popularity", "monthly_listeners", "listeners_flag",
                "place_of_birth", "genres", "image_url", "noloc_rank", "noloc_affinity", "rank_delta",
                "new_in_city", "found_via", "scene_seed", "tag_overlap", "shared_tags", "entity_checked", "local_status", "local_note",
                "taste_score", "local_score", "practical_score", "practical_note", "total_score", "excluded_reason")


def upsert_candidates(bid: str, run_id: str, cands: list[dict]) -> None:
    """Insert new candidates, update existing ones in place (keeping status and slot),
    and replace their evidence with this run's findings. One transaction."""
    ts = now()
    with tx() as c:
        for cand in cands:
            vals = [json.dumps(cand.get(f) or []) if f in ("genres", "shared_tags")
                    else int(bool(cand.get(f))) if f in ("new_in_city", "entity_checked")
                    else (cand.get(f) or "qloo") if f == "found_via"
                    else cand.get(f) for f in _CAND_FIELDS]
            row = c.execute("SELECT id FROM candidates WHERE brief_id=? AND qloo_id=?",
                            (bid, cand["qloo_id"])).fetchone()
            if row:
                cid = row["id"]
                sets = ", ".join(f"{f}=?" for f in _CAND_FIELDS)
                c.execute(f"UPDATE candidates SET {sets}, last_run_id=?, updated_at=? WHERE id=?",
                          (*vals, run_id, ts, cid))
            else:
                cid = str(uuid.uuid4())
                c.execute(
                    f"INSERT INTO candidates (id, brief_id, qloo_id, found_level, found_scope, "
                    f"{', '.join(_CAND_FIELDS)}, first_run_id, last_run_id, created_at, updated_at) "
                    f"VALUES ({','.join('?' * (len(_CAND_FIELDS) + 9))})",
                    (cid, bid, cand["qloo_id"], cand["found_level"], cand["found_scope"], *vals,
                     run_id, run_id, ts, ts))
            if "evidence" in cand:
                # earlier runs' evidence stays as history; the candidate points at the latest set
                c.execute("UPDATE candidates SET evidence_run_id=? WHERE id=?", (run_id, cid))
                c.execute("DELETE FROM evidence WHERE candidate_id=? AND run_id=?", (cid, run_id))
                for ev in cand["evidence"]:
                    c.execute(
                        "INSERT INTO evidence (id, candidate_id, run_id, kind, status, reason, method, claim, quote, "
                        "excerpt, source_title, source_url, source_label, scope, retrieved_at, source_year, place) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), cid, run_id, ev["kind"], ev["status"], ev["reason"], ev["method"],
                         ev["claim"], ev.get("quote"), ev.get("excerpt"), ev.get("source_title"), ev.get("source_url"),
                         ev["source_label"], ev["scope"], ev["retrieved_at"], ev.get("source_year"), ev.get("place")))
            # a candidate excluded by a filter cannot keep a slot
            if cand.get("excluded_reason"):
                c.execute("UPDATE candidates SET slot=NULL WHERE id=?", (cid,))


def evidence_for(cid: str) -> list[dict]:
    return _read("SELECT e.kind, e.status, e.reason, e.method, e.claim, e.quote, e.excerpt, e.source_title, "
                 "e.source_url, e.source_label, e.scope, e.retrieved_at, e.source_year, e.place FROM evidence e JOIN candidates c "
                 "ON c.id = e.candidate_id AND e.run_id = c.evidence_run_id WHERE e.candidate_id=? "
                 "ORDER BY CASE e.status WHEN 'verified' THEN 0 WHEN 'unverified' THEN 1 ELSE 2 END, "
                 "e.kind = 'context', e.kind != 'based_here'", (cid,))


def get_candidates(bid: str, with_evidence: bool = True) -> list[dict]:
    """Eligible verified locals first, then the rest by score; vetoed and filtered-out last."""
    rows = _read(
        "SELECT c.*, EXISTS (SELECT 1 FROM evidence e WHERE e.candidate_id=c.id AND e.run_id=c.evidence_run_id "
        "AND e.kind='played_here' AND e.status='verified') AS plays_market, "
        "(SELECT MAX(e.source_year) FROM evidence e WHERE e.candidate_id=c.id AND e.run_id=c.evidence_run_id "
        "AND e.status='verified' AND e.kind IN ('based_here','formed_here','played_here')) AS newest_source_year, "
        "(SELECT b.venue_capacity FROM briefs b WHERE b.id=c.brief_id) AS _capacity "
        "FROM candidates c WHERE c.brief_id=? "
        "ORDER BY c.excluded_reason IS NOT NULL, c.status='vetoed', COALESCE(c.local_score,0) >= ? DESC, "
        "c.total_score DESC, c.city_rank", (bid, rules.LOCAL_PROOF_THRESHOLD))
    for r in rows:
        r["genres"] = json.loads(r["genres"]) if r["genres"] else []
        r["shared_tags"] = json.loads(r["shared_tags"]) if r.get("shared_tags") else []
        r["entity_checked"] = bool(r.get("entity_checked"))
        r["new_in_city"] = bool(r["new_in_city"])
        r["plays_market"] = bool(r["plays_market"])
        r["local_proof"] = (r["local_score"] or 0) >= rules.LOCAL_PROOF_THRESHOLD
        r["fit_ok"], r["fit_note"] = rules.fit_evidence(r)
        r["gate"] = rules.booking_gate(r, r.pop("_capacity") or 800)
        r["qualified"] = r["gate"]["passed"]
        if with_evidence:
            r["evidence"] = evidence_for(r["id"])
    # acts that pass the Booking Decision Gate first, then local leads, then the rest by score
    rows.sort(key=lambda r: (r["excluded_reason"] is not None, r["status"] == "vetoed", not r["qualified"],
                             not r["local_proof"], -(r["total_score"] or 0)))
    return rows


def _candidate_in_brief(c: sqlite3.Connection, bid: str, cid: str) -> sqlite3.Row:
    row = c.execute("SELECT * FROM candidates WHERE id=? AND brief_id=?", (cid, bid)).fetchone()
    if not row:
        raise NotFound("Candidate not found in this brief")
    return row


def veto(bid: str, cid: str) -> None:
    with tx() as c:
        _candidate_in_brief(c, bid, cid)
        c.execute("UPDATE candidates SET status='vetoed', slot=NULL, updated_at=? WHERE id=?", (now(), cid))


def unveto(bid: str, cid: str) -> None:
    with tx() as c:
        _candidate_in_brief(c, bid, cid)
        c.execute("UPDATE candidates SET status='proposed', updated_at=? WHERE id=?", (now(), cid))


def set_slot(bid: str, cid: str, slot: str) -> None:
    if slot not in SLOTS:
        raise InvalidTransition(f"Unknown slot '{slot}'")
    with tx() as c:
        row = _candidate_in_brief(c, bid, cid)
        if row["status"] == "vetoed":
            raise InvalidTransition("A vetoed candidate cannot be placed in a slot. Un-veto it first.")
        if row["excluded_reason"]:
            raise InvalidTransition(f"This candidate is filtered out ({row['excluded_reason']}).")
        # free the slot, then move the candidate (a candidate holds at most one slot)
        c.execute("UPDATE candidates SET slot=NULL WHERE brief_id=? AND slot=?", (bid, slot))
        c.execute("UPDATE candidates SET slot=?, updated_at=? WHERE id=?", (slot, now(), cid))


def clear_slot(bid: str, cid: str) -> None:
    with tx() as c:
        _candidate_in_brief(c, bid, cid)
        c.execute("UPDATE candidates SET slot=NULL, updated_at=? WHERE id=?", (now(), cid))


def rescore(bid: str, weights: dict) -> None:
    """Recompute totals with new weights. No API calls needed."""
    w = rules.normalize_weights(weights)
    with tx() as c:
        c.execute("UPDATE briefs SET weights=?, updated_at=? WHERE id=?", (json.dumps(w), now(), bid))
        for r in c.execute("SELECT id, taste_score, local_score, practical_score FROM candidates WHERE brief_id=?",
                           (bid,)).fetchall():
            c.execute("UPDATE candidates SET total_score=? WHERE id=?", (rules.total(dict(r), w), r["id"]))


# ── API shape ─────────────────────────────────────────────────────────────
def fetch_brief(bid: str) -> Optional[dict]:
    b = get_brief(bid)
    if not b:
        return None
    cands = get_candidates(bid)
    versions = _read("SELECT version, share_token, created_at, snapshot FROM booking_briefs WHERE brief_id=? "
                     "ORDER BY version DESC", (bid,))
    for v in versions:  # only what the page needs from each saved version
        snap = json.loads(v.pop("snapshot") or "{}")
        v["kind"] = snap.get("kind")
        v["picks"] = {k: (p or {}).get("artist_name") for k, p in (snap.get("picks") or {}).items()}
        v["picks_provisional"] = (snap.get("decision") or {}).get("picks_provisional", [])
    runs = list_runs(bid)
    live = [c for c in cands if c["status"] != "vetoed" and not c["excluded_reason"]]
    return {
        "id": b["id"],
        "headliner_name": b["headliner_name"],
        "headliner_qloo_id": b["headliner_qloo_id"],
        "headliner_resolved_name": b["headliner_resolved_name"],
        "resolution_options": b["resolution_options"],
        "city": b["city"],
        "venue_capacity": b["venue_capacity"],
        "show_date": b["show_date"],
        "budget": b["budget"],
        "status": b["status"],
        "status_reason": b["status_reason"],
        "is_running": b["status"] in ACTIVE_STATUSES,
        "widen_level": b["widen_level"],
        "scope": (rules.scopes(b["city"])[b["widen_level"]] or {}).get("query"),
        "use_popularity_cap": bool(b["use_popularity_cap"]),
        "popularity_cap": rules.popularity_cap(b["venue_capacity"]) if b["use_popularity_cap"] else None,
        "weights": b["weights"],
        "headliner_tags": [t["name"] for t in b["headliner_tags"]][:12],
        "demo_mode": bool(b["demo_mode"]),
        "compare_parent_id": b["compare_parent_id"],
        "proven_count": sum(1 for c in live if c["local_proof"]),
        "qualified_count": sum(1 for c in live if c["qualified"]),
        "fit_min_tags": rules.FIT_MIN_TAGS,
        "local_proof_threshold": rules.LOCAL_PROOF_THRESHOLD,
        "candidates": cands,
        "runs": runs,
        "events": list_events(bid),
        "versions": versions,
        "created_at": b["created_at"],
    }


# ── booking briefs (immutable snapshots) ──────────────────────────────────
def save_booking_brief(bid: str, snapshot: dict) -> dict:
    token = secrets.token_urlsafe(12)
    bbid = str(uuid.uuid4())
    with tx() as c:
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM booking_briefs WHERE brief_id=?",
                            (bid,)).fetchone()[0]
        snapshot = {**snapshot, "id": bbid, "version": version, "share_token": token, "created_at": now()}
        c.execute("INSERT INTO booking_briefs (id, brief_id, version, share_token, snapshot, created_at) "
                  "VALUES (?,?,?,?,?,?)", (bbid, bid, version, token, json.dumps(snapshot), snapshot["created_at"]))
        c.execute("UPDATE briefs SET status='finalized', status_reason=NULL, updated_at=? WHERE id=?", (now(), bid))
    return snapshot


def get_share(token: str) -> Optional[dict]:
    rows = _read("SELECT snapshot FROM booking_briefs WHERE share_token=?", (token,))
    return json.loads(rows[0]["snapshot"]) if rows else None


def save_comparison(bid: str, raw: Optional[str], result: dict) -> None:
    with tx() as c:
        c.execute("INSERT INTO llm_comparisons (brief_id, llm_raw, result, created_at) VALUES (?,?,?,?)",
                  (bid, raw, json.dumps(result), now()))
