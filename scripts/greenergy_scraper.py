#!/usr/bin/env python3
"""Scraper Greenergy (Shopify /products.json). Output: greenergy_catalog.json"""
import urllib.request, json, time, ssl, sys, os
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
BASE="https://greenergy.com.mx"
OUT="/Users/ingfelipe/cotizador-web/caches/greenergy_catalog.json"
LOG="/tmp/greenergy_scraper.log"
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE

def log(m):
    print(m); open(LOG,"a").write(m+"\n")

def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA})
    return json.loads(urllib.request.urlopen(req,timeout=30,context=ctx).read())

items=[]; page=1
while True:
    url=f"{BASE}/products.json?limit=250&page={page}"
    log(f"GET {url}")
    try:
        d=fetch(url)
    except Exception as e:
        log(f"err {e}"); break
    prods=d.get("products",[])
    if not prods: break
    for p in prods:
        for v in p.get("variants",[]) or [{}]:
            items.append({
                "id": p.get("id"),
                "sku": v.get("sku") or "",
                "brand": p.get("vendor") or "",
                "title": p.get("title") or "",
                "url": f"{BASE}/products/{p.get('handle')}",
                "price": v.get("price"),
                "available": v.get("available"),
                "variant_id": v.get("id"),
                "product_type": p.get("product_type"),
            })
    log(f"page {page}: {len(prods)} prods (total items {len(items)})")
    page+=1
    if len(prods)<250: break
    time.sleep(1.2)

os.makedirs(os.path.dirname(OUT),exist_ok=True)
json.dump(items,open(OUT,"w"),ensure_ascii=False,indent=2)
log(f"DONE {len(items)} items -> {OUT}")
