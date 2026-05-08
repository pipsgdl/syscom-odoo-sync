#!/usr/bin/env python3
"""
db_recompute_pricelists.py
==========================
Re-aplica fórmula de margen a productos publicados, sincronizando
product.pricelist.item.fixed_price con base en product.template.list_price
y un margen configurable por pricelist.

PRICELISTS Ocean Tech (verificar con Felipe):
    1 = Default
    2 = Menudeo  → margen 1.30 sobre cost (o list_price)
    3 = Online   → margen 1.10 sobre list_price
    4 = Proyecto → margen 1.00 (cost)

USO:
    python3 db_recompute_pricelists.py --pricelist-id=3 --dry-run
    python3 db_recompute_pricelists.py --pricelist-id=3 --limit=500
    python3 db_recompute_pricelists.py --pricelist-id=3                # LIVE

Cron sugerido (trimestral):
    0 5 1 1,4,7,10 *  cd ~/syscom-odoo-sync/scripts && python3 db_recompute_pricelists.py --pricelist-id=3 --dry-run > logs/pricelist_q_$(date +%%Y%%m%%d).log 2>&1
"""
import xmlrpc.client
import json
import os
import argparse
from datetime import datetime

CFG_PATH = '/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json'
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPTS_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# margen por pricelist (multiplicador sobre product.template.list_price)
MARGINS = {
    1: 1.00,
    2: 1.30,
    3: 1.10,
    4: 1.00,
}


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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pricelist-id', type=int, required=True)
    ap.add_argument('--dry-run', action='store_true', default=False)
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()

    call = connect()
    margin = MARGINS.get(args.pricelist_id, 1.00)
    print(f"[+] pricelist_id={args.pricelist_id} margen={margin} dry_run={args.dry_run}")

    # productos publicados
    domain = [('is_published', '=', True), ('list_price', '>', 0)]
    pub_ids = call('product.template', 'search', [domain],
                   {'limit': args.limit or 0})
    print(f"[+] {len(pub_ids)} productos publicados con list_price>0")

    BATCH = 500
    stats = {'total': len(pub_ids), 'updated': 0,
             'created': 0, 'unchanged': 0, 'errors': 0}

    for i in range(0, len(pub_ids), BATCH):
        ids = pub_ids[i:i + BATCH]
        prods = call('product.template', 'read', [ids],
                     {'fields': ['id', 'default_code', 'list_price']})
        for p in prods:
            target = round(p['list_price'] * margin, 2)
            try:
                # busca pricelist.item para ese tmpl
                items = call('product.pricelist.item', 'search_read',
                             [[('pricelist_id', '=', args.pricelist_id),
                               ('product_tmpl_id', '=', p['id'])]],
                             {'fields': ['id', 'fixed_price'], 'limit': 1})
                if items:
                    cur = items[0]['fixed_price']
                    if abs(cur - target) < 0.01:
                        stats['unchanged'] += 1
                        continue
                    if args.dry_run:
                        print(f"  [DRY] tmpl {p['id']} ({p.get('default_code')}): "
                              f"{cur} -> {target}")
                    else:
                        call('product.pricelist.item', 'write',
                             [[items[0]['id']], {'fixed_price': target}])
                    stats['updated'] += 1
                else:
                    if args.dry_run:
                        print(f"  [DRY] crear item tmpl {p['id']} = {target}")
                    else:
                        call('product.pricelist.item', 'create', [{
                            'pricelist_id': args.pricelist_id,
                            'product_tmpl_id': p['id'],
                            'applied_on': '1_product',
                            'compute_price': 'fixed',
                            'fixed_price': target,
                        }])
                    stats['created'] += 1
            except Exception as e:
                print(f"  [err] tmpl {p['id']}: {e}")
                stats['errors'] += 1

        print(f"  ...{min(i+BATCH, len(pub_ids))}/{len(pub_ids)} stats={stats}")

    print("\n=== RESUMEN ===")
    print(json.dumps(stats, indent=2))

    log = os.path.join(LOGS_DIR,
        f"db_pricelist_{args.pricelist_id}_{datetime.now():%Y%m%d_%H%M%S}_"
        f"{'dry' if args.dry_run else 'live'}.json")
    with open(log, 'w') as f:
        json.dump({'args': vars(args), 'stats': stats}, f, indent=2)
    print(f"[+] log: {log}")


if __name__ == '__main__':
    main()
