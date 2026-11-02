#!/usr/bin/env python3
"""
Fibras Ópticas de México (FOM) scraper.
WooCommerce + Cloudflare → Playwright con anti-detection flags.

URL: https://fibrasopticasdemexico.com
Categorías: /categoria-producto/{slug}/
Producto: /producto/{slug}/
WC Store API: /wp-json/wc/store/v1/products (bloqueada por CF a curl directo,
pero accesible vía Playwright que pasa el challenge una vez)

10K+ productos.
"""
import asyncio, json, re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://fibrasopticasdemexico.com'
OUT = Path('/Users/ingfelipe/cotizador-web/caches/fom_catalog.json')
LOG = Path('/tmp/fom_scraper.log')

def log(m):
    msg = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(msg, flush=True)
    with open(LOG,'a') as f: f.write(msg+'\n')

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled','--no-sandbox',
                  '--disable-dev-shm-usage'])
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            viewport={'width':1280,'height':900},
            locale='es-MX',
            timezone_id='America/Mexico_City')
        page = await ctx.new_page()

        # 1) Pasar Cloudflare challenge una vez
        log("Pasando Cloudflare challenge...")
        try:
            await page.goto(BASE, wait_until='domcontentloaded', timeout=45000)
        except Exception as e:
            log(f"  goto warn: {e}")
        await page.wait_for_timeout(8000)
        title = await page.title()
        log(f"  Title: {title}")
        if 'momento' in title.lower() or 'cloudflare' in title.lower():
            log("  🔴 SIGUE EN CHALLENGE — esperar más")
            await page.wait_for_timeout(8000)
            title = await page.title()
            log(f"  Retry title: {title}")

        # 2) Usar WC Store API ahora que tenemos cookies CF
        log("Probando WC Store API...")
        api_works = await page.evaluate('''
        async () => {
          const r = await fetch('/wp-json/wc/store/v1/products?per_page=1', {credentials: 'include'});
          if (r.status !== 200) return {ok:false, status:r.status};
          const total = r.headers.get('x-wp-total');
          const totalPages = r.headers.get('x-wp-totalpages');
          const items = await r.json();
          return {ok:true, total, totalPages, sample: items[0]};
        }
        ''')
        log(f"  API status: {api_works}")

        all_items = []
        if api_works.get('ok'):
            total = int(api_works.get('total') or 0)
            log(f"  Total productos: {total}")
            per_page = 100
            num_pages = (total + per_page - 1) // per_page
            log(f"  Páginas: {num_pages} (per_page={per_page})")

            # 3) Recorrer todas las páginas usando fetch desde el browser (mantiene cookies)
            for pg in range(1, num_pages + 1):
                items = await page.evaluate(f'''
                async () => {{
                  const r = await fetch('/wp-json/wc/store/v1/products?per_page={per_page}&page={pg}', {{credentials: 'include'}});
                  if (r.status !== 200) return [];
                  return await r.json();
                }}
                ''')
                for it in items:
                    sku = (it.get('sku') or '').strip()
                    if not sku: continue
                    prices = it.get('prices') or {}
                    price_minor = prices.get('price') or '0'
                    try: price = float(price_minor) / (10 ** int(prices.get('currency_minor_unit', 2)))
                    except: price = 0
                    cats = [c.get('name','') for c in (it.get('categories') or [])]
                    # Brand: WC tiene attribute "Marca" o se infiere
                    brand = ''
                    for attr in it.get('attributes', []) or []:
                        if (attr.get('name') or '').lower() in ('marca','brand','fabricante'):
                            terms = attr.get('terms') or []
                            if terms: brand = terms[0].get('name','').strip()
                            break
                    all_items.append({
                        'id': it.get('id'),
                        'sku': sku,
                        'brand': brand.upper(),
                        'title': (it.get('name') or '').replace('&#8243;','"'),
                        'price': price,
                        'currency': prices.get('currency_code','MXN'),
                        'url': it.get('permalink'),
                        'categories': cats,
                        'in_stock': it.get('is_in_stock'),
                    })
                log(f"  pg={pg}/{num_pages}: +{len(items)} (total: {len(all_items)})")
                if pg % 5 == 0:
                    OUT.parent.mkdir(parents=True, exist_ok=True)
                    json.dump(all_items, open(OUT,'w'), ensure_ascii=False)
                await page.wait_for_timeout(800)
        else:
            log(f"  ❌ WC API no funciona. Status: {api_works.get('status')}")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        from collections import Counter
        brands = Counter(i['brand'] for i in all_items if i.get('brand'))
        log(f"\n✅ FOM FIN — {len(all_items)} productos → {OUT}")
        log("\nTop 20 marcas (si vienen como atributo):")
        for b,n in brands.most_common(20):
            log(f"  {b:<25} {n}")
        sin_brand = sum(1 for i in all_items if not i.get('brand'))
        log(f"\nSin marca: {sin_brand} (FOM marca propia)")

        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
