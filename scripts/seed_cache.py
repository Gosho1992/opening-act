"""Copy searches and LLM verdicts recorded by `live_smoke_test --record` into the persistent cache,
so the next live run reuses them instead of paying again.

    python -m scripts.seed_cache

Entries keep the date they were recorded (the fixture file's modification time) and expire
after CACHE_TTL_DAYS like any other cache entry.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import cache, config  # noqa: E402
from backend.evidence import VERIFIER_VERSION, TavilyEvidence  # noqa: E402


def main():
    path = config.FIXTURE_DIR / "evidence_replay.json"
    fx = json.loads(path.read_text(encoding="utf-8"))
    when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
    n_s = n_v = 0
    for q, hits in fx.get("searches", {}).items():
        if cache.get("tavily", q, TavilyEvidence.SEARCH_PARAMS) is None:
            cache.put("tavily", q, hits, TavilyEvidence.SEARCH_PARAMS, fetched_at=when)
            n_s += 1
    vparams = {"model": config.OPENAI_MODEL, "version": VERIFIER_VERSION}
    for k, v in fx.get("verdicts", {}).items():
        if cache.get("verdict", k, vparams) is None:
            cache.put("verdict", k, v, vparams, fetched_at=when)
            n_v += 1
    print(f"Added {n_s} searches and {n_v} verdicts recorded {when[:10]} to {config.SEARCH_CACHE_PATH}")
    print("Cache now:", cache.stats())


if __name__ == "__main__":
    main()
