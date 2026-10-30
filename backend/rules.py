"""Deterministic scoring and search-scope rules.

Every score here is OUR heuristic, not a Qloo-provided measurement:
  taste      relative position of a candidate's Qloo affinity within one query
  local      strength of the public evidence tying the artist to the show's area
  practical  size fit from Spotify monthly listeners vs venue capacity
"""
from __future__ import annotations

import math
from typing import Optional

DEFAULT_WEIGHTS = {"taste": 0.5, "local": 0.3, "practical": 0.2}
WEIGHT_KEYS = set(DEFAULT_WEIGHTS)
LOCAL_PROOF_THRESHOLD = 0.5
MIN_PROVEN = 3          # qualified acts needed for a full recommendation
FIT_MIN_TAGS = 0.15     # minimum share of the headliner's Qloo taste tags, for acts Qloo did not relate to the headliner


class WeightsError(ValueError):
    pass


def normalize_weights(weights: Optional[dict]) -> dict:
    """Validate and normalize to sum 1. Missing keys count as 0."""
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    if not isinstance(weights, dict):
        raise WeightsError("weights must be an object")
    unknown = set(weights) - WEIGHT_KEYS
    if unknown:
        raise WeightsError(f"unknown weight keys: {sorted(unknown)}")
    vals = {}
    for k in WEIGHT_KEYS:
        v = weights.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise WeightsError(f"weight '{k}' must be a number")
        if v < 0:
            raise WeightsError(f"weight '{k}' must not be negative")
        vals[k] = float(v)
    total = sum(vals.values())
    if total <= 0:
        raise WeightsError("at least one weight must be above zero")
    return {k: round(v / total, 4) for k, v in vals.items()}


def taste_score(affinity: Optional[float], aff_min: Optional[float], aff_max: Optional[float],
                rank_delta: Optional[int], new_in_city: bool) -> float:
    """0.5..1.0 from the candidate's affinity relative to its own query's range,
    plus a small bonus when the city signal surfaced or lifted the artist."""
    if affinity is None or aff_min is None or aff_max is None:
        base = 0.5
    elif aff_max - aff_min < 1e-9:
        base = 0.75
    else:
        base = 0.5 + 0.5 * (affinity - aff_min) / (aff_max - aff_min)
    bonus = 0.0
    if new_in_city:
        bonus = 0.05
    elif rank_delta:
        bonus = max(-0.05, min(0.05, rank_delta * 0.01))
    return round(max(0.0, min(1.0, base + bonus)), 3)


# Only verified "based here" evidence reaches the local-proof bar. Playing the city once is a
# market signal, not proof that an act is local.
def tag_overlap(headliner_tags: list[dict], candidate_tags: list[dict]) -> tuple[Optional[float], list[str]]:
    """Share of the smaller tag profile that both artists have in Qloo (genres, Qloo music styles,
    Qloo audience traits). Returns (0..1 or None if either profile is missing, shared tag names
    with genres first). This is Qloo's own description of the two artists, used to explain fit."""
    if not headliner_tags or not candidate_tags:
        return None, []
    h = {t["id"]: t for t in headliner_tags}
    shared = [h[t["id"]] for t in candidate_tags if t["id"] in h]
    score = len(shared) / min(len(h), len({t["id"] for t in candidate_tags}))
    order = {"urn:tag:genre": 0, "urn:tag:music": 1, "urn:tag:style": 2, "urn:tag:audience": 3}
    shared.sort(key=lambda t: next((v for k, v in order.items() if t["type"].startswith(k)), 9))
    names, seen = [], set()
    for t in shared:
        k = t["name"].strip().lower()
        if k not in seen:
            seen.add(k)
            names.append(t["name"].strip())
    return round(score, 3), names


def taste_fit(affinity_part: Optional[float], tag_part: Optional[float]) -> float:
    """Taste dimension: Qloo affinity to the headliner (relative, 0.5..1) and Qloo tag overlap with the
    headliner (0..1). Either can be missing, e.g. artists found through a local act have no
    affinity to the headliner, only tag overlap."""
    if affinity_part is not None and tag_part is not None:
        return round(0.6 * affinity_part + 0.4 * tag_part, 3)
    if tag_part is not None:
        return round(tag_part, 3)
    return round(affinity_part if affinity_part is not None else 0.5, 3)


# "Formed here" (a group's formation place, a person's birthplace) counts as local proof, but is
# shown as its own claim: it says nothing about where the act lives now or how popular it is.
EVIDENCE_WEIGHT = {("based_here", "verified"): 1.0, ("based_here", "unverified"): 0.3,
                   ("formed_here", "verified"): 0.8, ("formed_here", "unverified"): 0.2,
                   ("played_here", "verified"): 0.25, ("played_here", "unverified"): 0.1}


RECENT_YEARS = 3  # a "based here" source this recent counts as current


def _year(row: dict, now: int, encyclopedia_is_current: bool = False) -> Optional[int]:
    """Publication year. An undated encyclopedia page is NOT proof that a 'based here' statement is current
    (the sentence may be years old), but it IS a current statement when it places the act elsewhere, so it
    can mark an act as moved. Callers choose with encyclopedia_is_current."""
    y = row.get("source_year")
    if y:
        return int(y)
    url = str(row.get("source_url") or "")
    return now if encyclopedia_is_current and "wikipedia.org" in url else None


def fit_evidence(c: dict) -> tuple[bool, str]:
    """Is there a Qloo signal that this act suits the headliner's audience?
      - Qloo lists it among artists the headliner's fans like (it has a headliner affinity), or
      - it shares at least FIT_MIN_TAGS of the headliner's Qloo taste tags.
    A heuristic floor, not a validated threshold: it only stops acts with NO fit signal from being picked."""
    if c.get("city_affinity") is not None:
        return True, "Qloo relates it to the headliner's fans"
    ov = c.get("tag_overlap")
    if ov is not None and ov >= FIT_MIN_TAGS:
        return True, f"shares {round(ov * 100)}% of the headliner's Qloo taste tags"
    if ov is None:
        return False, "no Qloo taste data to compare with the headliner"
    return False, f"shares only {round(ov * 100)}% of the headliner's Qloo taste tags (needs {round(FIT_MIN_TAGS * 100)}%)"


def booking_gate(c: dict, venue_capacity: int, now_year: Optional[int] = None) -> dict:
    """The Booking Decision Gate: four independent checks, each pass / caution / fail with a reason.
    An act can be picked automatically only if NO check fails. Cautions are shown to the buyer.

      fit       Qloo relates the act to the headliner's fans, or it shares >= FIT_MIN_TAGS of its taste tags
      local     based here (recent source) = pass; older/undated base or local roots = caution;
                disputed / moved / played only / nothing = fail
      size      Qloo popularity above the room's limit, or Spotify listeners far above it = fail
                (applied to automatic picks even when the buyer turns the size filter off);
                listener count unknown = caution (popularity only)
      activity  a source says it split up / retired = fail (such acts are removed earlier);
                a dated source from the last RECENT_YEARS years = pass; otherwise caution"""
    from datetime import date
    now = now_year or date.today().year
    checks = {}
    ok, why = fit_evidence(c)
    checks["fit"] = ("pass" if ok else "fail", why)

    st = c.get("local_status")
    checks["local"] = (("pass", c.get("local_note") or "based here per a recent source") if st == "based_now"
                       else ("caution", (c.get("local_note") or "older or undated source") + "; confirm the current base")
                       if st in ("based_as_of", "roots")
                       else ("fail", {"disputed": "sources disagree on where the act is based",
                                      "moved": "a source places the act elsewhere now",
                                      "played": "has only played the area"}.get(st, "no verified local connection")))

    cap, pop, ml = popularity_cap(venue_capacity), c.get("popularity"), c.get("monthly_listeners")
    if cap is not None and pop is not None and pop > cap:
        checks["size"] = ("fail", f"Qloo popularity {pop:.3f} is above the {cap} limit for a {venue_capacity}-capacity room")
    elif ml is not None and ml > ideal_max_listeners(venue_capacity) * 3:
        checks["size"] = ("fail", f"{ml:,} monthly listeners: likely too big to open this room")
    elif ml is None:
        checks["size"] = ("caution", "listener count unknown; within the Qloo popularity limit only")
    elif ml > ideal_max_listeners(venue_capacity):
        checks["size"] = ("caution", f"{ml:,} monthly listeners: may be large for this room")
    else:
        checks["size"] = ("pass", f"{ml:,} monthly listeners, within the Qloo popularity limit")

    if c.get("excluded_reason") and "no longer be active" in str(c.get("excluded_reason")):
        checks["activity"] = ("fail", c["excluded_reason"])
    elif c.get("newest_source_year") and int(c["newest_source_year"]) >= now - RECENT_YEARS:
        checks["activity"] = ("pass", f"a source from {c['newest_source_year']}")
    else:
        y = c.get("newest_source_year")
        checks["activity"] = ("caution", "activity needs confirmation" + (f" (newest dated source {y})" if y
                                                                           else " (no dated source)"))
    passed = all(state != "fail" for state, _ in checks.values())
    cautions = [k for k, (state, _) in checks.items() if state == "caution"]
    # qualified = every check satisfied; provisional = no check failed but something still needs confirming
    status = "blocked" if not passed else "provisional" if cautions else "qualified"
    return {"passed": passed, "status": status, "to_confirm": cautions,
            "checks": {k: {"state": v[0], "note": v[1]} for k, v in checks.items()}}


def qualified(c: dict, venue_capacity: Optional[int] = None) -> bool:
    """Eligible for an automatic pick. With a capacity: the full Booking Decision Gate.
    Without one: verified local connection AND a Qloo fit signal."""
    if venue_capacity is not None:
        return booking_gate(c, venue_capacity)["passed"]
    return (c.get("local_score") or 0) >= LOCAL_PROOF_THRESHOLD and fit_evidence(c)[0]


def local_status(evidence: list[dict], now_year: Optional[int] = None) -> dict:
    """Combine all evidence into ONE honest label, score and note.

      based_now     verified 'based here' from a source dated within the last 3 years    1.0
      based_as_of   verified 'based here', older or undated source                      0.75
      roots         verified 'from / formed here' (origin, not necessarily current)      0.6
      disputed      local claim, but sources disagree and none is clearly newer          0.35
      moved         a newer (or encyclopedic) source says the act is based elsewhere     0.2
      played/none   only performances, unverified claims or nothing                      <= 0.3

    Verified local (counts toward the gate) = score >= 0.5: based_now, based_as_of, roots."""
    from datetime import date
    now = now_year or date.today().year
    ev = [e for e in evidence or [] if e.get("status") == "verified"]
    based = [e for e in ev if e.get("kind") == "based_here"]
    roots = [e for e in ev if e.get("kind") == "formed_here"]
    elsewhere = [e for e in ev if e.get("kind") == "based_elsewhere"]
    encyclopedic = any("wikipedia.org" in str(e.get("source_url") or "") for e in based)
    origin_else = [e for e in ev if e.get("kind") == "formed_elsewhere"]
    fallback = max((EVIDENCE_WEIGHT.get((e.get("kind"), e.get("status")), 0.0) for e in evidence or []), default=0.0)
    if not based and not roots:
        return {"status": "played" if fallback >= 0.25 else "none", "score": round(min(fallback, 0.3), 3), "note": None}

    local_years = [y for y in (_year(e, now) for e in based) if y]
    newest_local = max(local_years) if local_years else None
    recent_local = newest_local is not None and newest_local >= now - RECENT_YEARS
    if elsewhere:
        dated_else = [(y, e) for e in elsewhere for y in [_year(e, now, encyclopedia_is_current=True)] if y]
        newer = [(y, e) for y, e in dated_else if newest_local is None or y >= newest_local]
        if newer:
            y, e = max(newer, key=lambda t: t[0])
            return {"status": "moved", "score": 0.2,
                    "note": f"A {y} source says based in {e.get('place')}: \"{(e.get('quote') or '')[:120]}\""}
        if not recent_local:
            e = elsewhere[0]
            return {"status": "disputed", "score": 0.35,
                    "note": f"Sources disagree: one says based in {e.get('place')} (undated)"}
    if based:
        if recent_local:
            return {"status": "based_now", "score": 1.0, "note": f"Based here (source {newest_local})"}
        return {"status": "based_as_of", "score": 0.75,
                "note": f"Based here as of {newest_local}" if newest_local
                else "Based here per Wikipedia (date of the statement unknown)" if encyclopedic
                else "Based here (source date unknown)"}
    # origin only
    if origin_else and all(e.get("source_label") in ("musicbrainz", "qloo") for e in roots):
        e = origin_else[0]
        return {"status": "disputed", "score": 0.35,
                "note": f"Sources disagree on where the act began: one says {e.get('place')}"}
    return {"status": "roots", "score": 0.6, "note": "From or formed here; current base not confirmed"}


def local_score(evidence: list[dict]) -> float:
    """0..1 from local_status. Rejected rows count for nothing."""
    return local_status(evidence)["score"]


def inactive_note(evidence: list[dict]) -> Optional[str]:
    e = next((e for e in evidence or [] if e.get("kind") == "inactive" and e.get("status") == "verified"), None)
    return f"A source says the act may no longer be active: \"{(e.get('quote') or '')[:140]}\"" if e else None


def plays_market(evidence: list[dict]) -> bool:
    return any(e.get("kind") == "played_here" and e.get("status") == "verified" for e in evidence or [])


def popularity_cap(venue_capacity: int) -> Optional[float]:
    """Smaller-acts filter on Qloo's popularity percentile, by room size (heuristic).
    Acts above it are usually headliners in their own right, not openers for this room."""
    if venue_capacity < 500:
        return 0.95
    if venue_capacity < 1500:
        return 0.975
    if venue_capacity < 3000:
        return 0.99
    return None


def ideal_max_listeners(venue_capacity: int) -> int:
    """Rough ceiling for an opener's Spotify monthly listeners by room size (heuristic)."""
    if venue_capacity < 500:
        return 150_000
    if venue_capacity < 1500:
        return 750_000
    if venue_capacity < 3000:
        return 2_000_000
    return 5_000_000


def practical_score(monthly_listeners: Optional[int], venue_capacity: int) -> tuple[float, str]:
    """Size fit. Unknown listeners → neutral 0.5, never guessed."""
    if monthly_listeners is None:
        return 0.5, "Listener count unknown"
    ideal = ideal_max_listeners(venue_capacity)
    if monthly_listeners <= ideal:
        return 0.9, f"{monthly_listeners:,} monthly listeners fits a support slot in this {venue_capacity}-capacity room"
    if monthly_listeners <= ideal * 3:
        return 0.6, f"{monthly_listeners:,} monthly listeners: may be large for this room"
    return 0.3, f"{monthly_listeners:,} monthly listeners: likely too big to open in this {venue_capacity}-capacity room"


def total(scores: dict, weights: Optional[dict]) -> float:
    w = normalize_weights(weights)
    return round(sum(w[k] * float(scores.get(f"{k}_score") or 0.0) for k in WEIGHT_KEYS), 3)


# ── search scope (city → metro → region) ───────────────────────────────────
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
ABBR_TO_STATE = {v: k for k, v in US_STATES.items()}

# Standard nicknames for a city; they count at every level, including the city itself.
CITY_ALIASES = {
    "philadelphia": ["Philly"], "atlanta": ["ATL"], "new york": ["NYC", "New York City"], "new orleans": ["NOLA"],
    "los angeles": ["L.A."], "washington": ["D.C.", "DC"], "san francisco": ["SF"], "las vegas": ["Vegas"],
}

# What a buyer may type instead of the full name. Typed alone ("NOLA") it becomes the full city and state.
CITY_NICKNAMES = {
    "nola": "New Orleans, Louisiana", "philly": "Philadelphia, Pennsylvania", "atl": "Atlanta, Georgia",
    "nyc": "New York, New York", "new york city": "New York, New York", "la": "Los Angeles, California",
    "l.a.": "Los Angeles, California", "sf": "San Francisco, California", "vegas": "Las Vegas, Nevada",
    "dc": "Washington, District of Columbia", "d.c.": "Washington, District of Columbia",
    "chi-town": "Chicago, Illinois", "music city": "Nashville, Tennessee", "atx": "Austin, Texas",
}


def normalize_city(city: str) -> str:
    """'NOLA' -> 'New Orleans, Louisiana'. Anything else is returned unchanged."""
    c = " ".join((city or "").split())
    head = c.split(",")[0].strip().lower()
    if head in CITY_NICKNAMES and (c.lower() == head or "," in c):
        return CITY_NICKNAMES[head]
    return c


# Extra place names that count as "the same area" at the metro level.
METRO_TERMS = {
    "brooklyn": ["New York City", "NYC", "Queens", "Manhattan"],
    "los angeles": ["LA", "Silver Lake", "Echo Park", "Long Beach", "Pasadena"],
    "nashville": ["East Nashville", "Murfreesboro", "Franklin"],
    "atlanta": ["Decatur", "Athens"],
    "austin": ["San Marcos", "Round Rock"],
    "seattle": ["Tacoma", "Olympia", "Bellevue"],
}


def split_city(city: str) -> tuple[str, Optional[str]]:
    """'Austin, Texas' -> ('Austin', 'Texas'); 'Austin, TX' -> ('Austin', 'Texas')."""
    parts = [p.strip() for p in city.split(",") if p.strip()]
    if not parts:
        return city.strip(), None
    name = parts[0]
    state = None
    if len(parts) > 1:
        cand = parts[1]
        if cand.upper() in ABBR_TO_STATE:
            state = ABBR_TO_STATE[cand.upper()].title()
        elif cand.lower() in US_STATES:
            state = cand.title()
        else:
            state = cand
    return name, state


def scopes(city: str) -> list[Optional[dict]]:
    """Three search levels. A level is None when we cannot form it (e.g. no state given)."""
    name, state = split_city(city)
    city_query = f"{name}, {state}" if state else name
    city_terms = [name] + CITY_ALIASES.get(name.lower(), [])
    metro_terms = city_terms + [t for t in METRO_TERMS.get(name.lower(), []) if t not in city_terms]
    levels: list[Optional[dict]] = [
        {"level": 0, "label": "city", "query": city_query, "place_terms": city_terms},
        {"level": 1, "label": "metro", "query": f"{name} metropolitan area" + (f", {state}" if state else ""),
         "place_terms": metro_terms},
    ]
    if state:
        abbr = US_STATES.get(state.lower())
        levels.append({"level": 2, "label": "region", "query": state,
                       "place_terms": metro_terms + [state] + ([abbr] if abbr else [])})
    else:
        levels.append(None)
    return levels
