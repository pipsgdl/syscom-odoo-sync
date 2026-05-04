#!/usr/bin/env python3
"""
EXEL — Colector de SKUs por categoría usando Playwright
=========================================================
La paginación de XL-Store usa ASP.NET UpdatePanel (PostBack JS).
No se puede paginar con curl_cffi puro.

Este script:
1. Loguea con Playwright (reutiliza cookies si están vivas)
2. Por cada categoría: navega + click "Siguiente Página" hasta agotar
3. Acumula SKUs únicos
4. Guarda en ~/syscom-odoo-sync/.exel_skus.json

Después, exel_to_odoo_sync.py lee ese archivo y hace popup por SKU con curl_cffi.

Uso:
  python3 exel_collect_skus.py                # todas las categorías
  python3 exel_collect_skus.py --category 1   # solo Cómputo
  python3 exel_collect_skus.py --max-pages 5  # limitar páginas (test)
"""

import os
import sys
import json
import time
import re
from datetime import datetime
from pathlib import Path

for ep in ["/Users/ingfelipe/syscom-odoo-sync/.env"]:
    if os.path.exists(ep):
        for line in Path(ep).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

USER = os.environ.get("EXEL_USUARIO", "")
PWD = os.environ.get("EXEL_PASSWORD", "")
SKUS_FILE = "/Users/ingfelipe/syscom-odoo-sync/.exel_skus.json"

CATEGORIES = {
    1: "Computo", 2: "Impresion y Multifuncionales", 3: "Consumibles",
    4: "Almacenamiento", 5: "Electronica de Consumo", 6: "Camara Video y Proyeccion",
    7: "Audio y Entretenimiento", 8: "Redes", 9: "Software y Garantias",
    10: "Energia y Cables", 11: "Telefonia", 12: "Servidores y Almacenamiento",
    13: "Papel", 14: "Oficina y Escolar", 15: "Puntos de Venta", 16: "Videovigilancia",
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def collect_category_skus(page, cat_id, cat_name, max_pages=100):
    """Navegar a una categoría y paginar hasta agotar."""
    url = f"https://www.exel.com.mx/xlstore/Productos/buscar.aspx?IdCategoria={cat_id}"
    log(f"Cat {cat_id}: {cat_name} — navegando...")
    page.goto(url, wait_until="networkidle", timeout=60000)
    time.sleep(2)

    skus = set()
    page_num = 1
    consecutive_empty = 0

    while page_num <= max_pages:
        # Extraer SKUs visibles
        new_skus = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/Productos/Detalle/"]');
            const skus = new Set();
            for (const a of links) {
                const m = a.href.match(/\\/Detalle\\/([A-Z0-9]+)/);
                if (m) skus.add(m[1]);
            }
            return Array.from(skus);
        }""")

        added = 0
        for s in new_skus:
            if s not in skus:
                skus.add(s)
                added += 1
        log(f"  Pág {page_num}: +{added} (total {len(skus)})")

        if added == 0:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                log(f"  → Sin SKUs nuevos por 2 páginas, fin de categoría")
                break
        else:
            consecutive_empty = 0

        # Verificar si existe botón "Siguiente Página"
        has_next = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
            return btns.some(b => {
                const t = (b.innerText || b.value || '').toLowerCase();
                return (t.includes('siguiente') || t.includes('next')) && b.offsetParent !== null;
            });
        }""")
        if not has_next:
            log("  → No hay botón siguiente, fin")
            break

        # Click + esperar navegación completa (es un postback que recarga)
        try:
            with page.expect_navigation(timeout=20000, wait_until="networkidle"):
                page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
                    for (const b of btns) {
                        const t = (b.innerText || b.value || '').toLowerCase();
                        if ((t.includes('siguiente') || t.includes('next')) && b.offsetParent !== null) {
                            b.scrollIntoView();
                            b.click();
                            return;
                        }
                    }
                }""")
        except Exception as e:
            # Si no hubo navegación es porque era AJAX o el botón ya no existe
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
        time.sleep(2)
        page_num += 1

    log(f"  ✓ Cat {cat_id} ({cat_name}): {len(skus)} SKUs únicos")
    return list(skus)


def main():
    only_cat = None
    max_pages = 100
    headless = True

    for i, arg in enumerate(sys.argv):
        if arg == "--category" and i + 1 < len(sys.argv):
            only_cat = int(sys.argv[i + 1])
        if arg == "--max-pages" and i + 1 < len(sys.argv):
            max_pages = int(sys.argv[i + 1])
        if arg == "--headed":
            headless = False

    from playwright.sync_api import sync_playwright

    cats = [(only_cat, CATEGORIES[only_cat])] if only_cat else list(CATEGORIES.items())

    all_skus = {}
    if os.path.exists(SKUS_FILE):
        try:
            all_skus = json.load(open(SKUS_FILE)).get("by_category", {})
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="es-MX",
        )
        page = ctx.new_page()

        # Login
        log(f"Login con user={USER}")
        page.goto("https://www.exel.com.mx/xlstore/", wait_until="networkidle", timeout=60000)
        page.fill('input[name="ctl00$MainContent$txtUsuario"]', USER)
        page.fill('input[name="ctl00$MainContent$txtPassword"]', PWD)
        page.click('input[name="ctl00$MainContent$btnAceptar"]')
        time.sleep(5)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        # Validar login
        signals = page.evaluate("""() => {
            return {
                hasMyAccount: Array.from(document.querySelectorAll('a, button, span'))
                    .some(el => /mi cuenta|mi ejecutivo|cerrar sesi[oó]n|salir/i.test(el.innerText || '')),
                hasLoginForm: !!document.querySelector('input[name="ctl00$MainContent$txtPassword"]'),
            };
        }""")
        if not signals.get("hasMyAccount") or signals.get("hasLoginForm"):
            log("⚠️  Login parece haber fallado, intentando continuar...")

        # Guardar cookies para reuso de exel_to_odoo_sync.py
        cookies = ctx.cookies()
        json.dump({"cookies": cookies, "captured_at": datetime.now().isoformat()},
                  open("/Users/ingfelipe/syscom-odoo-sync/.exel_session.json", "w"), default=str)
        os.chmod("/Users/ingfelipe/syscom-odoo-sync/.exel_session.json", 0o600)

        # Iterar categorías
        for cat_id, cat_name in cats:
            try:
                skus = collect_category_skus(page, cat_id, cat_name, max_pages=max_pages)
                all_skus[str(cat_id)] = skus
                # Guardar después de cada categoría (resilience)
                json.dump({
                    "captured_at": datetime.now().isoformat(),
                    "by_category": all_skus,
                    "total_unique": len(set(s for sl in all_skus.values() for s in sl)),
                }, open(SKUS_FILE, "w"), indent=2, default=str)
            except Exception as e:
                log(f"  ❌ ERROR cat {cat_id}: {e}")
                continue

        browser.close()

    # Resumen final
    total_unique = len(set(s for sl in all_skus.values() for s in sl))
    log(f"\n{'='*60}")
    log("RESUMEN COLECTOR")
    for cat_id, cat_name in CATEGORIES.items():
        cnt = len(all_skus.get(str(cat_id), []))
        log(f"  Cat {cat_id:>2} {cat_name[:30]:<30}: {cnt:>5} SKUs")
    log(f"  TOTAL únicos: {total_unique}")
    log(f"  Archivo: {SKUS_FILE}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
