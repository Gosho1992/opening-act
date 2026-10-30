"""Where is the act NOW? Cases from the 29 Oct external review, checked against Wikipedia and interviews."""
from backend import rules
from backend.evidence import location_rows, source_year


def hit(title, content, url):
    return {"title": title, "content": content, "url": url}


WIDE_ATL = ["Atlanta", "Decatur", "Athens", "Georgia", "GA"]


def row(kind, status="verified", year=None, url="https://example.org/x", source="public", place=None):
    return {"kind": kind, "status": status, "source_year": year, "source_url": url, "source_label": source,
            "place": place, "quote": "q"}


def test_source_year_comes_from_url_dates_not_from_text():
    assert source_year({"url": "https://www.anchrmagazine.com/interviews/2017/3/20/a-chat-with-molly-burch"}) == 2017
    assert source_year({"url": "https://www.godisinthetvzine.co.uk/2025/06/02/in-conversation-girlpuppy/"}) == 2025
    assert source_year({"url": "https://example.org/band", "content": "formed in 2016"}) is None


def test_finds_a_move_and_a_retirement_on_encyclopedia_pages():
    mam = hit("Mamalarky - Wikipedia", "Mamalarky is an American indie rock band. The group is based in Los Angeles, "
              "California.", "https://en.wikipedia.org/wiki/Mamalarky")
    rows = location_rows("Mamalarky", mam, ["Atlanta"], WIDE_ATL, "city")
    assert rows and rows[0]["kind"] == "based_elsewhere"  # 'The group' on the band's own page
    mam2 = hit("Mamalarky - Wikipedia", "Mamalarky is an American indie rock band based in Los Angeles, California.",
               "https://en.wikipedia.org/wiki/Mamalarky")
    rows = location_rows("Mamalarky", mam2, ["Atlanta"], WIDE_ATL, "city")
    assert [r["kind"] for r in rows] == ["based_elsewhere"] and rows[0]["place"].startswith("Los Angeles")
    mb = hit("Molly Burch - Wikipedia", "In 2025, Molly Burch retired from music to start Picnic Studio.",
             "https://en.wikipedia.org/wiki/Molly_Burch")
    assert [r["kind"] for r in location_rows("Molly Burch", mb, ["Austin"], ["Austin", "Texas"], "city")] == ["inactive"]


def test_places_inside_the_wider_area_are_not_counted_as_elsewhere():
    h = hit("Lunar Vacation", "Lunar Vacation is a Georgia-based indie band.", "https://example.org/lv")
    assert location_rows("Lunar Vacation", h, ["Atlanta"], WIDE_ATL, "city") == []


def test_status_moved_when_a_newer_or_encyclopedic_source_says_elsewhere():
    s = rules.local_status([row("based_here", year=2017),
                            row("based_elsewhere", url="https://en.wikipedia.org/wiki/X", place="Los Angeles")],
                           now_year=2026)
    assert s["status"] == "moved" and s["score"] < rules.LOCAL_PROOF_THRESHOLD and "Los Angeles" in s["note"]


def test_status_based_now_requires_a_recent_source():
    assert rules.local_status([row("based_here", year=2025)], now_year=2026)["status"] == "based_now"
    old = rules.local_status([row("based_here", year=2017)], now_year=2026)
    assert old["status"] == "based_as_of" and "2017" in old["note"] and old["score"] >= rules.LOCAL_PROOF_THRESHOLD
    # a recent local source outranks an older one placing the act elsewhere
    s = rules.local_status([row("based_here", year=2025), row("based_elsewhere", year=2019, place="Brooklyn")],
                           now_year=2026)
    assert s["status"] == "based_now"


def test_origin_only_from_structured_data_is_disputed_when_a_source_says_it_began_elsewhere():
    s = rules.local_status([row("formed_here", source="musicbrainz", url="https://musicbrainz.org/artist/x"),
                            row("formed_elsewhere", place="Boston")], now_year=2026)
    assert s["status"] == "disputed" and s["score"] < rules.LOCAL_PROOF_THRESHOLD
    assert rules.local_status([row("formed_here")], now_year=2026)["status"] == "roots"


def test_musicbrainz_area_alone_is_not_local_proof():
    assert rules.local_score([row("based_here", status="unverified", source="musicbrainz")]) < rules.LOCAL_PROOF_THRESHOLD


def test_inactive_note():
    assert rules.inactive_note([row("inactive")]) and rules.inactive_note([row("based_here")]) is None


def test_full_name_on_own_page_and_other_bands_place():
    from backend.evidence import states_connection, own_page_subject
    s = own_page_subject("Faye Webster", "Faye Connell Webster (born June 25, 1997) is an American singer-songwriter "
                         "from Atlanta, Georgia.", True)
    assert states_connection("Faye Webster", s, ["Atlanta"]) == "formed_here"
    ron = hit("Ron Gallo - Wikipedia", "Ron Gallo began a solo career in 2014, after fronting various bands including "
              "Philadelphia-based band Toy Soldiers.", "https://en.wikipedia.org/wiki/Ron_Gallo")
    assert location_rows("Ron Gallo", ron, ["Nashville"], ["Nashville", "Tennessee"], "city") == []
    assert states_connection("Lunar Vacation", "Atlanta-based band Lunar Vacation dreams big", ["Atlanta"]) == "based_here"


def test_from_must_describe_the_artist_and_pronouns_need_an_identity_verb():
    from backend.evidence import states_connection, own_page_subject
    s = own_page_subject("Brittany Howard", "She became inspired to do a solo album after a long road trip that "
                         "went from Nashville to the West Coast.", True)
    assert states_connection("Brittany Howard", s, ["Nashville"]) is None
    assert states_connection("Soccer Mommy", "Since Soccer Mommy is from Nashville, the scene matters.",
                             ["Nashville"]) == "formed_here"
    assert states_connection("All Them Witches", "All Them Witches is an American rock band from Nashville, "
                             "Tennessee.", ["Nashville"]) == "formed_here"
    assert states_connection("Lunar Vacation", "Lunar Vacation are an American indie rock band from Atlanta, Georgia.",
                             ["Atlanta"]) == "formed_here"
    s2 = own_page_subject("Mamalarky", "The group is based in Los Angeles, California.", True)
    assert s2.startswith("Mamalarky is based")


# ── pronouns follow the person the page is talking about ─────────────────────
def test_pronoun_in_a_band_members_bio_is_not_the_artist():
    from backend.evidence import judge
    hit = {"title": "Cory Wong Camp | Instructors", "url": "https://corywongcamp.com",
           "content": "Kevin Gastonguay is a keyboardist based in Nashville, Tennessee. He is best known as "
                      "Cory Wong's longtime keyboardist. Petar Janjic is best known for playing with Cory Wong. "
                      "He moved to Nashville in 2019."}
    v = {"same_artist": "yes", "relation": "based_here", "quote": "He moved to Nashville in 2019.", "method": "llm"}
    row = judge("Cory Wong", hit, v, ["Nashville", "Tennessee"], "city")
    assert row["status"] != "verified"


def test_wikipedia_participle_base_is_read_as_elsewhere():
    from backend.evidence import location_rows
    hit = {"title": "Molly Burch - Wikipedia", "url": "https://en.wikipedia.org/wiki/Molly_Burch",
           "content": "Molly Burch (born October 23, 1990) is an American singer-songwriter. Based in Los Angeles, "
                      "California, her music often reflects themes of heartbreak and loss."}
    rows = location_rows("Molly Burch", hit, ["Austin"], ["Austin", "Texas"], "city")
    assert any(r["kind"] == "based_elsewhere" and "Los Angeles" in r["place"] for r in rows)


def test_pronoun_still_resolves_on_the_artists_own_bio():
    from backend.evidence import page_sentences
    sents = page_sentences("Ron Gallo", "Ron Gallo is a musician from Philadelphia. He moved to Nashville in 2016.", True)
    assert sents[1].startswith("Ron Gallo moved to Nashville")
