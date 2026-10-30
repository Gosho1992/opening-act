"""HTTP API. Long agent runs happen in the background; the client polls GET /api/briefs/{id}."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, config, db, llm
from .models import CityCompareIn, ConfirmIn, NegotiateIn, ShowBriefIn



@asynccontextmanager
async def lifespan(_app):
    db.init()
    db.recover_interrupted()
    yield


app = FastAPI(title="Opening Act", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"])


# ── rate limiting (per client IP, in memory) ──────────────────────────────
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _limit(request: Request) -> None:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown")
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= config.RATE_LIMIT_PER_HOUR:
            raise HTTPException(429, "Too many agent runs from this address. Try again later.")
        q.append(now)


def _brief_or_404(bid: str) -> dict:
    b = db.fetch_brief(bid)
    if not b:
        raise HTTPException(404, "Brief not found")
    return b


def _start(background: BackgroundTasks, bid: str, trigger: str) -> None:
    if agent.is_running(bid):
        raise HTTPException(409, "The agent is already working on this brief")
    db.set_status(bid, "pending")
    background.add_task(_safe_run, bid, trigger)


def _safe_run(bid: str, trigger: str) -> None:
    try:
        agent.run(bid, trigger)
    except agent.AgentBusy:
        pass


@app.exception_handler(db.InvalidTransition)
def _invalid(_: Request, exc: db.InvalidTransition):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(db.NotFound)
def _notfound(_: Request, exc: db.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


# ── routes ────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True, "demo_mode": config.DEMO_MODE,
            "providers": {"qloo": bool(config.QLOO_API_KEY) or config.DEMO_MODE,
                          "evidence": bool(config.TAVILY_API_KEY) or config.DEMO_MODE,
                          "llm": bool(config.OPENAI_API_KEY)}}


@app.post("/api/briefs", status_code=202)
def create_brief(inp: ShowBriefIn, background: BackgroundTasks, request: Request):
    _limit(request)
    bid = db.insert_brief(inp, demo_mode=config.DEMO_MODE)
    _start(background, bid, "initial")
    return _brief_or_404(bid)


@app.get("/api/briefs/{bid}")
def get_brief(bid: str):
    return _brief_or_404(bid)


@app.post("/api/briefs/{bid}/confirm", status_code=202)
def confirm(bid: str, inp: ConfirmIn, background: BackgroundTasks):
    b = db.require_brief(bid)
    if b["status"] != "needs_confirmation":
        raise HTTPException(409, "This brief is not waiting for a headliner confirmation")
    opt = next((o for o in b["resolution_options"] if o["id"] == inp.qloo_id), None)
    if not opt:
        raise HTTPException(422, "That artist was not one of the offered matches")
    db.update_brief(bid, headliner_qloo_id=opt["id"], headliner_resolved_name=opt["name"], resolution_options=[])
    db.add_event(bid, None, "buyer", f"Buyer confirmed the headliner is '{opt['name']}'")
    db.log_negotiation(bid, "confirm", inp.model_dump())
    _start(background, bid, "confirm")
    return _brief_or_404(bid)


@app.post("/api/briefs/{bid}/negotiate")
def negotiate(bid: str, inp: NegotiateIn, background: BackgroundTasks, request: Request):
    b = db.require_brief(bid)
    if agent.is_running(bid) or b["status"] in db.ACTIVE_STATUSES:
        raise HTTPException(409, "Wait for the agent to finish before changing the brief")
    if b["status"] == "needs_confirmation":
        raise HTTPException(409, "Confirm the headliner first")
    db.log_negotiation(bid, inp.action, inp.model_dump())
    rerun = None
    if inp.action == "veto":
        db.veto(bid, inp.candidate_id)
        db.add_event(bid, None, "buyer", "Buyer vetoed a candidate")
    elif inp.action == "unveto":
        db.unveto(bid, inp.candidate_id)
    elif inp.action == "set_slot":
        db.set_slot(bid, inp.candidate_id, inp.slot)
    elif inp.action == "clear_slot":
        db.clear_slot(bid, inp.candidate_id)
    elif inp.action == "reweight":
        db.rescore(bid, inp.weights)
        db.add_event(bid, None, "buyer", "Buyer re-weighted the ranking", inp.weights)
    elif inp.action == "widen":
        if b["widen_level"] >= 2:
            raise HTTPException(409, "The search already covers the widest scope")
        db.update_brief(bid, widen_level=b["widen_level"] + 1)
        db.add_event(bid, None, "buyer", "Buyer asked to widen the search area")
        rerun = "widen"
    elif inp.action == "toggle_cap":
        db.update_brief(bid, use_popularity_cap=0 if b["use_popularity_cap"] else 1)
        db.add_event(bid, None, "buyer", "Buyer toggled the smaller-acts preference")
        rerun = "toggle_cap"
    elif inp.action == "rerun":
        rerun = "rerun"
    if rerun:
        _limit(request)
        _start(background, bid, rerun)
    elif b["status"] == "finalized":
        # the buyer changed something after finalizing: the old snapshot stays, the brief is editable again
        db.set_status(bid, "ready", "Changed after the last booking brief; finalize again to save a new version")
    return _brief_or_404(bid)


@app.post("/api/briefs/{bid}/finalize")
def finalize(bid: str):
    if agent.is_running(bid):
        raise HTTPException(409, "Wait for the agent to finish")
    return agent.finalize(bid)


@app.post("/api/briefs/{bid}/compare")
def compare(bid: str, request: Request):
    _limit(request)
    try:
        return agent.compare_llm(bid)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, f"LLM-only comparison is unavailable: {e}")


@app.post("/api/briefs/{bid}/city-compare", status_code=202)
def city_compare(bid: str, inp: CityCompareIn, background: BackgroundTasks, request: Request):
    _limit(request)
    new_id = agent.create_city_comparison(bid, " ".join(inp.city.split()))
    _start(background, new_id, "city_compare")
    return _brief_or_404(new_id)


@app.get("/api/briefs/{bid}/qloo-calls")
def qloo_calls(bid: str):
    _brief_or_404(bid)
    return db.list_qloo_calls(bid)


@app.get("/api/share/{token}")
def share(token: str):
    snap = db.get_share(token)
    if not snap:
        raise HTTPException(404, "This booking brief link is invalid")
    return snap


# ── built frontend (single-page app) ──────────────────────────────────────
if (config.FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = config.FRONTEND_DIST / path
        if path and f.is_file() and config.FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
