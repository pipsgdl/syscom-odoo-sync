#!/usr/bin/env python3
"""
INGRAM MICRO → Odoo Production Sync
====================================
Carga catalogo de Ingram Micro (mx.ingrammicro.com) a Odoo produccion.

Sigue el patron CVA/TVC/Syscom sync:
- 3 listas de precios: Online, Menudeo, Proyecto
- SKU = manufacturePartNumber (VPN) cuando coincide con productos Syscom/TVC/CVA
       fallback: partNumber Ingram (campo Ingram propio)
- Imagenes: descarga y sube a Odoo
- Supplier: Ingram Micro (ID 369 en Odoo)
- Stock: agregado de todos los warehouses de Ingram
- Precios: en USD, convertidos a MXN con TC del dia (Banxico → fallback er-api)

Auth: OAuth2 con refresh_token rotativo (Okta tenant Ingram)
   El refresh_token se obtiene UNA vez del browser logueado y se rota cada hora.

Margenes Ingram (proyecto Ocean Tech, Abril 2026):
  Proyectores:        online 10%, menudeo 20%, proyecto 28%
  UPS/Energia:        online 18%, menudeo 28%, proyecto 33%
  Pantallas:          online 15%, menudeo 25%, proyecto 32%
  Soportes/Mounts:    online 20%, menudeo 35%, proyecto 45%
  Computo:            online 07%, menudeo 17%, proyecto 23%
  Redes/Networking:   online 15%, menudeo 28%, proyecto 33%
  Almacenamiento:     online 12%, menudeo 22%, proyecto 28%
  Audio/Video:        online 12%, menudeo 22%, proyecto 30%
  Default:            online 10%, menudeo 20%, proyecto 28%

Uso:
  python3 ingram_to_odoo_sync.py                # Full sync (~10k productos por keyword)
  python3 ingram_to_odoo_sync.py --dry-run      # Preview sin escribir Odoo
  python3 ingram_to_odoo_sync.py --limit 50     # Solo 50 productos
  python3 ingram_to_odoo_sync.py --vendor APC   # Solo un vendor
  python3 ingram_to_odoo_sync.py --status       # Ver progreso ultima ejecucion
  python3 ingram_to_odoo_sync.py --refresh-token  # Solo rotar token y salir

Requiere variables en .env:
  INGRAM_REFRESH_TOKEN      = <43 chars del browser localStorage["okta-token-storage"].refreshToken.refreshToken>
  INGRAM_OAUTH_CLIENT_ID    = <20 chars del browser localStorage["okta-token-storage"].idToken.clientId>
  INGRAM_CUSTOMER_NUMBER    = 80697300
  BANXICO_TOKEN             = (opcional, si lo registras en sieapis.banxico.org.mx)
"""

import json
import xmlrpc.client
import ssl
import urllib.request
import urllib.parse
import urllib.error
import base64
import time
import sys
import os
import uuid
from datetime import datetime
from pathlib import Path

# curl_cffi para evitar bloqueo Akamai (TLS fingerprint Chrome)
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    print("⚠️  curl_cffi no instalado. Akamai puede bloquear las requests.")
    print("    Instala: pip install curl_cffi (o source .venv/bin/activate)")

# === CONFIGURACION ===
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPTS_DIR)
LOGS_DIR = os.path.join(REPO_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

CONFIG_PATH = "/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json"
ENV_PATHS = [
    os.path.join(REPO_DIR, ".env"),
    "/Volumes/HIKSEMI 512/Claude code/LICITABOT/.env",
]
LOG_FILE = os.path.join(LOGS_DIR, f"ingram_sync_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
PROGRESS_FILE = os.path.join(SCRIPTS_DIR, "ingram_sync_progress.json")
TOKEN_CACHE = os.path.join(SCRIPTS_DIR, ".ingram_token_cache.json")
TC_CACHE = os.path.join(SCRIPTS_DIR, ".tc_cache.json")

# Cargar variables del primer .env que exista
for ep in ENV_PATHS:
    if os.path.exists(ep):
        with open(ep) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        break

INGRAM_REFRESH_TOKEN = os.environ.get("INGRAM_REFRESH_TOKEN", "")
INGRAM_OAUTH_CLIENT_ID = os.environ.get("INGRAM_OAUTH_CLIENT_ID", "")
INGRAM_CUSTOMER_NUMBER = os.environ.get("INGRAM_CUSTOMER_NUMBER", "80697300")
BANXICO_TOKEN = os.environ.get("BANXICO_TOKEN", "")

# Endpoints Ingram
INGRAM_HOST = "mx.ingrammicro.com"
INGRAM_OAUTH_HOST = "myaccount.ingrammicro.com"
INGRAM_OAUTH_PATH = "/oauth2/aus4rmpuo7DK22t9R357/v1/token"
INGRAM_PRODUCTS_API = f"https://{INGRAM_HOST}/api/product/v1/products"

# IDs Odoo verificados en produccion
INGRAM_SUPPLIER_ID = 369   # res.partner: Ingram Micro
PRICELIST_ONLINE = 3        # Online (Ecommerce)
PRICELIST_MENUDEO = 4       # Menudeo (Mostrador)
PRICELIST_PROYECTO = 5      # Proyecto (Cotizacion)
CURRENCY_MXN = 33

# Margenes (online, menudeo, proyecto)
MARGINS = {
    "proyectores":     (0.10, 0.20, 0.28),
    "ups":             (0.18, 0.28, 0.33),
    "pantallas":       (0.15, 0.25, 0.32),
    "soportes":        (0.20, 0.35, 0.45),
    "computo":         (0.07, 0.17, 0.23),
    "networking":      (0.15, 0.28, 0.33),
    "almacenamiento":  (0.12, 0.22, 0.28),
    "audiovideo":      (0.12, 0.22, 0.30),
    "impresion":       (0.10, 0.18, 0.25),
    "perifericos":     (0.18, 0.30, 0.38),
    "software":        (0.13, 0.20, 0.25),
    "default":         (0.10, 0.20, 0.28),
}

# Mapeo categoria Ingram → margin_key
INGRAM_CATEGORY_MARGINS = {
    "Protección Eléctrica":          "ups",
    "Equipamiento de Energía":       "ups",
    "Comunicaciones":                "networking",
    "Networking":                    "networking",
    "Computadoras":                  "computo",
    "Componentes":                   "computo",
    "Almacenamiento":                "almacenamiento",
    "Dispositivos de Video":         "audiovideo",
    "Pantallas":                     "pantallas",
    "Proyección":                    "proyectores",
    "Soportes":                      "soportes",
    "Accesorios":                    "perifericos",
    "Periféricos":                   "perifericos",
    "Impresión":                     "impresion",
    "Software":                      "software",
}

ctx = ssl.create_default_context()


def log(msg, also_print=True):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# =============================================================================
# OAuth2 — Refresh token rotativo
# =============================================================================

def load_token_cache():
    if os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_token_cache(data):
    with open(TOKEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_CACHE, 0o600)


def refresh_access_token():
    """Refrescar access_token usando refresh_token rotativo.
    Retorna access_token o None.
    Guarda el nuevo refresh_token (rota cada vez)."""
    cache = load_token_cache()
    refresh_token = cache.get("refresh_token") or INGRAM_REFRESH_TOKEN

    if not refresh_token:
        log("ERROR: INGRAM_REFRESH_TOKEN no esta configurado.")
        log("       Sacalo del browser: localStorage['okta-token-storage'].refreshToken.refreshToken")
        return None
    if not INGRAM_OAUTH_CLIENT_ID:
        log("ERROR: INGRAM_OAUTH_CLIENT_ID no esta configurado.")
        log("       Sacalo del browser: localStorage['okta-token-storage'].idToken.clientId")
        return None

    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": INGRAM_OAUTH_CLIENT_ID,
        "scope": "offline_access email profile openid",
    }).encode()

    req = urllib.request.Request(
        f"https://{INGRAM_OAUTH_HOST}{INGRAM_OAUTH_PATH}",
        data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            d = json.loads(r.read().decode())
        # Guardar nuevo refresh (rotativo) + access
        new_cache = {
            "access_token": d["access_token"],
            "refresh_token": d.get("refresh_token", refresh_token),  # rotacion
            "expires_at": int(time.time()) + int(d.get("expires_in", 3600)) - 60,  # margen 60s
            "scope": d.get("scope"),
            "rotated_at": datetime.now().isoformat(),
        }
        save_token_cache(new_cache)
        log(f"  Token OAuth refrescado (expira en {d.get('expires_in')}s)")
        return d["access_token"]
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode()[:300]
        log(f"  ERROR refresh OAuth {e.code}: {body_resp}")
        if e.code in (400, 401):
            log("  El refresh_token expiro o se invalido.")
            log("  Loguea de nuevo en Chrome y copia el nuevo refresh_token al .env")
        return None
    except Exception as e:
        log(f"  ERROR refresh OAuth: {e}")
        return None


def get_valid_access_token():
    """Devuelve access token vigente (refresca si necesario)."""
    cache = load_token_cache()
    if cache.get("access_token") and cache.get("expires_at", 0) > int(time.time()):
        return cache["access_token"]
    return refresh_access_token()


# =============================================================================
# Tipo de Cambio USD → MXN
# =============================================================================

def get_tc_usd_mxn():
    """Obtener TC USD→MXN del dia (Banxico → fallback er-api).
    Cache 1 dia."""
    if os.path.exists(TC_CACHE):
        try:
            with open(TC_CACHE) as f:
                c = json.load(f)
            if c.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return c["rate"], c.get("source")
        except Exception:
            pass

    # Banxico SIE (requiere token gratuito)
    if BANXICO_TOKEN:
        try:
            url = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF63528/datos/oportuno"
            req = urllib.request.Request(url, headers={
                "Bmx-Token": BANXICO_TOKEN, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode())
            dato = d["bmx"]["series"][0]["datos"][0]
            rate = float(dato["dato"].replace(",", ""))
            with open(TC_CACHE, "w") as f:
                json.dump({"date": datetime.now().strftime("%Y-%m-%d"),
                           "rate": rate, "source": "banxico_sf63528"}, f)
            return rate, "banxico"
        except Exception as e:
            log(f"  Banxico fallo: {e}; cayendo a fallback")

    # Fallback: open.er-api.com
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/USD",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            d = json.loads(r.read().decode())
        rate = float(d["rates"]["MXN"])
        with open(TC_CACHE, "w") as f:
            json.dump({"date": datetime.now().strftime("%Y-%m-%d"),
                       "rate": rate, "source": "er-api"}, f)
        return rate, "er-api"
    except Exception as e:
        log(f"  ERROR obteniendo TC: {e}")
        return None, None


# =============================================================================
# Ingram API client
# =============================================================================

def ingram_headers(access_token):
    cid = str(uuid.uuid4())
    return {
        "authorization": f"Bearer {access_token}",
        "accept": "application/json",
        "content-type": "application/json",
        "accept-language": "es-MX",
        "countrycode": "MX", "im-countrycode": "MX",
        "customernumber": INGRAM_CUSTOMER_NUMBER, "im-customernumber": INGRAM_CUSTOMER_NUMBER,
        "distributionchannel": "10",
        "division": "MX",
        "im-acceptlanguage": "es-MX",
        "im-environment": "prod",
        "im-microfrontendid": "product-microfrontend",
        "im-resellerid": INGRAM_CUSTOMER_NUMBER,
        "im-senderid": "XVANTAGE",
        "im-sitecode": "mx",
        "im-userid": INGRAM_CUSTOMER_NUMBER,
        "isocountrycode": "MX",
        "correlationid": cid, "im-correlationid": cid,
    }


def ingram_search(access_token, keyword="", page=1, size=100, vendor=None,
                  category=None, sort="relevance", retries=3):
    """Buscar productos en Ingram. Usa curl_cffi (impersona Chrome) si está disponible
    para esquivar el bloqueo TLS-fingerprint de Akamai."""
    body = {
        "EnablePNA": True,
        "HasDefaultFilters": False,
        "categoryHierarchy": [category] if category else [],
        "filters": [{"name": "vendorname", "values": [vendor]}] if vendor else [],
        "keyword": keyword,
        "page": page,
        "searchId": str(uuid.uuid4()),
        "size": size,
        "sort": [sort],
        "visitorId": str(uuid.uuid4()),
        "DisableSuggestionSearch": False,
    }
    headers_extra = {
        "Origin": "https://mx.ingrammicro.com",
        "Referer": "https://mx.ingrammicro.com/cep/app/product/productsearch",
    }
    headers = {**ingram_headers(access_token), **headers_extra}

    for attempt in range(retries):
        try:
            if HAS_CFFI:
                r = cffi_requests.post(INGRAM_PRODUCTS_API,
                    json=body, headers=headers, impersonate="chrome120", timeout=60)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401 and attempt == 0:
                    log("  401: refrescando token y reintentando")
                    access_token = refresh_access_token()
                    if not access_token:
                        return None
                    headers["authorization"] = f"Bearer {access_token}"
                    continue
                if r.status_code == 429:
                    wait = 30 * (attempt + 1)
                    log(f"  429 rate limit — esperando {wait}s")
                    time.sleep(wait)
                    continue
                log(f"  ERROR busqueda Ingram {r.status_code}: {r.text[:300]}")
                return None
            else:
                # Fallback a urllib (suele fallar con Akamai)
                req = urllib.request.Request(INGRAM_PRODUCTS_API, method="POST",
                    data=json.dumps(body).encode(), headers=headers)
                with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            log(f"  ERROR busqueda Ingram (intento {attempt+1}/{retries}): {e}")
            time.sleep(5)
    return None


def download_image_b64(url):
    """Descargar imagen del CDN Ingram. IMPORTANTE: el CDN hace content-negotiation y
    por defecto sirve AVIF (no soportado por Odoo). Forzamos JPEG con Accept header."""
    if not url:
        return None
    try:
        if HAS_CFFI:
            r = cffi_requests.get(url, impersonate="chrome120", timeout=20,
                                  headers={"Accept": "image/jpeg,image/png"})
            if r.status_code == 200 and len(r.content) > 500:
                # Validar que sea JPEG/PNG real (magic bytes)
                magic = r.content[:8]
                is_jpeg = magic[:3] == b'\xff\xd8\xff'
                is_png  = magic[:8] == b'\x89PNG\r\n\x1a\n'
                if is_jpeg or is_png:
                    return base64.b64encode(r.content).decode()
        else:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/jpeg,image/png",
            })
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                data = r.read()
                if data and len(data) > 500:
                    magic = data[:8]
                    if magic[:3] == b'\xff\xd8\xff' or magic[:8] == b'\x89PNG\r\n\x1a\n':
                        return base64.b64encode(data).decode()
    except Exception:
        pass
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
        self._sku_cache = {}    # {default_code: {id, has_image}}
        self._vpn_cache = {}    # {VPN normalizado: id} para match cross-supplier

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

    def preload_skus(self, with_supplier_data=False):
        """Cargar default_code de productos existentes para matching rapido.
        Si with_supplier_data=True, también carga supplierinfo Ingram (para modo --diff)."""
        log("Precargando SKUs de Odoo...")
        offset, batch = 0, 2000
        while True:
            time.sleep(0.3)
            prods = self.ex("product.template", "search_read",
                [[("default_code", "!=", False)]],
                {"fields": ["id", "default_code", "image_1920", "list_price", "standard_price"],
                 "offset": offset, "limit": batch})
            if not prods:
                break
            for p in prods:
                self._sku_cache[p["default_code"].strip().upper()] = {
                    "id": p["id"], "has_image": bool(p["image_1920"]),
                    "list_price": p.get("list_price", 0),
                    "standard_price": p.get("standard_price", 0),
                }
            offset += len(prods)
            if len(prods) < batch:
                break
        log(f"  {len(self._sku_cache)} SKUs cargados")

        if with_supplier_data:
            log("Precargando supplierinfo Ingram (partner=369)...")
            self._supplier_cache = {}
            offset = 0
            while True:
                time.sleep(0.3)
                sups = self.ex("product.supplierinfo", "search_read",
                    [[("partner_id", "=", INGRAM_SUPPLIER_ID)]],
                    {"fields": ["id", "product_tmpl_id", "product_code", "price"],
                     "offset": offset, "limit": batch})
                if not sups:
                    break
                for s in sups:
                    if s.get("product_tmpl_id"):
                        self._supplier_cache[s["product_tmpl_id"][0]] = {
                            "id": s["id"], "price": s.get("price", 0),
                            "product_code": s.get("product_code", ""),
                        }
                offset += len(sups)
                if len(sups) < batch:
                    break
            log(f"  {len(self._supplier_cache)} supplierinfo Ingram cargados")

    def find_product(self, sku_ingram, vpn):
        """Buscar producto existente en Odoo por:
        1) default_code = SKU Ingram
        2) default_code = VPN (cuando coincide con Syscom/CVA/TVC)"""
        if sku_ingram and sku_ingram.strip().upper() in self._sku_cache:
            return self._sku_cache[sku_ingram.strip().upper()]
        if vpn and vpn.strip().upper() in self._sku_cache:
            return self._sku_cache[vpn.strip().upper()]
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

    def upsert_supplier(self, product_id, sku_ingram, price_mxn):
        existing = self.ex("product.supplierinfo", "search_read",
            [[("product_tmpl_id", "=", product_id),
              ("partner_id", "=", INGRAM_SUPPLIER_ID)]],
            {"fields": ["id"], "limit": 1})
        vals = {"partner_id": INGRAM_SUPPLIER_ID, "product_tmpl_id": product_id,
                "price": price_mxn, "product_code": sku_ingram,
                "currency_id": CURRENCY_MXN, "min_qty": 1}
        if existing:
            self.ex("product.supplierinfo", "write",
                [[existing[0]["id"]], {"price": price_mxn, "product_code": sku_ingram}])
        else:
            self.ex("product.supplierinfo", "create", [vals])


# =============================================================================
# Sync logic
# =============================================================================

def calculate_prices_mxn(cost_usd, tc, margin_key):
    """Calcular precios MXN con IVA 16% e IVA aplicado al final."""
    if cost_usd <= 0 or tc <= 0:
        return (0.0, 0.0, 0.0)
    cost_mxn = cost_usd * tc
    m = MARGINS.get(margin_key, MARGINS["default"])
    return tuple(round(cost_mxn * (1 + mg) * 1.16, 2) for mg in m)


def margin_key_from_category(category, subcategory):
    """Inferir margin_key desde categoria/subcategoria Ingram."""
    if category in INGRAM_CATEGORY_MARGINS:
        return INGRAM_CATEGORY_MARGINS[category]
    text = (category + " " + subcategory).lower()
    if any(x in text for x in ["ups", "energ"]):
        return "ups"
    if any(x in text for x in ["proyec", "projector"]):
        return "proyectores"
    if any(x in text for x in ["pantalla", "display", "monitor"]):
        return "pantallas"
    if any(x in text for x in ["red", "switch", "router", "wifi"]):
        return "networking"
    if any(x in text for x in ["impres", "printer"]):
        return "impresion"
    if any(x in text for x in ["almacen", "storage", "ssd", "disco"]):
        return "almacenamiento"
    if any(x in text for x in ["audio", "video", "av "]):
        return "audiovideo"
    return "default"


def process_item(item, odoo, tc, dry_run=False, stats=None, diff_mode=False):
    """Procesar un item del catalogo Ingram → Odoo.
    Si diff_mode=True, solo escribe si hay cambio real (precio o supplierinfo)."""
    sku_ingram = (item.get("partNumber") or "").strip()
    vpn = (item.get("manufacturePartNumber") or item.get("exManufacturerpartnumber") or "").strip()
    if not sku_ingram and not vpn:
        return "skip_no_sku"

    pricing = item.get("pricingInformation") or {}
    inventory = item.get("inventoryInformation") or {}
    cost_usd = float(pricing.get("dealerPrice") or 0)
    msrp_usd = float(pricing.get("msrpPrice") or 0)
    currency = pricing.get("priceCurrency", "USD")
    if cost_usd <= 0:
        return "skip_no_price"

    # Stock total
    stock_total = inventory.get("totalAvailableQuantity")
    if stock_total is None:
        whs = inventory.get("warehouseDetails") or []
        stock_total = sum((w.get("quantityAvailable") or 0) for w in whs)

    title = (item.get("title") or item.get("shortdescription") or "").strip()
    long_desc = item.get("longDescription") or ""
    vendor = item.get("vendorname") or ""
    category = item.get("category") or ""
    subcategory = item.get("subcategory") or ""
    image_url = item.get("imageUrl") or ""

    margin_key = margin_key_from_category(category, subcategory)
    p_online, p_menudeo, p_proyecto = calculate_prices_mxn(cost_usd, tc, margin_key)
    cost_mxn = cost_usd * tc

    # Match en Odoo
    found = odoo.find_product(sku_ingram, vpn)
    is_new = found is None
    product_id = found["id"] if found else None
    has_image = found.get("has_image") if found else False

    # === MODO DIFFERENCIAL ===
    if diff_mode and not is_new:
        # Producto existe — solo actualizar si cambió precio o stock significativamente
        existing_supplier = getattr(odoo, '_supplier_cache', {}).get(product_id)
        if existing_supplier:
            old_price = float(existing_supplier.get("price", 0))
            # Umbral: solo escribir si cambio > $1 MXN o > 0.5%
            price_changed = abs(cost_mxn - old_price) > 1.0 and abs(cost_mxn - old_price) / max(old_price, 1) > 0.005
            if not price_changed:
                if stats is not None:
                    stats["unchanged"] = stats.get("unchanged", 0) + 1
                return "unchanged"
            # Solo actualizar supplierinfo + pricelist (NO recrear producto, NO descargar imagen)
            if dry_run:
                log(f"  [DRY-DIFF] PRICE Δ {sku_ingram} ${old_price:.2f}→${cost_mxn:.2f} "
                    f"({(cost_mxn-old_price)/max(old_price,1)*100:+.1f}%)")
                stats["price_changed"] = stats.get("price_changed", 0) + 1
                return "dry"
            try:
                # Actualizar supplierinfo
                odoo.ex("product.supplierinfo", "write",
                    [[existing_supplier["id"]],
                     {"price": cost_mxn, "product_code": sku_ingram}])
                # Actualizar las 3 pricelists
                odoo.set_pricelist(PRICELIST_ONLINE, product_id, p_online)
                odoo.set_pricelist(PRICELIST_MENUDEO, product_id, p_menudeo)
                odoo.set_pricelist(PRICELIST_PROYECTO, product_id, p_proyecto)
                # Actualizar standard_price + list_price del producto
                odoo.ex("product.template", "write",
                    [[product_id], {"standard_price": cost_mxn, "list_price": p_online}])
                # Actualizar cache local
                existing_supplier["price"] = cost_mxn
                if stats is not None:
                    stats["price_changed"] = stats.get("price_changed", 0) + 1
                return "ok_diff"
            except Exception as e:
                log(f"  ERROR diff {sku_ingram}: {e}")
                return "error_upsert"

    if dry_run:
        log(f"  [DRY] {'NEW' if is_new else 'UPD'} {sku_ingram} VPN={vpn} {vendor} "
            f"cost=${cost_usd:.2f}USD={cost_mxn:.2f}MXN online={p_online:.2f} stock={stock_total}")
        if stats is not None:
            stats["new" if is_new else "updated"] = stats.get("new" if is_new else "updated", 0) + 1
        return "dry"

    # Construir vals para upsert
    name = title or f"{vendor} {sku_ingram}".strip()
    vals = {
        "name": name[:255],
        "default_code": vpn or sku_ingram,
        "list_price": p_online,         # PV publico = lista Online
        "standard_price": cost_usd * tc,  # costo en MXN
        "is_storable": True,
        "type": "consu",  # consumible (mismo que CVA — Ocean Tech no maneja stock fisico Ingram)
        "available_threshold": 0,
        "allow_out_of_stock_order": True,
        "is_published": stock_total > 0,
        "website_published": stock_total > 0,
    }
    if long_desc:
        vals["description_sale"] = long_desc[:2000]
        vals["website_description"] = f"<div>{long_desc}</div>"

    # Imagen solo si nuevo o sin imagen
    img_b64 = None
    if image_url and (is_new or not has_image):
        img_b64 = download_image_b64(image_url)
        if img_b64:
            vals["image_1920"] = img_b64

    # Crear o actualizar
    try:
        pid = odoo.upsert_product(vals, existing_id=product_id)
    except Exception as e:
        # Si falla por imagen, reintentar sin imagen
        if "image" in str(e).lower() or "decoded" in str(e).lower():
            vals.pop("image_1920", None)
            try:
                pid = odoo.upsert_product(vals, existing_id=product_id)
            except Exception as e2:
                log(f"  ERROR upsert (sin imagen): {e2}")
                return "error_upsert"
        else:
            log(f"  ERROR upsert: {e}")
            return "error_upsert"
    if not pid:
        return "error_upsert"

    # Pricelists
    odoo.set_pricelist(PRICELIST_ONLINE, pid, p_online)
    odoo.set_pricelist(PRICELIST_MENUDEO, pid, p_menudeo)
    odoo.set_pricelist(PRICELIST_PROYECTO, pid, p_proyecto)

    # Supplier
    odoo.upsert_supplier(pid, sku_ingram, cost_usd * tc)

    # Cache para evitar re-lookups
    if vpn:
        odoo._sku_cache[vpn.upper()] = {"id": pid, "has_image": has_image or bool(image_url)}
    if sku_ingram:
        odoo._sku_cache[sku_ingram.upper()] = {"id": pid, "has_image": has_image or bool(image_url)}

    if stats is not None:
        stats["new" if is_new else "updated"] = stats.get("new" if is_new else "updated", 0) + 1
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
    refresh_only = "--refresh-token" in sys.argv
    diff_mode = "--diff" in sys.argv
    limit = None
    vendor_filter = None
    keyword = ""

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--vendor" and i + 1 < len(sys.argv):
            vendor_filter = sys.argv[i + 1]
        if arg == "--keyword" and i + 1 < len(sys.argv):
            keyword = sys.argv[i + 1]

    # Ver progreso ultima ejecucion
    if status_only:
        prog = load_progress()
        if prog:
            log(f"Ultima corrida: {prog.get('completed_at') or prog.get('started_at')}")
            log(f"  Procesados: {prog.get('processed')}")
            log(f"  Nuevos:     {prog.get('new', 0)}")
            log(f"  Actualizad: {prog.get('updated', 0)}")
            log(f"  Errores:    {prog.get('errors', 0)}")
        else:
            log("No hay progreso guardado.")
        return

    # Solo rotar token y salir
    if refresh_only:
        tok = refresh_access_token()
        log(f"Token rotado: {'OK' if tok else 'FALLO'}")
        return

    log("=" * 70)
    log(f"INGRAM → Odoo Sync (dry_run={dry_run}, diff={diff_mode}, limit={limit}, vendor={vendor_filter})")
    log("=" * 70)

    # 1) Token
    access_token = get_valid_access_token()
    if not access_token:
        log("ABORT: no se pudo obtener access token.")
        return

    # 2) Tipo de cambio
    tc, tc_source = get_tc_usd_mxn()
    if not tc:
        log("ABORT: no se pudo obtener TC USD/MXN")
        return
    log(f"TC USD/MXN = {tc:.4f} (fuente: {tc_source})")

    # 3) Odoo
    if not os.path.exists(CONFIG_PATH):
        log(f"ABORT: config Odoo no existe en {CONFIG_PATH}")
        return
    cfg = json.load(open(CONFIG_PATH))
    odoo = OdooSync(cfg)
    odoo.connect()
    odoo.preload_skus(with_supplier_data=diff_mode)

    # 4) Iterar paginas
    stats = {"processed": 0, "new": 0, "updated": 0, "errors": 0,
             "skipped_no_price": 0, "skipped_no_sku": 0,
             "unchanged": 0, "price_changed": 0,
             "started_at": datetime.now().isoformat(),
             "tc": tc, "tc_source": tc_source,
             "diff_mode": diff_mode,
             "vendor_filter": vendor_filter, "limit": limit}

    page = 1
    SIZE = 100
    while True:
        log(f"Pagina {page} (size={SIZE}, vendor={vendor_filter or '*'})")
        result = ingram_search(access_token, keyword=keyword, page=page, size=SIZE,
                               vendor=vendor_filter)
        if not result:
            stats["errors"] += 1
            break
        prods = result.get("products") or {}
        items = prods.get("items") or []
        total = prods.get("totalCount", 0)
        total_pages = prods.get("totalPages", 0)
        if not items:
            log("  Sin items, fin.")
            break

        log(f"  {len(items)} items en pagina (total={total}, paginas={total_pages})")

        for item in items:
            try:
                r = process_item(item, odoo, tc, dry_run=dry_run, stats=stats, diff_mode=diff_mode)
                if r == "skip_no_price":
                    stats["skipped_no_price"] += 1
                elif r == "skip_no_sku":
                    stats["skipped_no_sku"] += 1
                stats["processed"] += 1
            except Exception as e:
                stats["errors"] += 1
                log(f"  ERROR procesando item: {e}")

            if limit and stats["processed"] >= limit:
                log(f"Limite de {limit} alcanzado, deteniendo.")
                break

        # Persistir progreso
        stats["last_page"] = page
        save_progress(stats)

        if limit and stats["processed"] >= limit:
            break
        if page >= total_pages or page >= 100:  # cap defensivo
            break

        page += 1
        time.sleep(0.5)  # respeto al API

    # Reporte final
    stats["completed_at"] = datetime.now().isoformat()
    save_progress(stats)
    log("=" * 70)
    log("RESUMEN")
    log(f"  Procesados:       {stats['processed']}")
    log(f"  Nuevos:           {stats['new']}")
    log(f"  Actualizados:     {stats['updated']}")
    if diff_mode:
        log(f"  Cambios precio:   {stats.get('price_changed',0)}")
        log(f"  Sin cambios:      {stats.get('unchanged',0)}")
    log(f"  Sin precio:       {stats['skipped_no_price']}")
    log(f"  Sin SKU:          {stats['skipped_no_sku']}")
    log(f"  Errores:          {stats['errors']}")
    log("=" * 70)


if __name__ == "__main__":
    main()
