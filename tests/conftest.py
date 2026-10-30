import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Tests never call real providers.
os.environ.update({"QLOO_API_KEY": "", "TAVILY_API_KEY": "", "OPENAI_API_KEY": "", "DEMO_MODE": "true",
                   "RECORD_FIXTURES": "false"})

from backend import config, db  # noqa: E402
from backend.qloo_client import BaseQloo  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "QLOO_API_KEY", "")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "RECORD_FIXTURES", False)
    monkeypatch.setattr(config, "SEARCH_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(config, "MUSICBRAINZ", False)
    db.init()
    yield


def ent(eid, name, rank_aff, pop=0.9, listeners=100_000, pob=None):
    """A raw Qloo insights entity in the real response shape."""
    return {"entity_id": eid, "name": name, "popularity": pop, "query": {"affinity": rank_aff},
            "external": {"spotify": [{"monthly_listeners": listeners}]},
            "properties": {"place_of_birth": pob} if pob else {}}


class FakeQloo(BaseQloo):
    """Scriptable Qloo double. search: {name_lower: [raw]}, insights: {location_lower_or_'': [raw]}."""

    def __init__(self, search=None, insights=None, fail=None, entities=None):
        super().__init__(None)
        self.search = search or {}
        self.insights = insights or {}
        self.entities = entities or {}
        self.fail = fail
        self.calls = []

    def _get(self, purpose, path, params):
        self.calls.append((path, dict(params)))
        if self.fail:
            raise self.fail
        if path == "/search":
            return {"results": self.search.get(params["query"].lower(), [])}
        if path == "/entities":
            ids = str(params.get("entity_ids", "")).split(",")
            return {"results": [self.entities[i] for i in ids if i in self.entities]}
        loc = str(params.get("signal.location.query") or "").lower()
        seed = params.get("signal.interests.entities")
        found = self.insights.get(f"{seed}|{loc}", self.insights.get(loc, []))
        return {"results": {"entities": found}}


class FakeEvidence:
    available = True

    def __init__(self, by_artist=None):
        self.by_artist = by_artist or {}
        self.calls = []

    KINDS = {"based": "based_here", "played": "played_here"}

    def search(self, artist, place_terms, scope_label, context=None):
        """by_artist: {name: [("based"|"played"|"rejected", place)]}"""
        self.calls.append((artist, tuple(place_terms), scope_label))
        out = []
        for i, (kind, place) in enumerate(self.by_artist.get(artist, [])):
            if place in place_terms:
                rejected = kind == "rejected"
                out.append({"kind": "none" if rejected else self.KINDS[kind],
                            "status": "rejected" if rejected else "verified",
                            "reason": "test", "method": "rule", "claim": f"{artist} {place}",
                            "quote": None if rejected else f"{artist} is from {place}", "excerpt": "x",
                            "source_title": "t", "source_url": f"https://example.org/{artist.replace(' ', '-')}/{i}",
                            "source_label": "public", "scope": scope_label,
                            "retrieved_at": "2026-10-28T00:00:00+00:00"})
        return out


class ShowIn:
    def __init__(self, headliner="Khruangbin", city="Nashville, Tennessee", venue_capacity=800, cap=False):
        self.headliner, self.city, self.venue_capacity = headliner, city, venue_capacity
        self.show_date, self.budget, self.use_popularity_cap = None, None, cap


def profile(eid, name, *tags):
    """A raw /entities result with taste tags ('genre:x' style strings)."""
    return {"entity_id": eid, "name": name, "tags": [
        {"tag_id": f"urn:tag:{t}", "name": t.split(":")[-1], "type": "urn:tag:" + t.split(":")[0]} for t in tags]}
