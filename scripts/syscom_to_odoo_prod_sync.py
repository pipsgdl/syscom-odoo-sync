#!/usr/bin/env python3
"""
Syscom → Odoo Production Full Sync
===================================
Syncs products from Syscom API to Odoo production (ocean-tech.odoo.com)
- Fetches all 12 Syscom categories
- Creates/updates products with prices, images, SAT codes
- Assigns eCommerce public categories
- Rate-limited to avoid 429 errors

Usage:
  python3 syscom_to_odoo_prod_sync.py                    # Full sync
  python3 syscom_to_odoo_prod_sync.py --dry-run           # Preview only
  python3 syscom_to_odoo_prod_sync.py --category 22       # Single category
  python3 syscom_to_odoo_prod_sync.py --limit 10          # Limit per category
  python3 syscom_to_odoo_prod_sync.py --skip-images       # Faster (no images)
  python3 syscom_to_odoo_prod_sync.py --resume             # Resume from last position
"""

import json
import xmlrpc.client
import time
import sys
import os
import base64
import urllib.request
import urllib.parse
import http.client
import ssl
from datetime import datetime

# === CONFIGURATION ===
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = "/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json"
PROGRESS_FILE = os.path.join(SCRIPTS_DIR, "sync_progress.json")
LOG_FILE = os.path.join(SCRIPTS_DIR, f"sync_log_{datetime.now().strftime('%Y%m%d_%H%M')}.txt")

# Syscom API
SYSCOM_TOKEN_URL = "https://developers.syscom.mx/oauth/token"
SYSCOM_API_URL = "https://developers.syscom.mx/api/v1/productos"
SYSCOM_CLIENT_ID = "zq2u2Zr1VFGam5IzAg5UolwmeSnsChP7"
SYSCOM_CLIENT_SECRET = "thdUXtjZunSRT4IB1ascUHIZlWsA5PhtIL9mBWVz"

# Odoo Production accounts
INCOME_ACCOUNT = 145  # 401.01.02 Ventas y/o servicios gravados

# Syscom categories → Odoo internal category + expense account + eCommerce category
# categ_id = Odoo internal product.category ID (from Syscom sync)
# expense = account.account ID for expense
# ecom_ids = product.public.category IDs (eCommerce categories we created)
# categ_id = Odoo internal product.category ID (VERIFIED in production)
# expense = account.account ID for expense
# ecom_ids = product.public.category IDs (eCommerce categories we created)
CATEGORIES = [
    {"syscom_id": 22,    "name": "Videovigilancia",            "categ_id": 4,   "expense": 128, "ecom_ids": [1]},   # 1 CCTV
    {"syscom_id": 25,    "name": "Radiocomunicación",          "categ_id": 11,  "expense": 132, "ecom_ids": [9]},   # 5 REDES Y AUDIO-VIDEO
    {"syscom_id": 26,    "name": "Redes e IT",                 "categ_id": 11,  "expense": 132, "ecom_ids": [9]},   # 5 REDES Y AUDIO-VIDEO
    {"syscom_id": 27,    "name": "IoT / GPS / Telemática",     "categ_id": 11,  "expense": 132, "ecom_ids": [9]},   # 5 REDES Y AUDIO-VIDEO
    {"syscom_id": 30,    "name": "Energía / Herramientas",     "categ_id": 94,  "expense": 143, "ecom_ids": [21]},  # 4 ELECTRICO
    {"syscom_id": 32,    "name": "Automatización e Intrusión", "categ_id": 516, "expense": 137, "ecom_ids": [5]},   # 12 AUTOMATIZACION E INTRUSION
    {"syscom_id": 37,    "name": "Control de Acceso",          "categ_id": 7,   "expense": 131, "ecom_ids": [5]},   # 3 CONTROL DE ACCESO
    {"syscom_id": 38,    "name": "Detección de Fuego",         "categ_id": 9,   "expense": 129, "ecom_ids": [13]},  # 2 DETECCIÓN CONTRA INCENDIO
    {"syscom_id": 65747, "name": "Marketing",                  "categ_id": 14,  "expense": 128, "ecom_ids": [1]},   # *SIN CATEGORIA
    {"syscom_id": 65811, "name": "Cableado Estructurado",      "categ_id": 10,  "expense": 133, "ecom_ids": [17]},  # 6 CABLEADO ESTRUCTURADO
    {"syscom_id": 66523, "name": "Audio y Video",              "categ_id": 11,  "expense": 132, "ecom_ids": [9]},   # 5 REDES Y AUDIO-VIDEO
    {"syscom_id": 66630, "name": "Industria / BMS / Robots",   "categ_id": 516, "expense": 137, "ecom_ids": [5]},   # 12 AUTOMATIZACION E INTRUSION
]

# UOM mapping (Syscom name → Odoo production ID)
UOM_MAP = {
    "Pieza": 1, "Piezas": 1, "pieza": 1, "pza": 1, "Pza": 1, "PZA": 1,
    "Unidad": 27, "Unidades": 27, "Units": 1, "unidad": 27,
    "Bobina 100 mts": 39, "Bobina 305 mts": 40,
    "Metro": 5, "Metros": 5, "metro": 5, "mts": 5, "m": 5,
    "Rollo": 39, "rollo": 39,
    "Par": 1, "Juego": 1, "Kit": 1, "Set": 1,
}
DEFAULT_UOM = 1  # Units


# === HELPERS ===
def log(msg, also_print=True):
    """Log to file and optionally print"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    if also_print:
        print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_syscom_token():
    """Get OAuth token from Syscom API"""
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection("developers.syscom.mx", context=ctx)
    body = urllib.parse.urlencode({
        "client_id": SYSCOM_CLIENT_ID,
        "client_secret": SYSCOM_CLIENT_SECRET,
        "grant_type": "client_credentials"
    })
    conn.request("POST", "/oauth/token", body=body,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data.get("access_token")


def syscom_fetch(token, category_id, page=1):
    """Fetch products from Syscom API"""
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection("developers.syscom.mx", context=ctx, timeout=60)
    path = f"/api/v1/productos?categoria={category_id}&page={page}&moneda=MXN"
    conn.request("GET", path, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    })
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data


def download_image_base64(url):
    """Download image and return base64 string"""
    if not url:
        return None
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "OceanTech-Sync/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        img_data = resp.read()
        if img_data and len(img_data) > 100:
            return base64.b64encode(img_data).decode("utf-8")
        return None
    except Exception:
        return None


def num(val, default=0):
    """Safe number conversion"""
    try:
        n = float(val)
        return n if n == n else default  # NaN check
    except (TypeError, ValueError):
        return default


# === 3 LISTAS DE PRECIOS — ONLINE | MENUDEO | PROYECTO ===
# NOTA: La API Syscom con &moneda=MXN devuelve precios YA en MXN.
#       NO multiplicar por tipo de cambio — ya están convertidos.
# Margen = % sobre costo MXN (precio_descuento) antes de IVA
# Formula: costo_MXN * (1 + margen) * 1.16 (IVA)
# ONLINE = precio más bajo (ecommerce, competir con Amazon/ML)
# MENUDEO = mostrador, instaladores independientes (+10pp vs online)
# PROYECTO = cotización formal, licitaciones, gobierno (+22pp vs online)

# NOTA: Validación de mercado (abril 2026) mostró que Syscom precio_descuento
# en MXN es MÁS ALTO que el precio retail de Cyberpuerta/Abasteo en muchos SKU.
# Márgenes ONLINE deben ser mínimos para competir. La utilidad real está en
# MENUDEO (mostrador) y PROYECTO (donde se cobra mano de obra + diseño).
# Productos donde costo Syscom > precio retail deben venderse a margen 0-5%
# online y recuperar en servicio.

MARKUP_ONLINE = {
    22:    0.05,   # Videovigilancia — 5% (Cyberpuerta vende DVR/NVR más barato que nuestro costo)
    37:    0.08,   # Control de Acceso — 8%
    38:    0.15,   # Detección de Fuego — 15% (nicho, menos competencia online)
    26:    0.05,   # Redes e IT — 5% (Amazon/Cyberpuerta muy agresivos)
    25:    0.08,   # Radiocomunicación — 8%
    27:    0.08,   # IoT / GPS / Telemática — 8%
    30:    0.08,   # Energía / Herramientas — 8%
    32:    0.10,   # Automatización e Intrusión — 10%
    65747: 0.08,   # Marketing — 8%
    65811: 0.05,   # Cableado Estructurado — 5% (commodity)
    66523: 0.08,   # Audio y Video — 8%
    66630: 0.10,   # Industria / BMS / Robots — 10%
}

MARKUP_MENUDEO = {
    22:    0.18,   # Videovigilancia
    37:    0.18,   # Control de Acceso
    38:    0.30,   # Detección de Fuego
    26:    0.12,   # Redes e IT
    25:    0.15,   # Radiocomunicación
    27:    0.15,   # IoT / GPS / Telemática
    30:    0.15,   # Energía / Herramientas
    32:    0.18,   # Automatización e Intrusión
    65747: 0.15,   # Marketing
    65811: 0.12,   # Cableado Estructurado
    66523: 0.15,   # Audio y Video
    66630: 0.18,   # Industria / BMS / Robots
}

MARKUP_PROYECTO = {
    22:    0.30,   # Videovigilancia
    37:    0.30,   # Control de Acceso
    38:    0.45,   # Detección de Fuego
    26:    0.22,   # Redes e IT
    25:    0.25,   # Radiocomunicación
    27:    0.25,   # IoT / GPS / Telemática
    30:    0.25,   # Energía / Herramientas
    32:    0.30,   # Automatización e Intrusión
    65747: 0.25,   # Marketing
    65811: 0.22,   # Cableado Estructurado
    66523: 0.25,   # Audio y Video
    66630: 0.30,   # Industria / BMS / Robots
}

DEFAULT_MARKUP_ONLINE = 0.08
DEFAULT_MARKUP_MENUDEO = 0.15
DEFAULT_MARKUP_PROYECTO = 0.25

# IDs de pricelists en Odoo (se crean una vez, luego se usan estos IDs)
PRICELIST_IDS = {
    "online": None,
    "menudeo": None,
    "proyecto": None,
}


def calculate_price(costo_mxn, categoria_syscom=None, lista="online"):
    """Calcula precio de venta MXN con margen por categoría y lista de precios.

    IMPORTANTE: costo_mxn ya viene en pesos (API usa &moneda=MXN).
    Formula: costo_MXN * (1 + margen) * 1.16 (IVA)
    """
    if costo_mxn <= 0:
        return 0

    if lista == "menudeo":
        markup_table = MARKUP_MENUDEO
        default = DEFAULT_MARKUP_MENUDEO
    elif lista == "proyecto":
        markup_table = MARKUP_PROYECTO
        default = DEFAULT_MARKUP_PROYECTO
    else:
        markup_table = MARKUP_ONLINE
        default = DEFAULT_MARKUP_ONLINE

    margen = markup_table.get(categoria_syscom, default)
    precio_sin_iva = costo_mxn * (1 + margen)
    precio_con_iva = precio_sin_iva * 1.16
    return round(precio_con_iva, 2)


# === ODOO CONNECTION ===
class OdooSync:
    def __init__(self, config):
        self.url = config["url"]
        self.db = config["db"]
        self.username = config["username"]
        self.password = config["password"]
        self.uid = None
        self.models = None
        self._unspsc_cache = {}
        self._sku_cache = {}

    def connect(self):
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise ValueError("Authentication failed")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        log(f"Connected to {self.url} as UID={self.uid}")

    def execute(self, model, method, *args, **kwargs):
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, method, args, kwargs
        )

    def lookup_unspsc(self, sat_key):
        """Lookup UNSPSC code ID from SAT key with caching"""
        if not sat_key or sat_key in ("null", "undefined", "None", ""):
            return False
        sat_key = str(sat_key).strip()
        if len(sat_key) < 4:
            return False

        if sat_key in self._unspsc_cache:
            return self._unspsc_cache[sat_key]

        try:
            result = self.execute("product.unspsc.code", "search_read",
                                  [("code", "=", sat_key)],
                                  fields=["id"], limit=1)
            unspsc_id = result[0]["id"] if result else False
            self._unspsc_cache[sat_key] = unspsc_id
            return unspsc_id
        except Exception:
            self._unspsc_cache[sat_key] = False
            return False

    def find_product_by_sku(self, sku):
        """Find existing product by default_code"""
        if sku in self._sku_cache:
            return self._sku_cache[sku]

        result = self.execute("product.template", "search_read",
                              [("default_code", "=", sku)],
                              fields=["id", "default_code"], limit=1)
        prod_id = result[0]["id"] if result else None
        self._sku_cache[sku] = prod_id
        return prod_id

    def create_product(self, vals):
        """Create a new product"""
        prod_id = self.execute("product.template", "create", [vals])
        self._sku_cache[vals.get("default_code", "")] = prod_id
        return prod_id

    def update_product(self, prod_id, vals):
        """Update existing product with fresh connection"""
        # Use fresh ServerProxy to avoid stale connections
        fresh_models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        fresh_models.execute_kw(
            self.db, self.uid, self.password,
            "product.template", "write", [[prod_id], vals]
        )
        return prod_id

    def setup_pricelists(self):
        """Crea o encuentra las 3 listas de precios: Online, Menudeo, Proyecto.
        Retorna dict con IDs de Odoo para cada lista."""
        pricelists = {
            "online":   {"name": "Online (Ecommerce)",  "sequence": 1},
            "menudeo":  {"name": "Menudeo (Mostrador)", "sequence": 2},
            "proyecto": {"name": "Proyecto (Cotización)", "sequence": 3},
        }
        result = {}
        for key, info in pricelists.items():
            existing = self.execute("product.pricelist", "search_read",
                                    [("name", "=", info["name"])],
                                    fields=["id"], limit=1)
            if existing:
                result[key] = existing[0]["id"]
                log(f"  Lista '{info['name']}' ya existe: ID {result[key]}")
            else:
                new_id = self.execute("product.pricelist", "create", [{
                    "name": info["name"],
                    "sequence": info["sequence"],
                    "currency_id": 33,  # MXN (ID estándar en Odoo mexicano)
                }])
                result[key] = new_id
                log(f"  Lista '{info['name']}' creada: ID {new_id}")
        return result

    def set_pricelist_price(self, pricelist_id, product_id, price):
        """Crea o actualiza un item de pricelist para un producto específico"""
        existing = self.execute("product.pricelist.item", "search_read",
            [("pricelist_id", "=", pricelist_id),
             ("product_tmpl_id", "=", product_id),
             ("applied_on", "=", "0_product_variant")],
            fields=["id"], limit=1)
        vals = {
            "pricelist_id": pricelist_id,
            "product_tmpl_id": product_id,
            "applied_on": "0_product_variant",
            "compute_price": "fixed",
            "fixed_price": price,
        }
        if existing:
            self.execute("product.pricelist.item", "write",
                         [[existing[0]["id"]], {"fixed_price": price}])
        else:
            self.execute("product.pricelist.item", "create", [vals])

    def preload_skus(self):
        """Preload all existing SKUs for fast lookup"""
        log("Preloading existing SKUs...")
        products = self.execute("product.template", "search_read",
                                [("default_code", "!=", False)],
                                fields=["id", "default_code"])
        for p in products:
            self._sku_cache[p["default_code"]] = p["id"]
        log(f"  Loaded {len(self._sku_cache)} existing SKUs")


# === MAIN SYNC ===
def sync_category(odoo, token, cat, limit=None, skip_images=False, dry_run=False):
    """Sync all products from one Syscom category"""
    cat_id = cat["syscom_id"]
    cat_name = cat["name"]
    categ_id = cat["categ_id"]
    expense_id = cat["expense"]
    ecom_ids = cat["ecom_ids"]

    log(f"\n{'='*60}")
    log(f"CATEGORY: {cat_name} (Syscom ID: {cat_id})")
    log(f"  → Odoo categ_id: {categ_id}, expense: {expense_id}, ecom: {ecom_ids}")

    created = 0
    updated = 0
    errors = 0
    page = 1
    total_processed = 0

    while True:
        # Fetch page from Syscom
        time.sleep(1.5)  # Rate limit
        try:
            data = syscom_fetch(token, cat_id, page)
        except Exception as e:
            log(f"  ERROR fetching page {page}: {e}")
            errors += 1
            break

        productos = data.get("productos", [])
        total_pages = data.get("paginas", 0)

        if not productos:
            break

        log(f"  Page {page}/{total_pages}: {len(productos)} products")

        for p in productos:
            if limit and total_processed >= limit:
                log(f"  Limit {limit} reached")
                return created, updated, errors

            sku = str(p.get("modelo", "")).strip()
            if not sku:
                continue

            try:
                # Extract data
                nombre = str(p.get("titulo", "Producto sin nombre"))
                # precio_descuento ya viene en MXN (API usa &moneda=MXN)
                costo_real = num(p.get("precios", {}).get("precio_descuento",
                                p.get("precios", {}).get("precio_1", 0)))
                list_price = calculate_price(costo_real, cat_id, "online")
                sat_key = str(p.get("sat_key", ""))
                marca = str(p.get("marca", ""))
                img_url = str(p.get("img_portada", ""))
                uom_nombre = p.get("unidad_de_medida", {}).get("nombre", "Pieza") if p.get("unidad_de_medida") else "Pieza"
                uom_id = UOM_MAP.get(uom_nombre, DEFAULT_UOM)

                # UNSPSC lookup
                time.sleep(0.2)
                unspsc_id = odoo.lookup_unspsc(sat_key)

                # Image (optional)
                image_b64 = None
                if not skip_images and img_url:
                    image_b64 = download_image_base64(img_url)

                # Check if exists
                existing_id = odoo.find_product_by_sku(sku)

                if dry_run:
                    op = "UPDATE" if existing_id else "CREATE"
                    log(f"    [DRY] {op} {sku:>25s} | ${list_price:>10,.2f} | SAT:{sat_key} → UNSPSC:{unspsc_id} | {nombre[:40]}", also_print=False)
                    total_processed += 1
                    if existing_id:
                        updated += 1
                    else:
                        created += 1
                    continue

                # If not found by SKU, skip if barcode conflict would occur
                if not existing_id:
                    try:
                        barcode_check = odoo.execute("product.template", "search_read",
                            [("barcode", "=", sku)], fields=["id", "default_code"], limit=1)
                        if barcode_check:
                            # Product exists with different SKU but same barcode
                            existing_id = barcode_check[0]["id"]
                            odoo._sku_cache[sku] = existing_id
                            log(f"    BARCODE MATCH {sku} → existing ID:{existing_id} (SKU:{barcode_check[0].get('default_code','?')})", also_print=False)
                    except Exception:
                        pass

                if existing_id:
                    # === UPDATE (safe: only prices, category, ecom) ===
                    update_vals = {
                        "standard_price": costo_real,
                        "list_price": list_price,
                        "public_categ_ids": [(6, 0, ecom_ids)],
                    }
                    if unspsc_id:
                        update_vals["unspsc_code_id"] = unspsc_id
                    if image_b64:
                        update_vals["image_1920"] = image_b64

                    time.sleep(1)
                    try:
                        odoo.update_product(existing_id, update_vals)
                        updated += 1
                    except Exception as ue:
                        # Retry once with just prices
                        time.sleep(2)
                        try:
                            odoo.update_product(existing_id, {
                                "standard_price": costo_real,
                                "list_price": list_price,
                            })
                            updated += 1
                            log(f"    RETRY OK {sku} (prices only)", also_print=False)
                        except Exception as ue2:
                            log(f"    SKIP {sku} (ID:{existing_id}): {str(ue2)[:200]}")
                            errors += 1
                    image_b64 = None

                else:
                    # === CREATE ===
                    create_vals = {
                        "name": nombre,
                        "default_code": sku,
                        # barcode removed: conflicts with existing products that have different SKUs
                        "standard_price": costo_real,
                        "list_price": list_price,
                        "categ_id": categ_id,
                        "uom_id": uom_id,
                        "property_account_income_id": INCOME_ACCOUNT,
                        "property_account_expense_id": expense_id,
                        "description_sale": f"Marca: {marca}" if marca else "",
                        "sale_ok": True,
                        "purchase_ok": True,
                        "public_categ_ids": [(6, 0, ecom_ids)],
                    }
                    if unspsc_id:
                        create_vals["unspsc_code_id"] = unspsc_id
                    if image_b64:
                        create_vals["image_1920"] = image_b64

                    time.sleep(0.3)
                    odoo.create_product(create_vals)
                    created += 1
                    image_b64 = None

                # === LISTAS DE PRECIOS: Menudeo y Proyecto ===
                prod_id = existing_id if existing_id else odoo.find_product_by_sku(sku)
                if prod_id and PRICELIST_IDS.get("menudeo") and PRICELIST_IDS.get("proyecto"):
                    try:
                        precio_menudeo = calculate_price(costo_real, cat_id, "menudeo")
                        precio_proyecto = calculate_price(costo_real, cat_id, "proyecto")
                        odoo.set_pricelist_price(PRICELIST_IDS["menudeo"], prod_id, precio_menudeo)
                        odoo.set_pricelist_price(PRICELIST_IDS["proyecto"], prod_id, precio_proyecto)
                    except Exception as pl_err:
                        log(f"    PRICELIST ERR {sku}: {str(pl_err)[:100]}", also_print=False)

                total_processed += 1

                if total_processed % 25 == 0:
                    log(f"    ... {total_processed} processed (C:{created} U:{updated} E:{errors})")

            except Exception as e:
                errors += 1
                log(f"    ERROR {sku}: {str(e)[:150]}")

        # Next page
        if page >= total_pages or total_pages == 0:
            break
        page += 1

        # Refresh token every 5 pages
        if page % 5 == 0:
            try:
                token = get_syscom_token()
            except Exception:
                pass

    log(f"  DONE {cat_name}: Created={created} Updated={updated} Errors={errors}")
    return created, updated, errors


def save_progress(cat_idx, stats):
    """Save progress for resume capability"""
    with open(PROGRESS_FILE, "w") as f:
        json.dump({
            "last_category_index": cat_idx,
            "timestamp": datetime.now().isoformat(),
            "stats": stats
        }, f, indent=2)


def load_progress():
    """Load progress for resume"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return None


def main():
    # Parse arguments
    dry_run = "--dry-run" in sys.argv
    skip_images = "--skip-images" in sys.argv
    resume = "--resume" in sys.argv

    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    single_cat = None
    if "--category" in sys.argv:
        idx = sys.argv.index("--category")
        single_cat = int(sys.argv[idx + 1])

    # Load Odoo config
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    log(f"\n{'#'*60}")
    log(f"# SYSCOM → ODOO PRODUCTION SYNC")
    log(f"# Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"# Target: {config['url']}")
    log(f"# Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    log(f"# Images: {'SKIP' if skip_images else 'DOWNLOAD'}")
    if limit:
        log(f"# Limit: {limit} per category")
    if single_cat:
        log(f"# Single category: {single_cat}")
    log(f"{'#'*60}")

    # Connect to Odoo
    odoo = OdooSync(config)
    odoo.connect()

    # Preload existing SKUs for fast lookup
    time.sleep(1)
    odoo.preload_skus()

    # Crear/obtener las 3 listas de precios
    if not dry_run:
        log("Configurando listas de precios...")
        pricelist_ids = odoo.setup_pricelists()
        PRICELIST_IDS.update(pricelist_ids)
        log(f"  Online: ID {PRICELIST_IDS.get('online', 'N/A')}")
        log(f"  Menudeo: ID {PRICELIST_IDS.get('menudeo', 'N/A')}")
        log(f"  Proyecto: ID {PRICELIST_IDS.get('proyecto', 'N/A')}")

    # Get Syscom token
    token = get_syscom_token()
    if not token:
        log("ERROR: Failed to get Syscom token")
        sys.exit(1)
    log("Syscom token obtained ✅")

    # Determine starting point
    start_idx = 0
    if resume:
        progress = load_progress()
        if progress:
            start_idx = progress["last_category_index"] + 1
            log(f"Resuming from category index {start_idx}")

    # Filter categories
    cats_to_sync = CATEGORIES
    if single_cat:
        cats_to_sync = [c for c in CATEGORIES if c["syscom_id"] == single_cat]
        if not cats_to_sync:
            log(f"ERROR: Category {single_cat} not found")
            sys.exit(1)

    # Run sync
    total_created = 0
    total_updated = 0
    total_errors = 0

    for i, cat in enumerate(cats_to_sync):
        if i < start_idx:
            continue

        try:
            c, u, e = sync_category(odoo, token, cat,
                                     limit=limit,
                                     skip_images=skip_images,
                                     dry_run=dry_run)
            total_created += c
            total_updated += u
            total_errors += e

            save_progress(i, {
                "created": total_created,
                "updated": total_updated,
                "errors": total_errors
            })

            # Refresh token between categories
            time.sleep(2)
            try:
                token = get_syscom_token()
            except Exception:
                pass

        except KeyboardInterrupt:
            log(f"\n⚠️  Interrupted! Progress saved at category index {i}")
            save_progress(i - 1, {
                "created": total_created,
                "updated": total_updated,
                "errors": total_errors
            })
            break
        except Exception as e:
            log(f"FATAL ERROR in category {cat['name']}: {e}")
            total_errors += 1

    # Final summary
    log(f"\n{'='*60}")
    log(f"SYNC COMPLETE")
    log(f"  Created: {total_created}")
    log(f"  Updated: {total_updated}")
    log(f"  Errors:  {total_errors}")
    log(f"  Total:   {total_created + total_updated + total_errors}")
    log(f"  Log:     {LOG_FILE}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
