#!/usr/bin/env python3
"""
Exel del Norte (API WebService) → Odoo Production Sync.
Reemplaza el scraper de navegador (exel_to_odoo_sync.py). Usa la API oficial:
  GET {EXEL_API_BASE}/productos  (header Authorization: <EXEL_API_KEY>)
  -> referencia, sku, codigo_sat, nombre, stock, precio, precio_oferta, ...
Match Odoo: product.supplierinfo.product_code == referencia (partner Exel id 94).
Costo = precio_oferta. Idempotente, diff de precios, tolerante a fallo.

Uso:
  python3 exel_api_to_odoo_sync.py --dry-run   # solo reporta, no escribe Odoo
  python3 exel_api_to_odoo_sync.py             # aplica cambios de precio
"""
import os, sys, json, time, subprocess, urllib.request, urllib.error
from datetime import datetime

ENV = os.path.expanduser("~/syscom-odoo-sync/.env")
ODOO = "https://ocean-tech.odoo.com/jsonrpc"
EXEL_PARTNER_ID = 94
CACHE = "/tmp/exel_api_full.json"

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def load_env(p):
    d = {}
    for l in open(p, encoding="utf-8"):
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            k, v = l.split("=", 1); d[k] = v
    return d
ENVV = load_env(ENV)

def rpc(model, method, args, kwargs=None):
    for _ in range(5):
        p = {"jsonrpc": "2.0", "method": "call", "params": {"service": "object", "method": "execute_kw",
             "args": [ENVV.get("ODOO_DB","ocean-tech"), int(ENVV.get("ODOO_UID","2")), ENVV.get("ODOO_PASS",""), model, method, args, kwargs or {}]}}
        r = subprocess.run(["/usr/bin/curl", "-s", "--max-time", "90", "-H", "Content-Type: application/json",
            "-d", json.dumps(p), ODOO], capture_output=True, text=True, timeout=100)
        out = r.stdout.strip()
        if out and not out.startswith("<"):
            try: return json.loads(out).get("result")
            except: pass
        time.sleep(10)
    return None

def descargar_api():
    base, key = ENVV["EXEL_API_BASE"], ENVV["EXEL_API_KEY"]
    req = urllib.request.Request(base + "/productos", headers={"Authorization": key})
    raw = urllib.request.urlopen(req, timeout=180).read().decode("utf-8", "ignore")
    datos = json.loads(raw).get("datos", [])
    log(f"API Exel: {len(datos)} productos")
    return datos

def fnum(x):
    try: return round(float(x), 4)
    except: return 0.0

def main(dry=True):
    items = descargar_api()
    if not items or len(items) < 500:
        log(f"❌ ABORTA: API devolvió {len(items)} productos (< 500). ¿Catálogo no provisionado?"); return 2
    json.dump(items, open(CACHE, "w"), ensure_ascii=False)
    # mapa referencia -> costo (precio_oferta)
    api = {}
    for it in items:
        ref = (it.get("referencia") or "").strip().upper()
        if ref: api[ref] = fnum(it.get("precio_oferta") or it.get("precio") or 0)
    log(f"referencias con precio: {len(api)}")
    # supplierinfo Exel existentes
    sis = rpc("product.supplierinfo", "search_read", [[["partner_id", "=", EXEL_PARTNER_ID]]],
              {"fields": ["id", "product_code", "price"], "limit": 100000}) or []
    log(f"supplierinfo Exel en Odoo: {len(sis)}")
    upd = sinmatch = igual = ceros = 0
    cambios = []
    for s in sis:
        code = (s.get("product_code") or "").strip().upper()
        if code not in api:
            sinmatch += 1; continue
        nuevo = api[code]
        if nuevo <= 0:
            ceros += 1; continue
        if abs((s.get("price") or 0) - nuevo) > 0.5:
            cambios.append((s["id"], nuevo)); upd += 1
        else:
            igual += 1
    log(f"A actualizar: {upd} · iguales: {igual} · sin match en API: {sinmatch} · en \$0: {ceros}")
    log(f"API tiene {len(api)} refs; {len(api) - (upd+igual+ceros)} sin supplierinfo (altas futuras)")
    if dry:
        log("DRY-RUN — muestra de cambios de precio:")
        for sid, pr in cambios[:8]:
            old = next((x["price"] for x in sis if x["id"] == sid), "?")
            log(f"  si={sid}  {old} -> {pr}")
        return 0
    log(f"Aplicando {len(cambios)} cambios de precio…")
    for sid, pr in cambios:
        rpc("product.supplierinfo", "write", [[sid], {"price": pr}]); time.sleep(0.04)
    log(f"✅ Exel API sync: {upd} precios actualizados.")
    return 0

if __name__ == "__main__":
    sys.exit(main(dry=("--dry-run" in sys.argv)))
