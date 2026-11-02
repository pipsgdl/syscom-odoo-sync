#!/usr/bin/env python3
"""
Tamex scraper — WooCommerce Store API pública.
Endpoint: /wp-json/wc/store/v1/products?per_page=100&page=N
Devuelve sku/name/permalink/prices/is_in_stock. Sin Playwright.
"""
import json, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

BASE = 'https://tamex.mx'
OUT = Path('/tmp/tamex_catalog.json')
LOG = Path('/tmp/tamex_scraper.log')
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
                # WooCommerce Store API exposes total via header
                total_pages = r.headers.get('X-WP-TotalPages')
                total = r.headers.get('X-WP-Total')
                return json.loads(r.read()), total_pages, total
        except Exception as e:
            log(f"  retry {i+1}/{retries} {url[:80]} :: {str(e)[:120]}")
            time.sleep(2 + i*2)
    return None, None, None

def main(max_pages=500, per_page=100):
    LOG.unlink(missing_ok=True)
    log(f"Tamex scrape start per_page={per_page}")
    all_products = []
    seen = set()

    # discover total pages
    first, tp, tot = fetch(f"{BASE}/wp-json/wc/store/v1/products?per_page={per_page}&page=1")
    if first is None:
        log("FATAL: primer fetch falló")
        return
    log(f"  total productos reportados: {tot} | total páginas: {tp}")
    try:
        max_pages = min(max_pages, int(tp))
    except Exception:
        pass

    for page in range(1, max_pages + 1):
        url = f"{BASE}/wp-json/wc/store/v1/products?per_page={per_page}&page={page}"
        data, _, _ = fetch(url)
        if not data:
            log(f"  page {page}: FAIL — abort")
            break
        if not isinstance(data, list) or len(data) == 0:
            log(f"  page {page}: vacío — fin")
            break

        new = 0
        for p in data:
            pid = p.get('id')
            if pid in seen: continue
            seen.add(pid)
            prices = p.get('prices') or {}
            # brand: WooCommerce no da brand de fábrica; intentar attributes/categories
            brand = ''
            for a in (p.get('attributes') or []):
                if (a.get('name') or '').lower() in ('brand','marca','fabricante'):
                    terms = a.get('terms') or []
                    if terms: brand = terms[0].get('name','')
                    break
            cats = [c.get('name','') for c in (p.get('categories') or [])]
            item = {
                'id': pid,
                'sku': p.get('sku') or '',
                'brand': brand,
                'title': p.get('name') or '',
                'slug': p.get('slug') or '',
                'url': p.get('permalink') or '',
                'price': prices.get('price'),
                'price_currency': prices.get('currency_code'),
                'on_sale': p.get('on_sale'),
                'is_in_stock': p.get('is_in_stock'),
                'is_purchasable': p.get('is_purchasable'),
                'categories': cats,
            }
            all_products.append(item)
            new += 1

        log(f"  page {page}: +{new} (total {len(all_products)})")
        if new == 0:
            break
        if page % 5 == 0:
            with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
        time.sleep(1.0)

    with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
    from collections import Counter
    cats = Counter()
    for p in all_products:
        for c in p.get('categories') or []:
            cats[c] += 1
    log(f"\n✅ Tamex FIN — {len(all_products)} productos → {OUT}")
    log("Top 15 categorías:")
    for c, n in cats.most_common(15):
        log(f"  {c[:35]:35} {n:>5}")

if __name__ == '__main__':
    mp = 500
    if '--max-pages' in sys.argv:
        mp = int(sys.argv[sys.argv.index('--max-pages')+1])
    main(max_pages=mp)
