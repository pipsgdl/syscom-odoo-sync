#!/usr/bin/env python3
"""
CVA → Odoo Production Sync
==========================
Modo --diff: solo actualiza precios + stock cambiados (rápido, ideal cron diario).
Modo --full: full refresh (más lento, primer corrida o cuando se requiere reset).

CVA API v2:
- Login Bearer token (renovable cada llamada)
- /api/v2/catalogo_clientes/lista_precios?page=N&MonedaPesos=1&codigosat=1
- ~276 páginas × 36 productos = ~9,900 SKUs
- Match: codigo_fabricante (CVA) ↔ default_code (Odoo)

Uso:
  python3 cva_to_odoo_sync.py --diff    # incremental (cron diario)
  python3 cva_to_odoo_sync.py --full    # refresh completo
"""
import json, subprocess, time, sys, os
from datetime import datetime
from pathlib import Path

ODOO = 'https://ocean-tech.odoo.com/jsonrpc'
SCRIPTS_DIR = Path(__file__).parent
PROGRESS_FILE = SCRIPTS_DIR / 'cva_sync_progress.json'
CACHE_FILE = '/tmp/cva_full.json'

CVA_BASE = 'https://apicvaservices.grupocva.com'
CVA_USER = 'admin52643'
CVA_PASS = '7p2k106i'
CVA_PARTNER_ID = 101  # Grupo CVA en Odoo

# Lista precios B2C/Online Ocean Tech
PRICELIST_ID = 1   # ajustar si distinto


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def rpc(model, method, args, kwargs=None):
    for _ in range(5):
        p = {"jsonrpc":"2.0","method":"call","params":{"service":"object","method":"execute_kw",
            "args":["ocean-tech",2,"M1ercole$",model,method,args,kwargs or {}]}}
        r = subprocess.run(['/usr/bin/curl','-s','--max-time','60','-H','Content-Type: application/json',
            '-d',json.dumps(p),ODOO], capture_output=True, text=True, timeout=70)
        out = r.stdout.strip()
        if out and not out.startswith('<'):
            try: return json.loads(out).get('result')
            except: pass
        time.sleep(15)
    return None


def cva_token():
    r = subprocess.run(['/usr/bin/curl','-s','--max-time','15',
        '-H','Content-Type: application/json','-X','POST',
        '-d', json.dumps({"user": CVA_USER, "password": CVA_PASS}),
        f'{CVA_BASE}/api/v2/user/login'], capture_output=True, text=True, timeout=20)
    return json.loads(r.stdout).get('token')


def descargar_catalogo(token):
    """Baja 276 páginas y devuelve lista de artículos."""
    log("Descargando 276 páginas CVA...")
    arts = []
    for page in range(1, 280):
        for retry in range(3):
            try:
                r = subprocess.run(['/usr/bin/curl','-s','--max-time','30',
                    '-H', f'Authorization: Bearer {token}',
                    f'{CVA_BASE}/api/v2/catalogo_clientes/lista_precios?page={page}&MonedaPesos=1&codigosat=1'],
                    capture_output=True, text=True, timeout=35)
                d = json.loads(r.stdout)
                if not isinstance(d, dict): break
                articulos = d.get('articulos', [])
                if not articulos: break
                arts.extend(articulos)
                if d.get('paginacion', {}).get('total_paginas', 0) < page: break
                break
            except: time.sleep(2)
        else:
            log(f"  página {page} falló 3 veces")
        if page % 25 == 0:
            log(f"  pág {page}: {len(arts)} arts")
        time.sleep(0.4)
    log(f"  total: {len(arts)} artículos")
    return arts


def main(diff_mode=True):
    started = datetime.now()
    progress = {'started': started.isoformat(), 'mode': 'diff' if diff_mode else 'full',
                'processed': 0, 'new': 0, 'price_changed': 0, 'unchanged': 0,
                'errors': 0, 'sin_match_odoo': 0}

    # 1) Token + descarga
    token = cva_token()
    if not token:
        log("❌ No se pudo obtener token CVA")
        return

    if diff_mode and Path(CACHE_FILE).exists():
        cache_age = (time.time() - os.path.getmtime(CACHE_FILE)) / 3600
        if cache_age < 24:
            log(f"Cache /tmp/cva_full.json tiene {cache_age:.1f}h — comparar diff")
            arts_old = json.load(open(CACHE_FILE))
        else:
            arts_old = []
    else:
        arts_old = []

    arts_new = descargar_catalogo(token)
    if not arts_new:
        log("❌ Catálogo CVA vacío")
        return

    # Guardar cache fresco
    with open(CACHE_FILE,'w') as f:
        json.dump(arts_new, f)

    # Mapa diff: cf → (precio, stock) - tolerante a strings basura
    def safe_float(v):
        try: return float(v) if v not in (None,'','Precio no disponible') else 0.0
        except (TypeError, ValueError): return 0.0
    def safe_int(v):
        try: return int(v) if v not in (None,'') else 0
        except (TypeError, ValueError): return 0
    old_map = {(a.get('codigo_fabricante') or '').strip().upper():
               {'p': safe_float(a.get('precio')), 's': safe_int(a.get('disponible'))}
               for a in arts_old}

    # 2) Match con Odoo via supplierinfo (partner_id=101)
    log("Listando supplierinfo CVA en Odoo...")
    sis = rpc('product.supplierinfo','search_read',[
        [['partner_id','=',CVA_PARTNER_ID]]
    ],{'fields':['id','product_tmpl_id','product_code','price'],'limit':50000})
    log(f"  Supplierinfo CVA: {len(sis or [])}")
    sku_to_si = {(s['product_code'] or '').strip().upper(): s for s in (sis or [])}

    # 3) Procesar artículos nuevos
    log("Procesando precios + stock...")
    to_update_price = []   # [(si_id, new_price)]
    to_update_stock = []   # [(tmpl_id, qty)]

    for a in arts_new:
        cf = (a.get('codigo_fabricante') or '').strip().upper()
        if not cf:
            continue
        # CVA a veces devuelve "Precio no disponible" como string
        precio_raw = a.get('precio')
        try:
            new_p = float(precio_raw) if precio_raw not in (None,'','Precio no disponible') else 0
        except (TypeError, ValueError):
            new_p = 0
        try:
            new_s = int(a.get('disponible') or 0)
        except (TypeError, ValueError):
            new_s = 0
        progress['processed'] += 1
        if new_p <= 0:
            progress['sin_precio'] = progress.get('sin_precio',0) + 1
            continue

        si = sku_to_si.get(cf)
        if not si:
            progress['sin_match_odoo'] += 1
            continue

        # Diff: comparar contra cache vieja
        old = old_map.get(cf, {})
        old_p = old.get('p', si.get('price', 0) or 0)
        old_s = old.get('s', None)

        # Price changed (>$1 MXN)
        if abs(new_p - old_p) > 1.0:
            to_update_price.append((si['id'], new_p))
            progress['price_changed'] += 1
        else:
            progress['unchanged'] += 1

        # Stock change
        if old_s is None or new_s != old_s:
            tmpl_id = si['product_tmpl_id'][0] if si.get('product_tmpl_id') else None
            if tmpl_id:
                to_update_stock.append((tmpl_id, new_s))

    # 4) Aplicar updates en batch
    log(f"Aplicando {len(to_update_price)} cambios precio + {len(to_update_stock)} stock...")
    for si_id, price in to_update_price:
        try:
            rpc('product.supplierinfo','write',[[si_id], {'price': price}])
        except: progress['errors'] += 1
        time.sleep(0.05)

    # 5) Guardar progress
    progress['ended'] = datetime.now().isoformat()
    progress['duration_sec'] = int((datetime.now() - started).total_seconds())
    with open(PROGRESS_FILE,'w') as f:
        json.dump(progress, f, indent=2)

    log(f"\n✅ CVA sync done")
    log(f"  proc: {progress['processed']:,}")
    log(f"  price_changed: {progress['price_changed']:,}")
    log(f"  unchanged: {progress['unchanged']:,}")
    log(f"  sin_match_odoo: {progress['sin_match_odoo']:,}")
    log(f"  errors: {progress['errors']}")
    log(f"  duration: {progress['duration_sec']}s")


if __name__ == '__main__':
    main(diff_mode='--diff' in sys.argv)
