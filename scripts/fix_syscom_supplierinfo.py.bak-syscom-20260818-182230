#!/usr/bin/env python3
"""
FIX SYSCOM SUPPLIERINFO — repara déficit de proveedor Syscom en Odoo
=====================================================================
Diagnóstico: Odoo tiene ~54,000 productos pero solo 4,579 tienen supplierinfo
SYSCOM (partner_id=117). El resto fueron creados por el sync inicial v6 sin
supplierinfo asignado.

Este script:
1. Auth Syscom OAuth2 (cap 60 req/min)
2. Lee productos Odoo que NO tienen supplierinfo Syscom
3. Por cada uno, consulta Syscom API por su default_code
4. Si Syscom responde con producto válido → crea supplierinfo
5. Reanudable vía progress.json

Uso:
  python3 fix_syscom_supplierinfo.py                # full
  python3 fix_syscom_supplierinfo.py --dry-run      # preview sin escribir
  python3 fix_syscom_supplierinfo.py --limit 100    # test
  python3 fix_syscom_supplierinfo.py --status       # ver progreso
  python3 fix_syscom_supplierinfo.py --resume       # continúa desde checkpoint

Variables .env requeridas:
  SYSCOM_CLIENT_ID, SYSCOM_CLIENT_SECRET (ya están)
"""

import json
import xmlrpc.client
import urllib.parse
import ssl
import time
import sys
import os
from datetime import datetime
from pathlib import Path
from collections import deque

# curl_cffi requerido para esquivar Akamai TLS-fingerprint en Syscom
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    print("⚠️  curl_cffi no instalado. Akamai bloqueará Syscom auth.")
    print("    Instala: source .venv/bin/activate && pip install curl_cffi")
    sys.exit(1)

# === CONFIG ===
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPTS_DIR)
LOGS_DIR = os.path.join(REPO_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

CONFIG_PATH = "/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json"
LOG_FILE = os.path.join(LOGS_DIR, f"fix_syscom_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
PROGRESS_FILE = os.path.join(SCRIPTS_DIR, "fix_syscom_progress.json")

ENV_PATHS = [
    os.path.join(REPO_DIR, ".env"),
    "/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env",
]
for ep in ENV_PATHS:
    if os.path.exists(ep):
        with open(ep) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

SYSCOM_CLIENT_ID = os.environ.get("SYSCOM_CLIENT_ID", "")
SYSCOM_CLIENT_SECRET = os.environ.get("SYSCOM_CLIENT_SECRET", "")
SYSCOM_TOKEN_URL = "https://developers.syscom.mx/oauth/token"
SYSCOM_PRODUCTOS_SEARCH = "https://developers.syscom.mx/api/v1/productos"

# Odoo IDs
SYSCOM_SUPPLIER_ID = 117
CURRENCY_MXN = 33

# Rate limit Syscom
RATE_LIMIT_PER_MIN = 55  # margen de seguridad bajo cap 60

# TC USD→MXN (para productos Syscom sin moneda definida — la mayoría son USD)
TC_USD_MXN_FALLBACK = 17.50

ctx = ssl.create_default_context()


def get_tc_usd_mxn():
    """Obtener TC del día (cache simple)."""
    cache_file = os.path.join(SCRIPTS_DIR, ".tc_cache.json")
    if os.path.exists(cache_file):
        try:
            c = json.load(open(cache_file))
            if c.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return c["rate"]
        except Exception:
            pass
    try:
        r = cffi_requests.get("https://open.er-api.com/v6/latest/USD",
            impersonate="chrome120", timeout=15)
        if r.status_code == 200:
            rate = float(r.json()["rates"]["MXN"])
            json.dump({"date": datetime.now().strftime("%Y-%m-%d"),
                       "rate": rate}, open(cache_file, "w"))
            return rate
    except Exception:
        pass
    return TC_USD_MXN_FALLBACK


def log(msg, also_print=True):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# =============================================================================
# Syscom API client con rate limiting
# =============================================================================

class SyscomClient:
    def __init__(self):
        self.token = None
        self.token_expires = 0
        self._call_times = deque()  # timestamps últimos N calls

    def _rate_limit_wait(self):
        """Mantener < RATE_LIMIT_PER_MIN llamadas/minuto."""
        now = time.time()
        # Limpiar calls > 60s
        while self._call_times and now - self._call_times[0] > 60:
            self._call_times.popleft()
        if len(self._call_times) >= RATE_LIMIT_PER_MIN:
            sleep_for = 60 - (now - self._call_times[0]) + 0.5
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._call_times.append(time.time())

    def get_token(self):
        if self.token and self.token_expires > time.time() + 60:
            return self.token
        # Akamai bloquea urllib — usar curl_cffi
        r = cffi_requests.post(SYSCOM_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": SYSCOM_CLIENT_ID,
                "client_secret": SYSCOM_CLIENT_SECRET,
            },
            impersonate="chrome120", timeout=30)
        if r.status_code != 200:
            raise Exception(f"Auth Syscom {r.status_code}: {r.text[:200]}")
        d = r.json()
        self.token = d["access_token"]
        self.token_expires = time.time() + int(d.get("expires_in", 3600))
        return self.token

    def search_product(self, sku):
        """GET /productos?busqueda={sku} → busca por SKU/modelo, retorna primer match exacto.
        Si encuentra match exacto en 'modelo', devuelve el dict del producto."""
        self._rate_limit_wait()
        url = f"{SYSCOM_PRODUCTOS_SEARCH}?busqueda={urllib.parse.quote(sku, safe='')}"
        try:
            r = cffi_requests.get(url, headers={
                "Authorization": f"Bearer {self.get_token()}",
                "Accept": "application/json",
            }, impersonate="chrome120", timeout=20)
        except Exception:
            return None
        if r.status_code == 429:
            log(f"  Syscom 429 — esperando 30s")
            time.sleep(30)
            return self.search_product(sku)
        if r.status_code == 401:
            self.token = None
            return self.search_product(sku)
        if r.status_code != 200:
            return None
        try:
            d = r.json()
        except Exception:
            return None
        prods = d.get("productos") or d.get("data") or (d if isinstance(d, list) else [])
        if not isinstance(prods, list) or not prods:
            return None
        # Buscar match exacto por modelo (case insensitive)
        sku_up = sku.strip().upper()
        for p in prods:
            if str(p.get("modelo", "")).strip().upper() == sku_up:
                return p
        # Si no hay match exacto, NO devolvemos nada (evitamos falsos positivos)
        return None


# =============================================================================
# Odoo client
# =============================================================================

class OdooSync:
    def __init__(self, config):
        self.url = config["url"]
        self.db = config["db"]
        self.username = config["username"]
        self.password = config["password"]
        self.uid = None
        self.models = None

    def connect(self):
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        log(f"Conectado a Odoo UID={self.uid}")

    def ex(self, model, method, args, kwargs={}):
        for attempt in range(3):
            try:
                return self.models.execute_kw(
                    self.db, self.uid, self.password, model, method, args, kwargs)
            except Exception as e:
                if "429" in str(e) or "Too Many" in str(e):
                    wait = 30 * (attempt + 1)
                    log(f"  Odoo 429 — esperando {wait}s")
                    time.sleep(wait)
                    self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
                else:
                    raise
        return None

    def fetch_candidates_without_syscom(self):
        """Productos publicados con default_code que NO tienen supplierinfo Syscom."""
        log("Buscando productos sin supplierinfo Syscom...")
        # Estrategia: leer todos los publicados con default_code, en chunks
        candidatos = []
        offset, batch = 0, 2000
        while True:
            time.sleep(0.3)
            prods = self.ex("product.template", "search_read",
                [[("default_code", "!=", False)]],
                {"fields": ["id", "default_code", "name", "seller_ids"],
                 "offset": offset, "limit": batch})
            if not prods:
                break
            for p in prods:
                # Si NO tiene seller_ids o ninguno es Syscom (ID 117), candidato
                seller_ids = p.get("seller_ids") or []
                # seller_ids son IDs de product.supplierinfo, no de partner — necesitamos resolver
                candidatos.append({
                    "id": p["id"],
                    "default_code": p["default_code"].strip(),
                    "name": (p["name"] or "")[:60],
                    "seller_info_ids": seller_ids,
                })
            offset += len(prods)
            if len(prods) < batch:
                break
        log(f"  {len(candidatos)} productos con default_code")

        # Para cada uno, verificar si tiene supplierinfo Syscom
        # Mejor: query masivo de supplierinfo por partner_id=117
        log("Cargando supplierinfo Syscom existentes...")
        syscom_supplier_tmpl_ids = set()
        offset = 0
        while True:
            time.sleep(0.3)
            sups = self.ex("product.supplierinfo", "search_read",
                [[("partner_id", "=", SYSCOM_SUPPLIER_ID)]],
                {"fields": ["product_tmpl_id"], "offset": offset, "limit": 2000})
            if not sups:
                break
            for s in sups:
                if s.get("product_tmpl_id"):
                    syscom_supplier_tmpl_ids.add(s["product_tmpl_id"][0])
            offset += len(sups)
            if len(sups) < 2000:
                break
        log(f"  {len(syscom_supplier_tmpl_ids)} productos YA tienen Syscom")

        # Filtrar candidatos que NO tienen Syscom
        sin_syscom = [c for c in candidatos if c["id"] not in syscom_supplier_tmpl_ids]
        log(f"  → {len(sin_syscom)} productos SIN supplierinfo Syscom (a procesar)")
        return sin_syscom

    def upsert_supplier(self, product_id, sku_syscom, price):
        existing = self.ex("product.supplierinfo", "search_read",
            [[("product_tmpl_id", "=", product_id),
              ("partner_id", "=", SYSCOM_SUPPLIER_ID)]],
            {"fields": ["id"], "limit": 1})
        vals = {"partner_id": SYSCOM_SUPPLIER_ID, "product_tmpl_id": product_id,
                "price": price, "product_code": sku_syscom,
                "currency_id": CURRENCY_MXN, "min_qty": 1}
        if existing:
            self.ex("product.supplierinfo", "write",
                [[existing[0]["id"]], {"price": price, "product_code": sku_syscom}])
            return "updated"
        else:
            self.ex("product.supplierinfo", "create", [vals])
            return "created"


# =============================================================================
# Main
# =============================================================================

def save_progress(prog):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(prog, f, indent=2, default=str)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return json.load(open(PROGRESS_FILE))
        except Exception:
            pass
    return {}


def main():
    dry_run = "--dry-run" in sys.argv
    status_only = "--status" in sys.argv
    resume = "--resume" in sys.argv
    limit = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    if status_only:
        prog = load_progress()
        if prog:
            log(f"Inicio:    {prog.get('started_at')}")
            log(f"Procesados: {prog.get('processed', 0)} / {prog.get('total_candidates', '?')}")
            log(f"  Match Syscom + creó supplier: {prog.get('matched', 0)}")
            log(f"  No existe en Syscom (404):    {prog.get('not_found', 0)}")
            log(f"  Errores:                      {prog.get('errors', 0)}")
        else:
            log("No hay progreso.")
        return

    log("=" * 70)
    log(f"FIX SYSCOM SUPPLIERINFO (dry={dry_run}, limit={limit}, resume={resume})")
    log("=" * 70)

    if not SYSCOM_CLIENT_ID or not SYSCOM_CLIENT_SECRET:
        log("ABORT: faltan SYSCOM_CLIENT_ID/SECRET en .env")
        return

    # Conectar Odoo
    cfg = json.load(open(CONFIG_PATH))
    odoo = OdooSync(cfg)
    odoo.connect()

    # Auth Syscom
    syscom = SyscomClient()
    try:
        syscom.get_token()
        log("Token Syscom OK")
    except Exception as e:
        log(f"ABORT: error auth Syscom: {e}")
        return

    # Obtener candidatos
    candidatos = odoo.fetch_candidates_without_syscom()

    # Reanudar si resume
    prog = load_progress() if resume else {}
    processed_ids = set(prog.get("processed_ids", []))
    if resume:
        candidatos = [c for c in candidatos if c["id"] not in processed_ids]
        log(f"Reanudando: {len(candidatos)} pendientes (ya procesados: {len(processed_ids)})")

    stats = prog if resume else {
        "started_at": datetime.now().isoformat(),
        "total_candidates": len(candidatos) + len(processed_ids),
        "processed": len(processed_ids),
        "matched": prog.get("matched", 0),
        "not_found": prog.get("not_found", 0),
        "errors": prog.get("errors", 0),
        "processed_ids": list(processed_ids),
    }

    # TC del día (la mayoría de precios Syscom son en USD)
    tc = get_tc_usd_mxn()
    log(f"TC USD/MXN: {tc:.4f}")

    log(f"Procesando {len(candidatos)} productos (rate {RATE_LIMIT_PER_MIN}/min)")

    for i, c in enumerate(candidatos):
        sku = c["default_code"]
        try:
            data = syscom.search_product(sku)
            if not data:
                stats["not_found"] += 1
            elif isinstance(data, dict):
                # En la búsqueda, el precio viene en precio_descuento (USD si moneda=null)
                # Probar varias rutas
                precio = 0
                moneda = None
                if "precios" in data and isinstance(data["precios"], dict):
                    precio = float(data["precios"].get("precio_descuento") or
                                   data["precios"].get("precio_lista") or 0)
                elif "precio_descuento" in data:
                    precio = float(data.get("precio_descuento") or 0)
                elif "precio" in data:
                    precio = float(data.get("precio") or 0)
                moneda = data.get("moneda") or "USD"   # Syscom default = USD

                # Convertir a MXN si necesario
                precio_mxn = precio if moneda == "MXN" else precio * tc

                if precio_mxn > 0:
                    if dry_run:
                        log(f"  [DRY] MATCH {sku:<25} ${precio:.2f} {moneda} → ${precio_mxn:.2f} MXN")
                    else:
                        odoo.upsert_supplier(c["id"], sku, precio_mxn)
                    stats["matched"] += 1
                else:
                    stats["not_found"] += 1
        except Exception as e:
            stats["errors"] += 1
            log(f"  ERROR {sku}: {e}")

        stats["processed"] += 1
        stats["processed_ids"].append(c["id"])

        # Save progress cada 50
        if (i + 1) % 50 == 0:
            log(f"  {i+1}/{len(candidatos)} | match={stats['matched']} not_found={stats['not_found']} err={stats['errors']}")
            save_progress(stats)

        if limit and stats["processed"] >= limit:
            log(f"Limite {limit} alcanzado.")
            break

    stats["completed_at"] = datetime.now().isoformat()
    save_progress(stats)
    log("=" * 70)
    log("RESUMEN")
    log(f"  Procesados:   {stats['processed']}")
    log(f"  Match Syscom: {stats['matched']}  ← supplierinfo creadas")
    log(f"  Not found:    {stats['not_found']}")
    log(f"  Errores:      {stats['errors']}")
    log("=" * 70)


if __name__ == "__main__":
    main()
