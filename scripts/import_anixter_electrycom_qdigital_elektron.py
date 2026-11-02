#!/usr/bin/env python3
"""
Importa 4 caches scrapeados (Anixter, Electrycom, QDigital, Elektron) a Odoo
produccion como product.template + product.supplierinfo. NO publica productos.

- Espera a que el scraper de Elektron (PID detectado) termine antes de importar
  ese cache (max 2.5h).
- Cache local de marcas (product.attribute.value attribute_id=10).
- Rate limit 0.05s entre creates.
- Si error masivo (>10% en primeros 100), salta proveedor.
"""
import json
import os
import time
import sys
import subprocess
import xmlrpc.client
from collections import Counter
from datetime import datetime, timedelta

ODOO_URL = "https://ocean-tech.odoo.com"
ODOO_DB = "ocean-tech"
ODOO_UID = 2
ODOO_PWD = "M1ercole$"
ATTR_BRAND_ID = 10  # attribute id "Marca"

LOG_PATH = "/tmp/import_4caches.log"

CACHES = {
    "anixter": "/tmp/anixter_catalog.json",
    "electrycom": "/tmp/electrycom_catalog.json",
    "qdigital": "/tmp/qdigital_catalog.json",
    "elektron": "/tmp/elektron_catalog.json",
}

PARTNER_DEFS = {
    "Anixter":   {"website": "https://www.anixter.com"},
    "Electrycom":{"website": "https://www.electrycom.com"},
    "QDigital":  {"website": "https://qdigital.mx"},
    "Elektron":  {"website": "https://elektron.com.mx"},
}

models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
logf = open(LOG_PATH, "a", buffering=1)


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    logf.write(line + "\n")


def call(model, method, *args, **kwargs):
    delay = 2
    for attempt in range(8):
        try:
            return models.execute_kw(ODOO_DB, ODOO_UID, ODOO_PWD, model, method, list(args), kwargs)
        except xmlrpc.client.ProtocolError as pe:
            if pe.errcode == 429:
                log(f"  429 rate-limited on {model}.{method}, sleep {delay}s (attempt {attempt+1})")
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise
        except (ConnectionResetError, OSError) as ce:
            log(f"  conn err {ce} on {model}.{method}, sleep {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
    raise RuntimeError(f"call exhausted retries: {model}.{method}")


def connect_check():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    delay = 5
    for attempt in range(20):
        try:
            return common.version()
        except xmlrpc.client.ProtocolError as pe:
            if pe.errcode == 429:
                log(f"  connect 429, sleep {delay}s (attempt {attempt+1})")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise
        except Exception as e:
            log(f"  connect err {e}, sleep {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError("connect exhausted retries")


brand_value_cache = {}


def get_or_create_brand_value(brand):
    if not brand:
        return None
    key = brand.strip().upper()
    if not key:
        return None
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


def ensure_partner(name):
    ids = call("res.partner", "search", [["name", "=", name]], limit=1)
    if ids:
        log(f"Partner '{name}' exists: {ids[0]}")
        return ids[0]
    cfg = PARTNER_DEFS.get(name, {})
    pid = call(
        "res.partner",
        "create",
        {
            "name": name,
            "is_company": True,
            "supplier_rank": 1,
            "website": cfg.get("website", ""),
        },
    )
    log(f"Partner '{name}' CREATED: {pid}")
    return pid


def to_float(v):
    try:
        f = float(v)
        if f != f:  # NaN
            return 0.0
        return f
    except Exception:
        return 0.0


def import_catalog(items, partner_id, source_label, item_to_fields):
    """item_to_fields(item) -> dict with keys: code, title, url, brand, price (or None)."""
    new_count = 0
    upd_count = 0
    err_count = 0
    skipped = 0
    brand_counter = Counter()

    total = len(items)
    log(f"=== {source_label}: importing {total} items, partner={partner_id} ===")
    t0 = time.time()

    for i, item in enumerate(items, 1):
        try:
            f = item_to_fields(item)
            code = (f.get("code") or "").strip()
            if not code:
                skipped += 1
                continue

            title = (f.get("title") or code).strip()[:250]
            url = f.get("url", "") or ""
            brand = (f.get("brand") or "").strip()
            price = to_float(f.get("price")) if f.get("price") is not None else 0.0

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
                vals = {
                    "name": title,
                    "default_code": code,
                    "is_published": False,
                    "sale_ok": True,
                    "purchase_ok": True,
                    "type": "consu",
                    "description_sale": f"[{source_label} ref] {url}",
                    "list_price": price if price > 0 else 0,
                }
                tmpl_id = call("product.template", "create", vals)
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
                        "price": price if price > 0 else 0,
                    },
                )
                if not created:
                    upd_count += 1

            # Brand attribute value (best-effort)
            if brand:
                try:
                    bv_id = get_or_create_brand_value(brand)
                    if bv_id and created:
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

            time.sleep(0.15)
        except Exception as e:
            err_count += 1
            log(f"  ERR [{i}] {item.get('sku') or item}: {e}")
            if i == 100 and err_count > 10:
                log(f"!! ABORT {source_label}: too many errors in first 100 ({err_count})")
                break

        if i % 100 == 0:
            elapsed = int(time.time() - t0)
            log(
                f"  progress {source_label} {i}/{total} new={new_count} upd={upd_count} err={err_count} skipped={skipped} ({elapsed}s)"
            )

    elapsed = int(time.time() - t0)
    log(
        f"=== {source_label} DONE: new={new_count} upd={upd_count} err={err_count} skipped={skipped} time={elapsed}s ==="
    )
    log(f"  top brands ({source_label}): {brand_counter.most_common(10)}")
    return new_count, upd_count, err_count, brand_counter, elapsed


# === Field mappers per provider ===

def fields_anixter(item):
    pid = item.get("pid") or ""
    cat = (item.get("cat_slug") or "").replace("-", " ")
    title = (pid + (" - " + cat if cat else "")).strip() or item.get("sku")
    return {
        "code": item.get("sku"),
        "title": title,
        "url": item.get("url"),
        "brand": item.get("brand"),
        "price": None,
    }


def fields_electrycom(item):
    return {
        "code": item.get("sku"),
        "title": item.get("title"),
        "url": item.get("url"),
        "brand": item.get("brand"),
        "price": item.get("price"),
    }


def fields_qdigital(item):
    return {
        "code": item.get("sku"),
        "title": item.get("title"),
        "url": item.get("url"),
        "brand": item.get("brand"),
        "price": item.get("price"),
    }


def fields_elektron(item):
    return {
        "code": item.get("sku"),
        "title": item.get("title"),
        "url": item.get("url"),
        "brand": item.get("brand"),
        "price": item.get("price"),
    }


def wait_for_elektron_scraper(max_seconds=9000):
    """Wait until elektron_scraper process is no longer running (max 2.5h)."""
    start = time.time()
    while True:
        try:
            out = subprocess.run(
                ["pgrep", "-f", "elektron_scraper"],
                capture_output=True, text=True
            )
            if not out.stdout.strip():
                log("Elektron scraper finished.")
                return
        except Exception as e:
            log(f"  pgrep err: {e}")
            return
        elapsed = int(time.time() - start)
        if elapsed > max_seconds:
            log(f"!! Elektron scraper still alive after {elapsed}s -> proceeding with current cache")
            return
        if elapsed % 300 < 60:  # log every ~5 min
            log(f"  Waiting for elektron scraper... {elapsed}s elapsed")
        time.sleep(60)


def total_products():
    try:
        return call("product.template", "search_count", [])
    except Exception as e:
        return f"err {e}"


def main():
    log("######## IMPORT 4 CACHES START ########")

    try:
        ver = connect_check()
        log(f"Odoo version: {ver.get('server_version')}")
    except Exception as e:
        log(f"FATAL connect: {e}")
        sys.exit(1)

    log(f"Total product.template BEFORE: {total_products()}")

    overall = {}

    # 1) ANIXTER
    partner = ensure_partner("Anixter")
    with open(CACHES["anixter"]) as fp:
        items = json.load(fp)
    overall["Anixter"] = import_catalog(items, partner, "Anixter", fields_anixter)

    # 2) ELECTRYCOM
    partner = ensure_partner("Electrycom")
    with open(CACHES["electrycom"]) as fp:
        items = json.load(fp)
    overall["Electrycom"] = import_catalog(items, partner, "Electrycom", fields_electrycom)

    # 3) QDIGITAL
    partner = ensure_partner("QDigital")
    with open(CACHES["qdigital"]) as fp:
        items = json.load(fp)
    overall["QDigital"] = import_catalog(items, partner, "QDigital", fields_qdigital)

    # 4) ELEKTRON (wait for scraper)
    log("Waiting for Elektron scraper to finish (max 2.5h)...")
    wait_for_elektron_scraper(max_seconds=9000)
    partner = ensure_partner("Elektron")
    with open(CACHES["elektron"]) as fp:
        items = json.load(fp)
    overall["Elektron"] = import_catalog(items, partner, "Elektron", fields_elektron)

    # === FINAL REPORT ===
    log("######## FINAL REPORT ########")
    grand_brands = Counter()
    tot_new = tot_upd = tot_err = 0
    for prov, (n, u, e, b, t) in overall.items():
        log(f"{prov:12s} new={n:6d} upd={u:6d} err={e:5d} brands={len(b)} time={t}s")
        grand_brands += b
        tot_new += n
        tot_upd += u
        tot_err += e
    log(f"GRAND TOTAL  new={tot_new} upd={tot_upd} err={tot_err}")
    log(f"Total brand values created/used (cache): {len(brand_value_cache)}")
    log(f"Top 10 brands global (across providers): {grand_brands.most_common(10)}")
    log(f"Total product.template AFTER: {total_products()}")
    log("######## IMPORT 4 CACHES END ########")


if __name__ == "__main__":
    main()
