"""Blind taste-fit rating of the four shortlists from the last live run.

    python -m scripts.blind_sheet make     # writes blind_rating_sheet.csv (+ hidden key file)
    (a music-literate reviewer fills the two rating columns, 1 = poor, 5 = excellent)
    python -m scripts.blind_sheet score    # reveals which list was which, with average ratings
    python -m scripts.blind_sheet score gpt_ratings.csv   # score ratings saved in another file

The sheet shows each city's four lists as "List A-D" in a random order with artist names only:
no source, no scores, no evidence. The key that maps letters to methods is written to
blind_rating_key.json; the reviewer should not open it.
"""
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "live_smoke_report.json"
SHEET = ROOT / "blind_rating_sheet.csv"
KEY = ROOT / "blind_rating_key.json"
ARMS = {"llm_only": "LLM on its own", "qloo_no_city": "Qloo, no city", "qloo_city": "Qloo with the city",
        "opening_act": "Opening Act"}
COLS = ["city", "headliner", "venue_capacity", "list", "artists",
        "fit_with_headliner_1to5", "useful_as_support_shortlist_1to5", "notes"]


def make():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rng = random.Random()
    key, rows = {}, []
    for case in report:
        cmp = case.get("comparison")
        if not cmp:
            continue
        arms = list(ARMS)
        rng.shuffle(arms)
        key[case["city"]] = {}
        for letter, arm in zip("ABCD", arms):
            names = [r["artist_name"] for r in cmp["arms"][arm]]
            rng.shuffle(names)  # order inside a list must not give the method away either
            key[case["city"]][letter] = arm
            rows.append({"city": case["city"], "headliner": case["headliner"], "venue_capacity": 800,
                         "list": letter, "artists": "; ".join(names),
                         "fit_with_headliner_1to5": "", "useful_as_support_shortlist_1to5": "", "notes": ""})
    with SHEET.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows accents correctly
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    KEY.write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"Wrote {SHEET.name} ({len(rows)} lists). Give it to the reviewer; keep {KEY.name} closed until scoring.")


def score():
    key = json.loads(KEY.read_text(encoding="utf-8"))
    totals = {a: [] for a in ARMS}
    sheet = ROOT / sys.argv[2] if len(sys.argv) > 2 else SHEET  # e.g. gpt_ratings.csv
    raw = sheet.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):  # Excel may re-save the CSV in a Windows code page
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    import io
    with io.StringIO(text, newline="") as f:
        for r in csv.DictReader(f):
            arm = key[r["city"]][r["list"]]
            try:
                fit, use = float(r["fit_with_headliner_1to5"]), float(r["useful_as_support_shortlist_1to5"])
            except ValueError:
                continue
            totals[arm].append((fit, use))
            print(f"{r['city']:<24} List {r['list']} = {ARMS[arm]:<20} fit {fit:.0f}  useful {use:.0f}")
    print("\nAverages:")
    for arm, vals in totals.items():
        if vals:
            print(f"  {ARMS[arm]:<20} fit {sum(v[0] for v in vals) / len(vals):.2f}  "
                  f"useful {sum(v[1] for v in vals) / len(vals):.2f}  (n={len(vals)})")


if __name__ == "__main__":
    {"make": make, "score": score}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
