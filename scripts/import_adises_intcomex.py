#!/usr/bin/env python3
"""
Importa catalogo Adises e Intcomex a Odoo produccion como product.template
+ product.supplierinfo. NO publica productos (sin precios reales).
"""
import json
import time
import sys
import xmlrpc.client
from collections import Counter
from datetime import datetime

ODOO_URL = "https://ocean-tech.odoo.com"
ODOO_DB = "ocean-tech"
ODOO_UID = 2
ODOO_PWD = "M1ercole$"
ATTR_BRAND_ID = 10  # attribute id "Marca"

LOG_PATH = "/tmp/import_adises_intcomex.log"
ADISES_CACHE = "/tmp/adises_catalog.json"
INTCOMEX_CACHE = "/tmp/intcomex_catalog.json"

models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)

logf = open(LOG_PATH, "a", buffering=1)


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    logf.write(line + "\n")


def call(model, method, *args, **kwargs):
    return models.execute_kw(ODOO_DB, ODOO_UID, ODOO_PWD, model, method, list(args), kwargs)


# Cache attribute values brand
brand_value_cache = {}


def get_or_create_brand_value(brand):
    if not brand:
        return None
    key = brand.strip().upper()
    if key in brand_value_cache:
        return brand_value_cache[key]
    ids = call(
        "product.attribute.value",
        "search",
        [["attribute_id", "=", ATTR_BRAND_ID], ["name", "=ilike", key]],
        limit=1,
    )
    if ids:
        brand_value_cache[key] = ids[0]
        return ids[0]
    try:
        new_id = call(
            "product.attribute.value",
            "create",
            {"attribute_id": ATTR_BRAND_ID, "name": key},
        )
        brand_value_cache[key] = new_id
        log(f"  + brand value created: {key} -> {new_id}")
        return new_id
    except Exception as e:
        log(f"  ! cannot create brand value {key}: {e}")
        return None


def ensure_partner_intcomex():
    ids = call("res.partner", "search", [["name", "=", "Intcomex"]], limit=1)
    if ids:
        log(f"Intcomex partner exists: {ids[0]}")
        return ids[0]
    pid = call(
        "res.partner",
        "create",
        {
            "name": "Intcomex",
            "is_company": True,
            "supplier_rank": 1,
            "website": "https://www.intcomex.com",
        },
    )
    log(f"Intcomex partner created: {pid}")
    return pid


def import_catalog(items, partner_id, source_label, code_field, url_field="url"):
    new_count = 0
    upd_count = 0
    err_count = 0
    skipped = 0
    brand_counter = Counter()

    total = len(items)
    log(f"=== {source_label}: importing {total} items, partner={partner_id} ===")

    for i, item in enumerate(items, 1):
        try:
            code = (item.get(code_field) or "").strip()
            if not code and code_field == "part_number":
                code = (item.get("sku") or "").strip()
            if not code:
                skipped += 1
                continue

            title = (item.get("title") or code).strip()[:250]
            url = item.get(url_field, "")
            brand = (item.get("brand") or "").strip()

            tmpl_ids = call(
                "product.template",
                "search",
                [["default_code", "=", code]],
                limit=1,
            )
            if tmpl_ids:
                tmpl_id = tmpl_ids[0]
                created = False
            else:
                tmpl_id = call(
                    "product.template",
                    "create",
                    {
                        "name": title,
                        "default_code": code,
                        "is_published": False,
                        "sale_ok": True,
                        "purchase_ok": True,
                        "type": "consu",
                        "description_sale": f"[{source_label} ref] {url}",
                    },
                )
                created = True
                new_count += 1
                brand_counter[brand or "(sin marca)"] += 1

            # supplierinfo
            si_ids = call(
                "product.supplierinfo",
                "search",
                [
                    ["partner_id", "=", partner_id],
                    ["product_tmpl_id", "=", tmpl_id],
                    ["product_code", "=", code],
                ],
                limit=1,
            )
            if not si_ids:
                call(
                    "product.supplierinfo",
                    "create",
                    {
                        "partner_id": partner_id,
                        "product_tmpl_id": tmpl_id,
                        "product_code": code,
                        "price": 0,
                    },
                )
                if not created:
                    upd_count += 1

            # Brand attribute value (best-effort, do not fail import)
            if brand:
                try:
                    bv_id = get_or_create_brand_value(brand)
                    if bv_id and created:
                        # Add product.template.attribute.line if missing
                        line_ids = call(
                            "product.template.attribute.line",
                            "search",
                            [
                                ["product_tmpl_id", "=", tmpl_id],
                                ["attribute_id", "=", ATTR_BRAND_ID],
                            ],
                            limit=1,
                        )
                        if not line_ids:
                            call(
                                "product.template.attribute.line",
                                "create",
                                {
                                    "product_tmpl_id": tmpl_id,
                                    "attribute_id": ATTR_BRAND_ID,
                                    "value_ids": [(6, 0, [bv_id])],
                                },
                            )
                except Exception as be:
                    log(f"  ! brand attr fail for {code}: {be}")

            time.sleep(0.1)
        except Exception as e:
            err_count += 1
            log(f"  ERR [{i}] {item.get(code_field)}: {e}")
            if i <= 50 and err_count > 5 and err_count > i * 0.1:
                log(f"!! abort {source_label}: too many errors early ({err_count}/{i})")
                break

        if i % 100 == 0:
            log(
                f"  progress {source_label} {i}/{total} new={new_count} upd={upd_count} err={err_count} skipped={skipped}"
            )

    log(
        f"=== {source_label} DONE: new={new_count} upd={upd_count} err={err_count} skipped={skipped} ==="
    )
    log(f"  top brands ({source_label}): {brand_counter.most_common(10)}")
    return new_count, upd_count, err_count, brand_counter


def main():
    log("######## IMPORT START ########")

    # Verify connectivity
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        ver = common.version()
        log(f"Odoo version: {ver.get('server_version')}")
    except Exception as e:
        log(f"FATAL connect: {e}")
        sys.exit(1)

    # ADISES
    adises_partner = 1144
    adises_check = call("res.partner", "read", [adises_partner], ["name"])
    log(f"Adises partner check: {adises_check}")

    with open(ADISES_CACHE) as f:
        adises = json.load(f)
    a_new, a_upd, a_err, a_brands = import_catalog(
        adises, adises_partner, "Adises", "mod"
    )

    # INTCOMEX
    intcomex_partner = ensure_partner_intcomex()
    with open(INTCOMEX_CACHE) as f:
        intcomex = json.load(f)
    i_new, i_upd, i_err, i_brands = import_catalog(
        intcomex, intcomex_partner, "Intcomex", "part_number"
    )

    log("######## FINAL REPORT ########")
    log(f"Adises:   new={a_new} upd={a_upd} err={a_err}")
    log(f"Intcomex: new={i_new} upd={i_upd} err={i_err}")
    log(f"TOTAL:    new={a_new+i_new} upd={a_upd+i_upd} err={a_err+i_err}")
    total_brands = a_brands + i_brands
    log(f"Top 15 brands global: {total_brands.most_common(15)}")
    log("######## IMPORT END ########")


if __name__ == "__main__":
    main()
