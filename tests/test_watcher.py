import json, os, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bfwatch import cli, discord, store
from bfwatch.feeds import parse_feed
from bfwatch.parse import parse_title

B = json.loads((Path(__file__).resolve().parent.parent / "baselines.json").read_text())
P = lambda t: parse_title(t, B)


class ParseTests(unittest.TestCase):
    def test_full_config_price(self):
        d = P("Apple Mac mini M4 (16GB/256GB) $479 + Free Shipping @ Amazon")
        self.assertEqual((d.product, d.chip, d.ram, d.ssd, d.price, d.msrp, d.retailer),
                         ("Mac mini", "M4", 16, 256, 479, 599, "Amazon"))
        self.assertEqual(d.pct, 20.0)

    def test_dollars_off_only(self):
        d = P("Apple MacBook Pro 14-inch M4 Pro 24GB 512GB: $300 off at Best Buy")
        self.assertEqual((d.price, d.msrp, d.pct, d.size), (1699, 1999, 15.0, 14))

    def test_was_price_ignored(self):
        self.assertEqual(P("Mac mini M4 16GB 256GB was $599, now $499 at B&H").price, 499)

    def test_percent_off(self):
        self.assertEqual(P("Mac Studio M4 Max 36GB/512GB 10% off at Apple").price, 1799.1)

    def test_vague_title_uses_base_config(self):
        self.assertEqual(P("Mac mini M4 $499").msrp, 599)

    def test_unknown_size_uses_cheapest_msrp(self):
        d = P("MacBook Pro M4 Max $2999")  # 14" ($3199) vs 16" ($3499): never overstate
        self.assertEqual((d.msrp, d.pct), (3199, 6.3))

    def test_unknown_chip_has_no_baseline(self):
        d = P("Mac mini M9 $999")
        self.assertIsNone(d.pct); self.assertIsNone(d.savings)

    def test_rejects(self):
        for t in ["Apple Studio Display $1299", "MacBook Air M4 $799", "Mac mini case $25",
                  "Mac mini M4 £499", "Mac mini is great", "Mac mini M4 $19 gift"]:
            self.assertIsNone(P(t), t)

    def test_absurd_discount_rejected(self):
        self.assertIsNone(P("Mac Studio M4 Max 36GB/512GB $550"))

    def test_iphone(self):
        d = P("Apple iPhone 17 Pro Max 256GB Unlocked $1,049 at Amazon")
        self.assertEqual((d.product, d.chip, d.ssd, d.ram, d.price, d.retailer), ("iPhone", "17 Pro Max", 256, None, 1049, "Amazon"))
        self.assertEqual(d.config_label(), "iPhone 17 Pro Max 256GB")
        self.assertIsNone(d.msrp)  # no list price known: judged against median / stated savings
        self.assertEqual(P("iPhone Air 256GB $100 off at Best Buy").savings, 100)
        self.assertEqual(P("iPhone 16e 128GB $549 Walmart").ssd, 128)   # 128GB is storage, not RAM
        self.assertEqual(P("Apple iPhone 17 512GB $200 off + $100 Amazon gift card").savings, 200)

    def test_iphone_rejects(self):
        for t in ["iPhone 17 Pro Max free w/ trade-in at Verizon $0", "iPhone 17 256GB new line required $799",
                  "iPhone 17 Pro case $29.99", "iPhone 17 Pro MagSafe screen protector $400", "iPhone charger $35",
                  "iPhone 17 256GB $20/mo for 36 months", "128GB Apple iPhone 16 Pro for T-Mobile + $300 Walmart eGift Card"]:
            self.assertIsNone(P(t), t)

    def test_refurb_flag(self):
        self.assertEqual(P("Refurbished Mac mini M4 16GB 256GB $469").condition, "refurbished")


FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Apple Mac mini M4 (16GB/256GB) $449 @ Amazon</title><link>https://x.test/1</link><guid>g1</guid>
<pubDate>Fri, 28 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Apple MacBook Pro 14&quot; M4 Pro 24GB/512GB $1,499 at Best Buy</title><link>https://x.test/2</link><guid>g2</guid></item>
<item><title>Mac mini M4 16GB/256GB $449 at Amazon (dup)</title><link>https://x.test/3</link><guid>g3</guid></item>
<item><title>Unrelated toaster $20</title><link>https://x.test/4</link><guid>g4</guid></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>[Costco] Mac Studio M4 Max 36GB/512GB $1599</title>
<link href="https://r.test/a"/><id>t3_a</id><updated>2026-11-28T10:00:00+00:00</updated></entry></feed>"""


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.cfg = {"baselines": B, "sources": [{"name": "t", "url": "u"}], "max_age_hours": 10**6}

    def test_feed_parsing(self):
        self.assertEqual(len(parse_feed(FEED)), 4)
        e = parse_feed(ATOM)[0]
        self.assertEqual((e["link"], e["id"]), ("https://r.test/a", "t3_a"))

    def test_collect_dedupe_rank_notify(self):
        sent = []
        orig = discord.post
        discord.post = lambda url, payload: sent.append(payload)
        try:
            n = cli.collect(self.cfg, self.db, fetch=lambda u: parse_feed(FEED) + parse_feed(ATOM))
            self.assertEqual(n, 3)  # duplicate fingerprint + toaster dropped
            rows = store.ranked(self.db)
            self.assertEqual([r["rn"] for r in rows], [1, 2, 3])
            self.assertEqual(rows[0]["pct"], 25.0)  # mac mini 449 vs 599
            cli.notify(self.cfg, self.db, "http://hook")
            self.assertEqual(len(sent), 3)
            cli.notify(self.cfg, self.db, "http://hook")  # nothing re-sent
            self.assertEqual(len(sent), 3)
            self.assertIn("#1 of 3", json.dumps(sent[0]))
        finally:
            discord.post = orig

    def test_notification_cap_defers_rest(self):
        self.cfg["max_notifications_per_cycle"] = 1
        sent = []
        discord.post, orig = (lambda u, p: sent.append(p)), discord.post
        try:
            cli.collect(self.cfg, self.db, fetch=lambda u: parse_feed(FEED))
            cli.notify(self.cfg, self.db, "http://hook"); self.assertEqual(len(sent), 1)
            cli.notify(self.cfg, self.db, "http://hook"); self.assertEqual(len(sent), 2)
        finally:
            discord.post = orig

    def test_median_fallback_for_unknown_chip(self):
        prices = [("Amazon", 780), ("Best Buy", 790), ("Costco", 770), ("Micro Center", 650)]
        for i, (shop, price) in enumerate(prices):
            t = f"Mac mini M6 16GB/256GB ${price} at {shop}"
            e = {"id": str(i), "title": t, "link": f"https://x.test/{i}", "published": datetime.now(timezone.utc)}
            store.add_deal(self.db, P(t), e, "t", notified=True)  # stored silently, as on a first sighting
        store.rescore_by_median(self.db)
        rows = store.ranked(self.db)
        self.assertEqual([x["price"] for x in rows], [650, 770])  # only sub-median prices are ranked
        r = rows[0]
        self.assertFalse(cli.qualifies(rows[1], {}))  # 0.6% below median: ranked, but no alert
        self.assertEqual((r["price"], r["ref_kind"], r["ref_price"]), (650, "median", 775))
        self.assertTrue(cli.qualifies(r, {}))
        self.assertEqual(len(store.pending(self.db)), 2)  # re-armed; notify() filters by qualifies
        self.assertIn("below median", json.dumps(discord.deal_payload(r, 1)))

    def test_median_needs_enough_observations(self):
        for i, price in enumerate([780, 650]):
            t = f"Mac mini M6 16GB/256GB ${price} at Amazon{i}"
            e = {"id": str(i), "title": t, "link": "u", "published": None}
            store.add_deal(self.db, P(t), e, "t")
        store.rescore_by_median(self.db)
        self.assertEqual(store.ranked(self.db), [])

    def test_bad_source_does_not_crash(self):
        def boom(u): raise OSError("down")
        self.assertEqual(cli.collect(self.cfg, self.db, fetch=boom), 0)


from bfwatch import vn

LD_PAGE = """<html><script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer","price":"1110000",
"priceCurrency":"VND","availability":"https://schema.org/InStock"}}</script></html>"""
RAW_PAGE = '<script>var x={"price":1320000.0,"priceCurrency":"VND"}</script>'
TIKI = json.dumps({"data": [{"name": "Dong ho AMA Watch LT31", "price": 900000, "url_path": "a.html"},
                           {"name": "Tammi Watch Kid Max GPS", "price": 1355000, "url_path": "max.html"},
                           {"name": "Tammi Watch Kid Max (box)", "price": 1299000, "url_path": "max2.html"}]})


class VnTests(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(vn.extract_price(LD_PAGE), (1110000.0, True))
        self.assertEqual(vn.extract_price(RAW_PAGE), (1320000.0, None))
        self.assertIsNone(vn.extract_price('<p>"price":"12" nothing</p>'))

    def test_tiki_picks_cheapest_match(self):
        self.assertEqual(vn.tiki_cheapest(json.loads(TIKI), "tammi.*kid.*max"), (1299000.0, None, "https://tiki.vn/max2.html"))

    def test_collect_alerts_on_drop_and_restock(self):
        db = store.connect(":memory:")
        cfg = {"vn_watches": [{"name": "Tammi Watch Kid Max", "sources": [
            {"store": "A", "url": "http://a"}, {"store": "Dead", "url": "http://dead"}]}]}
        page = {"v": LD_PAGE}
        def fetch(u):
            if "dead" in u: raise OSError("blocked")
            return page["v"]
        self.assertEqual(vn.collect(cfg, db, lambda *a: None, fetch), [])      # first sighting: baseline only
        self.assertEqual(vn.collect(cfg, db, lambda *a: None, fetch), [])      # unchanged: nothing stored/alerted
        page["v"] = LD_PAGE.replace("1110000", "990000")
        (product, shop, url, ev), = vn.collect(cfg, db, lambda *a: None, fetch)
        self.assertEqual((ev["prev"], ev["price"], ev["new_low"]), (1110000, 990000, True))
        self.assertIn("lowest seen", json.dumps(vn.alert_payload(product, shop, url, ev, db), ensure_ascii=False))
        page["v"] = LD_PAGE.replace("1110000", "1000000")                       # +1% : no alert
        self.assertEqual(vn.collect(cfg, db, lambda *a: None, fetch), [])
        (d,) = vn.digest_payloads(db, cfg)
        self.assertIn("1.000.000₫", json.dumps(d, ensure_ascii=False))
        self.assertIn("990.000₫", json.dumps(d, ensure_ascii=False))           # lowest seen

    def test_model_keys(self):
        k = lambda u: vn.model_key(u, "acnos")
        self.assertEqual(k("https://x/loa-karaoke-xach-tay-mini-acnos-cs448-100w-2-bass-16.5cm-150w.html"), "cs448")
        self.assertEqual(k("https://x/loa-karaoke-acnos-soncamedia-cs201p.html"), "cs201p")
        self.assertEqual(k("https://x/loa-karaoke-xach-tay-acnos-quad-64-2-bass-16cm-180w"), "quad 64")
        self.assertEqual(k("https://x/loa-karaoke-di-dong-acnos-flac-36-ultimate-mau-den"), "flac 36 ultimate")
        self.assertEqual(k("https://x/loa-xach-tay-karaoke-acnos-acoustic-3.html"), "acoustic 3")
        self.assertIsNone(k("https://x/thiet-bi-am-thanh/loa/karaoke/acnos.html"))   # category page
        self.assertIsNone(k("https://x/loa-karaoke-xach-tay-acnos"))

    def test_discover_group_and_cheapest_variant(self):
        listing = ('<a href="/loa-karaoke-xach-tay-acnos">cat</a>'
                   '<a href="/p/loa-karaoke-acnos-s30-mau-den">a</a><a href="/p/loa-karaoke-acnos-s30-mau-trang">b</a>'
                   '<a href="/p/loa-karaoke-acnos-cs201plus?x=1">c</a><a href="https://other.com/acnos-zz">d</a>')
        pages = {"http://shop.vn/loa-karaoke-xach-tay-acnos": listing,
                 "http://shop.vn/p/loa-karaoke-acnos-s30-mau-den": '<x>"price":1500000</x>',
                 "http://shop.vn/p/loa-karaoke-acnos-s30-mau-trang": '<x>"price":1400000</x>',
                 "http://shop.vn/p/loa-karaoke-acnos-cs201plus": '<x>"price":3990000</x>'}
        cfg = {"vn_watches": [{"name": "Acnos", "title": "Acnos speakers", "name_prefix": "Acnos ", "key_after": "acnos",
                               "discover": [{"store": "Shop", "url": "http://shop.vn/loa-karaoke-xach-tay-acnos"}]}]}
        db = store.connect(":memory:")
        vn.collect(cfg, db, lambda *a: None, lambda u: pages[u])
        got = {r["product"]: r["price"] for n in ("Acnos S30", "Acnos CS201PLUS") for r in vn.current(db, n)}
        self.assertEqual(got, {"Acnos S30": 1400000.0, "Acnos CS201PLUS": 3990000.0})
        (d,) = vn.digest_payloads(db, cfg)
        self.assertIn("**S30** 1.400.000₫", d["embeds"][0]["description"])

    def test_include_filter(self):
        w = {"include": r"^(cs\d|s30\b|s60\b)"}
        for k in ("cs201plus", "cs160 pro", "s30", "s30 ver 2", "s60"):
            self.assertTrue(vn.wanted(k, w), k)
        for k in ("cps 16s", "flac 36", "dauris 200", "acoustic 3", "s600"):
            self.assertFalse(vn.wanted(k, w), k)

    def test_digest_splits_into_messages(self):
        db = store.connect(":memory:")
        cfg = {"vn_watches": [{"name": "Acnos", "name_prefix": "Acnos ", "key_after": "acnos", "discover": []}]}
        for i in range(60):
            vn.record(db, f"Acnos M{i}", "Shop", "https://shop.vn/" + "x" * 90, 1000000 + i, True)
        ps = vn.digest_payloads(db, cfg)
        self.assertGreater(len(ps), 1)
        for p in ps:
            self.assertLessEqual(sum(len(e["title"]) + len(e["description"]) for e in p["embeds"]), 6000)
            self.assertLessEqual(len(p["embeds"]), 10)


if __name__ == "__main__":
    unittest.main()
