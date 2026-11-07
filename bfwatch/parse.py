"""Turn a deal-feed title into a structured Mac deal (or None if it isn't one)."""
import html
import re
from dataclasses import dataclass

PRODUCTS = [
    ("Mac mini", r"mac\s*mini"),
    ("MacBook Pro", r"macbook\s*pro"),
    ("Mac Studio", r"mac\s*studio"),
]
# accessories / other products that mention our product names
EXCLUDE = re.compile(
    r"studio display|macbook air|\b(case|sleeve|dock|stand|hub|charger|cable|adapter|"
    r"keyboard|mouse|monitor|screen protector|skin|bag|backpack|applecare)\b", re.I)
FOREIGN = re.compile(r"[£€]|\bC\$|\bCAD\b|\bAUD\b|\bGBP\b|\bEUR\b", re.I)
REFURB = re.compile(r"refurb|renewed|open[- ]box|\bused\b|pre[- ]owned|certified", re.I)
RETAILERS = [
    (r"amazon", "Amazon"), (r"best\s*buy", "Best Buy"), (r"b&h|b\s*&\s*h|b and h", "B&H"),
    (r"costco", "Costco"), (r"walmart", "Walmart"), (r"micro\s*center", "Micro Center"),
    (r"adorama", "Adorama"), (r"target", "Target"), (r"newegg", "Newegg"),
    (r"sam'?s club", "Sam's Club"), (r"woot", "Woot"), (r"staples", "Staples"),
    (r"apple\s*(?:store|\.com)|\bat apple\b|@ apple|\[apple\]", "Apple"),
]
MONEY = re.compile(
    r"(?P<pre>\b(?:was|reg(?:ular)?|msrp|list|orig(?:inal)?|save|extra)\.?\s*:?\s*)?"
    r"\$\s?(?P<d>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<c>\d{2}))?"
    r"(?P<post>\s*(?:off|discount|savings|(?:\w+\s)?gift\s*card|credit|back|instant|rebate))?",
    re.I)
PCT_OFF = re.compile(r"(\d{1,2})\s*%\s*off", re.I)
CHIP = re.compile(r"\bM([1-9])(?:\s*(Pro|Max|Ultra))?\b", re.I)
SIZE = re.compile(r"\b(14|16)[\s-]?(?:inch|in\b|\")", re.I)
CAPACITY = re.compile(r"(\d+)\s*(GB|TB)\b", re.I)
RAM_SIZES = {8, 16, 18, 24, 32, 36, 48, 64, 96, 128, 192}
MIN_PRICE = 300       # below this it's an accessory / mis-parse
MAX_PLAUSIBLE_PCT = 60  # above this the parse is almost certainly wrong


@dataclass
class Deal:
    product: str
    chip: str | None
    size: int | None
    ram: int | None
    ssd: int | None
    condition: str
    retailer: str | None
    price: float | None
    msrp: float | None
    savings: float | None
    pct: float | None

    def config_label(self) -> str:
        parts = [self.product]
        if self.size:
            parts[0] += f" {self.size}\""
        if self.chip:
            parts.append(self.chip)
        mem = "/".join(x for x in (
            f"{self.ram}GB" if self.ram else "",
            (f"{self.ssd // 1000}TB" if self.ssd and self.ssd >= 1000 else f"{self.ssd}GB") if self.ssd else "",
        ) if x)
        if mem:
            parts.append(mem)
        return " ".join(parts)


def lookup_msrp(baselines, product, chip, size, ram, ssd):
    """Conservative: when the title is vague, pick the cheapest matching config
    so we never overstate a discount. Returns None if nothing matches."""
    if not chip:
        return None
    cands = [b for b in baselines if b["product"] == product and b["chip"].lower() == chip.lower()]
    if size:
        cands = [b for b in cands if b.get("size") in (None, size)]
    if ram:
        cands = [b for b in cands if b.get("ram") == ram]
    if ssd:
        cands = [b for b in cands if b.get("ssd") == ssd]
    if not ram and not ssd:
        cands = [b for b in cands if b.get("base")]
    return min((b["msrp"] for b in cands), default=None)


def _capacities(title):
    ram = ssd = None
    for n, unit in CAPACITY.findall(title):
        gb = int(n) * (1000 if unit.upper() == "TB" else 1)
        if ram is None and gb in RAM_SIZES and unit.upper() == "GB":
            ram = gb
        elif ssd is None and gb >= 256:
            ssd = gb
    return ram, ssd


def parse_title(raw_title: str, baselines: list) -> Deal | None:
    title = html.unescape(raw_title or "")
    if EXCLUDE.search(title) or FOREIGN.search(title):
        return None
    product = next((name for name, rx in PRODUCTS if re.search(rx, title, re.I)), None)
    if not product:
        return None

    chip = None
    if m := CHIP.search(title):
        chip = f"M{m.group(1)}" + (f" {m.group(2).title()}" if m.group(2) else "")
    size = int(m.group(1)) if (m := SIZE.search(title)) and product == "MacBook Pro" else None
    ram, ssd = _capacities(title)
    condition = "refurbished" if REFURB.search(title) else "new"
    retailer = None
    best_pos = None
    for rx, name in RETAILERS:
        if m := re.search(rx, title, re.I):
            if best_pos is None or m.start() < best_pos:
                best_pos, retailer = m.start(), name

    price = off = None
    for m in MONEY.finditer(title):
        amount = float(m.group("d").replace(",", "") + ("." + m.group("c") if m.group("c") else ""))
        pre = (m.group("pre") or "").lower().strip(" .:")
        if m.group("post") or pre in ("save", "extra"):
            off = max(off or 0, amount)
        elif pre in ("was", "reg", "regular", "msrp", "list", "orig", "original"):
            continue
        elif price is None and amount >= MIN_PRICE:
            price = amount
    stated_pct = float(m.group(1)) if (m := PCT_OFF.search(title)) else None

    msrp = lookup_msrp(baselines, product, chip, size, ram, ssd)
    if price is None and msrp:
        if off:
            price = msrp - off
        elif stated_pct:
            price = round(msrp * (1 - stated_pct / 100), 2)
    if price is not None and price < MIN_PRICE:
        return None

    savings = pct = None
    if price is not None and msrp:
        savings, pct = msrp - price, (msrp - price) / msrp * 100
    elif off:
        savings = off
    elif stated_pct:
        pct = stated_pct
    if price is None and savings is None and pct is None:
        return None  # a mention, not a priced deal
    if (pct is not None and not 0 < pct <= MAX_PLAUSIBLE_PCT) or (savings is not None and savings <= 0):
        return None
    return Deal(product, chip, size, ram, ssd, condition, retailer, price, msrp,
                round(savings, 2) if savings is not None else None,
                round(pct, 1) if pct is not None else None)
