#!/usr/bin/env python3
"""
Anixter LATAM scraper — bypass Cloudflare via curl_cffi (TLS fingerprint Chrome).

Estrategia ganadora (validada 2026-05-08): curl_cffi con impersonate='chrome120'
pasa el WAF de Cloudflare devolviendo HTML completo (status 200, sin challenge).

Uso:
    /private/tmp/playwright-env/.venv/bin/python3 anixter_scraper.py [--max-pages N]

Salidas:
    /tmp/anixter_catalog.json     — lista de productos
    /tmp/anixter_progress.json    — progreso por categoria
    /tmp/anixter_scraper.log      — log de ejecucion
"""
import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from curl_cffi import requests

OUT = Path('/tmp/anixter_catalog.json')
PROGRESS = Path('/tmp/anixter_progress.json')
LOG = Path('/tmp/anixter_scraper.log')

KNOWN_CATS = [
    'https://www.anixter.com/es_mx/products/Wire-and-Cable/c/CATEGORY_01',
    'https://www.anixter.com/es_mx/products/Communications/c/CATEGORY_02',
    'https://www.anixter.com/es_mx/products/Security/c/CATEGORY_03',
    'https://www.anixter.com/es_mx/products/Networking/c/CATEGORY_04',
    'https://www.anixter.com/es_mx/products/Hardware-and-Supplies/c/CATEGORY_05',
    'https://www.anixter.com/es_mx/products/Industrial-Communication-and-Control/c/CATEGORY_06',
    'https://www.anixter.com/es_mx/products/Networking/Switches/c/SW',
    'https://www.anixter.com/es_mx/products/Video-Surveillance/Surveillance-Cameras/c/IB',
    'https://www.anixter.com/es_mx/products/Video-Surveillance/Video-Recorders/c/PG',
    'https://www.anixter.com/es_mx/products/Access-Control/Readers/c/GG',
    'https://www.anixter.com/es_mx/products/Access-Control/Biometric-Scanners/c/AD',
    'https://www.anixter.com/es_mx/products/Access-Control/Cards/c/SI',
    'https://www.anixter.com/es_mx/products/Copper-Cabling-Infrastructure/Voice-and-Data-Cable/c/CV',
    'https://www.anixter.com/es_mx/products/Fiber-Optic-Cabling-Infrastructure/Indoor-Fiber-Optic-Cable/c/FM',
    'https://www.anixter.com/es_mx/products/Networking/Power-Over-Ethernet/c/PR',
    'https://www.anixter.com/es_mx/products/Electrified-Door-Hardware/Electrified-Door-Locks/c/ED',
    'https://www.anixter.com/es_mx/products/Mechanical-Door-Hardware/Door-Locks/c/DL',
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="120", "Not(A:Brand";v="24", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

PROD_RE = re.compile(r'/products/([^/"\s]+)/([^/"\s]+)/([^/"\s]+)/p/([^/?#"\s]+)')

_log_fh = None

def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    global _log_fh
    if _log_fh is None:
        _log_fh = open(LOG, 'a')
    _log_fh.write(line + "\n")
    _log_fh.flush()


def parse_products(html):
    """Extrae productos de un listado HTML. Dedupe por (sku|pid)."""
    seen = set()
    out = []
    for m in PROD_RE.finditer(html):
        sku, brand, cat_slug, pid = m.group(1), m.group(2), m.group(3), m.group(4)
        k = f"{sku}|{pid}"
        if k in seen:
            continue
        seen.add(k)
        out.append({
            'sku': sku.replace('%20', ' '),
            'brand': brand,
            'cat_slug': cat_slug,
            'pid': pid,
            'url': f"https://www.anixter.com/es_mx/products/{sku}/{brand}/{cat_slug}/p/{pid}",
        })
    return out


def fetch(session, url, max_retries=4):
    """Fetch con reintento + backoff exponencial. Retorna HTML o None."""
    for attempt in range(max_retries):
        try:
            r = session.get(url, headers=HEADERS, impersonate="chrome120", timeout=45)
            if r.status_code == 200 and 'Just a moment' not in r.text and 'Attention Required' not in r.text:
                return r.text
            log(f"    status={r.status_code} CF? len={len(r.text)} attempt={attempt+1}")
        except Exception as e:
            log(f"    err attempt={attempt+1}: {str(e)[:80]}")
        time.sleep(5 + attempt * 5 + random.uniform(0, 3))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-pages', type=int, default=50)
    args = ap.parse_args()

    log(f"START — {len(KNOWN_CATS)} categorias, max_pages={args.max_pages}")
    session = requests.Session()
    # Warm-up: una visita a la home para pegarle cookies
    try:
        session.get("https://www.anixter.com/es_mx", headers=HEADERS, impersonate="chrome120", timeout=30)
        log("warm-up home OK")
    except Exception as e:
        log(f"warm-up err: {e}")

    all_products = []
    global_seen = set()

    for ci, base in enumerate(KNOWN_CATS):
        cat_id = base.split('/c/')[1] if '/c/' in base else '?'
        log(f"[{ci+1}/{len(KNOWN_CATS)}] cat={cat_id}")
        cat_seen = set()
        for pg in range(1, args.max_pages + 1):
            url = f'{base}?q=%3Arelevance&page={pg}'
            html = fetch(session, url)
            if html is None:
                log(f"  pg={pg} fetch fail, skip cat")
                break
            products = parse_products(html)
            if not products:
                log(f"  pg={pg} 0 productos, fin cat")
                break
            page_new_in_cat = 0
            for prod in products:
                k = f"{prod['sku']}|{prod['pid']}"
                if k in cat_seen:
                    continue
                cat_seen.add(k)
                page_new_in_cat += 1
                if k not in global_seen:
                    global_seen.add(k)
                    all_products.append(prod)
            log(f"  pg={pg} parsed={len(products)} new_cat={page_new_in_cat} cat_total={len(cat_seen)} global={len(all_products)}")
            if page_new_in_cat == 0:
                log(f"  pg={pg} sin productos nuevos en cat, fin cat")
                break
            # Throttle aleatorio entre paginas (3-7s)
            time.sleep(3 + random.uniform(0, 4))

        # Persistir despues de cada cat
        with open(OUT, 'w') as f:
            json.dump(all_products, f, ensure_ascii=False)
        with open(PROGRESS, 'w') as f:
            json.dump({
                'cats_done': ci + 1,
                'total_cats': len(KNOWN_CATS),
                'products': len(all_products),
                'last_cat': cat_id,
                'updated': datetime.now().isoformat(),
            }, f)
        log(f"  cat={cat_id} fin: {len(cat_seen)} prods | global={len(all_products)}")
        # Pausa entre categorias (5-10s)
        time.sleep(5 + random.uniform(0, 5))

    # Resumen
    brands = Counter(p['brand'] for p in all_products)
    log(f"DONE — {len(all_products)} productos totales")
    for b, n in brands.most_common(25):
        log(f"  {b:30} {n:>4}")
    if _log_fh:
        _log_fh.close()


if __name__ == '__main__':
    main()
