#!/usr/bin/env python3
"""
QDigital scraper — BigCommerce con sitemap personalizado.
Index: /xmlsitemap.php → 5 sub-sitemaps; el de products pagina por &page=N.
Para cada URL de producto, scrape HTML para extraer SKU + brand + price + nombre.
Sin Playwright (servidor responde HTML completo).
"""
import json, sys, time, re, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser

BASE = 'https://qdigital.mx'
OUT = Path('/tmp/qdigital_catalog.json')
LOG = Path('/tmp/qdigital_scraper.log')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '\n')

def fetch_text(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/xml'})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                try:
                    return raw.decode('utf-8', errors='replace')
                except Exception:
                    return raw.decode('latin-1', errors='replace')
        except Exception as e:
            log(f"  retry {i+1}/{retries} {url[:80]} :: {str(e)[:100]}")
            time.sleep(1.5 + i*2)
    return None

def discover_product_urls(max_sitemap_pages=50):
    urls = set()
    for page in range(1, max_sitemap_pages + 1):
        sm = fetch_text(f"{BASE}/xmlsitemap.php?type=products&page={page}")
        if not sm: break
        found = re.findall(r'<loc>([^<]+)</loc>', sm)
        if not found: break
        before = len(urls)
        for u in found:
            urls.add(u.strip())
        log(f"  sitemap p{page}: +{len(urls)-before} (total {len(urls)})")
        if len(found) < 50: break
        time.sleep(0.5)
    return sorted(urls)

# --- minimal HTML extractors via regex (BigCommerce templates are pretty consistent) ---
RE_TITLE = re.compile(r'<h1[^>]*class="[^"]*productView-title[^"]*"[^>]*>(.*?)</h1>', re.S | re.I)
RE_TITLE_FALLBACK = re.compile(r'<title>(.*?)</title>', re.S | re.I)
RE_SKU = re.compile(r'data-product-sku[^>]*>\s*([^<]+?)\s*<', re.I)
RE_SKU2 = re.compile(r'"sku"\s*:\s*"([^"]+)"', re.I)
# Brand is wrapped <span class="productView-brand"><a><span><strong>BRAND</strong></span></a></span>
# Anterior regex capturaba el siguiente <a> tras el bloque (Create Wish List).
# Limitamos el .*? a no exceder cierre de </span> productView-brand.
RE_BRAND = re.compile(r'class="productView-brand"\s*>\s*<a[^>]*>(.*?)</a>', re.S | re.I)
RE_BRAND2 = re.compile(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', re.I)
RE_BRAND3 = re.compile(r'"brand"\s*:\s*"([^"]+)"', re.I)  # JSON-LD brand como string plano
RE_PRICE = re.compile(r'data-product-price-without-tax[^>]*content="([0-9.]+)"', re.I)
RE_PRICE2 = re.compile(r'"price"\s*:\s*"?([0-9.]+)"?', re.I)
RE_STOCK = re.compile(r'productView-info-name[^>]*>\s*Stock[^<]*</dt>\s*<dd[^>]*>\s*([^<]+?)\s*<', re.S | re.I)

def strip(s):
    if not s: return ''
    s = re.sub(r'<[^>]+>', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse_product(html, url):
    if not html: return None
    title = ''
    m = RE_TITLE.search(html)
    if m: title = strip(m.group(1))
    if not title:
        m = RE_TITLE_FALLBACK.search(html)
        if m: title = strip(m.group(1)).split('|')[0].strip()
    sku = ''
    m = RE_SKU.search(html) or RE_SKU2.search(html)
    if m: sku = m.group(1).strip()
    brand = ''
    # Preferimos JSON-LD primero (más confiable que regex sobre HTML)
    m = RE_BRAND2.search(html) or RE_BRAND3.search(html) or RE_BRAND.search(html)
    if m: brand = strip(m.group(1))
    # Filtrar valores erróneos que vinieron de una corrida previa
    if brand.lower() in ('create wish list', 'wish list', 'add to wishlist', ''):
        brand = ''
    price = None
    m = RE_PRICE.search(html) or RE_PRICE2.search(html)
    if m:
        try: price = float(m.group(1))
        except: pass
    stock = ''
    m = RE_STOCK.search(html)
    if m: stock = strip(m.group(1))
    return {
        'url': url,
        'sku': sku,
        'brand': brand,
        'title': title,
        'price': price,
        'stock': stock,
    }

def main(max_products=None):
    LOG.unlink(missing_ok=True)
    log("QDigital scrape start")
    log("Fase 1: descubrir URLs producto vía sitemap...")
    urls = discover_product_urls()
    log(f"Total productos a scrape: {len(urls)}")
    if max_products: urls = urls[:max_products]

    log("Fase 2: scrape páginas producto...")
    out = []
    fail = 0
    for i, u in enumerate(urls, 1):
        html = fetch_text(u)
        item = parse_product(html, u)
        if not item or (not item.get('sku') and not item.get('title')):
            fail += 1
        else:
            out.append(item)
        if i % 25 == 0:
            log(f"  [{i}/{len(urls)}] ok={len(out)} fail={fail}")
            with open(OUT, 'w') as f: json.dump(out, f, ensure_ascii=False)
        time.sleep(0.7)

    with open(OUT, 'w') as f: json.dump(out, f, ensure_ascii=False)
    from collections import Counter
    brands = Counter(p['brand'] for p in out if p.get('brand'))
    log(f"\n✅ QDigital FIN — {len(out)} productos ok / {fail} fail → {OUT}")
    log("Top 15 marcas:")
    for b, n in brands.most_common(15):
        log(f"  {b[:35]:35} {n:>5}")

if __name__ == '__main__':
    mp = None
    if '--max' in sys.argv: mp = int(sys.argv[sys.argv.index('--max')+1])
    main(max_products=mp)
