#!/usr/bin/env python3
"""Fetch card lists + prices for the characters in config.json -> docs/data/cards.json.
Sources: onepiece-cardgame.com official card list (One Piece EN + JP catalog), optcgapi.com (One Piece EN prices + event promos),
yuyu-tei.jp / cardrush-op.jp (One Piece JP prices, JPY), tcgdex.net (Pokemon EN + JP).
Run weekly (see .github/workflows/weekly.yml) or by hand: python3 update_data.py"""
import html, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
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

def nfkc(t):
    return unicodedata.normalize("NFKC", t).strip()

def official(host):
    """Every card on Bandai's official card list, one request per series: {id: card}."""
    base = f"https://{host}/cardlist/"
    series = re.findall(r'<option value="(\d{5,})"[^>]*>([^<]*)', fetch(base) or "")
    def one(s):
        label = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s[1])))
        pack = re.search(r"[\[【]([A-Za-z0-9\-]+)[\]】]", label)
        out = []
        for b in (fetch(f"{base}?series={s[0]}") or "").split('<dl class="modalCol"')[1:]:
            i = re.search(r'id="([^"]+)"', b)
            info = re.search(r'infoCol">\s*<span>([^<]*)</span>\s*\|\s*<span>([^<]*)</span>', b)
            nm = re.search(r'cardName">([^<]*)<', b)
            if i and info and nm:
                rar = "SP" if info[2].startswith("SP") else info[2].strip()
                out.append({"id": i[1], "number": info[1].strip(), "rarity": rar, "name": html.unescape(nm[1]).strip(),
                            "promo": s[0].endswith(("901", "801")), "pack": pack[1] if pack else None,
                            "image": f"https://{host}/images/cardlist/card/{i[1]}.png"})
        return out
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, series))
    cards = {}
    for out in res:
        for c in out:
            cards.setdefault(c["id"], c)
    return cards

def self_host(cards):
    """The official site refuses to be embedded by other sites, so keep small WebP copies of its images in docs/img/o/."""
    from PIL import Image
    def one(c):
        m = re.match(r"https://(en|www)\.onepiece-cardgame\.com/images/cardlist/card/(.+)\.png$", c["image"] or "")
        if not m:
            return
        rel = f'img/o/{"en" if m[1] == "en" else "jp"}/{m[2]}.webp'
        path = os.path.join(ROOT, "docs", rel)
        if not os.path.exists(path):
            try:
                with urllib.request.urlopen(urllib.request.Request(c["image"], headers=UA), timeout=60) as r:
                    im = Image.open(io.BytesIO(r.read())).convert("RGB")
            except Exception:
                c["image"] = ""
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            im.thumbnail((320, 448))
            im.save(path, "WEBP", quality=80)
        c["image"] = rel
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(one, cards))

def off_variant(c):
    m = re.search(r"_([pr])(\d+)$", c["id"])
    label = ("Parallel" if m[1] == "p" else "Reprint") + ("" if m[2] == "1" else " " + m[2]) if m else "Standard"
    pack = c["pack"]
    if pack and not c["number"].startswith("P-") and c["number"].split("-")[0] not in pack.replace("-", ""):
        label = ("Reprint" if label == "Standard" else label) + f" ({pack})"
    return label

def off_card(c, game_lang, character, cid):
    prefix = c["number"].split("-")[0]
    return {
        "id": cid, "game": "onepiece", "lang": game_lang, "promo": c["promo"] or c["number"].startswith("P-"),
        "character": character, "name": c["name"], "number": c["number"],
        "set": "Promo" if prefix == "P" else SETNAMES.get(prefix, prefix), "rarity": c["rarity"],
        "finish": "Non-foil" if c["rarity"] in ("C", "UC") and "_" not in c["id"] else "Foil",
        "variant": off_variant(c), "image": c["image"], "price": None, "currency": "USD" if game_lang == "EN" else "JPY",
    }

def norm_id(i):
    """optcgapi writes promo prints as _prN where the official list uses _pN."""
    return re.sub(r"_pr(\d+)$", r"_p\1", re.sub(r"#\d+$", "", i.replace("promo:", "")))

def add_official_en(cards, off, names, aliases):
    """Add the cards optcgapi lacks. optcgapi's '#2' duplicates are dropped: the official list has the real card."""
    cards = [c for c in cards if "#" not in c["id"]]
    have = {(c["id"].startswith("promo:"), norm_id(c["id"])) for c in cards}
    for c in off.values():
        if c["name"] not in names and c["name"] not in aliases:
            continue
        if (c["promo"], norm_id(c["id"])) not in have:
            cards.append(off_card(c, "EN", aliases.get(c["name"], c["name"]), ("promo:" if c["promo"] else "") + c["id"]))
    return cards

def onepiece_jp_official(off, jp):
    words = {nfkc(w): ch for ch, ws in jp.items() for w in ws}
    return [off_card(c, "JP", words[nfkc(c["name"])], "jp:" + c["id"]) for c in off.values() if nfkc(c["name"]) in words]

def attach_jp_prices(cards, shop):
    """Prices from the shops, only where one shop entry clearly matches one official card."""
    theirs, mine = defaultdict(list), defaultdict(list)
    for s in shop:
        theirs[s["number"]].append(s)
    for c in cards:
        mine[c["number"]].append(c)
    for num, cs in mine.items():
        for kind in ("Standard", "Parallel"):
            a = [c for c in cs if c["variant"].startswith(kind)]
            b = [s for s in theirs[num] if s["variant"] == kind]
            if len(a) == 1 and len(b) == 1 and b[0]["price"]:
                a[0]["price"] = b[0]["price"]

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
    prev = json.load(open(OUT))["cards"] if os.path.exists(OUT) else []
    old = {c["id"]: c for c in prev}
    off_en, off_jp = official("en.onepiece-cardgame.com"), official("www.onepiece-cardgame.com")
    if len(off_en) < 1000 or len(off_jp) < 1000:
        sys.exit(f"official card list unavailable ({len(off_en)} EN, {len(off_jp)} JP cards); keeping previous data")
    names, aliases, jpn = set(cfg["onepiece"]), cfg.get("onepiece_aliases", {}), cfg.get("onepiece_jp", {})
    groups = {("onepiece", "EN"): add_official_en(onepiece_en(names, aliases), off_en, names, aliases)}
    jp = onepiece_jp_official(off_jp, jpn)
    attach_jp_prices(jp, onepiece_jp(jpn))
    groups[("onepiece", "JP")] = jp + cardrush_jp(jpn, jp)
    for c in pokemon(cfg["pokemon"]):
        groups.setdefault((c["game"], c["lang"]), []).append(c)
    cards = []
    for key, new in groups.items():
        before = [c for c in prev if (c["game"], c["lang"]) == key]
        if len(new) < 0.8 * len(before):
            print(f"warning: {key} returned {len(new)} cards (was {len(before)}); keeping previous")
            new = before
        cards += new
    self_host(cards)
    ov = json.load(open(os.path.join(ROOT, "image_overrides.json"))) if os.path.exists(os.path.join(ROOT, "image_overrides.json")) else {}
    for c in cards:
        if (not c["image"] or "noimage" in c["image"]) and c["id"] in ov:
            c["image"], c["imageApprox"] = ov[c["id"]], True
        c["finish"] = cfg.get("finish_overrides", {}).get(c["id"], c["finish"])
        was = old.get(c["id"], {})
        if c["price"] is None and was.get("price") is not None:  # a shop was unreachable: keep the last known price
            c["price"], c["currency"] = was["price"], was["currency"]
        c["prev_price"] = was.get("price")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": date.today().isoformat(), "characters": {
        "onepiece": cfg["onepiece"], "pokemon": [r["name"] for r in cfg["pokemon"]]}, "cards": cards},
        open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"{len(cards)} cards written")

if __name__ == "__main__":
    main()
