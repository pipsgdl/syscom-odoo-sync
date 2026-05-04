#!/usr/bin/env python3
"""
EXEL DEL NORTE → Odoo Production Sync
======================================
Sync del catálogo de Exel del Norte (xlstore) a Odoo.

Estrategia híbrida:
- Login con Playwright (browser real, captura cookies de sesión)
- Sync con curl_cffi usando esas cookies (rápido)

Cobertura:
- 16 categorías top-level (Computo, Impresión, Consumibles, Almacenamiento,
  Electrónica, Cámara, Audio, Redes, Software, Energía, Telefonía,
  Servidores, Papel, Oficina, POS, Videovigilancia)
- Por categoría: lista SKUs vía /Productos/buscar.aspx?IdCategoria=N
- Por SKU: popup /PopUp_producto_y_existencias.aspx con precio + stock por sucursal

Match Odoo: default_code = SKU Exel (clave propietaria) → fallback nombre

Margenes Exel del Norte (mayorista cómputo + papelería):
  Computo:           online 7%, menudeo 17%, proyecto 23%
  Impresion:         online 10%, menudeo 18%, proyecto 25%
  Consumibles:       online 20%, menudeo 35%, proyecto 45%
  Almacenamiento:    online 12%, menudeo 22%, proyecto 28%
  Networking:        online 15%, menudeo 28%, proyecto 33%
  Audio/Video:       online 12%, menudeo 22%, proyecto 30%
  Software:          online 13%, menudeo 20%, proyecto 25%
  Energia:           online 18%, menudeo 28%, proyecto 33%
  Papel/Oficina:     online 15%, menudeo 30%, proyecto 40%   ← licitaciones gob
  POS:               online 12%, menudeo 22%, proyecto 30%
  Videovigilancia:   online 5%,  menudeo 18%, proyecto 30%
  Default:           online 10%, menudeo 20%, proyecto 28%

Uso:
  python3 exel_to_odoo_sync.py                # Full sync
  python3 exel_to_odoo_sync.py --diff         # Solo cambios precio
  python3 exel_to_odoo_sync.py --dry-run --limit 20
  python3 exel_to_odoo_sync.py --category 1   # Solo Cómputo
  python3 exel_to_odoo_sync.py --refresh-login # Re-loguear (cookies expiraron)
  python3 exel_to_odoo_sync.py --status

Requiere:
  - cookies en ~/syscom-odoo-sync/.exel_session.json (generar con
    exel_login_browser.py)
  - EXEL_USUARIO + EXEL_PASSWORD en .env (para auto-relogin)
"""

import json
import xmlrpc.client
import re
import time
import sys
import os
import subprocess
from datetime import datetime
from pathlib import Path
from html import unescape

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    print("⚠️  Instalar curl_cffi: pip install curl_cffi")
    sys.exit(1)

# === CONFIG ===
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPTS_DIR)
LOGS_DIR = os.path.join(REPO_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

CONFIG_PATH = "/Volumes/HIKSEMI 512/Antigravity/mcp-odoo/odoo_config_prod.json"
LOG_FILE = os.path.join(LOGS_DIR, f"exel_sync_{datetime.now().strftime('%Y%m%d_%H%M')}.log")
PROGRESS_FILE = os.path.join(SCRIPTS_DIR, "exel_sync_progress.json")
SESSION_FILE = os.path.join(REPO_DIR, ".exel_session.json")

EXEL_BASE = "https://www.exel.com.mx/xlstore"
EXEL_LIST_URL = f"{EXEL_BASE}/Productos/buscar.aspx?IdCategoria={{cat_id}}"
EXEL_DETAIL_URL = f"{EXEL_BASE}/Productos/Detalle/{{sku}}"
EXEL_POPUP_URL = f"{EXEL_BASE}/Productos/PopUp_producto_y_existencias.aspx?id_producto={{sku}}"

# 16 categorías de XL-Store con margenes
CATEGORIES = {
    1:  ("Computo",                       "computo"),
    2:  ("Impresion y Multifuncionales",  "impresion"),
    3:  ("Consumibles",                   "consumibles"),
    4:  ("Almacenamiento",                "almacenamiento"),
    5:  ("Electronica de Consumo",        "default"),
    6:  ("Camara Video y Proyeccion",     "audiovideo"),
    7:  ("Audio y Entretenimiento",       "audiovideo"),
    8:  ("Redes",                         "networking"),
    9:  ("Software y Garantias",          "software"),
    10: ("Energia y Cables",              "energia"),
    11: ("Telefonia",                     "default"),
    12: ("Servidores y Almacenamiento",   "almacenamiento"),
    13: ("Papel",                         "papel_oficina"),
    14: ("Oficina y Escolar",             "papel_oficina"),
    15: ("Puntos de Venta",               "pos"),
    16: ("Videovigilancia",               "videovig"),
}

# Odoo
EXEL_SUPPLIER_ID = 94      # res.partner: "Exel del Norte"
PRICELIST_ONLINE = 3
PRICELIST_MENUDEO = 4
PRICELIST_PROYECTO = 5
CURRENCY_MXN = 33

MARGINS = {
    "computo":         (0.07, 0.17, 0.23),
    "impresion":       (0.10, 0.18, 0.25),
    "consumibles":     (0.20, 0.35, 0.45),
    "almacenamiento":  (0.12, 0.22, 0.28),
    "networking":      (0.15, 0.28, 0.33),
    "audiovideo":      (0.12, 0.22, 0.30),
    "software":        (0.13, 0.20, 0.25),
    "energia":         (0.18, 0.28, 0.33),
    "papel_oficina":   (0.15, 0.30, 0.40),
    "pos":             (0.12, 0.22, 0.30),
    "videovig":        (0.05, 0.18, 0.30),
    "default":         (0.10, 0.20, 0.28),
}


def log(msg, also_print=True):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# =============================================================================
# Sesión Exel
# =============================================================================

def load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        s = json.load(open(SESSION_FILE))
        return {c["name"]: c["value"] for c in s.get("cookies", [])
                if "exel.com.mx" in c.get("domain", "")}
    except Exception as e:
        log(f"  ERROR leyendo sesión: {e}")
        return None


def refresh_login_via_playwright():
    """Lanzar exel_login_browser.py para refrescar cookies."""
    log("Refrescando login Exel via Playwright...")
    r = subprocess.run(
        ["python", os.path.join(SCRIPTS_DIR, "exel_login_browser.py")],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        log(f"  ERROR Playwright login: {r.stderr[:300]}")
        return False
    log("  ✓ login refrescado")
    return True


def get_session(auto_relogin=True):
    """Obtener cookies válidas. Si están viejas, refrescar via Playwright."""
    cookies = load_session()
    if not cookies:
        if not auto_relogin:
            return None
        if not refresh_login_via_playwright():
            return None
        cookies = load_session()
    return cookies


def make_session(cookies):
    s = cffi_requests.Session(impersonate="chrome120")
    s.cookies.update(cookies)
    s.headers.update({
        "Referer": EXEL_BASE + "/",
        "Origin": "https://www.exel.com.mx",
        "Accept-Language": "es-MX,es;q=0.9",
    })
    return s


def is_session_valid(sess):
    """Validar sesión pegando a una página interna."""
    try:
        r = sess.get(EXEL_BASE + "/MiCuenta/CompraRapida", timeout=15, allow_redirects=False)
        # Si redirige a /Acceso, sesión inválida
        loc = r.headers.get("Location", "")
        if r.status_code == 200 and "txtPassword" not in r.text:
            return True
        if r.status_code in (302, 301) and "Acceso" in loc:
            return False
        return r.status_code == 200
    except Exception:
        return False


# =============================================================================
# Scraping Exel
# =============================================================================

SKU_PATTERN = re.compile(r'/Productos/Detalle/([A-Z0-9]{6,20})')

def fetch_category_skus(sess, cat_id, retries=2):
    """Listar todos los SKUs visibles en una categoría."""
    url = EXEL_LIST_URL.format(cat_id=cat_id)
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 200 and "txtPassword" not in r.text:
                skus = list(set(SKU_PATTERN.findall(r.text)))
                return skus
            time.sleep(2)
        except Exception as e:
            log(f"  ERROR cat {cat_id}: {e}")
            time.sleep(3)
    return []


def fetch_product_info(sess, sku, retries=2):
    """Obtener precio + stock + nombre via popup HTML."""
    url = EXEL_POPUP_URL.format(sku=sku)
    for attempt in range(retries):
        try:
            r = sess.get(url, timeout=20)
            if r.status_code != 200:
                time.sleep(1)
                continue
            html = r.text
            # Si nos rebotó al login
            if "txtPassword" in html and "login" in html.lower():
                return None
            # Precio (formato "$ 1,234.56")
            m_price = re.search(r'\$\s*([\d,]+\.\d{2})', html)
            precio = float(m_price.group(1).replace(",", "")) if m_price else 0.0
            # Stocks por sucursal: <td>NUM</td>
            tds = re.findall(r'<td[^>]*>\s*(\d+)\s*</td>', html)
            stocks = [int(x) for x in tds if x.isdigit()]
            stock_total = sum(stocks)
            # Título / nombre del producto
            m_title = re.search(r'<h\d[^>]*>([^<]+)</h\d>', html)
            nombre = unescape(m_title.group(1).strip()) if m_title else ""
            return {
                "sku": sku,
                "precio": precio,
                "stock": stock_total,
                "stocks_por_sucursal": stocks,
                "nombre": nombre,
            }
        except Exception as e:
            time.sleep(2)
    return None


def fetch_product_detail(sess, sku):
    """Obtener detalle completo del producto (nombre, marca, descripción, imagen)."""
    url = EXEL_DETAIL_URL.format(sku=sku)
    try:
        r = sess.get(url, timeout=20)
        if r.status_code != 200:
            return None
        html = r.text
        m_title = re.search(r'<title>\s*([^<]+?)\s*</title>', html)
        title = unescape(m_title.group(1).strip()) if m_title else ""
        m_desc = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
        descripcion = unescape(m_desc.group(1).strip()) if m_desc else ""
        m_marca = re.search(r'(?:Marca|Brand):\s*([^<\n]+)', html, re.IGNORECASE)
        marca = unescape(m_marca.group(1).strip()[:60]) if m_marca else ""
        m_img = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
        imagen = m_img.group(1) if m_img else ""
        return {"title": title, "descripcion": descripcion, "marca": marca, "imagen": imagen}
    except Exception:
        return None


# =============================================================================
# Odoo client
# =============================================================================

class OdooSync:
    def __init__(self, config):
        self.url = config["url"]; self.db = config["db"]
        self.username = config["username"]; self.password = config["password"]
        self.uid = None; self.models = None
        self._sku_cache = {}; self._supplier_cache = {}

    def connect(self):
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.username, self.password, {})
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        log(f"Conectado Odoo UID={self.uid}")

    def ex(self, model, method, args, kwargs={}):
        for attempt in range(3):
            try:
                return self.models.execute_kw(self.db, self.uid, self.password, model, method, args, kwargs)
            except Exception as e:
                if "429" in str(e) or "Too Many" in str(e):
                    wait = 30 * (attempt + 1)
                    log(f"  Odoo 429 — esperando {wait}s")
                    time.sleep(wait)
                    self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
                else:
                    raise
        return None

    def preload(self, with_supplier_data=False):
        log("Precargando SKUs Odoo...")
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
        log(f"  {len(self._sku_cache)} SKUs Odoo cargados")
        if with_supplier_data:
            log("Precargando supplierinfo Exel (partner=94)...")
            offset = 0
            while True:
                time.sleep(0.3)
                sups = self.ex("product.supplierinfo", "search_read",
                    [[("partner_id", "=", EXEL_SUPPLIER_ID)]],
                    {"fields": ["id", "product_tmpl_id", "product_code", "price"],
                     "offset": offset, "limit": batch})
                if not sups: break
                for s in sups:
                    if s.get("product_tmpl_id"):
                        self._supplier_cache[s["product_tmpl_id"][0]] = {
                            "id": s["id"], "price": s.get("price", 0),
                            "product_code": s.get("product_code", ""),
                        }
                offset += len(sups)
                if len(sups) < batch: break
            log(f"  {len(self._supplier_cache)} supplierinfo Exel cargados")

    def find_product(self, sku_exel):
        return self._sku_cache.get(sku_exel.strip().upper())

    def upsert_product(self, vals, existing_id=None):
        if existing_id:
            self.ex("product.template", "write", [[existing_id], vals])
            return existing_id
        new_id = self.ex("product.template", "create", [vals])
        return new_id[0] if isinstance(new_id, list) else new_id

    def set_pricelist(self, pricelist_id, product_id, price):
        existing = self.ex("product.pricelist.item", "search_read",
            [[("pricelist_id", "=", pricelist_id), ("product_tmpl_id", "=", product_id),
              ("applied_on", "=", "1_product")]], {"fields": ["id"], "limit": 1})
        vals = {"pricelist_id": pricelist_id, "product_tmpl_id": product_id,
                "applied_on": "1_product", "compute_price": "fixed", "fixed_price": price}
        if existing:
            self.ex("product.pricelist.item", "write", [[existing[0]["id"]], {"fixed_price": price}])
        else:
            self.ex("product.pricelist.item", "create", [vals])

    def upsert_supplier(self, product_id, sku_exel, price):
        existing = self.ex("product.supplierinfo", "search_read",
            [[("product_tmpl_id", "=", product_id), ("partner_id", "=", EXEL_SUPPLIER_ID)]],
            {"fields": ["id"], "limit": 1})
        vals = {"partner_id": EXEL_SUPPLIER_ID, "product_tmpl_id": product_id,
                "price": price, "product_code": sku_exel,
                "currency_id": CURRENCY_MXN, "min_qty": 1}
        if existing:
            self.ex("product.supplierinfo", "write", [[existing[0]["id"]],
                {"price": price, "product_code": sku_exel}])
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


def process_sku(sess, sku, margin_key, odoo, dry_run=False, diff_mode=False, stats=None):
    """Procesar un SKU Exel → Odoo."""
    info = fetch_product_info(sess, sku)
    if not info or info["precio"] <= 0:
        return "skip_no_price"

    cost_mxn = info["precio"]
    stock_total = info["stock"]
    p_online, p_menudeo, p_proyecto = calculate_prices(cost_mxn, margin_key)

    product_id = odoo.find_product(sku)
    is_new = product_id is None

    # Modo diferencial
    if diff_mode and not is_new:
        existing = odoo._supplier_cache.get(product_id)
        if existing:
            old_price = float(existing.get("price", 0))
            price_changed = abs(cost_mxn - old_price) > 1.0 and abs(cost_mxn - old_price) / max(old_price, 1) > 0.005
            if not price_changed:
                if stats is not None:
                    stats["unchanged"] = stats.get("unchanged", 0) + 1
                return "unchanged"
            if dry_run:
                log(f"  [DRY-DIFF] {sku} ${old_price:.2f}→${cost_mxn:.2f}")
                stats["price_changed"] = stats.get("price_changed", 0) + 1
                return "dry"
            try:
                odoo.ex("product.supplierinfo", "write",
                    [[existing["id"]], {"price": cost_mxn, "product_code": sku}])
                odoo.set_pricelist(PRICELIST_ONLINE, product_id, p_online)
                odoo.set_pricelist(PRICELIST_MENUDEO, product_id, p_menudeo)
                odoo.set_pricelist(PRICELIST_PROYECTO, product_id, p_proyecto)
                odoo.ex("product.template", "write", [[product_id],
                    {"standard_price": cost_mxn, "list_price": p_online,
                     "is_published": stock_total > 0, "website_published": stock_total > 0}])
                existing["price"] = cost_mxn
                stats["price_changed"] = stats.get("price_changed", 0) + 1
                return "ok_diff"
            except Exception as e:
                log(f"  ERROR diff {sku}: {e}")
                return "error"

    if dry_run:
        log(f"  [DRY] {'NEW' if is_new else 'UPD'} {sku:<15} ${cost_mxn:.2f} stock={stock_total} online=${p_online:.2f}  '{info.get('nombre','')[:40]}'")
        stats["new" if is_new else "updated"] = stats.get("new" if is_new else "updated", 0) + 1
        return "dry"

    # Crear/actualizar producto completo
    name = info.get("nombre") or f"Exel {sku}"
    vals = {
        "name": name[:255],
        "default_code": sku,
        "list_price": p_online,
        "standard_price": cost_mxn,
        "is_storable": True,
        "type": "consu",
        "available_threshold": 0,
        "allow_out_of_stock_order": True,
        "is_published": stock_total > 0,
        "website_published": stock_total > 0,
    }

    try:
        pid = odoo.upsert_product(vals, existing_id=product_id)
    except Exception as e:
        log(f"  ERROR upsert {sku}: {e}")
        return "error"
    if not pid:
        return "error"

    odoo.set_pricelist(PRICELIST_ONLINE, pid, p_online)
    odoo.set_pricelist(PRICELIST_MENUDEO, pid, p_menudeo)
    odoo.set_pricelist(PRICELIST_PROYECTO, pid, p_proyecto)
    odoo.upsert_supplier(pid, sku, cost_mxn)

    odoo._sku_cache[sku.upper()] = pid
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
    diff_mode = "--diff" in sys.argv
    status_only = "--status" in sys.argv
    refresh_login = "--refresh-login" in sys.argv
    limit = None
    only_category = None

    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--category" and i + 1 < len(sys.argv):
            only_category = int(sys.argv[i + 1])

    if status_only:
        prog = load_progress()
        if prog:
            log(f"Última: {prog.get('completed_at') or prog.get('started_at')}")
            log(f"  Procesados: {prog.get('processed',0)}")
            log(f"  Nuevos:     {prog.get('new',0)}")
            log(f"  Actualizad: {prog.get('updated',0)}")
            log(f"  Cambios:    {prog.get('price_changed',0)}")
            log(f"  Errores:    {prog.get('errors',0)}")
        return

    if refresh_login:
        ok = refresh_login_via_playwright()
        log(f"Refresh login: {'OK' if ok else 'FALLO'}")
        return

    log("=" * 70)
    log(f"EXEL → Odoo Sync (dry={dry_run}, diff={diff_mode}, cat={only_category}, limit={limit})")
    log("=" * 70)

    # 1) Sesión Exel
    cookies = get_session(auto_relogin=True)
    if not cookies:
        log("ABORT: no hay sesión Exel y auto-relogin falló")
        return
    sess = make_session(cookies)
    if not is_session_valid(sess):
        log("Sesión expirada, refrescando...")
        if not refresh_login_via_playwright():
            log("ABORT: re-login falló")
            return
        cookies = load_session()
        sess = make_session(cookies)

    # 2) Odoo
    cfg = json.load(open(CONFIG_PATH))
    odoo = OdooSync(cfg)
    odoo.connect()
    odoo.preload(with_supplier_data=diff_mode)

    # 3) Iterar categorías
    stats = {"processed": 0, "new": 0, "updated": 0, "errors": 0,
             "skipped_no_price": 0, "unchanged": 0, "price_changed": 0,
             "diff_mode": diff_mode, "started_at": datetime.now().isoformat(),
             "limit": limit}

    cats_to_run = [(only_category, CATEGORIES[only_category])] if only_category \
                  else list(CATEGORIES.items())

    seen_skus = set()  # evitar procesar el mismo SKU 2 veces (algunos están en múltiples categorías)

    for cat_id, (cat_name, margin_key) in cats_to_run:
        log(f"\n=== Categoría {cat_id}: {cat_name} (margin={margin_key}) ===")
        skus = fetch_category_skus(sess, cat_id)
        log(f"  {len(skus)} SKUs encontrados")

        for sku in skus:
            if sku in seen_skus:
                continue
            seen_skus.add(sku)

            try:
                r = process_sku(sess, sku, margin_key, odoo, dry_run=dry_run,
                                diff_mode=diff_mode, stats=stats)
                if r == "skip_no_price":
                    stats["skipped_no_price"] += 1
                elif r == "error":
                    stats["errors"] += 1
                stats["processed"] += 1
            except Exception as e:
                stats["errors"] += 1
                log(f"  ERROR {sku}: {e}")

            if (stats["processed"] % 50) == 0:
                log(f"  Progreso: {stats['processed']} (new={stats['new']} upd={stats['updated']} chg={stats.get('price_changed',0)} err={stats['errors']})")
                save_progress(stats)

            if limit and stats["processed"] >= limit:
                log(f"Limite {limit} alcanzado")
                break

            # Pequeña pausa entre productos
            time.sleep(0.2)

        if limit and stats["processed"] >= limit:
            break

    stats["completed_at"] = datetime.now().isoformat()
    save_progress(stats)
    log("=" * 70)
    log("RESUMEN")
    log(f"  Procesados:    {stats['processed']}")
    log(f"  Nuevos:        {stats['new']}")
    log(f"  Actualizados:  {stats['updated']}")
    if diff_mode:
        log(f"  Cambios precio: {stats.get('price_changed',0)}")
        log(f"  Sin cambios:    {stats.get('unchanged',0)}")
    log(f"  Sin precio:    {stats['skipped_no_price']}")
    log(f"  Errores:       {stats['errors']}")
    log("=" * 70)


if __name__ == "__main__":
    main()
