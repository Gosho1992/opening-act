import pytest

from backend import rules


def test_weights_normalized_and_validated():
    assert rules.normalize_weights(None) == rules.DEFAULT_WEIGHTS
    w = rules.normalize_weights({"taste": 2, "local": 2})
    assert w == {"taste": 0.5, "local": 0.5, "practical": 0.0}
    for bad in ({"taste": -1}, {"taste": 0, "local": 0}, {"vibes": 1}, {"taste": "high"}, {"taste": float("nan")}, [1]):
        with pytest.raises(rules.WeightsError):
            rules.normalize_weights(bad)


def test_total_survives_missing_weight_keys():
    c = {"taste_score": 1.0, "local_score": 0.0, "practical_score": 0.0}
    assert rules.total(c, {"taste": 1}) == 1.0


def test_taste_is_relative_within_query():
    assert rules.taste_score(0.97, 0.95, 0.97, None, False) == 1.0
    assert rules.taste_score(0.95, 0.95, 0.97, None, False) == 0.5
    assert rules.taste_score(None, None, None, None, False) == 0.5
    assert rules.taste_score(0.95, 0.95, 0.97, None, True) > rules.taste_score(0.95, 0.95, 0.97, None, False)


def test_only_verified_based_here_reaches_local_proof():
    T = rules.LOCAL_PROOF_THRESHOLD
    assert rules.local_score([{"kind": "based_here", "status": "verified"}]) >= T
    assert rules.local_score([{"kind": "based_here", "status": "unverified"}]) < T
    assert rules.local_score([{"kind": "played_here", "status": "verified"}]) < T
    assert rules.local_score([{"kind": "none", "status": "rejected"}]) == 0.0
    assert rules.plays_market([{"kind": "played_here", "status": "verified"}])


def test_popularity_cap_scales_with_room():
    assert rules.popularity_cap(300) < rules.popularity_cap(800) < rules.popularity_cap(2000)
    assert rules.popularity_cap(10000) is None


def test_practical_neutral_when_listeners_unknown():
    assert rules.practical_score(None, 800)[0] == 0.5
    assert rules.practical_score(100_000, 800)[0] > rules.practical_score(5_000_000, 800)[0]


def test_scopes_from_city_and_state():
    s = rules.scopes("Austin, TX")
    assert [x["label"] for x in s] == ["city", "metro", "region"]
    assert s[0]["query"] == "Austin, Texas" and s[2]["query"] == "Texas"
    assert "TX" in s[2]["place_terms"]
    assert rules.scopes("Austin")[2] is None


# ── fit floor: a local act with no Qloo fit signal is a lead, never an automatic pick ──
def test_fit_evidence_from_qloo_affinity_or_tag_overlap():
    from backend import rules
    assert rules.fit_evidence({"city_affinity": 0.4, "tag_overlap": 0.0})[0]          # Qloo relates it to the fans
    assert rules.fit_evidence({"city_affinity": None, "tag_overlap": 0.24})[0]        # All Them Witches
    assert not rules.fit_evidence({"city_affinity": None, "tag_overlap": 0.0})[0]     # Margo Price for Khruangbin
    assert not rules.fit_evidence({"city_affinity": None, "tag_overlap": 0.04})[0]    # The Lone Bellow
    assert not rules.fit_evidence({"city_affinity": None, "tag_overlap": None})[0]


def test_qualified_needs_both_local_and_fit():
    from backend import rules
    assert rules.qualified({"local_score": 0.75, "city_affinity": None, "tag_overlap": 0.24})
    assert not rules.qualified({"local_score": 1.0, "city_affinity": None, "tag_overlap": 0.0})
    assert not rules.qualified({"local_score": 0.3, "city_affinity": 0.9, "tag_overlap": 0.7})


def test_undated_wikipedia_local_claim_is_not_current_but_wikipedia_elsewhere_can_mean_moved():
    from backend import rules
    wiki_local = {"kind": "based_here", "status": "verified", "source_url": "https://en.wikipedia.org/wiki/X"}
    st = rules.local_status([wiki_local], now_year=2026)
    assert st["status"] == "based_as_of" and "Wikipedia" in st["note"]
    old_local = {"kind": "based_here", "status": "verified", "source_year": 2017, "source_url": "https://a.com"}
    wiki_else = {"kind": "based_elsewhere", "status": "verified", "place": "Los Angeles",
                 "source_url": "https://en.wikipedia.org/wiki/X"}
    assert rules.local_status([old_local, wiki_else], now_year=2026)["status"] == "moved"


# ── Booking Decision Gate ──────────────────────────────────────────────────────
def _cand(**kw):
    base = {"city_affinity": 0.5, "tag_overlap": 0.4, "local_status": "based_now", "local_note": "Based here (source 2025)",
            "popularity": 0.9, "monthly_listeners": 100_000, "newest_source_year": 2025, "excluded_reason": None}
    return {**base, **kw}


def test_gate_passes_a_local_fitting_right_sized_active_act():
    from backend import rules
    g = rules.booking_gate(_cand(), 800, now_year=2026)
    assert g["passed"] and all(v["state"] == "pass" for v in g["checks"].values())


def test_gate_blocks_an_act_too_big_for_the_room_even_with_the_filter_off():
    from backend import rules
    g = rules.booking_gate(_cand(popularity=0.99), 800, now_year=2026)          # Japanese Breakfast for Big Thief
    assert not g["passed"] and g["checks"]["size"]["state"] == "fail"
    g = rules.booking_gate(_cand(monthly_listeners=5_000_000), 800, now_year=2026)
    assert not g["passed"] and g["checks"]["size"]["state"] == "fail"


def test_gate_cautions_do_not_block_but_are_reported():
    from backend import rules
    g = rules.booking_gate(_cand(local_status="based_as_of", monthly_listeners=None, newest_source_year=2018), 800,
                           now_year=2026)
    assert g["passed"]
    assert {k for k, v in g["checks"].items() if v["state"] == "caution"} == {"local", "size", "activity"}
    assert "activity needs confirmation" in g["checks"]["activity"]["note"]


def test_gate_blocks_weak_fit_disputed_and_moved():
    from backend import rules
    assert not rules.booking_gate(_cand(city_affinity=None, tag_overlap=0.0), 800)["passed"]   # Margo Price / Khruangbin
    assert not rules.booking_gate(_cand(local_status="disputed"), 800)["passed"]
    assert not rules.booking_gate(_cand(local_status="moved"), 800)["passed"]
    assert not rules.booking_gate(_cand(local_status="played"), 800)["passed"]


def test_gate_status_qualified_provisional_blocked():
    from backend import rules
    assert rules.booking_gate(_cand(), 800, now_year=2026)["status"] == "qualified"
    g = rules.booking_gate(_cand(local_status="based_as_of", newest_source_year=None), 800, now_year=2026)
    assert g["status"] == "provisional" and g["to_confirm"] == ["local", "activity"]
    assert rules.booking_gate(_cand(popularity=0.99), 800, now_year=2026)["status"] == "blocked"


def test_city_nicknames_are_expanded():
    from backend import rules
    assert rules.normalize_city("NOLA") == "New Orleans, Louisiana"
    assert rules.normalize_city("nola, LA") == "New Orleans, Louisiana"
    assert rules.normalize_city("Philly") == "Philadelphia, Pennsylvania"
    assert rules.normalize_city("Nashville, Tennessee") == "Nashville, Tennessee"
    assert rules.normalize_city("La Crosse, Wisconsin") == "La Crosse, Wisconsin"
