"""Black Friday Mac deal watcher.

  python -m bfwatch run            poll forever, notify Discord, daily digest
  python -m bfwatch once           one poll cycle (use with cron)
  python -m bfwatch rank [-n 20]   print the leaderboard of every deal collected
  python -m bfwatch digest         post the leaderboard to Discord now
  python -m bfwatch test-discord   send a test message
  python -m bfwatch parse "title"  see how a deal title is interpreted
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import discord, store
from .feeds import fetch_feed
from .parse import parse_title

ROOT = Path(__file__).resolve().parent.parent


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    cfg["baselines"] = json.loads((Path(path).parent / cfg.get("baselines_file", "baselines.json")).read_text())
    return cfg


def collect(cfg, db, fetch=fetch_feed):
    """Poll sources, store every Mac deal seen. Returns count of new deals."""
    now = datetime.now(timezone.utc)
    max_age = timedelta(hours=cfg.get("max_age_hours", 48))
    added = 0
    for src in cfg["sources"]:
        try:
            entries = fetch(src["url"])
        except Exception as e:  # one dead source must not stop the others
            log(f"! {src['name']}: {e}")
            continue
        parsed = skipped = 0
        for e in entries:
            deal = parse_title(e["title"], cfg["baselines"])
            parsed += bool(deal)
            if not deal and cfg.get("debug") is not False and skipped < 3:
                skipped += 1
                log(f"  unparsed: {e['title'][:90]}")
            if not deal or (deal.condition != "new" and not cfg.get("include_refurbished", False)):
                continue
            stale = e["published"] is not None and now - e["published"] > max_age
            if store.add_deal(db, deal, e, src["name"], notified=stale):
                added += 1
        log(f"{src['name']}: {len(entries)} entries, {parsed} parsed as Mac deals")
    return added


def qualifies(r, cfg):
    return ((r["pct"] or 0) >= cfg.get("min_discount_pct", 10)
            or (r["savings"] or 0) >= cfg.get("min_savings_usd", 100))


def notify(cfg, db, webhook, dry=False):
    ranks = {r["id"]: r for r in store.ranked(db)}
    sent = []
    for r in store.pending(db):
        if r["id"] not in ranks or not qualifies(r, cfg):
            sent.append(r["id"])  # not worth a ping; stays in the leaderboard
            continue
        if len([s for s in sent if s in ranks and qualifies(ranks[s], cfg)]) >= cfg.get("max_notifications_per_cycle", 8):
            break  # rest go out next cycle
        payload = discord.deal_payload(ranks[r["id"]], len(ranks))
        if dry or not webhook:
            log("DEAL", ranks[r["id"]]["label"], r["price"], f"{r['pct']}%", r["url"])
        else:
            discord.post(webhook, payload)
            time.sleep(1)
        sent.append(r["id"])
    store.mark_notified(db, sent)


def send_digest(cfg, db, webhook, dry=False):
    rows = store.ranked(db, top=cfg.get("digest_size", 10))
    if not rows:
        return
    payload = discord.digest_payload(rows, f"🏆 Top Mac deals so far ({len(store.ranked(db))} tracked)")
    if dry or not webhook:
        print(payload["embeds"][0]["description"])
    else:
        discord.post(webhook, payload)
    store.meta_set(db, "last_digest", datetime.now(timezone.utc).isoformat())


def cycle(cfg, db, webhook, dry=False):
    n = collect(cfg, db)
    log(f"poll done, {n} new deals")
    notify(cfg, db, webhook, dry)
    last = store.meta_get(db, "last_digest")
    due = timedelta(hours=cfg.get("digest_hours", 24))
    if not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) >= due:
        if last:  # skip the digest on the very first run; it would just repeat the pings
            send_digest(cfg, db, webhook, dry)
        else:
            store.meta_set(db, "last_digest", datetime.now(timezone.utc).isoformat())


def print_rank(db, top, product):
    rows = store.ranked(db, top=top, product=product)
    if not rows:
        print("nothing collected yet")
    for r in rows:
        pct = f"{r['pct']:5.1f}%" if r["pct"] is not None else "   ?  "
        price = f"${r['price']:>7,.0f}" if r["price"] else "      ?"
        print(f"{r['rn']:>3}. {pct} {price}  {r['label']:<34} {(r['retailer'] or '-'):<12} {r['condition']:<5} {r['url']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bfwatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["run", "once", "rank", "digest", "test-discord", "parse"])
    p.add_argument("title", nargs="?")
    p.add_argument("-c", "--config", default=str(ROOT / "config.json"))
    p.add_argument("-n", "--top", type=int, default=20)
    p.add_argument("--product")
    p.add_argument("--dry-run", action="store_true", help="print instead of posting to Discord")
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if a.cmd == "parse":
        print(parse_title(a.title or "", cfg["baselines"]))
        return
    db = store.connect(os.environ.get("BF_DB", str(ROOT / "deals.db")))
    if a.cmd in ("run", "once", "digest", "test-discord") and not webhook and not a.dry_run:
        sys.exit("Set DISCORD_WEBHOOK_URL (or pass --dry-run).")
    if a.cmd == "rank":
        print_rank(db, a.top, a.product)
    elif a.cmd == "test-discord":
        discord.post(webhook, {"content": "✅ Black Friday Mac watcher is connected."})
    elif a.cmd == "digest":
        send_digest(cfg, db, webhook, a.dry_run)
    elif a.cmd == "once":
        cycle(cfg, db, webhook, a.dry_run)
    else:
        log("watching; poll every", cfg.get("poll_minutes", 15), "min")
        while True:
            try:
                cycle(cfg, db, webhook, a.dry_run)
            except Exception as e:
                log("! cycle failed:", e)
            time.sleep(cfg.get("poll_minutes", 15) * 60)
