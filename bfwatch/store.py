import hashlib
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals(
  id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, entry_id TEXT, source TEXT,
  title TEXT, url TEXT, product TEXT, label TEXT, condition TEXT, retailer TEXT,
  price REAL, msrp REAL, savings REAL, pct REAL,
  first_seen TEXT, published TEXT, notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
# best deal first: known % off, then $ saved; unknown-baseline deals sink to the bottom
ORDER = "pct IS NULL, pct DESC, savings DESC"


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
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
