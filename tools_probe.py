import re, sys
sys.path.insert(0, ".")
from bfwatch import vn
for u in ["https://cellphones.com.vn/thiet-bi-am-thanh/loa/karaoke/acnos.html", "https://dienmaycholon.com/loa-karaoke-xach-tay-acnos", "https://hacom.vn/loa-karaoke"]:
    print("=====", u)
    body = vn.get(u)
    links = []
    for h in re.findall(r'href="([^"#?]*acnos[^"#?]*)"', body, re.I):
        if h not in links: links.append(h)
    print("n acnos links:", len(links))
    for h in links[:45]: print("  L", h)
