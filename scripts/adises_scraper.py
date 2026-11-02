#!/usr/bin/env python3
"""
Adises MX scraper v2 — selectores reales detectados.
- WordPress + Elementor + JetListing
- 40 productos/página
- URL pagination: /catalogo/{cat}/page/N/
- URL producto: /productos/{cat}/{slug}/  (filtrar 2 niveles)
"""
import asyncio, json, sys
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://adises.com.mx'
OUT = Path('/tmp/adises_catalog.json')
PROGRESS = Path('/tmp/adises_progress.json')

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

async def main(max_cats=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
            viewport={'width':1280,'height':900}
        )
        page = await ctx.new_page()

        log("Fase 1: catálogo URLs...")
        await page.goto(BASE, wait_until='domcontentloaded', timeout=45000)
        cat_urls = await page.evaluate('''
        () => {
          const all = Array.from(document.querySelectorAll('a[href*="/catalogo/"]'))
            .map(a => a.href.split('?')[0]);
          // Quitar duplicados
          return [...new Set(all)].filter(u => u.match(/\\/catalogo\\/[^/]+\\/?$/) || u.match(/\\/catalogo\\/[^/]+\\/[^/]+\\/?$/));
        }
        ''')
        log(f"  Subcategorías: {len(cat_urls)}")
        if max_cats: cat_urls = cat_urls[:max_cats]

        log("\nFase 2: productos por subcategoría...")
        all_products = []
        seen = set()

        for ci, base_url in enumerate(cat_urls):
            base_url = base_url.rstrip('/') + '/'
            for pg in range(1, 60):
                url = base_url if pg == 1 else f'{base_url}page/{pg}/'
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                    await page.wait_for_timeout(2500)
                    # Scroll para forzar lazy-load de los .marca_txt
                    for _ in range(3):
                        await page.evaluate('window.scrollBy(0, 1000)')
                        await page.wait_for_timeout(600)
                    products = await page.evaluate('''
                    () => {
                      // Productos REALES son /productos/{cat}/{slug}/ (2 niveles)
                      const links = Array.from(document.querySelectorAll('a[href*="/productos/"]'))
                        .filter(a => a.href.match(/\\/productos\\/[^/]+\\/[^/]+\\/?$/));
                      const seenLocal = new Set();
                      const result = [];
                      links.forEach(a => {
                        const url = a.href.split('?')[0];
                        if (seenLocal.has(url)) return;
                        seenLocal.add(url);
                        // Walk-up: encontrar la sección padre que contenga .marca_txt (selector limpio Adises)
                        let brand = '';
                        let title = '';
                        let mod = '';
                        let n = a;
                        for (let depth = 0; depth < 15 && n; depth++) {
                          const m = n.querySelector?.('.marca_txt');
                          if (m) {
                            brand = (m.innerText || '').trim();
                            // dentro del mismo contenedor: buscar título y MOD
                            const titleEl = n.querySelector('h2, h3, [class*="product-title"], .jet-woo-builder-archive-product-title');
                            if (titleEl) title = (titleEl.innerText || '').trim();
                            const containerText = (n.innerText || '');
                            const modMatch = containerText.match(/MOD[:.\\s]+([\\w./-]+)/i);
                            if (modMatch) mod = modMatch[1];
                            break;
                          }
                          n = n.parentElement;
                        }
                        if (!title) {
                          // fallback: del link mismo
                          title = (a.innerText || '').trim();
                        }
                        const slug = url.split('/').filter(Boolean).pop() || '';
                        result.push({ url, slug, mod, brand, title });
                      });
                      return result;
                    }
                    ''')
                    if not products: break
                    new_count = 0
                    for prod in products:
                        key = prod.get('slug') or prod.get('url')
                        if not key or key in seen: continue
                        seen.add(key)
                        all_products.append(prod)
                        new_count += 1
                    if new_count == 0: break
                except Exception as e:
                    log(f"  err {base_url} pg={pg}: {str(e)[:80]}")
                    break
                await page.wait_for_timeout(700)

            if (ci+1) % 5 == 0:
                log(f"  [{ci+1}/{len(cat_urls)}] {len(all_products):,}")
                with open(OUT,'w') as f: json.dump(all_products, f, ensure_ascii=False)
                with open(PROGRESS,'w') as f:
                    json.dump({'cats_done': ci+1, 'total_cats': len(cat_urls), 'products': len(all_products)}, f)

        with open(OUT,'w') as f: json.dump(all_products, f, ensure_ascii=False)
        from collections import Counter
        brands = Counter(p['brand'] for p in all_products if p.get('brand'))
        log(f"\n✅ Adises FIN — {len(all_products)} productos")
        log("\nTop 20 marcas:")
        for b, n in brands.most_common(20):
            log(f"  {b[:35]:35} {n:>5}")
        await browser.close()

if __name__ == '__main__':
    mc = None
    if '--max-cats' in sys.argv: mc = int(sys.argv[sys.argv.index('--max-cats')+1])
    asyncio.run(main(max_cats=mc))
