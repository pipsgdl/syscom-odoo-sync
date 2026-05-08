#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingram_token_health_check.py
============================

Valida el estado del refresh_token de Ingram ANTES de lanzar el sync diario.

Comportamiento:
  - Si NO hay token → exit 2 + log alerta CRITICA
  - Si el token cacheado expira en < 24h → intenta refrescar (rotacion)
  - Si refresh devuelve invalid_grant → exit 2 + log alerta CRITICA
                                         (Felipe debe pegar snippet DevTools)
  - Si todo OK → exit 0
  - Si rotated_at > 25 dias atras → WARNING (Okta refresh ~30d sin uso)

Uso:
    python scripts/ingram_token_health_check.py
    python scripts/ingram_token_health_check.py --quiet   (solo exit code)

Integracion en daily_sync_orchestrator.sh:
    python scripts/ingram_token_health_check.py || {
        echo "ALERTA: Ingram token requiere accion manual (DevTools snippet)"
        # mandar a log y saltar Ingram, seguir con CT/Exel/CVA
        SKIP_INGRAM=1
    }
"""
from __future__ import annotations
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPTS_DIR)
TOKEN_CACHE = os.path.join(SCRIPTS_DIR, ".ingram_token_cache.json")
ENV_FILE = os.path.join(PROJECT_DIR, ".env")

INGRAM_OAUTH_HOST = "myaccount.ingrammicro.com"
INGRAM_OAUTH_PATH = "/oauth2/aus4rmpuo7DK22t9R357/v1/token"

QUIET = "--quiet" in sys.argv

# Margenes de salud
WARN_DAYS_INACTIVE = 25      # Okta rota refresh cada uso, expira ~30d sin uso
CRIT_HOURS_TO_EXPIRE = 24    # si access expira en <24h, refrescar ya

ctx = ssl.create_default_context()


def log(msg, level="INFO"):
    if QUIET and level not in ("CRIT", "WARN"):
        return
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def load_env():
    """Lee .env (sin dependencia de python-dotenv)."""
    env = {}
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_cache():
    if not os.path.exists(TOKEN_CACHE):
        return None
    try:
        with open(TOKEN_CACHE) as f:
            return json.load(f)
    except Exception as e:
        log(f"Cache corrupto: {e}", "WARN")
        return None


def save_cache(data):
    with open(TOKEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(TOKEN_CACHE, 0o600)


def try_refresh(refresh_token: str, client_id: str) -> tuple[bool, dict | str]:
    """Intenta rotar el refresh_token. Retorna (ok, payload | error_msg)."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "scope": "offline_access email profile openid",
    }).encode()
    req = urllib.request.Request(
        f"https://{INGRAM_OAUTH_HOST}{INGRAM_OAUTH_PATH}",
        data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return True, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return False, f"EXC: {e}"


def main():
    log("=" * 60)
    log("Ingram OAuth — health check")
    log("=" * 60)

    env = load_env()
    refresh_env = env.get("INGRAM_REFRESH_TOKEN", "")
    client_env = env.get("INGRAM_OAUTH_CLIENT_ID", "")

    cache = load_cache()
    refresh_token = (cache or {}).get("refresh_token") or refresh_env
    client_id = client_env  # client_id no rota, vive en .env

    if not refresh_token:
        log("CRITICAL: no hay INGRAM_REFRESH_TOKEN ni en .env ni en cache", "CRIT")
        log("ACCION: Felipe debe loguear en mx.ingrammicro.com y pegar snippet DevTools (ver .env.example)", "CRIT")
        sys.exit(2)

    if not client_id:
        log("CRITICAL: no hay INGRAM_OAUTH_CLIENT_ID en .env", "CRIT")
        sys.exit(2)

    # Antiguedad de la ultima rotacion
    rotated_at = (cache or {}).get("rotated_at")
    if rotated_at:
        try:
            dt = datetime.fromisoformat(rotated_at)
            age_days = (datetime.now() - dt).days
            log(f"Ultima rotacion: {rotated_at} ({age_days}d atras)")
            if age_days >= WARN_DAYS_INACTIVE:
                log(f"WARN: refresh_token sin rotar hace {age_days}d (Okta caduca ~30d)", "WARN")
        except Exception:
            pass

    # Tiempo restante del access actual
    expires_at = (cache or {}).get("expires_at", 0)
    now = int(time.time())
    secs_left = expires_at - now
    if secs_left > 0:
        hrs_left = secs_left / 3600.0
        log(f"Access token vive {hrs_left:.1f}h mas")
        if hrs_left > CRIT_HOURS_TO_EXPIRE:
            log("OK: token vigente, no se refresca")
            sys.exit(0)

    # Refrescar para validar que el refresh_token sigue vivo
    log("Probando refresh OAuth para validar token...")
    ok, payload = try_refresh(refresh_token, client_id)
    if not ok:
        log(f"CRITICAL: refresh OAuth fallo — {payload}", "CRIT")
        if "invalid_grant" in str(payload):
            log("=" * 60, "CRIT")
            log("ACCION REQUERIDA — refresh_token expirado en Okta", "CRIT")
            log("1. Loguea en https://mx.ingrammicro.com (rz80 / Tunel$2380)", "CRIT")
            log("2. F12 -> Console, pega ESTA linea (todo, sin saltos):", "CRIT")
            log("", "CRIT")
            log("   var o = JSON.parse(localStorage['okta-token-storage']); copy(`INGRAM_REFRESH_TOKEN=${o.refreshToken.refreshToken}\\nINGRAM_OAUTH_CLIENT_ID=${o.idToken.clientId}\\nINGRAM_CUSTOMER_NUMBER=80697300`); console.log('COPIADO');", "CRIT")
            log("", "CRIT")
            log("3. Pega resultado en .env reemplazando esas 3 lineas", "CRIT")
            log(f"4. Borra cache: rm {TOKEN_CACHE}", "CRIT")
            log("5. Reintenta: python scripts/ingram_to_odoo_sync.py --diff", "CRIT")
            log("=" * 60, "CRIT")
        sys.exit(2)

    # Refresh OK — guardar nuevo cache
    new_cache = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "expires_at": now + int(payload.get("expires_in", 3600)) - 60,
        "scope": payload.get("scope"),
        "rotated_at": datetime.now().isoformat(),
    }
    save_cache(new_cache)
    log(f"OK: token rotado, expira en {payload.get('expires_in')}s")
    sys.exit(0)


if __name__ == "__main__":
    main()
