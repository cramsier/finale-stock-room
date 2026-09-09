#!/usr/bin/env python3
"""Inject snapshot.json into template.html -> dashboard.html"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
snap_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "snapshot.json")
out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "dashboard.html")

s = json.load(open(snap_path))
# trim what the page doesn't show
for o in s["sales"] + s["pos"] + s["transfers"]:
    o.pop("notes", None)
for p in s["products"]:
    p.pop("reservedRows", None)
    p["onOrderRows"] = [{"qty": r["qty"], "note": (r["note"] or "")[:120]} for r in p.get("onOrderRows", [])][:6]

data = json.dumps(s, separators=(",", ":")).replace("</", "<\\/")
tpl = open(os.path.join(HERE, "template.html")).read()
assert "__DATA__" in tpl
open(out_path, "w").write(tpl.replace("__DATA__", data, 1))
print(out_path, round(os.path.getsize(out_path) / 1024), "KB")
