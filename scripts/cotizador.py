#!/usr/bin/env python3
"""
Cotizador rápido para Ocean Tech.

Uso:
    cotizador.py SKU                # busca producto por SKU exacto o parcial
    cotizador.py --marca HIKVISION  # lista proveedores que venden marca
    cotizador.py --buscar "camara 4mp poe"   # búsqueda texto en nombre

Output: tabla con proveedores que lo venden + precio + stock disponible.
"""
import subprocess, json, sys, time

ODOO='https://ocean-tech.odoo.com/jsonrpc'

def rpc(m,me,a,k=None):
    p={"jsonrpc":"2.0","method":"call","params":{"service":"object","method":"execute_kw",
       "args":["ocean-tech",2,"M1ercole$",m,me,a,k or {}]}}
    r=subprocess.run(['/usr/bin/curl','-s','--max-time','30','-H','Content-Type: application/json',
       '-d',json.dumps(p),ODOO],capture_output=True,text=True,timeout=35)
    out=r.stdout.strip()
    try: return json.loads(out).get('result')
    except: return None

def buscar_sku(sku):
    """Busca producto por default_code y muestra todos sus supplierinfo."""
    print(f"\n🔍 Buscando SKU: {sku}\n")
    tmpls = rpc('product.template','search_read',[
        [['default_code','=ilike', f'%{sku}%']]
    ],{'fields':['id','name','default_code','list_price'],'limit':20})
    if not tmpls:
        print("  ❌ Sin match en Odoo")
        return
    for t in tmpls:
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📦 [{t['default_code']}] {t['name'][:80]}")
        print(f"   list_price: ${t.get('list_price',0):,.2f}")
        # Supplierinfos
        sis = rpc('product.supplierinfo','search_read',[
            [['product_tmpl_id','=',t['id']]]
        ],{'fields':['partner_id','product_code','price','min_qty','delay']})
        if not sis:
            print("   (sin proveedores)")
        else:
            print(f"   PROVEEDORES ({len(sis)}):")
            print(f"   {'Proveedor':<28} {'Código':<22} {'Precio':>12} {'Min':>5} {'Entrega':>8}")
            for s in sorted(sis, key=lambda x: x.get('price') or 9999999):
                pn = s['partner_id'][1] if s.get('partner_id') else '?'
                pc = s.get('product_code','-') or '-'
                pr = s.get('price') or 0
                mq = s.get('min_qty') or 1
                dl = s.get('delay') or 0
                print(f"   {pn[:28]:<28} {pc[:22]:<22} ${pr:>11,.2f} {mq:>5.0f} {dl:>7.0f}d")

def buscar_marca(marca):
    """Lista proveedores que venden esa marca, con conteo de SKUs."""
    print(f"\n🏷️  Marca: {marca}\n")
    # Buscar value en attribute Marca (id=10)
    v = rpc('product.attribute.value','search_read',[
        [['attribute_id','=',10],['name','=ilike', f'%{marca}%']]
    ],{'fields':['id','name'],'limit':5})
    if not v:
        print("  ❌ Marca no encontrada en Odoo")
        return
    for val in v:
        # Buscar attribute_lines con esa value
        lines = rpc('product.template.attribute.line','search_read',[
            [['attribute_id','=',10],['value_ids','=',val['id']]]
        ],{'fields':['product_tmpl_id'],'limit':5000})
        if not lines: continue
        tmpl_ids = [l['product_tmpl_id'][0] for l in lines if l.get('product_tmpl_id')]
        # Conteo por partner via supplierinfo
        from collections import Counter
        provs = Counter()
        for off in range(0, len(tmpl_ids), 200):
            chunk = tmpl_ids[off:off+200]
            sis = rpc('product.supplierinfo','search_read',[
                [['product_tmpl_id','in',chunk]]
            ],{'fields':['partner_id'],'limit':5000})
            for s in (sis or []):
                if s.get('partner_id'):
                    provs[s['partner_id'][1]] += 1
        print(f"📊 {val['name']} — {len(tmpl_ids)} SKUs en Odoo")
        print(f"   {'Proveedor':<32} {'SKUs':>8}")
        for p, n in provs.most_common(20):
            print(f"   {p[:32]:<32} {n:>8}")

def buscar_texto(query):
    """Búsqueda full-text en nombre del producto."""
    print(f"\n🔎 Búsqueda texto: '{query}'\n")
    tmpls = rpc('product.template','search_read',[
        [['name','ilike',query]]
    ],{'fields':['id','name','default_code','list_price'],'limit':20})
    if not tmpls:
        print("  ❌ Sin match")
        return
    print(f"  {len(tmpls)} matches:\n")
    print(f"  {'SKU':<24} {'Precio':>12}  Nombre")
    for t in tmpls:
        sku = t.get('default_code','-') or '-'
        pr = t.get('list_price',0) or 0
        print(f"  {sku[:24]:<24} ${pr:>11,.2f}  {t['name'][:60]}")
    print("\nUsa: cotizador.py SKU para detalle de proveedores.")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == '--marca':
        buscar_marca(sys.argv[2])
    elif sys.argv[1] == '--buscar':
        buscar_texto(' '.join(sys.argv[2:]))
    else:
        buscar_sku(sys.argv[1])
