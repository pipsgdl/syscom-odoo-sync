#!/usr/bin/env python3
"""
Importa catálogos Tamex + CDCShop a Odoo producción CON RETRY EXPONENCIAL para 429.
- Reanuda: SKIP si product.supplierinfo ya existe (partner_id + product_code)
- Crea product.template + product.supplierinfo cuando no existen
- Brand → product.attribute.value en attribute_id=10 (cache local)
- is_published=False, type='consu', sale_ok/purchase_ok=True
- Si list_price del JSON > 0 → asignar a list_price y supplierinfo.price
- Retry exponencial sobre 429: 6 reintentos (sleep 2,4,8,16,32,64s) hasta 8 totales
- Sleep 0.15s entre creates
"""
import json
import time
import sys
import xmlrpc.client
from datetime import datetime
from collections import Counter

# === Config ===
URL = "https://ocean-tech.odoo.com"
DB = "ocean-tech"
UID = 2
PASSWORD = "M1ercole$"
ATTR_BRAND_ID = 10
SLEEP = 0.15
LOG_PATH = "/tmp/import_tamex_cdcshop_retry.log"

TAMEX_PARTNER_ID = 1152
CDC_PARTNER_ID = 1171

# Retry exponencial
RETRY_DELAYS = [2, 4, 8, 16, 32, 64, 64, 64]  # hasta 8 reintentos
MAX_RETRIES = len(RETRY_DELAYS)

models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def is_429(exc):
    s = str(exc)
    return "429" in s or "Too Many Requests" in s


def execute(model, method, args, kwargs=None):
    """Ejecuta con retry exponencial sobre 429 / errores transitorios."""
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return models.execute_kw(DB, UID, PASSWORD, model, method, args, kwargs or {})
        except xmlrpc.client.ProtocolError as e:
            last = e
            if is_429(e) and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                log(f"  [429] {model}.{method} retry {attempt+1}/{MAX_RETRIES} en {delay}s")
                time.sleep(delay)
                continue
            raise
        except (xmlrpc.client.Fault, ConnectionError, OSError) as e:
            last = e
            if is_429(e) and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt]
                log(f"  [429f] {model}.{method} retry {attempt+1}/{MAX_RETRIES} en {delay}s")
                time.sleep(delay)
                continue
            raise
    raise last


# Cache local de marcas
brand_cache = {}


def load_brand_cache():
    vals = execute(
        "product.attribute.value",
        "search_read",
        [[["attribute_id", "=", ATTR_BRAND_ID]]],
        {"fields": ["id", "name"]},
    )
    for v in vals:
        brand_cache[v["name"].strip().lower()] = v["id"]
    log(f"Brand cache cargado: {len(brand_cache)} marcas existentes")


def get_or_create_brand_value(brand):
    if not brand:
        return None
    key = brand.strip().lower()
    if key in brand_cache:
        return brand_cache[key]
    new_id = execute(
        "product.attribute.value",
        "create",
        [{"attribute_id": ATTR_BRAND_ID, "name": brand.strip()}],
    )
    brand_cache[key] = new_id
    log(f"  Marca creada: '{brand}' id={new_id}")
    return new_id


def assign_brand_to_template(tmpl_id, brand_value_id):
    if not brand_value_id:
        return False
    lines = execute(
        "product.template.attribute.line",
        "search_read",
        [[["product_tmpl_id", "=", tmpl_id], ["attribute_id", "=", ATTR_BRAND_ID]]],
        {"fields": ["id", "value_ids"]},
    )
    if lines:
        line = lines[0]
        if brand_value_id in line["value_ids"]:
            return True
        new_vals = list(set(line["value_ids"] + [brand_value_id]))
        execute(
            "product.template.attribute.line",
            "write",
            [[line["id"]], {"value_ids": [(6, 0, new_vals)]}],
        )
        return True
    execute(
        "product.template.attribute.line",
        "create",
        [{
            "product_tmpl_id": tmpl_id,
            "attribute_id": ATTR_BRAND_ID,
            "value_ids": [(6, 0, [brand_value_id])],
        }],
    )
    return True


def safe_price(p):
    try:
        return float(p)
    except Exception:
        return 0.0


def process_catalog(name, items, partner_id, provider_label):
    stats = {"new": 0, "skipped": 0, "errors": 0, "no_sku": 0, "brands_created": 0}
    brands_before = set(brand_cache.keys())
    total = len(items)
    log(f"=== {name}: procesando {total} items (partner_id={partner_id}) ===")

    for i, p in enumerate(items, 1):
        sku = (p.get("sku") or "").strip()
        if not sku:
            stats["no_sku"] += 1
            continue
        title = (p.get("title") or sku).strip()
        brand = (p.get("brand") or "").strip()
        url = (p.get("url") or "").strip()
        price = safe_price(p.get("price"))

        try:
            # 1) SKIP si supplierinfo ya existe (partner_id + product_code)
            existing_supp = execute(
                "product.supplierinfo",
                "search",
                [[
                    ["partner_id", "=", partner_id],
                    ["product_code", "=", sku],
                ]],
                {"limit": 1},
            )
            if existing_supp:
                stats["skipped"] += 1
                if i % 200 == 0:
                    log(f"  {name} {i}/{total} | new={stats['new']} skip={stats['skipped']} err={stats['errors']}")
                continue

            # 2) Buscar template por default_code (puede existir de otro proveedor)
            tmpl_ids = execute(
                "product.template",
                "search",
                [[["default_code", "=", sku]]],
                {"limit": 1},
            )
            if tmpl_ids:
                tmpl_id = tmpl_ids[0]
            else:
                tmpl_vals = {
                    "name": title[:200] or sku,
                    "default_code": sku,
                    "is_published": False,
                    "sale_ok": True,
                    "purchase_ok": True,
                    "type": "consu",
                    "description_sale": f"[{provider_label} ref] {url}" if url else f"[{provider_label}]",
                }
                if price > 0:
                    tmpl_vals["list_price"] = price
                tmpl_id = execute("product.template", "create", [tmpl_vals])

            # 3) Crear supplierinfo
            supp_vals = {
                "partner_id": partner_id,
                "product_code": sku,
                "product_tmpl_id": tmpl_id,
                "price": price if price > 0 else 0.0,
            }
            execute("product.supplierinfo", "create", [supp_vals])

            # 4) Marca
            if brand:
                bv = get_or_create_brand_value(brand)
                assign_brand_to_template(tmpl_id, bv)

            stats["new"] += 1
            time.sleep(SLEEP)
        except Exception as e:
            stats["errors"] += 1
            log(f"  ERROR sku={sku}: {e}")
            # backoff suave si hay error no-429
            time.sleep(0.5)

        if i % 100 == 0:
            log(
                f"  {name} {i}/{total} | new={stats['new']} skip={stats['skipped']} err={stats['errors']}"
            )

    stats["brands_created"] = len(set(brand_cache.keys()) - brands_before)
    return stats


def main():
    t0 = time.time()
    log("=== INICIO IMPORT TAMEX + CDCSHOP (RETRY) ===")

    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    try:
        version = common.version()
        log(f"Odoo version: {version}")
    except Exception as e:
        log(f"WARN no version: {e}")

    load_brand_cache()
    initial_brand_count = len(brand_cache)

    with open("/tmp/tamex_catalog.json") as f:
        tamex = json.load(f)
    with open("/tmp/cdcshop_catalog.json") as f:
        cdc = json.load(f)

    log(f"Cargados: tamex={len(tamex)} cdc={len(cdc)}")

    tamex_stats = process_catalog("TAMEX", tamex, TAMEX_PARTNER_ID, "Tamex")
    cdc_stats = process_catalog("CDCSHOP", cdc, CDC_PARTNER_ID, "CDCShop")

    elapsed = time.time() - t0
    final_brand_count = len(brand_cache)

    log("=" * 60)
    log("=== REPORTE FINAL ===")
    log(f"Tamex:   nuevos={tamex_stats['new']} skipped={tamex_stats['skipped']} errores={tamex_stats['errors']} no_sku={tamex_stats['no_sku']} marcas_creadas={tamex_stats['brands_created']}")
    log(f"CDCShop: nuevos={cdc_stats['new']} skipped={cdc_stats['skipped']} errores={cdc_stats['errors']} no_sku={cdc_stats['no_sku']} marcas_creadas={cdc_stats['brands_created']}")
    log(f"Total marcas attr=10: {initial_brand_count} → {final_brand_count} (+{final_brand_count-initial_brand_count})")

    bcount = Counter()
    for p in tamex + cdc:
        b = (p.get("brand") or "").strip()
        if b:
            bcount[b] += 1
    log("Top 10 marcas (por frecuencia en catálogos):")
    for b, n in bcount.most_common(10):
        log(f"  {b}: {n}")

    log(f"Tiempo total: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log("=== FIN ===")


if __name__ == "__main__":
    main()
