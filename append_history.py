#!/usr/bin/env python3
"""Append today's snapshot to a history log for trend/seasonality charts.

Runs after pull_finale.py in the refresh workflow. Reads snapshot.json (already
pulled — no Finale API call here, no credentials needed) and appends one compact
line per day to docs/history.jsonl, skipping if today's entry is already there
(the workflow runs every 5 minutes; history should only grow once a day).

Usage: python3 append_history.py [snapshot.json] [docs/history.jsonl]
"""
import json, os, sys

def fba_buckets(p):
    full = inb = stuck = 0
    for loc, qty in p.get("byLoc", {}).items():
        if "Amazon" not in loc:
            continue
        if "Unfulfillable" in loc or "Researching" in loc or "Problem" in loc:
            stuck += qty
        elif "Fulfillable" in loc:
            full += qty
        else:
            inb += qty
    return full, inb, stuck

def last_entry_date(hist_path):
    """Read just the tail of the file rather than the whole thing, since it only grows.
    The tail window must be bigger than a single day's entry (~50KB for ~1000 SKUs today) or
    this silently never finds the last line and re-appends on every run instead of once a day."""
    if not os.path.exists(hist_path):
        return None
    with open(hist_path, "rb") as f:
        try:
            f.seek(-2_000_000, os.SEEK_END)
        except OSError:
            f.seek(0)
        tail = f.read().decode("utf-8", "ignore").strip().splitlines()
    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line).get("date")
        except ValueError:
            continue
    return None

def main():
    snap_path = sys.argv[1] if len(sys.argv) > 1 else "snapshot.json"
    hist_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join("docs", "history.jsonl")
    snap = json.load(open(snap_path))
    today = snap["meta"]["today"]

    if last_entry_date(hist_path) == today:
        print(f"history already has an entry for {today}, skipping")
        return

    rows = []
    for p in snap["products"]:
        full, inb, stuck = fba_buckets(p)
        row = {"id": p["id"], "onHand": p["onHand"], "vel": p["vel"], "s30": p["s30"]}
        if full or inb or stuck:
            row["fbaFull"], row["fbaInb"], row["fbaStuck"] = full, inb, stuck
        rows.append(row)

    entry = {"date": today, "pulledAt": snap["meta"]["pulledAt"], "products": rows}
    os.makedirs(os.path.dirname(hist_path) or ".", exist_ok=True)
    with open(hist_path, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    print(f"appended {today}: {len(rows)} SKUs, {hist_path} is now {os.path.getsize(hist_path)} bytes")

if __name__ == "__main__":
    main()
