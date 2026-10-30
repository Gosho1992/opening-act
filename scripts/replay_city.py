"""Re-run one city with the current code, using cached searches/verdicts (paid searches capped)."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "false"
from backend import agent, db
db.init()
class In:
    def __init__(s, h, c): s.headliner, s.city, s.venue_capacity, s.show_date, s.budget, s.use_popularity_cap = h, c, 800, None, None, True
h, c = sys.argv[1], sys.argv[2]
bid = db.insert_brief(In(h, c), False); agent.run(bid, "replay")
b = db.fetch_brief(bid)
print(f"== {h}/{c}: {b['status']} | {b['status_reason'][:150]}")
for e in b["events"]:
    if e["type"] in ("decision", "budget") or "Web searches" in e["message"]: print("   ", e["message"][:190])
for x in b["candidates"]:
    if x["local_proof"]:
        q = next(e for e in x["evidence"] if e["status"] == "verified" and e["kind"] in ("based_here", "formed_here"))
        print(f"   LOCAL {x['artist_name']:<20} {x['found_via']:<11} pop={x['popularity']:.2f} tags={x['tag_overlap']} {q['kind']}/{q['source_label']}: {(q['quote'] or '')[:100]}")
