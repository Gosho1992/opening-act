import sqlite3

import pytest

from backend import db
from tests.conftest import ShowIn


def _seed(bid):
    rid = db.start_run(bid, "test", 0, False)
    rows = []
    for i, name in enumerate(["A", "B", "C"], start=1):
        rows.append({"qloo_id": name, "artist_name": name, "found_level": 0, "found_scope": "X", "city_rank": i,
                     "taste_score": 0.5, "local_score": 0.0, "practical_score": 0.5, "total_score": 0.3,
                     "evidence": []})
    db.upsert_candidates(bid, rid, rows)
    return {c["artist_name"]: c["id"] for c in db.get_candidates(bid)}


def test_slot_is_unique_and_moves():
    bid = db.insert_brief(ShowIn(), False)
    ids = _seed(bid)
    db.set_slot(bid, ids["A"], "primary")
    db.set_slot(bid, ids["B"], "primary")
    slots = {c["artist_name"]: c["slot"] for c in db.get_candidates(bid)}
    assert slots == {"A": None, "B": "primary", "C": None}
    db.set_slot(bid, ids["B"], "backup")  # a candidate holds one slot at most
    slots = {c["artist_name"]: c["slot"] for c in db.get_candidates(bid)}
    assert slots["B"] == "backup"


def test_veto_clears_slot_and_blocks_selection():
    bid = db.insert_brief(ShowIn(), False)
    ids = _seed(bid)
    db.set_slot(bid, ids["A"], "primary")
    db.veto(bid, ids["A"])
    assert next(c for c in db.get_candidates(bid) if c["id"] == ids["A"])["slot"] is None
    with pytest.raises(db.InvalidTransition):
        db.set_slot(bid, ids["A"], "backup")


def test_candidate_must_belong_to_brief():
    b1 = db.insert_brief(ShowIn(), False)
    b2 = db.insert_brief(ShowIn(), False)
    ids = _seed(b1)
    with pytest.raises(db.NotFound):
        db.veto(b2, ids["A"])


def test_database_rejects_invalid_state():
    bid = db.insert_brief(ShowIn(), False)
    ids = _seed(bid)
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("UPDATE candidates SET status='vetoed', slot='primary' WHERE id=?", (ids["A"],))
    with pytest.raises(ValueError):
        db.set_status(bid, "made_up")


def test_rescore_and_upsert_keep_identity():
    bid = db.insert_brief(ShowIn(), False)
    ids = _seed(bid)
    db.veto(bid, ids["A"])
    _seed(bid)  # a rerun returns the same artists
    after = {c["artist_name"]: c for c in db.get_candidates(bid)}
    assert after["A"]["id"] == ids["A"] and after["A"]["status"] == "vetoed"
    db.rescore(bid, {"practical": 1})
    assert all(c["total_score"] == 0.5 for c in db.get_candidates(bid))
