import json

import httpx
import pytest

from backend.qloo_client import QlooClient, QlooError, ReplayQlooClient, parse_entity
from tests.conftest import ent


def client(handler, retries=2):
    return QlooClient(transport=httpx.MockTransport(handler), base_url="https://q.test", api_key="k", retries=retries)


def test_resolve_exact_unique_match():
    def h(req):
        assert req.url.path == "/search" and req.url.params["types"] == "urn:entity:artist"
        return httpx.Response(200, json={"results": [
            {"entity_id": "A", "name": "St. Vincent", "types": ["urn:entity:artist"]},
            {"entity_id": "B", "name": "St. Vincent and The National", "types": ["urn:entity:artist"]}]})
    r = client(h).resolve_artist("st vincent")
    assert r["status"] == "resolved" and r["match"]["id"] == "A"


def test_resolve_needs_confirmation_without_exact_match():
    def h(req):
        return httpx.Response(200, json={"results": [{"entity_id": "B", "name": "St. Vincent and The National"}]})
    r = client(h).resolve_artist("St. Vincent")
    assert r["status"] == "ambiguous" and r["options"][0]["id"] == "B"


def test_resolve_two_exact_matches_is_ambiguous():
    def h(req):
        return httpx.Response(200, json={"results": [{"entity_id": "A", "name": "Tennis"}, {"entity_id": "B", "name": "Tennis"}]})
    assert client(h).resolve_artist("Tennis")["status"] == "ambiguous"


def test_related_uses_interest_signal_and_parses_real_shape():
    seen = {}

    def h(req):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"results": {"entities": [
            ent("SEED", "Khruangbin", 0.99),
            ent("V", "Vulfpeck", 0.966, listeners=1_337_996),
            ent("BH", "Beach House", 0.95, pop=0.999, listeners=30),
            {"name": "no id"}]}})
    out = client(h).related("SEED", "Nashville, Tennessee", take=10)
    assert seen["signal.interests.entities"] == "SEED" and seen["signal.location.query"] == "Nashville, Tennessee"
    assert "filter.id" not in seen and "filter.location.query" not in seen
    names = [e["name"] for e in out["entities"]]
    assert names == ["Vulfpeck", "Beach House"]  # seed and id-less rows dropped
    assert out["entities"][0]["monthly_listeners"] == 1_337_996 and out["entities"][0]["affinity"] == 0.966
    assert out["entities"][1]["monthly_listeners"] is None and out["entities"][1]["listeners_flag"] == "implausible"


def test_retries_server_errors_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, json={"results": []})
    assert client(h).search_artists("x") == [] and n["c"] == 3


def test_auth_error_is_not_retried():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(401, text="unauthorized")
    with pytest.raises(QlooError) as e:
        client(h).search_artists("x")
    assert e.value.status == 401 and n["c"] == 1


def test_non_json_and_missing_key():
    with pytest.raises(QlooError):
        client(lambda r: httpx.Response(200, text="<html>")).search_artists("x")
    with pytest.raises(QlooError):
        QlooClient(api_key="").search_artists("x")


def test_popularity_filter_falls_back_to_client_side():
    def h(req):
        if "filter.popularity.max" in req.url.params:
            return httpx.Response(400, json={"error": "unsupported"})
        return httpx.Response(200, json={"results": {"entities": [ent("BIG", "Big", 0.97, pop=0.999),
                                                                   ent("SMALL", "Small", 0.96, pop=0.85)]}})
    out = client(h).related("SEED", "Austin", popularity_max=0.97)
    assert out["popularity_filter"] == "client" and [e["name"] for e in out["entities"]] == ["Small"]
    assert out["entities"][0]["rank"] == 1


def test_parse_entity_tolerates_missing_fields():
    e = parse_entity({"entity_id": "X", "name": "Y"}, 1)
    assert e["affinity"] is None and e["popularity"] is None and e["monthly_listeners"] is None


def test_replay_uses_real_recorded_responses():
    q = ReplayQlooClient()
    r = q.resolve_artist("Khruangbin")
    assert r["status"] == "resolved"
    names = [e["name"] for e in q.related(r["match"]["id"], "Nashville")["entities"]]
    assert "Vulfpeck" in names and "The Nude Party" in names  # recorded 2026-10-15
    assert q.related(r["match"]["id"], "Nashville metropolitan area, Tennessee")["entities"] == []
    assert q.resolve_artist("Some Unrecorded Band")["status"] == "not_found"
