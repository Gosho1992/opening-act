"""Verification cases taken from real search results that fooled the first version."""
from backend.evidence import (TavilyEvidence, clean_mentions, is_listing_page, judge, qloo_birthplace_evidence,
                              rule_verdict)


def hit(title, content, url="https://example.org/a"):
    return {"title": title, "content": content, "url": url}


class Provider(TavilyEvidence):
    """Tavily provider with canned hits and an optional scripted verifier."""

    def __init__(self, hits, verifier=None):
        super().__init__(api_key="x", verifier=verifier or (lambda *a: (_ for _ in ()).throw(RuntimeError())))
        self._hits = hits

    def _search(self, query):
        return self._hits if "live concert" not in query else []


def test_name_inside_a_longer_name_is_not_the_artist():
    assert clean_mentions("Whitney", "a 1991 live performance by Whitney Houston.", ["Nashville"]) == 0
    assert clean_mentions("SAULT", "Thank you Sault Ste Marie!!! We're in Nashville now", ["Nashville"]) == 0
    assert clean_mentions("Grimes", "Ty Grimes is an American drummer who resides in Austin", ["Austin"]) == 0
    assert clean_mentions("Grimes", "I am Sid Grimes, a singer-songwriter living in North Austin", ["Austin"]) == 0
    assert clean_mentions("Tennis", "a women’s tour-level tennis tournament in Austin", ["Austin"]) == 0
    assert clean_mentions("Grimes", "Grimes performing Vanessa at ACL Festival in Austin", ["Austin"]) == 1
    assert clean_mentions("Soccer Mommy", "Nashville-based Soccer Mommy played Atlanta", ["Atlanta"]) == 1


def test_ticket_and_listing_pages_are_rejected():
    assert is_listing_page("https://seatgeek.com/mitski-tickets/atlanta")
    assert is_listing_page("https://www.ticketmaster.com/snail-mail-tickets/artist/2359933")
    assert not is_listing_page("https://www.redandblack.com/culture/lunar-vacation")


def test_rule_separates_based_here_from_played_here():
    t = "Tennis, the Denver-based indie-pop duo of Alaina Moore and Patrick Riley. Tennis performs in Austin, TX"
    v = rule_verdict("Tennis", t, ["Austin"])
    assert v["relation"] == "played_here"
    v = rule_verdict("Lunar Vacation", "Atlanta-based band Lunar Vacation dreams big", ["Atlanta"])
    assert v["relation"] == "based_here" and v["same_artist"] == "yes"
    v = rule_verdict("Soccer Mommy", "Nashville-based Soccer Mommy played Variety Playhouse in Atlanta", ["Atlanta"])
    assert v["relation"] == "played_here"


def test_quote_must_be_in_the_page_and_name_the_area():
    h = hit("Lunar Vacation", "Atlanta-based band Lunar Vacation dreams big")
    ok = judge("Lunar Vacation", h, {"same_artist": "yes", "relation": "based_here",
                                     "quote": "Atlanta-based band Lunar Vacation"}, ["Atlanta"], "city")
    assert ok["status"] == "verified" and ok["kind"] == "based_here"
    thin = hit("Lunar Vacation review", "Lunar Vacation played the Earl in Atlanta last night")
    made_up = judge("Lunar Vacation", thin, {"same_artist": "yes", "relation": "based_here",
                                             "quote": "Lunar Vacation formed in Atlanta in 2016"}, ["Atlanta"], "city")
    assert made_up["status"] == "unverified"
    # a wrong quote on a page that does state it: the page's own sentence is used instead
    fixed = judge("Lunar Vacation", h, {"same_artist": "yes", "relation": "based_here",
                                        "quote": "Lunar Vacation formed in Atlanta in 2016"}, ["Atlanta"], "city")
    assert fixed["status"] == "verified" and "Atlanta-based" in fixed["quote"]
    other = judge("Whitney", h, {"same_artist": "no", "relation": "played_here", "quote": ""}, ["Atlanta"], "city")
    assert other["status"] == "rejected"


def test_model_label_is_not_trusted_without_a_sentence_that_states_it():
    """Real false positives from the 2026-10-28 live run."""
    cases = [
        ("The Nude Party", "The group gained the attention of Nashville-and-Athens-based label New West Records.",
         ["Nashville"]),
        ("Dehd", "I'm from Atlanta. But the band is from Chicago.", ["Atlanta"]),
        ("Suki Waterhouse", "We went beyond the music with Suki Waterhouse at Bonus Tracks - in Austin, TX.", ["Austin"]),
        ("Girlpool", "Girlpool review. austin, tx based indie music blog.", ["Austin"]),
    ]
    for artist, text, places in cases:
        quote = text.split(". ")[-1] if artist == "Girlpool" else text
        row = judge(artist, hit(artist, text), {"same_artist": "yes", "relation": "based_here", "quote": quote},
                    places, "city")
        assert row["status"] == "unverified", (artist, row["reason"])


def test_provider_end_to_end_with_rule_fallback():
    hits = [hit("Whitney Houston", "The Voice of Whitney is coming to Nashville's Schermerhorn Symphony Center "
                "featuring Whitney Houston recordings", "https://facebook.com/WhitneyHouston/posts/1"),
            hit("Whitney Austin tickets", "Whitney in Nashville", "https://seatgeek.com/whitney-tickets/nashville"),
            hit("Whitney", "Whitney is a folk rock band from Chicago. Whitney played Nashville's Basement East.",
                "https://example.org/w")]
    rows = Provider(hits).search("Whitney", ["Nashville"], "city")
    by_url = {r["source_url"]: r for r in rows}
    assert by_url["https://seatgeek.com/whitney-tickets/nashville"]["status"] == "rejected"
    assert all(r["kind"] != "based_here" or r["status"] != "verified" for r in rows)
    assert by_url["https://example.org/w"]["kind"] == "played_here"


def test_provider_uses_llm_verdicts_and_checks_them():
    hits = [hit("Lunar Vacation", "Atlanta-based band Lunar Vacation dreams big"),
            hit("Lunar Vacation review", "Lunar Vacation played Atlanta's Earl last night", "https://example.org/b")]
    verdicts = [{"same_artist": "yes", "relation": "based_here", "quote": "Atlanta-based band Lunar Vacation"},
                {"same_artist": "yes", "relation": "based_here", "quote": "Lunar Vacation are from Atlanta"}]
    rows = Provider(hits, verifier=lambda *a: verdicts).search("Lunar Vacation", ["Atlanta"], "city")
    assert [r["status"] for r in rows] == ["verified", "unverified"]
    assert rows[0]["method"] == "llm"


def test_birthplace_evidence_only_when_place_matches():
    assert qloo_birthplace_evidence("X", "Athens, Limestone County, Alabama", ["Nashville"], "city") == []
    ev = qloo_birthplace_evidence("X", "Atlanta, Fulton County, Georgia", ["Atlanta"], "city")
    assert ev[0]["source_label"] == "qloo" and ev[0]["status"] == "verified" and ev[0]["kind"] == "formed_here"


def test_current_base_and_origin_are_different_claims():
    from backend.evidence import states_connection as sc
    assert sc("Faye Webster", "Faye Webster is an Atlanta-based singer-songwriter.", ["Atlanta"]) == "based_here"
    assert sc("Ron Gallo", "Ron Gallo, who moved to Nashville in 2014, plays tonight.", ["Nashville"]) == "based_here"
    assert sc("Lunar Vacation", "Lunar Vacation are an American indie rock band from Atlanta, Georgia.",
              ["Atlanta"]) == "formed_here"
    assert sc("Soccer Mommy", "Soccer Mommy grew up in Nashville.", ["Nashville"]) == "formed_here"


def test_negated_past_and_hearsay_statements_do_not_count():
    from backend.evidence import states_connection as sc
    assert sc("Ron Gallo", "Ron Gallo is a Philadelphia-based musician, who has also spent time living in Nashville.",
              ["Nashville"]) is None
    assert sc("Tennis", "Tennis are not based in Austin, despite the rumours.", ["Austin"]) is None
    assert sc("Whitney", "Whitney, formerly based in Nashville, now live in Chicago.", ["Nashville"]) is None
    assert sc("Girlpool", "A blog claimed Girlpool were from Austin.", ["Austin"]) is None


def test_place_belonging_to_another_named_band_does_not_count():
    from backend.evidence import states_connection as sc
    s = ("Brittany Howard is also a singer in the band Bermuda Triangle with Jesse Lafser and Becca Mancari, "
         "which was formed in Nashville in 2017.")
    assert sc("Brittany Howard", s, ["Nashville"]) is None


def test_judge_relabels_by_what_the_sentence_says():
    h = hit("Lunar Vacation", "Lunar Vacation are an American indie rock band from Atlanta, Georgia.")
    row = judge("Lunar Vacation", h, {"same_artist": "yes", "relation": "based_here",
                                      "quote": "Lunar Vacation are an American indie rock band from Atlanta, Georgia."},
                ["Atlanta"], "city")
    assert row["status"] == "verified" and row["kind"] == "formed_here"


def test_place_must_belong_to_the_artist_not_another_person():
    """Real false positives from the 2026-10-29 live run."""
    from backend.evidence import states_connection as sc
    assert sc("Cory Wong", "Petar Janjic is best known for playing with Cory Wong these days, but stays busy "
                           "playing live and recording as a Nashville-based session drummer.", ["Nashville"]) is None
    assert sc("Molly Burch", "Molly Burch: Well, we're just driving up to New York, so – my mom lives in Atlanta, "
                             "so we just got in last night.", ["Atlanta"]) is None
    # still accepted: appositions, descriptors before the name, birth names in brackets
    assert sc("Ron Gallo", "Meet Ron Gallo, the Nashville-based musician voicing a revved-up agenda", ["Nashville"])
    assert sc("Molly Burch", "Austin-based (via Los Angeles) singer songwriter Molly Burch has been out on the road.",
              ["Austin"]) == "based_here"
    assert sc("Shakey Graves", "Shakey Graves (born Alejandro Rose-Garcia; June 4, 1987) is an American musician "
                               "from Austin, Texas.", ["Austin"]) == "formed_here"
    assert sc("Wilco", "Wilco is an American rock band based in Chicago.", ["Chicago"]) == "based_here"
