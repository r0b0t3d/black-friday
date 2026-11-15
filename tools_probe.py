import re, json, sys
sys.path.insert(0, ".")
from bfwatch import vn
URLS = [
 # Karcher VC 3 Plus
 "https://meta.vn/may-hut-bui-gia-dinh-karcher-vc-3-plus-1-198-050-0-p105252",
 "https://sgt.com.vn/products/may-hut-bui-karcher-vc-3-plus",
 "https://dienmayhaianh.vn/san-pham/may-hut-bui-karcher-vc-3-plus-chat-luong-cao-4938.html",
 "https://giadunghuylinh.vn/may-hut-bui-karcher-vc3-plus-kap-1-100w",
 "https://kinghouse.vn/san-pham/may-hut-bui-karcher-vc-3-plus-white.html",
 "https://mintmart.vn/san-pham/may-hut-bui-karcher-vc-3-plus-hang-chinh-hang/",
 "https://tiki.vn/may-hut-bui-karcher-vc-3-plus-hang-chinh-hang-p2166843.html",
 "https://tiki.vn/api/v2/products/2166843",
 # Acnos
 "https://cellphones.com.vn/loa-xach-tay-karaoke-acnos-cs201v.html",
 "https://cellphones.com.vn/loa-xach-tay-karaoke-acnos-cs251v.html",
 "https://cellphones.com.vn/loa-karaoke-xach-tay-acnos-cs3600.html",
 "https://fptshop.com.vn/phu-kien/loa-karaoke-xach-tay-acnos-cs201plus",
 "https://dienmaycholon.com/loa-karaoke-xach-tay/loa-karaoke-di-dong-acnos-cs201plus",
 "https://hacom.vn/loa-karaoke-xach-tay-mini-acnos-cs450neo-200w-bass-16.5cm-200w-kem-2-micro",
 # listings
 "https://cellphones.com.vn/thiet-bi-am-thanh/loa/karaoke/acnos.html",
 "https://fptshop.com.vn/phu-kien/loa/acnos",
 "https://dienmaycholon.com/loa-karaoke-xach-tay-acnos",
]
for u in URLS:
    print("=====", u)
    try:
        body = vn.get(u)
    except Exception as e:
        print("ERR", repr(e)[:120]); continue
    print("len", len(body), "extract_price ->", vn.extract_price(body))
    print("  ld+json blocks:", len(vn.LD_RX.findall(body)), "| 'Product' types:", len(re.findall(r'"@type"\s*:\s*"Product"', body)),
          "| price keys:", len(re.findall(r'"price"\s*:', body)), "| ₫/đ prices:", re.findall(r'\d{1,3}(?:[.,]\d{3}){1,2}\s?(?:₫|đ|VNĐ)', body)[:4])
    t = re.search(r"<title>(.*?)</title>", body, re.S)
    print("  title:", (t.group(1).strip()[:90] if t else None))
