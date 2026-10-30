"""Persistent cache for paid or rate-limited provider calls (web search, MusicBrainz, LLM verdicts).

Keyed by provider + normalized query + the parameters that change the response, so the same
search is never paid for twice within the time-to-live. Every entry keeps the time it was
fetched; results served from the cache are reported as such, so a cached answer is never
presented as a fresh lookup.

The cache lives in its own SQLite file (SEARCH_CACHE_PATH), separate from briefs, so clearing
briefs never throws away paid searches and vice versa.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config

_lock = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
  provider   TEXT NOT NULL,
  key        TEXT NOT NULL,
  query      TEXT NOT NULL,
  params     TEXT NOT NULL,
  response   TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (provider, key)
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip()).lower()


def make_key(provider: str, query: str, params: Optional[dict] = None) -> str:
    raw = json.dumps([provider, normalize_query(query), params or {}], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _conn() -> sqlite3.Connection:
    # TRUNCATE journal (no WAL, no file deletion): writes are serialized by _lock anyway, and
    # WAL or journal deletion fails on some shared/network drives and restricted folders.
    c = sqlite3.connect(config.SEARCH_CACHE_PATH, timeout=30)
    c.execute("PRAGMA journal_mode = TRUNCATE")
    c.executescript(_SCHEMA)
    return c


def get(provider: str, query: str, params: Optional[dict] = None, ttl_days: Optional[float] = None
        ) -> Optional[tuple[Any, str]]:
    """(response, fetched_at) if a fresh entry exists, else None."""
    if not config.SEARCH_CACHE:
        return None
    ttl = config.CACHE_TTL_DAYS if ttl_days is None else ttl_days
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT response, fetched_at FROM cache WHERE provider=? AND key=?",
                            (provider, make_key(provider, query, params))).fetchone()
        finally:
            c.close()
    if not row:
        return None
    fetched = datetime.fromisoformat(row[1])
    if ttl is not None and ttl >= 0 and _now() - fetched > timedelta(days=ttl):
        return None
    return json.loads(row[0]), row[1]


def put(provider: str, query: str, response: Any, params: Optional[dict] = None,
        fetched_at: Optional[str] = None) -> None:
    if not config.SEARCH_CACHE:
        return
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT OR REPLACE INTO cache (provider, key, query, params, response, fetched_at) "
                      "VALUES (?,?,?,?,?,?)",
                      (provider, make_key(provider, query, params), normalize_query(query),
                       json.dumps(params or {}, sort_keys=True), json.dumps(response),
                       fetched_at or _now().isoformat()))
            c.commit()
        finally:
            c.close()


def stats() -> dict:
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT provider, COUNT(*), MIN(fetched_at), MAX(fetched_at) FROM cache "
                             "GROUP BY provider").fetchall()
        finally:
            c.close()
    return {p: {"entries": n, "oldest": lo, "newest": hi} for p, n, lo, hi in rows}
