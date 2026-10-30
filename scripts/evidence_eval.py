"""Controlled evaluation of the evidence layer on hand-labelled hard cases.

Question: is this artist based in / from the show city? Each case has an expected answer.
Three configurations are scored on the SAME cases:

    tavily_only        web search + verification (the current pipeline)
    musicbrainz_only   MusicBrainz structured data only
    tavily_plus_mb     both (what the agent uses)

    python -m scripts.evidence_eval                 # live (uses the cache; paid searches capped)
    python -m scripts.evidence_eval --budget 40     # change the cap on paid searches
    python -m scripts.evidence_eval --heldout       # the held-out cities in scripts/heldout_cases.json

LABELS ARE A STARTING POINT, NOT GROUND TRUTH: they come from public bios as of Oct 2026.
Check each one and correct it in CASES before quoting the numbers. Writes evidence_eval_report.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEMO_MODE"] = "false"

from backend import config, rules  # noqa: E402
from backend.budget import Budget, BudgetExceeded  # noqa: E402
from backend.evidence import EvidenceError, TavilyEvidence  # noqa: E402
from backend.musicbrainz import MusicBrainz  # noqa: E402

# (artist, city, expected_local, why) — the hard cases from the 2026-10-28 live runs
CASES = [
    ("Lunar Vacation", "Atlanta, Georgia", True, "Atlanta band"),
    ("girlpuppy", "Atlanta, Georgia", True, "Becca Harvey, Atlanta"),
    ("Faye Webster", "Atlanta, Georgia", True, "Atlanta singer-songwriter"),
    ("Dehd", "Atlanta, Georgia", False, "Chicago band; a member spoke of being from Atlanta"),
    ("Soccer Mommy", "Atlanta, Georgia", False, "Nashville act that played Atlanta"),
    ("Mitski", "Atlanta, Georgia", False, "touring act"),
    ("Soccer Mommy", "Nashville, Tennessee", True, "Nashville"),
    ("All Them Witches", "Nashville, Tennessee", True, "Nashville band"),
    ("Ron Gallo", "Nashville, Tennessee", True, "Philadelphia-born, lives in Nashville"),
    ("The Nude Party", "Nashville, Tennessee", False, "North Carolina / New York; label is Nashville-based"),
    ("Whitney", "Nashville, Tennessee", False, "Chicago band; name collides with Whitney Houston"),
    ("Brittany Howard", "Nashville, Tennessee", True, "Athens AL native living in Nashville (check)"),
    ("Shakey Graves", "Austin, Texas", True, "Austin"),
    ("Black Pumas", "Austin, Texas", True, "Austin band"),
    ("Suki Waterhouse", "Austin, Texas", False, "London/LA; played ACL Fest"),
    ("Girlpool", "Austin, Texas", False, "Los Angeles; an Austin blog wrote about them"),
    ("Tennis", "Austin, Texas", False, "Denver band; name collides with the sport"),
]


def main():
    global CASES
    report_name = "evidence_eval_report.json"
    if "--heldout" in sys.argv:
        fx = json.loads((ROOT / "scripts" / "heldout_cases.json").read_text(encoding="utf-8"))
        CASES = [tuple(c) for c in fx["cases"]]
        report_name = "evidence_eval_heldout_report.json"
        if not fx.get("checked"):
            print("WARNING: heldout_cases.json labels are not marked as checked. Check them before quoting results.")
    budget_n = 40
    if "--budget" in sys.argv:
        budget_n = int(sys.argv[sys.argv.index("--budget") + 1])
    budget = Budget(budget_n)
    tavily = TavilyEvidence(budget=budget) if config.TAVILY_API_KEY else None
    mb = MusicBrainz()
    print(f"Providers: tavily={'on' if tavily else 'OFF (no key)'}, musicbrainz=on, verifier="
          f"{'LLM ' + config.OPENAI_MODEL if config.OPENAI_API_KEY else 'rule only'} | paid-search cap {budget_n}")

    rows = []
    for artist, city, expected, why in CASES:
        levels = rules.scopes(city)
        scope = levels[0]
        wide = list(dict.fromkeys(scope["place_terms"] + (levels[2] or levels[1])["place_terms"]))
        mbr = mb.evidence(artist, scope["place_terms"], "city")
        web, web_note = [], ""
        if tavily:
            try:  # same path as the agent: evidence search, then the current-base check if it looks local
                web = tavily.check(artist, scope["place_terms"], "city", context={"prior": mbr}, wide_terms=wide)
            except BudgetExceeded:
                web_note = "not checked (budget)"
            except EvidenceError as e:
                web_note = f"error: {e}"
        local = lambda ev: rules.local_score(ev) >= rules.LOCAL_PROOF_THRESHOLD
        best = lambda ev: next((f"{e['kind']}: {(e.get('quote') or e['claim'])[:70]}" for e in ev
                                if e["status"] == "verified" and e["kind"] in ("based_here", "formed_here")), "")
        ctx = "; ".join(e["claim"] for e in mbr if e["kind"] == "context")
        rows.append({"artist": artist, "city": city, "expected": expected, "why": why,
                     "tavily_only": None if web_note else local(web), "musicbrainz_only": local(mbr),
                     "tavily_plus_mb": None if web_note and not local(mbr) else local(web + mbr),
                     "web_note": web_note, "web_best": best(web), "mb_best": best(mbr), "mb_context": ctx,
                     "status": rules.local_status(web + mbr)["status"], "status_note": rules.local_status(web + mbr)["note"]})

    print(f"\n{'artist':<18}{'city':<11}{'truth':<7}{'tavily':<8}{'MB':<6}{'both':<6} notes")
    mark = lambda v, exp: "  -  " if v is None else ("yes" if v else "no ") + (" " if v == exp else "✗")
    for r in rows:
        note = f"[{r['status']}] " + (r["status_note"] or r["web_note"] or r["web_best"] or r["mb_best"] or r["mb_context"] or "")
        print(f"{r['artist']:<18}{r['city'].split(',')[0]:<11}{'yes' if r['expected'] else 'no':<7}"
              f"{mark(r['tavily_only'], r['expected']):<8}{mark(r['musicbrainz_only'], r['expected']):<6}"
              f"{mark(r['tavily_plus_mb'], r['expected']):<6} {note[:80]}")

    print("\nScores (cases not checked are left out):")
    summary = {}
    for cfg in ("tavily_only", "musicbrainz_only", "tavily_plus_mb"):
        scored = [r for r in rows if r[cfg] is not None]
        tp = sum(1 for r in scored if r[cfg] and r["expected"])
        fp = [r["artist"] + "/" + r["city"].split(",")[0] for r in scored if r[cfg] and not r["expected"]]
        fn = [r["artist"] + "/" + r["city"].split(",")[0] for r in scored if not r[cfg] and r["expected"]]
        prec = tp / (tp + len(fp)) if tp + len(fp) else None
        rec = tp / (tp + len(fn)) if tp + len(fn) else None
        summary[cfg] = {"checked": len(scored), "true_positives": tp, "false_positives": fp, "missed": fn,
                        "precision": prec, "recall": rec}
        print(f"  {cfg:<17} checked {len(scored):>2} | precision {'-' if prec is None else f'{prec:.0%}':>4} "
              f"| recall {'-' if rec is None else f'{rec:.0%}':>4} | false 'local': {fp or 'none'} | missed: {fn or 'none'}")
    print(f"\nWeb searches: {budget.live} paid, {budget.cached} from cache | MusicBrainz: {mb.lookups}")
    (ROOT / report_name).write_text(json.dumps({"cases": rows, "summary": summary,
                                                                "usage": budget.summary(), "musicbrainz": mb.lookups},
                                                               indent=1), encoding="utf-8")
    print(f"Report: {report_name}")


if __name__ == "__main__":
    main()
