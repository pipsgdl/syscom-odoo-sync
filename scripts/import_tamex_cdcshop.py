#!/usr/bin/env python3
"""
Importa catálogos limpios Tamex + CDCShop a Odoo producción.
- Crea/actualiza product.template (default_code=sku)
- Crea product.supplierinfo (vínculo a partner Tamex/CDCShop)
- Crea/asigna product.attribute.value para Marca (attribute_id=10)
- NO publica productos (is_published=False)
"""
import json
import time
import sys
import xmlrpc.client
from datetime import datetime

# === Config ===
URL = "https://ocean-tech.odoo.com"
DB = "ocean-tech"
UID = 2
PASSWORD = "M1ercole$"
ATTR_BRAND_ID = 10
SLEEP = 0.05
LOG_PATH = "/tmp/import_tamex_cdcshop.log"

models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def execute(model, method, args, kwargs=None):
    return models.execute_kw(DB, UID, PASSWORD, model, method, args, kwargs or {})


def get_or_create_partner(name_options, website):
    """Busca partner por cualquiera de los nombres; si no, crea."""
    for name in name_options:
        ids = execute("res.partner", "search", [[["name", "=", name]]], {"limit": 1})
        if ids:
            log(f"Partner '{name}' encontrado id={ids[0]}")
            return ids[0]
    new_id = execute(
        "res.partner",
        "create",
        [{
            "name": name_options[0],
            "is_company": True,
            "supplier_rank": 1,
            "website": website,
        }],
    )
    log(f"Partner '{name_options[0]}' creado id={new_id}")
    return new_id


# Cache local de marcas: brand_name (lower) -> attribute.value id
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
    """Crea o actualiza la attribute_line de Marca en el template para incluir el value."""
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
    stats = {"new": 0, "updated": 0, "errors": 0, "skipped": 0, "brands_created": 0}
    brands_before = set(brand_cache.keys())
    total = len(items)
    log(f"=== {name}: procesando {total} items ===")

    for i, p in enumerate(items, 1):
        sku = (p.get("sku") or "").strip()
        if not sku:
            stats["skipped"] += 1
            continue
        title = (p.get("title") or sku).strip()
        brand = (p.get("brand") or "").strip()
        url = (p.get("url") or "").strip()
        price = safe_price(p.get("price"))

        try:
            existing = execute(
                "product.template",
                "search",
                [[["default_code", "=", sku]]],
                {"limit": 1},
            )
            if existing:
                tmpl_id = existing[0]
                stats["updated"] += 1
            else:
                tmpl_id = execute(
                    "product.template",
                    "create",
                    [{
                        "name": title[:200],
                        "default_code": sku,
                        "is_published": False,
                        "sale_ok": True,
                        "purchase_ok": True,
                        "type": "consu",
                        "description_sale": f"[{provider_label} ref] {url}",
                    }],
                )
                stats["new"] += 1

            # supplierinfo: revisar duplicado
            supp = execute(
                "product.supplierinfo",
                "search",
                [[
                    ["partner_id", "=", partner_id],
                    ["product_tmpl_id", "=", tmpl_id],
                    ["product_code", "=", sku],
                ]],
                {"limit": 1},
            )
            if not supp:
                execute(
                    "product.supplierinfo",
                    "create",
                    [{
                        "partner_id": partner_id,
                        "product_code": sku,
                        "product_tmpl_id": tmpl_id,
                        "price": price,
                    }],
                )

            # marca
            if brand:
                bv = get_or_create_brand_value(brand)
                assign_brand_to_template(tmpl_id, bv)

            time.sleep(SLEEP)
        except Exception as e:
            stats["errors"] += 1
            log(f"  ERROR sku={sku}: {e}")
            # abort temprano si >10% en primeros 100
            if i <= 100 and stats["errors"] > 10:
                log(f"  ABORT: {stats['errors']} errores en primeros {i} items")
                stats["aborted"] = True
                return stats

        if i % 100 == 0:
            log(
                f"  {name} {i}/{total} | new={stats['new']} upd={stats['updated']} err={stats['errors']}"
            )

    stats["brands_created"] = len(set(brand_cache.keys()) - brands_before)
    return stats


def main():
    t0 = time.time()
    log("=== INICIO IMPORT TAMEX + CDCSHOP ===")

    # Test conexión
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    version = common.version()
    log(f"Odoo version: {version}")

    load_brand_cache()
    initial_brand_count = len(brand_cache)

    tamex_partner = get_or_create_partner(["Tamex"], "https://tamex.mx")
    cdc_partner = get_or_create_partner(
        ["Centro de Conectividad CDC", "CDCShop"], "https://cdcshop.com.mx"
    )

    with open("/tmp/tamex_catalog.json") as f:
        tamex = json.load(f)
    with open("/tmp/cdcshop_catalog.json") as f:
        cdc = json.load(f)

    tamex_stats = process_catalog("TAMEX", tamex, tamex_partner, "Tamex")
    cdc_stats = process_catalog("CDCSHOP", cdc, cdc_partner, "CDCShop")

    elapsed = time.time() - t0
    final_brand_count = len(brand_cache)

    # Top 10 marcas (brand_cache contiene todas; tomemos las nuevas + populares)
    log("=" * 60)
    log("=== REPORTE FINAL ===")
    log(f"Tamex: nuevos={tamex_stats['new']} updates={tamex_stats['updated']} errores={tamex_stats['errors']} skipped={tamex_stats['skipped']} marcas_creadas={tamex_stats['brands_created']}")
    log(f"CDCShop: nuevos={cdc_stats['new']} updates={cdc_stats['updated']} errores={cdc_stats['errors']} skipped={cdc_stats['skipped']} marcas_creadas={cdc_stats['brands_created']}")
    log(f"Total marcas en Odoo (attr=10): {initial_brand_count} → {final_brand_count} (+{final_brand_count-initial_brand_count})")

    # Top 10 marcas por frecuencia en catálogos
    from collections import Counter
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
