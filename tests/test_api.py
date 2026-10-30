from fastapi.testclient import TestClient

from backend import config, db
from backend.main import app


def _client():
    return TestClient(app)


def _create(c, **kw):
    body = {"headliner": "Khruangbin", "city": "Nashville, Tennessee", "venue_capacity": 800, **kw}
    return c.post("/api/briefs", json=body)


def test_full_demo_flow(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    with _client() as c:
        r = _create(c)
        assert r.status_code == 202
        bid = r.json()["id"]
        b = c.get(f"/api/briefs/{bid}").json()  # background task has run by now
        assert b["status"] in ("ready", "ready_low_evidence") and b["candidates"]
        assert b["events"] and b["runs"]
        cid = b["candidates"][0]["id"]
        assert c.post(f"/api/briefs/{bid}/negotiate", json={"action": "set_slot", "candidate_id": cid,
                                                             "slot": "primary"}).status_code == 200
        r = c.post(f"/api/briefs/{bid}/negotiate", json={"action": "reweight", "weights": {"taste": 1, "local": 1}})
        assert r.status_code == 200 and r.json()["weights"]["practical"] == 0
        fin = c.post(f"/api/briefs/{bid}/finalize").json()
        share = c.get(f"/api/share/{fin['share_token']}")
        assert share.status_code == 200 and share.json()["picks"]["primary"]["qloo_id"]
        assert c.get(f"/api/briefs/{bid}/qloo-calls").json()


def test_input_validation_and_bad_transitions(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    with _client() as c:
        assert _create(c, venue_capacity=5).status_code == 422
        assert _create(c, show_date="31/12/2026").status_code == 422
        assert _create(c, headliner="   ").status_code == 422
        bid = _create(c).json()["id"]
        bad = c.post(f"/api/briefs/{bid}/negotiate", json={"action": "reweight", "weights": {"taste": -1}})
        assert bad.status_code == 422
        assert c.post(f"/api/briefs/{bid}/negotiate", json={"action": "veto"}).status_code == 422
        r = c.post(f"/api/briefs/{bid}/negotiate", json={"action": "veto", "candidate_id": "not-mine"})
        assert r.status_code == 404
        assert c.post(f"/api/briefs/{bid}/confirm", json={"qloo_id": "x"}).status_code == 409
        assert c.get("/api/briefs/nope").status_code == 404
        assert c.get("/api/share/nope").status_code == 404
        assert c.post(f"/api/briefs/{bid}/compare").status_code == 503  # no OpenAI key


def test_failed_lookup_is_reported(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    with _client() as c:
        b = c.get(f"/api/briefs/{_create(c, headliner='Unrecorded Artist').json()['id']}").json()
        assert b["status"] == "failed" and b["status_reason"]
        assert c.post(f"/api/briefs/{b['id']}/finalize").status_code == 409


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_PER_HOUR", 2)
    from backend import main
    main._hits.clear()
    with _client() as c:
        codes = [_create(c).status_code for _ in range(3)]
    main._hits.clear()
    assert codes == [202, 202, 429]


def test_interrupted_runs_are_recovered():
    class In:
        headliner, city, venue_capacity, show_date, budget, use_popularity_cap = "X", "Y, Z", 800, None, None, False
    bid = db.insert_brief(In, True)
    db.set_status(bid, "investigating")
    assert db.recover_interrupted() == 1 and db.get_brief(bid)["status"] == "failed"


def test_past_show_date_is_rejected_and_nickname_city_is_expanded():
    from backend.models import ShowBriefIn
    import pytest
    with pytest.raises(Exception):
        ShowBriefIn(headliner="X", city="NOLA", venue_capacity=800, show_date="2021-02-01")
    assert ShowBriefIn(headliner="X", city="NOLA", venue_capacity=800).city == "New Orleans, Louisiana"
