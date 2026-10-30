"""Adversarial edge-case tests for Opening Act.

Place this file at: opening-act/tests/test_edge_cases.py
Run with: py -m pytest tests/test_edge_cases.py -v

These tests use no network/API keys. A few identity/verification tests are intentionally
strict: if they fail, they expose false-positive local evidence rather than a broken test.

Ported to the v2 evidence pipeline (search -> identity checks -> verification). `classify_hit`
is now a small helper that runs ONE search hit through the real pipeline with the no-LLM
rule verifier, and returns the first row that was not rejected (None = not accepted).
"""

import math

import pytest

from backend import rules
from backend.evidence import TavilyEvidence, qloo_birthplace_evidence


def hit(title="", content="", url="https://example.org/article"):
    return {"title": title, "content": content, "url": url}


class _OneHit(TavilyEvidence):
    """The real search+verify pipeline fed with one canned hit; no network, no LLM."""

    def __init__(self, h):
        def no_llm(*_):
            raise RuntimeError("no LLM in tests")  # forces the strict sentence rule
        super().__init__(api_key="test", verifier=no_llm)
        self._hit = h

    def _search(self, query):
        return [self._hit] if "live concert" not in query else []


def classify_hit(artist, h, place_terms, scope_label):
    """Accepted evidence row for one hit, or None if every reading of it was rejected."""
    rows = _OneHit(h).search(artist, place_terms, scope_label)
    return next((r for r in rows if r["status"] != "rejected"), None)


# --------------------------- Evidence: adversarial cases ---------------------------

def test_evidence_rejects_missing_or_non_string_url():
    assert classify_hit("Lunar Vacation", hit("Lunar Vacation in Atlanta", "show", None), ["Atlanta"], "city") is None
    assert classify_hit("Lunar Vacation", hit("Lunar Vacation in Atlanta", "show", ""), ["Atlanta"], "city") is None
    # Desired contract: malformed URLs must not become source evidence.
    assert classify_hit("Lunar Vacation", hit("Lunar Vacation in Atlanta", "show", 123), ["Atlanta"], "city") is None


def test_evidence_rejects_empty_artist_and_empty_place_terms():
    assert classify_hit("", hit("Atlanta concert", "Atlanta show"), ["Atlanta"], "city") is None
    assert classify_hit("Lunar Vacation", hit("Lunar Vacation concert in Atlanta", "tickets"), [], "city") is None


def test_evidence_rejects_artist_mentioned_without_city_connection():
    assert classify_hit(
        "Lunar Vacation",
        hit("Lunar Vacation announces album", "The band announced a new record.", "https://example.org/news"),
        ["Atlanta"],
        "city",
    ) is None


def test_evidence_rejects_place_substrings_not_whole_place_names():
    assert classify_hit(
        "Lunar Vacation", hit("Lunar Vacation", "Lunar Vacation played Austinville."), ["Austin"], "city"
    ) is None


def test_evidence_does_not_confuse_short_artist_name_with_longer_artist_identity():
    """Known risk: 'Whitney' can match a page about Whitney Houston/tribute acts."""
    result = classify_hit(
        "Whitney",
        hit("Whitney Houston tribute night in Nashville", "Tickets for a Whitney Houston tribute show in Nashville."),
        ["Nashville"],
        "city",
    )
    assert result is None, f"False identity match accepted as evidence: {result}"


def test_evidence_does_not_treat_common_word_artist_as_unrelated_topic_evidence():
    """Known risk: Tennis is both a band name and a sport."""
    result = classify_hit(
        "Tennis",
        hit("Tennis tournament returns to Austin", "Tennis fans gather in Austin for the regional tournament."),
        ["Austin"],
        "city",
    )
    assert result is None, f"Likely sports result accepted as band evidence: {result}"


def test_evidence_does_not_accept_unrelated_city_just_because_artist_is_named():
    assert classify_hit(
        "Japanese Breakfast",
        hit("Japanese Breakfast in Boston", "Japanese Breakfast announces a Boston concert."),
        ["Atlanta", "Decatur", "Athens"],
        "metro",
    ) is None


def test_evidence_keeps_source_excerpt_and_url_for_a_valid_local_hit():
    result = classify_hit(
        "Lunar Vacation",
        hit("Lunar Vacation is an Atlanta-based band", "Atlanta-based band Lunar Vacation will perform at a local venue."),
        ["Atlanta"],
        "city",
    )
    assert result is not None
    assert result["source_url"].startswith("https://")
    assert result["excerpt"]
    assert result["scope"] == "city"
    # v2: a valid local hit is verified "based here", with the supporting sentence quoted
    assert result["status"] == "verified" and result["kind"] == "based_here"
    assert "Atlanta-based" in result["quote"]
    assert rules.local_score([result]) >= rules.LOCAL_PROOF_THRESHOLD


def test_birthplace_evidence_rejects_empty_or_unmatched_places():
    assert qloo_birthplace_evidence("Artist", None, ["Austin"], "city") == []
    assert qloo_birthplace_evidence("Artist", "Atlanta, Georgia", ["Austin"], "city") == []
    assert qloo_birthplace_evidence("Artist", "", ["Austin"], "city") == []


def test_birthplace_evidence_matches_place_as_a_whole_term():
    assert qloo_birthplace_evidence("Artist", "Austinville, Texas", ["Austin"], "city") == []


# --------------------------- Ranking and numeric boundaries ------------------------

@pytest.mark.parametrize("affinity,low,high,delta,new_city", [
    (0.0, 0.0, 1.0, None, False),
    (1.0, 0.0, 1.0, None, False),
    (None, None, None, None, False),
    (0.8, 0.8, 0.8, None, False),
    (0.8, 0.8, 0.8, 999, True),
    (-10.0, 0.0, 1.0, -999, False),
    (10.0, 0.0, 1.0, 999, False),
])
def test_taste_score_always_stays_in_unit_interval(affinity, low, high, delta, new_city):
    score = rules.taste_score(affinity, low, high, delta, new_city)
    assert 0.0 <= score <= 1.0
    assert math.isfinite(score)


def test_weight_normalization_handles_zero_missing_and_extreme_values():
    assert rules.normalize_weights({"taste": 1}) == {"taste": 1.0, "local": 0.0, "practical": 0.0}
    normalized = rules.normalize_weights({"taste": 1e300, "local": 1e300, "practical": 0})
    assert sum(normalized.values()) == pytest.approx(1.0)
    assert all(0.0 <= v <= 1.0 for v in normalized.values())


@pytest.mark.parametrize("bad", [
    {"taste": True},
    {"local": float("inf")},
    {"practical": float("-inf")},
    {"taste": -0.0001},
    {"taste": 0, "local": 0, "practical": 0},
    {"unexpected": 1},
    "taste",
    None,
])
def test_weight_validation_rejects_invalid_values(bad):
    if bad is None:
        # None intentionally means defaults, not invalid input.
        assert rules.normalize_weights(None) == rules.DEFAULT_WEIGHTS
    else:
        with pytest.raises(rules.WeightsError):
            rules.normalize_weights(bad)


def test_local_score_is_zero_for_no_evidence_or_rejected_evidence():
    # v2 labels: kind based_here/played_here/none, status verified/unverified/rejected
    assert rules.local_score([]) == 0.0
    assert rules.local_score(None) == 0.0
    assert rules.local_score([{"kind": "based_here", "status": "rejected"}]) == 0.0
    assert rules.local_score([{"kind": "none", "status": "rejected"}]) == 0.0


def test_unverified_or_played_here_never_reaches_local_proof():
    for row in ({"kind": "based_here", "status": "unverified"}, {"kind": "played_here", "status": "verified"},
                {"kind": "played_here", "status": "unverified"}):
        assert rules.local_score([row]) < rules.LOCAL_PROOF_THRESHOLD


def test_local_score_does_not_crash_on_unrecognized_kind_or_status():
    assert rules.local_score([{"kind": "made_up_kind", "status": "made_up_status"}]) == 0.0
    assert rules.local_score([{}]) == 0.0
    # old v1 labels must not sneak through as proof
    assert rules.local_score([{"kind": "hometown", "confidence": "supported"}]) == 0.0


@pytest.mark.parametrize("capacity", [1, 499, 500, 1499, 1500, 2999, 3000, 1000000])
def test_practical_score_has_valid_output_at_capacity_boundaries(capacity):
    score, explanation = rules.practical_score(100_000, capacity)
    assert 0.0 <= score <= 1.0
    assert isinstance(explanation, str) and explanation


def test_practical_score_handles_unknown_listener_count_at_capacity_boundaries():
    for capacity in [1, 499, 500, 1499, 1500, 2999, 3000, 1000000]:
        score, explanation = rules.practical_score(None, capacity)
        assert score == 0.5
        assert "unknown" in explanation.lower()


def test_total_score_is_bounded_for_valid_scores():
    candidates = [
        {"taste_score": 0.0, "local_score": 0.0, "practical_score": 0.0},
        {"taste_score": 1.0, "local_score": 1.0, "practical_score": 1.0},
        {"taste_score": 0.2, "local_score": 0.8, "practical_score": 0.5},
    ]
    for candidate in candidates:
        score = rules.total(candidate, None)
        assert 0.0 <= score <= 1.0


def test_city_scope_handles_whitespace_and_state_abbreviation():
    scopes = rules.scopes("  Austin , TX  ")
    assert scopes[0]["query"] == "Austin, Texas"
    assert scopes[1]["label"] == "metro"
    assert scopes[2]["query"] == "Texas"


def test_city_scope_without_state_does_not_invent_region():
    scopes = rules.scopes("Austin")
    assert scopes[0]["query"] == "Austin"
    assert scopes[2] is None
