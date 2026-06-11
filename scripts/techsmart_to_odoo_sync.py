#!/usr/bin/env python3
"""
Techsmart → Odoo Production Sync (8º distribuidor)
==================================================
Mecánica REVERSE-ENGINEERED y probada (2026-06-11):
  - Login:  POST {BASE}/acciones/login.php  (rfc, usuario, txtPass)  -> cookie PHPSESSID
  - Portal cliente:  {BASE}/Clientes/...
  - Lista de precios (oficial, ASÍNCRONA):
        POST {BASE}/Clientes/ListaPreciosExcel.php  (txtMarca='T', Categoria='TODAS', dato='ok')
        -> mientras procesa: {"error":"si","msg":"...intente en unos minutos"}
        -> al terminar:      "ListaPreciosTechsmart_YYYYMMDDHHMMSS.xlsx"
        descarga: GET {BASE}/Clientes/ListaPreciosMovil/<archivo>
        columnas: Modelo | Descripción | Precio | Precio c/Desc | Moneda | Garantía | Disponible en

⚠️ BLOQUEO (2026-06-11): la cuenta admin/RFC autentica pero los precios salen en $0.00
   y la lista de precios se genera VACÍA. Techsmart debe ACTIVAR la lista de precios de
   distribuidor para esta cuenta (registro formal). Hasta entonces este conector ABORTA
   por el candado anti-ceros (nunca escribe $0 a Odoo).

Uso (cuando Techsmart habilite precios):
  python3 techsmart_to_odoo_sync.py --dry-run     # extrae y valida, NO escribe Odoo
  python3 techsmart_to_odoo_sync.py --diff        # incremental a Odoo

Credenciales: .env de LICITABOT (TECHSMART_*) + Odoo por env (ODOO_DB/ODOO_UID/ODOO_PASS).
Sin secretos hardcodeados.
"""
import os, re, sys, json, time, zipfile, subprocess
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
import requests

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
ENV_LICITABOT = '/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env'
ODOO_URL = 'https://ocean-tech.odoo.com/jsonrpc'
CACHE = '/tmp/techsmart_full.json'
PARTNER_NAME = 'Techsmart'
MIN_FILAS = 200          # candado: si la lista trae menos, algo está mal -> abortar
MAX_PCT_CEROS = 0.05     # candado: si >5% de precios son 0 -> abortar (cuenta sin precios)

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def load_env(path):
    d = {}
    for l in open(path, encoding='utf-8'):
        l = l.strip()
        if l and not l.startswith('#') and '=' in l:
            k, v = l.split('=', 1); d[k] = v
    return d

# ---------- Techsmart ----------
def login(env):
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest',
                      'Referer': env['TECHSMART_BASE'] + '/'})
    r = s.post(env['TECHSMART_BASE'] + '/acciones/login.php',
               data={'rfc': env['TECHSMART_RFC'], 'usuario': env['TECHSMART_USER'],
                     'txtPass': env['TECHSMART_PASS'], 'movil': ''}, timeout=30)
    if '"error":"no"' not in r.text:
        raise RuntimeError(f"Login Techsmart falló: {r.text[:120]}")
    log("Login Techsmart OK")
    return s

def exportar_lista(s, base, marca='T', categoria='TODAS', max_wait=420):
    """Dispara la generación async y espera el archivo. Devuelve bytes del xlsx."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        r = s.post(base + '/Clientes/ListaPreciosExcel.php',
                   data={'txtMarca': marca, 'Categoria': categoria, 'dato': 'ok'}, timeout=90)
        m = re.search(r'([A-Za-z0-9_]+\.xlsx)', r.text)
        if m:
            fn = m.group(1)
            rr = s.get(base + '/Clientes/ListaPreciosMovil/' + fn, timeout=120)
            if rr.content[:2] == b'PK':
                log(f"Lista descargada: {fn} ({len(rr.content)} b)")
                return rr.content
        time.sleep(20)
    raise TimeoutError("La lista de precios no terminó de generarse a tiempo")

def parse_xlsx(content):
    """Parser tolerante a PHPExcel (estilos inválidos) + inlineStr. -> list[dict]"""
    z = zipfile.ZipFile(__import__('io').BytesIO(content))
    sst = []
    if 'xl/sharedStrings.xml' in z.namelist():
        t = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in t.iter(NS + 'si'):
            sst.append(''.join(n.text or '' for n in si.iter(NS + 't')))
    def cval(c):
        ty = c.get('t')
        if ty == 'inlineStr':
            return ''.join(x.text or '' for x in c.iter(NS + 't'))
        v = c.find(NS + 'v')
        if v is None: return ''
        return sst[int(v.text)] if ty == 's' else v.text
    sx = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    rows = [[cval(c) for c in row.iter(NS + 'c')] for row in sx.iter(NS + 'row')]
    out = []
    for r in rows:
        if len(r) >= 5 and r[0] and r[0] not in ('Modelo', 'Techsmart', 'Lista de precios') and r[2]:
            out.append({'modelo': r[0].strip(), 'descripcion': (r[1] or '').strip(),
                        'precio': _num(r[2]), 'precio_desc': _num(r[3]) if len(r) > 3 else _num(r[2]),
                        'moneda': (r[4] or '').strip().upper() if len(r) > 4 else 'MXN',
                        'disponible': (r[6] or '').strip() if len(r) > 6 else ''})
    return out

def _num(x):
    try: return float(re.sub(r'[^\d.]', '', str(x)))
    except: return 0.0

def tipo_cambio(s, base):
    """Lee el tipo de cambio USD->MXN del portal (fallback 17.42)."""
    try:
        h = s.get(base + '/Clientes', timeout=30).text
        m = re.search(r'Tipo de cambio:\s*\$([\d.]+)', h)
        return float(m.group(1)) if m else 17.42
    except: return 17.42

# ---------- Odoo ----------
def rpc(env, model, method, args, kwargs=None):
    for _ in range(5):
        p = {"jsonrpc": "2.0", "method": "call", "params": {"service": "object",
             "method": "execute_kw", "args": [env['ODOO_DB'], int(env['ODOO_UID']),
             env['ODOO_PASS'], model, method, args, kwargs or {}]}}
        r = subprocess.run(['/usr/bin/curl', '-s', '--max-time', '60', '-H',
            'Content-Type: application/json', '-d', json.dumps(p), ODOO_URL],
            capture_output=True, text=True, timeout=70)
        out = r.stdout.strip()
        if out and not out.startswith('<'):
            try: return json.loads(out).get('result')
            except: pass
        time.sleep(10)
    return None

def get_partner_id(env):
    res = rpc(env, 'res.partner', 'search', [[['name', 'ilike', PARTNER_NAME], ['supplier_rank', '>', 0]]], {'limit': 1})
    if res: return res[0]
    res = rpc(env, 'res.partner', 'search', [[['name', 'ilike', PARTNER_NAME]]], {'limit': 1})
    return res[0] if res else None

# ---------- Main ----------
def main(dry_run=True, diff=True):
    env = load_env(ENV_LICITABOT)
    for k in ('ODOO_DB', 'ODOO_UID', 'ODOO_PASS'):
        env.setdefault(k, os.environ.get(k, ''))
    base = env['TECHSMART_BASE']
    s = login(env)
    tc = tipo_cambio(s, base)
    log(f"Tipo de cambio USD->MXN: {tc}")
    productos = parse_xlsx(exportar_lista(s, base))
    log(f"Productos en lista: {len(productos)}")

    # --- CANDADOS DE SEGURIDAD (nunca escribir basura a Odoo) ---
    if len(productos) < MIN_FILAS:
        log(f"❌ ABORTA: solo {len(productos)} productos (< {MIN_FILAS}). "
            f"La cuenta no tiene la lista de precios provisionada por Techsmart.")
        return 2
    ceros = sum(1 for p in productos if p['precio_desc'] <= 0)
    if ceros / len(productos) > MAX_PCT_CEROS:
        log(f"❌ ABORTA: {ceros}/{len(productos)} precios en $0. "
            f"Techsmart aún no habilita precios para esta cuenta.")
        return 2

    # normalizar costo a MXN
    for p in productos:
        p['costo_mxn'] = round(p['precio_desc'] * (tc if p['moneda'] == 'USD' else 1), 2)
    json.dump(productos, open(CACHE, 'w'), ensure_ascii=False)

    if dry_run:
        log(f"DRY-RUN ok: {len(productos)} productos válidos, "
            f"{sum(1 for p in productos if p['moneda']=='USD')} en USD. No se escribió Odoo.")
        for p in productos[:5]:
            log(f"  {p['modelo']:18} {p['costo_mxn']:>10} MXN  {p['descripcion'][:40]}")
        return 0

    # --- Escritura Odoo (supplierinfo por product_code) ---
    pid = get_partner_id(env)
    if not pid:
        log("❌ No existe el partner Techsmart en Odoo (créalo primero)."); return 3
    sis = rpc(env, 'product.supplierinfo', 'search_read',
              [[['partner_id', '=', pid]]],
              {'fields': ['id', 'product_code', 'price'], 'limit': 100000}) or []
    by_sku = {(x['product_code'] or '').strip().upper(): x for x in sis}
    cambios = sinmatch = 0
    for p in productos:
        si = by_sku.get(p['modelo'].upper())
        if not si: sinmatch += 1; continue
        if abs((si.get('price') or 0) - p['costo_mxn']) > 1.0:
            rpc(env, 'product.supplierinfo', 'write', [[si['id']], {'price': p['costo_mxn']}])
            cambios += 1
        time.sleep(0.05)
    log(f"✅ Odoo: {cambios} precios actualizados, {sinmatch} sin match.")
    return 0

if __name__ == '__main__':
    dry = '--diff' not in sys.argv and '--full' not in sys.argv
    sys.exit(main(dry_run=dry))
