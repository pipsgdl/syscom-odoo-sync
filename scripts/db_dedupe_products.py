#!/usr/bin/env python3
"""
db_dedupe_products.py
=====================
Job mensual de mantenimiento BD Odoo.

Detecta product.template duplicados por (default_code OR barcode) y mergea:
- el registro con id MENOR es el "maestro" (más antiguo, suele tener historial)
- los demás se desactivan (active=False, is_published=False)
- supplierinfo de los duplicados se mueve al maestro (si no existe ya uno
  con mismo partner_id + product_code)
- NO ejecuta unlink — preserva auditoría

USO:
    python3 db_dedupe_products.py --dry-run
    python3 db_dedupe_products.py --dry-run --limit=20
    python3 db_dedupe_products.py                    # LIVE

Cron sugerido (1er domingo del mes 4am):
    0 4 1-7 * 0  cd ~/syscom-odoo-sync/scripts && python3 db_dedupe_products.py --dry-run > logs/db_dedupe_$(date +%%Y%%m%%d).log 2>&1
"""
import xmlrpc.client
import json
import os
import argparse
from collections import defaultdict
from datetime import datetime

CFG_PATH = '/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json'
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPTS_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)


def connect():
    with open(CFG_PATH) as f:
        cfg = json.load(f)
    common = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(cfg['db'], cfg['username'], cfg['password'], {})
    if not uid:
        raise SystemExit("auth fallido")
    models = xmlrpc.client.ServerProxy(f"{cfg['url']}/xmlrpc/2/object", allow_none=True)

    def call(model, method, args, kwargs=None):
        return models.execute_kw(cfg['db'], uid, cfg['password'],
                                  model, method, args, kwargs or {})
    return call


def find_duplicates(call):
    """Devuelve dict {key: [tmpl_dict, ...]} con groups que tienen >1."""
    print("[1] leyendo todos los product.template con default_code o barcode...")
    all_p = []
    PAGE = 5000
    offset = 0
    while True:
        chunk = call('product.template', 'search_read',
                     [['|', ('default_code', '!=', False),
                            ('barcode', '!=', False)]],
                     {'fields': ['id', 'default_code', 'barcode', 'name',
                                 'is_published', 'active'],
                      'limit': PAGE, 'offset': offset})
        if not chunk:
            break
        all_p.extend(chunk)
        offset += len(chunk)
        if len(chunk) < PAGE:
            break

    print(f"  leidos {len(all_p)} templates")

    by_code = defaultdict(list)
    by_barcode = defaultdict(list)
    for p in all_p:
        c = (p.get('default_code') or '').strip().upper()
        b = (p.get('barcode') or '').strip()
        if c:
            by_code[c].append(p)
        if b:
            by_barcode[b].append(p)

    groups = {}
    for k, v in by_code.items():
        if len(v) > 1:
            groups[f'code:{k}'] = sorted(v, key=lambda x: x['id'])
    for k, v in by_barcode.items():
        if len(v) > 1:
            key = f'barcode:{k}'
            # evitar duplicar grupos que ya están por code
            ids = {p['id'] for p in v}
            already = any(ids.issubset({p['id'] for p in g})
                          for g in groups.values())
            if not already:
                groups[key] = sorted(v, key=lambda x: x['id'])
    return groups


def merge_group(call, master, dups, dry_run):
    """Mueve supplierinfo de dups → master, desactiva dups."""
    moved = 0
    skipped = 0
    deactivated = 0

    # supplierinfo del master
    master_si = call('product.supplierinfo', 'search_read',
                     [[('product_tmpl_id', '=', master['id'])]],
                     {'fields': ['partner_id', 'product_code']})
    master_keys = {(si['partner_id'][0] if si['partner_id'] else None,
                    si['product_code']) for si in master_si}

    for d in dups:
        si_list = call('product.supplierinfo', 'search_read',
                       [[('product_tmpl_id', '=', d['id'])]],
                       {'fields': ['id', 'partner_id', 'product_code']})
        for si in si_list:
            key = (si['partner_id'][0] if si['partner_id'] else None,
                   si['product_code'])
            if key in master_keys:
                skipped += 1
                continue
            if dry_run:
                print(f"     [DRY] mover supplierinfo {si['id']} "
                      f"({si['product_code']}) tmpl {d['id']} -> {master['id']}")
            else:
                call('product.supplierinfo', 'write',
                     [[si['id']], {'product_tmpl_id': master['id']}])
            moved += 1
            master_keys.add(key)

        if dry_run:
            print(f"     [DRY] desactivar tmpl {d['id']} ({d.get('default_code')})")
        else:
            call('product.template', 'write',
                 [[d['id']], {'is_published': False, 'active': False}])
        deactivated += 1
    return {'moved_si': moved, 'skipped_si': skipped, 'deactivated': deactivated}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', default=False)
    ap.add_argument('--limit', type=int, default=None,
                    help='máximo de grupos a procesar')
    args = ap.parse_args()

    call = connect()
    print(f"[+] dry_run={args.dry_run}")

    groups = find_duplicates(call)
    print(f"[+] {len(groups)} grupos duplicados encontrados")

    keys = list(groups.keys())
    if args.limit:
        keys = keys[:args.limit]

    totals = {'groups_processed': 0, 'moved_si': 0,
              'skipped_si': 0, 'deactivated': 0}
    detail = []

    for k in keys:
        g = groups[k]
        master = g[0]   # id menor
        dups = g[1:]
        print(f"\n[group] {k}  master_id={master['id']} dups={[d['id'] for d in dups]}")
        try:
            r = merge_group(call, master, dups, args.dry_run)
            for kk in ('moved_si', 'skipped_si', 'deactivated'):
                totals[kk] += r[kk]
            totals['groups_processed'] += 1
            detail.append({'key': k, 'master': master['id'],
                           'dups': [d['id'] for d in dups], **r})
        except Exception as e:
            print(f"  [err] {e}")

    print("\n=== RESUMEN ===")
    print(json.dumps(totals, indent=2))

    log = os.path.join(LOGS_DIR,
        f"db_dedupe_{datetime.now():%Y%m%d_%H%M%S}_"
        f"{'dry' if args.dry_run else 'live'}.json")
    with open(log, 'w') as f:
        json.dump({'args': vars(args), 'totals': totals,
                   'detail': detail[:200]}, f, indent=2)
    print(f"[+] log: {log}")


if __name__ == '__main__':
    main()
