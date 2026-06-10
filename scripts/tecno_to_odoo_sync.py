#!/usr/bin/env python3
"""
Tecnosinergia → Odoo Production Sync
====================================
API v3 token sigue funcionando aunque cuenta web esté pausada.

Uso:
  python3 tecno_to_odoo_sync.py --diff    # incremental
  python3 tecno_to_odoo_sync.py --full    # refresh completo
"""
import json, subprocess, time, sys, os
from datetime import datetime
from pathlib import Path

ODOO = 'https://ocean-tech.odoo.com/jsonrpc'
SCRIPTS_DIR = Path(__file__).parent
PROGRESS_FILE = SCRIPTS_DIR / 'tecno_sync_progress.json'
CACHE_FILE = '/tmp/tecno_full.json'

TECNO_TOKEN = "$2y$10$o.q7onp5Edv8jN6B8CPum.8z3lLjGLGcwFryEX4t.z.bZAaX46h5m"
TECNO_PARTNER_ID = 92


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


def descargar_catalogo():
    log("Descargando catálogo Tecno v3...")
    r = subprocess.run(['/usr/bin/curl','-sL','--max-time','120',
        '-H', f'api-token: {TECNO_TOKEN}',
        '-H', 'Content-Type: application/json',
        '-H', 'Accept: application/json',
        'https://api.tecnosinergia.info/v3/item/list'],
        capture_output=True, text=True, timeout=130)
    try:
        items = json.loads(r.stdout).get('data', [])
        log(f"  {len(items)} items")
        return items
    except Exception as e:
        log(f"  ❌ Error: {e}")
        return []


def main(diff_mode=True):
    started = datetime.now()
    progress = {'started': started.isoformat(), 'mode': 'diff' if diff_mode else 'full',
                'processed': 0, 'price_changed': 0, 'unchanged': 0,
                'errors': 0, 'sin_match_odoo': 0}

    items = descargar_catalogo()
    if not items: return

    # Cache diff
    if diff_mode and Path(CACHE_FILE).exists():
        items_old = json.load(open(CACHE_FILE))
    else:
        items_old = []

    with open(CACHE_FILE,'w') as f:
        json.dump(items, f)

    old_map = {(a.get('sku') or a.get('code') or '').strip().upper():
               {'p': float(a.get('sale_price') or a.get('regular_price') or 0), 's': int(a.get('available') or 0)}
               for a in items_old}

    # Listar supplierinfo Tecno
    log("Listando supplierinfo Tecnosinergia...")
    sis = rpc('product.supplierinfo','search_read',[
        [['partner_id','=',TECNO_PARTNER_ID]]
    ],{'fields':['id','product_tmpl_id','product_code','price'],'limit':50000})
    log(f"  Supplierinfo: {len(sis or [])}")
    sku_to_si = {(s['product_code'] or '').strip().upper(): s for s in (sis or [])}

    # Procesar
    to_update = []
    for it in items:
        sku = (it.get('sku') or it.get('code') or '').strip().upper()
        if not sku: continue
        progress['processed'] += 1
        new_p = float(it.get('sale_price') or it.get('regular_price') or 0)
        si = sku_to_si.get(sku)
        if not si:
            progress['sin_match_odoo'] += 1
            continue

        old_p = old_map.get(sku, {}).get('p', si.get('price', 0) or 0)
        if abs(new_p - old_p) > 1.0:
            to_update.append((si['id'], new_p))
            progress['price_changed'] += 1
        else:
            progress['unchanged'] += 1

    log(f"Aplicando {len(to_update)} cambios precio...")
    for si_id, price in to_update:
        try:
            rpc('product.supplierinfo','write',[[si_id], {'price': price}])
        except: progress['errors'] += 1
        time.sleep(0.05)

    progress['ended'] = datetime.now().isoformat()
    progress['duration_sec'] = int((datetime.now() - started).total_seconds())
    with open(PROGRESS_FILE,'w') as f:
        json.dump(progress, f, indent=2)

    log(f"\n✅ Tecno sync done")
    log(f"  proc: {progress['processed']:,}")
    log(f"  price_changed: {progress['price_changed']:,}")
    log(f"  unchanged: {progress['unchanged']:,}")
    log(f"  sin_match_odoo: {progress['sin_match_odoo']:,}")
    log(f"  errors: {progress['errors']}")


if __name__ == '__main__':
    main(diff_mode='--diff' in sys.argv)
