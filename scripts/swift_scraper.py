#!/usr/bin/env python3
"""
Swift de México scraper — WooCommerce Store API
URL: https://swiftdemexico.com/wp-json/wc/store/v1/products?per_page=100&page=N

345 productos. Sin login, sin precios (B2B), pero con SKU + marca via categorías.
9 marcas: Belden, Cablofil, Charofil, CPI, Dahua, Derrant, Landing Systems, Lapp, Milesight.
"""
import json, urllib.request, time, sys
from datetime import datetime
from pathlib import Path

BASE = 'https://swiftdemexico.com/wp-json/wc/store/v1/products'
OUT = '/Users/ingfelipe/cotizador-web/caches/swift_catalog.json'
LOG = '/tmp/swift_scraper.log'

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

# Mapeo categoría slug → marca (basado en URLs /catalogo-{marca}/)
CATEG_TO_BRAND = {
    'belden':'BELDEN','cablofil':'CABLOFIL','charofil':'CHAROFIL','cpi':'CPI',
    'dahua':'DAHUA','derrant':'DERRANT','landing-systems':'LANDING SYSTEMS',
    'lapp':'LAPP','milesight':'MILESIGHT','milesight-2':'MILESIGHT',
}

def log(m):
    msg = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(msg, flush=True)
    with open(LOG,'a') as f: f.write(msg+'\n')

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def detect_brand(item):
    """Detecta marca desde categorías o título."""
    # Por categorías slug
    for cat in (item.get('categories') or []):
        slug = (cat.get('slug') or '').lower()
        for key, brand in CATEG_TO_BRAND.items():
            if key in slug:
                return brand
    # Por título (fallback)
    name = (item.get('name') or '').upper()
    for key, brand in CATEG_TO_BRAND.items():
        if brand in name:
            return brand
    return ''

def main():
    log("Swift de México scraper (WC Store API)")
    all_items = []
    page = 1
    while True:
        url = f'{BASE}?per_page=100&page={page}'
        try:
            data = fetch(url)
        except Exception as e:
            log(f"  err pg={page}: {e}")
            break
        if not data: break
        for it in data:
            sku = it.get('sku') or ''
            if not sku: continue  # skip sin SKU
            cats = it.get('categories') or []
            cat_names = [c.get('name','') for c in cats]
            all_items.append({
                'id': it.get('id'),
                'sku': sku,
                'brand': detect_brand(it),
                'title': (it.get('name') or '').replace('&#8243;','"'),
                'price': float(it.get('prices',{}).get('price') or 0) / 100,  # WC envía en centavos
                'currency': it.get('prices',{}).get('currency_code','MXN'),
                'url': it.get('permalink'),
                'categories': cat_names,
                'in_stock': it.get('is_in_stock'),
            })
        log(f"  pg={page}: +{len(data)} (total: {len(all_items)})")
        if len(data) < 100: break
        page += 1
        time.sleep(1)

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    json.dump(all_items, open(OUT,'w'), ensure_ascii=False)
    log(f"\n✅ Swift FIN — {len(all_items)} productos → {OUT}")
    from collections import Counter
    brands = Counter(i['brand'] for i in all_items if i['brand'])
    log("\nTop marcas:")
    for b,n in brands.most_common(15):
        log(f"  {b:<20} {n}")
    sin_brand = sum(1 for i in all_items if not i['brand'])
    log(f"\nSin marca: {sin_brand}")

if __name__ == '__main__':
    main()
