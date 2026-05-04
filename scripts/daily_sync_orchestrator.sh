#!/bin/bash
# Orquestador diario de syncs proveedor → Odoo
# Ejecuta CT + Ingram en cadena, modo --diff (solo cambios)
# Diseñado para correr vía launchd 6:00 AM CST

set -u

REPO=~/syscom-odoo-sync
LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M)
ORCH_LOG="$LOG_DIR/daily_orchestrator_${TIMESTAMP}.log"

log() {
  echo "[$(date '+%F %T')] $1" | tee -a "$ORCH_LOG"
}

# Activar venv
source "$REPO/.venv/bin/activate" 2>/dev/null || {
  log "ERROR: no se pudo activar venv en $REPO/.venv"
  exit 1
}

cd "$REPO"

# ─────────────────────────────────────────────────────────────────────────────
# 1) CT — diferencial diario (lee cache Oracle, ya fresco por cron 15min)
# ─────────────────────────────────────────────────────────────────────────────
log "════════════════════════════════════════════════════════════════════════"
log "1/2 — CT → Odoo (modo --diff)"
log "════════════════════════════════════════════════════════════════════════"

CT_LOG="$LOG_DIR/ct_diff_${TIMESTAMP}.log"
python scripts/ct_to_odoo_sync.py --diff > "$CT_LOG" 2>&1
CT_EXIT=$?
log "CT exit=$CT_EXIT  log=$CT_LOG"

# Resumen rápido
if [ -f "$REPO/scripts/ct_sync_progress.json" ]; then
  CT_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/ct_sync_progress.json'))
print(f'CT  proc={d.get(\"processed\",0):>5} new={d.get(\"new\",0):>4} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $CT_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2) Ingram — diferencial diario
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "2/3 — Ingram → Odoo (modo --diff)"
log "════════════════════════════════════════════════════════════════════════"

INGRAM_LOG="$LOG_DIR/ingram_diff_${TIMESTAMP}.log"
python scripts/ingram_to_odoo_sync.py --diff > "$INGRAM_LOG" 2>&1
INGRAM_EXIT=$?
log "Ingram exit=$INGRAM_EXIT  log=$INGRAM_LOG"

if [ -f "$REPO/scripts/ingram_sync_progress.json" ]; then
  INGRAM_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/ingram_sync_progress.json'))
print(f'ING proc={d.get(\"processed\",0):>5} new={d.get(\"new\",0):>4} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $INGRAM_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3) Exel del Norte — diferencial diario (Playwright login + curl_cffi sync)
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "3/3 — Exel del Norte → Odoo (modo --diff)"
log "════════════════════════════════════════════════════════════════════════"

# Colectar SKUs de Exel con Playwright (paginación ASP.NET PostBack)
# También refresca cookies de sesión (caducan ~20 min)
log "Colectando SKUs Exel + refrescando cookies (Playwright)..."
EXEL_COLLECT_LOG="$LOG_DIR/exel_collect_${TIMESTAMP}.log"
python scripts/exel_collect_skus.py > "$EXEL_COLLECT_LOG" 2>&1
EXEL_COLLECT_EXIT=$?
log "Collect Exel exit=$EXEL_COLLECT_EXIT"

if [ $EXEL_COLLECT_EXIT -eq 0 ]; then
  EXEL_LOG="$LOG_DIR/exel_diff_${TIMESTAMP}.log"
  python scripts/exel_to_odoo_sync.py --diff > "$EXEL_LOG" 2>&1
  EXEL_EXIT=$?
  log "Exel sync exit=$EXEL_EXIT  log=$EXEL_LOG"

  if [ -f "$REPO/scripts/exel_sync_progress.json" ]; then
    EXEL_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/exel_sync_progress.json'))
print(f'EXE proc={d.get(\"processed\",0):>5} new={d.get(\"new\",0):>4} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
    log "  $EXEL_RESUMEN"
  fi
else
  log "⚠️  Skip sync Exel — collect Playwright falló"
  EXEL_RESUMEN="EXE collect_failed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Notificación Telegram (si está configurado)
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  MSG="🔄 *Sync diario completado* $(date '+%H:%M')%0A%0A"
  MSG="${MSG}*CT:*%0A${CT_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*Ingram:*%0A${INGRAM_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*Exel:*%0A${EXEL_RESUMEN:-no data}"
  curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}&text=${MSG}&parse_mode=Markdown" > /dev/null 2>&1
fi

log ""
log "════════════════════════════════════════════════════════════════════════"
log "Orquestador terminado"
log "════════════════════════════════════════════════════════════════════════"
