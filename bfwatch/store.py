import hashlib
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals(
  id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, entry_id TEXT, source TEXT,
  title TEXT, url TEXT, product TEXT, label TEXT, condition TEXT, retailer TEXT,
  price REAL, msrp REAL, savings REAL, pct REAL,
  first_seen TEXT, published TEXT, notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS auto_baseline(
  label TEXT, condition TEXT, price REAL, ts TEXT, PRIMARY KEY(label, condition));
CREATE TABLE IF NOT EXISTS vn_prices(
  id INTEGER PRIMARY KEY, product TEXT, store TEXT, url TEXT,
  price REAL, in_stock INTEGER, ts TEXT);
"""
# best deal first: known % off, then $ saved; unknown-baseline deals sink to the bottom
ORDER = "pct IS NULL, pct DESC, savings DESC"


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(deals)")}
    for col, typ in (("ref_price", "REAL"), ("ref_kind", "TEXT")):  # migrate older databases
        if col not in cols:
            db.execute(f"ALTER TABLE deals ADD COLUMN {col} {typ}")
    db.commit()
    return db


def fingerprint(d):
    key = "|".join(str(x) for x in (d.product, d.chip, d.size, d.ram, d.ssd, d.condition, d.retailer, d.price))
    return hashlib.sha1(key.encode()).hexdigest()


def add_deal(db, deal, entry, source, notified=False):
    """Insert if unseen. Returns row id, or None if duplicate."""
    cur = db.execute(
        "INSERT OR IGNORE INTO deals(fingerprint,entry_id,source,title,url,product,label,condition,"
        "retailer,price,msrp,savings,pct,first_seen,published,notified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fingerprint(deal), entry["id"], source, entry["title"], entry["link"], deal.product,
         deal.config_label(), deal.condition, deal.retailer, deal.price, deal.msrp, deal.savings,
         deal.pct, datetime.now(timezone.utc).isoformat(),
         entry["published"].isoformat() if entry["published"] else None, int(notified)))
    db.commit()
    return cur.lastrowid if cur.rowcount else None


def ranked(db, top=None, product=None):
    """All ranked deals with an overall position (rn), optionally filtered."""
    rows = db.execute(
        f"SELECT *, ROW_NUMBER() OVER (ORDER BY {ORDER}) AS rn FROM deals "
        "WHERE pct IS NOT NULL OR savings IS NOT NULL").fetchall()
    if product:
        rows = [r for r in rows if r["product"].lower() == product.lower()]
    return rows[:top] if top else rows


def pending(db):
    return db.execute(f"SELECT * FROM deals WHERE notified=0 ORDER BY {ORDER}").fetchall()


def mark_notified(db, ids):
    db.executemany("UPDATE deals SET notified=1 WHERE id=?", [(i,) for i in ids])
    db.commit()


def meta_get(db, k):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else None


def meta_set(db, k, v):
    db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    db.commit()


def rescore_by_median(db, min_obs=3, max_pct=60, rearm_hours=48, exclude=()):
    """Fallback for configs with no list price: compare against the median price
    seen so far for the same config + condition. Needs >= min_obs sightings.
    Deals newly scored and still fresh are re-armed for notification."""
    rows = db.execute(
        "SELECT id,label,condition,price,pct,published,first_seen FROM deals "
        "WHERE msrp IS NULL AND price IS NOT NULL AND (label LIKE '%GB%' OR label LIKE '%TB%') "
        "AND (savings IS NULL OR ref_kind='median')").fetchall()
    rows = [r for r in rows if db.execute("SELECT product FROM deals WHERE id=?", (r["id"],)).fetchone()[0] not in exclude]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["label"], r["condition"])].append(r)
    now = datetime.now(timezone.utc)
    for g in groups.values():
        if len(g) < min_obs:
            continue
        ref = statistics.median(r["price"] for r in g)
        for r in g:
            pct = (ref - r["price"]) / ref * 100
            if 0 < pct <= max_pct:
                db.execute("UPDATE deals SET pct=?, savings=?, ref_price=?, ref_kind='median' WHERE id=?",
                           (round(pct, 1), round(ref - r["price"], 2), ref, r["id"]))
                seen = datetime.fromisoformat(r["published"] or r["first_seen"])
                if r["pct"] is None and now - seen <= timedelta(hours=rearm_hours):
                    db.execute("UPDATE deals SET notified=0 WHERE id=?", (r["id"],))
            else:
                db.execute("UPDATE deals SET pct=NULL, savings=NULL, ref_price=?, ref_kind='median' WHERE id=?",
                           (ref, r["id"]))
    db.commit()


def rescore_first_seen(db, products, learn_hours=12, max_pct=60):
    """For products with no list price: the price when a config is first seen is its baseline.
    During the first `learn_hours` the baseline follows the cheapest price seen (so it means
    "the best price you could pay today"); after that it is frozen and cheaper listings score
    as discounts against it."""
    if not products:
        return
    marks = ",".join("?" * len(products))
    rows = db.execute(
        f"SELECT id,label,condition,price,first_seen FROM deals WHERE product IN ({marks}) AND msrp IS NULL "
        "AND price IS NOT NULL AND (label LIKE '%GB%' OR label LIKE '%TB%') "
        "AND (savings IS NULL OR ref_kind='first_seen')", list(products)).fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[(r["label"], r["condition"])].append(r)
    now = datetime.now(timezone.utc)
    for (label, cond), g in groups.items():
        low = min(r["price"] for r in g)
        b = db.execute("SELECT price, ts FROM auto_baseline WHERE label=? AND condition=?", (label, cond)).fetchone()
        if b is None:
            started = min(r["first_seen"] for r in g)  # a config seen days ago is already past its learning window
            db.execute("INSERT INTO auto_baseline VALUES(?,?,?,?)", (label, cond, low, started))
            base, learning = low, now - datetime.fromisoformat(started) < timedelta(hours=learn_hours)
        else:
            base = b["price"]
            learning = now - datetime.fromisoformat(b["ts"]) < timedelta(hours=learn_hours)
            if learning and low < base:
                base = low
                db.execute("UPDATE auto_baseline SET price=? WHERE label=? AND condition=?", (low, label, cond))
        for r in g:
            pct = (base - r["price"]) / base * 100
            if not learning and 0 < pct <= max_pct:
                db.execute("UPDATE deals SET pct=?, savings=?, ref_price=?, ref_kind='first_seen' WHERE id=?",
                           (round(pct, 1), round(base - r["price"], 2), base, r["id"]))
            else:
                db.execute("UPDATE deals SET pct=NULL, savings=NULL, ref_price=?, ref_kind='first_seen' WHERE id=?",
                           (base, r["id"]))
    db.commit()
