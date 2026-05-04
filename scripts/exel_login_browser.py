#!/usr/bin/env python3
"""
EXEL DEL NORTE — Login con Playwright + captura de cookies de sesión
======================================================================
El portal Exel del Norte (xlstore) tiene Cloudflare Turnstile que exige
ejecutar JavaScript para resolver el challenge antes de aceptar el form
de login. curl_cffi puro no puede.

Solución: Playwright headless abre Chrome real, completa el login
(Turnstile se resuelve automático), captura cookies post-login y las
guarda en ~/syscom-odoo-sync/.exel_session.json.

Después, exel_to_odoo_sync.py usa esas cookies con curl_cffi para
navegar el catálogo rápido (sin overhead de browser).

Uso:
  python3 exel_login_browser.py            # login + guarda cookies
  python3 exel_login_browser.py --headed   # mostrar browser (debug)
  python3 exel_login_browser.py --check    # validar sesión actual sin re-loguear
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Cargar .env
ENV_PATHS = [
    "/Users/ingfelipe/syscom-odoo-sync/.env",
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

EXEL_USUARIO = os.environ.get("EXEL_USUARIO", "")
EXEL_PASSWORD = os.environ.get("EXEL_PASSWORD", "")

SESSION_FILE = "/Users/ingfelipe/syscom-odoo-sync/.exel_session.json"
LOGIN_URL = "https://www.exel.com.mx/xlstore/"
POST_LOGIN_URL = "https://www.exel.com.mx/xlstore/Inicio"  # típico ASP.NET landing


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def login_and_save_session(headless=True):
    """Loguea en Exel del Norte, resuelve Turnstile, guarda cookies."""
    if not EXEL_USUARIO or not EXEL_PASSWORD:
        log("ABORT: EXEL_USUARIO/EXEL_PASSWORD no configurado en .env")
        return False

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        log(f"Lanzando Chromium {'headless' if headless else 'headed'}...")
        browser = p.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="es-MX",
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()

        # Step 1: cargar página de login
        log(f"GET {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)

        # Step 2: detectar si Turnstile está activo
        # En Exel del Norte el widget existe (height=0, sin sitekey) pero NO valida.
        # Solo esperar Turnstile si tiene sitekey (señal de challenge real).
        log("Verificando Turnstile...")
        turnstile_active = page.evaluate("""() => {
            const w = document.querySelector('[id*="turnstile"], .cf-turnstile, [data-sitekey]');
            return !!(w && w.getAttribute('data-sitekey'));
        }""")
        if turnstile_active:
            log("Turnstile activo, esperando resolver...")
            try:
                page.wait_for_function(
                    """() => {
                        const t = document.querySelector('input[name="ctl00$hdnTokenCloudflare"]');
                        return t && t.value && t.value.length > 50;
                    }""",
                    timeout=60000,
                )
                log("✓ Turnstile resuelto")
            except Exception:
                log("⚠️  Turnstile timeout — intentando login de todos modos")
        else:
            log("✓ Turnstile NO requerido (widget vacío)")

        # Step 3: llenar usuario y password
        log(f"Login user={EXEL_USUARIO}")
        page.fill('input[name="ctl00$MainContent$txtUsuario"]', EXEL_USUARIO)
        page.fill('input[name="ctl00$MainContent$txtPassword"]', EXEL_PASSWORD)

        # Pequeña pausa para que JS de Turnstile siga vivo
        time.sleep(2)

        # Step 4: click btnAceptar
        log("Click btnAceptar...")
        try:
            page.click('input[name="ctl00$MainContent$btnAceptar"]', timeout=15000)
        except Exception as e:
            log(f"  click falló: {e}; intentando submit con Enter")
            page.press('input[name="ctl00$MainContent$txtPassword"]', 'Enter')

        # Step 5: esperar respuesta del server (4s mínimo para que termine redirect post-login)
        log("Esperando respuesta post-login (8s)...")
        time.sleep(4)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        time.sleep(2)

        page.screenshot(path="/tmp/exel_post_login.png")
        log(f"Screenshot post-login: /tmp/exel_post_login.png")
        log(f"URL post-login: {page.url}")
        log(f"Title: {page.title()}")

        # Confirmar login con señales fuertes (no solo strings genéricos)
        login_signals = page.evaluate("""() => {
            // Buscar carrito $0.00 (señal post-login)
            const hasCart = !!document.querySelector('[class*="carrito"], [id*="carrito"]')
                            || /\\$\\s*0\\.00/.test(document.body.innerText);
            // Buscar "Mi Ejecutivo" / "Mi Cuenta" en links
            const hasMyAccount = Array.from(document.querySelectorAll('a, button, span'))
                .some(el => /mi cuenta|mi ejecutivo|cerrar sesi[oó]n|salir/i.test(el.innerText || ''));
            // Buscar form de login (señal NEGATIVA)
            const hasLoginForm = !!document.querySelector('input[name="ctl00$MainContent$txtPassword"]');
            return {hasCart, hasMyAccount, hasLoginForm, url: location.href};
        }""")
        log(f"Signals: {login_signals}")

        logueado = (login_signals.get("hasMyAccount") or login_signals.get("hasCart")) \
                   and not login_signals.get("hasLoginForm")
        log(f"Logueado: {logueado}")

        # Si aún en /Acceso pero realmente logueado, navegar a home
        if logueado and "Acceso" in page.url:
            log("Navegando a home /xlstore/...")
            page.goto("https://www.exel.com.mx/xlstore/", wait_until="networkidle")
            time.sleep(2)
            log(f"URL nueva: {page.url}")

        # Step 6: capturar cookies
        cookies = context.cookies()
        log(f"Cookies capturadas: {len(cookies)}")
        for c in cookies:
            log(f"  {c['name']:<30} domain={c['domain']:<25} {c['value'][:30]}...")

        # Step 7: guardar sesión
        session_data = {
            "captured_at": datetime.now().isoformat(),
            "url_after_login": page.url,
            "logueado": logueado,
            "cookies": cookies,
            "user_agent": page.evaluate("navigator.userAgent"),
        }
        with open(SESSION_FILE, "w") as f:
            json.dump(session_data, f, indent=2, default=str)
        os.chmod(SESSION_FILE, 0o600)
        log(f"✓ Sesión guardada en {SESSION_FILE}")

        # Captura screenshot para debug si --headed
        if not headless:
            page.screenshot(path="/tmp/exel_after_login.png")
            log("Screenshot: /tmp/exel_after_login.png")

        browser.close()
        return logueado


def check_session():
    """Validar sesión actual sin re-loguear."""
    if not os.path.exists(SESSION_FILE):
        log("Sin sesión guardada.")
        return False

    s = json.load(open(SESSION_FILE))
    captured = datetime.fromisoformat(s["captured_at"])
    age_min = (datetime.now() - captured).total_seconds() / 60
    log(f"Sesión capturada hace {age_min:.0f} min")
    log(f"Cookies guardadas: {len(s.get('cookies', []))}")
    log(f"Logueado al guardar: {s.get('logueado')}")

    # Validar que la sesión sigue viva con curl_cffi
    from curl_cffi import requests as cffi
    cookies_dict = {c["name"]: c["value"] for c in s.get("cookies", [])}
    sess = cffi.Session(impersonate="chrome120")
    sess.cookies.update(cookies_dict)
    r = sess.get("https://www.exel.com.mx/xlstore/Inicio", timeout=20, allow_redirects=False)
    log(f"GET /Inicio → {r.status_code}")
    if r.status_code == 200:
        body = r.text.lower()
        valid = "cerrar sesi" in body or "logout" in body or "mi cuenta" in body
        log(f"Sesión válida: {valid}")
        return valid
    log("Sesión NO válida (debe re-loguear)")
    return False


def main():
    headed = "--headed" in sys.argv
    check_only = "--check" in sys.argv

    if check_only:
        check_session()
        return

    ok = login_and_save_session(headless=not headed)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
