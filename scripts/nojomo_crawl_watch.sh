#!/bin/bash
# nojomo_crawl_watch.sh — Watchdog del crawl de catalogo Nojomo (refacciones e-commerce).
# Red de seguridad (regla Ocean "construir bien, no rapido"):
#   - AUTO-REINICIO: si el crawl se cae (red/login/timeout), lo relanza (es resumable).
#   - TERMINO REAL ("trabajo vivo", no solo "proceso vivo"): solo declara COMPLETO cuando
#     un relanzamiento termina SIN trabajo pendiente Y con cifras sustanciales (anti falso-positivo).
#   - IDEMPOTENTE: al completar marca nojomo_CRAWL_DONE y deja de actuar.
#   - ALERTA a Felipe via Telegram al terminar; warning throttled (6h) si algo huele mal.
set -u
ALERT_DC_TOKEN="_skip_"          # evita el SSH a Roger por el token de Discord (mandamos telegram-only)
source "$HOME/bin/lib-alert.sh" 2>/dev/null

SCRIPTS="$HOME/syscom-odoo-sync/scripts"
DATA="$HOME/LICITABOT/data"
BGLOG="$HOME/Library/Logs/nojomo_crawl_bg.log"
WLOG="$HOME/Library/Logs/nojomo_crawl_watch.log"
DONE_MARK="$DATA/nojomo_CRAWL_DONE"
WARN_MARK="$DATA/nojomo_watch_lastwarn"
PY=/usr/bin/python3
CATS="bat,ac,toner,drum,lcd"

ts(){ date '+%Y-%m-%d %H:%M:%S'; }
wlog(){ echo "[$(ts)] $*" >> "$WLOG"; }
warn_throttled(){   # alerta warning maximo 1 vez cada 6h (anti-spam)
  local now last=0
  now=$(date +%s)
  [ -f "$WARN_MARK" ] && last=$(cat "$WARN_MARK" 2>/dev/null || echo 0)
  if [ $(( now - last )) -ge 21600 ]; then
    alert_send "$1" "warning" "telegram-only"; echo "$now" > "$WARN_MARK"
  fi
}

# Ya completado: no hacer nada
[ -f "$DONE_MARK" ] && exit 0

# Crawl vivo: nada que hacer
if pgrep -f 'nojomo_scraper.py --cat' >/dev/null 2>&1; then
  wlog "crawl vivo — ok"
  exit 0
fi

# Crawl caido o terminado: relanzar (resumable) y ver si queda trabajo
wlog "crawl NO corre — relanzo (resumable) para continuar/verificar termino"
cd "$SCRIPTS" || { wlog "ERROR: no cd a $SCRIPTS"; exit 1; }
nohup $PY nojomo_scraper.py --cat "$CATS" >> "$BGLOG" 2>&1 &
NEWPID=$!
sleep 90

if kill -0 "$NEWPID" 2>/dev/null; then
  wlog "relanzado y sigue corriendo (PID $NEWPID) => habia trabajo pendiente (auto-restart). Continua."
  exit 0
fi

# Termino en <90s => o esta completo, o el relanzamiento fallo (login/red)
NP=$(wc -l < "$DATA/nojomo_productos.jsonl" 2>/dev/null | tr -d ' '); NP=${NP:-0}
NC=$(wc -l < "$DATA/nojomo_compat.jsonl"    2>/dev/null | tr -d ' '); NC=${NC:-0}
NL=$($PY -c "import json,sys
try: print(len(json.load(open('$DATA/nojomo_state.json')).get('done_listados',[])))
except: print(0)" 2>/dev/null); NL=${NL:-0}

if [ "$NP" -ge 500 ] && [ "$NL" -ge 200 ]; then
  alert_send "OK Crawl Nojomo TERMINADO — $NP refacciones · $NC compatibilidades (modelo->SKU). Listo para armar el almacen + la calculadora e-commerce." "info" "telegram-only"
  touch "$DONE_MARK"
  wlog "COMPLETO: productos=$NP compat=$NC listados=$NL — alertado + DONE"
else
  warn_throttled "Watchdog Nojomo: el crawl salio rapido con cifras bajas (prod=$NP, listados=$NL). Posible fallo de login/red. Reintento automatico cada ciclo."
  wlog "salida rapida cifras bajas (prod=$NP listados=$NL) — NO marco DONE (reintenta)"
fi
exit 0
