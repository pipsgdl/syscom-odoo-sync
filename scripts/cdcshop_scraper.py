#!/usr/bin/env python3
"""
CDCShop scraper — Shopify nativo, API JSON pública /products.json
Pagina hasta 250 productos por página. Sin Playwright.
"""
import json, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

BASE = 'https://cdcshop.com.mx'
OUT = Path('/tmp/cdcshop_catalog.json')
LOG = Path('/tmp/cdcshop_scraper.log')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '\n')

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            log(f"  retry {i+1}/{retries} {url[:80]} :: {str(e)[:120]}")
            time.sleep(2 + i*2)
    return None

def main(max_pages=200):
    LOG.unlink(missing_ok=True)
    log(f"CDCShop scrape start max_pages={max_pages}")
    all_products = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"{BASE}/products.json?limit=250&page={page}"
        data = fetch(url)
        if not data:
            log(f"  page {page}: FAIL — abort")
            break
        prods = data.get('products', [])
        if not prods:
            log(f"  page {page}: vacío — fin")
            break

        new = 0
        for p in prods:
            pid = p.get('id')
            if pid in seen_ids: continue
            seen_ids.add(pid)
            variants = p.get('variants') or [{}]
            v0 = variants[0] if variants else {}
            item = {
                'id': pid,
                'sku': v0.get('sku') or '',
                'brand': p.get('vendor') or '',
                'title': p.get('title') or '',
                'handle': p.get('handle') or '',
                'url': f"{BASE}/products/{p.get('handle','')}",
                'product_type': p.get('product_type') or '',
                'price': v0.get('price'),
                'available': v0.get('available'),
                'inventory_quantity': v0.get('inventory_quantity'),
                'variants_count': len(variants),
                'tags': p.get('tags') or [],
            }
            all_products.append(item)
            new += 1

        log(f"  page {page}: +{new} (total {len(all_products)})")
        if new == 0:
            break
        # cache parcial cada 5 páginas
        if page % 5 == 0:
            with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
        time.sleep(1.0)

    with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
    from collections import Counter
    brands = Counter(p['brand'] for p in all_products if p.get('brand'))
    log(f"\n✅ CDCShop FIN — {len(all_products)} productos → {OUT}")
    log("Top 15 marcas:")
    for b, n in brands.most_common(15):
        log(f"  {b[:35]:35} {n:>5}")

if __name__ == '__main__':
    mp = 200
    if '--max-pages' in sys.argv:
        mp = int(sys.argv[sys.argv.index('--max-pages')+1])
    main(max_pages=mp)
