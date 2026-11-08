#!/usr/bin/env python3
"""Fetch card lists + prices for the characters in config.json -> docs/data/cards.json.
Sources: optcgapi.com (One Piece EN + promos), yuyu-tei.jp (One Piece JP, JPY), tcgdex.net (Pokemon EN + JP).
Run weekly (see .github/workflows/weekly.yml) or by hand: python3 update_data.py"""
import html, json, os, re, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs", "data", "cards.json")
UA = {"User-Agent": "Mozilla/5.0 card-tracker/1.0"}

def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read().decode()
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(2)

def get(url):
    t = fetch(url)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None

SETNAMES = {}  # "OP01" -> "Romance Dawn", filled from the English data
JP_TERMS = [("スーパーパラレル", "Super Parallel"), ("パラレル", "Parallel"), ("刻印なし", "No stamp"),
            ("刻印あり", "Stamped"), ("漫画", "Manga"), ("リプリント", "Reprint")]

def en_variant(name):
    parts = []
    for raw in re.findall(r"[(（]([^)）]*)[)）]", name):
        for jp, en in JP_TERMS:
            raw = raw.replace(jp, en)
        parts.append(raw.strip())
    return " ".join(p for p in parts if p) or "Standard"

def onepiece_en(names, aliases):
    cards = {}
    for ep, promo in (("allSetCards", False), ("allSTCards", False), ("allPromos", True)):
        for c in get(f"https://optcgapi.com/api/{ep}/") or []:
            SETNAMES.setdefault(c["set_id"].replace("-", "").split("EB")[0] if False else c["set_id"].replace("-", ""), c["set_name"])
            base = c["card_name"].split(" (")[0].strip()
            if base not in names and base not in aliases:
                continue
            label = " ".join(re.findall(r"\(([^)]*)\)", c["card_name"]))
            label = " ".join(p for p in label.split() if not re.fullmatch(r"\d+|[A-Z]{2,4}\d*-\d+", p))
            rare = c["rarity"]
            foil = "Non-foil" if rare in ("C", "UC") and "_" not in c["card_image_id"] else "Foil"
            cid = ("promo:" if promo else "") + c["card_image_id"]
            n = 1
            while cid in cards:
                n += 1
                cid = f'{"promo:" if promo else ""}{c["card_image_id"]}#{n}'
            cards[cid] = {
                "id": cid, "game": "onepiece", "lang": "EN", "promo": promo or c["card_set_id"].startswith("P-"),
                "character": aliases.get(base, base), "name": c["card_name"], "number": c["card_set_id"],
                "set": c["set_name"], "rarity": rare, "finish": foil, "variant": label or "Standard",
                "image": c["card_image"], "price": c["market_price"], "currency": "USD",
            }
    return list(cards.values())

def onepiece_jp(jp):
    cards = {}
    for character, words in jp.items():
        for w in words:
            h = fetch("https://yuyu-tei.jp/sell/opc/s/search?search_word=" + urllib.parse.quote(w))
            if not h:
                continue
            for sec in re.split(r'(?=<span\s+class="py-2 d-inline-block px-2 me-2 text-white fw-bold">)', h)[1:]:
                rar = re.match(r'[^>]*>([^<]*)</span>', sec)[1].strip().replace("P-", "")
                for b in sec.split('class="card-product')[1:]:
                    m = re.search(r'/sell/opc/card/([^"]+)"', b)
                    n = re.search(r'<h4[^>]*>([^<]*)</h4>', b)
                    num = re.search(r'text-center my-2">([^<]*)<', b)
                    p = re.search(r'([\d,]+)\s*円', b)
                    img = re.search(r'src="(https://card\.yuyu-tei\.jp[^"]+)"', b)
                    if not (m and n and num):
                        continue
                    name = html.unescape(n[1]).strip()
                    if not name.startswith(w):
                        continue
                    variant = en_variant(name)
                    cards["jp:" + m[1]] = {
                        "id": "jp:" + m[1], "game": "onepiece", "lang": "JP",
                        "promo": rar in ("P", "-") or num[1].startswith("P-"),
                        "character": character, "name": name, "number": num[1].strip(),
                        "set": SETNAMES.get(m[1].split("/")[0].upper(), m[1].split("/")[0].upper()), "rarity": rar,
                        "finish": "Non-foil" if rar in ("C", "UC") and "パラレル" not in name else "Foil",
                        "variant": variant, "image": img[1].replace("100_140", "200_280") if img else "",
                        "price": int(p[1].replace(",", "")) if p else None, "currency": "JPY",
                    }
    return list(cards.values())

def cardrush_jp(jp, have):
    """Japanese promo cards that Yuyu-tei doesn't list, from cardrush-op.jp (prices incl. tax)."""
    cards = {}
    for character, words in jp.items():
        for w in words:
            for page in range(1, 8):
                h = fetch("https://www.cardrush-op.jp/product-list?keyword=" + urllib.parse.quote(w) + f"&page={page}")
                blocks = re.split(r'(?=<li class="list_item_cell)', h or "")[1:]
                if not blocks:
                    break
                for b in blocks:
                    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", b))).strip()
                    m = re.match(r"(?:〔状態A-?〕)?\s*(.+?)【([^】]+)】\{([^}]+)\}.*?([\d,]+)円.*?在庫数", text)
                    link = re.search(r'href="https://www\.cardrush-op\.jp/product/(\d+)"', b)
                    img = re.search(r'src="(https://www\.cardrush-op\.jp/data/[^"]+)"', b)
                    if not (m and link) or text.startswith("〔") and not text.startswith("〔状態A-〕"):
                        continue
                    name, rar, num = m[1].strip(), m[2], m[3]
                    if not name.startswith(w) or not num.startswith("P-") or any(num == c["number"] for c in have):
                        continue
                    variant = " ".join(re.findall(r"[(（]([^)）]*)[)）]", name)) or "Standard"
                    cid = f"jp:cr{num}:{variant}"
                    cards.setdefault(cid, {
                        "id": cid, "game": "onepiece", "lang": "JP", "promo": True, "character": character,
                        "name": name, "number": num, "set": "Promo", "rarity": rar, "finish": "Foil",
                        "variant": en_variant(name), "image": img[1] if img else "",
                        "price": int(m[4].replace(",", "")), "currency": "JPY"})
    return list(cards.values())

def pokemon(rules):
    cards = {}
    def detail(arg):
        lang, cid = arg
        return lang, get(f"https://api.tcgdex.net/v2/{lang}/cards/{urllib.parse.quote(cid)}")
    for rule in rules:
        for lang in ("en", "ja"):
            term = rule["name"] if lang == "en" else rule["ja"]
            res = get(f"https://api.tcgdex.net/v2/{lang}/cards?name=" + urllib.parse.quote(term)) or []
            ids = [(lang, c["id"]) for c in res
                   if (rule["match"] == "contains" and term.lower() in c["name"].lower())
                   or c["name"].lower().startswith(term.lower())]
            with ThreadPoolExecutor(8) as ex:
                details = list(ex.map(detail, ids))
            for _, d in details:
                if not d or (rule["category"] != "any" and d.get("category") != rule["category"]):
                    continue
                v = d.get("variants") or {}
                tp = (d.get("pricing") or {}).get("tcgplayer") or {}
                cm = (d.get("pricing") or {}).get("cardmarket") or {}
                finishes = [(k, f) for k, f in (("normal", "Non-foil"), ("reverse", "Reverse holo"), ("holo", "Holo")) if v.get(k)] or [("normal", "Non-foil")]
                for key, label in finishes:
                    tpk = {"normal": "normal", "reverse": "reverse-holofoil", "holo": "holofoil"}[key]
                    price, cur = (tp.get(tpk) or {}).get("marketPrice"), "USD"
                    if price is None and tp.get("holofoil") and key == "holo":
                        price = tp["holofoil"].get("marketPrice")
                    if price is None and cm:
                        price, cur = cm.get("trend-holo" if key != "normal" else "trend"), "EUR"
                    cid = ("" if lang == "en" else "ja:") + d["id"] + ":" + key
                    cards[cid] = {
                        "id": cid, "game": "pokemon", "lang": "EN" if lang == "en" else "JP",
                        "promo": bool(re.search("promo|プロモ", d["set"]["name"].lower())), "character": rule["name"],
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
    jp = onepiece_jp(cfg.get("onepiece_jp", {}))
    cards = (onepiece_en(set(cfg["onepiece"]), cfg.get("onepiece_aliases", {})) + jp
             + cardrush_jp(cfg.get("onepiece_jp", {}), jp) + pokemon(cfg["pokemon"]))
    ov = json.load(open(os.path.join(ROOT, "image_overrides.json"))) if os.path.exists(os.path.join(ROOT, "image_overrides.json")) else {}
    for c in cards:
        if (not c["image"] or "noimage" in c["image"]) and c["id"] in ov:
            c["image"], c["imageApprox"] = ov[c["id"]], True
        c["finish"] = cfg.get("finish_overrides", {}).get(c["id"], c["finish"])
        c["prev_price"] = old.get(c["id"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": date.today().isoformat(), "characters": {
        "onepiece": cfg["onepiece"], "pokemon": [r["name"] for r in cfg["pokemon"]]}, "cards": cards},
        open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(cards)} cards written")

if __name__ == "__main__":
    main()
