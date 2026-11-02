#!/usr/bin/env python3
"""Scraper CITSolar (WooCommerce Store API). Output: citsolar_catalog.json"""
import urllib.request, json, time, ssl, os
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BASE="https://tienda.citsolar.mx"
OUT="/Users/ingfelipe/cotizador-web/caches/citsolar_catalog.json"
LOG="/tmp/citsolar_scraper.log"
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
def log(m): print(m); open(LOG,"a").write(m+"\n")
def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA})
    return json.loads(urllib.request.urlopen(req,timeout=30,context=ctx).read())

items=[]; page=1
while True:
    url=f"{BASE}/wp-json/wc/store/v1/products?per_page=100&page={page}"
    log(f"GET {url}")
    try: prods=fetch(url)
    except Exception as e: log(f"err {e}"); break
    if not prods: break
    for p in prods:
        attrs={a.get("name"):", ".join(t.get("name","") for t in a.get("terms",[])) for a in p.get("attributes",[])}
        brand=attrs.get("Marca") or attrs.get("Brand") or ""
        items.append({
            "id": p.get("id"),
            "sku": p.get("sku") or "",
            "brand": brand,
            "title": p.get("name") or "",
            "url": p.get("permalink"),
            "price": (p.get("prices") or {}).get("price"),
            "price_currency": (p.get("prices") or {}).get("currency_code"),
            "is_in_stock": p.get("is_in_stock"),
            "categories": [c.get("name") for c in p.get("categories",[])],
            "attributes": attrs,
        })
    log(f"page {page}: {len(prods)} items (total {len(items)})")
    if len(prods)<100: break
    page+=1; time.sleep(1.2)

os.makedirs(os.path.dirname(OUT),exist_ok=True)
json.dump(items,open(OUT,"w"),ensure_ascii=False,indent=2)
log(f"DONE {len(items)} items -> {OUT}")
