#!/usr/bin/env python3
"""Pull a read-only snapshot from Finale Inventory (GraphQL) and write snapshot.json.

Output is a plain JSON document the dashboard renders from. Nothing here writes to Finale.
Usage: python3 pull_finale.py [out.json]
Credentials: .env beside this file (FINALE_ACCOUNT, FINALE_KEY, FINALE_SECRET) or env vars.
"""
import os, sys, json, base64, re, time, datetime as dt, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

def load_env():
    e = {k: os.environ.get(k) for k in ("FINALE_ACCOUNT", "FINALE_KEY", "FINALE_SECRET")}
    p = os.path.join(HERE, "finale.credentials.txt")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                e.setdefault(k, None)
                if not e[k]:
                    e[k] = v
    missing = [k for k, v in e.items() if not v]
    if missing:
        sys.exit(f"missing credentials: {missing}")
    return e

E = load_env()
URL = f"https://app.finaleinventory.com/{E['FINALE_ACCOUNT']}/api/graphql"
AUTH = "Basic " + base64.b64encode(f"{E['FINALE_KEY']}:{E['FINALE_SECRET']}".encode()).decode()
REQUESTS = 0

def gql(query, variables=None, retries=3):
    global REQUESTS
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(URL, data=body, headers={
            "Authorization": AUTH, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                REQUESTS += 1
                out = json.loads(r.read())
            if "errors" in out:
                raise RuntimeError(json.dumps(out["errors"])[:500])
            return out["data"]
        except urllib.error.HTTPError as ex:
            if ex.code == 429 and attempt < retries - 1:
                time.sleep(30 * (attempt + 1)); continue
            raise
    raise RuntimeError("unreachable")

def paged(conn_name, args, node_fields, page=200):
    """Iterate all nodes of a *ViewConnection."""
    after = None
    while True:
        a = args + (f', after:"{after}"' if after else "")
        d = gql(f"{{ {conn_name}(first:{page}, {a}) {{ pageInfo{{hasNextPage endCursor}} edges{{ node{{ {node_fields} }} }} }} }}")
        c = d[conn_name]
        for e in c["edges"]:
            yield e["node"]
        if not c["pageInfo"]["hasNextPage"]:
            break
        after = c["pageInfo"]["endCursor"]

# ---------- parsing helpers (Finale returns display strings) ----------
def num(s):
    if s is None: return None
    s = str(s).strip().replace(",", "")
    if s in ("", "--", "-"): return None
    try: return float(s) if "." in s else int(s)
    except ValueError:
        m = re.search(r"-?\d+(\.\d+)?", s)
        return float(m.group()) if m else None

def days(s):
    """'153 d' -> 153, '> 365 d' -> 999, '0 d' -> 0, None -> None"""
    if not s: return None
    s = str(s)
    if s.startswith(">"): return 999
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None

def iso(s):
    """'9/9/2026' -> '2026-09-09'"""
    if not s: return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(s))
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None

def days_between(a_iso, b_iso):
    if not a_iso or not b_iso: return None
    a = dt.date.fromisoformat(a_iso); b = dt.date.fromisoformat(b_iso)
    return (b - a).days

TODAY = dt.date.today().isoformat()
AMAZON_FBA = "Amazon - FBA"

# ---------- pulls ----------
def pull_facilities():
    return [{"url": n["facilityUrl"], "name": n["name"], "parent": n["parentName"], "type": n["type"], "status": n["status"]}
            for n in paged("facilityViewConnection", "", "facilityUrl name parentName type status", page=100)]

PRODUCT_FIELDS = """productId description category unitOfMeasure stdPackingUnitsPerCase universalProductCode supplier leadTime
 stockQuantityOnHandUnits salesLast30Days salesLast90Days salesVelocity stockoutDays daysUntilReorder reorderPoint stdReorderLevel
 stockItem(first:80){ edges{ node{ type lotId quantity location{name} sublocation{name} title } } }"""

def pull_products():
    out = []
    for n in paged("productViewConnection", 'status:["PRODUCT_ACTIVE"]', PRODUCT_FIELDS):
        by_loc, lots, on_order, reserved = {}, [], [], []
        for e in n["stockItem"]["edges"]:
            s = e["node"]; qty = num(s["quantity"]) or 0
            loc = (s.get("location") or {}).get("name"); sub = (s.get("sublocation") or {}).get("name") or loc
            if s["type"] == "On hand":
                by_loc[sub] = by_loc.get(sub, 0) + qty
                if s["lotId"]:
                    lots.append({"lot": s["lotId"].replace("Lot: ", ""), "qty": qty, "loc": sub})
            elif s["type"] == "On order":
                on_order.append({"qty": qty, "loc": loc, "note": s["title"]})
            elif s["type"] == "Reserved":
                reserved.append({"qty": qty, "loc": loc, "note": s["title"]})
        vel = num(n["salesVelocity"]) or 0
        out.append({
            "id": n["productId"], "desc": n["description"], "cat": n["category"] or "UNCATEGORIZED",
            "upc": n["universalProductCode"], "upc_": None, "uom": n["unitOfMeasure"], "perCase": num(n["stdPackingUnitsPerCase"]),
            "supplier": n["supplier"], "lead": num(n["leadTime"]),
            "onHand": num(n["stockQuantityOnHandUnits"]) or 0, "byLoc": by_loc, "lots": lots,
            "onOrder": sum(x["qty"] for x in on_order), "onOrderRows": on_order,
            "reserved": sum(x["qty"] for x in reserved), "reservedRows": reserved,
            "s30": num(n["salesLast30Days"]) or 0, "s90": num(n["salesLast90Days"]) or 0,
            "vel": vel, "cover": days(n["stockoutDays"]),
            "reorderPt": num(n["reorderPoint"]) or num(n["stdReorderLevel"]),
        })
    for p in out: p.pop("upc_", None)
    return out

ORDER_FIELDS = """orderId orderDate shipDate receiveDate dueDate status statusExtended saleSource fulfillment eligibleToShip shipmentsStatusSummary
 numberItems totalUnits customerPo privateNotes origin{name} destination{name} customer{name} supplier{name}"""
ITEM_FIELDS = """itemList(first:100){ edges{ node{ product{productId description} productUnitsOrdered productUnitsReceived productUnitsShipped productUnitsPacked productUnitsRemainingToBePackedShippedOrReceived } } }"""

def order_row(n, with_items):
    r = {
        "id": n["orderId"], "date": iso(n["orderDate"]), "ship": iso(n["shipDate"]), "recv": iso(n["receiveDate"]), "due": iso(n["dueDate"]),
        "status": n["status"], "source": n["saleSource"], "fulfil": n["fulfillment"], "eligible": n["eligibleToShip"],
        "shipStatus": n["shipmentsStatusSummary"], "items": num(n["numberItems"]) or 0, "units": num(n["totalUnits"]) or 0,
        "po": n["customerPo"], "notes": (n["privateNotes"] or "")[:300],
        "origin": (n.get("origin") or {}).get("name"), "dest": (n.get("destination") or {}).get("name"),
        "customer": (n.get("customer") or {}).get("name"), "supplier": (n.get("supplier") or {}).get("name"),
        "age": days_between(iso(n["orderDate"]), TODAY),
    }
    if with_items and n.get("itemList"):
        r["lines"] = [{
            "id": (e["node"]["product"] or {}).get("productId"), "desc": (e["node"]["product"] or {}).get("description"),
            "ordered": num(e["node"]["productUnitsOrdered"]) or 0, "received": num(e["node"]["productUnitsReceived"]) or 0,
            "shipped": num(e["node"]["productUnitsShipped"]) or 0, "packed": num(e["node"]["productUnitsPacked"]) or 0,
            "remaining": num(e["node"]["productUnitsRemainingToBePackedShippedOrReceived"]) or 0,
        } for e in n["itemList"]["edges"]]
    return r

def pull_open_sales():
    rows = [order_row(n, True) for n in paged("orderViewConnection",
            'type:["SALES_ORDER"], status:["ORDER_LOCKED"], sort:[{field:"orderDate",mode:"asc"}]', ORDER_FIELDS + " " + ITEM_FIELDS, page=150)]
    for r in rows:
        r["fba"] = AMAZON_FBA in (r["source"] or "")
        if r["fba"]: r.pop("lines", None)   # FBA orders: keep counts only
    return rows

def pull_open_pos():
    rows = [order_row(n, True) for n in paged("orderViewConnection",
            'type:["PURCHASE_ORDER"], status:["ORDER_LOCKED"], sort:[{field:"orderDate",mode:"asc"}]', ORDER_FIELDS + " " + ITEM_FIELDS, page=100)]
    for r in rows:
        r["remaining"] = sum(l["remaining"] for l in r.get("lines", []))
        r["ordered"] = sum(l["ordered"] for l in r.get("lines", []))
        r["fullyReceived"] = r["ordered"] > 0 and r["remaining"] == 0
        eta = r["recv"] or r["due"]
        r["eta"] = eta
        r["late"] = days_between(eta, TODAY) if eta and eta < TODAY and not r["fullyReceived"] else 0
    return rows

def pull_open_transfers():
    return [order_row(n, False) for n in paged("orderViewConnection",
            'type:["TRANSFER_ORDER"], status:["ORDER_LOCKED"], sort:[{field:"orderDate",mode:"asc"}]', ORDER_FIELDS, page=100)]

def draft_counts():
    out = {}
    for t, k in (("SALES_ORDER", "sales"), ("PURCHASE_ORDER", "purchase")):
        d = gql(f'{{ orderViewConnection(first:1, type:["{t}"], status:["ORDER_CREATED"]) {{ summary{{metrics{{count}}}} }} }}')
        out[k] = (d["orderViewConnection"]["summary"]["metrics"]["count"] or [0])[0]
    return out

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "snapshot.json")
    t0 = time.time()
    snap = {
        "meta": {"pulledAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "account": E["FINALE_ACCOUNT"], "today": TODAY},
        "facilities": pull_facilities(),
        "products": pull_products(),
        "sales": pull_open_sales(),
        "pos": pull_open_pos(),
        "transfers": pull_open_transfers(),
        "drafts": draft_counts(),
    }
    snap["meta"]["requests"] = REQUESTS
    snap["meta"]["seconds"] = round(time.time() - t0, 1)
    snap["meta"]["counts"] = {"products": len(snap["products"]), "sales": len(snap["sales"]), "pos": len(snap["pos"]), "transfers": len(snap["transfers"]),
                              "lotted": sum(1 for p in snap["products"] if p["lots"])}
    json.dump(snap, open(out_path, "w"), separators=(",", ":"))
    print(json.dumps(snap["meta"], indent=1))

if __name__ == "__main__":
    main()
