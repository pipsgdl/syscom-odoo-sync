#!/usr/bin/env python3
"""
techsmart_buscar.py — Cotizador rápido puntual en Techsmart (para armar una cotización
al vuelo, sin tocar Odoo). Para el SYNC de producción a Odoo usa techsmart_to_odoo_sync.py.

⚠️ LEE PRIMERO: ~/syscom-odoo-sync/scripts/techsmart_cotiza/README.md
   (categorías/marcas válidas, qué combos SÍ tienen precio, gaps conocidos del catálogo).

Uso:
  techsmart_buscar.py "PROCESADORES" "AMD"                  # una categoria+marca
  techsmart_buscar.py "PROCESADORES" "T"                     # TODAS las marcas de esa categoria
  techsmart_buscar.py "MONITORES" "T" --contiene "27 180HZ"  # filtra por texto en la descripcion
  techsmart_buscar.py "TARJETAS DE VIDEO" "T" --min 300      # filtra por precio minimo (MXN)

Normaliza SIEMPRE a MXN (USD -> MXN con el tipo de cambio que el propio sitio muestra).

Nota (2026-07-20): el regex de precio exigía exactamente 2 decimales (`\d{2}`). Cualquier
producto mostrado como "Consultar precio", "Agotado", o con formato de precio distinto
(sin decimales, 1 decimal, etc.) no matcheaba -> precio_mxn=0.0 -> se descartaba
silenciosamente como si "no tuviera precio", indistinguible de un catálogo genuinamente
vacío. Fix: regex tolera 1-2 decimales opcionales, y ahora se reporta explícitamente
cuántos productos se encontraron pero se descartaron por no poder parsear su precio.
"""
import argparse, re, sys
import requests

ENV_LICITABOT = '/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env'


def load_env(p):
    d = {}
    for l in open(p, encoding='utf-8'):
        l = l.strip()
        if l and not l.startswith('#') and '=' in l:
            k, v = l.split('=', 1); d[k] = v
    return d


def login(env):
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': env['TECHSMART_BASE'] + '/Clientes/Catalogo'})
    r = s.post(env['TECHSMART_BASE'] + '/acciones/login.php',
               data={'rfc': env['TECHSMART_RFC'], 'usuario': env['TECHSMART_USER'],
                     'txtPass': env['TECHSMART_PASS'], 'movil': ''}, timeout=30)
    if '"error":"no"' not in r.text:
        raise RuntimeError(f"Login Techsmart fallo: {r.text[:150]}")
    return s


def tipo_cambio(h):
    m = re.search(r'Tipo de cambio:\s*\$([\d.]+)', h)
    return float(m.group(1)) if m else None


def parse_pagina(h, tc):
    out = []
    anc = list(re.finditer(r'cveProducto=([A-Z0-9._-]+)&TipoMoneda=\w+&Marca=([^"&]+)', h))
    for i, m in enumerate(anc):
        cod, marca = m.group(1), m.group(2)
        seg = h[m.start(): anc[i+1].start() if i+1 < len(anc) else m.start()+2600]
        d = re.search(r'text-card">\s*(.*?)\s*<br', seg, re.S)
        desc = re.sub(r'\s+', ' ', d.group(1)).strip() if d else ''
        # BUGFIX 2026-07-06: acepta USD o MXN -- NUNCA solo MXN (categorias enteras vienen en USD)
        # BUGFIX 2026-07-20: decimales opcionales/variables -- "Consultar precio"/"Agotado" ya no
        # se confunden con precio=0 parseado correctamente; se marcan como precio_parseable=False.
        pr = re.findall(r'\$([\d,]+(?:\.\d{1,2})?)\s*(USD|MXN)', seg)
        vals = [(float(x.replace(',', '')), cur) for x, cur in pr]
        precio_parseable = bool(vals)
        pl, pl_cur = vals[0] if vals else (0.0, 'MXN')
        pd, pd_cur = vals[1] if len(vals) > 1 else (pl, pl_cur)
        if pd_cur == 'USD': pd = round(pd * tc, 2)
        out.append({'codigo': cod, 'marca': marca, 'desc': desc, 'precio_mxn': pd,
                    'precio_parseable': precio_parseable})
    return out


def buscar(s, base, categoria, marca):
    h = s.get(base + '/Clientes/Catalogo', params={'txtCategoria': categoria, 'txtMarca': marca}, timeout=40).text
    tc = tipo_cambio(h)
    if not tc:
        raise RuntimeError("no se pudo leer el Tipo de cambio del sitio (revisa sesion/layout)")
    return parse_pagina(h, tc), tc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('categoria', help='ej. "PROCESADORES", "MONITORES" (ver README para la lista completa)')
    ap.add_argument('marca', help='ej. "AMD", o "T" para TODAS las marcas de esa categoria')
    ap.add_argument('--contiene', help='filtra por texto (case-insensitive) en la descripcion')
    ap.add_argument('--min', type=float, default=0, help='precio minimo en MXN')
    ap.add_argument('--max', type=float, default=float('inf'), help='precio maximo en MXN')
    args = ap.parse_args()

    env = load_env(ENV_LICITABOT)
    s = login(env)
    prods, tc = buscar(s, env['TECHSMART_BASE'], args.categoria, args.marca)

    total_encontrados = len(prods)
    no_parseables = [p for p in prods if not p['precio_parseable']]

    if args.contiene:
        prods = [p for p in prods if args.contiene.lower() in p['desc'].lower()]
    prods_con_precio = [p for p in prods if p['precio_parseable'] and p['precio_mxn'] > 0
                         and args.min <= p['precio_mxn'] <= args.max]
    prods_con_precio.sort(key=lambda p: p['precio_mxn'])

    print(f"Techsmart · {args.categoria} / {args.marca} · t.c. USD->MXN: {tc}")
    print(f"{len(prods_con_precio)} producto(s) con precio\n")
    for p in prods_con_precio:
        print(f"  {p['codigo']:20} ${p['precio_mxn']:>10,.2f} MXN  [{p['marca']}]  {p['desc'][:80]}")
    if not prods_con_precio:
        print("  (sin resultados con precio -- revisa el README: puede ser un combo sin catalogo,\n"
              "   o una categoria que Techsmart genuinamente no maneja para esta cuenta)")

    if no_parseables:
        print(f"\n⚠️  {len(no_parseables)} de {total_encontrados} producto(s) encontrados en esta "
              f"categoría/marca NO se pudieron leer con precio (posible 'Consultar precio', 'Agotado', "
              f"o formato inesperado) -- NO están incluidos arriba, verificar manualmente si son relevantes:")
        for p in no_parseables[:15]:
            print(f"  {p['codigo']:20} [{p['marca']}]  {p['desc'][:80]}")
        if len(no_parseables) > 15:
            print(f"  ... y {len(no_parseables) - 15} más")


if __name__ == "__main__":
    sys.exit(main() or 0)
