# Opening Act: evaluation pack for independent review

## How to use this pack (instructions for the reviewing model)

You are reviewing the outputs and the scoring of **Opening Act**, an entry in the Qloo Agentic Hackathon (Oct 2026). The developers want an independent check, not praise. Please:

1. Read sections 1-6.
2. Check whether the **scoring rules** (section 2) are sound, and whether the **evaluation methods** (section 5) actually measure what they claim.
3. Spot-check the **outputs** (section 4) against your own knowledge: is each "verified local" artist really based in or from that city? Is any list unrealistic for an 800-capacity room?
4. Answer the questions in section 7, each with: verdict (agree / partly / disagree), the reason, and a concrete fix.
5. End with the 3 changes that would most improve the product or the credibility of its results.

The full structured data (every Qloo call, ranked candidates with quotes and URLs, all comparison lists, all ratings) is in the companion file `opening_act_eval_data.json`.

---

## 1. What the product does

A talent buyer at an independent venue enters a **headliner**, a **city** and a **venue capacity**. Opening Act builds a shortlist of **support (opening) acts** that (a) suit the headliner's audience, (b) are realistically sized for the room and (c) are verifiably local to the city. Every claim links to its source; unknowns (availability, fee, headliner approval) are always shown as unknown.

Data sources: **Qloo** Taste AI (hackathon API: `/search`, `/v2/insights`, `/entities`), **Tavily** web search, **MusicBrainz** (free structured artist data), and an LLM (OpenAI gpt-4o-mini) used only as a *hint* when reading web pages and for the summary text.

## 2. Pipeline and every scoring rule (exact values)

**Discovery (agent loop, per search area city -> metro -> state):**
1. Resolve the headliner to one Qloo entity (`/search`; only a unique exact name match is accepted, otherwise the buyer is asked).
2. Read the headliner's Qloo taste tags (`/entities`): genres, Qloo music styles, Qloo audience traits.
3. Ask Qloo for 50 artists the headliner's fans like, with the city as location signal (`/v2/insights`, `signal.interests.entities=<headliner>`, `signal.location.query=<city>`), with a **size filter** `filter.popularity.max` that depends on room size: <500 cap 0.95; 500-1,499 cap **0.975**; 1,500-2,999 cap 0.99; 3,000+ no filter. Qloo popularity is a percentile (0-1).
4. Move 1: investigate the top 15. Move 2 (if fewer than 3 verified locals): investigate the next 10 in the same list. Move 3 (if at least 1 verified local): ask Qloo what fans of up to 2 **verified local acts** like in the same area (`signal.interests.entities=<local act ids>`), rank those artists by Qloo **taste-tag overlap with the headliner**, investigate the top 10. Then widen the area. Stop when 3 verified local acts exist ("ready") or all areas are searched ("ready, low evidence").
5. Every candidate is re-checked with `/entities`; if the ID belongs to a different name it is excluded.

**Evidence verification (per artist, per area):**
- Sources: Qloo place of birth; MusicBrainz begin area / area (exact, unambiguous name match only); Tavily web search (2 queries per artist).
- Web hits are rejected if: ticket/listing/streaming page; the name only appears inside a longer name ("Whitney Houston"); the page doesn't name both artist and a place in the area.
- The LLM labels each remaining page and must quote a sentence. **The code then decides**: the quoted sentence (or another sentence on the page) must name the artist and match a pattern. *Based here now*: "<place>-based", "based in", "lives in", "moved to", "<place> band/singer/...". *From / formed here*: "from <place>", "formed in", "grew up in", "native of", "born in". Rejected if negated/past/hearsay ("not", "formerly", "spent time", "claimed"), about a relative ("my mom lives in"), about a label/blog/venue/radio, about another named band, or if another person is the subject ("X ... playing with <artist> ... in Nashville").
- Statuses: verified / unverified / rejected. "Played here" (performed in the city) is recorded separately and **is not local proof**.

**Scores (0-1):**
- *Local* = strongest evidence row: based_here verified 1.0; formed_here verified 0.8; based_here unverified 0.3; played_here verified 0.25; formed_here unverified 0.2; played_here unverified 0.1; rejected/other 0. **Verified local = local score >= 0.5.**
- *Taste* = 0.6 x affinity part + 0.4 x tag overlap. Affinity part = 0.5 + 0.5 x (affinity - min)/(max - min) within the same Qloo query, +/-0.05 bonus if the city signal lifted/surfaced it (range 0.5-1). Tag overlap = shared taste tags / size of the smaller tag profile. Artists found through a local act have no headliner affinity, so taste = tag overlap only.
- *Size* = Spotify monthly listeners vs an ideal ceiling by room (500-1,499 cap: 750k): <= ceiling 0.9; <= 3x ceiling 0.6; above 0.3; unknown 0.5.
- *Total* = 0.5 taste + 0.3 local + 0.2 size (buyer can re-weight). Shortlist order: verified locals first, then total.
- Booking brief: primary/backup/alternative auto-filled from that order unless the buyer picks; warnings if a pick is not verified local.

## 3. Test inputs

| Headliner | City | Capacity | Size filter |
|---|---|---|---|
| Khruangbin | Nashville, Tennessee | 800 | on (Qloo popularity <= 0.975) |
| Japanese Breakfast | Atlanta, Georgia | 800 | on (Qloo popularity <= 0.975) |
| St. Vincent | Austin, Texas | 800 | on (Qloo popularity <= 0.975) |

Live run on 29 Oct 2026 against the real Qloo hackathon API, Tavily and OpenAI (searches partly served from a 14-day cache of earlier identical searches).

## 4. Outputs


### Khruangbin in Nashville, Tennessee

**Status:** ready. 3 candidates verified as local to the region area  
**Candidates considered:** 82. **Picks:** primary Ron Gallo, backup Becca Mancari, alternative TWEN.

**Agent decisions:**
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Nashville, Tennessee before widening
- Expanding from the verified local act Ron Gallo: Qloo found 47 new artists their fans like in Nashville, Tennessee; checking the 10 whose Qloo taste tags overlap most with Khruangbin
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for city, so the search widens to metro
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Nashville metropolitan area, Tennessee before widening
- Expanding from the verified local act Ron Gallo: Qloo found 35 new artists their fans like in Nashville metropolitan area, Tennessee; checking the 10 whose Qloo taste tags overlap most with Khruangbin
- 1 of the 3 verified local acts needed after checking Qloo's list and the local scene for metro, so the search widens to region
- 1 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Tennessee before widening
- Expanding from the verified local act Ron Gallo: Qloo found 38 new artists their fans like in Tennessee; checking the 10 whose Qloo taste tags overlap most with Khruangbin
- 3 of 82 live candidates are verified as local to the region area (need 3)
- Web searches: 10 paid, 154 from the cache (budget 100 per run)

**Verified local acts (with the deciding evidence):**

| Artist | How found | Qloo popularity | Claim | Source | Evidence |
|---|---|---|---|---|---|
| Ron Gallo | deeper in Qloo list | 0.921 | based_here | public | "Meet Ron Gallo, the Nashville-based musician voicing a revv’d up headfirst agenda – HERO" https://hero-magazine.com/article/60622/meet-ron-gallo-the-nashville-based-musician-voicing-a-revvd-up-headfirst-agenda |
| Becca Mancari | via local act Ron Gallo | 0.791 | based_here | public | "Hi my name is Becca Mancari, and I am an Indie musician living in Nashville, TN." https://campfiretales.uk/country/hello-becca-mancari |
| TWEN | via local act Ron Gallo | 0.715 | formed_here | musicbrainz | "Begin area: Nashville" https://musicbrainz.org/artist/9987f4cb-15a4-492d-964b-7afb5bf05470 |

**Top of the shortlist as ranked (first 10):**

| # | Artist | How found | Pop. | Taste | Local | Size | Shared Qloo tags with headliner | Played city |
|---|---|---|---|---|---|---|---|---|
| 1 | Ron Gallo | qloo_deeper | 0.921 | 0.47 | 1.0 | 0.9 | - | yes |
| 2 | Becca Mancari | scene | 0.791 | 0.045 | 1.0 | 0.9 | Rock | - |
| 3 | TWEN | scene | 0.715 | 0.083 | 0.8 | 0.9 | Psychedelic, Atmospheric | - |
| 4 | Hermanos Gutiérrez | qloo | 0.965 | 0.705 | 0.3 | 0.9 | Psychedelic, Instrumental, Psychedelic Rock, Eclectic | yes |
| 5 | The Nude Party | qloo | 0.858 | 0.6 | 0.3 | 0.9 | Rock, Psychedelic Rock, Psychedelic | yes |
| 6 | Neal Francis | qloo_deeper | 0.828 | 0.561 | 0.25 | 0.9 | Funk, Rock, Soul, Funky | yes |
| 7 | Cory Wong | qloo_deeper | 0.934 | 0.521 | 0.3 | 0.9 | Funk, Soul, Funky, Groovy | - |
| 8 | Sam Evian | qloo | 0.902 | 0.547 | 0.25 | 0.9 | Rock, Laid-Back | yes |
| 9 | Kikagaku Moyo | qloo | 0.948 | 0.696 | 0.0 | 0.9 | Psychedelic, Psychedelic Rock, Open-Minded, Adventurous | - |
| 10 | Brittany Howard | qloo | 0.935 | 0.616 | 0.3 | 0.6 | Soul | yes |

**Booking-brief warnings:** none  
**Generated summary:** The booking brief features Khruangbin as the headliner in Nashville, Tennessee, at a venue with a capacity of 800. Ron Gallo is the primary support act, ranked 17 with verified local evidence of his presence and performances in the area. Becca Mancari, another local artist, is a backup option, while TWEN is an alternative choice, both having ties to Ron Gallo. Gallo has 358,650 monthly listeners, Mancari has 192,847, and TWEN has 2,513. Availability, fee, and headliner agent approval are unknown.  
*Note: Generated before the 29 Oct fix that stops scene-found picks being described by a Qloo rank.*

### Japanese Breakfast in Atlanta, Georgia

**Status:** ready. 3 candidates verified as local to the city area  
**Candidates considered:** 35. **Picks:** primary Lunar Vacation, backup girlpuppy, alternative Mamalarky.

**Agent decisions:**
- 2 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Atlanta, Georgia before widening
- Expanding from the verified local acts Lunar Vacation, girlpuppy: Qloo found 30 new artists their fans like in Atlanta, Georgia; checking the 10 whose Qloo taste tags overlap most with Japanese Breakfast
- 3 of 35 live candidates are verified as local to the city area (need 3)
- Web searches: 10 paid, 60 from the cache (budget 100 per run)

**Verified local acts (with the deciding evidence):**

| Artist | How found | Qloo popularity | Claim | Source | Evidence |
|---|---|---|---|---|---|
| Lunar Vacation | top of Qloo list | 0.892 | based_here | public | "Reaching for the stars: Atlanta-based band Lunar Vacation dreams big" https://www.redandblack.com/culture/reaching-for-the-stars-atlanta-based-band-lunar-vacation-dreams-big/article_5a5f06fa-f8ac-11e9-8b79-db46fb4a5959.html |
| girlpuppy | top of Qloo list | 0.759 | based_here | public | "In the Atlanta-based musician's case, it's her jangle-pop alias Girlpuppy, which found itself thriving after a COVID layoff." https://www.thefader.com/2021/06/30/girlpuppy-as-much-as-i-can |
| Mamalarky | via local act Lunar Vacation, girlpuppy | 0.808 | based_here | musicbrainz | "Area: Atlanta" https://musicbrainz.org/artist/83ad497d-a191-4f68-b5a4-dd9ac046ac28 |

**Top of the shortlist as ranked (first 10):**

| # | Artist | How found | Pop. | Taste | Local | Size | Shared Qloo tags with headliner | Played city |
|---|---|---|---|---|---|---|---|---|
| 1 | Lunar Vacation | qloo | 0.892 | 0.804 | 1.0 | 0.9 | Indie, Indie Pop, Dream Pop, Indie Rock | - |
| 2 | girlpuppy | qloo | 0.759 | 0.675 | 1.0 | 0.9 | Indie, Indie Pop, Indie Rock, Emotive | yes |
| 3 | Mamalarky | scene | 0.808 | 0.625 | 1.0 | 0.9 | Indie Rock, Indie, Indie Pop, Dream Pop | - |
| 4 | Okey Dokey | scene | 0.765 | 0.75 | 0.3 | 0.9 | Indie Pop, Indie, Indie Rock | - |
| 5 | sasami | qloo | 0.933 | 0.733 | 0.3 | 0.9 | Indie Rock, Experimental, Intimate, Expressive | yes |
| 6 | Dehd | qloo | 0.939 | 0.717 | 0.3 | 0.9 | Indie Rock, Dream Pop, Dreamy, Melodic | - |
| 7 | Rostam | qloo | 0.941 | 0.872 | 0.0 | 0.9 | - | - |
| 8 | Caroline Rose | qloo | 0.957 | 0.65 | 0.25 | 0.9 | Indie Pop, Indie, Indie Rock, Engaging | yes |
| 9 | Miya Folick | qloo | 0.938 | 0.707 | 0.1 | 0.9 | Indie, Indie Pop, Indie Rock, Emotive | - |
| 10 | Squirrel Flower | qloo_deeper | 0.906 | 0.593 | 0.25 | 0.9 | Dream Pop, Indie Rock, Emotive, Melodic | yes |

**Booking-brief warnings:** none  
**Generated summary:** The booking brief features Japanese Breakfast as the headliner in Atlanta, Georgia, at a venue with a capacity of 800. The primary support act is Lunar Vacation, an Atlanta-based band with a Qloo rank of 3, known for their indie and dream pop sound. Backup support is provided by girlpuppy, also local, with a Qloo rank of 10 and a history of performances in the area. An alternative option is Mamalarky, who recently relocated to Atlanta and has a Qloo rank of 20. Availability, fee, and headliner agent approval are unknown.  
*Note: Generated before the 29 Oct fix that stops scene-found picks being described by a Qloo rank.*

### St. Vincent in Austin, Texas

**Status:** ready low evidence. Searched up to region scope; only 2 of 61 candidates are verified as local. Treat the list as research, not a recommendation.  
**Candidates considered:** 61. **Picks:** primary Molly Burch, backup Why Bonnie, alternative Suki Waterhouse.

**Agent decisions:**
- 0 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Austin, Texas before widening
- 0 of the 3 verified local acts needed after checking Qloo's list for city, so the search widens to metro
- 0 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Austin metropolitan area, Texas before widening
- 0 of the 3 verified local acts needed after checking Qloo's list for metro, so the search widens to region
- 0 of the 3 verified local acts needed. Small local acts often sit lower in Qloo's list, so checking the next 10 for Texas before widening
- Expanding from the verified local act Molly Burch: Qloo found 41 new artists their fans like in Texas; checking the 10 whose Qloo taste tags overlap most with St. Vincent
- Searched up to region scope; only 2 of 61 candidates are verified as local. Treat the list as research, not a recommendation.
- Web searches: 0 paid, 122 from the cache (budget 100 per run)

**Verified local acts (with the deciding evidence):**

| Artist | How found | Qloo popularity | Claim | Source | Evidence |
|---|---|---|---|---|---|
| Molly Burch | deeper in Qloo list | 0.915 | based_here | public | "Austin-based (via Los Angeles) singer songwriter Molly Burch has been out on the road this past month, in support of her debut album Please Be Mine." https://www.anchrmagazine.com/interviews/2017/3/20/a-chat-with-molly-burch |
| Why Bonnie | via local act Molly Burch | 0.713 | formed_here | musicbrainz | "Begin area: Austin" https://musicbrainz.org/artist/ac23ff2a-1174-4f3b-8bba-a2470333cb60 |

**Top of the shortlist as ranked (first 10):**

| # | Artist | How found | Pop. | Taste | Local | Size | Shared Qloo tags with headliner | Played city |
|---|---|---|---|---|---|---|---|---|
| 1 | Molly Burch | qloo_deeper | 0.915 | 0.439 | 1.0 | 0.9 | Singer & Songwriter, Indie, Expressive | yes |
| 2 | Why Bonnie | scene | 0.713 | 0.375 | 0.8 | 0.9 | Indie Pop, Indie, Indie Rock, Alternative Rock | - |
| 3 | Suki Waterhouse | qloo | 0.963 | 0.65 | 0.3 | 0.9 | Indie Pop, Expressive, Electropop | yes |
| 4 | Caroline Rose | qloo | 0.957 | 0.644 | 0.3 | 0.9 | Singer & Songwriter, Indie Pop, Indie, Indie Rock | yes |
| 5 | Ramona Lisa | qloo | 0.89 | 0.667 | 0.25 | 0.9 | Indie, Experimental, Open-Minded, Artistic | yes |
| 6 | Kim Gordon | qloo | 0.941 | 0.665 | 0.25 | 0.9 | Alternative Rock, Indie Rock, Experimental, Innovative | yes |
| 7 | Alaska Thunderfuck | qloo_deeper | 0.967 | 0.746 | 0.1 | 0.9 | - | - |
| 8 | Mary Timony | qloo_deeper | 0.918 | 0.647 | 0.25 | 0.9 | Singer & Songwriter, Indie, Indie Rock, Alternative Rock | yes |
| 9 | Rostam | qloo | 0.941 | 0.724 | 0.1 | 0.9 | - | - |
| 10 | Eleanor Friedberger | qloo | 0.95 | 0.722 | 0.1 | 0.9 | Indie, Indie Rock, Alternative Rock, Engaging | - |

**Booking-brief warnings:** none  
**Generated summary:** The booking brief features St. Vincent as the headliner in Austin, Texas, at a venue with a capacity of 800. The primary support act is Molly Burch, who is local and has a Qloo rank of 23, fitting well for the slot with 236,257 monthly listeners. Backup act Why Bonnie, with a Qloo rank of 50, is also local but has not played in the area. Alternative act Suki Waterhouse, ranked 1, has performed locally and has 113,773 monthly listeners. Availability, fee, and headliner agent approval are unknown.  
*Note: Generated before the 29 Oct fix that stops scene-found picks being described by a Qloo rank.*

## 5. How we evaluated, and results

### 5a. Evidence accuracy on hand-labelled cases

Question per case: *is this artist based in, or from, this city?* Labels were written by the developer from public bios. **Precision** = of artists the system calls local, the share that really are; **recall** = of real locals, the share it finds. A case counts as "local" when local score >= 0.5. Three configurations are scored on the same cases.

**Development set (17 cases; 4 of them are false positives the rules were tuned on)**

| Artist | City | Truth | Web | MusicBrainz | Both | Label reason |
|---|---|---|---|---|---|---|
| Lunar Vacation | Atlanta | yes | yes | yes | yes | Atlanta band |
| girlpuppy | Atlanta | yes | yes | yes | yes | Becca Harvey, Atlanta |
| Faye Webster | Atlanta | yes | no | yes | yes | Atlanta singer-songwriter |
| Dehd | Atlanta | no | no | no | no | Chicago band; a member spoke of being from Atlanta |
| Soccer Mommy | Atlanta | no | no | no | no | Nashville act that played Atlanta |
| Mitski | Atlanta | no | no | no | no | touring act |
| Soccer Mommy | Nashville | yes | no | no | no | Nashville |
| All Them Witches | Nashville | yes | yes | yes | yes | Nashville band |
| Ron Gallo | Nashville | yes | yes | no | yes | Philadelphia-born, lives in Nashville |
| The Nude Party | Nashville | no | no | no | no | North Carolina / New York; label is Nashville-based |
| Whitney | Nashville | no | no | no | no | Chicago band; name collides with Whitney Houston |
| Brittany Howard | Nashville | yes | no | no | no | Athens AL native living in Nashville (check) |
| Shakey Graves | Austin | yes | yes | yes | yes | Austin |
| Black Pumas | Austin | yes | yes | yes | yes | Austin band |
| Suki Waterhouse | Austin | no | no | no | no | London/LA; played ACL Fest |
| Girlpool | Austin | no | no | no | no | Los Angeles; an Austin blog wrote about them |
| Tennis | Austin | no | no | no | no | Denver band; name collides with the sport |

| Configuration | Precision | Recall | Missed |
|---|---|---|---|
| Web only | 100% | 67% | Faye Webster/Atlanta, Soccer Mommy/Nashville, Brittany Howard/Nashville |
| MusicBrainz only | 100% | 67% | Soccer Mommy/Nashville, Ron Gallo/Nashville, Brittany Howard/Nashville |
| Both (used by the app) | 100% | 78% | Soccer Mommy/Nashville, Brittany Howard/Nashville |

**Held-out set (20 cases, Philadelphia and Chicago, never used during development)**

| Artist | City | Truth | Web | MusicBrainz | Both | Label reason |
|---|---|---|---|---|---|---|
| Alex G | Philadelphia | yes | no | no | no | Philadelphia-area songwriter |
| Hop Along | Philadelphia | yes | yes | yes | yes | Philadelphia band |
| The War on Drugs | Philadelphia | yes | yes | yes | yes | formed in Philadelphia |
| Kurt Vile | Philadelphia | yes | no | no | no | Philadelphia-area musician |
| Mannequin Pussy | Philadelphia | yes | yes | no | yes | Philadelphia band |
| Sheer Mag | Philadelphia | yes | yes | yes | yes | Philadelphia band |
| Snail Mail | Philadelphia | no | no | no | no | Baltimore act |
| Big Thief | Philadelphia | no | no | no | no | Brooklyn band |
| Waxahatchee | Philadelphia | no | no | no | no | Alabama / Kansas City |
| Soccer Mommy | Philadelphia | no | no | no | no | Nashville act |
| Whitney | Chicago | yes | yes | no | yes | Chicago band |
| Dehd | Chicago | yes | yes | yes | yes | Chicago band |
| Ratboys | Chicago | yes | yes | no | yes | Chicago band |
| Twin Peaks | Chicago | yes | yes | yes | yes | Chicago band (name collides with the TV show) |
| Wilco | Chicago | yes | yes | yes | yes | Chicago band |
| Tennis | Chicago | no | no | no | no | Denver band |
| Faye Webster | Chicago | no | no | no | no | Atlanta |
| Alex G | Chicago | no | no | no | no | Philadelphia |
| Hop Along | Chicago | no | no | no | no | Philadelphia |
| Lunar Vacation | Chicago | no | no | no | no | Atlanta |

| Configuration | Precision | Recall | Missed |
|---|---|---|---|
| Web only | 100% | 82% | Alex G/Philadelphia, Kurt Vile/Philadelphia |
| MusicBrainz only | 100% | 55% | Alex G/Philadelphia, Kurt Vile/Philadelphia, Mannequin Pussy/Philadelphia, Whitney/Chicago, Ratboys/Chicago |
| Both (used by the app) | 100% | 82% | Alex G/Philadelphia, Kurt Vile/Philadelphia |

### 5b. Four-way comparison

For each city, four lists of 8 artists for the same show: **LLM on its own** (gpt-4o-mini, no tools, asked for local or regional support acts), **Qloo, no city** (top 8 related artists without location), **Qloo with the city** (top 8 with location signal, no size filter), **Opening Act** (top 8 of the agent's shortlist). Every artist in every list was checked with the same evidence rules at the same area and the same size rule. Metrics count artists out of 8: *verified local*; *verified local and within size limit*; *above size limit* (Qloo popularity > 0.975); *played city only* (verified performance there, not local); *not in Qloo* (no exact catalog match).

| City | Measure | LLM on its own | Qloo, no city | Qloo with the city | Opening Act |
|---|---|---|---|---|---|
| Nashville | Verified local | 3 | 0 | 0 | 3 |
| Nashville | Local and within size limit | 3 | 0 | 0 | 3 |
| Nashville | Above size limit | 0 | 1 | 4 | 0 |
| Nashville | Played city only | 1 | 0 | 2 | 4 |
| Atlanta | Verified local | 1 | 0 | 1 | 3 |
| Atlanta | Local and within size limit | 0 | 0 | 0 | 3 |
| Atlanta | Above size limit | 7 | 5 | 6 | 0 |
| Atlanta | Played city only | 2 | 4 | 5 | 2 |
| Austin | Verified local | 3 | 0 | 0 | 2 |
| Austin | Local and within size limit | 2 | 0 | 0 | 2 |
| Austin | Above size limit | 3 | 7 | 8 | 0 |
| Austin | Played city only | 3 | 3 | 5 | 5 |

Full lists with per-artist flags are in `opening_act_eval_data.json` -> cities[].four_way_comparison.lists.

### 5c. Blind rating by an AI judge

The four lists per city were shuffled, labelled A-D and shown with artist names only (no source, scores or evidence) to ChatGPT in a fresh chat, using the prompt in `opening_act_eval_data.json` -> blind_rating.prompt. It scored each list 1-5 for **fit** (would the headliner's fans enjoy these artists) and **useful** (usefulness for booking an opener for an 800-capacity room in that city). The method behind each letter was revealed only afterwards.

| City | List | Method (revealed after) | Fit | Useful | Judge's note |
|---|---|---|---|---|---|
| Nashville | A | Opening Act | 4 | 4 | Strong psychedelic and groove-oriented mix; several credible regional acts, though some stylistic variation. |
| Nashville | B | Qloo, no city | 5 | 3 | Excellent global psych, funk and soul alignment; several acts are niche or may be difficult to book, and local relevance is uncertain. |
| Nashville | C | LLM on its own | 3 | 4 | Strong Americana and country-soul talent, but the overall sound is less aligned with Khruangbin's instrumental psychedelic grooves. |
| Nashville | D | Qloo with the city | 5 | 2 | Excellent adventurous funk and psych connections, but Vulfpeck, Black Pumas and Brittany Howard are likely too established or large for this opening slot. |
| Atlanta | A | Qloo, no city | 5 | 2 | Excellent indie-pop and art-rock fit, but Mitski, Alvvays, beabadoobee and several others are too established to be realistic openers. |
| Atlanta | B | Opening Act | 4 | 5 | A credible indie and art-pop shortlist with promising regional acts including Mamalarky, Lunar Vacation and girlpuppy; strongest practical booking mix. |
| Atlanta | C | Qloo with the city | 5 | 1 | Exceptional stylistic fit, but Phoebe Bridgers, Lucy Dacus, Big Thief and Mitski are far too prominent for a typical 800-capacity support slot. |
| Atlanta | D | LLM on its own | 4 | 1 | Indie-rock connections are strong, but including Japanese Breakfast itself is inappropriate and several other acts are too established. |
| Austin | A | LLM on its own | 3 | 2 | Eclectic lineup with some Texas connections, but country, soul and roots acts do not consistently match St. Vincent's art-rock sound; several are too large. |
| Austin | B | Opening Act | 5 | 4 | Strong art-rock and alternative connections, with several plausible independent acts; Alaska Thunderfuck is a stylistic wildcard and local fit varies. |
| Austin | C | Qloo, no city | 5 | 1 | Excellent experimental art-pop alignment, but Sufjan Stevens and Beach House are far too established, with other acts also difficult to book at this scale. |
| Austin | D | Qloo with the city | 5 | 1 | Outstanding experimental and art-pop fit, but most artists are major or established touring acts and the list is largely unrealistic for an 800-capacity opener. |

| Method | Mean fit | Mean useful |
|---|---|---|
| Opening Act | 4.33 | 4.33 |
| Qloo, no city | 5.00 | 2.00 |
| LLM on its own | 3.33 | 2.33 |
| Qloo with the city | 5.00 | 1.33 |

## 6. History: what earlier versions got wrong

| Version | What happened | Fix |
|---|---|---|
| 28 Oct, first live run | 13/15, 14/15 and 13/15 candidates called "local": any page naming artist + city counted, and having played the city counted as local. Wrong identities (Whitney -> Whitney Houston, SAULT -> Sault Ste. Marie, Tennis -> tennis club). | Identity checks, listing-page rejection, "played here" separated from local. |
| 28 Oct, second live run | 4 of 7 "local" artists wrong: label based in city (The Nude Party), interviewee from city (Dehd), festival appearance (Suki Waterhouse), blog based in city (Girlpool). | The LLM's label became a hint; a sentence must state the claim about the artist. |
| 29 Oct, morning | Brittany Howard credited via her side band "formed in Nashville"; Ron Gallo via "spent time living in". | Current base vs origin split; negation/past/hearsay and other-band guards. |
| 29 Oct, first run with new discovery | Cory Wong ("Petar Janjic ... playing with Cory Wong ... Nashville-based") and Molly Burch in Atlanta ("my mom lives in Atlanta"). Cory Wong was the primary pick. | Subject check (artist must be the subject), relative/kin guard. |
| 29 Oct, final run (this pack) | 8 verified locals across 3 cities, all judged correct by the developer; 5 of them found by the new "deeper" and "local scene" moves. | - |

## 7. Questions for the reviewer

1. **Verified locals:** are all 8 verified local acts in section 4 actually based in or from the stated city? Name any you believe are wrong.
2. **Evidence rules:** do the rules in section 2 have obvious holes (cases that would still produce a false "local" claim)? Give example sentences.
3. **Formed-here weight:** is counting "from / formed in the area" (0.8) as local proof, alongside "based here now" (1.0), reasonable for booking openers?
4. **Size filter:** is a Qloo popularity limit of 0.975 for an 800-capacity room a sensible proxy for "realistic opener"? What would be better?
5. **Taste score:** is combining relative Qloo affinity (60%) with tag overlap (40%) defensible? Nashville picks share only 0-8% of Khruangbin's tags, yet the AI judge rated that list 4/5 for fit. What does that suggest?
6. **Comparison fairness:** is the four-way comparison fair to the LLM baseline (prompt, list size, same checks for all)? What would make it more convincing?
7. **Blind rating:** how much weight should an AI judge get here, given that the LLM baseline list was also produced by an OpenAI model? What is the minimum human evaluation that would be credible?
8. **Precision claims:** with 17 + 20 labelled cases and 0 false positives, what precision can honestly be claimed (give a confidence interval)? Which labels look wrong to you?
9. **Overall:** which single weakness would a hackathon judge most likely notice, and how should it be fixed or disclosed?
