#!/usr/bin/env python3
"""
TVC → Odoo Production Sync
==========================
TVC no tiene API pública — usa cache local TSV/JSON.
Cache fresco lo provee scraping manual o el script tvc_collect.py.

Match: modelo (TVC) ↔ default_code (Odoo)

Uso:
  python3 tvc_to_odoo_sync.py --diff
  python3 tvc_to_odoo_sync.py --full
"""
import json, subprocess, time, sys, os, glob
from datetime import datetime
from pathlib import Path

ODOO = 'https://ocean-tech.odoo.com/jsonrpc'
SCRIPTS_DIR = Path(__file__).parent
PROGRESS_FILE = SCRIPTS_DIR / 'tvc_sync_progress.json'
CACHE_GLOB = '/Volumes/HIKSEMI 512/Claude code/LICITABOT/data/tvc_catalogo_*.json'
TVC_PARTNER_ID = 618


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


def cargar_cache_mas_reciente():
    """Toma el .json TVC más reciente."""
    files = sorted(glob.glob(CACHE_GLOB))
    if not files:
        log("❌ No hay cache TVC en " + CACHE_GLOB)
        return [], None
    latest = files[-1]
    age_h = (time.time() - os.path.getmtime(latest)) / 3600
    log(f"Cache TVC: {Path(latest).name}  ({age_h:.0f}h antigüedad)")
    if age_h > 168:  # >1 semana
        log(f"  ⚠️ Cache más viejo de 7 días — actualizar manualmente")
    return json.load(open(latest)), latest


def main(diff_mode=True):
    started = datetime.now()
    progress = {'started': started.isoformat(), 'mode': 'diff' if diff_mode else 'full',
                'processed': 0, 'price_changed': 0, 'unchanged': 0,
                'errors': 0, 'sin_match_odoo': 0}

    items, cache_path = cargar_cache_mas_reciente()
    if not items: return

    # Listar supplierinfo TVC en Odoo
    log("Listando supplierinfo TVC...")
    sis = rpc('product.supplierinfo','search_read',[
        [['partner_id','=',TVC_PARTNER_ID]]
    ],{'fields':['id','product_tmpl_id','product_code','price'],'limit':50000})
    log(f"  Supplierinfo: {len(sis or [])}")

    # Index por modelo (TVC) y por sku (TVC) para flexibilidad
    sku_to_si = {(s['product_code'] or '').strip().upper(): s for s in (sis or [])}

    # Procesar
    to_update = []
    for it in items:
        modelo = (it.get('modelo') or '').strip().upper()
        sku = (it.get('sku') or '').strip().upper()
        if not modelo and not sku: continue
        progress['processed'] += 1

        # Try modelo primero, luego sku
        si = sku_to_si.get(modelo) or sku_to_si.get(sku)
        if not si:
            progress['sin_match_odoo'] += 1
            continue

        try:
            new_p = float((it.get('precio_neto') or it.get('precio_lista') or '0').replace(',',''))
        except:
            new_p = 0
        if new_p <= 0:
            progress['errors'] += 1
            continue

        old_p = si.get('price') or 0
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

    progress['cache_used'] = cache_path
    progress['ended'] = datetime.now().isoformat()
    progress['duration_sec'] = int((datetime.now() - started).total_seconds())
    with open(PROGRESS_FILE,'w') as f:
        json.dump(progress, f, indent=2)

    log(f"\n✅ TVC sync done")
    log(f"  proc: {progress['processed']:,}")
    log(f"  price_changed: {progress['price_changed']:,}")
    log(f"  unchanged: {progress['unchanged']:,}")
    log(f"  sin_match_odoo: {progress['sin_match_odoo']:,}")


if __name__ == '__main__':
    main(diff_mode='--diff' in sys.argv)
