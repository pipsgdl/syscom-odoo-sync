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
  MSG="${MSG}*Exel:*%0A${EXEL_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*Syscom:*%0A${SYSCOM_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*CVA:*%0A${CVA_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*Tecno:*%0A${TECNO_RESUMEN:-no data}%0A%0A"
  MSG="${MSG}*TVC:*%0A${TVC_RESUMEN:-no data}"
  curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}&text=${MSG}&parse_mode=Markdown" > /dev/null 2>&1
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4) Syscom — sync resume diario (33K productos, sin imágenes para velocidad)
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "4/7 — Syscom → Odoo (modo --resume --skip-images)"
log "════════════════════════════════════════════════════════════════════════"

SYSCOM_LOG="$LOG_DIR/syscom_sync_${TIMESTAMP}.log"
python scripts/syscom_to_odoo_prod_sync.py --resume --skip-images > "$SYSCOM_LOG" 2>&1
SYSCOM_EXIT=$?
log "Syscom exit=$SYSCOM_EXIT  log=$SYSCOM_LOG"

if [ -f "$REPO/scripts/sync_progress.json" ]; then
  SYSCOM_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/sync_progress.json'))
print(f'SYS proc={d.get(\"processed\",0):>5} new={d.get(\"new\",0):>4} chg={d.get(\"updated\",0):>4} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $SYSCOM_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5) CVA — sync diff diario (cache /tmp/cva_full.json)
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "5/7 — CVA → Odoo (modo --diff)"
log "════════════════════════════════════════════════════════════════════════"

CVA_LOG="$LOG_DIR/cva_diff_${TIMESTAMP}.log"
python scripts/cva_to_odoo_sync.py --diff > "$CVA_LOG" 2>&1
CVA_EXIT=$?
log "CVA exit=$CVA_EXIT  log=$CVA_LOG"

if [ -f "$REPO/scripts/cva_sync_progress.json" ]; then
  CVA_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/cva_sync_progress.json'))
print(f'CVA proc={d.get(\"processed\",0):>5} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $CVA_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6) Tecnosinergia — API v3 sigue OK aunque cuenta web pausada
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "6/7 — Tecnosinergia → Odoo (modo --diff)"
log "════════════════════════════════════════════════════════════════════════"

TECNO_LOG="$LOG_DIR/tecno_diff_${TIMESTAMP}.log"
python scripts/tecno_to_odoo_sync.py --diff > "$TECNO_LOG" 2>&1
TECNO_EXIT=$?
log "Tecno exit=$TECNO_EXIT  log=$TECNO_LOG"

if [ -f "$REPO/scripts/tecno_sync_progress.json" ]; then
  TECNO_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/tecno_sync_progress.json'))
print(f'TEC proc={d.get(\"processed\",0):>5} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $TECNO_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7) TVC — cache local (refresh manual semanal)
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "7/7 — TVC → Odoo (modo --diff cache local)"
log "════════════════════════════════════════════════════════════════════════"

TVC_LOG="$LOG_DIR/tvc_diff_${TIMESTAMP}.log"
python scripts/tvc_to_odoo_sync.py --diff > "$TVC_LOG" 2>&1
TVC_EXIT=$?
log "TVC exit=$TVC_EXIT  log=$TVC_LOG"

if [ -f "$REPO/scripts/tvc_sync_progress.json" ]; then
  TVC_RESUMEN=$(python3 -c "
import json
d=json.load(open('$REPO/scripts/tvc_sync_progress.json'))
print(f'TVC proc={d.get(\"processed\",0):>5} chg={d.get(\"price_changed\",0):>4} unchanged={d.get(\"unchanged\",0):>5} err={d.get(\"errors\",0)}')
" 2>/dev/null)
  log "  $TVC_RESUMEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Auto-actualizar checklist proveedores (lee logs reales + sync Obsidian)
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════════════"
log "Actualizando checklist proveedores..."
log "════════════════════════════════════════════════════════════════════════"
python scripts/update_checklist_proveedores.py >> "$ORCH_LOG" 2>&1
log "  Checklist actualizado: /Volumes/HIKSEMI 512/Claude code/CHECKLIST_PROVEEDORES.md"
log "  Sync Obsidian: /Volumes/HIKSEMI 512/ObsidianVault/proyectos/checklist-proveedores.md"

log ""
log "════════════════════════════════════════════════════════════════════════"
log "Orquestador terminado"
log "════════════════════════════════════════════════════════════════════════"
