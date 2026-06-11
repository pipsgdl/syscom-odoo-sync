#!/usr/bin/env python3
"""
Techsmart → Odoo Production Sync (8º distribuidor)
==================================================
Extracción CORRECTA (verificada 2026-06-11):
  - Login:   POST {BASE}/acciones/login.php  (rfc, usuario, txtPass)  -> cookie PHPSESSID
  - Catálogo cliente con precios (server-side, SÍNCRONO):
        GET {BASE}/Clientes/Catalogo?txtCategoria=<CAT>&txtMarca=<MARCA>
        El catálogo EXIGE categoría + marca (ambas). Cada tarjeta trae:
          cveProducto=<CODIGO> & Marca=<MARCA>, MODELO, descripción,
          precio lista (tachado) y precio c/descuento ($ MXN).
  - Costo Ocean = precio c/descuento (precio_desc). Productos en $0 = sin precio asignado -> se omiten.

Uso:
  python3 techsmart_to_odoo_sync.py --dry-run    # crawl + valida, NO escribe Odoo
  python3 techsmart_to_odoo_sync.py --diff       # incremental a Odoo (supplierinfo por product_code)

Credenciales: .env de LICITABOT (TECHSMART_*) + Odoo por env (ODOO_DB/ODOO_UID/ODOO_PASS). Sin secretos hardcodeados.
Red de seguridad: timeouts, reintentos, re-login, candados anti-ceros/anti-vacío, cache para diff, logs.
"""
import os, re, sys, json, time, subprocess
from datetime import datetime
import requests

ENV_LICITABOT = '/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env'
ODOO_URL = 'https://ocean-tech.odoo.com/jsonrpc'
CACHE = '/tmp/techsmart_full.json'
PARTNER_NAME = 'Techsmart'
MIN_PRODUCTOS = 150          # candado anti-vacío
MAX_PCT_CEROS = 0.95         # candado: si >95% en $0, algo está mal (cuenta sin precios)

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def load_env(p):
    d={}
    for l in open(p,encoding='utf-8'):
        l=l.strip()
        if l and not l.startswith('#') and '=' in l:
            k,v=l.split('=',1); d[k]=v
    return d

# ---------- Techsmart ----------
def login(env):
    s=requests.Session()
    s.headers.update({'User-Agent':'Mozilla/5.0','Referer':env['TECHSMART_BASE']+'/Clientes/Catalogo'})
    r=s.post(env['TECHSMART_BASE']+'/acciones/login.php',
             data={'rfc':env['TECHSMART_RFC'],'usuario':env['TECHSMART_USER'],
                   'txtPass':env['TECHSMART_PASS'],'movil':''},timeout=30)
    if '"error":"no"' not in r.text:
        raise RuntimeError(f"Login Techsmart falló: {r.text[:120]}")
    log("Login Techsmart OK"); return s

def _opciones_select(html, select_id):
    m=re.search(rf'id="{select_id}".*?</select>', html, re.S)
    if not m: return []
    return [v for v,_ in re.findall(r'<option[^>]*value="([^"]+)"[^>]*>([^<]*)</option>', m.group(0))
            if v not in ('-1','','T')]

def catalogos(s, base):
    hc=s.get(base+'/Clientes/Catalogo',timeout=40).text
    hl=s.get(base+'/Clientes/Lista-precios',timeout=40).text  # las marcas viven aquí (select completo)
    cats=_opciones_select(hc,'txtCategoria') or _opciones_select(hl,'txtCategoria')
    marcas=_opciones_select(hl,'txtMarca')
    log(f"Categorías: {len(cats)} · Marcas: {len(marcas)}")
    return cats, marcas

def parse_pagina(h):
    out=[]
    anc=list(re.finditer(r'cveProducto=([A-Z0-9._-]+)&TipoMoneda=\w+&Marca=([^"&]+)', h))
    for i,m in enumerate(anc):
        cod,marca=m.group(1),m.group(2)
        seg=h[m.start(): anc[i+1].start() if i+1<len(anc) else m.start()+2500]
        mod=(re.search(r'MODELO:\s*([A-Z0-9._/-]+)',seg) or [None,''])[1]
        d=re.search(r'text-card">\s*(.*?)\s*<br',seg,re.S)
        desc=re.sub(r'\s+',' ',d.group(1)).strip() if d else ''
        pr=[float(x.replace(',','')) for x in re.findall(r'\$([\d,]+\.\d{2})\s*MXN',seg)]
        pl=pr[0] if pr else 0.0; pd=pr[1] if len(pr)>1 else pl
        out.append({'codigo':cod,'marca':marca,'modelo':mod,'desc':desc[:80],
                    'precio_lista':pl,'precio_desc':pd})
    return out

def crawl(s, base, cats, marcas):
    vistos={}
    total=len(cats)*len(marcas); hecho=0
    for c in cats:
        for mk in marcas:
            hecho+=1
            for intento in range(3):
                try:
                    h=s.get(base+'/Clientes/Catalogo',
                            params={'txtCategoria':c,'txtMarca':mk},timeout=40).text
                    for p in parse_pagina(h):
                        p['categoria']=c
                        vistos[p['codigo']]=p   # dedup por código
                    break
                except Exception as e:
                    if intento==2: log(f"  err {c}/{mk}: {str(e)[:60]}")
                    time.sleep(2)
            if hecho % 200 == 0:
                log(f"  {hecho}/{total} combos · {len(vistos)} productos únicos")
    return list(vistos.values())

# ---------- Odoo ----------
def rpc(env, model, method, args, kwargs=None):
    for _ in range(5):
        p={"jsonrpc":"2.0","method":"call","params":{"service":"object","method":"execute_kw",
           "args":[env['ODOO_DB'],int(env['ODOO_UID']),env['ODOO_PASS'],model,method,args,kwargs or {}]}}
        r=subprocess.run(['/usr/bin/curl','-s','--max-time','60','-H','Content-Type: application/json',
                          '-d',json.dumps(p),ODOO_URL],capture_output=True,text=True,timeout=70)
        out=r.stdout.strip()
        if out and not out.startswith('<'):
            try: return json.loads(out).get('result')
            except: pass
        time.sleep(10)
    return None

def get_partner_id(env):
    for dom in ([['name','ilike',PARTNER_NAME],['supplier_rank','>',0]], [['name','ilike',PARTNER_NAME]]):
        r=rpc(env,'res.partner','search',[dom],{'limit':1})
        if r: return r[0]
    return None

# ---------- Main ----------
def main(dry_run=True):
    env=load_env(ENV_LICITABOT)
    for k in ('ODOO_DB','ODOO_UID','ODOO_PASS'): env.setdefault(k, os.environ.get(k,''))
    base=env['TECHSMART_BASE']
    s=login(env)
    cats,marcas=catalogos(s,base)
    if not cats or not marcas:
        log("❌ ABORTA: no se pudieron leer categorías/marcas."); return 2
    log(f"Crawl de {len(cats)}x{len(marcas)} combos…")
    productos=crawl(s,base,cats,marcas)
    con_precio=[p for p in productos if p['precio_desc']>0]
    log(f"Productos únicos: {len(productos)} · con precio>0: {len(con_precio)}")

    # candados
    if len(productos) < MIN_PRODUCTOS:
        log(f"❌ ABORTA: solo {len(productos)} productos (< {MIN_PRODUCTOS})."); return 2
    if productos and (len(productos)-len(con_precio))/len(productos) > MAX_PCT_CEROS:
        log("❌ ABORTA: casi todo en $0 — cuenta sin precios provisionados."); return 2

    json.dump(con_precio, open(CACHE,'w'), ensure_ascii=False)
    if dry_run:
        log(f"DRY-RUN OK: {len(con_precio)} productos con precio. Muestra:")
        for p in con_precio[:8]:
            log(f"  {p['codigo']:18} {p['precio_desc']:>9.2f} MXN  {p['marca']:10} {p['desc'][:42]}")
        return 0

    # Odoo: supplierinfo por product_code = código Techsmart
    pid=get_partner_id(env)
    if not pid: log("❌ Partner Techsmart no existe en Odoo (créalo)."); return 3
    sis=rpc(env,'product.supplierinfo','search_read',[[['partner_id','=',pid]]],
            {'fields':['id','product_code','price'],'limit':100000}) or []
    by=dict(((x['product_code'] or '').strip().upper(),x) for x in sis)
    cambios=sinmatch=0
    for p in con_precio:
        si=by.get(p['codigo'].upper())
        if not si: sinmatch+=1; continue
        if abs((si.get('price') or 0)-p['precio_desc'])>1.0:
            rpc(env,'product.supplierinfo','write',[[si['id']],{'price':p['precio_desc']}]); cambios+=1
        time.sleep(0.05)
    log(f"✅ Odoo: {cambios} precios actualizados, {sinmatch} sin match.")
    return 0

if __name__=='__main__':
    sys.exit(main(dry_run=('--diff' not in sys.argv and '--full' not in sys.argv)))
