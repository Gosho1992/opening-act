"""Runtime configuration.

Values are read from the environment (and from a .env file at the project root,
if present). Modules read settings through this module at call time, so tests can
override them with monkeypatch.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DEMO_MODE = _bool(os.getenv("DEMO_MODE"), default=False)

QLOO_API_KEY = os.getenv("QLOO_API_KEY", "")
QLOO_BASE_URL = os.getenv("QLOO_BASE_URL", "https://hackathon.api.qloo.com").rstrip("/")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DB_PATH = os.getenv("DB_PATH", str(ROOT / "opening_act.db"))
FIXTURE_DIR = ROOT / "backend" / "fixtures"
FRONTEND_DIST = ROOT / "frontend" / "dist"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
QLOO_TAKE = _int("QLOO_TAKE", 15)   # shortlist size that gets investigated
QLOO_POOL = _int("QLOO_POOL", 50)   # related artists requested per Qloo query
EVIDENCE_WORKERS = _int("EVIDENCE_WORKERS", 6)

# Comma-separated list. Default allows the Vite dev server only.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# Simple per-IP limit on expensive endpoints (new briefs, comparisons, reruns).
RATE_LIMIT_PER_HOUR = _int("RATE_LIMIT_PER_HOUR", 30)

# When true, live Qloo/evidence responses are also written to the replay fixtures.
RECORD_FIXTURES = _bool(os.getenv("RECORD_FIXTURES"), default=False)

# ── cost control ──────────────────────────────────────────────────────────
# Persistent cache for web searches, MusicBrainz lookups and LLM verdicts (separate SQLite file).
SEARCH_CACHE = _bool(os.getenv("SEARCH_CACHE"), default=True)
SEARCH_CACHE_PATH = os.getenv("SEARCH_CACHE_PATH", str(ROOT / "search_cache.sqlite"))
CACHE_TTL_DAYS = float(os.getenv("CACHE_TTL_DAYS", "14"))
# Max uncached web searches (≈ Tavily credits for basic search) one agent run or comparison may spend.
SEARCH_BUDGET_PER_RUN = _int("SEARCH_BUDGET_PER_RUN", 100)

# MusicBrainz: free, no key, ~1 request/second. Used as a supplementary, structured source.
MUSICBRAINZ = _bool(os.getenv("MUSICBRAINZ"), default=True)
MUSICBRAINZ_USER_AGENT = os.getenv("MUSICBRAINZ_USER_AGENT", "OpeningAct/1.0 ( https://github.com/opening-act )")

# Agent discovery moves (per search area): top of Qloo's list, then deeper, then the local scene.
DEEPER_N = _int("DEEPER_N", 10)      # extra artists checked lower in Qloo's list before widening
SCENE_N = _int("SCENE_N", 10)        # artists checked from the verified locals' scene
SCENE_SEEDS = _int("SCENE_SEEDS", 2)
LLM_LOCAL_N = _int("LLM_LOCAL_N", 8)  # local names asked from the LLM (city level only); 0 turns the move off  # verified local acts used as Qloo seeds for the scene search
