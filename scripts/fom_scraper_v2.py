#!/usr/bin/env python3
"""
FOM v2 — Stealth + paciencia con Cloudflare.

Estrategia:
1. Usar playwright-stealth para evadir CF JS challenge
2. Navegar a una categoría (no home, suele tener más tiempo de challenge)
3. Esperar hasta 30s al título correcto
4. Scrapear HTML directamente (no API), porque API REST sigue bloqueada
"""
import asyncio, json, re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright
try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

BASE = 'https://fibrasopticasdemexico.com'
OUT = Path('/Users/ingfelipe/cotizador-web/caches/fom_catalog.json')
LOG = Path('/tmp/fom_scraper.log')

# Categorías top FOM
CATEGORIES = [
    'cables-de-fibra-optica',
    'herrajes',
    'cierres-de-empalme',
    'distribuidores',
    'ensambles-opticos',
    'consumibles-para-fibra-optica',
    'conectividad',
    'empalmadoras-y-cortadoras',
    'registros-para-fibra-optica',
    'gabinetes',
    'transceivers',
    'equipo-de-medicion',
    'kits-para-fibra-optica',
    'splitters-y-cwdm',
    'herramientas',
]

def log(m):
    msg = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(msg, flush=True)
    with open(LOG,'a') as f: f.write(msg+'\n')

async def wait_past_cf(page, timeout=60):
    """Espera a que CF challenge pase. Retorna True si pasó."""
    for i in range(timeout // 2):
        await page.wait_for_timeout(2000)
        title = await page.title()
        if 'momento' not in title.lower() and 'cloudflare' not in title.lower() and 'attention' not in title.lower():
            return True
    return False

async def main():
    log(f"FOM v2 — stealth={HAS_STEALTH}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled','--no-sandbox',
                  '--disable-dev-shm-usage','--disable-features=IsolateOrigins,site-per-process'])
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            viewport={'width':1280,'height':900},
            locale='es-MX',
            timezone_id='America/Mexico_City',
            extra_http_headers={
                'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            })
        page = await ctx.new_page()
        if HAS_STEALTH:
            await stealth_async(page)

        log("Visitando categoría inicial para resolver CF...")
        try:
            await page.goto(f'{BASE}/categoria-producto/cables-de-fibra-optica/',
                            wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            log(f"  goto warn: {e}")

        passed = await wait_past_cf(page, 60)
        title = await page.title()
        log(f"  Title final: {title}")
        if not passed:
            log("  🔴 No pasó CF — abortar")
            await browser.close()
            return

        all_items = []
        seen = set()

        for ci, cat_slug in enumerate(CATEGORIES):
            log(f"\n[{ci+1}/{len(CATEGORIES)}] {cat_slug}")
            for pg in range(1, 50):
                url = f'{BASE}/categoria-producto/{cat_slug}/' + (f'page/{pg}/' if pg > 1 else '')
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    await page.wait_for_timeout(2500)
                    # Esperar tarjetas WooCommerce
                    try: await page.wait_for_selector('.product, .woocommerce-loop-product__title, [class*="product"]', timeout=8000)
                    except: pass
                    products = await page.evaluate('''
                    () => {
                      const result = [];
                      // WooCommerce selectores típicos
                      const cards = document.querySelectorAll('li.product, .wc-block-grid__product, .product-item, [class*="product-grid"] > div, [data-product-id]');
                      cards.forEach(card => {
                        const link = card.querySelector('a[href*="/producto/"]');
                        if (!link) return;
                        const titleEl = card.querySelector('.woocommerce-loop-product__title, h2, h3, [class*="product-title"]');
                        const priceEl = card.querySelector('.price, [class*="price"]');
                        const skuEl = card.querySelector('.sku, [class*="sku"]');
                        // ID del producto
                        const id = card.getAttribute('data-product-id') ||
                                   (card.id || '').replace('product-','');
                        result.push({
                          id: id || '',
                          url: link.href.split('?')[0],
                          title: (titleEl?.innerText || '').trim().substring(0,200),
                          price_text: (priceEl?.innerText || '').trim().substring(0,80),
                          sku_text: (skuEl?.innerText || '').trim(),
                          text_sample: card.innerText.substring(0,300),
                        });
                      });
                      return result;
                    }
                    ''')
                    if not products: break
                    new = 0
                    for prod in products:
                        url_id = prod['url'].split('/').rstrip('/').split('/')[-1] if isinstance(prod['url'].split('/'), list) else prod['url']
                        slug = prod['url'].rstrip('/').split('/')[-1]
                        if slug in seen: continue
                        seen.add(slug)
                        # Extraer precio número
                        prices_found = re.findall(r'\$[\d,]+\.\d{2}', prod['price_text'])
                        price = 0
                        if prices_found:
                            try: price = float(prices_found[-1].replace('$','').replace(',',''))
                            except: pass
                        # SKU: del slug o texto
                        sku = prod.get('sku_text','').strip() or slug
                        all_items.append({
                            'sku': sku[:50],
                            'fom_id': prod['id'],
                            'brand': '',  # se llena después
                            'title': prod['title'] or slug.replace('-',' ').title(),
                            'price': price,
                            'url': prod['url'],
                            'categoria': cat_slug,
                        })
                        new += 1
                    log(f"  pg={pg}: +{len(products)} (new={new}, total={len(all_items)})")
                    if new == 0: break
                except Exception as e:
                    log(f"  err pg={pg}: {str(e)[:80]}")
                    break
                await page.wait_for_timeout(1200)
                if pg % 5 == 0:
                    OUT.parent.mkdir(parents=True, exist_ok=True)
                    json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        # Detectar marca de FOM (marcas propias en título)
        FOM_BRANDS = ['WAVEOPTICS','MEXFOSERV','AERIALGRIP','FUSIONGUARD','SPLICEXP','FOM',
                      'WAVE OPTICS','MEX FOSERV','AERIAL GRIP','FUSION GUARD','SPLICE XP']
        for it in all_items:
            t = it['title'].upper()
            for b in sorted(FOM_BRANDS, key=lambda x:-len(x)):
                if b in t:
                    it['brand'] = b
                    break
            if not it['brand']:
                # Por defecto, marca propia FOM
                it['brand'] = 'FOM'
        json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        from collections import Counter
        brands = Counter(i['brand'] for i in all_items if i['brand'])
        log(f"\n✅ FOM FIN — {len(all_items)} productos → {OUT}")
        log("\nTop marcas:")
        for b,n in brands.most_common(15):
            log(f"  {b:<25} {n}")

        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
