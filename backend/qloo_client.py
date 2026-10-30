"""Qloo API client.

Endpoints and response shapes follow real responses from the hackathon API
(recorded 2026-10-15):

  GET /search?query=...&types=urn:entity:artist&take=N
      -> {"results": [{"entity_id", "name", "types", ...}, ...]}
  GET /v2/insights?filter.type=urn:entity:artist
                  &signal.interests.entities=<seed id>
                  [&signal.location.query=<place>]&take=N
      -> {"results": {"entities": [{"entity_id", "name", "popularity",
                                     "query": {"affinity"}, "external": {...},
                                     "properties": {...}}, ...]}}

  GET /entities?entity_ids=<id>,<id>,...
      -> {"results": [{"entity_id", "name", "popularity", "tags": [{"name", "type", ...}], ...}]}
      Used to re-check every candidate's identity and to read its taste tags (genre, Qloo
      music style, Qloo audience traits) for the taste-overlap score.

`filter.location.query` is NOT used: in our tests it had no effect on results.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from typing import Any, Callable, Optional

import httpx

from . import config

ARTIST = "urn:entity:artist"

# A Spotify listener count this low for an artist Qloo ranks as popular is
# almost certainly a mismatched Spotify profile (seen in real data: Beach House 30).
_IMPLAUSIBLE_LISTENERS = 1000

Recorder = Callable[..., None]


class QlooError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def norm(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _results_list(data: Any) -> list[dict]:
    if not isinstance(data, dict):
        return []
    res = data.get("results")
    if isinstance(res, dict):
        res = res.get("entities")
    if not isinstance(res, list):
        res = data.get("entities") if isinstance(data.get("entities"), list) else []
    return [r for r in res if isinstance(r, dict)]


def parse_entity(raw: dict, rank: int) -> Optional[dict]:
    """Turn one raw Qloo entity into the fields Opening Act uses. None if unusable."""
    eid = raw.get("entity_id") or raw.get("id")
    name = raw.get("name")
    if not eid or not name:
        return None
    query = raw.get("query") if isinstance(raw.get("query"), dict) else {}
    external = raw.get("external") if isinstance(raw.get("external"), dict) else {}
    spotify = external.get("spotify")
    spotify = spotify[0] if isinstance(spotify, list) and spotify and isinstance(spotify[0], dict) else {}
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}

    popularity = raw.get("popularity")
    popularity = float(popularity) if isinstance(popularity, (int, float)) else None
    affinity = query.get("affinity")
    affinity = float(affinity) if isinstance(affinity, (int, float)) else None

    listeners = spotify.get("monthly_listeners")
    listeners_flag = None
    if not isinstance(listeners, (int, float)):
        listeners = None
    elif listeners < _IMPLAUSIBLE_LISTENERS and (popularity or 0) > 0.9:
        listeners, listeners_flag = None, "implausible"

    genres = props.get("primary_genres") if isinstance(props.get("primary_genres"), list) else []
    image = props.get("image") if isinstance(props.get("image"), dict) else {}
    return {
        "rank": rank,
        "id": str(eid),
        "name": str(name),
        "affinity": affinity,
        "popularity": popularity,
        "monthly_listeners": int(listeners) if listeners is not None else None,
        "listeners_flag": listeners_flag,
        "place_of_birth": props.get("place_of_birth") if isinstance(props.get("place_of_birth"), str) else None,
        "genres": [g for g in genres if isinstance(g, str)][:3],
        "image_url": image.get("url") if isinstance(image.get("url"), str) else None,
    }


TASTE_TAG_PREFIXES = ("urn:tag:genre", "urn:tag:music", "urn:tag:audience", "urn:tag:style")


def parse_tags(raw: dict) -> list[dict]:
    """Taste tags of one /entities result: [{"id", "name", "type"}], de-duplicated by id."""
    out, seen = [], set()
    for t in raw.get("tags") or []:
        if not isinstance(t, dict) or not isinstance(t.get("name"), str):
            continue
        ttype = str(t.get("type") or t.get("subtype") or "")
        if not ttype.startswith(TASTE_TAG_PREFIXES):
            continue
        tid = str(t.get("tag_id") or t.get("id") or f"{ttype}:{norm(t['name'])}")
        if tid not in seen:
            seen.add(tid)
            out.append({"id": tid, "name": t["name"].strip(), "type": ttype})
    return out


def _types_of(raw: dict) -> list[str]:
    t = raw.get("types") or raw.get("subtype") or raw.get("type") or []
    return t if isinstance(t, list) else [t]


class BaseQloo:
    """Shared logic: resolution rules and related-artist parsing."""

    def __init__(self, recorder: Optional[Recorder] = None):
        self.recorder = recorder

    # subclasses implement _get(purpose, path, params) -> dict
    def _get(self, purpose: str, path: str, params: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def search_artists(self, name: str, take: int = 10) -> list[dict]:
        data = self._get("resolve", "/search", {"query": name, "types": ARTIST, "take": take})
        out = []
        for i, raw in enumerate(_results_list(data), start=1):
            types = _types_of(raw)
            if types and ARTIST not in types:
                continue
            ent = parse_entity(raw, i)
            if ent:
                out.append(ent)
        return out

    def entity_profiles(self, ids: list[str], batch: int = 20) -> dict[str, dict]:
        """{id: {"name", "popularity", "tags"}} from /entities. Unknown ids are simply absent,
        which is how a candidate whose Qloo identity cannot be re-confirmed shows up."""
        out: dict[str, dict] = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), batch):
            data = self._get("entities", "/entities", {"entity_ids": ",".join(ids[i:i + batch])})
            res = data.get("results") if isinstance(data, dict) else None
            for raw in res if isinstance(res, list) else []:
                if isinstance(raw, dict) and raw.get("entity_id"):
                    out[str(raw["entity_id"])] = {"name": raw.get("name"), "popularity": raw.get("popularity"),
                                                  "tags": parse_tags(raw)}
        return out

    def resolve_artist(self, name: str) -> dict:
        """Returns {"status": resolved|ambiguous|not_found, "match", "options"}.

        Only an exact (normalized) name match that is unique is auto-resolved.
        Anything else goes to the buyer to confirm, so a wrong seed artist cannot
        silently contaminate the shortlist.
        """
        cands = self.search_artists(name)
        options = [{"id": c["id"], "name": c["name"], "popularity": c["popularity"]} for c in cands[:6]]
        if not cands:
            return {"status": "not_found", "match": None, "options": []}
        exact = [c for c in cands if norm(c["name"]) == norm(name)]
        if len(exact) == 1:
            m = exact[0]
            return {"status": "resolved", "match": {"id": m["id"], "name": m["name"]},
                    "options": [o for o in options if o["id"] != m["id"]][:4]}
        if len(exact) > 1:
            options = [{"id": c["id"], "name": c["name"], "popularity": c["popularity"]} for c in exact]
        return {"status": "ambiguous", "match": None, "options": options}

    def related(self, seed_id: str, location: Optional[str] = None, take: int = 15,
                popularity_max: Optional[float] = None) -> dict:
        """City-weighted (or baseline) related artists.

        Returns {"entities": [...], "params": {...}, "popularity_filter": None|"api"|"client"}.
        """
        params: dict[str, Any] = {"filter.type": ARTIST, "signal.interests.entities": seed_id, "take": take}
        if location:
            params["signal.location.query"] = location
        purpose = "discover_location" if location else "discover_baseline"
        pop_mode = None
        if popularity_max is not None:
            try:
                data = self._get(purpose, "/v2/insights", {**params, "filter.popularity.max": popularity_max})
                pop_mode = "api"
                params["filter.popularity.max"] = popularity_max
            except QlooError as err:
                if err.status != 400:
                    raise
                data = self._get(purpose, "/v2/insights", params)
                pop_mode = "client"
        else:
            data = self._get(purpose, "/v2/insights", params)

        ents = []
        for raw in _results_list(data):
            ent = parse_entity(raw, len(ents) + 1)
            if ent and ent["id"] != seed_id:
                ents.append(ent)
        hidden = []
        if pop_mode is not None:  # also guards against the API ignoring the filter
            hidden = [e["name"] for e in ents if e["popularity"] is not None and e["popularity"] > popularity_max]
            ents = [e for e in ents if e["popularity"] is None or e["popularity"] <= popularity_max]
            for i, e in enumerate(ents, start=1):
                e["rank"] = i
        return {"entities": ents, "params": params, "popularity_filter": pop_mode, "hidden": hidden}


class QlooClient(BaseQloo):
    """Live client. Retries timeouts, 429 and 5xx; raises QlooError otherwise."""

    def __init__(self, recorder: Optional[Recorder] = None, transport: Optional[httpx.BaseTransport] = None,
                 base_url: Optional[str] = None, api_key: Optional[str] = None, retries: int = 2):
        super().__init__(recorder)
        self.base = (base_url or config.QLOO_BASE_URL).rstrip("/")
        self.key = api_key if api_key is not None else config.QLOO_API_KEY
        self.retries = retries
        self._http = httpx.Client(transport=transport, timeout=config.HTTP_TIMEOUT)

    def _get(self, purpose: str, path: str, params: dict) -> dict:
        if not self.key:
            raise QlooError("QLOO_API_KEY is not set")
        url = f"{self.base}{path}"
        last_err: Optional[QlooError] = None
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            status, text, err = None, "", None
            try:
                r = self._http.get(url, params=params, headers={"X-Api-Key": self.key, "Accept": "application/json"})
                status, text = r.status_code, r.text
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}: {e}"
            ms = int((time.monotonic() - started) * 1000)
            if self.recorder:
                self.recorder(purpose=purpose, url=url, params=params, body=text[:200_000],
                              status=status, duration_ms=ms, error=err)
            if status == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    raise QlooError("Qloo returned a response that is not JSON", status)
                if config.RECORD_FIXTURES:
                    _record_fixture(path, params, data)
                return data
            retryable = err is not None or status == 429 or (status is not None and status >= 500)
            last_err = QlooError(err or f"Qloo HTTP {status}: {text[:200]}", status)
            if not retryable or attempt == self.retries:
                break
            time.sleep(0.5 * (3 ** attempt))
        raise last_err  # type: ignore[misc]


_fixture_lock = threading.Lock()


def _load_replay() -> dict:
    path = config.FIXTURE_DIR / "qloo_replay.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"search": {}, "insights": {}}


def _insights_key(params: dict) -> str:
    key = f"{params.get('signal.interests.entities')}|{str(params.get('signal.location.query') or '').lower()}"
    if params.get("filter.popularity.max") is not None:
        key += f"|pop<={params['filter.popularity.max']}"
    return key


def _record_fixture(path: str, params: dict, data: dict) -> None:
    """Append a live response to the replay file (used to refresh demo data)."""
    with _fixture_lock:
        fx = _load_replay()
        if path == "/search":
            fx.setdefault("search", {})[str(params.get("query", "")).lower()] = _results_list(data)
        elif path == "/entities":
            for raw in (data.get("results") or []) if isinstance(data, dict) else []:
                if isinstance(raw, dict) and raw.get("entity_id"):
                    keep = {k: raw.get(k) for k in ("entity_id", "name", "popularity", "types", "tags")}
                    fx.setdefault("entities", {})[raw["entity_id"]] = keep
        elif path == "/v2/insights":
            key = _insights_key(params)
            old = fx.setdefault("insights", {}).get(key)
            new = _results_list(data)
            if not old or len(new) >= len(old):  # keep the largest recording of the same query
                fx["insights"][key] = new
        (config.FIXTURE_DIR / "qloo_replay.json").write_text(json.dumps(fx, indent=1), encoding="utf-8")


class ReplayQlooClient(BaseQloo):
    """Demo client: replays real recorded Qloo responses. Unknown queries return
    an empty result, so the agent fails or widens honestly instead of inventing data."""

    def __init__(self, recorder: Optional[Recorder] = None, fixtures: Optional[dict] = None):
        super().__init__(recorder)
        self.fx = fixtures if fixtures is not None else _load_replay()

    def _get(self, purpose: str, path: str, params: dict) -> dict:
        if "filter.popularity.max" in params:
            hit = self.fx.get("insights", {}).get(_insights_key(params))
            if hit is None:
                raise QlooError("Replay data has no popularity-filtered response for this query", 400)
            return {"results": {"entities": hit[: int(params.get("take") or len(hit))]}}
        if path == "/entities":
            known = self.fx.get("entities", {})
            data = {"results": [known[i] for i in str(params.get("entity_ids", "")).split(",") if i in known]}
        elif path == "/search":
            results = self.fx.get("search", {}).get(str(params.get("query", "")).strip().lower(), [])
            data = {"results": results}
        else:
            seed = params.get("signal.interests.entities")
            loc = str(params.get("signal.location.query") or "").strip().lower()
            ins = self.fx.get("insights", {})
            results = ins.get(f"{seed}|{loc}")
            if results is None and loc:
                # "Nashville" matches the recorded "nashville, tennessee"
                for key, val in ins.items():
                    k_seed, _, k_loc = key.partition("|")
                    if "|pop<=" in k_loc:
                        continue
                    if k_seed == seed and k_loc and (k_loc.startswith(loc + ",") or k_loc.split(",")[0] == loc):
                        results = val
                        break
            data = {"results": {"entities": results or []}}
        if self.recorder:
            self.recorder(purpose=purpose, url=f"replay:{path}", params=params, body=json.dumps(data)[:200_000],
                          status=200, duration_ms=0, error=None)
        return data


def get_client(demo_mode: bool, recorder: Optional[Recorder] = None) -> BaseQloo:
    return ReplayQlooClient(recorder) if demo_mode else QlooClient(recorder)
