"""Turn field data into numbers for the write-up: CLIP threshold, GPS drift, screen-on time.

    python scripts/calibration.py
"""
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import db  # noqa: E402


def summary(xs):
    return f"n={len(xs)}  min={min(xs):.3f}  median={st.median(xs):.3f}  max={max(xs):.3f}" if xs else "n=0"


with db.tx() as c:
    names = {r["id"]: r["name"] or r["id"][:8] for r in c.execute("SELECT id, name FROM rocks")}
    samples = c.execute("SELECT * FROM samples").fetchall()
    visits = c.execute("SELECT * FROM visits").fetchall()
    walks = c.execute("SELECT * FROM walks ORDER BY started_at").fetchall()

same, diff, dists = [], [], []
hardest = []
for s in samples:
    for rock_id, sc in json.loads(s["scores"]).items():
        (same if rock_id == s["truth"] else diff).append(sc["similarity"])
        if rock_id == s["truth"] and sc.get("distance_m") is not None:
            dists.append(sc["distance_m"])
        if rock_id != s["truth"]:
            hardest.append((sc["similarity"], names.get(rock_id, rock_id[:8]), names.get(s["truth"], "none of mine")))

print("CLIP similarity (calibration samples)")
print(f"  same rock:      {summary(same)}")
print(f"  different rock: {summary(diff)}")
if same and diff:
    lo, hi = max(diff), min(same)
    if hi > lo:
        print(f"  separable: any threshold in ({lo:.3f}, {hi:.3f}) works; suggest {(lo + hi) / 2:.2f}")
    else:
        # Not separable: pick the threshold that gets the most samples right, and say so in the post.
        cands = sorted(set(same + diff))
        best = max(cands, key=lambda t: sum(x >= t for x in same) + sum(x < t for x in diff))
        acc = (sum(x >= best for x in same) + sum(x < best for x in diff)) / (len(same) + len(diff))
        print(f"  NOT separable by looks alone: best threshold {best:.3f} gets {acc:.0%} right. GPS has to do the rest.")
    print("  most confusable pairs:")
    for sim, a, b in sorted(hardest, reverse=True)[:5]:
        print(f"    {sim:.3f}  {a}  vs photo of  {b}")
if dists:
    print(f"\nGPS distance from adoption spot on true-rock photos (m): {summary(dists)}")

acc = [v for v in visits if v["accepted"]]
rej = [v for v in visits if not v["accepted"]]
print(f"\nVisits: {len(acc)} accepted, {len(rej)} refused")
print(f"  accepted similarity: {summary([v['similarity'] for v in acc if v['similarity'] is not None])}")
print(f"  accepted GPS distance (m): {summary([v['gps_distance_m'] for v in acc if v['gps_distance_m'] is not None])}")

if walks:
    print("\nWalks (the killer stat)")
    for w in walks:
        mins = (datetime.fromisoformat(w["ended_at"]) - datetime.fromisoformat(w["started_at"])).total_seconds() / 60
        print(f"  {w['started_at'][:10]}: {mins:.0f} min outside, screen on {w['screen_on_s']:.0f} s, {w['visits']} visits")
