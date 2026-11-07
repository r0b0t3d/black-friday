"""Fetch + parse RSS 2.0 / Atom feeds with the stdlib only."""
import html
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (compatible; bf-mac-watcher/1.0)"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(data: bytes):
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            name = _local(c.tag)
            if name == "link":
                if c.get("href") and (c.get("rel") in (None, "alternate")):
                    f.setdefault("link", c.get("href"))
                elif c.text and c.text.strip():
                    f.setdefault("link", c.text.strip())
            elif name in ("title", "id", "guid", "pubDate", "published", "updated"):
                f.setdefault(name, (c.text or "").strip())
        title = html.unescape(f.get("title", ""))
        link = f.get("link", "")
        out.append({
            "id": f.get("guid") or f.get("id") or link or title,
            "title": title,
            "link": link,
            "published": _date(f.get("pubDate") or f.get("published") or f.get("updated")),
        })
    return out


def fetch_feed(url: str, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_feed(r.read())
