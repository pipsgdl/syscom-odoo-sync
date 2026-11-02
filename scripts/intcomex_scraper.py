#!/usr/bin/env python3
"""
Intcomex MX Webstore scraper v2 — selectores reales detectados.
- Card class: .js-skuProduct
- Anchor: a[href*="/Product/Detail/"]
- 13 productos/pág, paginación "P.N" en URL ?p=N
"""
import asyncio, json, sys, re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://store.intcomex.com/es-XMX'
OUT = Path('/tmp/intcomex_catalog.json')
PROGRESS = Path('/tmp/intcomex_progress.json')

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

async def main(max_cats=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
            viewport={'width':1280,'height':900}
        )
        page = await ctx.new_page()

        # Set country cookie via direct nav
        await page.goto(f'{BASE}/Home', wait_until='domcontentloaded', timeout=45000)

        log("Fase 1: descubrir categorías reales...")
        cat_urls = await page.evaluate('''
        () => Array.from(document.querySelectorAll('a[href*="/Products/Category/"]'))
            .map(a => a.href.split('?')[0])
            .filter((v,i,arr) => arr.indexOf(v) === i)
        ''')
        log(f"  Categorías: {len(cat_urls)}")
        if max_cats: cat_urls = cat_urls[:max_cats]

        log("\nFase 2: productos paginados...")
        all_products = []
        seen = set()

        for ci, base_url in enumerate(cat_urls):
            for pg in range(1, 50):
                # Intcomex paginación: ?p=N o agregar al final
                url = f'{base_url}?r=True&p={pg}'
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=45000)
                    # Esperar productos a cargar (AJAX)
                    try:
                        await page.wait_for_selector('a[href*="/Product/Detail/"]', timeout=8000)
                    except: pass
                    products = await page.evaluate('''
                    () => {
                      const result = [];
                      const cards = document.querySelectorAll('.js-skuProduct, [class*="js-skuProduct"]');
                      cards.forEach(card => {
                        const a = card.querySelector('a[href*="/Product/Detail/"]');
                        if (!a) return;
                        const text = card.innerText || '';
                        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
                        const skuMatch = text.match(/SKU:\\s*([\\w]+)/i) || text.match(/Intcomex SKU:\\s*([\\w]+)/i);
                        const partMatch = text.match(/Número de parte:\\s*([\\w./-]+)/i);
                        // Detail/123456 → 123456
                        const detailMatch = a.href.match(/\\/Detail\\/([\\w]+)/);
                        result.push({
                          intcomex_id: detailMatch?.[1] || '',
                          sku: skuMatch?.[1] || '',
                          part_number: partMatch?.[1] || '',
                          // Brand suele ser la 2da línea, título la 1ra
                          title: lines[0] || '',
                          brand: lines[1] || '',
                          url: a.href.split('?')[0],
                        });
                      });
                      return result;
                    }
                    ''')
                    if not products: break
                    new_count = 0
                    for prod in products:
                        key = prod['intcomex_id'] or prod['sku']
                        if not key or key in seen: continue
                        seen.add(key)
                        all_products.append(prod)
                        new_count += 1
                    if new_count == 0: break
                except Exception as e:
                    log(f"  err {base_url} pg={pg}: {str(e)[:80]}")
                    break
                await page.wait_for_timeout(800)

            if (ci+1) % 3 == 0 or ci == len(cat_urls)-1:
                log(f"  [{ci+1}/{len(cat_urls)}] {len(all_products):,} productos")
                with open(OUT,'w') as f: json.dump(all_products, f, ensure_ascii=False)
                with open(PROGRESS,'w') as f:
                    json.dump({'cats_done': ci+1, 'total_cats': len(cat_urls), 'products': len(all_products)}, f)

        with open(OUT,'w') as f: json.dump(all_products, f, ensure_ascii=False)
        from collections import Counter
        brands = Counter(p['brand'] for p in all_products if p.get('brand'))
        log(f"\n✅ Intcomex FIN — {len(all_products)} productos")
        log("\nTop 20 marcas:")
        for b, n in brands.most_common(20):
            log(f"  {b[:35]:35} {n:>5}")
        await browser.close()

if __name__ == '__main__':
    mc = None
    if '--max-cats' in sys.argv: mc = int(sys.argv[sys.argv.index('--max-cats')+1])
    asyncio.run(main(max_cats=mc))
