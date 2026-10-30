import pytest

from backend import agent, db
from backend.qloo_client import QlooError
from tests.conftest import FakeEvidence, FakeQloo, ShowIn, ent

SEARCH = {"khruangbin": [{"entity_id": "SEED", "name": "Khruangbin"}]}
BASE = [ent("V", "Vulfpeck", 0.97), ent("S", "SAULT", 0.96)]
CITY = [ent("V", "Vulfpeck", 0.97), ent("N", "The Nude Party", 0.96), ent("B", "Black Pumas", 0.955),
        ent("H", "Brittany Howard", 0.95, pob="Athens, Limestone County, Alabama")]


def _brief(**kw):
    return db.insert_brief(ShowIn(**kw), False)


def _status(bid):
    return db.get_brief(bid)["status"]


def test_ready_at_city_level_when_three_have_local_proof():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")], "Black Pumas": [("based", "Nashville")],
                       "Vulfpeck": [("based", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    b = db.fetch_brief(bid)
    assert b["status"] == "ready" and b["widen_level"] == 0 and b["proven_count"] == 3
    nude = next(c for c in b["candidates"] if c["artist_name"] == "The Nude Party")
    assert nude["new_in_city"] and nude["noloc_rank"] is None
    v = next(c for c in b["candidates"] if c["artist_name"] == "Vulfpeck")
    assert v["rank_delta"] == 0 and v["noloc_rank"] == 1
    assert b["runs"][-1]["outcome"] == "ready"
    # the interest signal (not filter.id) carries the seed
    assert all(p.get("signal.interests.entities") == "SEED" for path, p in q.calls if path == "/v2/insights")


def test_widening_searches_evidence_with_the_wider_scope():
    ev = FakeEvidence({"The Nude Party": [("based", "Murfreesboro")], "Black Pumas": [("based", "Franklin")],
                       "Vulfpeck": [("based", "Tennessee")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY,
                          "nashville metropolitan area, tennessee": CITY, "tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    b = db.fetch_brief(bid)
    assert b["status"] == "ready" and b["widen_level"] == 2
    scopes_used = {s for _, _, s in ev.calls}
    assert scopes_used == {"city", "metro", "region"}
    region_terms = next(t for _, t, s in ev.calls if s == "region")
    assert "Tennessee" in region_terms and "TN" in region_terms
    assert any(e["type"] == "decision" and "widens" in e["message"] for e in b["events"])


def test_low_evidence_is_a_terminal_state_not_a_silent_success():
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=FakeEvidence())
    b = db.fetch_brief(bid)
    assert b["status"] == "ready_low_evidence" and "0 of" in b["status_reason"]


def test_no_results_anywhere_fails_with_reason():
    bid = _brief()
    agent.run(bid, qloo=FakeQloo(SEARCH, {}), evidence=FakeEvidence())
    b = db.get_brief(bid)
    assert b["status"] == "failed" and "no usable related artists" in b["status_reason"]


def test_unknown_headliner_fails_and_ambiguous_waits_for_buyer():
    bid = _brief(headliner="Nobody")
    agent.run(bid, qloo=FakeQloo({}, {}), evidence=FakeEvidence())
    assert _status(bid) == "failed"

    q = FakeQloo({"tennis": [{"entity_id": "T1", "name": "Tennis"}, {"entity_id": "T2", "name": "Tennis"}]}, {})
    bid = _brief(headliner="Tennis")
    agent.run(bid, qloo=q, evidence=FakeEvidence())
    b = db.get_brief(bid)
    assert b["status"] == "needs_confirmation" and {o["id"] for o in b["resolution_options"]} == {"T1", "T2"}
    assert not [c for c in db.get_candidates(bid)]  # nothing discovered from a guessed seed


def test_provider_failure_becomes_failed_state():
    bid = _brief()
    agent.run(bid, qloo=FakeQloo(fail=QlooError("Qloo HTTP 503", 503)), evidence=FakeEvidence())
    b = db.get_brief(bid)
    assert b["status"] == "failed" and "503" in b["status_reason"]


def test_unexpected_exception_never_leaves_brief_running():
    class Boom(FakeQloo):
        def related(self, *a, **k):
            raise RuntimeError("bug")
    bid = _brief()
    agent.run(bid, qloo=Boom(SEARCH, {}), evidence=FakeEvidence())
    assert _status(bid) == "failed"


def test_veto_survives_rerun_and_vetoed_artist_is_not_reinvestigated():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    nude = next(c for c in db.get_candidates(bid) if c["artist_name"] == "The Nude Party")
    db.veto(bid, nude["id"])
    ev.calls.clear()
    agent.run(bid, "rerun", qloo=q, evidence=ev)
    again = next(c for c in db.get_candidates(bid) if c["artist_name"] == "The Nude Party")
    assert again["id"] == nude["id"] and again["status"] == "vetoed" and again["evidence"]
    assert "The Nude Party" not in {a for a, _, _ in ev.calls}


def test_every_evidence_row_has_a_source():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    for c in db.get_candidates(bid):
        for e in c["evidence"]:
            assert e["source_url"] or e["source_label"] == "qloo"


def test_finalized_snapshot_is_immutable_and_versioned():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")], "Black Pumas": [("based", "Nashville")],
                       "Vulfpeck": [("based", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    howard = next(c for c in db.get_candidates(bid) if c["artist_name"] == "Brittany Howard")
    db.set_slot(bid, howard["id"], "primary")
    v1 = agent.finalize(bid)
    assert v1["picks"]["primary"]["artist_name"] == "Brittany Howard" and not v1["picks"]["primary"]["auto_selected"]
    assert any("chosen by the buyer) fails the local check" in w for w in v1["warnings"])
    assert v1["kind"] == "recommendation"
    assert v1["picks"]["backup"]["local_proof"]  # auto-fill prefers locally proven candidates
    assert v1["narrative_source"] == "template"  # no OpenAI key in tests
    db.veto(bid, howard["id"])
    db.set_status(bid, "ready")
    v2 = agent.finalize(bid)
    assert v2["version"] == 2 and v2["share_token"] != v1["share_token"]
    assert db.get_share(v1["share_token"])["picks"]["primary"]["artist_name"] == "Brittany Howard"


def test_finalize_rejects_when_nothing_is_left():
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY[:1]})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=FakeEvidence())
    for c in db.get_candidates(bid):
        db.veto(bid, c["id"])
    with pytest.raises(db.InvalidTransition):
        agent.finalize(bid)


def test_smaller_acts_preference_excludes_big_names():
    city = [ent("BIG", "Big Act", 0.97, pop=0.999), ent("SMALL", "Small Act", 0.96, pop=0.86)]
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": city})
    bid = _brief(cap=True)
    agent.run(bid, qloo=q, evidence=FakeEvidence())
    names = [c["artist_name"] for c in db.get_candidates(bid) if not c["excluded_reason"]]
    assert names == ["Small Act"]


def test_demo_mode_replays_real_data_end_to_end():
    bid = db.insert_brief(ShowIn(), True)
    agent.run(bid)
    b = db.fetch_brief(bid)
    assert b["status"] in ("ready", "ready_low_evidence")
    assert "The Nude Party" in [c["artist_name"] for c in b["candidates"]]


def test_playing_the_city_is_not_local_proof():
    ev = FakeEvidence({"The Nude Party": [("played", "Nashville")], "Black Pumas": [("played", "Nashville")],
                       "Vulfpeck": [("played", "Nashville"), ("rejected", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    b = db.fetch_brief(bid)
    assert b["status"] == "ready_low_evidence" and b["proven_count"] == 0
    v = next(c for c in b["candidates"] if c["artist_name"] == "Vulfpeck")
    assert v["plays_market"] and not v["local_proof"]
    assert any(e["type"] == "result" and "1 rejected" in e["message"] for e in b["events"])


def test_smaller_acts_filter_uses_room_size_and_hides_headliner_sized_acts():
    city = CITY + [ent("M", "Mitski", 0.99, pop=0.996)]
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": city})
    bid = _brief(cap=True)
    agent.run(bid, qloo=q, evidence=FakeEvidence())
    names = [c["artist_name"] for c in db.get_candidates(bid) if not c["excluded_reason"]]
    assert "Mitski" not in names and "Vulfpeck" in names
    assert any("Mitski" in e["message"] for e in db.list_events(bid))
    # Qloo is asked for the full pool, not just the shortlist
    assert any(p.get("take") == 50 for path, p in q.calls if path == "/v2/insights")


def test_evidence_history_is_kept_across_runs():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")]})
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    db.update_brief(bid, widen_level=0)
    agent.run(bid, "rerun", qloo=q, evidence=ev)
    nude = next(c for c in db.get_candidates(bid) if c["artist_name"] == "The Nude Party")
    assert len(nude["evidence"]) == 1  # only the latest run is shown
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM evidence WHERE candidate_id=?", (nude["id"],)).fetchone()[0]
    assert n == 2  # but the earlier run's evidence is still stored


def test_four_way_comparison_counts_are_consistent(monkeypatch):
    from backend import llm
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")], "Vulfpeck": [("played", "Nashville")]})
    city = CITY + [ent("M", "Mitski", 0.99, pop=0.996)]
    q = FakeQloo({**SEARCH, "the nude party": [{"entity_id": "N", "name": "The Nude Party", "popularity": 0.86}],
                  "mitski": [{"entity_id": "M", "name": "Mitski", "popularity": 0.996}]},
                 {"": BASE, "nashville, tennessee": city})
    bid = _brief(cap=True)
    agent.run(bid, qloo=q, evidence=ev)
    monkeypatch.setattr(llm, "suggest_llm_only", lambda *a, **k: (["The Nude Party", "Mitski", "Made Up Band"], "{}"))
    r = agent.compare_llm(bid, qloo=q, evidence=ev)
    s = r["summary"]
    assert set(r["arms"]) == {"llm_only", "qloo_no_city", "qloo_city", "opening_act"}
    assert s["llm_only"] == {"n": 3, "verified_local": 1, "played_city_only": 0, "too_big_for_room": 1,
                             "not_found_in_qloo": 1, "verified_local_and_right_size": 1, "shared_with_opening_act": 1,
                             "not_checked_budget": 0, "qualified": 1}
    assert s["opening_act"]["shared_with_opening_act"] is None
    assert s["opening_act"]["too_big_for_room"] == 0  # the size filter ran
    assert s["qloo_city"]["too_big_for_room"] == 1    # raw Qloo list still includes Mitski
    assert s["qloo_city"]["played_city_only"] == 1     # Vulfpeck played Nashville but is not local
    for arm in r["arms"].values():                      # identical lists would score identically
        for row in arm:
            assert row["verified_local"] == (row["artist_name"] == "The Nude Party")
            assert row["fit_ok"] == (row["artist_name"] != "Made Up Band")  # Qloo-related acts fit; unknown does not


def test_one_qualified_act_gives_a_partial_recommendation_with_only_that_act_picked():
    ev = FakeEvidence({"The Nude Party": [("based", "Nashville")]})  # 1 verified local (with Qloo affinity) of 3
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=ev)
    snap = agent.finalize(bid)
    assert snap["kind"] == "partial_recommendation"
    assert snap["picks"]["primary"]["artist_name"] == "The Nude Party"
    assert snap["picks"]["backup"] is None and snap["picks"]["alternative"] is None  # never padded
    assert snap["warnings"][0].startswith("Partial recommendation")
    # the buyer can still choose, and the brief says so
    vul = next(c for c in db.get_candidates(bid) if c["artist_name"] == "Vulfpeck")
    db.set_slot(bid, vul["id"], "backup")
    db.set_status(bid, "ready_low_evidence")
    snap2 = agent.finalize(bid)
    assert snap2["picks"]["backup"]["artist_name"] == "Vulfpeck"
    assert any("chosen by the buyer" in w for w in snap2["warnings"])


def test_no_verified_local_gives_a_research_shortlist_with_no_picks():
    q = FakeQloo(SEARCH, {"": BASE, "nashville, tennessee": CITY})
    bid = _brief()
    agent.run(bid, qloo=q, evidence=FakeEvidence({}))
    snap = agent.finalize(bid)
    assert snap["kind"] == "research_shortlist"
    assert all(v is None for v in snap["picks"].values()) and snap["pitch_line"] is None
    assert snap["warnings"][0].startswith("Research shortlist, not a booking recommendation")
