#!/usr/bin/env python3
"""Fetch card lists + prices for the characters in config.json -> docs/data/cards.json.
Run weekly (see .github/workflows/weekly.yml) or by hand: python3 update_data.py"""
import json, os, re, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "data", "cards.json")

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "card-tracker/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(2)

def onepiece(names):
    rows = []
    for ep in ("allSetCards", "allSTCards", "allPromoCards"):
        rows += get(f"https://optcgapi.com/api/{ep}/") or []
    cards = {}
    for c in rows:
        base = c["card_name"].split(" (")[0].strip()
        if base not in names:
            continue
        label = " ".join(re.findall(r"\(([^)]*)\)", c["card_name"]))
        label = " ".join(p for p in label.split() if not re.fullmatch(r"\d+|[A-Z]{2,4}\d*-\d+", p))
        rare = c["rarity"]
        foil = "Non-foil" if rare in ("C", "UC") and "_" not in c["card_image_id"] else "Foil"
        cards[c["card_image_id"]] = {
            "id": c["card_image_id"], "game": "onepiece", "character": base,
            "name": c["card_name"], "number": c["card_set_id"], "set": c["set_name"],
            "rarity": rare, "finish": foil, "variant": label or "Standard",
            "image": c["card_image"], "price": c["market_price"], "currency": "USD",
        }
    return list(cards.values())

def pokemon(rules):
    cards = {}
    def detail(cid):
        return get(f"https://api.tcgdex.net/v2/en/cards/{urllib.parse.quote(cid)}")
    for rule in rules:
        res = get("https://api.tcgdex.net/v2/en/cards?name=" + urllib.parse.quote(rule["name"])) or []
        ids = [c["id"] for c in res
               if (rule["match"] == "contains" and rule["name"].lower() in c["name"].lower())
               or c["name"].lower().startswith(rule["name"].lower())]
        with ThreadPoolExecutor(8) as ex:
            details = list(ex.map(detail, ids))
        for d in details:
            if not d or (rule["category"] != "any" and d.get("category") != rule["category"]):
                continue
            v, tp, cm = d.get("variants") or {}, (d.get("pricing") or {}).get("tcgplayer") or {}, (d.get("pricing") or {}).get("cardmarket") or {}
            finishes = [(k, f) for k, f in (("normal", "Non-foil"), ("reverse", "Reverse holo"), ("holo", "Holo")) if v.get(k)]
            if not finishes:
                finishes = [("normal", "Non-foil")]
            for key, label in finishes:
                tpk = {"normal": "normal", "reverse": "reverse-holofoil", "holo": "holofoil"}[key]
                price, cur = (tp.get(tpk) or {}).get("marketPrice"), "USD"
                if price is None and tp.get("holofoil") and key == "holo":
                    price = tp["holofoil"].get("marketPrice")
                if price is None and cm:
                    price, cur = cm.get("trend-holo" if key != "normal" else "trend"), "EUR"
                cards[d["id"] + ":" + key] = {
                    "id": d["id"] + ":" + key, "game": "pokemon", "character": rule["name"],
                    "name": d["name"], "number": f'{d["localId"]}/{d["set"]["cardCount"]["official"]}',
                    "set": d["set"]["name"], "rarity": d.get("rarity") or "-", "finish": label,
                    "variant": "Standard", "image": (d["image"] + "/low.webp") if d.get("image") else "",
                    "price": price, "currency": cur,
                }
    return list(cards.values())

def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    old = {}
    if os.path.exists(OUT):
        old = {c["id"]: c.get("price") for c in json.load(open(OUT))["cards"]}
    cards = onepiece(set(cfg["onepiece"])) + pokemon(cfg["pokemon"])
    for c in cards:
        c["prev_price"] = old.get(c["id"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": date.today().isoformat(), "characters": {
        "onepiece": cfg["onepiece"], "pokemon": [r["name"] for r in cfg["pokemon"]]}, "cards": cards},
        open(OUT, "w"), separators=(",", ":"))
    print(f"{len(cards)} cards written")

if __name__ == "__main__":
    main()
