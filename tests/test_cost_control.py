"""Cache and run budget: repeat runs must not pay twice, and no run may overspend."""
import httpx
import pytest

from backend import agent, cache, config, db
from backend.budget import Budget, BudgetExceeded
from backend.evidence import TavilyEvidence
from tests.conftest import FakeQloo, ShowIn, ent


def tavily_transport(counter, content="Lunar Vacation are an indie band from Atlanta, Georgia."):
    def handler(request):
        counter.append(request)
        return httpx.Response(200, json={"results": [{"title": "Lunar Vacation", "url": f"https://ex.org/{len(counter)}",
                                                      "content": content}]})
    return httpx.MockTransport(handler)


def provider(calls, limit=100):
    return TavilyEvidence(transport=tavily_transport(calls), api_key="k", budget=Budget(limit),
                          verifier=lambda *a: (_ for _ in ()).throw(RuntimeError()))


def test_cache_key_ignores_case_and_spacing_but_not_params():
    assert cache.make_key("tavily", "Lunar  Vacation Atlanta") == cache.make_key("tavily", " lunar vacation atlanta")
    assert cache.make_key("tavily", "x", {"max_results": 5}) != cache.make_key("tavily", "x", {"max_results": 10})


def test_second_run_is_served_from_cache_and_costs_nothing():
    calls = []
    p1 = provider(calls)
    rows1 = p1.search("Lunar Vacation", ["Atlanta"], "city")
    assert len(calls) == 2 and p1.budget.live == 2       # two queries per artist
    p2 = provider(calls)                                 # a new run: new provider, same cache file
    rows2 = p2.search("Lunar Vacation", ["Atlanta"], "city")
    assert len(calls) == 2 and p2.budget.live == 0 and p2.budget.cached == 2
    assert [r["status"] for r in rows1] == [r["status"] for r in rows2]


def test_expired_cache_entries_are_fetched_again():
    import sqlite3
    calls = []
    provider(calls).search("Lunar Vacation", ["Atlanta"], "city")
    c = sqlite3.connect(config.SEARCH_CACHE_PATH)  # age the stored entries past the 14-day TTL
    c.execute("UPDATE cache SET fetched_at='2020-01-01T00:00:00+00:00'")
    c.commit()
    c.close()
    p = provider(calls)
    p.search("Lunar Vacation", ["Atlanta"], "city")
    assert p.budget.live == 2


def test_cache_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_CACHE", False)
    calls = []
    provider(calls).search("Lunar Vacation", ["Atlanta"], "city")
    provider(calls).search("Lunar Vacation", ["Atlanta"], "city")
    assert len(calls) == 4


def test_budget_raises_before_the_paid_call():
    calls = []
    p = provider(calls, limit=1)
    with pytest.raises(BudgetExceeded):
        p.search("Lunar Vacation", ["Atlanta"], "city")
    assert len(calls) == 1 and p.budget.exhausted   # the second query was never sent


def test_agent_stops_gracefully_when_budget_runs_out_and_keeps_work(monkeypatch):
    calls = []
    p = provider(calls, limit=4)  # enough for 2 of the 4 artists
    city = [ent("A", "Lunar Vacation", 0.97), ent("B", "Band Two", 0.96), ent("C", "Band Three", 0.95),
            ent("D", "Band Four", 0.94)]
    q = FakeQloo({"khruangbin": [{"entity_id": "SEED", "name": "Khruangbin"}]}, {"": [], "nashville, tennessee": city})
    monkeypatch.setattr(config, "EVIDENCE_WORKERS", 1)
    bid = db.insert_brief(ShowIn(), False)
    agent.run(bid, qloo=q, evidence=p)
    b = db.fetch_brief(bid)
    assert len(calls) == 4
    assert b["status"] in ("ready", "ready_low_evidence") and "search budget" in b["status_reason"]
    assert len(b["candidates"]) == 4                      # all candidates kept
    assert any(e["type"] == "budget" for e in b["events"])
    assert b["widen_level"] == 0                          # no widening after the budget ran out
