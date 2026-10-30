"""The agent's discovery moves: top of Qloo's list, deeper in the list, the verified locals' scene."""
from backend import agent, config, db, rules
from backend.qloo_client import parse_tags
from tests.conftest import FakeEvidence, FakeQloo, ShowIn, ent, profile

SEARCH = {"japanese breakfast": [{"entity_id": "SEED", "name": "Japanese Breakfast"}]}
HEAD = profile("SEED", "Japanese Breakfast", "genre:indie pop", "genre:dream pop", "audience:qloo:wistful",
               "music:qloo:dreamy")


def run(insights, evidence, entities=None, monkeypatch=None, **kw):
    if monkeypatch:
        monkeypatch.setattr(config, "QLOO_TAKE", 2)
        monkeypatch.setattr(config, "DEEPER_N", 2)
        monkeypatch.setattr(config, "SCENE_N", 2)
    q = FakeQloo(SEARCH, insights, entities={"SEED": HEAD, **(entities or {})})
    bid = db.insert_brief(ShowIn(headliner="Japanese Breakfast", city="Atlanta, Georgia", **kw), False)
    agent.run(bid, qloo=q, evidence=FakeEvidence(evidence))
    return bid, q


def test_tag_overlap_explains_fit_with_genres_first():
    head = parse_tags(HEAD)
    cand = parse_tags(profile("X", "X", "audience:qloo:wistful", "genre:dream pop", "genre:metal"))
    score, shared = rules.tag_overlap(head, cand)
    assert score == round(2 / 3, 3) and shared == ["dream pop", "wistful"]
    assert rules.tag_overlap([], cand) == (None, [])
    assert rules.taste_fit(None, 0.4) == 0.4 and rules.taste_fit(1.0, 0.5) == 0.8 and rules.taste_fit(None, None) == 0.5


def test_digs_deeper_in_the_same_list_before_widening(monkeypatch):
    city = [ent("A", "Big One", 0.99), ent("B", "Big Two", 0.98), ent("C", "Local One", 0.97),
            ent("D", "Local Two", 0.96)]
    ev = {"Local One": [("based", "Atlanta")], "Local Two": [("based", "Atlanta")], "Big One": [("based", "Atlanta")]}
    bid, _ = run({"": [], "atlanta, georgia": city}, ev, monkeypatch=monkeypatch)
    b = db.fetch_brief(bid)
    assert b["status"] == "ready" and b["widen_level"] == 0
    deeper = {c["artist_name"]: c["found_via"] for c in b["candidates"]}
    assert deeper["Local One"] == "qloo_deeper" and deeper["Big One"] == "qloo"
    assert any(e["type"] == "decision" and "lower in Qloo's list" in e["message"] for e in b["events"])


def test_expands_from_verified_locals_and_ranks_scene_by_taste_tags(monkeypatch):
    city = [ent("L1", "Lunar Vacation", 0.97), ent("X", "Touring Act", 0.96)]
    scene = [ent("S1", "Metal Band", 0.99), ent("S2", "Dreamy Locals", 0.98), ent("S3", "Indie Locals", 0.97)]
    entities = {"S1": profile("S1", "Metal Band", "genre:metal"),
                "S2": profile("S2", "Dreamy Locals", "genre:dream pop", "music:qloo:dreamy"),
                "S3": profile("S3", "Indie Locals", "genre:indie pop")}
    ev = {"Lunar Vacation": [("based", "Atlanta")], "Dreamy Locals": [("based", "Atlanta")],
          "Indie Locals": [("based", "Atlanta")], "Metal Band": [("based", "Atlanta")]}
    bid, q = run({"": [], "atlanta, georgia": city, "L1|atlanta, georgia": scene}, ev, entities,
                 monkeypatch=monkeypatch)
    b = db.fetch_brief(bid)
    via = {c["artist_name"]: c for c in b["candidates"]}
    assert b["status"] == "ready"
    assert via["Dreamy Locals"]["found_via"] == "scene" and via["Dreamy Locals"]["scene_seed"] == "Lunar Vacation"
    assert "Metal Band" not in via                       # lowest tag overlap, not investigated (SCENE_N=2)
    assert via["Dreamy Locals"]["shared_tags"][:1] == ["dream pop"]
    assert via["Dreamy Locals"]["city_affinity"] is None  # affinity to a local act is not affinity to the headliner
    assert any(p.get("signal.interests.entities") == "L1" for path, p in q.calls if path == "/v2/insights")


def test_identity_mismatch_from_entities_excludes_the_candidate(monkeypatch):
    city = [ent("A", "Lunar Vacation", 0.97)]
    entities = {"A": profile("A", "Someone Else")}
    bid, _ = run({"": [], "atlanta, georgia": city}, {"Lunar Vacation": [("based", "Atlanta")]}, entities,
                 monkeypatch=monkeypatch)
    c = db.get_candidates(bid)[0]
    assert c["excluded_reason"].startswith("Qloo identity check failed")


def test_headliner_tags_are_stored_and_shown():
    bid, _ = run({"": [], "atlanta, georgia": [ent("A", "X", 0.9)]}, {})
    assert "dream pop" in db.fetch_brief(bid)["headliner_tags"]


def test_scene_picks_are_described_by_how_they_were_found_not_by_a_headliner_rank():
    from backend import llm
    brief = {"headliner_name": "St. Vincent", "city": "Austin, Texas", "venue_capacity": 800}
    p = {"artist_name": "Why Bonnie", "found_via": "scene", "scene_seed": "Molly Burch", "city_rank": 50,
         "shared_tags": ["Indie Rock", "Dream Pop"], "local_proof": True, "noloc_rank": None}
    pitch = llm.write_pitch(brief, p)
    assert "found through the local act Molly Burch" in pitch and "#50" not in pitch and "Indie Rock" in pitch
    text = llm.template_narrative(brief, {"primary": p})
    assert "#50" not in text and "Molly Burch" in text


def test_llm_local_names_are_leads_checked_like_qloo_ones(monkeypatch):
    """The AI-named move: names must resolve to one Qloo artist and pass the size filter; then they get the
    same evidence path. A name that is too big or missing from Qloo never becomes a candidate."""
    from backend import agent, config, llm
    monkeypatch.setattr(config, "OPENAI_API_KEY", "x")
    monkeypatch.setattr(llm, "suggest_local_acts", lambda *a, **k: ["Local One", "Huge Star", "Nobody"])

    class Q:
        def search_artists(self, name, take=5):
            return {"Local One": [{"id": "L1", "name": "Local One", "popularity": 0.5, "monthly_listeners": None,
                                   "listeners_flag": None, "place_of_birth": None, "genres": ["indie"], "image_url": None}],
                    "Huge Star": [{"id": "H1", "name": "Huge Star", "popularity": 0.999, "monthly_listeners": None,
                                   "listeners_flag": None, "place_of_birth": None, "genres": [], "image_url": None}]}.get(name, [])
    seen = {}
    monkeypatch.setattr(agent, "_process_batch", lambda ctx, cands, profiles=None: seen.setdefault("c", cands) and None)
    monkeypatch.setattr(agent, "_event", lambda *a, **k: None)
    monkeypatch.setattr(agent.db, "get_candidates", lambda bid, with_evidence=False: [])
    ctx = {"bid": "b", "run_id": "r", "brief": {"headliner_resolved_name": "H", "headliner_name": "H", "city": "Austin, Texas",
           "venue_capacity": 800, "demo_mode": 0}, "qloo": Q(), "scope": {"query": "Austin, Texas", "label": "city"},
           "provider": object(), "cap": 0.95, "seed": "S", "level": 0}
    agent._llm_local(ctx)
    assert [c["artist_name"] for c in seen["c"]] == ["Local One"]
    assert seen["c"][0]["found_via"] == "llm_local" and seen["c"][0]["city_affinity"] is None
