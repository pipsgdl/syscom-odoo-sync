#!/usr/bin/env python3
"""
CJP Telecom (tienda.cjp.mx) scraper.
Custom CMS, precios PÚBLICOS sin login.

Estructura:
  Categorías: /catalog/{slug}-{id}?p={page}
  Productos: /compra/{slug}-{id}
  36 productos/página

Output: /Users/ingfelipe/cotizador-web/caches/cjp_catalog.json
"""
import asyncio, json, re
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE = 'https://tienda.cjp.mx'
OUT = Path('/Users/ingfelipe/cotizador-web/caches/cjp_catalog.json')
LOG = Path('/tmp/cjp_scraper.log')

# 9 megacategorías detectadas en home
CATEGORIES = [
    'radiocomunicacion-25733',
    'iot-gps-telematica-y-senalizacion-audiovisual-30507',
    'deteccion-de-fuego-25768',
    'cableado-estructurado-25748',
    'automatizacion-e-intrusion-25735',
    'control-de-acceso-25730',
    'videovigilancia-25719',
    'energia-herramientas-30729',
    'otras-25639',
]

def log(m):
    msg = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(msg, flush=True)
    with open(LOG,'a') as f: f.write(msg+'\n')

def extract_brand_from_title(title):
    """Detecta marca al inicio del título (CJP usa formato 'MARCA modelo descripcion')."""
    if not title: return ''
    # Patrones comunes: "EPCOM POWERLINE JR-52", "HiLook by HIKVISION IPC", "GEWISS GW-44"
    known = ['EPCOM PROFESSIONAL','EPCOM POWERLINE','EPCOM TITANIUM','EPCOM',
             'HiLook by HIKVISION','HIKVISION','DAHUA','TIANDY','BOSCH','HONEYWELL',
             'GEWISS','PANDUIT','BELDEN','CHAROFIL','ROSSLARE','SUPREMA','ZKTECO',
             'AVIGILON','CDVI','SECOLARM','AXIS','VIVOTEK','ESI','EPSON','HID',
             'CYBERPOWER','TRIPP LITE','APC','MIKROTIK','UBIQUITI','TP-LINK',
             'CAME','DSC','OPTEX','FIRE-LITE','FIRE LITE','SIMPLEX','TOA',
             'KERI','LINKEDPRO','ANCHO','ANCO','SAXXON','ZK','ZKTECO','UNV',
             'ENSON','ENERGY','TIANDY','MILESIGHT','PROVISION','HIKMICRO']
    t_up = title.upper()
    # Match al inicio (primeras palabras)
    for b in sorted(known, key=lambda x:-len(x)):
        if t_up.startswith(b.upper()):
            return b.upper()
    # Match en cualquier parte (boundary)
    for b in sorted(known, key=lambda x:-len(x)):
        if re.search(r'(?<![A-Z])' + re.escape(b.upper()) + r'(?![A-Z])', t_up):
            return b.upper()
    # Fallback: primera palabra mayúsculas (probable marca)
    first_word = title.split()[0] if title.split() else ''
    if first_word.isupper() and len(first_word) >= 3:
        return first_word
    return ''

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled','--no-sandbox'])
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
            viewport={'width':1280,'height':900}, locale='es-MX')
        page = await ctx.new_page()

        log(f"CJP Telecom scraper — {len(CATEGORIES)} categorías")
        all_items = []
        seen = set()

        for ci, cat_slug in enumerate(CATEGORIES):
            log(f"\n[{ci+1}/{len(CATEGORIES)}] {cat_slug}")
            for pg in range(1, 200):  # cap 200 págs
                url = f'{BASE}/catalog/{cat_slug}?p={pg}'
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    await page.wait_for_timeout(1500)
                    products = await page.evaluate('''
                    () => {
                      const result = [];
                      // Cada producto tiene link a /compra/{slug}-{id}
                      const links = document.querySelectorAll('a[href*="/compra/"]');
                      const seen = new Set();
                      links.forEach(a => {
                        const href = a.href.split('?')[0].split('#')[0];
                        if (seen.has(href)) return;
                        seen.add(href);
                        // ID del producto al final de URL: -123456
                        const idMatch = href.match(/-(\\d+)$/);
                        // Buscar la tarjeta padre
                        const card = a.closest('div, article, li');
                        const text = (card?.innerText || '').trim();
                        // Title = primer línea no-numero
                        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
                        let title = '';
                        for (const ln of lines) {
                          if (ln.length > 20 && !/^\\$|^\\d+%/.test(ln)) {
                            title = ln; break;
                          }
                        }
                        // Precios: típicamente $XX.XX (oferta) y $YY.YY (lista)
                        const priceMatches = text.match(/\\$[\\d,]+\\.\\d{2}/g) || [];
                        const oferta = priceMatches[0] ? parseFloat(priceMatches[0].replace(/[$,]/g,'')) : 0;
                        const lista = priceMatches[1] ? parseFloat(priceMatches[1].replace(/[$,]/g,'')) : 0;
                        const descMatch = text.match(/(\\d+)%/);
                        result.push({
                          id: idMatch ? idMatch[1] : '',
                          url: href,
                          title: title.substring(0,200),
                          price: oferta || lista,
                          price_lista: lista,
                          descuento_pct: descMatch ? parseInt(descMatch[1]) : 0,
                          text_sample: text.substring(0,500),
                        });
                      });
                      return result;
                    }
                    ''')
                    if not products: break
                    new_count = 0
                    for prod in products:
                        if prod['id'] in seen: continue
                        seen.add(prod['id'])
                        # Extraer SKU del primer token del title (ej. "E8-TURBO-G2W EPCOM PROFESSIONAL...")
                        sku = ''
                        if prod['title']:
                            words = prod['title'].split()
                            for w in words[:3]:
                                if re.search(r'[A-Z]+[-\\.]?\\d', w) or re.search(r'\\d{2,}', w):
                                    sku = w.strip(',').strip('.')
                                    break
                        if not sku and prod['id']:
                            sku = prod['id']
                        # Brand
                        brand = extract_brand_from_title(prod['title'])
                        all_items.append({
                            'sku': sku,
                            'cjp_id': prod['id'],
                            'brand': brand,
                            'title': prod['title'],
                            'price': prod['price'],
                            'price_lista': prod['price_lista'],
                            'descuento_pct': prod['descuento_pct'],
                            'url': prod['url'],
                            'categoria': cat_slug,
                        })
                        new_count += 1
                    log(f"  pg={pg}: +{len(products)} (new={new_count}, total={len(all_items)})")
                    if new_count == 0: break
                except Exception as e:
                    log(f"  err pg={pg}: {str(e)[:80]}")
                    break
                await page.wait_for_timeout(700)
                # Guardar cada 10 págs
                if pg % 10 == 0:
                    OUT.parent.mkdir(parents=True, exist_ok=True)
                    json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump(all_items, open(OUT,'w'), ensure_ascii=False)

        from collections import Counter
        brands = Counter(i['brand'] for i in all_items if i.get('brand'))
        log(f"\n✅ CJP FIN — {len(all_items)} productos → {OUT}")
        log("\nTop 20 marcas:")
        for b,n in brands.most_common(20):
            log(f"  {b:<25} {n}")
        sin_brand = sum(1 for i in all_items if not i.get('brand'))
        log(f"\nSin marca: {sin_brand}")

        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
