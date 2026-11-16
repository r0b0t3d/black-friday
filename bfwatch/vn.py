"""Price watcher for products sold in Vietnam (e.g. Tammi Watch Kid Plus/Max).

Each watch lists store pages. A page price is read from schema.org JSON-LD,
falling back to a "price": NNN pattern. Tiki is read through its public search API.
A reading is saved only when the price or stock changes, so the table is a price history.
"""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from . import discord

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LD_RX = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
PRICE_RX = re.compile(r'"price"\s*:\s*"?(\d{5,9})(?:\.\d+)?')
AVAIL_RX = re.compile(r'"availability"\s*:\s*"[^"]*?(InStock|OutOfStock|SoldOut|PreOrder|Discontinued|LimitedAvailability)', re.I)
MIN_VND, MAX_VND = 100_000, 100_000_000  # sanity range for a kids' watch page


def vnd(x):
    return f"{x:,.0f}".replace(",", ".") + "₫" if x is not None else "?"


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def extract_price(html: str):
    """Return (price, in_stock|None) from a product page, or None."""
    for block in LD_RX.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in _walk(data):
            offers = node.get("offers") if isinstance(node, dict) else None
            for offer in (offers if isinstance(offers, list) else [offers] if offers else []):
                if not isinstance(offer, dict):
                    continue
                raw = offer.get("price") or offer.get("lowPrice")
                try:
                    price = float(str(raw).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if MIN_VND <= price <= MAX_VND:
                    avail = str(offer.get("availability", ""))
                    return price, (None if not avail else "InStock" in avail or "LimitedAvailability" in avail)
    for m in PRICE_RX.finditer(html):
        price = float(m.group(1))
        if MIN_VND <= price <= MAX_VND:
            a = AVAIL_RX.search(html)
            return price, (None if not a else a.group(1).lower() in ("instock", "limitedavailability"))
    return None


def tiki_cheapest(data: dict, match: str):
    """Cheapest Tiki search result whose name matches the regex."""
    best = None
    for it in data.get("data", []):
        if re.search(match, it.get("name", ""), re.I) and it.get("price"):
            if best is None or it["price"] < best[0]:
                best = (float(it["price"]), None, "https://tiki.vn/" + it.get("url_path", ""))
    return best


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9",
                                               "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def read_source(src, fetch=get):
    """-> (price, in_stock, url) or None."""
    if src.get("type") == "tiki":
        return tiki_cheapest(json.loads(fetch(src["url"])), src.get("match", "tammi"))
    got = extract_price(fetch(src["url"]))
    return (got[0], got[1], src["url"]) if got else None


def record(db, product, store, url, price, in_stock):
    """Save a reading if changed. Returns dict describing what changed, or None."""
    last = db.execute("SELECT * FROM vn_prices WHERE product=? AND store=? ORDER BY id DESC LIMIT 1",
                      (product, store)).fetchone()
    stock = None if in_stock is None else int(in_stock)
    if last and last["price"] == price and last["in_stock"] == stock:
        return None
    prior_low = db.execute("SELECT MIN(price) FROM vn_prices WHERE product=?", (product,)).fetchone()[0]
    db.execute("INSERT INTO vn_prices(product,store,url,price,in_stock,ts) VALUES(?,?,?,?,?,?)",
               (product, store, url, price, stock, datetime.now(timezone.utc).isoformat()))
    db.commit()
    if not last:
        return {"kind": "first", "price": price, "prev": None, "new_low": False}
    return {"kind": "change", "price": price, "prev": last["price"],
            "new_low": prior_low is not None and price < prior_low,
            "restock": stock == 1 and last["in_stock"] == 0}


def current(db, product):
    """Latest reading per store, cheapest first."""
    rows = db.execute("SELECT * FROM vn_prices WHERE product=? AND id IN "
                      "(SELECT MAX(id) FROM vn_prices WHERE product=? GROUP BY store) ORDER BY price", (product, product)).fetchall()
    return rows


def all_time_low(db, product):
    return db.execute("SELECT * FROM vn_prices WHERE product=? ORDER BY price, id LIMIT 1", (product,)).fetchone()


def alert_payload(product, store, url, ev, db):
    drop = (ev["prev"] - ev["price"]) / ev["prev"] * 100 if ev["prev"] else 0
    tags = []
    if drop > 0:
        tags.append(f"−{drop:.0f}%")
    if ev.get("new_low"):
        tags.append("lowest seen")
    if ev.get("restock"):
        tags.append("back in stock")
    cur = current(db, product)
    fields = [{"name": "Price", "value": vnd(ev["price"]), "inline": True},
              {"name": "Previous", "value": vnd(ev["prev"]), "inline": True},
              {"name": "Store", "value": store, "inline": True},
              {"name": "Cheapest now", "value": f"{vnd(cur[0]['price'])} at {cur[0]['store']}" if cur else "?", "inline": False}]
    return {"embeds": [{"title": f"{'📉' if drop > 0 else '📦'} {product} — {vnd(ev['price'])} ({', '.join(tags) or 'changed'})"[:256],
                        "url": url, "color": 0x2ECC71 if drop > 0 else 0x3498DB, "fields": fields}]}


# ---- discovering product pages from a brand/category listing -------------------------------------
SKIP_TOKENS = {"soncamedia", "mini", "xach", "tay", "di", "dong"}
STOP_TOKENS = {"bass", "bluetooth", "kem", "mau", "den", "trang", "xam", "do", "xanh", "vang", "hong"}


def model_key(url: str, after: str):
    """'.../loa-karaoke-xach-tay-acnos-cs448-100w-2-bass-16.5cm' -> 'cs448'; None if not a product slug."""
    slug = re.sub(r"\.html?$", "", url.split("?")[0].rstrip("/").rsplit("/", 1)[-1])
    m = re.search(rf"{after}-(.+)$", slug, re.I)
    if not m:
        return None
    toks = []
    for t in m.group(1).lower().split("-"):
        if t in STOP_TOKENS or re.fullmatch(r"\d+w|\d+(?:\.\d+)?cm|spac\d+", t):
            break
        if t not in SKIP_TOKENS:
            toks.append(t)
    if len(toks) > 1 and toks[-1].isdigit() and toks[-2].isdigit():  # 'quad 64 2' (2 = number of bass drivers)
        toks.pop()
    return " ".join(toks) or None


def wanted(key: str, w: dict) -> bool:
    """Optional `include` regex on the model key (e.g. only CS, S30, S60 models)."""
    return not w.get("include") or bool(re.search(w["include"], key, re.I))


def find_links(html: str, base: str, word: str):
    host = urlparse(base).netloc
    out, seen = [], set()
    for h in re.findall(r'href="([^"#]*)"', html):
        full = urljoin(base, h.split("?")[0])
        if word in full.lower() and urlparse(full).netloc == host and full not in seen and full != base:
            seen.add(full)
            out.append(full)
    return out


def expand(cfg, log, fetch):
    """All (product, store, source) to read this round, including discovered pages."""
    tasks = []
    for w in cfg.get("vn_watches", []):
        for src in w.get("sources", []):
            if src.get("enabled") is not False:
                tasks.append((w["name"], src["store"], src))
        for d in w.get("discover", []):
            if d.get("enabled") is False:
                continue
            try:
                html = fetch(d["url"])
            except Exception as e:
                log(f"! {w.get('title', 'discover')} @ {d['store']}: {e}")
                continue
            found = 0
            for link in find_links(html, d["url"], w["key_after"]):
                key = model_key(link, w["key_after"])
                if key and wanted(key, w):
                    found += 1
                    tasks.append((w["name_prefix"] + key.upper(), d["store"], {"store": d["store"], "url": link}))
            log(f"{w.get('title', 'discover')} @ {d['store']}: {found} product pages found")
    return tasks


def collect(cfg, db, log=print, fetch=get):
    tasks = expand(cfg, log, fetch)

    def read(task):
        try:
            return task, read_source(task[2], fetch), None
        except Exception as e:
            return task, None, e

    with ThreadPoolExecutor(cfg.get("vn_threads", 8)) as ex:
        results = list(ex.map(read, tasks))
    best = {}  # one reading per (product, store): colour variants of a page -> keep the cheapest
    for (product, store, src), got, err in results:
        if err or not got:
            log(f"! {product} @ {store}: {err or 'no price found'}")
            continue
        if (product, store) not in best or got[0] < best[(product, store)][0]:
            best[(product, store)] = got
    alerts = []
    for (product, store), (price, stock, url) in best.items():
        ev = record(db, product, store, url, price, stock)
        if ev and ev["kind"] == "change":
            drop = (ev["prev"] - price) / ev["prev"] * 100
            if drop >= cfg.get("vn_min_drop_pct", 3) or ev["new_low"] and price < ev["prev"] or ev["restock"]:
                alerts.append((product, store, url, ev))
    return alerts


# ---- Discord summary ----------------------------------------------------------------------------
def sections(db, cfg):
    """[(title, [product names])] for the summary."""
    out = []
    for w in cfg.get("vn_watches", []):
        if "name_prefix" in w:
            names = [r[0] for r in db.execute("SELECT DISTINCT product FROM vn_prices WHERE product LIKE ? ORDER BY product",
                                              (w["name_prefix"] + "%",))]
            names = [n for n in names if wanted(n[len(w["name_prefix"]):], w)]
            out.append((w.get("title", w["name_prefix"].strip()), names, w["name_prefix"]))
        else:
            out.append((w["name"], [w["name"]], ""))
    return out


def _line(db, product, prefix):
    cur = current(db, product)
    if not cur:
        return None
    c = cur[0]
    line = f"**{product[len(prefix):] if prefix else product}** {vnd(c['price'])} [{c['store']}]({c['url']})"
    line += " · hết hàng" if c["in_stock"] == 0 else ""
    others = [f"{vnd(r['price'])} {r['store']}" for r in cur[1:4]]
    if others:
        line += " · " + " / ".join(others)
    low = all_time_low(db, product)
    if low["price"] < c["price"]:
        line += f" · _low {vnd(low['price'])}_"
    return line


def digest_payloads(db, cfg, budget=5200):
    """Summary split into messages that fit Discord's limits (6000 chars of embeds, 10 embeds)."""
    embeds = []
    for title, names, prefix in sections(db, cfg):
        lines = [l for l in (_line(db, n, prefix) for n in names) if l]
        chunk, size, part = [], 0, 1
        for l in lines + [None]:
            if l is None or size + len(l) > 3800:
                if chunk:
                    embeds.append({"title": f"🇻🇳 {title}" + (f" ({part})" if part > 1 or l else ""),
                                   "description": "\n".join(chunk), "color": 0xE67E22})
                    part += 1
                chunk, size = [], 0
            if l:
                chunk.append(l)
                size += len(l) + 1
    payloads, cur, size = [], [], 0
    for e in embeds:
        n = len(e["title"]) + len(e["description"])
        if cur and (size + n > budget or len(cur) >= 10):
            payloads.append({"embeds": cur})
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        payloads.append({"embeds": cur})
    return payloads


def cycle(cfg, db, webhook, store_mod, dry=False, log=print, fetch=get):
    now = datetime.now(timezone.utc)
    last_poll = store_mod.meta_get(db, "vn_poll")
    if last_poll and now - datetime.fromisoformat(last_poll) < timedelta(hours=cfg.get("vn_poll_hours", 3)):
        return  # prices here change slowly; poll less often than the Mac feeds
    alerts = collect(cfg, db, log, fetch)
    store_mod.meta_set(db, "vn_poll", now.isoformat())
    for product, shop, url, ev in alerts:
        if dry or not webhook:
            log("VN DEAL", product, shop, ev["price"], "was", ev["prev"], url)
        else:
            discord.post(webhook, alert_payload(product, shop, url, ev, db))
            time.sleep(1)
    last = store_mod.meta_get(db, "vn_digest")
    if not last or now - datetime.fromisoformat(last) >= timedelta(hours=cfg.get("vn_digest_hours", 24)):
        payloads = digest_payloads(db, cfg)
        if payloads:  # the first run sends one too: current prices are what you asked for
            for pl in payloads:
                if dry or not webhook:
                    for e in pl["embeds"]:
                        print(e["title"] + "\n" + e["description"])
                else:
                    discord.post(webhook, pl)
                    time.sleep(1)
            store_mod.meta_set(db, "vn_digest", now.isoformat())
