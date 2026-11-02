#!/usr/bin/env python3
"""
Electrycom scraper — Joomla + HikaShop.

Hallazgos clave (debugging 2026-05-08):
- Listing principal (option=com_hikashop&view=product&layout=listing) muestra solo
  ~129 productos con limit=5000. El catálogo público anonymous está limitado.
- "Página 1 de 1369" es contador GLOBAL del sitio (no del listing actual).
- Páginas tienen 4 carouseles: jcarousel[0]=listing real; jcarousel[1..3]=módulos
  laterales fijos (NO iterar — repiten misma data).
- Pagination via POST AJAX con campo limitstart_hikashop_category_information_menu__2.
- Para >129 productos, hay que iterar las URLs de marca:
  /newecv4/index.php/marcas-2/product/listing/{id}-{slug}
  visibles solo desde homepage. Hacemos crawl de homepage + listing.

ESTRATEGIA actual:
1) Listing principal con limit=5000 → ~129 productos.
2) Homepage para extraer URLs de marca → scrape cada marca con limit=5000.
3) Deduplicar por URL.

TODO si productos < esperados (~952): hay sub-categorias /marcas-2/category/listing/...
y /productos-3/... que requieren crawl recursivo. Ver capturas /tmp/electry_*.html
para próximas pistas.
"""
import json, sys, re, time, urllib.request, urllib.parse, http.cookiejar
from datetime import datetime
from pathlib import Path

BASE = 'https://www.electrycom.com/newecv4'
LISTING = f'{BASE}/index.php?option=com_hikashop&view=product&layout=listing'
HOME = f'{BASE}/'
OUT = Path('/tmp/electrycom_catalog.json')
LOG = Path('/tmp/electrycom_scraper.log')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '\n')

# Producto está en <li class="spanN hikashop_product hikashop_product_column_C hikashop_product_row_R...">
RE_HREF = re.compile(r'href="(/newecv4/index\.php[^"]*?/product/(\d+)-([^"?#]+))"', re.I)
RE_NAME = re.compile(r'class="hikashop_product_name"[^>]*>(?:\s*<[^>]+>\s*)*([^<]+)', re.I | re.S)
RE_PRICE = re.compile(r'class="hikashop_product_price hikashop_product_price_\d+"[^>]*>\s*([0-9][^<]+)', re.I)
RE_DESC = re.compile(r'class="short_desc[^"]*"[^>]*>([^<]+)', re.I)


def extract_main_carousel(html):
    """Devuelve solo los <li> productos del jcarousel[0] (listing principal),
    ignorando los carouseles laterales (módulos 95/94/97 etc)."""
    # Encontrar el primer jcarousel hikashop_products
    m = re.search(r'<ul[^>]*class="jcarousel hikashop_products listing_div row-fluid toan ?"[^>]*>', html)
    if not m: return []
    start = m.end()
    # Cierre de ese <ul>
    rest = html[start:]
    end = rest.find('</ul>')
    if end < 0: end = len(rest)
    return parse_blocks(rest[:end])


def parse_blocks(html):
    out = []
    seen = set()
    blocks = re.split(r'<li class="span\d+ hikashop_product hikashop_product_column', html)
    for blk in blocks[1:]:
        m_href = RE_HREF.search(blk)
        if not m_href: continue
        href = m_href.group(1).replace('&amp;', '&')
        pid = m_href.group(2)
        slug = m_href.group(3)
        url = f'https://www.electrycom.com{href}'
        if url in seen: continue
        seen.add(url)
        name = ''
        m = RE_NAME.search(blk)
        if m: name = re.sub(r'\s+', ' ', m.group(1)).strip()
        price_raw = ''
        m = RE_PRICE.search(blk)
        if m: price_raw = re.sub(r'\s+', ' ', m.group(1)).strip()
        desc = ''
        m = RE_DESC.search(blk)
        if m: desc = re.sub(r'\s+', ' ', m.group(1)).strip()
        pm = re.search(r'([0-9,]+\.\d+)', price_raw)
        price = float(pm.group(1).replace(',', '')) if pm else None
        out.append({
            'url': url,
            'product_id': pid,
            'sku': name,
            'brand': '',
            'title': desc[:300],
            'price': price,
            'price_raw': price_raw,
            'slug': slug,
        })
    return out


def open_with_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ('User-Agent', UA),
        ('Accept', 'text/html,application/xhtml+xml'),
        ('Accept-Language', 'es-MX,es;q=0.9,en;q=0.8'),
    ]
    return opener


def fetch(opener, url, retries=3):
    for i in range(retries):
        try:
            with opener.open(url, timeout=45) as r:
                return r.read().decode('utf-8', errors='replace')
        except Exception as e:
            log(f"  retry {i+1}: {url[:80]} :: {str(e)[:100]}")
            time.sleep(1.5 + i * 1.5)
    return None


def scrape_listing_url(opener, url, brand_hint=''):
    """Scrape listing con limit=5000 (max servidor) — todos en una página."""
    # Set limit via POST
    html = fetch(opener, url)
    if not html: return []
    # POST con limit alto
    sep = '&' if '?' in url else '?'
    post_url = url
    body = urllib.parse.urlencode({
        'limit_hikashop_category_information_menu__2': '5000',
        'limitstart_hikashop_category_information_menu__2': '0',
        'filter_order_hikashop_category_information_menu__2': 'a.ordering',
        'filter_order_Dir_hikashop_category_information_menu__2': 'ASC',
    }).encode('utf-8')
    req = urllib.request.Request(post_url, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('User-Agent', UA)
    req.add_header('Referer', url)
    try:
        with opener.open(req, timeout=90) as r:
            html = r.read().decode('utf-8', errors='replace')
    except Exception as e:
        log(f"  POST err {url[:60]}: {str(e)[:100]}")
        return []
    items = extract_main_carousel(html)
    if brand_hint:
        for it in items:
            it['brand'] = brand_hint
    return items


def discover_brand_urls(opener):
    """Scrape homepage para encontrar URLs /marcas-2/product/listing/{id}-{slug}."""
    html = fetch(opener, HOME)
    if not html: return []
    urls = re.findall(r'href="(/newecv4/index\.php/marcas-2/product/listing/\d+-[^"]+)"', html)
    urls = list(dict.fromkeys(urls))  # dedupe preservando orden
    return [(u, u.rsplit('-', 1)[-1]) for u in urls]


def main():
    LOG.unlink(missing_ok=True)
    log("Electrycom scrape start v2 (listing principal + marcas)")
    opener = open_with_session()

    all_products = []
    seen = set()

    # 1) Listing principal
    log("Fase 1: listing principal con limit=5000")
    items = scrape_listing_url(opener, LISTING)
    for it in items:
        if it['url'] in seen: continue
        seen.add(it['url'])
        all_products.append(it)
    log(f"  → +{len(items)} (total {len(all_products)})")

    # 2) Marcas
    log("Fase 2: descubrir URLs de marcas desde homepage")
    brand_urls = discover_brand_urls(opener)
    log(f"  marcas encontradas: {len(brand_urls)}")

    for i, (rel_url, brand_slug) in enumerate(brand_urls, 1):
        full_url = f'https://www.electrycom.com{rel_url}'
        items = scrape_listing_url(opener, full_url, brand_hint=brand_slug)
        new = 0
        for it in items:
            if it['url'] in seen: continue
            seen.add(it['url'])
            all_products.append(it)
            new += 1
        log(f"  [{i}/{len(brand_urls)}] {brand_slug}: +{new} (total {len(all_products)})")
        if i % 10 == 0:
            with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
        time.sleep(0.5)

    with open(OUT, 'w') as f: json.dump(all_products, f, ensure_ascii=False)
    from collections import Counter
    brands = Counter(p['brand'] for p in all_products if p.get('brand'))
    with_price = sum(1 for p in all_products if p.get('price'))
    log(f"\n✅ Electrycom FIN — {len(all_products)} productos | with_price={with_price} → {OUT}")
    log(f"Top marcas: {brands.most_common(15)}")
    # TODO si total < 952: explorar /productos-3/... y /marcas-2/category/listing/... recursivamente


if __name__ == '__main__':
    main()
