#!/usr/bin/env python3
"""
db_create_missing_products.py
==============================
Job semanal de mantenimiento BD Odoo.

Toma los SKUs sin match (sin_match_odoo) de los últimos sync runs de
CVA/TVC/Tecnosinergia y los crea como product.template + supplierinfo
en Odoo, después de intentar fuzzy match por nombre + barcode.

USO:
    python3 db_create_missing_products.py --vendor=cva --dry-run --limit=100
    python3 db_create_missing_products.py --vendor=all --dry-run
    python3 db_create_missing_products.py --vendor=cva                 # LIVE

REGLAS:
    - SIEMPRE correr primero con --dry-run y revisar el log
    - batch ≤ 100 por proveedor
    - antes de crear, fuzzy match por nombre (>=85 ratio) y por barcode exacto
    - si match: solo agrega supplierinfo, NO crea duplicado
    - si NO match: crea product.template con default_code, name, list_price=0,
      type='consu', sale_ok=True, purchase_ok=True, is_published=False
    - NUNCA hardcodea credenciales — lee odoo_config_prod.json

Cron sugerido:
    0 3 * * 0  cd ~/syscom-odoo-sync/scripts && python3 db_create_missing_products.py --vendor=all --dry-run > logs/db_create_$(date +%%Y%%m%%d).log 2>&1
"""
import xmlrpc.client
import json
import os
import sys
import argparse
from datetime import datetime
from difflib import SequenceMatcher

CFG_PATH = '/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json'
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPTS_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

VENDOR_PROGRESS = {
    'cva':   'cva_sync_progress.json',
    'tvc':   'tvc_sync_progress.json',
    'tecno': 'tecno_sync_progress.json',
}

# Vendor → partner_id en Odoo (res.partner). Ajustar según realidad.
# Si no existe partner se busca por nombre exacto.
VENDOR_PARTNER_NAME = {
    'cva':   'CVA',
    'tvc':   'TVC',
    'tecno': 'Tecnosinergia',
}


def load_cfg():
    with open(CFG_PATH) as f:
        return json.load(f)


def connect():
    cfg = load_cfg()
    common = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(cfg['db'], cfg['username'], cfg['password'], {})
    if not uid:
        raise SystemExit("No se pudo autenticar en Odoo")
    models = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/object", allow_none=True)
    return cfg, uid, models


def make_call(cfg, uid, models):
    def call(model, method, args, kwargs=None):
        return models.execute_kw(cfg['db'], uid, cfg['password'],
                                  model, method, args, kwargs or {})
    return call


def load_sin_match_skus(vendor):
    """
    Lee el progress.json del vendor. Si tiene lista de SKUs en
    'sin_match_skus' o similar la regresa. Si solo tiene contador,
    intenta leer un cache adyacente (vendor_sin_match.json).
    Devuelve lista de dicts: [{'sku', 'name', 'barcode', 'price', 'stock'}]
    """
    progress_file = VENDOR_PROGRESS.get(vendor)
    if not progress_file:
        return []
    path = os.path.join(SCRIPTS_DIR, progress_file)
    if not os.path.exists(path):
        print(f"[!] no existe {path}")
        return []

    with open(path) as f:
        data = json.load(f)

    # Caso A: SKUs ya están listados
    for key in ('sin_match_skus', 'sin_match_list', 'unmatched_products', 'sin_match_odoo_list'):
        if key in data and isinstance(data[key], list):
            return data[key]

    # Caso B: hay un archivo aparte vendor_sin_match.json
    extra = os.path.join(SCRIPTS_DIR, f'{vendor}_sin_match.json')
    if os.path.exists(extra):
        with open(extra) as f:
            d = json.load(f)
        if isinstance(d, list):
            return d
        if isinstance(d, dict) and 'items' in d:
            return d['items']

    print(f"[!] {progress_file} reporta sin_match_odoo={data.get('sin_match_odoo','?')}, "
          f"pero no hay lista de SKUs. El sync debe persistir 'sin_match_skus' "
          f"para que este job los pueda crear. Saltando vendor={vendor}.")
    return []


def fuzzy_match(call, sku, name, barcode):
    """
    Intenta encontrar un product.template existente:
    1) por barcode exacto
    2) por default_code case-insensitive (no debería pasar si el sync lo hizo bien)
    3) por nombre con ratio >= 0.85
    Devuelve product.template id o None.
    """
    if barcode:
        ids = call('product.template', 'search',
                   [[('barcode', '=', barcode)]], {'limit': 1})
        if ids:
            return ids[0]
    if sku:
        ids = call('product.template', 'search',
                   [[('default_code', '=ilike', sku)]], {'limit': 1})
        if ids:
            return ids[0]
    if name and len(name) > 8:
        # Busca por substring de las primeras 4 palabras
        words = name.split()[:4]
        if words:
            q = ' '.join(words)
            cands = call('product.template', 'search_read',
                         [[('name', 'ilike', q)]],
                         {'fields': ['id', 'name'], 'limit': 30})
            best = None
            best_r = 0.0
            for c in cands:
                r = SequenceMatcher(None, name.lower(), c['name'].lower()).ratio()
                if r > best_r:
                    best_r = r
                    best = c
            if best and best_r >= 0.85:
                return best['id']
    return None


def get_or_find_partner(call, vendor):
    name = VENDOR_PARTNER_NAME.get(vendor)
    if not name:
        return None
    ids = call('res.partner', 'search',
               [[('name', '=ilike', name), ('supplier_rank', '>', 0)]],
               {'limit': 1})
    if ids:
        return ids[0]
    # fallback: cualquier partner con ese nombre
    ids = call('res.partner', 'search', [[('name', '=ilike', name)]], {'limit': 1})
    return ids[0] if ids else None


def process_vendor(call, vendor, dry_run, limit):
    print(f"\n[=== {vendor.upper()} ===]")
    items = load_sin_match_skus(vendor)
    if not items:
        print(f"[{vendor}] 0 items para procesar")
        return {'vendor': vendor, 'total': 0, 'matched': 0, 'created': 0, 'skipped': 0}

    if limit:
        items = items[:limit]

    partner_id = get_or_find_partner(call, vendor)
    if not partner_id:
        print(f"[!] no encontré partner para {vendor} — saltando creación de supplierinfo")

    stats = {'vendor': vendor, 'total': len(items),
             'matched': 0, 'created': 0, 'skipped': 0, 'errors': 0}

    BATCH = 100
    for i, it in enumerate(items[:BATCH * (1 + len(items) // BATCH)]):
        sku = (it.get('sku') or it.get('default_code') or '').strip()
        name = (it.get('name') or it.get('nombre') or '').strip()
        barcode = (it.get('barcode') or it.get('ean') or '').strip()
        price = float(it.get('price') or it.get('precio') or 0)

        if not sku or not name:
            stats['skipped'] += 1
            continue

        try:
            existing = fuzzy_match(call, sku, name, barcode)
        except Exception as e:
            print(f"[err] match {sku}: {e}")
            stats['errors'] += 1
            continue

        if existing:
            stats['matched'] += 1
            print(f"  [match] {sku} -> tmpl_id={existing}")
            if not dry_run and partner_id:
                # Verifica si ya tiene supplierinfo de este partner
                exists_si = call('product.supplierinfo', 'search_count',
                                 [[('product_tmpl_id', '=', existing),
                                   ('partner_id', '=', partner_id)]])
                if not exists_si:
                    call('product.supplierinfo', 'create', [{
                        'product_tmpl_id': existing,
                        'partner_id': partner_id,
                        'product_code': sku,
                        'price': price,
                    }])
            continue

        # Crear nuevo product.template
        vals = {
            'name': name[:200],
            'default_code': sku,
            'type': 'consu',
            'sale_ok': True,
            'purchase_ok': True,
            'is_published': False,
            'list_price': 0.0,
        }
        if barcode:
            vals['barcode'] = barcode

        if dry_run:
            print(f"  [DRY] crearía: {sku} | {name[:60]}")
            stats['created'] += 1
        else:
            try:
                new_id = call('product.template', 'create', [vals])
                if partner_id:
                    call('product.supplierinfo', 'create', [{
                        'product_tmpl_id': new_id,
                        'partner_id': partner_id,
                        'product_code': sku,
                        'price': price,
                    }])
                stats['created'] += 1
                print(f"  [LIVE] creado tmpl_id={new_id} sku={sku}")
            except Exception as e:
                stats['errors'] += 1
                print(f"  [err] crear {sku}: {e}")

    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vendor', default='all',
                    choices=['all', 'cva', 'tvc', 'tecno'])
    ap.add_argument('--dry-run', action='store_true', default=False)
    ap.add_argument('--limit', type=int, default=None,
                    help='máximo de items a procesar por vendor')
    args = ap.parse_args()

    cfg, uid, models = connect()
    call = make_call(cfg, uid, models)
    print(f"[+] conectado a {cfg['url']} db={cfg['db']} uid={uid}")
    print(f"[+] dry_run={args.dry_run} vendor={args.vendor} limit={args.limit}")

    vendors = ['cva', 'tvc', 'tecno'] if args.vendor == 'all' else [args.vendor]
    summary = []
    for v in vendors:
        try:
            summary.append(process_vendor(call, v, args.dry_run, args.limit))
        except Exception as e:
            print(f"[FATAL] {v}: {e}")
            summary.append({'vendor': v, 'fatal': str(e)})

    print("\n=== RESUMEN ===")
    for s in summary:
        print(s)

    log_path = os.path.join(LOGS_DIR,
        f"db_create_{datetime.now():%Y%m%d_%H%M%S}_"
        f"{'dry' if args.dry_run else 'live'}.json")
    with open(log_path, 'w') as f:
        json.dump({'args': vars(args), 'summary': summary}, f, indent=2)
    print(f"[+] log: {log_path}")


if __name__ == '__main__':
    main()
