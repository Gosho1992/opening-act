# Opening Act

Find the local support act whose fans already overlap with your headliner's audience in your city,
with the evidence behind every pick. Built for the Qloo Agentic Hackathon 2026.

## How it works

1. **Resolve** the headliner to an exact Qloo entity (`/search`). Ambiguous names stop and ask the buyer.
2. **Taste profile**: the headliner's Qloo taste tags (`/entities`): genres, styles, audience traits.
3. **Discover** (`/v2/insights`): artists the headliner's fans like, with `signal.location.query` for the
   show's city and once without, plus a **size filter** (`filter.popularity.max`; 0.975 for 500-1,499 rooms).
4. **Agent moves**, each logged with its reason, until 3 acts qualify or every area is searched:
   1. check the top 15 of Qloo's list;
   2. check the next 10 (small local acts sit lower);
   3. **local scene**: ask Qloo what fans of the verified local acts like in the area, rank by Qloo
      taste-tag overlap with the headliner, check the top 10;
   4. **LLM leads** (city level): ask the language model for local acts in the headliner's Qloo style.
      Every name must match exactly one Qloo artist and pass the size filter; it is only a lead;
   5. widen city → metro → state.
   Every candidate's identity is re-checked with `/entities`.
5. **Verify evidence** (Tavily, MusicBrainz, Qloo place of birth). Listing pages and names inside longer
   names ("Whitney Houston") are rejected. The LLM reads each page and must quote a sentence; **the code
   decides** whether that sentence says the artist itself is based in / from the area (not a label, a
   relative, another band or another person; not negated or past). A neutral "where is X based now" search
   looks for contrary sources; acts that moved or stopped performing are flagged or removed. "Played here"
   is recorded but is never local proof.
6. **Local status** per act: *based here (recent source)*, *based here (older or undated source)*, *local
   roots*, *disputed*, *moved*, with the source year shown.
7. **Decision policy**: an act can be picked automatically only if it has BOTH a verified local connection
   AND a Qloo taste-fit signal (Qloo relates it to the headliner's fans, or it shares at least 15% of the
   headliner's Qloo taste tags; a heuristic floor). 3 such acts → **recommendation**; 1-2 → **partial
   recommendation** (empty slots stay empty); none → **research shortlist**. Local acts with little fit and
   strong-fit acts that are not verified local are shown as **trade-offs for the buyer**.
8. **Negotiate**: re-weight, veto, choose picks, widen, toggle the size filter. Reweighting never re-calls the APIs.
9. **Booking brief**: an immutable, versioned snapshot behind an unguessable share link. Its summary is
   written only from stored fields, so it cannot claim more than the evidence.

Scores are labelled heuristics: taste = Qloo affinity relative to its own query plus Qloo tag overlap;
local = the local status above; size = Spotify monthly listeners vs venue capacity. None predict ticket sales.

**Evaluation** (details in `eval_pack/`): `python -m scripts.evidence_eval [--heldout]` scores the evidence
layer on hand-labelled cases; `python -m scripts.live_smoke_test` runs three live cities and a four-way
comparison (LLM alone, Qloo without city, Qloo with city, Opening Act) judged by the same rules.

## Run it

Requires Python 3.10+ and Node 18+.

```bash
cp .env.example .env            # add your keys; never commit .env
python -m pip install -r requirements.txt
python -m pytest -q             # 144 tests, no network needed

cd frontend && npm install && npm run build && cd ..
python -m uvicorn backend.main:app --port 8000
# open http://localhost:8000  (the API serves the built frontend)
```

Frontend development with hot reload: run the API on :8000, then `cd frontend && npm run dev`
and open http://localhost:5173 (Vite proxies `/api`).

### Demo mode

`DEMO_MODE=true` needs no keys. It replays **real** Qloo responses recorded on 2026-10-15 for
Khruangbin, Japanese Breakfast and St. Vincent (`backend/fixtures/qloo_replay.json`). Anything not
recorded returns no results; nothing is invented. Evidence replays only what
`python -m scripts.live_smoke_test --record` captured from live runs.

### Cost control

- Web searches, MusicBrainz lookups and AI verdicts are cached in `search_cache.sqlite` for 14 days
  (`CACHE_TTL_DAYS`), so rerunning the same artists costs nothing.
- `python -m scripts.seed_cache` loads searches recorded by `live_smoke_test --record` into the cache.
- Each agent run and each comparison may make at most `SEARCH_BUDGET_PER_RUN` (default 100) paid
  searches. When the cap is reached the run stops cleanly, keeps its work and says which artists were
  not checked; "Run again" continues from the cache.
- MusicBrainz (free, no key, ~1 request/second) adds "formed in" and "based in" data as separate,
  labelled claims. Set `MUSICBRAINZ=false` to turn it off.
- `python -m scripts.evidence_eval` scores the evidence layer on hand-labelled hard cases.

### Live end-to-end check

```bash
python -m scripts.live_smoke_test
```
Runs the full agent against live Qloo, Tavily and OpenAI and writes `live_smoke_report.json` with
every request, response summary, evidence decision and final status.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/briefs` | Create a brief; the agent runs in the background (202) |
| GET | `/api/briefs/{id}` | Brief, candidates, evidence, runs and agent events (poll while running) |
| POST | `/api/briefs/{id}/confirm` | Pick the right headliner when the name was ambiguous |
| POST | `/api/briefs/{id}/negotiate` | `veto`, `unveto`, `set_slot`, `clear_slot`, `reweight`, `widen`, `toggle_cap`, `rerun` |
| POST | `/api/briefs/{id}/finalize` | Save a new booking-brief version |
| POST | `/api/briefs/{id}/compare` | Four lists (LLM only, Qloo without city, Qloo with city, Opening Act) judged by the same evidence and size rules |
| POST | `/api/briefs/{id}/city-compare` | Same headliner, another city |
| GET | `/api/briefs/{id}/qloo-calls` | Every Qloo request with status and timing |
| GET | `/api/share/{token}` | A saved booking brief |

## Security notes

API keys stay server-side. Briefs are reachable only by their random UUID; share links use a
random token. CORS is limited to `CORS_ORIGINS`. Expensive endpoints are rate-limited per IP
(`RATE_LIMIT_PER_HOUR`). There are no user accounts in this MVP.

## Known limits

- Qloo's location signal shifts taste toward a city; it does not guarantee an artist is local.
  That is why local proof comes from a separate evidence step.
- Search-based evidence can miss real local ties. "Verified" means a quoted sentence on a public
  page supports the claim, not that the claim is true; every item links its source.
- Agent locks are per process: run a single worker (the default `uvicorn` command does).
- Availability, fees and headliner approval are always shown as unknown.
- `filter.popularity.max` is tried first for the smaller-acts preference; if the API rejects it,
  results are filtered locally and the agent log says so.
