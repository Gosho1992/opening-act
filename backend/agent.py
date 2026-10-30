"""The Opening Act agent.

State machine for one brief:

  pending → resolving ─┬→ needs_confirmation        (buyer picks the right artist)
                        ├→ failed                    (artist not in Qloo / provider down)
                        └→ discovering ⇄ investigating
                              ├→ ready               (≥3 live candidates with local proof)
                              ├→ (widen city → metro → region and loop)
                              ├→ ready_low_evidence  (all scopes searched, proof still thin)
                              └→ failed              (no related artists at any scope)
  ready / ready_low_evidence → finalized (immutable, versioned booking brief)

Every step writes an agent event, so the UI can show what the agent did and why.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import config, db, llm, rules
from .budget import BudgetExceeded
from .evidence import EvidenceError, get_provider, qloo_birthplace_evidence
from .musicbrainz import get_musicbrainz
from .qloo_client import BaseQloo, QlooError, get_client, norm

RULES_VERSION = "2026-10-29.v3"  # bump when scoring or evidence rules change; stored in every booking brief
COMPARE_N = 8

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(bid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(bid, threading.Lock())


def is_running(bid: str) -> bool:
    return _lock_for(bid).locked()


class AgentBusy(RuntimeError):
    pass


# ── main loop ─────────────────────────────────────────────────────────────
def run(bid: str, trigger: str = "initial", qloo: Optional[BaseQloo] = None, evidence=None, mb=None) -> None:
    lock = _lock_for(bid)
    if not lock.acquire(blocking=False):
        raise AgentBusy("The agent is already working on this brief")
    run_id = None
    try:
        brief = db.require_brief(bid)
        run_id = db.start_run(bid, trigger, brief["widen_level"], bool(brief["use_popularity_cap"]))
        _run(brief, run_id, qloo, evidence, mb)
    except Exception as e:  # last line of defence: never leave a brief mid-state
        reason = f"Unexpected error ({type(e).__name__}). Use 'Run again' to retry."
        db.add_event(bid, run_id, "error", reason)
        db.set_status(bid, "failed", reason)
        if run_id:
            db.finish_run(run_id, "failed", reason)
    finally:
        lock.release()


def _event(bid: str, run_id: str, type_: str, msg: str, data: Optional[dict] = None) -> None:
    db.add_event(bid, run_id, type_, msg, data)


def _fail(bid: str, run_id: str, reason: str, level: Optional[int] = None) -> None:
    _event(bid, run_id, "failed", reason)
    db.set_status(bid, "failed", reason)
    db.finish_run(run_id, "failed", reason, final_level=level)


def _run(brief: dict, run_id: str, qloo: Optional[BaseQloo], evidence, mb=None) -> None:
    bid = brief["id"]
    demo = bool(brief["demo_mode"])
    qloo = qloo or get_client(demo, db.make_qloo_recorder(bid, run_id))
    provider = evidence or get_provider(demo)
    mb = mb if mb is not None else (get_musicbrainz(demo) if evidence is None else None)
    if demo:
        _event(bid, run_id, "info", "Demo mode: replaying real Qloo responses recorded on 2026-10-15. "
                                    "Queries that were not recorded return nothing.")

    # ── RESOLVE ──────────────────────────────────────────────────────────
    if not brief["headliner_qloo_id"]:
        db.set_status(bid, "resolving")
        _event(bid, run_id, "step", f"Looking up '{brief['headliner_name']}' in Qloo")
        try:
            res = qloo.resolve_artist(brief["headliner_name"])
        except QlooError as e:
            return _fail(bid, run_id, f"Qloo lookup failed: {e}")
        if res["status"] == "not_found":
            return _fail(bid, run_id, f"No artist named '{brief['headliner_name']}' was found in Qloo.")
        if res["status"] == "ambiguous":
            db.update_brief(bid, status="needs_confirmation", resolution_options=res["options"],
                            status_reason="More than one possible match. Pick the right artist.")
            _event(bid, run_id, "needs_input", "The name matches several Qloo entities; waiting for the buyer to "
                                               "confirm which artist is headlining.",
                   {"options": [o["name"] for o in res["options"]]})
            db.finish_run(run_id, "needs_confirmation")
            return
        m = res["match"]
        db.update_brief(bid, headliner_qloo_id=m["id"], headliner_resolved_name=m["name"])
        _event(bid, run_id, "step", f"Matched headliner to Qloo entity '{m['name']}'", {"qloo_id": m["id"]})
        brief = db.require_brief(bid)

    seed = brief["headliner_qloo_id"]
    levels = rules.scopes(brief["city"])
    use_cap = bool(brief["use_popularity_cap"])
    cap = rules.popularity_cap(brief["venue_capacity"]) if use_cap else None
    take = config.QLOO_TAKE          # first batch investigated per area
    pool = max(take, config.QLOO_POOL)  # how many related artists we ask Qloo for
    if cap is None:
        _clear_cap(bid)
    if not getattr(provider, "available", True):
        _event(bid, run_id, "warning", "Evidence search is not configured (no TAVILY_API_KEY). Only Qloo's "
                                       "place-of-birth data can count as local evidence.")

    # ── HEADLINER TASTE PROFILE (Qloo /entities) ─────────────────────────
    head_tags: list[dict] = []
    try:
        prof = qloo.entity_profiles([seed]).get(seed)
        head_tags = (prof or {}).get("tags") or []
    except QlooError as e:
        _event(bid, run_id, "warning", f"Could not read the headliner's Qloo taste tags ({e}); taste fit uses "
                                       f"affinity only")
    if head_tags:
        db.update_brief(bid, headliner_tags=head_tags)
        _event(bid, run_id, "step", f"Read {brief['headliner_resolved_name']}'s Qloo taste profile: {len(head_tags)} "
                                    f"tags, e.g. {', '.join(t['name'] for t in head_tags[:6])}")

    # ── BASELINE (no location) once per run ──────────────────────────────
    db.set_status(bid, "discovering")
    try:
        baseline = qloo.related(seed, None, take=pool)["entities"]
    except QlooError as e:
        return _fail(bid, run_id, f"Qloo discovery failed: {e}")
    base_rank = {e["id"]: e["rank"] for e in baseline}
    base_aff = {e["id"]: e["affinity"] for e in baseline}
    ctx = {"bid": bid, "run_id": run_id, "brief": brief, "qloo": qloo, "provider": provider, "mb": mb,
           "cap": cap, "head_tags": head_tags, "seed": seed}

    final_level = brief["widen_level"]
    for level in range(brief["widen_level"], 3):
        scope = levels[level]
        if scope is None:
            _event(bid, run_id, "info", "No state or region given for this city, so the regional search is skipped. "
                                        "Enter the city as 'City, State' to enable it.")
            continue
        final_level = level
        wide = levels[2] or levels[1] or scope
        scope = {**scope, "wide_terms": list(dict.fromkeys(scope["place_terms"] + wide["place_terms"]))}
        ctx.update(level=level, scope=scope)
        db.update_brief(bid, widen_level=level, status="discovering")
        _event(bid, run_id, "step", f"Asking Qloo for artists {brief['headliner_resolved_name']} fans like, "
                                    f"weighted to {scope['query']} ({scope['label']} scope)",
               {"level": level, "scope": scope["query"]})
        try:
            res = qloo.related(seed, scope["query"], take=pool, popularity_max=cap)
        except QlooError as e:
            if level == brief["widen_level"]:
                return _fail(bid, run_id, f"Qloo discovery failed: {e}", level)
            _event(bid, run_id, "warning", f"Qloo failed at {scope['label']} scope ({e}); keeping earlier results")
            break
        if cap:
            how = "by Qloo" if res["popularity_filter"] == "api" else "locally"
            hidden = res.get("hidden", [])
            _event(bid, run_id, "info", f"Size filter for this {brief['venue_capacity']}-capacity room: artists "
                                        f"above Qloo popularity {cap} are left out (filtered {how})"
                                        + (f"; {len(hidden)} hidden, e.g. {', '.join(hidden[:4])}" if hidden else ""),
                   {"hidden": hidden})
        ranked = res["entities"]
        if not ranked:
            _event(bid, run_id, "warning", f"Qloo returned no related artists for {scope['query']}")
            continue
        affs = [e["affinity"] for e in ranked if e["affinity"] is not None]
        ctx["aff_range"] = (min(affs), max(affs)) if affs else (None, None)

        # Move 1: the top of Qloo's list for this area
        first = _merge(ranked[:take], base_rank, base_aff, level, scope, "qloo", ctx["aff_range"])
        new_n = sum(1 for c in first if c["new_in_city"])
        _event(bid, run_id, "result", f"Qloo returned {len(ranked)} artists; checking the top {len(first)} first "
                                      f"({new_n} of them appear only with the location signal)",
               {"names": [c["artist_name"] for c in first]})
        outcome = _process_batch(ctx, first)
        if outcome:
            return outcome
        proven, qual = _proven(bid), _qualified(bid)
        if len(qual) >= rules.MIN_PROVEN:
            return _ready(ctx, qual)

        # Move 2: dig deeper in the same list before changing area
        deeper = [e for e in ranked[take:take + config.DEEPER_N] if not _known(bid, e["id"], level)]
        if deeper:
            _event(bid, run_id, "decision",
                   f"{_need(qual, proven)}. Small local acts often sit "
                   f"lower in Qloo's list, so checking the next {len(deeper)} for {scope['query']} before widening")
            outcome = _process_batch(ctx, _merge(deeper, base_rank, base_aff, level, scope, "qloo_deeper",
                                                 ctx["aff_range"]))
            if outcome:
                return outcome
            proven, qual = _proven(bid), _qualified(bid)
            if len(qual) >= rules.MIN_PROVEN:
                return _ready(ctx, qual)

        # Move 3: expand from the local acts already verified (their local scene)
        if proven:
            outcome = _expand_scene(ctx, proven, base_rank, base_aff)
            if outcome:
                return outcome
            proven, qual = _proven(bid), _qualified(bid)
            if len(qual) >= rules.MIN_PROVEN:
                return _ready(ctx, qual)

        # Move 4 (city level only): ask the LLM for local names, then treat them like any other lead
        if level == 0 and config.LLM_LOCAL_N > 0:
            outcome = _llm_local(ctx)
            if outcome:
                return outcome
            proven, qual = _proven(bid), _qualified(bid)
            if len(qual) >= rules.MIN_PROVEN:
                return _ready(ctx, qual)

        if level < 2 and levels[level + 1] is not None:
            _event(bid, run_id, "decision",
                   f"{_need(qual, proven)} after checking Qloo's list"
                   + (" and the local scene" if proven else "")
                   + f" for {scope['label']}, so the search widens to {levels[level + 1]['label']}")

    # ── searched every scope ─────────────────────────────────────────────
    live = _live(bid)
    proven = [c for c in live if c["local_proof"]]
    qual = [c for c in live if c["qualified"]]
    if not live:
        return _fail(bid, run_id, "Qloo returned no usable related artists at any scope.", final_level)
    reason = (f"Searched up to {levels[final_level]['label'] if levels[final_level] else 'city'} scope; "
              f"{len(qual)} of {len(live)} candidates pass the booking gate"
              + (f" ({_split(qual)})" if qual else "") + f"; {len(proven)} have a verified local connection. "
              + ("Picks are filled only with those; the rest are leads." if qual else
                 "Treat the list as research, not a recommendation."))
    _event(bid, run_id, "decision", reason)
    _usage_event(bid, run_id, provider)
    db.set_status(bid, "ready_low_evidence", reason)
    db.finish_run(run_id, "ready_low_evidence", reason, final_level, len(live), len(proven))


def _live(bid: str) -> list[dict]:
    return [c for c in db.get_candidates(bid, with_evidence=False) if c["status"] != "vetoed" and not c["excluded_reason"]]


def _proven(bid: str) -> list[dict]:
    """Verified local acts (used as scene seeds)."""
    return [c for c in _live(bid) if c["local_proof"]]


def _qualified(bid: str) -> list[dict]:
    """Verified local AND a Qloo taste-fit signal: what the gate counts and what picks are filled with."""
    return [c for c in _live(bid) if c["qualified"]]


def _need(qual: list[dict], proven: list[dict]) -> str:
    weak = len(proven) - len(qual)
    return (f"{len(qual)} of the {rules.MIN_PROVEN} acts needed pass the booking gate (taste fit, local connection, "
            f"size, activity)" + (f"; {weak} more have a verified local connection but fail a check" if weak else ""))


def _known(bid: str, qloo_id: str, level: int) -> bool:
    """Already investigated at this or a wider level in this brief."""
    return any(c["qloo_id"] == qloo_id and c["found_level"] >= level for c in db.get_candidates(bid, with_evidence=False))


def _split(cands: list[dict]) -> str:
    q = sum(1 for c in cands if c["gate"]["status"] == "qualified")
    return f"{q} fully qualified, {len(cands) - q} provisional (no check failed, something still to confirm)"


def _ready(ctx: dict, proven: list[dict]):
    bid, run_id, scope = ctx["bid"], ctx["run_id"], ctx["scope"]
    live = _live(bid)
    _event(bid, run_id, "gate", f"{len(proven)} of {len(live)} live candidates are verified as local to the "
                                f"{scope['label']} area and pass the booking gate (need {rules.MIN_PROVEN})",
           {"proven": [c["artist_name"] for c in proven]})
    _usage_event(bid, run_id, ctx["provider"])
    db.set_status(bid, "ready", f"{len(proven)} candidates pass the booking gate (taste fit, local connection, size, "
                                f"activity) in the {scope['label']} area: {_split(proven)}")
    db.finish_run(run_id, "ready", final_level=ctx["level"], candidate_count=len(live), proven_count=len(proven))
    return True


def _expand_scene(ctx: dict, proven: list[dict], base_rank: dict, base_aff: dict):
    """Ask Qloo what fans of the verified local acts like in this area, keep artists whose Qloo
    taste tags overlap most with the headliner's, and investigate those."""
    bid, run_id, qloo, scope, level = ctx["bid"], ctx["run_id"], ctx["qloo"], ctx["scope"], ctx["level"]
    seeds = sorted(proven, key=lambda c: (c["local_score"] or 0, c["taste_score"] or 0), reverse=True)[:config.SCENE_SEEDS]
    names = ", ".join(c["artist_name"] for c in seeds)
    try:
        res = qloo.related(",".join(c["qloo_id"] for c in seeds), scope["query"], take=config.QLOO_POOL,
                           popularity_max=ctx["cap"])
    except QlooError as e:
        _event(bid, run_id, "warning", f"Qloo scene search failed ({e}); continuing")
        return None
    known = {c["qloo_id"] for c in db.get_candidates(bid, with_evidence=False)} | {ctx["seed"]}
    pool = [e for e in res["entities"] if e["id"] not in known]
    if not pool:
        return None
    try:
        profiles = qloo.entity_profiles([e["id"] for e in pool])
    except QlooError:
        profiles = {}
    for e in pool:
        e["_tag"] = rules.tag_overlap(ctx["head_tags"], (profiles.get(e["id"]) or {}).get("tags") or [])[0]
    pool.sort(key=lambda e: (e["_tag"] is not None, e["_tag"] or 0), reverse=True)
    batch = pool[:config.SCENE_N]
    _event(bid, run_id, "decision",
           f"Expanding from the verified local act{'s' if len(seeds) > 1 else ''} {names}: Qloo found {len(pool)} new "
           f"artists their fans like in {scope['query']}; checking the {len(batch)} whose Qloo taste tags overlap "
           f"most with {ctx['brief']['headliner_resolved_name']}", {"names": [e["name"] for e in batch]})
    cands = _merge(batch, {}, {}, level, scope, "scene", (None, None), scene_seed=names)
    return _process_batch(ctx, cands, profiles=profiles)


def _llm_local(ctx: dict):
    """The language model is good at naming local acts and bad at knowing whether they are still local,
    still active or the right size. So its names are only leads: each must resolve to exactly one Qloo
    artist, pass the size filter and get the same source checks as Qloo's suggestions."""
    bid, run_id, brief, qloo, scope = ctx["bid"], ctx["run_id"], ctx["brief"], ctx["qloo"], ctx["scope"]
    if not config.OPENAI_API_KEY and not brief["demo_mode"]:
        return None
    budget = getattr(ctx["provider"], "budget", None)
    n = config.LLM_LOCAL_N
    if budget is not None and budget.limit is not None and getattr(ctx["provider"], "name", "") == "tavily":
        n = min(n, max(0, (budget.limit - budget.live) // 3))  # ~3 searches per artist; never start what cannot finish
    if n <= 0:
        return None
    try:
        style = [t["name"] for t in ctx.get("head_tags") or []][:10]
        names = llm.suggest_local_acts(brief["headliner_resolved_name"] or brief["headliner_name"], brief["city"],
                                       brief["venue_capacity"], n=config.LLM_LOCAL_N, demo=bool(brief["demo_mode"]),
                                       style=style)[:n]
    except llm.LLMUnavailable as e:
        _event(bid, run_id, "warning", f"Could not ask the language model for local names ({e}); continuing")
        return None
    if not names:
        return None
    known = {c["qloo_id"] for c in db.get_candidates(bid, with_evidence=False)} | {ctx["seed"]}
    found, skipped = [], []
    for nm in names:
        try:
            hits = qloo.search_artists(nm, take=5)
        except QlooError:
            hits = []
        exact = [h for h in hits if norm(h["name"]) == norm(nm)]
        if len(exact) != 1:
            skipped.append(f"{nm} ({'not in Qloo' if not exact else 'several Qloo matches'})")
            continue
        e = exact[0]
        if e["id"] in known:
            continue
        if ctx["cap"] is not None and e["popularity"] is not None and e["popularity"] > ctx["cap"]:
            skipped.append(f"{nm} (too big for the room)")
            continue
        known.add(e["id"])
        found.append({**e, "rank": None, "affinity": None})
    _event(bid, run_id, "decision",
           f"Asked the language model for acts based in {brief['city']} now in the headliner's style "
           f"(Qloo tags: {', '.join(t['name'] for t in (ctx.get('head_tags') or [])[:5]) or 'none'}). It named {len(names)}; "
           f"{len(found)} resolve to one Qloo artist within the size filter and get the same source checks"
           + (f". Skipped: {', '.join(skipped[:6])}" if skipped else ""),
           {"names": names, "kept": [e["name"] for e in found], "skipped": skipped})
    if not found:
        return None
    cands = _merge(found, {}, {}, ctx["level"], scope, "llm_local", (None, None))
    return _process_batch(ctx, cands)


def _process_batch(ctx: dict, cands: list[dict], profiles: Optional[dict] = None):
    """Identity re-check and taste tags (Qloo /entities), evidence, scoring, storage.
    Returns True if the run ended here (budget), else None."""
    bid, run_id, brief, scope, level = ctx["bid"], ctx["run_id"], ctx["brief"], ctx["scope"], ctx["level"]
    provider = ctx["provider"]
    if profiles is None:
        try:
            profiles = ctx["qloo"].entity_profiles([c["qloo_id"] for c in cands])
        except QlooError as e:
            _event(bid, run_id, "warning", f"Qloo /entities failed ({e}); identities not re-checked this batch")
            profiles = {}
    mismatched = []
    for c in cands:
        prof = profiles.get(c["qloo_id"])
        c["entity_checked"] = bool(prof)
        if prof and prof.get("name") and norm(prof["name"]) != norm(c["artist_name"]):
            c["excluded_reason"] = f"Qloo identity check failed: the ID belongs to '{prof['name']}'"
            mismatched.append(c["artist_name"])
        c["tag_overlap"], c["shared_tags"] = rules.tag_overlap(ctx["head_tags"], (prof or {}).get("tags") or [])
    if profiles:
        _event(bid, run_id, "step", f"Re-checked {sum(c['entity_checked'] for c in cands)} of {len(cands)} artists "
                                    f"with Qloo /entities and compared their taste tags with the headliner's"
               + (f"; identity mismatch: {', '.join(mismatched)}" if mismatched else ""))

    db.set_status(bid, "investigating")
    vetoed = {c["qloo_id"] for c in db.get_candidates(bid, with_evidence=False) if c["status"] == "vetoed"}
    to_check = [c for c in cands if c["qloo_id"] not in vetoed and not c.get("excluded_reason")]
    _event(bid, run_id, "step", f"Checking sources for a {scope['label']}-level connection ({len(to_check)} artists)")
    errors = _investigate(to_check, provider, scope, ctx["mb"])
    unchecked = [c for c in to_check if c.pop("unchecked", False)]
    for c in unchecked:
        c.pop("evidence", None)  # keep any evidence stored from an earlier run
    if errors:
        _event(bid, run_id, "warning", f"Evidence search failed for {errors} artist(s); their local "
                                       f"connection is marked unknown")
    rows = [e for c in to_check for e in c.get("evidence", [])]
    count = lambda k: sum(1 for e in rows if e["kind"] == k and e["status"] == "verified")
    _event(bid, run_id, "result",
           f"Checked {len(rows)} sources: {count('based_here')} verified 'based here now', {count('formed_here')} "
           f"verified 'from / formed here', {count('played_here')} verified 'played here', "
           f"{sum(1 for e in rows if e['status'] == 'unverified' and e['kind'] != 'context')} unverified, "
           f"{sum(1 for e in rows if e['status'] == 'rejected')} rejected",
           {"rejected": [{"artist": c["artist_name"], "url": e["source_url"], "reason": e["reason"]}
                         for c in to_check for e in c.get("evidence", []) if e["status"] == "rejected"][:30]})
    for c in cands:
        if c["qloo_id"] in vetoed:
            c.pop("evidence", None)
        _score(c, brief)
    db.upsert_candidates(bid, run_id, cands)
    if ctx["cap"] is not None:
        _apply_cap_to_existing(bid, ctx["cap"])
    if unchecked:
        live = _live(bid)
        proven = [c for c in live if c["qualified"]]
        reason = (f"Stopped early: the search budget of {provider.budget.limit} paid searches per run was reached. "
                  f"{len(unchecked)} artists were not checked ({', '.join(c['artist_name'] for c in unchecked[:5])}"
                  f"{'…' if len(unchecked) > 5 else ''}). {len(proven)} of {len(live)} candidates are verified local "
                  f"with a Qloo taste fit. Completed searches are cached, so 'Run again' continues cheaply.")
        _event(bid, run_id, "budget", reason, {"unchecked": [c["artist_name"] for c in unchecked]})
        _usage_event(bid, run_id, provider)
        status = "ready" if len(proven) >= rules.MIN_PROVEN else "ready_low_evidence"
        db.set_status(bid, status, reason)
        db.finish_run(run_id, status, reason, level, len(live), len(proven))
        return True
    return None


def _merge(found: list[dict], base_rank: dict, base_aff: dict, level: int, scope: dict, found_via: str = "qloo",
           aff_range: tuple = (None, None), scene_seed: Optional[str] = None) -> list[dict]:
    """Candidate rows for one batch. Affinity is only comparable within the headliner's own list,
    so artists found through a local act ('scene') carry no headliner affinity or baseline rank."""
    outside = found_via in ("scene", "llm_local")  # not ranked in the headliner's own Qloo list
    lo, hi = aff_range if not outside else (None, None)
    out = []
    for e in found:
        in_base = e["id"] in base_rank
        out.append({
            "qloo_id": e["id"], "artist_name": e["name"], "found_level": level, "found_scope": scope["query"],
            "found_via": found_via, "scene_seed": scene_seed,
            "city_rank": e["rank"], "city_affinity": e["affinity"] if not outside else None,
            "popularity": e["popularity"],
            "monthly_listeners": e["monthly_listeners"], "listeners_flag": e["listeners_flag"],
            "place_of_birth": e["place_of_birth"], "genres": e["genres"], "image_url": e["image_url"],
            "noloc_rank": base_rank.get(e["id"]), "noloc_affinity": base_aff.get(e["id"]),
            "rank_delta": (base_rank[e["id"]] - e["rank"]) if in_base else None,
            "new_in_city": (not in_base) if not outside else False, "excluded_reason": None,
            "_aff_range": (lo, hi),
        })
    return out


def _investigate(cands: list[dict], provider, scope: dict, mb=None) -> int:
    """Fills c["evidence"]. Returns the number of search failures. Artists skipped because the
    search budget ran out get c["unchecked"] = True and keep whatever evidence they had before."""
    def one(c):
        ev = qloo_birthplace_evidence(c["artist_name"], c["place_of_birth"], scope["place_terms"], scope["label"])
        if mb is not None:
            ev += mb.evidence(c["artist_name"], scope["place_terms"], scope["label"], c.get("genres"))
        err = 0
        if getattr(provider, "available", True):
            try:
                ev += _check(provider, c["artist_name"], scope, c.get("genres"), c.get("place_of_birth"), ev)
            except BudgetExceeded:
                c["unchecked"] = True
                return 0
            except EvidenceError:
                err = 1
        c["evidence"] = ev
        return err

    if not cands:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, config.EVIDENCE_WORKERS)) as pool:
        return sum(pool.map(one, cands))


def _check(provider, name: str, scope: dict, genres, place_of_birth, prior: list[dict]) -> list[dict]:
    """Evidence search plus, when the artist looks local, the current-base check (if the provider has one)."""
    ctx = {"genres": genres, "place_of_birth": place_of_birth, "prior": prior}
    if hasattr(provider, "check"):
        return provider.check(name, scope["place_terms"], scope["label"], context=ctx,
                              wide_terms=scope.get("wide_terms") or scope["place_terms"])
    return provider.search(name, scope["place_terms"], scope["label"], context=ctx)


def _usage_event(bid: str, run_id: str, provider) -> None:
    b = getattr(provider, "budget", None)
    if b is None or getattr(provider, "name", "") != "tavily":
        return
    u = b.summary()
    _event(bid, run_id, "info", f"Web searches: {u['live_searches']} paid, {u['cached_searches']} from the cache "
                                f"(budget {u['limit']} per run)", u)


def _score(c: dict, brief: dict) -> None:
    lo, hi = c.pop("_aff_range", (None, None))
    aff = (rules.taste_score(c["city_affinity"], lo, hi, c["rank_delta"], c["new_in_city"])
           if c.get("city_affinity") is not None else None)
    c["taste_score"] = rules.taste_fit(aff, c.get("tag_overlap"))
    status = rules.local_status(c.get("evidence", []))
    c["local_score"], c["local_status"], c["local_note"] = status["score"], status["status"], status["note"]
    gone = rules.inactive_note(c.get("evidence", []))
    if gone and not c.get("excluded_reason"):
        c["excluded_reason"] = gone
    c["practical_score"], c["practical_note"] = rules.practical_score(c["monthly_listeners"], brief["venue_capacity"])
    c["total_score"] = rules.total(c, brief["weights"])


def _apply_cap_to_existing(bid: str, cap: float) -> None:
    with db.tx() as c:
        c.execute("UPDATE candidates SET excluded_reason=?, slot=NULL WHERE brief_id=? AND popularity > ?",
                  (f"Above the smaller-acts preference (Qloo popularity > {cap})", bid, cap))


def _clear_cap(bid: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE candidates SET excluded_reason=NULL WHERE brief_id=? AND excluded_reason LIKE "
                  "'Above the smaller-acts preference%'", (bid,))


# ── finalize ──────────────────────────────────────────────────────────────
def finalize(bid: str) -> dict:
    brief = db.require_brief(bid)
    if brief["status"] not in ("ready", "ready_low_evidence", "finalized"):
        raise db.InvalidTransition(f"Cannot create a booking brief while the brief is '{brief['status']}'")
    cands = db.get_candidates(bid)
    live = [c for c in cands if c["status"] != "vetoed" and not c["excluded_reason"]]
    if not live:
        raise db.InvalidTransition("Every candidate is vetoed or filtered out; nothing to recommend")

    proven = [c for c in live if c["local_proof"]]
    qual = [c for c in live if c["qualified"]]
    head = brief["headliner_resolved_name"] or brief["headliner_name"]
    kind = ("recommendation" if len(qual) >= rules.MIN_PROVEN
            else "partial_recommendation" if qual else "research_shortlist")
    research_only = kind == "research_shortlist"
    picks: dict[str, Optional[dict]] = {s: next((c for c in live if c["slot"] == s), None) for s in db.SLOTS}
    taken = {c["id"] for c in picks.values() if c}
    auto = set()
    # Only QUALIFIED acts (verified local connection AND a Qloo taste-fit signal) are filled in automatically.
    # The buyer can still choose anyone; that choice is labelled as theirs.
    # fully qualified acts first, then provisional ones (no failed check, something still to confirm)
    remaining = sorted((c for c in qual if c["id"] not in taken),
                       key=lambda c: (c["gate"]["status"] == "qualified", c["total_score"] or 0), reverse=True)
    for s in db.SLOTS:
        if picks[s] is None and remaining:
            picks[s] = remaining.pop(0)
            auto.add(s)

    # trade-offs the buyer has to decide, shown instead of being silently resolved
    gate_fail = lambda c, k: c["gate"]["checks"][k]["state"] == "fail"
    local_weak_fit = sorted((c for c in proven if gate_fail(c, "fit") and c["id"] not in taken),
                            key=lambda c: c["local_score"] or 0, reverse=True)[:4]
    fit_not_local = sorted((c for c in live if c["fit_ok"] and not c["local_proof"] and c["id"] not in taken),
                           key=lambda c: (c["plays_market"], c["taste_score"] or 0), reverse=True)[:4]
    blocked = sorted((c for c in proven if c["fit_ok"] and not c["qualified"] and c["id"] not in taken),
                     key=lambda c: c["total_score"] or 0, reverse=True)[:4]

    warnings = []
    if kind == "research_shortlist":
        warnings.append(f"Research shortlist, not a booking recommendation: no act passed all four checks (taste fit, "
                        f"local connection, size, activity); {len(proven)} have a verified local connection. No picks "
                        f"were filled in automatically" + ("; the picks shown were chosen by the buyer." if taken else "."))
    elif kind == "partial_recommendation":
        warnings.append(f"Partial recommendation: {len(qual)} of the {rules.MIN_PROVEN} acts needed passed the booking "
                        f"gate. The empty slots are left for the buyer; see the trade-offs.")
    prov = [p for p in picks.values() if p and p["qualified"] and p["gate"]["status"] == "provisional"]
    if prov:
        warnings.append("Provisional: " + "; ".join(f"{p['artist_name']} (confirm {', '.join(p['gate']['to_confirm'])})"
                                                    for p in prov)
                        + ". No check failed, but these are not confirmed booking picks until you verify them.")
    if not brief["use_popularity_cap"]:
        warnings.append(f"Exploration mode: the size filter is off, so bigger artists are listed. Automatic picks "
                        f"still had to pass the size check for a {brief['venue_capacity']}-capacity room.")
    for s_, p in picks.items():
        if not p:
            continue
        for k, chk in p["gate"]["checks"].items():
            if chk["state"] == "fail":
                warnings.append(f"{p['artist_name']} ({s_}, chosen by the buyer) fails the {k} check: {chk['note']}.")
            elif chk["state"] == "caution":
                warnings.append(f"{p['artist_name']} ({s_}): {k}: {chk['note']}.")
    if brief["demo_mode"]:
        warnings.append("Built in demo mode from recorded Qloo responses.")

    research = [_snap_candidate(c, False) for c in sorted(live, key=lambda c: (c["qualified"], c["local_score"] or 0,
                                                                              c["total_score"] or 0), reverse=True)[:8]]
    narrative, narrative_source = llm.facts_narrative(brief, picks, kind, len(qual), len(proven),
                                                      local_weak_fit, fit_not_local, blocked), "template"
    next_actions = ["Confirm where each act is based now (sources are dated in the evidence)",
                    "Contact the act's representative and confirm availability on the show date",
                    "Agree the fee and get the headliner's agent to approve the support act"]
    snapshot = {
        "kind": kind,
        "decision": {"qualified": sum(1 for c in live if c["gate"]["status"] == "qualified"),
                     "provisional": sum(1 for c in live if c["gate"]["status"] == "provisional"),
                     "blocked": sum(1 for c in live if c["gate"]["status"] == "blocked"),
                     "picks_provisional": [p["artist_name"] for p in prov]},
        "research_candidates": research,
        "tradeoffs": {"local_but_weak_fit": [_snap_candidate(c, False) for c in local_weak_fit],
                      "fit_but_not_verified_local": [_snap_candidate(c, False) for c in fit_not_local],
                      "local_and_fit_but_blocked": [_snap_candidate(c, False) for c in blocked]},
        "policy": (f"Booking Decision Gate: an act is picked automatically only if none of four checks fails. Taste fit: "
                   f"Qloo relates it to the headliner's fans, or it shares at least {round(rules.FIT_MIN_TAGS * 100)}% "
                   f"of the headliner's Qloo taste tags. Local connection: a verified source says it is based in, or "
                   f"from, the area (older or undated sources are a caution). Size: Qloo popularity within the room's "
                   f"limit and Spotify listeners not far above it (unknown listeners are a caution). Activity: no source "
                   f"says it stopped (no recent dated source is a caution). The thresholds are documented heuristics, "
                   f"not validated cut-offs."),
        "next_actions": next_actions,
        "brief": {k: brief[k] for k in ("headliner_name", "headliner_resolved_name", "city", "venue_capacity",
                                        "show_date", "budget")},
        "scope_reached": (rules.scopes(brief["city"])[brief["widen_level"]] or {}).get("query"),
        "weights": brief["weights"],
        "picks": {s: (_snap_candidate(c, s in auto) if c else None) for s, c in picks.items()},
        "narrative": narrative,
        "narrative_source": narrative_source,
        "pitch_line": llm.write_pitch(brief, picks["primary"]) if picks["primary"] else None,
        "unknowns": ["Availability on the show date", "Fee", "Headliner agent approval of a local support act"],
        "warnings": warnings,
        "method": ("Candidates come from Qloo's related artists for the headliner with the show city as a location "
                   "signal, limited by a Qloo-popularity size filter for the room. When too few qualify, the agent "
                   "checks deeper in Qloo's list, asks Qloo what fans of the verified local acts like in the same area, "
                   "and asks the language model for local acts in the headliner's style; every name, whatever its "
                   "source, must resolve to one Qloo artist and passes the same checks. Local connection = a source "
                   "(public page, MusicBrainz or Qloo data) saying the act is based in the area or is from or formed "
                   "there; public sentences are quoted and checked against the page, and each claim is labelled by how "
                   "current it is. A neutral 'where is X based' search looks for contrary sources; a source at least as "
                   "new placing the act elsewhere marks it as moved, and one saying it stopped performing removes it. "
                   "Having played the city is shown separately and is not local proof. Taste = Qloo affinity to the "
                   "headliner combined with the share of Qloo taste tags both artists have. Size = Spotify monthly "
                   "listeners vs venue capacity, a rule of thumb. The ranking is a decision aid, not a ticket-sales "
                   "prediction."),
        "rules_version": RULES_VERSION,
        "qloo_calls": len(db.list_qloo_calls(bid)),
        "demo_mode": bool(brief["demo_mode"]),
    }
    return db.save_booking_brief(bid, snapshot)


def _snap_candidate(c: dict, auto: bool) -> dict:
    keep = ("artist_name", "qloo_id", "city_rank", "noloc_rank", "rank_delta", "new_in_city", "found_scope",
            "monthly_listeners", "genres", "image_url", "taste_score", "local_score", "practical_score",
            "practical_note", "total_score", "local_proof", "plays_market", "evidence", "found_via", "scene_seed",
            "shared_tags", "tag_overlap", "entity_checked", "local_status", "local_note", "fit_ok", "fit_note", "qualified",
            "gate", "popularity")
    return {**{k: c.get(k) for k in keep}, "auto_selected": auto}


# ── comparisons ───────────────────────────────────────────────────────────
def compare_llm(bid: str, qloo: Optional[BaseQloo] = None, evidence=None, n: int = COMPARE_N) -> dict:
    """Four ways to build a shortlist for the same show, all judged by the same rules:

      llm_only       an LLM with no tools
      qloo_no_city   Qloo related artists without the location signal
      qloo_city      Qloo related artists with the show city as location signal
      opening_act    the agent's shortlist (size filter, verified evidence, buyer edits)

    Every artist in every arm goes through the same evidence search and verification at the
    same scope, and the same size rule. The metrics count artists, so they are comparable."""
    brief = db.require_brief(bid)
    if brief["status"] not in ("ready", "ready_low_evidence", "finalized"):
        raise db.InvalidTransition("Run the agent before comparing")
    demo = bool(brief["demo_mode"])
    qloo = qloo or get_client(demo, db.make_qloo_recorder(bid, None))
    provider = evidence or get_provider(demo)
    mb = get_musicbrainz(demo) if evidence is None else None
    levels = rules.scopes(brief["city"])
    scope = levels[brief["widen_level"]] or levels[0]
    wide = levels[2] or levels[1] or scope
    scope = {**scope, "wide_terms": list(dict.fromkeys(scope["place_terms"] + wide["place_terms"]))}
    cap = rules.popularity_cap(brief["venue_capacity"])
    seed = brief["headliner_qloo_id"]

    names, raw = llm.suggest_llm_only(brief["headliner_name"], brief["city"], brief["venue_capacity"], n=n)
    if not names:
        raise llm.LLMUnavailable("The LLM returned no parsable artist names")

    def lookup(name):
        try:
            hits = qloo.search_artists(name, take=5)
        except QlooError:
            return None, None
        exact = next((h for h in hits if norm(h["name"]) == norm(name)), None)
        return exact, bool(exact)

    arms: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=max(1, config.EVIDENCE_WORKERS)) as pool:
        found = list(pool.map(lookup, names))
    arms["llm_only"] = [{"artist_name": nm, "in_qloo": ok, "popularity": (e or {}).get("popularity"),
                         "genres": (e or {}).get("genres"), "place_of_birth": (e or {}).get("place_of_birth"),
                         "qloo_id": (e or {}).get("id"), "related_to_headliner": False}
                        for nm, (e, ok) in zip(names, found)]
    for arm, loc in (("qloo_no_city", None), ("qloo_city", levels[0]["query"])):
        try:
            ents = qloo.related(seed, loc, take=n)["entities"][:n]
        except QlooError:
            ents = []
        arms[arm] = [{"artist_name": e["name"], "in_qloo": True, "popularity": e["popularity"], "genres": e["genres"],
                      "place_of_birth": e["place_of_birth"], "qloo_id": e["id"], "related_to_headliner": True}
                     for e in ents]
    live = [c for c in db.get_candidates(bid, with_evidence=False) if c["status"] != "vetoed" and not c["excluded_reason"]]
    arms["opening_act"] = [{"artist_name": c["artist_name"], "in_qloo": True, "popularity": c["popularity"],
                            "genres": c["genres"], "place_of_birth": c["place_of_birth"], "qloo_id": c["qloo_id"],
                            "related_to_headliner": c.get("city_affinity") is not None} for c in live[:n]]

    # the same taste-fit rule for every list: Qloo relates the act to the headliner's fans, or it shares
    # enough of the headliner's Qloo taste tags (read from /entities for every artist in every list)
    head_tags = brief.get("headliner_tags") or []
    ids = [r["qloo_id"] for rows in arms.values() for r in rows if r.get("qloo_id")]
    try:
        profiles = qloo.entity_profiles(ids) if head_tags and ids else {}
    except QlooError:
        profiles = {}
    related = {norm(r["artist_name"]) for rows in arms.values() for r in rows if r["related_to_headliner"]}
    for rows in arms.values():
        for r in rows:
            r["related_to_headliner"] = norm(r["artist_name"]) in related  # same artist, same verdict in every list
            ov = rules.tag_overlap(head_tags, (profiles.get(r.get("qloo_id")) or {}).get("tags") or [])[0] \
                if r.get("qloo_id") in profiles else None
            r["tag_overlap"] = ov
            r["fit_ok"] = rules.fit_evidence({"city_affinity": 1 if r["related_to_headliner"] else None,
                                              "tag_overlap": ov})[0]

    # one check per distinct artist, shared by every arm that lists it
    unique = {norm(r["artist_name"]): r for rows in arms.values() for r in rows}

    def check(row):
        ev = qloo_birthplace_evidence(row["artist_name"], row.get("place_of_birth"), scope["place_terms"], scope["label"])
        if mb is not None:
            ev += mb.evidence(row["artist_name"], scope["place_terms"], scope["label"], row.get("genres"))
        err = False
        if getattr(provider, "available", True):
            try:
                ev += _check(provider, row["artist_name"], scope, row.get("genres"), row.get("place_of_birth"), ev)
            except BudgetExceeded:
                err = "unchecked"
            except EvidenceError:
                err = True
        return norm(row["artist_name"]), ev, err

    with ThreadPoolExecutor(max_workers=max(1, config.EVIDENCE_WORKERS)) as pool:
        checked = {k: (ev, err) for k, ev, err in pool.map(check, unique.values())}

    oa_names = {norm(r["artist_name"]) for r in arms["opening_act"]}
    out_arms, summary = {}, {}
    for arm, rows in arms.items():
        enriched = []
        for r in rows:
            ev, err = checked[norm(r["artist_name"])]
            pop = r.get("popularity")
            enriched.append({**{k: r[k] for k in ("artist_name", "in_qloo", "popularity", "tag_overlap", "fit_ok",
                                                  "related_to_headliner")},
                             "verified_local": rules.local_score(ev) >= rules.LOCAL_PROOF_THRESHOLD,
                             "local_status": rules.local_status(ev)["status"],
                             "possibly_inactive": bool(rules.inactive_note(ev)),
                             "plays_market": rules.plays_market(ev),
                             "too_big": bool(cap is not None and pop is not None and pop > cap),
                             "rejected_sources": sum(1 for e in ev if e["status"] == "rejected"),
                             "evidence_error": err is True,
                             "unchecked": err == "unchecked",
                             "evidence": [e for e in ev if e["status"] != "rejected"][:3],
                             "also_in_opening_act": norm(r["artist_name"]) in oa_names})
        out_arms[arm] = enriched
        summary[arm] = {
            "n": len(enriched),
            "verified_local": sum(r["verified_local"] for r in enriched),
            "played_city_only": sum(r["plays_market"] and not r["verified_local"] for r in enriched),
            "too_big_for_room": sum(r["too_big"] for r in enriched),
            "not_found_in_qloo": sum(1 for r in enriched if r["in_qloo"] is False),
            "verified_local_and_right_size": sum(r["verified_local"] and not r["too_big"] for r in enriched),
            # the product's promise in one number: local, within the size limit AND a Qloo taste fit
            "qualified": sum(r["verified_local"] and not r["too_big"] and r["fit_ok"] for r in enriched),
            "shared_with_opening_act": sum(r["also_in_opening_act"] for r in enriched) if arm != "opening_act" else None,
            "not_checked_budget": sum(r["unchecked"] for r in enriched),
        }
    result = {
        "city": brief["city"], "headliner": brief["headliner_name"], "scope": scope["query"], "popularity_cap": cap,
        "arms": out_arms, "summary": summary,
        "search_usage": getattr(getattr(provider, "budget", None), "summary", lambda: None)(),
        "method": (f"Up to {n} artists per list. Every artist is checked the same way: public sources and Qloo data "
                   f"or MusicBrainz must say the artist is from, formed in or based in the {scope['label']} area ({scope['query']}), with the "
                   f"supporting sentence quoted and checked against the page. 'Too big' means Qloo popularity above "
                   f"{cap} for a {brief['venue_capacity']}-capacity room. 'Not found in Qloo' means no exact name "
                   f"match in Qloo's catalog. 'Taste fit' means Qloo relates the artist to the headliner's fans or it "
                   f"shares at least {round(rules.FIT_MIN_TAGS * 100)}% of the headliner's Qloo taste tags (read from "
                   f"/entities for every list). 'Qualified' = verified local, within the size limit and a taste fit. "
                   f"The LLM's list is cached, so re-running the comparison uses the same list. The Opening Act list "
                   f"is the agent's own shortlist after the size filter and the buyer's vetoes."),
    }
    db.save_comparison(bid, raw, result)
    return result


def create_city_comparison(bid: str, city: str) -> str:
    """A sibling brief: same headliner, venue and preferences, different city."""
    brief = db.require_brief(bid)
    if not brief["headliner_qloo_id"]:
        raise db.InvalidTransition("Confirm the headliner before comparing cities")

    class _In:  # minimal input object for insert_brief
        headliner = brief["headliner_name"]
        venue_capacity = brief["venue_capacity"]
        show_date = brief["show_date"]
        budget = brief["budget"]
        use_popularity_cap = bool(brief["use_popularity_cap"])
    _In.city = city
    return db.insert_brief(_In, bool(brief["demo_mode"]), headliner_qloo_id=brief["headliner_qloo_id"],
                           headliner_resolved_name=brief["headliner_resolved_name"], compare_parent_id=bid,
                           weights=brief["weights"])
