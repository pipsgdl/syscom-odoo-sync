#!/usr/bin/env python3
"""
CT INTERNACIONAL → Odoo Production Sync
=========================================
Carga catalogo CT (ctonline.mx) a Odoo produccion.

Flujo:
1. Descarga ct-catalog.json desde Oracle VPS via SCP
   (Oracle es la unica IP whitelisteada por CT — ya tiene cron 15min con FTP)
2. Lee el JSON indexado por clave/numParte/modelo
3. Deduplica por idProducto
4. Match en Odoo: default_code = numParte (VPN) → fallback clave CT
5. Upsert product.template + 3 pricelists + supplierinfo partner_id=93

CT da precios en MXN nativo (sin conversion USD requerida como Ingram).
Stock total = suma de existencias por sucursal (CUN, TRN, XLP, DFA, CEL...).

Margenes CT (Mayorista cómputo/POS — patrón similar CVA):
  Computo:           online 7%, menudeo 17%, proyecto 23%
  Almacenamiento:    online 12%, menudeo 22%, proyecto 28%
  Redes/Networking:  online 15%, menudeo 28%, proyecto 33%
  UPS/Energia:       online 18%, menudeo 28%, proyecto 33%
  POS:               online 12%, menudeo 22%, proyecto 30%
  Audio/Gaming:      online 15%, menudeo 28%, proyecto 35%
  Software:          online 13%, menudeo 20%, proyecto 25%
  Default:           online 10%, menudeo 20%, proyecto 28%

Uso:
  python3 ct_to_odoo_sync.py                 # Full sync (~5,913 productos)
  python3 ct_to_odoo_sync.py --dry-run       # Preview
  python3 ct_to_odoo_sync.py --limit 50
  python3 ct_to_odoo_sync.py --status
  python3 ct_to_odoo_sync.py --refresh-cache # solo descarga cache de Oracle
  python3 ct_to_odoo_sync.py --use-local     # usa cache local (no descargar)
"""

import json
import xmlrpc.client
import time
import sys
import os
import subprocess
from datetime import datetime
from pathlib import Path

# === CONFIG ===
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPTS_DIR)
LOGS_DIR = os.path.join(REPO_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

CONFIG_PATH = "/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json"
LOG_FILE = os.path.join(LOGS_DIR, f"ct_sync_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
PROGRESS_FILE = os.path.join(SCRIPTS_DIR, "ct_sync_progress.json")

# Cache local (descargado de Oracle)
CACHE_DIR = os.path.expanduser("~/ocean-cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(CACHE_DIR, "ct-catalog.json")
META_FILE = os.path.join(CACHE_DIR, "ct-meta.json")

# Oracle VPS source (ya whitelisteado por CT)
ORACLE_SSH_HOST = "oracle-vps"   # alias en ~/.ssh/config
ORACLE_CACHE_PATH = "/opt/ct-cache/ct-catalog.json"
ORACLE_META_PATH = "/opt/ct-cache/ct-meta.json"

# Odoo IDs (verificados en producción)
CT_SUPPLIER_ID = 93         # res.partner: CT Internacional
PRICELIST_ONLINE = 3
PRICELIST_MENUDEO = 4
PRICELIST_PROYECTO = 5
CURRENCY_MXN = 33

# Margenes (online, menudeo, proyecto)
MARGINS = {
    "computo":         (0.07, 0.17, 0.23),
    "almacenamiento":  (0.12, 0.22, 0.28),
    "networking":      (0.15, 0.28, 0.33),
    "ups":             (0.18, 0.28, 0.33),
    "pos":             (0.12, 0.22, 0.30),
    "audio_gaming":    (0.15, 0.28, 0.35),
    "software":        (0.13, 0.20, 0.25),
    "perifericos":     (0.18, 0.30, 0.38),
    "impresion":       (0.10, 0.18, 0.25),
    "videovig":        (0.05, 0.18, 0.30),
    "default":         (0.10, 0.20, 0.28),
}

# Categoria CT → margin_key (basado en exploración del catálogo real)
CT_CATEGORY_MARGINS = {
    "Computadoras":            "computo",
    "Componentes de Computo":  "computo",
    "Componentes":             "computo",
    "Almacenamiento":          "almacenamiento",
    "Discos Duros":            "almacenamiento",
    "Memoria":                 "almacenamiento",
    "Redes":                   "networking",
    "Networking":              "networking",
    "WiFi":                    "networking",
    "UPS":                     "ups",
    "Energia":                 "ups",
    "No Break":                "ups",
    "Punto de Venta":          "pos",
    "POS":                     "pos",
    "Impresoras":              "impresion",
    "Consumibles":             "impresion",
    "Software":                "software",
    "Audio":                   "audio_gaming",
    "Accesorios Gaming":       "audio_gaming",
    "Gaming":                  "audio_gaming",
    "Bocinas":                 "audio_gaming",
    "Diademas":                "audio_gaming",
    "Perifericos":             "perifericos",
    "Mouse":                   "perifericos",
    "Teclados":                "perifericos",
    "Camaras":                 "videovig",
    "Videovigilancia":         "videovig",
    "DVR/NVR":                 "videovig",
}


def log(msg, also_print=True):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# =============================================================================
# Cache desde Oracle
# =============================================================================

def refresh_cache_from_oracle():
    """SCP del cache desde Oracle a local."""
    log("Descargando cache CT desde Oracle...")
    try:
        # ct-catalog.json
        r = subprocess.run(
            ["scp", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
             f"{ORACLE_SSH_HOST}:{ORACLE_CACHE_PATH}", CACHE_FILE],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            log(f"  ERROR scp catalog: {r.stderr}")
            return False
        # ct-meta.json
        subprocess.run(
            ["scp", "-o", "ConnectTimeout=15",
             f"{ORACLE_SSH_HOST}:{ORACLE_META_PATH}", META_FILE],
            capture_output=True, timeout=30)
        # Reportar
        if os.path.exists(META_FILE):
            meta = json.load(open(META_FILE))
            log(f"  ✓ Cache actualizado: {meta.get('products_total')} productos, "
                f"{meta.get('keys_indexed')} keys, generado {meta.get('updated_at')}")
        return True
    except Exception as e:
        log(f"  ERROR refresh cache: {e}")
        return False


def load_cache():
    if not os.path.exists(CACHE_FILE):
        log("ERROR: cache no existe. Corre con --refresh-cache primero.")
        return None
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception as e:
        log(f"ERROR cargando cache: {e}")
        return None


def get_unique_products(catalog):
    """El catalog está indexado por clave/numParte/modelo (3 entries por producto).
    Deduplicar por idProducto."""
    seen = {}
    for k, p in catalog.items():
        pid = p.get('idProducto')
        if pid and pid not in seen:
            seen[pid] = p
    return list(seen.values())


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
        self._sku_cache = {}

    def connect(self):
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise ValueError("Auth Odoo fallo")
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

    def preload_skus(self):
        log("Precargando SKUs de Odoo...")
        offset, batch = 0, 2000
        while True:
            time.sleep(0.3)
            prods = self.ex("product.template", "search_read",
                [[("default_code", "!=", False)]],
                {"fields": ["id", "default_code"], "offset": offset, "limit": batch})
            if not prods:
                break
            for p in prods:
                self._sku_cache[p["default_code"].strip().upper()] = p["id"]
            offset += len(prods)
            if len(prods) < batch:
                break
        log(f"  {len(self._sku_cache)} SKUs cargados")

    def find_product(self, vpn, clave):
        if vpn and vpn.strip().upper() in self._sku_cache:
            return self._sku_cache[vpn.strip().upper()]
        if clave and clave.strip().upper() in self._sku_cache:
            return self._sku_cache[clave.strip().upper()]
        return None

    def upsert_product(self, vals, existing_id=None):
        if existing_id:
            self.ex("product.template", "write", [[existing_id], vals])
            return existing_id
        new_id = self.ex("product.template", "create", [vals])
        return new_id[0] if isinstance(new_id, list) else new_id

    def set_pricelist(self, pricelist_id, product_id, price):
        existing = self.ex("product.pricelist.item", "search_read",
            [[("pricelist_id", "=", pricelist_id),
              ("product_tmpl_id", "=", product_id),
              ("applied_on", "=", "1_product")]],
            {"fields": ["id"], "limit": 1})
        vals = {"pricelist_id": pricelist_id, "product_tmpl_id": product_id,
                "applied_on": "1_product", "compute_price": "fixed", "fixed_price": price}
        if existing:
            self.ex("product.pricelist.item", "write",
                [[existing[0]["id"]], {"fixed_price": price}])
        else:
            self.ex("product.pricelist.item", "create", [vals])

    def upsert_supplier(self, product_id, sku_ct, price):
        existing = self.ex("product.supplierinfo", "search_read",
            [[("product_tmpl_id", "=", product_id),
              ("partner_id", "=", CT_SUPPLIER_ID)]],
            {"fields": ["id"], "limit": 1})
        vals = {"partner_id": CT_SUPPLIER_ID, "product_tmpl_id": product_id,
                "price": price, "product_code": sku_ct,
                "currency_id": CURRENCY_MXN, "min_qty": 1}
        if existing:
            self.ex("product.supplierinfo", "write",
                [[existing[0]["id"]], {"price": price, "product_code": sku_ct}])
        else:
            self.ex("product.supplierinfo", "create", [vals])


# =============================================================================
# Sync logic
# =============================================================================

def calculate_prices(cost_mxn, margin_key):
    if cost_mxn <= 0:
        return (0.0, 0.0, 0.0)
    m = MARGINS.get(margin_key, MARGINS["default"])
    return tuple(round(cost_mxn * (1 + mg) * 1.16, 2) for mg in m)


def margin_key_from_category(categoria, subcategoria):
    if categoria in CT_CATEGORY_MARGINS:
        return CT_CATEGORY_MARGINS[categoria]
    text = (categoria + " " + subcategoria).lower()
    if any(x in text for x in ["ups", "energ", "no break"]):
        return "ups"
    if any(x in text for x in ["red", "switch", "router", "wifi", "access point"]):
        return "networking"
    if any(x in text for x in ["pos", "punto de venta", "tpv"]):
        return "pos"
    if any(x in text for x in ["impres", "printer", "toner", "tinta"]):
        return "impresion"
    if any(x in text for x in ["disco", "ssd", "memoria", "almacen", "usb", "sd card"]):
        return "almacenamiento"
    if any(x in text for x in ["software", "licencia", "antivirus"]):
        return "software"
    if any(x in text for x in ["audio", "gaming", "diadema", "bocina"]):
        return "audio_gaming"
    if any(x in text for x in ["camara", "dvr", "nvr", "video"]):
        return "videovig"
    if any(x in text for x in ["laptop", "compu", "desktop", "all in one"]):
        return "computo"
    if any(x in text for x in ["mouse", "teclado", "perifer", "headset"]):
        return "perifericos"
    return "default"


def process_product(p, odoo, dry_run=False, stats=None):
    """Procesar 1 producto CT → Odoo."""
    clave = (p.get('clave') or '').strip()
    vpn = (p.get('numParte') or '').strip()
    modelo = (p.get('modelo') or '').strip()
    if not clave and not vpn:
        return "skip_no_sku"

    # CT da precio en MXN nativo (cuando moneda=MXN). Si moneda=USD aplica tipoCambio.
    precio_raw = p.get('precio') or 0
    moneda = p.get('moneda', 'MXN')
    tc = p.get('tipoCambio') or 1
    cost_mxn = float(precio_raw) * float(tc) if moneda == 'USD' else float(precio_raw)
    if cost_mxn <= 0:
        return "skip_no_price"

    # Stock = suma de todas las sucursales
    existencias = p.get('existencia') or {}
    if isinstance(existencias, dict):
        stock_total = sum((v or 0) for v in existencias.values() if isinstance(v, (int, float)))
    else:
        stock_total = 0

    # Solo procesar si activo
    if not p.get('activo'):
        return "skip_inactive"

    nombre = (p.get('nombre') or '').strip()
    descripcion = (p.get('descripcion') or '').strip()
    marca = (p.get('marca') or '').strip()
    categoria = (p.get('categoria') or '').strip()
    subcategoria = (p.get('subcategoria') or '').strip()

    margin_key = margin_key_from_category(categoria, subcategoria)
    p_online, p_menudeo, p_proyecto = calculate_prices(cost_mxn, margin_key)

    # Match en Odoo (prioridad: VPN → clave CT → modelo)
    product_id = odoo.find_product(vpn, clave) or odoo.find_product(modelo, None)
    is_new = product_id is None

    if dry_run:
        log(f"  [DRY] {'NEW' if is_new else 'UPD'} clave={clave:<12} vpn={vpn:<20} {marca[:15]:<15} "
            f"${cost_mxn:.2f}MXN online={p_online:.2f} stock={stock_total:>4}")
        if stats is not None:
            stats["new" if is_new else "updated"] += 1
        return "dry"

    # default_code: preferir VPN si existe, fallback clave CT
    default_code = vpn or clave
    name = nombre or f"{marca} {modelo or clave}".strip()

    vals = {
        "name": name[:255],
        "default_code": default_code,
        "list_price": p_online,
        "standard_price": cost_mxn,
        "is_storable": True,
        "type": "consu",
        "available_threshold": 0,
        "allow_out_of_stock_order": True,
        "is_published": stock_total > 0,
        "website_published": stock_total > 0,
    }
    if descripcion:
        vals["description_sale"] = descripcion[:2000]
        vals["website_description"] = f"<div>{descripcion}</div>"

    try:
        pid = odoo.upsert_product(vals, existing_id=product_id)
    except Exception as e:
        log(f"  ERROR upsert {clave}: {e}")
        return "error"
    if not pid:
        return "error"

    # Pricelists + supplierinfo
    odoo.set_pricelist(PRICELIST_ONLINE, pid, p_online)
    odoo.set_pricelist(PRICELIST_MENUDEO, pid, p_menudeo)
    odoo.set_pricelist(PRICELIST_PROYECTO, pid, p_proyecto)
    odoo.upsert_supplier(pid, clave, cost_mxn)

    # Cache para evitar re-lookups
    if vpn:
        odoo._sku_cache[vpn.upper()] = pid
    if clave:
        odoo._sku_cache[clave.upper()] = pid

    if stats is not None:
        stats["new" if is_new else "updated"] += 1
    return "ok_new" if is_new else "ok_upd"


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
    refresh_only = "--refresh-cache" in sys.argv
    use_local = "--use-local" in sys.argv
    limit = None

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    if status_only:
        prog = load_progress()
        if prog:
            log(f"Última corrida: {prog.get('completed_at') or prog.get('started_at')}")
            log(f"  Procesados:    {prog.get('processed', 0)}")
            log(f"  Nuevos:        {prog.get('new', 0)}")
            log(f"  Actualizados:  {prog.get('updated', 0)}")
            log(f"  Sin precio:    {prog.get('skipped_no_price', 0)}")
            log(f"  Inactivos:     {prog.get('skipped_inactive', 0)}")
            log(f"  Errores:       {prog.get('errors', 0)}")
        else:
            log("No hay progreso guardado.")
        return

    log("=" * 70)
    log(f"CT → Odoo Sync (dry_run={dry_run}, limit={limit}, use_local={use_local})")
    log("=" * 70)

    # 1) Cache
    if not use_local:
        if not refresh_cache_from_oracle():
            log("ABORT: no se pudo descargar cache desde Oracle")
            return
    if refresh_only:
        log("Refresh cache OK. Saliendo.")
        return

    catalog = load_cache()
    if not catalog:
        return

    products = get_unique_products(catalog)
    log(f"Productos únicos en cache: {len(products)}")

    # 2) Odoo
    if not os.path.exists(CONFIG_PATH):
        log(f"ABORT: config Odoo no existe en {CONFIG_PATH}")
        return
    cfg = json.load(open(CONFIG_PATH))
    odoo = OdooSync(cfg)
    odoo.connect()
    odoo.preload_skus()

    # 3) Procesar
    stats = {"processed": 0, "new": 0, "updated": 0, "errors": 0,
             "skipped_no_price": 0, "skipped_no_sku": 0, "skipped_inactive": 0,
             "started_at": datetime.now().isoformat(),
             "limit": limit}

    for i, p in enumerate(products):
        try:
            r = process_product(p, odoo, dry_run=dry_run, stats=stats)
            if r == "skip_no_price":
                stats["skipped_no_price"] += 1
            elif r == "skip_no_sku":
                stats["skipped_no_sku"] += 1
            elif r == "skip_inactive":
                stats["skipped_inactive"] += 1
            elif r == "error":
                stats["errors"] += 1
            stats["processed"] += 1
        except Exception as e:
            stats["errors"] += 1
            log(f"  ERROR procesando producto: {e}")

        if (i + 1) % 100 == 0:
            log(f"  Progreso: {i+1}/{len(products)} (new={stats['new']}, upd={stats['updated']}, err={stats['errors']})")
            save_progress(stats)

        if limit and stats["processed"] >= limit:
            log(f"Limite de {limit} alcanzado, deteniendo.")
            break

    stats["completed_at"] = datetime.now().isoformat()
    save_progress(stats)

    log("=" * 70)
    log("RESUMEN")
    log(f"  Procesados:    {stats['processed']}")
    log(f"  Nuevos:        {stats['new']}")
    log(f"  Actualizados:  {stats['updated']}")
    log(f"  Sin precio:    {stats['skipped_no_price']}")
    log(f"  Sin SKU:       {stats['skipped_no_sku']}")
    log(f"  Inactivos:     {stats['skipped_inactive']}")
    log(f"  Errores:       {stats['errors']}")
    log("=" * 70)


if __name__ == "__main__":
    main()
