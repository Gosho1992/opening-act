"""LLM use is fenced: it only explains data already in the brief, and it powers the
LLM-only baseline for the comparison. Every call has a timeout and a fallback."""
from __future__ import annotations

import json
import re
from typing import Optional

from . import config


class LLMUnavailable(Exception):
    pass


SYSTEM = (
    "You write the summary of a booking brief for a venue talent buyer. Use ONLY the JSON provided. "
    "Never add an artist, fact, number, venue, date or URL that is not in the JSON. Scores are the tool's own "
    "heuristics, not audience measurements; do not call them percentages of fans. If something is missing, "
    "say it is unknown. Under 110 words, plain prose, no headings."
)


def _client():
    if not config.OPENAI_API_KEY:
        raise LLMUnavailable("OPENAI_API_KEY is not set")
    from openai import OpenAI
    return OpenAI(api_key=config.OPENAI_API_KEY, timeout=30, max_retries=1)


def _chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = False) -> str:
    kwargs = {"model": config.OPENAI_MODEL, "messages": messages, "temperature": temperature}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()
    except LLMUnavailable:
        raise
    except Exception as e:  # network, auth, rate limit, malformed response
        raise LLMUnavailable(f"{type(e).__name__}") from e


def _slim(c: Optional[dict]) -> Optional[dict]:
    if not c:
        return None
    return {
        "artist": c["artist_name"],
        # a scene artist's rank is its rank among a LOCAL act's fans, not the headliner's: leave it out
        "qloo_rank_for_headliner_fans_with_city_signal": c.get("city_rank") if c.get("found_via") not in ("scene", "llm_local") else None,
        "qloo_rank_for_headliner_fans_without_city_signal": c.get("noloc_rank"),
        "new_with_city_signal": c.get("new_in_city"),
        "verified_evidence": [{"claim": e["claim"], "quote": e.get("quote"), "source": e.get("source_url") or "Qloo"}
                              for e in c.get("evidence", []) if e.get("status") == "verified"],
        "verified_local": c.get("local_proof"),
        "found_via": c.get("found_via"),
        "found_through_local_act": c.get("scene_seed"),
        "shared_qloo_taste_tags_with_headliner": (c.get("shared_tags") or [])[:6],
        "has_played_the_area": c.get("plays_market"),
        "size_note": c.get("practical_note"),
    }


def template_narrative(brief: dict, picks: dict) -> str:
    p = picks.get("primary")
    if not p:
        return (f"No candidate is selected for {brief['headliner_name']} in {brief['city']}. "
                "Availability, fee and headliner approval are unknown.")
    if p.get("found_via") in ("scene", "llm_local"):
        origin = (f"found through the local act {p.get('scene_seed')}" if p.get("found_via") == "scene"
                  else "suggested by the language model as a local act, then found in Qloo and checked against sources")
        if p.get("shared_tags"):
            origin += f" and sharing Qloo taste tags with the headliner ({', '.join(p['shared_tags'][:3])})"
        origin += "."
    else:
        origin = (f"Qloo rank #{p.get('city_rank')} with the city signal"
                  + (f" (#{p['noloc_rank']} without it)." if p.get("noloc_rank") else " (not in the no-city list)."))
    parts = [f"For {brief['headliner_name']} in {brief['city']} ({brief['venue_capacity']} capacity), the primary pick is "
             f"{p['artist_name']}, {origin}"]
    parts.append("It has public local evidence." if p.get("local_proof") else "Its local connection is not established.")
    for slot in ("backup", "alternative"):
        c = picks.get(slot)
        if c:
            parts.append(f"{slot.title()}: {c['artist_name']}"
                         + (" (local evidence found)." if c.get("local_proof") else " (local connection not established)."))
    parts.append("Availability, fee and headliner-agent approval are unknown.")
    return " ".join(parts)


_STATUS_TEXT = {"based_now": "based in the area per a recent source", "based_as_of": "based in the area per an older or undated source",
                "roots": "from or formed in the area (current base not confirmed)"}


def _fit_text(c: dict) -> str:
    bits = []
    if c.get("city_rank") is not None and c.get("found_via") in ("qloo", "qloo_deeper"):
        bits.append(f"Qloo ranks it #{c['city_rank']} among artists the headliner's fans like with the city signal")
    if c.get("shared_tags"):
        bits.append(f"shares Qloo tags {', '.join(c['shared_tags'][:3])}")
    return "; ".join(bits) or (c.get("fit_note") or "fit unknown")


def facts_narrative(brief: dict, picks: dict, kind: str, n_qual: int, n_local: int,
                    local_weak_fit: list[dict], fit_not_local: list[dict], blocked: Optional[list[dict]] = None) -> str:
    """The brief's summary, written ONLY from stored fields, so it cannot say more than the evidence does."""
    head, city = brief.get("headliner_resolved_name") or brief["headliner_name"], brief["city"]
    out = []
    if kind == "recommendation":
        out.append(f"For {head} in {city} ({brief['venue_capacity']} capacity), {n_qual} acts passed the booking gate: "
                   f"none failed a check on taste fit, local connection, size or activity.")
    elif kind == "partial_recommendation":
        out.append(f"For {head} in {city}, only {n_qual} of the 3 acts needed passed the booking gate (no failed check on "
                   f"taste fit, local connection, size or activity), so this is a partial recommendation.")
    else:
        out.append(f"For {head} in {city}, no act passed all four checks (taste fit, local connection, size, activity; "
                   f"{n_local} have a verified local connection), so this is a research shortlist with no automatic picks.")
    for slot in ("primary", "backup", "alternative"):
        c = picks.get(slot)
        if c:
            who = (" (chosen by the buyer)" if not c.get("qualified")
                   else f" (provisional: confirm {', '.join(c['gate']['to_confirm'])})"
                   if (c.get("gate") or {}).get("status") == "provisional" else " (qualified)")
            out.append(f"{slot.title()}: {c['artist_name']}{who}, "
                       f"{_STATUS_TEXT.get(c.get('local_status'), 'local connection not verified')}"
                       f"{' (' + c['local_note'] + ')' if c.get('local_note') and c.get('local_status') == 'based_as_of' else ''}; "
                       f"{_fit_text(c)}.")
    if local_weak_fit:
        out.append("Local, but little Qloo taste fit: " + ", ".join(c["artist_name"] for c in local_weak_fit[:3]) + ".")
    if blocked:
        out.append("Local and a taste fit, but blocked by the size or activity check: "
                   + ", ".join(f"{c['artist_name']} ({next(v['note'] for v in c['gate']['checks'].values() if v['state'] == 'fail')})"
                               for c in blocked[:3]) + ".")
    if fit_not_local:
        out.append("Strong Qloo taste fit, local connection not verified: "
                   + ", ".join(c["artist_name"] + (" (has played the city)" if c.get("plays_market") else "")
                               for c in fit_not_local[:3]) + ".")
    out.append("Availability, fee and headliner-agent approval are unknown.")
    return " ".join(out)


def write_narrative(brief: dict, picks: dict) -> tuple[str, str]:
    """Returns (text, source) where source is 'llm' or 'template'."""
    payload = {"headliner": brief["headliner_name"], "city": brief["city"],
               "venue_capacity": brief["venue_capacity"],
               **{slot: _slim(picks.get(slot)) for slot in ("primary", "backup", "alternative")},
               "always_unknown": ["availability", "fee", "headliner agent approval"]}
    try:
        text = _chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload)}])
        allowed = {n.lower() for n in [brief["headliner_name"], brief["city"]] +
                   [picks[s]["artist_name"] for s in picks if picks.get(s)]}
        # Guard: reject narratives that cite a URL we did not provide.
        urls = set(re.findall(r"https?://\S+", text))
        known = {e.get("source_url") for s in picks.values() if s for e in s.get("evidence", [])}
        if text and not (urls - known) and allowed:
            return text, "llm"
    except LLMUnavailable:
        pass
    return template_narrative(brief, picks), "template"


def write_pitch(brief: dict, primary: Optional[dict]) -> str:
    if not primary:
        return "No candidate is selected yet."
    if primary.get("found_via") == "scene":
        rank = f"found through the local act {primary.get('scene_seed')}"
    else:
        rank = f"Qloo rank #{primary.get('city_rank')} for {brief['headliner_name']} fans with the {brief['city']} signal"
        if primary.get("new_in_city"):
            rank += ", and it only appears when the city signal is on"
    if primary.get("shared_tags"):
        rank += f"; shares {', '.join(primary['shared_tags'][:3])} with {brief['headliner_name']} in Qloo"
    proof = "public local evidence attached" if primary.get("local_proof") else "local connection still to confirm"
    role = "local support" if primary.get("local_proof") else "support"
    return f"We'd like {primary['artist_name']} as {role} for {brief['headliner_name']} in {brief['city']}: {rank}; {proof}."


VERIFY_SYSTEM = (
    "You check web search results for a music talent buyer. For each numbered source decide:\n"
    "same_artist: 'yes' if the source is clearly about the given music artist (use the genres and facts given), "
    "'no' if it is about a different person, band, place, sport or thing that shares the name, 'unclear' otherwise.\n"
    "relation: 'based_here' ONLY if the text says the artist is from, based in, formed in, lives in, grew up in or "
    "moved to one of the listed places; 'played_here' if it only says they performed or will perform there; "
    "'none' if it states neither for the listed places. A band from elsewhere that played the city is 'played_here'. "
    "Being from a different city is NOT based_here.\n"
    "quote: copy ONE sentence or phrase verbatim from the source text that supports the relation and names the "
    "place. Empty string if relation is 'none'. Never paraphrase.\n"
    'Reply as JSON: {"results": [{"i": 1, "same_artist": "...", "relation": "...", "quote": "..."}]}'
)


def verify_sources(artist: str, context: dict, place_terms: list[str], hits: list[dict]) -> list[dict]:
    """One call per artist. Returns one verdict per hit, in order. Raises LLMUnavailable."""
    facts = {"artist": artist, "genres": context.get("genres") or [], "qloo_place_of_birth": context.get("place_of_birth"),
             "places_in_search_area": place_terms}
    sources = [{"i": i + 1, "title": (h.get("title") or "")[:200], "url": h.get("url"),
                "text": (h.get("content") or "")[:700]} for i, h in enumerate(hits)]
    raw = _chat([{"role": "system", "content": VERIFY_SYSTEM},
                 {"role": "user", "content": json.dumps({"facts": facts, "sources": sources})}],
                temperature=0, json_mode=True)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise LLMUnavailable("Verifier returned invalid JSON") from e
    by_i = {r.get("i"): r for r in (data.get("results") or []) if isinstance(r, dict)}
    out = []
    for i in range(1, len(hits) + 1):
        r = by_i.get(i) or {}
        out.append({"same_artist": r.get("same_artist") if r.get("same_artist") in ("yes", "no", "unclear") else "unclear",
                    "relation": r.get("relation") if r.get("relation") in ("based_here", "played_here", "none") else "none",
                    "quote": str(r.get("quote") or "")[:400]})
    return out


def suggest_llm_only(headliner: str, city: str, capacity: int, n: int = 8) -> tuple[list[str], str]:
    """The baseline arm: an LLM with no tools. Raises LLMUnavailable. Cached so that a comparison is
    reproducible: the same show always gets the same LLM list until the cache entry expires."""
    from . import cache
    key, params = f"{headliner.lower()}|{city.lower()}|{capacity}|{n}", {"model": config.OPENAI_MODEL, "version": 1}
    hit = cache.get("llm_only", key, params)
    if hit is not None:
        return parse_name_list(hit[0])[:n], hit[0]
    prompt = (f"A talent buyer in {city} needs a local or regional support act for {headliner} at a "
              f"{capacity}-capacity venue. Suggest {n} artists. Reply as JSON: {{\"artists\": [\"name\", ...]}}")
    raw = _chat([{"role": "user", "content": prompt}], temperature=0.3, json_mode=True)
    names = parse_name_list(raw)[:n]
    if names:
        cache.put("llm_only", key, raw, params)
    return names, raw


LOCAL_PROMPT_VERSION = 2


def suggest_local_acts(headliner: str, city: str, capacity: int, n: int = 8, demo: bool = False,
                       style: Optional[list[str]] = None) -> list[str]:
    """Names only. Used as one more DISCOVERY source: every name is then resolved in Qloo and
    checked against sources exactly like Qloo's own suggestions; nothing here counts as evidence.
    Cached (and recorded for demo replay) so a re-run asks the same question once."""
    from . import cache
    from .evidence import _load_replay, _record
    style = [t for t in (style or []) if isinstance(t, str)][:10]
    key = f"{headliner.lower()}|{city.lower()}|{capacity}|{n}|{','.join(style).lower()}"
    params = {"model": config.OPENAI_MODEL, "version": LOCAL_PROMPT_VERSION}
    if demo:
        return list(_load_replay().get("llm_local", {}).get(key, []))
    hit = cache.get("llm_local", key, params)
    if hit is not None:
        return hit[0]
    prompt = (f"A talent buyer in {city} needs a support act for {headliner} at a {capacity}-capacity venue. "
              f"List up to {n} musical acts that are based in the {city} area NOW (living and working there, still "
              f"active), whose music would suit {headliner}'s fans"
              + (f" (Qloo describes {headliner} as: {', '.join(style)}; prefer acts with that sound)" if style else "")
              + f", and that are small enough to open in a room "
              f"this size. Leave out touring acts from elsewhere and acts that moved away. If unsure, list fewer. "
              f"Reply as JSON: {{\"artists\": [\"name\", ...]}}")
    names = parse_name_list(_chat([{"role": "user", "content": prompt}], temperature=0.2, json_mode=True))[:n]
    cache.put("llm_local", key, names, params)
    if config.RECORD_FIXTURES:
        _record("llm_local", key, names)
    return names


def parse_name_list(raw: str) -> list[str]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    names = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            names.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"].strip())
    return [n for n in names if n]
