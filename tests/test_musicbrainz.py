"""MusicBrainz as a supplementary source. Shapes follow real responses checked on 2026-10-28."""
import httpx

from backend import agent, db
from backend.musicbrainz import MusicBrainz, claims, pick_artist
from backend import rules
from tests.conftest import FakeEvidence, FakeQloo, ShowIn, ent

LUNAR = {"id": "mb-lv", "name": "Lunar Vacation", "type": "Group", "score": 100,
         "area": {"name": "United States", "type": "Country"}, "begin-area": {"name": "Atlanta", "type": "City"}}
DEHD = {"id": "mb-dehd", "name": "Dehd", "type": "Group", "score": 100,
        "area": {"name": "United States", "type": "Country"}, "begin-area": {"name": "Chicago", "type": "City"}}
GALLO = {"id": "mb-rg", "name": "Ron Gallo", "type": "Person", "score": 100,
         "area": {"name": "Philadelphia", "type": "City"}, "begin-area": {"name": "New Jersey", "type": "Subdivision"}}


def test_formed_in_the_area_is_its_own_claim_and_counts_as_proof():
    rows = claims("Lunar Vacation", LUNAR, ["Atlanta", "Decatur"], "metro")
    assert [(r["kind"], r["status"]) for r in rows] == [("formed_here", "verified")]
    assert rows[0]["source_url"] == "https://musicbrainz.org/artist/mb-lv" and rows[0]["source_label"] == "musicbrainz"
    assert rules.local_score(rows) >= rules.LOCAL_PROOF_THRESHOLD
    assert rows[0]["kind"] != "based_here"  # formation place is not presented as where they live now


def test_place_outside_the_area_is_context_with_no_score():
    rows = claims("Dehd", DEHD, ["Atlanta"], "city")
    assert [(r["kind"], r["status"]) for r in rows] == [("context", "unverified")]
    assert "Chicago" in rows[0]["claim"] and rules.local_score(rows) == 0.0


def test_country_level_area_is_ignored_and_city_area_is_only_a_lead():
    assert all(r["kind"] != "based_here" for r in claims("Lunar Vacation", LUNAR, ["United States"], "city"))
    rows = claims("Ron Gallo", GALLO, ["Philadelphia"], "city")
    assert ("based_here", "unverified") in [(r["kind"], r["status"]) for r in rows]  # undated lead, not proof


def test_identity_needs_an_exact_unambiguous_name_match():
    assert pick_artist("Lunar Vacation", [{**LUNAR, "name": "Lunar Vacations"}])[0] is None
    assert pick_artist("Lunar Vacation", [{**LUNAR, "score": 70}])[0] is None
    twins = [{**DEHD, "id": "a", "name": "Tennis", "tags": [{"name": "indie pop"}]},
             {**DEHD, "id": "b", "name": "Tennis", "tags": [{"name": "hardcore"}]}]
    assert pick_artist("Tennis", twins)[0] is None                       # ambiguous without genres
    assert pick_artist("Tennis", twins, ["Indie Pop"])[0]["id"] == "a"  # Qloo genres break the tie


def test_lookups_are_cached_and_sent_with_a_user_agent():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"artists": [LUNAR]})
    mb = MusicBrainz(transport=httpx.MockTransport(handler))
    assert mb.evidence("Lunar Vacation", ["Atlanta"], "city")[0]["kind"] == "formed_here"
    assert MusicBrainz(transport=httpx.MockTransport(handler)).evidence("Lunar Vacation", ["Atlanta"], "city")
    assert len(seen) == 1 and "OpeningAct" in seen[0].headers["user-agent"]


def test_errors_and_offline_mode_never_invent_evidence():
    mb = MusicBrainz(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert mb.evidence("Lunar Vacation", ["Atlanta"], "city") == []
    assert MusicBrainz(offline=True).evidence("Unknown Band", ["Atlanta"], "city") == []


def test_agent_uses_musicbrainz_alongside_web_evidence():
    class FakeMB:
        def evidence(self, name, place_terms, scope_label, genres=None):
            return claims(name, LUNAR, place_terms, scope_label) if name == "Lunar Vacation" else []
    q = FakeQloo({"japanese breakfast": [{"entity_id": "SEED", "name": "Japanese Breakfast"}]},
                 {"": [], "atlanta, georgia": [ent("L", "Lunar Vacation", 0.97), ent("X", "Other", 0.96)]})
    bid = db.insert_brief(ShowIn(headliner="Japanese Breakfast", city="Atlanta, Georgia"), False)
    agent.run(bid, qloo=q, evidence=FakeEvidence(), mb=FakeMB())
    lv = next(c for c in db.get_candidates(bid) if c["artist_name"] == "Lunar Vacation")
    assert lv["local_proof"] and lv["evidence"][0]["source_label"] == "musicbrainz"


def test_an_ended_group_is_marked_inactive():
    from backend.musicbrainz import claims
    a = {"id": "x", "name": "Old Band", "type": "Group", "life-span": {"ended": True, "end": "2019"},
         "begin-area": {"name": "Philadelphia"}}
    rows = claims("Old Band", a, ["Philadelphia"], "city")
    assert any(r["kind"] == "inactive" and "2019" in r["quote"] for r in rows)
    person = {**a, "type": "Person"}
    assert not any(r["kind"] == "inactive" for r in claims("Old Band", person, ["Philadelphia"], "city"))
