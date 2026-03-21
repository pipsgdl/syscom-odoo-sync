#!/bin/zsh
# ⚔️ ARES v4 — Full Catalog Sync Syscom → Odoo (54,391 productos)
# Dispara via webhook, espera nueva ejecución, repite

N8N_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwYmJmOWMxNi1iY2QwLTRkZWYtOWJiMy1hZjA0ZjVkNDcyMWQiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiYjc4MjRjMTAtOTQxMS00ZDhmLTgwNTEtMWYzYzYzNmZmYTBkIiwiaWF0IjoxNzcyMjEyMzYwfQ.oFs4zmce5fdQv-hXHuVdt56pBI9i_24EFk_TBE3WNu8"
BASE="https://n8n.dealbapropiedades.com.mx/api/v1"
WEBHOOK="https://n8n.dealbapropiedades.com.mx/webhook/syscom-sync-v3"
WF_ID="2rMRq0B9ewvd1lBn"
CATS=("Videovigilancia" "Radiocomunicación" "Redes e IT" "IoT/GPS/Telemática" "Energía/Herramientas" "Automatización" "Control Acceso" "Det. Fuego" "Marketing" "Cableado" "Audio/Video" "Industria/BMS")
TOTAL_CATS=12
TOTAL_PAGES=913

LOG="/tmp/ares_v4_log.txt"
WAIT_SUCCESS=3
WAIT_ERROR=120
CONSEC_ERRORS=0
MAX_CONSEC_ERRORS=5
RUN=0

COUNT_BEFORE=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,'M1ercole\$','product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=20).read()).get('result',0))
" 2>/dev/null)

echo "$(date '+%H:%M:%S') - ARES v4 iniciado | Baseline: $COUNT_BEFORE | Target: 54,391" > $LOG

# Get initial last exec ID
LAST_KNOWN_ID=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
  "$BASE/executions?workflowId=$WF_ID&limit=1" | python3 -c "
import sys,json; d=json.load(sys.stdin).get('data',[]); print(d[0]['id'] if d else 0)
" 2>/dev/null)

while true; do
  clear

  # Get position from staticData
  STATE=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" "$BASE/workflows/$WF_ID" | python3 -c "
import sys,json
w=json.load(sys.stdin)
g=w.get('staticData',{}).get('global',{})
print(g.get('categoryIndex',0), g.get('page',1), g.get('offset',0), g.get('totalCreated',0), g.get('totalUpdated',0), g.get('totalErrors',0), g.get('totalProcessed',0))
" 2>/dev/null)
  CAT_IDX=$(echo $STATE | awk '{print $1}')
  PAGE=$(echo $STATE | awk '{print $2}')
  OFFSET=$(echo $STATE | awk '{print $3}')
  G_CREATED=$(echo $STATE | awk '{print $4}')
  G_UPDATED=$(echo $STATE | awk '{print $5}')
  G_ERRORS=$(echo $STATE | awk '{print $6}')
  G_PROCESSED=$(echo $STATE | awk '{print $7}')

  PRODUCTS=$(python3 -c "
import urllib.request,json
p={'jsonrpc':'2.0','method':'call','id':1,'params':{'service':'object','method':'execute_kw',
   'args':['ocean-tech-0326',2,'M1ercole\$','product.template','search_count',[[['active','=',True]]]]}}
req=urllib.request.Request('https://ocean-tech-0326.odoo.com/jsonrpc',data=json.dumps(p).encode(),
   headers={'Content-Type':'application/json'},method='POST')
print(json.loads(urllib.request.urlopen(req,timeout=20).read()).get('result',0))
" 2>/dev/null)
  NEW=$((PRODUCTS - COUNT_BEFORE))

  # Progress based on total processed vs 54391
  if [ "$G_PROCESSED" -gt "0" ] 2>/dev/null; then
    PCT=$((G_PROCESSED * 100 / 54391))
  else
    PCT=0
  fi
  FILLED=$((PCT / 2))
  [ $FILLED -gt 50 ] && FILLED=50
  EMPTY=$((50 - FILLED))
  BAR=$(printf '█%.0s' $(seq 1 $FILLED 2>/dev/null))
  SPACE=$(printf '░%.0s' $(seq 1 $EMPTY 2>/dev/null))

  echo "\033[1;36m╔══════════════════════════════════════════════════════════╗"
  echo "║  ⚔️  ARES v4 — Full Catalog Sync (54,391 productos)     ║"
  echo "╠══════════════════════════════════════════════════════════╣\033[0m"

  if [ "$CAT_IDX" -ge "$TOTAL_CATS" ] 2>/dev/null; then
    echo "\033[1;32m║  🏁  ¡SINCRONIZACIÓN COMPLETA!                           ║"
    echo "║  📦 Productos Odoo: $PRODUCTS (eran $COUNT_BEFORE)"
    echo "║  ➕ Nuevos:         +$NEW"
    echo "║  ✨ Creados:        $G_CREATED"
    echo "║  🔄 Actualizados:   $G_UPDATED"
    echo "║  ❌ Errores:        $G_ERRORS"
    echo "║  📊 Procesados:     $G_PROCESSED / 54,391"
    echo "║  🔄 Runs totales:   $RUN"
    echo "╚══════════════════════════════════════════════════════════╝\033[0m"
    echo "$(date '+%H:%M:%S') - 🏁 COMPLETADO +$NEW | C:$G_CREATED U:$G_UPDATED E:$G_ERRORS" >> $LOG
    sleep 600
    continue
  fi

  echo "  📊 [\033[1;36m${BAR}\033[0;90m${SPACE}\033[0m] ${PCT}%  (${G_PROCESSED}/54,391)"
  echo "  📂 \033[1;37m${CATS[$((CAT_IDX+1))]}\033[0m [cat $CAT_IDX/$TOTAL_CATS]  pág \033[1;37m$PAGE\033[0m  offset $OFFSET"
  echo "  📦 Odoo: \033[1;37m${PRODUCTS}\033[0m  (\033[1;32m+${NEW}\033[0m nuevos en Odoo)"
  echo "  ✨ C:\033[1;32m${G_CREATED}\033[0m  🔄 U:${G_UPDATED}  ❌ E:\033[1;31m${G_ERRORS}\033[0m  runs: $RUN  consec_err: $CONSEC_ERRORS"
  echo "\033[1;36m╠══════════════════════════════════════════════════════════╣\033[0m"

  # === FIRE ===
  RUN=$((RUN+1))
  WH=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$WEBHOOK" \
    -H "Content-Type: application/json" -d '{}' --max-time 10)

  if [ "$WH" != "200" ]; then
    CONSEC_ERRORS=$((CONSEC_ERRORS+1))
    echo "  \033[1;31m❌ #$RUN webhook=$WH err#$CONSEC_ERRORS\033[0m"
    echo "$(date '+%H:%M:%S') - ❌ #$RUN wh=$WH err$CONSEC_ERRORS" >> $LOG
    echo "\033[1;36m╚══════════════════════════════════════════════════════════╝\033[0m"
    [ $CONSEC_ERRORS -ge $MAX_CONSEC_ERRORS ] && sleep $((WAIT_ERROR * 3)) || sleep $WAIT_ERROR
    continue
  fi

  echo "  🚀 #$RUN disparado, esperando exec > #$LAST_KNOWN_ID..."

  # === WAIT for new execution to appear and finish ===
  ATTEMPTS=0
  FOUND_NEW=false
  NEW_ID=0
  NEW_STATUS="?"

  while [ $ATTEMPTS -lt 60 ]; do
    sleep 5
    ATTEMPTS=$((ATTEMPTS+1))

    LAST=$(curl -s --max-time 8 -H "X-N8N-API-KEY: $N8N_KEY" \
      "$BASE/executions?workflowId=$WF_ID&limit=1" | python3 -c "
import sys,json
data=json.load(sys.stdin).get('data',[])
e=data[0] if data else {}
print(e.get('id',0), e.get('status','?'))
" 2>/dev/null)
    CUR_ID=$(echo $LAST | awk '{print $1}')
    CUR_STATUS=$(echo $LAST | awk '{print $2}')

    if [ "$CUR_ID" -gt "$LAST_KNOWN_ID" ] 2>/dev/null; then
      NEW_ID=$CUR_ID
      NEW_STATUS=$CUR_STATUS

      if [ "$CUR_STATUS" = "success" ] || [ "$CUR_STATUS" = "error" ] || [ "$CUR_STATUS" = "crashed" ]; then
        FOUND_NEW=true
        break
      fi
      echo "  ⏳ [$ATTEMPTS] #$CUR_ID $CUR_STATUS..."
    else
      [ $((ATTEMPTS % 6)) -eq 0 ] && echo "  ⏳ [$ATTEMPTS] waiting for new exec..."
    fi
  done

  if [ "$FOUND_NEW" = "true" ]; then
    LAST_KNOWN_ID=$NEW_ID

    if [ "$NEW_STATUS" = "success" ]; then
      CONSEC_ERRORS=0
      echo "  \033[1;32m✅ #$RUN → exec #$NEW_ID OK\033[0m"
      echo "$(date '+%H:%M:%S') - ✅ #$RUN cat[$CAT_IDX] p$PAGE o$OFFSET #$NEW_ID" >> $LOG
    else
      CONSEC_ERRORS=$((CONSEC_ERRORS+1))
      echo "  \033[1;31m❌ #$RUN → exec #$NEW_ID $NEW_STATUS\033[0m"
      echo "$(date '+%H:%M:%S') - ❌ #$RUN $NEW_STATUS #$NEW_ID err$CONSEC_ERRORS" >> $LOG
      if [ $CONSEC_ERRORS -ge $MAX_CONSEC_ERRORS ]; then
        sleep $((WAIT_ERROR * CONSEC_ERRORS))
        continue
      fi
    fi
  else
    echo "  \033[1;33m⚠️ #$RUN timeout (no new exec after 5min)\033[0m"
    echo "$(date '+%H:%M:%S') - ⚠️ #$RUN timeout" >> $LOG
    CONSEC_ERRORS=$((CONSEC_ERRORS+1))
    sleep $WAIT_ERROR
    continue
  fi

  echo "\033[1;36m╚══════════════════════════════════════════════════════════╝\033[0m"
  sleep $WAIT_SUCCESS
done
