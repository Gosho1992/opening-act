"""End-to-end check against the LIVE providers (Qloo, Tavily, OpenAI) using keys from .env.

It reports five things separately, because "every API call worked" is not the same as
"the shortlist is trustworthy":

  1. Execution        did every provider call and agent step run?
  2. Data integrity   are ranks, scopes and evidence rows stored consistently?
  3. Evidence quality how many sources were verified, unverified or rejected, and why?
  4. Recommendation   is the primary pick verified as local and the right size for the room?
  5. Comparison       are the four-way comparison counts internally consistent?

    python -m scripts.live_smoke_test                      # default cases
    python -m scripts.live_smoke_test "Khruangbin" "Nashville, Tennessee"
    python -m scripts.live_smoke_test --record              # also refresh demo fixtures

Writes live_smoke_report.json in the project root. Never prints API keys.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if "--record" in sys.argv:
    os.environ["RECORD_FIXTURES"] = "true"
    sys.argv.remove("--record")
os.environ.setdefault("DB_PATH", str(ROOT / "live_smoke.db"))
os.environ["DEMO_MODE"] = "false"

from backend import agent, config, db, llm, rules  # noqa: E402

CASES = [("Khruangbin", "Nashville, Tennessee"), ("Japanese Breakfast", "Atlanta, Georgia"),
         ("St. Vincent", "Austin, Texas")]


class In:
    def __init__(self, h, c):
        self.headliner, self.city, self.venue_capacity = h, c, 800
        self.show_date, self.budget, self.use_popularity_cap = None, None, True


def check(label, ok, detail=""):
    mark = "PASS" if ok is True else "WARN" if ok is None else "FAIL"
    print(f"  [{mark}] {label}" + (f": {detail}" if detail else ""))
    return ok is not False


def run_case(h, c):
    print(f"\n=== {h} in {c} (800 capacity) ===")
    bid = db.insert_brief(In(h, c), False)
    agent.run(bid, "smoke")
    b = db.fetch_brief(bid)
    calls = db.list_qloo_calls(bid)
    for call in calls:
        print(f"  Qloo {call['purpose']:<18} HTTP {call['status_code']} {call['duration_ms']}ms "
              f"params={call['params']}" + (f" ERROR {call['error']}" if call["error"] else ""))
    for e in b["events"]:
        print(f"  [{e['type']}] {e['message']}")
    print(f"  STATUS: {b['status']} | {b['status_reason']}")

    print("  Candidates (verified-local first):")
    for cand in b["candidates"]:
        if cand["excluded_reason"]:
            continue
        tag = (f"LOCAL:{cand['local_status']}{'+FIT' if cand['qualified'] else ' (weak fit)'}" if cand["local_proof"]
               else cand["local_status"] if cand["local_status"] in ("moved", "disputed")
               else "plays city" if cand["plays_market"] else "-")
        via = {"qloo": "", "qloo_deeper": " [deeper]", "scene": f" [scene via {cand['scene_seed']}]",
               "llm_local": " [AI-named local]"}.get(cand["found_via"], "")
        shared = f" shares: {', '.join(cand['shared_tags'][:4])}" if cand["shared_tags"] else ""
        print(f"   #{cand['city_rank'] or '+':<2} {cand['artist_name']:<26} pop={(cand['popularity'] or 0):.3f} "
              f"base={cand['noloc_rank']} {tag}{via}{shared}")
        for e in cand["evidence"]:
            if e["status"] == "rejected":
                continue
            print(f"        {e['status']:<10} {e['kind']:<11} [{e['method']}] \"{(e['quote'] or '')[:110]}\" "
                  f"{e['source_url'] or 'Qloo'}")

    ok = True
    print("  Checks:")
    # 1. execution
    # a failed attempt that a retry with the same request then completed is a warning, not a failure
    failed = [x for i, x in enumerate(calls) if x["error"] or (x["status_code"] or 200) >= 400]
    unrecovered = [x for x in failed if not any(y["status_code"] == 200 and y["purpose"] == x["purpose"]
                                                and y["params"] == x["params"] for y in calls[calls.index(x) + 1:])]
    ok &= check("Execution", (b["status"] != "failed" and not unrecovered) if not failed or unrecovered else None,
                b["status"] + (f"; {len(failed) - len(unrecovered)} Qloo call(s) failed once and succeeded on retry"
                               if len(failed) > len(unrecovered) else "")
                + (f"; {len(unrecovered)} Qloo call(s) failed" if unrecovered else ""))
    # 2. data integrity
    live = [x for x in b["candidates"] if x["status"] != "vetoed" and not x["excluded_reason"]]
    cap = rules.popularity_cap(800)
    bad_rows = [e for x in b["candidates"] for e in x["evidence"]
                if (e["status"] == "verified" and e["source_label"] == "public" and not e["quote"])
                or (not e["source_url"] and e["source_label"] != "qloo")]
    too_big_live = [x["artist_name"] for x in live if cap and x["popularity"] and x["popularity"] > cap]
    ok &= check("Data integrity", not bad_rows and not too_big_live,
                f"{len(bad_rows)} malformed evidence rows; oversized live candidates: {too_big_live or 'none'}")
    # 3. evidence quality
    rows = [e for x in b["candidates"] for e in x["evidence"]]
    st = Counter(e["status"] for e in rows)
    rej = Counter(e["reason"].split(";")[0][:60] for e in rows if e["status"] == "rejected")
    methods = Counter(e["method"] for e in rows if e["status"] == "verified")
    check("Evidence quality", None if not st["verified"] else True,
          f"{st['verified']} verified ({dict(methods)}), {st['unverified']} unverified, {st['rejected']} rejected")
    for reason, n in rej.most_common():
        print(f"         rejected x{n}: {reason}")
    # 4. recommendation
    entry = {"headliner": h, "city": c, "status": b["status"], "reason": b["status_reason"], "qloo_calls": calls,
             "candidates": b["candidates"], "events": b["events"]}
    if b["status"] in ("ready", "ready_low_evidence"):
        snap = agent.finalize(bid)
        entry["booking_brief"] = snap
        p = snap["picks"]["primary"]
        print(f"  Booking brief v{snap['version']} ({snap['narrative_source']}): {snap['narrative'][:200]}…")
        auto = [v for v in snap["picks"].values() if v and v.get("auto_selected")]
        all_qualified = all(v.get("qualified") for v in auto)
        if b["status"] == "ready":
            ok &= check("Recommendation", snap["kind"] == "recommendation" and len(auto) == 3 and all_qualified,
                        f"picks = {[v['artist_name'] for v in auto]}; every automatic pick verified local AND a taste fit")
        else:
            ok &= check("Recommendation", snap["kind"] in ("partial_recommendation", "research_shortlist")
                        and all_qualified and len(auto) < 3,
                        f"{snap['kind']}: automatic picks {[v['artist_name'] for v in auto] or 'none'} "
                        f"(only acts both local and a taste fit)")
        t = snap.get("tradeoffs") or {}
        print(f"         local but weak fit: {[c['artist_name'] for c in t.get('local_but_weak_fit', [])]}")
        print(f"         fit but not verified local: {[c['artist_name'] for c in t.get('fit_but_not_verified_local', [])]}")
        for w in snap["warnings"]:
            print(f"         warning: {w}")
        # 5. comparison
        if config.OPENAI_API_KEY:
            try:
                cmp = agent.compare_llm(bid)
                entry["comparison"] = cmp
                consistent = all(
                    s["verified_local"] == sum(r["verified_local"] for r in cmp["arms"][a])
                    and s["n"] == len(cmp["arms"][a])
                    and s["qualified"] <= s["verified_local_and_right_size"] <= s["verified_local"] <= s["n"]
                    and (s["shared_with_opening_act"] is None or s["shared_with_opening_act"] <= s["n"])
                    for a, s in cmp["summary"].items())
                print("  Four-way comparison (same checks for every list):")
                print(f"    {'list':<14}{'n':>3}{'qualified':>11}{'local':>7}{'local+size':>12}{'played only':>13}{'too big':>9}{'not in Qloo':>13}")
                for a, s in cmp["summary"].items():
                    print(f"    {a:<14}{s['n']:>3}{s['qualified']:>11}{s['verified_local']:>7}{s['verified_local_and_right_size']:>12}"
                          f"{s['played_city_only']:>13}{s['too_big_for_room']:>9}{s['not_found_in_qloo']:>13}")
                ok &= check("Comparison validity", consistent)
            except llm.LLMUnavailable as e:
                check("Comparison validity", None, f"LLM unavailable: {e}")
    return ok, entry


def main():
    print("Providers configured:", {"qloo": bool(config.QLOO_API_KEY), "tavily": bool(config.TAVILY_API_KEY),
                                     "openai": bool(config.OPENAI_API_KEY)}, "| base:", config.QLOO_BASE_URL)
    if not config.QLOO_API_KEY:
        sys.exit("QLOO_API_KEY missing in .env")
    if "hackathon" not in config.QLOO_BASE_URL:
        print("  note: hackathon keys only work with QLOO_BASE_URL=https://hackathon.api.qloo.com")
    db.init()
    cases = [(sys.argv[1], sys.argv[2])] if len(sys.argv) >= 3 else CASES
    report, all_ok = [], True
    for h, c in cases:
        ok, entry = run_case(h, c)
        all_ok &= ok
        report.append(entry)
    (ROOT / "live_smoke_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("\nReport: live_smoke_report.json | RESULT:", "PASS" if all_ok else "FAIL (see checks above)")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
