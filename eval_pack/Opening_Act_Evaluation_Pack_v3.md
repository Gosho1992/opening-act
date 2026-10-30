# Opening Act: evaluation pack for independent review (version 3)

## How to use this pack (instructions for the reviewing model)

You are reviewing **Opening Act**, an entry in the Qloo Agentic Hackathon (Oct 2026). The developer wants an independent, critical check, not praise. Please:

1. Read sections 1-7. The full structured data (every Qloo call, every agent decision, ranked shortlists with quotes and URLs, all comparison lists) is in the companion file `opening_act_eval_data_v3.json`.
2. Check whether the **rules** (section 2) are sound and whether the **evaluations** (section 6) measure what they claim.
3. Spot-check the **outputs** (section 5) against your own knowledge: is each 'verified local' act really based in that city now? Is each 'moved' or 'disputed' flag right? Would each pick suit the headliner's audience in an 800-capacity room?
4. Answer every question in section 8 with: verdict (agree / partly / disagree), reason, concrete fix.
5. End with the 3 changes that would most improve the product, and the 3 that would most improve the credibility of the results.

Where you are unsure whether an artist is currently local, say so rather than guessing.

---

## 1. What the product does

A talent buyer at an independent venue enters a **headliner**, a **city** and a **venue capacity**. Opening Act builds a shortlist of **support (opening) acts** that (a) suit the headliner's audience, (b) are realistically sized for the room and (c) are verifiably local to the city now. Every claim links to its quoted source. If fewer than 3 acts are verified local, it returns a **research shortlist** with no automatic picks instead of a recommendation. Availability, fee and headliner approval are always shown as unknown.

Data sources: **Qloo** Taste AI (hackathon API: `/search`, `/v2/insights`, `/entities`), **Tavily** web search, **MusicBrainz**, and an LLM (OpenAI gpt-4o-mini) used for three things only: a *hint* when reading web pages (the code decides), naming candidate local acts (leads only, checked like any other), and the summary text.

## 2. Pipeline and every rule (exact values)

**Discovery (agent loop; area widens city -> metro -> state):**

1. Resolve the headliner to one Qloo entity (`/search`; only a unique exact name match is accepted).
2. Read the headliner's Qloo taste tags (`/entities`).
3. Ask Qloo for 50 artists the headliner's fans like, with the area as location signal (`/v2/insights`, `signal.interests.entities`, `signal.location.query`) and a **size filter** `filter.popularity.max`: rooms <500 cap 0.95; 500-1,499 **0.975**; 1,500-2,999 0.99; 3,000+ none. Qloo popularity is a percentile (0-1).
4. **Move 1:** check the top 15. **Move 2:** if fewer than 3 verified locals, check the next 10. **Move 3:** if at least 1 verified local, ask Qloo what fans of up to 2 verified local acts like in the area, rank by Qloo taste-tag overlap with the headliner, check the top 10. **Move 4 (new in v3, city level only):** ask the LLM for up to 8 acts based in the city now; each name must match exactly one Qloo artist and pass the size filter, then gets the same evidence checks. Then widen the area. Stop at 3 verified locals ('ready') or when all areas are searched ('ready, low evidence').
5. Every candidate is re-checked with `/entities`; if the ID belongs to another name it is excluded.

**Evidence (per artist, per area):**

- Sources: Qloo place of birth; MusicBrainz begin area (formed here, verified) and area (based here, *unverified* because it is undated); Tavily web search (2 queries per artist).
- Web hits are rejected if: ticket/listing/streaming page; the name only appears inside a longer name; the page does not name both the artist and a place in the area.
- The LLM labels each remaining page and quotes a sentence. **The code decides**: a sentence must name the artist as its subject and match a pattern. *Based here*: '<place>-based', 'based in', 'lives in', 'moved to', '<place> band/singer'. *From / formed here*: 'is/are/band ... from <place>', 'formed in', 'grew up in', 'native of', 'born in'. Not counted: negated, past or hearsay ('not', 'formerly', 'spent time', 'claimed'); relatives ('my mom lives in'); labels, blogs, venues, radio; another named band; another person as subject.
- **Pronouns (new in v3):** 'He/She/They/The band ...' is read as the artist only on a page about the artist and only while the page is still talking about the artist (the last sentence that opened with a full name decides). 'Based in X, her music ...' is read as the artist on the artist's own page.
- **Current-base check:** for any artist with a local lead, one neutral search (`"<artist>" musician OR band based in`). Sentences placing the act **elsewhere** (based_elsewhere / formed_elsewhere) or saying it **stopped** (retired, split up, disbanded, final show) are recorded. **New in v3:** the same scan also runs over the pages already fetched by the evidence search (no extra cost).
- 'Played here' (performed in the city) is recorded separately and is **not** local proof. Inactive acts are excluded.

**Local status (one label per artist):**

| Status | Score | Rule |
|---|---|---|
| based_now | 1.0 | verified 'based here' from a source <= 3 years old, or Wikipedia |
| based_as_of | 0.75 | verified 'based here', older or undated source |
| roots | 0.6 | verified 'from / formed here', current base not confirmed |
| disputed | 0.35 | a verified source places the act elsewhere and the local source is not clearly newer |
| moved | 0.2 | a source at least as new as the newest local source places the act elsewhere |
| played / none | <= 0.3 | performances only, unverified claims, or nothing |

**Verified local** = score >= 0.5 (based_now, based_as_of, roots). **Gate:** 3 verified locals -> recommendation; fewer -> research shortlist, no automatic picks.

**Scores (0-1):** *Taste* = 0.6 x Qloo affinity part (0.5..1, within the same query) + 0.4 x tag overlap; artists outside the headliner's Qloo list (scene, LLM-named) use tag overlap only. *Size* = Spotify monthly listeners vs 750k ceiling for 500-1,499 rooms: <= ceiling 0.9, <= 3x 0.6, above 0.3, unknown 0.5. *Total* = 0.5 taste + 0.3 local + 0.2 size; shortlist order: verified locals first, then total. Brief warnings are added when a pick's source is old or undated ('confirm current base'), when it shares under 10% of the headliner's Qloo tags, or when it was chosen by the buyer and is not verified local.

## 3. What changed since version 2, and why

| Change | Why |
|---|---|
| Local status replaces a single 'local' score: based_now / based_as_of / roots / disputed / moved, with the source year. | v2 counted a 2017 source as proof the act is local now (Molly Burch). |
| Current-base check: one neutral search per artist with a local lead; sources placing the act elsewhere or saying it stopped are recorded. | v2 never looked for contrary evidence. |
| MusicBrainz 'area' is now an unverified lead, not proof. | It is undated and often stale (Mamalarky). |
| Pronoun resolution tracks who the page is talking about. | A band member's bio ('He moved to Nashville in 2019') made Cory Wong look local. |
| 'Based in X, her music ...' (Wikipedia style) is read; the elsewhere-scan also covers pages already fetched. | Molly Burch's Wikipedia line was missed. |
| Move 4: LLM-named local acts as leads, checked like Qloo's. | After the stricter rules, Qloo's lists alone gave 0-1 verified locals per city. |
| Research-shortlist contract: below 3 verified locals, no automatic picks. | v2 filled picks with non-local acts (Suki Waterhouse in Austin). |
| Inactive acts excluded ('split up', 'retired'). | Girlpool (split up) was a candidate. |
| City aliases (Philly, ATL, NYC, NOLA, ...). | 'based in Philly' was read as elsewhere. |

## 4. Test inputs

| Headliner | City | Capacity | Size filter |
|---|---|---|---|
| Khruangbin | Nashville, Tennessee | 800 | on (Qloo popularity <= 0.975) |
| Japanese Breakfast | Atlanta, Georgia | 800 | on (Qloo popularity <= 0.975) |
| St. Vincent | Austin, Texas | 800 | on (Qloo popularity <= 0.975) |

Live run on 29 Oct 2026, 13:25 UTC against the real Qloo hackathon API, Tavily, MusicBrainz and OpenAI. Web searches and AI page verdicts were served from a 14-day cache of identical earlier calls (0 paid searches in this run; the previous run 8 minutes earlier made the paid calls). Command: `py -m scripts.live_smoke_test --record`.

## 5. Outputs

### Khruangbin in Nashville, Tennessee

**Status:** ready. 3 candidates verified as local to the city area  
**Output type:** recommendation. **Picks:** primary All Them Witches, backup Margo Price, alternative The Lone Bellow. **Candidates checked:** 31.

**Agent decisions:**

- 0 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Nashville, Tennessee before widening
- Asked the language model for acts based in Nashville, Tennessee now. It named 8; 6 resolve to one Qloo artist within the size filter and get the same source checks. Skipped: The Futurebirds (not in Qloo), The Black Angels (too big for the room)
- 3 of 31 live candidates are verified as local to the city area (need 3)
- Web searches: 0 paid, 73 from the cache (budget 100 per run)

**Where the verified locals came from (stage breakdown):**

| Discovery move | Artists checked | Verified local |
|---|---|---|
| named by the LLM as local, then checked | 6 | All Them Witches, Margo Price, The Lone Bellow |
| top of Qloo list | 15 | - |
| deeper in Qloo list | 10 | - |

**LLM-named step:** asked for acts based in the city now; it named: The Futurebirds, All Them Witches, The Black Angels, The Wild Reeds, Lilly Hiatt, The Lone Bellow, Margo Price, The War and Treaty. Kept for checking: All Them Witches, The Wild Reeds, Lilly Hiatt, The Lone Bellow, Margo Price, The War and Treaty. Skipped: The Futurebirds (not in Qloo), The Black Angels (too big for the room).

**Verified local acts, with the deciding evidence:**

| Artist | How found | Status | Pop. | Taste | Shared Qloo tags | Evidence (quote, source) |
|---|---|---|---|---|---|---|
| All Them Witches | named by the LLM as local, then checked | based_as_of (Based here (source date unknown)) | 0.974 | 0.24 | Psychedelic, Psychedelic Rock, Open-Minded, Adventurous | "Image 16: The Sun, Moon & Herbs A versatile hard rock quartet based out of Nashville, Tennessee, All Them Witches draw from a deep well of musical inspirations " https://www.allmusic.com/artist/all-them-witches-mn0003061563 |
| Margo Price | named by the LLM as local, then checked | based_now (Based here (source 2026)) | 0.894 | 0.0 | - | "Margo Price (born April 15, 1983) is an American country singer-songwriter, producer, and author based in Nashville." https://en.wikipedia.org/wiki/Margo_Price |
| The Lone Bellow | named by the LLM as local, then checked | based_as_of (Based here (source date unknown)) | 0.956 | 0.04 | Rock | "The Lone Bellow was born in Brooklyn, but is now based in Nashville." https://www.thebluegrassstandard.com/post/the-lone-bellow-music-made-from-folk-togetherness |

**Flagged as moved or disputed (not counted as local):**

| Artist | Status | Why |
|---|---|---|
| Ron Gallo | disputed | Sources disagree: one says based in Philadelphia (undated) |

**Shortlist as ranked (first 12):**

| # | Artist | Found via | Pop. | Taste | Local | Size | Status | Played city | Shared tags |
|---|---|---|---|---|---|---|---|---|---|
| 1 | All Them Witches | llm_local | 0.974 | 0.24 | 0.75 | 0.5 | based_as_of | - | Psychedelic, Psychedelic Rock, Open-Minded, Adventurous |
| 2 | Margo Price | llm_local | 0.894 | 0.0 | 1.0 | 0.5 | based_now | yes | - |
| 3 | The Lone Bellow | llm_local | 0.956 | 0.04 | 0.75 | 0.5 | based_as_of | - | Rock |
| 4 | Hermanos Gutiérrez | qloo | 0.965 | 0.692 | 0.3 | 0.9 | played | yes | Psychedelic, Instrumental, Psychedelic Rock, Eclectic |
| 5 | The Nude Party | qloo | 0.858 | 0.626 | 0.3 | 0.9 | played | yes | Rock, Psychedelic Rock, Psychedelic |
| 6 | Mdou Moctar | qloo | 0.938 | 0.614 | 0.3 | 0.9 | played | - | Psychedelic Rock, Psychedelic |
| 7 | Sam Evian | qloo | 0.902 | 0.587 | 0.25 | 0.9 | played | yes | Rock, Laid-Back |
| 8 | Neal Francis | qloo_deeper | 0.828 | 0.587 | 0.25 | 0.9 | played | yes | Funk, Rock, Soul, Funky |
| 9 | Ron Gallo | qloo | 0.921 | 0.497 | 0.35 | 0.9 | disputed | yes | - |
| 10 | Kikagaku Moyo | qloo | 0.948 | 0.696 | 0.0 | 0.9 | none | - | Psychedelic, Psychedelic Rock, Open-Minded, Adventurous |
| 11 | Louis Prince | qloo_deeper | 0.293 | 0.506 | 0.3 | 0.9 | played | - | Soul |
| 12 | Brittany Howard | qloo | 0.935 | 0.616 | 0.3 | 0.6 | played | yes | Soul |

**Booking-brief warnings:**

- All Them Witches (primary): Based here (source date unknown). Confirm where the act is based now before pitching.
- Margo Price (backup) shares few Qloo taste tags with Khruangbin (0%): local, but check the musical fit.
- The Lone Bellow (alternative): Based here (source date unknown). Confirm where the act is based now before pitching.
- The Lone Bellow (alternative) shares few Qloo taste tags with Khruangbin (4%): local, but check the musical fit.

**Generated summary (llm):** The booking brief features Khruangbin as the headliner in Nashville, Tennessee, at a venue with a capacity of 800. The primary act is All Them Witches, a local band known for their unique blend of hard rock and psychedelia, formed in Nashville. Margo Price serves as a backup, also based in the area and recognized for her country music. The alternative act is The Lone Bellow, now based in Nashville but originally from Brooklyn. All acts share a connection to the local music scene, though listener counts and other specifics remain unknown.

### Japanese Breakfast in Atlanta, Georgia

**Status:** ready low evidence. Searched up to region scope; only 1 of 81 candidates are verified as local. Treat the list as research, not a recommendation.  
**Output type:** research shortlist. **Picks:** primary -, backup -, alternative -. **Candidates checked:** 81.

**Agent decisions:**

- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Atlanta, Georgia before widening
- Expanding from the verified local act Lunar Vacation: Qloo found 34 new artists their fans like in Atlanta, Georgia; checking the 10 whose Qloo taste tags overlap most with Japanese Breakfast
- Asked the language model for acts based in Atlanta, Georgia now. It named 8; 1 resolve to one Qloo artist within the size filter and get the same source checks. Skipped: Faye Webster (too big for the room), Kishi Bashi (too big for the room), Mitski (too big for the room), Pinegrove (too big for the room), Soccer Mommy (too big for the room), Snail Mail (too big for the room)
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for city, so the search widens to metro
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Atlanta metropolitan area, Georgia before widening
- Expanding from the verified local act Lunar Vacation: Qloo found 32 new artists their fans like in Atlanta metropolitan area, Georgia; checking the 10 whose Qloo taste tags overlap most with Japanese Breakfast
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for metro, so the search widens to region
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Georgia before widening
- Expanding from the verified local act Lunar Vacation: Qloo found 27 new artists their fans like in Georgia; checking the 10 whose Qloo taste tags overlap most with Japanese Breakfast
- Searched up to region scope; only 1 of 81 candidates are verified as local. Treat the list as research, not a recommendation.
- Web searches: 0 paid, 171 from the cache (budget 100 per run)

**Where the verified locals came from (stage breakdown):**

| Discovery move | Artists checked | Verified local |
|---|---|---|
| top of Qloo list | 29 | Lunar Vacation |
| via a verified local act | 30 | - |
| deeper in Qloo list | 21 | - |
| named by the LLM as local, then checked | 1 | - |

**LLM-named step:** asked for acts based in the city now; it named: Faye Webster, Kishi Bashi, Mitski, The Backseat Lovers, Pinegrove, Soccer Mommy, Snail Mail, Japanese Breakfast. Kept for checking: The Backseat Lovers. Skipped: Faye Webster (too big for the room), Kishi Bashi (too big for the room), Mitski (too big for the room), Pinegrove (too big for the room), Soccer Mommy (too big for the room), Snail Mail (too big for the room).

**Verified local acts, with the deciding evidence:**

| Artist | How found | Status | Pop. | Taste | Shared Qloo tags | Evidence (quote, source) |
|---|---|---|---|---|---|---|
| Lunar Vacation | top of Qloo list | based_as_of (Based here as of 2019) | 0.892 | 0.818 | Indie, Indie Pop, Dream Pop, Indie Rock | "Reaching for the stars: Atlanta-based band Lunar Vacation dreams big" https://www.redandblack.com/culture/reaching-for-the-stars-atlanta-based-band-lunar-vacation-dreams-big/article_5a5f06fa-f8ac-11e9-8b79-db46fb4a5959.html |

**Flagged as moved or disputed (not counted as local):**

| Artist | Status | Why |
|---|---|---|
| girlpuppy | disputed | Sources disagree: one says based in Brooklyn, NY (undated) |
| Mamalarky | moved | A 2026 source says based in Los Angeles, California: "Mamalarky is based in Los Angeles, California after a period residing in Atlanta, Georgia." |

**Shortlist as ranked (first 12):**

| # | Artist | Found via | Pop. | Taste | Local | Size | Status | Played city | Shared tags |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Lunar Vacation | qloo | 0.892 | 0.818 | 0.75 | 0.9 | based_as_of | - | Indie, Indie Pop, Dream Pop, Indie Rock |
| 2 | Okey Dokey | scene | 0.765 | 0.75 | 0.3 | 0.9 | played | - | Indie Pop, Indie, Indie Rock |
| 3 | girlpuppy | qloo | 0.759 | 0.707 | 0.35 | 0.9 | disputed | yes | Indie, Indie Pop, Indie Rock, Emotive |
| 4 | Rostam | qloo | 0.941 | 0.885 | 0.0 | 0.9 | none | - | - |
| 5 | Suki Waterhouse | qloo | 0.963 | 0.7 | 0.25 | 0.9 | played | yes | Indie Pop, Dreamy, Melodic, Intimate |
| 6 | Caroline Rose | qloo | 0.957 | 0.7 | 0.25 | 0.9 | played | yes | Indie Pop, Indie, Indie Rock, Engaging |
| 7 | sasami | qloo | 0.933 | 0.619 | 0.3 | 0.9 | played | yes | Indie Rock, Experimental, Intimate, Expressive |
| 8 | Lomelda | qloo | 0.945 | 0.646 | 0.25 | 0.9 | played | yes | Indie, Indie Rock, Emotive, Intimate |
| 9 | Better Oblivion Community Center | qloo | 0.961 | 0.725 | 0.1 | 0.9 | none | - | Indie, Indie Rock, Melodic, Intimate |
| 10 | Loud Hound | scene | 0.361 | 0.75 | 0.0 | 0.9 | none | - | Indie Pop, Indie Rock, Indie |
| 11 | Squirrel Flower | qloo_deeper | 0.906 | 0.598 | 0.25 | 0.9 | played | yes | Dream Pop, Indie Rock, Emotive, Melodic |
| 12 | Mamalarky | scene | 0.808 | 0.625 | 0.2 | 0.9 | moved | - | Indie Rock, Indie, Indie Pop, Dream Pop |

**Booking-brief warnings:**

- Research shortlist, not a booking recommendation: only 1 of the 3 verified local acts needed were found. No picks were filled in automatically.

**Generated summary (template):** Not enough verified local evidence for a recommendation in Atlanta, Georgia: 1 of 3 needed. Leads to research: Lunar Vacation (based_as_of), girlpuppy (disputed), Okey Dokey (played), sasami (played), Molly Burch (played). Availability, fee and headliner-agent approval are unknown.

### St. Vincent in Austin, Texas

**Status:** ready low evidence. Searched up to region scope; only 2 of 75 candidates are verified as local. Treat the list as research, not a recommendation.  
**Output type:** research shortlist. **Picks:** primary -, backup -, alternative -. **Candidates checked:** 75.

**Agent decisions:**

- 0 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Austin, Texas before widening
- Asked the language model for acts based in Austin, Texas now. It named 8; 5 resolve to one Qloo artist within the size filter and get the same source checks. Skipped: Shakey Graves (too big for the room), Leon Bridges (too big for the room)
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for city, so the search widens to metro
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Austin metropolitan area, Texas before widening
- Expanding from the verified local act Black Pumas: Qloo found 46 new artists their fans like in Austin metropolitan area, Texas; checking the 10 whose Qloo taste tags overlap most with St. Vincent
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for metro, so the search widens to region
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Texas before widening
- Expanding from the verified local act Black Pumas: Qloo found 48 new artists their fans like in Texas; checking the 10 whose Qloo taste tags overlap most with St. Vincent
- Searched up to region scope; only 2 of 75 candidates are verified as local. Treat the list as research, not a recommendation.
- Web searches: 0 paid, 161 from the cache (budget 100 per run)

**Where the verified locals came from (stage breakdown):**

| Discovery move | Artists checked | Verified local |
|---|---|---|
| via a verified local act | 20 | Golden Dawn Arkestra |
| named by the LLM as local, then checked | 5 | Black Pumas |
| top of Qloo list | 28 | - |
| deeper in Qloo list | 22 | - |

**LLM-named step:** asked for acts based in the city now; it named: Shakey Graves, Margaret Glaspy, Black Pumas, Sofia Valdes, Caroline Rose, Leon Bridges, Kacy Hill, The Texas Gentlemen. Kept for checking: Margaret Glaspy, Black Pumas, Sofía Valdés, Kacy Hill, The Texas Gentlemen. Skipped: Shakey Graves (too big for the room), Leon Bridges (too big for the room).

**Verified local acts, with the deciding evidence:**

| Artist | How found | Status | Pop. | Taste | Shared Qloo tags | Evidence (quote, source) |
|---|---|---|---|---|---|---|
| Golden Dawn Arkestra | via a verified local act | roots (From or formed here; current base not confirmed) | 0.668 | 0.231 | Eclectic, Experimental, Dynamic, Engaging | "Begin area: Austin" https://musicbrainz.org/artist/4f966c87-2751-4f5b-a2a9-fdb26185126e |
| Black Pumas | named by the LLM as local, then checked | based_now (Based here (source 2026)) | 0.973 | 0.08 | Expressive, Dynamic | "Black Pumas is an American psychedelic soul band based in Austin, Texas, led by singer/songwriter Eric Burton and guitarist/producer Adrian Quesada." https://en.wikipedia.org/wiki/Black_Pumas |

**Flagged as moved or disputed (not counted as local):**

| Artist | Status | Why |
|---|---|---|
| Molly Burch | moved | A 2026 source says based in Los Angeles, California: "Molly Burch is based in Los Angeles, California, her music often reflects themes of heartbreak and loss." |

**Shortlist as ranked (first 12):**

| # | Artist | Found via | Pop. | Taste | Local | Size | Status | Played city | Shared tags |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Golden Dawn Arkestra | scene | 0.668 | 0.231 | 0.6 | 0.9 | roots | - | Eclectic, Experimental, Dynamic, Engaging |
| 2 | Black Pumas | llm_local | 0.973 | 0.08 | 1.0 | 0.5 | based_now | yes | Expressive, Dynamic |
| 3 | Suki Waterhouse | qloo | 0.963 | 0.65 | 0.3 | 0.9 | played | yes | Indie Pop, Expressive, Electropop |
| 4 | Caroline Rose | qloo | 0.957 | 0.644 | 0.3 | 0.9 | played | yes | Singer & Songwriter, Indie Pop, Indie, Indie Rock |
| 5 | Ramona Lisa | qloo | 0.89 | 0.667 | 0.25 | 0.9 | played | yes | Indie, Experimental, Open-Minded, Artistic |
| 6 | Kim Gordon | qloo | 0.941 | 0.665 | 0.25 | 0.9 | played | yes | Alternative Rock, Indie Rock, Experimental, Innovative |
| 7 | Alaska Thunderfuck | qloo_deeper | 0.967 | 0.746 | 0.1 | 0.9 | none | - | - |
| 8 | Mary Timony | qloo_deeper | 0.918 | 0.647 | 0.25 | 0.9 | played | yes | Singer & Songwriter, Indie, Indie Rock, Alternative Rock |
| 9 | Rostam | qloo | 0.941 | 0.724 | 0.1 | 0.9 | none | - | - |
| 10 | Eleanor Friedberger | qloo | 0.95 | 0.722 | 0.1 | 0.9 | none | - | Indie, Indie Rock, Alternative Rock, Engaging |
| 11 | Blood Diamonds | qloo_deeper | 0.935 | 0.75 | 0.0 | 0.9 | none | - | - |
| 12 | Cherry Glazerr | qloo | 0.972 | 0.599 | 0.25 | 0.9 | played | yes | Indie Pop, Indie, Indie Rock, Alternative Rock |

**Booking-brief warnings:**

- Research shortlist, not a booking recommendation: only 2 of the 3 verified local acts needed were found. No picks were filled in automatically.

**Generated summary (template):** Not enough verified local evidence for a recommendation in Austin, Texas: 2 of 3 needed. Leads to research: Black Pumas (based_now), Golden Dawn Arkestra (roots), Suki Waterhouse (played), Caroline Rose (played), Trixie Mattel (played). Availability, fee and headliner-agent approval are unknown.

## 6. How we evaluated, and results

### 6a. Evidence accuracy on hand-labelled cases

Question per case: *is this artist based in, or from, this city?* Labels were written by the developer from public bios (Oct 2026); the held-out labels have not been checked by a second person. **Precision** = of artists called local, the share that really are. **Recall** = of real locals, the share found. A case counts as local when its local status is based_now, based_as_of or roots. Same cases, three configurations. Command: `py -m scripts.evidence_eval [--heldout]`.

**Development set (17 cases from the live runs; the rules were tuned on these)**

| Artist | City | Label | Web | MusicBrainz | Both | v3 status | Label reason |
|---|---|---|---|---|---|---|---|
| Lunar Vacation | Atlanta | yes | yes | yes | yes | based_as_of | Atlanta band |
| girlpuppy | Atlanta | yes | no (wrong) | yes | no (wrong) | disputed | Becca Harvey, Atlanta |
| Faye Webster | Atlanta | yes | yes | no (wrong) | yes | roots | Atlanta singer-songwriter |
| Dehd | Atlanta | no | no | no | no | none | Chicago band; a member spoke of being from Atlanta |
| Soccer Mommy | Atlanta | no | no | no | no | none | Nashville act that played Atlanta |
| Mitski | Atlanta | no | no | no | no | played | touring act |
| Soccer Mommy | Nashville | yes | yes | no (wrong) | yes | roots | Nashville |
| All Them Witches | Nashville | yes | yes | yes | yes | based_as_of | Nashville band |
| Ron Gallo | Nashville | yes | no (wrong) | no (wrong) | no (wrong) | disputed | Philadelphia-born, lives in Nashville |
| The Nude Party | Nashville | no | no | no | no | played | North Carolina / New York; label is Nashville-based |
| Whitney | Nashville | no | no | no | no | none | Chicago band; name collides with Whitney Houston |
| Brittany Howard | Nashville | yes | no (wrong) | no (wrong) | no (wrong) | played | Athens AL native living in Nashville (check) |
| Shakey Graves | Austin | yes | yes | yes | yes | roots | Austin |
| Black Pumas | Austin | yes | yes | yes | yes | based_now | Austin band |
| Suki Waterhouse | Austin | no | no | no | no | played | London/LA; played ACL Fest |
| Girlpool | Austin | no | no | no | no | played | Los Angeles; an Austin blog wrote about them |
| Tennis | Austin | no | no | no | no | played | Denver band; name collides with the sport |

| Configuration | v3 precision | v3 recall | v2 recall | Missed in v3 |
|---|---|---|---|---|
| Web only | 100% (6/6) | 67% | 67% | girlpuppy/Atlanta, Ron Gallo/Nashville, Brittany Howard/Nashville |
| MusicBrainz only | 100% (5/5) | 56% | 67% | Faye Webster/Atlanta, Soccer Mommy/Nashville, Ron Gallo/Nashville, Brittany Howard/Nashville |
| Both (used by the app) | 100% (6/6) | 67% | 78% | girlpuppy/Atlanta, Ron Gallo/Nashville, Brittany Howard/Nashville |

**Held-out set (20 cases, Philadelphia and Chicago, never used for tuning)**

| Artist | City | Label | Web | MusicBrainz | Both | v3 status | Label reason |
|---|---|---|---|---|---|---|---|
| Alex G | Philadelphia | yes | no (wrong) | no (wrong) | no (wrong) | played | Philadelphia-area songwriter |
| Hop Along | Philadelphia | yes | yes | yes | yes | based_as_of | Philadelphia band |
| The War on Drugs | Philadelphia | yes | yes | yes | yes | based_as_of | formed in Philadelphia |
| Kurt Vile | Philadelphia | yes | no (wrong) | no (wrong) | no (wrong) | played | Philadelphia-area musician |
| Mannequin Pussy | Philadelphia | yes | yes | no (wrong) | yes | roots | Philadelphia band |
| Sheer Mag | Philadelphia | yes | yes | yes | yes | based_as_of | Philadelphia band |
| Snail Mail | Philadelphia | no | no | no | no | none | Baltimore act |
| Big Thief | Philadelphia | no | no | no | no | none | Brooklyn band |
| Waxahatchee | Philadelphia | no | no | no | no | played | Alabama / Kansas City |
| Soccer Mommy | Philadelphia | no | no | no | no | none | Nashville act |
| Whitney | Chicago | yes | yes | no (wrong) | yes | based_as_of | Chicago band |
| Dehd | Chicago | yes | yes | yes | yes | based_now | Chicago band |
| Ratboys | Chicago | yes | yes | no (wrong) | yes | based_as_of | Chicago band |
| Twin Peaks | Chicago | yes | yes | yes | yes | roots | Chicago band (name collides with the TV show) |
| Wilco | Chicago | yes | yes | yes | yes | based_now | Chicago band |
| Tennis | Chicago | no | no | no | no | played | Denver band |
| Faye Webster | Chicago | no | no | no | no | played | Atlanta |
| Alex G | Chicago | no | no | no | no | played | Philadelphia |
| Hop Along | Chicago | no | no | no | no | none | Philadelphia |
| Lunar Vacation | Chicago | no | no | no | no | none | Atlanta |

| Configuration | v3 precision | v3 recall | v2 recall | Missed in v3 |
|---|---|---|---|---|
| Web only | 100% (9/9) | 82% | 82% | Alex G/Philadelphia, Kurt Vile/Philadelphia |
| MusicBrainz only | 100% (6/6) | 55% | 55% | Alex G/Philadelphia, Kurt Vile/Philadelphia, Mannequin Pussy/Philadelphia, Whitney/Chicago, Ratboys/Chicago |
| Both (used by the app) | 100% (9/9) | 82% | 82% | Alex G/Philadelphia, Kurt Vile/Philadelphia |

**Reading these numbers.** No false 'local' claims in 37 cases (15 of 15 local calls correct). With so few positives the 95% lower confidence bound on precision is about 54% (development, 6/6), 66% (held-out, 9/9) and 78% (both, 15/15). Development recall fell from 78% to 67% because girlpuppy and Ron Gallo are now *disputed*: each has a verified source placing them elsewhere (girlpuppy's own Facebook page: 'based in Brooklyn, NY'; a magazine: Ron Gallo 'is a Philadelphia-based musician, who has also spent time living in Nashville'). Either the labels or the rule may be wrong; we have not decided which, and we ask the reviewer (section 8).

### 6b. Self-audit: the 8 'verified local' acts claimed in report v2

| Artist | City | v2 evidence | v3 status | Why |
|---|---|---|---|---|
| Ron Gallo | Nashville | "Meet Ron Gallo, the Nashville-based musician voicing a revv’d up headfirst agenda – HERO" | disputed | A source says he is Philadelphia-based and only 'spent time living in Nashville' (wethecoolmagazine); MusicBrainz area: Philadelphia |
| Becca Mancari | Nashville | "Hi my name is Becca Mancari, and I am an Indie musician living in Nashville, TN." | not re-found | Found in v2 via the scene move from Ron Gallo; Ron Gallo is no longer a verified seed, so the move did not run from him in v3. v2 evidence (self-description 'living in Nashville') was not contradicted. |
| TWEN | Nashville | "Begin area: Nashville" | not re-found | Found in v2 via the scene move from Ron Gallo (MusicBrainz begin area: Nashville). Not re-checked in v3 for the same reason. |
| Lunar Vacation | Atlanta | "Reaching for the stars: Atlanta-based band Lunar Vacation dreams big" | based_as_of | Atlanta-based, newest source 2019; still counted local but flagged 'confirm current base' |
| girlpuppy | Atlanta | "In the Atlanta-based musician's case, it's her jangle-pop alias Girlpuppy, which found itself thriving after a" | disputed | 2021 Fader: 'Atlanta-based'; her own Facebook page: 'based in Brooklyn, NY' (undated) |
| Mamalarky | Atlanta | "Area: Atlanta" | moved | 2026 source: 'Mamalarky is based in Los Angeles, California after a period residing in Atlanta' |
| Molly Burch | Austin | "Austin-based (via Los Angeles) singer songwriter Molly Burch has been out on the road this past month, in supp" | moved | Wikipedia: 'Based in Los Angeles, California'; the Austin source is from 2017 |
| Why Bonnie | Austin | "Begin area: Austin" | moved | 2022 source: 'Why Bonnie is based out of New York and originally from Austin' |

**Result:** 1 of 8 still hold as verified local under the v3 rules; 3 moved, 2 disputed, 2 not re-checked. Report v2 said '8 found, 8 judged correct'. That claim was wrong: the v2 rules accepted old and undated sources as proof of a current base.

### 6c. Four-way comparison

For each city, four lists of 8 artists for the same show: **LLM on its own** (gpt-4o-mini, no tools, asked for local or regional support acts; asked fresh each run, so it varies), **Qloo, no city** (top 8 related artists, no location), **Qloo with the city** (top 8 with location signal, no size filter), **Opening Act** (top 8 of the agent's shortlist). Every artist in every list is checked by the same evidence rules, at the area the agent reached, with the same size rule. 'LLM on its own, local and within size limit' is the same as 'LLM list + our filters'.

| City | Measure (out of 8) | LLM on its own | Qloo, no city | Qloo with the city | Opening Act |
|---|---|---|---|---|---|
| Nashville | Local and within size limit | 4 | 0 | 0 | 3 |
|  | Verified local | 4 | 0 | 0 | 3 |
|  | Above size limit | 0 | 1 | 4 | 0 |
|  | Played city only | 0 | 0 | 2 | 4 |
|  | Not in Qloo | 0 | 0 | 0 | 0 |
| Atlanta | Local and within size limit | 2 | 0 | 0 | 1 |
|  | Verified local | 4 | 0 | 1 | 1 |
|  | Above size limit | 3 | 5 | 6 | 0 |
|  | Played city only | 1 | 4 | 5 | 5 |
|  | Not in Qloo | 2 | 0 | 0 | 0 |
| Austin | Local and within size limit | 1 | 0 | 0 | 2 |
|  | Verified local | 2 | 0 | 0 | 2 |
|  | Above size limit | 4 | 7 | 8 | 0 |
|  | Played city only | 5 | 3 | 5 | 5 |
|  | Not in Qloo | 0 | 0 | 0 | 0 |

**The lists, with each artist's status:**

*Khruangbin in Nashville* (checked at: Nashville, Tennessee)

| LLM on its own | Qloo, no city | Qloo with the city | Opening Act |
|---|---|---|---|
| The Wild Feathers (roots) | Arc De Soleil (none) | Vulfpeck (none, too big) | All Them Witches (based_as_of) |
| Lilly Hiatt (played) | Glass Beams (none) | Black Pumas (played, too big) | Margo Price (based_now) |
| The Lone Bellow (based_as_of) | Mildlife (none) | Hiatus Kaiyote (none, too big) | The Lone Bellow (based_as_of) |
| Maggie Rose (played) | Common Saints (none) | SAULT (none, too big) | Hermanos Gutiérrez (played) |
| The Black Lillies (played) | Robohands (none) | Kikagaku Moyo (none) | The Nude Party (played) |
| Nikki Lane (based_as_of) | Thee Sacred Souls (none) | Brittany Howard (played) | Mdou Moctar (played) |
| The Band CAMINO (played) | SAULT (none, too big) | Mk.gee (none) | Sam Evian (played) |
| Kelsey Waldon (based_as_of) | KOKOROKO (none) | Glass Beams (none) | Neal Francis (played) |

*Japanese Breakfast in Atlanta* (checked at: Georgia)

| LLM on its own | Qloo, no city | Qloo with the city | Opening Act |
|---|---|---|---|
| Faye Webster (roots, too big) | Soccer Mommy (none, too big) | Faye Webster (roots, too big) | Lunar Vacation (based_as_of) |
| Kishi Bashi (based_as_of, too big) | Mitski (played, too big) | Soccer Mommy (none, too big) | Okey Dokey (none) |
| Mitski (played, too big) | Snail Mail (none, too big) | Mitski (played, too big) | girlpuppy (disputed) |
| The Coathangers (based_as_of) | Alvvays (played, too big) | Lucy Dacus (played, too big) | Rostam (none) |
| Pillbox Patti (none, not in Qloo) | sasami (played) | Big Thief (played, too big) | Suki Waterhouse (played) |
| The Backseat Lovers (none) | beabadoobee (played, too big) | Phoebe Bridgers (played, too big) | Caroline Rose (played) |
| Algiers (roots) | Samia (none) | Jay Som (none) | sasami (played) |
| Sasha Alex Sloane (Tierra Whack) (none, not in Qloo) | Jay Som (none) | sasami (played) | Lomelda (played) |

*St. Vincent in Austin* (checked at: Texas)

| LLM on its own | Qloo, no city | Qloo with the city | Opening Act |
|---|---|---|---|
| Shakey Graves (roots, too big) | Perfume Genius (none, too big) | Perfume Genius (none, too big) | Golden Dawn Arkestra (roots) |
| Margaret Glaspy (none) | Dirty Projectors (played, too big) | Dirty Projectors (played, too big) | Black Pumas (based_now) |
| Sufjan Stevens (played, too big) | Ema (none) | Beach House (none, too big) | Suki Waterhouse (played) |
| Leon Bridges (played, too big) | Owen Pallett (none, too big) | tUnE-yArDs (played, too big) | Caroline Rose (played) |
| Black Pumas (based_now) | Chairlift (none, too big) | Chairlift (none, too big) | Ramona Lisa (played) |
| Kacey Musgraves (played, too big) | Beach House (none, too big) | Sufjan Stevens (played, too big) | Kim Gordon (played) |
| Caroline Rose (played) | tUnE-yArDs (played, too big) | Grimes (played, too big) | Alaska Thunderfuck (none) |
| The Texas Gentlemen (played) | Sufjan Stevens (played, too big) | Animal Collective (played, too big) | Mary Timony (played) |

### 6d. Blind rating by an AI judge (from version 2, not re-run)

The v2 lists were shuffled, labelled A-D and rated blind by ChatGPT for fit and usefulness (1-5). Means: Opening Act fit 4.33, useful 4.33; Qloo, no city fit 5.00, useful 2.00; LLM on its own fit 3.33, useful 2.33; Qloo with the city fit 5.00, useful 1.33. **The Opening Act lists have changed since then**, so this rating does not describe v3.

## 7. Known weaknesses (found by us during this analysis)

1. **Taste fit of LLM-named picks.** In Nashville all three picks came from the LLM-named step. Their Qloo taste fit with Khruangbin is low: All Them Witches 0.24, The Lone Bellow 0.04, Margo Price 0.00 (a country singer). The ranking puts every verified local above every non-local, so a local act with no taste overlap outranks a strong-fit act whose base is only 'played here' (Hermanos Gutierrez 0.69). The brief warns about the fit, but still fills the picks.
2. **Size for LLM-named acts is unknown.** These acts come from Qloo `/search`, which returns no Spotify listener count, so their size score is the neutral 0.5. Only the Qloo popularity filter applies (All Them Witches 0.974 against a 0.975 cap).
3. **The LLM is poor at locality.** In Atlanta it named Mitski, Soccer Mommy, Snail Mail and Pinegrove as acts 'based in Atlanta now' (none are), plus the headliner itself; the LLM-only list included 'Sasha Alex Sloane (Tierra Whack)', which is not an artist. Checking catches these, but it means Move 4 adds little in some cities.
4. **Comparison against the LLM is close and noisy.** Local and within size limit, Opening Act vs LLM alone: Nashville 3 vs 4, Atlanta 1 vs 2, Austin 2 vs 1. The LLM arm is asked fresh each run and changes (Nashville's LLM locals changed between two runs 8 minutes apart). Opening Act's advantage is consistent only on 'above size limit' (0 vs 0-4).
5. **Labels.** Written by the developer; held-out labels not independently checked; two development labels are now in doubt (girlpuppy, Ron Gallo).
6. **Size filter is a popularity proxy**, not ticket or draw data. '0 above the limit' only shows the filter was obeyed.
7. **Suburbs** are not mapped to their city (Alex G: Havertown; Kurt Vile: Lansdowne).
8. **Small samples:** 3 live cities, 37 labelled cases, one AI judge.

## 8. Questions for the reviewer

1. Is the local-status ladder (based_now 1.0 / based_as_of 0.75 / roots 0.6 / disputed 0.35 / moved 0.2) sound? Should 'roots' (formed here, current base unknown) count toward the gate of 3?
2. girlpuppy (2021 'Atlanta-based' vs undated own Facebook 'based in Brooklyn, NY') and Ron Gallo ('Philadelphia-based, has spent time living in Nashville' vs 'the Nashville-based musician'): are 'disputed' right, or are our labels right? What do you know about where each is based now?
3. Check each verified local in section 5 (All Them Witches, Margo Price, The Lone Bellow in Nashville; Lunar Vacation in Atlanta; Black Pumas, Golden Dawn Arkestra in Austin). Are any wrong or stale?
4. Check each 'moved' flag (Mamalarky -> Los Angeles, Molly Burch -> Los Angeles, Why Bonnie -> New York). Any wrong?
5. Should the gate require a minimum taste fit (for example tag overlap >= 0.1 or a Qloo affinity) before a local act can be an automatic pick? Would you rather see Hermanos Gutierrez (strong fit, not local) or Margo Price (local, no fit) as a Khruangbin opener?
6. Is asking the LLM for local names (Move 4) a legitimate agent step given every name is checked, or does it undercut the claim that Qloo drives discovery? How should it be presented to judges?
7. Is the four-way comparison fair? It checks every list at the area the agent reached (state level in Atlanta and Austin), counts 8 artists per list, and the LLM arm is not cached. What would you change?
8. Which single metric best shows whether the product helps a talent buyer: verified local and within size, a human booker's rating, or something else?
9. What do you see in the shortlists (section 5) that a real talent buyer would find wrong or embarrassing?
10. Given 8 of 8 v2 locals were reported correct and only 1 of 8 still holds today (2 could not be re-checked), how should the report present precision so it is not overstated again?

## 9. Files

- `Opening_Act_Test_Report_v3.pdf`: the same results as a readable report, plus the list of all 132 automated tests.
- `opening_act_eval_data_v3.json`: everything above in machine-readable form (`cities[*]` holds inputs, Qloo calls, decisions, stage breakdown, verified locals with quotes and URLs, flagged acts, shortlist, brief and the four lists; `self_audit_of_report_v2_locals`; `evidence_eval_development` and `evidence_eval_heldout` with every case; `rules`).
