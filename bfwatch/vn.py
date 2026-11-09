"""Price watcher for products sold in Vietnam (e.g. Tammi Watch Kid Plus/Max).

Each watch lists store pages. A page price is read from schema.org JSON-LD,
falling back to a "price": NNN pattern. Tiki is read through its public search API.
A reading is saved only when the price or stock changes, so the table is a price history.
"""
import json
import re
import urllib.request
from datetime import datetime, timezone

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


def digest_payload(db, products):
    fields = []
    for product in products:
        cur = current(db, product)
        if not cur:
            continue
        lines = [f"{'**' if i == 0 else ''}{vnd(r['price'])} — [{r['store']}]({r['url']}){'**' if i == 0 else ''}"
                 f"{' · hết hàng' if r['in_stock'] == 0 else ''}" for i, r in enumerate(cur)]
        low = all_time_low(db, product)
        lines.append(f"_Lowest seen: {vnd(low['price'])} at {low['store']} ({low['ts'][:10]})_")
        fields.append({"name": product, "value": "\n".join(lines)[:1024], "inline": False})
    return {"embeds": [{"title": "🇻🇳 Giá hiện tại / Current prices", "color": 0xE67E22, "fields": fields}]} if fields else None


def collect(cfg, db, log=print, fetch=get):
    alerts = []
    for w in cfg.get("vn_watches", []):
        for src in w["sources"]:
            if src.get("enabled") is False:
                continue
            try:
                got = read_source(src, fetch)
            except Exception as e:
                log(f"! {w['name']} @ {src['store']}: {e}")
                continue
            if not got:
                log(f"! {w['name']} @ {src['store']}: no price found")
                continue
            price, stock, url = got
            ev = record(db, w["name"], src["store"], url, price, stock)
            if ev and ev["kind"] == "change":
                drop = (ev["prev"] - price) / ev["prev"] * 100
                if drop >= cfg.get("vn_min_drop_pct", 3) or ev["new_low"] and price < ev["prev"] or ev["restock"]:
                    alerts.append((w["name"], src["store"], url, ev))
    return alerts


def cycle(cfg, db, webhook, store_mod, dry=False, log=print, fetch=get):
    from datetime import timedelta
    alerts = collect(cfg, db, log, fetch)
    for product, shop, url, ev in alerts:
        payload = alert_payload(product, shop, url, ev, db)
        if dry or not webhook:
            log("VN DEAL", product, shop, ev["price"], "was", ev["prev"], url)
        else:
            discord.post(webhook, payload)
    last = store_mod.meta_get(db, "vn_digest")
    due = timedelta(hours=cfg.get("vn_digest_hours", 24))
    if not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) >= due:
        payload = digest_payload(db, [w["name"] for w in cfg.get("vn_watches", [])])
        if payload:  # first run sends one too: you asked for the current prices
            if dry or not webhook:
                for f in payload["embeds"][0]["fields"]:
                    print(f["name"], "\n" + f["value"])
            else:
                discord.post(webhook, payload)
            store_mod.meta_set(db, "vn_digest", datetime.now(timezone.utc).isoformat())
