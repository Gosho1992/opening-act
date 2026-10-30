"""MusicBrainz: free, structured artist data used as a SUPPLEMENTARY evidence source.

What it can say, kept as separate claims:
  formed_here   the artist's "begin area" (where a group formed, or a person was born) is in the search area
  based_here    the artist's "area" (the place MusicBrainz primarily associates them with) is in the search area
  context       a city-level begin area or area OUTSIDE the search area, shown to the buyer as a
                possible conflict; it carries no score

It cannot say anything about current local popularity, recent shows or availability.

Identity: only an exact (normalized) name match counts. If several MusicBrainz artists share
the name, Qloo's genres are compared with MusicBrainz tags to pick one; if that is still
ambiguous, nothing is claimed.

MusicBrainz asks for at most ~1 request per second and a User-Agent with contact details;
both are enforced here. Responses are cached (see cache.py).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from . import cache, config
from .qloo_client import norm

API = "https://musicbrainz.org/ws/2/artist/"
_rate_lock = threading.Lock()
_last_call = [0.0]


class MusicBrainzError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _in_area(place: Optional[str], place_terms: list[str]) -> Optional[str]:
    if not place:
        return None
    pn = norm(place)
    return next((p for p in place_terms if norm(p) == pn), None)


def _row(kind, status, reason, claim, quote, mbid, scope_label):
    return {"kind": kind, "status": status, "reason": reason, "claim": claim, "quote": quote, "excerpt": quote,
            "source_title": "MusicBrainz artist record", "source_url": f"https://musicbrainz.org/artist/{mbid}",
            "source_label": "musicbrainz", "method": "musicbrainz", "scope": scope_label, "retrieved_at": _now()}


def pick_artist(name: str, results: list[dict], genres: Optional[list[str]] = None) -> tuple[Optional[dict], str]:
    """(artist, note). None when there is no exact match or the match is ambiguous."""
    exact = [a for a in results if norm(a.get("name", "")) == norm(name) and int(a.get("score", 0)) >= 90]
    if not exact:
        return None, "no exact name match"
    if len(exact) == 1:
        return exact[0], "exact name match"
    g = {norm(x) for x in (genres or [])}
    def overlap(a):
        tags = {norm(t.get("name", "")) for t in a.get("tags") or []}
        return sum(1 for t in tags if any(t in x or x in t for x in g if x))
    ranked = sorted(exact, key=overlap, reverse=True)
    if g and overlap(ranked[0]) > overlap(ranked[1]):
        return ranked[0], f"{len(exact)} artists share the name; picked by genre tags"
    return None, f"{len(exact)} MusicBrainz artists share the name '{name}'"


def claims(name: str, artist: dict, place_terms: list[str], scope_label: str) -> list[dict]:
    mbid = artist.get("id")
    group = artist.get("type") == "Group"
    begin = (artist.get("begin-area") or {}).get("name")
    area = artist.get("area") or {}
    rows = []
    hit = _in_area(begin, place_terms)
    if hit:
        verb = "formed in" if group else "born or began in"
        rows.append(_row("formed_here", "verified", "MusicBrainz structured data",
                         f"MusicBrainz lists {name} as {verb} {begin}", f"Begin area: {begin}", mbid, scope_label))
    elif begin:
        rows.append(_row("context", "unverified", "Begin area is outside the search area",
                         f"MusicBrainz lists {name}'s begin area as {begin}", f"Begin area: {begin}", mbid, scope_label))
    life = artist.get("life-span") or {}
    if group and life.get("ended"):  # a group MusicBrainz lists as ended (for a person 'ended' means died; not used)
        end = f" ({life.get('end')})" if life.get("end") else ""
        rows.append(_row("inactive", "verified", "MusicBrainz lists the group as ended",
                         f"A source says {name} may no longer be active", f"MusicBrainz: group ended{end}", mbid, scope_label))
    area_name = area.get("name")
    if area.get("type") != "Country":
        hit = _in_area(area_name, place_terms)
        if hit:
            # MusicBrainz 'area' is undated and often stale (an act that moved keeps its old area), so it is a
            # lead, not proof of where the act is based now.
            rows.append(_row("based_here", "unverified", "MusicBrainz area is undated; current base not confirmed",
                             f"MusicBrainz associates {name} with {area_name}", f"Area: {area_name}", mbid, scope_label))
        elif area_name and area_name != begin:
            rows.append(_row("context", "unverified", "Area is outside the search area",
                             f"MusicBrainz associates {name} with {area_name}", f"Area: {area_name}", mbid, scope_label))
    return rows


class MusicBrainz:
    name = "musicbrainz"

    def __init__(self, transport: Optional[httpx.BaseTransport] = None, offline: bool = False):
        self.offline = offline  # demo mode: cache only, never the network
        self._http = httpx.Client(transport=transport, timeout=config.HTTP_TIMEOUT,
                                  headers={"User-Agent": config.MUSICBRAINZ_USER_AGENT, "Accept": "application/json"})
        self.lookups = {"live": 0, "cached": 0, "errors": 0}

    def _search(self, name: str) -> list[dict]:
        query = f'artist:"{name}"'
        hit = cache.get(self.name, query, {"limit": 5, "v": 2}, ttl_days=30)
        if hit is not None:
            self.lookups["cached"] += 1
            return hit[0]
        if self.offline:
            return []
        r = None
        for attempt in range(2):  # one retry if MusicBrainz says it is busy (503)
            with _rate_lock:  # ~1 request per second across all threads
                wait = (1.1 if attempt == 0 else 2.5) - (time.monotonic() - _last_call[0])
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = self._http.get(API, params={"query": query, "fmt": "json", "limit": 5})
                except httpx.HTTPError as e:
                    self.lookups["errors"] += 1
                    raise MusicBrainzError(type(e).__name__) from e
                finally:
                    _last_call[0] = time.monotonic()
            if r.status_code != 503:
                break
        if r.status_code != 200:
            self.lookups["errors"] += 1
            raise MusicBrainzError(f"HTTP {r.status_code}")
        try:
            results = [a for a in r.json().get("artists", []) if isinstance(a, dict)]
        except ValueError as e:
            self.lookups["errors"] += 1
            raise MusicBrainzError("invalid JSON") from e
        keep = ("id", "name", "type", "score", "area", "begin-area", "tags", "disambiguation", "country", "life-span")
        results = [{k: a.get(k) for k in keep} for a in results]
        cache.put(self.name, query, results, {"limit": 5, "v": 2})
        self.lookups["live"] += 1
        return results

    def evidence(self, name: str, place_terms: list[str], scope_label: str, genres: Optional[list[str]] = None
                 ) -> list[dict]:
        try:
            artist, _note = pick_artist(name, self._search(name), genres)
        except MusicBrainzError:
            return []
        return claims(name, artist, place_terms, scope_label) if artist else []


def get_musicbrainz(demo_mode: bool) -> Optional[MusicBrainz]:
    if not config.MUSICBRAINZ:
        return None
    return MusicBrainz(offline=demo_mode)
