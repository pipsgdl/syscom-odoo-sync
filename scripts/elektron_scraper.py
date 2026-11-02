#!/usr/bin/env python3
"""
Elektron scraper — Magento 2.
- API REST /rest/V1/products requiere token (401)
- Sitemap público: /media/sitemap.xml (~10MB) con TODAS las URLs (categorías y productos)
- Estrategia: descargar sitemap, filtrar URLs de producto (Magento usa .html como sufijo
  típico, o URL con varios niveles), y scrape HTML con Playwright para SKU/precio/marca.
"""
import asyncio, json, sys, re, time, urllib.request
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://www.elektron.com.mx'
SITEMAP = f'{BASE}/media/sitemap.xml'
OUT = Path('/tmp/elektron_catalog.json')
LOG = Path('/tmp/elektron_scraper.log')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '\n')

def fetch_text(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf-8', errors='replace')
        except Exception as e:
            log(f"  retry {i+1}/{retries}: {str(e)[:120]}")
            time.sleep(2 + i*2)
    return None

def discover_products():
    log("Descargando sitemap...")
    sm = fetch_text(SITEMAP)
    if not sm:
        return [], []
    locs = re.findall(r'<loc>([^<]+)</loc>', sm)
    log(f"  total URLs en sitemap: {len(locs)}")
    # Heurística Elektron: producto = un único segmento de slug largo después del dominio
    # con muchos hyphens. Categorías son cortas o multi-segmento (a/b/c).
    products = []
    for u in locs:
        path = u.replace('https://www.elektron.com.mx/', '').rstrip('/')
        if not path: continue
        if path.startswith('catalog/'): continue
        if '/' in path: continue  # categorías tipo conductores/baja-tension
        # producto típico: muchos hyphens y >25 chars
        if path.count('-') >= 4 and len(path) > 25:
            products.append(u)
    log(f"  URLs producto (heurística): {len(products)}")
    return products, locs

async def main(max_products=None, concurrency=4):
    LOG.unlink(missing_ok=True)
    log("Elektron scrape start")
    urls, _ = discover_products()
    if not urls:
        log("FATAL: no se obtuvieron URLs")
        return
    if max_products: urls = urls[:max_products]
    log(f"A scrape: {len(urls)} URLs, conc={concurrency}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={'width': 1280, 'height': 900},
        )
        # Block images/fonts/css for speed
        async def block(route):
            r = route.request
            if r.resource_type in ('image','font','media','stylesheet'):
                await route.abort()
            else:
                await route.continue_()
        await ctx.route('**/*', block)

        out = []
        fail = 0
        semaphore = asyncio.Semaphore(concurrency)

        async def scrape_one(idx, url):
            nonlocal fail
            async with semaphore:
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    # Pequeña espera para lazy-load de precio (Magento dispara XHR price-render)
                    try:
                        await page.wait_for_selector('[data-price-amount]', timeout=5000)
                    except Exception:
                        pass
                    data = await page.evaluate('''
                      () => {
                        const get = (s) => { const e = document.querySelector(s); return e ? (e.innerText || e.content || '').trim() : ''; };
                        // Verificar que es página de producto
                        const isProduct = document.body.classList.contains('catalog-product-view')
                                          || !!document.querySelector('[data-price-amount]')
                                          || !!document.querySelector('.product-info-stock-sku');
                        if (!isProduct) return { _skip: true };
                        let sku='', brand='', title='', price=null;
                        // Magento JSON-LD
                        const lds = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                        for (const ld of lds) {
                          try {
                            const j = JSON.parse(ld.innerText);
                            const arr = Array.isArray(j) ? j : [j];
                            for (const x of arr) {
                              if (x['@type'] === 'Product' || (Array.isArray(x['@type']) && x['@type'].includes('Product'))) {
                                sku = x.sku || sku;
                                title = x.name || title;
                                if (x.brand) {
                                  brand = (typeof x.brand === 'string') ? x.brand : (x.brand.name || '');
                                }
                                if (x.offers) {
                                  const o = Array.isArray(x.offers) ? x.offers[0] : x.offers;
                                  if (o && o.price) price = parseFloat(o.price);
                                }
                              }
                            }
                          } catch (e) {}
                        }
                        if (!title) title = get('h1.page-title, .page-title span, h1');
                        if (!sku) {
                          const skuEl = document.querySelector('[itemprop="sku"], .product.attribute.sku .value, .product-info-stock-sku .value');
                          if (skuEl) sku = (skuEl.innerText || '').trim();
                        }
                        if (price === null) {
                          // Magento: span data-price-amount
                          const pes = Array.from(document.querySelectorAll('[data-price-amount]'));
                          for (const pe of pes) {
                            const v = parseFloat(pe.getAttribute('data-price-amount'));
                            if (v && v > 0) { price = v; break; }
                          }
                        }
                        if (price === null) {
                          // Fallback: parsear span.price con texto $1,234.56
                          const pt = document.querySelector('.price-final_price .price, span.price');
                          if (pt) {
                            const tx = (pt.innerText || '').replace(/[^0-9.,]/g,'').replace(/,/g,'');
                            const v = parseFloat(tx);
                            if (v && v > 0) price = v;
                          }
                        }
                        const stockEl = document.querySelector('.stock, [itemprop="availability"]');
                        const stock = stockEl ? (stockEl.innerText || stockEl.getAttribute('content') || '').trim() : '';
                        return { sku, brand, title, price, stock };
                      }
                    ''')
                    if data and data.get('_skip'):
                        # No es página de producto (CMS, bot challenge, etc.)
                        fail += 1
                    elif data and (data.get('sku') or data.get('title')):
                        # Heurística marca: prefijo del SKU mapea a marca conocida
                        if not data.get('brand') and data.get('sku'):
                            sku_pref = data['sku'].split('-')[0].upper() if '-' in data['sku'] else ''
                            sku_brand_map = {
                                'ES': 'Estevez', 'CN': 'Condulac', 'CX': 'Condumex',
                                'PR': 'Prysmian', 'IS': 'IUSA', 'CR': 'Cooper',
                                'SQ': 'Square D', 'SCH': 'Schneider', 'GE': 'General Electric',
                                'PH': 'Philips', 'OS': 'Osram', 'SY': 'Sylvania',
                                'BTC': 'Bticino', 'LT': 'Leviton', 'SCN': 'Schneider',
                                'VL': 'Volteck', 'TR': 'Truper',
                            }
                            data['brand'] = sku_brand_map.get(sku_pref, '')
                        # Si aún no hay marca, intentar última palabra del título
                        if not data.get('brand') and data.get('title'):
                            words = data['title'].rstrip('.,').split()
                            if words:
                                last = words[-1]
                                # Si es una palabra capitalizada y >3 chars, asumirla marca
                                if len(last) >= 4 and last[0].isupper() and last.isalpha():
                                    data['brand'] = last
                        data['url'] = url
                        out.append(data)
                    else:
                        fail += 1
                except Exception as e:
                    fail += 1
                finally:
                    await page.close()

            if idx % 25 == 0:
                log(f"  [{idx}/{len(urls)}] ok={len(out)} fail={fail}")
                with open(OUT, 'w') as f: json.dump(out, f, ensure_ascii=False)

        tasks = [asyncio.create_task(scrape_one(i+1, u)) for i, u in enumerate(urls)]
        # Run tasks but allow crash-recovery if takes too long
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            log(f"gather err: {e}")

        with open(OUT, 'w') as f: json.dump(out, f, ensure_ascii=False)
        from collections import Counter
        brands = Counter(p['brand'] for p in out if p.get('brand'))
        log(f"\n✅ Elektron FIN — {len(out)} ok / {fail} fail → {OUT}")
        log("Top 15 marcas:")
        for b, n in brands.most_common(15):
            log(f"  {b[:35]:35} {n:>5}")
        await browser.close()

if __name__ == '__main__':
    mp = None
    if '--max' in sys.argv: mp = int(sys.argv[sys.argv.index('--max')+1])
    cc = 2  # reducido — concurrency alto dispara Cloudflare bot challenge ("Let's confirm you are human")
    if '--conc' in sys.argv: cc = int(sys.argv[sys.argv.index('--conc')+1])
    asyncio.run(main(max_products=mp, concurrency=cc))
