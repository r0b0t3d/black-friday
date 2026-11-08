import json
import time
import urllib.error
import urllib.request

COLORS = {"hot": 0xE74C3C, "good": 0x2ECC71, "ok": 0x3498DB}


def post(webhook: str, payload: dict):
    data = json.dumps(payload).encode()
    for _ in range(4):
        req = urllib.request.Request(webhook, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "bf-mac-watcher/1.0"})  # Discord/Cloudflare rejects the default Python UA
        try:
            urllib.request.urlopen(req, timeout=15).read()
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
                continue
            raise


def _money(x):
    return f"${x:,.0f}" if x is not None else "?"


def deal_payload(r, total):
    pct = r["pct"]
    color = COLORS["hot"] if (pct or 0) >= 25 else COLORS["good"] if (pct or 0) >= 15 else COLORS["ok"]
    head = f"{r['label']} — {_money(r['price'])}" + (f" ({pct:.0f}% {'below median' if r['ref_kind'] == 'median' else 'off'})" if pct is not None else "")
    median = r["ref_kind"] == "median"
    fields = [
        {"name": "Price", "value": _money(r["price"]), "inline": True},
        {"name": "Median price seen" if median else "Typical price",
         "value": _money(r["ref_price"] if median else r["msrp"]) if (r["ref_price"] if median else r["msrp"]) else "unknown",
         "inline": True},
        {"name": "You save", "value": _money(r["savings"]), "inline": True},
        {"name": "Retailer", "value": r["retailer"] or "see link", "inline": True},
        {"name": "Condition", "value": r["condition"], "inline": True},
        {"name": "Rank so far", "value": f"#{r['rn']} of {total}", "inline": True},
    ]
    return {"embeds": [{"title": head[:256], "url": r["url"] or None, "description": r["title"][:400],
                        "color": color, "fields": fields, "footer": {"text": f"via {r['source']}"}}]}


def digest_payload(rows, heading):
    lines = []
    for r in rows:
        pct = f"−{r['pct']:.0f}%" if r["pct"] is not None else "−?%"
        where = f" · {r['retailer']}" if r["retailer"] else ""
        cond = " · refurb" if r["condition"] != "new" else ""
        lines.append(f"**{r['rn']}.** {_money(r['price'])} ({pct}) {r['label']}{where}{cond} — [link]({r['url']})")
    return {"embeds": [{"title": heading, "description": "\n".join(lines)[:4000], "color": 0xF1C40F}]}
