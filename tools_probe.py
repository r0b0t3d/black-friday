import re, json, urllib.request, urllib.error
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
URLS = [
 "https://www.thegioididong.com/dong-ho-thong-minh/dong-ho-dinh-vi-tre-em-tammi-watch-kid-max",
 "https://www.thegioididong.com/dong-ho-thong-minh/dong-ho-dinh-vi-tre-em-tammi-watch-kid-plus",
 "https://www.dienmayxanh.com/dong-ho-thong-minh/dong-ho-dinh-vi-tre-em-tammi-watch-kid-max",
 "https://cellphones.com.vn/dong-ho-thong-minh-tre-em-tammi-watch-kid-max.html",
 "https://cellphones.com.vn/dong-ho-thong-minh-tre-em-tammi-watch-kid-plus.html",
 "https://hoanghamobile.com/dong-ho-thong-minh/tammi-watch-kid-max",
 "https://hoanghamobile.com/dong-ho-thong-minh/tammi-watch-kid-plus",
 "https://tiki.vn/api/v2/products?limit=10&q=tammi+watch+kid+max",
 "https://shopee.vn/api/v4/search/search_items?keyword=tammi%20watch%20kid%20max&limit=10&newest=0&order=desc&page_type=search&version=2",
]
for u in URLS:
    print("=====", u)
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9", "Accept": "text/html,application/json,*/*"}), timeout=20)
        body = r.read().decode("utf-8", "replace")
        print("status", r.status, "len", len(body))
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read()[:150]); continue
    except Exception as e:
        print("ERR", repr(e)[:150]); continue
    for rx in [r'"price"\s*:\s*"?[\d.,]+', r'product:price:amount"[^>]*content="[^"]+', r'itemprop="price"[^>]*', r'box-price-present[^<]*<[^>]*>[^<]*|box-price-present">[^<]*', r'[\d.]{5,}\s?₫', r'"priceCurrency"[^,]*']:
        m = re.findall(rx, body)[:3]
        if m: print("  ", rx[:28], "->", m)
    if body.lstrip().startswith("{"): print("  json:", body[:300])
