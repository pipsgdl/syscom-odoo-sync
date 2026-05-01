#!/bin/bash
# Watchdog: espera a que termine el sync Ingram (PID 41324) y luego lanza fix Syscom.
# Corre en background con nohup.
#
# Lanzar: nohup bash watchdog_syscom_fix.sh > /tmp/watchdog_syscom.log 2>&1 < /dev/null &

INGRAM_PID=41324
REPO=~/syscom-odoo-sync
TIMESTAMP=$(date +%Y%m%d_%H%M)
WATCHDOG_LOG="$REPO/logs/watchdog_${TIMESTAMP}.log"

log() {
  echo "[$(date '+%F %T')] $1" | tee -a "$WATCHDOG_LOG"
}

log "Watchdog iniciado. Vigilando PID $INGRAM_PID (Ingram sync)"
log "Cuando termine, lanzaré fix_syscom_supplierinfo.py"

# Loop hasta que muera el PID Ingram
while ps -p $INGRAM_PID > /dev/null 2>&1; do
  sleep 60
done

log "✓ Ingram sync (PID $INGRAM_PID) terminado."
log "Esperando 60s extra para asegurar que liberó conexiones Odoo..."
sleep 60

# Lanzar fix Syscom en background
FIX_LOG="$REPO/logs/fix_syscom_${TIMESTAMP}.log"
log "Lanzando fix_syscom_supplierinfo.py → $FIX_LOG"

cd "$REPO"
source .venv/bin/activate 2>/dev/null

nohup python scripts/fix_syscom_supplierinfo.py > "$FIX_LOG" 2>&1 < /dev/null &
FIX_PID=$!
disown $FIX_PID 2>/dev/null

log "Fix Syscom lanzado con PID $FIX_PID"
echo "$FIX_PID" > /tmp/fix_syscom_pid.txt
log "PID guardado en /tmp/fix_syscom_pid.txt"

# Esperar 30s y verificar que arrancó OK
sleep 30
if ps -p $FIX_PID > /dev/null 2>&1; then
  log "✓ Fix Syscom corriendo (PID $FIX_PID)"
  log "Watchdog termina aquí. El fix sigue en background."
else
  log "⚠ Fix Syscom murió en los primeros 30s. Revisar $FIX_LOG"
fi
